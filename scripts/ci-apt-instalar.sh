#!/usr/bin/env bash
# Instala pacotes do Ubuntu na CI sem deixar um espelho lento prender o job.
#
# Uso: ci-apt-instalar.sh <diretório-dos-debs> <pacote>...
#
# 1. Se o diretório já tem .deb (restaurado do cache do Actions), instala só deles, sem rede.
#    Instalados por caminho, eles não passam pela assinatura do InRelease: são confiados como o
#    próprio cache do Actions (escopo por ref; PR de fork não escreve no escopo da main; jobs com
#    segredo não usam cache).
# 2. Senão, baixa de um espelho por vez, com teto por tentativa: em 2026-11-05 o espelho do Azure
#    e em 2026-11-10 o archive.ubuntu.com entregaram cerca de 40 KB/s, e um único `apt-get update`
#    levou quase 6 minutos. Lentidão não é erro para o apt, então só o teto faz trocar de espelho.
#    Os .deb já baixados ficam no diretório e valem para a tentativa seguinte.
# 3. Deixa no diretório só os .deb, legíveis pelo runner, para o passo que salva o cache.
#
# CI_APT_ETC (padrão /etc/apt) e CI_APT_TENTATIVA_S existem para os testes.
set -euo pipefail

if (($# < 2)); then
  echo "uso: $0 <diretório-dos-debs> <pacote>..." >&2
  exit 2
fi
debs=$1
shift
pacotes=("$@")

etc=${CI_APT_ETC:-/etc/apt}
# Um download saudável (update e pacotes) levou de 20 s a 66 s nos runs recentes. Três tentativas
# de 150 s somam 7,5 min e cabem no teto de 10 min do passo, com folga para a instalação local.
tentativa_s=${CI_APT_TENTATIVA_S:-150}
# Espelhos oficiais do Ubuntu, todos com o mesmo caminho /ubuntu. No download o apt confere a
# assinatura do InRelease, então trocar de espelho não muda o que é instalado.
espelhos=(archive.ubuntu.com mirrors.edge.kernel.org azure.archive.ubuntu.com)
hosts_do_ubuntu='(?:azure\.archive\.ubuntu\.com|archive\.ubuntu\.com|mirrors\.edge\.kernel\.org)'
opcoes=(-q -y --no-install-recommends -o "Dir::Cache::archives=$debs")

main() {
  # Fora de /var/cache/apt o apt não cria o `partial` sozinho.
  mkdir -p "$debs/partial"
  if instalar_do_cache; then
    deixar_so_os_debs
    return
  fi
  "$(dirname "$0")/ci-apt-sem-azure.sh"
  for espelho in "${espelhos[@]}"; do
    usar_espelho "$espelho"
    if baixar; then
      sudo apt-get install "${opcoes[@]}" --no-download "${pacotes[@]}"
      deixar_so_os_debs
      return
    fi
    echo "::warning::apt: ${espelho} não entregou os pacotes em ${tentativa_s} s; tentando o próximo espelho"
  done
  echo "::error::apt: nenhum espelho entregou ${pacotes[*]} em ${tentativa_s} s por tentativa"
  return 1
}

instalar_do_cache() {
  local cache=("$debs"/*.deb)
  [[ -e ${cache[0]} ]] || return 1
  if sudo apt-get install "${opcoes[@]}" --no-download "${cache[@]}"; then
    return 0
  fi
  echo "::warning::apt: os .deb do cache não instalaram; baixando de novo"
  return 1
}

usar_espelho() {
  local arquivos
  mapfile -t arquivos < <(sudo grep -rlE '//(azure\.)?archive\.ubuntu\.com/|//mirrors\.edge\.kernel\.org/' "$etc" || true)
  if ((${#arquivos[@]} > 0)); then
    sudo perl -pi -e "s#//${hosts_do_ubuntu}/ubuntu#//$1/ubuntu#g" "${arquivos[@]}"
  fi
}

baixar() {
  local fim=$((SECONDS + tentativa_s))
  sudo timeout -k 10 "$(restante "$fim")" apt-get update -q &&
    sudo timeout -k 10 "$(restante "$fim")" apt-get install "${opcoes[@]}" --download-only "${pacotes[@]}"
}

# `timeout 0` desligaria o teto: o mínimo é 1 s.
restante() {
  local segundos=$(($1 - SECONDS))
  echo $((segundos > 0 ? segundos : 1))
}

deixar_so_os_debs() {
  sudo rm -rf "$debs/partial" "$debs/lock"
  sudo chown -R "$(id -u):$(id -g)" "$debs"
}

main
