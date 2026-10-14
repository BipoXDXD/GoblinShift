#!/usr/bin/env bash
# Liga os hooks versionados em .githooks/ (hoje, o pre-commit que procura segredos).
# A configuração vale para o clone e todos os seus worktrees; num worktree cuja branch ainda
# não tem .githooks/, o git simplesmente não roda hook nenhum.
set -euo pipefail
cd "$(dirname "$0")/.."
git config core.hooksPath .githooks
echo "Hooks ligados: $(git config core.hooksPath)"
