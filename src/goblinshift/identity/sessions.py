"""Sessão web opaca e revogável no PostgreSQL (docs/GoblinShiftPlan.md, 3.6; Frente B).

Token de 256 bits no cookie `__Host-gs_session`, só o SHA-256 no banco, comparação em tempo
constante. Expira por 30 min sem uso ou 8 h depois do login no provedor, o que vier antes;
`last_seen_at` é gravado no máximo uma vez por minuto. Conta bloqueada ou com exclusão pedida
invalida a sessão.

Contrato fixado na Fase 0 da fatia 6; os corpos são da Frente B.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from goblinshift.identity.principal import Principal

IDLE_TIMEOUT = timedelta(minutes=30)
ABSOLUTE_TIMEOUT = timedelta(hours=8)
LAST_SEEN_RESOLUTION = timedelta(minutes=1)
# Emitir chave e excluir a conta exigem login no provedor há no máximo isto (3.6).
RECENT_AUTH_WINDOW = timedelta(minutes=10)


@dataclass(frozen=True)
class IssuedSession:
    """Sessão recém-criada; os tokens só existem aqui, para virar cookies."""

    session_id: UUID
    token: str = field(repr=False)  # valor do cookie __Host-gs_session
    csrf_token: str = field(repr=False)  # valor do cookie __Host-gs_csrf
    idle_expires_at: datetime
    absolute_expires_at: datetime


def start_session(
    session: Session, *, user_id: UUID, authenticated_at: datetime, now: datetime
) -> IssuedSession:
    raise NotImplementedError("Frente B da fatia 6")


def authenticate_session(session: Session, token: str, now: datetime) -> Principal | None:
    """Principal com todos os escopos, ou None para token desconhecido, vencido ou revogado."""
    raise NotImplementedError("Frente B da fatia 6")


def revoke_session(session: Session, session_id: UUID, now: datetime) -> None:
    """Revoga a sessão; repetir não muda o instante da primeira revogação."""
    raise NotImplementedError("Frente B da fatia 6")


def revoke_all_credentials(session: Session, owner_id: UUID, now: datetime) -> None:
    """Revoga todas as sessões e todas as chaves de API da conta (`logout-all`, bloqueio)."""
    raise NotImplementedError("Frente B da fatia 6")
