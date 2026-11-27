"""deploy/filesystems.sh: os compartimentos de disco do provisionamento (3.8; item 36b, D12).

Roda o script de verdade com `fallocate`, `mkfs.ext4`, `mountpoint` e `mount` falsos no PATH e um
`fstab` temporário: nenhum teste monta nada no host.
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy" / "filesystems.sh"
# `declare -A` exige bash 4+; o bash 3.2 do macOS não serve.
HOMEBREW_BASH = Path("/opt/homebrew/bin/bash")
BASH = str(HOMEBREW_BASH) if HOMEBREW_BASH.exists() else "/bin/bash"
SCRIPT_TIMEOUT_SECONDS = 30
AREA_OPTIONS = "loop,noatime,nofail,nosuid,nodev,noexec"
DEFAULT_OPTIONS = "loop,noatime,nofail"


def fake_command(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(0o755)


def run(tmp_path: Path, fstab_text: str = "") -> tuple[subprocess.CompletedProcess[str], Path]:
    """Roda o script com o `fstab` dado; `mountpoint` diz que tudo já está montado."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("fallocate", "mkfs.ext4", "mount"):
        fake_command(bin_dir / name, f'echo "{name} $*" >> "{tmp_path}/calls.log"')
    fake_command(bin_dir / "mountpoint", "exit 0")
    fstab = tmp_path / "fstab"
    fstab.write_text(fstab_text)
    result = subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
        [BASH, str(SCRIPT)],
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "GS_ROOT": str(tmp_path / "srv"),
            "FSTAB": str(fstab),
        },
        capture_output=True,
        text=True,
        timeout=SCRIPT_TIMEOUT_SECONDS,
        check=False,
    )
    return result, fstab


def fstab_options(fstab: Path) -> dict[str, str]:
    """Ponto de montagem (último componente) -> coluna de opções."""
    entries = {}
    for line in fstab.read_text().splitlines():
        _, mount_point, _, options, *_ = line.split()
        entries[Path(mount_point).name] = options
    return entries


def test_provision_monta_a_area_sem_exec_suid_e_dispositivos(tmp_path: Path) -> None:
    result, fstab = run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert fstab_options(fstab) == {
        "pg": DEFAULT_OPTIONS,
        "storage": DEFAULT_OPTIONS,
        "exec-work": AREA_OPTIONS,
        "telemetry": DEFAULT_OPTIONS,
    }


def test_linha_da_area_sem_as_opcoes_avisa_e_nao_reescreve(tmp_path: Path) -> None:
    srv = tmp_path / "srv"
    old = "".join(
        f"{srv}/images/{name}.img {srv}/{name} ext4 {DEFAULT_OPTIONS} 0 2\n"
        for name in ("pg", "storage", "exec-work", "telemetry")
    )

    result, fstab = run(tmp_path, old)

    assert result.returncode == 0, result.stderr
    assert fstab.read_text() == old
    assert f"{srv}/exec-work" in result.stderr
    assert "nosuid,nodev,noexec" in result.stderr


def test_linha_da_area_ja_com_as_opcoes_nao_avisa(tmp_path: Path) -> None:
    first, fstab = run(tmp_path)
    assert first.returncode == 0, first.stderr
    written = fstab.read_text()
    (tmp_path / "bin").rename(tmp_path / "bin-1")

    second, _ = run(tmp_path, written)

    assert second.returncode == 0, second.stderr
    assert second.stderr == ""
    assert fstab.read_text() == written


def test_provision_delega_os_filesystems_ao_script() -> None:
    provision = (ROOT / "deploy" / "provision.sh").read_text()

    assert 'bash "$(dirname "${BASH_SOURCE[0]}")/filesystems.sh"' in provision
    assert "/etc/fstab" not in provision
