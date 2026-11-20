#!/usr/bin/env bash
# Exercise the released edge and all read-only journeys with an ephemeral account.
set -euo pipefail
cd "$(dirname "$0")/.."
[[ "${GITHUB_ACTIONS:-}" == true ]] || { echo 'Hosted CI only' >&2; exit 1; }
base_url=http://localhost

compose() { docker compose -f compose.yaml -f compose.e2e-release.yaml "$@"; }
trap 'compose down --volumes --remove-orphans' EXIT
scripts/dev-secrets.sh
compose up --detach --no-build api nginx
for attempt in {1..90}; do
  if curl --silent --fail --max-time 2 "$base_url/readyz" >/dev/null; then break; fi
  if ((attempt == 90)); then echo 'CI stack did not become ready' >&2; exit 1; fi
  sleep 2
done

scripts/check-spa-edge.sh "$base_url"
user=$(compose exec -T api gs-admin create-user --display-name 'CI release read-only')
key=$(compose exec -T api gs-admin issue-key --user "$user" --days 1 \
  --scopes files:read,runs:read,presets:read)
[[ -n "$key" ]]
echo "::add-mask::$key"
(
  cd web
  E2E_CI_READ=1 E2E_BASE_URL="$base_url" GS_E2E_API_KEY="$key" \
    npx playwright test -c e2e --trace off --retries=0
  npm run e2e:check-skips -- e2e-results/all.json
  python3 ../scripts/verificar_artefatos_sem_chave.py playwright-report
)
