"""SQLSTATE do PostgreSQL no log de erro de banco, sem a mensagem (docs/GoblinShiftPlan.md, 3.7).

Sem banco: as exceções são as classes do psycopg, que trazem o SQLSTATE na própria classe, e o
`DBAPIError` que o SQLAlchemy põe em volta delas. A mensagem de cada uma leva um canário.
"""

import io
import json
import logging
import uuid
from collections.abc import Iterator

import pytest
from psycopg import OperationalError as PsycopgOperationalError
from psycopg import errors as pg_errors
from sqlalchemy.exc import DBAPIError

from goblinshift.platform.telemetry import (
    JsonFormatter,
    database_sqlstate,
    omit_database_error_detail,
)

UNIQUE_VIOLATION = "23505"
INVALID_TEXT_REPRESENTATION = "22P02"


def _canary() -> str:
    return f"CANARY-{uuid.uuid4()}"


def _raised(error: BaseException) -> BaseException:
    try:
        raise error
    except BaseException as caught:
        return caught


def _wrapped(error: Exception, canary: str) -> BaseException:
    """Como o SQLAlchemy entrega o erro do driver: SQL e parâmetros na mensagem externa, e o erro
    do driver como causa (`raise ... from`)."""
    try:
        try:
            raise error
        except Exception as cause:
            raise DBAPIError(f"SELECT {canary}", {"value": canary}, cause) from cause
    except DBAPIError as wrapped:
        return wrapped


@pytest.fixture
def log() -> Iterator[tuple[logging.Logger, io.StringIO]]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(omit_database_error_detail)
    logger = logging.getLogger(f"tests.log_sqlstate.{uuid.uuid4()}")
    logger.propagate = False
    logger.addHandler(handler)
    yield logger, stream
    logger.removeHandler(handler)


def _log_exception(log: tuple[logging.Logger, io.StringIO], error: BaseException) -> dict[str, str]:
    logger, stream = log
    try:
        raise error
    except BaseException:
        logger.exception("falhou")
    return json.loads(stream.getvalue())  # type: ignore[no-any-return]


def test_log_de_erro_de_banco_traz_o_sqlstate_em_campo_proprio(
    log: tuple[logging.Logger, io.StringIO],
) -> None:
    canary = _canary()

    entry = _log_exception(log, _wrapped(pg_errors.UniqueViolation(canary), canary))

    assert entry["sqlstate"] == UNIQUE_VIOLATION


def test_texto_da_excecao_no_log_traz_o_sqlstate_junto_do_tipo(
    log: tuple[logging.Logger, io.StringIO],
) -> None:
    canary = _canary()

    entry = _log_exception(log, _wrapped(pg_errors.InvalidTextRepresentation(canary), canary))

    assert (
        f"psycopg.errors.InvalidTextRepresentation (SQLSTATE {INVALID_TEXT_REPRESENTATION})"
        in entry["exception"]
    )


def test_log_com_sqlstate_nao_traz_o_canario_passado_como_parametro(
    log: tuple[logging.Logger, io.StringIO],
) -> None:
    canary = _canary()
    _, stream = log

    _log_exception(log, _wrapped(pg_errors.UniqueViolation(canary), canary))

    assert canary not in stream.getvalue()


def test_erro_de_banco_sem_sqlstate_sai_sem_o_campo(
    log: tuple[logging.Logger, io.StringIO],
) -> None:
    canary = _canary()

    entry = _log_exception(log, PsycopgOperationalError(canary))

    assert "sqlstate" not in entry
    assert canary not in json.dumps(entry)


def test_excecao_que_nao_e_de_banco_sai_sem_sqlstate(
    log: tuple[logging.Logger, io.StringIO],
) -> None:
    entry = _log_exception(log, ValueError("valor"))

    assert "sqlstate" not in entry
    assert "ValueError: valor" in entry["exception"]


def test_log_sem_excecao_sai_sem_sqlstate(log: tuple[logging.Logger, io.StringIO]) -> None:
    logger, stream = log

    logger.error("sem exceção")

    assert "sqlstate" not in json.loads(stream.getvalue())


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        pytest.param(pg_errors.UniqueViolation("x"), UNIQUE_VIOLATION, id="psycopg"),
        pytest.param(
            _wrapped(pg_errors.InvalidTextRepresentation("x"), "x"),
            INVALID_TEXT_REPRESENTATION,
            id="dentro-do-sqlalchemy",
        ),
        pytest.param(PsycopgOperationalError("x"), None, id="psycopg-sem-sqlstate"),
        pytest.param(ValueError("x"), None, id="nao-e-de-banco"),
    ],
)
def test_database_sqlstate_acha_o_codigo_na_cadeia(
    error: BaseException, expected: str | None
) -> None:
    assert database_sqlstate(error) == expected


def test_database_sqlstate_acha_o_codigo_na_causa() -> None:
    try:
        try:
            raise pg_errors.UniqueViolation("x")
        except pg_errors.UniqueViolation as cause:
            raise RuntimeError("camada de cima") from cause
    except RuntimeError as error:
        assert database_sqlstate(error) == UNIQUE_VIOLATION


class _ForgedSqlstate(pg_errors.UniqueViolation):
    sqlstate = "23505 CANARY"


def test_database_sqlstate_descarta_codigo_malformado() -> None:
    assert database_sqlstate(_raised(_ForgedSqlstate("x"))) is None
