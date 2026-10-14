"""Conta a partir da identidade externa do login (docs/GoblinShiftPlan.md, 1.8 e 3.6; Frente A).

A identidade é `(issuer, subject)`, nunca e-mail (ADR 008). Com o cadastro fechado (ADR 023),
identidade nova nasce `blocked` e só entra depois do `gs-admin unblock`.

Transaction script com a Session direto, na transação de quem chama (3.2).
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from goblinshift.identity.tables import app_user, web_session

ACTIVE_STATUS = "active"
BLOCKED_STATUS = "blocked"


@dataclass(frozen=True)
class ExternalIdentity:
    """Identidade já validada e normalizada pelo provedor (Google: `sub`; GitHub: `id`)."""

    issuer: str
    subject: str
    display_name: str


class LoginRefused(Enum):
    """Motivo interno da recusa; o navegador recebe sempre o mesmo erro genérico."""

    BLOCKED = auto()
    PENDING_APPROVAL = auto()
    DELETION_REQUESTED = auto()


def resolve_login(
    session: Session, identity: ExternalIdentity, *, signup_open: bool, now: datetime
) -> UUID | LoginRefused:
    """Cria ou atualiza a conta da identidade e decide se o login pode seguir.

    Um único `INSERT ... ON CONFLICT` cria a conta ou atualiza o nome de exibição, sem
    "consultar e depois inserir": dois callbacks simultâneos da mesma identidade chegam à mesma
    linha. A conta nova nasce `active` só com o cadastro aberto (ADR 023); o cadastro aberto não
    reativa uma conta que já existe bloqueada.
    """
    statement = (
        insert(app_user)
        .values(
            issuer=identity.issuer,
            subject=identity.subject,
            display_name=identity.display_name,
            status=ACTIVE_STATUS if signup_open else BLOCKED_STATUS,
            created_at=now,
        )
        .on_conflict_do_update(
            index_elements=[app_user.c.issuer, app_user.c.subject],
            set_={"display_name": identity.display_name},
        )
        .returning(app_user.c.id, app_user.c.status, app_user.c.deletion_requested_at)
    )
    account = session.execute(statement).one()
    if account.deletion_requested_at is not None:
        return LoginRefused.DELETION_REQUESTED
    if account.status == BLOCKED_STATUS:
        return _blocked_reason(session, account.id)
    user_id: UUID = account.id
    return user_id


def _blocked_reason(session: Session, user_id: UUID) -> LoginRefused:
    """Pendente é a conta bloqueada que nunca teve sessão (o critério do ADR 023)."""
    had_session = session.execute(
        select(exists().where(web_session.c.user_id == user_id))
    ).scalar_one()
    return LoginRefused.BLOCKED if had_session else LoginRefused.PENDING_APPROVAL
