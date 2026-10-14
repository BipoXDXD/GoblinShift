#!/usr/bin/env bash
# Mede o pior caso do Image Forge v1 pela API (plano da fatia 5, item 9; ADR 018): 100 JPEGs de
# 24 MP em Display P3 com orientação 6 e miniaturas, numa tentativa só, sob o executor de
# produção (gVisor, 1 vCPU). Passa só se a execução terminar em `succeeded` na primeira tentativa,
# o que prova que coube nos 10 min do teto; imprime o tempo da fase de execução para o ADR 018.
#
# Uso: GS_API_KEY=gsk_… scripts/measure-image-forge.sh [url-base]   (padrão: http://localhost)
# Gera ~430 MiB de fixtures no diretório temporário e os envia num lote só.
set -euo pipefail

# A tentativa pode levar até 10 min, além da fila: espera até 15 min antes de desistir.
POLL_LIMIT="${POLL_LIMIT:-450}"
# shellcheck source=scripts/e2e-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/e2e-lib.sh"
e2e_setup "${1:-}"

fixtures="$WORK/fixtures"

echo "1. fixtures (100 JPEGs de 24 MP)"
generate_fixtures image-forge-carga "$fixtures"

echo "2. upload em lote"
upload_fixtures "$fixtures" >"$WORK/upload_ids"
command=$(command_from_expected "$fixtures" "$WORK/upload_ids")

echo "3. execução"
run_id=$(start_run "$command" image-forge-carga)
started_at=""
status=""
for _ in $(seq 1 "$POLL_LIMIT"); do
  status=$(api GET "/runs/$run_id" | json '["status"]')
  case "$status" in
    queued) ;;
    running | stopping) [[ -n "$started_at" ]] || started_at=$(date +%s) ;;
    *) break ;;
  esac
  sleep "$POLL_INTERVAL_S"
done
finished_at=$(date +%s)
run=$(api GET "/runs/$run_id")
attempt=$(json '["attempt"]' <<<"$run")
[[ "$status" == "succeeded" ]] || fail "execução $run_id terminou em $status: $run"
[[ "$attempt" == "1" ]] || fail "a execução precisou de $attempt tentativas; a medição vale só para uma"

echo "4. pacote"
require_roles "$run_id" output report manifest package
fetch_package "$run_id" image-forge "$WORK/pacote.zip"
check_package "$WORK/pacote.zip" "$fixtures"

elapsed=$((finished_at - ${started_at:-$finished_at}))
echo "ok: 100 imagens de 24 MP em ~${elapsed} s de execução (run $run_id; teto da tentativa: 600 s)"
