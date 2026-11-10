"""Exceção de resposta interrompida não sai com detalhe no span `main` (GoblinShiftPlan.md, 3.7).

Sem banco: a exceção é construída com as classes reais do SQLAlchemy, e os bytes conferidos são os
que o `OTLPSpanExporter` enviaria, depois do mesmo processador da produção.
"""

import io
import logging
import threading
import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import Link, SpanContext, StatusCode, TraceFlags
from psycopg import Error as PsycopgError
from psycopg import OperationalError as PsycopgOperationalError
from psycopg import errors as pg_errors
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from uvicorn.logging import DefaultFormatter

from goblinshift.entrypoints import maintenance, worker
from goblinshift.entrypoints.api.app import create_app
from goblinshift.entrypoints.api.transfers import DownloadResponse, TransferTimeouts
from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from goblinshift.platform.telemetry import (
    JsonFormatter,
    make_span_processor,
    omit_database_error_detail,
)
from goblinshift.runs.worker import attempt_event
from goblinshift.runs.worker.claim import ClaimedAttempt
from goblinshift.runs.worker.coordinator import Coordinator

TEST_ROUTES = "/api/v1/_test"
TIMEOUTS = TransferTimeouts(total=timedelta(seconds=10), idle=timedelta(seconds=10))
FIRST_CHUNK = b"primeiro pedaco"
OPERATIONAL_ERROR_TYPE = "sqlalchemy.exc.OperationalError"
REDACTED = "exception detail redacted"


class OtlpBytesExporter(SpanExporter):
    """Guarda os spans e os bytes protobuf que iriam para o Collector."""

    def __init__(self) -> None:
        self.spans: list[ReadableSpan] = []
        self.payload = bytearray()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.spans.extend(spans)
        self.payload += encode_spans(spans).SerializeToString()
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None

    def main_span(self) -> ReadableSpan:
        (main,) = [span for span in self.spans if (span.attributes or {}).get("main") is True]
        return main


@dataclass(frozen=True)
class Canaries:
    sql: str
    parameter: str
    host: str
    path: str

    def database_error(self) -> OperationalError:
        return OperationalError(
            f"/* {self.sql} */ SELECT 1 WHERE %(id)s IS NOT NULL",
            {"id": self.parameter},
            Exception(f'connection to server at "{self.host}", port 5432 failed'),
        )

    def permanent_database_error(self) -> OperationalError:
        return OperationalError(
            f"/* {self.sql} */ SELECT currval(%(id)s)",
            {"id": self.parameter},
            pg_errors.lookup("55000")(f'currval em "{self.host}"'),
        )

    def storage_error(self) -> OSError:
        return OSError(f"falha ao ler {self.path}")

    def all(self) -> tuple[str, ...]:
        return (self.sql, self.parameter, self.host, self.path)


class FileFailingAfterFirstChunk(io.BytesIO):
    def __init__(self, error: Exception) -> None:
        super().__init__(FIRST_CHUNK)
        self._error = error
        self._delivered = False

    def read(self, size: int | None = -1, /) -> bytes:
        if self._delivered:
            raise self._error
        self._delivered = True
        return super().read(size)


@dataclass
class Traced:
    app: FastAPI
    client: TestClient
    exporter: OtlpBytesExporter
    canaries: Canaries
    log: io.StringIO


def _new_canary() -> str:
    return f"CANARY-{uuid.uuid4()}"


def _download_routes(canaries: Canaries) -> APIRouter:
    router = APIRouter(prefix="/api/v1/_test")

    def raise_database_error() -> None:
        raise canaries.database_error()

    def download(file: io.BytesIO, on_close: Callable[[], None]) -> DownloadResponse:
        return DownloadResponse(file, size=64, filename="x", timeouts=TIMEOUTS, on_close=on_close)

    @router.get("/interrupted")
    def interrupted() -> DownloadResponse:
        return download(FileFailingAfterFirstChunk(canaries.storage_error()), lambda: None)

    @router.get("/interrupted-close-fails")
    def interrupted_close_fails() -> DownloadResponse:
        return download(FileFailingAfterFirstChunk(canaries.storage_error()), raise_database_error)

    @router.get("/complete-close-fails")
    def complete_close_fails() -> DownloadResponse:
        return DownloadResponse(
            io.BytesIO(FIRST_CHUNK),
            size=len(FIRST_CHUNK),
            filename="x",
            timeouts=TIMEOUTS,
            on_close=raise_database_error,
        )

    @router.get("/plain")
    def plain() -> dict[str, bool]:
        return {"ok": True}

    @router.get("/plain-fails")
    def plain_fails() -> None:
        raise_database_error()

    @router.get("/plain-fails-permanent")
    def plain_fails_permanent() -> None:
        raise canaries.permanent_database_error()

    @router.get("/plain-fails-outside-database")
    def plain_fails_outside_database() -> None:
        raise canaries.storage_error()

    return router


@pytest.fixture
def traced() -> Iterator[Traced]:
    exporter = OtlpBytesExporter()
    canaries = Canaries(_new_canary(), _new_canary(), _new_canary(), _new_canary())
    app: FastAPI = create_app(
        Settings(environment="test", db_port=1, db_connect_timeout_s=1), span_exporter=exporter
    )
    app.include_router(_download_routes(canaries))
    # O handler real de `configure_logging`, com a saída trocada: filtro e formatador de produção.
    log = io.StringIO()
    handler = next(
        h for h in logging.getLogger().handlers if isinstance(h.formatter, JsonFormatter)
    )
    assert isinstance(handler, logging.StreamHandler)
    handler.setStream(log)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield Traced(app, client, exporter, canaries, log)
    finally:
        SQLAlchemyInstrumentor().uninstrument()


def _assert_no_canary_in_otlp(traced: Traced) -> None:
    payload = bytes(traced.exporter.payload)
    leaked = [canary for canary in traced.canaries.all() if canary.encode() in payload]
    assert leaked == []


def _assert_no_canary_in_logs(traced: Traced) -> None:
    output = traced.log.getvalue()
    if not output:
        pytest.fail("nenhum log capturado: o cenário não chegou ao erro")
    leaked = [canary for canary in traced.canaries.all() if canary in output]
    assert leaked == []


def _exception_events(span: ReadableSpan) -> list[dict[str, object]]:
    return [dict(event.attributes or {}) for event in span.events if event.name == "exception"]


def _assert_detail_redacted_keeping_type(span: ReadableSpan, exception_type: str) -> None:
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description == REDACTED
    events = _exception_events(span)
    assert events
    for event in events:
        assert event == {
            "exception.type": exception_type,
            "exception.escaped": "False",
            "exception.message": REDACTED,
            "exception.stacktrace": REDACTED,
        }


def test_download_interrompido_sai_sem_o_detalhe_no_otlp(
    traced: Traced, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)

    traced.client.get(f"{TEST_ROUTES}/interrupted")

    _assert_no_canary_in_otlp(traced)
    _assert_detail_redacted_keeping_type(traced.exporter.main_span(), "OSError")
    assert b"OSError" in bytes(traced.exporter.payload)


def test_on_close_que_falha_no_download_interrompido_sai_sem_o_detalhe_no_otlp(
    traced: Traced, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)

    traced.client.get(f"{TEST_ROUTES}/interrupted-close-fails")

    _assert_no_canary_in_otlp(traced)
    _assert_detail_redacted_keeping_type(traced.exporter.main_span(), OPERATIONAL_ERROR_TYPE)
    assert OPERATIONAL_ERROR_TYPE.encode() in bytes(traced.exporter.payload)


def test_download_interrompido_continua_no_log_com_o_request_id_e_a_excecao(
    traced: Traced, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)

    traced.client.get(f"{TEST_ROUTES}/interrupted-close-fails")

    [record] = [r for r in caplog.records if r.name.endswith(".problems")]
    assert record.getMessage().startswith("erro inesperado na requisição ")
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], OperationalError)


def test_log_do_download_interrompido_nao_traz_canario(
    traced: Traced, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)

    traced.client.get(f"{TEST_ROUTES}/interrupted-close-fails")

    _assert_no_canary_in_logs(traced)


def test_on_close_que_falha_depois_do_download_completo_sai_sem_o_detalhe_no_otlp(
    traced: Traced, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)

    response = traced.client.get(f"{TEST_ROUTES}/complete-close-fails")

    assert response.content == FIRST_CHUNK
    _assert_no_canary_in_otlp(traced)
    assert _exception_events(traced.exporter.main_span()) == []


def test_rota_comum_sai_sem_eventos_e_sem_erro(traced: Traced) -> None:
    response = traced.client.get(f"{TEST_ROUTES}/plain")

    assert response.json() == {"ok": True}
    main = traced.exporter.main_span()
    assert main.events == ()
    assert main.status.status_code is StatusCode.UNSET


def test_rota_comum_que_falha_antes_da_resposta_sai_sem_eventos_e_sem_detalhe(
    traced: Traced, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)

    response = traced.client.get(f"{TEST_ROUTES}/plain-fails")

    assert response.status_code == 503
    assert response.json()["code"] == "SERVICE_UNAVAILABLE"
    _assert_no_canary_in_otlp(traced)
    assert traced.exporter.main_span().events == ()


# Log: erro de banco só com tipo e frames; o resto, por inteiro


def _uvicorn_log(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> str:
    """O que o Uvicorn escreve ao receber a exceção: handler próprio, sem passar pelo root."""
    logger = logging.getLogger("uvicorn.error")
    monkeypatch.setattr(logger, "propagate", False)
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(DefaultFormatter("%(levelprefix)s %(message)s", use_colors=False))
    logger.addHandler(handler)
    try:
        logger.error("Exception in ASGI application\n", exc_info=error)
    finally:
        logger.removeHandler(handler)
    return output.getvalue()


def _raised(error: Exception) -> Exception:
    try:
        raise error
    except Exception as caught:
        return caught


def test_log_do_500_comum_com_erro_de_banco_nao_traz_canario(
    traced: Traced, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)

    traced.client.get(f"{TEST_ROUTES}/plain-fails-permanent")

    _assert_no_canary_in_logs(traced)


def test_log_do_erro_de_banco_mantem_o_tipo_e_os_frames(
    traced: Traced, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)

    traced.client.get(f"{TEST_ROUTES}/plain-fails-permanent")

    output = traced.log.getvalue()
    assert OPERATIONAL_ERROR_TYPE in output
    assert "plain_fails_permanent" in output
    [record] = [r for r in caplog.records if r.name.endswith(".problems")]
    assert record.exc_info is not None


@pytest.mark.parametrize(
    "route",
    ["interrupted", "plain-fails-outside-database"],
)
def test_excecao_que_nao_e_de_banco_continua_no_log_por_inteiro(
    traced: Traced, caplog: pytest.LogCaptureFixture, route: str
) -> None:
    caplog.set_level(logging.ERROR)

    traced.client.get(f"{TEST_ROUTES}/{route}")

    assert f"OSError: falha ao ler {traced.canaries.path}" in traced.log.getvalue()


def test_uvicorn_nao_escreve_o_detalhe_do_erro_de_banco(
    traced: Traced, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = _uvicorn_log(monkeypatch, _raised(traced.canaries.database_error()))

    assert [canary for canary in traced.canaries.all() if canary in output] == []
    assert OPERATIONAL_ERROR_TYPE in output
    assert "Exception in ASGI application" in output


def test_uvicorn_escreve_por_inteiro_a_excecao_que_nao_e_de_banco(
    traced: Traced, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = _uvicorn_log(monkeypatch, _raised(traced.canaries.storage_error()))

    assert f"OSError: falha ao ler {traced.canaries.path}" in output


def _wrapped_with_cause(canaries: Canaries) -> Exception:
    try:
        raise canaries.database_error()
    except OperationalError as error:
        try:
            raise RuntimeError("falha ao encerrar") from error
        except RuntimeError as wrapper:
            return wrapper


def _wrapped_with_context(canaries: Canaries) -> Exception:
    try:
        raise canaries.database_error()
    except OperationalError:
        try:
            raise RuntimeError("falha ao encerrar")
        except RuntimeError as wrapper:
            return wrapper


def _grouped(canaries: Canaries) -> Exception:
    return _raised(ExceptionGroup("grupo", [_raised(canaries.database_error())]))


def _nested_group(canaries: Canaries) -> Exception:
    inner = ExceptionGroup("dentro", [_raised(canaries.database_error())])
    return _raised(ExceptionGroup("fora", [inner]))


def _cycle(canaries: Canaries) -> Exception:
    database_error = _raised(canaries.database_error())
    other = ValueError(canaries.path)
    database_error.__context__ = other
    other.__context__ = database_error
    return database_error


@pytest.mark.parametrize(
    "build", [_wrapped_with_cause, _wrapped_with_context, _grouped, _nested_group, _cycle]
)
def test_erro_de_banco_em_qualquer_ponto_da_cadeia_tira_as_mensagens(
    traced: Traced,
    monkeypatch: pytest.MonkeyPatch,
    build: Callable[[Canaries], Exception],
) -> None:
    output = _uvicorn_log(monkeypatch, build(traced.canaries))

    assert [canary for canary in traced.canaries.all() if canary in output] == []
    assert OPERATIONAL_ERROR_TYPE in output


def test_excecao_que_descarta_o_erro_de_banco_com_from_none_fica_inteira(
    traced: Traced, monkeypatch: pytest.MonkeyPatch
) -> None:
    canaries = traced.canaries
    try:
        raise canaries.database_error()
    except OperationalError:
        try:
            raise ValueError(f"sem relação {canaries.path}") from None
        except ValueError as replaced:
            output = _uvicorn_log(monkeypatch, replaced)

    assert f"ValueError: sem relação {canaries.path}" in output
    assert canaries.sql not in output


# Log: o filtro vale para qualquer logger do processo


NON_API_LOGGERS = [
    "goblinshift.entrypoints.worker",
    "goblinshift.entrypoints.maintenance",
    "goblinshift.runs.worker.coordinator",
]


def _log_exception(logger_name: str, error: Exception) -> None:
    try:
        raise error
    except Exception:
        logging.getLogger(logger_name).exception("falha fora da requisição")


@pytest.mark.parametrize("logger_name", NON_API_LOGGERS)
def test_log_de_processo_fora_da_api_nao_traz_o_detalhe_do_erro_de_banco(
    traced: Traced, logger_name: str
) -> None:
    _log_exception(logger_name, traced.canaries.database_error())

    _assert_no_canary_in_logs(traced)
    assert OPERATIONAL_ERROR_TYPE in traced.log.getvalue()


@pytest.mark.parametrize("logger_name", NON_API_LOGGERS)
def test_log_de_processo_fora_da_api_mantem_excecao_que_nao_e_de_banco(
    traced: Traced, logger_name: str
) -> None:
    _log_exception(logger_name, ValueError(traced.canaries.path))

    assert f"ValueError: {traced.canaries.path}" in traced.log.getvalue()


def test_log_nao_traz_o_detalhe_de_erro_cru_do_psycopg(traced: Traced) -> None:
    _log_exception(NON_API_LOGGERS[0], PsycopgOperationalError(traced.canaries.host))

    _assert_no_canary_in_logs(traced)
    assert "OperationalError" in traced.log.getvalue()


def test_uvicorn_nao_escreve_o_detalhe_de_erro_cru_do_psycopg(
    traced: Traced, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = _uvicorn_log(monkeypatch, _raised(PsycopgOperationalError(traced.canaries.host)))

    assert traced.canaries.host not in output


def test_readyz_com_banco_fora_nao_loga_o_detalhe(
    traced: Traced, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail() -> None:
        raise traced.canaries.database_error()

    monkeypatch.setattr(traced.app.state.engine, "connect", fail)

    response = traced.client.get("/readyz")

    assert response.status_code == 503
    _assert_no_canary_in_logs(traced)
    assert OPERATIONAL_ERROR_TYPE in traced.log.getvalue()


def test_filtro_troca_o_texto_ja_em_cache_no_registro(traced: Traced) -> None:
    error = _raised(traced.canaries.database_error())
    record = logging.LogRecord("x", logging.ERROR, "x", 1, "m", None, (type(error), error, None))
    record.exc_text = traced.canaries.sql

    omit_database_error_detail(record)

    assert traced.canaries.sql not in (record.exc_text or "")


class _ConnectionWithRepr:
    def __init__(self, host: str) -> None:
        self._host = host

    def __repr__(self) -> str:
        return f"<psycopg.Connection [IDLE] (host={self._host} database=g) at 0x1>"


def test_mensagem_do_pool_nao_traz_o_repr_da_conexao(traced: Traced) -> None:
    connection = _ConnectionWithRepr(traced.canaries.host)
    pool_logger = logging.getLogger("sqlalchemy.pool.impl.QueuePool")

    pool_logger.error("Exception closing connection %r", connection)
    pool_logger.error(f"non-checked-in connection {connection!r}")

    output = traced.log.getvalue()
    assert traced.canaries.host not in output
    assert "Exception closing connection <psycopg connection>" in output


# Processos: a exceção que escapa de `serve` vai para o log, não para o `sys.excepthook`


@pytest.fixture
def restored_root_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers = root.handlers[:]
    level = root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def _offline_settings() -> Settings:
    return Settings(environment="test", db_port=1, db_connect_timeout_s=1)


def _connection_refused(canary: str) -> PsycopgOperationalError:
    return PsycopgOperationalError(f'connection to server at "{canary}", port 5432 failed')


@pytest.fixture
def worker_offline(monkeypatch: pytest.MonkeyPatch, restored_root_logging: None) -> None:
    settings = _offline_settings()
    monkeypatch.setattr(worker, "Settings", lambda: settings)
    monkeypatch.setattr(worker, "build_runner", lambda _settings: MagicMock())
    monkeypatch.setattr(worker, "shutdown_on_signals", threading.Event)


def _assert_failed_without_canary(
    exit_code: int, capsys: pytest.CaptureFixture[str], canary: str
) -> None:
    captured = capsys.readouterr()
    assert exit_code != 0
    assert canary not in captured.err + captured.out
    assert "OperationalError" in captured.err


@pytest.mark.usefixtures("worker_offline")
def test_worker_sem_banco_no_boot_sai_com_erro_e_sem_o_detalhe(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    canary = _new_canary()

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise _connection_refused(canary)

    monkeypatch.setattr("psycopg.connect", refuse)

    _assert_failed_without_canary(worker.main(), capsys, canary)


class _ListenerLosingTheConnection:
    """Listener que deixa o erro de banco escapar da espera.

    O `QueueListener` real absorve a queda da conexão e reconecta
    (tests/runs/test_queue_listener.py); este falso cobre a fronteira do processo para qualquer
    erro de banco que ainda chegue até ela.
    """

    def __init__(self, canary: str) -> None:
        self._canary = canary

    def __enter__(self) -> _ListenerLosingTheConnection:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def wait(self, _timeout_s: float) -> bool:
        raise _connection_refused(self._canary)


@pytest.mark.usefixtures("worker_offline")
def test_erro_de_banco_que_escapa_do_listener_sai_com_erro_e_sem_o_detalhe(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    canary = _new_canary()
    monkeypatch.setattr(
        worker, "QueueListener", lambda *_args: _ListenerLosingTheConnection(canary)
    )
    monkeypatch.setattr(worker, "_work_once", lambda *_args: False)

    _assert_failed_without_canary(worker.main(), capsys, canary)


@pytest.mark.usefixtures("worker_offline")
def test_worker_mantem_inteira_a_excecao_que_nao_e_de_banco(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    canary = _new_canary()

    def fail(*_args: object) -> None:
        raise ValueError(canary)

    monkeypatch.setattr(worker, "serve", fail)

    assert worker.main() != 0
    assert f"ValueError: {canary}" in capsys.readouterr().err


@pytest.mark.usefixtures("restored_root_logging")
def test_manutencao_que_falha_ao_subir_sai_com_erro_e_sem_o_detalhe(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    canary = _new_canary()
    settings = _offline_settings()

    def fail(*_args: object) -> None:
        raise _connection_refused(canary)

    monkeypatch.setattr(maintenance, "Settings", lambda: settings)
    monkeypatch.setattr(maintenance, "build_runner", lambda _settings: MagicMock())
    monkeypatch.setattr("signal.signal", lambda *_args: None)
    monkeypatch.setattr(maintenance, "FileStorage", fail)

    _assert_failed_without_canary(maintenance.main(), capsys, canary)


# Engine: `hide_parameters`


def test_engine_nao_poe_o_parametro_na_mensagem_do_erro() -> None:
    canary = _new_canary()
    engine = make_engine(Settings(environment="test", db_port=1, db_connect_timeout_s=1))

    # Como o `Connection` constrói o erro do driver, com o valor do engine.
    error = DBAPIError.instance(
        "SELECT 1 WHERE %(k)s IS NOT NULL",
        {"k": canary},
        PsycopgOperationalError("falhou"),
        PsycopgError,
        hide_parameters=engine.hide_parameters,
    )

    assert canary not in str(error)
    assert "SQL parameters hidden" in str(error)


@pytest.mark.integration
def test_erro_real_do_postgres_nao_traz_o_parametro(db_settings: Settings) -> None:
    canary = _new_canary()
    engine = make_engine(db_settings)

    try:
        with engine.connect() as conn, pytest.raises(DBAPIError) as caught:
            conn.execute(text("SELECT 1 / 0, CAST(:k AS text)"), {"k": canary})
    finally:
        engine.dispose()

    assert canary not in str(caught.value)
    assert "SQL parameters hidden" in str(caught.value)


# Processador: o que a redação preserva, e o span `attempt` do worker


def _provider_exporting_to(exporter: OtlpBytesExporter) -> TracerProvider:
    processor = make_span_processor(exporter)
    assert processor is not None
    provider = TracerProvider()
    provider.add_span_processor(processor)
    return provider


def test_span_sem_main_mantem_o_detalhe_da_excecao() -> None:
    exporter = OtlpBytesExporter()
    span = _provider_exporting_to(exporter).get_tracer("outra").start_span("x")

    span.record_exception(ValueError("detalhe"))
    span.end()

    (exported,) = exporter.spans
    (event,) = _exception_events(exported)
    assert event["exception.message"] == "detalhe"
    assert exported.status.status_code is StatusCode.UNSET


def test_span_main_preserva_links_eventos_comuns_e_instantes() -> None:
    exporter = OtlpBytesExporter()
    link = Link(SpanContext(1, 2, is_remote=True, trace_flags=TraceFlags(TraceFlags.SAMPLED)))
    tracer = _provider_exporting_to(exporter).get_tracer("x")
    span = tracer.start_span("x", attributes={"main": True}, links=[link])

    span.add_event("antes", {"k": 1}, timestamp=111)
    span.record_exception(ValueError("segredo"), timestamp=222)
    span.add_event("depois", timestamp=333)
    span.end()

    (exported,) = exporter.spans
    assert [(event.name, event.timestamp) for event in exported.events] == [
        ("antes", 111),
        ("exception", 222),
        ("depois", 333),
    ]
    assert dict(exported.events[0].attributes or {}) == {"k": 1}
    assert [item.context.span_id for item in exported.links] == [2]


def test_excecao_capturada_no_span_main_nao_inventa_erro_no_status() -> None:
    exporter = OtlpBytesExporter()
    span = (
        _provider_exporting_to(exporter).get_tracer("x").start_span("x", attributes={"main": True})
    )

    span.record_exception(ValueError("segredo"))
    span.end()

    (exported,) = exporter.spans
    assert exported.status.status_code is StatusCode.UNSET
    assert exported.status.description is None


def test_atributo_extra_do_evento_de_excecao_nao_sai() -> None:
    canary = _new_canary()
    exporter = OtlpBytesExporter()
    span = (
        _provider_exporting_to(exporter).get_tracer("x").start_span("x", attributes={"main": True})
    )

    span.record_exception(ValueError("segredo"), attributes={"extra": canary})
    span.end()

    assert canary.encode() not in bytes(exporter.payload)


def _claimed_attempt() -> ClaimedAttempt:
    return cast(
        ClaimedAttempt,
        SimpleNamespace(
            run_id=uuid.uuid4(),
            attempt_id=uuid.uuid4(),
            owner_id=uuid.uuid4(),
            number=1,
            generation=1,
            recipe_id="receita",
            recipe_version=1,
            input_upload_ids=(),
            queue_wait_ms=1.0,
            traceparent="00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
        ),
    )


def test_span_attempt_do_worker_sai_sem_o_detalhe(monkeypatch: pytest.MonkeyPatch) -> None:
    canaries = Canaries(_new_canary(), _new_canary(), _new_canary(), _new_canary())
    exporter = OtlpBytesExporter()
    tracer = _provider_exporting_to(exporter).get_tracer("goblinshift.worker")
    coordinator = Coordinator(MagicMock(), MagicMock(), MagicMock(), MagicMock(), tracer=tracer)

    def fail(_claimed: ClaimedAttempt) -> None:
        raise canaries.database_error()

    monkeypatch.setattr(coordinator, "_run_traced", fail)

    with pytest.raises(OperationalError):
        coordinator.run(_claimed_attempt())

    assert [c for c in canaries.all() if c.encode() in bytes(exporter.payload)] == []
    (attempt,) = exporter.spans
    assert attempt.name == attempt_event.SPAN_NAME
    assert len(attempt.links) == 1
    _assert_detail_redacted_keeping_type(attempt, OPERATIONAL_ERROR_TYPE)
