#!/usr/bin/env bash
# Marca do site a partir da rodada r1 da logo (decisão do usuário em 2026-10-15):
#   - emblema a1 (escudo da Rúbia): AVIF + WebP com 48, 96 e 144 px de altura (cabeçalho em 1×,
#     2× e 3×) e 512 px, em web/src/assets/art/marca/;
#   - selo b1 (marca secundária): AVIF + WebP com 128 e 256 px de altura, para /creditos;
#   - ícones fixos em web/public/: apple-touch-icon.png (180), icon-192.png e icon-512.png (o a1
#     sobre madeira), favicon.ico (16/32/48 do favicon.svg) e og-image.jpg (1200×630).
# O favicon.svg é desenhado à mão (obra própria) e versionado; este script só o rasteriza.
# Os PNGs intermediários ficam numa pasta temporária: os brutos continuam fora do Git.
#
# Uso: ARTE=~/Documents/GoblinShift-arte scripts/arte/gerar-marca.sh
# Requer: magick, avifenc, cwebp, pngquant e oxipng no PATH; node e o Chromium do Playwright em web/.
set -euo pipefail

ARTE="${ARTE:-$HOME/Documents/GoblinShift-arte}"
EMBLEM_HEIGHTS=(48 96 144 512)
SEAL_HEIGHTS=(128 256)
# Qualidade acima da das goblins (gerar-imagens.sh): contorno e rebites finos borram antes.
AVIF_QUALITY=66
AVIF_ALPHA_QUALITY=85
WEBP_QUALITY=80
WEBP_ALPHA_QUALITY=90
# Madeira da paleta Taverna escura (--color-wood-900), fundo dos ícones quadrados.
WOOD="#231813"
# Margem do emblema dentro dos ícones quadrados, em % do lado (zona segura do iOS e do Android).
ICON_PADDING_PERCENT=12
# Ícones em paleta de 256 cores: o de 512 px cai de ~185 KB para menos de um terço.
PNGQUANT_QUALITY=80-95
# A imagem de compartilhamento não tem transparência: JPEG pesa um quinto do PNG.
OG_JPEG_QUALITY=85

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
art="$repo/web/src/assets/art/marca"
public="$repo/web/public"
src="$ARTE/logo/r1"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

mkdir -p "$art"

# Limpeza do PNG gerado: o platô opaco vem com alfa 249 a 254 (vira 255), há pontos quase
# invisíveis espalhados pelo fundo (somem: só fica o que está a até 6 px da figura) e os pixels
# transparentes carregam RGB escuro, que vaza como halo ao reduzir (vira preto com alfa 0, e o
# redimensionamento pondera pelo alfa). Depois, recorte justo.
clean() {
  local png="$1" out="$2"
  magick "$png" -alpha extract "$work/alpha.png"
  magick "$work/alpha.png" -threshold 50% -morphology Dilate Disk:6 "$work/near.png"
  magick "$work/alpha.png" "$work/near.png" -compose Multiply -composite \
    -black-threshold 3% -white-threshold 97% "$work/alpha-clean.png"
  magick "$png" "$work/alpha-clean.png" -compose CopyOpacity -composite \
    -background black -alpha background -trim +repage "$out"
}

export_heights() {
  local png="$1" dest_prefix="$2"
  shift 2
  local height
  for height in "$@"; do
    magick "$png" -filter Lanczos -resize "x${height}" "$work/h.png"
    avifenc -q "$AVIF_QUALITY" --qalpha "$AVIF_ALPHA_QUALITY" -s 4 "$work/h.png" \
      "${dest_prefix}-${height}h.avif" >/dev/null
    cwebp -quiet -q "$WEBP_QUALITY" -alpha_q "$WEBP_ALPHA_QUALITY" -m 6 "$work/h.png" \
      -o "${dest_prefix}-${height}h.webp"
  done
}

# Emblema centrado num quadrado de madeira, com folga.
square_icon() {
  local png="$1" side="$2" out="$3"
  local inner=$((side * (100 - 2 * ICON_PADDING_PERCENT) / 100))
  magick "$png" -filter Lanczos -resize "${inner}x${inner}" -background "$WOOD" -gravity center \
    -extent "${side}x${side}" -alpha remove -alpha off "$work/icon.png"
  pngquant --quality "$PNGQUANT_QUALITY" --speed 1 --strip --force --output "$out" "$work/icon.png"
  oxipng -q -o max --strip safe "$out"
}

clean "$src/a1-escudo.png" "$work/a1.png"
clean "$src/b1-selo.png" "$work/b1.png"
export_heights "$work/a1.png" "$art/rubia-escudo" "${EMBLEM_HEIGHTS[@]}"
export_heights "$work/b1.png" "$art/selo-oficina" "${SEAL_HEIGHTS[@]}"

square_icon "$work/a1.png" 180 "$public/apple-touch-icon.png"
square_icon "$work/a1.png" 192 "$public/icon-192.png"
square_icon "$work/a1.png" 512 "$public/icon-512.png"

# favicon.svg e og-image passam pelo Chromium: o mesmo motor que vai desenhá-los na aba, e a
# Bricolage Grotesque do título vem do pacote de fontes do site.
magick "$work/b1.png" -filter Lanczos -resize x520 "$work/og-selo.png"
(cd "$repo/web" && node "$here/render-marca.mjs" "$public/favicon.svg" "$work/og-selo.png" "$work")
magick "$work/favicon-16.png" "$work/favicon-32.png" "$work/favicon-48.png" "$public/favicon.ico"
magick "$work/og-image.png" -strip -sampling-factor 4:2:0 -interlace JPEG -quality "$OG_JPEG_QUALITY" \
  "$public/og-image.jpg"

find "$art" "$public" -maxdepth 1 -type f \
  \( -name '*.avif' -o -name '*.webp' -o -name '*.png' -o -name '*.jpg' -o -name '*.ico' \) -exec wc -c {} + |
  sort -k2
