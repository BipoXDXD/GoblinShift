#!/usr/bin/env bash
# Preparação da VPS para o GoblinShift (docs/GoblinShiftPlan.md, 3.8). Idempotente.
#
# A VPS é compartilhada com outro projeto, então este script é ADITIVO: não substitui o que
# já existe, não reinicia o Docker com containers rodando e não remove regras de firewall.
#
# Uso, por um usuário com sudo:
#   CI_PUBKEY="ssh-ed25519 AAAA… goblinshift-ci" sudo --preserve-env=CI_PUBKEY bash provision.sh
set -euo pipefail

: "${CI_PUBKEY:?defina CI_PUBKEY (chave pública usada pela CI para o deploy)}"
REPO_URL="${REPO_URL:-https://github.com/BipoXDXD/GoblinShift.git}"
ROOT=/srv/goblinshift
EDGE=/srv/edge
APP_USER=goblinshift
# Chave primária "Docker Release (CE deb)", publicada na documentação oficial de instalação do
# Docker Engine no Ubuntu (docs.docker.com/engine/install/ubuntu); conferida em 27/11/2026.
DOCKER_KEY_FINGERPRINT=9DC858229FC7DD38854AE2D88D81803C0EBFCD88
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/apt-key.sh
source "$here/apt-key.sh"
[[ $EUID -eq 0 ]] || { echo "rode com sudo" >&2; exit 1; }
note() { printf '==> %s\n' "$*"; }

note "Pacotes (sem upgrade geral: atualizações ficam com o unattended-upgrades)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
# gnupg: o verify_apt_key (deploy/apt-key.sh) confere a chave do Docker antes de confiar nela.
apt-get install -yq ca-certificates curl git gnupg ufw unattended-upgrades openssl

note "Docker Engine"
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  docker_key=$(mktemp)
  trap 'rm -f "$docker_key"' EXIT
  curl -fsSL --connect-timeout 10 --max-time 60 --retry 3 https://download.docker.com/linux/ubuntu/gpg -o "$docker_key"
  verify_apt_key "$docker_key" "$DOCKER_KEY_FINGERPRINT"
  install -m 0644 "$docker_key" /etc/apt/keyrings/docker.asc
  rm -f "$docker_key"
  trap - EXIT
  # /etc/os-release só existe na VPS.
  # shellcheck disable=SC1091
  cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  apt-get update -q
  apt-get install -yq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
# A rotação de logs dos containers fica no Compose, por serviço (retenção em 1.7): o
# /etc/docker/daemon.json vale para todos os projetos da VPS e não é alterado aqui.

note "SSH só por chave e sem root (só altera se ainda não estiver assim)"
if sshd -T | grep -qx 'permitrootlogin no' && sshd -T | grep -qx 'passwordauthentication no'; then
  echo "   já endurecido; nada alterado."
else
  cat > /etc/ssh/sshd_config.d/10-hardening.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
  sshd -t && systemctl reload ssh
fi

note "Firewall (regras acrescentadas)"
# Portas publicadas pelo Docker contornam o ufw. Só o proxy de borda publica a 80 e a 443;
# o OpenObserve fica preso a 127.0.0.1.
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
if ufw status | grep -q '^Status: inactive'; then
  ufw default deny incoming >/dev/null
  ufw default allow outgoing >/dev/null
  ufw --force enable
  echo "   ufw ativado com SSH, 80 e 443. Outros serviços do host precisam das suas próprias regras."
fi

note "Usuário de sistema ${APP_USER} (só a chave da CI, presa ao deploy)"
id "$APP_USER" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "$APP_USER"
# Grupo docker = root no host, inclusive sobre o outro projeto da VPS: risco aceito no ADR 034
# (o deploy e o backup precisam do daemon; o Compose de produção passa pelo fitness test de lá).
usermod -aG docker "$APP_USER"
install -d -m 700 -o "$APP_USER" -g "$APP_USER" "/home/$APP_USER/.ssh"
# O deploy.sh está morto (a publicação é pelo Coolify) e só recusa; a chave não abre shell.
cat > "/home/$APP_USER/.ssh/authorized_keys" <<EOF
command="${ROOT}/app/deploy/deploy.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ${CI_PUBKEY}
EOF
chown "$APP_USER:$APP_USER" "/home/$APP_USER/.ssh/authorized_keys"
chmod 600 "/home/$APP_USER/.ssh/authorized_keys"

note "Filesystems de tamanho fixo (compartimentos de disco; área do executor sem exec)"
bash "$(dirname "${BASH_SOURCE[0]}")/filesystems.sh"

note "Segredos do GoblinShift (fora do repositório)"
install -d -m 700 -o "$APP_USER" -g "$APP_USER" "$ROOT/secrets"
bash "$here/provision-secrets.sh" "$ROOT/secrets"
chown "$APP_USER:$APP_USER" "$ROOT/secrets/db_password" "$ROOT/secrets/app.env"

note "Código"
if [[ ! -d "$ROOT/app/.git" ]]; then
  git clone --quiet "$REPO_URL" "$ROOT/app"
fi
chown -R "$APP_USER:$APP_USER" "$ROOT/app"

note "Proxy de borda do host (${EDGE})"
# Instalado uma vez a partir do repositório; depois disso pertence ao host, e as
# configurações de outros projetos em conf.d/ nunca são sobrescritas.
if [[ ! -d "$EDGE" ]]; then
  install -d -m 755 "$EDGE"
  cp -r "$ROOT/app/deploy/edge/." "$EDGE/"
fi
docker compose --project-directory "$EDGE" -f "$EDGE/compose.yaml" up -d

note "Sandbox do executor (gVisor)"
bash "$(dirname "${BASH_SOURCE[0]}")/gvisor.sh"

note "executor-ctl (serviço do host, único com acesso ao Docker)"
bash "$(dirname "${BASH_SOURCE[0]}")/executor-ctl/install.sh"

note "Atualizações de segurança automáticas"
dpkg-reconfigure -f noninteractive unattended-upgrades

echo
echo "Pronto. Configure no GitHub: variáveis VPS_HOST e VPS_USER=${APP_USER};"
echo "segredos VPS_SSH_KEY (chave privada da CI) e VPS_KNOWN_HOSTS (ssh-keyscan do host) no"
echo "environment production, com política de branches só main (não no nível do repositório)."
