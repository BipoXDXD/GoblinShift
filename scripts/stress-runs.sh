#!/usr/bin/env bash
# Estresse dos testes de execução: N suítes `tests/runs` em paralelo (plano da fatia 8, 5.3-4).
#
# É a condição em que os testes do worker como processo e do Runner local já falharam (PR #19):
# máquina carregada, várias sessões disputando CPU, cada uma com o próprio PostgreSQL de teste.
# Use depois de mexer no worker, na manutenção ou no runner, ou para reproduzir uma falha rara.
# O `-p no:cacheprovider` impede as rodadas de disputarem o .pytest_cache.
#
# Uso: ./scripts/stress-runs.sh [suítes]   (padrão: 5; precisa de Docker, como `-m integration`)
set -euo pipefail

ROUNDS="${1-5}"
[[ "$ROUNDS" =~ ^[1-9][0-9]*$ ]] || {
  echo "uso: $0 [suítes], com suítes inteiro positivo" >&2
  exit 2
}
logs="$(mktemp -d)"

pids=()
for round in $(seq 1 "$ROUNDS"); do
  uv run pytest tests/runs -q -p no:cacheprovider >"$logs/rodada-$round.log" 2>&1 &
  pids+=("$!")
done

passed=0
failed=()
for index in "${!pids[@]}"; do
  if wait "${pids[$index]}"; then
    passed=$((passed + 1))
  else
    failed+=("$((index + 1))")
  fi
done

echo "$passed de $ROUNDS suítes verdes"
if ((${#failed[@]})); then
  for round in "${failed[@]}"; do
    echo "rodada $round: $logs/rodada-$round.log"
  done
  exit 1
fi
rm -rf "$logs"
