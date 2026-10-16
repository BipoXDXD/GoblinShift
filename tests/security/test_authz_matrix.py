"""Matriz de autorização aplicada às rotas registradas (docs/GoblinShiftPlan.md, 1.6 e 3.6).

Cada teste de acesso confere o status e o código do problem+json, e que o corpo não traz dado
da conta.
"""

import re
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.api.app import create_app
from goblinshift.entrypoints.api.cookies import CSRF_COOKIE, CSRF_HEADER
from goblinshift.identity import Scope, issue_csrf_token
from goblinshift.identity.tables import audit_event
from goblinshift.platform.settings import Settings
from tests.api.support import assert_problem
from tests.identity.support import ALL_SCOPES, new_key, new_user, prepare_database
from tests.security.authz_matrix import MATRIX, Policy, Rule, entries

# HEAD acompanha GET nas rotas do Starlette e não é uma operação à parte.
_IMPLICIT_METHODS = {"HEAD"}
_PATH_PARAM = re.compile(r"\{(?P<name>[a-z_]+)\}")
_ACCOUNT_NAME = "Conta de teste"


def _registered_routes(app: FastAPI) -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for context in iter_route_contexts(app.routes):
        # Rota sem caminho (montagem sem path) seria invisível à matriz: falha em vez de pular.
        assert context.path is not None, f"rota sem caminho: {context.name}"
        for method in (context.methods or set()) - _IMPLICIT_METHODS:
            routes.add((method, context.path))
    return routes


# Parâmetros que não são ids: um valor da allowlist da própria rota.
_NAMED_VALUES = {"provider": "google", "name": "swagger-init.js"}


def _concrete(path: str) -> str:
    """Troca cada parâmetro do caminho por um valor que passa na validação do tipo."""

    def value(match: re.Match[str]) -> str:
        return _NAMED_VALUES.get(match["name"]) or str(uuid4())

    return _PATH_PARAM.sub(value, path)


def _cases(*policies: Policy) -> list[object]:
    return [
        pytest.param(method, path, rule, id=f"{method} {path}")
        for method, path, rule in entries(*policies)
    ]


# --- Enumeração (sem banco) -------------------------------------------------------------------


@pytest.fixture
def offline_app() -> FastAPI:
    return create_app(Settings(environment="test", db_port=1, db_connect_timeout_s=1))


def test_toda_rota_registrada_esta_na_matriz(offline_app: FastAPI) -> None:
    registered = _registered_routes(offline_app)
    expected = set(MATRIX)

    assert registered - expected == set(), "rota registrada fora da matriz"
    assert expected - registered == set(), "entrada da matriz sem rota registrada"


def test_regra_de_escopo_exige_o_escopo() -> None:
    with pytest.raises(ValueError, match="SCOPE"):
        Rule(Policy.SCOPE)
    with pytest.raises(ValueError, match="SCOPE"):
        Rule(Policy.SESSION, scope=Scope.RUNS_READ)


def test_allowlist_publica_e_a_esperada() -> None:
    public = {(method, path) for method, path, _ in entries(Policy.PUBLIC)}

    assert public == {
        ("GET", "/livez"),
        ("GET", "/readyz"),
        ("GET", "/docs"),
        ("GET", "/docs/assets/{name}"),
        ("GET", "/openapi.json"),
        ("GET", "/auth/login/{provider}"),
        ("GET", "/auth/callback/{provider}"),
    }


def test_so_o_logout_dispensa_credencial_mas_exige_o_par_csrf() -> None:
    assert [(method, path) for method, path, _ in entries(Policy.CSRF_ONLY)] == [
        ("POST", "/auth/logout")
    ]


def test_toda_rota_com_recurso_declara_o_parametro_do_caminho() -> None:
    for (_, path), rule in MATRIX.items():
        if rule.resource_param is not None:
            assert f"{{{rule.resource_param}}}" in path


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


def _send(client: TestClient, method: str, path: str, token: str | None = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    # O redirect é a própria resposta (o login responde 302/303); segui-lo testaria outra rota.
    return client.request(method, _concrete(path), headers=headers, follow_redirects=False)


@pytest.mark.integration
@pytest.mark.parametrize(("method", "path", "rule"), _cases(Policy.PUBLIC))
def test_rota_publica_responde_sem_credencial(
    client: TestClient, method: str, path: str, rule: Rule
) -> None:
    response = _send(client, method, path)

    # 404 e 405 mostram que a rota não existe; 401 e 403, que ela pediu credencial.
    assert response.status_code not in {401, 403, 404, 405}, response.text


@pytest.mark.integration
@pytest.mark.parametrize(("method", "path", "rule"), _cases(Policy.CSRF_ONLY))
def test_rota_so_com_csrf_recusa_sem_o_par_e_aceita_com_ele(
    client: TestClient, method: str, path: str, rule: Rule
) -> None:
    token = issue_csrf_token(b"k" * 32, uuid4())
    with_pair = {"Cookie": f"{CSRF_COOKIE}={token}", CSRF_HEADER: token}

    assert_problem(_send(client, method, path), 403, "CSRF_FAILED")
    response = client.request(method, _concrete(path), headers=with_pair)
    assert response.status_code not in {401, 403, 404, 405}, response.text


@pytest.mark.integration
@pytest.mark.parametrize(
    ("method", "path", "rule"),
    _cases(Policy.ANY_CREDENTIAL, Policy.SCOPE, Policy.SESSION, Policy.RECENT_SESSION),
)
def test_rota_nao_publica_sem_credencial_401(
    client: TestClient, method: str, path: str, rule: Rule
) -> None:
    response = _send(client, method, path)

    assert_problem(response, 401, "AUTHENTICATION_REQUIRED")


@pytest.mark.integration
@pytest.mark.parametrize(("method", "path", "rule"), _cases(Policy.SCOPE))
def test_chave_sem_escopo_403_insufficient_scope(
    client: TestClient, factory: sessionmaker[Session], method: str, path: str, rule: Rule
) -> None:
    assert rule.scope is not None
    owner = new_user(factory)
    issued = new_key(factory, owner, scopes=ALL_SCOPES - {rule.scope})

    response = _send(client, method, path, issued.token)

    assert_problem(response, 403, "INSUFFICIENT_SCOPE")
    assert _ACCOUNT_NAME not in response.text
    assert _audit_events(factory, owner) == [("ACCESS_DENIED", "failure")]


@pytest.mark.integration
@pytest.mark.parametrize(("method", "path", "rule"), _cases(Policy.SESSION, Policy.RECENT_SESSION))
def test_chave_em_rota_de_sessao_403(
    client: TestClient, factory: sessionmaker[Session], method: str, path: str, rule: Rule
) -> None:
    issued = new_key(factory, new_user(factory), scopes=ALL_SCOPES)

    response = _send(client, method, path, issued.token)

    assert_problem(response, 403, "FORBIDDEN")
    assert _ACCOUNT_NAME not in response.text


def _audit_events(factory: sessionmaker[Session], owner_id: UUID) -> list[tuple[str, str]]:
    with factory() as session:
        rows = session.execute(
            select(audit_event.c.event_type, audit_event.c.outcome)
            .where(audit_event.c.user_id == owner_id)
            .order_by(audit_event.c.id)
        ).all()
    return [(row.event_type, row.outcome) for row in rows]
