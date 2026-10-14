#!/usr/bin/env bash
# Implanta a versão já em checkout (docs/GoblinShiftPlan.md, 3.8). Chamado pelo deploy.sh depois
# de validar o pedido e fazer o checkout do commit; por isso esta é sempre a versão do commit
# implantado. Roda como o usuário `goblinshift`, em /srv/goblinshift/app.
#
# Uso (só pelo deploy.sh): deploy/release.sh <image-tag>
set -euo pipefail

tag="${1:?uso: release.sh <image-tag>}"
ROOT=/srv/goblinshift
cd "$ROOT/app"
export GS_IMAGE_TAG="$tag"
export GS_ENVIRONMENT=production
export GS_SECRETS_DIR="$ROOT/secrets"
export GS_PG_DATA="$ROOT/pg/data"
export GS_TELEMETRY_DATA="$ROOT/telemetry/data"
export GS_STORAGE_DATA="$ROOT/storage"
# Segredo do cursor de paginação (3.3): criado uma vez, junto dos outros segredos da aplicação.
if ! grep -q '^GS_CURSOR_SECRET=' "$ROOT/secrets/app.env"; then
  echo "GS_CURSOR_SECRET=$(openssl rand -hex 32)" >>"$ROOT/secrets/app.env"
fi
compose=(docker compose --env-file "$ROOT/secrets/app.env" -f compose.yaml -f compose.prod.yaml)

"${compose[@]}" pull --quiet
# Migração (expand) antes da API nova; a velha continua atendendo com o schema compatível.
"${compose[@]}" run --rm --no-TTY migrate
"${compose[@]}" run --rm --no-TTY storage-init
# O template do Nginx vem do checkout e só é renderizado na partida: renderiza de novo e
# recarrega sem derrubar conexões. Vem antes do rollout, para que o Nginx já resolva a API
# pelo DNS quando os containers forem trocados.
if [[ -n "$("${compose[@]}" ps --quiet nginx)" ]]; then
  "${compose[@]}" exec -T nginx sh -c \
    '/docker-entrypoint.d/20-envsubst-on-templates.sh >/dev/null && nginx -t -q && nginx -s reload'
fi
# API sem queda (deploy/rollout-api.sh); o resto só é recriado se a configuração mudou.
deploy/rollout-api.sh "${compose[@]}"
"${compose[@]}" up -d --no-build --remove-orphans --wait --wait-timeout 180 \
  postgres otel-collector openobserve nginx worker maintenance

# Prontidão verificada dentro do container da API, sem depender do proxy de borda.
readyz=(python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=2)")
for _ in $(seq 1 30); do
  if "${compose[@]}" exec -T api "${readyz[@]}" >/dev/null 2>&1; then
    echo "deploy de $tag concluído e pronto"
    docker image prune -f --filter "until=168h" >/dev/null
    exit 0
  fi
  sleep 2
done
echo "deploy de $tag subiu, mas /readyz não ficou pronto em 60 s" >&2
exit 1
