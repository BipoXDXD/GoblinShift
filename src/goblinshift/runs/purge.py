"""Fim da conta em `runs`: cancelamento e purga do histórico (docs/GoblinShiftPlan.md, 1.8).

`cancel_active_run` serve ao bloqueio (`gs-admin block`) e ao `DELETE /me`. `purge_owner_batch`
apaga Runs terminais (com entradas, saídas, tentativas e `idempotency_record` por CASCADE) de uma
conta com exclusão pedida; Run não terminal fica, e a purga só termina depois que ela terminar.
"""

from uuid import UUID

from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.objects import release
from goblinshift.files.purge import attempts_with_pending_outputs
from goblinshift.files.states import HolderKind
from goblinshift.files.storage import FileStorage
from goblinshift.runs.cancellation import CancelResult, RunNotFoundError, cancel_run
from goblinshift.runs.lifecycle import TERMINAL_STATUSES
from goblinshift.runs.tables import attempt, run


def cancel_active_run(
    factory: sessionmaker[Session], storage: FileStorage, owner_id: UUID
) -> CancelResult | None:
    """Pede o cancelamento da execução não terminal da conta; None se não houver.

    O índice único parcial garante no máximo uma. Repetir é seguro: o cancelamento é idempotente.
    """
    with factory() as session, session.begin():
        run_id = session.execute(
            select(run.c.id).where(
                run.c.owner_id == owner_id, run.c.status.not_in(TERMINAL_STATUSES)
            )
        ).scalar_one_or_none()
    if run_id is None:
        return None
    try:
        return cancel_run(factory, storage, owner_id, run_id)
    except RunNotFoundError:
        # Purgada entre a leitura e o cancelamento: o estado final é o mesmo.
        return None


def purge_owner_batch(session: Session, owner_id: UUID, limit: int) -> bool:
    """Processa até `limit` Runs; True quando não sobra nada da conta em `runs`."""
    chosen = list(
        session.execute(
            select(run.c.id)
            .where(run.c.owner_id == owner_id, run.c.status.in_(TERMINAL_STATUSES))
            .order_by(run.c.created_at, run.c.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).scalars()
    )
    waiting = _waiting_for_discard(session, chosen)
    deletable = [run_id for run_id in chosen if run_id not in waiting]
    for run_id in deletable:
        # Uso que ficou aberto numa Run terminal prenderia o objeto para sempre sem a Run.
        release(session, HolderKind.RUN, run_id)
    if deletable:
        session.execute(delete(run).where(run.c.id.in_(deletable)))
    remaining = session.execute(select(exists().where(run.c.owner_id == owner_id))).scalar_one()
    return not remaining


def _waiting_for_discard(session: Session, run_ids: list[UUID]) -> set[UUID]:
    """Runs com tentativa que ainda tem saída pendente.

    A manutenção descarta essas saídas (e apaga o arquivo) só enquanto a tentativa existe; apagar
    a Run antes deixaria um arquivo da conta sem quem o remova.
    """
    if not run_ids:
        return set()
    attempts = session.execute(
        select(attempt.c.id, attempt.c.run_id).where(attempt.c.run_id.in_(run_ids))
    ).all()
    pending = attempts_with_pending_outputs(session, [row.id for row in attempts])
    return {row.run_id for row in attempts if row.id in pending}
