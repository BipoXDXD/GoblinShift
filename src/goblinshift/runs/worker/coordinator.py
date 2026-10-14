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
   afetar a linha, o motivo é relido e os candidatos são descartados.
5. `remove`: só depois de o desfecho estar no banco. Se o Runner falhar aqui, a área fica para a
   limpeza da manutenção.
"""

import hashlib
import json
import logging
import os
import random
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import insert, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.artifacts import commit_candidate, create_candidate_object, publish_artifact
from goblinshift.files.candidates import HeldContent, HeldContentMissingError, held_input_contents
from goblinshift.files.states import ArtifactRole
from goblinshift.files.storage import FileStorage, StorageError
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
    apply_transition,
    complete_after_commit,
    lock_fenced,
    run_transaction,
)
from goblinshift.runs.tables import attempt, run, run_input, run_output
from goblinshift.runs.worker.claim import ClaimedAttempt
from goblinshift.runs.worker.collection import (
    Collected,
    CollectionError,
    OutputFile,
    OutputLimitError,
    collect_outputs,
)
from goblinshift.runs.worker.runner import ExecutorStatus, Runner, RunnerError

logger = logging.getLogger(__name__)

EXIT_SUCCEEDED = 0
EXIT_RECIPE_FAILURE = 3
COPY_CHUNK_BYTES = 1024 * 1024
FAILURE_MAX_BYTES = 64 * 1024
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


@dataclass(frozen=True)
class StoredCandidate:
    name: str
    role: ArtifactRole
    object_id: uuid.UUID


class Coordinator:
    def __init__(
        self,
        factory: sessionmaker[Session],
        storage: FileStorage,
        runner: Runner,
        config: CoordinatorConfig,
    ) -> None:
        self._factory = factory
        self._storage = storage
        self._runner = runner
        self._config = config

    def run(self, claimed: ClaimedAttempt) -> Applied | None:
        """Leva a tentativa até um estado decidido; `None` se o fencing foi perdido."""
        started = time.monotonic()
        try:
            applied = self._drive(claimed)
        except LostFencing as lost:
            _log_attempt(claimed, "lost_fencing", started, detail=str(lost))
            self._abandon(claimed)
            return None
        complete_after_commit(self._factory, self._storage, applied)
        self.remove_work(claimed)
        _log_attempt(claimed, applied.state.status, started, error=applied.state.error_code)
        return applied

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
        # Só o manifesto usa a imagem; num arquivo à parte, o executor anterior à fatia 5, que
        # recusa chaves desconhecidas no job.json, continua aceitando o job (ADR 021).
        context = {"executor_image": claimed.executor_image}
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
        progress = self._runner.work_dir(claimed.attempt_id) / "progress.jsonl"
        try:
            with progress.open("rb") as lines:
                return sum(1 for line in lines if line.endswith(b"\n"))
        except FileNotFoundError:
            return 0

    # 4. Desfecho -----------------------------------------------------------------------

    def finish(self, claimed: ClaimedAttempt, status: ExecutorStatus) -> Applied:
        if status.oom_killed or status.timed_out:
            # 1.6: memória estourada e os 10 min da tentativa são limite de recurso; repetir
            # daria o mesmo resultado.
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

    def _package_and_publish(self, claimed: ClaimedAttempt) -> Applied:
        self.reach_phase(claimed, Phase.PACKAGING)
        out_dir = self._runner.work_dir(claimed.attempt_id) / "out"
        try:
            collected = collect_outputs(
                out_dir, len(claimed.input_upload_ids), recipe_id=claimed.recipe_id
            )
        except OutputLimitError as limit:
            code = limit.code
            return self._conclude(
                claimed,
                lambda _loaded: DeterministicFailure(claimed.generation, code),
                attempt_error=code,
            )
        except CollectionError as error:
            logger.warning("coleta recusada", extra={"fields": _ids(claimed, error)})
            return self.fail_transiently(claimed)
        stored = self.store_candidates(claimed, collected)
        self.reach_phase(claimed, Phase.FINALIZING)
        return self.publish(claimed, collected, stored)

    def store_candidates(
        self, claimed: ClaimedAttempt, collected: Collected
    ) -> list[StoredCandidate]:
        """Grava as saídas como candidatos em `runs/{run}/g{geração}/…`, fora de transação."""
        files: list[tuple[OutputFile, ArtifactRole]] = [
            *((output, ArtifactRole.OUTPUT) for output in collected.outputs),
            (collected.report_file, ArtifactRole.REPORT),
        ]

        def reserve(session: Session) -> list[tuple[uuid.UUID, str]]:
            self._lock_running(session, claimed)
            reserved = []
            for _ in files:
                candidate = create_candidate_object(
                    session,
                    claimed.owner_id,
                    claimed.run_id,
                    claimed.attempt_id,
                    claimed.generation,
                )
                reserved.append((candidate.object_id, candidate.storage_key))
            return reserved

        reserved = run_transaction(self._factory, reserve)
        written = []
        for (output, _role), (_object_id, key) in zip(files, reserved, strict=True):
            with output.path.open("rb") as source:
                written.append(self._storage.create_exclusive(key, _chunks(source), output.size))

        def confirm(session: Session) -> None:
            self._lock_running(session, claimed)
            for (object_id, _key), (sha256, size) in zip(reserved, written, strict=True):
                commit_candidate(session, claimed.owner_id, object_id, sha256, size)

        run_transaction(self._factory, confirm)
        return [
            StoredCandidate(output.name, role, object_id)
            for (output, role), (object_id, _key) in zip(files, reserved, strict=True)
        ]

    def publish(
        self, claimed: ClaimedAttempt, collected: Collected, stored: list[StoredCandidate]
    ) -> Applied:
        report = collected.report

        def publish_outputs(session: Session) -> None:
            for position, candidate in enumerate(stored):
                artifact = publish_artifact(
                    session, claimed.owner_id, candidate.object_id, candidate.role, candidate.name
                )
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
                    .values(outcome="ok" if item.accepted else "rejected", error_code=item.code)
                )

        return self._conclude(
            claimed,
            lambda loaded: Published(claimed.generation, report.outcome, loaded.now),
            attempt_error=None,
            publish=publish_outputs,
            summary=dict(report.summary),
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
        summary: dict[str, object] | None = None,
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
        return apply_transition(session, loaded, decision, attempt_error=reason)

    def _apply_event(self, session: Session, loaded: LoadedRun, event: Event) -> Applied:
        decision = decide_transition(loaded.state, event)
        if isinstance(decision, Refused):
            raise LostFencing(f"{type(event).__name__} recusado: {decision.reason}")
        return apply_transition(session, loaded, decision)

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
