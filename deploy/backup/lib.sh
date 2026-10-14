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

# require_count <rótulo> <valor>: o valor é um inteiro decimal não negativo, ou o script para.
# Valores lidos do banco restaurado vêm de um dump que pode ser forjado, e a aritmética do bash
# executa substituições (`a[$(comando)]`): nada do banco entra em (( )) sem passar por aqui.
require_count() {
  [[ "$2" =~ ^[0-9]+$ ]] || {
    echo "valor inesperado para $1 (esperado um inteiro decimal)" >&2
    exit 1
  }
}

# require_revision <rótulo> <valor>: só letras minúsculas, dígitos e _, como as revisões do Alembic.
require_revision() {
  [[ "$2" =~ ^[0-9a-z_]+$ ]] || {
    echo "valor inesperado para $1 (esperada uma revisão do Alembic)" >&2
    exit 1
  }
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
# A URL é segredo (quem a tem cala o alerta pingando sucesso) e vai pelo stdin, como a credencial.
healthcheck_ping() {
  local url="$1" suffix="$2"
  [[ -n "$url" ]] || return 0
  url="${url%/}${suffix}"
  url="${url//\\/\\\\}"
  url="${url//\"/\\\"}"
  printf 'url = "%s"\n' "$url" |
    curl --config - --fail --silent --show-error --max-time 10 --retry 5 --output /dev/null ||
    echo "aviso: o ping ${suffix:-de sucesso} ao healthchecks falhou" >&2
}

# Nome do dump: o instante UTC ordena as chaves na listagem; os 128 bits aleatórios impedem que
# a chave que só escreve sobrescreva um dump existente adivinhando o nome dele. O nome inteiro
# não vai para log nenhum: o journal da VPS e o log público do Actions mostram só o instante.
# shellcheck disable=SC2034  # usado pelo pg-restore-check.sh, que carrega este arquivo
DUMP_NAME_PATTERN='goblinshift-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}\.dump\.age'
dump_name() {
  printf 'goblinshift-%s-%s.dump.age' "$(date -u +%Y%m%dT%H%M%SZ)" "$(openssl rand -hex 16)"
}

# dump_instant <chave>: só o instante UTC do nome, o que pode aparecer em log.
dump_instant() {
  local name="${1##*/}"
  printf '%s' "${name:12:16}"
}
