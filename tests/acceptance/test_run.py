"""Suspensão da manutenção durante as sondas do teste de aceitação (deploy/acceptance/run.py).

A sonda cria `<área>/<uuid>` e o container `gs-exec-<uuid>` como uma tentativa de produção, sem
linha no banco; com a manutenção no ar, ela para e remove a sonda no ciclo seguinte. O Docker é
um falso que registra os comandos, porque o script inteiro só roda na VPS.
"""

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest
from acceptance import run
from acceptance.run import Outcome

from executor_ctl import profile

DEPLOY_DIR = Path(__file__).resolve().parents[2] / "deploy"
MAINTENANCE = "c0ffee"
PROBE_OUTCOME = Outcome("sonda", True, "ok")


class FakeDocker:
    def __init__(
        self,
        *,
        running: tuple[str, ...] = (MAINTENANCE,),
        start_fails: bool = False,
        running_after_start: str = "true",
        stop_failures: int = 0,
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self._running = running
        self._start_fails = start_fails
        self._running_after_start = running_after_start
        self._stop_failures = stop_failures

    def __call__(self, *args: str) -> str:
        self.calls.append(args)
        if args[0] == "ps":
            return "\n".join(self._running) + "\n"
        if args[0] == "stop" and self._stop_failures:
            self._stop_failures -= 1
            raise subprocess.CalledProcessError(143, ["docker", *args], stderr="")
        if args[0] == "start" and self._start_fails:
            raise subprocess.CalledProcessError(1, ["docker", *args], stderr="sem memória")
        if args[0] == "inspect":
            return f"{self._running_after_start}\n"
        return ""

    def commands(self) -> list[str]:
        return [call[0] for call in self.calls]


def test_probes_run_with_maintenance_stopped() -> None:
    docker = FakeDocker()
    seen: list[str] = []

    def probes() -> list[Outcome]:
        seen.extend(docker.commands())
        return [PROBE_OUTCOME]

    run.run_with_maintenance_suspended(probes, docker)

    assert seen == ["ps", "stop"]
    assert docker.calls[0] == (
        "ps",
        "--quiet",
        "--filter=label=com.docker.compose.project=goblinshift",
        "--filter=label=com.docker.compose.service=maintenance",
    )
    assert docker.calls[1][0] == "stop"
    assert docker.calls[1][-1] == MAINTENANCE


def test_maintenance_restarted_and_verified_after_probes() -> None:
    docker = FakeDocker()

    outcomes = run.run_with_maintenance_suspended(lambda: [PROBE_OUTCOME], docker)

    assert docker.commands() == ["ps", "stop", "start", "inspect"]
    assert docker.calls[2] == ("start", MAINTENANCE)
    assert docker.calls[3][-1] == MAINTENANCE
    assert outcomes[0] == PROBE_OUTCOME
    assert outcomes[1].criterion == run.MAINTENANCE_CRITERION
    assert outcomes[1].passed


def test_maintenance_restarted_when_a_probe_raises() -> None:
    docker = FakeDocker()

    def probes() -> list[Outcome]:
        raise subprocess.CalledProcessError(125, ["docker", "run"])

    with pytest.raises(subprocess.CalledProcessError):
        run.run_with_maintenance_suspended(probes, docker)

    assert docker.commands() == ["ps", "stop", "start", "inspect"]


SIGTERM_DURING_PROBES = """
import os
import signal
import sys
import time

from acceptance import run


def docker(*args):
    print(args[0], file=sys.stderr, flush=True)
    return "m4nt\\n" if args[0] == "ps" else "true\\n"


def probes():
    os.kill(os.getpid(), signal.SIGTERM)
    time.sleep(30)


run.run_with_maintenance_suspended(probes, docker)
"""


def test_maintenance_restarted_when_sigterm_arrives_during_probes() -> None:
    # Processo próprio: sem o tratamento do sinal, o SIGTERM mata quem o recebe.
    result = subprocess.run(  # noqa: S603 - script fixo acima
        [sys.executable, "-B", "-c", SIGTERM_DURING_PROBES],
        cwd=DEPLOY_DIR,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )

    assert result.stderr.split() == ["ps", "stop", "start", "inspect"]
    assert result.returncode == 128 + signal.SIGTERM


SIGTERM_IN_DOCKER_CALL = """
import os
import signal
import sys

from acceptance import run

def docker(*args):
    print(args[0], file=sys.stderr, flush=True)
    if args[0] == sys.argv[1]:
        os.kill(os.getpid(), signal.SIGTERM)
    return "m4nt\\n" if args[0] == "ps" else "true\\n"


def probes():
    print("sondas", file=sys.stderr, flush=True)
    return []


run.run_with_maintenance_suspended(probes, docker)
"""


@pytest.mark.parametrize(
    ("command", "calls"),
    [
        ("stop", ["ps", "stop", "start", "inspect"]),
        ("start", ["ps", "stop", "sondas", "start", "inspect", "start", "inspect"]),
        ("inspect", ["ps", "stop", "sondas", "start", "inspect", "start", "inspect"]),
    ],
)
def test_sigterm_outside_the_probes_waits_for_this_process_to_read_the_state(
    command: str, calls: list[str]
) -> None:
    result = subprocess.run(  # noqa: S603 - script fixo acima
        [sys.executable, "-B", "-c", SIGTERM_IN_DOCKER_CALL, command],
        cwd=DEPLOY_DIR,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )

    assert result.stderr.split() == calls
    assert result.returncode == 128 + signal.SIGTERM


SECOND_SIGTERM_DURING_RESTART = """
import os
import signal
import subprocess
import sys
import time

from acceptance import run


def docker(*args):
    if args[0] == "start":
        os.kill(os.getpid(), signal.SIGTERM)
        raise subprocess.CalledProcessError(1, ["docker", *args], stderr="sem memória")
    return "m4nt\\n" if args[0] == "ps" else "true\\n"


def probes():
    os.kill(os.getpid(), signal.SIGTERM)
    time.sleep(30)


run.run_with_maintenance_suspended(probes, docker)
"""


def test_second_sigterm_does_not_cut_the_report_of_the_failed_restart() -> None:
    result = subprocess.run(  # noqa: S603 - script fixo acima
        [sys.executable, "-B", "-c", SECOND_SIGTERM_DURING_RESTART],
        cwd=DEPLOY_DIR,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )

    assert "FALHOU" in result.stderr
    assert "sem memória" in result.stderr
    assert result.returncode == 128 + signal.SIGTERM


def test_stop_that_did_not_finish_is_repeated_before_the_restart() -> None:
    # Cliente morto não interrompe a parada no daemon: o `start` não mudaria nada e ela terminaria
    # depois, com a manutenção parada e o `inspect` já lido.
    docker = FakeDocker(stop_failures=1)

    with pytest.raises(subprocess.CalledProcessError):
        run.run_with_maintenance_suspended(lambda: [PROBE_OUTCOME], docker)

    assert docker.commands() == ["ps", "stop", "stop", "start", "inspect"]


def test_probes_leave_the_sigterm_handler_as_it_was() -> None:
    before = signal.getsignal(signal.SIGTERM)

    run.run_with_maintenance_suspended(lambda: [PROBE_OUTCOME], FakeDocker())

    assert signal.getsignal(signal.SIGTERM) == before


def test_restart_failure_fails_the_acceptance_with_recovery_command() -> None:
    docker = FakeDocker(start_fails=True)

    outcomes = run.run_with_maintenance_suspended(lambda: [PROBE_OUTCOME], docker)

    restored = outcomes[-1]
    assert not restored.passed
    assert f"docker start {MAINTENANCE}" in restored.evidence
    assert "sem memória" in restored.evidence


def test_maintenance_not_running_after_start_fails_the_acceptance() -> None:
    docker = FakeDocker(running_after_start="false")

    outcomes = run.run_with_maintenance_suspended(lambda: [PROBE_OUTCOME], docker)

    assert not outcomes[-1].passed
    assert f"docker start {MAINTENANCE}" in outcomes[-1].evidence


def test_maintenance_already_stopped_is_left_as_is() -> None:
    docker = FakeDocker(running=())

    outcomes = run.run_with_maintenance_suspended(lambda: [PROBE_OUTCOME], docker)

    assert docker.commands() == ["ps"]
    assert outcomes[-1].passed
    assert "não estava rodando" in outcomes[-1].evidence


def _probe_run(
    log_config: dict[str, object],
    *,
    runtime: str | None = "runsc",
    sandbox_kernel: str | None = "4.4.0",
    timed_out: bool = False,
) -> run.ProbeRun:
    memory = profile.MEMORY_BYTES
    host_config = {
        "Runtime": runtime,
        "NetworkMode": "none",
        "ReadonlyRootfs": True,
        "PidsLimit": profile.SANDBOX_TASKS_LIMIT,
        "Memory": memory,
        "MemorySwap": memory,
        "NanoCpus": profile.CPUS * 1_000_000_000,
        "Ulimits": [
            {
                "Name": "fsize",
                "Soft": profile.FILE_SIZE_LIMIT_BYTES,
                "Hard": profile.FILE_SIZE_LIMIT_BYTES,
            },
            {"Name": "nproc", "Soft": profile.PROCESS_LIMIT, "Hard": profile.PROCESS_LIMIT},
        ],
        "CapDrop": ["ALL"],
        "SecurityOpt": ["no-new-privileges"],
        "LogConfig": log_config,
    }
    report = {
        "kernel": sandbox_kernel,
        "uid": profile.EXECUTOR_UID,
        "root_write_error": "EROFS",
        "docker_sock_exists": False,
    }
    state = run.ExecutorState(
        running=False, exit_code=0, oom_killed=False, error="", host_config=host_config
    )
    return run.ProbeRun(state, [report], "", 1.0, timed_out=timed_out)


def _fixed_profile(probe: run.ProbeRun) -> Outcome:
    return next(o for o in run.check_identity(probe) if o.criterion == "perfil fixo do executor")


def test_perfil_fixo_aceita_o_log_limitado_do_proprio_perfil() -> None:
    probe = _probe_run(
        {"Type": "local", "Config": {"max-size": "1m", "max-file": "1", "compress": "false"}}
    )

    assert _fixed_profile(probe).passed


@pytest.mark.parametrize(
    "log_config",
    [
        {"Type": "json-file", "Config": {"max-size": "10m", "max-file": "3"}},
        {"Type": "local", "Config": {}},
        {},
    ],
    ids=["padrao-do-host", "local-sem-limite", "ausente"],
)
def test_perfil_fixo_reprova_log_sem_o_limite_do_perfil(log_config: dict[str, object]) -> None:
    outcome = _fixed_profile(_probe_run(log_config))

    assert not outcome.passed
    assert "LogConfig" in outcome.evidence


# Kernel do host fixo: o critério compara o kernel visto no sandbox com o do host (`os.uname`).
HOST_KERNEL = "6.8.0-146-generic"
GVISOR_KERNEL = "4.4.0"
LOG_DO_PERFIL: dict[str, object] = {
    "Type": "local",
    "Config": {"max-size": "1m", "max-file": "1", "compress": "false"},
}


@pytest.fixture
def host_kernel(monkeypatch: pytest.MonkeyPatch) -> str:
    real = os.uname()
    fixed = os.uname_result((real.sysname, real.nodename, HOST_KERNEL, real.version, real.machine))
    monkeypatch.setattr(os, "uname", lambda: fixed)
    return HOST_KERNEL


def _runtime_criterion(probe: run.ProbeRun) -> Outcome:
    return next(o for o in run.check_identity(probe) if o.criterion == "container sob runsc")


@pytest.mark.usefixtures("host_kernel")
def test_container_sob_runsc_passa_com_runtime_runsc_e_kernel_do_gvisor() -> None:
    outcome = _runtime_criterion(_probe_run(LOG_DO_PERFIL))

    assert outcome.passed
    assert "runtime runsc" in outcome.evidence


@pytest.mark.parametrize(
    "runtime",
    ["runc", "io.containerd.runc.v2", "", None],
    ids=["runc", "runc-do-containerd", "vazio", "ausente"],
)
@pytest.mark.usefixtures("host_kernel")
def test_container_sob_runsc_reprova_outro_runtime_mesmo_com_kernel_diferente(
    runtime: str | None,
) -> None:
    # Kernel diferente do host isola a checagem do campo Runtime: só ela pode reprovar aqui.
    outcome = _runtime_criterion(
        _probe_run(LOG_DO_PERFIL, runtime=runtime, sandbox_kernel=GVISOR_KERNEL)
    )

    assert not outcome.passed
    assert f"runtime {runtime}" in outcome.evidence


def test_container_sob_runc_real_reprova_com_o_kernel_do_host(host_kernel: str) -> None:
    outcome = _runtime_criterion(
        _probe_run(LOG_DO_PERFIL, runtime="runc", sandbox_kernel=host_kernel)
    )

    assert not outcome.passed
    assert f"kernel visto no sandbox {host_kernel}, no host {host_kernel}" in outcome.evidence


@pytest.mark.parametrize(
    "sandbox_kernel", [HOST_KERNEL, None], ids=["kernel-do-host", "sonda-sem-kernel"]
)
@pytest.mark.usefixtures("host_kernel")
def test_container_sob_runsc_reprova_runsc_declarado_sem_kernel_proprio(
    sandbox_kernel: str | None,
) -> None:
    outcome = _runtime_criterion(_probe_run(LOG_DO_PERFIL, sandbox_kernel=sandbox_kernel))

    assert not outcome.passed


@pytest.mark.usefixtures("host_kernel")
def test_container_sob_runsc_reprova_sonda_que_passou_do_prazo() -> None:
    outcome = _runtime_criterion(_probe_run(LOG_DO_PERFIL, timed_out=True))

    assert not outcome.passed
