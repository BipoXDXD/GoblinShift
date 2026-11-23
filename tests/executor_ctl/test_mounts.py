"""Opções de montagem da área (item 36b, D12): o ctl só sobe com `nosuid`, `nodev` e `noexec`."""

import logging
import os
from pathlib import Path

import pytest

from executor_ctl import __main__ as main_module
from executor_ctl import profile
from executor_ctl.__main__ import EXIT_HOST_NOT_READY
from executor_ctl.config import Config
from executor_ctl.mounts import mount_options
from executor_ctl.profile import IoLimits

pytestmark = pytest.mark.host_python

AREA = Path("/srv/goblinshift/exec-work")
ROOT_LINE = "22 1 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw,errors=remount-ro"
SAFE_AREA_LINE = (
    "36 22 7:2 / /srv/goblinshift/exec-work rw,nosuid,nodev,noexec,noatime shared:9 "
    "- ext4 /dev/loop2 rw"
)
PLAIN_AREA_LINE = "36 22 7:2 / /srv/goblinshift/exec-work rw,noatime shared:9 - ext4 /dev/loop2 rw"


def test_opcoes_vem_da_linha_do_ponto_de_montagem() -> None:
    text = f"{ROOT_LINE}\n{SAFE_AREA_LINE}\n"

    assert mount_options(text, AREA) == frozenset({"rw", "nosuid", "nodev", "noexec", "noatime"})


def test_montagem_empilhada_vale_a_ultima() -> None:
    text = f"{ROOT_LINE}\n{SAFE_AREA_LINE}\n{PLAIN_AREA_LINE}\n"

    assert mount_options(text, AREA) == frozenset({"rw", "noatime"})


def test_caminho_sem_montagem_propria_nao_tem_opcoes() -> None:
    assert mount_options(f"{ROOT_LINE}\n", AREA) == frozenset()


def test_prefixo_do_caminho_nao_conta_como_a_area() -> None:
    sibling = SAFE_AREA_LINE.replace("/exec-work ", "/exec-work-old ")

    assert mount_options(f"{ROOT_LINE}\n{sibling}\n", AREA) == frozenset()


def test_espaco_escapado_no_mountinfo_e_decodificado() -> None:
    line = "40 22 7:3 / /srv/area\\040nova rw,nosuid shared:2 - ext4 /dev/loop3 rw"

    assert mount_options(line, Path("/srv/area nova")) == frozenset({"rw", "nosuid"})


def _main_with_mountinfo(monkeypatch: pytest.MonkeyPatch, mountinfo: str) -> tuple[int, list[str]]:
    """Roda `main` com a área "montada" e o `mountinfo` dado; devolve os grupos consultados."""
    config = Config(
        socket=Path("/run/goblinshift/executor-ctl.sock"),
        group="gs-exec",
        socket_group="gs-ctl",
        work_area=AREA,
        runtime="runsc",
        images_file=Path("/var/lib/coolify-release/goblinshift/executor-images.json"),
        require_mount=True,
    )
    looked_up: list[str] = []

    def no_group(name: str) -> None:
        looked_up.append(name)

    monkeypatch.setattr(main_module, "configure_logging", lambda: None)
    monkeypatch.setattr(main_module, "load_config", lambda _path: config)
    monkeypatch.setattr(os.path, "ismount", lambda path: Path(path) == AREA)
    monkeypatch.setattr(main_module, "_group_id", no_group)
    code = main_module.main(["executor-ctl.json"], read_mountinfo=lambda: mountinfo)
    return code, looked_up


def test_ctl_recusa_area_sem_noexec(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR, logger="executor_ctl")

    code, looked_up = _main_with_mountinfo(monkeypatch, f"{ROOT_LINE}\n{PLAIN_AREA_LINE}\n")

    assert code == EXIT_HOST_NOT_READY
    assert looked_up == []
    assert [record.getMessage() for record in caplog.records] == [
        "área montada sem nosuid,nodev,noexec"
    ]


@pytest.mark.parametrize("missing", ["nosuid", "nodev", "noexec"])
def test_ctl_recusa_area_sem_qualquer_uma_das_tres(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    line = SAFE_AREA_LINE.replace(f",{missing}", "")

    code, looked_up = _main_with_mountinfo(monkeypatch, f"{ROOT_LINE}\n{line}\n")

    assert code == EXIT_HOST_NOT_READY
    assert looked_up == []


def test_ctl_com_a_area_protegida_segue_para_os_grupos(monkeypatch: pytest.MonkeyPatch) -> None:
    _, looked_up = _main_with_mountinfo(monkeypatch, f"{ROOT_LINE}\n{SAFE_AREA_LINE}\n")

    assert looked_up == ["gs-exec", "gs-ctl"]


def test_ctl_recusa_subir_com_io_limits_e_dispositivo_sem_no_real(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Sem o link do udev, /dev/block/M:m fica com `:` e todo start falharia no Docker.
    caplog.set_level(logging.ERROR, logger="executor_ctl")
    config = Config(
        socket=Path("/run/goblinshift/executor-ctl.sock"),
        group="gs-exec",
        socket_group="gs-ctl",
        work_area=tmp_path,
        runtime="runsc",
        images_file=Path("/var/lib/coolify-release/goblinshift/executor-images.json"),
        require_mount=False,
        io_limits=IoLimits(read_bps=1, write_bps=2, read_iops=3, write_iops=4),
    )
    looked_up: list[str] = []
    monkeypatch.setattr(main_module, "configure_logging", lambda: None)
    monkeypatch.setattr(main_module, "load_config", lambda _path: config)
    monkeypatch.setattr(main_module, "_group_id", looked_up.append)
    monkeypatch.setattr(profile, "block_device", lambda _st_dev: "/dev/block/7:3")

    code = main_module.main(["executor-ctl.json"])

    assert code == EXIT_HOST_NOT_READY
    assert looked_up == []
    assert [record.getMessage() for record in caplog.records] == [
        "dispositivo da área sem nó utilizável para o teto de I/O"
    ]
