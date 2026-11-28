"""Conta a partir da identidade externa do login (docs/GoblinShiftPlan.md, 1.8 e 3.6; Frente A).

A identidade é `(issuer, subject)`, nunca e-mail (ADR 008). Com o cadastro fechado (ADR 023),
identidade nova nasce `blocked` e só entra depois do `gs-admin unblock`.

Transaction script com a Session direto, na transação de quem chama (3.2).
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from uuid import UUID

from sqlalchemy import ColumnElement, and_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from goblinshift.identity.tables import app_user

ACTIVE_STATUS = "active"
BLOCKED_STATUS = "blocked"


@dataclass(frozen=True)
class ExternalIdentity:
    """Identidade já validada e normalizada pelo provedor (Google: `sub`; GitHub: `id`)."""

    issuer: str
    subject: str
    display_name: str
    # Hora da autenticação no provedor, quando ele a informa (o `auth_time` do Google).
    authenticated_at: datetime | None = None


class LoginRefused(Enum):
    """Motivo interno da recusa; o navegador recebe sempre o mesmo erro genérico."""

    BLOCKED = auto()
    PENDING_APPROVAL = auto()
    DELETION_REQUESTED = auto()
    # Reautenticação (`reauth=1`) que voltou com a identidade de outra conta, ou de nenhuma.
    REAUTH_MISMATCH = auto()


@dataclass(frozen=True)
class LoginRefusal:
    """Recusa com a conta que a provocou: o servidor a audita, o navegador nunca a vê."""

    account_id: UUID
    reason: LoginRefused


def _is_identity(identity: ExternalIdentity) -> ColumnElement[bool]:
    """A linha de `app_user` da identidade: `(issuer, subject)` é a chave única dela."""
    return and_(app_user.c.issuer == identity.issuer, app_user.c.subject == identity.subject)


def find_login_account(session: Session, identity: ExternalIdentity) -> UUID | None:
    """A conta da identidade, sem criar nem atualizar nada; None se ela ainda não existe."""
    user_id: UUID | None = session.execute(
        select(app_user.c.id).where(_is_identity(identity))
    ).scalar_one_or_none()
    return user_id


def resolve_login(
    session: Session, identity: ExternalIdentity, *, signup_open: bool, now: datetime
) -> UUID | LoginRefusal:
    """Cria ou atualiza a conta da identidade e decide se o login pode seguir.

    Um único `INSERT ... ON CONFLICT` cria a conta ou atualiza o nome de exibição, sem
    "consultar e depois inserir": dois callbacks simultâneos da mesma identidade chegam à mesma
    linha. A conta nova nasce `active` só com o cadastro aberto (ADR 023); o cadastro aberto não
    reativa uma conta que já existe bloqueada.

    O nome só é regravado em conta ativa e sem exclusão pedida: a conta recusada não grava dado do
    provedor. Aí o `RETURNING` volta vazio, e as mesmas colunas são lidas da linha, que o
    `ON CONFLICT` já travou nesta transação.
    """
    columns = (
        app_user.c.id,
        app_user.c.status,
        app_user.c.awaiting_approval,
        app_user.c.deletion_requested_at,
    )
    statement = (
        insert(app_user)
        .values(
            issuer=identity.issuer,
            subject=identity.subject,
            display_name=identity.display_name,
            status=ACTIVE_STATUS if signup_open else BLOCKED_STATUS,
            awaiting_approval=not signup_open,
            created_at=now,
        )
        .on_conflict_do_update(
            index_elements=[app_user.c.issuer, app_user.c.subject],
            set_={"display_name": identity.display_name},
            where=and_(
                app_user.c.status == ACTIVE_STATUS, app_user.c.deletion_requested_at.is_(None)
            ),
        )
        .returning(*columns)
    )
    account = session.execute(statement).one_or_none()
    if account is None:
        account = session.execute(select(*columns).where(_is_identity(identity))).one()
    user_id: UUID = account.id
    if account.deletion_requested_at is not None:
        return LoginRefusal(user_id, LoginRefused.DELETION_REQUESTED)
    if account.status == BLOCKED_STATUS:
        pending = account.awaiting_approval
        return LoginRefusal(
            user_id, LoginRefused.PENDING_APPROVAL if pending else LoginRefused.BLOCKED
        )
    return user_id
