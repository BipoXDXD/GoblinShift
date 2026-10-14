"""Última etapa da purga de uma conta com exclusão pedida (docs/GoblinShiftPlan.md, 1.8; Frente C).

Roda depois que `files` e `runs` apagaram tudo o que é da conta: remove chaves, sessões, o pool
de capacidade (`capacity.purge_owner`) e a própria `app_user`. A auditoria fica (ADR 025).

Também a retenção do que a identidade acumula: estado de login vencido, sessões encerradas há
mais de 7 dias e eventos de auditoria com mais de 90 dias, sempre em lotes.
"""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import ColumnElement, Table, delete, func, select
from sqlalchemy.orm import Session

from goblinshift import capacity
from goblinshift.identity.tables import api_key, app_user, audit_event, login_state, web_session

# Prazo da purga é 24 h (1.8); o alerta da fatia 8 dispara antes, com margem para agir.
DELETION_OVERDUE_AFTER = timedelta(hours=20)
WEB_SESSION_RETENTION = timedelta(days=7)
AUDIT_RETENTION = timedelta(days=90)


class AccountNotMarkedForDeletionError(Exception):
    """Purga pedida para conta sem `DELETE /me`: defeito de quem chamou, nunca se apaga."""


def purge_owner(session: Session, owner_id: UUID) -> None:
    """Apaga chaves, sessões, capacidade e a conta; conta que já não existe é sucesso.

    A FK RESTRICT de `run`, `stored_object` e `transfer_slot` impede apagar a conta antes da
    purga de `runs` e `files`: a ordem errada falha em vez de deixar dado sem dono.
    """
    account = session.execute(
        select(app_user.c.deletion_requested_at).where(app_user.c.id == owner_id).with_for_update()
    ).first()
    if account is None:
        return
    if account.deletion_requested_at is None:
        raise AccountNotMarkedForDeletionError(f"conta {owner_id} não pediu exclusão")
    session.execute(delete(api_key).where(api_key.c.owner_id == owner_id))
    session.execute(delete(web_session).where(web_session.c.user_id == owner_id))
    capacity.purge_owner(session, owner_id)
    session.execute(delete(app_user).where(app_user.c.id == owner_id))


def accounts_pending_purge(session: Session, limit: int) -> list[UUID]:
    """Contas com exclusão pedida, as mais antigas primeiro."""
    return list(
        session.execute(
            select(app_user.c.id)
            .where(app_user.c.deletion_requested_at.is_not(None))
            .order_by(app_user.c.deletion_requested_at, app_user.c.id)
            .limit(limit)
        ).scalars()
    )


def count_overdue_deletions(session: Session, now: datetime) -> int:
    """Contas com exclusão pedida há `DELETION_OVERDUE_AFTER` ou mais (métrica para o alerta)."""
    count: int = session.execute(
        select(func.count())
        .select_from(app_user)
        .where(app_user.c.deletion_requested_at <= now - DELETION_OVERDUE_AFTER)
    ).scalar_one()
    return count


def delete_expired_login_states(session: Session, now: datetime, limit: int) -> int:
    return _delete_batch(
        session, login_state, login_state.c.key, login_state.c.expires_at <= now, limit
    )


def delete_ended_web_sessions(session: Session, now: datetime, limit: int) -> int:
    """Sessões cujo fim (revogação, inatividade ou teto, o primeiro) passou há mais de 7 dias."""
    ended_at = func.least(
        web_session.c.revoked_at, web_session.c.idle_expires_at, web_session.c.absolute_expires_at
    )
    return _delete_batch(
        session, web_session, web_session.c.id, ended_at < now - WEB_SESSION_RETENTION, limit
    )


def delete_old_audit_events(session: Session, now: datetime, limit: int) -> int:
    return _delete_batch(
        session,
        audit_event,
        audit_event.c.id,
        audit_event.c.occurred_at < now - AUDIT_RETENTION,
        limit,
    )


def _delete_batch(
    session: Session,
    table: Table,
    key: ColumnElement[object],
    condition: ColumnElement[bool],
    limit: int,
) -> int:
    chosen = select(key).where(condition).limit(limit).with_for_update(skip_locked=True)
    deleted = session.execute(delete(table).where(key.in_(chosen.scalar_subquery())).returning(key))
    return len(deleted.all())
