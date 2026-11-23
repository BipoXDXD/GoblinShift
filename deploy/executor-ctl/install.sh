#!/usr/bin/env bash
# Instala o executor-ctl como serviço systemd do host (docs/GoblinShiftPlan.md, 3.6 e 3.8; ADR 004).
# Idempotente e aditivo, como o provision.sh, que chama este script: não sobrescreve configuração
# existente. Exige migração administrativa prévia e drenada; não cria política vazia.
# Não reinicia o Docker e não mexe em firewall. Reinicia só o próprio executor-ctl, e só
# quando o pacote ou a unit mudaram (os containers das tentativas não dependem dele para rodar).
#
# Uso: sudo bash deploy/executor-ctl/install.sh
# Depois de instalar ou atualizar, rode o teste de aceitação (cd deploy && sudo python3 -B -m acceptance.run).
set -euo pipefail

GROUP=gs-exec
GROUP_ID=61001
# Só o socket de controle fica neste grupo, fora do --user do executor (61000:61001): quem
# escapa do sandbox não para nem remove o executor de outras Runs (D11).
SOCKET_GROUP=gs-ctl
SOCKET_GROUP_ID=61002
APP_USER=goblinshift
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../executor_ctl" && pwd)"
PREFIX=/opt/goblinshift/executor-ctl
CONFIG_DIR=/etc/goblinshift
CONFIG="$CONFIG_DIR/executor-ctl.json"
WORK_AREA=/srv/goblinshift/exec-work
IMAGES_FILE=/var/lib/coolify-release/goblinshift/executor-images.json
SERVICE=goblinshift-executor-ctl.service
UNIT="/etc/systemd/system/$SERVICE"
SOCKET=/run/goblinshift/executor-ctl.sock
# Gravado antes de trocar pacote ou unit e apagado só depois do restart: uma falha no meio (no
# daemon-reload, por exemplo) não deixa a execução seguinte ver tudo igual e pular o reinício.
RESTART_PENDING="$PREFIX/.restart-pending"
[[ $EUID -eq 0 ]] || { echo "rode com sudo" >&2; exit 1; }
note() { printf '==> %s\n' "$*"; }
changed=0
staging=""
unit_tmp=""
# Falha no meio não deixa o staging em /opt nem a unit temporária.
cleanup() {
  if [[ -n "$staging" ]]; then rm -rf "$staging"; fi
  if [[ -n "$unit_tmp" ]]; then rm -f "$unit_tmp"; fi
}
trap cleanup EXIT
mark_restart_pending() {
  : > "$RESTART_PENDING"
  changed=1
}

# A migration must prepare BOTH files before this script can mutate even a group.
# Read the source package without PYTHONPATH/user site; do not trust a stale installed reader.
note "Preflight administrativo (somente leitura)"
if ! python3 -I -B - "$SOURCE" "$CONFIG" <<'PY_PREFLIGHT'
import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]).parent))
from executor_ctl.config import ConfigError, load_config
from executor_ctl.images import ImageNotAllowedError, _read_allowlist

try:
    path = Path(sys.argv[2])
    config = load_config(path)
    if not _read_allowlist(config.images_file):
        raise ImageNotAllowedError("política vazia não autoriza instalação")
except (ConfigError, ImageNotAllowedError) as error:
    print(f"migração administrativa revisada necessária antes de instalar: {error}", file=sys.stderr)
    sys.exit(1)
PY_PREFLIGHT
then
  exit 1
fi

# Os dois GIDs entram no group_add do Compose, e o do gs-exec também no --user do executor:
# outro número quebraria os dois.
ensure_group() {
  local name="$1" gid="$2" actual
  note "Grupo ${name} (GID ${gid})"
  if getent group "$name" >/dev/null; then
    actual="$(getent group "$name" | cut -d: -f3)"
    [[ "$actual" == "$gid" ]] || { echo "o grupo $name existe com GID $actual, esperado $gid" >&2; exit 1; }
    echo "   já existe."
  elif getent group "$gid" >/dev/null; then
    echo "o GID $gid já pertence ao grupo $(getent group "$gid" | cut -d: -f1)" >&2
    exit 1
  else
    groupadd --system --gid "$gid" "$name"
  fi
}
ensure_group "$GROUP" "$GROUP_ID"
ensure_group "$SOCKET_GROUP" "$SOCKET_GROUP_ID"
if id "$APP_USER" >/dev/null 2>&1; then
  usermod -aG "$GROUP,$SOCKET_GROUP" "$APP_USER"
else
  echo "   usuário $APP_USER não existe; rode o provision.sh antes."
fi

note "Pacote em ${PREFIX}"
install -d -m 755 /opt/goblinshift "$PREFIX" "$PREFIX/docker-config"
if [[ -e "$RESTART_PENDING" ]]; then
  echo "   reinício pendente de uma execução anterior."
  changed=1
fi
if ! diff -rq --exclude=__pycache__ "$SOURCE" "$PREFIX/executor_ctl" >/dev/null 2>&1; then
  staging="$(mktemp -d "$PREFIX/.executor_ctl.XXXXXX")"
  install -m 644 "$SOURCE"/*.py "$staging/"
  chmod 755 "$staging"
  mark_restart_pending
  rm -rf "$PREFIX/executor_ctl"
  mv "$staging" "$PREFIX/executor_ctl"
  staging=""
  echo "   atualizado."
else
  echo "   já está igual ao checkout."
fi

note "Configuração ${CONFIG} e política ${IMAGES_FILE} validadas; mantidas sem escrita."

note "Área de trabalho ${WORK_AREA}"
if mountpoint -q "$WORK_AREA"; then
  # O worker cria <area>/<id> pelo grupo; setgid deixa tudo lá dentro no grupo gs-exec, e o
  # sticky impede o worker de renomear o <id> que o start passou ao root (workarea.py).
  chown root:"$GROUP" "$WORK_AREA"
  chmod 3770 "$WORK_AREA"
else
  echo "   $WORK_AREA não está montada; o serviço não sobe sem ela (provision.sh a cria)."
fi

note "Unit ${SERVICE}"
unit_tmp="$(mktemp)"
cat > "$unit_tmp" <<EOF
# Gerado por deploy/executor-ctl/install.sh; mudanças manuais são sobrescritas.
[Unit]
Description=GoblinShift executor-ctl (único acesso ao Docker para o executor)
Requires=docker.service
After=docker.service
# Sem o filesystem de tamanho fixo, uma tentativa escreveria no disco raiz (ADR 005).
RequiresMountsFor=${WORK_AREA}

[Service]
Type=simple
WorkingDirectory=${PREFIX}
# -E e -s: sem variáveis PYTHON* nem site do usuário; -B: sem bytecode; o pacote vem do cwd.
ExecStart=/usr/bin/python3 -E -s -B -m executor_ctl ${CONFIG}
Environment=DOCKER_CONFIG=${PREFIX}/docker-config
Restart=on-failure
RestartSec=2
# mixed: o SIGTERM vai só ao processo principal, que para de aceitar conexões e espera as em
# voo; o docker que elas chamam não recebe o sinal e termina a operação. O SIGKILL (no fim do
# prazo, ou no que sobrar quando o principal sai) vai ao cgroup inteiro.
KillMode=mixed
# Acima da operação mais longa em voo: o start, até 30 s por chamada ao Docker vezes 3 dele e 3
# de quem segura o lock, mais 10 s (190 s, START_TIMEOUT do worker); remove 160 s; stop até
# 40 + 4 x 30 + 5 s de lock (165 s).
TimeoutStopSec=210
RuntimeDirectory=goblinshift
RuntimeDirectoryMode=0755
# O worker monta /run/goblinshift; recriar o diretório num restart cortaria esse bind mount.
RuntimeDirectoryPreserve=yes
UMask=0077

# Hardening: root só para o Docker e para apagar arquivos do UID 61000 na área. CAP_FSETID mantém
# o setgid dos modos 3770/2750/2770 do start, que o kernel tira de quem não é do grupo gs-exec.
NoNewPrivileges=yes
CapabilityBoundingSet=CAP_CHOWN CAP_FOWNER CAP_FSETID CAP_DAC_OVERRIDE CAP_DAC_READ_SEARCH
ProtectSystem=strict
ReadWritePaths=${WORK_AREA}
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
RestrictNamespaces=yes
RestrictRealtime=yes
LockPersonality=yes
RestrictAddressFamilies=AF_UNIX
SystemCallArchitectures=native

[Install]
WantedBy=multi-user.target
EOF
if ! cmp -s "$unit_tmp" "$UNIT"; then
  mark_restart_pending
  install -m 644 "$unit_tmp" "$UNIT"
  echo "   escrita."
else
  echo "   já está igual."
fi

note "Serviço"
systemctl daemon-reload
if systemctl is-active --quiet "$SERVICE"; then
  if [[ $changed -eq 1 ]]; then
    systemctl restart "$SERVICE"
    rm -f "$RESTART_PENDING"
    echo "   reiniciado (pacote, unit ou reinício pendente)."
  fi
  systemctl enable --quiet "$SERVICE"
else
  systemctl enable --now "$SERVICE"
  rm -f "$RESTART_PENDING"
fi
for _ in $(seq 1 20); do
  [[ -S "$SOCKET" ]] && break
  sleep 0.5
done
[[ -S "$SOCKET" ]] || { echo "o socket $SOCKET não apareceu; veja journalctl -u $SERVICE" >&2; exit 1; }
stat -c '   socket %n: %U:%G %a' "$SOCKET"
