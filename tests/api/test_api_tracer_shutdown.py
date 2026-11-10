"""A parada da API encerra o tracer provider e exporta o lote pendente, dentro de um prazo curto.

Sem banco: a API sobe com o `BatchSpanProcessor` de produção, com o exportador OTLP trocado por um
em memória, e um atraso de lote longo o bastante para o span ficar na fila até a parada.
"""

import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.trace.export import SpanExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from goblinshift.entrypoints.api.app import create_app
from goblinshift.platform import telemetry
from goblinshift.platform.settings import Settings
from tests.span_exporters import (
    BATCH_DELAY_MS,
    SHORT_SHUTDOWN_TIMEOUT_S,
    BrokenShutdownExporter,
    StuckExporter,
)
from tests.test_api_server_limits import LIFESPAN_SHUTDOWN_MARGIN_SECONDS

TRACED_PATH = "/openapi.json"
SHUTDOWN_SPAN = "passo anterior da parada"


@pytest.fixture
def otlp(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://coletor.invalid")
    monkeypatch.setenv("OTEL_BSP_SCHEDULE_DELAY", BATCH_DELAY_MS)
    try:
        yield monkeypatch
    finally:
        SQLAlchemyInstrumentor().uninstrument()


def _api_with(otlp: pytest.MonkeyPatch, exporter: SpanExporter) -> TestClient:
    otlp.setattr(telemetry, "OTLPSpanExporter", lambda: exporter)
    app = create_app(Settings(environment="test", db_port=1, db_connect_timeout_s=1))
    return TestClient(app)


def _span_names(exporter: InMemorySpanExporter) -> list[str]:
    return [span.name for span in exporter.get_finished_spans()]


def test_parada_da_api_exporta_o_span_que_ficou_na_fila(otlp: pytest.MonkeyPatch) -> None:
    exporter = InMemorySpanExporter()

    with _api_with(otlp, exporter) as client:
        assert client.get(TRACED_PATH).status_code == 200
        assert _span_names(exporter) == []

    assert f"GET {TRACED_PATH}" in _span_names(exporter)


def test_parada_da_api_nao_espera_o_collector_alem_do_prazo(otlp: pytest.MonkeyPatch) -> None:
    exporter = StuckExporter()
    client = _api_with(otlp, exporter)
    try:
        with client:
            client.get(TRACED_PATH)
            started = time.monotonic()
        elapsed = time.monotonic() - started
    finally:
        exporter.gate.set()

    assert elapsed < LIFESPAN_SHUTDOWN_MARGIN_SECONDS


def test_prazo_estourado_no_encerramento_do_tracing_fica_no_log(
    otlp: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    otlp.setattr(telemetry, "TRACING_SHUTDOWN_TIMEOUT_S", SHORT_SHUTDOWN_TIMEOUT_S)
    exporter = StuckExporter()
    client = _api_with(otlp, exporter)
    try:
        with client:
            client.get(TRACED_PATH)
    finally:
        exporter.gate.set()

    assert "encerramento do tracing passou do prazo" in capsys.readouterr().err


def test_parada_da_api_encerra_o_tracing_depois_dos_outros_passos(
    otlp: pytest.MonkeyPatch,
) -> None:
    exporter = InMemorySpanExporter()
    client = _api_with(otlp, exporter)
    app_state = client.app.state  # type: ignore[attr-defined]
    dispose = app_state.engine.dispose

    def dispose_emitting_span() -> None:
        # Representa qualquer passo da parada que ainda emite span antes do encerramento.
        with app_state.tracing.tracer.start_as_current_span(SHUTDOWN_SPAN):
            dispose()

    otlp.setattr(app_state.engine, "dispose", dispose_emitting_span)

    with client:
        pass

    assert SHUTDOWN_SPAN in _span_names(exporter)


def test_falha_ao_encerrar_o_tracing_nao_derruba_a_parada(
    otlp: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    exporter = BrokenShutdownExporter()

    with _api_with(otlp, exporter) as client:
        client.get(TRACED_PATH)

    assert f"GET {TRACED_PATH}" in _span_names(exporter)
    # `configure_logging` escreve JSON no stderr, sem propagar para o `caplog`.
    assert "encerramento do tracing falhou" in capsys.readouterr().err
