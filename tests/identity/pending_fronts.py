"""Substitutos provisórios das funções das Frentes B e D que a Frente C chama (fatia 6).

A Frente C desenvolve em paralelo com B (sessões) e D (capacidade), cujas funções ainda são os
stubs da Fase 0. Cada substituto só entra se a função real ainda levantar `NotImplementedError`:
depois do merge da frente dona, a função real roda nos mesmos testes, sem edição, e este arquivo
pode sair na Fase final.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid7

import pytest
from fastapi import FastAPI
from sqlalchemy import delete, func, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift import capacity
from goblinshift.capacity.tables import capacity_pool, resource_reservation
from goblinshift.entrypoints.api.dependencies import (
    CurrentPrincipal,
    Now,
    SessionPrincipal,
    require_recent_session,
    require_session,
)
from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode
from goblinshift.identity import Principal, sessions
from goblinshift.identity.sessions import RECENT_AUTH_WINDOW
from goblinshift.identity.tables import api_key, web_session

_PROBE_TIME = datetime(2026, 1, 1, tzinfo=UTC)


def install_domain_stand_ins(
    monkeypatch: pytest.MonkeyPatch, factory: sessionmaker[Session]
) -> None:
    """`revoke_all_credentials` (B) e `capacity.purge_owner` (D), se ainda forem stubs."""
    if _raises_not_implemented(
        factory, lambda s: sessions.revoke_all_credentials(s, uuid7(), _PROBE_TIME)
    ):
        monkeypatch.setattr(sessions, "revoke_all_credentials", _revoke_all_credentials)
    if _raises_not_implemented(factory, lambda s: capacity.purge_owner(s, uuid7())):
        monkeypatch.setattr(capacity, "purge_owner", _purge_capacity)


def install_route_stand_ins(app: FastAPI) -> None:
    """`require_session` e `require_recent_session` (B), se ainda forem stubs."""
    key = Principal(owner_id=uuid7(), scopes=frozenset(), via="api_key")
    try:
        require_session(key)
    except NotImplementedError:
        app.dependency_overrides[require_session] = _require_session
    except ApiProblem:
        pass
    session = Principal(
        owner_id=uuid7(),
        scopes=frozenset(),
        via="session",
        session_id=uuid7(),
        authenticated_at=_PROBE_TIME,
    )
    try:
        require_recent_session(session, _PROBE_TIME)
    except NotImplementedError:
        app.dependency_overrides[require_recent_session] = _require_recent_session


def _raises_not_implemented(
    factory: sessionmaker[Session], probe: Callable[[Session], object]
) -> bool:
    with factory() as session:
        try:
            probe(session)
        except NotImplementedError:
            return True
        finally:
            session.rollback()
    return False


def _revoke_all_credentials(session: Session, owner_id: UUID, now: datetime) -> None:
    session.execute(
        update(web_session)
        .where(web_session.c.user_id == owner_id, web_session.c.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    session.execute(
        update(api_key)
        .where(api_key.c.owner_id == owner_id)
        .values(revoked_at=func.coalesce(api_key.c.revoked_at, now))
    )


def _purge_capacity(session: Session, owner_id: UUID) -> None:
    session.execute(delete(resource_reservation).where(resource_reservation.c.owner_id == owner_id))
    session.execute(delete(capacity_pool).where(capacity_pool.c.owner_id == owner_id))


def _require_session(principal: CurrentPrincipal) -> Principal:
    if principal.via != "session":
        raise ApiProblem(ProblemCode.FORBIDDEN, "Esta operação exige a sessão web.")
    return principal


def _require_recent_session(principal: SessionPrincipal, now: Now) -> Principal:
    assert principal.authenticated_at is not None
    if now - principal.authenticated_at > RECENT_AUTH_WINDOW:
        raise ApiProblem(ProblemCode.REAUTH_REQUIRED, "Entre de novo para continuar.")
    return principal
