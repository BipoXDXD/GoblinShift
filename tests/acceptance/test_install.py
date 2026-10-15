"""deploy/acceptance/install.sh como root, num container Debian com o usuário goblinshift.

O serviço roda como root; por isso o código que ele executa e a configuração que ele lê ficam em
caminhos que só o root grava. O goblinshift é dono do checkout e de /srv/goblinshift/secrets
(deploy/provision.sh): os cenários de ataque partem do que ele consegue plantar ali.
"""

import pytest

from tests.host_sandbox import run_as_root

pytestmark = pytest.mark.host_installer

INSTALL = "bash /srv/goblinshift/app/deploy/acceptance/install.sh"
PREFIX = "/usr/local/lib/goblinshift"
CONFIG = "/etc/goblinshift/acceptance.env"
FILLED_CONFIG = f"""
install -d -m 755 /etc/goblinshift
(umask 077; echo 'ACCEPTANCE_HEALTHCHECK_URL=https://hc-ping.com/x' >{CONFIG})
"""
WRITABLE_BY_OTHERS = 0o022


def test_copia_o_codigo_da_aceitacao_para_diretorio_que_so_o_root_grava() -> None:
    result = run_as_root(
        FILLED_CONFIG
        + f"""
{INSTALL}
{INSTALL}
find {PREFIX} -printf 'F %u:%g %m %p\\n'
"""
    )

    assert result.returncode == 0, result.stderr
    entries = installed(result.stdout)
    assert {
        f"{PREFIX}/acceptance/scheduled.sh",
        f"{PREFIX}/acceptance/run.py",
        f"{PREFIX}/acceptance/probe.py",
        f"{PREFIX}/acceptance/__init__.py",
        f"{PREFIX}/executor_ctl/__init__.py",
        f"{PREFIX}/backup/lib.sh",
    } <= entries.keys()
    for path, (owner, mode) in entries.items():
        assert owner == "root:root", path
        assert int(mode, 8) & WRITABLE_BY_OTHERS == 0, path
    assert entries[f"{PREFIX}/acceptance/scheduled.sh"][1] == "755"


def installed(stdout: str) -> dict[str, tuple[str, str]]:
    """Linhas `F <dono:grupo> <modo> <caminho>` do find: caminho -> (dono:grupo, modo)."""
    entries = {}
    for line in stdout.splitlines():
        if line.startswith("F "):
            _, owner, mode, path = line.split(" ", 3)
            entries[path] = (owner, mode)
    return entries


def test_script_instalado_carrega_a_lib_e_o_pacote_da_propria_copia() -> None:
    result = run_as_root(
        FILLED_CONFIG
        + f"""
{INSTALL} >/dev/null
install -d /tmp/fakebin
printf '#!/bin/sh\\necho "python3 $* em $PWD" >>/tmp/calls\\n' >/tmp/fakebin/python3
printf '#!/bin/sh\\necho "docker $*" >>/tmp/calls\\n' >/tmp/fakebin/docker
chmod 755 /tmp/fakebin/*
cd / && PATH="/tmp/fakebin:$PATH" ACCEPTANCE_HEALTHCHECK_URL=https://hc-ping.com/x \
  {PREFIX}/acceptance/scheduled.sh
cat /tmp/calls
"""
    )

    assert result.returncode == 0, result.stderr
    assert f"python3 -E -s -B -m acceptance.run em {PREFIX}" in result.stdout


def test_cria_a_configuracao_vazia_do_root_fora_dos_segredos_do_app() -> None:
    result = run_as_root(
        f"""
rc=0; {INSTALL} || rc=$?
echo "rc=$rc"
stat -c '%U:%G %a' {CONFIG}
cat {CONFIG}
test -e /srv/goblinshift/secrets/acceptance.env && echo "NOS SEGREDOS DO APP" || true
"""
    )

    assert "rc=1" in result.stdout, result.stderr
    assert "root:root 600" in result.stdout
    assert "ACCEPTANCE_HEALTHCHECK_URL=\n" in result.stdout
    assert "NOS SEGREDOS DO APP" not in result.stdout
    assert "systemctl enable" not in result.stdout


def test_symlink_plantado_pelo_app_nos_segredos_nao_cria_arquivo_como_root() -> None:
    # Ataque do achado symlink-following-write: o goblinshift é dono de /srv/goblinshift/secrets.
    result = run_as_root(
        f"""
as_app ln -s /etc/nologin /srv/goblinshift/secrets/acceptance.env
{INSTALL} || true
test -e /etc/nologin && echo "CRIADO PELO ROOT" || true
"""
    )

    assert "CRIADO PELO ROOT" not in result.stdout


def test_recusa_diretorio_de_configuracao_que_outro_usuario_grava() -> None:
    result = run_as_root(
        f"""
install -d -m 755 -o goblinshift -g goblinshift /etc/goblinshift
rc=0; {INSTALL} || rc=$?
echo "rc=$rc"
test -e {CONFIG} && echo "CRIADO" || true
"""
    )

    assert "rc=0" not in result.stdout
    assert "CRIADO" not in result.stdout
    assert "recusado: /etc/goblinshift" in result.stderr


def test_recusa_diretorio_do_codigo_que_outro_usuario_grava() -> None:
    result = run_as_root(
        FILLED_CONFIG
        + f"""
install -d -m 755 -o goblinshift -g goblinshift {PREFIX}
rc=0; {INSTALL} || rc=$?
echo "rc=$rc"
test -e {PREFIX}/acceptance/scheduled.sh && echo "COPIADO" || true
"""
    )

    assert "rc=0" not in result.stdout
    assert "COPIADO" not in result.stdout
    assert PREFIX in result.stderr
