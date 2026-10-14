"""Rotas de execuções: contrato HTTP de 1.6 com PostgreSQL real."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.catalog import Catalog, VersionStatus
from goblinshift.catalog.versions import CSV_JANITOR_V1
from goblinshift.entrypoints.api.app import create_app
from goblinshift.entrypoints.api.dependencies import current_time
from goblinshift.files.states import ArtifactRole
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import artifact, stored_object
from goblinshift.identity import Scope
from goblinshift.platform.db import unit_of_work
from goblinshift.platform.settings import ExecutorConfigError, Settings
from goblinshift.runs import acceptance
from goblinshift.runs.acceptance import MAX_QUEUED_RUNS
from goblinshift.runs.lifecycle import Status
from goblinshift.runs.tables import run, run_output
from tests.api.support import assert_problem
from tests.identity.support import new_key, prepare_database
from tests.runs.support import (
    count_rows,
    insert_finished_run,
    new_owner,
    ready_inputs,
    unavailable_upload,
)

pytestmark = pytest.mark.integration

RUNS = "/api/v1/runs"
RUN_KEYS = {
    "id",
    "recipe_id",
    "recipe_version",
    "status",
    "attempt",
    "progress",
    "queue_ahead",
    "created_at",
    "queue_expires_at",
    "deadline_at",
    "error",
}
BASE_TIME = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
EXECUTOR_IMAGE = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "c" * 64


class Account:
    def __init__(self, factory: sessionmaker[Session], scopes: frozenset[Scope] | None = None):
        self.id = new_owner(factory)
        issued = (
            new_key(factory, self.id)
            if scopes is None
            else new_key(factory, self.id, scopes=scopes)
        )
        self.headers = {"Authorization": f"Bearer {issued.token}"}


@pytest.fixture
def storage(tmp_path: Path) -> FileStorage:
    return FileStorage(tmp_path / "storage")


def body(upload_ids: list[uuid.UUID], parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "recipe_id": "csv-janitor",
        "recipe_version": 1,
        "input_upload_ids": [str(u) for u in upload_ids],
        "parameters": parameters or {},
    }


def post_run(
    client: TestClient,
    account: Account,
    payload: dict[str, Any],
    key: str | None = None,
    query: str = "",
) -> httpx.Response:
    headers = dict(account.headers)
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.post(f"{RUNS}{query}", json=payload, headers=headers)


def create_run(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage, account: Account
) -> dict[str, Any]:
    response = post_run(
        client, account, body(ready_inputs(factory, storage, account.id)), str(uuid.uuid4())
    )
    assert response.status_code == 202, response.text
    created: dict[str, Any] = response.json()
    return created


# POST /runs


def test_aceita_com_202_location_e_o_corpo_do_contrato(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    account = Account(factory)
    inputs = ready_inputs(factory, storage, account.id, 2)

    response = post_run(client, account, body(inputs), str(uuid.uuid4()))

    assert response.status_code == 202, response.text
    created = response.json()
    assert created.keys() == RUN_KEYS | {"idempotency_expires_at"}
    assert response.headers["location"] == f"{RUNS}/{created['id']}"
    assert "idempotency-replayed" not in response.headers
    assert created["recipe_id"] == "csv-janitor"
    assert created["recipe_version"] == 1
    assert created["status"] == "queued"
    assert created["attempt"] == 0
    assert created["progress"] == {"phase": None, "processed": 0, "total": 2}
    assert created["queue_ahead"] == 0
    assert created["deadline_at"] is None
    assert created["error"] is None
    created_at = datetime.fromisoformat(created["created_at"])
    assert datetime.fromisoformat(created["queue_expires_at"]) - created_at == timedelta(hours=2)
    assert created["created_at"].endswith("Z")


def test_replay_devolve_o_mesmo_location_sem_criar_nada(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    account = Account(factory)
    payload = body(ready_inputs(factory, storage, account.id))
    key = str(uuid.uuid4())
    first = post_run(client, account, payload, key)
    before = count_rows(factory)

    again = post_run(client, account, payload, key)

    assert again.status_code == 202
    assert again.headers["location"] == first.headers["location"]
    assert again.headers["idempotency-replayed"] == "true"
    assert again.json()["id"] == first.json()["id"]
    assert again.json()["idempotency_expires_at"] == first.json()["idempotency_expires_at"]
    assert count_rows(factory) == before


def test_mesma_chave_com_outro_comando_responde_422(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    account = Account(factory)
    inputs = ready_inputs(factory, storage, account.id)
    key = str(uuid.uuid4())
    post_run(client, account, body(inputs), key)

    response = post_run(client, account, body(inputs, {"trim_columns": ["a"]}), key)

    assert_problem(response, 422, "IDEMPOTENCY_KEY_REUSED")


@pytest.mark.parametrize("key", [None, "a" * 15, "a" * 65, "chave com espaco!"])
def test_chave_ausente_ou_invalida_responde_400(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage, key: str | None
) -> None:
    account = Account(factory)

    response = post_run(client, account, body(ready_inputs(factory, storage, account.id)), key)

    assert_problem(response, 400, "IDEMPOTENCY_KEY_INVALID")
    assert count_rows(factory)["run"] == 0


@pytest.mark.parametrize("length", [16, 64])
def test_chave_com_16_ou_64_caracteres_e_aceita(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage, length: int
) -> None:
    account = Account(factory)

    response = post_run(
        client, account, body(ready_inputs(factory, storage, account.id)), "k" * length
    )

    assert response.status_code == 202, response.text


@pytest.mark.parametrize("reason", ["inexistente", "alheio", "nao-pronto", "excluido"])
def test_upload_indisponivel_responde_o_mesmo_422_para_todos_os_motivos(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage, reason: str
) -> None:
    account = Account(factory)
    bad = unavailable_upload(factory, storage, account.id, reason)

    response = post_run(client, account, body([bad]), str(uuid.uuid4()))

    problem = assert_problem(response, 422, "INPUT_NOT_AVAILABLE")
    assert problem["detail"] == "Alguma entrada citada não está disponível."
    assert str(bad) not in response.text
    assert "errors" not in problem


def test_upload_repetido_responde_422_no_campo(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    account = Account(factory)
    [upload_id] = ready_inputs(factory, storage, account.id)

    response = post_run(client, account, body([upload_id, upload_id]), str(uuid.uuid4()))

    problem = assert_problem(response, 422, "VALIDATION_FAILED")
    assert [e["field"] for e in problem["errors"]] == ["body.input_upload_ids"]


@pytest.mark.parametrize(("count", "status"), [(5, 202), (6, 422)])
def test_ate_cinco_tabelas(
    client: TestClient,
    factory: sessionmaker[Session],
    storage: FileStorage,
    count: int,
    status: int,
) -> None:
    account = Account(factory)

    response = post_run(
        client, account, body(ready_inputs(factory, storage, account.id, count)), str(uuid.uuid4())
    )

    assert response.status_code == status, response.text


def test_parametro_invalido_responde_422_com_o_campo(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    account = Account(factory)
    payload = body(ready_inputs(factory, storage, account.id), {"formula_guard": "talvez"})

    response = post_run(client, account, payload, str(uuid.uuid4()))

    problem = assert_problem(response, 422, "VALIDATION_FAILED")
    assert [e["field"] for e in problem["errors"]] == ["body.parameters.formula_guard"]


def test_campo_desconhecido_no_corpo_responde_422(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    account = Account(factory)
    payload = {**body(ready_inputs(factory, storage, account.id)), "status": "succeeded"}

    response = post_run(client, account, payload, str(uuid.uuid4()))

    assert_problem(response, 422, "VALIDATION_FAILED")
    assert count_rows(factory)["run"] == 0


def test_versao_retirada_responde_422(
    client: TestClient,
    factory: sessionmaker[Session],
    storage: FileStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retired = replace(CSV_JANITOR_V1, status=VersionStatus.RETIRED)
    monkeypatch.setattr(acceptance, "CATALOG", Catalog([retired]))
    account = Account(factory)

    response = post_run(
        client, account, body(ready_inputs(factory, storage, account.id)), str(uuid.uuid4())
    )

    assert_problem(response, 422, "RECIPE_VERSION_RETIRED")


def test_segunda_execucao_ativa_responde_409(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    account = Account(factory)
    create_run(client, factory, storage, account)

    response = post_run(
        client, account, body(ready_inputs(factory, storage, account.id)), str(uuid.uuid4())
    )

    assert_problem(response, 409, "ACTIVE_RUN_LIMIT")


def test_fila_cheia_responde_503_com_retry_after(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    for _ in range(MAX_QUEUED_RUNS):
        create_run(client, factory, storage, Account(factory))
    account = Account(factory)

    response = post_run(
        client, account, body(ready_inputs(factory, storage, account.id)), str(uuid.uuid4())
    )

    assert_problem(response, 503, "QUEUE_FULL")
    assert int(response.headers["retry-after"]) > 0


def test_validate_only_resolve_os_parametros_sem_criar_nem_consumir_a_chave(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    account = Account(factory)
    key = str(uuid.uuid4())
    payload = {
        "recipe_id": "csv-janitor",
        "recipe_version": 1,
        "parameters": {"trim_columns": ["a"]},
    }

    response = post_run(client, account, payload, key, "?validate_only=true")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "recipe_id": "csv-janitor",
        "recipe_version": 1,
        "input_upload_ids": [],
        "parameters": {
            "case_rules": [],
            "deduplicate_by": [],
            "filters": [],
            "formula_guard": "none",
            "input_delimiter": ",",
            "output_columns": None,
            "output_delimiter": ",",
            "trim_columns": ["a"],
        },
    }
    assert count_rows(factory) == {
        "run": 0,
        "run_input": 0,
        "object_use": 0,
        "idempotency_record": 0,
    }
    real = post_run(
        client,
        account,
        body(ready_inputs(factory, storage, account.id), {"trim_columns": ["a"]}),
        key,
    )
    assert real.status_code == 202
    assert "idempotency-replayed" not in real.headers


@pytest.mark.parametrize("recipe_id", ["image-forge", "downloads-detox"])
def test_aceita_as_receitas_da_fatia_5_com_os_defaults(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage, recipe_id: str
) -> None:
    account = Account(factory)
    payload = {**body(ready_inputs(factory, storage, account.id)), "recipe_id": recipe_id}

    response = post_run(client, account, payload, str(uuid.uuid4()))

    assert response.status_code == 202, response.text
    assert response.json()["recipe_id"] == recipe_id


def test_detox_recusa_name_prefix_enviado_com_name_mode_preserve(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    account = Account(factory)
    payload = {
        "recipe_id": "downloads-detox",
        "recipe_version": 1,
        "parameters": {"name_mode": "preserve", "name_prefix": "doc"},
    }

    response = post_run(client, account, payload, None, "?validate_only=true")

    assert_problem(response, 422, "VALIDATION_FAILED")


def test_validate_only_nao_confere_uploads_nem_exige_chave(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    account = Account(factory)

    response = post_run(client, account, body([uuid.uuid4()]), None, "?validate_only=true")

    assert response.status_code == 200, response.text


def test_validate_only_com_parametro_invalido_responde_422(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    account = Account(factory)

    response = post_run(
        client, account, body([], {"formula_guard": "x"}), None, "?validate_only=true"
    )

    assert_problem(response, 422, "VALIDATION_FAILED")


def test_criar_exige_o_escopo_runs_write(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    account = Account(factory, frozenset({Scope.RUNS_READ}))

    response = post_run(client, account, body([]), str(uuid.uuid4()))

    assert_problem(response, 403, "INSUFFICIENT_SCOPE")


# GET /runs/{id}


def test_execucao_nao_terminal_tem_retry_after_2(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    account = Account(factory)
    created = create_run(client, factory, storage, account)

    response = client.get(f"{RUNS}/{created['id']}", headers=account.headers)

    assert response.status_code == 200
    assert response.headers["retry-after"] == "2"
    assert response.json().keys() == RUN_KEYS
    assert response.json()["id"] == created["id"]


def test_execucao_terminal_nao_tem_retry_after_e_traz_o_erro(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    account = Account(factory)
    run_id = insert_finished_run(
        factory, account.id, BASE_TIME, status=Status.FAILED, error_code="QUEUE_EXPIRED"
    )

    response = client.get(f"{RUNS}/{run_id}", headers=account.headers)

    assert response.status_code == 200
    assert "retry-after" not in response.headers
    assert response.json()["status"] == "failed"
    assert response.json()["error"]["code"] == "QUEUE_EXPIRED"


def test_queue_ahead_conta_as_aceitas_antes_que_nao_comecaram(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    first = create_run(client, factory, storage, Account(factory))
    started = create_run(client, factory, storage, Account(factory))
    with unit_of_work(factory) as session:
        session.execute(
            update(run)
            .where(run.c.id == uuid.UUID(started["id"]))
            .values(
                status="running",
                phase="inspecting",
                claim_count=1,
                generation=1,
                deadline_at=BASE_TIME,
            )
        )
    account = Account(factory)
    third = create_run(client, factory, storage, account)

    response = client.get(f"{RUNS}/{third['id']}", headers=account.headers)

    assert third["queue_ahead"] == 1
    assert response.json()["queue_ahead"] == 1
    assert first["queue_ahead"] == 0


def test_outra_conta_recebe_404_igual_ao_de_inexistente(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = Account(factory)
    created = create_run(client, factory, storage, owner)
    intruder = Account(factory)

    foreign = client.get(f"{RUNS}/{created['id']}", headers=intruder.headers)
    missing = client.get(f"{RUNS}/{uuid.uuid7()}", headers=intruder.headers)
    foreign_results = client.get(f"{RUNS}/{created['id']}/results", headers=intruder.headers)

    for response in (foreign, missing, foreign_results):
        problem = assert_problem(response, 404, "NOT_FOUND")
        assert problem["detail"] == "Execução não encontrada."
        assert created["id"] not in response.text.replace(problem["instance"], "")


def test_listagem_de_outra_conta_nao_mostra_execucoes_alheias(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = Account(factory)
    created = create_run(client, factory, storage, owner)
    intruder = Account(factory)

    response = client.get(RUNS, headers=intruder.headers)

    assert response.json() == {"data": [], "next_cursor": None}
    assert created["id"] not in response.text


# GET /runs


def list_ids(client: TestClient, account: Account, query: str = "") -> tuple[list[str], Any]:
    response = client.get(f"{RUNS}{query}", headers=account.headers)
    assert response.status_code == 200, response.text
    page = response.json()
    return [item["id"] for item in page["data"]], page["next_cursor"]


def test_paginacao_estavel_com_empates_de_created_at(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    account = Account(factory)
    tied = [insert_finished_run(factory, account.id, BASE_TIME) for _ in range(4)]
    older = insert_finished_run(factory, account.id, BASE_TIME - timedelta(seconds=1))
    newer = insert_finished_run(factory, account.id, BASE_TIME + timedelta(seconds=1))
    expected = [str(newer), *sorted((str(t) for t in tied), reverse=True), str(older)]

    seen: list[str] = []
    ids, cursor = list_ids(client, account, "?limit=2")
    seen += ids
    while cursor is not None:
        ids, cursor = list_ids(client, account, f"?limit=2&cursor={cursor}")
        seen += ids

    assert seen == expected


def test_ultima_pagina_cheia_termina_com_cursor_nulo(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    account = Account(factory)
    for offset in range(2):
        insert_finished_run(factory, account.id, BASE_TIME + timedelta(seconds=offset))

    ids, cursor = list_ids(client, account, "?limit=2")

    assert len(ids) == 2
    assert cursor is None


def test_listagem_padrao_traz_25(client: TestClient, factory: sessionmaker[Session]) -> None:
    account = Account(factory)
    for offset in range(26):
        insert_finished_run(factory, account.id, BASE_TIME + timedelta(seconds=offset))

    ids, cursor = list_ids(client, account)

    assert len(ids) == 25
    assert cursor is not None


def test_filtros_por_receita_status_e_periodo(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    account = Account(factory)
    before = insert_finished_run(factory, account.id, BASE_TIME - timedelta(seconds=1))
    at_start = insert_finished_run(factory, account.id, BASE_TIME)
    failed = insert_finished_run(
        factory,
        account.id,
        BASE_TIME + timedelta(minutes=1),
        status=Status.FAILED,
        error_code="QUEUE_EXPIRED",
    )
    other_recipe = insert_finished_run(
        factory, account.id, BASE_TIME + timedelta(minutes=2), recipe_id="image-forge"
    )
    at_end = insert_finished_run(factory, account.id, BASE_TIME + timedelta(hours=1))
    window = "created_from=2026-10-01T12:00:00Z&created_to=2026-10-01T13:00:00Z"

    assert list_ids(client, account, f"?{window}")[0] == [
        str(other_recipe),
        str(failed),
        str(at_start),
    ]
    assert list_ids(client, account, "?status=failed")[0] == [str(failed)]
    assert list_ids(client, account, "?recipe_id=image-forge")[0] == [str(other_recipe)]
    assert str(before) not in list_ids(client, account, f"?{window}")[0]
    assert str(at_end) not in list_ids(client, account, f"?{window}")[0]


@pytest.mark.parametrize(("limit", "status"), [(0, 422), (1, 200), (100, 200), (101, 422)])
def test_limit_entre_1_e_100(
    client: TestClient, factory: sessionmaker[Session], limit: int, status: int
) -> None:
    account = Account(factory)

    response = client.get(f"{RUNS}?limit={limit}", headers=account.headers)

    assert response.status_code == status


def test_cursor_adulterado_responde_400(client: TestClient, factory: sessionmaker[Session]) -> None:
    account = Account(factory)
    for offset in range(3):
        insert_finished_run(factory, account.id, BASE_TIME + timedelta(seconds=offset))
    _, cursor = list_ids(client, account, "?limit=1")
    forged = cursor[:-2] + ("AA" if not cursor.endswith("AA") else "BB")

    response = client.get(f"{RUNS}?limit=1&cursor={forged}", headers=account.headers)

    assert_problem(response, 400, "BAD_REQUEST")


def test_cursor_com_filtro_diferente_responde_400(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    account = Account(factory)
    for offset in range(3):
        insert_finished_run(factory, account.id, BASE_TIME + timedelta(seconds=offset))
    _, cursor = list_ids(client, account, "?limit=1")

    response = client.get(
        f"{RUNS}?limit=1&status=succeeded&cursor={cursor}", headers=account.headers
    )

    problem = assert_problem(response, 400, "BAD_REQUEST")
    assert "filtros" in problem["detail"]


def test_cursor_vencido_responde_400(
    app: FastAPI, client: TestClient, factory: sessionmaker[Session]
) -> None:
    account = Account(factory)
    for offset in range(3):
        insert_finished_run(factory, account.id, BASE_TIME + timedelta(seconds=offset))
    _, cursor = list_ids(client, account, "?limit=1")
    later = datetime.now(UTC) + timedelta(hours=24, minutes=1)
    app.dependency_overrides[current_time] = lambda: later

    response = client.get(f"{RUNS}?limit=1&cursor={cursor}", headers=account.headers)

    assert_problem(response, 400, "BAD_REQUEST")


def test_data_sem_fuso_no_filtro_responde_422(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    account = Account(factory)

    response = client.get(f"{RUNS}?created_from=2026-10-01T12:00:00", headers=account.headers)

    assert_problem(response, 422, "VALIDATION_FAILED")


# GET /runs/{id}/results


def test_resultados_antes_da_publicacao(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    account = Account(factory)
    created = create_run(client, factory, storage, account)

    response = client.get(f"{RUNS}/{created['id']}/results", headers=account.headers)

    assert response.status_code == 200
    assert response.json() == {"published": False, "expires_at": None, "artifacts": []}


def test_resultados_publicados_em_ordem_de_posicao(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    account = Account(factory)
    run_id = insert_finished_run(factory, account.id, BASE_TIME)
    report, table = publish(factory, account.id, run_id)

    response = client.get(f"{RUNS}/{run_id}/results", headers=account.headers)

    assert response.status_code == 200
    results = response.json()
    assert results["published"] is True
    assert results["expires_at"] is not None
    assert results["artifacts"] == [
        {
            "artifact_id": str(table),
            "logical_name": "dados.csv",
            "role": "output",
            "file_name": "output-final.dat",
        },
        {
            "artifact_id": str(report),
            "logical_name": "report.json",
            "role": "report",
            "file_name": "report-final.dat",
        },
    ]


def test_publicado_sem_saidas_e_published_true(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    account = Account(factory)
    run_id = insert_finished_run(
        factory, account.id, BASE_TIME, status=Status.FAILED, error_code="NO_VALID_OUTPUTS"
    )

    response = client.get(f"{RUNS}/{run_id}/results", headers=account.headers)

    assert response.json() == {"published": True, "expires_at": None, "artifacts": []}


def publish(
    factory: sessionmaker[Session], owner_id: uuid.UUID, run_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    """Grava artefatos e vínculos como a publicação do worker gravaria (posição 1 antes)."""
    ids: dict[ArtifactRole, uuid.UUID] = {}
    with unit_of_work(factory) as session:
        for role in (ArtifactRole.REPORT, ArtifactRole.OUTPUT):
            object_id = session.execute(
                insert(stored_object)
                .values(
                    owner_id=owner_id,
                    storage_key=f"runs/{run_id}/{role}",
                    state="committed",
                    sha256=b"\x00" * 32,
                    size_bytes=1,
                )
                .returning(stored_object.c.id)
            ).scalar_one()
            ids[role] = session.execute(
                insert(artifact)
                .values(
                    owner_id=owner_id,
                    object_id=object_id,
                    role=role,
                    file_name=f"{role}-final.dat",
                    expires_at=BASE_TIME + timedelta(hours=48),
                )
                .returning(artifact.c.id)
            ).scalar_one()
        session.execute(
            insert(run_output),
            [
                {
                    "run_id": run_id,
                    "position": 1,
                    "owner_id": owner_id,
                    "artifact_id": ids[ArtifactRole.REPORT],
                    "logical_name": "report.json",
                },
                {
                    "run_id": run_id,
                    "position": 0,
                    "owner_id": owner_id,
                    "artifact_id": ids[ArtifactRole.OUTPUT],
                    "logical_name": "dados.csv",
                },
            ],
        )
    return ids[ArtifactRole.REPORT], ids[ArtifactRole.OUTPUT]


def test_openapi_publica_a_variante_do_catalogo(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()

    operation = spec["paths"][RUNS]["post"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    variants = schema["allOf"][1]["oneOf"]
    assert [v["properties"]["recipe_id"]["const"] for v in variants] == [
        "csv-janitor",
        "image-forge",
        "downloads-detox",
    ]
    assert schema["unevaluatedProperties"] is False
    assert operation["operationId"] == "createRun"


# Imagem do executor


def test_execucao_aceita_fixa_a_imagem_do_executor_da_configuracao(
    db_settings: Settings, tmp_path: Path, storage: FileStorage
) -> None:
    settings = db_settings.model_copy(
        update={
            "storage_root": tmp_path / "storage",
            "runner": "executor_ctl",
            "executor_image": EXECUTOR_IMAGE,
        }
    )
    app = create_app(settings)
    prepare_database(settings, app.state.engine)
    factory: sessionmaker[Session] = app.state.session_factory
    account = Account(factory)
    # O lifespan do cliente descarta o engine na saída: a leitura fica dentro do bloco.
    with TestClient(app) as client, factory() as session:
        created = create_run(client, factory, storage, account)
        pinned = session.execute(
            select(run.c.executor_digest).where(run.c.id == uuid.UUID(created["id"]))
        ).scalar_one()

    assert pinned == EXECUTOR_IMAGE


def test_api_em_producao_nao_sobe_sem_o_executor_ctl() -> None:
    settings = Settings(
        environment="production", db_port=1, cursor_secret=SecretStr("s" * 32), runner="local"
    )

    with pytest.raises(ExecutorConfigError, match="GS_RUNNER=executor_ctl"):
        create_app(settings)


def test_api_em_producao_nao_sobe_sem_a_imagem_do_executor() -> None:
    settings = Settings(
        environment="production",
        db_port=1,
        cursor_secret=SecretStr("s" * 32),
        runner="executor_ctl",
    )

    with pytest.raises(ExecutorConfigError, match="GS_EXECUTOR_IMAGE"):
        create_app(settings)
