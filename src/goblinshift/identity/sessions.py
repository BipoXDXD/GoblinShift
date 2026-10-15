"""Sessão web opaca e revogável no PostgreSQL (docs/GoblinShiftPlan.md, 3.6; Frente B).

Token de 256 bits no cookie `__Host-gs_session`, só o SHA-256 no banco, comparação em tempo
constante. Expira por 30 min sem uso ou 8 h depois do login no provedor, o que vier antes;
`last_seen_at` é gravado no máximo uma vez por minuto, e a inatividade conta a partir dele.
Conta bloqueada ou com exclusão pedida invalida a sessão.

Transaction scripts com a Session direto, como `accounts`: cada função roda na transação de quem
a chama.
"""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from goblinshift.identity.csrf import issue_csrf_token
from goblinshift.identity.principal import Principal, Scope
from goblinshift.identity.tables import api_key, app_user, web_session

IDLE_TIMEOUT = timedelta(minutes=30)
ABSOLUTE_TIMEOUT = timedelta(hours=8)
LAST_SEEN_RESOLUTION = timedelta(minutes=1)
# Emitir chave e excluir a conta exigem login no provedor há no máximo isto (3.6).
RECENT_AUTH_WINDOW = timedelta(minutes=10)

TOKEN_BYTES = 32
ACTIVE_STATUS = "active"
# `secrets.token_urlsafe(32)`: 43 caracteres base64url. fullmatch e classes explícitas.
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
_TOKEN_LENGTH = 43
# A sessão usa as permissões da conta: todos os escopos (1.6).
_SESSION_SCOPES = frozenset(Scope)


@dataclass(frozen=True)
class IssuedSession:
    """Sessão recém-criada; os tokens só existem aqui, para virar cookies."""

    session_id: UUID
    token: str = field(repr=False)  # valor do cookie __Host-gs_session
    csrf_token: str = field(repr=False)  # valor do cookie __Host-gs_csrf
    idle_expires_at: datetime
    absolute_expires_at: datetime


def start_session(
    session: Session,
    *,
    user_id: UUID,
    authenticated_at: datetime,
    now: datetime,
    csrf_key: bytes,
) -> IssuedSession:
    """Abre uma sessão nova, sempre com token novo (contra fixação de sessão).

    `authenticated_at` é o login no provedor; um login de 8 h ou mais já nasceria vencido, o que
    só acontece por erro de quem chama.
    """
    absolute_expires_at = authenticated_at + ABSOLUTE_TIMEOUT
    if absolute_expires_at <= now:
        raise ValueError("login no provedor há 8 h ou mais não abre sessão")
    idle_expires_at = min(now + IDLE_TIMEOUT, absolute_expires_at)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    session_id: UUID = session.execute(
        insert(web_session)
        .values(
            user_id=user_id,
            secret_hash=_hash_token(token),
            authenticated_at=authenticated_at,
            last_seen_at=now,
            idle_expires_at=idle_expires_at,
            absolute_expires_at=absolute_expires_at,
        )
        .returning(web_session.c.id)
    ).scalar_one()
    return IssuedSession(
        session_id=session_id,
        token=token,
        csrf_token=issue_csrf_token(csrf_key, session_id),
        idle_expires_at=idle_expires_at,
        absolute_expires_at=absolute_expires_at,
    )


def authenticate_session(session: Session, token: str, now: datetime) -> Principal | None:
    """Principal com todos os escopos, ou None para token desconhecido, vencido ou revogado.

    Registrar o uso faz parte de autenticar: é o que estende a inatividade.
    """
    if len(token) != _TOKEN_LENGTH or _TOKEN_PATTERN.fullmatch(token) is None:
        return None
    presented_hash = _hash_token(token)
    row = session.execute(
        select(
            web_session.c.id,
            web_session.c.user_id,
            web_session.c.secret_hash,
            web_session.c.authenticated_at,
            web_session.c.idle_expires_at,
            web_session.c.absolute_expires_at,
            web_session.c.revoked_at,
            app_user.c.status,
            app_user.c.deletion_requested_at,
        )
        .join(app_user, app_user.c.id == web_session.c.user_id)
        .where(web_session.c.secret_hash == presented_hash)
    ).first()
    # A busca é pelo hash: quem envia o token não controla os bytes do hash, então o tempo do
    # índice nada revela do segredo. A comparação explícita fecha o contrato de 3.6.
    if row is None or not hmac.compare_digest(row.secret_hash, presented_hash):
        return None
    usable = (
        row.revoked_at is None
        and now < row.idle_expires_at
        and now < row.absolute_expires_at
        and row.status == ACTIVE_STATUS
        and row.deletion_requested_at is None
    )
    if not usable:
        return None
    _record_use(session, row.id, now)
    return Principal(
        owner_id=row.user_id,
        scopes=_SESSION_SCOPES,
        via="session",
        session_id=row.id,
        authenticated_at=row.authenticated_at,
    )


def revoke_session(session: Session, session_id: UUID, now: datetime) -> None:
    """Revoga a sessão; repetir não muda o instante da primeira revogação."""
    session.execute(
        update(web_session)
        .where(web_session.c.id == session_id)
        .values(revoked_at=func.coalesce(web_session.c.revoked_at, now))
    )


def revoke_all_credentials(session: Session, owner_id: UUID, now: datetime) -> None:
    """Revoga todas as sessões e todas as chaves de API da conta (`logout-all`, bloqueio)."""
    session.execute(
        update(web_session)
        .where(web_session.c.user_id == owner_id, web_session.c.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    session.execute(
        update(api_key)
        .where(api_key.c.owner_id == owner_id, api_key.c.revoked_at.is_(None))
        .values(revoked_at=now)
    )


def _record_use(session: Session, session_id: UUID, now: datetime) -> None:
    # UPDATE condicional, como em `accounts._record_use`: no máximo uma escrita por minuto, e
    # requisições simultâneas não gravam todas. A inatividade nunca passa do teto absoluto.
    session.execute(
        update(web_session)
        .where(
            web_session.c.id == session_id,
            web_session.c.last_seen_at <= now - LAST_SEEN_RESOLUTION,
        )
        .values(
            last_seen_at=now,
            idle_expires_at=func.least(now + IDLE_TIMEOUT, web_session.c.absolute_expires_at),
        )
    )


def _hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii")).digest()
