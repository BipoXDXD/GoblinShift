# shellcheck shell=bash
# Funções comuns do elenco (elenco.tsv) para otimizar-glb.sh e gerar-imagens.sh, que fazem `source`
# deste arquivo. Não roda sozinho.

# check_ids ELENCO [PEDIDOS...]: sai com 1 se algum ID pedido não estiver no elenco, antes de
# qualquer trabalho; um ID digitado errado não pode passar calado ao lado de um válido.
check_ids() {
  local elenco="$1" wanted id found
  shift
  for wanted in "$@"; do
    found=0
    while IFS=$'\t' read -r id _; do
      [[ "$id" == "$wanted" ]] && found=1 && break
    done <"$elenco"
    if ((found == 0)) || [[ "$wanted" == \#* ]]; then
      echo "ID fora do elenco.tsv: $wanted" >&2
      exit 1
    fi
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
