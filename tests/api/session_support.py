"""Sessões web prontas para os testes da API, sem passar pelo login do provedor."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.api.cookies import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from goblinshift.identity import IssuedSession, start_session
from goblinshift.platform.db import unit_of_work
from goblinshift.platform.settings import WebIdentityConfig


def open_session(
    app: FastAPI,
    user_id: UUID,
    *,
    authenticated_at: datetime | None = None,
    now: datetime | None = None,
) -> IssuedSession:
    factory: sessionmaker[Session] = app.state.session_factory
    web_identity: WebIdentityConfig = app.state.web_identity
    now = now or datetime.now(UTC)
    with unit_of_work(factory) as session:
        return start_session(
            session,
            user_id=user_id,
            authenticated_at=authenticated_at or now,
            now=now,
            csrf_key=web_identity.csrf_key,
        )


def session_headers(
    app: FastAPI, issued: IssuedSession, *, csrf_header: str | None = None
) -> dict[str, str]:
    """Cookies da sessão e os headers de uma mutação legítima feita pela SPA.

    `csrf_header` troca o valor do `X-CSRF-Token`; por padrão ele repete o cookie.
    """
    web_identity: WebIdentityConfig = app.state.web_identity
    return {
        "Cookie": f"{SESSION_COOKIE}={issued.token}; {CSRF_COOKIE}={issued.csrf_token}",
        CSRF_HEADER: issued.csrf_token if csrf_header is None else csrf_header,
        "Sec-Fetch-Site": "same-origin",
        "Origin": web_identity.public_origin,
    }
