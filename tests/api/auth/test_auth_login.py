"""`GET /auth/login/{provider}`: pedido de autorização e cookie de login (3.6; ADRs 008, 025)."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.identity.providers import GITHUB_AUTHORIZE_URL, GOOGLE_AUTHORIZE_URL
from goblinshift.identity.tables import login_state
from goblinshift.platform.db import unit_of_work
from goblinshift.platform.settings import Settings
from tests.api.auth.support import (
    LOGIN_FAILED_LOCATION,
    PUBLIC_ORIGIN,
    StoredSessions,
    assert_login_failed,
    authorize_query,
    browser,
    build_login_app,
    cookie_header,
    login,
    login_cookie_payload,
    login_settings,
    open_signup,
    start_login,
)
from tests.api.support import assert_problem
from tests.identity.fake_providers import GITHUB_CLIENT, GOOGLE_CLIENT, FakeProviders

pytestmark = pytest.mark.integration

PROVIDERS = ["google", "github"]


def test_pedido_ao_google_tem_so_openid(client: TestClient) -> None:
    response = start_login(client, "google")

    assert response.headers["location"].startswith(f"{GOOGLE_AUTHORIZE_URL}?")
    query = authorize_query(response)
    assert query["scope"] == "openid"
    assert query["client_id"] == GOOGLE_CLIENT.client_id
    assert query["response_type"] == "code"
    assert len(query["nonce"]) >= 16


def test_pedido_ao_github_nao_tem_scope(client: TestClient) -> None:
    response = start_login(client, "github")

    assert response.headers["location"].startswith(f"{GITHUB_AUTHORIZE_URL}?")
    query = authorize_query(response)
    assert "scope" not in query
    assert query["client_id"] == GITHUB_CLIENT.client_id
    assert query["response_type"] == "code"


@pytest.mark.parametrize("provider", PROVIDERS)
def test_pedido_tem_pkce_s256_e_state(client: TestClient, provider: str) -> None:
    query = authorize_query(start_login(client, provider))

    assert query["code_challenge_method"] == "S256"
    assert len(query["code_challenge"]) == 43
    assert len(query["state"]) >= 16
    assert "code_verifier" not in query


@pytest.mark.parametrize("provider", PROVIDERS)
def test_redirect_uri_vem_da_configuracao_e_nao_do_host(client: TestClient, provider: str) -> None:
    response = client.get(f"/auth/login/{provider}", headers={"Host": "evil.example"})

    assert response.status_code == 302
    query = authorize_query(response)
    assert query["redirect_uri"] == f"{PUBLIC_ORIGIN}/auth/callback/{provider}"


@pytest.mark.parametrize("provider", PROVIDERS)
def test_states_de_dois_inicios_sao_diferentes(client: TestClient, provider: str) -> None:
    first = authorize_query(start_login(client, provider))
    second = authorize_query(start_login(client, provider))

    assert first["state"] != second["state"]
    assert first["code_challenge"] != second["code_challenge"]


@pytest.mark.parametrize("provider", PROVIDERS)
def test_reauth_pede_escolha_de_conta(client: TestClient, provider: str) -> None:
    assert authorize_query(start_login(client, provider, reauth="1"))["prompt"] == "select_account"


@pytest.mark.parametrize("params", [{}, {"reauth": "0"}, {"reauth": "true"}])
def test_sem_reauth_nao_ha_prompt(client: TestClient, params: dict[str, str]) -> None:
    assert "prompt" not in authorize_query(start_login(client, "google", **params))


def test_parametro_desconhecido_nao_chega_ao_provedor(client: TestClient) -> None:
    query = authorize_query(start_login(client, "google", scope="openid email", prompt="none"))

    assert query["scope"] == "openid"
    assert "prompt" not in query


@pytest.mark.parametrize("provider", ["facebook", "Google", "google ", "%00"])
def test_provedor_desconhecido_404(client: TestClient, provider: str) -> None:
    response = client.get(f"/auth/login/{provider}")

    assert_problem(response, 404, "NOT_FOUND")


def test_callback_de_provedor_desconhecido_404(client: TestClient) -> None:
    assert_problem(client.get("/auth/callback/facebook?code=x&state=y"), 404, "NOT_FOUND")


def test_provedor_desligado_responde_o_erro_generico(
    db_settings: Settings, tmp_path: Path, fake: FakeProviders
) -> None:
    app = build_login_app(login_settings(db_settings, tmp_path, github=False), fake)

    with browser(app) as client:
        assert_login_failed(client.get("/auth/login/github"))
        assert_login_failed(client.get("/auth/callback/github?code=x&state=y"))


def test_cookie_de_login_tem_os_atributos_do_adr_024(client: TestClient) -> None:
    header = cookie_header(start_login(client, "google"), "__Secure-gs_login").lower()

    assert "path=/auth" in header
    assert "secure" in header
    assert "httponly" in header
    assert "samesite=lax" in header
    assert "max-age=600" in header
    assert "domain=" not in header


def test_estado_fica_no_servidor_e_o_cookie_leva_so_o_marcador(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    response = start_login(client, "google")
    query = authorize_query(response)

    value = cookie_header(response, "__Secure-gs_login").split(";")[0].split("=", 1)[1]
    payload = login_cookie_payload(value)
    assert query["nonce"] not in str(payload)
    assert "code_verifier" not in str(payload)
    with unit_of_work(factory) as session:
        stored = session.execute(select(login_state.c.key, login_state.c.value)).all()
    assert [row.key for row in stored] == [f"_state_google_{query['state']}"]
    assert query["nonce"] in stored[0].value


@pytest.mark.parametrize(
    ("return_to", "expected"),
    [
        ("/runs?status=queued&page=2", "/runs?status=queued&page=2"),
        ("/acesso/chaves", "/acesso/chaves"),
        ("/" + "a" * 511, "/" + "a" * 511),
        ("/" + "a" * 512, "/"),
        ("//evil.example", "/"),
        ("https://evil.example", "/"),
        ("/\\evil.example", "/"),
        ("/runs\x00", "/"),
        ("/runs\r\nSet-Cookie: x=1", "/"),
        ("runs", "/"),
        ("", "/"),
        ("javascript:alert(1)", "/"),
    ],
    ids=[
        "caminho-com-query",
        "caminho",
        "512-chars",
        "513-chars",
        "protocol-relative",
        "absoluto",
        "barra-invertida",
        "nul",
        "crlf",
        "relativo",
        "vazio",
        "javascript",
    ],
)
def test_return_to_so_aceita_caminho_local(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    login_app: FastAPI,
    return_to: str,
    expected: str,
) -> None:
    open_signup(login_app)
    response = login(client, fake, "github", return_to=return_to)

    assert response.status_code == 303
    assert response.headers["location"] == expected


def test_sem_return_to_volta_para_a_raiz(
    client: TestClient, fake: FakeProviders, sessions: StoredSessions, login_app: FastAPI
) -> None:
    open_signup(login_app)
    assert login(client, fake, "github").headers["location"] == "/"


def test_return_to_e_de_cada_provedor(
    client: TestClient, fake: FakeProviders, sessions: StoredSessions, login_app: FastAPI
) -> None:
    open_signup(login_app)
    google = start_login(client, "google", return_to="/do-google")
    github = start_login(client, "github", return_to="/do-github")

    assert client.get(fake.consent(github.headers["location"])).headers["location"] == "/do-github"
    assert client.get(fake.consent(google.headers["location"])).headers["location"] == "/do-google"


def test_erro_generico_e_um_caminho_local(client: TestClient) -> None:
    assert LOGIN_FAILED_LOCATION.startswith("/?")
    assert_login_failed(client.get("/auth/callback/google"))
