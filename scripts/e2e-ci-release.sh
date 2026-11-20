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
  # A conta é compartilhada e a pilha usa os recursos do mesmo runner que os navegadores.
  if ! started=$(date -u '+%Y-%m-%d %H:%M:%S UTC'); then
    started=''
    echo 'Hora de início do diagnóstico indisponível.'
  fi
  status=0
  # Publish process IDs and exit status, leaving native browser messages out of public CI logs.
  DEBUG=pw:browser DEBUG_COLORS=0 \
    E2E_CI_READ=1 E2E_BASE_URL="$base_url" GS_E2E_API_KEY="$key" \
    npx playwright test -c e2e --trace off --retries=0 --workers=1 2>&1 |
    LC_ALL=C sed -En '
      /pw:browser / {
        s/.*\[pid=([0-9]+)\] (<process did exit: exitCode=([0-9]+|null), signal=([A-Z0-9]+|null)>).*/Browser PID \1: \2/p
        s/.*[[:space:]]process ([0-9]+) exited on signal ([0-9]+).*/Content PID \1: signal=\2/p
        d
      }
      p
    ' || status=${PIPESTATUS[0]}
  if ((status != 0)); then
    timeout 5s uname -sr || echo 'Versão do kernel indisponível.'
    timeout 5s free -m || echo 'Dados de memória indisponíveis.'
    timeout 5s df -h /dev/shm || echo 'Dados de memória compartilhada indisponíveis.'
    if [[ -n "$started" ]] && ! timeout 8s sudo -n journalctl \
      --dmesg --since "$started" --no-pager --grep 'oom-kill|Out of memory|Killed process|segfault'; then
      echo 'Sem mensagem correspondente ou journal indisponível.'
    fi
    exit "$status"
  fi
  npm run e2e:check-skips -- e2e-results/all.json
  python3 ../scripts/verificar_artefatos_sem_chave.py playwright-report
)
