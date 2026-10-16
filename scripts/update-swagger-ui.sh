#!/usr/bin/env bash
# Atualiza o Swagger UI servido em /docs (src/goblinshift/entrypoints/api/swagger_ui/).
#
# Baixa o pacote swagger-ui-dist do registro do npm na versão exata pedida, confere o tarball com
# o `integrity` publicado pelo registro e copia só o bundle, o CSS e as licenças. Depois, troque
# SWAGGER_UI_VERSION em api_docs.py e rode a suíte: o SRI é calculado dos próprios arquivos.
#
# Uso: scripts/update-swagger-ui.sh 5.33.0
set -euo pipefail

version="${1:?uso: scripts/update-swagger-ui.sh <versão exata, ex.: 5.33.0>}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "versão inválida: $version" >&2; exit 2; }

target="$(cd "$(dirname "$0")/.." && pwd)/src/goblinshift/entrypoints/api/swagger_ui"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

meta="$(curl -fsS --max-time 30 "https://registry.npmjs.org/swagger-ui-dist/${version}")"
tarball="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["dist"]["tarball"])' <<<"$meta")"
integrity="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["dist"]["integrity"])' <<<"$meta")"

curl -fsSL --max-time 120 -o "$work/pkg.tgz" "$tarball"
actual="sha512-$(openssl dgst -sha512 -binary "$work/pkg.tgz" | base64 | tr -d '\n')"
[[ "$actual" == "$integrity" ]] || { echo "integrity diverge: $actual != $integrity" >&2; exit 1; }

tar -xzf "$work/pkg.tgz" -C "$work"
for file in swagger-ui-bundle.js swagger-ui-bundle.js.LICENSE.txt swagger-ui.css LICENSE NOTICE; do
  cp "$work/package/$file" "$target/$file"
done
printf 'swagger-ui-dist %s\n%s\n%s\n' "$version" "$tarball" "$integrity" >"$target/SOURCE"
echo "Swagger UI $version em $target; atualize SWAGGER_UI_VERSION em api_docs.py."
