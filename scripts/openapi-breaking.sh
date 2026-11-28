#!/usr/bin/env bash
# Barra mudança incompatível em /api/v1 (job "Contrato" do CI; ADR 061).
#
# Uso: scripts/openapi-breaking.sh BASE_SPEC HEAD_SPEC [ACEITAS]
#
# Roda `oasdiff breaking` da imagem fixada abaixo entre a spec anterior (BASE_SPEC) e a nova
# (HEAD_SPEC), só nos caminhos de /api/v1. Sai com o código do oasdiff (1 quando há quebra de
# nível ERR) e imprime o relatório. ACEITAS (padrão docs/api/quebras-aceitas.txt) é o arquivo do
# --err-ignore: cada entrada vem logo abaixo do ADR que justifica a quebra.
#
# A spec nova vem do PR, então o contêiner roda sem rede e sem $ref externo, e os arquivos entram
# só para leitura.
set -euo pipefail

OASDIFF_IMAGE="tufin/oasdiff:v1.37.0@sha256:ff02af37a254b5199b9d72e7bba93dae5837a9119d59459168d1398893b47aea"

# Níveis próprios, no formato do --severity-levels (sem comentários: o oasdiff os recusa).
# - O plano (1.6) manda o cliente tolerar valor novo em enum de saída, e o padrão da v1.37.0 é
#   ERR. Com WARN, o valor novo aparece no relatório sem reprovar o job.
# - Tirar da resposta um campo opcional não é aditivo, e o padrão da v1.37.0 é INFO. Com ERR, a
#   remoção reprova como a de um campo obrigatório.
NIVEIS="response-property-enum-value-added WARN
response-optional-property-removed ERR"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "uso: $0 BASE_SPEC HEAD_SPEC [ACEITAS]" >&2
  exit 2
fi

base="$1"
head="$2"
aceitas="${3:-${REPO_ROOT}/docs/api/quebras-aceitas.txt}"

for arquivo in "$base" "$head" "$aceitas"; do
  if [[ ! -f "$arquivo" ]]; then
    echo "arquivo não encontrado: $arquivo" >&2
    exit 2
  fi
done

absoluto() {
  echo "$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
}

niveis="$(mktemp)"
trap 'rm -f "$niveis"' EXIT
echo "$NIVEIS" >"$niveis"

docker run --rm --network none \
  --mount "type=bind,src=$(absoluto "$base"),dst=/specs/base.json,readonly" \
  --mount "type=bind,src=$(absoluto "$head"),dst=/specs/head.json,readonly" \
  --mount "type=bind,src=$(absoluto "$aceitas"),dst=/specs/quebras-aceitas.txt,readonly" \
  --mount "type=bind,src=${niveis},dst=/specs/niveis.txt,readonly" \
  "$OASDIFF_IMAGE" \
  breaking /specs/base.json /specs/head.json \
  --fail-on ERR \
  --match-path '^/api/v1/' \
  --err-ignore /specs/quebras-aceitas.txt \
  --severity-levels /specs/niveis.txt \
  --allow-external-refs=false \
  --color never
