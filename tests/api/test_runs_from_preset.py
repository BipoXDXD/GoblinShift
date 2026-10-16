"""`POST /presets/{preset_id}/runs`: nova execução com a configuração do preset (1.1 e 1.6)."""

import uuid
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.identity import Scope
from goblinshift.runs.tables import run
from tests.api.support import assert_problem
from tests.identity.support import ALL_SCOPES, new_key
from tests.runs.support import count_rows, new_owner, ready_inputs

pytestmark = pytest.mark.integration

PRESETS = "/api/v1/presets"


class Account:
    def __init__(self, factory: sessionmaker[Session], scopes: frozenset[Scope] = ALL_SCOPES):
        self.id = new_owner(factory)
        self.headers = {"Authorization": f"Bearer {new_key(factory, self.id, scopes=scopes).token}"}


@pytest.fixture
def storage(tmp_path: Path) -> FileStorage:
    return FileStorage(tmp_path / "storage")


def create_preset(
    client: TestClient, account: Account, recipe_id: str = "csv-janitor", **parameters: Any
) -> dict[str, Any]:
    response = client.post(
        PRESETS,
        json={
            "name": f"p-{uuid.uuid4()}",
            "recipe_id": recipe_id,
            "recipe_version": 1,
            "parameters": parameters,
        },
        headers=account.headers,
    )
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def run_preset(
    client: TestClient,
    account: Account,
    preset_id: str,
    upload_ids: list[uuid.UUID],
    key: str | None = None,
) -> httpx.Response:
    return client.post(
        f"{PRESETS}/{preset_id}/runs",
        json={"input_upload_ids": [str(u) for u in upload_ids]},
        headers={**account.headers, "Idempotency-Key": key or str(uuid.uuid4())},
    )


def stored_run(factory: sessionmaker[Session], run_id: str) -> Any:
    with factory() as session:
        return session.execute(
            select(run.c.preset_id, run.c.parameters, run.c.recipe_id).where(
                run.c.id == uuid.UUID(run_id)
            )
        ).one()


def test_aceita_com_202_e_a_configuracao_do_preset(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    account = Account(factory)
    preset = create_preset(client, account, trim_columns=["a"])

    response = run_preset(client, account, preset["id"], ready_inputs(factory, storage, account.id))

    assert response.status_code == 202, response.text
    body = response.json()
    assert response.headers["location"] == f"/api/v1/runs/{body['id']}"
    assert (body["recipe_id"], body["recipe_version"]) == ("csv-janitor", 1)
    saved = stored_run(factory, body["id"])
    assert saved.preset_id == uuid.UUID(preset["id"])
    assert saved.parameters["trim_columns"] == ["a"]
    assert saved.parameters["formula_guard"] == "none"


@pytest.mark.parametrize(
    "parameters",
    [{}, {"name_mode": "indexed", "name_prefix": "foto"}],
    ids=["defaults", "indexed"],
)
def test_preset_do_detox_executa_com_os_parametros_guardados(
    client: TestClient,
    factory: sessionmaker[Session],
    storage: FileStorage,
    parameters: dict[str, Any],
) -> None:
    # O default de `name_prefix` não pode voltar como "enviado" com `name_mode: preserve`.
    account = Account(factory)
    preset = create_preset(client, account, "downloads-detox", **parameters)

    response = run_preset(client, account, preset["id"], ready_inputs(factory, storage, account.id))

    assert response.status_code == 202, response.text
    assert stored_run(factory, response.json()["id"]).recipe_id == "downloads-detox"


def test_replay_com_a_mesma_chave_devolve_a_mesma_execucao(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    account = Account(factory)
    preset = create_preset(client, account)
    inputs = ready_inputs(factory, storage, account.id)
    key = str(uuid.uuid4())

    first = run_preset(client, account, preset["id"], inputs, key)
    again = run_preset(client, account, preset["id"], inputs, key)

    assert again.status_code == 202
    assert again.headers["idempotency-replayed"] == "true"
    assert again.json()["id"] == first.json()["id"]


def test_mesma_chave_em_post_runs_e_no_preset_e_outro_comando(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    account = Account(factory)
    preset = create_preset(client, account)
    inputs = ready_inputs(factory, storage, account.id)
    key = str(uuid.uuid4())
    direct = client.post(
        "/api/v1/runs",
        json={
            "recipe_id": "csv-janitor",
            "recipe_version": 1,
            "input_upload_ids": [str(u) for u in inputs],
            "parameters": {},
        },
        headers={**account.headers, "Idempotency-Key": key},
    )

    response = run_preset(client, account, preset["id"], inputs, key)

    assert direct.status_code == 202
    assert_problem(response, 422, "IDEMPOTENCY_KEY_REUSED")


def test_preset_de_outra_conta_responde_404_sem_criar(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    preset = create_preset(client, Account(factory), trim_columns=["segredo"])
    attacker = Account(factory)

    response = run_preset(
        client, attacker, preset["id"], ready_inputs(factory, storage, attacker.id)
    )

    assert_problem(response, 404, "NOT_FOUND")
    assert "segredo" not in response.text
    assert count_rows(factory)["run"] == 0


def test_exige_tambem_o_escopo_presets_read(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = Account(factory)
    preset = create_preset(client, owner)
    without_presets_read = new_key(factory, owner.id, scopes=ALL_SCOPES - {Scope.PRESETS_READ})

    response = client.post(
        f"{PRESETS}/{preset['id']}/runs",
        json={"input_upload_ids": [str(u) for u in ready_inputs(factory, storage, owner.id)]},
        headers={
            "Authorization": f"Bearer {without_presets_read.token}",
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )

    assert_problem(response, 403, "INSUFFICIENT_SCOPE")
    assert count_rows(factory)["run"] == 0


@pytest.mark.parametrize(
    "body",
    [
        {"input_upload_ids": [], "parameters": {}},
        {"input_upload_ids": [], "recipe_id": "image-forge"},
        {"input_upload_ids": [], "preset_id": str(uuid.uuid4())},
    ],
    ids=["parameters", "recipe_id", "preset_id"],
)
def test_campo_alem_das_entradas_responde_422(
    client: TestClient, factory: sessionmaker[Session], body: dict[str, Any]
) -> None:
    account = Account(factory)
    preset = create_preset(client, account)

    response = client.post(
        f"{PRESETS}/{preset['id']}/runs",
        json=body,
        headers={**account.headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    assert_problem(response, 422, "VALIDATION_FAILED")
    assert count_rows(factory)["run"] == 0


def test_sem_entradas_responde_422(client: TestClient, factory: sessionmaker[Session]) -> None:
    account = Account(factory)
    preset = create_preset(client, account)

    response = run_preset(client, account, preset["id"], [])

    body = assert_problem(response, 422, "VALIDATION_FAILED")
    assert [e["field"] for e in body["errors"]] == ["body.input_upload_ids"]


def test_sem_idempotency_key_responde_400(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    account = Account(factory)
    preset = create_preset(client, account)

    response = client.post(
        f"{PRESETS}/{preset['id']}/runs",
        json={"input_upload_ids": [str(u) for u in ready_inputs(factory, storage, account.id)]},
        headers=account.headers,
    )

    assert_problem(response, 400, "IDEMPOTENCY_KEY_INVALID")


def test_excluir_o_preset_mantem_a_execucao_sem_o_vinculo(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    account = Account(factory)
    preset = create_preset(client, account, trim_columns=["a"])
    accepted = run_preset(client, account, preset["id"], ready_inputs(factory, storage, account.id))

    deleted = client.delete(
        f"{PRESETS}/{preset['id']}", headers={**account.headers, "If-Match": '"1"'}
    )

    assert deleted.status_code == 204
    saved = stored_run(factory, accepted.json()["id"])
    assert saved.preset_id is None
    assert saved.parameters["trim_columns"] == ["a"]


def test_editar_o_preset_nao_muda_a_execucao_ja_aceita(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    account = Account(factory)
    preset = create_preset(client, account, trim_columns=["a"])
    accepted = run_preset(client, account, preset["id"], ready_inputs(factory, storage, account.id))

    edited = client.put(
        f"{PRESETS}/{preset['id']}",
        json={
            "name": "Outro",
            "recipe_id": "csv-janitor",
            "recipe_version": 1,
            "parameters": {"trim_columns": ["b"]},
        },
        headers={**account.headers, "If-Match": '"1"'},
    )

    assert edited.status_code == 200
    saved = stored_run(factory, accepted.json()["id"])
    assert saved.parameters["trim_columns"] == ["a"]
    assert saved.preset_id == uuid.UUID(preset["id"])


def test_retry_depois_de_editar_o_preset_repete_a_mesma_execucao(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    account = Account(factory)
    preset = create_preset(client, account, trim_columns=["a"])
    inputs = ready_inputs(factory, storage, account.id)
    key = str(uuid.uuid4())
    first = run_preset(client, account, preset["id"], inputs, key)
    edited = client.put(
        f"{PRESETS}/{preset['id']}",
        json={
            "name": "Editado",
            "recipe_id": "csv-janitor",
            "recipe_version": 1,
            "parameters": {"trim_columns": ["b"]},
        },
        headers={**account.headers, "If-Match": '"1"'},
    )
    assert edited.status_code == 200, edited.text

    again = run_preset(client, account, preset["id"], inputs, key)

    assert again.status_code == 202, again.text
    assert again.headers["idempotency-replayed"] == "true"
    assert again.json()["id"] == first.json()["id"]


def test_retry_depois_de_excluir_o_preset_repete_a_mesma_execucao(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    account = Account(factory)
    preset = create_preset(client, account)
    inputs = ready_inputs(factory, storage, account.id)
    key = str(uuid.uuid4())
    first = run_preset(client, account, preset["id"], inputs, key)
    deleted = client.delete(
        f"{PRESETS}/{preset['id']}", headers={**account.headers, "If-Match": '"1"'}
    )
    assert deleted.status_code == 204, deleted.text

    again = run_preset(client, account, preset["id"], inputs, key)

    assert again.status_code == 202, again.text
    assert again.json()["id"] == first.json()["id"]


def test_mesma_chave_com_outras_entradas_no_mesmo_preset_e_outro_comando(
    client: TestClient, factory: sessionmaker[Session], storage: FileStorage
) -> None:
    account = Account(factory)
    preset = create_preset(client, account)
    key = str(uuid.uuid4())
    run_preset(client, account, preset["id"], ready_inputs(factory, storage, account.id), key)

    other = run_preset(
        client, account, preset["id"], ready_inputs(factory, storage, account.id), key
    )

    assert_problem(other, 422, "IDEMPOTENCY_KEY_REUSED")
