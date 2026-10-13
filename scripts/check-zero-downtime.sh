#!/usr/bin/env bash
# Prova local de que deploy/rollout-api.sh troca a API sem queda (docs/GoblinShiftPlan.md, 3.8).
#
# Pré-requisito: a pilha local de pé (docker compose up --build -d). Dispara requisições
# contínuas a /readyz pelo Nginx durante o rollout e sai com 1 se alguma não for 200.
#
# Uso: ./scripts/check-zero-downtime.sh [rodadas]   (padrão: 2, para trocar a API duas vezes)
set -euo pipefail

ROUNDS="${1:-2}"
URL="http://127.0.0.1:${GS_HTTP_PORT:-80}/readyz"
codes="$(mktemp)"
trap 'rm -f "$codes"' EXIT

curl -fsS --max-time 2 "$URL" >/dev/null || { echo "a pilha local não responde em $URL" >&2; exit 1; }

# Sem --fail: o código HTTP (000 se a conexão falhar) é justamente o que se conta, e uma falha
# de conexão não pode encerrar o laço (set -e).
(while true; do curl -s -o /dev/null -w '%{http_code}\n' --max-time 2 "$URL" >>"$codes" || true; done) &
load=$!
for _ in $(seq 1 "$ROUNDS"); do
  deploy/rollout-api.sh docker compose
done
sleep 2
kill "$load"
wait "$load" 2>/dev/null || true

total=$(wc -l <"$codes" | tr -d ' ')
failures=$(grep -vc '^200$' "$codes" || true)
echo "requisições durante o rollout: $total; respostas fora de 200: $failures"
if ((failures > 0)); then
  sort "$codes" | uniq -c >&2
  exit 1
fi
