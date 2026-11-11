#!/usr/bin/env bash
# Instala o runsc no runner da CI e o registra no Docker. Preparo comum aos jobs da CI com gVisor.
set -euo pipefail

# Mesma versão da VPS (docs/adr/004-container-gvisor-por-tentativa.md). O repositório do gVisor
# publica cada release como uma suíte própria, então a versão fica fixada aqui.
GVISOR_SUITE="20261005"
GVISOR_VERSION="20261005.0"

# Repositório oficial do gVisor, a mesma origem de deploy/gvisor.sh.
keyring=/usr/share/keyrings/gvisor-archive-keyring.gpg
curl -fsSL --connect-timeout 10 --max-time 60 --retry 3 https://gvisor.dev/archive.key | sudo gpg --dearmor -o "$keyring"
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
