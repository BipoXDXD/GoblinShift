"""deploy/storage-mark.sh e o serviço `storage-mark`: a sentinela de montagem do storage (3.8).

Sem a sentinela a manutenção não marca objeto nenhum como `missing` (files/missing.py). O script
só a grava com o filesystem montado; roda de verdade, com `mountpoint` e o `compose.sh` falsos.
"""

import shutil
import subprocess
from pathlib import Path, PurePosixPath

import pytest
import yaml

from goblinshift.files.storage import MOUNT_MARKER

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy" / "storage-mark.sh"
STORAGE_MOUNT = "/srv/goblinshift/storage"
SCRIPT_TIMEOUT_SECONDS = 30


def fake_command(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(0o755)


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """Cópia do script com um `compose.sh` ao lado que só registra os argumentos."""
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    shutil.copy(SCRIPT, deploy / SCRIPT.name)
    fake_command(deploy / "compose.sh", f'echo "$*" >> "{tmp_path}/compose.log"')
    (tmp_path / "bin").mkdir()
    return tmp_path


def run(
    sandbox: Path, *, mounted: bool, args: tuple[str, ...] = ()
) -> subprocess.CompletedProcess[str]:
    fake_command(
        sandbox / "bin" / "mountpoint",
        f'echo "$*" >> "{sandbox}/mountpoint.log"; exit {0 if mounted else 1}',
    )
    return subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
        ["bash", str(sandbox / "deploy" / SCRIPT.name), *args],  # noqa: S607
        env={"PATH": f"{sandbox / 'bin'}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=SCRIPT_TIMEOUT_SECONDS,
        check=False,
    )


def compose_calls(sandbox: Path) -> list[str]:
    log = sandbox / "compose.log"
    return log.read_text().splitlines() if log.exists() else []


def test_com_o_storage_montado_grava_a_sentinela_pelo_servico(sandbox: Path) -> None:
    result = run(sandbox, mounted=True)

    assert result.returncode == 0, result.stderr
    assert compose_calls(sandbox) == ["run --rm --no-TTY storage-mark"]
    assert (sandbox / "mountpoint.log").read_text().split() == ["-q", STORAGE_MOUNT]


def test_sem_montagem_falha_sem_gravar(sandbox: Path) -> None:
    result = run(sandbox, mounted=False)

    assert result.returncode != 0
    assert STORAGE_MOUNT in result.stderr
    assert compose_calls(sandbox) == []


@pytest.mark.parametrize(("mounted", "ok"), [(True, True), (False, False)])
def test_check_so_confere_a_montagem(sandbox: Path, mounted: bool, ok: bool) -> None:
    result = run(sandbox, mounted=mounted, args=("check",))

    assert (result.returncode == 0) is ok
    assert compose_calls(sandbox) == []


def test_release_confere_a_montagem_antes_do_pull_e_grava_depois_do_storage_init() -> None:
    lines = [line.strip() for line in (ROOT / "deploy" / "release.sh").read_text().splitlines()]

    check = lines.index("deploy/storage-mark.sh check")
    pull = next(i for i, line in enumerate(lines) if line.endswith("pull --quiet"))
    init = lines.index('"${compose[@]}" run --rm --no-TTY storage-init')
    mark = lines.index("deploy/storage-mark.sh")

    assert check < pull < init < mark


def compose_services() -> dict[str, dict[str, object]]:
    services: dict[str, dict[str, object]] = yaml.safe_load((ROOT / "compose.yaml").read_text())[
        "services"
    ]
    return services


def test_sentinela_do_compose_fica_na_raiz_que_a_aplicacao_le() -> None:
    services = compose_services()
    environment = services["worker"]["environment"]
    assert isinstance(environment, dict)
    storage_root = PurePosixPath(environment["GS_STORAGE_ROOT"])
    mark = services["storage-mark"]["command"]
    init = services["storage-init"]["command"]
    assert isinstance(mark, list) and isinstance(init, list)

    maintenance = services["maintenance"]["environment"]
    assert isinstance(maintenance, dict)

    assert PurePosixPath(mark[-1]) == storage_root / MOUNT_MARKER
    assert PurePosixPath(init[-1]) == storage_root
    assert maintenance["GS_STORAGE_ROOT"] == str(storage_root)


def test_storage_mark_fica_fora_do_up() -> None:
    assert compose_services()["storage-mark"]["profiles"] == ["storage-mark"]
