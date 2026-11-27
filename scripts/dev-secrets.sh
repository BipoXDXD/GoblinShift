#!/usr/bin/env bash
# Gera segredos locais de desenvolvimento (nunca versionados): .secrets/ e .env.
# Em produção, os mesmos arquivos ficam em /srv/goblinshift/secrets (README, "Segredos do login").
# Nenhum segredo vai no .env: o Compose o interpolaria em variável de ambiente, visível no
# `docker inspect` (#442, 34a). No .env fica só o e-mail do root do OpenObserve.
set -euo pipefail
cd "$(dirname "$0")/.."

umask 077
mkdir -p .secrets
if [[ ! -f .secrets/db_password ]]; then
  openssl rand -hex 32 > .secrets/db_password
fi

if [[ ! -f .env ]]; then
  echo "OPENOBSERVE_ROOT_EMAIL=admin@goblinshift.local" > .env
fi
email="$(sed -n 's/^OPENOBSERVE_ROOT_EMAIL=//p' .env)"

if [[ ! -f .secrets/openobserve_env ]]; then
  # Instalação anterior: a senha estava no .env, e é a do root já criado no volume.
  password="$(sed -n 's/^OPENOBSERVE_ROOT_PASSWORD=//p' .env)"
  if [[ -z "$password" ]]; then
    # OpenObserve exige maiúscula, minúscula, dígito e caractere especial.
    password="$(openssl rand -base64 30 | tr -dc 'A-Za-z0-9' | head -c 28)Aa1_"
  fi
  printf 'ZO_ROOT_USER_PASSWORD=%s\n' "$password" > .secrets/openobserve_env
fi
password="$(sed -n 's/^ZO_ROOT_USER_PASSWORD=//p' .secrets/openobserve_env)"

# Sem quebra de linha final: o Collector usa o conteúdo cru no cabeçalho Authorization.
if [[ ! -f .secrets/openobserve_basic_auth ]]; then
  printf '%s' "$(printf '%s:%s' "$email" "$password" | base64 | tr -d '\n')" \
    > .secrets/openobserve_basic_auth
fi

# Os arquivos são lidos dentro dos containers por outros usuários (postgres, app, Collector);
# a proteção no host vem do diretório .secrets/ com permissão 700.
chmod 644 .secrets/db_password .secrets/openobserve_env .secrets/openobserve_basic_auth
echo "Segredos locais prontos em .secrets/ e .env"
