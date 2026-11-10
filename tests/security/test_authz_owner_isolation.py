"""Isolamento entre contas em toda rota com recurso (docs/GoblinShiftPlan.md, 1.6 e 3.6).

A conta B pede o recurso da conta A e recebe o mesmo 404 do recurso inexistente; nas mutações, o
recurso de A fica intacto. A conta A, com o mesmo pedido, é atendida (nem 404 nem recusa de
acesso): o recurso existe e a recusa vem do dono, não de um pedido inválido.
"""

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.api.app import create_app
from goblinshift.platform.settings import Settings
from tests.api.files_support import bearer, without_request_identity
from tests.api.session_support import open_session, session_headers
from tests.api.support import assert_problem
from tests.identity.support import new_key, new_user, prepare_database
from tests.security.authz_matrix import (
    MATRIX,
    NAMED_PATH_VALUES,
    Policy,
    entries,
    fill_path,
    id_params,
    path_params,
)
from tests.security.owned_resources import (
    OWNED_RESOURCES,
    ROUTE_REQUESTS,
    ROUTE_RESOURCES,
    OwnedResource,
    owned_resource,
)

_ACCOUNT_NAME = "Conta de teste"
_ACCESS_REFUSED = {401, 403, 404}
_SERVER_ERROR = 500
# Muda a cada requisição; o resto dos headers do 404 alheio e do inexistente é igual.
_PER_REQUEST_HEADERS = {"x-request-id"}


def _with_resource() -> set[tuple[str, str]]:
    return {(m, p) for m, p, rule in entries() if rule.resource_param}


# --- Fitness (sem banco) ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/runs/{run_id:uuid}", ["run_id"]),
        ("/files/{file_path:path}", ["file_path"]),
        ("/runs/{runId}", ["runId"]),
        ("/uploads/{upload_id2}", ["upload_id2"]),
    ],
)
def test_parametro_de_caminho_casa_com_conversor_maiuscula_e_digito(
    path: str, expected: list[str]
) -> None:
    assert path_params(path) == expected


@pytest.mark.parametrize("path", ["/x/{}", "/x/{a", "/x/{a}/{b:}{"])
def test_parametro_fora_do_formato_falha_fechado(path: str) -> None:
    with pytest.raises(ValueError, match="fora do formato"):
        path_params(path)


@pytest.mark.parametrize(("method", "path"), sorted(MATRIX))
def test_todo_parametro_de_id_e_o_recurso_declarado_da_rota(method: str, path: str) -> None:
    resource = MATRIX[(method, path)].resource_param

    assert id_params(path) == ([resource] if resource else []), "id sem resource_param na matriz"


@pytest.mark.parametrize(("method", "path"), sorted(_with_resource()))
def test_toda_rota_com_recurso_tem_fabrica(method: str, path: str) -> None:
    resource = MATRIX[(method, path)].resource_param

    assert resource in OWNED_RESOURCES, f"sem fábrica para {resource} em owned_resources.py"


def test_excecoes_por_rota_sao_de_rotas_com_recurso() -> None:
    assert set(ROUTE_REQUESTS) <= _with_resource()
    assert set(ROUTE_RESOURCES) <= _with_resource()


def test_valores_nomeados_sao_de_parametros_de_rotas_da_matriz() -> None:
    paths = {path for _, path in MATRIX}
    for path, values in NAMED_PATH_VALUES.items():
        assert path in paths
        assert set(values) <= set(path_params(path))


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
    """Toda rota com recurso pela sessão; as de escopo também por chave de API."""
    cases: list[object] = []
    for method, path, rule in entries():
        if rule.resource_param is None:
            continue
        credentials = ["sessao", "chave"] if rule.policy is Policy.SCOPE else ["sessao"]
        cases += [
            pytest.param(method, path, rule.resource_param, kind, id=f"{method} {path} {kind}")
            for kind in credentials
        ]
    return cases


def _auth(app: FastAPI, user_id: UUID, kind: str) -> dict[str, str]:
    factory: sessionmaker[Session] = app.state.session_factory
    if kind == "chave":
        return bearer(new_key(factory, user_id).token)
    return session_headers(app, open_session(app, user_id))


def _request(
    client: TestClient, method: str, path: str, resource_id: UUID, auth: dict[str, str]
) -> httpx.Response:
    build = ROUTE_REQUESTS.get((method, path))
    extra = build() if build else {}
    headers = {**auth, **extra.pop("headers", {})}
    url = fill_path(path, str(resource_id))
    return client.request(method, url, headers=headers, follow_redirects=False, **extra)


def _snapshot(factory: sessionmaker[Session], owned: OwnedResource, resource_id: UUID) -> object:
    with factory() as session:
        return owned.snapshot(session, resource_id)


def _headers_without_request_identity(response: httpx.Response) -> dict[str, str]:
    return {k: v for k, v in response.headers.items() if k not in _PER_REQUEST_HEADERS}


@pytest.mark.integration
@pytest.mark.parametrize(("method", "path", "resource_param", "kind"), _resource_cases())
def test_outra_conta_recebe_o_404_do_inexistente_e_o_recurso_fica_intacto(
    app: FastAPI,
    client: TestClient,
    factory: sessionmaker[Session],
    method: str,
    path: str,
    resource_param: str,
    kind: str,
) -> None:
    owned = owned_resource(method, path, resource_param)
    owner, intruder = new_user(factory), new_user(factory)
    resource_id = owned.create(app, owner)
    before = _snapshot(factory, owned, resource_id)
    assert before is not None, "a fábrica não criou o recurso"
    intruder_auth = _auth(app, intruder, kind)

    foreign = _request(client, method, path, resource_id, intruder_auth)
    missing = _request(client, method, path, uuid4(), intruder_auth)

    foreign_body = assert_problem(foreign, 404, "NOT_FOUND")
    assert without_request_identity(foreign_body) == without_request_identity(
        assert_problem(missing, 404, "NOT_FOUND")
    )
    assert _headers_without_request_identity(foreign) == _headers_without_request_identity(missing)
    assert str(resource_id) not in foreign.text.replace(foreign_body["instance"], "")
    assert _ACCOUNT_NAME not in foreign.text
    assert _snapshot(factory, owned, resource_id) == before
    # Controle: o mesmo pedido do dono é atendido; sem isso o 404 acima provaria pouco.
    control = _request(client, method, path, resource_id, _auth(app, owner, kind))
    assert control.status_code < _SERVER_ERROR, control.text
    assert control.status_code not in _ACCESS_REFUSED, control.text
