#!/usr/bin/env bash
# GLBs da cena 3D da abertura (ADR 030 e 038): lê o modelo de origem de cada goblin do elenco
# (elenco.tsv) fora do repositório e grava o otimizado em
# web/src/assets/art/oficina-3d/oficina-3d-ID.glb. Todas usam a mesma configuração da Rúbia.
#
# Passos e porquê:
#   1. weld (gltf-transform): junta os vértices iguais, para o simplify enxergar as costuras de UV.
#   2. simplify com atributos (web/scripts/simplificar-glb.ts): o meshoptimizer mede o desvio de
#      posição, normal e UV, e para onde qualquer um passaria do erro aceito. O simplify comum,
#      só de posição, achatava a bochecha, apagava a íris do olho esquerdo e esticava o UV da
#      coxa até amostrar o cabelo (ADR 030, "Defeitos da primeira versão").
#   3. texturas redimensionadas para TEXTURE_PX e gravadas em WebP (EXT_texture_webp). O fonte externa
#      entrega 2048 px; contra 1024 px, eles tiram ~40% da diferença para o bruto nos closes, por
#      ~320 KB. Com uma fonte de textura maior, compare closes contra o bruto antes de subir.
#   4. Meshopt (EXT_meshopt_compression) sobre a malha quantizada (KHR_mesh_quantization), com UV
#      em 14 bits: o padrão de 12 bits dá passos de 1 texel numa textura de 4096 px.
#
# Uso: ARTE=/caminho/arte RUBIA_SOURCE=/caminho/bruto.glb scripts/arte/otimizar-glb.sh [ID...]
#      Sem ID, otimiza todas; com IDs do elenco.tsv (como `nyx`), só essas.
#      FONTE=/caminho/bruto.glb TEXTURE_PX=4096 scripts/arte/otimizar-glb.sh ID   (um ID só)
# Requer: Node e `npm ci` em web/ (o CLI do gltf-transform vem fixado pelo npx).
set -euo pipefail

ARTE="${ARTE:-$HOME/Documents/GoblinShift-arte}"
TEXTURE_PX="${TEXTURE_PX:-2048}"
TEXCOORD_BITS=14
GLTF_TRANSFORM="@gltf-transform/cli@4.5.1"

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
out_dir="$repo/web/src/assets/art/oficina-3d"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

[[ -d "$repo/web/node_modules/meshoptimizer" ]] || { echo "rode npm ci em web/ antes" >&2; exit 1; }
if [[ -n "${FONTE:-}" && $# -ne 1 ]]; then
  echo "FONTE vale para um ID só" >&2
  exit 1
fi
mkdir -p "$out_dir"

gt() { npx --yes "$GLTF_TRANSFORM" "$@" >/dev/null; }

# selected ID [PEDIDOS...]: verdadeiro sem pedidos ou se ID está entre eles.
selected() {
  local id="$1" wanted
  shift
  (($# == 0)) && return 0
  for wanted in "$@"; do
    [[ "$wanted" == "$id" ]] && return 0
  done
  return 1
}

optimize() {
  local src="$1" out="$2"
  [[ -f "$src" ]] || { echo "GLB bruto não encontrado: $src" >&2; exit 1; }
  gt weld "$src" "$work/soldada.glb"
  node "$repo/web/scripts/simplificar-glb.ts" "$work/soldada.glb" "$work/simplificada.glb"
  gt resize "$work/simplificada.glb" "$work/redimensionada.glb" \
    --width "$TEXTURE_PX" --height "$TEXTURE_PX"
  gt webp "$work/redimensionada.glb" "$work/webp.glb"
  gt meshopt "$work/webp.glb" "$out" --quantize-texcoord "$TEXCOORD_BITS"

  printf 'bruto:      %10d bytes  %s\n' "$(wc -c <"$src")" "$src"
  printf 'otimizado:  %10d bytes  %s\n' "$(wc -c <"$out")" "$out"
  npx --yes "$GLTF_TRANSFORM" inspect "$out" | sed 's/\x1b\[[0-9;]*m//g' | grep -E '│ 0 +│ model +│ TRI|image/webp'
}

# O elenco entra pelo descritor 3, para o npx e o node não consumirem as linhas pelo stdin.
while IFS=$'\t' read -r -u 3 id glb _; do
  [[ -z "$id" || "$id" == \#* ]] && continue
  selected "$id" "$@" || continue
  source="${FONTE:-$ARTE/$glb}"
  if [[ "$id" == "rubia" ]]; then
    source="${FONTE:-${RUBIA_SOURCE:?Defina FONTE ou RUBIA_SOURCE com o caminho do GLB de origem registrado}}"
  fi
  optimize "$source" "$out_dir/oficina-3d-$id.glb"
done 3<"$here/elenco.tsv"
