"""deploy/executor-ctl/install.sh como root, num container Debian com o usuário goblinshift.

O ctl roda como root e executa o código copiado para /opt/goblinshift/executor-ctl. O goblinshift
é dono do checkout (deploy/provision.sh): os cenários de ataque partem do que ele planta ali, ou
de um /opt/goblinshift que outro usuário grava.
"""

import json

import pytest

from tests.host_sandbox import run_as_root

pytestmark = pytest.mark.host_installer

INSTALL = "bash /srv/goblinshift/app/deploy/executor-ctl/install.sh"
APP = "/srv/goblinshift/app/deploy"
PREFIX = "/opt/goblinshift/executor-ctl"
CONFIG = "/etc/goblinshift/executor-ctl.json"
POLICY = "/var/lib/coolify-release/goblinshift/executor-images.json"
SOCKET = "/run/goblinshift/executor-ctl.sock"
IMAGE = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "b" * 64
CONFIG_CONTENT = json.dumps(
    {
        "socket": SOCKET,
        "group": "gs-exec",
        "socket_group": "gs-ctl",
        "work_area": "/srv/goblinshift/exec-work",
        "runtime": "runsc",
        "images_file": POLICY,
    }
)

# Migração administrativa feita e serviço "no ar" (o socket existe): o instalador chega ao fim.
EXECUTOR_CTL_READY = f"""
install -d -m 755 /etc/goblinshift
echo '{CONFIG_CONTENT}' >{CONFIG}
chmod 644 {CONFIG}
install -d -m 755 /var/lib/coolify-release /var/lib/coolify-release/goblinshift
(umask 077; echo '{{"allowed": ["{IMAGE}"]}}' >{POLICY})
install -d -m 755 /run/goblinshift
python3 -c 'import socket; socket.socket(socket.AF_UNIX).bind("{SOCKET}")'
"""


def test_symlink_no_checkout_nao_copia_arquivo_que_so_o_root_le() -> None:
    # Um módulo que o preflight não importa: a recusa vem da cópia, não do preflight.
    result = run_as_root(
        EXECUTOR_CTL_READY
        + f"""
(umask 077; echo CANARY >/root/segredo)
as_app ln -s /root/segredo {APP}/executor_ctl/zz_plantado.py
status=0
{INSTALL} || status=$?
echo "status=$status"
if grep -rq CANARY /opt/goblinshift; then echo CANARY_COPIADO; fi
"""
    )

    assert "status=0" not in result.stdout, result.stdout + result.stderr
    assert "status=" in result.stdout, result.stderr
    assert "CANARY_COPIADO" not in result.stdout
    assert f"recusado: {APP}/executor_ctl/zz_plantado.py" in result.stderr, result.stderr


def test_prefixo_gravavel_por_outro_usuario_e_recusado() -> None:
    result = run_as_root(
        EXECUTOR_CTL_READY
        + f"""
install -d -m 777 /opt/goblinshift
status=0
{INSTALL} || status=$?
echo "status=$status"
if [ -e {PREFIX}/executor_ctl ]; then echo PACOTE_INSTALADO; fi
"""
    )

    assert "status=0" not in result.stdout, result.stdout + result.stderr
    assert "status=" in result.stdout, result.stderr
    assert "PACOTE_INSTALADO" not in result.stdout
    assert "recusado: /opt/goblinshift precisa ser do root" in result.stderr, result.stderr


def test_reinstalar_sem_mudanca_nao_reinicia() -> None:
    result = run_as_root(
        EXECUTOR_CTL_READY
        + f"""
{INSTALL}
rm -f /tmp/calls
{INSTALL}
echo '--- segunda'
cat /tmp/calls
"""
    )

    assert result.returncode == 0, result.stderr
    second = result.stdout.split("--- segunda", 1)[1]
    assert "systemctl daemon-reload" in second
    assert "systemctl restart" not in second
