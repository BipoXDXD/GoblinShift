#!/usr/bin/env bash
# Segredos gerados pela própria aplicação, criados uma vez no app.env (docs/GoblinShiftPlan.md):
# o do cursor de paginação (3.3) e o do `user.hash` dos eventos amplos (3.7). Chamado pelo
# release.sh a cada deploy; não muda um segredo que já existe.
#
# Uso: deploy/app-secrets.sh <app.env>
set -euo pipefail

env_file="${1:?uso: app-secrets.sh <app.env>}"
for name in GS_CURSOR_SECRET GS_USER_HASH_SECRET; do
  grep -q "^${name}=" "$env_file" && continue
  # Arquivo editado à mão pode terminar sem quebra de linha: o segredo grudaria na última.
  if [[ -s "$env_file" && -n "$(tail -c 1 "$env_file")" ]]; then
    printf '\n' >>"$env_file"
  fi
  printf '%s=%s\n' "$name" "$(openssl rand -hex 32)" >>"$env_file"
done
