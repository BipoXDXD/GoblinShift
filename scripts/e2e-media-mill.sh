#!/usr/bin/env bash
# Executa o Media Mill de ponta a ponta pela API (docs/GoblinShiftPlan.md, 1.5 e 3.9; ADR 036):
# MP4 (H.264 + AAC, com um canário nos metadados) e WebM (VP9 + Opus) viram FLAC de 16 bits e três
# miniaturas cada; uma playlist HLS com nome de MP4, um 5.1 e um MPEG-4 Part 2 são recusados. Espera
# `succeeded_with_warnings` e confere o ZIP com scripts/e2e_check_package.py: lista e ordem,
# manifesto, taxa e canais do FLAC, dimensões e ausência de EXIF das miniaturas, e o canário fora
# de toda saída. O mesmo pedido roda duas vezes e os dois ZIPs têm de ser idênticos.
#
# Uso: GS_API_KEY=gsk_… scripts/e2e-media-mill.sh [url-base]   (padrão: http://localhost)
# A chave precisa dos escopos files:* e runs:*. As fixtures saem de scripts/e2e_fixtures.py,
# geradas com o `uv` do repositório e o FFmpeg completo do PATH (com libx264, libvpx e libopus; o
# build mínimo do executor não codifica vídeo); o resto usa só curl e o python3 do host.
set -euo pipefail

# shellcheck source=scripts/e2e-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/e2e-lib.sh"
e2e_setup "${1:-}"
run_fixture_scenario media-mill "Media Mill"
