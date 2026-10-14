"""Callback do GitHub (OAuth 2.0): identidade pelo `id`, token descartado (ADR 008; 3.6)."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.identity.providers import GITHUB_ISSUER, GITHUB_USER_URL, MAX_GITHUB_USER_BYTES
from goblinshift.identity.tables import app_user
from goblinshift.platform.db import metadata, unit_of_work
from tests.api.auth.support import (
    FakeSessions,
    accounts,
    assert_login_failed,
    audit_events,
    login,
    open_signup,
)
from tests.identity.fake_providers import GITHUB_USER, FakeProviders

pytestmark = pytest.mark.integration


def test_login_github_cria_a_conta_pelo_id(
    client: TestClient,
    fake: FakeProviders,
    sessions: FakeSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
) -> None:
    open_signup(login_app)

    response = login(client, fake, "github", return_to="/acesso")

    assert response.headers["location"] == "/acesso"
    [account] = accounts(factory)
    assert (account["issuer"], account["subject"]) == (GITHUB_ISSUER, "583231")
    assert account["display_name"] == "octocat"
    assert sessions.started_for == [account["id"]]
    assert audit_events(factory) == [("LOGIN_SUCCEEDED", "success", account["id"])]


def test_github_pede_o_usuario_com_o_token_e_o_accept_da_api(
    client: TestClient, fake: FakeProviders, sessions: FakeSessions, login_app: FastAPI
) -> None:
    open_signup(login_app)

    login(client, fake, "github")

    [request] = fake.requests_to(GITHUB_USER_URL)
    assert request.method == "GET"
    assert request.headers["accept"] == "application/vnd.github+json"
    assert request.headers["authorization"] == f"Bearer {fake.issued_access_tokens[0]}"


def test_github_identidade_pelo_id_e_nao_pelo_login(
    client: TestClient,
    fake: FakeProviders,
    sessions: FakeSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
) -> None:
    open_signup(login_app)
    login(client, fake, "github")
    fake.github_user_body = json.dumps(GITHUB_USER | {"login": "octocat-renomeado"}).encode()

    login(client, fake, "github")

    [account] = accounts(factory)
    assert account["display_name"] == "octocat-renomeado"
    assert sessions.started_for == [account["id"], account["id"]]


def test_mesmo_login_com_outro_id_e_outra_conta(
    client: TestClient,
    fake: FakeProviders,
    sessions: FakeSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
) -> None:
    open_signup(login_app)
    login(client, fake, "github")
    fake.github_user_body = json.dumps(GITHUB_USER | {"id": 999}).encode()

    login(client, fake, "github")

    assert [account["subject"] for account in accounts(factory)] == ["583231", "999"]


def test_google_e_github_com_mesmo_email_criam_duas_contas(
    client: TestClient,
    fake: FakeProviders,
    sessions: FakeSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
) -> None:
    open_signup(login_app)

    login(client, fake, "google")
    login(client, fake, "github")

    created = accounts(factory)
    assert {account["issuer"] for account in created} == {
        GITHUB_ISSUER,
        "https://accounts.google.com",
    }
    assert len({account["id"] for account in created}) == 2
    assert len(set(sessions.started_for)) == 2


def test_access_token_do_github_nao_e_persistido(
    client: TestClient,
    fake: FakeProviders,
    sessions: FakeSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
) -> None:
    open_signup(login_app)

    login(client, fake, "github")

    [access_token] = fake.issued_access_tokens
    with unit_of_work(factory) as session:
        for table in metadata.sorted_tables:
            rows = session.execute(select(table)).all()
            dump = repr(rows)
            assert access_token not in dump, table.name
            assert access_token.encode().hex() not in dump, table.name


@pytest.mark.parametrize(
    "body",
    [
        json.dumps(GITHUB_USER | {"id": "583231"}).encode(),
        json.dumps(GITHUB_USER | {"id": -583231}).encode(),
        json.dumps(GITHUB_USER | {"login": "octo\u0000cat"}).encode(),
        json.dumps(GITHUB_USER | {"bio": "x" * (10 * 1024 * 1024)}).encode(),
        b"<html>Bad gateway</html>",
    ],
    ids=["id-texto", "id-negativo", "login-nul", "10-mib", "html"],
)
def test_github_user_malicioso_e_recusado(
    client: TestClient,
    fake: FakeProviders,
    sessions: FakeSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
    body: bytes,
) -> None:
    open_signup(login_app)
    fake.github_user_body = body

    response = login(client, fake, "github")

    assert_login_failed(response)
    assert sessions.started == []
    assert accounts(factory) == []
    assert audit_events(factory) == [("LOGIN_FAILED", "failure", None)]


def test_teto_do_corpo_do_github_cabe_um_usuario_real() -> None:
    assert len(json.dumps(GITHUB_USER | {"bio": "x" * 160})) < MAX_GITHUB_USER_BYTES


@pytest.mark.parametrize("status", [401, 403, 500, 502])
def test_github_user_com_erro_e_recusado(
    client: TestClient,
    fake: FakeProviders,
    sessions: FakeSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
    status: int,
) -> None:
    open_signup(login_app)
    fake.github_user_status = status

    assert_login_failed(login(client, fake, "github"))
    assert sessions.started == []
    with unit_of_work(factory) as session:
        assert session.execute(select(app_user.c.id)).all() == []


def test_conta_github_bloqueada_nao_completa_login(
    client: TestClient,
    fake: FakeProviders,
    sessions: FakeSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
) -> None:
    open_signup(login_app)
    login(client, fake, "github")
    with unit_of_work(factory) as session:
        session.execute(app_user.update().values(status="blocked"))

    assert_login_failed(login(client, fake, "github"))
    assert len(sessions.started) == 1
    assert [event for event, _, _ in audit_events(factory)] == ["LOGIN_SUCCEEDED", "LOGIN_FAILED"]


def test_conta_com_exclusao_pedida_nao_completa_login(
    client: TestClient,
    fake: FakeProviders,
    sessions: FakeSessions,
    factory: sessionmaker[Session],
    login_app: FastAPI,
) -> None:
    open_signup(login_app)
    login(client, fake, "github")
    with unit_of_work(factory) as session:
        session.execute(app_user.update().values(deletion_requested_at=app_user.c.created_at))

    assert_login_failed(login(client, fake, "github"))
    assert len(sessions.started) == 1
