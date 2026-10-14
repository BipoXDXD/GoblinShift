"""Teste de carga k6 (docs/GoblinShiftPlan.md, fatia 8; deploy/load/README.md).

Não roda o k6: confere a estrutura dos cenários, que os thresholds seguem os SLOs de
`deploy/openobserve` (se um SLO mudar, este teste aponta o cenário a ajustar), que os cenários do
limite por conta ficam desligados até a A2 e que o script local recusa alvo fora da máquina.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from openobserve.catalog import SQL_DIR, slo_named

ROOT = Path(__file__).resolve().parent.parent
LOAD_DIR = ROOT / "deploy" / "load"
RUN_LOCAL = LOAD_DIR / "run-local.sh"
SCENARIOS = LOAD_DIR / "scenarios"
SCENARIO_NAMES = ("jornada", "fila", "transferencias", "limite-por-conta", "limite-auth")
DEFERRED_SCENARIOS = ("limite-por-conta", "limite-auth")
SCRIPT_TIMEOUT_SECONDS = 10


def _scenario(name: str) -> str:
    return (SCENARIOS / f"{name}.js").read_text(encoding="utf-8")


def _sql_bound(file_name: str, column: str, operator: str) -> int:
    sql = (SQL_DIR / file_name).read_text(encoding="utf-8")
    match = re.search(rf"TRY_CAST\({column} AS DOUBLE\) {re.escape(operator)} (\d+)", sql)
    assert match is not None, f"{column} não encontrado em {file_name}"
    return int(match.group(1))


def _percentile(slo_name: str) -> str:
    return f"p({slo_named(slo_name).target * 100:g})"


def _run_local(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
        [str(RUN_LOCAL), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=SCRIPT_TIMEOUT_SECONDS,
    )


@pytest.mark.parametrize("name", SCENARIO_NAMES)
def test_every_scenario_declares_thresholds(name: str) -> None:
    assert re.search(r"\bthresholds\s*:", _scenario(name)), name


@pytest.mark.parametrize("name", SCENARIO_NAMES)
def test_scenario_is_valid_javascript_module(name: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node ausente")
    result = subprocess.run(  # noqa: S603 - binário localizado pelo which
        [node, "--input-type=module", "--check"],
        input=_scenario(name),
        capture_output=True,
        text=True,
        check=False,
        timeout=SCRIPT_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, result.stderr


def test_journey_acceptance_threshold_follows_acceptance_slo() -> None:
    bound = _sql_bound("aceitacao.eventos.sql", "duration_ms", "<")
    assert f"'{_percentile('aceitacao')}<{bound}'" in _scenario("jornada")


def test_journey_download_threshold_follows_download_slo() -> None:
    bound = _sql_bound("download.eventos.sql", "http_response_first_byte_ms", "<")
    assert f"'{_percentile('download')}<{bound}'" in _scenario("jornada")


def test_journey_execution_threshold_follows_execution_slo() -> None:
    bound = _sql_bound("execucao.eventos.sql", "run_duration_ms", "<=")
    journey = _scenario("jornada")
    assert f"EXECUTION_SLO_MS = {bound}" in journey
    assert f"'rate>={slo_named('execucao').target:g}'" in journey


def test_journey_polling_threshold_is_p99_below_500_ms() -> None:
    assert "'p(99)<500'" in _scenario("jornada")


@pytest.mark.parametrize("name", DEFERRED_SCENARIOS)
def test_deferred_scenarios_say_what_they_wait_for(name: str) -> None:
    assert "Espera a A2" in _scenario(name) or "Espera a fatia 6" in _scenario(name)


def test_deferred_scenarios_only_run_when_enabled() -> None:
    script = RUN_LOCAL.read_text(encoding="utf-8")
    assert 'if [[ "${GS_LOAD_RATE_LIMIT:-0}" == 1 ]]' in script
    assert 'if [[ "${GS_LOAD_AUTH:-0}" == 1 ]]' in script


def test_default_target_is_local() -> None:
    result = _run_local("--check-target")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "target",
    ["http://nginx", "http://localhost:8080", "http://127.0.0.1", "http://192.168.0.10:80"],
)
def test_local_targets_are_accepted(target: str) -> None:
    result = _run_local("--check-target", target)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "target",
    [
        "https://goblinshift.dev",
        "http://8.8.8.8",
        "http://localhost.evil.example",
        "http://localhost@evil.example",
        "http://10.0.0.5.evil.example",
        "http://172.32.0.1",
        "ftp://localhost",
        "",
    ],
)
def test_remote_targets_are_refused(target: str) -> None:
    result = _run_local("--check-target", target)
    assert result.returncode == 2
    assert "recusado" in result.stderr


def test_run_local_passes_shellcheck() -> None:
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        pytest.skip("shellcheck ausente")
    result = subprocess.run(  # noqa: S603 - binário localizado pelo which
        [shellcheck, str(RUN_LOCAL)],
        capture_output=True,
        text=True,
        check=False,
        timeout=SCRIPT_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, result.stdout
