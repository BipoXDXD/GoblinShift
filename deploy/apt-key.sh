# shellcheck shell=bash
# verify_apt_key <arquivo> <impressão digital>: aceita a chave apt só se o arquivo tiver exatamente
# uma chave primária, com essa impressão digital (40 hex, maiúsculas). O TLS do download sozinho
# não basta: quem controlar o servidor ou o caminho entregaria outra chave, e o apt confiaria nela.
# Usado por deploy/gvisor.sh, deploy/provision.sh e scripts/ci-install-runsc.sh.
verify_apt_key() {
  local arquivo=$1 esperado=$2 primarias
  primarias=$(gpg --batch --show-keys --with-colons "$arquivo" 2>/dev/null |
    awk -F: '$1 == "pub" { p = 1; next } $1 == "fpr" && p { print $10; p = 0 }') || primarias=""
  if [[ $primarias != "$esperado" ]]; then
    echo "chave apt recusada (${arquivo}): impressão digital '${primarias//$'\n'/ }', esperada ${esperado}" >&2
    return 1
  fi
}
