"""Tetos dos campos do corpo no próprio contrato, não por acaso no catálogo (#442, 44e e 44h)."""

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

PRESETS = "/api/v1/presets"
RUNS = "/api/v1/runs"

type Auth = tuple[uuid.UUID, dict[str, str]]
type SendBody = Callable[[str, dict[str, Any]], httpx.Response]


@pytest.fixture
def auth(factory: sessionmaker[Session]) -> Auth:
    owner_id = new_owner(factory)
    return owner_id, {"Authorization": f"Bearer {new_key(factory, owner_id).token}"}


@pytest.fixture
def send_body(
    client: TestClient, factory: sessionmaker[Session], auth: Auth, tmp_path: Path
) -> SendBody:
    """Envia um corpo válido de `POST /runs` ou `POST /presets` com os campos trocados."""
    owner_id, headers = auth
    inputs = ready_inputs(factory, FileStorage(tmp_path / "storage"), owner_id)

    def send(route: str, overrides: dict[str, Any]) -> httpx.Response:
        command: dict[str, Any] = {"recipe_id": "csv-janitor", "recipe_version": 1}
        if route == "runs":
            command["input_upload_ids"] = [str(upload_id) for upload_id in inputs]
            key = {"Idempotency-Key": str(uuid.uuid4())}
            return client.post(
                RUNS, json={**command, "parameters": {}, **overrides}, headers=headers | key
            )
        body = {"name": "Mensal", **command, "parameters": {}, **overrides}
        return client.post(PRESETS, json=body, headers=headers)

    return send


def create_preset(client: TestClient, auth: Auth, *, name: str) -> httpx.Response:
    body = {"name": name, "recipe_id": "csv-janitor", "recipe_version": 1, "parameters": {}}
    return client.post(PRESETS, json=body, headers=auth[1])


# O código é a regra do próprio corpo, não a recusa do catálogo (`value_error`), que só pegava
# esses valores por acaso, ao não achar a versão.
@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("recipe_version", 2**63, "less_than_equal"),
        ("recipe_version", 1000, "less_than_equal"),
        ("recipe_id", "csv\x07janitor", "string_pattern_mismatch"),
    ],
    ids=["versao-2-63", "versao-1000", "id-com-bel"],
)
@pytest.mark.parametrize("route", ["runs", "presets"])
def test_recipe_fora_do_padrao_no_corpo_e_422_no_campo(
    send_body: SendBody, route: str, field: str, value: object, code: str
) -> None:
    response = send_body(route, {field: value})

    body = assert_problem(response, 422, "VALIDATION_FAILED")
    assert [(error["field"], error["code"]) for error in body["errors"]] == [
        (f"body.{field}", code)
    ]


# "a" + til combinante: 120 code points em NFD, 60 "ã" depois do NFC.
@pytest.mark.parametrize("name", ["a\u0303" * 60, "p" * 100 + " "], ids=["nfd-60", "100-e-espaco"])
def test_nome_de_preset_que_cabe_depois_de_normalizar_e_aceito(
    client: TestClient, auth: Auth, name: str
) -> None:
    response = create_preset(client, auth, name=name)

    assert response.status_code == 201, response.text


def test_nome_de_preset_com_101_depois_de_normalizar_e_422(client: TestClient, auth: Auth) -> None:
    response = create_preset(client, auth, name=" " + "p" * 101 + " ")

    body = assert_problem(response, 422, "VALIDATION_FAILED")
    assert [error["field"] for error in body["errors"]] == ["body.name"]
