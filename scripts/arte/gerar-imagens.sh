#!/usr/bin/env bash
# Imagens 2D das goblins para o site (ADR 030): render de cada GLB bruto no Blender, recorte em
# 4:5 com fundo transparente e AVIF + WebP nas larguras que a interface pede.
#   - estados (vazio, carregando, erro, sucesso): 128 e 256 px de largura (1× e 2× da caixa de
#     128×160 px CSS), em web/src/assets/art/goblins/;
#   - imagem estática da cena 3D da Rúbia (placeholder e fallback): 192, 384 e 640 px, em
#     web/src/assets/art/oficina-3d/, com o prefixo que o check-budget reconhece.
# Os PNGs intermediários ficam numa pasta temporária: os brutos e renders continuam fora do Git.
#
# Uso: ARTE=~/Documents/GoblinShift-arte scripts/arte/gerar-imagens.sh [ID...]
#      Sem ID, gera todas; com IDs do elenco.tsv (como `rubia`), só essas.
# Requer: blender 5.x, magick, avifenc e cwebp no PATH, e $ARTE/ferramentas/render_glb.py.
set -euo pipefail

ARTE="${ARTE:-$HOME/Documents/GoblinShift-arte}"
RENDER_PX=1400
YAW_DEGREES=20
STATE_WIDTHS=(128 256)
POSTER_WIDTHS=(192 384 640)
AVIF_QUALITY=60
AVIF_ALPHA_QUALITY=80
WEBP_QUALITY=80
WEBP_ALPHA_QUALITY=90

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
art="$repo/web/src/assets/art"
render="$ARTE/ferramentas/render_glb.py"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

[[ -f "$render" ]] || { echo "render_glb.py não encontrado: $render" >&2; exit 1; }
mkdir -p "$art/goblins" "$art/oficina-3d"

# Render transparente → recorte justo → caixa 4:5 com 4% de folga, centrada embaixo (os pés
# ficam no mesmo lugar em todas as goblins).
render_framed() {
  local glb="$1" png="$2"
  shift 2
  blender -b --factory-startup -P "$render" -- "$glb" "$work/render.png" "$RENDER_PX" \
    "--yaw=$YAW_DEGREES" "$@" >/dev/null 2>&1
  magick "$work/render.png" -trim +repage "$work/trim.png"
  local w h side_h side_w
  read -r w h < <(magick identify -format '%w %h\n' "$work/trim.png")
  side_h=$(((h * 104) / 100))
  side_w=$(((side_h * 4) / 5))
  if ((side_w < w)); then
    side_w=$(((w * 104) / 100))
    side_h=$(((side_w * 5) / 4))
  fi
  magick "$work/trim.png" -background none -gravity south -extent "${side_w}x${side_h}" "$png"
}

export_sizes() {
  local png="$1" dest_prefix="$2"
  shift 2
  local width
  for width in "$@"; do
    magick "$png" -filter Lanczos -resize "${width}x" "$work/w.png"
    avifenc -q "$AVIF_QUALITY" --qalpha "$AVIF_ALPHA_QUALITY" -s 4 "$work/w.png" \
      "${dest_prefix}-${width}w.avif" >/dev/null
    cwebp -quiet -q "$WEBP_QUALITY" -alpha_q "$WEBP_ALPHA_QUALITY" -m 6 "$work/w.png" \
      -o "${dest_prefix}-${width}w.webp"
  done
}

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

while IFS=$'\t' read -r id glb _; do
  [[ -z "$id" || "$id" == \#* ]] && continue
  selected "$id" "$@" || continue
  if [[ "$id" == "rubia" ]]; then
    # A imagem estática da cena usa a luz realista (Cycles, três luzes, tom Standard), a mesma que
    # a cena do three imita (ADR 030). As goblins dos estados ficam no modo padrão (EEVEE, tom AgX,
    # um sol), o dos renders que o usuário aprovou para elas.
    render_framed "$ARTE/$glb" "$work/$id.png" --realista
    export_sizes "$work/$id.png" "$art/oficina-3d/oficina-3d-rubia" "${POSTER_WIDTHS[@]}"
  else
    render_framed "$ARTE/$glb" "$work/$id.png"
    export_sizes "$work/$id.png" "$art/goblins/$id" "${STATE_WIDTHS[@]}"
  fi
  echo "$id: ok"
done <"$here/elenco.tsv"

find "$art/goblins" "$art/oficina-3d" -type f \( -name '*.avif' -o -name '*.webp' \) \
  -exec wc -c {} + | sort -k2
