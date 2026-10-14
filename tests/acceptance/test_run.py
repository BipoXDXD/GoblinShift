"""Suspensão da manutenção durante as sondas do teste de aceitação (deploy/acceptance/run.py).

A sonda cria `<área>/<uuid>` e o container `gs-exec-<uuid>` como uma tentativa de produção, sem
linha no banco; com a manutenção no ar, ela para e remove a sonda no ciclo seguinte. O Docker é
um falso que registra os comandos, porque o script inteiro só roda na VPS.
"""

import subprocess

import pytest
from acceptance import run
from acceptance.run import Outcome

from executor_ctl import profile

MAINTENANCE = "c0ffee"
PROBE_OUTCOME = Outcome("sonda", True, "ok")


class FakeDocker:
    def __init__(
        self,
        *,
        running: tuple[str, ...] = (MAINTENANCE,),
        start_fails: bool = False,
        running_after_start: str = "true",
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self._running = running
        self._start_fails = start_fails
        self._running_after_start = running_after_start

    def __call__(self, *args: str) -> str:
        self.calls.append(args)
        if args[0] == "ps":
            return "\n".join(self._running) + "\n"
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


def _probe_run(log_config: dict[str, object]) -> run.ProbeRun:
    memory = profile.MEMORY_BYTES
    host_config = {
        "Runtime": "runsc",
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
        "kernel": "4.4.0",
        "uid": profile.EXECUTOR_UID,
        "root_write_error": "EROFS",
        "docker_sock_exists": False,
    }
    state = run.ExecutorState(
        running=False, exit_code=0, oom_killed=False, error="", host_config=host_config
    )
    return run.ProbeRun(state, [report], "", 1.0, timed_out=False)


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
