#!/usr/bin/env bash
# Instala o runsc no runner da CI e o registra no Docker. Preparo comum aos jobs da CI com gVisor.
set -euo pipefail

# Mesma versão e mesma chave da VPS (docs/adr/004-container-gvisor-por-tentativa.md), lidas de
# deploy/gvisor-version.env, que o deploy/gvisor.sh também lê.
raiz="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=deploy/gvisor-version.env
source "$raiz/deploy/gvisor-version.env"
# shellcheck source=deploy/apt-key.sh
source "$raiz/deploy/apt-key.sh"

# Repositório oficial do gVisor, a mesma origem de deploy/gvisor.sh; a chave só vira anel do apt
# depois de conferida pela impressão digital.
keyring=/usr/share/keyrings/gvisor-archive-keyring.gpg
key=$(mktemp)
trap 'rm -f "$key"' EXIT
curl -fsSL --connect-timeout 10 --max-time 60 --retry 3 https://gvisor.dev/archive.key -o "$key"
verify_apt_key "$key" "$GVISOR_KEY_FINGERPRINT"
sudo gpg --batch --yes --dearmor -o "$keyring" "$key"
echo "deb [arch=$(dpkg --print-architecture) signed-by=${keyring}] https://storage.googleapis.com/gvisor/releases ${GVISOR_SUITE} main" |
  sudo tee /etc/apt/sources.list.d/gvisor.list >/dev/null
# O `update` lê só a fonte do gVisor: os espelhos do Ubuntu, que já prenderam este passo por 50 min,
# ficam de fora, e as listas da imagem seguem valendo.
apt_rede=(-o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 -o Acquire::Retries=3)
sudo apt-get update -q "${apt_rede[@]}" -o Dir::Etc::sourcelist=sources.list.d/gvisor.list -o Dir::Etc::sourceparts=- -o APT::Get::List-Cleanup=0
sudo apt-get install -yq "${apt_rede[@]}" "runsc=${GVISOR_VERSION}"
runsc --version | head -n 1

# O runner é efêmero e só roda este job, então reiniciar o Docker aqui não derruba nada.
# Na VPS isso nunca se faz: lá o Docker é só recarregado (deploy/gvisor.sh), porque o host é
# compartilhado com outro projeto.
if ! docker info --format '{{json .Runtimes}}' | grep -q '"runsc"'; then
  sudo runsc install
fi
sudo systemctl restart docker
docker info --format '{{json .Runtimes}}' | grep -q '"runsc"'
