"""deploy/backup/install.sh como root, num container Debian com o usuário goblinshift.

O backup.env mora em /srv/goblinshift/secrets, que pertence ao goblinshift (deploy/provision.sh):
o instalador mexe nele com os privilégios do goblinshift, nunca como root.
"""

import shutil
import subprocess

import pytest

from tests.host_sandbox import ROOT, run_as_root

pytestmark = pytest.mark.host_installer

INSTALL = "bash /srv/goblinshift/app/deploy/backup/install.sh"
CONFIG = "/srv/goblinshift/secrets/backup.env"
PLANTED = "/etc/profile.d/zz-goblinshift.sh"


def test_cria_a_configuracao_vazia_do_usuario_da_aplicacao() -> None:
    result = run_as_root(
        f"""
rc=0; {INSTALL} || rc=$?
echo "rc=$rc"
stat -c '%U:%G %a' {CONFIG}
"""
    )

    assert "rc=1" in result.stdout, result.stderr
    assert "goblinshift:goblinshift 600" in result.stdout
    assert "BACKUP_HEALTHCHECK_URL" in result.stderr


def test_symlink_plantado_pelo_app_nao_cria_nem_entrega_arquivo_do_sistema() -> None:
    # Ataque do achado symlink-following-write: sem a correção, o root cria o alvo e o passa ao
    # goblinshift (chown), que escreve ali um script que todo login shell do root executa.
    result = run_as_root(
        f"""
as_app ln -s {PLANTED} {CONFIG}
{INSTALL} || true
test -e {PLANTED} && stat -c 'CRIADO %U' {PLANTED} || true
"""
    )

    assert "CRIADO" not in result.stdout


def test_symlink_para_arquivo_existente_nao_muda_dono_nem_modo() -> None:
    result = run_as_root(
        f"""
as_app ln -s /etc/passwd {CONFIG}
{INSTALL} || true
stat -c '%U:%G %a' /etc/passwd
"""
    )

    assert "root:root 644" in result.stdout


def test_instalador_passa_no_shellcheck() -> None:
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        pytest.skip("shellcheck ausente")
    installer = ROOT / "deploy" / "backup" / "install.sh"
    result = subprocess.run(
        [shellcheck, "--external-sources", str(installer)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        cwd=installer.parent,
    )

    assert result.returncode == 0, result.stdout
