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
# Segredos gerados pela própria aplicação (criados uma vez, nunca trocados) e os gravados pelo
# operador conferidos: sem um deles o deploy para aqui, antes do `migrate`.
deploy/app-secrets.sh "$ROOT/secrets/app.env"

# Imagem do executor do mesmo commit, sempre pelo digest: entra na allowlist do executor-ctl
# antes de a API nova aceitar execuções com ela. Sem digest resolvido o deploy para aqui.
executor_image=$(deploy/executor-image.sh "$tag" "$ROOT/executor/images.json")
# A versão implantada vai para o host antes do primeiro comando do Compose, que já a interpola; o
# deploy/compose.sh a lê, e o operador usa o mesmo script nos comandos manuais.
deploy/release-env.sh "$ROOT/secrets/release.env" "$tag" "$executor_image"
compose=(deploy/compose.sh)

"${compose[@]}" pull --quiet
# Migração (expand) antes da API nova; a velha continua atendendo com o schema compatível.
"${compose[@]}" run --rm --no-TTY migrate
# O storage é um filesystem próprio (3.8); desmontado, a pasta seria recriada no disco do
# sistema e a manutenção veria todos os objetos ausentes. Sem montagem o deploy para aqui.
mountpoint -q "$ROOT/storage" || { echo "storage não montado em $ROOT/storage" >&2; exit 1; }
"${compose[@]}" run --rm --no-TTY storage-init
"${compose[@]}" run --rm --no-TTY storage-mark
# API sem queda (deploy/rollout-api.sh); o resto só é recriado se a configuração mudou. O Nginx
# (imagem goblinshift-web, com o build da SPA e a configuração) muda a cada tag e é recriado no
# `up`: a borda repete a conexão enquanto ele volta (lb_try_duration em deploy/edge/conf.d).
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
