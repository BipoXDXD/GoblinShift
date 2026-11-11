"""Prazo das duas voltas do agendador e o diagnóstico do estouro (sem Docker)."""

from pathlib import Path

import pytest
import yaml

from tests.openobserve.scheduler_round import (
    JOB_OVERHEAD_S,
    heartbeat_report,
    scheduler_round_s,
    two_rounds_timeout_s,
)
from tests.openobserve.test_alert_firing import FIRING_TIMEOUT_S, scenario_catalogs

CI_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


@pytest.mark.parametrize(
    ("alert_count", "round_s"),
    [
        (38, 120.0),  # a medição de referência
        (57, 180.0),  # 50% mais alertas, 50% mais volta
        (19, 60.0),  # exatamente a frequência configurada
        (10, 60.0),  # poucos alertas: a volta não desce abaixo de 1 minuto
        (1, 60.0),
    ],
)
def test_volta_cresce_com_os_alertas_sem_descer_da_frequencia(
    alert_count: int, round_s: float
) -> None:
    assert scheduler_round_s(alert_count) == pytest.approx(round_s)


def test_prazo_cobre_duas_voltas_com_metade_de_folga() -> None:
    assert two_rounds_timeout_s(38) == pytest.approx(360.0)


def test_alerta_novo_aumenta_o_prazo() -> None:
    assert two_rounds_timeout_s(39) > two_rounds_timeout_s(38)


@pytest.mark.parametrize("alert_count", [0, -1])
def test_cenario_sem_alertas_e_recusado(alert_count: int) -> None:
    with pytest.raises(ValueError, match="pelo menos um alerta"):
        scheduler_round_s(alert_count)


def test_relatorio_mostra_prazo_instantes_e_voltas_observadas() -> None:
    # Marco em 1000 s; dois heartbeats antes dele e um depois, a 130 s e 125 s de distância.
    report = heartbeat_report([1095.0, 840.0, 970.0], after=1000.0, alert_count=38, timeout_s=360.0)

    assert "prazo de 360 s (fórmula: 2 voltas x 120 s x 1.5 = 360 s" in report
    assert "38 alertas x 3.16 s e mínimo de 60 s" in report
    assert "1 heartbeat(s) depois do marco" in report
    assert "relativos ao marco: -160.0 s, -30.0 s, +95.0 s" in report
    assert "intervalo entre heartbeats): 130.0 s, 125.0 s" in report


def test_relatorio_mostra_o_prazo_usado_mesmo_fora_da_formula() -> None:
    report = heartbeat_report([], after=1000.0, alert_count=38, timeout_s=1.0)

    assert report.startswith("prazo de 1 s (fórmula: 2 voltas x 120 s x 1.5 = 360 s")


def test_relatorio_sem_heartbeat_diz_nenhum() -> None:
    report = heartbeat_report([], after=1000.0, alert_count=38, timeout_s=360.0)

    assert "0 heartbeat(s) depois do marco" in report
    assert "relativos ao marco: nenhum" in report
    assert "intervalo entre heartbeats): nenhum" in report


def test_esperas_do_cenario_cabem_no_teto_do_job_do_ci() -> None:
    # Sem esta conta, um catálogo maior faz o Actions matar o job por timeout, sem o relatório
    # dos heartbeats. A contagem é a mesma que o cenário provisiona.
    timeout_minutes = yaml.safe_load(CI_WORKFLOW.read_text())["jobs"]["openobserve"][
        "timeout-minutes"
    ]
    job_ceiling_s = timeout_minutes * 60.0
    alert_count = sum(len(catalog) for _, catalog in scenario_catalogs("q", "n", "s"))
    worst_waits_s = FIRING_TIMEOUT_S + two_rounds_timeout_s(alert_count)

    assert worst_waits_s + JOB_OVERHEAD_S <= job_ceiling_s, (
        f"{alert_count} alertas: as esperas somam até {worst_waits_s:.0f} s"
        f" ({FIRING_TIMEOUT_S:.0f} s da queima + {two_rounds_timeout_s(alert_count):.0f} s das"
        f" duas voltas), mais {JOB_OVERHEAD_S:.0f} s de setup e outros testes, passam do teto de"
        f" {job_ceiling_s:.0f} s (timeout-minutes: {timeout_minutes}) do job openobserve;"
        " suba timeout-minutes no ci.yml ou reduza o catálogo do cenário"
    )
