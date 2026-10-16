#!/usr/bin/env bash
# Teste de carga contra o Compose local (deploy/load/README.md). Nunca contra produção: o alvo
# precisa ser o Nginx do Compose, a própria máquina ou um IP privado; qualquer outro é recusado.
#
# Uso:
#   deploy/load/run-local.sh                    # cria as contas, roda os cenários e as revoga
#   deploy/load/run-local.sh --check-target URL # só confere se o alvo é aceito (sai com 0 ou 2)
#
# Variáveis: GS_LOAD_TARGET (padrão http://nginx, na rede do Compose), GS_LOAD_PROJECT (projeto do
# Compose, padrão goblinshift), GS_LOAD_RESULTS (pasta dos resumos JSON), GS_LOAD_RATE_LIMIT=0 e
# GS_LOAD_AUTH=0 (desligam os cenários do limite por conta e do limite de /auth/*).
set -euo pipefail

readonly DEFAULT_TARGET="http://nginx"
readonly K6_IMAGE="grafana/k6:2.3.0"
readonly ACCOUNT_COUNT=20
readonly KEY_DAYS=1
readonly KEY_SCOPES="files:read,files:write,runs:read,runs:write"
readonly EXIT_REFUSED=2

LOAD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly LOAD_DIR
ROOT="$(cd "$LOAD_DIR/../.." && pwd)"
readonly ROOT

refuse() {
  echo "run-local: alvo recusado: $1" >&2
  exit "$EXIT_REFUSED"
}

# Ecoa o host do alvo; recusa esquema, credencial embutida e host fora da allowlist local.
target_host() {
  local url="$1" rest host
  [[ "$url" =~ ^https?:// ]] || refuse "'$url' não é http(s)"
  rest="${url#*://}"
  rest="${rest%%/*}"
  [[ "$rest" != *@* ]] || refuse "'$url' traz credencial no endereço"
  host="${rest%%:*}"
  is_local_host "$host" || refuse "'$host' não é o Compose local, a máquina nem um IP privado"
  echo "$host"
}

is_local_host() {
  local host="$1"
  case "$host" in
    nginx | localhost | host.docker.internal) return 0 ;;
  esac
  local octet='(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])'
  [[ "$host" =~ ^$octet\.$octet\.$octet\.$octet$ ]] || return 1
  local a="${BASH_REMATCH[1]}" b="${BASH_REMATCH[2]}"
  ((a == 127 || a == 10 || (a == 172 && b >= 16 && b <= 31) || (a == 192 && b == 168)))
}

compose() {
  docker compose --project-directory "$ROOT" -p "$PROJECT" \
    -f "$ROOT/compose.yaml" -f "$ROOT/compose.override.yaml" "$@"
}

# Rede do container do k6: a do Compose para o Nginx pelo nome; a do host para a própria máquina.
k6_network() {
  case "$1" in
    nginx) echo "${PROJECT}_internal" ;;
    localhost | 127.*) echo host ;;
    *) echo bridge ;;
  esac
}

create_accounts() {
  local i user token public_id tokens=()
  for ((i = 1; i <= ACCOUNT_COUNT; i++)); do
    user="$(compose exec -T api gs-admin create-user --display-name "carga-$i")"
    token="$(compose exec -T api gs-admin issue-key --user "$user" --scopes "$KEY_SCOPES" \
      --days "$KEY_DAYS" 2>"$WORK_DIR/issue.err")"
    public_id="$(sed -n 's/^chave \([0-9a-f]*\) .*/\1/p' "$WORK_DIR/issue.err")"
    echo "$public_id" >>"$WORK_DIR/keys"
    tokens+=("\"$token\"")
  done
  local IFS=,
  (umask 077 && echo "[${tokens[*]}]" >"$WORK_DIR/accounts/accounts.json")
}

revoke_accounts() {
  [[ -f "$WORK_DIR/keys" ]] || return 0
  local public_id
  while read -r public_id; do
    compose exec -T api gs-admin revoke-key --key "$public_id" 2>/dev/null ||
      echo "run-local: não revoguei a chave $public_id; revogue com gs-admin revoke-key" >&2
  done <"$WORK_DIR/keys"
}

cleanup() {
  revoke_accounts
  rm -rf "$WORK_DIR"
}

run_scenario() {
  local name="$1"
  echo "=== cenário: $name" >&2
  if docker run --rm --user "$(id -u):$(id -g)" --network "$NETWORK" \
    -v "$LOAD_DIR/scenarios:/scripts:ro" -v "$WORK_DIR/accounts:/accounts:ro" \
    -v "$RESULTS:/results" \
    -e GS_LOAD_TARGET="$TARGET" -e GS_LOAD_ACCOUNTS=/accounts/accounts.json \
    "$K6_IMAGE" run --quiet --summary-export "/results/$name.json" "/scripts/$name.js"; then
    PASSED+=("$name")
  else
    FAILED+=("$name")
  fi
}

# A fila só enche com o worker parado; ele volta mesmo se o cenário falhar. A manutenção para e
# volta junto: ela vive no namespace de PIDs do worker (compose.override.yaml) e morreria com ele.
run_queue_scenario() {
  compose stop maintenance worker >&2
  run_scenario fila
  compose start worker maintenance >&2
}

main() {
  if [[ "${1:-}" == --check-target ]]; then
    target_host "${2-$DEFAULT_TARGET}" >/dev/null
    exit 0
  fi
  TARGET="${GS_LOAD_TARGET:-$DEFAULT_TARGET}"
  PROJECT="${GS_LOAD_PROJECT:-goblinshift}"
  local host
  host="$(target_host "$TARGET")"
  NETWORK="$(k6_network "$host")"
  RESULTS="${GS_LOAD_RESULTS:-$ROOT/deploy/load/results}"
  mkdir -p "$RESULTS"
  compose ps --status running --services | grep -qx api ||
    { echo "run-local: a API do projeto '$PROJECT' não está rodando" >&2; exit 1; }

  WORK_DIR="$(mktemp -d)"
  mkdir -m 700 "$WORK_DIR/accounts"
  trap cleanup EXIT
  create_accounts

  PASSED=()
  FAILED=()
  run_queue_scenario
  run_scenario transferencias
  run_scenario jornada
  if [[ "${GS_LOAD_RATE_LIMIT:-1}" != 0 ]]; then
    run_scenario limite-por-conta
  fi
  if [[ "${GS_LOAD_AUTH:-1}" != 0 ]]; then
    run_scenario limite-auth
  fi

  echo "aprovados: ${PASSED[*]:-nenhum}; reprovados: ${FAILED[*]:-nenhum}; resumos em $RESULTS" >&2
  ((${#FAILED[@]} == 0))
}

main "$@"
