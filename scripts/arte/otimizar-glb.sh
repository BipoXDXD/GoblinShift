#!/usr/bin/env bash
# GLB da cena 3D da abertura (ADR 030): lê o bruto do Rodin fora do repositório e grava o
# otimizado em web/src/assets/art/oficina-3d/. Só a Rúbia vai para o 3D.
#
# Passos e porquê:
#   1. weld + simplify (meshoptimizer, erro de até 1% do raio): 500 mil → ~120 mil triângulos.
#      As UVs do Rodin são picotadas em ilhas minúsculas e o simplify não atravessa as costuras,
#      por isso para aí. Ir além (Decimate do Blender, ou reassar a textura numa malha nova)
#      deixou pintas brancas e pretas no cabelo e na pele: comparação no ADR 030.
#   2. texturas de 1024 px em WebP (EXT_texture_webp): a cena aparece com no máximo 400 px CSS
#      de altura; 2048 px custariam +350 KB sem diferença visível.
#   3. Meshopt (EXT_meshopt_compression) sobre a malha quantizada (KHR_mesh_quantization): leva
#      a geometria de ~3,8 MB a ~1,8 MB. O decoder é WASM embutido no chunk do three, servido da
#      própria origem; a CSP ganha só 'wasm-unsafe-eval' (ADR 030). Sem Draco nem KTX2, que
#      pediriam Worker de blob:.
#
# Uso: ARTE=~/Documents/GoblinShift-arte scripts/arte/otimizar-glb.sh
# Requer: Node (o CLI do gltf-transform vem fixado pelo npx).
set -euo pipefail

ARTE="${ARTE:-$HOME/Documents/GoblinShift-arte}"
SIMPLIFY_RATIO=0.06
SIMPLIFY_ERROR=0.01
TEXTURE_PX=1024
GLTF_TRANSFORM="@gltf-transform/cli@4.5.1"

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
out_dir="$repo/web/src/assets/art/oficina-3d"
src="$ARTE/aprovados-2026-10-15/$(awk -F'\t' '$1 == "rubia" { print $2 }' "$here/elenco.tsv")"
out="$out_dir/oficina-3d-rubia.glb"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

[[ -f "$src" ]] || { echo "GLB bruto não encontrado: $src" >&2; exit 1; }
mkdir -p "$out_dir"

gt() { npx --yes "$GLTF_TRANSFORM" "$@" >/dev/null; }

gt weld "$src" "$work/soldada.glb"
# Os outros passos do optimize ficam desligados: a malha é uma só e não tem instâncias.
# O optimize já faz o prune; um prune depois regravaria o arquivo sem a compressão Meshopt.
gt optimize "$work/soldada.glb" "$out" \
  --compress meshopt \
  --texture-compress webp --texture-size "$TEXTURE_PX" \
  --simplify-ratio "$SIMPLIFY_RATIO" --simplify-error "$SIMPLIFY_ERROR" \
  --flatten false --join false --instance false --palette false

printf 'bruto:      %10d bytes  %s\n' "$(wc -c <"$src")" "$src"
printf 'otimizado:  %10d bytes  %s\n' "$(wc -c <"$out")" "$out"
npx --yes "$GLTF_TRANSFORM" inspect "$out" | sed 's/\x1b\[[0-9;]*m//g' | grep -E '│ 0 +│ model +│ TRI'
