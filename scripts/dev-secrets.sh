#!/usr/bin/env bash
# Gera segredos locais de desenvolvimento (nunca versionados): .secrets/ e .env.
# Em produção, o equivalente é deploy/provision.sh, em /srv/goblinshift/secrets.
set -euo pipefail
cd "$(dirname "$0")/.."

umask 077
mkdir -p .secrets
if [[ ! -f .secrets/db_password ]]; then
  openssl rand -hex 32 > .secrets/db_password
fi
# O arquivo é lido dentro dos containers por outros usuários (postgres, app);
# a proteção no host vem do diretório .secrets/ com permissão 700.
chmod 644 .secrets/db_password

if [[ ! -f .env ]]; then
  email="admin@goblinshift.local"
  # OpenObserve exige maiúscula, minúscula, dígito e caractere especial; sem "$",
  # que o Compose interpretaria como variável.
  password="$(openssl rand -base64 30 | tr -dc 'A-Za-z0-9' | head -c 28)Aa1_"
  {
    echo "OPENOBSERVE_ROOT_EMAIL=${email}"
    echo "OPENOBSERVE_ROOT_PASSWORD=${password}"
    echo "OPENOBSERVE_BASIC_AUTH=$(printf '%s:%s' "$email" "$password" | base64 -w0)"
  } > .env
fi
echo "Segredos locais prontos em .secrets/ e .env"
