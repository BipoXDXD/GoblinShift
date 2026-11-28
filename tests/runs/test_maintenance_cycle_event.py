"""Span `maintenance.cycle`: um evento amplo por ciclo da manutenção (GoblinShiftPlan.md, 3.7).

Sem banco: os ciclos são funções falsas, e o span vai para um `InMemorySpanExporter`.
"""

import io
import json
import logging
from collections.abc import Callable, Iterator

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from goblinshift.entrypoints.maintenance import (
    ACCOUNT_STEPS_FAILED,
    CYCLE_SPAN,
    BacklogTrend,
    run_cycles,
)
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


@pytest.mark.parametrize(
    ("series", "expected"),
    [([5, 6, 7], 2), ([5, 6, 7, 8], 3), ([5, 6, 6, 7], 1), ([5, 4, 5, 6], 2), ([], 0)],
)
def test_tendencia_conta_voltas_seguidas_de_crescimento(series: list[int], expected: int) -> None:
    trend = BacklogTrend()
    result = 0
    for value in series:
        result = trend.observe({"runs_to_delete": value})

    assert result == expected


def test_chave_ausente_na_volta_zera_a_sequencia() -> None:
    # Volta sem a contagem (o passo falhou): não dá para dizer que cresceu.
    trend = BacklogTrend()
    for value in (1, 2, 3):
        trend.observe({"runs_to_delete": value})

    assert trend.observe({}) == 0
    assert trend.observe({"runs_to_delete": 4}) == 0


def test_tendencia_devolve_a_maior_sequencia_entre_os_acumulos() -> None:
    trend = BacklogTrend()
    for runs, uploads in ((1, 9), (2, 8), (3, 9)):
        result = trend.observe({"runs_to_delete": runs, "uploads_to_delete": uploads})

    assert result == 2


def test_volta_grava_a_tendencia_no_span(
    provider: TracerProvider, exporter: InMemorySpanExporter
) -> None:
    trend = BacklogTrend()
    for value in (1, 2, 3, 4):
        run_cycles(
            provider.get_tracer("t"),
            (("execuções", lambda value=value: {"runs_to_delete": value}),),
            trend=trend,
        )

    spans = exporter.get_finished_spans()
    growing = [
        dict(span.attributes or {})["maintenance.retention_backlog_growing"] for span in spans
    ]
    assert growing == [0, 1, 2, 3]


def test_tres_voltas_de_acumulo_logam_o_alerta_com_a_chave(
    provider: TracerProvider, listen: Callable[[logging.Logger], io.StringIO]
) -> None:
    # A linha formatada pelo JsonFormatter de produção: campo fora da allowlist sairia descartado.
    stream = listen(logging.getLogger("goblinshift.maintenance"))
    trend = BacklogTrend()
    for value in (1, 2, 3, 4):
        run_cycles(
            provider.get_tracer("t"),
            (("execuções", lambda value=value: {"uploads_to_delete": value}),),
            trend=trend,
        )

    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    [only] = [line for line in lines if line.get("alert") == "retention_backlog_growing"]
    assert only["backlogs"] == ["uploads_to_delete"]
    assert only["cycles"] == 3


def test_janelas_do_limite_de_requisicoes_nao_contam_como_acumulo() -> None:
    # `request_windows` conta as janelas vivas: cresce com o tráfego, não com a retenção atrasada.
    trend = BacklogTrend()
    for value in (10, 20, 30, 40):
        result = trend.observe({"request_windows": value})

    assert result == 0
