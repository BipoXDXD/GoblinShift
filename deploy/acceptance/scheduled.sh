#!/usr/bin/env bash
# Teste de aceitação do ambiente, agendado (docs/GoblinShiftPlan.md, 3.7 e 3.8; fatia 8, F8-9).
# Roda pelo goblinshift-acceptance.timer, como root, o mesmo `python3 -B -m acceptance.run` do
# README, e pinga o check "aceitação diária" do healthchecks.io: /start, sucesso ou /fail. Sem o
# ping de sucesso no prazo, o próprio healthchecks.io avisa (aceitação reprovada ou que não rodou).
#
# O worker fica parado durante o teste: as sondas enchem a área de trabalho, que só admite uma
# tentativa por vez (README, "Teste de aceitação do ambiente"). Ele volta no fim, também quando
# a aceitação reprova; se não voltar, o resultado é /fail. A manutenção, o próprio run.py
# suspende e religa (ADR 017).
#
# Roda da cópia que o deploy/acceptance/install.sh faz em /usr/local/lib/goblinshift (do root), nunca
# do checkout, que pertence ao usuário goblinshift; o lib.sh e o pacote vêm da mesma cópia.
#
# Variável (EnvironmentFile da unit, /etc/goblinshift/acceptance.env):
#   ACCEPTANCE_HEALTHCHECK_URL   URL de ping do check; é segredo e vai pelo stdin do curl
#
# Uso: sudo systemctl start goblinshift-acceptance.service
set -euo pipefail

DEPLOY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../backup/lib.sh
source "$DEPLOY/backup/lib.sh"
require_env ACCEPTANCE_HEALTHCHECK_URL

APP_PROJECT=goblinshift
# O stop_grace_period do worker no compose.yaml: tempo para a tentativa em curso encerrar.
WORKER_STOP_S=30

healthcheck_ping "$ACCEPTANCE_HEALTHCHECK_URL" "/start"

mapfile -t workers < <(docker ps --quiet \
  --filter="label=com.docker.compose.project=${APP_PROJECT}" \
  --filter="label=com.docker.compose.service=worker")

status=0
if ((${#workers[@]})); then
  echo "worker parado durante o teste; se ele for interrompido à força, religue com:" \
    "docker start ${workers[*]}"
  docker stop --time="$WORKER_STOP_S" "${workers[@]}" >/dev/null || status=$?
fi
if ((status == 0)); then
  # -E e -s: sem variáveis PYTHON* nem site do usuário; o pacote vem só do cwd, que é do root.
  (cd "$DEPLOY" && python3 -E -s -B -m acceptance.run) || status=$?
fi
if ((${#workers[@]})); then
  docker start "${workers[@]}" >/dev/null || {
    echo "o worker não voltou; religue com: docker start ${workers[*]}" >&2
    ((status != 0)) || status=1
  }
fi

if ((status == 0)); then
  healthcheck_ping "$ACCEPTANCE_HEALTHCHECK_URL" ""
else
  healthcheck_ping "$ACCEPTANCE_HEALTHCHECK_URL" "/fail"
fi
exit "$status"
