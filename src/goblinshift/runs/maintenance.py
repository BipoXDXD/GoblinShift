"""Reconciliação e retenção, um ciclo por vez (docs/GoblinShiftPlan.md, 3.4, 3.5 e 1.7).

Cada passo escolhe candidatos sem trava e decide cada execução numa transação curta própria,
travando a Run com `FOR UPDATE SKIP LOCKED` e conferindo a geração lida (fencing): se um worker
vivo estiver escrevendo, a linha é pulada e volta no ciclo seguinte. Consultas e paradas do
Runner acontecem fora de transação.

Lease vencido não prova que o executor morreu: antes de reagendar, o Runner é consultado pelo
nome; executor vivo recebe `stop`, e só a confirmação do término libera a decisão. Sem
confirmação, ou com o Runner fora do ar (`executor-ctl` parado), a execução fica como está
(reserva física conservada) até o próximo ciclo: nada é reagendado sem ser inspecionado. O
`stop` só sai se o lease, relido numa transação curta e sem trava logo antes, ainda estiver
vencido: um worker pausado que voltou e renovou não perde o executor.

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

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from goblinshift import capacity
from goblinshift.files.candidates import attempts_with_loose_candidates, discard_candidates
from goblinshift.files.limits import EXPIRY_BATCH_SIZE, STALE_TEMPORARY_TTL
from goblinshift.files.missing import (
    DEFAULT_MAX_ABSENT_RATIO,
    MissingSweep,
    sweep_missing_objects,
)
from goblinshift.files.objects import holders_with_open_uses
from goblinshift.files.retention import (
    anonymize_deleted_uploads,
    delete_old_file_rows,
    expire_artifacts,
    expire_incomplete_uploads,
    expire_unused_uploads,
    file_backlog,
    purge_deleted_objects,
    reconcile_capacity,
    remove_file_after_commit,
)
from goblinshift.files.states import HolderKind
from goblinshift.files.storage import FileStorage
from goblinshift.runs.lifecycle import (
    TERMINAL_STATUSES,
    DeadlineExceeded,
    Event,
    ExecutorStopped,
    LeaseExpired,
    QueueExpired,
    Refused,
    Status,
    decide_transition,
)
from goblinshift.runs.retention import (
    HISTORY_REFERENCES,
    anonymize_finished_runs,
    anonymize_output_names,
    delete_expired_idempotency_records,
    delete_old_runs,
    history_backlog,
)
from goblinshift.runs.store import (
    ACTIVE_STATUSES,
    Applied,
    LoadedRun,
    LostFencing,
    RunFinishedHook,
    apply_transition,
    complete_after_commit,
    db_now,
    load_run,
    release_run_holds,
    run_transaction,
)
from goblinshift.runs.tables import attempt, run
from goblinshift.runs.worker.coordinator import TRANSIENT_ATTEMPT_ERROR, random_retry_jitter
from goblinshift.runs.worker.runner import STOP_GRACE, ExecutorStatus, Runner, RunnerError

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 50
# Quantos objetos ilegíveis vão nomeados no log do lote: com o disco inteiro devolvendo erro, a
# contagem por errno diz o resto sem uma linha por objeto a cada ciclo.
UNREADABLE_LOG_SAMPLE = 5
# Alerta de causa (3.7): Run em `stopping` há mais que isto sem confirmar a parada.
STUCK_STOPPING_AFTER = timedelta(minutes=10)
# Contagem do evento do ciclo: passos que levantaram exceção. O ciclo segue para os outros, e a
# volta conta como falha (`entrypoints/maintenance.run_cycles`, causa `gs-causa-ciclos-falhando`).
STEPS_FAILED = "steps_failed"
# Passos não tentados na volta: os de banco depois de uma falha de conexão e os que a parada
# pedida deixou para trás.
STEPS_SKIPPED = "steps_skipped"
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
        except Exception as error:
            # Fronteira do passo: o seguinte recomeça do banco, sem estado em memória.
            _log_step_failure(step.name, error)
            self.failed += 1
            if self.connection_lost_in is None and _is_connection_error(error):
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


def _is_connection_error(error: Exception) -> bool:
    """Erro de conexão: repetir o banco noutro passo só esperaria o `connect_timeout` de novo.

    Conexão que o pool invalidou, ou `OperationalError` sem SQLSTATE (o `connect` recusado ou
    sem resposta) ou com SQLSTATE de conexão ou de servidor parando. `statement_timeout` (57014)
    também chega como `OperationalError`, mas o banco respondeu: os outros passos seguem.
    """
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
        # Candidatos que ficaram no disco no ciclo em curso. Em atributo porque vêm de vários
        # passos, por `_decide`, cujo retorno já é a transição.
        self._candidates_left_on_disk = 0

    def run_cycle(self, should_stop: Callable[[], bool] = _never) -> dict[str, int]:
        """Um ciclo completo; devolve as contagens do evento amplo da manutenção (3.7).

        Passos independentes: a exceção de um fica no log e em `steps_failed`, e o ciclo segue
        para prazo, retenção, expurgo e medidores; as contagens do passo que falhou ficam fora do
        evento. Falha de conexão (banco inalcançável) pula os passos de banco restantes da volta,
        que só esperariam o `connect_timeout` de novo; `should_stop` (a parada pedida por sinal) é
        conferido antes de cada passo. Os dois contam em `steps_skipped`. Interrupção do processo
        (`KeyboardInterrupt`, `SystemExit`) não é `Exception` e sobe.
        """
        started = time.monotonic()
        self._candidates_left_on_disk = 0
        outcome = _CycleOutcome()
        for step in self._cycle_steps():
            if should_stop():
                outcome.skipped_for_stop.append(step.name)
            elif outcome.connection_lost_in is not None and step.uses_database:
                outcome.skipped_after_connection.append(step.name)
            else:
                outcome.run(step)
        counts = outcome.finish()
        counts["candidates_left_on_disk"] = self._candidates_left_on_disk
        fields: dict[str, object] = {"main": True, **counts}
        fields["duration_ms"] = round((time.monotonic() - started) * 1000)
        logger.info("ciclo de manutenção", extra={"fields": fields})
        return counts

    def _cycle_steps(self) -> tuple[_Step, ...]:
        """Os passos do ciclo, na ordem: reconciliação, fila, prazo, usos, retenção e medidores."""
        return (
            _Step(
                "reconcile_expired_leases",
                lambda: {"leases_reconciled": self.reconcile_expired_leases()},
            ),
            _Step("expire_queue", lambda: {"queue_expired": self.expire_queue()}),
            _Step("apply_deadlines", lambda: {"deadlines": self.apply_deadlines()}),
            _Step("release_orphan_holds", lambda: {"holds_released": self.release_orphan_holds()}),
            _Step("discard_stale_candidates", self.discard_stale_candidates),
            _Step("sweep_work_areas", lambda: {"work_areas_removed": self.sweep_work_areas()}),
            _Step("apply_retention", self.apply_retention),
            # Só o storage: segue mesmo com o banco fora.
            _Step("sweep_temporary_parts", self.sweep_temporary_parts, uses_database=False),
            _Step("mark_missing_objects", self.mark_missing_objects),
            _Step("measure", self.measure),
        )

    # Lease vencido e parada ------------------------------------------------------------

    def reconcile_expired_leases(self) -> int:
        """Tentativas com lease vencido: confirma o término do executor e decide."""
        reconciled = 0
        for expired in self._expired_attempts():
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
                extra={"fields": {**fields, "error": str(error)}},
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
                extra={"fields": {"attempt_id": str(attempt_id), "error": str(error)}},
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
        os passos de banco restantes, que só esperariam o `connect_timeout` de novo.
        """
        try:
            return self._lease_still_expired(expired)
        except DBAPIError as error:
            if _is_connection_error(error):
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
        Runner. Não fecha a janela inteira (o worker pode renovar entre esta leitura e o `stop`),
        só a encurta; quem decide depois continua sendo o fencing de `_lease_event`.

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
        # Um coordenador vivo pode ter renovado depois da leitura: então o caso é dele.
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

    def release_orphan_holds(self) -> int:
        """Execuções terminais com uso aberto: a transação seguinte ao término não chegou."""
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

    def sweep_work_areas(self) -> int:
        """Remove executor e área de tentativas encerradas (ou sem registro) que ficaram para trás.

        A tentativa viva (`ended_at` nulo) é do worker ou da reconciliação de lease: não é tocada.
        """
        try:
            present = self._runner.list_work()[: self._config.batch_size]
        except OSError as error:
            logger.warning("área de trabalho ilegível", extra={"fields": {"error": str(error)}})
            return 0
        if not present:
            return 0
        with self._factory() as session, session.begin():
            live = set(
                session.execute(
                    select(attempt.c.id).where(
                        attempt.c.id.in_(present), attempt.c.ended_at.is_(None)
                    )
                ).scalars()
            )
        removed = 0
        for attempt_id in present:
            if attempt_id in live:
                continue
            status = self._confirm_ended(attempt_id, run_id=None)
            if status is not None and self._try_remove(attempt_id):
                removed += 1
        return removed

    # Retenção --------------------------------------------------------------------------

    def apply_retention(self) -> dict[str, int]:
        incomplete = expire_incomplete_uploads(self._new_session(), self._storage)
        counts = {
            "incomplete_uploads_expired": incomplete.uploads,
            "unused_uploads_expired": expire_unused_uploads(self._new_session()),
            "artifacts_expired": expire_artifacts(self._new_session()),
        }
        report = purge_deleted_objects(self._new_session(), self._storage)
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
        counts["objects_purged"] = report.objects
        counts["bytes_purged"] = report.bytes
        # Com o banco já confirmado, nada repete a remoção: o alerta de causa lê isto (3.7).
        counts["purge_files_not_removed"] = len(incomplete.failed_keys) + len(report.failed_keys)
        # Depois do expurgo: o que acabou de ser liberado já sai das somas dos pools.
        reconciled = reconcile_capacity(self._new_session(), limit=self._config.batch_size)
        counts["capacity_pools_corrected"] = reconciled.pools_corrected
        counts["capacity_objects_adopted"] = reconciled.objects_adopted
        counts["purge_limit_reached"] = int(report.limit_reached)
        return {**counts, **self.apply_history_retention()}

    def sweep_temporary_parts(self) -> dict[str, int]:
        """Apaga os temporários que uma queda deixou no storage e mede o que ficou no diretório.

        Eles ficam fora do livro-razão e do `file_backlog`. Isolada do resto do ciclo, como a
        varredura de ausentes: uma falha de disco, no diretório ou em um arquivo, fica no log e
        em `tmp_parts_sweep_failed`.
        """
        # O `mtime` vem do relógio desta máquina, e não do banco: é com ele que se compara.
        modified_before = time.time() - STALE_TEMPORARY_TTL.total_seconds()
        try:
            sweep = self._storage.sweep_stale_temporaries(
                modified_before, self._config.retention_batch_size
            )
        except OSError:
            logger.exception("limpeza de temporários falhou")
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
        do ciclo seguinte já vê as reservas liberadas aqui.
        """
        try:
            sweep = sweep_missing_objects(
                self._new_session(),
                self._storage,
                after=self._missing_resume_after,
                limit=self._config.missing_sweep_batch_size,
                max_absent_ratio=self._config.missing_max_absent_ratio,
            )
        except Exception:
            # Não engole: registra com o traceback e conta; o cursor fica, e o lote volta.
            logger.exception(
                "varredura de ausentes falhou",
                extra={"fields": {"alert": "missing_sweep_failed"}},
            )
            return {**_sweep_counts(MissingSweep.idle()), "missing_sweep_failed": 1}
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

    def apply_history_retention(self) -> dict[str, int]:
        """Retenção longa (1.7), cada etapa um lote numa transação própria.

        A ordem importa: a Run sai antes das linhas de arquivos que o histórico dela referencia,
        e as linhas de arquivos só saem depois do arquivo no disco (`delete_old_file_rows`).
        """
        batch = self._config.retention_batch_size
        steps: dict[str, Callable[[Session, datetime], int]] = {
            "idempotency_records_removed": lambda s, now: delete_expired_idempotency_records(
                s, now, batch
            ),
            "runs_anonymized": lambda s, now: anonymize_finished_runs(s, now, batch),
            "outputs_anonymized": lambda s, now: anonymize_output_names(s, now, batch),
            "uploads_anonymized": lambda s, now: anonymize_deleted_uploads(s, now, batch),
            "runs_deleted": lambda s, now: delete_old_runs(s, now, batch),
            "file_rows_deleted": lambda s, now: delete_old_file_rows(
                s, now, batch, HISTORY_REFERENCES
            ),
            "reservations_removed": lambda s, now: capacity.delete_released_reservations(
                s, now, batch
            ),
        }
        return {name: self._at_db_now(step) for name, step in steps.items()}

    def measure(self) -> dict[str, int]:
        """Medidores do evento do ciclo: o tamanho do que acumula e o que os alertas leem."""
        with self._factory() as session, session.begin():
            now = db_now(session)
            stuck: int = session.execute(
                select(func.count()).where(
                    run.c.status == Status.STOPPING,
                    func.least(run.c.cancel_requested_at, run.c.deadline_at)
                    <= now - STUCK_STOPPING_AFTER,
                )
            ).scalar_one()
            storage = capacity.global_usage(session)
            return {
                **history_backlog(session, now),
                **file_backlog(session, now),
                "runs_stuck_stopping": stuck,
                "storage_global_reserved_bytes": storage.storage_reserved_bytes,
                "storage_global_max_bytes": storage.storage_max_bytes,
            }

    def _at_db_now(self, step: Callable[[Session, datetime], int]) -> int:
        with self._factory() as session, session.begin():
            return step(session, db_now(session))

    # Comum -----------------------------------------------------------------------------

    def _new_session(self) -> Session:
        return self._factory()

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
            loaded = load_run(session, run_id, skip_locked=True)
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
        loaded = load_run(session, run_id)
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


def _lease_expired() -> ColumnElement[bool]:
    """Tentativa aberta de Run ativa com o lease vencido no relógio do banco."""
    return and_(
        attempt.c.ended_at.is_(None),
        attempt.c.lease_expires_at < func.now(),
        run.c.status.in_(ACTIVE_STATUSES),
    )


def _log_step_failure(step: str, error: Exception) -> None:
    """Registra o passo que falhou com o tipo e o SQLSTATE nos campos.

    A mensagem do banco traz host, porta, usuário e valores (3.7): não entra nos campos, e o
    filtro do processo (`omit_database_error_detail`) tira o texto dela do traceback.
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


def _sweep_counts(sweep: MissingSweep) -> dict[str, int]:
    """As contagens da varredura no evento do ciclo."""
    return {
        "objects_missing_checked": sweep.checked,
        "objects_missing_absent": sweep.absent,
        "objects_missing_unreadable": sweep.unreadable,
        "objects_marked_missing": sweep.marked,
        "missing_sweep_skipped": int(sweep.storage_unreachable),
        "missing_governor_tripped": int(sweep.governor_tripped),
    }


def _discard_locked(session: Session, run_id: uuid.UUID, attempt_id: uuid.UUID) -> list[str] | None:
    # Ordem de locks: Run → Attempt → arquivos; Run ocupada fica para o próximo ciclo.
    if load_run(session, run_id, skip_locked=True) is None:
        return None
    return discard_candidates(session, attempt_id)
