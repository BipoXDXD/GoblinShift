#!/usr/bin/env bash
# Executa o PDF Press de ponta a ponta pela API (docs/GoblinShiftPlan.md, 1.5 e 3.9; ADR 035):
# dois PDFs sintéticos separados em partes de 2 páginas. Espera `succeeded` e confere o ZIP com
# scripts/e2e_check_package.py: mapa de páginas, três partes, manifesto e relatório, na ordem. O
# mesmo pedido roda duas vezes e os dois ZIPs têm de ser idênticos.
#
# Uso: GS_API_KEY=gsk_… scripts/e2e-pdf-press.sh [url-base]   (padrão: http://localhost)
# A chave precisa dos escopos files:* e runs:*. As fixtures saem de scripts/e2e_fixtures.py,
# geradas com o `uv` do repositório; o resto usa só curl e o python3 do host.
set -euo pipefail

# shellcheck source=scripts/e2e-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/e2e-lib.sh"
e2e_setup "${1:-}"

recipe=pdf-press
fixtures="$WORK/fixtures"

echo "1. fixtures"
generate_fixtures "$recipe" "$fixtures"

echo "2. upload em lote"
upload_fixtures "$fixtures" >"$WORK/upload_ids"
command=$(command_from_expected "$fixtures" "$WORK/upload_ids")
status=$(expected_field "$fixtures" '["status"]')

for round in 1 2; do
  echo "3.$round. execução"
  run_id=$(start_run "$command" "$recipe-$round")
  echo "   acompanhamento de $run_id (esperado: $status)"
  require_status "$run_id" "$status"
  require_roles "$run_id" output report manifest package
  fetch_package "$run_id" "$recipe" "$WORK/pacote-$round.zip"
  check_package "$WORK/pacote-$round.zip" "$fixtures"
done

echo "4. determinismo"
require_same_package "$WORK/pacote-1.zip" "$WORK/pacote-2.zip"

echo "ok: PDF Press de ponta a ponta em $BASE (duas runs com o mesmo ZIP)"
