#!/usr/bin/env bash
# Executa o CSV Janitor de ponta a ponta pela API, só com curl (critério de pronto das fatias 3 e
# 5, docs/GoblinShiftPlan.md, 3.9): upload, execução idempotente, acompanhamento, resultados
# (com `manifest` e `package`) e download, conferindo o conteúdo limpo byte a byte. O mesmo
# pedido roda duas vezes, com chaves diferentes e os mesmos uploads, e os dois ZIPs têm de ser
# idênticos.
#
# Uso: GS_API_KEY=gsk_… scripts/e2e-csv-janitor.sh [url-base]   (padrão: http://localhost)
# A chave vem do gs-admin (README, "Chave de API") e precisa dos escopos files:* e runs:*.
# Sai com 0 só se as duas execuções terminarem em `succeeded` com a saída esperada.
set -euo pipefail

# shellcheck source=scripts/e2e-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/e2e-lib.sh"
e2e_setup "${1:-}"

# Tabela com tudo que a receita trata: BOM, delimitador ;, espaços, caixa, duplicata e
# zeros à esquerda; a saída esperada foi escrita à mão.
printf '\xef\xbb\xbfid;nome;email\r\n00123; Ana ;ANA@X.COM\r\n00456;Bruno;bruno@x.com\r\n00123;Ana;ana@x.com\r\n' \
  >"$WORK/contatos.csv"
printf 'id,nome,email\r\n00123,Ana,ana@x.com\r\n00456,Bruno,bruno@x.com\r\n' >"$WORK/esperado.csv"

echo "1. upload"
upload_id=$(upload_files "$WORK/contatos.csv")

command=$(
  cat <<EOF
{"recipe_id": "csv-janitor", "recipe_version": 1, "input_upload_ids": ["$upload_id"],
 "parameters": {"input_delimiter": ";", "trim_columns": ["nome"],
                "case_rules": [{"column": "email", "mode": "lower"}], "deduplicate_by": ["id"]}}
EOF
)

for round in 1 2; do
  echo "2.$round. execução"
  run_id=$(start_run "$command" "csv-janitor-$round")
  echo "   acompanhamento de $run_id"
  require_status "$run_id" succeeded
  require_roles "$run_id" output report manifest package
  output_id=$(artifact_id "$run_id" output)
  download "$output_id" "$WORK/limpo-$round.csv"
  cmp "$WORK/limpo-$round.csv" "$WORK/esperado.csv" || fail "saída diferente da esperada"
  fetch_package "$run_id" csv-janitor "$WORK/pacote-$round.zip"
done

echo "3. determinismo"
require_same_package "$WORK/pacote-1.zip" "$WORK/pacote-2.zip"

echo "ok: CSV Janitor de ponta a ponta em $BASE (runs com o mesmo ZIP)"
