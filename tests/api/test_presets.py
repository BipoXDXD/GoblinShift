"""Rotas de presets: CRUD com revisão, If-Match e dono na query (1.6, 1.7 e 3.6)."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.catalog import CATALOG, Catalog, VersionStatus
from goblinshift.entrypoints.api import presets as presets_route
from goblinshift.identity import Scope
from goblinshift.platform.db import unit_of_work
from goblinshift.presets import MAX_PRESETS_PER_ACCOUNT, create_preset, parse_draft
from goblinshift.presets import drafts as drafts_module
from goblinshift.presets.tables import preset
from tests.api.support import assert_problem
from tests.identity.support import ALL_SCOPES, new_key, new_user

pytestmark = pytest.mark.integration

PRESETS = "/api/v1/presets"
PRESET_KEYS = {
    "id",
    "name",
    "recipe_id",
    "recipe_version",
    "parameters",
    "revision",
    "created_at",
    "updated_at",
}
SUNSET = datetime(2027, 1, 15, 12, 30, tzinfo=UTC)


class Account:
    def __init__(self, factory: sessionmaker[Session], scopes: frozenset[Scope] = ALL_SCOPES):
        self.id = new_user(factory)
        self.headers = {"Authorization": f"Bearer {new_key(factory, self.id, scopes=scopes).token}"}


def preset_body(name: str = "Mensal", **parameters: Any) -> dict[str, Any]:
    return {
        "name": name,
        "recipe_id": "csv-janitor",
        "recipe_version": 1,
        "parameters": parameters,
    }


def create(client: TestClient, account: Account, name: str = "Mensal") -> dict[str, Any]:
    response = client.post(PRESETS, json=preset_body(name), headers=account.headers)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def put(
    client: TestClient,
    account: Account,
    preset_id: str,
    body: dict[str, Any],
    if_match: str | None,
) -> httpx.Response:
    headers = dict(account.headers)
    if if_match is not None:
        headers["If-Match"] = if_match
    return client.put(f"{PRESETS}/{preset_id}", json=body, headers=headers)


def delete(
    client: TestClient, account: Account, preset_id: str, if_match: str | None
) -> httpx.Response:
    headers = dict(account.headers)
    if if_match is not None:
        headers["If-Match"] = if_match
    return client.delete(f"{PRESETS}/{preset_id}", headers=headers)


def stored(factory: sessionmaker[Session], preset_id: str) -> dict[str, Any] | None:
    with factory() as session:
        row = session.execute(
            select(preset.c.name, preset.c.revision, preset.c.parameters).where(
                preset.c.id == uuid.UUID(preset_id)
            )
        ).one_or_none()
    return None if row is None else row._asdict()


def count_presets(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        total: int = session.execute(select(func.count()).select_from(preset)).scalar_one()
    return total


def deprecate_csv_janitor(monkeypatch: pytest.MonkeyPatch) -> None:
    deprecated = Catalog(
        [
            replace(
                CATALOG.get_version("csv-janitor", 1),
                status=VersionStatus.DEPRECATED,
                sunset_at=SUNSET,
            )
        ]
    )
    monkeypatch.setattr(drafts_module, "CATALOG", deprecated)
    monkeypatch.setattr(presets_route, "CATALOG", deprecated)


# POST /presets


def test_criar_responde_201_com_location_etag_e_o_corpo_do_contrato(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    account = Account(factory)

    response = client.post(
        PRESETS, json=preset_body("Mensal", trim_columns=["a"]), headers=account.headers
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body.keys() == PRESET_KEYS
    assert response.headers["location"] == f"{PRESETS}/{body['id']}"
    assert response.headers["etag"] == '"1"'
    assert body["name"] == "Mensal"
    assert body["parameters"] == {"trim_columns": ["a"]}
    assert body["revision"] == 1
    assert "sunset" not in response.headers


@pytest.mark.parametrize("field", ["id", "owner_id", "revision", "created_at"])
def test_campo_do_servidor_no_corpo_responde_422_sem_criar(
    client: TestClient, factory: sessionmaker[Session], field: str
) -> None:
    account = Account(factory)
    body = {**preset_body(), field: str(uuid.uuid4())}

    response = client.post(PRESETS, json=body, headers=account.headers)

    assert_problem(response, 422, "VALIDATION_FAILED")
    assert count_presets(factory) == 0


def test_parametro_invalido_responde_422_no_campo(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    account = Account(factory)

    response = client.post(
        PRESETS, json=preset_body(formula_guard="talvez"), headers=account.headers
    )

    body = assert_problem(response, 422, "VALIDATION_FAILED")
    assert [error["field"] for error in body["errors"]] == ["body.parameters.formula_guard"]
    assert count_presets(factory) == 0


@pytest.mark.parametrize("name", ["", "a" * 101, "‮evil"], ids=["vazio", "101", "bidi"])
def test_nome_invalido_responde_422_no_campo_name(
    client: TestClient, factory: sessionmaker[Session], name: str
) -> None:
    response = client.post(PRESETS, json=preset_body(name), headers=Account(factory).headers)

    body = assert_problem(response, 422, "VALIDATION_FAILED")
    assert [error["field"] for error in body["errors"]] == ["body.name"]


def test_nome_repetido_responde_409(client: TestClient, factory: sessionmaker[Session]) -> None:
    account = Account(factory)
    create(client, account, "Mensal")

    response = client.post(PRESETS, json=preset_body("Mensal"), headers=account.headers)

    assert_problem(response, 409, "STATE_CONFLICT")
    assert count_presets(factory) == 1


def test_centesimo_primeiro_preset_responde_409(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    account = Account(factory)
    # Pelo serviço: 100 POST passariam do limite de 60 comandos por minuto (1.7).
    with unit_of_work(factory) as session:
        for index in range(MAX_PRESETS_PER_ACCOUNT):
            create_preset(session, account.id, parse_draft(f"p{index}", "csv-janitor", 1, {}))

    response = client.post(PRESETS, json=preset_body("excedente"), headers=account.headers)

    body = assert_problem(response, 409, "STATE_CONFLICT")
    assert "100" in body["detail"]
    assert count_presets(factory) == MAX_PRESETS_PER_ACCOUNT


def test_versao_retirada_responde_422_recipe_version_retired(
    client: TestClient, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    retired = replace(CATALOG.get_version("csv-janitor", 1), status=VersionStatus.RETIRED)
    monkeypatch.setattr(drafts_module, "CATALOG", Catalog([retired]))

    response = client.post(PRESETS, json=preset_body(), headers=Account(factory).headers)

    assert_problem(response, 422, "RECIPE_VERSION_RETIRED")
    assert count_presets(factory) == 0


def test_versao_deprecated_e_aceita_com_sunset(
    client: TestClient, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    deprecate_csv_janitor(monkeypatch)
    account = Account(factory)

    created = client.post(PRESETS, json=preset_body(), headers=account.headers)
    read = client.get(created.headers["location"], headers=account.headers)

    assert created.status_code == 201
    assert created.headers["sunset"] == "Fri, 15 Jan 2027 12:30:00 GMT"
    assert read.headers["sunset"] == "Fri, 15 Jan 2027 12:30:00 GMT"


# GET /presets e /presets/{id}


def test_listagem_traz_so_os_presets_da_conta(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    account = Account(factory)
    create(client, account, "b")
    create(client, account, "a")
    create(client, Account(factory), "alheio")

    response = client.get(PRESETS, headers=account.headers)

    assert response.status_code == 200
    body = response.json()
    assert body.keys() == {"data"}
    assert [item["name"] for item in body["data"]] == ["a", "b"]
    assert all(item.keys() == PRESET_KEYS for item in body["data"])


def test_ler_traz_o_etag_da_revisao(client: TestClient, factory: sessionmaker[Session]) -> None:
    account = Account(factory)
    created = create(client, account)

    response = client.get(f"{PRESETS}/{created['id']}", headers=account.headers)

    assert response.status_code == 200
    assert response.json() == created
    assert response.headers["etag"] == '"1"'


def test_ler_preset_de_outra_conta_responde_404_sem_dados(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    created = create(client, Account(factory), "Segredo da vítima")

    response = client.get(f"{PRESETS}/{created['id']}", headers=Account(factory).headers)

    assert_problem(response, 404, "NOT_FOUND")
    assert "Segredo da vítima" not in response.text


# PUT /presets/{id}


def test_substituir_com_if_match_responde_200_com_a_revisao_nova(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    account = Account(factory)
    created = create(client, account)

    response = put(client, account, created["id"], preset_body("Novo", trim_columns=["b"]), '"1"')

    assert response.status_code == 200, response.text
    body = response.json()
    assert body.keys() == PRESET_KEYS
    assert (body["name"], body["revision"]) == ("Novo", 2)
    assert body["parameters"]["trim_columns"] == ["b"]
    assert response.headers["etag"] == '"2"'


@pytest.mark.parametrize("if_match", [None, "", "*"], ids=["ausente", "vazio", "asterisco"])
def test_substituir_sem_if_match_responde_428_sem_mudar(
    client: TestClient, factory: sessionmaker[Session], if_match: str | None
) -> None:
    account = Account(factory)
    created = create(client, account)

    response = put(client, account, created["id"], preset_body("Novo"), if_match)

    assert_problem(response, 428, "PRECONDITION_REQUIRED")
    assert stored(factory, created["id"]) == {
        "name": "Mensal",
        "revision": 1,
        "parameters": {},
    }


def test_substituir_com_revisao_vencida_responde_412_sem_mudar(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    account = Account(factory)
    created = create(client, account)

    response = put(client, account, created["id"], preset_body("Novo"), '"2"')

    assert_problem(response, 412, "PRECONDITION_FAILED")
    assert stored(factory, created["id"])["revision"] == 1  # type: ignore[index]


@pytest.mark.parametrize(
    "if_match", ['W/"1"', '"x"', "1", '"1", "2"'], ids=["fraca", "lixo", "sem-aspas", "lista"]
)
def test_substituir_com_if_match_fora_do_formato_responde_400_sem_mudar(
    client: TestClient, factory: sessionmaker[Session], if_match: str
) -> None:
    account = Account(factory)
    created = create(client, account)

    response = put(client, account, created["id"], preset_body("Novo"), if_match)

    assert_problem(response, 400, "BAD_REQUEST")
    assert stored(factory, created["id"])["revision"] == 1  # type: ignore[index]


def test_put_vencido_com_nome_em_conflito_responde_412(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    # A revisão é conferida antes do nome: quem leu uma versão velha precisa reler primeiro.
    account = Account(factory)
    create(client, account, "Ocupado")
    created = create(client, account, "Livre")
    put(client, account, created["id"], preset_body("Renomeado"), '"1"')

    response = put(client, account, created["id"], preset_body("Ocupado"), '"1"')

    assert_problem(response, 412, "PRECONDITION_FAILED")


def test_dois_put_com_o_mesmo_etag_o_segundo_recebe_412(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    account = Account(factory)
    created = create(client, account)

    first = put(client, account, created["id"], preset_body("Primeiro"), '"1"')
    second = put(client, account, created["id"], preset_body("Segundo"), '"1"')

    assert first.status_code == 200
    assert_problem(second, 412, "PRECONDITION_FAILED")
    assert stored(factory, created["id"])["name"] == "Primeiro"  # type: ignore[index]


def test_substituir_preset_de_outra_conta_responde_404_e_nao_muda(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    created = create(client, Account(factory), "Da vítima")

    response = put(client, Account(factory), created["id"], preset_body("Invadido"), '"1"')

    assert_problem(response, 404, "NOT_FOUND")
    assert "Da vítima" not in response.text
    assert stored(factory, created["id"])["name"] == "Da vítima"  # type: ignore[index]


def test_substituir_com_campo_desconhecido_responde_422_sem_mudar(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    account = Account(factory)
    created = create(client, account)

    response = put(client, account, created["id"], {**preset_body("Novo"), "revision": 9}, '"1"')

    assert_problem(response, 422, "VALIDATION_FAILED")
    assert stored(factory, created["id"])["revision"] == 1  # type: ignore[index]


# DELETE /presets/{id}


def test_excluir_com_if_match_responde_204_e_repetir_responde_404(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    account = Account(factory)
    created = create(client, account)

    first = delete(client, account, created["id"], '"1"')
    second = delete(client, account, created["id"], '"1"')

    assert first.status_code == 204
    assert first.content == b""
    assert_problem(second, 404, "NOT_FOUND")
    assert stored(factory, created["id"]) is None


def test_excluir_sem_if_match_responde_428_e_mantem(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    account = Account(factory)
    created = create(client, account)

    response = delete(client, account, created["id"], None)

    assert_problem(response, 428, "PRECONDITION_REQUIRED")
    assert stored(factory, created["id"]) is not None


def test_excluir_com_revisao_vencida_responde_412_e_mantem(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    account = Account(factory)
    created = create(client, account)
    put(client, account, created["id"], preset_body("Novo"), '"1"')

    response = delete(client, account, created["id"], '"1"')

    assert_problem(response, 412, "PRECONDITION_FAILED")
    assert stored(factory, created["id"]) is not None


def test_excluir_preset_de_outra_conta_responde_404_e_mantem(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    created = create(client, Account(factory))

    response = delete(client, Account(factory), created["id"], '"1"')

    assert_problem(response, 404, "NOT_FOUND")
    assert stored(factory, created["id"]) is not None


# Escopos


@pytest.mark.parametrize(
    ("method", "scope"),
    [("GET", Scope.PRESETS_READ), ("POST", Scope.PRESETS_WRITE)],
)
def test_cada_operacao_exige_o_proprio_escopo(
    client: TestClient, factory: sessionmaker[Session], method: str, scope: Scope
) -> None:
    account = Account(factory, scopes=ALL_SCOPES - {scope})

    response = client.request(method, PRESETS, json=preset_body(), headers=account.headers)

    assert_problem(response, 403, "INSUFFICIENT_SCOPE")
    assert count_presets(factory) == 0


def test_openapi_publica_o_corpo_como_variantes_do_catalogo(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    schema = spec["paths"]["/api/v1/presets"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ]

    assert schema["unevaluatedProperties"] is False
    common, variants = schema["allOf"]
    assert common["required"] == ["name"]
    assert {v["properties"]["recipe_id"]["const"] for v in variants["oneOf"]} == {
        version.recipe_id for version in CATALOG.list_versions()
    }
    put_spec = spec["paths"]["/api/v1/presets/{preset_id}"]["put"]
    assert put_spec["requestBody"]["content"]["application/json"]["schema"] == schema
    if_match = [p for p in put_spec["parameters"] if p["name"] == "If-Match"]
    assert if_match[0]["required"] is True
