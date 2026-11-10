#!/usr/bin/env bash
# Tira o espelho do Azure do apt do runner. Ele já caiu para cerca de 40 KB/s, o que trava o
# pacote do FFmpeg por dezenas de minutos e estoura o tempo dos jobs. Roda só na CI.
# CI_APT_ETC troca o /etc/apt só nos testes.
set -euo pipefail

etc=${CI_APT_ETC:-/etc/apt}

# Traduções não servem à CI e eram cerca de 2 MB de cada `apt-get update`. Um pedido sem nenhum
# byte por 20 s é abandonado, e o apt passa ao próximo espelho da lista do runner.
printf '%s\n' \
  'Acquire::Languages "none";' \
  'Acquire::http::Timeout "20";' \
  'Acquire::https::Timeout "20";' \
  'Acquire::Retries "3";' |
  sudo tee "$etc/apt.conf.d/99ci-rede" >/dev/null

# O grep sai com 1 quando nenhum arquivo cita o espelho, e aí não há o que trocar.
mapfile -t arquivos < <(sudo grep -rl azure.archive.ubuntu.com "$etc" || true)
if ((${#arquivos[@]} > 0)); then
  sudo perl -pi -e 's#//azure\.archive\.ubuntu\.com/#//archive.ubuntu.com/#g' "${arquivos[@]}"
fi
