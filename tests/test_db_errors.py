"""A leitura dos erros do banco: as duas regras de "banco indisponível" e o SQLSTATE nos logs.

As duas regras não são a mesma pergunta. A manutenção quer saber se a conexão caiu (repetir o
banco noutro passo só esperaria o `connect_timeout`); a API quer saber se vale o cliente repetir
a requisição. A tabela trava onde elas já divergiam.
"""

import uuid

import psycopg
import pytest
from psycopg import errors as pg_errors
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from goblinshift.entrypoints import maintenance as maintenance_entrypoint
from goblinshift.platform.db import is_connection_lost, is_unavailable_for_request
from goblinshift.runs import maintenance as runs_maintenance


def _wrapped(driver: Exception, *, invalidated: bool = False) -> Exception:
    return DBAPIError.instance(
        "SELECT 1", {}, driver, psycopg.Error, connection_invalidated=invalidated
    )


def _server(sqlstate: str) -> Exception:
    return _wrapped(pg_errors.lookup(sqlstate)("x"))


# (id, erro, conexão perdida para a manutenção, indisponível para a API)
CASES: list[tuple[str, Exception, bool, bool]] = [
    ("pool-esgotado", PoolTimeoutError(), True, True),
    ("sem-sqlstate", _wrapped(psycopg.OperationalError("x")), True, True),
    ("08006", _server("08006"), True, True),
    ("08P01", _server("08P01"), True, False),
    ("57P01", _server("57P01"), True, True),
    ("57P02", _server("57P02"), True, True),
    ("57P03", _server("57P03"), True, True),
    ("57014", _server("57014"), False, True),
    ("53300", _server("53300"), False, True),
    ("55P03", _server("55P03"), False, True),
    ("40001", _server("40001"), False, True),
    ("40P01", _server("40P01"), False, True),
    ("58030", _server("58030"), False, False),
    ("25P03", _server("25P03"), False, False),
    ("25P03-invalidada", _wrapped(pg_errors.lookup("25P03")("x"), invalidated=True), True, True),
    ("23505", _server("23505"), False, False),
    ("nao-e-banco", ValueError("x"), False, False),
]


@pytest.mark.parametrize(
    ("error", "lost", "unavailable"), [c[1:] for c in CASES], ids=[c[0] for c in CASES]
)
def test_as_duas_regras_divergem_onde_ja_divergiam(
    error: Exception, lost: bool, unavailable: bool
) -> None:
    assert is_connection_lost(error) is lost
    assert is_unavailable_for_request(error) is unavailable


def _raw_lock_error() -> psycopg.Error:
    """O erro cru do psycopg, sem o embrulho do SQLAlchemy (sem `orig`)."""
    try:
        raise pg_errors.lookup("55P03")("x")
    except psycopg.Error as error:
        return error


def test_falha_de_passo_com_erro_cru_leva_o_sqlstate(caplog: pytest.LogCaptureFixture) -> None:
    error = _raw_lock_error()
    with caplog.at_level("ERROR"):
        try:
            raise error
        except psycopg.Error as caught:
            runs_maintenance.log_step_failure("passo", caught)
    assert caplog.records[-1].__dict__["fields"]["sqlstate"] == "55P03"


def test_falha_de_purga_com_erro_cru_leva_o_sqlstate(caplog: pytest.LogCaptureFixture) -> None:
    error = _raw_lock_error()
    with caplog.at_level("ERROR"):
        try:
            raise error
        except psycopg.Error as caught:
            maintenance_entrypoint._log_purge_failure(uuid.uuid4(), caught)
    assert caplog.records[-1].__dict__["fields"]["sqlstate"] == "55P03"
