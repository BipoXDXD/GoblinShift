#!/usr/bin/env bash
# Tira o espelho do Azure do apt do runner. Ele já caiu para cerca de 40 KB/s, o que trava o
# pacote do FFmpeg por dezenas de minutos e estoura o tempo dos jobs. Roda só na CI.
set -euo pipefail

mapfile -d '' -t arquivos < <(sudo grep -rlZ azure.archive.ubuntu.com /etc/apt || true)
if ((${#arquivos[@]} > 0)); then
  sudo sed -i 's/azure\.archive\.ubuntu\.com/archive.ubuntu.com/g' "${arquivos[@]}"
fi
