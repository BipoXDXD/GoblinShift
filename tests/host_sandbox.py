"""Roda os instaladores de deploy/ como root num container Debian descartável.

Reproduz o que importa da VPS para os cenários de ataque: o usuário `goblinshift`, dono do
checkout (/srv/goblinshift/app) e de /srv/goblinshift/secrets, como no deploy/provision.sh.
`systemctl`, `age` e `curl` são falsos que registram as chamadas em /tmp/calls; o instalador não
liga nada de verdade.
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGE = (
    "python:3.14.9-slim-trixie"
    "@sha256:a145a961ec2cf5741772d5e39773d979271132884fd778f6c3fad34e52b85402"
)
TIMEOUT_S = 120

PRELUDE = r"""
set -euo pipefail
useradd --create-home goblinshift
install -d -m 755 /srv/goblinshift /srv/goblinshift/app
install -d -m 700 -o goblinshift -g goblinshift /srv/goblinshift/secrets
cp -r /src/deploy /srv/goblinshift/app/deploy
chown -R goblinshift:goblinshift /srv/goblinshift/app
install -d -m 755 /etc/systemd/system
for fake in systemctl age curl; do
  printf '#!/bin/sh\necho "%s $*" >>/tmp/calls\n' "$fake" >"/usr/local/bin/$fake"
  chmod 755 "/usr/local/bin/$fake"
done
as_app() { runuser -u goblinshift -- "$@"; }
"""


def run_as_root(scenario: str) -> subprocess.CompletedProcess[str]:
    """Roda o cenário (bash) depois do PRELUDE; a função `as_app` executa como goblinshift."""
    return subprocess.run(  # noqa: S603 - argv fixo
        [  # noqa: S607 - o docker do PATH
            "docker",
            "run",
            "--rm",
            "--network=none",
            f"--volume={ROOT / 'deploy'}:/src/deploy:ro",
            IMAGE,
            "bash",
            "-c",
            PRELUDE + scenario,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=TIMEOUT_S,
    )
