#!/usr/bin/env bash
# Gera o pior caso do Media Mill v1 (ADR 036) para `deploy/acceptance/media_mill_load.py`: 5 vídeos
# de 300 s em 1920x1080 a 60 fps (H.264 + AAC estéreo 48 kHz), no teto de 1.5, com keyframe a cada
# 10 s (600 quadros), o maior intervalo que a receita processa dentro do teto de 60 s por miniatura.
# A 2 Mbit/s cada um fica perto de 75 MiB, e os cinco cabem nos 512 MiB do lote.
#
# Uso (numa máquina com o FFmpeg completo, com libx264): scripts/media-mill-worst-case.sh <dir>
# Depois: scp <dir>/*.mp4 para a VPS e rode o media_mill_load lá.
set -euo pipefail

target="${1:?uso: $0 <dir>}"
mkdir -p "$target"
for index in 1 2 3 4 5; do
  ffmpeg -nostdin -hide_banner -loglevel error -y \
    -f lavfi -i "testsrc2=size=1920x1080:rate=60:duration=300" \
    -f lavfi -i "sine=frequency=$((300 + index * 50)):sample_rate=48000:duration=300" \
    -c:v libx264 -preset veryfast -b:v 2M -maxrate 2M -bufsize 4M -g 600 -pix_fmt yuv420p \
    -c:a aac -ac 2 -shortest -f mp4 "$target/pior-caso-$index.mp4"
  echo "gerado $target/pior-caso-$index.mp4"
done
