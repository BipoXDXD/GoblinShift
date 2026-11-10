"""Todo job da CI que pode rodar tem teto de tempo, e o apt do runsc não prende o runner."""

from pathlib import Path
from typing import Any

import pytest
import yaml

CI = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
GVISOR_JOB = "gvisor"
RUNSC_STEP_PREFIX = "Instalar o runsc"
RUNSC_STEP_LIMIT_RANGE = (2, 5)
APT_TIMEOUT_OPTIONS = (
    "Acquire::http::Timeout=",
    "Acquire::https::Timeout=",
    "Acquire::Retries=",
)
APT_OPTIONS_EXPANSION = '"${apt_rede[@]}"'
APT_COMMANDS = ("apt-get update", "apt-get install")
HEALTHY_MARGIN = 2
# Maior duração, em minutos, de um job bem-sucedido nos runs recentes de PR e de push na main.
MEASURED_MAX_MINUTES = {
    "checks": 13.8,
    "openobserve": 6.0,
    "web": 3.2,
    "secrets": 0.3,
    "e2e": 11.1,
    GVISOR_JOB: 5.7,
    "image": 3.0,
    "executor-image": 26.1,
    "web-image": 3.5,
}


def _jobs() -> dict[str, dict[str, Any]]:
    document: dict[str, Any] = yaml.safe_load(CI.read_text())
    jobs: dict[str, dict[str, Any]] = document["jobs"]
    return jobs


def _runnable_jobs() -> list[str]:
    return [
        job_id for job_id, job in _jobs().items() if "runs-on" in job and job.get("if") != "false"
    ]


def _runsc_step() -> dict[str, Any]:
    steps = _jobs()[GVISOR_JOB]["steps"]
    return next(step for step in steps if step.get("name", "").startswith(RUNSC_STEP_PREFIX))


def test_o_job_do_gvisor_esta_entre_os_verificados() -> None:
    assert GVISOR_JOB in _runnable_jobs()


@pytest.mark.parametrize("job_id", _runnable_jobs())
def test_job_que_pode_rodar_tem_teto_de_tempo(job_id: str) -> None:
    limit = _jobs()[job_id].get("timeout-minutes")

    assert isinstance(limit, int)
    assert limit > 0


@pytest.mark.parametrize(("job_id", "measured"), MEASURED_MAX_MINUTES.items())
def test_teto_do_job_folga_sobre_o_maior_run_saudavel(job_id: str, measured: float) -> None:
    assert _jobs()[job_id]["timeout-minutes"] >= measured * HEALTHY_MARGIN


@pytest.mark.parametrize("job_id", _runnable_jobs())
def test_teto_de_passo_nunca_passa_do_teto_do_job(job_id: str) -> None:
    job = _jobs()[job_id]

    for step in job["steps"]:
        assert step.get("timeout-minutes", 0) <= job.get("timeout-minutes", 0)


def test_passo_do_runsc_tem_limite_curto() -> None:
    lowest, highest = RUNSC_STEP_LIMIT_RANGE

    assert lowest <= _runsc_step().get("timeout-minutes", 0) <= highest


def test_download_da_chave_do_gvisor_tem_timeout_e_retry() -> None:
    download = next(line for line in _runsc_step()["run"].splitlines() if "curl " in line)

    assert "--max-time" in download
    assert "--retry" in download


@pytest.mark.parametrize("option", APT_TIMEOUT_OPTIONS)
def test_opcoes_do_apt_do_runsc_definem_timeout_e_retry(option: str) -> None:
    assert option in _runsc_step()["run"]


@pytest.mark.parametrize("command", APT_COMMANDS)
def test_comandos_apt_do_runsc_usam_as_opcoes(command: str) -> None:
    line = next(line for line in _runsc_step()["run"].splitlines() if command in line)

    assert APT_OPTIONS_EXPANSION in line
