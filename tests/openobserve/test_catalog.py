"""Catálogo de SLOs e alertas (docs/GoblinShiftPlan.md, 3.7), sem OpenObserve."""

import re

import pytest

from openobserve.catalog import (
    BURN_LEVELS,
    CAUSE_COLUMNS,
    SLOS,
    Alert,
    DestinationKey,
    Slo,
    alerts,
    slo_named,
)
from openobserve.provision import TRACE_COLUMNS

PREFIX = "gs-"


def test_os_tres_slos_do_plano_com_as_metas() -> None:
    targets = {slo.name: 1 - 1 / slo.budget_denominator for slo in SLOS}

    assert targets == pytest.approx({"aceitacao": 0.995, "execucao": 0.99, "download": 0.995})


@pytest.mark.parametrize("slo", SLOS, ids=lambda slo: slo.name)
@pytest.mark.parametrize(("level", "burn_rate"), BURN_LEVELS)
def test_uma_falha_sozinha_nunca_dispara_a_queima(
    slo: Slo, level: DestinationKey, burn_rate: int
) -> None:
    # A queima dispara com bad * N >= taxa * elegíveis. Com o mínimo de eventos na janela longa,
    # um único evento ruim não pode chegar lá (3.7: "uma falha em vinte requisições não pode
    # acordar ninguém").
    assert 1 * slo.budget_denominator < burn_rate * slo.min_events


def test_niveis_de_queima_sao_aviso_em_2x_e_urgente_em_4x() -> None:
    assert dict(BURN_LEVELS) == {DestinationKey.WARNING: 2, DestinationKey.URGENT: 4}


def test_janela_curta_exige_um_quarto_do_minimo_da_longa_arredondado_para_cima() -> None:
    assert (slo_named("aceitacao").min_events, slo_named("aceitacao").min_events_short) == (101, 26)
    assert (slo_named("execucao").min_events, slo_named("execucao").min_events_short) == (51, 13)


def test_catalogo_tem_queima_por_slo_e_nivel_causas_e_heartbeat() -> None:
    names = {alert.name for alert in alerts(stream="default", prefix=PREFIX)}

    assert names == {
        "gs-slo-aceitacao-aviso",
        "gs-slo-aceitacao-urgente",
        "gs-slo-execucao-aviso",
        "gs-slo-execucao-urgente",
        "gs-slo-download-aviso",
        "gs-slo-download-urgente",
        "gs-causa-fila-cheia",
        "gs-causa-disco",
        "gs-causa-execucao-presa",
        "gs-causa-limitador-de-exclusoes",
        "gs-causa-exclusao-de-conta-atrasada",
        "gs-causa-capacidade-global",
        "gs-heartbeat",
    }


def test_queima_urgente_vai_ao_destino_urgente_e_aviso_ao_de_aviso() -> None:
    by_name = {alert.name: alert for alert in alerts(stream="default", prefix=PREFIX)}

    assert by_name["gs-slo-download-urgente"].destination is DestinationKey.URGENT
    assert by_name["gs-slo-download-aviso"].destination is DestinationKey.WARNING
    assert by_name["gs-heartbeat"].destination is DestinationKey.HEARTBEAT


def test_queima_avalia_a_janela_de_uma_hora() -> None:
    burn = next(a for a in alerts(stream="default", prefix=PREFIX) if a.name.endswith("urgente"))

    assert burn.period_minutes == 60


@pytest.mark.parametrize("alert", alerts(stream="trafego", prefix=PREFIX), ids=lambda a: a.name)
def test_sql_renderizado_sem_marcador_nem_comentario(alert: Alert) -> None:
    assert "$" not in alert.sql
    assert "--" not in alert.sql
    # Alertas agendados do OpenObserve recusam SELECT * (ADR 013).
    assert not re.search(r"select\s+\*", alert.sql, re.IGNORECASE)


def test_sql_de_traces_le_o_stream_pedido() -> None:
    traces = [a for a in alerts(stream="trafego", prefix=PREFIX) if a.stream_type == "traces"]

    assert traces
    for alert in traces:
        assert alert.stream_name == "trafego"
        assert 'FROM "trafego"' in alert.sql


def test_alerta_de_disco_le_o_stream_de_metricas() -> None:
    disk = next(a for a in alerts(stream="trafego", prefix=PREFIX) if a.name == "gs-causa-disco")

    assert (disk.stream_type, disk.stream_name) == ("metrics", "system_filesystem_utilization")


def test_heartbeat_le_as_metricas_que_chegam_mesmo_sem_trafego() -> None:
    by_name = {a.name: a for a in alerts(stream="trafego", prefix=PREFIX)}

    heartbeat = by_name["gs-heartbeat"]
    assert (heartbeat.stream_type, heartbeat.stream_name) == (
        "metrics",
        "system_filesystem_utilization",
    )


@pytest.mark.parametrize("slo", SLOS, ids=lambda slo: slo.name)
def test_colunas_declaradas_aparecem_no_sql_do_slo(slo: Slo) -> None:
    sql = slo.events_sql("default")

    for column in slo.columns:
        assert re.search(rf"\b{column}\b", sql), column


def test_prefixo_nomeia_todos_os_alertas() -> None:
    assert all(a.name.startswith("teste-") for a in alerts(stream="s", prefix="teste-"))


@pytest.mark.parametrize("alert", alerts(stream="s", prefix=PREFIX), ids=lambda a: a.name)
def test_descricao_cabe_no_json_do_template_sem_escape(alert: Alert) -> None:
    # O template do webhook põe a descrição dentro de uma string JSON, sem escapar.
    assert not set(alert.description) & {'"', "\\", "\n", "\r", "\t"}


def test_alertas_da_manutencao_leem_o_span_do_ciclo_no_stream_de_traces() -> None:
    names = {
        "gs-causa-execucao-presa",
        "gs-causa-limitador-de-exclusoes",
        "gs-causa-exclusao-de-conta-atrasada",
        "gs-causa-capacidade-global",
    }
    chosen = [a for a in alerts(stream="s", prefix=PREFIX) if a.name in names]

    assert len(chosen) == len(names)
    for alert in chosen:
        assert (alert.stream_type, alert.stream_name) == ("traces", "s"), alert.name
        assert "operation_name = 'maintenance.cycle'" in alert.sql, alert.name


def test_colunas_dos_alertas_da_manutencao_sao_declaradas() -> None:
    for column in CAUSE_COLUMNS:
        assert column in TRACE_COLUMNS
    assert "maintenance_runs_stuck_stopping" in CAUSE_COLUMNS
