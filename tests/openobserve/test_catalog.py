"""Catálogo de SLOs e alertas (docs/GoblinShiftPlan.md, 3.7), sem OpenObserve."""

import re
from pathlib import Path

import pytest

from goblinshift.entrypoints.api.saturation import REJECTED_ATTRIBUTE, SATURATION_SPAN
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
SRC = Path(__file__).resolve().parents[2] / "src" / "goblinshift"

ABSENCE_ALERTS = ("gs-causa-manutencao-sem-ciclo", "gs-causa-sem-traces")
# As sondas da aceitação param a manutenção por até 20 min, com a API sem tráfego (ADR 017).
ACCEPTANCE_SUSPENSION_MINUTES = 20

# Cada evento `alert=` do log e o alerta que o torna visível fora da VPS.
LOG_EVENT_ALERTS = {
    "account_deletion_overdue": "gs-causa-exclusao-de-conta-atrasada",
    "capacity_divergence": "gs-causa-capacidade-divergente",
    "capacity_over_max": "gs-causa-capacidade-divergente",
    "executor_start_failed": "gs-causa-executor-nao-iniciou",
    "missing_governor": "gs-causa-limitador-de-ausentes",
    "missing_sweep_failed": "gs-causa-varredura-falhou",
    "objects_missing": "gs-causa-objetos-ausentes",
    "objects_unreadable": "gs-causa-objetos-ilegiveis",
    "purge_file_not_removed": "gs-causa-arquivos-nao-removidos",
    "purge_governor": "gs-causa-limitador-de-exclusoes",
    "run_without_reservation": "gs-causa-sem-reserva",
    "storage_unreachable": "gs-causa-storage-sem-sentinela",
    "upload_without_reservation": "gs-causa-sem-reserva",
}
# Evento que a causa lê pela contagem do `maintenance.cycle`, não por `alert_<evento>`. Só cabe a
# quem é emitido dentro da manutenção: fora dela, a contagem do ciclo não enxerga o evento.
CYCLE_COUNTERS = {
    "account_deletion_overdue": "maintenance_accounts_deletion_overdue",
    "missing_governor": "maintenance_missing_governor_tripped",
    "missing_sweep_failed": "maintenance_missing_sweep_failed",
    "objects_missing": "maintenance_objects_marked_missing",
    "objects_unreadable": "maintenance_objects_missing_unreadable",
    "purge_governor": "maintenance_purge_limit_reached",
    "storage_unreachable": "maintenance_missing_sweep_skipped",
}
MAINTENANCE_MODULES = {"entrypoints/maintenance.py", "runs/maintenance.py"}


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
        "gs-causa-storage-sem-sentinela",
        "gs-causa-limitador-de-ausentes",
        "gs-causa-objetos-ausentes",
        "gs-causa-objetos-ilegiveis",
        "gs-causa-arquivos-nao-removidos",
        "gs-causa-varredura-falhou",
        "gs-causa-ciclos-falhando",
        "gs-causa-capacidade-divergente",
        "gs-causa-sem-reserva",
        "gs-causa-executor-nao-iniciou",
        "gs-causa-manutencao-sem-ciclo",
        "gs-causa-sem-traces",
        "gs-causa-api-saturada",
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
        "gs-causa-storage-sem-sentinela",
        "gs-causa-limitador-de-ausentes",
        "gs-causa-objetos-ausentes",
        "gs-causa-objetos-ilegiveis",
        "gs-causa-arquivos-nao-removidos",
        "gs-causa-varredura-falhou",
        "gs-causa-ciclos-falhando",
        "gs-causa-manutencao-sem-ciclo",
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


@pytest.mark.parametrize("log", ["executor não iniciou", "área de trabalho falhou"])
def test_aviso_do_executor_que_nao_inicia_cita_o_log_de_cada_causa(log: str) -> None:
    # Start recusado e área de trabalho não criada levam ao mesmo alerta: a notificação diz qual
    # log procurar, com os nomes do runbook e do log que o coordenador emite.
    alert = next(
        a for a in alerts(stream="s", prefix=PREFIX) if a.name == "gs-causa-executor-nao-iniciou"
    )
    emitter = (SRC / "runs" / "worker" / "coordinator.py").read_text(encoding="utf-8")

    assert f"log {log}" in alert.description
    assert f'"{log}"' in emitter


def test_executor_que_nao_inicia_avisa_no_destino_urgente_lendo_o_span_da_tentativa() -> None:
    alert = next(
        a for a in alerts(stream="s", prefix=PREFIX) if a.name == "gs-causa-executor-nao-iniciou"
    )

    assert alert.destination is DestinationKey.URGENT
    assert (alert.stream_type, alert.stream_name) == ("traces", "s")
    assert "alert_executor_start_failed" in CAUSE_COLUMNS
    assert "operation_name = 'attempt'" in alert.sql
    assert (alert.period_minutes, alert.frequency_minutes, alert.silence_minutes) == (10, 5, 60)


def test_causas_da_varredura_de_ausentes_vao_ao_destino_de_aviso() -> None:
    by_name = {alert.name: alert for alert in alerts(stream="default", prefix=PREFIX)}

    for name in (
        "gs-causa-storage-sem-sentinela",
        "gs-causa-limitador-de-ausentes",
        "gs-causa-objetos-ausentes",
        "gs-causa-objetos-ilegiveis",
        "gs-causa-arquivos-nao-removidos",
        "gs-causa-varredura-falhou",
    ):
        assert by_name[name].destination is DestinationKey.WARNING, name


def test_contadores_lidos_pelas_causas_sao_declarados() -> None:
    # Coluna fora de CAUSE_COLUMNS não existe no stream quando o provisionamento valida a consulta.
    for alert in alerts(stream="s", prefix=PREFIX):
        for column in re.findall(r"\b(?:maintenance|alert|api)_\w+", alert.sql):
            assert column in CAUSE_COLUMNS, f"{alert.name}: {column}"


def modules_emitting_log_events() -> dict[str, set[str]]:
    modules: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        for event in _log_events(path.read_text(encoding="utf-8")):
            modules.setdefault(event, set()).add(path.relative_to(SRC).as_posix())
    return modules


def _log_events(source: str) -> set[str]:
    # O evento vem como literal ou como constante do próprio módulo (`"alert": NOME`).
    constants = dict(re.findall(r'^([A-Z_]+) = "(\w+)"$', source, flags=re.MULTILINE))
    events = set(re.findall(r'"alert": "(\w+)"', source))
    events |= {constants[name] for name in re.findall(r'"alert": ([A-Z_]+)\b', source)}
    return events


def test_todo_evento_de_alerta_do_log_tem_um_alerta_no_catalogo() -> None:
    # Os logs não são exportados: evento novo sem alerta fica invisível fora da VPS.
    names = {alert.name for alert in alerts(stream="s", prefix=PREFIX)}

    assert set(modules_emitting_log_events()) == set(LOG_EVENT_ALERTS)
    assert set(LOG_EVENT_ALERTS.values()) <= names


@pytest.mark.parametrize(("event", "name"), LOG_EVENT_ALERTS.items(), ids=lambda value: value)
def test_o_alerta_le_o_proprio_evento(event: str, name: str) -> None:
    by_name = {alert.name: alert for alert in alerts(stream="s", prefix=PREFIX)}
    sql = by_name[name].sql

    if f"alert_{event}" in sql:
        return
    assert CYCLE_COUNTERS[event] in sql
    assert modules_emitting_log_events()[event] <= MAINTENANCE_MODULES


@pytest.mark.parametrize("name", ABSENCE_ALERTS)
def test_ausencia_tolera_a_suspensao_da_manutencao_pela_aceitacao(name: str) -> None:
    by_name = {alert.name: alert for alert in alerts(stream="s", prefix=PREFIX)}

    assert by_name[name].period_minutes > ACCEPTANCE_SUSPENSION_MINUTES


@pytest.mark.parametrize(
    "name", (*ABSENCE_ALERTS, "gs-causa-ciclos-falhando"), ids=lambda name: name
)
def test_o_que_cala_os_outros_alertas_vai_ao_destino_urgente(name: str) -> None:
    by_name = {alert.name: alert for alert in alerts(stream="s", prefix=PREFIX)}

    assert by_name[name].destination is DestinationKey.URGENT


@pytest.mark.parametrize("slo", SLOS, ids=lambda slo: slo.name)
def test_sli_e_queima_so_contam_o_evento_amplo(slo: Slo) -> None:
    # A API também exporta um span por consulta ao banco, no mesmo stream.
    assert "main = 'true'" in slo.sli_sql("s")
    assert "main = 'true'" in slo.burn_sql("s", 14)


def test_todo_alerta_sobre_traces_so_conta_o_evento_amplo() -> None:
    trace_alerts = [
        alert for alert in alerts(stream="s", prefix=PREFIX) if alert.stream_type == "traces"
    ]

    assert trace_alerts
    for alert in trace_alerts:
        assert "main = 'true'" in alert.sql, alert.name


@pytest.mark.parametrize("name", ABSENCE_ALERTS)
def test_ausencia_le_o_stream_de_traces_e_dispara_sem_nenhuma_linha(name: str) -> None:
    by_name = {alert.name: alert for alert in alerts(stream="s", prefix=PREFIX)}

    alert = by_name[name]
    assert (alert.stream_type, alert.stream_name) == ("traces", "s")
    assert "HAVING count(*) = 0" in alert.sql
    # A semente do provisionamento (`main = false`) não pode contar como telemetria viva.
    assert "main = 'true'" in alert.sql


def test_api_saturada_le_o_span_da_janela_e_avisa_no_destino_urgente() -> None:
    alert = next(a for a in alerts(stream="s", prefix=PREFIX) if a.name == "gs-causa-api-saturada")

    assert alert.destination is DestinationKey.URGENT
    assert (alert.stream_type, alert.stream_name) == ("traces", "s")
    assert "operation_name = 'api.saturation'" in alert.sql
    assert "api_saturation_rejected" in CAUSE_COLUMNS
    assert (alert.period_minutes, alert.frequency_minutes, alert.silence_minutes) == (10, 5, 60)


def test_api_saturada_le_o_atributo_e_o_span_que_a_api_exporta() -> None:
    # O nome no OpenObserve troca "." por "_": uma mudança só de um lado cala o alerta.
    alert = next(a for a in alerts(stream="s", prefix=PREFIX) if a.name == "gs-causa-api-saturada")

    assert f"operation_name = '{SATURATION_SPAN}'" in alert.sql
    assert REJECTED_ATTRIBUTE.replace(".", "_") in alert.sql
