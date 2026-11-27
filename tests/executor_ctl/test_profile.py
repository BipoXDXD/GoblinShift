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


def test_io_limits_vira_as_quatro_flags_no_no_real_do_dispositivo() -> None:
    # O Docker separa dispositivo e valor no PRIMEIRO `:`: o caminho não pode ter `:` (/dev/loop3).
    throttle = profile.DiskThrottle(device="/dev/loop3", limits=LIMITS)

    arguments = profile.run_arguments(
        ATTEMPT, IMAGE, work_dir=WORK_DIR, runtime="runsc", executor_gid=61001, throttle=throttle
    )

    assert [argument for argument in arguments if argument.startswith("--device")] == [
        "--device-read-bps=/dev/loop3:104857600",
        "--device-write-bps=/dev/loop3:52428800",
        "--device-read-iops=/dev/loop3:2000",
        "--device-write-iops=/dev/loop3:1000",
    ]
    assert arguments[-1] == IMAGE


def test_block_device_segue_o_link_do_udev_ate_o_no_real(monkeypatch: pytest.MonkeyPatch) -> None:
    links = {"/dev/block/7:3": "/dev/loop3"}
    monkeypatch.setattr(os.path, "realpath", lambda path: links.get(path, path))

    assert profile.block_device(os.makedev(7, 3)) == "/dev/loop3"


def test_disk_throttle_resolve_o_dispositivo_do_st_dev_da_area(tmp_path: Path) -> None:
    seen: list[int] = []

    def resolve(st_dev: int) -> str:
        seen.append(st_dev)
        return "/dev/loop3"

    throttle = profile.disk_throttle(tmp_path, LIMITS, resolve_device=resolve)

    assert throttle == profile.DiskThrottle(device="/dev/loop3", limits=LIMITS)
    assert seen == [os.stat(tmp_path).st_dev]


def test_disk_throttle_sem_io_limits_nao_resolve_nada(tmp_path: Path) -> None:
    def resolve(_st_dev: int) -> str:
        raise AssertionError("sem teto não há dispositivo a resolver")

    assert profile.disk_throttle(tmp_path / "nem-existe", None, resolve_device=resolve) is None


@pytest.mark.parametrize("device", ["/dev/block/7:3", "loop3", "/dev/loop3:1", ""])
def test_dispositivo_com_dois_pontos_ou_fora_de_dev_e_recusado(tmp_path: Path, device: str) -> None:
    with pytest.raises(profile.UnusableDiskDeviceError):
        profile.disk_throttle(tmp_path, LIMITS, resolve_device=lambda _st_dev: device)
