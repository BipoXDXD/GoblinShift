#!/usr/bin/env bash
# Backup diário do PostgreSQL para outro provedor (docs/GoblinShiftPlan.md, 3.8; ADR 9xx).
#
# pg_dump -Fc dentro do container do PostgreSQL → age com a chave pública → PUT no S3 com uma
# chave só de escrita → ping no healthchecks.io (/start, sucesso ou /fail). A expiração em 30
# dias é regra de ciclo de vida do bucket, não deste script: a chave da VPS não apaga nem lê.
#
# Rodado pelo timer goblinshift-pg-backup.timer (deploy/backup/install.sh), com a configuração
# em /srv/goblinshift/secrets/backup.env. Variáveis:
#   BACKUP_PG_CONTAINER         container do PostgreSQL (a unit define goblinshift-postgres-1)
#   BACKUP_PG_USER, BACKUP_PG_DB    padrão: goblinshift
#   BACKUP_AGE_RECIPIENTS_FILE  padrão: deploy/backup/age-recipients.txt, versionado
#   BACKUP_S3_ENDPOINT, BACKUP_S3_REGION, BACKUP_S3_BUCKET, BACKUP_S3_PREFIX (padrão: pg/)
#   BACKUP_S3_KEY_ID, BACKUP_S3_SECRET  chave de aplicação só de escrita
#   BACKUP_HEALTHCHECK_URL      URL de ping do check "backup diário"
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=SCRIPTDIR/lib.sh
source "$here/lib.sh"

require_env BACKUP_PG_CONTAINER BACKUP_S3_ENDPOINT BACKUP_S3_REGION BACKUP_S3_BUCKET \
  BACKUP_S3_KEY_ID BACKUP_S3_SECRET BACKUP_HEALTHCHECK_URL
pg_user="${BACKUP_PG_USER:-goblinshift}"
pg_db="${BACKUP_PG_DB:-goblinshift}"
prefix="${BACKUP_S3_PREFIX:-pg/}"
recipients="${BACKUP_AGE_RECIPIENTS_FILE:-$here/age-recipients.txt}"

workdir="$(mktemp -d)"
finish() {
  local status=$?
  rm -rf "$workdir"
  if ((status == 0)); then
    healthcheck_ping "$BACKUP_HEALTHCHECK_URL" ""
  else
    echo "backup falhou (saída $status)" >&2
    healthcheck_ping "$BACKUP_HEALTHCHECK_URL" "/fail"
  fi
}
trap finish EXIT
healthcheck_ping "$BACKUP_HEALTHCHECK_URL" "/start"

# Sem destinatário o age recusaria cifrar; a checagem dá a mensagem certa antes do dump.
if ! grep -qvE '^[[:space:]]*(#|$)' "$recipients" 2>/dev/null; then
  echo "nenhum destinatário age em $recipients: acrescente a chave pública (age1...)" >&2
  exit 1
fi

key="${prefix}$(dump_name)"
encrypted="$workdir/dump.age"
# Formato custom: comprimido e restaurável seletivamente pelo pg_restore. Com pipefail, um
# pg_dump que falha no meio derruba o script antes do upload de um arquivo truncado.
docker exec "$BACKUP_PG_CONTAINER" \
  pg_dump --format=custom --no-password --username="$pg_user" --dbname="$pg_db" |
  age --encrypt --recipients-file "$recipients" --output "$encrypted"

# PUT simples (até 5 GB, folga sobre os 10 GiB do banco comprimidos): o S3 não aceita corpo
# chunked, por isso o dump passa por um arquivo temporário em vez de ir direto do pipe.
s3_curl --upload-file "$encrypted" --header "Content-Type: application/octet-stream" \
  --max-time 1800 --output /dev/null "$(s3_url "$key")"
echo "backup enviado: dump de $(dump_instant "$key") ($(wc -c <"$encrypted" | tr -d ' ') bytes cifrados)"
