"""deploy/backup/install.sh como root, num container Debian com o usuário goblinshift.

O backup.env mora em /srv/goblinshift/secrets, que pertence ao goblinshift (deploy/provision.sh):
o instalador mexe nele com os privilégios do goblinshift, nunca como root. O código do timer fica
em /usr/local/lib/goblinshift/backup, do root: não depende de nenhum checkout depois de instalado.
"""

import shutil
import subprocess

import pytest

from tests.host_sandbox import ROOT, installed, run_as_root

pytestmark = pytest.mark.host_installer

INSTALL = "bash /srv/goblinshift/app/deploy/backup/install.sh"
CONFIG = "/srv/goblinshift/secrets/backup.env"
PLANTED = "/etc/profile.d/zz-goblinshift.sh"
BACKUP_PREFIX = "/usr/local/lib/goblinshift/backup"
SERVICE = "/etc/systemd/system/goblinshift-pg-backup.service"
FILLED_CONFIG = f"""
cat <<'EOF' | as_app bash -c 'umask 077 && cat >{CONFIG}'
BACKUP_S3_ENDPOINT=http://s3.invalid
BACKUP_S3_REGION=us-east-1
BACKUP_S3_BUCKET=gs-backups
BACKUP_S3_KEY_ID=chave
BACKUP_S3_SECRET=segredo
BACKUP_HEALTHCHECK_URL=http://hc.invalid/ping
EOF
"""
WRITABLE_BY_OTHERS = 0o022


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


def test_symlink_no_checkout_nao_copia_arquivo_que_so_o_root_le() -> None:
    # Ataque do achado symlink-following-read: o root copia as units do checkout do goblinshift.
    result = run_as_root(
        """
(umask 077; echo 'CANARY-so-do-root' >/root/segredo)
as_app ln -sf /root/segredo /srv/goblinshift/app/deploy/backup/goblinshift-pg-backup.timer
"""
        + f"""
rc=0; {INSTALL} || rc=$?
echo "rc=$rc"
grep -q CANARY /etc/systemd/system/goblinshift-pg-backup.timer 2>/dev/null && echo VAZOU || true
"""
    )

    assert "VAZOU" not in result.stdout
    assert "recusado" in result.stderr


def test_copia_o_que_o_backup_usa_para_diretorio_que_so_o_root_grava() -> None:
    result = run_as_root(
        FILLED_CONFIG
        + f"""
{INSTALL}
{INSTALL}
find {BACKUP_PREFIX} -printf 'F %u:%g %m %p\\n'
find /usr/local/lib/goblinshift -maxdepth 1 -name '.novo*' -printf 'SOBRA %p\\n'
"""
    )

    assert result.returncode == 0, result.stderr
    assert "SOBRA" not in result.stdout
    entries = installed(result.stdout)
    assert {
        f"{BACKUP_PREFIX}/pg-backup.sh",
        f"{BACKUP_PREFIX}/lib.sh",
        f"{BACKUP_PREFIX}/age-recipients.txt",
    } <= entries.keys()
    for path, (owner, mode) in entries.items():
        assert owner == "root:root", path
        assert int(mode, 8) & WRITABLE_BY_OTHERS == 0, path
    assert entries[f"{BACKUP_PREFIX}/pg-backup.sh"][1] == "755"


def test_unit_nova_nunca_aponta_para_arquivo_que_nao_existe() -> None:
    # O systemctl falso olha a unit no momento do daemon-reload, quando ela passa a valer.
    result = run_as_root(
        f"""
cat >/usr/local/bin/systemctl <<'EOF'
#!/bin/bash
script="$(sed -n 's/^ExecStart=//p' {SERVICE})"
verdict="SEM SCRIPT"
[[ -x "$script" ]] && verdict=CONFERIDO
echo "$verdict $script" >>/tmp/calls
EOF
"""
        + FILLED_CONFIG
        + f"""
{INSTALL}
cat /tmp/calls
"""
    )

    assert result.returncode == 0, result.stderr
    assert "SEM SCRIPT" not in result.stdout
    assert f"CONFERIDO {BACKUP_PREFIX}/pg-backup.sh" in result.stdout


@pytest.mark.parametrize("planted", ["pg-backup.sh", "lib.sh", "age-recipients.txt"])
def test_copia_recusada_mantem_a_unit_antiga_e_nao_instala_nada(planted: str) -> None:
    # A VPS tem a unit antiga, apontando para a cópia parada: ela só pode ser trocada depois que o
    # destino novo estiver completo, ou o backup diário deixa de rodar.
    result = run_as_root(
        f"""
(umask 077; echo 'CANARY-so-do-root' >/root/segredo)
echo 'ExecStart=/unit/antiga' >{SERVICE}
as_app ln -sf /root/segredo /srv/goblinshift/app/deploy/backup/{planted}
"""
        + FILLED_CONFIG
        + f"""
rc=0; {INSTALL} || rc=$?
echo "rc=$rc"
cat {SERVICE}
test -e {BACKUP_PREFIX}/pg-backup.sh && echo "TROCOU O CODIGO" || true
grep -rq CANARY {BACKUP_PREFIX} 2>/dev/null && echo "VAZOU" || true
cat /tmp/calls 2>/dev/null || true
"""
    )

    assert "rc=0" not in result.stdout
    assert "recusado" in result.stderr
    assert "ExecStart=/unit/antiga" in result.stdout
    assert "TROCOU O CODIGO" not in result.stdout
    assert "VAZOU" not in result.stdout
    assert "daemon-reload" not in result.stdout


def test_recusa_destino_que_outro_usuario_controla() -> None:
    result = run_as_root(
        FILLED_CONFIG
        + f"""
install -d -m 755 /usr/local/lib/goblinshift
as_app mkdir /tmp/do-app
ln -s /tmp/do-app {BACKUP_PREFIX}
rc=0; {INSTALL} || rc=$?
echo "rc=$rc"
test -e /tmp/do-app/pg-backup.sh && echo "COPIADO" || true
"""
    )

    assert "rc=0" not in result.stdout
    assert "COPIADO" not in result.stdout
    assert BACKUP_PREFIX in result.stderr


def test_script_instalado_faz_o_backup_sem_a_copia_parada() -> None:
    """Prova do ExecStart da unit: como goblinshift, com /srv/goblinshift/app já arquivado."""
    result = run_as_root(
        FILLED_CONFIG
        + f"""
{INSTALL} >/dev/null
rm -rf /srv/goblinshift/app
install -m 666 /dev/null /tmp/calls
cat >/usr/local/bin/docker <<'EOF'
#!/bin/sh
echo "docker $*" >>/tmp/calls
echo dump
EOF
cat >/usr/local/bin/age <<'EOF'
#!/bin/bash
echo "age $*" >>/tmp/calls
while (($#)); do
  if [[ $1 == --output ]]; then cat >"$2"; exit 0; fi
  shift
done
EOF
cat >/usr/local/bin/curl <<'EOF'
#!/bin/sh
cat >/dev/null
echo "curl $*" >>/tmp/calls
EOF
chmod 755 /usr/local/bin/docker /usr/local/bin/age /usr/local/bin/curl
cd /
set -a
. <(as_app cat {CONFIG})
set +a
runuser -u goblinshift -- env BACKUP_PG_CONTAINER=goblinshift-postgres-1 \\
  {BACKUP_PREFIX}/pg-backup.sh
cat /tmp/calls
"""
    )

    assert result.returncode == 0, result.stderr
    assert "docker exec goblinshift-postgres-1 pg_dump" in result.stdout
    assert f"--recipients-file {BACKUP_PREFIX}/age-recipients.txt" in result.stdout
    assert "--upload-file" in result.stdout


def test_instala_do_checkout_temporario_do_runbook_sem_a_copia_parada() -> None:
    # Como no README: o diretório do mktemp precisa do chmod 755, porque o instalador lê o checkout
    # com os privilégios do goblinshift.
    result = run_as_root(
        FILLED_CONFIG
        + f"""
rm -rf /srv/goblinshift/app
gs_tmp="$(mktemp -d /tmp/goblinshift.XXXXXX)"
chmod 755 "$gs_tmp"
mkdir "$gs_tmp/repo"
cp -r /src/deploy "$gs_tmp/repo/deploy"
bash "$gs_tmp/repo/deploy/backup/install.sh"
test -x {BACKUP_PREFIX}/pg-backup.sh && echo INSTALADO
"""
    )

    assert result.returncode == 0, result.stderr
    assert "INSTALADO" in result.stdout


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
