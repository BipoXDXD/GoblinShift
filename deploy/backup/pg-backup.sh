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
#                               guarda last-upload-ok; a unit antiga ainda faz backup sem marcador
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
marker_tmp=
snapshot_pid=
close_snapshot() {
  [[ -n "$snapshot_pid" ]] || return 0
  local pid="$snapshot_pid"
  snapshot_pid=
  # EOF encerra o psql e a transação que exportou o snapshot.
  if [[ -n "${snapshot_in:-}" ]]; then exec {snapshot_in}>&-; fi
  if [[ -n "${snapshot_out:-}" ]]; then exec {snapshot_out}<&-; fi
  wait "$pid"
}
finish() {
  local status=$?
  close_snapshot || :
  rm -rf "${workdir:?}"
  if [[ -n "$marker_tmp" ]]; then
    rm -f -- "${marker_tmp:?}" || :
  fi
  if ((status == 0)); then
    healthcheck_ping "$BACKUP_HEALTHCHECK_URL" ""
  else
    echo "backup falhou (saída $status)" >&2
    healthcheck_ping "$BACKUP_HEALTHCHECK_URL" "/fail"
  fi
}
trap finish EXIT
# Um psql que fecha a entrada pode interromper o printf no FIFO com SIGPIPE.
trap 'exit 141' PIPE
healthcheck_ping "$BACKUP_HEALTHCHECK_URL" "/start"

# Sem destinatário o age recusaria cifrar; a checagem dá a mensagem certa antes do dump.
if ! grep -qvE '^[[:space:]]*(#|$)' "$recipients" 2>/dev/null; then
  echo "nenhum destinatário age em $recipients: acrescente a chave pública (age1...)" >&2
  exit 1
fi

# O pg_dump de um volume recriado vazio sai 0. A conferência e o dump precisam enxergar
# o mesmo estado, inclusive se a purga apagar a última conta entre os dois.
mkfifo "$workdir/psql-in" "$workdir/psql-out"
docker exec -i "$BACKUP_PG_CONTAINER" psql --no-psqlrc --quiet --tuples-only --no-align \
  --set ON_ERROR_STOP=1 --no-password --username="$pg_user" --dbname="$pg_db" \
  <"$workdir/psql-in" >"$workdir/psql-out" &
snapshot_pid=$!
exec {snapshot_in}>"$workdir/psql-in"
exec {snapshot_out}<"$workdir/psql-out"
printf '%s\n' 'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;' >&"$snapshot_in"

pg_value() {
  printf '%s\n' "$1" >&"$snapshot_in"
  IFS= read -r "$2" <&"$snapshot_out"
}
migrated='' accounts='' snapshot=''
pg_value "SELECT to_regclass('public.alembic_version') IS NOT NULL;" migrated
if [[ "$migrated" != "t" ]]; then
  echo "o banco não tem alembic_version: dump não enviado" >&2
  exit 1
fi
pg_value "SELECT count(*) FROM public.app_user;" accounts
require_count accounts "$accounts"
if ((accounts == 0)); then
  echo "o banco não tem contas: dump não enviado" >&2
  exit 1
fi
pg_value 'SELECT pg_export_snapshot();' snapshot
if [[ ! "$snapshot" =~ ^[[:xdigit:]-]+$ ]]; then
  echo "o PostgreSQL não retornou um snapshot válido: dump não enviado" >&2
  exit 1
fi

key="${prefix}$(dump_name)"
encrypted="$workdir/dump.age"
# Formato custom: comprimido e restaurável seletivamente pelo pg_restore. Com pipefail, um
# pg_dump que falha no meio derruba o script antes do upload de um arquivo truncado.
docker exec "$BACKUP_PG_CONTAINER" \
  pg_dump --format=custom --no-password --username="$pg_user" --dbname="$pg_db" \
    --snapshot="$snapshot" |
  age --encrypt --recipients-file "$recipients" --output "$encrypted"
close_snapshot

# PUT simples (até 5 GB, folga sobre os 10 GiB do banco comprimidos): o S3 não aceita corpo
# chunked, por isso o dump passa por um arquivo temporário em vez de ir direto do pipe.
# O B2 recusa com 400 o PUT sem Content-MD5 num bucket com Object Lock. O hash é do arquivo já
# gravado, com os mesmos bytes que o curl envia, e o header entra na assinatura SigV4.
md5="$(openssl dgst -md5 -binary "$encrypted" | base64)"
# O --fail do curl cobre 4xx/5xx, mas um redirect também pode sair com código zero.
# A confirmação do PutObject da API S3 é HTTP 200.
http_status="$(s3_curl --upload-file "$encrypted" --header "Content-Type: application/octet-stream" \
  --header "Content-MD5: $md5" --max-time 1800 --output /dev/null \
  --write-out '%{http_code}' "$(s3_url "$key")")"
if [[ "$http_status" != 200 ]]; then
  echo "o S3 não confirmou o upload (esperado HTTP 200)" >&2
  exit 1
fi
echo "backup enviado: dump de $(dump_instant "$key") ($(wc -c <"$encrypted" | tr -d ' ') bytes cifrados)"

# Marcador do último upload bom, para a conferência do host (fora deste repositório, ainda não
# ligada; ver ADR 022) alertar quando ele passar de 2 dias: os /fail repetidos não correm em
# silêncio enquanto o ciclo de vida do bucket expira os dumps antigos. Um date ou mv que falhe
# aqui pinga /fail com o dump já enviado: a escolha conservadora, porque o marcador deixaria de
# refletir o último upload.
# Só o instante UTC, sem bucket nem chave. Gravado num temporário e renomeado: quem lê nunca vê
# um arquivo pela metade, e uma falha antes daqui deixa o marcador anterior intacto.
# O instalador troca o script antes do daemon-reload. Se a unit antiga ainda estiver em
# memória, preserve o backup e o ping de sucesso e avise que a instalação precisa terminar.
if [[ -z "${STATE_DIRECTORY:-}" ]]; then
  echo "aviso: STATE_DIRECTORY ausente; conclua a instalação da unit para gravar last-upload-ok" >&2
  exit 0
fi
marker="$STATE_DIRECTORY/last-upload-ok"
if [[ -d "$marker" ]]; then
  echo "last-upload-ok deveria ser um arquivo" >&2
  exit 1
fi
marker_tmp="$(mktemp "$STATE_DIRECTORY/.last-upload-ok.XXXXXX")"
date -u +%Y-%m-%dT%H:%M:%SZ >"$marker_tmp"
mv -f -- "$marker_tmp" "$marker"
marker_tmp=
