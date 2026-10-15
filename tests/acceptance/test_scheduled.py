"""Teste de aceitação agendado (F8-9): unit, timer, instalador e o script que pinga o healthchecks.

O script só roda na VPS, como root. Aqui `docker`, `python3` e `curl` são falsos que registram,
numa única lista, cada chamada na ordem em que aconteceu; o `curl` registra a URL que leu do
stdin, que é por onde ela passa (deploy/backup/lib.sh).
"""

import configparser
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_DIR = ROOT / "deploy" / "acceptance"
SCRIPT = ACCEPTANCE_DIR / "scheduled.sh"
INSTALLER = ACCEPTANCE_DIR / "install.sh"
SERVICE = "goblinshift-acceptance.service"
TIMER = "goblinshift-acceptance.timer"
BACKUP_TIMER = ROOT / "deploy" / "backup" / "goblinshift-pg-backup.timer"
EXECUTOR_CTL_INSTALLER = ROOT / "deploy" / "executor-ctl" / "install.sh"
# Só o root grava aqui: o checkout e /srv/goblinshift/secrets pertencem ao goblinshift.
INSTALLED_SCRIPT = Path("/usr/local/lib/goblinshift/acceptance/scheduled.sh")
CONFIG_FILE = "/etc/goblinshift/acceptance.env"
CHECK_URL = "https://hc-ping.com/0b1c2d3e-CANARY-aceitacao"
WORKER = "w0rk3r"
SCRIPT_TIMEOUT_S = 30

FAKE_DOCKER = """#!/bin/sh
echo "docker $*" >>"$FAKE_CALLS"
case "$1" in
  ps) [ -n "$FAKE_WORKER" ] && echo "$FAKE_WORKER"; exit 0 ;;
  stop) exit "${FAKE_STOP_RC:-0}" ;;
  start) exit "${FAKE_START_RC:-0}" ;;
esac
"""
FAKE_PYTHON = """#!/bin/sh
echo "python3 $* (em $(basename "$PWD"))" >>"$FAKE_CALLS"
exit "${FAKE_ACCEPTANCE_RC:-0}"
"""
FAKE_CURL = """#!/bin/sh
echo "curl-argv $*" >>"$FAKE_ARGV"
echo "curl $(sed -n 's/^url = "\\(.*\\)"$/\\1/p')" >>"$FAKE_CALLS"
"""


def unit(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read(ACCEPTANCE_DIR / name)
    return parser


@pytest.fixture
def fakes(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("docker", FAKE_DOCKER), ("python3", FAKE_PYTHON), ("curl", FAKE_CURL)):
        command = bin_dir / name
        command.write_text(body)
        command.chmod(0o755)
    return tmp_path


def scheduled(
    fakes: Path, *, url: str | None = CHECK_URL, worker: str = WORKER, **rc: int
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    env = {
        "PATH": f"{fakes / 'bin'}:{os.environ['PATH']}",
        "FAKE_CALLS": str(fakes / "calls"),
        "FAKE_ARGV": str(fakes / "argv"),
        "FAKE_WORKER": worker,
        **{f"FAKE_{name.upper()}_RC": str(code) for name, code in rc.items()},
    }
    if url is not None:
        env["ACCEPTANCE_HEALTHCHECK_URL"] = url
    result = subprocess.run(  # noqa: S603 - script do repositório
        ["bash", str(SCRIPT)],  # noqa: S607 - o bash do PATH, como na unit
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=SCRIPT_TIMEOUT_S,
    )
    calls = fakes / "calls"
    return result, calls.read_text().splitlines() if calls.exists() else []


def worker_lookup() -> str:
    return (
        "docker ps --quiet --filter=label=com.docker.compose.project=goblinshift"
        " --filter=label=com.docker.compose.service=worker"
    )


# --- Script -----------------------------------------------------------------------------------


def test_sucesso_pinga_start_e_sucesso_com_o_worker_parado_durante_o_teste(fakes: Path) -> None:
    result, calls = scheduled(fakes)

    assert result.returncode == 0, result.stderr
    assert calls == [
        f"curl {CHECK_URL}/start",
        worker_lookup(),
        f"docker stop --time=30 {WORKER}",
        "python3 -E -s -B -m acceptance.run (em deploy)",
        f"docker start {WORKER}",
        f"curl {CHECK_URL}",
    ]


def test_aceitacao_reprovada_pinga_fail_religa_o_worker_e_sai_com_o_codigo_dela(
    fakes: Path,
) -> None:
    result, calls = scheduled(fakes, acceptance=1)

    assert result.returncode == 1
    assert calls[-2:] == [f"docker start {WORKER}", f"curl {CHECK_URL}/fail"]


def test_worker_que_nao_rodava_fica_como_estava(fakes: Path) -> None:
    result, calls = scheduled(fakes, worker="")

    assert result.returncode == 0, result.stderr
    assert calls == [
        f"curl {CHECK_URL}/start",
        worker_lookup(),
        "python3 -E -s -B -m acceptance.run (em deploy)",
        f"curl {CHECK_URL}",
    ]


def test_worker_que_nao_parou_impede_a_aceitacao(fakes: Path) -> None:
    # As sondas enchem a área de trabalho: com uma tentativa real ao lado, as duas falhariam.
    result, calls = scheduled(fakes, stop=1)

    assert result.returncode != 0
    assert not any(call.startswith("python3") for call in calls)
    assert calls[-2:] == [f"docker start {WORKER}", f"curl {CHECK_URL}/fail"]


def test_worker_que_nao_volta_reprova_mesmo_com_a_aceitacao_aprovada(fakes: Path) -> None:
    result, calls = scheduled(fakes, start=1)

    assert result.returncode != 0
    assert calls[-1] == f"curl {CHECK_URL}/fail"
    assert f"docker start {WORKER}" in result.stderr


def test_sem_a_url_nao_roda_nada(fakes: Path) -> None:
    result, calls = scheduled(fakes, url=None)

    assert result.returncode == 2
    assert "ACCEPTANCE_HEALTHCHECK_URL" in result.stderr
    assert calls == []


def test_url_do_check_nunca_vai_para_o_argv(fakes: Path) -> None:
    scheduled(fakes, acceptance=1)

    assert "CANARY" not in (fakes / "argv").read_text()


@pytest.mark.parametrize("script", [SCRIPT, INSTALLER], ids=["scheduled", "install"])
def test_scripts_passam_no_shellcheck(script: Path) -> None:
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        pytest.skip("shellcheck ausente")
    result = subprocess.run(  # noqa: S603 - binário localizado pelo which
        [shellcheck, "--external-sources", str(script)],
        capture_output=True,
        text=True,
        check=False,
        timeout=SCRIPT_TIMEOUT_S,
        cwd=script.parent,
    )
    assert result.returncode == 0, result.stdout


# --- Units e instalador -----------------------------------------------------------------------


def test_servico_roda_como_root_so_codigo_e_configuracao_que_so_o_root_grava() -> None:
    service = unit(SERVICE)["Service"]
    installer = INSTALLER.read_text()

    assert "User" not in service, "o teste de aceitação exige root (deploy/acceptance/run.py)"
    assert service["Type"] == "oneshot"
    assert Path(service["ExecStart"]) == INSTALLED_SCRIPT
    assert f"PREFIX={INSTALLED_SCRIPT.parent.parent}" in installer
    assert service["EnvironmentFile"] == CONFIG_FILE
    assert f"CONFIG={CONFIG_FILE}" in installer
    assert "/srv/goblinshift/app" not in (ACCEPTANCE_DIR / SERVICE).read_text()
    assert "hc-ping" not in (ACCEPTANCE_DIR / SERVICE).read_text()


def test_servico_root_so_grava_na_area_de_trabalho() -> None:
    service = unit(SERVICE)["Service"]
    work_area = re.search(r"^WORK_AREA=(\S+)$", EXECUTOR_CTL_INSTALLER.read_text(), re.MULTILINE)
    assert work_area is not None

    assert service["ProtectSystem"] == "strict"
    assert service["ReadWritePaths"] == work_area[1]
    assert service["ProtectHome"] == "yes"
    assert service["PrivateTmp"] == "yes"
    assert service["NoNewPrivileges"] == "yes"


def test_servico_root_so_tem_as_capabilities_das_sondas() -> None:
    # mkdir + chown/chmod 2770 de <área>/<id> para o UID do worker e leitura da área de todos os
    # donos (run.py); o Docker é o daemon que faz o resto, pelo socket.
    service = unit(SERVICE)["Service"]

    assert set(service["CapabilityBoundingSet"].split()) == {
        "CAP_CHOWN",
        "CAP_FOWNER",
        "CAP_FSETID",
        "CAP_DAC_OVERRIDE",
        "CAP_DAC_READ_SEARCH",
    }
    for directive in (
        "PrivateDevices",
        "ProtectKernelTunables",
        "ProtectKernelModules",
        "ProtectKernelLogs",
        "ProtectControlGroups",
        "LockPersonality",
    ):
        assert service[directive] == "yes", directive


def test_servico_tem_prazo_acima_das_sondas() -> None:
    # A sonda de disco tem até 900 s e as outras somam 720 s, fora o pull da imagem.
    service = unit(SERVICE)["Service"]

    assert service["TimeoutStartSec"] == "1h"


def test_timer_e_diario_com_jitter_e_recupera_execucao_perdida() -> None:
    timer = unit(TIMER)["Timer"]

    assert timer["OnCalendar"] == "*-*-* 07:30:00 UTC"
    assert timer["RandomizedDelaySec"] == "30min"
    assert timer["Persistent"] == "true"


def test_timer_nao_cruza_a_janela_do_backup() -> None:
    backup = unit_at(BACKUP_TIMER)["Timer"]
    acceptance = unit(TIMER)["Timer"]

    backup_window_end = _minutes(backup["OnCalendar"]) + _minutes_of(backup["RandomizedDelaySec"])

    assert _minutes(acceptance["OnCalendar"]) >= backup_window_end + 60


def test_instalador_exige_o_que_o_script_exige() -> None:
    script = SCRIPT.read_text()
    match = re.search(r"^require_env (.+)$", script, re.MULTILINE)
    assert match is not None
    installer = INSTALLER.read_text()
    required = re.search(r"^REQUIRED=\(([^)]*)\)", installer, re.MULTILINE)
    assert required is not None

    assert set(required.group(1).split()) == set(match.group(1).split())


def unit_at(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read(path)
    return parser


def _minutes(on_calendar: str) -> int:
    """Minuto do dia de um `OnCalendar` diário em UTC (`*-*-* HH:MM:SS UTC`)."""
    match = re.fullmatch(r"\*-\*-\* (\d\d):(\d\d):00 UTC", on_calendar)
    assert match is not None, on_calendar
    return int(match[1]) * 60 + int(match[2])


def _minutes_of(span: str) -> int:
    match = re.fullmatch(r"(\d+)min", span)
    assert match is not None, span
    return int(match[1])
