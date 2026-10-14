#!/usr/bin/env bash
# Grava no host a versão implantada: a tag da aplicação e a imagem do executor por digest
# (docs/GoblinShiftPlan.md, 3.8). Chamado pelo release.sh antes do primeiro comando do Compose; o
# deploy/compose.sh lê o arquivo, para que todo comando do Compose de produção, do deploy ou do
# operador, use a mesma versão.
#
# Os valores são validados antes de gravar, porque o arquivo é lido como --env-file: uma quebra de
# linha num deles acrescentaria outra variável. A escrita é atômica (temporário + mv), e o arquivo
# fica legível só pelo dono, como o app.env ao lado dele.
#
# Uso: deploy/release-env.sh <arquivo> <image-tag> <executor-image>
set -euo pipefail
export LC_ALL=C

EXECUTOR_REF_PATTERN='^ghcr\.io/bipoxdxd/goblinshift-executor@sha256:[0-9a-f]{64}$'
RELEASE_ENV_MODE=600

release_env="${1:?uso: release-env.sh <arquivo> <image-tag> <executor-image>}"
tag="${2:-}"
executor_image="${3:-}"
[[ "$tag" =~ ^sha-[0-9a-f]{12}$ ]] || { echo "tag inválida: '$tag'" >&2; exit 64; }
[[ "$executor_image" =~ $EXECUTOR_REF_PATTERN ]] || {
  echo "imagem do executor inválida: '$executor_image'" >&2
  exit 64
}

tmp=$(mktemp "$(dirname "$release_env")/.release.env.XXXXXX")
trap 'rm -f "$tmp"' EXIT
chmod "$RELEASE_ENV_MODE" "$tmp"
printf 'GS_IMAGE_TAG=%s\nGS_EXECUTOR_IMAGE=%s\n' "$tag" "$executor_image" >"$tmp"
mv -f "$tmp" "$release_env"
trap - EXIT
