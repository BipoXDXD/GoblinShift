#!/usr/bin/env bash
# Executa o Downloads Detox de ponta a ponta pela API (critério de pronto da fatia 5,
# docs/GoblinShiftPlan.md, 1.4 e 3.9): oito entradas com uma cópia exata, Foto.png e foto.PNG
# com conteúdos diferentes, ferramenta.zip que é um JAR e pagina.txt com HTML. Espera
# `succeeded_with_warnings` (a rejeição de pagina.txt; a duplicata sozinha não gera aviso) e
# confere o ZIP com scripts/e2e_check_package.py: seis arquivos, duplicata com `duplicate_of`,
# nome desambiguado e bytes preservados. Confere também que o artefato de uma imagem tem o
# SHA-256 do upload. O mesmo pedido roda duas vezes e os dois ZIPs têm de ser idênticos.
#
# Uso: GS_API_KEY=gsk_… scripts/e2e-downloads-detox.sh [url-base]   (padrão: http://localhost)
# A chave precisa dos escopos files:* e runs:*. As fixtures saem de scripts/e2e_fixtures.py,
# geradas com o `uv` do repositório; o resto usa só curl e o python3 do host.
set -euo pipefail

# shellcheck source=scripts/e2e-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/e2e-lib.sh"
e2e_setup "${1:-}"

recipe=downloads-detox
fixtures="$WORK/fixtures"
image_output="images/ferias.jpg" # saída da entrada 0 (ferias.jpg)

echo "1. fixtures"
generate_fixtures "$recipe" "$fixtures"

echo "2. upload em lote"
upload_fixtures "$fixtures" >"$WORK/upload_ids"
command=$(command_from_expected "$fixtures" "$WORK/upload_ids")
status=$(expected_field "$fixtures" '["status"]')
image_upload_id=$(head -n 1 "$WORK/upload_ids")

for round in 1 2; do
  echo "3.$round. execução"
  run_id=$(start_run "$command" "$recipe-$round")
  echo "   acompanhamento de $run_id (esperado: $status)"
  require_status "$run_id" "$status"
  require_roles "$run_id" output report manifest package
  fetch_package "$run_id" "$recipe" "$WORK/pacote-$round.zip"
  check_package "$WORK/pacote-$round.zip" "$fixtures"

  upload_sha=$(api GET "/uploads/$image_upload_id" | json '["sha256"]')
  output_id=$(artifact_id "$run_id" output "$image_output")
  output_sha=$(artifact_sha256 "$output_id")
  [[ "$output_sha" == "$upload_sha" ]] ||
    fail "$image_output: SHA-256 do artefato ($output_sha) difere do upload ($upload_sha)"
done

echo "4. determinismo"
require_same_package "$WORK/pacote-1.zip" "$WORK/pacote-2.zip"

echo "ok: Downloads Detox de ponta a ponta em $BASE (duas runs com o mesmo ZIP)"
