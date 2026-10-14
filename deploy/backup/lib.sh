# shellcheck shell=bash
# Funções comuns ao backup e à restauração do PostgreSQL (docs/adr/9xx-backup-externo.md).
# Carregado com `source`; não roda sozinho.
#
# O S3 é acessado só pelo curl, com a assinatura AWS SigV4 dele (curl >= 7.75): o host não
# precisa de CLI da AWS nem de rclone. A credencial entra pelo stdin do curl (`--config -`),
# escrita por `printf`, que é builtin do bash: nunca aparece na linha de comando nem no `ps`.

# Para na hora se faltar alguma variável, com o nome de todas as que faltam.
require_env() {
  local name missing=()
  for name in "$@"; do
    [[ -n "${!name:-}" ]] || missing+=("$name")
  done
  if ((${#missing[@]})); then
    echo "configuração incompleta: defina ${missing[*]}" >&2
    exit 2
  fi
}

# s3_curl <argumentos do curl...>: requisição assinada com BACKUP_S3_KEY_ID/BACKUP_S3_SECRET.
# Repete só falhas transitórias (timeout, 408, 429, 5xx); 4xx de credencial falha de primeira.
s3_curl() {
  printf 'user = "%s:%s"\n' "$BACKUP_S3_KEY_ID" "$BACKUP_S3_SECRET" |
    curl --config - --fail --silent --show-error \
      --aws-sigv4 "aws:amz:${BACKUP_S3_REGION}:s3" \
      --connect-timeout 10 --retry 3 "$@"
}

# URL path-style do objeto: <endpoint>/<bucket>/<chave>.
s3_url() {
  printf '%s/%s/%s' "${BACKUP_S3_ENDPOINT%/}" "$BACKUP_S3_BUCKET" "$1"
}

# healthcheck_ping <url ou vazio> <sufixo>: "/start", "" (sucesso) ou "/fail".
# Falhar o ping não muda o resultado: sem o ping de sucesso, o healthchecks.io avisa sozinho.
healthcheck_ping() {
  local url="$1" suffix="$2"
  [[ -n "$url" ]] || return 0
  curl --fail --silent --show-error --max-time 10 --retry 5 --output /dev/null \
    "${url%/}${suffix}" || echo "aviso: o ping ${suffix:-de sucesso} ao healthchecks falhou" >&2
}

# Nome do dump: o instante UTC no nome ordena as chaves na listagem e dá a idade do dump.
# shellcheck disable=SC2034  # usado pelo pg-restore-check.sh, que carrega este arquivo
DUMP_NAME_PATTERN='goblinshift-[0-9]{8}T[0-9]{6}Z\.dump\.age'
dump_name() {
  printf 'goblinshift-%s.dump.age' "$(date -u +%Y%m%dT%H%M%SZ)"
}
