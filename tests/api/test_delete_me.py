"""`DELETE /api/v1/me`: pedido de exclusão da conta (docs/GoblinShiftPlan.md, 1.6 e 1.8)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.identity import authenticate_api_key
from goblinshift.identity.tables import app_user, audit_event
from goblinshift.platform.db import unit_of_work
from goblinshift.runs.lifecycle import Status
from goblinshift.runs.tables import run
from tests.api.session_support import FakeSessions
from tests.api.support import assert_problem
from tests.identity.pending_fronts import install_domain_stand_ins
from tests.identity.support import new_key, new_user
from tests.runs.worker_support import queue_run

pytestmark = pytest.mark.integration

ME = "/api/v1/me"
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


@pytest.fixture
def sessions(
    app: FastAPI, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> FakeSessions:
    install_domain_stand_ins(monkeypatch, factory)
    return FakeSessions(app, NOW)


def deletion_requested_at(factory: sessionmaker[Session], owner_id: UUID) -> datetime | None:
    with factory() as session:
        value: datetime | None = session.execute(
            select(app_user.c.deletion_requested_at).where(app_user.c.id == owner_id)
        ).scalar_one()
    return value


def events_of(factory: sessionmaker[Session], owner_id: UUID) -> list[str]:
    with factory() as session:
        return list(
            session.execute(
                select(audit_event.c.event_type).where(audit_event.c.user_id == owner_id)
            ).scalars()
        )


def test_delete_me_marca_a_exclusao_revoga_tudo_e_limpa_o_navegador(
    client: TestClient, factory: sessionmaker[Session], sessions: FakeSessions
) -> None:
    owner = new_user(factory)
    issued = new_key(factory, owner, now=NOW)
    sessions.sign_in(owner)

    response = client.delete(ME)

    assert response.status_code == 202, response.text
    assert response.content == b""
    assert response.headers["clear-site-data"] == '"cookies", "storage"'
    assert deletion_requested_at(factory, owner) == NOW
    with unit_of_work(factory) as session:
        assert authenticate_api_key(session, issued.token, NOW) is None
    assert events_of(factory, owner) == ["ACCOUNT_DELETION_REQUESTED"]


def test_delete_me_cancela_a_execucao_ativa(
    client: TestClient,
    factory: sessionmaker[Session],
    sessions: FakeSessions,
    app: FastAPI,
) -> None:
    owner = new_user(factory)
    storage: FileStorage = app.state.storage
    run_id = queue_run(factory, storage, owner)
    sessions.sign_in(owner)

    response = client.delete(ME)

    assert response.status_code == 202, response.text
    with factory() as session:
        status = session.execute(select(run.c.status).where(run.c.id == run_id)).scalar_one()
    assert status == Status.CANCELED


def test_repetir_delete_me_202_mantem_o_primeiro_pedido(
    client: TestClient, factory: sessionmaker[Session], sessions: FakeSessions
) -> None:
    owner = new_user(factory)
    sessions.sign_in(owner)
    client.delete(ME)
    sessions.now = NOW + timedelta(minutes=5)

    response = client.delete(ME)

    assert response.status_code == 202, response.text
    assert deletion_requested_at(factory, owner) == NOW


def test_delete_me_com_login_de_11_min_401_reauth_required(
    client: TestClient, factory: sessionmaker[Session], sessions: FakeSessions
) -> None:
    owner = new_user(factory)
    issued = new_key(factory, owner, now=NOW)
    sessions.sign_in(owner, minutes_ago=11)

    response = client.delete(ME)

    assert_problem(response, 401, "REAUTH_REQUIRED")
    assert deletion_requested_at(factory, owner) is None
    with unit_of_work(factory) as session:
        assert authenticate_api_key(session, issued.token, NOW) is not None


def test_delete_me_com_login_de_10_min_exatos_aceita(
    client: TestClient, factory: sessionmaker[Session], sessions: FakeSessions
) -> None:
    owner = new_user(factory)
    sessions.sign_in(owner, minutes_ago=10)

    assert client.delete(ME).status_code == 202
    assert deletion_requested_at(factory, owner) == NOW


def test_delete_me_nao_toca_outra_conta(
    client: TestClient,
    factory: sessionmaker[Session],
    sessions: FakeSessions,
    app: FastAPI,
) -> None:
    owner, other = new_user(factory), new_user(factory)
    other_key = new_key(factory, other, now=NOW)
    other_run = queue_run(factory, app.state.storage, other)
    sessions.sign_in(owner)

    client.delete(ME)

    assert deletion_requested_at(factory, other) is None
    with unit_of_work(factory) as session:
        assert authenticate_api_key(session, other_key.token, NOW) is not None
        status = session.execute(select(run.c.status).where(run.c.id == other_run)).scalar_one()
    assert status == Status.QUEUED
