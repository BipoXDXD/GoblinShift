"""SIGTERM no meio da aceitação, com `run_probe`, `DockerCli` e o subprocesso reais.

A unit não define `KillMode`: o systemd sinaliza o cgroup inteiro, inclusive o cliente `docker` em
curso, e os containers ficam fora dele. Por isso cada cenário roda em processo e grupo próprios,
com um `docker` falso no PATH, e o sinal vai para o grupo, como na VPS. Nada aqui usa Docker.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = ROOT / "deploy"
TIMEOUT_S = 20
SIGTERM_EXIT = 128 + signal.SIGTERM

# `stop` de sonda desliga o container, salvo com FAKE_PROBE_SURVIVES; `rm` recusa container vivo,
# com o texto do Docker; `stop-fim` só aparece se o cliente da parada da manutenção não morreu.
FAKE_DOCKER = """#!/bin/sh
echo "$1" >>"$FAKE_DIR/calls"
case "$1" in
  ps) echo "$FAKE_MAINTENANCE" ;;
  run) : >"$FAKE_DIR/probe-alive"; echo id ;;
  wait) sleep 30 ;;
  stop)
    case "$*" in
      *gs-exec*) [ -n "$FAKE_PROBE_SURVIVES" ] || rm -f "$FAKE_DIR/probe-alive" ;;
      *) sleep "${FAKE_MAINTENANCE_STOP_S:-0}"; echo stop-fim >>"$FAKE_DIR/calls" ;;
    esac
    ;;
  rm)
    if [ -e "$FAKE_DIR/probe-alive" ]; then
      echo "Error: cannot remove container: container is running: stop it first" >&2
      exit 1
    fi
    ;;
  inspect) echo true ;;
esac
"""

# A área falsa troca o que exige root (chown, posse) e deixa o resto do `run_probe` real.
PROBE_SCRIPT = """
import pathlib
import sys
import types

from acceptance import run
from executor_ctl import profile, workarea

area = pathlib.Path(sys.argv[1])
run.create_attempt_dir = lambda host, attempt: (area / str(attempt)).mkdir()
workarea.prepare_for_start = lambda _a, attempt, _g: types.SimpleNamespace(path=area / str(attempt))
profile.run_options = lambda *_args, **_kwargs: []
config = types.SimpleNamespace(work_area=area, runtime="runsc")
host = types.SimpleNamespace(config=config, group_id=0)
run.run_with_maintenance_suspended(lambda: [run.run_probe(host, "identity", timeout_s=60)])
"""

NO_PROBES_SCRIPT = """
from acceptance import run

run.run_with_maintenance_suspended(lambda: [run.Outcome("sonda", True, "ok")])
"""


class Fakes:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.area = root / "area"
        self.area.mkdir()
        bin_dir = root / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        docker.write_text(FAKE_DOCKER)
        docker.chmod(0o755)
        self.path = f"{bin_dir}:{os.environ['PATH']}"

    def calls(self) -> list[str]:
        calls = self.root / "calls"
        return calls.read_text().split() if calls.exists() else []

    def interrupt_at(self, script: str, position: int, **env: str) -> tuple[int, list[str], str]:
        """Roda `script` e sinaliza o grupo na `position`-ésima chamada do `docker`."""
        process = subprocess.Popen(  # noqa: S603 - script fixo deste arquivo
            [sys.executable, "-B", "-c", script, str(self.area)],
            cwd=DEPLOY_DIR,
            env={
                **os.environ,
                "PATH": self.path,
                "FAKE_DIR": str(self.root),
                "FAKE_MAINTENANCE": env.pop("FAKE_MAINTENANCE", "m4nt"),
                **env,
            },
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        deadline = time.monotonic() + TIMEOUT_S
        while len(self.calls()) < position:
            assert time.monotonic() < deadline, f"só chegou a {self.calls()}"
            time.sleep(0.01)
        os.killpg(process.pid, signal.SIGTERM)
        _, stderr = process.communicate(timeout=TIMEOUT_S)
        return process.returncode, self.calls(), stderr


@pytest.fixture
def fakes(tmp_path: Path) -> Fakes:
    return Fakes(tmp_path)


def test_sigterm_na_sonda_para_o_container_antes_de_remover_e_sai_com_143(fakes: Fakes) -> None:
    code, calls, stderr = fakes.interrupt_at(PROBE_SCRIPT, position=5)

    assert code == SIGTERM_EXIT, stderr
    assert calls == ["ps", "stop", "stop-fim", "run", "wait", "stop", "rm", "start", "inspect"]
    assert list(fakes.area.iterdir()) == []


def test_limpeza_que_falha_nao_troca_a_saida_143_nem_deixa_a_area(fakes: Fakes) -> None:
    code, calls, stderr = fakes.interrupt_at(PROBE_SCRIPT, position=5, FAKE_PROBE_SURVIVES="1")

    assert code == SIGTERM_EXIT, stderr
    assert "não descartada" in stderr
    assert list(fakes.area.iterdir()) == []
    assert calls[-2:] == ["start", "inspect"]


def test_sigterm_com_a_manutencao_ja_parada_tambem_limpa_a_sonda(fakes: Fakes) -> None:
    code, calls, stderr = fakes.interrupt_at(PROBE_SCRIPT, position=3, FAKE_MAINTENANCE="")

    assert code == SIGTERM_EXIT, stderr
    assert calls == ["ps", "run", "wait", "stop", "rm"]
    assert list(fakes.area.iterdir()) == []


def test_sigterm_na_parada_da_manutencao_espera_a_parada_e_religa(fakes: Fakes) -> None:
    code, calls, stderr = fakes.interrupt_at(
        NO_PROBES_SCRIPT, position=2, FAKE_MAINTENANCE_STOP_S="1"
    )

    assert code == SIGTERM_EXIT, stderr
    assert calls == ["ps", "stop", "stop-fim", "start", "inspect"]
