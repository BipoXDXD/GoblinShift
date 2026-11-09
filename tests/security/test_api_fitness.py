"""Portões de segurança da API que não precisam de banco (docs/GoblinShiftPlan.md, 1.6 e 3.6).

A aplicação sobe sem PostgreSQL: qualquer acesso ao banco nestes caminhos viraria 500, então os
testes provam também que a recusa acontece antes de tocar dado.
"""

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4, uuid7

import pytest
from fastapi.testclient import TestClient

from goblinshift.entrypoints.api.app import create_app
from goblinshift.entrypoints.api.cookies import CSRF_HEADER, SESSION_COOKIE
from goblinshift.entrypoints.openapi_export import render_openapi
from goblinshift.platform.settings import Settings
from tests.api.simulated_session import SimulatedSessions
from tests.api.support import assert_problem
from tests.security.authz_matrix import Policy, entries

SPEC: dict[str, Any] = json.loads(render_openapi())
# Com o sufixo, fica fora do tamanho de um token de sessão (43) e de uma chave (64): sem consulta.
CANARY = f"CANARY-{uuid4()}-x"
_PATH_PARAM = re.compile(r"\{[a-z_]+\}")
_JSON = {"Content-Type": "application/json"}
# Campos que só o servidor conhece: nenhum modelo publicado pode trazê-los (rotas `runs`, 1.6).
_INTERNAL_FIELDS = frozenset(
    {
        "owner_id",
        "user_id",
        "secret_hash",
        "storage_key",
        "traceparent",
        "generation",
        "executor_image",
    }
)
_CREDENTIALED_POLICIES = (
    Policy.ANY_CREDENTIAL,
    Policy.SCOPE,
    Policy.SESSION,
    Policy.RECENT_SESSION,
)
_CANARY_CREDENTIALS = {
    "chave": {"Authorization": f"Bearer {CANARY}"},
    "sessao": {"Cookie": f"{SESSION_COOKIE}={CANARY}"},
}


def _server_log(caplog: pytest.LogCaptureFixture) -> str:
    """Sem o log do `httpx2`: o TestClient registra a URL pedida, com a query."""
    return "\n".join(
        record.getMessage() for record in caplog.records if not record.name.startswith("httpx")
    )


def _offline_app_settings() -> Settings:
    return Settings(environment="test", db_port=1, db_connect_timeout_s=1)


def _concrete(path: str) -> str:
    return _PATH_PARAM.sub(lambda _match: str(uuid4()), path)


def _json_commands() -> list[tuple[str, str]]:
    return [
        (method.upper(), path)
        for path, operations in SPEC["paths"].items()
        for method, operation in operations.items()
        if "application/json" in operation.get("requestBody", {}).get("content", {})
    ]


def _credentialed_routes() -> list[tuple[str, str]]:
    return [(method, path) for method, path, _ in entries(*_CREDENTIALED_POLICIES)]


def _property_names(node: object) -> Iterator[str]:
    if isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict):
            yield from properties
        for value in node.values():
            yield from _property_names(value)
    elif isinstance(node, list):
        for item in node:
            yield from _property_names(item)


@pytest.fixture(scope="module")
def anonymous_client() -> Iterator[TestClient]:
    with TestClient(create_app(_offline_app_settings())) as client:
        yield client


@pytest.fixture(scope="module")
def signed_in_client() -> Iterator[TestClient]:
    app = create_app(_offline_app_settings())
    SimulatedSessions(app, datetime.now(UTC)).sign_in(uuid7())
    with TestClient(app) as client:
        yield client


def test_o_contrato_publica_comandos_json() -> None:
    assert _json_commands() != []


@pytest.mark.parametrize("extra", ["owner_id", "status", "role"])
@pytest.mark.parametrize(("method", "path"), _json_commands())
def test_comando_json_recusa_campo_que_o_cliente_nao_define(
    signed_in_client: TestClient, method: str, path: str, extra: str
) -> None:
    response = signed_in_client.request(
        method, _concrete(path), content=json.dumps({extra: "x"}), headers=_JSON
    )

    problem = assert_problem(response, 422, "VALIDATION_FAILED")
    errors = [(error["field"], error["code"]) for error in problem["errors"]]
    assert (f"body.{extra}", "extra_forbidden") in errors


def test_nenhum_modelo_publicado_traz_campo_interno() -> None:
    published = set(_property_names(SPEC["components"]["schemas"]))

    assert sorted(published & _INTERNAL_FIELDS) == []


@pytest.mark.parametrize("credential", sorted(_CANARY_CREDENTIALS))
@pytest.mark.parametrize(("method", "path"), _credentialed_routes())
def test_credencial_recusada_nao_aparece_na_resposta_nem_no_log(
    anonymous_client: TestClient,
    caplog: pytest.LogCaptureFixture,
    method: str,
    path: str,
    credential: str,
) -> None:
    headers = {
        **_CANARY_CREDENTIALS[credential],
        CSRF_HEADER: CANARY,
        "User-Agent": CANARY,
    }

    with caplog.at_level("DEBUG"):
        response = anonymous_client.request(
            method, f"{_concrete(path)}?cursor={CANARY}", headers=headers
        )

    assert_problem(response, 401, "AUTHENTICATION_REQUIRED")
    assert CANARY not in response.text
    assert CANARY not in str(response.headers)
    assert CANARY not in _server_log(caplog)
