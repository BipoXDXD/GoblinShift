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
[[ $EUID -eq 0 ]] || { echo "rode com sudo" >&2; exit 1; }
note() { printf '==> %s\n' "$*"; }

note "Pacotes (sem upgrade geral: atualizações ficam com o unattended-upgrades)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -yq ca-certificates curl git ufw unattended-upgrades openssl

note "Docker Engine"
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
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
if [[ ! -f /etc/docker/daemon.json ]]; then
  # Rotação de logs dos containers (retenção em 1.7).
  echo '{ "log-driver": "local", "log-opts": { "max-size": "20m", "max-file": "5" } }' > /etc/docker/daemon.json
  if [[ -z "$(docker ps -q)" ]]; then
    systemctl restart docker
  else
    echo "   daemon.json criado; há containers rodando, então o Docker NÃO foi reiniciado."
    echo "   A rotação vale após o próximo reinício planejado do Docker."
  fi
else
  echo "   /etc/docker/daemon.json já existe; mantido como está."
fi

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
# Portas publicadas pelo Docker contornam o ufw. Só o proxy de borda publica a 80;
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
usermod -aG docker "$APP_USER"
install -d -m 700 -o "$APP_USER" -g "$APP_USER" "/home/$APP_USER/.ssh"
cat > "/home/$APP_USER/.ssh/authorized_keys" <<EOF
command="${ROOT}/app/deploy/deploy.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ${CI_PUBKEY}
EOF
chown "$APP_USER:$APP_USER" "/home/$APP_USER/.ssh/authorized_keys"
chmod 600 "/home/$APP_USER/.ssh/authorized_keys"

note "Filesystems de tamanho fixo (compartimentos de disco)"
install -d -m 755 "$ROOT" "$ROOT/images"
declare -A sizes=([pg]=10G [storage]=24G [exec-work]=6G [telemetry]=10G)
for name in pg storage exec-work telemetry; do
  img="$ROOT/images/$name.img"
  mnt="$ROOT/$name"
  if [[ ! -f "$img" ]]; then
    fallocate -l "${sizes[$name]}" "$img"
    mkfs.ext4 -q -m 0 -L "gs-$name" "$img"
  fi
  install -d "$mnt"
  grep -q " $mnt " /etc/fstab || echo "$img $mnt ext4 loop,noatime,nofail 0 2" >> /etc/fstab
  mountpoint -q "$mnt" || mount "$mnt"
done
install -d "$ROOT/pg/data" "$ROOT/telemetry/data"

note "Segredos do GoblinShift (fora do repositório)"
install -d -m 700 -o "$APP_USER" -g "$APP_USER" "$ROOT/secrets"
[[ -f "$ROOT/secrets/db_password" ]] || openssl rand -hex 32 > "$ROOT/secrets/db_password"
# Lido dentro dos containers por outros usuários; protegido pelo diretório 700.
chmod 644 "$ROOT/secrets/db_password"
if [[ ! -f "$ROOT/secrets/app.env" ]]; then
  email="admin@goblinshift.local"
  password="$(openssl rand -base64 30 | tr -dc 'A-Za-z0-9' | head -c 28)Aa1_"
  {
    echo "OPENOBSERVE_ROOT_EMAIL=${email}"
    echo "OPENOBSERVE_ROOT_PASSWORD=${password}"
    echo "OPENOBSERVE_BASIC_AUTH=$(printf '%s:%s' "$email" "$password" | base64 -w0)"
  } > "$ROOT/secrets/app.env"
  chmod 600 "$ROOT/secrets/app.env"
fi
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

note "Atualizações de segurança automáticas"
dpkg-reconfigure -f noninteractive unattended-upgrades

echo
echo "Pronto. Configure no GitHub: variáveis VPS_HOST e VPS_USER=${APP_USER};"
echo "segredos VPS_SSH_KEY (chave privada da CI) e VPS_KNOWN_HOSTS (ssh-keyscan do host)."
