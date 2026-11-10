"""Isolamento entre contas em toda rota com recurso (docs/GoblinShiftPlan.md, 1.6 e 3.6).

A conta B pede o recurso da conta A e recebe o mesmo 404 do recurso inexistente; nas mutações, o
recurso de A fica intacto. A conta A, com o mesmo pedido, não recebe 404: o recurso existe e a
recusa vem do dono, não de um pedido inválido.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.api.app import create_app
from goblinshift.platform.settings import Settings
from tests.api.files_support import without_request_identity
from tests.api.session_support import open_session, session_headers
from tests.api.support import assert_problem
from tests.identity.support import new_user, prepare_database
from tests.security.authz_matrix import MATRIX, NAMED_PATH_VALUES, Rule, entries, path_params
from tests.security.owned_resources import OWNED_RESOURCES, ROUTE_REQUESTS, OwnedResource

_ACCOUNT_NAME = "Conta de teste"


# --- Fitness (sem banco) ----------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), sorted(MATRIX))
def test_todo_parametro_de_id_e_o_recurso_declarado_da_rota(method: str, path: str) -> None:
    ids = [name for name in path_params(path) if name not in NAMED_PATH_VALUES]
    resource = MATRIX[(method, path)].resource_param

    assert ids == ([resource] if resource else []), "id no caminho sem resource_param na matriz"


@pytest.mark.parametrize(
    ("method", "path"), [(m, p) for m, p, rule in entries() if rule.resource_param]
)
def test_toda_rota_com_recurso_tem_fabrica(method: str, path: str) -> None:
    resource = MATRIX[(method, path)].resource_param

    assert resource in OWNED_RESOURCES, f"sem fábrica para {resource} em owned_resources.py"


def test_pedidos_especiais_sao_de_rotas_com_recurso() -> None:
    with_resource = {(m, p) for m, p, rule in entries() if rule.resource_param}

    assert set(ROUTE_REQUESTS) <= with_resource


# --- Comportamento (PostgreSQL real) ----------------------------------------------------------


@pytest.fixture
def app(db_settings: Settings, tmp_path: Path) -> FastAPI:
    app = create_app(db_settings.model_copy(update={"storage_root": tmp_path / "storage"}))
    prepare_database(db_settings, app.state.engine)
    return app


@pytest.fixture
def factory(app: FastAPI) -> sessionmaker[Session]:
    factory: sessionmaker[Session] = app.state.session_factory
    return factory


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


def _resource_cases() -> list[object]:
    return [
        pytest.param(method, path, rule, id=f"{method} {path}")
        for method, path, rule in entries()
        if rule.resource_param
    ]


def _auth(app: FastAPI, user_id: UUID) -> dict[str, str]:
    # Sessão web: tem todos os escopos e passa em toda política com recurso.
    return session_headers(app, open_session(app, user_id))


def _request(
    client: TestClient, method: str, path: str, resource_id: UUID, auth: dict[str, str]
) -> httpx.Response:
    extra = ROUTE_REQUESTS.get((method, path), {})
    url = path
    for name in path_params(path):
        url = url.replace(f"{{{name}}}", NAMED_PATH_VALUES.get(name, str(resource_id)))
    headers = {**auth, **extra.get("headers", {})}
    kwargs = {key: value for key, value in extra.items() if key != "headers"}
    return client.request(method, url, headers=headers, follow_redirects=False, **kwargs)


def _snapshot(factory: sessionmaker[Session], owned: OwnedResource, resource_id: UUID) -> Any:
    with factory() as session:
        row = session.execute(
            select(owned.table).where(owned.table.c.id == resource_id)
        ).one_or_none()
    assert row is not None, "a fábrica não criou a linha do recurso"
    return dict(row._mapping)


@pytest.mark.integration
@pytest.mark.parametrize(("method", "path", "rule"), _resource_cases())
def test_outra_conta_recebe_o_404_do_inexistente_e_o_recurso_fica_intacto(
    app: FastAPI,
    client: TestClient,
    factory: sessionmaker[Session],
    method: str,
    path: str,
    rule: Rule,
) -> None:
    assert rule.resource_param is not None
    owned = OWNED_RESOURCES[rule.resource_param]
    owner, intruder = new_user(factory), new_user(factory)
    resource_id = owned.create(app, owner)
    before = _snapshot(factory, owned, resource_id)
    intruder_auth = _auth(app, intruder)

    foreign = _request(client, method, path, resource_id, intruder_auth)
    missing = _request(client, method, path, uuid4(), intruder_auth)

    foreign_body = assert_problem(foreign, 404, "NOT_FOUND")
    assert without_request_identity(foreign_body) == without_request_identity(
        assert_problem(missing, 404, "NOT_FOUND")
    )
    assert str(resource_id) not in foreign.text.replace(foreign_body["instance"], "")
    assert _ACCOUNT_NAME not in foreign.text
    assert _snapshot(factory, owned, resource_id) == before
    # Controle: o mesmo pedido do dono acha o recurso; sem isso o 404 acima provaria pouco.
    assert _request(client, method, path, resource_id, _auth(app, owner)).status_code != 404
