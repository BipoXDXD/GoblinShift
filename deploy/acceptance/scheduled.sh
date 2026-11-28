#!/usr/bin/env bash
# Teste de aceitação do ambiente, agendado (docs/GoblinShiftPlan.md, 3.7 e 3.8; fatia 8, F8-9).
# Roda pelo goblinshift-acceptance.timer, como root, o mesmo `python3 -B -m acceptance.run` do
# README, e pinga o check "aceitação semanal" do healthchecks.io: /start, sucesso ou /fail. Sem o
# ping de sucesso no prazo, o próprio healthchecks.io avisa (aceitação reprovada ou que não rodou).
#
# O worker fica parado durante o teste: as sondas enchem a área de trabalho, que só admite uma
# tentativa por vez (README, "Teste de aceitação do ambiente"). Ele volta no fim, também quando
# a aceitação reprova ou o serviço recebe SIGTERM (parada, reinício da máquina ou TimeoutStartSec:
# container parado por `docker stop` não volta com `restart: unless-stopped`). O estado é relido
# depois do `start`; se ele não voltar, o resultado é /fail. Só o SIGKILL não deixa religar. A
# manutenção, o próprio run.py suspende e religa (ADR 017).
#
# Antes de tudo, o script toma a trava da publicação do projeto (a release.lock que o receptor,
# deploy/coolify/release/receiver.py, toma com LOCK_NB) e a segura até o fim: um merge na janela
# recriaria worker e manutenção no meio das sondas, e a manutenção nova apagaria as sondas. Espera
# até 15 min pela publicação em curso (D14); ocupada ou ausente depois disso, pinga /fail e sai com
# 75 sem tocar em nada. A publicação que chega durante o teste falha na hora e precisa ser rodada
# de novo depois da janela (README, "Aceitação semanal, agendada"). A trava não é a host.lock,
# que barraria por até 1 h a publicação dos outros projetos do host.
#
# Roda da cópia que o deploy/acceptance/install.sh faz em /usr/local/lib/goblinshift (do root), nunca
# do checkout, que pertence ao usuário goblinshift; o lib.sh e o pacote vêm da mesma cópia.
#
# Variável (EnvironmentFile da unit, /etc/goblinshift/acceptance.env):
#   ACCEPTANCE_HEALTHCHECK_URL   URL de ping do check; é segredo e vai pelo stdin do curl
#   ACCEPTANCE_RELEASE_LOCK      opcional; a trava da publicação (padrão abaixo, só para o teste)
#   ACCEPTANCE_LOCK_WAIT_S       opcional; espera pela trava, 900 s (só para o teste)
#
# Uso: sudo systemctl start goblinshift-acceptance.service
set -euo pipefail

DEPLOY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../backup/lib.sh
source "$DEPLOY/backup/lib.sh"
require_env ACCEPTANCE_HEALTHCHECK_URL

APP_PROJECT=goblinshift
# O stop_grace_period do worker no compose.yaml. A tentativa em curso que não terminar nesse
# prazo leva SIGKILL e volta pelo lease vencido.
WORKER_STOP_S=30
SIGTERM_EXIT=143
RELEASE_LOCK="${ACCEPTANCE_RELEASE_LOCK:-/var/lib/coolify-release/${APP_PROJECT}/release.lock}"
LOCK_WAIT_S="${ACCEPTANCE_LOCK_WAIT_S:-900}"
# EX_TEMPFAIL: a aceitação não rodou, mas nada foi tocado.
LOCK_BUSY_EXIT=75

# restart_workers: religa e confere; sai com erro se algum não ficou rodando. Interrompido, o
# cliente do `docker stop` pode ter morrido com a parada ainda em curso no daemon. O `start` nessa
# hora sai com 0 sem mudar nada, e o worker para em seguida: o `stop` repetido espera a parada.
restart_workers() {
  local states state
  if ((interrupted)); then
    docker stop --time="$WORKER_STOP_S" "${workers[@]}" >/dev/null || return 1
  fi
  docker start "${workers[@]}" >/dev/null || return 1
  states="$(docker inspect --type=container --format='{{.State.Running}}' "${workers[@]}")" ||
    return 1
  [[ -n "$states" ]] || return 1
  for state in $states; do
    [[ "$state" == true ]] || return 1
  done
}

# Somente leitura: o flock vale em qualquer modo, e a unit tem ProtectSystem=strict. O descritor 9
# fica aberto até o fim do script, e com ele a trava.
if ! { exec 9<"$RELEASE_LOCK"; } 2>/dev/null || ! flock -w "$LOCK_WAIT_S" 9; then
  echo "a trava da publicação ($RELEASE_LOCK) está ocupada ou ausente depois de ${LOCK_WAIT_S} s;" \
    "a aceitação não rodou e o worker não foi tocado" >&2
  healthcheck_ping "$ACCEPTANCE_HEALTHCHECK_URL" "/fail"
  exit "$LOCK_BUSY_EXIT"
fi

healthcheck_ping "$ACCEPTANCE_HEALTHCHECK_URL" "/start"

# O SIGTERM do systemd vai ao cgroup inteiro: o `docker` e o python em curso morrem ou limpam por
# conta própria, e este bash, com o trap, só anota e segue até religar o worker e pingar /fail.
interrupted=0
trap 'interrupted=1' TERM INT

status=0
# Em variável, e não em `< <(...)`: a substituição de processo esconde a falha do `docker ps`, e
# uma lista vazia por erro pareceria worker parado.
running="$(docker ps --quiet \
  --filter="label=com.docker.compose.project=${APP_PROJECT}" \
  --filter="label=com.docker.compose.service=worker")" || status=$?
mapfile -t workers < <(printf '%s' "$running")

if ((${#workers[@]})); then
  echo "worker parado durante o teste; se este script morrer por SIGKILL, religue com:" \
    "docker start ${workers[*]}"
  docker stop --time="$WORKER_STOP_S" "${workers[@]}" >/dev/null || status=$?
fi
if ((status == 0 && interrupted == 0)); then
  # -E e -s: sem variáveis PYTHON* nem site do usuário; o pacote vem só do cwd, que é do root.
  (cd "$DEPLOY" && python3 -E -s -B -m acceptance.run) || status=$?
fi
if ((${#workers[@]})); then
  # O sinal no meio da volta mata o cliente em curso: repete, agora sabendo da interrupção.
  restart_workers || { ((interrupted)) && restart_workers; } || {
    echo "o worker não voltou; religue com: docker start ${workers[*]}" >&2
    ((status != 0)) || status=1
  }
fi
((interrupted == 0 || status != 0)) || status=$SIGTERM_EXIT

if ((status == 0)); then
  healthcheck_ping "$ACCEPTANCE_HEALTHCHECK_URL" ""
else
  healthcheck_ping "$ACCEPTANCE_HEALTHCHECK_URL" "/fail"
fi
exit "$status"
