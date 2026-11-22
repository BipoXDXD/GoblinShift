"""O 422 diz onde e qual regra falhou, sem ecoar o que o cliente mandou (#442, item 44f)."""

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from tests.api.support import assert_problem
from tests.identity.support import new_key
from tests.runs.support import new_owner, ready_inputs

pytestmark = pytest.mark.integration

CANARY = f"CANARY-{uuid.uuid4()}"
NOT_ACCEPTED = "campo não aceito"

type Send = Callable[[dict[str, Any]], httpx.Response]


@pytest.fixture
def auth(factory: sessionmaker[Session]) -> tuple[uuid.UUID, dict[str, str]]:
    owner_id = new_owner(factory)
    return owner_id, {"Authorization": f"Bearer {new_key(factory, owner_id).token}"}


@pytest.fixture(params=["POST /runs", "PUT /presets/{id}"])
def send(
    request: pytest.FixtureRequest,
    client: TestClient,
    factory: sessionmaker[Session],
    auth: tuple[uuid.UUID, dict[str, str]],
    tmp_path: Path,
) -> Send:
    owner_id, headers = auth
    if request.param == "POST /runs":
        inputs = ready_inputs(factory, FileStorage(tmp_path / "storage"), owner_id)

        def post_run(parameters: dict[str, Any]) -> httpx.Response:
            command = {
                "recipe_id": "csv-janitor",
                "recipe_version": 1,
                "input_upload_ids": [str(upload_id) for upload_id in inputs],
                "parameters": parameters,
            }
            key = {"Idempotency-Key": str(uuid.uuid4())}
            return client.post("/api/v1/runs", json=command, headers=headers | key)

        return post_run

    preset = {"name": "Mensal", "recipe_id": "csv-janitor", "recipe_version": 1, "parameters": {}}
    created = client.post("/api/v1/presets", json=preset, headers=headers)
    assert created.status_code == 201, created.text
    url = f"/api/v1/presets/{created.json()['id']}"

    def put_preset(parameters: dict[str, Any]) -> httpx.Response:
        return client.put(
            url, json=preset | {"parameters": parameters}, headers=headers | {"If-Match": '"1"'}
        )

    return put_preset


@pytest.mark.parametrize(
    "parameters",
    [
        {"filters": [{"column": CANARY, "op": "is_empty", "value": "x"}]},
        {"filters": [{"column": CANARY, "op": "eq"}]},
        {"trim_columns": [CANARY, CANARY]},
        {CANARY: 1},
        {"filters": [{"column": "a", "op": "eq", "value": "x", CANARY: 1}]},
    ],
    ids=["is_empty-com-valor", "eq-sem-valor", "coluna-repetida", "chave-extra", "chave-aninhada"],
)
def test_422_nao_ecoa_o_que_o_cliente_mandou(send: Send, parameters: dict[str, Any]) -> None:
    response = send(parameters)

    body = assert_problem(response, 422, "VALIDATION_FAILED")
    assert CANARY not in response.text
    assert CANARY not in str(response.headers)
    assert not any(error["message"].startswith("Value error") for error in body["errors"])


def test_chave_extra_vira_o_local_parameters(send: Send) -> None:
    response = send({CANARY: 1, f"{CANARY}2": 2})

    assert response.json()["errors"] == [
        {"field": "body.parameters", "code": "extra_forbidden", "message": NOT_ACCEPTED}
    ]


def test_chave_extra_aninhada_vira_o_caminho_do_objeto(send: Send) -> None:
    response = send({"filters": [{"column": "a", "op": "eq", "value": "x", CANARY: 1}]})

    assert response.json()["errors"] == [
        {"field": "body.parameters.filters.0", "code": "extra_forbidden", "message": NOT_ACCEPTED}
    ]


def test_regra_da_receita_sai_so_com_a_opcao_e_a_regra(send: Send) -> None:
    response = send({"trim_columns": [CANARY, CANARY]})

    assert response.json()["errors"] == [
        {
            "field": "body.parameters.trim_columns",
            "code": "value_error",
            "message": "trim_columns repete colunas",
        }
    ]
