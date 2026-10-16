"""`DELETE /runs/{run_id}/artifacts`: saídas excluídas antes das 48 h, originais intactos (1.6)."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.artifacts import publish_artifact
from goblinshift.files.states import ArtifactRole
from goblinshift.files.tables import stored_object, upload
from goblinshift.platform.db import unit_of_work
from goblinshift.runs.lifecycle import Status
from goblinshift.runs.tables import run, run_input, run_output
from tests.api.files_support import new_account, publish, storage_of
from tests.api.support import assert_problem
from tests.files.support import ready_upload
from tests.runs.support import insert_finished_run

pytestmark = pytest.mark.integration

RUNS = "/api/v1/runs"
CREATED = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class FinishedRun:
    run_id: uuid.UUID
    upload_id: uuid.UUID
    # Saída que reaproveita o objeto da entrada (Detox, ADR 020) e saída com objeto próprio.
    reused_artifact: uuid.UUID
    own_artifact: uuid.UUID


def finished_run(app: FastAPI, owner: uuid.UUID, status: Status = Status.SUCCEEDED) -> FinishedRun:
    factory: sessionmaker[Session] = app.state.session_factory
    run_id = insert_finished_run(factory, owner, CREATED, status=status)
    source = ready_upload(factory, storage_of(app), owner, b"conteudo")
    own = publish(app, owner, b"relatorio", "report.json")
    with unit_of_work(factory) as session:
        object_id = session.execute(
            select(upload.c.object_id).where(upload.c.id == source.id)
        ).scalar_one()
        reused = publish_artifact(session, owner, object_id, ArtifactRole.OUTPUT, "a.csv").id
        session.execute(
            insert(run_input).values(run_id=run_id, position=0, owner_id=owner, upload_id=source.id)
        )
        session.execute(
            insert(run_output),
            [
                {
                    "run_id": run_id,
                    "position": position,
                    "owner_id": owner,
                    "artifact_id": artifact_id,
                    "logical_name": name,
                }
                for position, (artifact_id, name) in enumerate(
                    [(reused, "a.csv"), (own, "report.json")]
                )
            ],
        )
    return FinishedRun(run_id, source.id, reused, own)


def upload_object_state(factory: sessionmaker[Session], upload_id: uuid.UUID) -> str:
    with factory() as session:
        state: str = session.execute(
            select(stored_object.c.state)
            .join(upload, upload.c.object_id == stored_object.c.id)
            .where(upload.c.id == upload_id)
        ).scalar_one()
    return state


def test_exclui_as_saidas_com_202_e_bloqueia_novos_downloads(
    app: FastAPI, client: TestClient
) -> None:
    owner, auth = new_account(app.state.session_factory)
    finished = finished_run(app, owner)

    response = client.delete(f"{RUNS}/{finished.run_id}/artifacts", headers=auth)

    assert response.status_code == 202
    assert response.content == b""
    for artifact_id in (finished.reused_artifact, finished.own_artifact):
        details = client.get(f"/api/v1/artifacts/{artifact_id}", headers=auth).json()
        assert details["availability"] == "deleted"
        download = client.get(f"/api/v1/artifacts/{artifact_id}/download", headers=auth)
        assert_problem(download, 410, "GONE")


def test_o_original_continua_disponivel_mesmo_com_objeto_compartilhado(
    app: FastAPI, client: TestClient
) -> None:
    factory: sessionmaker[Session] = app.state.session_factory
    owner, auth = new_account(factory)
    finished = finished_run(app, owner)

    client.delete(f"{RUNS}/{finished.run_id}/artifacts", headers=auth)

    source = client.get(f"/api/v1/uploads/{finished.upload_id}", headers=auth).json()
    assert source["availability"] == "available"
    assert upload_object_state(factory, finished.upload_id) == "committed"


def test_repetir_responde_202_de_novo(app: FastAPI, client: TestClient) -> None:
    owner, auth = new_account(app.state.session_factory)
    finished = finished_run(app, owner)

    first = client.delete(f"{RUNS}/{finished.run_id}/artifacts", headers=auth)
    second = client.delete(f"{RUNS}/{finished.run_id}/artifacts", headers=auth)

    assert (first.status_code, second.status_code) == (202, 202)


def test_execucao_sem_saidas_responde_202(app: FastAPI, client: TestClient) -> None:
    factory: sessionmaker[Session] = app.state.session_factory
    owner, auth = new_account(factory)
    run_id = insert_finished_run(factory, owner, CREATED, status=Status.CANCELED)

    response = client.delete(f"{RUNS}/{run_id}/artifacts", headers=auth)

    assert response.status_code == 202


def test_execucao_nao_terminal_responde_409(app: FastAPI, client: TestClient) -> None:
    factory: sessionmaker[Session] = app.state.session_factory
    owner, auth = new_account(factory)
    run_id = insert_finished_run(factory, owner, CREATED)
    with unit_of_work(factory) as session:
        session.execute(
            update(run).where(run.c.id == run_id).values(status="queued", finished_at=None)
        )

    response = client.delete(f"{RUNS}/{run_id}/artifacts", headers=auth)

    assert_problem(response, 409, "STATE_CONFLICT")


def test_execucao_de_outra_conta_responde_404_e_nada_muda(app: FastAPI, client: TestClient) -> None:
    factory: sessionmaker[Session] = app.state.session_factory
    victim, victim_auth = new_account(factory)
    finished = finished_run(app, victim)
    _, attacker_auth = new_account(factory)

    response = client.delete(f"{RUNS}/{finished.run_id}/artifacts", headers=attacker_auth)

    assert_problem(response, 404, "NOT_FOUND")
    details = client.get(f"/api/v1/artifacts/{finished.own_artifact}", headers=victim_auth)
    assert details.json()["availability"] == "available"


def test_execucao_inexistente_responde_404(app: FastAPI, client: TestClient) -> None:
    _, auth = new_account(app.state.session_factory)

    response = client.delete(f"{RUNS}/{uuid.uuid4()}/artifacts", headers=auth)

    assert_problem(response, 404, "NOT_FOUND")
