"""Reconciliação e retenção, um ciclo por vez (docs/GoblinShiftPlan.md, 3.4, 3.5 e 1.7).

Cada passo escolhe candidatos sem trava e decide cada execução numa transação curta própria,
travando a Run com `FOR UPDATE SKIP LOCKED` e conferindo a geração lida (fencing): se um worker
vivo estiver escrevendo, a linha é pulada e volta no ciclo seguinte. Consultas e paradas do
Runner acontecem fora de transação.

Lease vencido não prova que o executor morreu: antes de reagendar, o Runner é consultado pelo
nome; executor vivo recebe `stop`, e só a confirmação do término libera a decisão. Sem
confirmação, ou com o Runner fora do ar (`executor-ctl` parado), a execução fica como está
(reserva física conservada) até o próximo ciclo: nada é reagendado sem ser inspecionado.

O executor e a área de trabalho só são removidos depois de a decisão estar no banco. Se a remoção
falhar, a limpeza das áreas (`sweep_work_areas`) a repete nos ciclos seguintes: o `executor-ctl`
recusa iniciar com a área suja, então a área de uma tentativa encerrada não pode ficar para trás.
"""

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from functools import partial

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.candidates import attempts_with_loose_candidates, discard_candidates
from goblinshift.files.objects import holders_with_open_uses
from goblinshift.files.retention import (
    expire_artifacts,
    expire_incomplete_uploads,
    expire_unused_uploads,
    purge_deleted_objects,
    reconcile_capacity,
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
from goblinshift.runs.store import (
    ACTIVE_STATUSES,
    Applied,
    LoadedRun,
    LostFencing,
    RunFinishedHook,
    apply_transition,
    complete_after_commit,
    load_run,
    release_run_holds,
    run_transaction,
)
from goblinshift.runs.tables import attempt, run
from goblinshift.runs.worker.coordinator import TRANSIENT_ATTEMPT_ERROR, random_retry_jitter
from goblinshift.runs.worker.runner import ExecutorStatus, Runner, RunnerError

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 50


@dataclass(frozen=True)
class MaintenanceConfig:
    batch_size: int = DEFAULT_BATCH_SIZE
    stop_grace: timedelta = timedelta(seconds=10)
    jitter: Callable[[], timedelta] = field(default=random_retry_jitter)


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

    def run_cycle(self) -> dict[str, int]:
        """Um ciclo completo; devolve as contagens do evento amplo da manutenção (3.7)."""
        started = time.monotonic()
        counts = {
            "leases_reconciled": self.reconcile_expired_leases(),
            "queue_expired": self.expire_queue(),
            "deadlines": self.apply_deadlines(),
            "holds_released": self.release_orphan_holds(),
            "candidates_discarded": self.discard_stale_candidates(),
            "work_areas_removed": self.sweep_work_areas(),
            **self.apply_retention(),
        }
        fields: dict[str, object] = {"main": True, **counts}
        fields["duration_ms"] = round((time.monotonic() - started) * 1000)
        logger.info("ciclo de manutenção", extra={"fields": fields})
        return counts

    # Lease vencido e parada ------------------------------------------------------------

    def reconcile_expired_leases(self) -> int:
        """Tentativas com lease vencido: confirma o término do executor e decide."""
        reconciled = 0
        for expired in self._expired_attempts():
            status = self._confirm_ended(expired.attempt_id, expired.run_id)
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
        self, attempt_id: uuid.UUID, run_id: uuid.UUID | None
    ) -> ExecutorStatus | None:
        """Estado final do executor, parando-o se vivo; `None` se o fim não foi confirmado."""
        fields = {"attempt_id": str(attempt_id), "run_id": str(run_id) if run_id else None}
        try:
            status = self._runner.status(attempt_id)
            if status.is_running:
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
                .where(
                    attempt.c.ended_at.is_(None),
                    attempt.c.lease_expires_at < func.now(),
                    run.c.status.in_(ACTIVE_STATUSES),
                )
                .order_by(attempt.c.lease_expires_at)
                .limit(self._config.batch_size)
            ).all()
        return [_ExpiredAttempt(row.id, row.generation, row.attempt_id) for row in rows]

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

    def discard_stale_candidates(self) -> int:
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
                self._storage.delete(key)
            discarded += 1
        return discarded

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
        counts = {
            "incomplete_uploads_expired": expire_incomplete_uploads(
                self._new_session(), self._storage
            ),
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
        # Depois do expurgo: o que acabou de ser liberado já sai das somas dos pools.
        reconciled = reconcile_capacity(self._new_session(), limit=self._config.batch_size)
        counts["capacity_pools_corrected"] = reconciled.pools_corrected
        counts["capacity_objects_adopted"] = reconciled.objects_adopted
        return counts

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
            complete_after_commit(self._factory, self._storage, applied, self._on_finished)
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


def _discard_locked(session: Session, run_id: uuid.UUID, attempt_id: uuid.UUID) -> list[str] | None:
    # Ordem de locks: Run → Attempt → arquivos; Run ocupada fica para o próximo ciclo.
    if load_run(session, run_id, skip_locked=True) is None:
        return None
    return discard_candidates(session, attempt_id)
