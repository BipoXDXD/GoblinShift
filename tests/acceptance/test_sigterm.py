"""SIGTERM no meio da aceitação, com `run_probe`, `DockerCli` e o subprocesso reais.

A unit não define `KillMode`: o systemd sinaliza o cgroup inteiro, inclusive o cliente `docker` em
curso, e os containers ficam fora dele. Por isso cada cenário roda em processo e grupo próprios,
com um `docker` falso no PATH que morre com o sinal, como o CLI real, e o sinal vai para o grupo.
Nada aqui usa Docker.
"""

import contextlib
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

# O CLI real é Go, e o runtime do Go não deixa bloquear SIGTERM: o cliente morre com o sinal do
# cgroup mesmo herdando a máscara e o SIG_IGN. Um `sh` herdaria os dois e sobreviveria, por isso o
# falso é Python e desfaz ambos ao nascer. O daemon são arquivos: a parada começa no primeiro `stop`
# e sobrevive à morte do cliente; `start` durante ela sai com 0 sem mudar nada (304).
FAKE_WRAPPER = """#!/bin/sh
exec "$FAKE_PYTHON" -I "$FAKE_DIR/docker.py" "$@"
"""
FAKE_DOCKER = """
import os
import signal
import sys
import time
from pathlib import Path

signal.signal(signal.SIGTERM, signal.SIG_DFL)
signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM})

fake = Path(os.environ["FAKE_DIR"])
stopping = fake / "maintenance-stopping"
probe_alive = fake / "probe-alive"
command, *rest = sys.argv[1:]
is_probe = bool(rest) and "gs-exec" in rest[-1]


def wait_for_stop():
    time.sleep(max(0, float(stopping.read_text()) - time.time()))


if command == "stop" and not is_probe and not stopping.exists():
    stopping.write_text(str(time.time() + float(os.environ.get("FAKE_STOP_S", "0"))))
# Só o primeiro `start` é lento, e a marca vem antes do registro da chamada: o teste sinaliza o
# grupo assim que a vê, e o sinal entre o registro e a marca deixava o `start` repetido dormir o
# FAKE_START_S inteiro (30 s, além do TIMEOUT_S).
slow_start = fake / "slow-start-done"
slow = command == "start" and bool(os.environ.get("FAKE_START_S")) and not slow_start.exists()
if slow:
    slow_start.touch()
with (fake / "calls").open("a") as calls:
    calls.write(command + "\\n")

if command == "ps":
    print(os.environ["FAKE_MAINTENANCE"])
elif command == "run":
    probe_alive.touch()
    print("id")
elif command == "wait":
    time.sleep(30)
elif command == "stop" and is_probe:
    if not os.environ.get("FAKE_PROBE_SURVIVES"):
        probe_alive.unlink(missing_ok=True)
elif command == "stop":
    wait_for_stop()
elif command == "rm" and probe_alive.exists():
    print("Error: cannot remove container: container is running: stop it first", file=sys.stderr)
    sys.exit(1)
elif command == "start":
    if slow:
        time.sleep(float(os.environ["FAKE_START_S"]))
    if stopping.exists() and time.time() >= float(stopping.read_text()):
        stopping.unlink()
elif command == "inspect":
    print("true")
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

# A carga do Image Forge divide a área e a sonda com o `run_probe`.
LOAD_SCRIPT = """
import pathlib
import sys
import types

from acceptance import image_forge_load as load
from acceptance import run
from executor_ctl import profile, workarea

area = pathlib.Path(sys.argv[1])


def create_attempt_dir(_host, attempt):
    (area / str(attempt)).mkdir()


run.create_attempt_dir = create_attempt_dir
load.create_attempt_dir = create_attempt_dir
load._fill_work_dir = lambda *_args: None
workarea.prepare_for_start = lambda _a, attempt, _g: types.SimpleNamespace(path=area / str(attempt))
profile.run_options = lambda *_args, **_kwargs: []
config = types.SimpleNamespace(work_area=area, runtime="runsc")
host = types.SimpleNamespace(config=config, group_id=0)
run.run_with_maintenance_suspended(
    lambda: [load.measure(host, area, {}, criterion="carga")]
)
"""

NO_PROBES_SCRIPT = """
from acceptance import run

run.run_with_maintenance_suspended(lambda: [run.Outcome("sonda", True, "ok")])
"""

# Um cenário preso: ignora o SIGTERM e não sai sozinho. O `docker` falso morre com o sinal se ainda
# estiver rodando, e o resultado dele não importa aqui (sem `check`).
IGNORES_SIGTERM_SCRIPT = """
import signal
import subprocess
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
subprocess.run(["docker", "ps"], check=False, capture_output=True)
time.sleep(60)
"""


class Fakes:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.area = root / "area"
        self.area.mkdir()
        bin_dir = root / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        docker.write_text(FAKE_WRAPPER)
        docker.chmod(0o755)
        (root / "docker.py").write_text(FAKE_DOCKER)
        self.path = f"{bin_dir}:{os.environ['PATH']}"
        self.process: subprocess.Popen[str] | None = None

    def _env(self, **env: str) -> dict[str, str]:
        return {
            **os.environ,
            "PATH": self.path,
            "FAKE_DIR": str(self.root),
            "FAKE_PYTHON": sys.executable,
            "FAKE_MAINTENANCE": env.pop("FAKE_MAINTENANCE", "m4nt"),
            **env,
        }

    def docker(self, *args: str, **env: str) -> None:
        """Uma chamada avulsa ao `docker` falso, sem cenário em volta."""
        subprocess.run(  # noqa: S603 - o falso deste arquivo
            [str(self.root / "bin" / "docker"), *args],
            env=self._env(**env),
            capture_output=True,
            check=False,
        )

    def slow_start_claimed(self) -> bool:
        return (self.root / "slow-start-done").exists()

    def maintenance_stopped(self) -> bool:
        return (self.root / "maintenance-stopping").exists()

    def calls(self) -> list[str]:
        calls = self.root / "calls"
        return calls.read_text().split() if calls.exists() else []

    def interrupt_at(
        self, script: str, position: int, timeout_s: float = TIMEOUT_S, **env: str
    ) -> tuple[int, list[str], str]:
        """Roda `script` e sinaliza o grupo na `position`-ésima chamada do `docker`."""
        process = subprocess.Popen(  # noqa: S603 - script fixo deste arquivo
            [sys.executable, "-B", "-c", script, str(self.area)],
            cwd=DEPLOY_DIR,
            env=self._env(**env),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        self.process = process
        try:
            deadline = time.monotonic() + timeout_s
            while len(self.calls()) < position:
                assert time.monotonic() < deadline, f"só chegou a {self.calls()}"
                time.sleep(0.01)
            os.killpg(process.pid, signal.SIGTERM)
            _, stderr = process.communicate(timeout=timeout_s)
        except BaseException:
            # O cenário vivo depois da falha vazava como ResourceWarning no teste seguinte.
            _kill_and_reap(process)
            raise
        return process.returncode, self.calls(), stderr


def _kill_and_reap(process: subprocess.Popen[str]) -> None:
    """SIGKILL no grupo do cenário (o script e o `docker` falso em curso) e colheita do líder."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.communicate()


@pytest.fixture
def fakes(tmp_path: Path) -> Fakes:
    return Fakes(tmp_path)


def test_sigterm_na_sonda_para_o_container_antes_de_remover_e_sai_com_143(fakes: Fakes) -> None:
    code, calls, stderr = fakes.interrupt_at(PROBE_SCRIPT, position=4)

    assert code == SIGTERM_EXIT, stderr
    assert calls == ["ps", "stop", "run", "wait", "stop", "rm", "start", "inspect"]
    assert list(fakes.area.iterdir()) == []


def test_limpeza_que_falha_nao_troca_a_saida_143_nem_deixa_a_area(fakes: Fakes) -> None:
    code, calls, stderr = fakes.interrupt_at(PROBE_SCRIPT, position=4, FAKE_PROBE_SURVIVES="1")

    assert code == SIGTERM_EXIT, stderr
    assert "não descartada" in stderr
    assert list(fakes.area.iterdir()) == []
    assert calls[-2:] == ["start", "inspect"]


def test_sigterm_com_a_manutencao_ja_parada_tambem_limpa_a_sonda(fakes: Fakes) -> None:
    code, calls, stderr = fakes.interrupt_at(PROBE_SCRIPT, position=3, FAKE_MAINTENANCE="")

    assert code == SIGTERM_EXIT, stderr
    assert calls == ["ps", "run", "wait", "stop", "rm"]
    assert list(fakes.area.iterdir()) == []


def test_sigterm_na_carga_do_image_forge_para_o_container_antes_de_remover(fakes: Fakes) -> None:
    code, calls, stderr = fakes.interrupt_at(LOAD_SCRIPT, position=4)

    assert code == SIGTERM_EXIT, stderr
    assert calls == ["ps", "stop", "run", "wait", "stop", "rm", "start", "inspect"]
    assert list(fakes.area.iterdir()) == []


def test_cliente_morto_na_parada_repete_o_stop_e_so_entao_religa(fakes: Fakes) -> None:
    code, calls, stderr = fakes.interrupt_at(NO_PROBES_SCRIPT, position=2, FAKE_STOP_S="0.5")

    assert code == SIGTERM_EXIT, stderr
    assert calls == ["ps", "stop", "stop", "start", "inspect"]
    assert not fakes.maintenance_stopped()


def test_cliente_morto_no_start_religa_de_novo_e_rele_o_estado(fakes: Fakes) -> None:
    code, calls, stderr = fakes.interrupt_at(NO_PROBES_SCRIPT, position=3, FAKE_START_S="30")

    assert code == SIGTERM_EXIT, stderr
    assert calls == ["ps", "stop", "start", "start", "inspect"]
    assert not fakes.maintenance_stopped()


def test_start_lento_fica_marcado_antes_de_a_chamada_aparecer(fakes: Fakes) -> None:
    # O teste sinaliza o grupo assim que vê a chamada: se a marca do start lento viesse depois do
    # registro, o sinal podia cair entre os dois e o start repetido dormiria os 30 s inteiros.
    # Sem onde registrar a chamada (`calls` é pasta), só a ordem decide se a marca já existe.
    (fakes.root / "calls").mkdir()

    fakes.docker("start", FAKE_START_S="30")

    assert fakes.slow_start_claimed()


def test_cenario_que_estoura_o_prazo_mata_e_colhe_o_grupo(fakes: Fakes) -> None:
    with pytest.raises(subprocess.TimeoutExpired):
        fakes.interrupt_at(IGNORES_SIGTERM_SCRIPT, position=1, timeout_s=0.5)

    assert fakes.process is not None
    assert fakes.process.returncode is not None
    assert fakes.process.stderr is not None and fakes.process.stderr.closed
    with pytest.raises(ProcessLookupError):
        os.killpg(fakes.process.pid, 0)
