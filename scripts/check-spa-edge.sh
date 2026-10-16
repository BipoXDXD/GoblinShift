#!/usr/bin/env bash
# Confere a SPA na borda, de fora, como o navegador a recebe (docs/GoblinShiftPlan.md, 3.6 e 3.8):
# cabeçalhos de segurança, cache (index sem cache, assets com hash imutáveis), 404 de verdade onde
# não há fallback da SPA e o tipo de conteúdo de cada classe de arquivo. Só faz GET.
#
# Uso: ./scripts/check-spa-edge.sh [url-base]   (padrão: https://goblinshift.dev)
# Sai com 1 se alguma verificação falhar, listando todas as falhas.
set -euo pipefail

BASE="${1:-https://goblinshift.dev}"
BASE="${BASE%/}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
failures=0

fail() {
  echo "FALHA: $*" >&2
  failures=$((failures + 1))
}

ok() {
  echo "ok: $*"
}

# GET do caminho; grava o corpo e os cabeçalhos e devolve o status (000 se a conexão falhar).
# Sem --fail: o status é o que se confere.
get() {
  local path="$1" status
  : >"$work/body"
  : >"$work/headers"
  # Em falha de conexão o curl já escreve 000 no -w; o `|| true` só segura o set -e.
  status="$(curl -sS --max-time 15 -o "$work/body" -D "$work/headers" -w '%{http_code}' \
    "$BASE$path" 2>"$work/curl-error" || true)"
  echo "${status:-000}"
}

# Valor do cabeçalho (sem diferenciar maiúsculas), da última resposta; vazio se ausente.
header() {
  grep -i "^$1:" "$work/headers" | tail -n 1 | cut -d: -f2- | tr -d '\r' | sed 's/^ *//' || true
}

expect_status() {
  local path="$1" expected="$2" status
  status="$(get "$path")"
  if [[ "$status" != "$expected" ]]; then
    fail "$path respondeu $status, esperado $expected"
    return 1
  fi
}

expect_header_contains() {
  local path="$1" name="$2" fragment="$3" value
  value="$(header "$name")"
  if [[ "$value" != *"$fragment"* ]]; then
    fail "$path: $name sem '$fragment' (veio '${value}')"
    return 1
  fi
}

# Cabeçalhos que toda resposta da SPA leva (spa-headers.conf).
expect_spa_headers() {
  local path="$1"
  expect_header_contains "$path" Content-Security-Policy "default-src 'self'" || true
  expect_header_contains "$path" Content-Security-Policy "frame-ancestors 'none'" || true
  expect_header_contains "$path" Content-Security-Policy "object-src 'none'" || true
  expect_header_contains "$path" X-Content-Type-Options nosniff || true
  expect_header_contains "$path" Referrer-Policy strict-origin-when-cross-origin || true
  expect_header_contains "$path" Cross-Origin-Opener-Policy same-origin || true
  if [[ "$BASE" == https://* ]]; then
    expect_header_contains "$path" Strict-Transport-Security "max-age=" || true
  fi
}

# Página da SPA: HTML, sem cache (o index aponta os assets da versão atual) e os cabeçalhos.
check_page() {
  local path="$1"
  expect_status "$path" 200 || return 0
  expect_header_contains "$path" Content-Type text/html || true
  expect_header_contains "$path" Cache-Control no-cache || true
  expect_spa_headers "$path"
  ok "$path"
}

# Arquivo fora do fallback: tipo e cache esperados.
check_file() {
  local path="$1" type="$2" cache="$3"
  expect_status "$path" 200 || return 0
  expect_header_contains "$path" Content-Type "$type" || true
  expect_header_contains "$path" Cache-Control "$cache" || true
  expect_header_contains "$path" X-Content-Type-Options nosniff || true
  ok "$path"
}

# Onde não há fallback, o que não existe é 404, nunca o index.html com 200.
check_missing() {
  local path="$1"
  expect_status "$path" 404 || return 0
  ok "$path (404)"
}

if [[ "$(get /)" == "000" ]]; then
  echo "FALHA: $BASE não responde: $(cat "$work/curl-error")" >&2
  exit 1
fi

check_page /
# Os assets da versão no ar, lidos do próprio index.html.
script="$(grep -oE '/assets/[A-Za-z0-9._-]+\.js' "$work/body" | head -n 1 || true)"
style="$(grep -oE '/assets/[A-Za-z0-9._-]+\.css' "$work/body" | head -n 1 || true)"
check_page /historico
check_page /rota-que-nao-existe-na-spa

if [[ -z "$script" ]]; then
  fail "o index.html não aponta nenhum /assets/*.js"
else
  check_file "$script" javascript "max-age=31536000"
  expect_header_contains "$script" Cache-Control immutable || true
fi
if [[ -z "$style" ]]; then
  fail "o index.html não aponta nenhum /assets/*.css"
else
  check_file "$style" text/css "max-age=31536000"
  expect_header_contains "$style" Cache-Control immutable || true
fi
check_missing /assets/nao-existe-check-spa-edge.js

check_file /theme-init.js javascript no-cache
check_file /manifest.webmanifest application/manifest+json no-cache
check_file /favicon.svg image/svg+xml no-cache
check_missing /nao-existe-check-spa-edge.png
check_file /.well-known/security.txt "text/plain; charset=utf-8" no-cache
check_missing /.well-known/nao-existe-check-spa-edge

# A API atrás da mesma borda: erro em problem+json e a documentação pública.
if expect_status /api/v1/rota-que-nao-existe 404; then
  expect_header_contains /api/v1/rota-que-nao-existe Content-Type application/problem+json || true
  ok "/api/v1/rota-que-nao-existe (404 problem+json)"
fi
if expect_status /openapi.json 200; then
  expect_header_contains /openapi.json Content-Type application/json || true
  ok /openapi.json
fi
if expect_status /docs 200; then
  # Sem a location da API, o fallback serviria o index.html com 200: confere o conteúdo.
  if grep -q swagger-ui "$work/body"; then
    ok /docs
  else
    fail "/docs: o corpo não é a Swagger UI"
  fi
fi
if expect_status /livez 200; then
  expect_header_contains /livez Content-Type application/json && ok /livez
fi

if ((failures > 0)); then
  echo "$failures verificação(ões) falharam em $BASE." >&2
  exit 1
fi
echo "Borda da SPA em $BASE: tudo certo."
