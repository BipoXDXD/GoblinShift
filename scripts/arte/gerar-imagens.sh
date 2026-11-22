#!/usr/bin/env bash
# Imagens 2D das goblins para o site (ADR 030): render de cada GLB do elenco no Blender
# (scripts/arte/render_glb.py), recorte em 4:5 com fundo transparente e AVIF + WebP nas larguras
# que a interface pede.
#   - estados (vazio, carregando, erro, sucesso) e elenco dos créditos: 256, 384, 512, 768, 1024 e
#     1280 px de largura (telas de 1× a 3×), em web/src/assets/art/goblins/;
#   - imagem estática da cena 3D de cada goblin (placeholder, miniatura do seletor e fallback;
#     ADR 038): 192, 384, 640 e 960 px, em web/src/assets/art/oficina-3d/, com o prefixo que o
#     check-budget reconhece, e com a luz realista que a cena imita. A Rúbia sai de um render só:
#     a mesma imagem vira a estática da cena e a goblin dos estados.
# A fonte de cada goblin é o GLB aprovado que o elenco.tsv aponta (desde 22/11, o <id>-site.glb do
# Hi3D Quality, com a textura de cor em 4096 px). Se o recorte sair mais estreito que a maior
# largura pedida, o script para antes de gravar qualquer arquivo daquela goblin, em vez de ampliar.
# Os PNGs intermediários ficam numa pasta temporária: as fontes e os renders ficam fora do Git.
#
# Uso: ARTE=/caminho/modelos scripts/arte/gerar-imagens.sh [ID...]
#      Sem ID, gera todas; com IDs do elenco.tsv (como `rubia`), só essas.
#      FONTE=/caminho/modelo.glb scripts/arte/gerar-imagens.sh ID   (um ID só, outra fonte)
#      ALVO=cena gera só as imagens da cena 3D; ALVO=estados, só as dos estados (padrão: as duas).
#      Gerar de novo muda os bytes: atualize o sha256 no PROVENANCE.json.
# Requer: blender 5.x, magick, avifenc e cwebp no PATH.
set -euo pipefail

ARTE="${ARTE:-$HOME/Documents/Modelos 3D/GoblinShift}"
ALVO="${ALVO:-tudo}"
# 2400 px no quadrado dão ~1700 px de altura à goblin e ~1360 px de largura ao recorte 4:5.
RENDER_PX=2400
YAW_DEGREES=20
STATE_WIDTHS=(256 384 512 768 1024 1280)
POSTER_WIDTHS=(192 384 640 960)
# A última largura de cada lista é a maior (sem índice negativo: o bash do macOS é o 3.2).
STATE_WIDEST=${STATE_WIDTHS[${#STATE_WIDTHS[@]} - 1]}
POSTER_WIDEST=${POSTER_WIDTHS[${#POSTER_WIDTHS[@]} - 1]}
AVIF_QUALITY=60
AVIF_ALPHA_QUALITY=80
WEBP_QUALITY=80
WEBP_ALPHA_QUALITY=90

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
art="$repo/web/src/assets/art"
render="$here/render_glb.py"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

[[ -f "$render" ]] || { echo "render_glb.py não encontrado: $render" >&2; exit 1; }
case "$ALVO" in
  tudo | cena | estados) ;;
  *) echo "ALVO deve ser tudo, cena ou estados: $ALVO" >&2; exit 1 ;;
esac
if [[ -n "${FONTE:-}" && $# -ne 1 ]]; then
  echo "FONTE vale para um ID só" >&2
  exit 1
fi
mkdir -p "$art/goblins" "$art/oficina-3d"

# Render transparente → recorte justo → caixa 4:5 com 4% de folga, centrada embaixo (os pés
# ficam no mesmo lugar em todas as goblins).
render_framed() {
  local glb="$1" png="$2"
  shift 2
  # Sem --python-exit-code, o Blender sai com 0 mesmo se o script quebrar; o rm evita recortar o
  # render da goblin anterior.
  rm -f "$work/render.png"
  blender -b --factory-startup --python-exit-code 1 -P "$render" -- "$glb" "$work/render.png" \
    "$RENDER_PX" "--yaw=$YAW_DEGREES" "$@" >/dev/null 2>&1
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

wants() { [[ "$ALVO" == "tudo" || "$ALVO" == "$1" ]]; }

# wide_enough PNG LARGURA ID: o recorte precisa cobrir a maior largura pedida sem ampliar.
wide_enough() {
  local png="$1" widest="$2" id="$3" width
  width=$(magick identify -format '%w' "$png")
  if ((width < widest)); then
    echo "fonte pequena demais: $width px para ${widest}w ($id)" >&2
    exit 1
  fi
}

# O elenco entra pelo descritor 3, para o blender e o magick não consumirem as linhas pelo stdin.
while IFS=$'\t' read -r -u 3 id glb _; do
  [[ -z "$id" || "$id" == \#* ]] && continue
  selected "$id" "$@" || continue
  source="${FONTE:-$ARTE/$glb}"
  [[ -f "$source" ]] || { echo "GLB de origem não encontrado: $source ($id)" >&2; exit 1; }
  # A imagem da cena usa a luz realista (Cycles, três luzes, tom Standard), a mesma que a cena do
  # three imita (ADR 030). As goblins dos estados ficam no modo padrão (EEVEE, tom AgX, um sol), o
  # dos renders que o usuário aprovou para elas; a Rúbia é a exceção, com o render realista nos
  # dois lugares. Todos os renders e conferências vêm antes de gravar o primeiro arquivo.
  if wants cena || [[ "$id" == "rubia" ]]; then
    render_framed "$source" "$work/$id-realista.png" --realista
  fi
  if wants cena; then
    wide_enough "$work/$id-realista.png" "$POSTER_WIDEST" "$id"
  fi
  if wants estados; then
    if [[ "$id" == "rubia" ]]; then
      cp "$work/$id-realista.png" "$work/$id.png"
    else
      render_framed "$source" "$work/$id.png"
    fi
    wide_enough "$work/$id.png" "$STATE_WIDEST" "$id"
  fi
  if wants cena; then
    export_sizes "$work/$id-realista.png" "$art/oficina-3d/oficina-3d-$id" "${POSTER_WIDTHS[@]}"
  fi
  if wants estados; then
    export_sizes "$work/$id.png" "$art/goblins/$id" "${STATE_WIDTHS[@]}"
  fi
  echo "$id: ok"
done 3<"$here/elenco.tsv"

find "$art/goblins" "$art/oficina-3d" -type f \( -name '*.avif' -o -name '*.webp' \) \
  -exec wc -c {} + | sort -k2
