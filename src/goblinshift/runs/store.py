"""Persistência da Run com fencing (docs/GoblinShiftPlan.md, 3.4 e 3.5).

A decisão é da máquina pura (`lifecycle`); aqui só se lê a Run como `RunState`, com o relógio do
banco, e se grava a `Transition` decidida. Toda escrita é condicionada à geração e ao status
lidos: se outra tentativa ou o reconciliador assumiu entre a leitura e a escrita, nenhuma linha
muda e `LostFencing` interrompe quem chamou, que para sem escrever mais nada.

Ordem de locks (3.4): Run → Attempt → arquivos. A liberação de usos e da reserva de capacidade
fica para uma transação seguinte (`release_run_holds`), como na publicação e na manutenção: a
publicação só move saldo entre reservas, sem travar pool depois da Run.
"""

import logging
import time
import uuid
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, exists, func, insert, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from goblinshift import capacity
from goblinshift.files.candidates import discard_candidates
from goblinshift.files.objects import release
from goblinshift.files.states import HolderKind
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import artifact, stored_object
from goblinshift.runs.lifecycle import (
    TERMINAL_STATUSES,
    DiscardCandidates,
    EndAttempt,
    ErrorCode,
    Phase,
    PublishOutputs,
    ReleaseReservations,
    RequestStop,
    RunState,
    StartAttempt,
    Status,
    Transition,
)
from goblinshift.runs.tables import attempt, run, run_output

logger = logging.getLogger(__name__)

# Deadlock e falha de serialização repetem a transação inteira, com teto (3.4).
RETRYABLE_SQLSTATES = frozenset({"40P01", "40001"})
MAX_TRANSACTION_ATTEMPTS = 3
_RETRY_PAUSE_S = 0.05

ACTIVE_STATUSES = frozenset({Status.RUNNING, Status.STOPPING})

# Chamado com o id da Run que acabou de chegar a estado terminal, depois do commit e da
# liberação dos usos: emite o evento de término (goblinshift.runs.finished, 3.7).
RunFinishedHook = Callable[[uuid.UUID], None]


# O nome segue o vocabulário do protocolo (3.4: fencing), não o sufixo Error.
class LostFencing(Exception):  # noqa: N818
    """Outra tentativa ou o reconciliador assumiu a execução; quem recebe para sem escrever."""


def container_name(attempt_id: uuid.UUID) -> str:
    """Nome determinístico do executor da tentativa: repetir o `start` adota o existente."""
    return f"gs-exec-{attempt_id}"


@dataclass(frozen=True)
class Fence:
    """Token de fencing de quem escreve: a geração lida e, para o coordenador, a sua Attempt."""

    run_id: uuid.UUID
    generation: int
    attempt_id: uuid.UUID | None = None


@dataclass(frozen=True)
class LiveAttempt:
    id: uuid.UUID
    generation: int
    number: int
    lease_expires_at: datetime
    executor_started_at: datetime | None


@dataclass(frozen=True)
class LoadedRun:
    """A Run travada (`FOR UPDATE`) e o `now()` da transação, o único relógio das decisões."""

    run_id: uuid.UUID
    owner_id: uuid.UUID
    state: RunState
    live_attempt: LiveAttempt | None
    now: datetime


@dataclass(frozen=True)
class Applied:
    """O que sobra fazer depois do commit de uma transição."""

    run_id: uuid.UUID
    state: RunState
    started_attempt_id: uuid.UUID | None
    # O executor precisa receber `stop`; a confirmação vem num evento seguinte.
    stop_requested: bool
    # Usos de objetos a liberar numa transação seguinte (`release_run_holds`).
    release_pending: bool
    # Candidatos pendentes cuja linha saiu: os arquivos são apagados depois do commit.
    discarded_keys: tuple[str, ...]


def run_transaction[T](factory: sessionmaker[Session], work: Callable[[Session], T]) -> T:
    """Roda `work` numa transação; deadlock ou serialização repetem tudo, até o teto.

    `work` precisa ser repetível: lê de novo o que decide, nunca reaproveita leitura anterior.
    """
    for attempt_number in range(1, MAX_TRANSACTION_ATTEMPTS + 1):
        try:
            with factory() as session, session.begin():
                return work(session)
        except DBAPIError as error:
            sqlstate = getattr(error.orig, "sqlstate", None)
            if sqlstate not in RETRYABLE_SQLSTATES or attempt_number == MAX_TRANSACTION_ATTEMPTS:
                raise
            logger.warning(
                "transação repetida",
                extra={"fields": {"sqlstate": sqlstate, "attempt": attempt_number}},
            )
            time.sleep(_RETRY_PAUSE_S * attempt_number)
    raise RuntimeError("laço de retentativa terminou sem resultado")  # inalcançável


def load_run(
    session: Session,
    run_id: uuid.UUID,
    *,
    owner_id: uuid.UUID | None = None,
    skip_locked: bool = False,
) -> LoadedRun | None:
    """Lê e trava a Run (`FOR UPDATE`, ou `SKIP LOCKED` na manutenção) como `RunState`.

    `physical_attempts` e `executor_started` saem das Attempts, e `has_published_outputs` dos
    `RunOutput`: são dados derivados, nunca colunas que poderiam divergir.
    """
    live = attempt.alias("live")
    physical = (
        select(func.count())
        .where(attempt.c.run_id == run.c.id, attempt.c.executor_started_at.is_not(None))
        .scalar_subquery()
    )
    conditions = [run.c.id == run_id]
    if owner_id is not None:
        conditions.append(run.c.owner_id == owner_id)
    row = session.execute(
        select(
            run.c.id,
            run.c.owner_id,
            run.c.status,
            run.c.phase,
            run.c.generation,
            run.c.claim_count,
            run.c.eligible_at,
            run.c.queue_expires_at,
            run.c.deadline_at,
            run.c.cancel_requested_at,
            run.c.stop_reason,
            run.c.error_code,
            physical.label("physical_attempts"),
            exists().where(run_output.c.run_id == run.c.id).label("published"),
            live.c.id.label("live_id"),
            live.c.generation.label("live_generation"),
            live.c.number.label("live_number"),
            live.c.lease_expires_at.label("live_lease"),
            live.c.executor_started_at.label("live_started"),
            func.now().label("now"),
        )
        .outerjoin(live, and_(live.c.run_id == run.c.id, live.c.ended_at.is_(None)))
        .where(*conditions)
        .with_for_update(of=run, skip_locked=skip_locked)
    ).one_or_none()
    if row is None:
        return None
    live_attempt = (
        LiveAttempt(
            row.live_id, row.live_generation, row.live_number, row.live_lease, row.live_started
        )
        if row.live_id is not None
        else None
    )
    state = RunState(
        status=Status(row.status),
        phase=Phase(row.phase) if row.phase is not None else None,
        generation=row.generation,
        claim_count=row.claim_count,
        physical_attempts=row.physical_attempts,
        executor_started=live_attempt is not None and live_attempt.executor_started_at is not None,
        eligible_at=row.eligible_at,
        queue_expires_at=row.queue_expires_at,
        deadline_at=row.deadline_at,
        cancel_requested=row.cancel_requested_at is not None,
        stop_reason=ErrorCode(row.stop_reason) if row.stop_reason is not None else None,
        error_code=ErrorCode(row.error_code) if row.error_code is not None else None,
        has_published_outputs=row.published,
    )
    return LoadedRun(row.id, row.owner_id, state, live_attempt, row.now)


def lock_fenced(session: Session, fence: Fence, statuses: Collection[Status]) -> LoadedRun:
    """Trava a Run só se a geração, o status e a Attempt viva ainda forem os de quem escreve.

    O lock vale até o commit: entre esta leitura e as escritas da mesma transação, nenhum claim
    muda a geração. Qualquer divergência é `LostFencing`.
    """
    loaded = load_run(session, fence.run_id)
    if loaded is None:
        raise LostFencing(f"execução {fence.run_id} não existe")
    state = loaded.state
    if state.generation != fence.generation or state.status not in statuses:
        raise LostFencing(
            f"execução {fence.run_id}: geração {state.generation}/{state.status}, "
            f"esperava {fence.generation}"
        )
    if fence.attempt_id is not None and (
        loaded.live_attempt is None or loaded.live_attempt.id != fence.attempt_id
    ):
        raise LostFencing(f"tentativa {fence.attempt_id} não é mais a viva")
    return loaded


def apply_transition(
    session: Session,
    loaded: LoadedRun,
    transition: Transition,
    *,
    lease: timedelta | None = None,
    attempt_error: str | None = None,
    publish: Callable[[Session], None] | None = None,
    summary: Mapping[str, object] | None = None,
) -> Applied:
    """Grava a transição com fencing e aplica os efeitos de banco na ordem decidida.

    O `UPDATE` da Run exige a geração e o status lidos; na publicação, também ausência de
    cancelamento e `now() <= deadline_at` no relógio do banco (3.4). Zero linhas → `LostFencing`.
    """
    _update_run(session, loaded, transition.run, transition, summary)
    old, new = loaded.state, transition.run
    if new.physical_attempts > old.physical_attempts:
        _mark_executor_started(session, loaded)
    started_attempt_id: uuid.UUID | None = None
    stop_requested = release_pending = False
    discarded: list[str] = []
    for effect in transition.effects:
        match effect:
            case StartAttempt():
                started_attempt_id = _insert_attempt(session, loaded.run_id, effect, lease)
            case EndAttempt():
                _end_attempt(session, loaded.run_id, effect.generation, attempt_error)
            case RequestStop():
                stop_requested = True
            case PublishOutputs():
                if publish is None:
                    raise ValueError("transição de publicação sem a função que publica")
                publish(session)
                _reserve_published_objects(session, loaded.run_id)
            case DiscardCandidates():
                discarded.extend(_discard(session, loaded, effect.generation))
            case ReleaseReservations():
                release_pending = True
    return Applied(
        run_id=loaded.run_id,
        state=new,
        started_attempt_id=started_attempt_id,
        stop_requested=stop_requested,
        release_pending=release_pending,
        discarded_keys=tuple(discarded),
    )


def release_run_holds(session: Session, run_id: uuid.UUID, generation: int) -> None:
    """Libera os usos de objetos e o resto da reserva de saída da execução terminal.

    Repetir é no-op.

    Condicionada à geração, como toda escrita (3.4): um worker atrasado não libera os usos de
    uma tentativa que não é a dele.
    """
    locked = session.execute(
        select(run.c.id)
        .where(
            run.c.id == run_id,
            run.c.generation == generation,
            run.c.status.in_(TERMINAL_STATUSES),
        )
        .with_for_update(read=True)
    ).one_or_none()
    if locked is None:
        raise LostFencing(f"execução {run_id} não está terminal na geração {generation}")
    release(session, HolderKind.RUN, run_id)
    capacity.release(session, holder=capacity.HolderKind.RUN, holder_id=run_id)


def complete_after_commit(
    factory: sessionmaker[Session],
    storage: FileStorage,
    applied: Applied,
    on_finished: RunFinishedHook | None = None,
) -> None:
    """O que a transição deixou para depois do commit: arquivos descartados, usos e o evento.

    `release_pending` só vem de transição para estado terminal (`ReleaseReservations`), então é
    também o sinal de que esta transição terminou a Run e o evento de término sai uma vez.
    """
    for key in applied.discarded_keys:
        try:
            storage.delete(key)
        except OSError:
            # O arquivo vira órfão sob `runs/{run}/g{n}/`: só ocupa espaço, e o log o aponta.
            logger.warning("candidato descartado ficou no disco", extra={"fields": {"key": key}})
    if applied.release_pending:
        run_transaction(
            factory,
            lambda session: release_run_holds(session, applied.run_id, applied.state.generation),
        )
        if on_finished is not None:
            on_finished(applied.run_id)


def _reserve_published_objects(session: Session, run_id: uuid.UUID) -> None:
    """Passa da reserva da Run para cada objeto publicado o tamanho dele (ADR 023, D-5).

    Objeto que já tem reserva (a entrada reaproveitada como saída) não é contado de novo. O resto
    do teto continua com a Run até `release_run_holds`.
    """
    published = session.execute(
        select(stored_object.c.id, stored_object.c.size_bytes)
        .join(artifact, artifact.c.object_id == stored_object.c.id)
        .join(run_output, run_output.c.artifact_id == artifact.c.id)
        .where(
            run_output.c.run_id == run_id,
            ~capacity.has_live_reservation(capacity.HolderKind.OBJECT, stored_object.c.id),
        )
        .distinct()
        .order_by(stored_object.c.id)
    ).all()
    for object_id, size in published:
        try:
            capacity.transfer(
                session,
                from_holder=(capacity.HolderKind.RUN, run_id),
                to_holder=(capacity.HolderKind.OBJECT, object_id),
                amount=size,
            )
        except capacity.ReservationNotFoundError:
            # Run aceita pela API anterior ao deploy da 0006, sem reserva de saída: publica
            # assim mesmo, e a reconciliação da manutenção adota os objetos.
            logger.warning(
                "publicação sem reserva de capacidade",
                extra={"fields": {"alert": "run_without_reservation", "run_id": str(run_id)}},
            )
            return


def db_now(session: Session) -> datetime:
    """`now()` da transação: o relógio de todas as decisões de prazo (3.4)."""
    now: datetime = session.execute(select(func.now())).scalar_one()
    return now


def _update_run(
    session: Session,
    loaded: LoadedRun,
    new: RunState,
    transition: Transition,
    summary: Mapping[str, object] | None,
) -> None:
    old = loaded.state
    values: dict[str, object] = {
        "status": new.status,
        "phase": new.phase,
        "generation": new.generation,
        "claim_count": new.claim_count,
        "eligible_at": new.eligible_at,
        "deadline_at": new.deadline_at,
        "stop_reason": new.stop_reason,
        "error_code": new.error_code,
    }
    if new.cancel_requested and not old.cancel_requested:
        values["cancel_requested_at"] = func.now()
    if new.is_terminal:
        values["finished_at"] = func.now()
    if any(isinstance(effect, StartAttempt) for effect in transition.effects):
        values["started_at"] = func.coalesce(run.c.started_at, func.now())
    if summary is not None:
        values["summary"] = dict(summary)
    conditions = [
        run.c.id == loaded.run_id,
        run.c.generation == old.generation,
        run.c.status == old.status,
    ]
    if any(isinstance(effect, PublishOutputs) for effect in transition.effects):
        conditions += [run.c.cancel_requested_at.is_(None), func.now() <= run.c.deadline_at]
    updated = session.execute(
        update(run).where(*conditions).values(**values).returning(run.c.id)
    ).scalar_one_or_none()
    if updated is None:
        raise LostFencing(f"execução {loaded.run_id}: a transição não afetou a linha")


def _mark_executor_started(session: Session, loaded: LoadedRun) -> None:
    if loaded.live_attempt is None:
        raise LostFencing(f"execução {loaded.run_id} sem tentativa viva para contar")
    session.execute(
        update(attempt)
        .where(attempt.c.id == loaded.live_attempt.id, attempt.c.ended_at.is_(None))
        .values(executor_started_at=func.coalesce(attempt.c.executor_started_at, func.now()))
    )


def _insert_attempt(
    session: Session, run_id: uuid.UUID, effect: StartAttempt, lease: timedelta | None
) -> uuid.UUID:
    if lease is None:
        raise ValueError("claim sem a duração do lease")
    attempt_id = uuid.uuid7()
    session.execute(
        insert(attempt).values(
            id=attempt_id,
            run_id=run_id,
            number=effect.number,
            generation=effect.generation,
            container_name=container_name(attempt_id),
            lease_expires_at=func.now() + lease,
        )
    )
    return attempt_id


def _end_attempt(
    session: Session, run_id: uuid.UUID, generation: int, error_code: str | None
) -> None:
    ended = session.execute(
        update(attempt)
        .where(
            attempt.c.run_id == run_id,
            attempt.c.generation == generation,
            attempt.c.ended_at.is_(None),
        )
        .values(ended_at=func.now(), error_code=error_code)
        .returning(attempt.c.id)
    ).scalar_one_or_none()
    if ended is None:
        raise LostFencing(f"tentativa da geração {generation} já tinha terminado")


def _discard(session: Session, loaded: LoadedRun, generation: int) -> list[str]:
    attempt_id = session.execute(
        select(attempt.c.id).where(
            attempt.c.run_id == loaded.run_id, attempt.c.generation == generation
        )
    ).scalar_one_or_none()
    if attempt_id is None:
        return []
    return discard_candidates(session, attempt_id)
