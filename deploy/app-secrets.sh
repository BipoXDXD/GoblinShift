#!/usr/bin/env bash
# Segredos da aplicação no lugar antes do deploy seguir (docs/GoblinShiftPlan.md; README,
# "Segredos do login"). Chamado pelo release.sh a cada deploy, antes do `migrate`: assim um
# segredo faltando para o deploy sem mexer no banco, em vez de derrubar a API nova no rollout.
#
# - Gera, uma vez, os que a própria aplicação cria, e nunca troca um que já existe: no app.env, o
#   do cursor de paginação (3.3) e o do `user.hash` dos eventos amplos (3.7); em arquivo, ao lado
#   do app.env, as chaves do CSRF e do cookie de login (fatia 6, 3.6).
# - Confere os que o operador grava (client secrets do Google e do GitHub), sem ler o conteúdo.
#
# Uso: deploy/app-secrets.sh <app.env>
set -euo pipefail

env_file="${1:?uso: app-secrets.sh <app.env>}"
secrets_dir=$(dirname "$env_file")

for name in GS_CURSOR_SECRET GS_USER_HASH_SECRET; do
  grep -q "^${name}=" "$env_file" && continue
  # Arquivo editado à mão pode terminar sem quebra de linha: o segredo grudaria na última.
  if [[ -s "$env_file" && -n "$(tail -c 1 "$env_file")" ]]; then
    printf '\n' >>"$env_file"
  fi
  printf '%s=%s\n' "$name" "$(openssl rand -hex 32)" >>"$env_file"
done

# Modo 644 dentro do diretório 700, como o db_password: o container lê com o UID da aplicação.
for name in csrf_secret login_cookie_secret; do
  file="$secrets_dir/$name"
  [[ -s "$file" ]] && continue
  key=$(openssl rand -hex 32)
  tmp=$(mktemp "$secrets_dir/.$name.XXXXXX")
  printf '%s' "$key" >"$tmp"
  chmod 644 "$tmp"
  mv -f "$tmp" "$file"
done

failed=0
for name in google_client_secret github_client_secret; do
  file="$secrets_dir/$name"
  if [[ ! -s "$file" ]]; then
    echo "falta $file (ausente ou vazio): grave o client secret com read -rs (README, \"Segredos do login\")" >&2
    failed=1
  elif [[ -z "$(find "$file" -perm -004)" ]]; then
    echo "a API não consegue ler $file: use modo 644 dentro do diretório 700 (chmod 644 $file)" >&2
    failed=1
  fi
done
exit "$failed"
