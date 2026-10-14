"""Substitutos provisórios das funções das Frentes A, B e D que a Frente C usa (fatia 6).

A Frente C desenvolve em paralelo com A (login), B (sessões) e D (capacidade), cujas funções ainda
são os stubs da Fase 0. Cada substituto só entra se a função real ainda for o stub, reconhecido
pelo código e não por uma chamada (a assinatura das dependências pode mudar na frente dona):
depois do merge, a função real roda nos mesmos testes, sem edição, e este arquivo sai na Fase
final.
"""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

import pytest
from fastapi import FastAPI
from sqlalchemy import delete, func, update
from sqlalchemy.orm import Session

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


def is_stub(function: Callable[..., object]) -> bool:
    """O corpo da Fase 0 só levanta `NotImplementedError`; a implementação real não o cita."""
    return "NotImplementedError" in function.__code__.co_names


def install_domain_stand_ins(monkeypatch: pytest.MonkeyPatch) -> None:
    """`revoke_all_credentials` (B) e `capacity.purge_owner` (D), se ainda forem stubs."""
    if is_stub(sessions.revoke_all_credentials):
        monkeypatch.setattr(sessions, "revoke_all_credentials", _revoke_all_credentials)
    if is_stub(capacity.purge_owner):
        monkeypatch.setattr(capacity, "purge_owner", _purge_capacity)


def install_route_stand_ins(app: FastAPI) -> None:
    """`require_session` e `require_recent_session` (B), se ainda forem stubs."""
    if is_stub(require_session):
        app.dependency_overrides[require_session] = _require_session
    if is_stub(require_recent_session):
        app.dependency_overrides[require_recent_session] = _require_recent_session


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
