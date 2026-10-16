"""O start com as capabilities da unit do executor-ctl, como root fora do grupo gs-exec.

Em produção o ctl é root (gid 0, sem grupos suplementares) e o grupo de `<id>` é o gs-exec. Sem
CAP_FSETID, o kernel limpa em silêncio o setgid de um chmod feito por quem não é do grupo do
arquivo. Aqui a unit gerada pelo install.sh fornece a lista de capabilities, e o kernel do
container decide, como na VPS.
"""

import re
import subprocess

import pytest

from tests.host_sandbox import IMAGE, ROOT, TIMEOUT_S

pytestmark = pytest.mark.host_installer

INSTALLER = ROOT / "deploy" / "executor-ctl" / "install.sh"
EXECUTOR_GID = 61001
WORKER_UID = 10001

START_AS_CTL = f"""
import os, stat, uuid
from pathlib import Path
from executor_ctl import workarea

area = Path("/srv/exec-work")
area.mkdir()
os.chown(area, 0, {EXECUTOR_GID})
os.chmod(area, 0o3770)
attempt = uuid.UUID("0193a3f0-7b2c-7def-8a01-23456789abcd")
work_dir = workarea.attempt_dir(area, attempt)
(work_dir / "inputs").mkdir(parents=True)
(work_dir / "inputs" / "0.csv").write_text("a,b\\n")
for path in (work_dir, work_dir / "inputs", work_dir / "inputs" / "0.csv"):
    os.chown(path, {WORKER_UID}, {EXECUTOR_GID})
workarea.prepare_for_start(area, attempt, {EXECUTOR_GID})
for name in (".", "inputs", "inputs/0.csv", "out"):
    print(name, oct(stat.S_IMODE((work_dir / name).lstat().st_mode)))
"""


def unit_capabilities() -> list[str]:
    match = re.search(r"^CapabilityBoundingSet=(.+)$", INSTALLER.read_text(), re.MULTILINE)
    assert match is not None
    return match[1].split()


def test_start_mantem_setgid_e_sticky_com_as_capabilities_da_unit() -> None:
    cap_args = [f"--cap-add={capability}" for capability in unit_capabilities()]

    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--cap-drop=ALL",
            *cap_args,
            f"--volume={ROOT / 'deploy'}:/src/deploy:ro",
            "--workdir=/src/deploy",
            IMAGE,
            "python3",
            "-B",
            "-c",
            START_AS_CTL,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=TIMEOUT_S,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split("\n") == [
        ". 0o3770",
        "inputs 0o2750",
        "inputs/0.csv 0o640",
        "out 0o2770",
        "",
    ]
