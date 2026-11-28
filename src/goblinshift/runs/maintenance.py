"""Reconciliação e retenção, um ciclo por vez (docs/GoblinShiftPlan.md, 3.4, 3.5 e 1.7).

Cada passo escolhe candidatos sem trava e decide cada execução numa transação curta própria,
travando a Run com `FOR UPDATE SKIP LOCKED` e conferindo a geração lida (fencing): se um worker
vivo estiver escrevendo, a linha é pulada e volta no ciclo seguinte. Consultas e paradas do
Runner acontecem fora de transação.

Lease vencido não prova que o executor morreu: antes de reagendar, o Runner é consultado pelo
nome; executor vivo recebe `stop`, e só a confirmação do término libera a decisão. Sem
confirmação, ou com o Runner fora do ar (`executor-ctl` parado), a execução fica como está
(reserva física conservada) até o próximo ciclo: nada é reagendado sem ser inspecionado. O
`stop` só sai se a tentativa, relida numa transação curta e sem trava logo antes, ainda estiver
aberta e vencida. Lease vencido é definitivo: o worker não o renova (`LostFencing`), e o worker
pausado que volta abandona a tentativa em vez de disputá-la. A janela entre a releitura e o
`stop` encolhe, mas não some (ver `_lease_still_expired`).

O executor e a área de trabalho só são removidos depois de a decisão estar no banco. Se a remoção
falhar, a limpeza das áreas (`sweep_work_areas`) a repete nos ciclos seguintes: o `executor-ctl`
recusa iniciar com a área suja, então a área de uma tentativa encerrada não pode ficar para trás.
"""

import logging
import time
import uuid
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from functools import partial

from sqlalchemy import ColumnElement, and_, exists, func, or_, select
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session, sessionmaker

from goblinshift import capacity
from goblinshift.files import retention as file_retention
from goblinshift.files.candidates import attempts_with_loose_candidates, discard_candidates
from goblinshift.files.limits import EXPIRY_BATCH_SIZE, STALE_TEMPORARY_TTL
from goblinshift.files.missing import (
    DEFAULT_MAX_ABSENT_RATIO,
    MissingSweep,
    sweep_missing_objects,
)
from goblinshift.files.objects import holders_with_open_uses, release_expired_downloads
from goblinshift.files.retention import file_backlog, remove_file_after_commit
from goblinshift.files.states import HolderKind
from goblinshift.files.storage import FileStorage, ObjectTamperedError
from goblinshift.platform.telemetry import exception_fields
from goblinshift.runs import retention as run_retention
from goblinshift.runs.lifecycle import (
    TERMINAL_STATUSES,
    DeadlineExceeded,
    Event,
    ExecutorStopped,
    LeaseExpired,
    Phase,
    QueueExpired,
    Refused,
    Status,
    decide_transition,
)
from goblinshift.runs.retention import HISTORY_REFERENCES, history_backlog
from goblinshift.runs.store import (
    ACTIVE_STATUSES,
    Applied,
    LoadedRun,
    LostFencing,
    RunFinishedHook,
    apply_transition,
    complete_after_commit,
    db_now,
    load_run_any_owner,
    release_run_holds,
    run_transaction,
)
from goblinshift.runs.tables import attempt, run
from goblinshift.runs.worker.coordinator import TRANSIENT_ATTEMPT_ERROR, random_retry_jitter
from goblinshift.runs.worker.runner import STOP_GRACE, ExecutorStatus, Runner, RunnerError

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 50
# Quantos objetos ilegíveis (ou adulterados) vão nomeados no log do lote: com o disco inteiro
# devolvendo erro, a contagem por errno diz o resto sem uma linha por objeto a cada ciclo.
UNREADABLE_LOG_SAMPLE = 5
# Alerta de causa (3.7): Run em `stopping` há mais que isto, desde o pedido, sem confirmar a parada.
STUCK_STOPPING_AFTER = timedelta(minutes=10)
# Alerta de causa (#442, 21a): Run elegível há mais que isto sem nenhuma tentativa viva no banco.
# Com pouco tráfego, o SLO de execução não avalia, e a Run venceria em `QUEUE_EXPIRED` sem aviso.
QUEUE_STALLED_AFTER = timedelta(minutes=10)
# `run.finished` ao menos uma vez (D5 do #442): a Run terminal sem a marca de reportada é reemitida
# passada a carência, que deixa quem a concluiu emitir primeiro; mais velha que o horizonte, não
# volta, para uma falha que se repete não reemitir a mesma Run para sempre.
UNREPORTED_FINISH_GRACE = timedelta(minutes=5)
UNREPORTED_FINISH_HORIZON = timedelta(hours=24)
# Contagem do evento do ciclo: Runs cujo `run.finished` a varredura reemitiu.
FINISHES_REPORTED = "finishes_reported"
# Contagem do evento do ciclo: passos que levantaram exceção. O ciclo segue para os outros, e a
# volta conta como falha (`entrypoints/maintenance.run_cycles`, causa `gs-causa-ciclos-falhando`).
STEPS_FAILED = "steps_failed"
# Passos não tentados na volta: os de banco depois de uma falha de conexão e os que a parada
# pedida deixou para trás.
STEPS_SKIPPED = "steps_skipped"
# Contagens dos usos órfãos encerrados na volta, uma por tipo de detentor: execuções terminais
# (nome anterior aos downloads, mantido com o mesmo significado) e downloads além do lease.
RUN_HOLDS_RELEASED = "holds_released"
DOWNLOAD_HOLDS_RELEASED = "download_holds_released"
# SQLSTATE de conexão (classe 08) e de servidor parando (admin_shutdown, crash_shutdown,
# cannot_connect_now): o banco não vai responder ao passo seguinte desta volta.
_CONNECTION_EXCEPTION_CLASS = "08"
_SERVER_STOPPING_SQLSTATES = frozenset({"57P01", "57P02", "57P03"})


@dataclass(frozen=True)
class MaintenanceConfig:
    batch_size: int = DEFAULT_BATCH_SIZE
    # Lote de cada etapa da retenção longa (1.7): linhas simples, transação curta.
    retention_batch_size: int = EXPIRY_BATCH_SIZE
    # Objetos `committed` conferidos no disco por ciclo (um `stat` cada), em rodízio pelo ID.
    missing_sweep_batch_size: int = EXPIRY_BATCH_SIZE
    # Fração do lote ausente acima da qual nada é marcado (files/missing.py); 1.0 desliga o
    # limitador, para o administrador depois de restaurar de propósito um backup antigo (3.8).
    missing_max_absent_ratio: float = DEFAULT_MAX_ABSENT_RATIO
    stop_grace: timedelta = STOP_GRACE
    jitter: Callable[[], timedelta] = field(default=random_retry_jitter)


def _never() -> bool:
    return False


def _always() -> bool:
    return True


@dataclass(frozen=True)
class _Step:
    name: str
    run: Callable[[], Mapping[str, int]]
    uses_database: bool = True


class _StepFailedWithCountsError(Exception):
    """Passo que falhou depois de confirmar parte do trabalho, ou que isola a própria falha mas
    perdeu a conexão: o ciclo precisa saber das duas coisas.

    Leva as contagens do que já foi confirmado (ou da falha, para o medidor do alerta) e o erro
    original. `_CycleOutcome.run` aplica as contagens e trata o passo como falho, igual a qualquer
    passo que levanta esse erro: perda de conexão ainda pula os passos de banco restantes.
    """

    def __init__(self, error: Exception, counts: Mapping[str, int]) -> None:
        super().__init__(type(error).__name__)
        self.error = error
        self.counts = counts


@dataclass
class _CycleOutcome:
    """O que a volta acumulou: contagens dos passos que rodaram, falhas e passos pulados."""

    counts: dict[str, int] = field(default_factory=dict)
    failed: int = 0
    connection_lost_in: str | None = None
    skipped_after_connection: list[str] = field(default_factory=list)
    skipped_for_stop: list[str] = field(default_factory=list)

    def run(self, step: _Step) -> None:
        try:
            self.counts.update(step.run())
        except _StepFailedWithCountsError as lost:
            self.counts.update(lost.counts)
            self._record_failure(step, lost.error)
        except Exception as error:
            # Fronteira do passo: o seguinte recomeça do banco, sem estado em memória.
            self._record_failure(step, error)

    def _record_failure(self, step: _Step, error: Exception) -> None:
        log_step_failure(step.name, error)
        self.failed += 1
        if self.connection_lost_in is None and is_connection_error(error):
            self.connection_lost_in = step.name

    def finish(self) -> dict[str, int]:
        """As contagens da volta, com falhas e pulos; os pulos vão ao log, uma linha por motivo."""
        self.counts[STEPS_FAILED] = self.failed
        self.counts[STEPS_SKIPPED] = len(self.skipped_after_connection) + len(self.skipped_for_stop)
        if self.skipped_after_connection:
            fields = {
                "failed_step": self.connection_lost_in,
                "skipped": self.skipped_after_connection,
            }
            logger.warning(
                "banco inalcançável; passos de banco pulados nesta volta",
                extra={"fields": fields},
            )
        if self.skipped_for_stop:
            logger.info(
                "parada pedida; passos restantes pulados",
                extra={"fields": {"skipped": self.skipped_for_stop}},
            )
        return self.counts


def _log_items_left(step: str, items_left: int) -> None:
    """A parada pedida cortou o lote do passo; os itens restantes voltam no próximo processo."""
    logger.info(
        "parada pedida; itens restantes do passo pulados",
        extra={"fields": {"step": step, "items_left": items_left}},
    )


def is_connection_error(error: Exception) -> bool:
    """Erro de conexão: repetir o banco noutro passo só esperaria o `connect_timeout` de novo.

    Conexão que o pool invalidou, pool esgotado (`TimeoutError` do SQLAlchemy, que não é
    `DBAPIError`: o pool desiste antes de pedir conexão, e o passo seguinte esperaria o
    `pool_timeout` de novo), ou `OperationalError` sem SQLSTATE (o `connect` recusado ou sem
    resposta) ou com SQLSTATE de conexão ou de servidor parando. `statement_timeout` (57014)
    também chega como `OperationalError`, mas o banco respondeu: os outros passos seguem.
    """
    if isinstance(error, PoolTimeoutError):
        return True
    if isinstance(error, DBAPIError) and error.connection_invalidated:
        return True
    if not isinstance(error, OperationalError):
        return False
    sqlstate: str | None = getattr(error.orig, "sqlstate", None)
    return (
        sqlstate is None
        or sqlstate.startswith(_CONNECTION_EXCEPTION_CLASS)
        or sqlstate in _SERVER_STOPPING_SQLSTATES
    )


@dataclass(frozen=True)
class _ExpiredAttempt:
    run_id: uuid.UUID
    generation: int
    attempt_id: uuid.UUID


class Maintenance:
    def __init__(
        self,
        factory: sessionmaker[Session],
        storage: FileStorage,
        runner: Runner,
        config: MaintenanceConfig = MaintenanceConfig(),  # noqa: B008 - dataclass imutável
        *,
        on_finished: RunFinishedHook | None = None,
    ) -> None:
        self._factory = factory
        self._storage = storage
        self._runner = runner
        self._config = config
        self._on_finished = on_finished
        # Onde a varredura de ausentes parou. Só em memória: reiniciar o processo recomeça do
        # primeiro objeto, o que custa uma volta a mais e nada mais.
        self._missing_resume_after: uuid.UUID | None = None
        # Onde a varredura das áreas de trabalho parou. Só em memória: evita que áreas vivas ou
        # com falha no início do lote escondam as seguintes; ao passar do fim, volta ao início.
        self._work_resume_after: uuid.UUID | None = None
        # Candidatos que ficaram no disco no ciclo em curso. Em atributo porque vêm de vários
        # passos, por `_decide`, cujo retorno já é a transição.
        self._candidates_left_on_disk = 0
        # Arquivos que a retenção tirou de uso e não conseguiu remover do disco no ciclo em curso.
        # Em atributo porque vêm de dois passos (uploads incompletos e expurgo): a contagem de um
        # não some se o outro falhar. Com o banco já confirmado, nada repete a remoção: o alerta de
        # causa lê isto (3.7).
        self._files_left_on_disk = 0

    def run_cycle(self, should_stop: Callable[[], bool] = _never) -> dict[str, int]:
        """Um ciclo completo; devolve as contagens do evento amplo da manutenção (3.7).

        Passos independentes: a exceção de um fica no log e em `steps_failed`, e o ciclo segue
        para prazo, retenção, expurgo e medidores; as contagens do passo que falhou ficam fora do
        evento, exceto as que o passo leva em `_StepFailedWithCountsError`: a varredura de
        ausentes, mesmo na perda de conexão, e os downloads já encerrados pelos usos órfãos.
        Cada sub-etapa da retenção é um passo: um `statement_timeout` que se repete numa delas a
        cada ciclo não leva as outras junto. Pelo mesmo isolamento, a reconciliação de capacidade
        roda mesmo com o expurgo falho; quando ele conclui, ela já vê o que ele liberou.
        Falha de conexão (banco inalcançável) pula os passos de banco restantes da volta, que só
        esperariam o `connect_timeout` de novo, mas não a limpeza de temporários, que só usa o
        storage; `should_stop` (a parada pedida por sinal) é conferido antes de cada passo. Os
        dois contam em `steps_skipped`. Os passos que chamam o Runner item a item também conferem
        a parada entre os itens: o item em curso termina, e o
        seguinte fica para o próximo processo. Esse item em curso pode, sozinho, passar da carência
        da plataforma até o SIGKILL, que então leva o evento do ciclo: o limite é de um item, não
        de tempo. Interrupção do processo (`KeyboardInterrupt`, `SystemExit`) não é `Exception` e
        sobe.
        """
        started = time.monotonic()
        self._candidates_left_on_disk = 0
        self._files_left_on_disk = 0
        outcome = _CycleOutcome()
        for step in self._cycle_steps(should_stop):
            if should_stop():
                outcome.skipped_for_stop.append(step.name)
            elif outcome.connection_lost_in is not None and step.uses_database:
                outcome.skipped_after_connection.append(step.name)
            else:
                outcome.run(step)
        counts = outcome.finish()
        counts["candidates_left_on_disk"] = self._candidates_left_on_disk
        counts["purge_files_not_removed"] = self._files_left_on_disk
        fields: dict[str, object] = {"main": True, **counts}
        fields["duration_ms"] = round((time.monotonic() - started) * 1000)
        logger.info("ciclo de manutenção", extra={"fields": fields})
        return counts

    def _cycle_steps(self, should_stop: Callable[[], bool]) -> tuple[_Step, ...]:
        """Os passos do ciclo, na ordem: reconciliação, fila, prazo, usos, retenção e medidores."""
        return (
            _Step(
                "reconcile_expired_leases",
                lambda: {
                    "leases_reconciled": self.reconcile_expired_leases(should_stop=should_stop)
                },
            ),
            _Step("expire_queue", lambda: {"queue_expired": self.expire_queue()}),
            _Step("apply_deadlines", lambda: {"deadlines": self.apply_deadlines()}),
            _Step("release_orphan_holds", self.release_orphan_holds),
            _Step("report_unreported_finishes", self.report_unreported_finishes),
            _Step("discard_stale_candidates", self.discard_stale_candidates),
            _Step(
                "sweep_work_areas",
                lambda: {"work_areas_removed": self.sweep_work_areas(should_stop=should_stop)},
            ),
            # Retenção (1.7), uma sub-etapa por passo.
            _Step("expire_incomplete_uploads", self.expire_incomplete_uploads),
            _Step("expire_unused_uploads", self.expire_unused_uploads),
            _Step("expire_artifacts", self.expire_artifacts),
            _Step("purge_deleted_objects", self.purge_deleted_objects),
            # Depois do expurgo: o que acabou de ser liberado já sai das somas dos pools.
            _Step("reconcile_capacity", self.reconcile_capacity),
            # A ordem importa: a Run sai antes das linhas de arquivos que o histórico dela
            # referencia, e as linhas de arquivos só saem depois do arquivo no disco.
            _Step("delete_expired_idempotency_records", self.delete_expired_idempotency_records),
            _Step("forget_upload_request_keys", self.forget_upload_request_keys),
            _Step("anonymize_finished_runs", self.anonymize_finished_runs),
            _Step("anonymize_output_names", self.anonymize_output_names),
            _Step("anonymize_deleted_uploads", self.anonymize_deleted_uploads),
            _Step("delete_old_runs", self.delete_old_runs),
            _Step("delete_old_file_rows", self.delete_old_file_rows),
            _Step("delete_released_reservations", self.delete_released_reservations),
            # Só o storage: segue mesmo com o banco fora.
            _Step("sweep_temporary_parts", self.sweep_temporary_parts, uses_database=False),
            _Step("mark_missing_objects", self.mark_missing_objects),
            _Step("measure", self.measure),
        )

    # Lease vencido e parada ------------------------------------------------------------

    def reconcile_expired_leases(self, *, should_stop: Callable[[], bool] = _never) -> int:
        """Tentativas com lease vencido: confirma o término do executor e decide.

        `should_stop` é conferido antes de cada tentativa, nunca no meio dela: uma tentativa
        começada vai até a decisão e o `remove`.
        """
        reconciled = 0
        batch = self._expired_attempts()
        for position, expired in enumerate(batch):
            if should_stop():
                _log_items_left("reconcile_expired_leases", len(batch) - position)
                break
            status = self._confirm_ended(
                expired.attempt_id,
                expired.run_id,
                may_stop=partial(self._may_stop, expired),
            )
            if status is None:
                continue
            applied = self._decide(
                expired.run_id,
                partial(self._lease_event, expired=expired, status=status),
            )
            if applied is not None:
                self._try_remove(expired.attempt_id)
                reconciled += 1
        return reconciled

    def _confirm_ended(
        self,
        attempt_id: uuid.UUID,
        run_id: uuid.UUID | None,
        may_stop: Callable[[], bool] = _always,
    ) -> ExecutorStatus | None:
        """Estado final do executor, parando-o se vivo; `None` se o fim não foi confirmado.

        `may_stop` é conferido entre o `status` e o `stop`, sem trava segurada durante as
        chamadas ao Runner: falso deixa o executor vivo e a execução para o próximo ciclo.
        """
        fields = {"attempt_id": str(attempt_id), "run_id": str(run_id) if run_id else None}
        try:
            status = self._runner.status(attempt_id)
            if status.is_running:
                if not may_stop():
                    logger.info(
                        "lease não confirmado vencido na releitura; executor não é parado",
                        extra={"fields": fields},
                    )
                    return None
                status = self._runner.stop(attempt_id, self._config.stop_grace)
        except RunnerError as error:
            logger.warning(
                "executor sem inspeção; execução fica para o próximo ciclo",
                extra={"fields": {**fields, **exception_fields(error)}},
            )
            return None
        if status.is_running:
            logger.warning(
                "executor não confirmou a parada; execução segue em reconciliação",
                extra={"fields": fields},
            )
            return None
        return status

    def _try_remove(self, attempt_id: uuid.UUID) -> bool:
        try:
            self._runner.remove(attempt_id)
        except RunnerError as error:
            logger.warning(
                "área de trabalho não removida; a limpeza tenta de novo",
                extra={"fields": {"attempt_id": str(attempt_id), **exception_fields(error)}},
            )
            return False
        return True

    def _expired_attempts(self) -> list[_ExpiredAttempt]:
        with self._factory() as session, session.begin():
            rows = session.execute(
                select(run.c.id, run.c.generation, attempt.c.id.label("attempt_id"))
                .join(attempt, attempt.c.run_id == run.c.id)
                .where(_lease_expired())
                .order_by(attempt.c.lease_expires_at)
                .limit(self._config.batch_size)
            ).all()
        return [_ExpiredAttempt(row.id, row.generation, row.attempt_id) for row in rows]

    def _may_stop(self, expired: _ExpiredAttempt) -> bool:
        """A releitura isolada por tentativa: erro de banco nela é "não parar neste ciclo".

        O erro fica no log com tipo e SQLSTATE (a mensagem do banco traz host e valores, 3.7) e
        o lote segue para a próxima tentativa. Erro de conexão sobe: o passo falha e o ciclo pula
        os passos de banco restantes, que só esperariam o `connect_timeout` de novo. O
        `TimeoutError` do pool não é `DBAPIError`, então o `except` abaixo nem o vê e ele sobe do
        mesmo jeito, que é o que `is_connection_error` pede: não há o que isolar sem conexão.
        """
        try:
            return self._lease_still_expired(expired)
        except DBAPIError as error:
            if is_connection_error(error):
                raise
            fields = {
                "attempt_id": str(expired.attempt_id),
                "run_id": str(expired.run_id),
                "error_type": type(error).__name__,
                "sqlstate": getattr(error.orig, "sqlstate", None),
            }
            logger.warning(
                "releitura do lease falhou; executor não é parado neste ciclo",
                extra={"fields": fields},
            )
            return False

    def _lease_still_expired(self, expired: _ExpiredAttempt) -> bool:
        """Relê a tentativa antes do `stop`: ainda aberta, de Run ativa e com o lease vencido.

        Leitura simples, sem `FOR UPDATE`, numa transação que termina antes da chamada ao
        Runner. Lease vencido não se renova (`renew_lease` levanta `LostFencing`), então o
        worker não desfaz esta releitura renovando. Ela ainda pode ficar falsa antes do `stop`:
        outra manutenção decide a tentativa, ou o worker grava o desfecho de um executor que
        terminou sozinho (`Coordinator.supervise` devolve o fim sem renovar).
        Nos dois casos o `stop` cai num executor já encerrado, e o fencing de `_lease_event`
        recusa decidir de novo.

        A geração não é conferida: ela só sobe no claim, que exige a Run em `queued` ou
        `retry_wait`, e só se chega a `retry_wait` encerrando a tentativa viva; com o índice
        `attempt_one_alive_per_run_idx` (uma viva por Run), tentativa ainda aberta implica a
        geração lida.
        """
        with self._factory() as session, session.begin():
            found = session.execute(
                select(attempt.c.id)
                .join(run, attempt.c.run_id == run.c.id)
                .where(_lease_expired(), attempt.c.id == expired.attempt_id)
            ).first()
        return found is not None

    def _lease_event(
        self, loaded: LoadedRun, expired: _ExpiredAttempt, status: ExecutorStatus
    ) -> Event | None:
        live = loaded.live_attempt
        same_attempt = live is not None and live.id == expired.attempt_id
        if loaded.state.generation != expired.generation or not same_attempt:
            return None
        if loaded.state.status is Status.STOPPING:
            return ExecutorStopped(expired.generation)
        # Defesa: `renew_lease` não renova lease vencido; se este não estiver vencido, não é nosso.
        if live is None or live.lease_expires_at >= loaded.now:
            return None
        return LeaseExpired(expired.generation, loaded.now, status.ran, self._config.jitter())

    # Fila e prazo ----------------------------------------------------------------------

    def expire_queue(self) -> int:
        ids = self._select_ids(
            (run.c.status == Status.QUEUED) & (run.c.queue_expires_at < func.now())
        )
        return sum(
            self._decide(run_id, lambda loaded: QueueExpired(loaded.now)) is not None
            for run_id in ids
        )

    def apply_deadlines(self) -> int:
        """Prazo vencido: sem executor vivo termina; com executor, vai para `stopping`.

        A parada física fica com o coordenador vivo (que lê `stopping` ao renovar o lease) ou,
        se ele sumiu, com a reconciliação de lease vencido.
        """
        ids = self._select_ids(
            run.c.status.in_([Status.RUNNING]) & (run.c.deadline_at < func.now())
        )
        return sum(
            self._decide(run_id, lambda loaded: DeadlineExceeded(loaded.now)) is not None
            for run_id in ids
        )

    # Usos e candidatos -----------------------------------------------------------------

    def release_orphan_holds(self) -> dict[str, int]:
        """Usos órfãos: de downloads que passaram do lease e de execuções terminais.

        No download, a API caiu antes de `finish_download`; na execução, a transação seguinte ao
        término não chegou. Cada tipo de detentor tem a sua contagem: `holds_released` segue só
        com as execuções. Os downloads são encerrados numa transação própria, confirmada antes da
        parte das execuções: se esta falhar, o passo sobe como `_StepFailedWithCountsError` com a
        contagem dos downloads, e o ciclo a mantém no evento.
        """
        downloads = run_transaction(
            self._factory,
            partial(release_expired_downloads, limit=self._config.batch_size),
        )
        try:
            runs = self._release_terminal_run_holds()
        except Exception as error:
            raise _StepFailedWithCountsError(error, {DOWNLOAD_HOLDS_RELEASED: downloads}) from error
        return {RUN_HOLDS_RELEASED: runs, DOWNLOAD_HOLDS_RELEASED: downloads}

    def _release_terminal_run_holds(self) -> int:
        """Libera os usos das execuções já terminais; devolve quantas execuções."""
        with self._factory() as session, session.begin():
            holders = holders_with_open_uses(session, HolderKind.RUN, self._config.batch_size)
            terminal = session.execute(
                select(run.c.id, run.c.generation).where(
                    run.c.id.in_(holders), run.c.status.in_(TERMINAL_STATUSES)
                )
            ).all()
        for row in terminal:
            run_transaction(
                self._factory,
                partial(release_run_holds, run_id=row.id, generation=row.generation),
            )
        return len(terminal)

    def report_unreported_finishes(self) -> dict[str, int]:
        """Reemite o `run.finished` das Runs terminais que quem as concluiu não reportou.

        Só as terminadas entre a carência e o horizonte, as mais antigas primeiro, até um lote. O
        reporter grava a marca depois de emitir e engole a própria falha: a Run sem marca volta no
        ciclo seguinte. Sem `on_finished` (manutenção sem telemetria), não há o que emitir.
        """
        on_finished = self._on_finished
        if on_finished is None:
            return {FINISHES_REPORTED: 0}
        batch = self._config.batch_size
        ids = self._at_db_now(lambda s, now: _unreported_finishes(s, now, batch))
        for run_id in ids:
            on_finished(run_id)
        return {FINISHES_REPORTED: len(ids)}

    def discard_stale_candidates(self) -> dict[str, int]:
        """Candidatos de tentativas encerradas que não viraram artefato (gerações vencidas)."""
        with self._factory() as session, session.begin():
            loose = attempts_with_loose_candidates(session, self._config.batch_size)
            ended = session.execute(
                select(attempt.c.id, attempt.c.run_id).where(
                    attempt.c.id.in_(loose), attempt.c.ended_at.is_not(None)
                )
            ).all()
        discarded = 0
        for row in ended:
            keys = run_transaction(
                self._factory,
                partial(_discard_locked, run_id=row.run_id, attempt_id=row.id),
            )
            if keys is None:
                continue
            for key in keys:
                if not remove_file_after_commit(self._storage, key):
                    self._candidates_left_on_disk += 1
            discarded += 1
        return {"candidates_discarded": discarded}

    # Áreas de trabalho ------------------------------------------------------------------

    def sweep_work_areas(self, *, should_stop: Callable[[], bool] = _never) -> int:
        """Remove executor e área de tentativas encerradas (ou sem registro) que ficaram para trás.

        A tentativa viva (`ended_at` nulo) é do worker ou da reconciliação de lease: não é tocada.
        Avança por cursor em ordem de UUID para que áreas presas ou vivas não travem as seguintes;
        `should_stop` é conferido antes de cada área, nunca no meio dela, e áreas não examinadas
        na interrupção não avançam o cursor.
        """
        try:
            present = sorted(self._runner.list_work())
        except OSError as error:
            logger.warning("área de trabalho ilegível", extra={"fields": exception_fields(error)})
            return 0
        remaining = [
            attempt_id
            for attempt_id in present
            if self._work_resume_after is None or attempt_id > self._work_resume_after
        ]
        if not remaining:
            self._work_resume_after = None
            return 0
        window = remaining[: self._config.batch_size]
        if not window:
            return 0
        live = self._live_attempts(window)
        left_behind_remaining = sum(1 for attempt_id in window if attempt_id not in live)
        removed = 0
        last_examined: uuid.UUID | None = None
        stopped = False
        for attempt_id in window:
            if attempt_id in live:
                last_examined = attempt_id
                continue
            if should_stop():
                _log_items_left("sweep_work_areas", left_behind_remaining)
                stopped = True
                break
            left_behind_remaining -= 1
            status = self._confirm_ended(attempt_id, run_id=None)
            if status is not None and self._try_remove(attempt_id):
                removed += 1
            last_examined = attempt_id
        if stopped:
            if last_examined is not None:
                self._work_resume_after = last_examined
        elif len(remaining) > len(window):
            self._work_resume_after = window[-1]
        else:
            self._work_resume_after = None
        return removed

    def _live_attempts(self, present: list[uuid.UUID]) -> set[uuid.UUID]:
        """Das tentativas com área no disco, as ainda abertas (`ended_at` nulo)."""
        with self._factory() as session, session.begin():
            return set(
                session.execute(
                    select(attempt.c.id).where(
                        attempt.c.id.in_(present), attempt.c.ended_at.is_(None)
                    )
                ).scalars()
            )

    # Retenção --------------------------------------------------------------------------

    def expire_incomplete_uploads(self) -> dict[str, int]:
        with self._factory() as session:
            incomplete = file_retention.expire_incomplete_uploads(session, self._storage)
        self._files_left_on_disk += len(incomplete.failed_keys)
        return {"incomplete_uploads_expired": incomplete.uploads}

    def expire_unused_uploads(self) -> dict[str, int]:
        with self._factory() as session:
            return {"unused_uploads_expired": file_retention.expire_unused_uploads(session)}

    def expire_artifacts(self) -> dict[str, int]:
        with self._factory() as session:
            return {"artifacts_expired": file_retention.expire_artifacts(session)}

    def purge_deleted_objects(self) -> dict[str, int]:
        with self._factory() as session:
            report = file_retention.purge_deleted_objects(session, self._storage)
        if report.limit_reached:
            # Alerta de causa (3.7): o limitador cortou o expurgo; o administrador investiga.
            logger.warning(
                "limitador de exclusões acionado",
                extra={"fields": {"alert": "purge_governor", "objects": report.objects}},
            )
        if report.failed_keys:
            logger.warning(
                "arquivos não removidos do disco",
                extra={"fields": {"failed": len(report.failed_keys)}},
            )
        self._files_left_on_disk += len(report.failed_keys)
        return {
            "objects_purged": report.objects,
            "bytes_purged": report.bytes,
            "purge_limit_reached": int(report.limit_reached),
        }

    def reconcile_capacity(self) -> dict[str, int]:
        with self._factory() as session:
            reconciled = file_retention.reconcile_capacity(session, limit=self._config.batch_size)
        return {
            "capacity_pools_corrected": reconciled.pools_corrected,
            "capacity_objects_adopted": reconciled.objects_adopted,
        }

    def sweep_temporary_parts(self) -> dict[str, int]:
        """Apaga os temporários que uma queda deixou no storage e mede o que ficou no diretório.

        Eles ficam fora do livro-razão e do `file_backlog`. Isolada do resto do ciclo, como a
        varredura de ausentes: uma falha de disco, no diretório ou em um arquivo, fica no log e
        em `tmp_parts_sweep_failed`, assim como um `.tmp` trocado por symlink ou arquivo.
        """
        # O `mtime` vem do relógio desta máquina, e não do banco: é com ele que se compara.
        modified_before = time.time() - STALE_TEMPORARY_TTL.total_seconds()
        try:
            sweep = self._storage.sweep_stale_temporaries(
                modified_before, self._config.retention_batch_size
            )
        except (OSError, ObjectTamperedError) as error:
            logger.exception(
                "limpeza de temporários falhou", extra={"fields": exception_fields(error)}
            )
            return {
                "tmp_parts_removed": 0,
                "tmp_parts": 0,
                "tmp_parts_bytes": 0,
                "tmp_parts_sweep_failed": 1,
            }
        return {
            "tmp_parts_removed": sweep.removed,
            "tmp_parts": sweep.remaining,
            "tmp_parts_bytes": sweep.remaining_bytes,
            "tmp_parts_sweep_failed": int(sweep.failed > 0),
        }

    def mark_missing_objects(self) -> dict[str, int]:
        """Um lote da varredura de objetos sem arquivo no disco (1.7, 3.8: `missing`).

        Isolada do resto do ciclo: uma falha dela (disco, banco) fica no log e na contagem
        `missing_sweep_failed`, e a retenção e os medidores seguem. A reconciliação de capacidade
        do ciclo seguinte já vê as reservas liberadas aqui. Perda de conexão tem o mesmo log e a
        mesma contagem, e além disso sobe como `_StepFailedWithCountsError`: o ciclo conta o
        passo em `steps_failed` e pula os passos de banco restantes, que só esperariam o timeout de
        novo. Chamada fora do ciclo, ela levanta nesse caso.
        """
        try:
            with self._factory() as session:
                sweep = sweep_missing_objects(
                    session,
                    self._storage,
                    after=self._missing_resume_after,
                    limit=self._config.missing_sweep_batch_size,
                    max_absent_ratio=self._config.missing_max_absent_ratio,
                )
        except Exception as error:
            # Não engole: registra com o traceback e conta; o cursor fica, e o lote volta.
            logger.exception(
                "varredura de ausentes falhou",
                extra={"fields": {"alert": "missing_sweep_failed"}},
            )
            failed = {**_sweep_counts(MissingSweep.idle()), "missing_sweep_failed": 1}
            if is_connection_error(error):
                raise _StepFailedWithCountsError(error, failed) from error
            return failed
        self._missing_resume_after = sweep.resume_after
        self._report_sweep(sweep)
        return {**_sweep_counts(sweep), "missing_sweep_failed": 0}

    def _report_sweep(self, sweep: MissingSweep) -> None:
        if sweep.unreadable:
            # Nada é marcado por erro de disco: o errno e a chave só ficam aqui.
            logger.warning(
                "objetos ilegíveis no storage",
                extra={"fields": _unreadable_fields(sweep)},
            )
        if sweep.tampered:
            # Symlink ou diretório no lugar do objeto (ADR 054): o mesmo sinal da entrada
            # adulterada do worker, então o mesmo evento e o mesmo alerta. Nada é marcado nem
            # tocado; só a chave lógica sai, nunca o caminho no disco nem o alvo do link.
            logger.warning(
                "objetos adulterados no storage",
                extra={"fields": _tampered_fields(sweep)},
            )
        if sweep.storage_unreachable:
            # Sem a sentinela de montagem: filesystem do storage desmontado ou trocado.
            logger.warning(
                "storage sem a sentinela de montagem; varredura de ausentes suspensa",
                extra={"fields": {"alert": "storage_unreachable"}},
            )
        if sweep.governor_tripped:
            # Ausentes demais num lote: storage errado ou backup antigo restaurado (3.8); o
            # administrador confere antes de liberar (`missing_max_absent_ratio`).
            logger.warning(
                "limitador de ausentes acionado; nada marcado",
                extra={
                    "fields": {
                        "alert": "missing_governor",
                        "checked": sweep.checked,
                        "absent": sweep.absent,
                        "unreadable": sweep.unreadable,
                    }
                },
            )
        if sweep.marked:
            # Esperado depois de restaurar o banco de um backup (3.8); fora disso, investigar.
            logger.warning(
                "objetos ausentes do storage marcados missing",
                extra={"fields": {"alert": "objects_missing", "objects": sweep.marked}},
            )

    # Retenção longa (1.7): cada sub-etapa é um lote numa transação própria, no `now` do banco.

    def delete_expired_idempotency_records(self) -> dict[str, int]:
        batch = self._config.retention_batch_size
        removed = self._at_db_now(
            lambda s, now: run_retention.delete_expired_idempotency_records(s, now, batch)
        )
        return {"idempotency_records_removed": removed}

    def forget_upload_request_keys(self) -> dict[str, int]:
        batch = self._config.retention_batch_size
        forgotten = self._at_db_now(
            lambda s, now: file_retention.forget_upload_request_keys(s, now, batch)
        )
        return {"upload_request_keys_forgotten": forgotten}

    def anonymize_finished_runs(self) -> dict[str, int]:
        batch = self._config.retention_batch_size
        anonymized = self._at_db_now(
            lambda s, now: run_retention.anonymize_finished_runs(s, now, batch)
        )
        return {"runs_anonymized": anonymized}

    def anonymize_output_names(self) -> dict[str, int]:
        batch = self._config.retention_batch_size
        anonymized = self._at_db_now(
            lambda s, now: run_retention.anonymize_output_names(s, now, batch)
        )
        return {"outputs_anonymized": anonymized}

    def anonymize_deleted_uploads(self) -> dict[str, int]:
        batch = self._config.retention_batch_size
        anonymized = self._at_db_now(
            lambda s, now: file_retention.anonymize_deleted_uploads(s, now, batch)
        )
        return {"uploads_anonymized": anonymized}

    def delete_old_runs(self) -> dict[str, int]:
        batch = self._config.retention_batch_size
        deleted = self._at_db_now(lambda s, now: run_retention.delete_old_runs(s, now, batch))
        return {"runs_deleted": deleted}

    def delete_old_file_rows(self) -> dict[str, int]:
        """As linhas de arquivos que o histórico já não referencia, depois do arquivo no disco."""
        batch = self._config.retention_batch_size
        deleted = self._at_db_now(
            lambda s, now: file_retention.delete_old_file_rows(s, now, batch, HISTORY_REFERENCES)
        )
        return {"file_rows_deleted": deleted}

    def delete_released_reservations(self) -> dict[str, int]:
        batch = self._config.retention_batch_size
        removed = self._at_db_now(
            lambda s, now: capacity.delete_released_reservations(s, now, batch)
        )
        return {"reservations_removed": removed}

    def measure(self) -> dict[str, int]:
        """Medidores do evento do ciclo: o tamanho do que acumula e o que os alertas leem."""
        with self._factory() as session, session.begin():
            now = db_now(session)
            stuck: int = session.execute(
                select(func.count()).where(
                    run.c.status == Status.STOPPING,
                    # Sem o instante, a Run foi para `stopping` pelo código anterior à 0032.
                    func.coalesce(
                        run.c.stop_requested_at,
                        func.least(run.c.cancel_requested_at, run.c.deadline_at),
                    )
                    <= now - STUCK_STOPPING_AFTER,
                )
            ).scalar_one()
            storage = capacity.global_usage(session)
            return {
                "queue_stalled_runs": _queue_stalled_runs(session, now),
                **history_backlog(session, now),
                **file_backlog(session, now),
                "reservations_to_remove": capacity.released_backlog(session, now),
                "runs_stuck_stopping": stuck,
                "storage_global_reserved_bytes": storage.storage_reserved_bytes,
                "storage_global_max_bytes": storage.storage_max_bytes,
            }

    def _at_db_now[T](self, step: Callable[[Session, datetime], T]) -> T:
        with self._factory() as session, session.begin():
            return step(session, db_now(session))

    # Comum -----------------------------------------------------------------------------

    def _select_ids(self, condition: ColumnElement[bool]) -> list[uuid.UUID]:
        with self._factory() as session, session.begin():
            return list(
                session.execute(
                    select(run.c.id)
                    .where(condition)
                    .order_by(run.c.created_at, run.c.id)
                    .limit(self._config.batch_size)
                ).scalars()
            )

    def _decide(
        self, run_id: uuid.UUID, event_for: Callable[[LoadedRun], Event | None]
    ) -> Applied | None:
        """Decide numa transação com `SKIP LOCKED`; `None` se a linha está ocupada ou mudou."""

        def decide(session: Session) -> Applied | None:
            loaded = load_run_any_owner(session, run_id, skip_locked=True)
            if loaded is None:
                return None
            event = event_for(loaded)
            if event is None:
                return None
            decision = decide_transition(loaded.state, event)
            if isinstance(decision, Refused):
                return None
            attempt_error = (
                loaded.state.stop_reason
                if isinstance(event, ExecutorStopped)
                else TRANSIENT_ATTEMPT_ERROR
            )
            applied = apply_transition(session, loaded, decision, attempt_error=attempt_error)
            if applied.stop_requested and not isinstance(event, DeadlineExceeded):
                # O executor já foi confirmado parado: a parada pedida se confirma na hora.
                return self._confirm_stopped(session, run_id, applied)
            return applied

        try:
            applied = run_transaction(self._factory, decide)
        except LostFencing:
            return None
        if applied is not None:
            self._candidates_left_on_disk += complete_after_commit(
                self._factory, self._storage, applied, self._on_finished
            )
        return applied

    def _confirm_stopped(self, session: Session, run_id: uuid.UUID, first: Applied) -> Applied:
        loaded = load_run_any_owner(session, run_id)
        if loaded is None:
            raise LostFencing(f"execução {run_id} sumiu durante a reconciliação")
        decision = decide_transition(loaded.state, ExecutorStopped(loaded.state.generation))
        if isinstance(decision, Refused):
            raise LostFencing(f"parada não confirmada: {decision.reason}")
        stopped = apply_transition(
            session, loaded, decision, attempt_error=loaded.state.stop_reason
        )
        return Applied(
            run_id=stopped.run_id,
            state=stopped.state,
            started_attempt_id=None,
            stop_requested=False,
            release_pending=stopped.release_pending,
            discarded_keys=first.discarded_keys + stopped.discarded_keys,
        )


def _unreported_finishes(session: Session, now: datetime, limit: int) -> list[uuid.UUID]:
    return list(
        session.execute(
            select(run.c.id)
            .where(
                run.c.finished_at.is_not(None),
                run.c.finished_reported_at.is_(None),
                run.c.finished_at <= now - UNREPORTED_FINISH_GRACE,
                run.c.finished_at >= now - UNREPORTED_FINISH_HORIZON,
            )
            .order_by(run.c.finished_at, run.c.id)
            .limit(limit)
        ).scalars()
    )


def _lease_expired() -> ColumnElement[bool]:
    """Tentativa aberta de Run ativa com o lease vencido no relógio do banco."""
    return and_(
        attempt.c.ended_at.is_(None),
        attempt.c.lease_expires_at < func.now(),
        run.c.status.in_(ACTIVE_STATUSES),
    )


def log_step_failure(step: str, error: Exception) -> None:
    """Registra o passo que falhou com o tipo e o SQLSTATE nos campos.

    A mensagem da exceção pode trazer host, porta, usuário e valores (3.7): não entra nos campos,
    e o filtro do processo (`omit_exception_detail`) tira o texto dela do traceback, seja ou não
    erro de banco.
    """
    driver_error = getattr(error, "orig", error)
    fields = {
        "step": step,
        "error_type": type(error).__name__,
        "sqlstate": getattr(driver_error, "sqlstate", None),
    }
    logger.exception("passo da manutenção falhou", extra={"fields": fields})


def _unreadable_fields(sweep: MissingSweep) -> dict[str, object]:
    """Campos do alerta do lote: uma linha só, com a amostra limitada e a contagem por errno."""
    return {
        "alert": "objects_unreadable",
        "unreadable": sweep.unreadable,
        "errnos": dict(Counter(found.errno_name for found in sweep.unreadable_objects)),
        "sample": [asdict(found) for found in sweep.unreadable_objects[:UNREADABLE_LOG_SAMPLE]],
    }


def _tampered_fields(sweep: MissingSweep) -> dict[str, object]:
    """Campos do alerta do lote: uma linha só, com a amostra de chaves limitada."""
    return {
        "alert": "input_tampered",
        "tampered": sweep.tampered,
        "sample": [{"storage_key": key} for key in sweep.tampered_keys[:UNREADABLE_LOG_SAMPLE]],
    }


def _sweep_counts(sweep: MissingSweep) -> dict[str, int]:
    """As contagens da varredura no evento do ciclo."""
    return {
        "objects_missing_checked": sweep.checked,
        "objects_missing_absent": sweep.absent,
        "objects_missing_unreadable": sweep.unreadable,
        "objects_missing_tampered": sweep.tampered,
        "objects_marked_missing": sweep.marked,
        "missing_sweep_skipped": int(sweep.storage_unreachable),
        "missing_governor_tripped": int(sweep.governor_tripped),
    }


def _discard_locked(session: Session, run_id: uuid.UUID, attempt_id: uuid.UUID) -> list[str] | None:
    # Ordem de locks: Run → Attempt → arquivos; Run ocupada fica para o próximo ciclo.
    if load_run_any_owner(session, run_id, skip_locked=True) is None:
        return None
    return discard_candidates(session, attempt_id)


def _queue_stalled_runs(session: Session, now: datetime) -> int:
    """Runs elegíveis há mais de `QUEUE_STALLED_AFTER` com nenhum worker trabalhando.

    Conta as que esperam claim (`queued`, ou `running` em `retry_wait`) só quando não há tentativa
    aberta com o lease em dia: o worker ocupado numa Run longa não é fila parada, e qualquer
    tentativa viva mostra que há worker de pé.
    """
    live_attempt = exists().where(attempt.c.ended_at.is_(None), attempt.c.lease_expires_at >= now)
    waiting = or_(
        run.c.status == Status.QUEUED,
        and_(run.c.status == Status.RUNNING, run.c.phase == Phase.RETRY_WAIT),
    )
    stalled: int = session.execute(
        select(func.count()).where(
            waiting, run.c.eligible_at <= now - QUEUE_STALLED_AFTER, ~live_attempt
        )
    ).scalar_one()
    return stalled
