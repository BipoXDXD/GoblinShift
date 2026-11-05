#!/usr/bin/env bash
# Confere se a conta da chave comporta mais uma rodada da jornada com escrita, antes de gastar cota
# (workflow e2e-producao.yml). Imprime `fits=true` ou `fits=false` no stdout, no formato do
# $GITHUB_OUTPUT; chave recusada, escopo ausente ou termos pendentes saem com erro.
#
# Uso: GS_API_KEY=gsk_… scripts/e2e-quota-check.sh [url-base]   (padrão: http://localhost)
set -euo pipefail

# shellcheck source=scripts/e2e-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/e2e-lib.sh"
e2e_setup "${1:-}"
me=$(api GET /me) || fail "GET /me falhou: $me"
python3 -I "$E2E_REPO_ROOT/scripts/e2e_quota_check.py" <<<"$me"
