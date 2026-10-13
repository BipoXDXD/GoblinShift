#!/usr/bin/env bash
# Troca os containers da API sem queda (docs/GoblinShiftPlan.md, 3.8).
#
# Sobe containers novos ao lado dos atuais, espera o healthcheck deles, dá tempo ao Nginx de
# enxergá-los pelo DNS do Docker e só então para os antigos com SIGTERM. Se algo falhar antes
# disso, os antigos continuam atendendo. Durante a troca, duas versões da API convivem por alguns
# segundos; o expand/contract das migrações já conta com isso.
#
# Uso: deploy/rollout-api.sh <comando compose…>
#   ex.: deploy/rollout-api.sh docker compose -f compose.yaml -f compose.prod.yaml
set -euo pipefail

(($# > 0)) || { echo "uso: rollout-api.sh <comando compose…>" >&2; exit 64; }
compose=("$@")
SERVICE=api
# Maior que o `valid` do resolver no template do Nginx (5 s): depois disso, o cache dele já
# contém os containers novos.
DNS_SETTLE_S=7
# Prazo para a API velha terminar as requisições em andamento depois do SIGTERM.
STOP_GRACE_S=30
WAIT_TIMEOUT_S=120

mapfile -t old < <("${compose[@]}" ps --quiet "$SERVICE")
if ((${#old[@]} == 0)); then
  echo "rollout: nenhuma $SERVICE rodando; subindo a primeira"
  "${compose[@]}" up -d --no-deps --no-build --wait --wait-timeout "$WAIT_TIMEOUT_S" "$SERVICE"
  exit 0
fi

# --no-recreate mantém os containers atuais; o --scale cria os novos com a configuração atual.
"${compose[@]}" up -d --no-deps --no-build --no-recreate \
  --scale "$SERVICE=$((${#old[@]} * 2))" --wait --wait-timeout "$WAIT_TIMEOUT_S" "$SERVICE"

mapfile -t current < <("${compose[@]}" ps --quiet "$SERVICE")
new=()
for id in "${current[@]}"; do
  [[ " ${old[*]} " == *" $id "* ]] || new+=("$id")
done
((${#new[@]} > 0)) || { echo "rollout: nenhum container novo; os atuais foram mantidos" >&2; exit 1; }

echo "rollout: ${#new[@]} novo(s) saudável(is); aguardando ${DNS_SETTLE_S}s pelo DNS do Nginx"
sleep "$DNS_SETTLE_S"
docker stop --time "$STOP_GRACE_S" "${old[@]}" >/dev/null
docker rm "${old[@]}" >/dev/null
echo "rollout: ${#old[@]} container(s) antigo(s) parado(s) e removido(s)"
