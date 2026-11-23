"""Cópia temporária do install.sh que roda inteira, com raiz sintética e `systemctl` falso.

O preflight administrativo tem a suíte própria (test_install_preflight.py); aqui ele passa direto,
e o que se observa é o resto do instalador: pacote, unit, marcador de reinício e serviço. Nada
toca o host: os caminhos absolutos do script apontam para uma raiz em `tmp_path`, e os comandos
que mexeriam no sistema (grupos, usuário, montagem, systemd) são falsos.
"""

import os
import shutil
import socket as socketlib
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "deploy/executor-ctl/install.sh"
SERVICE = "goblinshift-executor-ctl.service"

# Grupos já criados com os GIDs certos: ensure_group não chama groupadd.
_GETENT = """#!/bin/sh
case "$2" in
  gs-exec) echo gs-exec:x:61001: ;;
  gs-ctl) echo gs-ctl:x:61002: ;;
  *) exit 2 ;;
esac
"""
# Serviço ativo; com SYSTEMCTL_FAIL_ONCE, o primeiro daemon-reload falha e os seguintes passam.
_SYSTEMCTL = """#!/bin/sh
echo "$*" >> "$SYSTEMCTL_LOG"
once="$SYSTEMCTL_FAIL_ONCE"
if [ "$1" = daemon-reload ] && [ -n "$once" ] && [ ! -e "$once" ]; then
  : > "$once"
  exit 1
fi
exit 0
"""
_STUBS = {
    "python3": "#!/bin/sh\nexit 0\n",
    "getent": _GETENT,
    "id": "#!/bin/sh\nexit 1\n",
    "mountpoint": "#!/bin/sh\nexit 1\n",
    # O `stat -c` do fim é do GNU; o socket aqui é do teste.
    "stat": "#!/bin/sh\nexit 0\n",
    "systemctl": _SYSTEMCTL,
}


@dataclass(frozen=True)
class InstallerCopy:
    script: Path
    root: Path
    env: dict[str, str]
    systemctl_log: Path

    @property
    def prefix(self) -> Path:
        return self.root / "opt/goblinshift/executor-ctl"

    @property
    def unit(self) -> Path:
        return self.root / "etc/systemd/system" / SERVICE

    def run(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(self.script)],
            env=self.env,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )

    def systemctl_calls(self) -> list[str]:
        if not self.systemctl_log.exists():
            return []
        return self.systemctl_log.read_text().splitlines()


@contextmanager
def installer_copy(
    tmp_path: Path, *, fail_first_daemon_reload: bool = False, failing: tuple[str, ...] = ()
) -> Iterator[InstallerCopy]:
    """Prepara a cópia; `failing` lista comandos que saem com erro (ex.: `chmod`)."""
    root = tmp_path / "root"
    (root / "etc/systemd/system").mkdir(parents=True)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for name, body in {**_STUBS, **{name: "#!/bin/sh\nexit 1\n" for name in failing}}.items():
        stub = binaries / name
        stub.write_text(body)
        stub.chmod(0o755)
    # O caminho de um socket Unix tem teto de ~104 bytes no macOS; o tmp_path passa disso.
    with tempfile.TemporaryDirectory(prefix="gs-inst-") as directory:
        socket_path = Path(directory) / "ctl.sock"
        # O script acha o pacote ao lado (../executor_ctl), como no checkout.
        checkout = tmp_path / "checkout/deploy"
        (checkout / "executor-ctl").mkdir(parents=True)
        shutil.copytree(
            ROOT / "deploy/executor_ctl",
            checkout / "executor_ctl",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        script = checkout / "executor-ctl/install.sh"
        script.write_text(_redirected(INSTALLER.read_text(), root, socket_path))
        env = {
            **os.environ,
            "PATH": f"{binaries}:/usr/bin:/bin",
            "SYSTEMCTL_LOG": str(tmp_path / "systemctl.log"),
            "SYSTEMCTL_FAIL_ONCE": str(tmp_path / "daemon-reload-falhou")
            if fail_first_daemon_reload
            else "",
        }
        with socketlib.socket(socketlib.AF_UNIX) as listener:
            listener.bind(str(socket_path))
            yield InstallerCopy(script, root, env, tmp_path / "systemctl.log")


def _redirected(text: str, root: Path, socket_path: Path) -> str:
    """Troca root e caminhos absolutos do host pelos da raiz sintética; cada troca tem de casar."""
    replacements = {
        "[[ $EUID -eq 0 ]]": "[[ 0 -eq 0 ]]",
        "/opt/goblinshift": f"{root}/opt/goblinshift",
        "/etc/systemd/system": f"{root}/etc/systemd/system",
        "SOCKET=/run/goblinshift/executor-ctl.sock": f"SOCKET={socket_path}",
    }
    for old, new in replacements.items():
        assert old in text, f"install.sh mudou: {old!r} não aparece mais"
        text = text.replace(old, new)
    return text
