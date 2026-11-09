"""Exceção de resposta interrompida não sai com detalhe no span `main` (GoblinShiftPlan.md, 3.7).

Sem banco: a exceção é construída com as classes reais do SQLAlchemy, e os bytes conferidos são os
que o `OTLPSpanExporter` enviaria, depois do mesmo processador da produção.
"""

import io
import logging
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
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from uvicorn.logging import DefaultFormatter

from goblinshift.entrypoints.api.app import create_app
from goblinshift.entrypoints.api.transfers import DownloadResponse, TransferTimeouts
from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from goblinshift.platform.telemetry import JsonFormatter, make_span_processor
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
    client: TestClient
    exporter: OtlpBytesExporter
    canaries: Canaries


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
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield Traced(client, exporter, canaries)
    finally:
        SQLAlchemyInstrumentor().uninstrument()


def _assert_no_canary_in_otlp(traced: Traced) -> None:
    payload = bytes(traced.exporter.payload)
    leaked = [canary for canary in traced.canaries.all() if canary.encode() in payload]
    assert leaked == []


def _assert_no_canary_in_logs(traced: Traced, caplog: pytest.LogCaptureFixture) -> None:
    formatter = JsonFormatter()
    output = "\n".join(formatter.format(record) for record in caplog.records)
    if not caplog.records:
        # Fora de `AssertionError`: sem log, o `xfail` do vazamento não pode absorver a falha.
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

    _assert_no_canary_in_logs(traced, caplog)


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

    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    _assert_no_canary_in_otlp(traced)
    assert traced.exporter.main_span().events == ()


# Log: erro de banco só com tipo e frames; o resto, por inteiro


def _json_log(caplog: pytest.LogCaptureFixture) -> str:
    formatter = JsonFormatter()
    return "\n".join(formatter.format(record) for record in caplog.records)


def _uvicorn_log(caplog: pytest.LogCaptureFixture, error: BaseException) -> str:
    """O que o Uvicorn escreve ao receber a exceção da aplicação, com o formatador dele."""
    logging.getLogger("uvicorn.error").error("Exception in ASGI application\n", exc_info=error)
    [record] = [r for r in caplog.records if r.name == "uvicorn.error"]
    return DefaultFormatter("%(levelprefix)s %(message)s", use_colors=False).format(record)


def _raised(error: Exception) -> Exception:
    try:
        raise error
    except Exception as caught:
        return caught


def test_log_do_500_comum_com_erro_de_banco_nao_traz_canario(
    traced: Traced, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)

    traced.client.get(f"{TEST_ROUTES}/plain-fails")

    _assert_no_canary_in_logs(traced, caplog)


def test_log_do_erro_de_banco_mantem_o_tipo_e_os_frames(
    traced: Traced, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)

    traced.client.get(f"{TEST_ROUTES}/plain-fails")

    output = _json_log(caplog)
    assert OPERATIONAL_ERROR_TYPE in output
    assert "raise_database_error" in output
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

    assert f"OSError: falha ao ler {traced.canaries.path}" in _json_log(caplog)


def test_uvicorn_nao_escreve_o_detalhe_do_erro_de_banco(
    traced: Traced, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)

    output = _uvicorn_log(caplog, _raised(traced.canaries.database_error()))

    assert [canary for canary in traced.canaries.all() if canary in output] == []
    assert OPERATIONAL_ERROR_TYPE in output
    assert "Exception in ASGI application" in output


def test_uvicorn_escreve_por_inteiro_a_excecao_que_nao_e_de_banco(
    traced: Traced, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)

    output = _uvicorn_log(caplog, _raised(traced.canaries.storage_error()))

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
            raise RuntimeError("falha ao encerrar")  # noqa: B904 - o teste exige o contexto implícito
        except RuntimeError as wrapper:
            return wrapper


def _grouped(canaries: Canaries) -> Exception:
    return _raised(ExceptionGroup("grupo", [_raised(canaries.database_error())]))


@pytest.mark.parametrize("build", [_wrapped_with_cause, _wrapped_with_context, _grouped])
def test_erro_de_banco_em_qualquer_ponto_da_cadeia_tira_as_mensagens(
    traced: Traced,
    caplog: pytest.LogCaptureFixture,
    build: Callable[[Canaries], Exception],
) -> None:
    caplog.set_level(logging.ERROR)

    output = _uvicorn_log(caplog, build(traced.canaries))

    assert [canary for canary in traced.canaries.all() if canary in output] == []
    assert OPERATIONAL_ERROR_TYPE in output


def test_excecao_que_descarta_o_erro_de_banco_com_from_none_fica_inteira(
    traced: Traced, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)
    canaries = traced.canaries
    try:
        raise canaries.database_error()
    except OperationalError:
        try:
            raise ValueError(f"sem relação {canaries.path}") from None
        except ValueError as replaced:
            output = _uvicorn_log(caplog, replaced)

    assert f"ValueError: sem relação {canaries.path}" in output
    assert canaries.sql not in output


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

    with engine.connect() as conn, pytest.raises(DBAPIError) as caught:
        conn.execute(text("SELECT 1 / 0, CAST(:k AS text)"), {"k": canary})

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
