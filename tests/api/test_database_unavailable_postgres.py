"""PostgreSQL real separa indisponibilidade (503) de erro de programação (500).

O psycopg classifica como `OperationalError` SQLSTATEs que não têm nada de temporário, e só o
servidor real prova qual SQLSTATE cada situação produz.
"""

import logging

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from tests.api.support import assert_problem

pytestmark = pytest.mark.integration

LOGGER = "goblinshift.entrypoints.api.problems"
LARGE_INCOMPRESSIBLE_TEXT = "(SELECT string_agg(md5(i::text), '') FROM generate_series(1, 400) i)"

PROGRAMMING_ERRORS: dict[str, list[str]] = {
    "55000": ["CREATE TEMP SEQUENCE probe_seq", "SELECT currval('probe_seq')"],
    "3B001": ["ROLLBACK TO SAVEPOINT missing_savepoint"],
    "55P02": ["SET shared_buffers = '1MB'"],
    "2F005": [
        "CREATE FUNCTION pg_temp.no_return() RETURNS int LANGUAGE plpgsql AS 'BEGIN END'",
        "SELECT pg_temp.no_return()",
    ],
    "54000": [
        "CREATE TEMP TABLE probe_index (value text)",
        "CREATE INDEX ON probe_index (value)",
        f"INSERT INTO probe_index SELECT {LARGE_INCOMPRESSIBLE_TEXT}",
    ],
    # `%(value)s` vira $1 e só ele tem valor: o Bind não fecha com o $2 (protocolo estendido).
    "08P01": ["SELECT %(value)s::int, $2::int"],
}
STATEMENT_TIMEOUT: list[str] = ["SET LOCAL statement_timeout = '10ms'", "SELECT pg_sleep(5)"]
BACKEND_TERMINATED: list[str] = ["SELECT pg_terminate_backend(pg_backend_pid())"]


def _run(engine: Engine, statements: list[str]) -> None:
    with engine.begin() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement, {"value": 1})


@pytest.fixture
def pg_client(app: FastAPI, client: TestClient) -> TestClient:
    def programming_error(sqlstate: str, request: Request) -> None:
        _run(request.app.state.engine, PROGRAMMING_ERRORS[sqlstate])

    def statement_timeout(request: Request) -> None:
        _run(request.app.state.engine, STATEMENT_TIMEOUT)

    def backend_terminated(request: Request) -> None:
        _run(request.app.state.engine, BACKEND_TERMINATED)

    def lock_timeout(request: Request) -> None:
        engine = request.app.state.engine
        with engine.begin() as holder:
            holder.exec_driver_sql("LOCK TABLE alembic_version")
            _run(
                engine,
                ["SET LOCAL lock_timeout = '10ms'", "LOCK TABLE alembic_version"],
            )

    app.add_api_route("/_pg/programming/{sqlstate}", programming_error)
    app.add_api_route("/_pg/statement-timeout", statement_timeout)
    app.add_api_route("/_pg/backend-terminated", backend_terminated)
    app.add_api_route("/_pg/lock-timeout", lock_timeout)
    return client


@pytest.mark.parametrize("sqlstate", sorted(PROGRAMMING_ERRORS))
def test_erro_de_programacao_no_postgres_responde_500(
    pg_client: TestClient, caplog: pytest.LogCaptureFixture, sqlstate: str
) -> None:
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        response = pg_client.get(f"/_pg/programming/{sqlstate}")

    assert_problem(response, 500, "INTERNAL_ERROR")
    assert "retry-after" not in response.headers
    [record] = [r for r in caplog.records if r.name == LOGGER]
    assert record.__dict__["fields"]["sqlstate"] == sqlstate


@pytest.mark.parametrize(
    ("path", "sqlstate"),
    [
        ("/_pg/statement-timeout", "57014"),
        ("/_pg/lock-timeout", "55P03"),
        ("/_pg/backend-terminated", "57P01"),
    ],
)
def test_indisponibilidade_no_postgres_responde_503(
    pg_client: TestClient, caplog: pytest.LogCaptureFixture, path: str, sqlstate: str
) -> None:
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        response = pg_client.get(path)

    assert_problem(response, 503, "SERVICE_UNAVAILABLE")
    assert response.headers["retry-after"] == "5"
    [record] = [r for r in caplog.records if r.name == LOGGER]
    assert record.__dict__["fields"]["sqlstate"] == sqlstate
