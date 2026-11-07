"""Eventos `alert=` do log viram contadores no span corrente (docs/GoblinShiftPlan.md, 3.7).

Os logs não saem da VPS: o que os alertas leem são os atributos dos spans. Sem banco: os spans vão
para um `InMemorySpanExporter`.
"""

import logging
from collections.abc import Iterator

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from goblinshift.platform.telemetry import AlertEventCounter, configure_logging

logger = logging.getLogger("tests.alert_events")


def alert_event(name: str) -> None:
    logger.warning("evento", extra={"fields": {"alert": name, "upload_id": "nao-exportar"}})


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def provider(exporter: InMemorySpanExporter) -> TracerProvider:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider


@pytest.fixture
def counting() -> Iterator[None]:
    handler = AlertEventCounter()
    logger.addHandler(handler)
    yield
    logger.removeHandler(handler)


@pytest.mark.usefixtures("counting")
def test_cada_evento_soma_no_atributo_do_span_corrente(
    provider: TracerProvider, exporter: InMemorySpanExporter
) -> None:
    with provider.get_tracer("t").start_as_current_span("volta"):
        alert_event("capacity_divergence")
        alert_event("capacity_divergence")
        alert_event("capacity_over_max")

    [span] = exporter.get_finished_spans()
    attributes = dict(span.attributes or {})
    assert attributes["alert.capacity_divergence"] == 2
    assert attributes["alert.capacity_over_max"] == 1


@pytest.mark.usefixtures("counting")
def test_so_a_contagem_sai_os_campos_do_evento_ficam_no_log(
    provider: TracerProvider, exporter: InMemorySpanExporter
) -> None:
    with provider.get_tracer("t").start_as_current_span("volta"):
        alert_event("upload_without_reservation")

    [span] = exporter.get_finished_spans()
    assert dict(span.attributes or {}) == {"alert.upload_without_reservation": 1}


@pytest.mark.usefixtures("counting")
def test_log_sem_o_campo_alert_nao_conta(
    provider: TracerProvider, exporter: InMemorySpanExporter
) -> None:
    with provider.get_tracer("t").start_as_current_span("volta"):
        logger.warning("sem campos")
        logger.warning("outros campos", extra={"fields": {"run_id": "x"}})

    [span] = exporter.get_finished_spans()
    assert dict(span.attributes or {}) == {}


@pytest.mark.usefixtures("counting")
def test_evento_fora_de_um_span_nao_derruba_quem_registra() -> None:
    alert_event("run_without_reservation")


@pytest.fixture
def restored_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.mark.usefixtures("restored_logging")
def test_a_configuracao_dos_processos_liga_o_contador(
    provider: TracerProvider, exporter: InMemorySpanExporter
) -> None:
    configure_logging()

    with provider.get_tracer("t").start_as_current_span("volta"):
        alert_event("missing_sweep_failed")

    [span] = exporter.get_finished_spans()
    assert dict(span.attributes or {}) == {"alert.missing_sweep_failed": 1}
