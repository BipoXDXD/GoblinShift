#!/usr/bin/env bash
# Deploy na VPS (docs/GoblinShiftPlan.md, 3.8). Roda como o usuário `goblinshift`.
#
# A chave SSH da CI está presa a este script (forced command em authorized_keys), então
# ele recebe o pedido em SSH_ORIGINAL_COMMAND no formato: deploy <commit-sha> <image-tag>.
# Também pode ser chamado direto: deploy/deploy.sh <commit-sha> <image-tag>
set -euo pipefail

if [[ $# -eq 0 && -n "${SSH_ORIGINAL_COMMAND:-}" ]]; then
  read -r -a request <<<"$SSH_ORIGINAL_COMMAND"
  [[ "${request[0]:-}" == "deploy" && ${#request[@]} -eq 3 ]] || {
    echo "comando recusado" >&2
    exit 64
  }
  set -- "${request[1]}" "${request[2]}"
fi

commit="${1:?uso: deploy.sh <commit-sha> <image-tag>}"
tag="${2:?uso: deploy.sh <commit-sha> <image-tag>}"
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || { echo "commit inválido" >&2; exit 64; }
[[ "$tag" =~ ^sha-[0-9a-f]{12}$ ]] || { echo "tag inválida" >&2; exit 64; }
# A imagem precisa ser a do próprio commit: impede combinar código de um commit com outro.
[[ "$tag" == "sha-${commit:0:12}" ]] || { echo "tag não corresponde ao commit" >&2; exit 64; }

ROOT=/srv/goblinshift
cd "$ROOT/app"
git fetch --quiet origin main
# Só commits que já estão na main (passaram pela CI); nada de branches nem commits soltos.
git merge-base --is-ancestor "$commit" origin/main || { echo "commit fora da main" >&2; exit 65; }
git checkout --quiet --detach "$commit"

export GS_IMAGE_TAG="$tag"
export GS_ENVIRONMENT=production
export GS_SECRETS_DIR="$ROOT/secrets"
export GS_PG_DATA="$ROOT/pg/data"
export GS_TELEMETRY_DATA="$ROOT/telemetry/data"
compose=(docker compose --env-file "$ROOT/secrets/app.env" -f compose.yaml -f compose.prod.yaml)

"${compose[@]}" pull --quiet
"${compose[@]}" up -d --no-build --remove-orphans --wait --wait-timeout 180

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
