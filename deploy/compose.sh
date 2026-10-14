#!/usr/bin/env bash
# Docker Compose da produção (docs/GoblinShiftPlan.md, 3.8), o mesmo para o deploy e o operador.
# Junta os arquivos do Compose, os segredos (secrets/app.env), a versão implantada
# (secrets/release.env, gravado pelo deploy/release-env.sh) e os diretórios de dados do host. Sem
# a versão implantada, o compose.prod.yaml recusa o arquivo (GS_EXECUTOR_IMAGE) e o compose.yaml
# cairia na imagem `dev`.
#
# Os caminhos saem da posição do script: ele vive em <raiz>/app/deploy, com <raiz> =
# /srv/goblinshift. Roda como o usuário `goblinshift` (ou root), que lê secrets/.
#
# Uso: deploy/compose.sh <argumentos do docker compose…>
#   ex.: sudo -u goblinshift /srv/goblinshift/app/deploy/compose.sh exec api gs-admin …
set -euo pipefail

app=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
root=$(dirname "$app")
release_env="$root/secrets/release.env"
[[ -r "$release_env" ]] || {
  echo "não consigo ler $release_env: rode como goblinshift (ou root), depois de um deploy" >&2
  exit 1
}

# `sudo -u goblinshift` herda o diretório de quem chamou, que esse usuário pode não ler, e o
# Compose faz stat no diretório atual.
cd "$app"
export GS_ENVIRONMENT=production
export GS_SECRETS_DIR="$root/secrets"
export GS_PG_DATA="$root/pg/data"
export GS_TELEMETRY_DATA="$root/telemetry/data"
export GS_STORAGE_DATA="$root/storage"
exec docker compose --env-file "$root/secrets/app.env" --env-file "$release_env" \
  -f "$app/compose.yaml" -f "$app/compose.prod.yaml" "$@"
