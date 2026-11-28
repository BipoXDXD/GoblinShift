"""Callback do Google (OIDC): ID token, state ligado ao navegador e sessão nova (3.6; ADR 025).

STRIDE da fatia 6: CSRF de login, ID token forjado e fixação de sessão.
"""

import hmac
import io
import json
import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import RSAKey
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.api import auth_login
from goblinshift.entrypoints.api.dependencies import current_time
from goblinshift.entrypoints.api.problems import REQUEST_ID_HEADER
from goblinshift.identity import csrf_token_matches
from goblinshift.identity.providers import GOOGLE_ISSUER, GOOGLE_TOKEN_URL
from goblinshift.identity.tables import app_user, login_state, web_session
from goblinshift.platform.db import unit_of_work
from goblinshift.platform.settings import WebIdentityConfig
from tests.api.auth.support import (
    StoredSessions,
    accounts,
    alert_events,
    assert_login_failed,
    audit_by_ref,
    audit_events,
    browser,
    cookie_header,
    cookie_value,
    delete_me_headers,
    login,
    mask_ref,
    open_signup,
    start_login,
)
from tests.api.support import assert_problem
from tests.identity.fake_providers import (
    GOOGLE_CLIENT,
    GOOGLE_SUBJECT,
    KEY_ID,
    FakeProviders,
    b64url,
    unsigned_jwt,
)

pytestmark = pytest.mark.integration


def count_rows(factory: sessionmaker[Session], table: Any) -> int:
    with unit_of_work(factory) as session:
        return session.execute(select(func.count()).select_from(table)).scalar_one()


def assert_nothing_logged_in(
    factory: sessionmaker[Session], sessions: StoredSessions, response: httpx.Response
) -> None:
    assert_login_failed(response)
    assert sessions.started == []
    assert audit_events(factory) == [("LOGIN_FAILED", "failure", None)]
    assert count_rows(factory, app_user) == 0


# --- Sucesso ----------------------------------------------------------------------------------


def test_login_google_cria_a_conta_e_emite_a_sessao(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
) -> None:
    open_signup(login_app)

    response = login(client, fake, "google", return_to="/runs")

    assert response.status_code == 303
    assert response.headers["location"] == "/runs"
    [account] = accounts(factory)
    assert account["issuer"] == GOOGLE_ISSUER
    assert account["subject"] == GOOGLE_SUBJECT
    assert account["display_name"] == "Conta Google"
    assert account["status"] == "active"
    assert sessions.started_for == [account["id"]]
    assert audit_events(factory) == [("LOGIN_SUCCEEDED", "success", account["id"])]


def test_cookies_de_sessao_e_csrf_tem_os_atributos(
    client: TestClient, fake: FakeProviders, sessions: StoredSessions, login_app: FastAPI
) -> None:
    open_signup(login_app)

    response = login(client, fake, "google")

    [issued] = sessions.started
    session_cookie = cookie_header(response, "__Host-gs_session")
    assert issued.issued_for(cookie_value(session_cookie))
    lowered = session_cookie.lower()
    assert "httponly" in lowered
    assert "secure" in lowered
    assert "samesite=lax" in lowered
    assert "path=/;" in lowered + ";"
    assert "domain=" not in lowered
    assert "max-age" not in lowered
    csrf_cookie = cookie_header(response, "__Host-gs_csrf")
    csrf_token = cookie_value(csrf_cookie)
    web: WebIdentityConfig = login_app.state.web_identity
    assert csrf_token_matches(web.csrf_key, issued.session_id, csrf_token, csrf_token)
    lowered = csrf_cookie.lower()
    assert "httponly" not in lowered
    assert "secure" in lowered
    assert "samesite=strict" in lowered
    assert "path=/;" in lowered + ";"


def test_callback_apaga_o_estado_e_o_marcador(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
) -> None:
    open_signup(login_app)

    login(client, fake, "google")

    assert count_rows(factory, login_state) == 0
    assert "__Host-gs_login" not in client.cookies


def test_login_emite_sessao_nova_e_revoga_a_anterior(
    client: TestClient, fake: FakeProviders, sessions: StoredSessions, login_app: FastAPI
) -> None:
    open_signup(login_app)
    login(client, fake, "google")
    first_token = client.cookies["__Host-gs_session"]

    login(client, fake, "google")

    first, second = sessions.started
    second_token = client.cookies["__Host-gs_session"]
    assert second_token != first_token
    assert first.issued_for(first_token)
    assert second.issued_for(second_token)
    assert sessions.revoked == [first.session_id]


def test_cookie_de_sessao_desconhecido_nao_atrapalha_o_login(
    client: TestClient, fake: FakeProviders, sessions: StoredSessions, login_app: FastAPI
) -> None:
    open_signup(login_app)
    client.cookies.set(
        "__Host-gs_session", "fixado-pelo-atacante", domain="testserver.local", path="/"
    )

    response = login(client, fake, "google")

    assert response.status_code == 303
    [issued] = sessions.started
    assert issued.issued_for(client.cookies["__Host-gs_session"])
    assert sessions.revoked == []


# --- Reautenticação só vale para a conta da sessão --------------------------------------------


def test_reauth_com_outra_conta_e_recusada_e_mantem_a_sessao(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
) -> None:
    open_signup(login_app)
    fake.google_subject = "sub-a"
    login(client, fake, "google")
    [account_a] = [account["id"] for account in accounts(factory)]
    fake.google_subject = "sub-b"

    response = login(client, fake, "google", reauth="1")

    assert_login_failed(response)
    assert sessions.revoked == []
    assert len(sessions.started) == 1
    assert [account["subject"] for account in accounts(factory)] == ["sub-a"]
    assert audit_events(factory)[-1] == ("LOGIN_FAILED", "failure", account_a)


def test_reauth_com_a_mesma_conta_abre_sessao_recente_e_revoga_a_anterior(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
) -> None:
    open_signup(login_app)
    login(client, fake, "google")
    [account] = accounts(factory)

    response = login(client, fake, "google", reauth="1", return_to="/acesso")

    assert response.status_code == 303
    assert response.headers["location"] == "/acesso"
    first, second = sessions.started
    assert second.issued_for(client.cookies["__Host-gs_session"])
    assert sessions.revoked == [first.session_id]
    assert sessions.started_for == [account["id"], account["id"]]
    assert audit_events(factory)[-1] == ("LOGIN_SUCCEEDED", "success", account["id"])


def test_reauth_sem_sessao_valida_vira_login_comum(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
) -> None:
    open_signup(login_app)

    response = login(client, fake, "google", reauth="1")

    assert response.status_code == 303
    [account] = accounts(factory)
    assert sessions.started_for == [account["id"]]
    assert audit_events(factory) == [("LOGIN_SUCCEEDED", "success", account["id"])]


def test_reauth_com_sessao_revogada_vira_login_comum(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
) -> None:
    open_signup(login_app)
    fake.google_subject = "sub-a"
    login(client, fake, "google")
    with unit_of_work(factory) as session:
        session.execute(web_session.update().values(revoked_at=func.now()))
    fake.google_subject = "sub-b"

    response = login(client, fake, "google", reauth="1")

    assert response.status_code == 303
    assert [account["subject"] for account in accounts(factory)] == ["sub-a", "sub-b"]
    assert sessions.started[-1].issued_for(client.cookies["__Host-gs_session"])


# --- Recência pelo `auth_time` (D1, D3, D4) --------------------------------------------------


def freeze_api_clock(app: FastAPI) -> int:
    """Fixa o relógio da API no segundo atual e o devolve; o `auth_time` do teste parte dele."""
    frozen = int(time.time())
    app.dependency_overrides[current_time] = lambda: datetime.fromtimestamp(frozen, UTC)
    return frozen


def session_authenticated_at(factory: sessionmaker[Session]) -> list[datetime]:
    with unit_of_work(factory) as session:
        return list(
            session.execute(
                select(web_session.c.authenticated_at).order_by(
                    web_session.c.created_at, web_session.c.id
                )
            ).scalars()
        )


def test_sessao_do_google_conta_recencia_pelo_auth_time(
    client: TestClient, fake: FakeProviders, login_app: FastAPI
) -> None:
    # Navegador logado no Google há 3 dias: o login entra, mas não vale como recente.
    open_signup(login_app)
    fake.claims_patch = {"auth_time": _now() - 3 * 24 * 3600}

    assert login(client, fake, "google").status_code == 303
    response = client.delete("/api/v1/me", headers=delete_me_headers(client))

    assert_problem(response, 401, "REAUTH_REQUIRED")


def test_auth_time_recente_vale_como_login_recente(
    client: TestClient, fake: FakeProviders, factory: sessionmaker[Session], login_app: FastAPI
) -> None:
    open_signup(login_app)
    frozen = freeze_api_clock(login_app)
    fake.claims_patch = {"auth_time": frozen - 60}

    login(client, fake, "google")

    assert session_authenticated_at(factory) == [datetime.fromtimestamp(frozen - 60, UTC)]
    assert client.delete("/api/v1/me", headers=delete_me_headers(client)).status_code == 202


def test_sem_auth_time_vale_agora_e_alerta(
    client: TestClient,
    fake: FakeProviders,
    factory: sessionmaker[Session],
    login_app: FastAPI,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # "Session age claims" desligado ou app não verificado: falha aberta com alerta (D3).
    open_signup(login_app)
    frozen = freeze_api_clock(login_app)
    fake.omit_auth_time = True
    caplog.set_level(logging.WARNING, logger=auth_login.__name__)

    assert login(client, fake, "google").headers["location"] == "/"

    assert session_authenticated_at(factory) == [datetime.fromtimestamp(frozen, UTC)]
    assert alert_events(caplog.records) == ["google_auth_time_missing"]


def test_com_auth_time_nao_ha_alerta(
    client: TestClient, fake: FakeProviders, login_app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    open_signup(login_app)
    caplog.set_level(logging.WARNING, logger=auth_login.__name__)

    login(client, fake, "google")

    assert alert_events(caplog.records) == []


def test_auth_time_no_futuro_vira_agora(
    client: TestClient, fake: FakeProviders, factory: sessionmaker[Session], login_app: FastAPI
) -> None:
    # Relógio do Google adiantado: nem recusa nem `ValueError` de `start_session`.
    open_signup(login_app)
    frozen = freeze_api_clock(login_app)
    fake.claims_patch = {"auth_time": frozen + 120}

    assert login(client, fake, "google").headers["location"] == "/"

    assert session_authenticated_at(factory) == [datetime.fromtimestamp(frozen, UTC)]


@pytest.mark.parametrize("auth_time", ["123", True, -5], ids=repr)
def test_auth_time_com_tipo_errado_e_recusado(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
    auth_time: object,
) -> None:
    open_signup(login_app)
    fake.claims_patch = {"auth_time": auth_time}

    assert_nothing_logged_in(factory, sessions, login(client, fake, "google"))


# --- Reautenticação com `auth_time` velho (D2) -----------------------------------------------

REAUTH_STALE_WITH_REF = re.compile(
    r"/\?login_error=reauth_stale#ref=([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)


def test_reauth_com_auth_time_velho_mantem_a_sessao_e_avisa(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
) -> None:
    # "Entre de novo", e o Google volta sem pedir a senha: a confirmação não vale.
    open_signup(login_app)
    login(client, fake, "google")
    [account] = accounts(factory)
    fake.claims_patch = {"auth_time": _now() - 2 * 3600}

    response = login(client, fake, "google", reauth="1")

    assert response.status_code == 303
    matched = REAUTH_STALE_WITH_REF.fullmatch(response.headers["location"])
    assert matched is not None, response.headers["location"]
    assert matched.group(1) == response.headers[REQUEST_ID_HEADER]
    assert "__Host-gs_session" not in response.headers.get("set-cookie", "")
    assert sessions.revoked == []
    assert len(sessions.started) == 1
    assert sessions.started[0].issued_for(client.cookies["__Host-gs_session"])
    assert audit_events(factory)[-1] == ("REAUTH_REQUIRED", "failure", account["id"])
    assert audit_by_ref(factory, UUID(matched.group(1))) == [("REAUTH_REQUIRED", account["id"])]


@pytest.mark.parametrize(("age", "stale"), [(600, False), (601, True)], ids=["600s", "601s"])
def test_reauth_na_fronteira_de_10_min(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    login_app: FastAPI,
    age: int,
    stale: bool,
) -> None:
    # Como `require_recent_session`, só passar de 10 min recusa.
    open_signup(login_app)
    frozen = freeze_api_clock(login_app)
    login(client, fake, "google")
    fake.claims_patch = {"auth_time": frozen - age}

    response = login(client, fake, "google", reauth="1")

    assert response.headers["location"].startswith("/?login_error=reauth_stale") is stale
    assert len(sessions.started) == (1 if stale else 2)


def test_reauth_com_auth_time_recente_abre_sessao_recente(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    login_app: FastAPI,
) -> None:
    open_signup(login_app)
    login(client, fake, "google")
    fake.claims_patch = {"auth_time": _now() - 60}

    response = login(client, fake, "google", reauth="1")

    assert response.headers["location"] == "/"
    first, second = sessions.started
    assert sessions.revoked == [first.session_id]
    assert second.issued_for(client.cookies["__Host-gs_session"])
    assert client.delete("/api/v1/me", headers=delete_me_headers(client)).status_code == 202


# --- Cadastro fechado e contas recusadas -----------------------------------------------------


def test_cadastro_fechado_identidade_nova_e_recusada_e_fica_pendente(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    factory: sessionmaker[Session],
) -> None:
    response = login(client, fake, "google")

    assert_login_failed(response)
    assert sessions.started == []
    [account] = accounts(factory)
    assert account["status"] == "blocked"
    assert audit_events(factory) == [("LOGIN_FAILED", "failure", account["id"])]


def test_conta_bloqueada_pendente_e_erro_do_provedor_respondem_igual(
    db_settings: Any,
    tmp_path: Any,
    fake: FakeProviders,
    sessions: StoredSessions,
    login_app: FastAPI,
    factory: sessionmaker[Session],
) -> None:
    def attempt(prepare: Callable[[], None]) -> tuple[int, str, bytes, list[str]]:
        prepare()
        with browser(login_app) as client:
            response = login(client, fake, "google")
        names = sorted(cookie.split("=")[0] for cookie in response.headers.get_list("set-cookie"))
        return response.status_code, mask_ref(response.headers["location"]), response.content, names

    def block() -> None:
        with unit_of_work(factory) as session:
            session.execute(app_user.update().values(status="blocked"))

    pending = attempt(lambda: None)
    blocked = attempt(block)
    fake.claims_patch = {"aud": "outro-cliente"}
    provider_error = attempt(lambda: None)

    assert pending == blocked == provider_error
    assert sessions.started == []


# --- State ligado ao navegador (CSRF de login, CVE-2026-41425) --------------------------------


def test_callback_sem_state_e_recusado(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    factory: sessionmaker[Session],
) -> None:
    started = start_login(client, "google")
    callback = urlsplit(fake.consent(started.headers["location"]))
    code = parse_qs(callback.query)["code"][0]

    response = client.get(f"{callback.path}?{urlencode({'code': code})}")

    assert_nothing_logged_in(factory, sessions, response)
    assert fake.requests_to(GOOGLE_TOKEN_URL) == []


def test_callback_com_state_valido_sem_cookie_de_login_e_recusado(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
) -> None:
    # O atacante inicia o login no navegador dele e entrega o callback (com o code dele) à vítima.
    attacker_callback = fake.consent(start_login(client, "google").headers["location"])

    with browser(login_app) as victim:
        response = victim.get(attacker_callback)

    assert_nothing_logged_in(factory, sessions, response)
    assert fake.requests_to(GOOGLE_TOKEN_URL) == []


def test_callback_com_state_de_outro_navegador_e_recusado(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
) -> None:
    attacker_callback = fake.consent(start_login(client, "google").headers["location"])

    with browser(login_app) as victim:
        start_login(victim, "google")
        response = victim.get(attacker_callback)

    assert_nothing_logged_in(factory, sessions, response)


def test_callback_com_state_reusado_e_recusado(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
) -> None:
    open_signup(login_app)
    started = start_login(client, "google")
    marker = client.cookies["__Host-gs_login"]
    callback = fake.consent(started.headers["location"])
    assert client.get(callback).status_code == 303

    # Mesmo com o cookie de login antigo de volta, o estado já foi consumido.
    client.cookies.set("__Host-gs_login", marker, path="/")
    response = client.get(callback)

    assert_login_failed(response)
    assert len(sessions.started) == 1


def test_state_do_google_nao_serve_no_callback_do_github(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    factory: sessionmaker[Session],
) -> None:
    callback = fake.consent(start_login(client, "google").headers["location"])

    response = client.get(callback.replace("/auth/callback/google", "/auth/callback/github"))

    assert_nothing_logged_in(factory, sessions, response)


def test_code_adulterado_e_recusado_pelo_provedor(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    factory: sessionmaker[Session],
) -> None:
    callback = urlsplit(fake.consent(start_login(client, "google").headers["location"]))
    state = parse_qs(callback.query)["state"][0]

    response = client.get(f"{callback.path}?{urlencode({'code': 'inventado', 'state': state})}")

    assert_nothing_logged_in(factory, sessions, response)


def test_usuario_que_nega_no_provedor_recebe_o_erro_generico(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    factory: sessionmaker[Session],
) -> None:
    state = parse_qs(urlsplit(start_login(client, "google").headers["location"]).query)["state"][0]

    response = client.get(
        "/auth/callback/google", params={"error": "access_denied", "state": state}
    )

    assert_nothing_logged_in(factory, sessions, response)
    assert fake.requests_to(GOOGLE_TOKEN_URL) == []


# --- ID token forjado -------------------------------------------------------------------------


def _alg_none(claims: dict[str, Any], key: RSAKey) -> str:
    return unsigned_jwt({"alg": "none", "kid": KEY_ID}, claims)


def _alg_none_case(claims: dict[str, Any], key: RSAKey) -> str:
    return unsigned_jwt({"alg": "nonE", "kid": KEY_ID}, claims)


def _hs256_with_public_key(claims: dict[str, Any], key: RSAKey) -> str:
    public_pem = key.as_pem(private=False)
    unsigned = unsigned_jwt({"alg": "HS256", "kid": KEY_ID, "typ": "JWT"}, claims)
    signing_input = unsigned.rsplit(".", 1)[0]
    signature = hmac.new(public_pem, signing_input.encode(), sha256).digest()
    return f"{signing_input}.{b64url(signature)}"


def _tampered_signature(claims: dict[str, Any], key: RSAKey) -> str:
    token = jwt.encode({"alg": "RS256", "kid": KEY_ID}, claims, key)
    head, body, signature = token.split(".")
    flipped = ("A" if signature[10] != "A" else "B").join([signature[:10], signature[11:]])
    return f"{head}.{body}.{flipped}"


def _tampered_claims(claims: dict[str, Any], key: RSAKey) -> str:
    token = jwt.encode({"alg": "RS256", "kid": KEY_ID}, claims, key)
    head, _, signature = token.split(".")
    forged = unsigned_jwt({}, claims | {"sub": "outra-pessoa"}).split(".")[1]
    return f"{head}.{forged}.{signature}"


def _other_key_same_kid(claims: dict[str, Any], key: RSAKey) -> str:
    other = RSAKey.generate_key(2048, parameters={"kid": KEY_ID})
    return jwt.encode({"alg": "RS256", "kid": KEY_ID}, claims, other)


def _not_a_jwt(claims: dict[str, Any], key: RSAKey) -> str:
    return "isto-nao-e-um-jwt"


@pytest.mark.parametrize(
    "factory_fn",
    [
        _alg_none,
        _alg_none_case,
        _hs256_with_public_key,
        _tampered_signature,
        _tampered_claims,
        _other_key_same_kid,
        _not_a_jwt,
    ],
    ids=[
        "alg-none",
        "alg-nonE",
        "hs256-com-chave-publica",
        "assinatura-adulterada",
        "claims-adulterados",
        "outra-chave-mesmo-kid",
        "lixo",
    ],
)
def test_id_token_forjado_e_recusado(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
    factory_fn: Callable[[dict[str, Any], RSAKey], str],
) -> None:
    open_signup(login_app)
    fake.id_token_factory = factory_fn

    assert_nothing_logged_in(factory, sessions, login(client, fake, "google"))


def _now() -> int:
    return int(time.time())


@pytest.mark.parametrize(
    "patch",
    [
        {"exp": _now() - 3600},
        {"aud": "outro-cliente"},
        {"aud": ["outro-cliente"]},
        {"aud": [GOOGLE_CLIENT.client_id, "outro-cliente"]},
        {"azp": "outro-cliente"},
        {"aud": "outro-cliente", "azp": GOOGLE_CLIENT.client_id},
        {"iss": "https://evil.example"},
        {"iss": "https://accounts.google.com.evil.example"},
        {"nonce": "nonce-de-outro-login"},
        {"nonce": None},
        {"exp": None},
        {"sub": None},
        {"sub": "conta com espaço"},
        {"iat": None},
    ],
    ids=[
        "expirado",
        "aud-errada",
        "aud-lista-errada",
        "aud-com-outro-sem-azp",
        "azp-errado",
        "aud-errada-com-nosso-azp",
        "iss-errado",
        "iss-parecido",
        "nonce-diferente",
        "sem-nonce",
        "sem-exp",
        "sem-sub",
        "sub-invalido",
        "sem-iat",
    ],
)
def test_id_token_com_claims_errados_e_recusado(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
    patch: dict[str, Any],
) -> None:
    open_signup(login_app)
    fake.claims_patch = patch
    fake.id_token_factory = _drop_none_claims(fake)

    assert_nothing_logged_in(factory, sessions, login(client, fake, "google"))


def test_id_token_dentro_da_folga_de_relogio_e_aceito(
    client: TestClient, fake: FakeProviders, sessions: StoredSessions, login_app: FastAPI
) -> None:
    open_signup(login_app)
    fake.claims_patch = {"exp": _now() - 30}

    assert login(client, fake, "google").headers["location"] == "/"


# Instante fixo do relógio da validação (2027-01-15T08:00:00Z) e a folga padrão do Authlib, a
# que `parse_id_token` usa: o token vale enquanto `exp > agora - folga`.
CLOCK_NOW = 1_800_000_000
ID_TOKEN_LEEWAY_SECONDS = 120


@pytest.fixture
def fixed_validation_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("joserfc._rfc7519.claims._generate_now", lambda: CLOCK_NOW)


@pytest.mark.usefixtures("fixed_validation_clock")
@pytest.mark.parametrize(
    "exp",
    [CLOCK_NOW, CLOCK_NOW - ID_TOKEN_LEEWAY_SECONDS + 1],
    ids=["exp-igual-a-agora", "ultimo-segundo-da-folga"],
)
def test_id_token_ate_o_ultimo_segundo_da_folga_e_aceito(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    login_app: FastAPI,
    exp: int,
) -> None:
    open_signup(login_app)
    fake.claims_patch = {"iat": CLOCK_NOW - 3600, "exp": exp}

    assert login(client, fake, "google").headers["location"] == "/"
    assert len(sessions.started) == 1


@pytest.mark.usefixtures("fixed_validation_clock")
@pytest.mark.parametrize(
    "exp",
    [CLOCK_NOW - ID_TOKEN_LEEWAY_SECONDS, CLOCK_NOW - ID_TOKEN_LEEWAY_SECONDS - 1],
    ids=["exp-igual-ao-limite-da-folga", "um-segundo-alem-da-folga"],
)
def test_id_token_vencido_no_limite_da_folga_e_recusado(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
    exp: int,
) -> None:
    open_signup(login_app)
    fake.claims_patch = {"iat": CLOCK_NOW - 3600, "exp": exp}

    assert_nothing_logged_in(factory, sessions, login(client, fake, "google"))


def test_emissor_sem_https_e_aceito_e_normalizado(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
) -> None:
    open_signup(login_app)
    fake.claims_patch = {"iss": "accounts.google.com"}

    assert login(client, fake, "google").headers["location"] == "/"
    assert [account["issuer"] for account in accounts(factory)] == [GOOGLE_ISSUER]


def test_resposta_sem_id_token_e_recusada(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
) -> None:
    open_signup(login_app)
    fake.omit_id_token = True

    assert_nothing_logged_in(factory, sessions, login(client, fake, "google"))


# --- Falhas do provedor -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(500, text="<html><body>Traceback: Internal Server Error</body></html>"),
        httpx.Response(200, text="<html>não é JSON</html>"),
        httpx.Response(400, json={"error": "invalid_grant"}),
        httpx.Response(429, text="slow down"),
    ],
    ids=["500-html", "200-html", "invalid-grant", "429"],
)
def test_falha_no_provedor_nao_vaza_excecao(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    factory: sessionmaker[Session],
    failure: httpx.Response,
) -> None:
    fake.token_failure = failure

    response = login(client, fake, "google")

    assert_nothing_logged_in(factory, sessions, response)
    for leak in ("Exception", "Traceback", "httpx", "authlib", "invalid_grant"):
        assert leak not in response.text
        assert leak not in str(response.headers)


@pytest.mark.parametrize(
    "error",
    [httpx.ReadTimeout("lento"), httpx.ConnectError("recusada")],
    ids=["timeout", "conexao-recusada"],
)
def test_provedor_fora_do_ar_responde_o_erro_generico(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    factory: sessionmaker[Session],
    error: Exception,
) -> None:
    callback = fake.consent(start_login(client, "google").headers["location"])
    fake.network_failure = error

    assert_nothing_logged_in(factory, sessions, client.get(callback))


def test_canario_em_code_e_state_nunca_aparece_no_log_nem_na_resposta(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    caplog: pytest.LogCaptureFixture,
) -> None:
    canary = "CANARY-6f0c9a1e-2b7d-4c55-9a43-1d2e3f4a5b6c"
    start_login(client, "google")

    with caplog.at_level("DEBUG"):
        response = client.get("/auth/callback/google", params={"code": canary, "state": canary})

    assert_login_failed(response)
    assert canary not in server_log(caplog)
    assert canary not in str(response.headers)


def test_login_recusado_registra_o_mesmo_request_id_do_ref(
    client: TestClient,
    fake: FakeProviders,
    listen: Callable[[logging.Logger], io.StringIO],
) -> None:
    log = listen(logging.getLogger(auth_login.__name__))
    fake.token_failure = httpx.Response(400, json={"error": "invalid_grant"})

    response = login(client, fake, "google")

    ref = str(assert_login_failed(response))
    lines = [json.loads(line) for line in log.getvalue().splitlines()]
    [refused] = [line for line in lines if line["message"].startswith("login google recusado")]
    assert refused["request_id"] == ref == response.headers[REQUEST_ID_HEADER]


def test_tokens_do_provedor_nunca_aparecem_no_log(
    client: TestClient,
    fake: FakeProviders,
    sessions: StoredSessions,
    login_app: FastAPI,
    caplog: pytest.LogCaptureFixture,
) -> None:
    open_signup(login_app)

    with caplog.at_level("DEBUG"):
        login(client, fake, "google")

    [access_token] = fake.issued_access_tokens
    assert access_token not in caplog.text
    assert client.cookies["__Host-gs_session"] not in server_log(caplog)


# --- Apoio ------------------------------------------------------------------------------------


def server_log(caplog: pytest.LogCaptureFixture) -> str:
    """Log do servidor, sem o do `httpx2`: o TestClient registra a URL que o navegador pediu."""
    return "\n".join(
        record.getMessage() for record in caplog.records if not record.name.startswith("httpx")
    )


def _drop_none_claims(fake: FakeProviders) -> Callable[[dict[str, Any], RSAKey], str]:
    """`None` no patch remove o claim, para testar a ausência."""

    def sign(claims: dict[str, Any], key: RSAKey) -> str:
        return fake.sign({name: value for name, value in claims.items() if value is not None})

    return sign
