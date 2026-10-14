#!/usr/bin/env bash
# Instala o backup diário do PostgreSQL como timer do systemd (docs/adr/022-backup-externo.md).
# Idempotente e aditivo, como o deploy/executor-ctl/install.sh: não sobrescreve a configuração
# existente e só liga o timer quando ela está completa, porque um backup sem a URL do
# healthchecks.io falharia em silêncio.
#
# Uso: sudo bash deploy/backup/install.sh
set -euo pipefail

APP_USER=goblinshift
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG=/srv/goblinshift/secrets/backup.env
UNIT_DIR=/etc/systemd/system
TIMER=goblinshift-pg-backup.timer
REQUIRED=(BACKUP_S3_ENDPOINT BACKUP_S3_REGION BACKUP_S3_BUCKET BACKUP_S3_KEY_ID BACKUP_S3_SECRET
  BACKUP_HEALTHCHECK_URL)
[[ $EUID -eq 0 ]] || { echo "rode com sudo" >&2; exit 1; }
note() { printf '==> %s\n' "$*"; }

note "Ferramentas (age e curl com SigV4)"
command -v age >/dev/null || apt-get install -yq age
command -v curl >/dev/null || apt-get install -yq curl

note "Configuração ${CONFIG}"
if [[ ! -f "$CONFIG" ]]; then
  {
    echo "# Backup externo do PostgreSQL; preencha e rode este instalador de novo."
    echo "# Chave de aplicação do B2 SÓ DE ESCRITA (writeFiles, sem deleteFiles nem readFiles)."
    for name in "${REQUIRED[@]}"; do echo "${name}="; done
  } >"$CONFIG"
  chown "$APP_USER:$APP_USER" "$CONFIG"
  chmod 600 "$CONFIG"
  echo "   criado vazio."
else
  echo "   já existe; mantido como está."
fi

note "Units em ${UNIT_DIR}"
install -m 644 "$SOURCE/goblinshift-pg-backup.service" "$SOURCE/$TIMER" "$UNIT_DIR/"
systemctl daemon-reload

missing=()
for name in "${REQUIRED[@]}"; do
  grep -qE "^${name}=.+" "$CONFIG" || missing+=("$name")
done
if ((${#missing[@]})); then
  echo "   timer não ligado: falta ${missing[*]} em $CONFIG" >&2
  exit 1
fi
systemctl enable --now "$TIMER"
echo "   timer ligado; teste já com: sudo systemctl start goblinshift-pg-backup.service"
