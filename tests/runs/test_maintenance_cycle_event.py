"""Span `maintenance.cycle`: um evento amplo por ciclo da manutenção (GoblinShiftPlan.md, 3.7).

Sem banco: os ciclos são funções falsas, e o span vai para um `InMemorySpanExporter`.
"""

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from goblinshift.entrypoints.maintenance import CYCLE_SPAN, run_cycles


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def provider(exporter: InMemorySpanExporter) -> TracerProvider:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider


def test_um_span_main_com_as_contagens_dos_dois_ciclos(
    provider: TracerProvider, exporter: InMemorySpanExporter
) -> None:
    run_cycles(
        provider.get_tracer("t"),
        (("execuções", lambda: {"runs_deleted": 3}), ("contas", lambda: {"accounts_purged": 1})),
    )

    [span] = exporter.get_finished_spans()
    assert span.name == CYCLE_SPAN == "maintenance.cycle"
    attributes = dict(span.attributes or {})
    assert attributes["main"] is True
    assert attributes["maintenance.runs_deleted"] == 3
    assert attributes["maintenance.accounts_purged"] == 1
    assert attributes["maintenance.failed_cycles"] == 0
    assert isinstance(attributes["duration_ms"], int)
    assert span.status.status_code is StatusCode.UNSET


def test_ciclo_que_falha_nao_impede_o_outro_e_marca_o_span(
    provider: TracerProvider, exporter: InMemorySpanExporter
) -> None:
    def broken() -> dict[str, int]:
        raise RuntimeError("banco fora")

    run_cycles(
        provider.get_tracer("t"),
        (("execuções", broken), ("contas", lambda: {"accounts_purged": 2})),
    )

    [span] = exporter.get_finished_spans()
    attributes = dict(span.attributes or {})
    assert attributes["maintenance.accounts_purged"] == 2
    assert attributes["maintenance.failed_cycles"] == 1
    assert span.status.status_code is StatusCode.ERROR
    # A mensagem da exceção pode trazer valores do banco: o evento leva só o nome do ciclo.
    assert "banco fora" not in str(span.status.description)
    assert not span.events
