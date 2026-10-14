#!/usr/bin/env bash
# Instala o executor-ctl como serviço systemd do host (docs/GoblinShiftPlan.md, 3.6 e 3.8; ADR 004).
# Idempotente e aditivo, como o provision.sh, que chama este script: não sobrescreve configuração
# existente, não reinicia o Docker e não mexe em firewall. Reinicia só o próprio executor-ctl, e só
# quando o pacote ou a unit mudaram (os containers das tentativas não dependem dele para rodar).
#
# Uso: sudo bash deploy/executor-ctl/install.sh
# Depois de instalar ou atualizar, rode o teste de aceitação (cd deploy && sudo python3 -B -m acceptance.run).
set -euo pipefail

GROUP=gs-exec
GROUP_ID=61001
APP_USER=goblinshift
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../executor_ctl" && pwd)"
PREFIX=/opt/goblinshift/executor-ctl
CONFIG_DIR=/etc/goblinshift
CONFIG="$CONFIG_DIR/executor-ctl.json"
WORK_AREA=/srv/goblinshift/exec-work
IMAGES_DIR=/srv/goblinshift/executor
IMAGES_FILE="$IMAGES_DIR/images.json"
SERVICE=goblinshift-executor-ctl.service
UNIT="/etc/systemd/system/$SERVICE"
SOCKET=/run/goblinshift/executor-ctl.sock
[[ $EUID -eq 0 ]] || { echo "rode com sudo" >&2; exit 1; }
note() { printf '==> %s\n' "$*"; }
changed=0

note "Grupo ${GROUP} (GID ${GROUP_ID})"
if getent group "$GROUP" >/dev/null; then
  actual="$(getent group "$GROUP" | cut -d: -f3)"
  # O GID entra no --user do executor e no group_add do Compose: outro número quebraria os dois.
  [[ "$actual" == "$GROUP_ID" ]] || { echo "o grupo $GROUP existe com GID $actual, esperado $GROUP_ID" >&2; exit 1; }
  echo "   já existe."
elif getent group "$GROUP_ID" >/dev/null; then
  echo "o GID $GROUP_ID já pertence ao grupo $(getent group "$GROUP_ID" | cut -d: -f1)" >&2
  exit 1
else
  groupadd --system --gid "$GROUP_ID" "$GROUP"
fi
if id "$APP_USER" >/dev/null 2>&1; then
  usermod -aG "$GROUP" "$APP_USER"
else
  echo "   usuário $APP_USER não existe; rode o provision.sh antes."
fi

note "Pacote em ${PREFIX}"
install -d -m 755 /opt/goblinshift "$PREFIX" "$PREFIX/docker-config"
if ! diff -rq --exclude=__pycache__ "$SOURCE" "$PREFIX/executor_ctl" >/dev/null 2>&1; then
  staging="$(mktemp -d "$PREFIX/.executor_ctl.XXXXXX")"
  install -m 644 "$SOURCE"/*.py "$staging/"
  chmod 755 "$staging"
  rm -rf "$PREFIX/executor_ctl"
  mv "$staging" "$PREFIX/executor_ctl"
  changed=1
  echo "   atualizado."
else
  echo "   já está igual ao checkout."
fi

note "Configuração ${CONFIG}"
install -d -m 755 "$CONFIG_DIR"
if [[ ! -f "$CONFIG" ]]; then
  cat > "$CONFIG" <<EOF
{
  "socket": "${SOCKET}",
  "group": "${GROUP}",
  "work_area": "${WORK_AREA}",
  "runtime": "runsc",
  "images_file": "${IMAGES_FILE}"
}
EOF
  chmod 644 "$CONFIG"
  changed=1
else
  echo "   já existe; mantida como está."
fi

note "Allowlist de imagens ${IMAGES_FILE} (gravada pelo deploy)"
[[ -d "$IMAGES_DIR" ]] || install -d -m 755 -o "$APP_USER" -g "$APP_USER" "$IMAGES_DIR"
if [[ ! -f "$IMAGES_FILE" ]]; then
  echo '{"allowed": []}' > "$IMAGES_FILE"
  chown "$APP_USER:$APP_USER" "$IMAGES_FILE"
  chmod 644 "$IMAGES_FILE"
else
  echo "   já existe; mantida como está."
fi

note "Área de trabalho ${WORK_AREA}"
if mountpoint -q "$WORK_AREA"; then
  # O worker cria <area>/<id> pelo grupo; setgid deixa tudo lá dentro no grupo gs-exec.
  chown root:"$GROUP" "$WORK_AREA"
  chmod 2770 "$WORK_AREA"
else
  echo "   $WORK_AREA não está montada; o serviço não sobe sem ela (provision.sh a cria)."
fi

note "Unit ${SERVICE}"
unit_tmp="$(mktemp)"
trap 'rm -f "$unit_tmp"' EXIT
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
# stop em voo leva até 10 s de carência; o servidor espera as requisições antes de sair.
TimeoutStopSec=30
RuntimeDirectory=goblinshift
RuntimeDirectoryMode=0755
# O worker monta /run/goblinshift; recriar o diretório num restart cortaria esse bind mount.
RuntimeDirectoryPreserve=yes
UMask=0077

# Hardening: root só para o Docker e para apagar arquivos do UID 61000 na área.
NoNewPrivileges=yes
CapabilityBoundingSet=CAP_CHOWN CAP_FOWNER CAP_DAC_OVERRIDE CAP_DAC_READ_SEARCH
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
  install -m 644 "$unit_tmp" "$UNIT"
  changed=1
  echo "   escrita."
else
  echo "   já está igual."
fi

note "Serviço"
systemctl daemon-reload
if systemctl is-active --quiet "$SERVICE"; then
  if [[ $changed -eq 1 ]]; then
    systemctl restart "$SERVICE"
    echo "   reiniciado (pacote, configuração ou unit mudaram)."
  fi
  systemctl enable --quiet "$SERVICE"
else
  systemctl enable --now "$SERVICE"
fi
for _ in $(seq 1 20); do
  [[ -S "$SOCKET" ]] && break
  sleep 0.5
done
[[ -S "$SOCKET" ]] || { echo "o socket $SOCKET não apareceu; veja journalctl -u $SERVICE" >&2; exit 1; }
stat -c '   socket %n: %U:%G %a' "$SOCKET"
