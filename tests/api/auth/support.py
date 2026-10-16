"""Google e GitHub falsos, navegador e conferências para os testes do login.

A sessão é a real (`identity/sessions.py`): `StoredSessions` lê as linhas de `web_session` que o
callback gravou, e o token do cookie é conferido contra o hash guardado.
"""

import base64
import hmac
import json
import re
from dataclasses import dataclass, field, replace
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx2 as httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export import SpanExporter
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.api import auth_login
from goblinshift.entrypoints.api.app import create_app
from goblinshift.identity.login_state import PostgresLoginStateCache
from goblinshift.identity.tables import app_user, audit_event, web_session
from goblinshift.platform.db import unit_of_work
from goblinshift.platform.settings import Settings
from tests.identity.fake_providers import GITHUB_CLIENT, GOOGLE_CLIENT, FakeProviders
from tests.identity.support import prepare_database

PUBLIC_ORIGIN = "https://goblinshift.test"
LOGIN_FAILED_LOCATION = "/?login_error=failed"
# A recusa leva o request_id do callback no fragmento, que o navegador nunca envia a servidor.
LOGIN_FAILED_WITH_REF = re.compile(
    re.escape(LOGIN_FAILED_LOCATION)
    + r"#ref=([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})"
)
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


def build_login_app(
    settings: Settings, fake: FakeProviders, *, span_exporter: SpanExporter | None = None
) -> FastAPI:
    """App real com os provedores apontando para o servidor falso (mesmos endereços)."""
    app = create_app(settings, span_exporter=span_exporter)
    prepare_database(settings, app.state.engine)
    cache = PostgresLoginStateCache(app.state.session_factory)
    app.state.login_providers = auth_login.build_login_providers(
        app.state.web_identity, cache, transport=fake.transport
    )
    return app


def open_signup(app: FastAPI) -> None:
    """Cadastro aberto nesta aplicação (o padrão é fechado, ADR 023)."""
    app.state.web_identity = replace(app.state.web_identity, signup_open=True)


def browser(app: FastAPI) -> TestClient:
    """Um navegador: cookies próprios, HTTPS (os cookies são Secure) e sem seguir redirects."""
    return TestClient(app, base_url=BROWSER_ORIGIN, follow_redirects=False)


@dataclass(frozen=True)
class StoredSession:
    """Uma linha de `web_session`, sem o hash exposto ao teste."""

    session_id: UUID
    user_id: UUID
    revoked: bool
    _secret_hash: bytes = field(repr=False)

    def issued_for(self, token: str) -> bool:
        """O cookie `token` é o desta sessão (o banco guarda só o SHA-256)."""
        return hmac.compare_digest(sha256(token.encode("ascii")).digest(), self._secret_hash)


@dataclass(frozen=True)
class StoredSessions:
    """Sessões que o login gravou no banco, na ordem de criação."""

    factory: sessionmaker[Session]

    @property
    def started(self) -> list[StoredSession]:
        with unit_of_work(self.factory) as session:
            rows = session.execute(
                select(
                    web_session.c.id,
                    web_session.c.user_id,
                    web_session.c.revoked_at,
                    web_session.c.secret_hash,
                ).order_by(web_session.c.created_at, web_session.c.id)
            ).all()
        return [
            StoredSession(row.id, row.user_id, row.revoked_at is not None, bytes(row.secret_hash))
            for row in rows
        ]

    @property
    def started_for(self) -> list[UUID]:
        return [stored.user_id for stored in self.started]

    @property
    def revoked(self) -> list[UUID]:
        return [stored.session_id for stored in self.started if stored.revoked]


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


def assert_login_failed(response: httpx.Response) -> UUID:
    """Toda recusa responde igual: o mesmo redirect, sem cookie de sessão nem detalhe.

    Devolve o código de referência, que é o `X-Request-ID` do próprio callback.
    """
    assert response.status_code == 303, response.text
    matched = LOGIN_FAILED_WITH_REF.fullmatch(response.headers["location"])
    assert matched is not None, response.headers["location"]
    assert matched.group(1) == response.headers["x-request-id"]
    assert response.content == b""
    assert "__Host-gs_session" not in response.headers.get("set-cookie", "")
    return UUID(matched.group(1))


def mask_ref(location: str) -> str:
    """O destino com o código aleatório trocado por `<ref>`, para comparar recusas."""
    return LOGIN_FAILED_WITH_REF.sub(f"{LOGIN_FAILED_LOCATION}#ref=<ref>", location)


def set_cookie_headers(response: httpx.Response) -> list[str]:
    return response.headers.get_list("set-cookie")


def cookie_header(response: httpx.Response, name: str) -> str:
    matches = [value for value in set_cookie_headers(response) if value.startswith(f"{name}=")]
    assert len(matches) == 1, set_cookie_headers(response)
    return matches[0]


def cookie_value(set_cookie: str) -> str:
    """Valor de um `Set-Cookie` (`nome=valor; atributos`)."""
    return set_cookie.split(";", 1)[0].split("=", 1)[1]


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


def audit_by_ref(factory: sessionmaker[Session], ref: UUID) -> list[tuple[str, UUID | None]]:
    """A consulta do operador: o código da tela de login leva ao evento e à conta."""
    with unit_of_work(factory) as session:
        rows = session.execute(
            select(audit_event.c.event_type, audit_event.c.user_id)
            .where(audit_event.c.request_id == ref)
            .order_by(audit_event.c.id)
        ).all()
    return [(row.event_type, row.user_id) for row in rows]


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
