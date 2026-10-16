#!/usr/bin/env bash
# Executa o Image Forge de ponta a ponta pela API (critério de pronto da fatia 5,
# docs/GoblinShiftPlan.md, 3.9): JPEG Display P3 com orientação EXIF 6 e GPS, MPO de 2 quadros,
# PNG RGBA, PNG de 16 bits e PNG bomba, com miniaturas. Espera `succeeded_with_warnings` com
# as rejeições UNSUPPORTED_BIT_DEPTH e IMAGE_TOO_LARGE e confere o ZIP (lista, ordem, manifesto,
# dimensões orientadas e EXIF removido) com scripts/e2e_check_package.py. O mesmo pedido roda
# duas vezes, com os mesmos uploads, e os dois ZIPs têm de ser idênticos.
#
# Uso: GS_API_KEY=gsk_… scripts/e2e-image-forge.sh [url-base]   (padrão: http://localhost)
# A chave precisa dos escopos files:* e runs:*. As fixtures saem de scripts/e2e_fixtures.py,
# geradas com o `uv` do repositório; o resto usa só curl e o python3 do host.
set -euo pipefail

# shellcheck source=scripts/e2e-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/e2e-lib.sh"
e2e_setup "${1:-}"
run_fixture_scenario image-forge "Image Forge"
