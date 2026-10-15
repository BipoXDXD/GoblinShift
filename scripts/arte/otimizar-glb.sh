#!/usr/bin/env bash
# GLB da cena 3D da abertura (ADR 030): lê o bruto do Rodin fora do repositório e grava o
# otimizado em web/src/assets/art/oficina-3d/. Só a Rúbia vai para o 3D.
#
# Passos e porquê:
#   1. weld (gltf-transform): junta os vértices iguais, para o simplify enxergar as costuras de UV.
#   2. simplify com atributos (web/scripts/simplificar-glb.ts): o meshoptimizer mede o desvio de
#      posição, normal e UV, e para onde qualquer um passaria do erro aceito. O simplify comum,
#      só de posição, achatava a bochecha, apagava a íris do olho esquerdo e esticava o UV da
#      coxa até amostrar o cabelo (ADR 030, "Defeitos da primeira versão").
#   3. texturas redimensionadas para TEXTURE_PX e gravadas em WebP (EXT_texture_webp). O Rodin
#      entrega 2048 px; contra 1024 px, eles tiram ~40% da diferença para o bruto nos closes, por
#      ~320 KB. Com uma fonte de textura maior, compare closes contra o bruto antes de subir.
#   4. Meshopt (EXT_meshopt_compression) sobre a malha quantizada (KHR_mesh_quantization), com UV
#      em 14 bits: o padrão de 12 bits dá passos de 1 texel numa textura de 4096 px.
#
# Uso: ARTE=~/Documents/GoblinShift-arte scripts/arte/otimizar-glb.sh
#      FONTE=/caminho/bruto.glb TEXTURE_PX=4096 scripts/arte/otimizar-glb.sh
# Requer: Node e `npm ci` em web/ (o CLI do gltf-transform vem fixado pelo npx).
set -euo pipefail

ARTE="${ARTE:-$HOME/Documents/GoblinShift-arte}"
TEXTURE_PX="${TEXTURE_PX:-2048}"
TEXCOORD_BITS=14
GLTF_TRANSFORM="@gltf-transform/cli@4.5.1"

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
out_dir="$repo/web/src/assets/art/oficina-3d"
src="${FONTE:-$ARTE/$(awk -F'\t' '$1 == "rubia" { print $2 }' "$here/elenco.tsv")}"
out="$out_dir/oficina-3d-rubia.glb"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

[[ -f "$src" ]] || { echo "GLB bruto não encontrado: $src" >&2; exit 1; }
[[ -d "$repo/web/node_modules/meshoptimizer" ]] || { echo "rode npm ci em web/ antes" >&2; exit 1; }
mkdir -p "$out_dir"

gt() { npx --yes "$GLTF_TRANSFORM" "$@" >/dev/null; }

gt weld "$src" "$work/soldada.glb"
node "$repo/web/scripts/simplificar-glb.ts" "$work/soldada.glb" "$work/simplificada.glb"
gt resize "$work/simplificada.glb" "$work/redimensionada.glb" \
  --width "$TEXTURE_PX" --height "$TEXTURE_PX"
gt webp "$work/redimensionada.glb" "$work/webp.glb"
gt meshopt "$work/webp.glb" "$out" --quantize-texcoord "$TEXCOORD_BITS"

printf 'bruto:      %10d bytes  %s\n' "$(wc -c <"$src")" "$src"
printf 'otimizado:  %10d bytes  %s\n' "$(wc -c <"$out")" "$out"
npx --yes "$GLTF_TRANSFORM" inspect "$out" | sed 's/\x1b\[[0-9;]*m//g' | grep -E '│ 0 +│ model +│ TRI|image/webp'
