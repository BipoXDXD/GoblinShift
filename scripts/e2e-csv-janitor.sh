#!/usr/bin/env bash
# Executa o CSV Janitor de ponta a ponta pela API, só com curl (critério de pronto da fatia 3,
# docs/GoblinShiftPlan.md, 3.9): upload em lote, envio do conteúdo, execução idempotente,
# acompanhamento, resultados e download, conferindo o conteúdo limpo byte a byte.
#
# Uso: GS_API_KEY=gsk_… scripts/e2e-csv-janitor.sh [url-base]   (padrão: http://localhost)
# A chave vem do gs-admin (README, "Chave de API") e precisa dos escopos files:* e runs:*.
# Sai com 0 só se a execução terminar em `succeeded` com a saída esperada.
set -euo pipefail

BASE="${1:-http://localhost}/api/v1"
KEY="${GS_API_KEY:?defina GS_API_KEY com uma chave emitida pelo gs-admin}"
POLL_INTERVAL_S=2
POLL_LIMIT=150 # 5 min, acima do que uma tabela pequena leva mesmo com retentativa

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# Tabela com tudo que a receita trata: BOM, delimitador ;, espaços, caixa, duplicata e
# zeros à esquerda; a saída esperada foi escrita à mão.
printf '\xef\xbb\xbfid;nome;email\r\n00123; Ana ;ANA@X.COM\r\n00456;Bruno;bruno@x.com\r\n00123;Ana;ana@x.com\r\n' \
  >"$work/contatos.csv"
printf 'id,nome,email\r\n00123,Ana,ana@x.com\r\n00456,Bruno,bruno@x.com\r\n' >"$work/esperado.csv"
size=$(wc -c <"$work/contatos.csv" | tr -d ' ')

api() {
  # Falha com o corpo problem+json visível em vez de seguir com uma resposta de erro.
  local method="$1" path="$2"
  shift 2
  curl --silent --show-error --fail-with-body --max-time 30 -X "$method" \
    -H "Authorization: Bearer $KEY" "$@" "$BASE$path"
}

json() {
  # Extrai um campo do JSON da entrada padrão: json '["items"][0]["id"]'
  python3 -c "import json, sys; print(json.load(sys.stdin)$1)"
}

echo "1. intenção de upload"
upload_id=$(api POST /uploads -H 'Content-Type: application/json' \
  --data "{\"items\": [{\"name\": \"contatos.csv\", \"size\": $size}]}" | json '["items"][0]["id"]')

echo "2. envio do conteúdo ($size bytes)"
state=$(api PUT "/uploads/$upload_id/content" -H 'Content-Type: application/octet-stream' \
  --data-binary "@$work/contatos.csv" | json '["state"]')
[[ "$state" == "ready" ]] || { echo "upload não ficou pronto: $state" >&2; exit 1; }

echo "3. execução"
command=$(
  cat <<EOF
{"recipe_id": "csv-janitor", "recipe_version": 1, "input_upload_ids": ["$upload_id"],
 "parameters": {"input_delimiter": ";", "trim_columns": ["nome"],
                "case_rules": [{"column": "email", "mode": "lower"}], "deduplicate_by": ["id"]}}
EOF
)
idem_key="e2e-$(date +%s)-$RANDOM-csv-janitor"
run_id=$(api POST /runs -H 'Content-Type: application/json' -H "Idempotency-Key: $idem_key" \
  --data "$command" | json '["id"]')
replayed=$(api POST /runs -H 'Content-Type: application/json' -H "Idempotency-Key: $idem_key" \
  --data "$command" -o /dev/null -D - | tr -d '\r' | awk -F': ' 'tolower($1)=="idempotency-replayed" {print $2}')
[[ "$replayed" == "true" ]] || { echo "repetir a mesma chave não devolveu replay" >&2; exit 1; }

echo "4. acompanhamento de $run_id"
for _ in $(seq 1 "$POLL_LIMIT"); do
  status=$(api GET "/runs/$run_id" | json '["status"]')
  case "$status" in
    queued | running | stopping) sleep "$POLL_INTERVAL_S" ;;
    *) break ;;
  esac
done
[[ "$status" == "succeeded" ]] || { echo "execução terminou em $status" >&2; api GET "/runs/$run_id" >&2; exit 1; }

echo "5. resultados e download"
artifact_id=$(api GET "/runs/$run_id/results" |
  python3 -c 'import json, sys; print(next(a["artifact_id"] for a in json.load(sys.stdin)["artifacts"] if a["role"] == "output"))')
api GET "/artifacts/$artifact_id/download" -o "$work/limpo.csv"
cmp "$work/limpo.csv" "$work/esperado.csv" || { echo "saída diferente da esperada" >&2; exit 1; }

echo "ok: CSV Janitor de ponta a ponta em $BASE (run $run_id)"
