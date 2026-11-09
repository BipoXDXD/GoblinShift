"""Banco ou pool indisponível responde 503 com `Retry-After` (docs/GoblinShiftPlan.md, 1.6 e 3.2).

Sem Docker: o `offline_client` aponta para uma porta sem banco, então a recusa de conexão é real,
e o pool esgotado sai de um `QueuePool` de verdade sobre SQLite. Os erros do banco são as classes
do próprio psycopg, embrulhadas pelo SQLAlchemy como em produção; o caminho com PostgreSQL real
está em `test_database_unavailable_postgres.py`.
"""

import logging
from collections.abc import Iterator
from typing import Annotated, Any

import psycopg
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from psycopg import errors as pg_errors
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import QueuePool

from tests.api.support import assert_problem

CANARY = "CANARY-valor-da-consulta"
SQL = "SELECT secret_hash FROM api_key WHERE token = %(token)s"
LOGGER = "goblinshift.entrypoints.api.problems"
UNAVAILABLE_LOG = "banco indisponível"
UNEXPECTED_LOG = "erro inesperado"
BACKEND_TERMINATED = "57P01"


def _wrapped(driver_error: Exception, *, connection_invalidated: bool = False) -> BaseException:
    """Embrulha como o SQLAlchemy faz: a classe resultante vem do tipo do erro do driver."""
    return DBAPIError.instance(
        SQL,
        {"token": CANARY},
        driver_error,
        psycopg.Error,
        connection_invalidated=connection_invalidated,
    )


def _server_error(sqlstate: str) -> Exception:
    """O erro que o psycopg levanta quando o servidor responde este SQLSTATE."""
    return pg_errors.lookup(sqlstate)(f"senha incorreta: {CANARY}")


def _session_that_cannot_connect() -> Iterator[None]:
    raise _wrapped(psycopg.OperationalError(f"connection failed: {CANARY}"))
    yield


# Fora da allowlist: só a conexão invalidada pode tornar este erro um 503.
UNLISTED_SQLSTATE = "58030"

# Erros do driver sem SQLSTATE: falha ao conectar ou conexão perdida, e o que não é disso.
DRIVER_ERRORS: dict[str, Exception] = {
    "connection-failed": psycopg.OperationalError(f"connection failed: {CANARY}"),
    "connection-timeout": pg_errors.ConnectionTimeout(f"connection timeout: {CANARY}"),
    "data": psycopg.DataError(f"cannot contain NUL: {CANARY}"),
    "internal": psycopg.InternalError(f"estado inválido: {CANARY}"),
    "integrity": psycopg.IntegrityError(f"duplicate key: {CANARY}"),
    "programming": psycopg.ProgrammingError(f"sintaxe: {CANARY}"),
    "not-supported": psycopg.NotSupportedError(f"sem suporte: {CANARY}"),
}


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

    def server_error(sqlstate: str) -> None:
        raise _wrapped(_server_error(sqlstate))

    def invalidated_connection() -> None:
        raise _wrapped(_server_error(UNLISTED_SQLSTATE), connection_invalidated=True)

    def driver_error(name: str) -> None:
        raise _wrapped(DRIVER_ERRORS[name])

    def in_dependency(_session: Annotated[Any, Depends(_session_that_cannot_connect)]) -> None:
        pass

    app.add_api_route("/_db/connection-refused", connection_refused)
    app.add_api_route("/_db/pool-exhausted", pool_exhausted)
    app.add_api_route("/_db/sqlstate/{sqlstate}", server_error)
    app.add_api_route("/_db/invalidated-connection", invalidated_connection)
    app.add_api_route("/_db/driver/{name}", driver_error)
    app.add_api_route("/_db/in-dependency", in_dependency)
    return offline_client


def _assert_unavailable(client: TestClient, path: str) -> None:
    response = client.get(path)

    body = assert_problem(response, 503, "SERVICE_UNAVAILABLE")
    assert body["instance"] == path
    assert body["request_id"] == response.headers["x-request-id"]
    assert response.headers["retry-after"] == "5"
    assert CANARY not in response.text


def _assert_internal_error(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert_problem(response, 500, "INTERNAL_ERROR")
    assert "retry-after" not in response.headers
    assert CANARY not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/_db/connection-refused",
        "/_db/pool-exhausted",
        "/_db/in-dependency",
        "/_db/invalidated-connection",
        "/_db/driver/connection-failed",
        "/_db/driver/connection-timeout",
    ],
)
def test_banco_ou_pool_indisponivel_responde_503_com_retry_after(
    client: TestClient, path: str
) -> None:
    _assert_unavailable(client, path)


@pytest.mark.parametrize(
    "sqlstate",
    [
        pytest.param("08001", id="conexao-nao-estabelecida"),
        pytest.param("08006", id="falha-de-conexao"),
        pytest.param("53300", id="conexoes-demais"),
        pytest.param("57014", id="statement-timeout"),
        pytest.param("57P03", id="banco-subindo"),
        pytest.param(BACKEND_TERMINATED, id="backend-encerrado"),
        pytest.param("55P03", id="lock-timeout"),
        pytest.param("40001", id="falha-de-serializacao"),
        pytest.param("40P01", id="deadlock"),
    ],
)
def test_sqlstate_temporario_responde_503(client: TestClient, sqlstate: str) -> None:
    _assert_unavailable(client, f"/_db/sqlstate/{sqlstate}")


@pytest.mark.parametrize(
    "sqlstate",
    [
        pytest.param("55000", id="currval-sem-nextval"),
        pytest.param("3B001", id="savepoint-inexistente"),
        pytest.param("55P02", id="parametro-interno"),
        pytest.param("2F005", id="plpgsql-sem-return"),
        pytest.param("54000", id="linha-de-indice-grande"),
        pytest.param("08P01", id="violacao-de-protocolo"),
        pytest.param("40002", id="integridade-em-transacao"),
        pytest.param("40003", id="conclusao-desconhecida"),
        pytest.param("55006", id="objeto-em-uso"),
        pytest.param(UNLISTED_SQLSTATE, id="erro-de-io"),
    ],
)
def test_operational_error_que_nao_e_indisponibilidade_responde_500(
    client: TestClient, sqlstate: str
) -> None:
    assert issubclass(pg_errors.lookup(sqlstate), psycopg.OperationalError)

    _assert_internal_error(client, f"/_db/sqlstate/{sqlstate}")


@pytest.mark.parametrize("name", ["data", "internal", "integrity", "programming", "not-supported"])
def test_outras_classes_do_driver_sem_sqlstate_respondem_500(client: TestClient, name: str) -> None:
    _assert_internal_error(client, f"/_db/driver/{name}")


def test_resposta_de_banco_indisponivel_nao_vaza_sql_nem_valores(client: TestClient) -> None:
    response = client.get(f"/_db/sqlstate/{BACKEND_TERMINATED}")

    assert_problem(response, 503, "SERVICE_UNAVAILABLE")
    for leaked in (CANARY, "secret_hash", "OperationalError", "senha incorreta"):
        assert leaked not in response.text


def _records_with(caplog: pytest.LogCaptureFixture, message: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER and message in r.getMessage()]


def test_log_registra_tipo_e_sqlstate_sem_a_mensagem_do_banco(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        response = client.get(f"/_db/sqlstate/{BACKEND_TERMINATED}")

    [record] = _records_with(caplog, UNAVAILABLE_LOG)
    assert record.levelno == logging.ERROR
    assert response.json()["request_id"] in record.getMessage()
    assert record.__dict__["fields"] == {
        "error.type": "OperationalError",
        "sqlstate": BACKEND_TERMINATED,
    }
    assert record.exc_info is None
    assert CANARY not in caplog.text


def test_log_de_pool_esgotado_nao_tem_sqlstate(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        client.get("/_db/pool-exhausted")

    [record] = _records_with(caplog, UNAVAILABLE_LOG)
    assert record.__dict__["fields"] == {"error.type": "TimeoutError", "sqlstate": None}


def test_erro_de_programacao_mantem_o_log_de_erro_inesperado(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        response = client.get("/_db/sqlstate/55000")

    [record] = _records_with(caplog, UNEXPECTED_LOG)
    assert response.json()["request_id"] in record.getMessage()
    assert record.exc_info is not None
    assert _records_with(caplog, UNAVAILABLE_LOG) == []
