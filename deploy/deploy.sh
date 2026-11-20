#!/usr/bin/env bash
# MORTO: o deploy por SSH foi desativado. A publicação é só pelo Coolify (deploy/coolify/README.md),
# e o job `deploy` da CI verifica essa recusa e o contrato do receptor em fixtures locais.
#
# O arquivo continua porque o forced command do deploy/provision.sh aponta para ele; agora ele só
# recusa, com ou sem pedido válido. O deploy antigo recriava a pilha fora do Coolify.
set -euo pipefail

echo "deploy por SSH desativado: a publicação é só pelo Coolify (deploy/coolify/README.md)" >&2
exit 64
