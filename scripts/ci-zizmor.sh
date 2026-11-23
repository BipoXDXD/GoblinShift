#!/usr/bin/env bash
# Audita `.github/` inteiro com o zizmor instalado por hash (#366).
#
# Uso: ci-zizmor.sh [argumentos extras do zizmor...]   (a CI passa --format=github)
#
# É o portão local e o da CI: a versão e os hashes de todo arquivo publicado dela ficam só em
# .github/zizmor/requirements.txt, que o Dependabot atualiza. `uvx zizmor@X` baixaria o que o PyPI
# servisse com aquele número, sem conferir hash. O alvo `.github/` (e não só `workflows/`) põe o
# dependabot.yml nos audits `dependabot-*`. `--offline` dispensa o token da API do GitHub.
#
# ZIZMOR_VENV escolhe onde criar o venv (padrão: diretório temporário, apagado no fim).
set -euo pipefail

cd "$(dirname "$0")/.."

if ! python3 -c 'import venv, ensurepip' 2>/dev/null; then
  echo "ci-zizmor.sh: o python3 do PATH não tem os módulos venv e ensurepip;" \
    "instale-os (no Ubuntu, o pacote python3-venv) ou use outro python3." >&2
  exit 1
fi

if [[ -n ${ZIZMOR_VENV:-} ]]; then
  venv=$ZIZMOR_VENV
else
  venv=$(mktemp -d)
  trap 'rm -rf "$venv"' EXIT
fi

python3 -m venv "$venv"
PIP_DISABLE_PIP_VERSION_CHECK=1 "$venv/bin/pip" install --require-hashes --no-deps \
  --only-binary=:all: -r .github/zizmor/requirements.txt
"$venv/bin/zizmor" --offline "$@" .github/
