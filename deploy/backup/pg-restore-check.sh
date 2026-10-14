#!/usr/bin/env bash
# Restauração testada do backup do PostgreSQL (docs/GoblinShiftPlan.md, 3.8; ADR 9xx).
#
# Roda fora da VPS (workflow restore-check.yml): baixa o dump mais recente com a chave só de
# leitura, decifra com a chave privada age, restaura num PostgreSQL vazio e descartável e
# confere o resultado. Qualquer falha sai com status diferente de zero e pinga /fail.
#
# Variáveis:
#   BACKUP_S3_ENDPOINT, BACKUP_S3_REGION, BACKUP_S3_BUCKET, BACKUP_S3_PREFIX (padrão: pg/)
#   BACKUP_S3_KEY_ID, BACKUP_S3_SECRET  chave de aplicação só de leitura e listagem
#   BACKUP_AGE_IDENTITY_FILE    arquivo com a chave privada age, gerada pelo age-keygen
#   RESTORE_PG_CONTAINER, RESTORE_PG_USER, RESTORE_PG_DB   destino vazio, acessado por docker exec
#   RESTORE_KNOWN_REVISIONS     revisões do Alembic que o código conhece, separadas por espaço
#   RESTORE_MAX_AGE_HOURS       idade máxima do dump mais recente (padrão: 26)
#   RESTORE_MIN_RETENTION_DAYS  retenção mínima do Object Lock em compliance (padrão: 30)
#   RESTORE_HEALTHCHECK_URL     URL de ping do check "restauração mensal" (opcional)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=SCRIPTDIR/lib.sh
source "$here/lib.sh"

require_env BACKUP_S3_ENDPOINT BACKUP_S3_REGION BACKUP_S3_BUCKET BACKUP_S3_KEY_ID \
  BACKUP_S3_SECRET BACKUP_AGE_IDENTITY_FILE RESTORE_PG_CONTAINER RESTORE_PG_USER RESTORE_PG_DB \
  RESTORE_KNOWN_REVISIONS
prefix="${BACKUP_S3_PREFIX:-pg/}"
max_age_hours="${RESTORE_MAX_AGE_HOURS:-26}"
min_retention_days="${RESTORE_MIN_RETENTION_DAYS:-30}"
healthcheck="${RESTORE_HEALTHCHECK_URL:-}"

workdir="$(mktemp -d)"
finish() {
  local status=$?
  rm -rf "$workdir"
  if ((status == 0)); then
    healthcheck_ping "$healthcheck" ""
  else
    echo "restauração testada falhou (saída $status)" >&2
    healthcheck_ping "$healthcheck" "/fail"
  fi
}
trap finish EXIT
healthcheck_ping "$healthcheck" "/start"

fail() {
  echo "$*" >&2
  exit 1
}

psql_value() {
  docker exec "$RESTORE_PG_CONTAINER" psql --no-psqlrc --tuples-only --no-align \
    --set ON_ERROR_STOP=1 --username="$RESTORE_PG_USER" --dbname="$RESTORE_PG_DB" --command="$1"
}

# Restaurar por cima de tabelas existentes não provaria nada sobre o dump.
tables="$(psql_value "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")"
require_count tables "$tables"
((tables == 0)) || fail "o banco de destino precisa estar vazio (tem $tables tabelas em public)"

# Listagem com até 1000 chaves; a retenção de 30 dias deixa umas 31. Se um dia passar disso,
# falha em vez de escolher um dump que talvez não seja o mais recente. O dump_check.py escolhe
# pelo instante de envio que o S3 grava, não pelo nome, e recusa datas futuras.
s3_curl --max-time 60 --output "$workdir/listing.xml" \
  "${BACKUP_S3_ENDPOINT%/}/${BACKUP_S3_BUCKET}?list-type=2&prefix=${prefix}"
python3 -I "$here/dump_check.py" latest --prefix "$prefix" --max-age-hours "$max_age_hours" \
  <"$workdir/listing.xml" >"$workdir/latest.txt"
{ read -r latest && read -r age_hours; } <"$workdir/latest.txt"
require_count age_hours "$age_hours"
echo "dump mais recente: $(dump_instant "$latest"), enviado há $age_hours h"

# Imutável por 30 dias: sem o Object Lock em compliance, a chave da VPS sobrescreveria o dump.
s3_curl --head --max-time 60 --output "$workdir/head.txt" "$(s3_url "$latest")"
python3 -I "$here/dump_check.py" retention --min-days "$min_retention_days" <"$workdir/head.txt"

s3_curl --max-time 1800 --output "$workdir/dump.age" "$(s3_url "$latest")"
age --decrypt --identity "$BACKUP_AGE_IDENTITY_FILE" --output "$workdir/dump" "$workdir/dump.age"

# Sem dono nem privilégios: o destino efêmero não tem os papéis da produção.
docker exec --interactive "$RESTORE_PG_CONTAINER" pg_restore --exit-on-error --no-owner \
  --no-privileges --username="$RESTORE_PG_USER" --dbname="$RESTORE_PG_DB" <"$workdir/dump"

revision="$(psql_value "SELECT version_num FROM alembic_version")"
require_revision revision "$revision"
[[ " $RESTORE_KNOWN_REVISIONS " == *" $revision "* ]] ||
  fail "alembic_version $revision não é uma revisão conhecida pelo código"
accounts="$(psql_value "SELECT count(*) FROM app_user")"
runs="$(psql_value "SELECT count(*) FROM run")"
require_count accounts "$accounts"
require_count runs "$runs"
((accounts > 0)) || fail "o dump restaurado não tem contas"
((runs > 0)) || fail "o dump restaurado não tem execuções"
echo "restauração conferida: revisão $revision, $accounts contas, $runs execuções, dump de $age_hours h"
