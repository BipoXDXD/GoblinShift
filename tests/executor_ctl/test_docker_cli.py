"""Leitura do `docker inspect` e tradução dos erros do CLI, sem Docker."""

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from executor_ctl.docker_cli import (
    ContainerIdentity,
    ContainerRunningError,
    ContainerStatus,
    DockerCli,
    DockerUnavailableError,
    parse_inspect,
)

pytestmark = pytest.mark.host_python

NEVER = "0001-01-01T00:00:00Z"
STARTED = "2026-10-14T04:10:16.315791338Z"
FINISHED = "2026-10-14T04:10:46.1Z"
CONTAINER_ID = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
ATTEMPT = "0193a3f0-7b2c-7def-8a01-23456789abcd"
IMAGE = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "a" * 64
IDENTITY = ContainerIdentity(container_id=CONTAINER_ID, attempt_label=ATTEMPT, image=IMAGE)
OWN_LABELS = {"goblinshift.attempt": ATTEMPT, "org.opencontainers.image.title": "executor"}


def inspect_info(
    *, labels: dict[str, Any] | None = None, image: Any = IMAGE, **state: Any
) -> dict[str, Any]:
    base = {
        "Status": "running",
        "Running": True,
        "OOMKilled": False,
        "ExitCode": 0,
        "StartedAt": STARTED,
        "FinishedAt": NEVER,
    }
    return {
        "Id": CONTAINER_ID,
        "Name": "/gs-exec-x",
        "State": {**base, **state},
        "Config": {"Image": image, "Labels": OWN_LABELS if labels is None else labels},
    }


def inspect_output(
    *, labels: dict[str, Any] | None = None, image: Any = IMAGE, **state: Any
) -> str:
    return json.dumps([inspect_info(labels=labels, image=image, **state)])


def test_container_rodando_nao_tem_codigo_de_saida_nem_fim() -> None:
    assert parse_inspect(inspect_output()) == ContainerStatus(
        state="running",
        exit_code=None,
        oom_killed=False,
        started_at=STARTED,
        finished_at=None,
        identity=IDENTITY,
    )


def test_container_terminado_traz_codigo_oom_e_horarios() -> None:
    output = inspect_output(
        Status="exited", Running=False, ExitCode=137, OOMKilled=True, FinishedAt=FINISHED
    )

    assert parse_inspect(output) == ContainerStatus(
        state="exited",
        exit_code=137,
        oom_killed=True,
        started_at=STARTED,
        finished_at=FINISHED,
        identity=IDENTITY,
    )


def test_container_criado_e_nunca_iniciado_nao_tem_horarios() -> None:
    output = inspect_output(Status="created", Running=False, StartedAt=NEVER)

    assert parse_inspect(output) == ContainerStatus(
        state="created",
        exit_code=None,
        oom_killed=False,
        started_at=None,
        finished_at=None,
        identity=IDENTITY,
    )


@pytest.mark.parametrize("docker_status", ["paused", "restarting"])
def test_pausado_ou_reiniciando_conta_como_rodando(docker_status: str) -> None:
    assert parse_inspect(inspect_output(Status=docker_status)).state == "running"


@pytest.mark.parametrize("docker_status", ["dead", "removing"])
def test_morto_ou_em_remocao_conta_como_terminado(docker_status: str) -> None:
    output = inspect_output(Status=docker_status, Running=False, ExitCode=1)

    assert parse_inspect(output) == ContainerStatus(
        state="exited",
        exit_code=1,
        oom_killed=False,
        started_at=STARTED,
        finished_at=None,
        identity=IDENTITY,
    )


def test_status_absent_vira_json_do_contrato() -> None:
    assert ContainerStatus.absent().to_json() == {
        "state": "absent",
        "exit_code": None,
        "oom_killed": False,
        "started_at": None,
        "finished_at": None,
    }


def test_inspect_le_id_label_e_imagem() -> None:
    status = parse_inspect(
        inspect_output(labels={"goblinshift.attempt": "outra"}, image="x@sha256:b")
    )

    assert status.identity == ContainerIdentity(
        container_id=CONTAINER_ID, attempt_label="outra", image="x@sha256:b"
    )


def without(key: str, section: str | None = None) -> str:
    info = inspect_info()
    del (info if section is None else info[section])[key]
    return json.dumps([info])


@pytest.mark.parametrize(
    "output",
    [
        without("Config"),
        without("Id"),
        without("Image", "Config"),
        json.dumps([{**inspect_info(), "Id": "gs-exec-x"}]),
        json.dumps([{**inspect_info(), "Id": CONTAINER_ID.upper()}]),
        json.dumps([{**inspect_info(), "Config": "texto"}]),
        inspect_output(image=None),
        inspect_output(labels={"goblinshift.attempt": 1}),
        json.dumps([{**inspect_info(), "Config": {"Image": IMAGE, "Labels": ["x"]}}]),
    ],
    ids=[
        "sem_config",
        "sem_id",
        "sem_imagem",
        "id_que_e_nome",
        "id_maiusculo",
        "config_texto",
        "imagem_nula",
        "label_numerica",
        "labels_lista",
    ],
)
def test_inspect_sem_config_e_formato_inesperado(output: str) -> None:
    with pytest.raises(DockerUnavailableError):
        parse_inspect(output)


def test_labels_nulas_viram_label_ausente() -> None:
    output = json.dumps([{**inspect_info(), "Config": {"Image": IMAGE, "Labels": None}}])

    identity = parse_inspect(output).identity

    assert identity is not None
    assert identity.attempt_label is None


def test_container_sem_a_label_da_tentativa_tem_label_ausente() -> None:
    identity = parse_inspect(inspect_output(labels={"outro.projeto": ATTEMPT})).identity

    assert identity is not None
    assert identity.attempt_label is None


def test_identidade_fica_fora_do_json_do_contrato() -> None:
    assert parse_inspect(inspect_output()).to_json() == {
        "state": "running",
        "exit_code": None,
        "oom_killed": False,
        "started_at": STARTED,
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


# Texto real do CLI do Docker 29 com o socket ausente: tem "no such", mas não fala do container.
DAEMON_UNREACHABLE = (
    "failed to connect to the docker API at unix:///var/run/docker.sock; check if the path is "
    "correct and if the daemon is running: dial unix /var/run/docker.sock: connect: no such file "
    "or directory\n"
)


def test_inspect_com_daemon_inacessivel_nao_vira_absent(tmp_path: Path) -> None:
    docker = fake_docker(tmp_path, DAEMON_UNREACHABLE, 1)

    with pytest.raises(DockerUnavailableError):
        docker.inspect("gs-exec-x")


def test_stop_com_daemon_inacessivel_nao_vira_sucesso(tmp_path: Path) -> None:
    docker = fake_docker(tmp_path, DAEMON_UNREACHABLE, 1)

    with pytest.raises(DockerUnavailableError):
        docker.stop("gs-exec-x", grace_s=1)


def test_remove_com_daemon_inacessivel_nao_vira_sucesso(tmp_path: Path) -> None:
    docker = fake_docker(tmp_path, DAEMON_UNREACHABLE, 1)

    with pytest.raises(DockerUnavailableError):
        docker.remove("gs-exec-x")


def test_imagem_que_o_inspect_acha_esta_presente(tmp_path: Path) -> None:
    assert fake_docker(tmp_path, "", 0).image_present("repo@sha256:" + "a" * 64) is True


@pytest.mark.parametrize(
    "stderr",
    [
        "Error response from daemon: No such image: repo@sha256:aaa\n",
        "Error: No such image: repo@sha256:aaa\n",
    ],
)
def test_imagem_que_o_daemon_nao_tem_esta_ausente(tmp_path: Path, stderr: str) -> None:
    assert fake_docker(tmp_path, stderr, 1).image_present("repo@sha256:" + "a" * 64) is False


@pytest.mark.parametrize(
    "stderr",
    [
        DAEMON_UNREACHABLE,
        "Cannot connect to the Docker daemon at unix:///var/run/docker.sock.\n",
        # Os marcadores de container não significam imagem ausente.
        "Error: No such object: repo@sha256:aaa\n",
        "Error response from daemon: toomanyrequests\n",
        "",
    ],
    ids=["socket-ausente", "daemon-fora", "objeto", "outro-erro", "sem-mensagem"],
)
def test_erro_que_nao_nomeia_a_imagem_nunca_vira_ausente(tmp_path: Path, stderr: str) -> None:
    docker = fake_docker(tmp_path, stderr, 1)

    with pytest.raises(DockerUnavailableError):
        docker.image_present("repo@sha256:" + "a" * 64)


def test_sonda_com_binario_ausente_vira_docker_indisponivel(tmp_path: Path) -> None:
    with pytest.raises(DockerUnavailableError):
        DockerCli(binary=str(tmp_path / "nao-existe")).image_present("repo@sha256:" + "a" * 64)


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
