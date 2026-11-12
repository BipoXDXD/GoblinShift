#!/usr/bin/env bash
# Playwright contra a API real com cada tentativa num container gVisor, pelo executor-ctl do host.
# Roda só no runner efêmero da CI: grava em /etc, /var/lib e /srv e instala um serviço systemd.
#
#   scripts/e2e-gvisor.sh up                 sobe executor-ctl e pilha (exige runsc no Docker)
#   scripts/e2e-gvisor.sh journeys           cria uma conta e chave por arquivo de spec, roda as specs,
#                                            confere que nenhuma resposta foi 429 e o runtime
#   scripts/e2e-gvisor.sh verify <arquivo>   confere o runtime gravado contra o banco
#   scripts/e2e-gvisor.sh rate-limits <arquivo>  falha se o log do Nginx tiver resposta 429, ou
#                                            se não tiver nenhuma linha de acesso
#   scripts/e2e-gvisor.sh check-rate-limits <desde>  lê o log do Nginx desde o instante e confere
#   scripts/e2e-gvisor.sh logs <diretório>   guarda os logs da pilha e do executor-ctl
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

BASE_URL=http://localhost
SERVICE=goblinshift-executor-ctl.service
WORK_ROOT=/srv/goblinshift
# Só o tempo do push: com ela o `docker push` dá o digest que a allowlist exige, e a imagem fica
# no Docker do runner para o `--pull=never` do executor-ctl.
REGISTRY_IMAGE=registry:3.0.0@sha256:6c5666b861f3505b116bb9aa9b25175e71210414bd010d92035ff64018f9457e
REGISTRY_ADDRESS=127.0.0.1:5000
EXECUTOR_REPOSITORY="$REGISTRY_ADDRESS/goblinshift-executor"
READY_ATTEMPTS=90
RUNSC=runsc
SPEC_DIR="$ROOT/web/e2e/specs"
E2E_KEY_SCOPES=files:read,files:write,runs:read,runs:write,presets:read,presets:write

compose() {
  docker compose -f "$ROOT/compose.yaml" -f "$ROOT/compose.e2e-gvisor.yaml" "$@"
}

fail() {
  echo "$*" >&2
  exit 1
}

require_ephemeral_runner() {
  [[ "${CI:-}" == true ]] || fail "este comando só roda no runner efêmero da CI"
}

publish_executor_image() {
  docker build --quiet --file "$ROOT/docker/executor.Dockerfile" --tag "$EXECUTOR_REPOSITORY:e2e" "$ROOT" >&2
  docker run --detach --name e2e-registry --publish "$REGISTRY_ADDRESS:5000" "$REGISTRY_IMAGE" >&2
  wait_for_url "http://$REGISTRY_ADDRESS/v2/"
  docker push --quiet "$EXECUTOR_REPOSITORY:e2e" >&2
  docker rm --force e2e-registry >&2
  docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$EXECUTOR_REPOSITORY:e2e" |
    grep -F "$EXECUTOR_REPOSITORY@sha256:"
}

# O filesystem de tamanho fixo da área de trabalho, como no deploy/provision.sh.
mount_work_area() {
  local image="$WORK_ROOT/exec-work.img"
  sudo install -d -m 755 "$WORK_ROOT" "$WORK_ROOT/exec-work"
  sudo fallocate -l 6G "$image"
  sudo mkfs.ext4 -q -m 0 -L gs-exec-work "$image"
  sudo mount -o loop,noatime "$image" "$WORK_ROOT/exec-work"
}

# Os dois arquivos que o instalador exige prontos, no lugar e com os donos de produção.
write_executor_policy() {
  local image="$1" images_dir=/var/lib/coolify-release/goblinshift
  sudo install -d -m 755 -o root -g root /etc/goblinshift /var/lib/coolify-release "$images_dir"
  printf '{"allowed": ["%s"]}\n' "$image" | sudo tee "$images_dir/executor-images.json" >/dev/null
  sudo chmod 600 "$images_dir/executor-images.json"
  sudo tee /etc/goblinshift/executor-ctl.json >/dev/null <<EOF
{
  "socket": "/run/goblinshift/executor-ctl.sock",
  "group": "gs-exec",
  "work_area": "$WORK_ROOT/exec-work",
  "runtime": "$RUNSC",
  "images_file": "$images_dir/executor-images.json"
}
EOF
}

wait_for_url() {
  local url="$1" attempt
  for ((attempt = 0; attempt < READY_ATTEMPTS; attempt++)); do
    curl --silent --fail --max-time 2 --output /dev/null "$url" && return 0
    sleep 2
  done
  fail "sem resposta de $url"
}

up() {
  require_ephemeral_runner
  "$ROOT/scripts/dev-secrets.sh"
  local image
  image="$(publish_executor_image)"
  mount_work_area
  write_executor_policy "$image"
  sudo bash "$ROOT/deploy/executor-ctl/install.sh"
  # No .env, para os `docker compose` dos passos seguintes interpolarem a mesma imagem.
  echo "GS_EXECUTOR_IMAGE=$image" >>"$ROOT/.env"
  compose up --detach --build
  wait_for_url "$BASE_URL/readyz"
}

# Cada tentativa vive poucos segundos e o container some depois: o runtime só pode ser lido
# enquanto ela roda. A saída tem uma linha por tentativa iniciada.
record_runtimes() {
  local id
  while IFS= read -r id; do
    docker inspect --format '{{.HostConfig.Runtime}}' "$id" 2>/dev/null || echo "sem-inspect"
  done
}

# O limite de leituras (120 por minuto) vale por conta. Com uma conta só para a suíte inteira, o
# run 43143543196 fez 133 GETs em 60 s e o 429 chegou como timeout do `expect` (issue #338). Cada
# arquivo de spec ganha a conta própria, para desktop e celular: medido no Compose local, o maior
# consumo de um arquivo foi 51 leituras por minuto (journeys e theme), contra 120 da conta única.
# O resultado é o objeto JSON de GS_E2E_API_KEYS, com a chave de cada arquivo (variável SPEC_KEYS,
# não saída, para o `::add-mask::` chegar ao log do job). Uma ida só ao container, sem o custo de
# um `docker compose exec` por chave.
# Recebe o arquivo da spec ($1) e os escopos ($2); imprime "<arquivo> <chave>".
# shellcheck disable=SC2016 # expande no sh do container, não aqui
ISSUE_ACCOUNT='
user_id="$(gs-admin create-user --display-name "E2E $1")" || exit 255
key="$(gs-admin issue-key --user "$user_id" --days 1 --scopes "$2")" || exit 255
printf "%s %s\n" "$1" "$key"
'
SPEC_KEYS='{}'
issue_spec_keys() {
  local ids=() spec
  for spec in "$SPEC_DIR"/*.spec.ts; do
    ids+=("$(basename "$spec")")
  done
  local lines
  # Quatro em paralelo: cada `gs-admin` sobe um Python, e em série as contas levavam 1 min. Cada
  # linha sai de um único printf, curta o bastante para não se misturar com as dos vizinhos.
  lines="$(printf '%s\n' "${ids[@]}" |
    compose exec -T api xargs -P 4 -I{} sh -c "$ISSUE_ACCOUNT" _ {} "$E2E_KEY_SCOPES")"
  local id key
  while read -r id key; do
    echo "::add-mask::$key"
    SPEC_KEYS="$(jq --compact-output --arg id "$id" --arg key "$key" '. + {($id): $key}' <<<"$SPEC_KEYS")"
  done <<<"$lines"
  [[ "$(jq length <<<"$SPEC_KEYS")" -eq "${#ids[@]}" ]] || fail "faltou conta para alguma spec"
}

journeys() {
  require_ephemeral_runner
  local runtimes started_at status=0 events_pid recorder_pid
  issue_spec_keys

  runtimes="$(mktemp)"
  exec 3< <(docker events --filter type=container --filter event=start \
    --filter label=goblinshift.attempt --format '{{.ID}}')
  events_pid=$!
  record_runtimes <&3 >"$runtimes" &
  recorder_pid=$!
  exec 3<&-

  started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  (cd "$ROOT/web" && E2E_BASE_URL="$BASE_URL" GS_E2E_API_KEYS="$SPEC_KEYS" npm run e2e) || status=$?

  # O `docker events` caído antes da hora não mascara o resultado: o verify acusa a falta.
  kill "$events_pid" 2>/dev/null || true
  wait "$recorder_pid"
  # Antes do status das specs: com 429 no log, a causa aparece junto da falha, e não só o timeout.
  check_rate_limits "$started_at" || [[ $status -ne 0 ]] || status=1
  [[ $status -eq 0 ]] || exit "$status"
  verify "$runtimes"
}

# Lê o log do Nginx desde `since` e confere os 429. Falhar em ler o log, ou ler um log sem nenhuma
# linha de acesso (serviço com outro nome, log rodado, formato mudado), reprova: o detector cego
# deixaria o 429 voltar a parecer timeout.
check_rate_limits() {
  local since="$1" log
  log="$(mktemp)"
  if ! compose logs --no-color --since "$since" nginx >"$log" 2>&1; then
    echo "::error::não consegui ler o log do Nginx para conferir os 429 (compose logs falhou)." >&2
    head -5 "$log" >&2
    return 1
  fi
  rate_limits "$log"
}

# O Nginx registra todo 429, o dele (`limit_req`) e o da API (limite por conta). A SPA não repete
# 4xx, então o 429 aparece como elemento que não veio, e o teste morre num timeout de 5 s. Qualquer
# 429 nas specs é falha com a causa escrita.
rate_limits() {
  local log="$1" accesses total rc=0
  accesses="$(grep -c '"status":[0-9]' "$log")" || rc=$?
  if [[ $rc -gt 1 ]]; then
    echo "::error::não consegui ler o log do Nginx ($log)." >&2
    return 1
  fi
  if [[ "$accesses" -eq 0 ]]; then
    echo "::error::o log do Nginx não tem nenhuma linha de acesso: sem ela não há como conferir os 429." >&2
    return 1
  fi
  rc=0
  total="$(grep -c '"status":429' "$log")" || rc=$?
  if [[ $rc -gt 1 ]]; then
    echo "::error::não consegui ler o log do Nginx ($log)." >&2
    return 1
  fi
  if [[ "$total" -eq 0 ]]; then
    return 0
  fi
  echo "::error::$total resposta(s) 429 durante as specs: alguma conta passou do limite por minuto." >&2
  echo "Os timeouts das specs são só o sintoma. Caminhos e origem (upstream vazio: limit_req do Nginx):" >&2
  grep '"status":429' "$log" |
    sed -E 's/.*"path":"([^"]*)".*"upstream_status":"([^"]*)".*/\1 upstream=\2/' |
    sort | uniq -c | sort -rn | head -20 >&2
  return 1
}

attempts_in_database() {
  compose exec -T postgres psql -U goblinshift -d goblinshift -tAc 'SELECT count(*) FROM attempt'
}

verify() {
  local runtimes="$1" attempts recorded under_runsc
  attempts="$(attempts_in_database | tr -d '[:space:]')"
  recorded="$(wc -l <"$runtimes" | tr -d '[:space:]')"
  under_runsc="$(grep -cxF "$RUNSC" "$runtimes" || true)"
  echo "Tentativas no banco: $attempts; iniciadas: $recorded; sob $RUNSC: $under_runsc"
  [[ "$attempts" -gt 0 ]] || fail "nenhuma tentativa no banco: as jornadas não passaram pelo executor"
  [[ "$recorded" -eq "$attempts" && "$under_runsc" -eq "$attempts" ]] ||
    fail "nem toda tentativa rodou sob $RUNSC: $(sort "$runtimes" | uniq -c | tr -s ' ' | tr '\n' ';')"
}

logs() {
  local directory="$1"
  mkdir -p "$directory"
  compose logs --no-color >"$directory/compose.log" 2>&1 || true
  sudo journalctl --unit "$SERVICE" --no-pager 2>&1 | tee "$directory/executor-ctl.log" >/dev/null || true
}

case "${1:-}" in
  up) up ;;
  journeys) journeys ;;
  verify) verify "${2:?informe o arquivo com os runtimes}" ;;
  rate-limits) rate_limits "${2:?informe o arquivo com o log do Nginx}" ;;
  check-rate-limits) check_rate_limits "${2:?informe o instante inicial (RFC 3339)}" ;;
  logs) logs "${2:?informe o diretório de saída}" ;;
  *) fail "uso: $0 up|journeys|verify <arquivo>|rate-limits <arquivo>|check-rate-limits <desde>|logs <diretório>" ;;
esac
