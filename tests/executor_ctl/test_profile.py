"""Perfil fixo do `docker run` do executor (docs/GoblinShiftPlan.md, 3.6; ADR 015)."""

import uuid
from pathlib import Path

from executor_ctl import profile

ATTEMPT = uuid.UUID("0193a3f0-7b2c-7def-8a01-23456789abcd")
IMAGE = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "a" * 64
WORK_DIR = Path("/srv/goblinshift/exec-work") / str(ATTEMPT)


def test_nome_do_container_deriva_do_attempt_id() -> None:
    assert profile.container_name(ATTEMPT) == f"gs-exec-{ATTEMPT}"


def test_argumentos_do_docker_run_sao_exatamente_o_perfil_fixo() -> None:
    arguments = profile.run_arguments(
        ATTEMPT, IMAGE, work_dir=WORK_DIR, runtime="runsc", executor_gid=61001
    )

    assert arguments == [
        "run",
        "--detach",
        f"--name=gs-exec-{ATTEMPT}",
        f"--label=goblinshift.attempt={ATTEMPT}",
        "--runtime=runsc",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user=61000:61001",
        "--pids-limit=256",
        "--ulimit=nproc=64",
        "--memory=2147483648",
        "--memory-swap=2147483648",
        "--cpus=1",
        "--ulimit=fsize=1073741824",
        "--pull=never",
        "--log-driver=local",
        "--log-opt=max-size=1m",
        "--log-opt=max-file=1",
        "--log-opt=compress=false",
        f"--mount=type=bind,src={WORK_DIR},dst=/work",
        f"--mount=type=bind,src={WORK_DIR}/inputs,dst=/work/inputs,readonly",
        IMAGE,
    ]


def test_imagem_e_o_ultimo_argumento_sem_comando_depois() -> None:
    # Depois da imagem viria o comando do container; o cliente nunca o escolhe.
    arguments = profile.run_arguments(
        ATTEMPT, IMAGE, work_dir=WORK_DIR, runtime="runc", executor_gid=20
    )

    assert arguments[-1] == IMAGE
    assert arguments.count(IMAGE) == 1
    assert "--runtime=runc" in arguments
    assert "--user=61000:20" in arguments
