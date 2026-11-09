"""As consultas de deploy/openobserve/sql contra o OpenObserve real, com valores à mão.

Cada teste escreve spans sintéticos num stream só dele e confere o resultado da consulta
renderizada pelo catálogo, a mesma que o provisionamento envia.
"""

import pytest

from openobserve.catalog import SLOS, alerts, slo_named
from openobserve.provision import declare_columns
from tests.openobserve.conftest import (
    EMPTY_MOUNT,
    FULL_MOUNT,
    RUNS_ROUTE,
    US_PER_S,
    Telemetry,
    now_us,
    unique_stream,
    wait_until,
)

pytestmark = pytest.mark.openobserve

HOUR_S = 3600
MINUTE_S = 60
RECENT_S = 5 * MINUTE_S  # nas duas janelas
OLD_S = 40 * MINUTE_S  # só na janela de 1 h


def sli(telemetry: Telemetry, slo_name: str, stream: str) -> tuple[int, int]:
    rows = telemetry.api.search(
        slo_named(slo_name).sli_sql(stream),
        "traces",
        now_us() - 2 * HOUR_S * US_PER_S,
        now_us() + MINUTE_S * US_PER_S,
    )
    return int(rows[0]["eligible"]), int(rows[0]["good"])


def burn_rows(telemetry: Telemetry, slo_name: str, stream: str, rate: int) -> list[dict[str, int]]:
    rows = telemetry.api.search(
        slo_named(slo_name).burn_sql(stream, rate),
        "traces",
        now_us() - HOUR_S * US_PER_S,
        now_us() + MINUTE_S * US_PER_S,
    )
    return [{key: int(value) for key, value in row.items() if key != "_timestamp"} for row in rows]


# --- SLI ----------------------------------------------------------------------------------------


def test_sli_de_aceitacao(telemetry: Telemetry) -> None:
    stream = unique_stream("aceitacao")
    traces = telemetry.traces(stream)
    traces.acceptance(202, duration_ms=120)  # bom
    traces.acceptance(202, duration_ms=499.9)  # bom: abaixo de 500 ms
    traces.acceptance(202, duration_ms=500)  # ruim: 500 ms não é "menos de 500 ms"
    traces.acceptance(202, duration_ms=1500)  # ruim
    traces.acceptance(500, error_code="INTERNAL_ERROR")  # ruim
    traces.acceptance(503, error_code="SERVICE_UNAVAILABLE")  # ruim
    traces.acceptance(503, error_code="QUEUE_FULL")  # fora: recusa de política
    traces.acceptance(429, error_code="RATE_LIMITED")  # fora
    traces.acceptance(429, error_code="QUOTA_EXCEEDED")  # fora
    traces.acceptance(409, error_code="ACTIVE_RUN_LIMIT")  # fora
    traces.acceptance(422, error_code="VALIDATION_FAILED")  # fora: corpo inválido
    traces.acceptance(401, error_code="AUTHENTICATION_REQUIRED")  # fora: sem credencial
    traces.acceptance(200)  # fora: ?validate_only=true não cria Run
    traces.request("GET", RUNS_ROUTE, 500)  # fora: outra operação
    traces.request("POST", RUNS_ROUTE, 202, main=False)  # fora: não é o evento amplo
    traces.database_query()  # fora: span de consulta, sem atributo `main`
    traces.flush()

    assert sli(telemetry, "aceitacao", stream) == (6, 2)


def test_sli_de_download(telemetry: Telemetry) -> None:
    stream = unique_stream("download")
    traces = telemetry.traces(stream)
    traces.download(200, first_byte_ms=10)  # bom
    traces.download(200, first_byte_ms=999.9)  # bom
    traces.download(206, first_byte_ms=10)  # bom: faixa parcial também é 2xx
    traces.download(200, first_byte_ms=1000)  # ruim: 1 s não é "menos de 1 s"
    traces.download(200, completed=False)  # ruim: transferência interrompida
    traces.download(500, completed=False)  # ruim
    traces.download(404)  # fora: inexistente ou alheio
    traces.download(410)  # fora: expirado
    traces.download(429)  # fora: limite de transferências
    traces.request("GET", "/api/v1/artifacts/{artifact_id}", 500)  # fora: metadados
    traces.flush()

    assert sli(telemetry, "download", stream) == (6, 3)


def test_sli_de_execucao(telemetry: Telemetry) -> None:
    stream = unique_stream("execucao")
    traces = telemetry.traces(stream)
    traces.run_finished(run_duration_ms=1000)  # boa
    traces.run_finished(run_duration_ms=300_000)  # boa: 5 min exatos
    traces.run_finished(error_code="NO_VALID_OUTPUTS", run_duration_ms=2000)  # boa: conteúdo
    traces.run_finished(run_duration_ms=300_001)  # ruim: passou de 5 min
    traces.run_finished(error_code="EXECUTOR_UNAVAILABLE")  # ruim
    traces.run_finished(error_code="INFRA_POISON")  # ruim
    traces.run_finished(error_code="DEADLINE_EXCEEDED")  # ruim
    traces.run_finished(error_code="QUEUE_EXPIRED", run_duration_ms=None)  # ruim: nem começou
    traces.run_finished(error_code="CANCELED", eligible=False)  # fora: cancelada pela pessoa
    traces.run_finished(eligible=False)  # fora: mais de 10 arquivos ou 50 MiB
    traces.run_finished(main=False)  # fora
    traces.flush()

    assert sli(telemetry, "execucao", stream) == (8, 3)


def test_sli_sem_eventos_elegiveis_e_zero_de_zero(telemetry: Telemetry) -> None:
    stream = unique_stream("vazio")
    traces = telemetry.traces(stream)
    traces.request("GET", "/api/v1/me", 200)
    traces.flush()
    declare_columns(telemetry.api, stream)

    for slo in SLOS:
        assert sli(telemetry, slo.name, stream) == (0, 0)


# --- Queima em duas janelas (aceitação: orçamento 1/200, mínimo 101 na janela de 1 h) -----------


def acceptance_traffic(
    telemetry: Telemetry, *, recent: tuple[int, int], old: tuple[int, int] = (0, 0)
) -> str:
    """(bons, ruins) nos últimos 5 min e há 40 min; devolve o stream."""
    stream = unique_stream("queima")
    traces = telemetry.traces(stream)
    for (good, bad), age_s in ((recent, RECENT_S), (old, OLD_S)):
        for _ in range(good):
            traces.acceptance(202, age_s=age_s)
        for _ in range(bad):
            traces.acceptance(500, error_code="INTERNAL_ERROR", age_s=age_s)
    traces.flush()
    return stream


def test_queima_urgente_dispara_no_limite_de_4x(telemetry: Telemetry) -> None:
    # 200 elegíveis, 4 ruins: 4 * 200 = 4 * 200, exatamente 4x o ritmo permitido.
    stream = acceptance_traffic(telemetry, recent=(196, 4))

    assert burn_rows(telemetry, "aceitacao", stream, 4) == [
        {"eligible_long": 200, "bad_long": 4, "eligible_short": 200, "bad_short": 4}
    ]


def test_queima_urgente_nao_dispara_logo_abaixo_de_4x_mas_o_aviso_sim(
    telemetry: Telemetry,
) -> None:
    # 3 ruins em 200: 3 * 200 = 600 < 800 (4x), mas >= 400 (2x).
    stream = acceptance_traffic(telemetry, recent=(197, 3))

    assert burn_rows(telemetry, "aceitacao", stream, 4) == []
    assert len(burn_rows(telemetry, "aceitacao", stream, 2)) == 1


def test_aviso_nao_dispara_logo_abaixo_de_2x(telemetry: Telemetry) -> None:
    # 1 ruim em 200: 200 < 400.
    stream = acceptance_traffic(telemetry, recent=(199, 1))

    assert burn_rows(telemetry, "aceitacao", stream, 2) == []


def test_queima_exige_o_minimo_de_eventos_na_janela_longa(telemetry: Telemetry) -> None:
    below = acceptance_traffic(telemetry, recent=(0, 100))
    at_minimum = acceptance_traffic(telemetry, recent=(0, 101))

    assert burn_rows(telemetry, "aceitacao", below, 4) == []
    assert len(burn_rows(telemetry, "aceitacao", at_minimum, 4)) == 1


def test_queima_exige_o_minimo_de_eventos_na_janela_curta(telemetry: Telemetry) -> None:
    # A hora queima muito, mas nos últimos 15 min só há 25 eventos (o mínimo é 26).
    few = acceptance_traffic(telemetry, recent=(0, 25), old=(150, 50))
    enough = acceptance_traffic(telemetry, recent=(0, 26), old=(150, 50))

    assert burn_rows(telemetry, "aceitacao", few, 4) == []
    assert len(burn_rows(telemetry, "aceitacao", enough, 4)) == 1


def test_queima_que_ja_parou_nao_dispara(telemetry: Telemetry) -> None:
    # A hora ainda acumula 50 ruins de 40 min atrás, mas os últimos 15 min estão limpos.
    stream = acceptance_traffic(telemetry, recent=(60, 0), old=(150, 50))

    assert burn_rows(telemetry, "aceitacao", stream, 4) == []


def test_queima_ignora_eventos_de_mais_de_uma_hora(telemetry: Telemetry) -> None:
    stream = unique_stream("antigo")
    traces = telemetry.traces(stream)
    # Os 15 min queimam (2 ruins em 30), mas só com a hora de 150 ruins de 70 min atrás a
    # janela longa teria o mínimo de eventos.
    for _ in range(150):
        traces.acceptance(500, error_code="INTERNAL_ERROR", age_s=70 * MINUTE_S)
    for _ in range(28):
        traces.acceptance(202, age_s=RECENT_S)
    for _ in range(2):
        traces.acceptance(500, error_code="INTERNAL_ERROR", age_s=RECENT_S)
    traces.flush()
    rows = telemetry.api.search(
        slo_named("aceitacao").burn_sql(stream, 4),
        "traces",
        now_us() - 2 * HOUR_S * US_PER_S,
        now_us() + MINUTE_S * US_PER_S,
    )

    assert rows == []


def test_queima_de_execucao_usa_o_orcamento_de_1_em_100(telemetry: Telemetry) -> None:
    # 100 Runs, 4 ruins: 4 * 100 >= 4 * 100; com orçamento 1/200 não chegaria a 4x.
    stream = unique_stream("execucao_queima")
    traces = telemetry.traces(stream)
    for _ in range(96):
        traces.run_finished(age_s=RECENT_S)
    for _ in range(4):
        traces.run_finished(error_code="EXECUTOR_UNAVAILABLE", age_s=RECENT_S)
    traces.flush()

    assert len(burn_rows(telemetry, "execucao", stream, 4)) == 1


# --- Causas e heartbeat -------------------------------------------------------------------------


def cause_rows(telemetry: Telemetry, name: str, stream: str) -> list[dict[str, object]]:
    alert = next(a for a in alerts(stream=stream, prefix="t-") if a.name == f"t-{name}")
    return telemetry.api.search(
        alert.sql,
        alert.stream_type,
        now_us() - alert.period_minutes * MINUTE_S * US_PER_S,
        now_us() + MINUTE_S * US_PER_S,
    )


@pytest.mark.parametrize(
    ("ages_min", "fires"),
    [
        ((1, 6, 11), True),  # uma recusa em cada fatia de 5 min
        ((1, 2, 6, 7), False),  # só duas fatias: fila cheia há menos de 15 min
        ((1, 6, 16), False),  # a terceira recusa saiu da janela
    ],
)
def test_fila_cheia_sustentada(
    telemetry: Telemetry, ages_min: tuple[int, ...], fires: bool
) -> None:
    stream = unique_stream("fila")
    traces = telemetry.traces(stream)
    for age in ages_min:
        traces.acceptance(503, error_code="QUEUE_FULL", age_s=age * MINUTE_S + 30)
    traces.acceptance(503, error_code="SERVICE_UNAVAILABLE", age_s=11 * MINUTE_S)
    traces.flush()

    assert bool(cause_rows(telemetry, "causa-fila-cheia", stream)) is fires


def test_disco_acima_de_85_porcento_aparece_so_com_o_ponto_cheio(telemetry: Telemetry) -> None:
    def full_mounts() -> list[object] | None:
        rows = [row["mountpoint"] for row in cause_rows(telemetry, "causa-disco", "x")]
        return rows or None

    assert wait_until(full_mounts, "métrica de disco do Collector", 90) == [FULL_MOUNT]
    seen = telemetry.api.search(
        'SELECT mountpoint, max(value) AS used FROM "system_filesystem_utilization" '
        "GROUP BY mountpoint ORDER BY mountpoint",
        "metrics",
        now_us() - 10 * MINUTE_S * US_PER_S,
        now_us() + MINUTE_S * US_PER_S,
    )
    assert [row["mountpoint"] for row in seen] == [EMPTY_MOUNT, FULL_MOUNT]


def test_heartbeat_le_as_metricas_e_devolve_uma_linha(telemetry: Telemetry) -> None:
    rows = cause_rows(telemetry, "heartbeat", "trafego-sem-nenhum-span")

    assert len(rows) == 1
    assert int(str(rows[0]["samples"])) > 0


def test_heartbeat_devolve_uma_linha_mesmo_num_periodo_sem_dados(telemetry: Telemetry) -> None:
    alert = next(a for a in alerts(stream="s", prefix="t-") if a.name == "t-heartbeat")
    future_us = now_us() + 24 * HOUR_S * US_PER_S

    rows = telemetry.api.search(
        alert.sql, alert.stream_type, future_us, future_us + HOUR_S * US_PER_S
    )

    assert len(rows) == 1


# --- Causas lidas do span da manutenção --------------------------------------------------------


@pytest.mark.parametrize(("stuck", "fires"), [(0, False), (1, True)])
def test_execucao_presa_em_stopping(telemetry: Telemetry, stuck: int, fires: bool) -> None:
    stream = unique_stream("presa")
    traces = telemetry.traces(stream)
    traces.maintenance_cycle(runs_stuck_stopping=stuck, age_s=MINUTE_S)
    traces.maintenance_cycle(runs_stuck_stopping=0, age_s=3 * MINUTE_S)
    traces.flush()

    assert bool(cause_rows(telemetry, "causa-execucao-presa", stream)) is fires


@pytest.mark.parametrize(("reached", "fires"), [(0, False), (1, True)])
def test_limitador_de_exclusoes_acionado(telemetry: Telemetry, reached: int, fires: bool) -> None:
    stream = unique_stream("limitador")
    traces = telemetry.traces(stream)
    traces.maintenance_cycle(purge_limit_reached=reached)
    traces.maintenance_cycle(purge_limit_reached=0, age_s=2 * MINUTE_S)
    traces.flush()

    assert bool(cause_rows(telemetry, "causa-limitador-de-exclusoes", stream)) is fires


@pytest.mark.parametrize(("overdue", "fires"), [(0, False), (2, True)])
def test_exclusao_de_conta_atrasada(telemetry: Telemetry, overdue: int, fires: bool) -> None:
    stream = unique_stream("exclusao")
    traces = telemetry.traces(stream)
    traces.maintenance_cycle(accounts_deletion_overdue=overdue)
    traces.flush()

    assert bool(cause_rows(telemetry, "causa-exclusao-de-conta-atrasada", stream)) is fires


@pytest.mark.parametrize(
    ("reserved", "fires"),
    [
        ((86, 90), True),  # acima de 85% o período todo
        ((85, 90), False),  # 85% exatos não é "acima"
        ((86, 50), False),  # caiu abaixo no período: pico, não falta de capacidade
    ],
)
def test_capacidade_global_perto_do_teto(
    telemetry: Telemetry, reserved: tuple[int, int], fires: bool
) -> None:
    stream = unique_stream("capacidade")
    traces = telemetry.traces(stream)
    for age_min, percent in zip((2, 7), reserved, strict=True):
        traces.maintenance_cycle(
            storage_global_reserved_bytes=percent,
            storage_global_max_bytes=100,
            age_s=age_min * MINUTE_S,
        )
    # Um evento sem o teto (de outro processo ou de uma versão antiga) não derruba a consulta.
    traces.maintenance_cycle(runs_deleted=1, age_s=4 * MINUTE_S)
    traces.flush()

    assert bool(cause_rows(telemetry, "causa-capacidade-global", stream)) is fires


@pytest.mark.parametrize(
    ("cause", "counter"),
    [
        ("causa-storage-sem-sentinela", "missing_sweep_skipped"),
        ("causa-limitador-de-ausentes", "missing_governor_tripped"),
        ("causa-objetos-ausentes", "objects_marked_missing"),
        ("causa-objetos-ilegiveis", "objects_missing_unreadable"),
    ],
)
@pytest.mark.parametrize(("value", "fires"), [(0, False), (1, True)])
def test_causas_da_varredura_de_ausentes(
    telemetry: Telemetry, cause: str, counter: str, value: int, fires: bool
) -> None:
    stream = unique_stream("ausentes")
    traces = telemetry.traces(stream)
    traces.maintenance_cycle(**{counter: value})
    traces.maintenance_cycle(**{counter: 0}, age_s=2 * MINUTE_S)
    traces.flush()

    assert bool(cause_rows(telemetry, cause, stream)) is fires


@pytest.mark.parametrize(
    ("purge", "candidate", "fires"),
    [(0, 0, False), (1, 0, True), (0, 1, True)],
)
def test_arquivo_que_ficou_no_disco_dispara_por_qualquer_origem(
    telemetry: Telemetry, purge: int, candidate: int, fires: bool
) -> None:
    stream = unique_stream("orfaos")
    traces = telemetry.traces(stream)
    traces.maintenance_cycle(purge_files_not_removed=purge, candidates_left_on_disk=candidate)
    traces.maintenance_cycle(
        purge_files_not_removed=0, candidates_left_on_disk=0, age_s=2 * MINUTE_S
    )
    traces.flush()
    declare_columns(telemetry.api, stream)

    assert bool(cause_rows(telemetry, "causa-arquivos-nao-removidos", stream)) is fires


def test_span_que_nao_e_da_manutencao_nao_dispara_as_causas_dela(telemetry: Telemetry) -> None:
    stream = unique_stream("outro")
    traces = telemetry.traces(stream)
    # Três spans: as causas com `HAVING count(*) >= 3` só disparariam a partir daí.
    for minute in range(3):
        traces.span(
            "outra.coisa",
            (minute + 1) * MINUTE_S,
            {
                "main": True,
                "maintenance.runs_stuck_stopping": 3,
                "maintenance.purge_limit_reached": 1,
                "maintenance.accounts_deletion_overdue": 1,
                "maintenance.missing_sweep_skipped": 1,
                "maintenance.missing_governor_tripped": 1,
                "maintenance.objects_marked_missing": 1,
                "maintenance.objects_missing_unreadable": 1,
                "maintenance.purge_files_not_removed": 1,
                "maintenance.candidates_left_on_disk": 1,
                "maintenance.missing_sweep_failed": 1,
                "maintenance.failed_cycles": 1,
                "maintenance.executor_image_missing": 1,
                "maintenance.executor_image_probe_failed": 1,
                "alert.executor_start_failed": 1,
            },
            1.0,
        )
    traces.flush()
    declare_columns(telemetry.api, stream)

    for name in (
        "causa-execucao-presa",
        "causa-limitador-de-exclusoes",
        "causa-exclusao-de-conta-atrasada",
        "causa-storage-sem-sentinela",
        "causa-limitador-de-ausentes",
        "causa-objetos-ausentes",
        "causa-objetos-ilegiveis",
        "causa-arquivos-nao-removidos",
        "causa-varredura-falhou",
        "causa-ciclos-falhando",
        "causa-executor-nao-iniciou",
        "causa-imagem-executor-ausente",
        "causa-sonda-imagem-falhou",
    ):
        assert not cause_rows(telemetry, name, stream), name


@pytest.mark.parametrize(("value", "fires"), [(0, False), (1, True)])
def test_imagem_do_executor_ausente_dispara_com_uma_volta(
    telemetry: Telemetry, value: int, fires: bool
) -> None:
    stream = unique_stream("imagem")
    traces = telemetry.traces(stream)
    traces.maintenance_cycle(executor_image_missing=value, executor_image_probe_failed=0)
    traces.maintenance_cycle(
        executor_image_missing=0, executor_image_probe_failed=0, age_s=2 * MINUTE_S
    )
    traces.flush()

    assert bool(cause_rows(telemetry, "causa-imagem-executor-ausente", stream)) is fires


# Voltas de 30 s: dez falhas são 5 min, e o restart do `executor-ctl` não chega perto.
@pytest.mark.parametrize(("failing", "fires"), [(9, False), (10, True)])
def test_sonda_da_imagem_falhando_dispara_a_partir_de_dez_voltas(
    telemetry: Telemetry, failing: int, fires: bool
) -> None:
    stream = unique_stream("sonda")
    traces = telemetry.traces(stream)
    for turn in range(failing):
        traces.maintenance_cycle(
            executor_image_missing=0,
            executor_image_probe_failed=1,
            age_s=(turn + 1) * MINUTE_S / 2,
        )
    traces.maintenance_cycle(executor_image_missing=0, executor_image_probe_failed=0, age_s=30.0)
    traces.flush()

    assert bool(cause_rows(telemetry, "causa-sonda-imagem-falhou", stream)) is fires


def test_volta_sem_as_contagens_da_sonda_nao_dispara(telemetry: Telemetry) -> None:
    # Runner local, ou versão anterior da manutenção: o span chega sem as colunas.
    stream = unique_stream("sem_sonda")
    traces = telemetry.traces(stream)
    traces.maintenance_cycle(runs_deleted=1)
    traces.flush()
    declare_columns(telemetry.api, stream)

    assert not cause_rows(telemetry, "causa-imagem-executor-ausente", stream)
    assert not cause_rows(telemetry, "causa-sonda-imagem-falhou", stream)


# Três voltas: uma falha isolada se resolve na volta seguinte (30 s).
@pytest.mark.parametrize(
    ("cause", "counter"),
    [
        ("causa-ciclos-falhando", "failed_cycles"),
        ("causa-varredura-falhou", "missing_sweep_failed"),
    ],
)
@pytest.mark.parametrize(("failing", "fires"), [(2, False), (3, True)])
def test_falha_repetida_da_manutencao_dispara(
    telemetry: Telemetry, cause: str, counter: str, failing: int, fires: bool
) -> None:
    stream = unique_stream("falhando")
    traces = telemetry.traces(stream)
    for minute in range(failing):
        traces.maintenance_cycle(**{counter: 1}, age_s=(minute + 1) * MINUTE_S)
    traces.maintenance_cycle(**{counter: 0}, age_s=5 * MINUTE_S)
    traces.flush()

    assert bool(cause_rows(telemetry, cause, stream)) is fires


@pytest.mark.parametrize(
    ("cause", "attribute"),
    [
        ("causa-capacidade-divergente", "alert.capacity_divergence"),
        ("causa-capacidade-divergente", "alert.capacity_over_max"),
        ("causa-sem-reserva", "alert.upload_without_reservation"),
        ("causa-sem-reserva", "alert.run_without_reservation"),
        ("causa-arquivos-nao-removidos", "alert.purge_file_not_removed"),
    ],
)
@pytest.mark.parametrize(("value", "fires"), [(0, False), (1, True)])
def test_evento_de_alerta_contado_em_qualquer_span_main_dispara(
    telemetry: Telemetry, cause: str, attribute: str, value: int, fires: bool
) -> None:
    stream = unique_stream("evento")
    traces = telemetry.traces(stream)
    traces.span("attempt", MINUTE_S, {"main": True, attribute: value}, 1.0)
    traces.span("attempt", 2 * MINUTE_S, {"main": False, attribute: 1}, 1.0)
    traces.flush()
    declare_columns(telemetry.api, stream)

    assert bool(cause_rows(telemetry, cause, stream)) is fires


# Uma Run sem imagem no daemon falha quatro starts em cerca de 2 min (MAX_CLAIMS, com jitter de até
# 30 s entre eles); um start isolado que se recupera na retentativa não pede ninguém.
@pytest.mark.parametrize(("failing", "fires"), [(2, False), (3, True), (4, True)])
def test_executor_que_nao_inicia_em_tres_tentativas_dispara(
    telemetry: Telemetry, failing: int, fires: bool
) -> None:
    stream = unique_stream("executor")
    attribute = "alert.executor_start_failed"
    traces = telemetry.traces(stream)
    for minute in range(failing):
        traces.span("attempt", (minute + 1) * MINUTE_S, {"main": True, attribute: 1}, 1.0)
    # Tentativa normal e span que não é o principal nunca contam.
    traces.span("attempt", MINUTE_S, {"main": True, attribute: 0}, 1.0)
    traces.span("attempt", MINUTE_S, {"main": False, attribute: 1}, 1.0)
    traces.flush()
    declare_columns(telemetry.api, stream)

    assert bool(cause_rows(telemetry, "causa-executor-nao-iniciou", stream)) is fires


# --- Ausência: a telemetria que deveria chegar e não chegou -------------------------------------


def test_arquivo_perdido_num_ciclo_que_falha_dispara_pelo_contador_de_eventos(
    telemetry: Telemetry,
) -> None:
    stream = unique_stream("ciclofalho")
    traces = telemetry.traces(stream)
    # As contagens do ciclo que levantou exceção não entram no span; o evento do log, sim.
    traces.span(
        "maintenance.cycle",
        MINUTE_S,
        {"main": True, "maintenance.failed_cycles": 1, "alert.purge_file_not_removed": 1},
        5.0,
    )
    traces.flush()
    declare_columns(telemetry.api, stream)

    assert cause_rows(telemetry, "causa-arquivos-nao-removidos", stream)


@pytest.mark.parametrize(
    ("age_min", "fires"),
    [
        (2, False),
        (29, False),  # ainda dentro dos 30 min
        (31, True),  # última volta fora do período: parada ou travada
    ],
)
def test_manutencao_sem_ciclo(telemetry: Telemetry, age_min: int, fires: bool) -> None:
    stream = unique_stream("semciclo")
    traces = telemetry.traces(stream)
    traces.maintenance_cycle(age_s=age_min * MINUTE_S)
    traces.flush()

    assert bool(cause_rows(telemetry, "causa-manutencao-sem-ciclo", stream)) is fires


def test_trafego_nao_substitui_o_ciclo_da_manutencao(telemetry: Telemetry) -> None:
    stream = unique_stream("soapi")
    traces = telemetry.traces(stream)
    traces.acceptance(202)
    traces.flush()

    assert cause_rows(telemetry, "causa-manutencao-sem-ciclo", stream)


@pytest.mark.parametrize(("age_min", "fires"), [(2, False), (31, True)])
def test_sem_traces(telemetry: Telemetry, age_min: int, fires: bool) -> None:
    stream = unique_stream("semtraces")
    traces = telemetry.traces(stream)
    traces.acceptance(202, age_s=age_min * MINUTE_S)
    traces.flush()

    assert bool(cause_rows(telemetry, "causa-sem-traces", stream)) is fires


def test_a_semente_do_provisionamento_nao_conta_como_telemetria(telemetry: Telemetry) -> None:
    stream = unique_stream("semente")
    declare_columns(telemetry.api, stream)

    assert cause_rows(telemetry, "causa-sem-traces", stream)
    assert cause_rows(telemetry, "causa-manutencao-sem-ciclo", stream)


@pytest.mark.parametrize("name", ["causa-sem-traces", "causa-manutencao-sem-ciclo"])
def test_ausencia_devolve_linha_num_periodo_sem_nenhum_dado(
    telemetry: Telemetry, name: str
) -> None:
    # Como o heartbeat: sem linha, o alerta de ausência nunca dispararia justo quando deve.
    stream = unique_stream("vazio")
    declare_columns(telemetry.api, stream)
    alert = next(a for a in alerts(stream=stream, prefix="t-") if a.name == f"t-{name}")
    future_us = now_us() + 24 * HOUR_S * US_PER_S

    rows = telemetry.api.search(
        alert.sql, alert.stream_type, future_us, future_us + HOUR_S * US_PER_S
    )

    assert len(rows) == 1
