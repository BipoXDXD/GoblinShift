"""Provedor hostil ou quebrado: corpo enorme, resposta lenta e JSON com forma errada.

Em todos os casos o callback responde o erro genérico de login, sem ler o corpo inteiro, sem
passar do prazo total e sem virar 500.
"""

import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.api import auth_login
from goblinshift.identity.login_state import PostgresLoginStateCache
from goblinshift.identity.providers import GITHUB_USER_URL, GOOGLE_JWKS_URL, GOOGLE_TOKEN_URL
from tests.api.auth.support import (
    StoredSessions,
    accounts,
    assert_login_failed,
    audit_events,
    open_signup,
    start_login,
)
from tests.identity.fake_providers import FakeProviders

pytestmark = pytest.mark.integration

# Prazo por fase do httpx nos testes de lentidão; o do callback inteiro é o dobro.
PHASE_TIMEOUT_S = 0.3
CALLBACK_DEADLINE_S = auth_login.LOGIN_FETCH_TIMEOUT_FACTOR * PHASE_TIMEOUT_S
# Folga para o agendamento do teste, a auditoria e o redirect de recusa (CI lenta).
SLACK_S = 0.3


def assert_refused_without_effects(
    factory: sessionmaker[Session], sessions: StoredSessions, response: httpx.Response
) -> None:
    assert_login_failed(response)
    assert sessions.started == []
    assert accounts(factory) == []
    assert audit_events(factory) == [("LOGIN_FAILED", "failure", None)]


def callback_after_consent(client: TestClient, fake: FakeProviders, provider: str) -> str:
    return fake.consent(start_login(client, provider).headers["location"])


def use_phase_timeout(app: FastAPI, transport: httpx.AsyncBaseTransport) -> None:
    """Provedores com `oauth_http_timeout_s` curto, que o callback também usa no prazo total."""
    web = replace(app.state.web_identity, oauth_http_timeout_s=PHASE_TIMEOUT_S)
    app.state.web_identity = web
    cache = PostgresLoginStateCache(app.state.session_factory)
    app.state.login_providers = auth_login.build_login_providers(web, cache, transport=transport)


# --- Teto de bytes ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "url"),
    [
        ("google", GOOGLE_TOKEN_URL),
        ("google", GOOGLE_JWKS_URL),
        ("github", GITHUB_USER_URL),
    ],
    ids=["token", "jwks", "github-user"],
)
def test_resposta_de_1_mb_vira_o_erro_generico_sem_ler_tudo(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
    provider: str,
    url: str,
) -> None:
    open_signup(login_app)
    fake.oversized_url = url
    callback = callback_after_consent(client, fake, provider)

    response = client.get(callback)

    assert_refused_without_effects(factory, sessions, response)
    assert fake.oversized_chunks * 64 * 1024 >= 1024 * 1024
    assert fake.chunks_served <= 2


# --- Prazo total ------------------------------------------------------------------------------


def test_provedor_lento_em_cada_fase_respeita_o_prazo_total(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
) -> None:
    # Token e JWKS somam 1 s; o callback desiste em 0,6 s.
    open_signup(login_app)
    use_phase_timeout(login_app, fake.transport)
    fake.latency_s = 0.5
    callback = callback_after_consent(client, fake, "google")

    started = time.monotonic()
    response = client.get(callback)
    elapsed = time.monotonic() - started

    assert_refused_without_effects(factory, sessions, response)
    assert elapsed < CALLBACK_DEADLINE_S + SLACK_S


class DripServer:
    """Token endpoint que responde o cabeçalho e depois pinga um byte a cada `interval_s`.

    Cada byte chega bem antes do prazo de leitura do httpx, então só o prazo total do callback
    corta a conversa.
    """

    def __init__(self, interval_s: float, total_bytes: int) -> None:
        self.stop = threading.Event()
        self.requests = 0
        drip = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                drip.requests += 1
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                try:
                    for _ in range(total_bytes):
                        if drip.stop.wait(interval_s):
                            return
                        self.wfile.write(b" ")
                        self.wfile.flush()
                except BrokenPipeError, ConnectionResetError:
                    return

            def log_message(self, format: str, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/token"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> DripServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop.set()
        self._server.shutdown()
        self._server.server_close()


class TokenEndpointAt(httpx.AsyncBaseTransport):
    """Manda o token endpoint do Google a um servidor HTTP de verdade e o resto ao provedor falso.

    A rede real passa pelo `AsyncHTTPTransport`, que cumpre os prazos por fase do httpx; o
    `MockTransport` do provedor falso os ignora.
    """

    def __init__(self, url: str, fake: httpx.AsyncBaseTransport) -> None:
        self._url = httpx.URL(url)
        self._real = httpx.AsyncHTTPTransport()
        self._fake = fake

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) != GOOGLE_TOKEN_URL:
            return await self._fake.handle_async_request(request)
        request.url = self._url
        return await self._real.handle_async_request(request)

    async def aclose(self) -> None:
        await self._real.aclose()


@pytest.fixture
def drip() -> Iterator[DripServer]:
    # 0,05 s por byte, 200 bytes: 10 s até o fim do corpo, contra o prazo de 0,6 s.
    with DripServer(interval_s=0.05, total_bytes=200) as server:
        yield server


def test_token_endpoint_que_pinga_bytes_nao_segura_o_login_alem_do_prazo(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
    drip: DripServer,
) -> None:
    open_signup(login_app)
    use_phase_timeout(login_app, TokenEndpointAt(drip.url, fake.transport))
    callback = callback_after_consent(client, fake, "google")

    started = time.monotonic()
    response = client.get(callback)
    elapsed = time.monotonic() - started

    assert drip.requests == 1
    assert_refused_without_effects(factory, sessions, response)
    assert elapsed < CALLBACK_DEADLINE_S + SLACK_S


# --- Forma errada -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("token_body", "jwks_body"),
    [
        (b"[]", None),
        (b'{"token_type": "Bearer", "expires_in": 3600}', None),
        (b'"texto"', None),
        (b'{"access_token": "a", "token_type": "Bearer", "id_token": 5}', None),
        (None, b'{"keys": "x"}'),
        (None, b"[]"),
        (None, b"null"),
        (None, b'{"keys": [{"kty": "RSA", "kid": "chave-de-teste", "n": 1, "e": 2}]}'),
    ],
    ids=[
        "token-lista",
        "token-sem-access-token",
        "token-texto",
        "id-token-numero",
        "jwks-keys-texto",
        "jwks-lista",
        "jwks-null",
        "jwks-chave-com-numeros",
    ],
)
def test_forma_errada_do_provedor_vira_o_erro_generico(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
    token_body: bytes | None,
    jwks_body: bytes | None,
) -> None:
    open_signup(login_app)
    fake.token_body = token_body
    fake.jwks_body = jwks_body
    callback = callback_after_consent(client, fake, "google")

    response = client.get(callback)

    assert_refused_without_effects(factory, sessions, response)
