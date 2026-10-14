"""Lógica pura do teste de caos (deploy/acceptance/chaos.py); o script inteiro só roda na VPS."""

import uuid

import pytest
from acceptance import chaos_rules as chaos
from acceptance.chaos_rules import (
    AttemptRow,
    Boundary,
    ChaosError,
    Fault,
    MarkedOutput,
    Observation,
    OutputRow,
    Position,
    Timing,
)

from goblinshift.processing.csv_janitor import TableCleaned, clean_table
from goblinshift.processing.csv_janitor.params import params_from_mapping

ATTEMPT = uuid.UUID("01890a5d-ac96-774b-bcce-b302099a8057")
OTHER_ATTEMPT = uuid.UUID("01890a5d-ac96-774b-bcce-b302099a8058")


def observation(
    *,
    status: str = "running",
    phase: str | None = "inspecting",
    attempt_id: uuid.UUID | None = ATTEMPT,
    executor_started: bool = False,
    attempt_ended: bool = False,
    outputs: int = 0,
) -> Observation:
    return Observation(
        run_status=status,
        phase=phase,
        attempt_id=attempt_id,
        generation=1 if attempt_id else None,
        executor_started=executor_started,
        attempt_ended=attempt_ended,
        outputs=outputs,
    )


# Chave de API --------------------------------------------------------------------------


def test_identificador_publico_vem_do_stderr_do_gs_admin() -> None:
    stderr = "chave 0123456789abcdef válida até 2026-10-15T12:00:00+00:00\n"

    assert chaos.parse_key_public_id(stderr) == "0123456789abcdef"


@pytest.mark.parametrize(
    "stderr",
    ["", "gs-admin: conta x não existe\n", "chave XYZ válida até amanhã\n", "chave 0123 válida\n"],
)
def test_stderr_sem_identificador_valido_e_erro(stderr: str) -> None:
    with pytest.raises(ChaosError):
        chaos.parse_key_public_id(stderr)


# Sessão do psql ------------------------------------------------------------------------


def test_saida_marcada_devolve_as_linhas_antes_do_marcador() -> None:
    reader = MarkedOutput("__fim__")

    assert reader.feed(b"a|1\nb|2\n__fim__\n") == ["a|1", "b|2"]


def test_saida_marcada_espera_o_marcador_chegar_em_pedacos() -> None:
    reader = MarkedOutput("__fim__")

    assert reader.feed(b"a|") is None
    assert reader.feed(b"1\n__f") is None
    assert reader.feed(b"im__\n") == ["a|1"]


def test_saida_marcada_guarda_o_que_veio_depois_para_a_proxima_consulta() -> None:
    reader = MarkedOutput("__fim__")

    assert reader.feed(b"__fim__\nx\n") == []
    assert reader.feed(b"__fim__\n") == ["x"]


def test_saida_marcada_nao_confunde_linha_que_contem_o_marcador() -> None:
    reader = MarkedOutput("__fim__")

    assert reader.feed(b"a__fim__\n") is None


def test_comando_do_psql_e_lista_de_argumentos_sem_senha() -> None:
    command = chaos.psql_command("abc123")

    assert command[:4] == ["docker", "exec", "--interactive", "abc123"]
    assert "psql" in command
    assert "ON_ERROR_STOP=1" in command
    assert not any("password" in part.lower() for part in command)


# Observação e fronteiras -----------------------------------------------------------------


def test_observacao_com_tentativa_e_lida_campo_a_campo() -> None:
    line = f"running|processing|{ATTEMPT}|2|t|f|0"

    assert chaos.parse_observation(line) == Observation(
        run_status="running",
        phase="processing",
        attempt_id=ATTEMPT,
        generation=2,
        executor_started=True,
        attempt_ended=False,
        outputs=0,
    )


def test_observacao_na_fila_nao_tem_tentativa_nem_fase() -> None:
    assert chaos.parse_observation("queued||||||0") == Observation(
        run_status="queued",
        phase=None,
        attempt_id=None,
        generation=None,
        executor_started=False,
        attempt_ended=False,
        outputs=0,
    )


@pytest.mark.parametrize("line", ["", "running|x", "running|processing|nao-uuid|1|t|f|0"])
def test_observacao_malformada_e_erro(line: str) -> None:
    with pytest.raises(ChaosError):
        chaos.parse_observation(line)


@pytest.mark.parametrize(
    ("obs", "expected"),
    [
        (observation(status="queued", phase=None, attempt_id=None), Position.BEFORE),
        (observation(), Position.AT),
        (observation(executor_started=True, phase="processing"), Position.PAST),
        (observation(status="succeeded", phase=None, outputs=6), Position.PAST),
    ],
)
def test_fronteira_do_claim_e_tentativa_viva_sem_executor(
    obs: Observation, expected: Position
) -> None:
    assert chaos.locate(Boundary.CLAIMED, obs) is expected


@pytest.mark.parametrize(
    ("obs", "expected"),
    [
        (observation(), Position.BEFORE),
        (observation(executor_started=True, phase="processing"), Position.AT),
        (observation(executor_started=True, phase="packaging"), Position.PAST),
        (observation(status="failed", phase=None, executor_started=True), Position.PAST),
    ],
)
def test_fronteira_do_executor_e_processing_com_inicio_registrado(
    obs: Observation, expected: Position
) -> None:
    assert chaos.locate(Boundary.EXECUTING, obs) is expected


@pytest.mark.parametrize(
    ("obs", "expected"),
    [
        (observation(executor_started=True, phase="processing"), Position.BEFORE),
        (observation(executor_started=True, phase="packaging"), Position.AT),
        (observation(executor_started=True, phase="finalizing"), Position.AT),
        (observation(executor_started=True, phase="finalizing", outputs=6), Position.PAST),
        (observation(status="succeeded", phase=None, outputs=6), Position.PAST),
    ],
)
def test_fronteira_da_coleta_e_packaging_ou_finalizing_sem_publicacao(
    obs: Observation, expected: Position
) -> None:
    assert chaos.locate(Boundary.COLLECTING, obs) is expected


def test_tentativa_encerrada_ainda_nao_e_a_fronteira() -> None:
    reconciled = observation(phase="retry_wait", attempt_ended=True)

    assert chaos.locate(Boundary.CLAIMED, reconciled) is Position.BEFORE


def test_falha_caiu_na_fronteira_se_a_mesma_tentativa_continua_nela() -> None:
    hit = observation()

    assert chaos.fault_landed(Boundary.CLAIMED, hit, observation())
    assert not chaos.fault_landed(
        Boundary.CLAIMED, hit, observation(executor_started=True, phase="processing")
    )
    assert not chaos.fault_landed(Boundary.CLAIMED, hit, observation(attempt_id=OTHER_ATTEMPT))


# Comandos de falha -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fault", "inject", "recover"),
    [
        (Fault.KILL, ["docker", "kill", "--signal=KILL", "w1"], ["docker", "start", "w1"]),
        (Fault.PAUSE, ["docker", "pause", "w1"], ["docker", "unpause", "w1"]),
    ],
)
def test_cada_falha_tem_injecao_e_recuperacao(
    fault: Fault, inject: list[str], recover: list[str]
) -> None:
    assert chaos.inject_command(fault, "w1") == inject
    assert chaos.recover_command(fault, "w1") == recover


def test_cenarios_cobrem_cada_falha_em_cada_fronteira() -> None:
    assert set(chaos.SCENARIOS) == {(fault, boundary) for fault in Fault for boundary in Boundary}


# Prazos ----------------------------------------------------------------------------------


def test_prazo_da_reconciliacao_soma_lease_transacao_ociosa_dois_ciclos_e_carencia() -> None:
    timing = Timing(lease_s=60, maintenance_interval_s=30, idle_in_transaction_s=30)

    # 60 + 30 + 2 * 30 + 10 de carência do stop + 60 de folga.
    assert timing.reconcile_timeout_s == 220


def test_prazo_da_execucao_inclui_a_reconciliacao_o_jitter_e_uma_tentativa_inteira() -> None:
    timing = Timing(lease_s=60, maintenance_interval_s=30, idle_in_transaction_s=30)

    # 220 + 30 de jitter máximo + 600 de teto da tentativa.
    assert timing.run_timeout_s == 850


# Tabelas ---------------------------------------------------------------------------------


def test_tabela_pequena_tem_saida_escrita_a_mao() -> None:
    table = chaos.build_table(1, rows=2, note_chars=3)

    assert table.name == "caos-1.csv"
    assert table.content == (
        b"id;nome;email;notas\r\n"
        b"1-000000; Nome 0 ;USER0@X.COM;xxx\r\n"
        b"1-000001; Nome 1 ;USER1@X.COM;xxx\r\n"
        b"1-000000;Nome 0;user0@x.com;xxx\r\n"
    )
    assert table.expected == (
        b"id,nome,email,notas\r\n"
        b"1-000000,Nome 0,user0@x.com,xxx\r\n"
        b"1-000001,Nome 1,user1@x.com,xxx\r\n"
    )


def test_receita_produz_a_saida_esperada_da_tabela_grande() -> None:
    """Confere o fixture contra a receita real: a saída esperada não pode estar errada na VPS."""
    table = chaos.build_table(3)

    result = clean_table(table.content, params_from_mapping(chaos.RUN_PARAMETERS))

    assert isinstance(result, TableCleaned)
    assert result.content == table.expected


def test_tabela_grande_fica_abaixo_dos_limites_do_csv() -> None:
    table = chaos.build_table(1)

    assert len(table.content) <= 8 * 1024 * 1024
    # Registros de dados, contando a duplicata, até 20.000 (1.7).
    assert table.content.count(b"\r\n") - 1 <= 20_000


# Julgamentos -----------------------------------------------------------------------------


def attempt_row(
    generation: int,
    *,
    attempt_id: uuid.UUID | None = None,
    started: bool = True,
    ended: bool = True,
    error: str = "",
) -> AttemptRow:
    return AttemptRow(
        attempt_id=attempt_id or uuid.uuid4(),
        generation=generation,
        executor_started=started,
        ended=ended,
        error_code=error,
    )


def test_tentativas_coerentes_passam() -> None:
    rows = [
        attempt_row(1, attempt_id=ATTEMPT, started=False, error="EXECUTOR_UNAVAILABLE"),
        attempt_row(2),
    ]

    assert chaos.judge_attempts(rows, faulted=ATTEMPT, final_generation=2).passed


@pytest.mark.parametrize(
    "rows",
    [
        # A tentativa atingida publicou (é a geração final).
        [attempt_row(1, attempt_id=ATTEMPT)],
        # A tentativa atingida nunca foi encerrada.
        [attempt_row(1, attempt_id=ATTEMPT, ended=False), attempt_row(2)],
        # Três tentativas físicas: passou do teto de duas.
        [attempt_row(1, attempt_id=ATTEMPT), attempt_row(2), attempt_row(3)],
        # A geração final terminou com erro.
        [attempt_row(1, attempt_id=ATTEMPT), attempt_row(2, error="EXECUTOR_UNAVAILABLE")],
        # A tentativa atingida não aparece.
        [attempt_row(1), attempt_row(2)],
    ],
)
def test_tentativas_incoerentes_falham(rows: list[AttemptRow]) -> None:
    final = max(row.generation for row in rows)

    assert not chaos.judge_attempts(rows, faulted=ATTEMPT, final_generation=final).passed


def test_linha_de_tentativa_e_lida_do_psql() -> None:
    assert chaos.parse_attempt(f"{ATTEMPT}|2|t|f|") == AttemptRow(
        attempt_id=ATTEMPT, generation=2, executor_started=True, ended=False, error_code=""
    )


EXPECTED_NAMES = {"caos-1.csv", "caos-2.csv", "report.json"}


def published(generation: int = 2) -> list[OutputRow]:
    return [
        OutputRow(0, "output", "caos-1.csv", generation),
        OutputRow(1, "output", "caos-2.csv", generation),
        OutputRow(2, "report", "report.json", generation),
    ]


def test_publicacao_unica_da_geracao_final_passa() -> None:
    assert chaos.judge_publication(published(), final_generation=2, expected=EXPECTED_NAMES).passed


@pytest.mark.parametrize(
    "rows",
    [
        [],
        published()[:2],
        [*published(), OutputRow(3, "output", "caos-1.csv", 2)],
        [OutputRow(0, "output", "caos-1.csv", 1), *published()[1:]],
        [OutputRow(0, "output", "caos-1.csv", 2), *published()[1:2], published()[1]],
    ],
)
def test_publicacao_faltando_duplicada_ou_de_geracao_vencida_falha(rows: list[OutputRow]) -> None:
    assert not chaos.judge_publication(rows, final_generation=2, expected=EXPECTED_NAMES).passed


def test_linha_de_saida_publicada_e_lida_do_psql() -> None:
    assert chaos.parse_output("0|output|caos-1.csv|2") == OutputRow(0, "output", "caos-1.csv", 2)


def test_download_identico_ao_esperado_passa() -> None:
    expected = {"a.csv": b"x", "b.csv": b"y"}

    assert chaos.judge_downloads(dict(expected), expected).passed


@pytest.mark.parametrize("downloaded", [{"a.csv": b"x"}, {"a.csv": b"x", "b.csv": b"z"}])
def test_download_faltando_ou_diferente_falha(downloaded: dict[str, bytes]) -> None:
    assert not chaos.judge_downloads(downloaded, {"a.csv": b"x", "b.csv": b"y"}).passed


def test_limpeza_sem_container_e_so_lost_found_passa() -> None:
    assert chaos.judge_cleanup(containers=[], work_entries=["lost+found"]).passed


@pytest.mark.parametrize(
    ("containers", "entries"),
    [(["gs-exec-1"], ["lost+found"]), ([], ["lost+found", str(ATTEMPT)])],
)
def test_container_orfao_ou_area_suja_falha(containers: list[str], entries: list[str]) -> None:
    assert not chaos.judge_cleanup(containers=containers, work_entries=entries).passed
