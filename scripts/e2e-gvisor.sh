#!/usr/bin/env bash
# Playwright contra a API real com cada tentativa num container gVisor, pelo executor-ctl do host.
# Roda só no runner efêmero da CI: grava em /etc, /var/lib e /srv e instala um serviço systemd.
#
#   scripts/e2e-gvisor.sh up                 sobe executor-ctl e pilha (exige runsc no Docker)
#   scripts/e2e-gvisor.sh journeys           cria conta e chave, roda as specs e confere o runtime
#   scripts/e2e-gvisor.sh verify <arquivo>   confere o runtime gravado contra o banco
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

journeys() {
  require_ephemeral_runner
  local user_id key runtimes status=0 events_pid recorder_pid
  user_id="$(compose exec -T api gs-admin create-user --display-name "E2E gVisor")"
  key="$(compose exec -T api gs-admin issue-key --user "$user_id" --days 1 \
    --scopes files:read,files:write,runs:read,runs:write,presets:read,presets:write)"
  echo "::add-mask::$key"

  runtimes="$(mktemp)"
  exec 3< <(docker events --filter type=container --filter event=start \
    --filter label=goblinshift.attempt --format '{{.ID}}')
  events_pid=$!
  record_runtimes <&3 >"$runtimes" &
  recorder_pid=$!
  exec 3<&-

  (cd "$ROOT/web" && E2E_BASE_URL="$BASE_URL" GS_E2E_API_KEY="$key" npm run e2e) || status=$?

  # O `docker events` caído antes da hora não mascara o resultado: o verify acusa a falta.
  kill "$events_pid" 2>/dev/null || true
  wait "$recorder_pid"
  [[ $status -eq 0 ]] || exit "$status"
  verify "$runtimes"
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
  logs) logs "${2:?informe o diretório de saída}" ;;
  *) fail "uso: $0 up|journeys|verify <arquivo>|logs <diretório>" ;;
esac
