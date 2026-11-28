#!/usr/bin/env bash
# Snapshot de versão publicada nunca muda nem some (docs/GoblinShiftPlan.md, 1.6).
#
# `tests/catalog/test_versoes_congeladas.py` compara o catálogo com `tests/catalog/congeladas/`,
# mas não vê o histórico: apagar um snapshot e regerá-lo com o código novo passaria. Este script
# compara a pasta com a base do PR e recusa arquivo modificado, apagado ou renomeado (sem detecção
# de renomeação, o caminho antigo aparece como apagado). Arquivo novo
# (versão nova) passa.
#
# Uso: ./scripts/check-snapshots-imutaveis.sh SHA_BASE
# Sai com 1 se algum snapshot existente mudou, listando os arquivos; com 2 sem a base.
set -euo pipefail

SNAPSHOTS="tests/catalog/congeladas/"

if [[ $# -ne 1 || -z $1 ]]; then
  echo "uso: $0 SHA_BASE" >&2
  exit 2
fi
base="$1"

changed="$(git diff --name-only --no-renames --diff-filter=MD "$base" HEAD -- "$SNAPSHOTS")"

if [[ -n $changed ]]; then
  echo "FALHA: snapshot congelado mudou, sumiu ou foi renomeado (crie uma versão nova):" >&2
  printf '%s\n' "$changed" >&2
  exit 1
fi
echo "ok: nenhum snapshot existente mudou em $SNAPSHOTS"
