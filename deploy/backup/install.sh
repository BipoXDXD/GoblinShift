#!/usr/bin/env bash
# Instala o backup diário do PostgreSQL como timer do systemd (docs/adr/022-backup-externo.md).
# Idempotente e aditivo, como o deploy/executor-ctl/install.sh: não sobrescreve a configuração
# existente e só liga o timer quando ela está completa, porque um backup sem a URL do
# healthchecks.io falharia em silêncio.
#
# O backup.env mora em /srv/goblinshift/secrets, que pertence ao usuário da aplicação: tudo o que o
# instalador faz ali roda com os privilégios dele (as_app), nunca como root. Como root, um symlink
# plantado no lugar do arquivo levaria a escrita, o chown e o chmod para qualquer arquivo do sistema.
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
as_app() { (cd / && runuser -u "$APP_USER" -- "$@"); }
# create_as_app <arquivo>: grava o stdin num arquivo novo, 600, como o usuário da aplicação. O
# noclobber cria com O_EXCL: falha se o caminho já existir, mesmo como symlink.
create_as_app() {
  # shellcheck disable=SC2016 # o $1 é expandido pelo bash do goblinshift, não por este.
  as_app bash -c 'umask 077 && set -o noclobber && cat >"$1"' _ "$1"
}

note "Ferramentas (age e curl com SigV4)"
command -v age >/dev/null || apt-get install -yq age
command -v curl >/dev/null || apt-get install -yq curl

note "Configuração ${CONFIG}"
if ! as_app test -e "$CONFIG"; then
  {
    echo "# Backup externo do PostgreSQL; preencha e rode este instalador de novo."
    echo "# Chave de aplicação do B2 SÓ DE ESCRITA (writeFiles, sem deleteFiles nem readFiles)."
    for name in "${REQUIRED[@]}"; do echo "${name}="; done
  } | create_as_app "$CONFIG"
  echo "   criado vazio."
else
  echo "   já existe; mantido como está."
fi

note "Units em ${UNIT_DIR}"
install -m 644 "$SOURCE/goblinshift-pg-backup.service" "$SOURCE/$TIMER" "$UNIT_DIR/"
systemctl daemon-reload

missing=()
for name in "${REQUIRED[@]}"; do
  as_app grep -qE "^${name}=.+" "$CONFIG" || missing+=("$name")
done
if ((${#missing[@]})); then
  echo "   timer não ligado: falta ${missing[*]} em $CONFIG" >&2
  exit 1
fi
systemctl enable --now "$TIMER"
echo "   timer ligado; teste já com: sudo systemctl start goblinshift-pg-backup.service"
