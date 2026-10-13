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

# O resto vem do commit implantado: depois do checkout, o bash continuaria executando a versão
# anterior deste arquivo, e uma mudança no deploy só valeria no deploy seguinte.
exec deploy/release.sh "$tag"
