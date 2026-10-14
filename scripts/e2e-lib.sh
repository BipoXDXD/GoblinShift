# shellcheck shell=bash
# Funções comuns dos scripts de ponta a ponta (e2e-csv-janitor.sh, e2e-image-forge.sh e
# e2e-downloads-detox.sh). Não roda sozinho: cada script faz `source` e chama `e2e_setup`.
#
# Só curl e o python3 do host com -I (biblioteca padrão, sem o ambiente do projeto), exceto na
# geração das fixtures, que usa o `uv` do repositório (o Pillow do uv.lock).
# Compatível com o bash 3.2 do macOS: sem mapfile nem arrays associativos.

E2E_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
POLL_INTERVAL_S=2
POLL_LIMIT="${POLL_LIMIT:-150}" # 5 min por execução; aumente na VPS lenta com POLL_LIMIT=…

# Define BASE, KEY e WORK (diretório temporário apagado na saída). Uso: e2e_setup "${1:-}"
e2e_setup() {
  BASE="${1:-http://localhost}/api/v1"
  KEY="${GS_API_KEY:?defina GS_API_KEY com uma chave emitida pelo gs-admin}"
  WORK="$(mktemp -d)"
  trap 'rm -rf "$WORK"' EXIT
}

fail() {
  echo "$*" >&2
  exit 1
}

api() {
  # Falha com o corpo problem+json visível em vez de seguir com uma resposta de erro.
  local method="$1" path="$2"
  shift 2
  curl --silent --show-error --fail-with-body --max-time 30 -X "$method" \
    -H "Authorization: Bearer $KEY" "$@" "$BASE$path"
}

json() {
  # Extrai um campo do JSON da entrada padrão: json '["items"][0]["id"]'
  python3 -I -c "import json, sys; print(json.load(sys.stdin)$1)"
}

sha256_of() {
  python3 -I -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$1"
}

# Envia os arquivos num lote só: uma intenção (POST /uploads com `items`) e um PUT por arquivo.
# O nome do upload é o nome do arquivo. Confere o estado `ready` e o SHA-256 que a API calculou.
# Imprime os upload_ids, um por linha, na ordem dos argumentos.
upload_files() {
  local body ids_text id file received index=0
  local -a ids=()
  body=$(python3 -I -c '
import json, os, sys
items = [{"name": os.path.basename(p), "size": os.path.getsize(p)} for p in sys.argv[1:]]
print(json.dumps({"items": items}))' "$@")
  ids_text=$(api POST /uploads -H 'Content-Type: application/json' --data "$body" |
    python3 -I -c 'import json, sys; print("\n".join(i["id"] for i in json.load(sys.stdin)["items"]))')
  while IFS= read -r id; do ids+=("$id"); done <<<"$ids_text"
  [[ ${#ids[@]} -eq $# ]] || fail "POST /uploads devolveu ${#ids[@]} intenções para $# arquivos"
  for file in "$@"; do
    id="${ids[$index]}"
    index=$((index + 1))
    received=$(api PUT "/uploads/$id/content" -H 'Content-Type: application/octet-stream' \
      --data-binary "@$file")
    [[ "$(json '["state"]' <<<"$received")" == "ready" ]] ||
      fail "upload de $(basename "$file") não ficou pronto: $received"
    [[ "$(json '["sha256"]' <<<"$received")" == "$(sha256_of "$file")" ]] ||
      fail "SHA-256 do upload de $(basename "$file") difere do arquivo local"
    echo "$id"
  done
}

# Cria a Run com uma chave de idempotência nova e confere que repetir a mesma chave devolve o
# replay. Uso: start_run "$command_json" rótulo   → imprime o run_id.
start_run() {
  local command="$1" label="$2" key run_id replayed
  key="e2e-$(date +%s)-$RANDOM-$label"
  run_id=$(api POST /runs -H 'Content-Type: application/json' -H "Idempotency-Key: $key" \
    --data "$command" | json '["id"]')
  replayed=$(api POST /runs -H 'Content-Type: application/json' -H "Idempotency-Key: $key" \
    --data "$command" -o /dev/null -D - |
    tr -d '\r' | awk -F': ' 'tolower($1)=="idempotency-replayed" {print $2}')
  [[ "$replayed" == "true" ]] || fail "repetir a chave $key não devolveu replay"
  echo "$run_id"
}

# Espera a Run sair de queued/running/stopping e exige o estado final pedido.
require_status() {
  local run_id="$1" expected="$2" status=""
  for _ in $(seq 1 "$POLL_LIMIT"); do
    status=$(api GET "/runs/$run_id" | json '["status"]')
    case "$status" in
      queued | running | stopping) sleep "$POLL_INTERVAL_S" ;;
      *) break ;;
    esac
  done
  if [[ "$status" != "$expected" ]]; then
    echo "execução $run_id terminou em $status; esperado $expected" >&2
    api GET "/runs/$run_id" >&2
    exit 1
  fi
}

# Exige pelo menos um artefato de cada papel nos resultados. Uso: require_roles RUN papel...
require_roles() {
  local run_id="$1"
  shift
  api GET "/runs/$run_id/results" | python3 -I -c '
import json, sys
results = json.load(sys.stdin)
present = {a["role"] for a in results["artifacts"]}
missing = [role for role in sys.argv[1:] if role not in present]
if not results["published"] or missing:
    sys.exit(f"resultados sem publicação ou sem os papéis {missing}: {sorted(present)}")' "$@"
}

# Imprime o artifact_id do único resultado com o papel (e, se dado, o caminho lógico pedido).
artifact_id() {
  local run_id="$1" role="$2" logical_name="${3:-}"
  api GET "/runs/$run_id/results" | python3 -I -c '
import json, sys
role, name = sys.argv[1], sys.argv[2]
found = [a["artifact_id"] for a in json.load(sys.stdin)["artifacts"]
         if a["role"] == role and name in ("", a["logical_name"])]
if len(found) != 1:
    sys.exit("%d artefatos com papel %s e nome %s" % (len(found), role, name or "(qualquer)"))
print(found[0])' "$role" "$logical_name"
}

artifact_sha256() {
  api GET "/artifacts/$1" | json '["sha256"]'
}

download() {
  api GET "/artifacts/$1/download" -o "$2"
}

# Baixa o pacote (papel `package`, nome `<recipe_id>.zip`) para DEST e exige que o SHA-256 dos
# metadados seja o dos bytes baixados. Uso: fetch_package RUN recipe_id DEST
fetch_package() {
  local run_id="$1" recipe_id="$2" dest="$3" id
  id=$(artifact_id "$run_id" package "$recipe_id.zip")
  download "$id" "$dest"
  [[ "$(artifact_sha256 "$id")" == "$(sha256_of "$dest")" ]] ||
    fail "SHA-256 dos metadados do pacote difere do ZIP baixado (run $run_id)"
}

# Determinismo (3.6, 3.9): duas execuções do mesmo pedido dão o mesmo ZIP, byte a byte.
require_same_package() {
  local first="$1" second="$2"
  [[ "$(sha256_of "$first")" == "$(sha256_of "$second")" ]] ||
    fail "o mesmo pedido gerou ZIPs diferentes: $(sha256_of "$first") e $(sha256_of "$second")"
}

# --- Cenários com fixtures geradas (scripts/e2e_fixtures.py) ---

generate_fixtures() {
  local recipe_id="$1" dir="$2"
  (cd "$E2E_REPO_ROOT" && uv run --quiet python -m scripts.e2e_fixtures "$recipe_id" "$dir")
}

expected_field() {
  json "$2" <"$1/expected.json"
}

# Envia as entradas do cenário na ordem do expected.json; imprime os upload_ids.
upload_fixtures() {
  local dir="$1" path
  local -a paths=()
  python3 -I -c 'import json, sys; [print(e["path"]) for e in json.load(open(sys.argv[1]))["inputs"]]' \
    "$dir/expected.json" >"$WORK/fixture_paths"
  while IFS= read -r path; do paths+=("$dir/$path"); done <"$WORK/fixture_paths"
  upload_files "${paths[@]}"
}

# Corpo do POST /runs a partir do expected.json e do arquivo com os upload_ids.
command_from_expected() {
  python3 -I -c '
import json, sys
expected = json.load(open(sys.argv[1]))
ids = open(sys.argv[2]).read().split()
print(json.dumps({"recipe_id": expected["recipe_id"], "recipe_version": expected["recipe_version"],
                  "input_upload_ids": ids, "parameters": expected["parameters"]}))' \
    "$1/expected.json" "$2"
}

check_package() {
  python3 -I "$E2E_REPO_ROOT/scripts/e2e_check_package.py" "$1" "$2/expected.json" "$2"
}
