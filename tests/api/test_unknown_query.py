"""Parâmetro de consulta fora do contrato em `/api` recebe 422 (#442, item 44a)."""

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from tests.api.support import assert_problem
from tests.identity.support import new_key
from tests.runs.support import count_rows, new_owner, ready_inputs

pytestmark = pytest.mark.integration

RUNS = "/api/v1/runs"
UNKNOWN_QUERY_ERRORS = [
    {"field": "query", "code": "extra_forbidden", "message": "parâmetro de consulta não aceito"}
]


@pytest.fixture
def owner_id(factory: sessionmaker[Session]) -> uuid.UUID:
    return new_owner(factory)


@pytest.fixture
def auth(factory: sessionmaker[Session], owner_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {new_key(factory, owner_id).token}"}


@pytest.fixture
def csv_command(
    factory: sessionmaker[Session], owner_id: uuid.UUID, tmp_path: Path
) -> dict[str, Any]:
    inputs = ready_inputs(factory, FileStorage(tmp_path / "storage"), owner_id)
    return {
        "recipe_id": "csv-janitor",
        "recipe_version": 1,
        "input_upload_ids": [str(upload_id) for upload_id in inputs],
        "parameters": {},
    }


def count_runs(factory: sessionmaker[Session]) -> int:
    return count_rows(factory)["run"]


def test_post_runs_com_query_desconhecida_e_422_e_nao_cria_run(
    client: TestClient,
    factory: sessionmaker[Session],
    auth: dict[str, str],
    csv_command: dict[str, Any],
) -> None:
    # `validate_onl` (erro de digitação) era ignorado: a Run nascia e gastava cota.
    headers = auth | {"Idempotency-Key": str(uuid.uuid4())}

    response = client.post(f"{RUNS}?validate_onl=true", json=csv_command, headers=headers)

    assert_problem(response, 422, "VALIDATION_FAILED")
    assert response.json()["errors"] == UNKNOWN_QUERY_ERRORS
    assert count_runs(factory) == 0


def test_get_runs_com_filtro_inexistente_e_422(client: TestClient, auth: dict[str, str]) -> None:
    # `state` não é filtro; ignorá-lo listaria tudo como se o filtro tivesse valido.
    response = client.get(f"{RUNS}?state=failed", headers=auth)

    assert_problem(response, 422, "VALIDATION_FAILED")
    assert response.json()["errors"] == UNKNOWN_QUERY_ERRORS


def test_query_declarada_continua_aceita(client: TestClient, auth: dict[str, str]) -> None:
    assert client.get(f"{RUNS}?status=failed&limit=5", headers=auth).status_code == 200


def test_nome_da_query_desconhecida_nao_ecoa(client: TestClient, auth: dict[str, str]) -> None:
    canary = f"CANARY-{uuid.uuid4()}"

    response = client.get(f"{RUNS}?{canary}=1", headers=auth)

    assert response.status_code == 422
    assert canary not in response.text
    assert canary not in str(response.headers)


@pytest.mark.parametrize(
    "url",
    [
        "/readyz?x=1",
        "/auth/login/github?iss=x&session_state=y",
        "/auth/callback/github?code=c&state=s&iss=https://github.com",
    ],
)
def test_query_desconhecida_fora_da_api_nao_e_recusada(client: TestClient, url: str) -> None:
    assert client.get(url, follow_redirects=False).status_code != 422
