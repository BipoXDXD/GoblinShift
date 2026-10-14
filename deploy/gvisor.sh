#!/usr/bin/env bash
# Instala o gVisor (runsc) e o registra no Docker (docs/GoblinShiftPlan.md, 3.6 e 3.8; ADR 004).
# Idempotente e aditivo, como o provision.sh, que chama este script.
#
# A VPS é compartilhada com outro projeto: o daemon.json existente é preservado (o `runsc install`
# só acrescenta o runtime) e o Docker é recarregado, não reiniciado, então nenhum container cai.
#
# Uso: sudo bash deploy/gvisor.sh
# Depois de instalar ou atualizar o runsc, rode o teste de aceitação
# (cd deploy && sudo python3 -B -m acceptance.run).
set -euo pipefail

KEYRING=/usr/share/keyrings/gvisor-archive-keyring.gpg
[[ $EUID -eq 0 ]] || { echo "rode com sudo" >&2; exit 1; }
note() { printf '==> %s\n' "$*"; }
has_runsc_runtime() { docker info --format '{{json .Runtimes}}' | grep -q '"runsc"'; }

note "Pacote runsc (repositório oficial do gVisor)"
if ! dpkg -s runsc >/dev/null 2>&1; then
  # O pós-instalação do pacote já registra o runtime no daemon.json e recarrega o Docker.
  [[ -f /etc/docker/daemon.json ]] && cp -n /etc/docker/daemon.json /etc/docker/daemon.json.antes-do-gvisor
  export DEBIAN_FRONTEND=noninteractive
  apt-get install -yq curl gnupg
  [[ -f "$KEYRING" ]] || curl -fsSL https://gvisor.dev/archive.key | gpg --dearmor -o "$KEYRING"
  echo "deb [arch=$(dpkg --print-architecture) signed-by=${KEYRING}] https://storage.googleapis.com/gvisor/releases release main" \
    > /etc/apt/sources.list.d/gvisor.list
  apt-get update -q
  apt-get install -yq runsc
else
  echo "   já instalado; atualizar o runsc é mudança no host e pede o teste de aceitação."
fi

note "Runtime runsc no Docker"
if has_runsc_runtime; then
  echo "   já registrado; nada alterado."
else
  runsc install
  systemctl reload docker
fi
has_runsc_runtime || { echo "o Docker não lista o runtime runsc" >&2; exit 1; }
runsc --version | head -n 1
