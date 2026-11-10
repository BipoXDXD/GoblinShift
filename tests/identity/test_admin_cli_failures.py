"""Falha não prevista do `gs-admin`: uma linha curta no stderr e saída 4, sem banco.

A falha vem das `Settings`, o primeiro passo do comando; o caso com o PostgreSQL real está em
`test_admin_cli.py`. A mensagem de cada exceção leva um canário, que não pode chegar à tela.
"""

import uuid
from collections.abc import Callable

import pytest
from psycopg import OperationalError as PsycopgOperationalError
from psycopg import errors as pg_errors
from sqlalchemy.exc import DBAPIError

from goblinshift.entrypoints import admin_cli
from goblinshift.entrypoints.admin_cli import EXIT_FAILURE, EXIT_INTERRUPTED, main


def _canary() -> str:
    return f"CANARY-{uuid.uuid4()}"


def _wrapped(error: Exception, canary: str) -> DBAPIError:
    """Como o SQLAlchemy entrega o erro do driver: SQL e parâmetros na mensagem externa."""
    try:
        try:
            raise error
        except Exception as cause:
            raise DBAPIError(f"SELECT '{canary}'", {"value": canary}, cause) from cause
    except DBAPIError as wrapped:
        return wrapped


def _fail_with(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    def settings() -> object:
        raise error

    monkeypatch.setattr(admin_cli, "Settings", settings)


def _run(capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    exit_code = main(["list-users"])
    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


def test_erro_de_banco_sai_com_4_e_uma_linha_com_o_sqlstate(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    canary = _canary()
    _fail_with(monkeypatch, _wrapped(pg_errors.UniqueViolation(canary), canary))

    exit_code, out, err = _run(capsys)

    assert exit_code == EXIT_FAILURE
    assert out == ""
    assert err == "gs-admin: falha no banco de dados (DBAPIError, SQLSTATE 23505)\n"


def test_erro_de_banco_sem_sqlstate_sai_so_com_o_tipo(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    canary = _canary()
    _fail_with(monkeypatch, PsycopgOperationalError(f'connection to server at "{canary}" failed'))

    exit_code, _, err = _run(capsys)

    assert exit_code == EXIT_FAILURE
    assert err == "gs-admin: falha no banco de dados (OperationalError)\n"


def test_erro_que_nao_e_de_banco_sai_com_4_e_so_o_tipo(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    canary = _canary()
    _fail_with(monkeypatch, ValueError(canary))

    exit_code, out, err = _run(capsys)

    assert exit_code == EXIT_FAILURE
    assert out == ""
    assert err == "gs-admin: falha inesperada (ValueError); detalhes omitidos\n"


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(
            lambda canary: _wrapped(pg_errors.UniqueViolation(canary), canary), id="banco"
        ),
        pytest.param(lambda canary: PsycopgOperationalError(canary), id="psycopg-cru"),
        pytest.param(lambda canary: ValueError(canary), id="nao-e-de-banco"),
        pytest.param(lambda canary: OSError(2, canary, canary), id="sistema"),
    ],
)
def test_falha_nao_traz_canario_nem_stack_trace(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Callable[[str], Exception],
) -> None:
    canary = _canary()
    _fail_with(monkeypatch, error(canary))

    _, out, err = _run(capsys)

    assert canary not in out + err
    assert "Traceback" not in err
    assert err.count("\n") == 1


def test_interrupcao_sai_com_130_sem_a_cadeia_de_erros(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ctrl-C durante o `engine.dispose()`, com um erro de banco ainda propagando."""
    canary = _canary()
    interrupted = KeyboardInterrupt()
    interrupted.__context__ = _wrapped(pg_errors.UniqueViolation(canary), canary)
    _fail_with(monkeypatch, interrupted)

    exit_code, out, err = _run(capsys)

    assert exit_code == EXIT_INTERRUPTED
    assert out == ""
    assert err == "gs-admin: interrompido\n"
