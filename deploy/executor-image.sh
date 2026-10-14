#!/usr/bin/env bash
# Baixa a imagem do executor da tag implantada, resolve o digest dela e o põe na allowlist do
# executor-ctl (docs/GoblinShiftPlan.md, 3.6 e 3.8). Chamado pelo release.sh, como o usuário
# `goblinshift`; imprime a referência `<repo>@sha256:<digest>` no stdout, para o Compose.
#
# A allowlist guarda o digest novo e o anterior, para que tentativas já aceitas com o digest
# antigo ainda possam terminar; nunca mais que dois. A escrita é atômica (temporário + mv), porque
# o executor-ctl pode ler o arquivo a qualquer momento.
#
# Uso: deploy/executor-image.sh <image-tag> <images-file>
set -euo pipefail
export LC_ALL=C

REPO=ghcr.io/bipoxdxd/goblinshift-executor
REF_PATTERN="${REPO//./\\.}@sha256:[0-9a-f]{64}"
ALLOWLIST_MODE=644

tag="${1:-}"
images_file="${2:?uso: executor-image.sh <image-tag> <images-file>}"
[[ "$tag" =~ ^sha-[0-9a-f]{12}$ ]] || { echo "tag inválida: '$tag'" >&2; exit 64; }

# O executor-ctl usa --pull=never: a imagem precisa estar no host antes da primeira tentativa.
docker pull --quiet "$REPO:$tag" >/dev/null

# RepoDigests lista um digest por repositório de onde a imagem veio; só serve o do executor.
image_ref=$(
  docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$REPO:$tag" \
    | grep -m 1 -xE "$REF_PATTERN" || true
)
[[ -n "$image_ref" ]] || { echo "sem digest de $REPO para $tag" >&2; exit 1; }

# O anterior é a primeira entrada válida do arquivo atual que não seja a nova.
previous=""
if [[ -f "$images_file" ]]; then
  previous=$(grep -oE "\"$REF_PATTERN\"" "$images_file" | tr -d '"' | grep -m 1 -vxF "$image_ref" || true)
fi

allowed="\"$image_ref\""
[[ -n "$previous" ]] && allowed+=", \"$previous\""

tmp=$(mktemp "$(dirname "$images_file")/.images.json.XXXXXX")
trap 'rm -f "$tmp"' EXIT
printf '{"allowed": [%s]}\n' "$allowed" >"$tmp"
chmod "$ALLOWLIST_MODE" "$tmp"
mv -f "$tmp" "$images_file"
trap - EXIT

echo "$image_ref"
