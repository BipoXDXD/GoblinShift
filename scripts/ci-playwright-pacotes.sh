#!/usr/bin/env bash
# Lista, um por linha, os pacotes do apt que faltam ao Playwright para os navegadores pedidos, sem
# instalar nada. O `install --with-deps` dele faz `apt-get update` e `install` sem teto nenhum; na
# CI quem instala é o scripts/ci-apt-instalar.sh, e o navegador entra depois, sem `--with-deps`.
# Roda onde o `npx playwright` do projeto resolve (web/). Sem faltas, não imprime nada.
#
# Na 1.64.1 o `install-deps --dry-run` simula `apt-get install -s` e imprime uma de duas saídas:
#   "All system dependencies are installed."                      e sai com 0, ou
#   "Missing system dependencies (N):" seguido de N linhas "  pacote", e sai com 1.
# Qualquer outra coisa (erro, formato novo) falha aqui, em vez de instalar uma lista errada.
#
# Uso: ci-playwright-pacotes.sh <navegador>...
set -euo pipefail

if (($# == 0)); then
  echo "uso: $0 <navegador>..." >&2
  exit 2
fi

status=0
saida=$(npx playwright install-deps --dry-run "$@") || status=$?

falhar() {
  echo "saída inesperada de 'playwright install-deps --dry-run' (status ${status}): $1" >&2
  echo "$saida" >&2
  exit 1
}

if ((status == 0)) && [[ $saida == "All system dependencies are installed." ]]; then
  exit 0
fi
((status == 1)) || falhar "status diferente de 0 e 1"

mapfile -t linhas <<<"$saida"
[[ ${linhas[0]} =~ ^Missing\ system\ dependencies\ \(([0-9]+)\):$ ]] || falhar "sem o cabeçalho"
quantidade=${BASH_REMATCH[1]}
pacotes=("${linhas[@]:1}")
((${#pacotes[@]} == quantidade)) || falhar "o cabeçalho diz ${quantidade} pacotes"
for linha in "${pacotes[@]}"; do
  [[ $linha =~ ^\ \ ([^[:space:]]+)$ ]] || falhar "linha fora do formato: '${linha}'"
done
for linha in "${pacotes[@]}"; do
  echo "${linha#  }"
done
