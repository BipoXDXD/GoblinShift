"""Autenticação única por sessão ou chave, cookies, logout e logout-all (3.6)."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

import httpx2 as httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.responses import Response

from goblinshift.entrypoints.api.cookies import (
    CSRF_COOKIE,
    CSRF_HEADER,
    SESSION_COOKIE,
    clear_session_cookies,
    set_session_cookies,
)
from goblinshift.entrypoints.api.dependencies import current_time, require_recent_session
from goblinshift.identity import (
    IssuedSession,
    Principal,
    authenticate_api_key,
    issue_csrf_token,
)
from goblinshift.identity.tables import audit_event
from goblinshift.platform.db import unit_of_work
from tests.api.session_support import open_session, session_headers
from tests.api.support import assert_problem
from tests.identity.support import new_key, new_user

ME = "/api/v1/me"
LOGOUT = "/auth/logout"
LOGOUT_ALL = "/auth/logout-all"
RECENT = "/api/v1/_test/recent-session"
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
ISSUED = IssuedSession(
    session_id=UUID("00000000-0000-0000-0000-000000000001"),
    token="S" * 43,
    csrf_token="C" * 87,
    idle_expires_at=NOW,
    absolute_expires_at=NOW,
)
# Token no formato real, sem sessão viva a que o HMAC se ligue (logout depois de expirar).
ANONYMOUS_CSRF = issue_csrf_token(b"k" * 32, UUID("00000000-0000-0000-0000-000000000002"))


def _cookie_only(issued: IssuedSession) -> dict[str, str]:
    return {"Cookie": f"{SESSION_COOKIE}={issued.token}"}


def _freeze(app: FastAPI, now: datetime) -> None:
    app.dependency_overrides[current_time] = lambda: now


def _audit_events(factory: sessionmaker[Session]) -> list[tuple[str, UUID | None, str]]:
    with factory() as session:
        rows = session.execute(
            select(audit_event.c.event_type, audit_event.c.user_id, audit_event.c.outcome).order_by(
                audit_event.c.id
            )
        ).all()
    return [(row.event_type, row.user_id, row.outcome) for row in rows]


# --- Cookies (sem banco) ----------------------------------------------------------------------


def test_cookies_da_sessao_tem_exatamente_os_atributos_de_3_6() -> None:
    response = Response()

    set_session_cookies(response, ISSUED)

    assert response.headers.getlist("set-cookie") == [
        f"{SESSION_COOKIE}={'S' * 43}; Path=/; Secure; HttpOnly; SameSite=Lax",
        f"{CSRF_COOKIE}={'C' * 87}; Path=/; Secure; SameSite=Strict",
    ]


def test_apagar_os_cookies_repete_os_atributos_e_zera_o_prazo() -> None:
    response = Response()

    clear_session_cookies(response)

    assert response.headers.getlist("set-cookie") == [
        f"{SESSION_COOKIE}=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0",
        f"{CSRF_COOKIE}=; Path=/; Secure; SameSite=Strict; Max-Age=0",
    ]


# --- Autenticação por sessão ------------------------------------------------------------------


@pytest.fixture
def recent_app(app: FastAPI) -> FastAPI:
    """O app com uma rota que exige sessão recente; as rotas reais são da Frente C."""

    @app.post(RECENT)
    def recent(
        principal: Annotated[Principal, Depends(require_recent_session)],
    ) -> dict[str, str]:
        return {"owner_id": str(principal.owner_id)}

    return app


@pytest.fixture
def recent_client(recent_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(recent_app) as client:
        yield client


@pytest.mark.integration
def test_sessao_autentica_com_os_escopos_da_conta(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    user_id = new_user(factory)
    issued = open_session(app, user_id)

    response = client.get(ME, headers=_cookie_only(issued))

    assert response.status_code == 200
    body = response.json()
    # Só o que a autenticação decide; limites e consumo são de `test_me_usage.py`.
    assert (body["id"], body["via"]) == (str(user_id), "session")
    assert body["scopes"] == ["files:read", "files:write", "runs:read", "runs:write"]


@pytest.mark.integration
def test_cookie_e_bearer_juntos_400(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    user_id = new_user(factory)
    issued = open_session(app, user_id)
    token = new_key(factory, user_id).token

    response = client.get(ME, headers={**_cookie_only(issued), "Authorization": f"Bearer {token}"})

    assert_problem(response, 400, "BAD_REQUEST")
    assert "Conta de teste" not in response.text


@pytest.mark.integration
@pytest.mark.parametrize(
    "token",
    [
        pytest.param("", id="vazio"),
        pytest.param("S" * 43, id="desconhecido"),
        pytest.param("nao-e-um-token", id="malformado"),
    ],
)
def test_cookie_de_sessao_invalido_401(client: TestClient, token: str) -> None:
    response = client.get(ME, headers={"Cookie": f"{SESSION_COOKIE}={token}"})

    assert_problem(response, 401, "AUTHENTICATION_REQUIRED")


@pytest.mark.integration
def test_sessao_expira_com_30_min_sem_uso(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    issued = open_session(app, new_user(factory), now=NOW)

    _freeze(app, NOW + timedelta(minutes=29, seconds=59))
    assert client.get(ME, headers=_cookie_only(issued)).status_code == 200
    _freeze(app, NOW + timedelta(minutes=29, seconds=59) + timedelta(minutes=30))
    response = client.get(ME, headers=_cookie_only(issued))

    assert_problem(response, 401, "AUTHENTICATION_REQUIRED")


@pytest.mark.integration
def test_sessao_expira_8h_apos_login_mesmo_com_uso(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    issued = open_session(
        app, new_user(factory), authenticated_at=NOW - timedelta(hours=7), now=NOW
    )

    for minutes in (25, 50):
        _freeze(app, NOW + timedelta(minutes=minutes))
        assert client.get(ME, headers=_cookie_only(issued)).status_code == 200
    _freeze(app, NOW + timedelta(hours=1))
    response = client.get(ME, headers=_cookie_only(issued))

    assert_problem(response, 401, "AUTHENTICATION_REQUIRED")


# --- Autenticação recente ---------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "age",
    [pytest.param(timedelta(0), id="agora"), pytest.param(timedelta(minutes=10), id="10-min")],
)
def test_sessao_recente_aceita_ate_10_min(
    recent_app: FastAPI,
    recent_client: TestClient,
    factory: sessionmaker[Session],
    age: timedelta,
) -> None:
    user_id = new_user(factory)
    issued = open_session(recent_app, user_id, authenticated_at=NOW - age, now=NOW)
    _freeze(recent_app, NOW)

    response = recent_client.post(RECENT, headers=session_headers(recent_app, issued))

    assert response.status_code == 200, response.text
    assert response.json() == {"owner_id": str(user_id)}


@pytest.mark.integration
def test_login_de_10_min_e_1_s_401_reauth_required_auditado(
    recent_app: FastAPI, recent_client: TestClient, factory: sessionmaker[Session]
) -> None:
    user_id = new_user(factory)
    issued = open_session(
        recent_app, user_id, authenticated_at=NOW - timedelta(minutes=10, seconds=1), now=NOW
    )
    _freeze(recent_app, NOW)

    response = recent_client.post(RECENT, headers=session_headers(recent_app, issued))

    assert_problem(response, 401, "REAUTH_REQUIRED")
    assert _audit_events(factory) == [("REAUTH_REQUIRED", user_id, "failure")]


@pytest.mark.integration
def test_chave_em_rota_de_sessao_recente_403_auditado(
    recent_client: TestClient, factory: sessionmaker[Session]
) -> None:
    user_id = new_user(factory)
    token = new_key(factory, user_id).token

    response = recent_client.post(RECENT, headers={"Authorization": f"Bearer {token}"})

    assert_problem(response, 403, "FORBIDDEN")
    assert _audit_events(factory) == [("ACCESS_DENIED", user_id, "failure")]


# --- Logout -----------------------------------------------------------------------------------


def _assert_signed_out(response: httpx.Response) -> None:
    assert response.status_code == 204
    assert response.headers["clear-site-data"] == '"cookies", "storage"'
    assert response.headers.get_list("set-cookie") == [
        f"{SESSION_COOKIE}=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0",
        f"{CSRF_COOKIE}=; Path=/; Secure; SameSite=Strict; Max-Age=0",
    ]


@pytest.mark.integration
def test_logout_revoga_no_servidor_e_cookie_antigo_401(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    user_id = new_user(factory)
    issued = open_session(app, user_id)
    other = open_session(app, user_id)

    response = client.post(LOGOUT, headers=session_headers(app, issued))

    _assert_signed_out(response)
    assert response.content == b""
    assert_problem(client.get(ME, headers=_cookie_only(issued)), 401, "AUTHENTICATION_REQUIRED")
    assert client.get(ME, headers=_cookie_only(other)).status_code == 200
    assert _audit_events(factory) == [("LOGOUT", user_id, "success")]


def _anonymous_logout_headers(session_cookie: str | None) -> dict[str, str]:
    """Par CSRF da própria origem (cookie e header iguais), com ou sem cookie de sessão."""
    cookies = [f"{CSRF_COOKIE}={ANONYMOUS_CSRF}"]
    if session_cookie is not None:
        cookies.insert(0, f"{SESSION_COOKIE}={session_cookie}")
    return {"Cookie": "; ".join(cookies), CSRF_HEADER: ANONYMOUS_CSRF}


@pytest.mark.integration
@pytest.mark.parametrize(
    "session_cookie",
    [
        pytest.param(None, id="sem-cookie-de-sessao"),
        pytest.param("S" * 43, id="sessao-desconhecida"),
        pytest.param("lixo", id="cookie-malformado"),
    ],
)
def test_logout_sem_sessao_valida_com_o_par_csrf_responde_204(
    client: TestClient, factory: sessionmaker[Session], session_cookie: str | None
) -> None:
    response = client.post(LOGOUT, headers=_anonymous_logout_headers(session_cookie))

    _assert_signed_out(response)
    assert _audit_events(factory) == []


@pytest.mark.integration
@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="sem-cookie-nem-token"),
        pytest.param(
            {"Cookie": f"{SESSION_COOKIE}={'S' * 43}"}, id="sessao-desconhecida-sem-token"
        ),
        pytest.param({"Cookie": f"{CSRF_COOKIE}={ANONYMOUS_CSRF}"}, id="cookie-sem-header"),
        pytest.param({CSRF_HEADER: ANONYMOUS_CSRF}, id="header-sem-cookie"),
        pytest.param(
            {**_anonymous_logout_headers(None), "Sec-Fetch-Site": "cross-site"}, id="outro-site"
        ),
        pytest.param(
            {**_anonymous_logout_headers(None), "Origin": "https://evil.example"},
            id="outra-origem",
        ),
    ],
)
def test_logout_sem_sessao_valida_e_sem_o_par_csrf_403(
    client: TestClient, factory: sessionmaker[Session], headers: dict[str, str]
) -> None:
    response = client.post(LOGOUT, headers=headers)

    assert_problem(response, 403, "CSRF_FAILED")
    assert "set-cookie" not in response.headers
    assert "clear-site-data" not in response.headers
    assert _audit_events(factory) == []


@pytest.mark.integration
def test_logout_de_sessao_ja_revogada_responde_204(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    issued = open_session(app, new_user(factory))
    client.post(LOGOUT, headers=session_headers(app, issued))

    _assert_signed_out(client.post(LOGOUT, headers=session_headers(app, issued)))


@pytest.mark.integration
def test_logout_sem_csrf_403_e_a_sessao_continua_valida(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    issued = open_session(app, new_user(factory))

    response = client.post(LOGOUT, headers=_cookie_only(issued))

    assert_problem(response, 403, "CSRF_FAILED")
    assert "set-cookie" not in response.headers
    assert client.get(ME, headers=_cookie_only(issued)).status_code == 200


@pytest.mark.integration
def test_logout_all_revoga_todas_as_sessoes_e_chaves(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, other = new_user(factory), new_user(factory)
    current, second = open_session(app, owner), open_session(app, owner)
    owner_key = new_key(factory, owner).token
    other_session = open_session(app, other)
    other_key = new_key(factory, other).token

    response = client.post(LOGOUT_ALL, headers=session_headers(app, current))

    _assert_signed_out(response)
    for issued in (current, second):
        assert_problem(client.get(ME, headers=_cookie_only(issued)), 401, "AUTHENTICATION_REQUIRED")
    now = datetime.now(UTC)
    with unit_of_work(factory) as session:
        assert authenticate_api_key(session, owner_key, now) is None
        assert authenticate_api_key(session, other_key, now) is not None
    assert client.get(ME, headers=_cookie_only(other_session)).status_code == 200
    assert _audit_events(factory) == [("LOGOUT_ALL", owner, "success")]


@pytest.mark.integration
def test_logout_all_sem_csrf_403_e_nada_revogado(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    issued = open_session(app, new_user(factory))

    response = client.post(LOGOUT_ALL, headers=_cookie_only(issued))

    assert_problem(response, 403, "CSRF_FAILED")
    assert client.get(ME, headers=_cookie_only(issued)).status_code == 200
