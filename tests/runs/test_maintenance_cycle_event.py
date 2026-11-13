"""Span `maintenance.cycle`: um evento amplo por ciclo da manutenção (GoblinShiftPlan.md, 3.7).

Sem banco: os ciclos são funções falsas, e o span vai para um `InMemorySpanExporter`.
"""

import logging
from collections.abc import Iterator

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from goblinshift.entrypoints.maintenance import ACCOUNT_STEPS_FAILED, CYCLE_SPAN, run_cycles
from goblinshift.platform.telemetry import AlertEventCounter
from goblinshift.runs.maintenance import STEPS_FAILED

logger = logging.getLogger("tests.maintenance_cycle_event")


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


def test_ciclo_com_passo_falho_conta_como_falha_e_mantem_as_contagens(
    provider: TracerProvider, exporter: InMemorySpanExporter
) -> None:
    run_cycles(
        provider.get_tracer("t"),
        (
            ("execuções", lambda: {"deadlines": 2, STEPS_FAILED: 1}),
            ("contas", lambda: {"accounts_purged": 1}),
        ),
    )

    [span] = exporter.get_finished_spans()
    attributes = dict(span.attributes or {})
    # O mesmo caminho do ciclo que levanta: a causa `gs-causa-ciclos-falhando` lê isto.
    assert attributes["maintenance.failed_cycles"] == 1
    assert attributes["maintenance.steps_failed"] == 1
    assert attributes["maintenance.deadlines"] == 2
    assert span.status.status_code is StatusCode.ERROR


def test_ciclo_com_zero_passos_falhos_nao_conta_como_falha(
    provider: TracerProvider, exporter: InMemorySpanExporter
) -> None:
    run_cycles(provider.get_tracer("t"), (("execuções", lambda: {STEPS_FAILED: 0}),))

    [span] = exporter.get_finished_spans()
    assert dict(span.attributes or {})["maintenance.failed_cycles"] == 0
    assert span.status.status_code is StatusCode.UNSET


def test_parada_pedida_nao_comeca_o_ciclo_seguinte(
    provider: TracerProvider, exporter: InMemorySpanExporter
) -> None:
    ran: list[str] = []

    def executions() -> dict[str, int]:
        ran.append("execuções")
        return {"deadlines": 1}

    def accounts() -> dict[str, int]:
        ran.append("contas")
        return {}

    run_cycles(
        provider.get_tracer("t"),
        (("execuções", executions), ("contas", accounts)),
        should_stop=lambda: bool(ran),
    )

    assert ran == ["execuções"]
    [span] = exporter.get_finished_spans()
    attributes = dict(span.attributes or {})
    assert attributes["maintenance.deadlines"] == 1
    assert attributes["maintenance.failed_cycles"] == 0


@pytest.fixture
def counting(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # `create_app` deixa outro contador no logger raiz (configure_logging), e quase toda a suíte
    # cria a app: sem cortar a propagação, cada evento seria contado duas vezes.
    monkeypatch.setattr(logger, "propagate", False)
    handler = AlertEventCounter()
    logger.addHandler(handler)
    yield
    logger.removeHandler(handler)


@pytest.mark.usefixtures("counting")
def test_evento_de_alerta_de_um_ciclo_que_depois_falha_chega_ao_span_da_volta(
    provider: TracerProvider, exporter: InMemorySpanExporter
) -> None:
    def broken() -> dict[str, int]:
        logger.warning("divergente", extra={"fields": {"alert": "capacity_divergence"}})
        raise RuntimeError("banco fora")

    run_cycles(provider.get_tracer("t"), (("execuções", broken),))

    [span] = exporter.get_finished_spans()
    assert dict(span.attributes or {})["alert.capacity_divergence"] == 1


def test_ciclo_de_contas_com_passo_falho_conta_como_falha_sem_apagar_o_de_execucoes(
    provider: TracerProvider, exporter: InMemorySpanExporter
) -> None:
    run_cycles(
        provider.get_tracer("t"),
        (
            ("execuções", lambda: {STEPS_FAILED: 0}),
            ("contas", lambda: {"login_states_removed": 1, ACCOUNT_STEPS_FAILED: 1}),
        ),
    )

    [span] = exporter.get_finished_spans()
    attributes = dict(span.attributes or {})
    assert attributes["maintenance.failed_cycles"] == 1
    # Cada ciclo com a própria contagem: a de contas não sobrescreve a de execuções.
    assert attributes["maintenance.steps_failed"] == 0
    assert attributes["maintenance.account_steps_failed"] == 1
    assert attributes["maintenance.login_states_removed"] == 1
    assert span.status.status_code is StatusCode.ERROR
