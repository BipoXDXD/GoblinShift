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

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# GVISOR_SUITE, GVISOR_VERSION e GVISOR_KEY_FINGERPRINT: a mesma versão da CI.
# shellcheck source=deploy/gvisor-version.env
source "$here/gvisor-version.env"
# shellcheck source=deploy/apt-key.sh
source "$here/apt-key.sh"

KEYRING=/usr/share/keyrings/gvisor-archive-keyring.gpg
[[ $EUID -eq 0 ]] || { echo "rode com sudo" >&2; exit 1; }
note() { printf '==> %s\n' "$*"; }
has_runsc_runtime() { grep -q '"runsc"' <<<"$(docker info --format '{{json .Runtimes}}')"; }

# Anel conferido pela impressão digital e fonte na suíte fixada; vale também no host que já tem o
# runsc, para a troca de versão descrita abaixo achar o pacote.
configure_repo() {
  if [[ ! -f "$KEYRING" ]]; then
    key=$(mktemp)
    trap 'rm -f "$key"' EXIT
    curl -fsSL --connect-timeout 10 --max-time 60 --retry 3 https://gvisor.dev/archive.key -o "$key"
    verify_apt_key "$key" "$GVISOR_KEY_FINGERPRINT"
    gpg --batch --dearmor -o "$KEYRING" "$key"
  fi
  # Também o anel que já existia: só entra nas fontes a chave do gVisor.
  verify_apt_key "$KEYRING" "$GVISOR_KEY_FINGERPRINT"
  echo "deb [arch=$(dpkg --print-architecture) signed-by=${KEYRING}] https://storage.googleapis.com/gvisor/releases ${GVISOR_SUITE} main" \
    > /etc/apt/sources.list.d/gvisor.list
}

note "Pacote runsc ${GVISOR_VERSION} (repositório oficial do gVisor, suíte fixada)"
if ! dpkg -s runsc >/dev/null 2>&1; then
  # O pós-instalação do pacote já registra o runtime no daemon.json e recarrega o Docker.
  [[ -f /etc/docker/daemon.json ]] && cp -n /etc/docker/daemon.json /etc/docker/daemon.json.antes-do-gvisor
  export DEBIAN_FRONTEND=noninteractive
  apt-get install -yq curl gnupg
  configure_repo
  apt-get update -q
  apt-get install -yq "runsc=${GVISOR_VERSION}"
  # Segurado: um `apt upgrade` não troca o runtime do sandbox sem o teste de aceitação.
  apt-mark hold runsc
else
  # Trocar a versão é mudança no host, feita pelo dono: `apt-mark unhold runsc`,
  # `apt-get update`, `apt-get install runsc=<nova>`, `apt-mark hold runsc` e o teste de aceitação.
  configure_repo
  instalada=$(dpkg-query -W -f='${Version}' runsc)
  apt-mark hold runsc
  if [[ $instalada != "$GVISOR_VERSION" ]]; then
    echo "aviso: runsc instalado ${instalada}, diferente do fixado ${GVISOR_VERSION}; segurado como está." >&2
  else
    echo "   já instalado na versão fixada e segurado."
  fi
fi

note "Runtime runsc no Docker"
if has_runsc_runtime; then
  echo "   já registrado; nada alterado."
else
  runsc install
  systemctl reload docker
fi
has_runsc_runtime || { echo "o Docker não lista o runtime runsc" >&2; exit 1; }
runsc --version | sed -n 1p
