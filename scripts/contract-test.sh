#!/usr/bin/env bash
# Teste de contrato com Schemathesis (job "Contrato" do CI; seção Segurança de APIs do projeto).
#
# Sobe a API deste checkout em 127.0.0.1, migra o banco, cria uma conta e uma chave com o
# gs-admin e roda `schemathesis run --checks=all` contra o contrato exportado por
# gs-export-openapi. Falha com 500, com resposta fora da spec (status, corpo, headers, tipo) ou
# com a API aceitando o que a spec proíbe.
#
# Nunca roda contra produção: a URL é sempre a da API que o próprio script sobe, e ele se recusa
# a começar com GS_ENVIRONMENT=production, com o segredo do banco em arquivo (como na VPS) ou com
# um banco fora desta máquina. Contra a VPS ele consumiria cotas e criaria Runs.
#
# Pré-requisito: um PostgreSQL vazio e descartável em localhost, nas variáveis GS_DB_* (o CI usa
# um service container). Localmente:
#   docker run -d --rm --name gs-contract -e POSTGRES_USER=goblinshift \
#     -e POSTGRES_PASSWORD=goblinshift -e POSTGRES_DB=goblinshift -p 55432:5432 postgres:18.6-trixie
#   GS_DB_PORT=55432 ./scripts/contract-test.sh
#
# Variáveis: CONTRACT_MAX_EXAMPLES (padrão 100, casos por operação em cada fase) e CONTRACT_SEED
# (padrão fixo, para que um achado se reproduza no CI e na máquina; troque para explorar).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${CONTRACT_PORT:-18765}"
# Os caminhos da spec já trazem o prefixo /api/v1.
BASE_URL="http://127.0.0.1:${PORT}"
MAX_EXAMPLES="${CONTRACT_MAX_EXAMPLES:-100}"
SEED="${CONTRACT_SEED:-20261014}"

refuse() {
  echo "contract-test: recusado: $*" >&2
  exit 2
}
[[ "${GS_ENVIRONMENT:-test}" != "production" ]] || refuse "GS_ENVIRONMENT=production"
[[ -z "${GS_DB_PASSWORD_FILE:-}" ]] || refuse "GS_DB_PASSWORD_FILE definido (banco da VPS?)"
case "${GS_DB_HOST:-localhost}" in
  localhost | 127.0.0.1) ;;
  *) refuse "GS_DB_HOST=${GS_DB_HOST} não é local" ;;
esac
export GS_ENVIRONMENT=test GS_RUNNER=local

cd "$REPO_ROOT"
WORK="$(mktemp -d)"
export GS_STORAGE_ROOT="$WORK/storage" GS_WORK_ROOT="$WORK/work"
api_pid=""
# SC2329: chamada pelo trap EXIT, que o shellcheck não conta como uso.
# shellcheck disable=SC2329
cleanup() {
  if [[ -n "$api_pid" ]]; then
    kill "$api_pid" 2>/dev/null || true
    wait "$api_pid" 2>/dev/null || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

uv run alembic upgrade head

# Fábrica de teste isolada: orçamento alto de requisições para o fuzzer chegar às validações;
# autenticação, cotas e contador continuam reais. A imagem de produção não inclui scripts/.
uv run uvicorn scripts.contract_server:create_app --factory \
  --host 127.0.0.1 --port "$PORT" --no-access-log >"$WORK/api.log" 2>&1 &
api_pid=$!
for _ in $(seq 1 60); do
  curl -fsS --max-time 1 "http://127.0.0.1:${PORT}/readyz" >/dev/null 2>&1 && break
  kill -0 "$api_pid" 2>/dev/null || { cat "$WORK/api.log" >&2; exit 1; }
  sleep 0.5
done
curl -fsS --max-time 1 "http://127.0.0.1:${PORT}/readyz" >/dev/null ||
  { echo "contract-test: a API não ficou pronta" >&2; cat "$WORK/api.log" >&2; exit 1; }

user_id="$(uv run gs-admin create-user --display-name "Contrato")"
key="$(uv run gs-admin issue-key --user "$user_id" \
  --scopes files:read,files:write,runs:read,runs:write --days 1 2>/dev/null)"

uv run gs-export-openapi "$WORK/openapi.json"
# O gerador do Schemathesis não suporta `unevaluatedProperties` (corpo de POST /runs); a derivação
# reescreve só esse corpo num equivalente e falha se o formato mudar (scripts/contract_spec.py).
uv run python -m scripts.contract_spec "$WORK/openapi.json" "$WORK/openapi.contract.json"

status=0
# Recusas que o domínio impõe e o JSON Schema não expressa: scripts/contract_rules.py.
SCHEMATHESIS_HOOKS=scripts.contract_hooks uv run --group contract \
  schemathesis run "$WORK/openapi.contract.json" \
  --url "$BASE_URL" \
  --header "Authorization: Bearer $key" \
  --checks all \
  --max-examples "$MAX_EXAMPLES" \
  --seed "$SEED" \
  --request-timeout 10 ||
  status=$?

if ((status != 0)); then
  # Cada 500 deixa uma linha ERROR com o traceback; sem ela o achado não tem causa visível no CI.
  echo "--- erros no log da API ---" >&2
  grep '"level": "ERROR"' "$WORK/api.log" >&2 || echo "(nenhum)" >&2
fi
exit "$status"
