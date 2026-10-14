"""Leitura do `docker inspect` e tradução dos erros do CLI, sem Docker."""

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from executor_ctl.docker_cli import (
    ContainerRunningError,
    ContainerStatus,
    DockerCli,
    DockerUnavailableError,
    parse_inspect,
)

NEVER = "0001-01-01T00:00:00Z"
STARTED = "2026-10-14T04:10:16.315791338Z"
FINISHED = "2026-10-14T04:10:46.1Z"


def inspect_output(**state: Any) -> str:
    base = {
        "Status": "running",
        "Running": True,
        "OOMKilled": False,
        "ExitCode": 0,
        "StartedAt": STARTED,
        "FinishedAt": NEVER,
    }
    return json.dumps([{"Name": "/gs-exec-x", "State": {**base, **state}}])


def test_container_rodando_nao_tem_codigo_de_saida_nem_fim() -> None:
    assert parse_inspect(inspect_output()) == ContainerStatus(
        state="running", exit_code=None, oom_killed=False, started_at=STARTED, finished_at=None
    )


def test_container_terminado_traz_codigo_oom_e_horarios() -> None:
    output = inspect_output(
        Status="exited", Running=False, ExitCode=137, OOMKilled=True, FinishedAt=FINISHED
    )

    assert parse_inspect(output) == ContainerStatus(
        state="exited", exit_code=137, oom_killed=True, started_at=STARTED, finished_at=FINISHED
    )


def test_container_criado_e_nunca_iniciado_nao_tem_horarios() -> None:
    output = inspect_output(Status="created", Running=False, StartedAt=NEVER)

    assert parse_inspect(output) == ContainerStatus(
        state="created", exit_code=None, oom_killed=False, started_at=None, finished_at=None
    )


@pytest.mark.parametrize("docker_status", ["paused", "restarting"])
def test_pausado_ou_reiniciando_conta_como_rodando(docker_status: str) -> None:
    assert parse_inspect(inspect_output(Status=docker_status)).state == "running"


@pytest.mark.parametrize("docker_status", ["dead", "removing"])
def test_morto_ou_em_remocao_conta_como_terminado(docker_status: str) -> None:
    output = inspect_output(Status=docker_status, Running=False, ExitCode=1)

    assert parse_inspect(output) == ContainerStatus(
        state="exited", exit_code=1, oom_killed=False, started_at=STARTED, finished_at=None
    )


def test_status_absent_vira_json_do_contrato() -> None:
    assert ContainerStatus.absent().to_json() == {
        "state": "absent",
        "exit_code": None,
        "oom_killed": False,
        "started_at": None,
        "finished_at": None,
    }


@pytest.mark.parametrize(
    "output",
    [
        "não é json",
        "[]",
        json.dumps([{"State": {"Status": "inventado"}}]),
        inspect_output(ExitCode="0"),
        inspect_output(StartedAt="ontem"),
    ],
)
def test_saida_inesperada_do_inspect_e_erro_explicito(output: str) -> None:
    with pytest.raises(DockerUnavailableError):
        parse_inspect(output)


def fake_docker(tmp_path: Path, stderr: str, exit_code: int) -> DockerCli:
    """Um "docker" que só escreve no stderr e sai com o código pedido."""
    script = tmp_path / "docker"
    script.write_text(
        f"#!{sys.executable}\nimport sys\nsys.stderr.write({stderr!r})\nsys.exit({exit_code})\n"
    )
    script.chmod(0o755)
    return DockerCli(binary=str(script))


def test_inspect_de_container_inexistente_e_absent(tmp_path: Path) -> None:
    docker = fake_docker(tmp_path, "error: no such object: gs-exec-x\n", 1)

    assert docker.inspect("gs-exec-x") == ContainerStatus.absent()


def test_stop_e_remove_de_container_inexistente_sao_sucesso(tmp_path: Path) -> None:
    docker = fake_docker(tmp_path, "Error response from daemon: No such container: x\n", 1)

    docker.stop("gs-exec-x", grace_s=1)
    docker.remove("gs-exec-x")


def test_remove_de_container_rodando_e_recusado(tmp_path: Path) -> None:
    docker = fake_docker(
        tmp_path,
        'Error response from daemon: cannot remove container "x": container is running\n',
        1,
    )

    with pytest.raises(ContainerRunningError):
        docker.remove("gs-exec-x")


def test_daemon_fora_do_ar_vira_docker_indisponivel(tmp_path: Path) -> None:
    docker = fake_docker(
        tmp_path, "Cannot connect to the Docker daemon at unix:///var/run/docker.sock.\n", 1
    )

    with pytest.raises(DockerUnavailableError):
        docker.inspect("gs-exec-x")


def test_binario_ausente_vira_docker_indisponivel(tmp_path: Path) -> None:
    docker = DockerCli(binary=str(tmp_path / "nao-existe"))

    with pytest.raises(DockerUnavailableError):
        docker.inspect("gs-exec-x")
