#!/usr/bin/env bash
# Gera os segredos do provisionamento em <diretório> (chamado pelo provision.sh, que cuida do dono).
# Segredo que já existe com conteúdo não é trocado. Cada arquivo nasce num temporário do mesmo
# diretório e só entra no lugar por `mv`, depois de conferido: um openssl que falhe não deixa
# arquivo vazio que a execução seguinte pularia.
#
# Uso: bash deploy/provision-secrets.sh /srv/goblinshift/secrets
set -euo pipefail

dir=${1:?uso: provision-secrets.sh <diretório dos segredos>}
[[ -d $dir ]] || { echo "diretório de segredos inexistente: $dir" >&2; exit 1; }
tmp=""
trap '[[ -z $tmp ]] || rm -f "$tmp"' EXIT

if [[ ! -s "$dir/db_password" ]]; then
  tmp=$(mktemp "$dir/.db_password.XXXXXX")
  openssl rand -hex 32 > "$tmp"
  grep -qxE '[0-9a-f]{64}' "$tmp" || { echo "db_password gerado fora do formato" >&2; exit 1; }
  # Lido dentro dos containers por outros usuários; protegido pelo diretório 700.
  chmod 644 "$tmp"
  mv -f "$tmp" "$dir/db_password"
  tmp=""
fi

if [[ ! -s "$dir/app.env" ]]; then
  email="admin@goblinshift.local"
  aleatorio=$(openssl rand -base64 30)
  password="$(tr -dc 'A-Za-z0-9' <<<"$aleatorio" | cut -c1-28)Aa1_"
  (( ${#password} == 32 )) || { echo "senha do OpenObserve gerada curta" >&2; exit 1; }
  tmp=$(mktemp "$dir/.app.env.XXXXXX")
  {
    echo "OPENOBSERVE_ROOT_EMAIL=${email}"
    echo "OPENOBSERVE_ROOT_PASSWORD=${password}"
    echo "OPENOBSERVE_BASIC_AUTH=$(printf '%s:%s' "$email" "$password" | base64 | tr -d '\n')"
  } > "$tmp"
  for chave in OPENOBSERVE_ROOT_EMAIL OPENOBSERVE_ROOT_PASSWORD OPENOBSERVE_BASIC_AUTH; do
    grep -qE "^${chave}=.+" "$tmp" || { echo "app.env sem ${chave}" >&2; exit 1; }
  done
  chmod 600 "$tmp"
  mv -f "$tmp" "$dir/app.env"
  tmp=""
fi
