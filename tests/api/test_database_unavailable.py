"""Banco ou pool indisponível responde 503 com `Retry-After` (docs/GoblinShiftPlan.md, 1.6 e 3.2).

Sem Docker: o `offline_client` aponta para uma porta sem banco, então a recusa de conexão é real,
e o pool esgotado sai de um `QueuePool` de verdade sobre SQLite.
"""

import logging
from collections.abc import Iterator
from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.pool import QueuePool

from tests.api.support import assert_problem

CANARY = "CANARY-valor-da-consulta"
SQL = "SELECT secret_hash FROM api_key WHERE token = %(token)s"
LOGGER = "goblinshift.entrypoints.api.problems"


def _operational_error() -> OperationalError:
    return OperationalError(SQL, {"token": CANARY}, Exception(f"senha incorreta: {CANARY}"))


def _session_that_cannot_connect() -> Iterator[None]:
    raise _operational_error()
    yield


@pytest.fixture
def exhausted_pool() -> Iterator[Engine]:
    engine = create_engine(
        "sqlite://",
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.01,
        connect_args={"check_same_thread": False},
    )
    yield engine
    engine.dispose()


@pytest.fixture
def client(offline_client: TestClient, exhausted_pool: Engine) -> TestClient:
    app: FastAPI = offline_client.app  # type: ignore[assignment]

    def connection_refused(request: Request) -> None:
        with request.app.state.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def pool_exhausted() -> None:
        with exhausted_pool.connect(), exhausted_pool.connect():
            pass

    def operational_error() -> None:
        raise _operational_error()

    def in_dependency(_session: Annotated[Any, Depends(_session_that_cannot_connect)]) -> None:
        pass

    def integrity_error() -> None:
        raise IntegrityError(SQL, {"token": CANARY}, Exception("duplicate key"))

    app.add_api_route("/_db/connection-refused", connection_refused)
    app.add_api_route("/_db/pool-exhausted", pool_exhausted)
    app.add_api_route("/_db/operational-error", operational_error)
    app.add_api_route("/_db/in-dependency", in_dependency)
    app.add_api_route("/_db/integrity-error", integrity_error)
    return offline_client


@pytest.mark.parametrize(
    "path",
    [
        "/_db/connection-refused",
        "/_db/pool-exhausted",
        "/_db/operational-error",
        "/_db/in-dependency",
    ],
)
def test_banco_ou_pool_indisponivel_responde_503_com_retry_after(
    client: TestClient, path: str
) -> None:
    response = client.get(path)

    body = assert_problem(response, 503, "SERVICE_UNAVAILABLE")
    assert body["instance"] == path
    assert body["request_id"] == response.headers["x-request-id"]
    assert response.headers["retry-after"] == "5"


def test_resposta_de_banco_indisponivel_nao_vaza_sql_nem_valores(client: TestClient) -> None:
    response = client.get("/_db/operational-error")

    assert_problem(response, 503, "SERVICE_UNAVAILABLE")
    for leaked in (CANARY, "secret_hash", "OperationalError", "senha incorreta"):
        assert leaked not in response.text


def test_log_registra_o_tipo_do_erro_sem_a_mensagem_do_banco(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        response = client.get("/_db/operational-error")

    [record] = [r for r in caplog.records if r.name == LOGGER]
    assert record.levelno == logging.ERROR
    assert response.json()["request_id"] in record.getMessage()
    assert record.__dict__["fields"] == {"error.type": "OperationalError"}
    assert record.exc_info is None
    assert CANARY not in caplog.text


def test_erro_de_integridade_continua_sendo_500(client: TestClient) -> None:
    response = client.get("/_db/integrity-error")

    assert_problem(response, 500, "INTERNAL_ERROR")
    assert "retry-after" not in response.headers
