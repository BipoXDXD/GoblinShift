"""`POST /runs/{id}/cancel`: 200 terminal, 202 parando, 404 alheio, escopo exigido (1.6, 3.5)."""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.identity import Scope
from goblinshift.platform.db import unit_of_work
from goblinshift.runs.tables import run
from goblinshift.runs.worker.claim import claim_next
from tests.api.files_support import new_account, storage_of
from tests.api.support import assert_problem
from tests.identity.support import new_key, new_user
from tests.runs.worker_support import LEASE, open_run_uses, queue_run, run_row

pytestmark = pytest.mark.integration


def cancel_url(run_id: uuid.UUID) -> str:
    return f"/api/v1/runs/{run_id}/cancel"


def test_cancelar_na_fila_responde_200_com_canceled(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    run_id = queue_run(factory, storage_of(app), owner)

    response = client.post(cancel_url(run_id), headers=auth)

    assert response.status_code == 200, response.text
    assert response.json() == {"id": str(run_id), "status": "canceled", "error_code": "CANCELED"}
    assert open_run_uses(factory, run_id) == 0


def test_cancelar_rodando_responde_202_com_stopping(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    run_id = queue_run(factory, storage_of(app), owner)
    assert claim_next(factory, storage_of(app), LEASE) is not None

    response = client.post(cancel_url(run_id), headers=auth)

    assert response.status_code == 202, response.text
    assert response.json()["status"] == "stopping"
    assert run_row(factory, run_id)["cancel_requested_at"] is not None


def test_cancelar_de_novo_e_seguro(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    run_id = queue_run(factory, storage_of(app), owner)
    client.post(cancel_url(run_id), headers=auth)

    response = client.post(cancel_url(run_id), headers=auth)

    assert response.status_code == 200
    assert response.json()["status"] == "canceled"


def test_execucao_ja_terminada_devolve_o_estado_real(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner, auth = new_account(factory)
    run_id = queue_run(factory, storage_of(app), owner)
    with unit_of_work(factory) as session:
        session.execute(
            update(run)
            .where(run.c.id == run_id)
            .values(status="failed", error_code="QUEUE_EXPIRED", finished_at=run.c.created_at)
        )

    response = client.post(cancel_url(run_id), headers=auth)

    assert response.status_code == 200
    assert response.json() == {"id": str(run_id), "status": "failed", "error_code": "QUEUE_EXPIRED"}
    assert run_row(factory, run_id)["cancel_requested_at"] is None


@pytest.mark.parametrize("alheia", [True, False])
def test_execucao_alheia_ou_inexistente_responde_404_igual(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session], alheia: bool
) -> None:
    owner, _ = new_account(factory)
    _, other_auth = new_account(factory)
    run_id = queue_run(factory, storage_of(app), owner) if alheia else uuid.uuid4()

    response = client.post(cancel_url(run_id), headers=other_auth)

    body = assert_problem(response, 404, "NOT_FOUND")
    assert str(run_id) not in body["detail"]
    if alheia:
        assert run_row(factory, run_id)["status"] == "queued"


def test_sem_escopo_runs_write_responde_403(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    owner = new_user(factory)
    key = new_key(factory, owner, scopes=frozenset({Scope.RUNS_READ}))
    run_id = queue_run(factory, storage_of(app), owner)

    response = client.post(cancel_url(run_id), headers={"Authorization": f"Bearer {key.token}"})

    assert_problem(response, 403, "INSUFFICIENT_SCOPE")
    assert run_row(factory, run_id)["status"] == "queued"


def test_sem_credencial_responde_401(client: TestClient) -> None:
    response = client.post(cancel_url(uuid.uuid4()))

    assert_problem(response, 401, "AUTHENTICATION_REQUIRED")
