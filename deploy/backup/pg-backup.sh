#!/usr/bin/env bash
# Backup diário do PostgreSQL para outro provedor (docs/GoblinShiftPlan.md, 3.8; ADR 022).
#
# pg_dump -Fc dentro do container do PostgreSQL → age com a chave pública → PUT no S3 com uma
# chave só de escrita → ping no healthchecks.io (/start, sucesso ou /fail). A expiração em 30
# dias é regra de ciclo de vida do bucket, não deste script: a chave da VPS não apaga nem lê.
#
# Rodado pelo timer goblinshift-pg-backup.timer, da cópia que o deploy/backup/install.sh faz em
# /usr/local/lib/goblinshift/backup, com a configuração em /srv/goblinshift/secrets/backup.env.
# Variáveis:
#   BACKUP_PG_CONTAINER         container do PostgreSQL (a unit define goblinshift-postgres-1)
#   BACKUP_PG_USER, BACKUP_PG_DB    padrão: goblinshift
#   BACKUP_AGE_RECIPIENTS_FILE  padrão: age-recipients.txt, versionado, ao lado deste script
#   BACKUP_S3_ENDPOINT, BACKUP_S3_REGION, BACKUP_S3_BUCKET, BACKUP_S3_PREFIX (padrão: pg/)
#   BACKUP_S3_KEY_ID, BACKUP_S3_SECRET  chave de aplicação só de escrita
#   BACKUP_HEALTHCHECK_URL      URL de ping do check "backup diário"
#   STATE_DIRECTORY             definido pelo systemd (StateDirectory=goblinshift-backup, 0700):
#                               guarda o marcador last-upload-ok com o instante do último upload bom
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=SCRIPTDIR/lib.sh
source "$here/lib.sh"

require_env BACKUP_PG_CONTAINER BACKUP_S3_ENDPOINT BACKUP_S3_REGION BACKUP_S3_BUCKET \
  BACKUP_S3_KEY_ID BACKUP_S3_SECRET BACKUP_HEALTHCHECK_URL STATE_DIRECTORY
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

# O pg_dump de um volume recriado vazio, com o esquema refeito pelo migrate, sai 0. Sem esta
# conferência ele seria o backup bom do dia, e a restauração mensal só perceberia semanas depois.
# Nada sobe, e o EXIT pinga /fail no alerta que já existe.
pg_value() {
  docker exec "$BACKUP_PG_CONTAINER" psql --no-psqlrc --tuples-only --no-align \
    --set ON_ERROR_STOP=1 --no-password --username="$pg_user" --dbname="$pg_db" --command="$1"
}
# Em variável, e não dentro do [[ ]]: com o psql falhando (container parado, autenticação), o
# set -e para aqui com o erro do psql, em vez de culpar a falta de alembic_version.
migrated="$(pg_value "SELECT to_regclass('public.alembic_version') IS NOT NULL")"
if [[ "$migrated" != "t" ]]; then
  echo "o banco não tem alembic_version: dump não enviado" >&2
  exit 1
fi
accounts="$(pg_value "SELECT count(*) FROM app_user")"
require_count accounts "$accounts"
if ((accounts == 0)); then
  echo "o banco não tem contas: dump não enviado" >&2
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
# O B2 recusa com 400 o PUT sem Content-MD5 num bucket com Object Lock. O hash é do arquivo já
# gravado, com os mesmos bytes que o curl envia, e o header entra na assinatura SigV4.
md5="$(openssl dgst -md5 -binary "$encrypted" | base64)"
s3_curl --upload-file "$encrypted" --header "Content-Type: application/octet-stream" \
  --header "Content-MD5: $md5" --max-time 1800 --output /dev/null "$(s3_url "$key")"
echo "backup enviado: dump de $(dump_instant "$key") ($(wc -c <"$encrypted" | tr -d ' ') bytes cifrados)"

# Marcador do último upload bom, para a conferência do host (fora deste repositório, ainda não
# ligada; ver ADR 022) alertar quando ele passar de 2 dias: os /fail repetidos não correm em
# silêncio enquanto o ciclo de vida do bucket expira os dumps antigos. Um date ou mv que falhe
# aqui pinga /fail com o dump já enviado: a escolha conservadora, porque o marcador deixaria de
# refletir o último upload.
# Só o instante UTC, sem bucket nem chave. Gravado num temporário e renomeado: quem lê nunca vê
# um arquivo pela metade, e uma falha antes daqui deixa o marcador anterior intacto.
marker="$STATE_DIRECTORY/last-upload-ok"
(umask 077 && date -u +%Y-%m-%dT%H:%M:%SZ >"$marker.tmp")
mv -f "$marker.tmp" "$marker"
