"""Exceção no log só com tipo e frames, de banco ou não (docs/GoblinShiftPlan.md, 3.7; #442).

A mensagem de uma exceção pode trazer nome de arquivo, valor de parâmetro ou trecho de entrada do
usuário. Cada teste semeia um canário nela e lê a saída inteira do handler de produção: o
`JsonFormatter` com o filtro que `configure_logging` liga.
"""

import io
import json
import logging
import uuid
from collections.abc import Callable, Iterator
from typing import Any, cast

import pytest
from opentelemetry.trace import NoOpTracer
from psycopg import errors as pg_errors
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints import maintenance as maintenance_entrypoint
from goblinshift.entrypoints.api import saturation
from goblinshift.platform.settings import MissingSecretError
from goblinshift.platform.telemetry import (
    UVICORN_ERROR_LOGGER,
    UserHasher,
    configure_logging,
    exception_fields,
    omit_exception_detail,
)
from goblinshift.runs import finished as finished_module
from goblinshift.runs import maintenance as maintenance_module
from goblinshift.runs.finished import RunFinishedReporter
from goblinshift.runs.worker.executor_ctl_runner import ExecutorCtlError

UNIQUE_VIOLATION = "23505"
CONFIG_MESSAGE = "GS_CURSOR_SECRET é obrigatório em produção"


def _log_exception(log: tuple[logging.Logger, io.StringIO], error: BaseException) -> dict[str, Any]:
    logger, stream = log
    try:
        raise error
    except BaseException:
        logger.exception("falhou")
    return json.loads(stream.getvalue())  # type: ignore[no-any-return]


def _only_line(stream: io.StringIO) -> dict[str, Any]:
    [line] = stream.getvalue().splitlines()
    return json.loads(line)  # type: ignore[no-any-return]


def _chained(canary: str) -> BaseException:
    try:
        try:
            raise ValueError(canary)
        except ValueError as cause:
            raise RuntimeError("camada de cima") from cause
    except RuntimeError as error:
        return error


class _BrokenStrError(Exception):
    def __str__(self) -> str:
        raise RuntimeError("__str__ quebrado")


class _WithCodeError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@pytest.mark.parametrize(
    ("make", "kind"),
    [
        pytest.param(ValueError, "builtins.ValueError", id="ValueError"),
        pytest.param(KeyError, "builtins.KeyError", id="KeyError"),
        pytest.param(
            lambda c: FileNotFoundError(2, c, f"/srv/uploads/{c}"),
            "builtins.FileNotFoundError",
            id="FileNotFoundError",
        ),
    ],
)
def test_excecao_fora_do_banco_sai_sem_a_mensagem(
    production_log: tuple[logging.Logger, io.StringIO],
    canary: str,
    make: Callable[[str], BaseException],
    kind: str,
) -> None:
    line = _log_exception(production_log, make(canary))

    assert canary not in json.dumps(line, ensure_ascii=False)
    assert line["exception"].endswith(kind)
    assert "test_log_exception_detail.py" in line["exception"]


def test_causa_e_contexto_saem_sem_mensagem(
    production_log: tuple[logging.Logger, io.StringIO], canary: str
) -> None:
    line = _log_exception(production_log, _chained(canary))

    assert canary not in production_log[1].getvalue()
    assert "builtins.ValueError" in line["exception"]
    assert "builtins.RuntimeError" in line["exception"]


def test_notas_e_grupos_saem_sem_mensagem(
    production_log: tuple[logging.Logger, io.StringIO], canary: str
) -> None:
    inner = ValueError(canary)
    inner.add_note(canary)

    line = _log_exception(production_log, ExceptionGroup(canary, [inner, KeyError(canary)]))

    assert canary not in production_log[1].getvalue()
    assert "builtins.ExceptionGroup" in line["exception"]
    assert "builtins.ValueError" in line["exception"]
    assert "builtins.KeyError" in line["exception"]


def test_excecao_com_str_quebrado_ainda_gera_a_linha(
    production_log: tuple[logging.Logger, io.StringIO],
) -> None:
    line = _log_exception(production_log, _BrokenStrError())

    assert line["exception"].endswith("_BrokenStrError")


def test_erro_de_configuracao_mantem_a_mensagem(
    production_log: tuple[logging.Logger, io.StringIO],
) -> None:
    line = _log_exception(production_log, MissingSecretError(CONFIG_MESSAGE))

    assert CONFIG_MESSAGE in line["exception"]


class _DerivedSecretError(MissingSecretError):
    """Subclasse que não passou pela revisão das mensagens: não herda a dispensa."""


def test_subclasse_de_erro_de_configuracao_sai_sem_mensagem(
    production_log: tuple[logging.Logger, io.StringIO], canary: str
) -> None:
    line = _log_exception(production_log, _DerivedSecretError(canary))

    assert canary not in production_log[1].getvalue()
    assert line["exception"].endswith("_DerivedSecretError")


def test_causa_de_erro_de_configuracao_sai_sem_mensagem(
    production_log: tuple[logging.Logger, io.StringIO], canary: str
) -> None:
    try:
        try:
            raise FileNotFoundError(2, "No such file", f"/run/secrets/{canary}")
        except FileNotFoundError as cause:
            raise MissingSecretError("GS_X_FILE aponta para um arquivo que não existe") from cause
    except MissingSecretError as error:
        line = _log_exception(production_log, error)

    assert canary not in production_log[1].getvalue()
    assert "GS_X_FILE aponta para um arquivo que não existe" in line["exception"]


def test_erro_de_banco_continua_com_sqlstate(
    production_log: tuple[logging.Logger, io.StringIO], canary: str
) -> None:
    try:
        try:
            raise pg_errors.UniqueViolation(canary)
        except pg_errors.UniqueViolation as cause:
            raise DBAPIError(f"SELECT {canary}", {"value": canary}, cause) from cause
    except DBAPIError as wrapped:
        line = _log_exception(production_log, wrapped)

    assert line["sqlstate"] == UNIQUE_VIOLATION
    assert f"psycopg.errors.UniqueViolation (SQLSTATE {UNIQUE_VIOLATION})" in line["exception"]
    assert canary not in json.dumps(line, ensure_ascii=False)


def test_exception_fields_leva_tipo_e_codigo_sem_mensagem(canary: str) -> None:
    assert exception_fields(ExecutorCtlError("image_not_allowed", canary)) == {
        "error_type": "ExecutorCtlError",
        "error_reason": "image_not_allowed",
    }
    assert exception_fields(ValueError(canary)) == {"error_type": "ValueError"}
    assert "error_reason" not in exception_fields(_WithCodeError(code=f"Bad {canary}"))


def test_exception_fields_leva_o_sqlstate_do_banco(canary: str) -> None:
    fields = exception_fields(pg_errors.UniqueViolation(canary))

    assert fields == {"error_type": "UniqueViolation", "sqlstate": UNIQUE_VIOLATION}


@pytest.fixture
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    uvicorn_error = logging.getLogger(UVICORN_ERROR_LOGGER)
    filters = uvicorn_error.filters[:]
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    uvicorn_error.filters[:] = filters


@pytest.mark.usefixtures("restore_logging")
def test_configure_logging_liga_o_filtro_no_root_e_no_uvicorn() -> None:
    configure_logging()

    assert omit_exception_detail in logging.getLogger().handlers[0].filters
    assert omit_exception_detail in logging.getLogger(UVICORN_ERROR_LOGGER).filters


# Fronteiras que registram exceções fora do banco ---------------------------------------------


def test_passo_da_manutencao_que_falha_sai_sem_a_mensagem(
    listen: Callable[[logging.Logger], io.StringIO], canary: str
) -> None:
    stream = listen(maintenance_module.logger)

    try:
        raise RuntimeError(canary)
    except RuntimeError as error:
        maintenance_module.log_step_failure("passo", error)

    line = _only_line(stream)
    assert canary not in stream.getvalue()
    assert line["exception"].endswith("builtins.RuntimeError")


def test_purga_que_falha_sai_sem_a_mensagem(
    listen: Callable[[logging.Logger], io.StringIO], canary: str
) -> None:
    stream = listen(maintenance_entrypoint.logger)

    try:
        raise RuntimeError(canary)
    except RuntimeError as error:
        maintenance_entrypoint._log_purge_failure(uuid.uuid4(), error)

    line = _only_line(stream)
    assert canary not in stream.getvalue()
    assert line["exception"].endswith("builtins.RuntimeError")


def test_run_finished_perdido_sai_sem_a_mensagem(
    listen: Callable[[logging.Logger], io.StringIO], canary: str
) -> None:
    stream = listen(finished_module.logger)

    def factory() -> Session:
        raise RuntimeError(canary)

    reporter = RunFinishedReporter(
        cast(sessionmaker[Session], factory), UserHasher(b"k" * 32), NoOpTracer()
    )
    reporter.report(uuid.uuid4())

    line = _only_line(stream)
    assert canary not in stream.getvalue()
    assert line["exception"].endswith("builtins.RuntimeError")


def test_relato_de_saturacao_que_falha_sai_sem_a_mensagem(
    listen: Callable[[logging.Logger], io.StringIO],
    monkeypatch: pytest.MonkeyPatch,
    canary: str,
) -> None:
    stream = listen(saturation.logger)

    def fail(tracer: object, counter: object) -> None:
        raise RuntimeError(canary)

    monkeypatch.setattr(saturation, "report_window", fail)

    saturation._report_safely(NoOpTracer(), saturation.ConcurrencyLimitCounter())

    line = _only_line(stream)
    assert canary not in stream.getvalue()
    assert line["exception"].endswith("builtins.RuntimeError")
