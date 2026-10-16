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
run_fixture_scenario pdf-press "PDF Press"
