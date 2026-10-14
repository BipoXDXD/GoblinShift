"""Google e GitHub falsos, navegador e conferências para os testes do login.

A sessão web é da Frente B. Enquanto `start_session` e companhia forem stubs, `FakeSessions`
cumpre o contrato delas (`identity/sessions.py`) para o callback poder completar; o teste
`test_login_completo_com_a_sessao_real` fica em xfail estrito e avisa quando a Frente B chegar.
"""

import base64
import json
import secrets
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx2 as httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.api import auth_login
from goblinshift.entrypoints.api.app import create_app
from goblinshift.identity import IssuedSession, Principal, Scope
from goblinshift.identity.login_state import PostgresLoginStateCache
from goblinshift.identity.tables import app_user, audit_event
from goblinshift.platform.db import unit_of_work
from goblinshift.platform.settings import Settings
from tests.identity.fake_providers import GITHUB_CLIENT, GOOGLE_CLIENT, FakeProviders
from tests.identity.support import prepare_database

PUBLIC_ORIGIN = "https://goblinshift.test"
LOGIN_FAILED_LOCATION = "/?login_error=failed"
BROWSER_ORIGIN = "https://testserver"


def login_settings(db_settings: Settings, tmp_path: Path, *, github: bool = True) -> Settings:
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir(exist_ok=True)
    google_secret = secrets_dir / "google"
    google_secret.write_text(GOOGLE_CLIENT.client_secret)
    update: dict[str, Any] = {
        "storage_root": tmp_path / "storage",
        "public_origin": PUBLIC_ORIGIN,
        "google_client_id": GOOGLE_CLIENT.client_id,
        "google_client_secret_file": google_secret,
    }
    if github:
        github_secret = secrets_dir / "github"
        github_secret.write_text(GITHUB_CLIENT.client_secret)
        update |= {
            "github_client_id": GITHUB_CLIENT.client_id,
            "github_client_secret_file": github_secret,
        }
    return db_settings.model_copy(update=update)


def build_login_app(settings: Settings, fake: FakeProviders) -> FastAPI:
    """App real com os provedores apontando para o servidor falso (mesmos endereços)."""
    app = create_app(settings)
    prepare_database(settings, app.state.engine)
    cache = PostgresLoginStateCache(app.state.session_factory)
    app.state.login_providers = auth_login.build_login_providers(
        app.state.web_identity, cache, transport=fake.transport
    )
    return app


def open_signup(app: FastAPI) -> None:
    """Cadastro aberto nesta aplicação (o padrão é fechado, ADR 022)."""
    app.state.web_identity = replace(app.state.web_identity, signup_open=True)


def browser(app: FastAPI) -> TestClient:
    """Um navegador: cookies próprios, HTTPS (os cookies são Secure) e sem seguir redirects."""
    return TestClient(app, base_url=BROWSER_ORIGIN, follow_redirects=False)


@dataclass
class FakeSessions:
    """Cumpre o contrato de `identity.sessions` (Frente B) em memória."""

    started: list[IssuedSession] = field(default_factory=list)
    started_for: list[UUID] = field(default_factory=list)
    revoked: list[UUID] = field(default_factory=list)
    by_token: dict[str, Principal] = field(default_factory=dict)

    def start_session(
        self, session: Session, *, user_id: UUID, authenticated_at: datetime, now: datetime
    ) -> IssuedSession:
        issued = IssuedSession(
            session_id=uuid4(),
            token=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(32),
            idle_expires_at=now,
            absolute_expires_at=now,
        )
        self.started.append(issued)
        self.started_for.append(user_id)
        self.by_token[issued.token] = Principal(
            owner_id=user_id,
            scopes=frozenset(Scope),
            via="session",
            session_id=issued.session_id,
            authenticated_at=authenticated_at,
        )
        return issued

    def authenticate_session(self, session: Session, token: str, now: datetime) -> Principal | None:
        principal = self.by_token.get(token)
        if principal is None or principal.session_id in self.revoked:
            return None
        return principal

    def revoke_session(self, session: Session, session_id: UUID, now: datetime) -> None:
        self.revoked.append(session_id)


def start_login(client: TestClient, provider: str, **params: str) -> httpx.Response:
    response = client.get(f"/auth/login/{provider}", params=params)
    assert response.status_code == 302, response.text
    return response


def authorize_query(response: httpx.Response) -> dict[str, str]:
    query = parse_qs(urlsplit(response.headers["location"]).query)
    return {name: values[0] for name, values in query.items()}


def login(client: TestClient, fake: FakeProviders, provider: str, **params: str) -> httpx.Response:
    """Fluxo inteiro num navegador: início, aprovação no provedor e callback."""
    started = start_login(client, provider, **params)
    return client.get(fake.consent(started.headers["location"]))


def assert_login_failed(response: httpx.Response) -> None:
    """Toda recusa responde igual: o mesmo redirect, sem cookie de sessão nem detalhe."""
    assert response.status_code == 303, response.text
    assert response.headers["location"] == LOGIN_FAILED_LOCATION
    assert response.content == b""
    assert "__Host-gs_session" not in response.headers.get("set-cookie", "")


def set_cookie_headers(response: httpx.Response) -> list[str]:
    return response.headers.get_list("set-cookie")


def cookie_header(response: httpx.Response, name: str) -> str:
    matches = [value for value in set_cookie_headers(response) if value.startswith(f"{name}=")]
    assert len(matches) == 1, set_cookie_headers(response)
    return matches[0]


def login_cookie_payload(value: str) -> dict[str, Any]:
    """Conteúdo do cookie assinado do Starlette (base64 de JSON, assinado e não cifrado)."""
    encoded = value.split(".")[0]
    payload: dict[str, Any] = json.loads(base64.b64decode(encoded + "=" * (-len(encoded) % 4)))
    return payload


def audit_events(factory: sessionmaker[Session]) -> list[tuple[str, str, UUID | None]]:
    with unit_of_work(factory) as session:
        rows = session.execute(
            select(audit_event.c.event_type, audit_event.c.outcome, audit_event.c.user_id).order_by(
                audit_event.c.id
            )
        ).all()
    return [(row.event_type, row.outcome, row.user_id) for row in rows]


def accounts(factory: sessionmaker[Session]) -> list[dict[str, Any]]:
    with unit_of_work(factory) as session:
        rows = session.execute(
            select(
                app_user.c.id,
                app_user.c.issuer,
                app_user.c.subject,
                app_user.c.display_name,
                app_user.c.status,
            ).order_by(app_user.c.created_at)
        ).mappings()
        return [dict(row) for row in rows]
