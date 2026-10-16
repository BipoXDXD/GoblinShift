"""Coordenador de uma tentativa, do claim à publicação (docs/GoblinShiftPlan.md, 3.4 a 3.6).

Roda depois do commit do claim. Nenhuma transação fica aberta durante cópia, subprocesso ou
espera: cada escrita é uma transação curta que começa travando a Run com o token de fencing
(`Fence`: geração e Attempt viva). Se a trava falhar, outra tentativa ou o reconciliador assumiu;
o coordenador recebe `LostFencing`, registra e para sem escrever mais nada.

Passos:
1. `prepare`: o Runner cria a área vazia; as entradas do storage são copiadas para `inputs/`,
   conferindo SHA-256 e tamanho, `job.json` recebe os parâmetros fixados na aceitação e
   `context.json`, a imagem do executor fixada na Run (para o manifesto, ADR 021).
2. `start`: confere que ninguém pediu parada, chama `runner.start` com a imagem fixada na Run e
   registra `ExecutorStarted` (a partir daqui a tentativa conta como física) e a fase
   `processing`.
3. `supervise`: renova o lease a cada terço do prazo, grava o progresso, aplica o prazo vencido
   e, se a Run foi para `stopping` (cancelamento ou prazo) ou a tentativa passou do teto de
   duração, pede `stop` ao Runner. Runner fora do ar não interrompe a espera: o lease segue
   renovado e a consulta se repete (3.5: execuções aguardam dentro dos prazos).
4. `finish`: pelo código de saída, publica (0), falha de forma determinística (3, ou memória
   estourada) ou transitória (demais, inclusive o teto de 10 min). A publicação condiciona o
   `UPDATE` à geração, a `running`, à ausência de cancelamento e a `now() <= deadline_at`; se não
   afetar a linha, o motivo é relido e os candidatos são descartados. Coleta e gravação dos
   candidatos renovam o lease como o `supervise` (`_LeaseKeeper`), e erro de E/S nelas (disco do
   storage cheio, área ilegível) é infraestrutura: falha transitória na hora, sem esperar o lease
   vencer. Disco cheio aqui não é o limite de recurso de 1.6, que é o do sandbox do executor.
5. `remove`: só depois de o desfecho estar no banco. Se o Runner falhar aqui, a área fica para a
   limpeza da manutenção.
"""

import hashlib
import json
import logging
import os
import random
import stat
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from typing import BinaryIO

from opentelemetry.context import Context
from opentelemetry.trace import NoOpTracer, Tracer
from opentelemetry.util.types import AttributeValue
from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.artifacts import publish_artifact
from goblinshift.files.candidates import HeldContent, HeldContentMissingError, held_input_contents
from goblinshift.files.errors import ObjectNotAvailableError
from goblinshift.files.storage import FileStorage, StorageError
from goblinshift.platform.telemetry import UserHasher
from goblinshift.runs.lifecycle import (
    DETERMINISTIC_CODES,
    MAX_RETRY_JITTER,
    MIN_RETRY_JITTER,
    DeadlineExceeded,
    DeterministicFailure,
    ErrorCode,
    Event,
    ExecutorStarted,
    ExecutorStopped,
    Phase,
    PhaseReached,
    Published,
    RefusalReason,
    Refused,
    Status,
    TransientFailure,
    Transition,
    decide_transition,
)
from goblinshift.runs.store import (
    ACTIVE_STATUSES,
    Applied,
    Fence,
    LoadedRun,
    LostFencing,
    RunFinishedHook,
    apply_transition,
    complete_after_commit,
    lock_fenced,
    run_transaction,
)
from goblinshift.runs.tables import attempt, run, run_input, run_output
from goblinshift.runs.worker import attempt_event
from goblinshift.runs.worker.candidate_writer import CandidateWriter, StoredCandidate
from goblinshift.runs.worker.claim import ClaimedAttempt
from goblinshift.runs.worker.collection import (
    Collected,
    CollectionError,
    OutputLimitError,
    collect_outputs,
)
from goblinshift.runs.worker.runner import ExecutorStatus, Runner, RunnerError

logger = logging.getLogger(__name__)

EXIT_SUCCEEDED = 0
EXIT_RECIPE_FAILURE = 3
# `ExitCode.NO_SPACE` do executor: o disco da área de trabalho encheu (limite de recurso, 1.6). O
# worker não importa o laço do executor, que carrega todas as receitas.
EXIT_NO_SPACE = 6
_MILLISECOND = timedelta(milliseconds=1)
COPY_CHUNK_BYTES = 1024 * 1024
FAILURE_MAX_BYTES = 64 * 1024
# Uma linha `{"position": n}` por entrada avaliada: 1 MiB sobra para qualquer lote de 1.7.
PROGRESS_MAX_BYTES = 1024 * 1024
# Arquivos que o worker escreve para o executor, que os lê pelo grupo (contrato da fatia 4).
WORKER_FILE_MODE = 0o640
# Teto de cada tentativa (1.7, "Duração"); é duração, medida com o relógio monotônico.
DEFAULT_ATTEMPT_TIMEOUT = timedelta(minutes=10)
# Erro gravado na Attempt que termina por infraestrutura (catálogo de 1.6).
TRANSIENT_ATTEMPT_ERROR = ErrorCode.EXECUTOR_UNAVAILABLE


def random_retry_jitter() -> timedelta:
    """Espera da retentativa sorteada em [5 s, 30 s] (3.4), para não sincronizar retries."""
    low, high = MIN_RETRY_JITTER.total_seconds(), MAX_RETRY_JITTER.total_seconds()
    return timedelta(seconds=random.uniform(low, high))  # noqa: S311 - jitter, não segredo


@dataclass(frozen=True)
class CoordinatorConfig:
    lease: timedelta
    status_poll: timedelta = timedelta(seconds=0.2)
    stop_grace: timedelta = timedelta(seconds=10)
    attempt_timeout: timedelta = DEFAULT_ATTEMPT_TIMEOUT
    jitter: Callable[[], timedelta] = field(default=random_retry_jitter)

    @property
    def renew_every(self) -> timedelta:
        return self.lease / 3


class PreparationError(Exception):
    """A área de trabalho não pôde ser montada (entrada sumida ou divergente): infraestrutura."""


class _StopRequested(Exception):  # noqa: N818 - sinal interno de controle, não erro
    """A Run foi para `stopping` no meio do empacotamento: conclui pela parada."""


class _LeaseKeeper:
    """Renova o lease em trabalho longo fora do `supervise`, a cada `every`, entre blocos.

    O primeiro bloco já renova: a última renovação do `supervise` pode estar perto de vencer.
    Run em `stopping` (cancelamento, ou prazo vencido aplicado na renovação) levanta
    `_StopRequested`; fencing perdido, `LostFencing`.
    """

    def __init__(self, renew: Callable[[], Status], every: timedelta) -> None:
        self._renew = renew
        self._every_s = every.total_seconds()
        self._last = float("-inf")

    def beating(self, chunks: Iterable[bytes]) -> Iterator[bytes]:
        for chunk in chunks:
            self.beat()
            yield chunk

    def beat(self) -> None:
        if time.monotonic() - self._last < self._every_s:
            return
        self._last = time.monotonic()
        if self._renew() is Status.STOPPING:
            raise _StopRequested


class Coordinator:
    def __init__(
        self,
        factory: sessionmaker[Session],
        storage: FileStorage,
        runner: Runner,
        config: CoordinatorConfig,
        *,
        on_finished: RunFinishedHook | None = None,
        tracer: Tracer | None = None,
        user_hasher: UserHasher | None = None,
    ) -> None:
        self._factory = factory
        self._storage = storage
        self._runner = runner
        self._config = config
        self._on_finished = on_finished
        self._candidates = CandidateWriter(factory, storage)
        self._tracer: Tracer = tracer if tracer is not None else NoOpTracer()
        self._user_hasher = user_hasher

    def run(self, claimed: ClaimedAttempt) -> Applied | None:
        """Leva a tentativa até um estado decidido; `None` se o fencing foi perdido.

        Tudo acontece dentro do span `attempt` (3.7): raiz do próprio trace (`Context()` vazio),
        com link para a aceitação. Os passos acrescentam atributos ao span em curso.
        """
        with self._tracer.start_as_current_span(
            attempt_event.SPAN_NAME,
            context=Context(),
            links=attempt_event.links_to_acceptance(claimed.traceparent),
            attributes=self._identity_attributes(claimed),
        ):
            return self._run_traced(claimed)

    def _run_traced(self, claimed: ClaimedAttempt) -> Applied | None:
        started = time.monotonic()
        try:
            applied = self._drive(claimed)
        except LostFencing as lost:
            _record_end(started, "lost_fencing", error=None)
            _log_attempt(claimed, "lost_fencing", started, detail=str(lost))
            self._abandon(claimed)
            return None
        complete_after_commit(self._factory, self._storage, applied, self._on_finished)
        self.remove_work(claimed)
        _record_end(started, applied.state.status, error=applied.state.error_code)
        _log_attempt(claimed, applied.state.status, started, error=applied.state.error_code)
        return applied

    def _identity_attributes(self, claimed: ClaimedAttempt) -> dict[str, AttributeValue]:
        attributes: dict[str, AttributeValue] = {
            "main": True,
            "run_id": str(claimed.run_id),
            "attempt_id": str(claimed.attempt_id),
            "attempt.number": claimed.number,
            "attempt.generation": claimed.generation,
            "recipe_id": claimed.recipe_id,
            "recipe_version": claimed.recipe_version,
            "attempt.inputs": len(claimed.input_upload_ids),
            "queue_wait_ms": claimed.queue_wait_ms,
        }
        if self._user_hasher is not None:
            attributes["user.hash"] = self._user_hasher.hash(claimed.owner_id)
        return attributes

    def _drive(self, claimed: ClaimedAttempt) -> Applied:
        try:
            self.prepare(claimed)
        except PreparationError as error:
            logger.warning("área de trabalho falhou", extra={"fields": _ids(claimed, error)})
            return self.fail_transiently(claimed)
        if not self.may_start(claimed):
            return self.conclude_stop(claimed)
        try:
            self._runner.start(claimed.attempt_id, claimed.executor_image)
        except RunnerError as error:
            # Toda recusa do start é de infraestrutura, inclusive imagem fora da allowlist do
            # host: não é culpa da receita nem da entrada, e o deploy pode corrigi-la a tempo.
            logger.warning("executor não iniciou", extra={"fields": _ids(claimed, error)})
            return self.fail_transiently(claimed)
        self.record_start(claimed)
        return self.finish(claimed, self.supervise(claimed))

    # 1. Área de trabalho ---------------------------------------------------------------

    def prepare(self, claimed: ClaimedAttempt) -> None:
        try:
            with self._factory() as session, session.begin():
                contents = held_input_contents(session, claimed.owner_id, claimed.input_upload_ids)
        except HeldContentMissingError as error:
            raise PreparationError(str(error)) from error
        work_dir = self._runner.create_work_dir(claimed.attempt_id)
        inputs_dir = work_dir / "inputs"
        entries = [
            self._copy_input(content, position, inputs_dir)
            for position, content in enumerate(contents)
        ]
        job = {
            "recipe_id": claimed.recipe_id,
            "recipe_version": claimed.recipe_version,
            "parameters": claimed.parameters,
            "inputs": entries,
        }
        with _create_for_executor(work_dir / "job.json") as target:
            target.write(json.dumps(job, ensure_ascii=False).encode("utf-8"))
        # Num arquivo à parte, o executor anterior à fatia 5, que recusa chaves desconhecidas no
        # job.json, continua aceitando o job (ADR 021). Os uploads, na ordem das posições, são
        # como o select do PDF Press cita as páginas (ADR 035); executores anteriores os ignoram.
        context = {
            "executor_image": claimed.executor_image,
            "input_upload_ids": [str(upload_id) for upload_id in claimed.input_upload_ids],
        }
        with _create_for_executor(work_dir / "context.json") as target:
            target.write(json.dumps(context, ensure_ascii=False).encode("utf-8"))

    def _copy_input(
        self, content: HeldContent, position: int, inputs_dir: Path
    ) -> dict[str, object]:
        name = f"{position:04}.bin"
        digest = hashlib.sha256()
        size = 0
        try:
            with (
                self._storage.open(content.storage_key) as source,
                _create_for_executor(inputs_dir / name) as target,
            ):
                for chunk in _chunks(source):
                    digest.update(chunk)
                    size += len(chunk)
                    target.write(chunk)
        except StorageError as error:
            raise PreparationError(f"entrada {position}: {error}") from error
        if digest.digest() != content.sha256 or size != content.size_bytes:
            raise PreparationError(f"entrada {position} diverge do hash ou tamanho registrado")
        return {
            "position": position,
            "path": name,
            "original_name": content.original_name,
            "size": size,
            "sha256": digest.hexdigest(),
        }

    # 2. Início -------------------------------------------------------------------------

    def may_start(self, claimed: ClaimedAttempt) -> bool:
        """Cancelamento ou prazo podem ter chegado durante a preparação: não iniciar."""

        def check(session: Session) -> bool:
            loaded = lock_fenced(session, _fence(claimed), ACTIVE_STATUSES)
            return loaded.state.status is Status.RUNNING

        return run_transaction(self._factory, check)

    def record_start(self, claimed: ClaimedAttempt) -> None:
        def record(session: Session) -> None:
            loaded = lock_fenced(session, _fence(claimed), ACTIVE_STATUSES)
            self._apply_event(session, loaded, ExecutorStarted(claimed.generation))
            if loaded.state.status is Status.RUNNING:
                self.reach_phase_in(session, claimed, Phase.PROCESSING)

        run_transaction(self._factory, record)

    # 3. Acompanhamento -----------------------------------------------------------------

    def supervise(self, claimed: ClaimedAttempt) -> ExecutorStatus:
        """Espera o executor terminar, renovando o lease; pede `stop` se a Run vai parar."""
        started = time.monotonic()
        last_renew = float("-inf")
        while True:
            status = self._observe(claimed)
            if status is not None and not status.is_running:
                return status
            if time.monotonic() - last_renew >= self._config.renew_every.total_seconds():
                last_renew = time.monotonic()
                if self.renew_lease(claimed) is Status.STOPPING:
                    stopped = self._try_stop(claimed)
                    # Sem confirmação de término não há desfecho: o lease segue renovado.
                    if stopped is not None and not stopped.is_running:
                        return stopped
            if time.monotonic() - started >= self._config.attempt_timeout.total_seconds():
                stopped = self._try_stop(claimed)
                if stopped is not None and not stopped.is_running:
                    return replace(stopped, timed_out=True)
            time.sleep(self._config.status_poll.total_seconds())

    def _observe(self, claimed: ClaimedAttempt) -> ExecutorStatus | None:
        """Estado do executor; `None` se o Runner não respondeu (a espera continua)."""
        try:
            return self._runner.status(claimed.attempt_id)
        except RunnerError as error:
            logger.warning("executor sem estado", extra={"fields": _ids(claimed, error)})
            return None

    def _try_stop(self, claimed: ClaimedAttempt) -> ExecutorStatus | None:
        try:
            return self._runner.stop(claimed.attempt_id, self._config.stop_grace)
        except RunnerError as error:
            logger.warning("executor não parou", extra={"fields": _ids(claimed, error)})
            return None

    def renew_lease(self, claimed: ClaimedAttempt) -> Status:
        """Renova o lease e grava o progresso; aplica o prazo vencido. Devolve o status."""
        processed = self._processed_count(claimed)

        def renew(session: Session) -> Status:
            loaded = lock_fenced(session, _fence(claimed), ACTIVE_STATUSES)
            session.execute(
                update(attempt)
                .where(attempt.c.id == claimed.attempt_id, attempt.c.ended_at.is_(None))
                .values(lease_expires_at=loaded.now + self._config.lease)
            )
            if loaded.state.status is not Status.RUNNING:
                return loaded.state.status
            deadline = loaded.state.deadline_at
            if deadline is not None and loaded.now > deadline:
                applied = self._apply_event(session, loaded, DeadlineExceeded(loaded.now))
                return applied.state.status
            progress = {
                "progress": {"processed": processed, "total": len(claimed.input_upload_ids)}
            }
            session.execute(
                update(run)
                .where(
                    run.c.id == claimed.run_id,
                    run.c.generation == claimed.generation,
                    run.c.status == Status.RUNNING,
                )
                .values(summary=progress)
            )
            return Status.RUNNING

        return run_transaction(self._factory, renew)

    def reach_phase(self, claimed: ClaimedAttempt, phase: Phase) -> None:
        def reach(session: Session) -> None:
            self.reach_phase_in(session, claimed, phase)

        run_transaction(self._factory, reach)

    def reach_phase_in(self, session: Session, claimed: ClaimedAttempt, phase: Phase) -> None:
        loaded = lock_fenced(session, _fence(claimed), ACTIVE_STATUSES)
        if loaded.state.status is Status.STOPPING:
            raise _StopRequested
        self._apply_event(session, loaded, PhaseReached(claimed.generation, phase))

    def _processed_count(self, claimed: ClaimedAttempt) -> int:
        """Linhas de `progress.jsonl`, até o total de entradas.

        O arquivo é escrito pelo sandbox: não segue symlink (para `/dev/zero`, seria leitura sem
        fim), só lê arquivo regular (um FIFO travaria o worker) e lê no máximo um teto de bytes.
        """
        total = len(claimed.input_upload_ids)
        progress = self._runner.work_dir(claimed.attempt_id) / "progress.jsonl"
        try:
            fd = os.open(progress, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            # Ausente, symlink (ELOOP) ou ilegível: o progresso é informativo, conta zero.
            return 0
        with os.fdopen(fd, "rb") as lines:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return 0
            data = lines.read(PROGRESS_MAX_BYTES)
        return min(data.count(b"\n"), total)

    # 4. Desfecho -----------------------------------------------------------------------

    def finish(self, claimed: ClaimedAttempt, status: ExecutorStatus) -> Applied:
        self._record_executor(claimed, status)
        if status.oom_killed or status.timed_out or status.exit_code == EXIT_NO_SPACE:
            # 1.6: memória estourada, disco cheio e os 10 min da tentativa são limite de
            # recurso; repetir daria o mesmo resultado.
            return self._conclude(
                claimed,
                lambda _loaded: DeterministicFailure(
                    claimed.generation, ErrorCode.RESOURCE_LIMIT_EXCEEDED
                ),
                attempt_error=ErrorCode.RESOURCE_LIMIT_EXCEEDED,
            )
        if status.exit_code not in (EXIT_SUCCEEDED, EXIT_RECIPE_FAILURE):
            return self.fail_transiently(claimed)
        if status.exit_code == EXIT_RECIPE_FAILURE:
            code = self._failure_code(claimed)
            if code is None:
                return self.fail_transiently(claimed)
            return self._conclude(
                claimed,
                lambda _loaded: DeterministicFailure(claimed.generation, code),
                attempt_error=code,
            )
        try:
            return self._package_and_publish(claimed)
        except _StopRequested:
            return self.conclude_stop(claimed)

    def _record_executor(self, claimed: ClaimedAttempt, status: ExecutorStatus) -> None:
        attributes: dict[str, AttributeValue] = {
            "executor.oom_killed": status.oom_killed,
            "executor.timed_out": status.timed_out,
            "executor.enospc": status.exit_code == EXIT_NO_SPACE,
        }
        if status.exit_code is not None:
            attributes["executor.exit_code"] = status.exit_code
        timings_path = self._runner.work_dir(claimed.attempt_id) / attempt_event.TIMINGS_FILE
        attributes.update(attempt_event.read_executor_timings(timings_path))
        attempt_event.record(attributes)

    def _package_and_publish(self, claimed: ClaimedAttempt) -> Applied:
        self.reach_phase(claimed, Phase.PACKAGING)
        out_dir = self._runner.work_dir(claimed.attempt_id) / "out"
        keeper = self._lease_keeper(claimed)
        collect_started = time.monotonic()
        try:
            collected = collect_outputs(
                out_dir,
                len(claimed.input_upload_ids),
                recipe_id=claimed.recipe_id,
                heartbeat=keeper.beat,
            )
        except OutputLimitError as limit:
            code = limit.code
            return self._conclude(
                claimed,
                lambda _loaded: DeterministicFailure(claimed.generation, code),
                attempt_error=code,
            )
        except (CollectionError, OSError) as error:
            logger.warning("coleta recusada", extra={"fields": _ids(claimed, error)})
            return self.fail_transiently(claimed)
        try:
            stored = self.store_candidates(claimed, collected)
        except (StorageError, OSError) as error:
            logger.warning("candidatos não gravados", extra={"fields": _ids(claimed, error)})
            return self.fail_transiently(claimed)
        attempt_event.record(
            {"collect_ms": _elapsed_ms(collect_started), **_output_attributes(collected)}
        )
        self.reach_phase(claimed, Phase.FINALIZING)
        publish_started = time.monotonic()
        try:
            applied = self.publish(claimed, collected, stored)
        except ObjectNotAvailableError:
            # O dono excluiu uma entrada reaproveitada depois da gravação dos candidatos; a
            # retentativa a encontra `delete_requested` e grava a cópia (ADR 020).
            logger.warning("objeto reaproveitado indisponível", extra={"fields": _ids(claimed)})
            return self.fail_transiently(claimed)
        attempt_event.record({"publish_ms": _elapsed_ms(publish_started)})
        return applied

    def store_candidates(
        self, claimed: ClaimedAttempt, collected: Collected
    ) -> list[StoredCandidate]:
        """Grava as saídas como candidatos da geração, fora de transação (`CandidateWriter`).

        Cada transação trava a Run com fencing, e a cópia renova o lease a cada terço do prazo;
        parada pedida no meio levanta `_StopRequested`.
        """
        keeper = self._lease_keeper(claimed)
        return self._candidates.store(
            claimed,
            collected,
            lock_running=lambda session: self._lock_running(session, claimed),
            beating=keeper.beating,
        )

    def publish(
        self, claimed: ClaimedAttempt, collected: Collected, stored: list[StoredCandidate]
    ) -> Applied:
        report = collected.report

        def publish_outputs(session: Session) -> None:
            published: dict[str, uuid.UUID] = {}
            for position, candidate in enumerate(stored):
                source = None if candidate.preview_of is None else published[candidate.preview_of]
                artifact = publish_artifact(
                    session,
                    claimed.owner_id,
                    candidate.object_id,
                    candidate.role,
                    candidate.name,
                    source_artifact_id=source,
                )
                published[candidate.name] = artifact.id
                session.execute(
                    insert(run_output).values(
                        run_id=claimed.run_id,
                        position=position,
                        owner_id=claimed.owner_id,
                        artifact_id=artifact.id,
                        logical_name=candidate.name,
                    )
                )
            for item in report.items:
                session.execute(
                    update(run_input)
                    .where(
                        run_input.c.run_id == claimed.run_id, run_input.c.position == item.position
                    )
                    .values(
                        outcome="ok" if item.accepted else "rejected",
                        error_code=item.code,
                        details={
                            "warnings": [code.value for code in item.warnings],
                            "notices": [code.value for code in item.notices],
                            "counts": dict(item.counts),
                            "duplicate_of": item.duplicate_of,
                            "formula_cells": (
                                item.formula_cells.as_json()
                                if item.formula_cells is not None
                                else None
                            ),
                        },
                    )
                )

        return self._conclude(
            claimed,
            lambda loaded: Published(claimed.generation, report.outcome, loaded.now),
            attempt_error=None,
            publish=publish_outputs,
            summary=report.summary.as_json(),
        )

    def fail_transiently(self, claimed: ClaimedAttempt) -> Applied:
        return self._conclude(
            claimed,
            lambda loaded: TransientFailure(claimed.generation, loaded.now, self._config.jitter()),
            attempt_error=TRANSIENT_ATTEMPT_ERROR,
        )

    def conclude_stop(self, claimed: ClaimedAttempt) -> Applied:
        """O executor não roda mais e a Run está parando: confirma o término."""
        return self._conclude(
            claimed,
            lambda _loaded: ExecutorStopped(claimed.generation),
            attempt_error=None,
        )

    def _abandon(self, claimed: ClaimedAttempt) -> None:
        """Sem fencing, a tentativa não é mais desta instância: só o próprio executor é parado.

        Nada vai para o banco. O executor tem o nome desta Attempt, que nenhuma tentativa nova
        reutiliza, então pará-lo nunca atinge o trabalho de quem assumiu. Se o Runner falhar, a
        manutenção encontra a área da tentativa encerrada e termina o serviço.
        """
        stopped = self._try_stop(claimed)
        if stopped is not None and not stopped.is_running:
            self._try_remove(claimed)

    def remove_work(self, claimed: ClaimedAttempt) -> None:
        """Remove executor e área; chamado só depois de o desfecho estar no banco."""
        status = self._observe(claimed)
        if status is not None and not status.is_running:
            self._try_remove(claimed)

    def _try_remove(self, claimed: ClaimedAttempt) -> None:
        try:
            self._runner.remove(claimed.attempt_id)
        except RunnerError as error:
            logger.warning("área de trabalho não removida", extra={"fields": _ids(claimed, error)})

    def _conclude(
        self,
        claimed: ClaimedAttempt,
        event_for: Callable[[LoadedRun], Event],
        *,
        attempt_error: str | None,
        publish: Callable[[Session], None] | None = None,
        summary: Mapping[str, object] | None = None,
    ) -> Applied:
        """Aplica o desfecho com o executor já terminado, numa transação curta.

        Se a Run foi para `stopping` (cancelamento ou prazo persistidos antes), o desfecho vira
        a confirmação da parada, e os candidatos são descartados. Prazo vencido na publicação
        vira parada pelo prazo, confirmada na mesma transação.
        """

        def conclude(session: Session) -> Applied:
            loaded = lock_fenced(session, _fence(claimed), ACTIVE_STATUSES)
            if loaded.state.status is Status.STOPPING:
                return self._confirm_stopped(session, loaded, claimed)
            decision = decide_transition(loaded.state, event_for(loaded))
            if isinstance(decision, Refused):
                decision = _deadline_instead(loaded, decision)
            applied = apply_transition(
                session,
                loaded,
                decision,
                attempt_error=attempt_error,
                publish=publish,
                summary=summary,
            )
            if not applied.stop_requested:
                return applied
            stopped = self._confirm_stopped(
                session, lock_fenced(session, _fence(claimed), ACTIVE_STATUSES), claimed
            )
            return replace(stopped, discarded_keys=applied.discarded_keys + stopped.discarded_keys)

        return run_transaction(self._factory, conclude)

    def _confirm_stopped(
        self, session: Session, loaded: LoadedRun, claimed: ClaimedAttempt
    ) -> Applied:
        decision = decide_transition(loaded.state, ExecutorStopped(claimed.generation))
        if isinstance(decision, Refused):
            raise LostFencing(f"parada não confirmada: {decision.reason}")
        reason = loaded.state.stop_reason
        _record_stop(session, loaded, reason)
        return apply_transition(session, loaded, decision, attempt_error=reason)

    def _apply_event(self, session: Session, loaded: LoadedRun, event: Event) -> Applied:
        decision = decide_transition(loaded.state, event)
        if isinstance(decision, Refused):
            raise LostFencing(f"{type(event).__name__} recusado: {decision.reason}")
        return apply_transition(session, loaded, decision)

    def _lease_keeper(self, claimed: ClaimedAttempt) -> _LeaseKeeper:
        return _LeaseKeeper(lambda: self.renew_lease(claimed), self._config.renew_every)

    def _lock_running(self, session: Session, claimed: ClaimedAttempt) -> None:
        loaded = lock_fenced(session, _fence(claimed), ACTIVE_STATUSES)
        if loaded.state.status is Status.STOPPING:
            raise _StopRequested

    def _failure_code(self, claimed: ClaimedAttempt) -> ErrorCode | None:
        """Código de `failure.json`; fora do catálogo de falhas determinísticas → `None`."""
        path = self._runner.work_dir(claimed.attempt_id) / "failure.json"
        try:
            with path.open("rb") as source:
                document = json.loads(source.read(FAILURE_MAX_BYTES))
        except OSError, ValueError:
            return None
        code = document.get("code") if isinstance(document, dict) else None
        if code not in {c.value for c in DETERMINISTIC_CODES}:
            return None
        return ErrorCode(code)


def _create_for_executor(path: Path) -> BinaryIO:
    """Cria o arquivo novo, legível pelo grupo do executor (o modo não depende do umask)."""
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, WORKER_FILE_MODE), "wb")


def _deadline_instead(loaded: LoadedRun, refused: Refused) -> Transition:
    """Publicação recusada pelo prazo: o prazo vira a razão de parada (3.5)."""
    if refused.reason is not RefusalReason.DEADLINE_PASSED:
        raise LostFencing(f"desfecho recusado: {refused.reason}")
    decision = decide_transition(loaded.state, DeadlineExceeded(loaded.now))
    if isinstance(decision, Refused):
        raise LostFencing(f"prazo recusado: {decision.reason}")
    return decision


def _fence(claimed: ClaimedAttempt) -> Fence:
    return Fence(claimed.run_id, claimed.generation, claimed.attempt_id)


def _chunks(source: BinaryIO) -> Iterator[bytes]:
    while chunk := source.read(COPY_CHUNK_BYTES):
        yield chunk


def _ids(claimed: ClaimedAttempt, error: Exception | None = None) -> dict[str, object]:
    fields: dict[str, object] = {
        "run_id": str(claimed.run_id),
        "attempt_id": str(claimed.attempt_id),
        "generation": claimed.generation,
        "attempt.number": claimed.number,
    }
    if error is not None:
        fields["error"] = str(error)
    return fields


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def _output_attributes(collected: Collected) -> dict[str, AttributeValue]:
    """Quantidade e bytes das saídas de conteúdo e rejeições; nunca os nomes."""
    attributes: dict[str, AttributeValue] = {
        "attempt.outputs": len(collected.outputs),
        "attempt.output_bytes": sum(output.size for output in collected.outputs),
        "attempt.rejected": collected.report.summary.rejected,
    }
    # Um atributo por código: a coleta só aceita códigos do catálogo fechado `ItemRejectionCode`,
    # então a cardinalidade é a do catálogo.
    codes = Counter(item.code for item in collected.report.items if item.code is not None)
    for code, count in sorted(codes.items()):
        attributes[f"attempt.rejected.{code.value}"] = count
    return attributes


def _record_stop(session: Session, loaded: LoadedRun, reason: ErrorCode | None) -> None:
    """Razão da parada e, no cancelamento, a latência até esta confirmação (relógio do banco)."""
    if reason is None:
        return
    attributes: dict[str, AttributeValue] = {"attempt.stop_reason": reason.value}
    if reason is ErrorCode.CANCELED:
        requested_at = session.execute(
            select(run.c.cancel_requested_at).where(run.c.id == loaded.run_id)
        ).scalar_one()
        if requested_at is not None:
            attributes["attempt.cancel_latency_ms"] = (loaded.now - requested_at) // _MILLISECOND
    attempt_event.record(attributes)


def _record_end(started: float, outcome: str, *, error: str | None) -> None:
    attributes: dict[str, AttributeValue] = {
        "attempt.outcome": outcome,
        "duration_ms": _elapsed_ms(started),
    }
    if error is not None:
        attributes["error.code"] = error
    attempt_event.record(attributes)


def _log_attempt(
    claimed: ClaimedAttempt,
    outcome: str,
    started: float,
    *,
    error: ErrorCode | None = None,
    detail: str | None = None,
) -> None:
    fields = _ids(claimed)
    fields |= {
        "main": True,
        "outcome": outcome,
        "error.code": error,
        "duration_ms": round((time.monotonic() - started) * 1000),
    }
    if detail is not None:
        fields["detail"] = detail
    logger.info("tentativa encerrada", extra={"fields": fields})
