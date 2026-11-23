"""Perfil fixo do `docker run` do executor (docs/GoblinShiftPlan.md, 3.6; ADR 015)."""

import os
import uuid
from pathlib import Path

import pytest

from executor_ctl import profile

pytestmark = pytest.mark.host_python

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


LIMITS = profile.IoLimits(read_bps=104857600, write_bps=52428800, read_iops=2000, write_iops=1000)


def test_sem_io_limits_nao_ha_flag_de_dispositivo() -> None:
    arguments = profile.run_arguments(
        ATTEMPT, IMAGE, work_dir=WORK_DIR, runtime="runsc", executor_gid=61001
    )

    assert not [argument for argument in arguments if argument.startswith("--device")]


def test_io_limits_vira_as_quatro_flags_no_dispositivo_da_area() -> None:
    # O dispositivo é o loop device do filesystem da área (major 7), não o disco do host.
    throttle = profile.DiskThrottle(device=profile.block_device(os.makedev(7, 3)), limits=LIMITS)

    arguments = profile.run_arguments(
        ATTEMPT, IMAGE, work_dir=WORK_DIR, runtime="runsc", executor_gid=61001, throttle=throttle
    )

    assert [argument for argument in arguments if argument.startswith("--device")] == [
        "--device-read-bps=/dev/block/7:3:104857600",
        "--device-write-bps=/dev/block/7:3:52428800",
        "--device-read-iops=/dev/block/7:3:2000",
        "--device-write-iops=/dev/block/7:3:1000",
    ]
    assert arguments[-1] == IMAGE
