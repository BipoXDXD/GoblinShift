#!/usr/bin/env bash
# Instala o teste de aceitação diário como timer do systemd (fatia 8, F8-9).
# Idempotente e aditivo, como o deploy/backup/install.sh: não sobrescreve a configuração existente
# e só liga o timer quando ela está completa, porque sem a URL do healthchecks.io uma aceitação
# reprovada não avisaria ninguém.
#
# O serviço roda como root. Por isso nada do que ele executa ou lê fica em caminho que o usuário
# goblinshift grava (o checkout e /srv/goblinshift/secrets são dele, deploy/provision.sh): o código
# é copiado do checkout para PREFIX e a configuração mora em /etc/goblinshift. Uma mudança na
# aceitação só chega ao timer quando este instalador roda de novo, como no executor-ctl.
# A cópia lê o checkout com os privilégios do goblinshift: um symlink plantado ali não leva para
# PREFIX nem para as units um arquivo que só o root lê.
#
# Uso: sudo bash deploy/acceptance/install.sh
set -euo pipefail

APP_USER=goblinshift
DEPLOY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX=/usr/local/lib/goblinshift
CONFIG_DIR=/etc/goblinshift
CONFIG=/etc/goblinshift/acceptance.env
UNIT_DIR=/etc/systemd/system
TIMER=goblinshift-acceptance.timer
REQUIRED=(ACCEPTANCE_HEALTHCHECK_URL)
[[ $EUID -eq 0 ]] || { echo "rode com sudo" >&2; exit 1; }
note() { printf '==> %s\n' "$*"; }
as_app() { (cd / && runuser -u "$APP_USER" -- "$@"); }
if ! id "$APP_USER" >/dev/null 2>&1; then
  echo "usuário $APP_USER não existe; rode o provision.sh antes" >&2
  exit 1
fi

# require_root_dir <diretório>: ele e cada ancestral são diretórios de verdade (não symlinks), do
# root e sem escrita para grupo ou outros. Quem grava num deles troca o que o root executa ou lê.
require_root_dir() {
  local dir="$1" owner mode
  while :; do
    if [[ -L "$dir" || ! -d "$dir" ]]; then
      echo "recusado: $dir não é um diretório (ou é symlink)" >&2
      exit 1
    fi
    read -r owner mode < <(stat -c '%u %a' "$dir")
    if [[ "$owner" != 0 ]] || (((8#$mode & 8#022) != 0)); then
      echo "recusado: $dir precisa ser do root e sem escrita para grupo ou outros" >&2
      exit 1
    fi
    [[ "$dir" != / ]] || return 0
    dir="$(dirname "$dir")"
  done
}

# copy_from_checkout <modo> <destino> <arquivo...>: o conteúdo é lido pelo goblinshift, dono do
# checkout, então um symlink no arquivo ou num diretório do caminho só alcança o que ele já lê. O
# destino é aberto pelo root, num diretório que só o root grava.
copy_from_checkout() {
  local mode="$1" dest="$2" src
  shift 2
  for src in "$@"; do
    if [[ -L "$src" || ! -f "$src" ]]; then
      echo "recusado: $src não é um arquivo comum (ou é symlink)" >&2
      exit 1
    fi
    as_app cat -- "$src" >"$dest/${src##*/}"
    chmod "$mode" "$dest/${src##*/}"
  done
}

# root_dir <diretório>: cria (root, 755) se faltar e confere com require_root_dir.
root_dir() {
  require_root_dir "$(dirname "$1")"
  [[ -e "$1" || -L "$1" ]] || install -d -m 755 -o root -g root "$1"
  require_root_dir "$1"
}

note "Código em ${PREFIX}"
root_dir "$PREFIX"
install -d -m 755 -o root -g root "$PREFIX/docker-config"
staging="$(mktemp -d "$PREFIX/.novo.XXXXXX")"
trap 'rm -rf "$staging"' EXIT
install -d -m 755 "$staging/acceptance" "$staging/executor_ctl" "$staging/backup" "$staging/units"
copy_from_checkout 644 "$staging/acceptance" "$DEPLOY"/acceptance/*.py
copy_from_checkout 755 "$staging/acceptance" "$DEPLOY/acceptance/scheduled.sh"
copy_from_checkout 644 "$staging/executor_ctl" "$DEPLOY"/executor_ctl/*.py
copy_from_checkout 644 "$staging/backup" "$DEPLOY/backup/lib.sh"
copy_from_checkout 644 "$staging/units" "$DEPLOY/acceptance/goblinshift-acceptance.service" \
  "$DEPLOY/acceptance/$TIMER"
for package in acceptance executor_ctl backup; do
  rm -rf "${PREFIX:?}/$package"
  mv "$staging/$package" "$PREFIX/$package"
done
echo "   copiado do checkout."

note "Configuração ${CONFIG}"
root_dir "$CONFIG_DIR"
if [[ ! -e "$CONFIG" && ! -L "$CONFIG" ]]; then
  # umask e noclobber: o arquivo nasce 600 e o bash não escreve por cima de nada que surja ali.
  (
    umask 077
    set -o noclobber
    {
      echo "# Teste de aceitação diário; preencha e rode este instalador de novo."
      echo "# URL de ping do check \"aceitação diária\" do healthchecks.io (é segredo)."
      for name in "${REQUIRED[@]}"; do echo "${name}="; done
    } >"$CONFIG"
  )
  echo "   criado vazio."
elif [[ -L "$CONFIG" || ! -f "$CONFIG" ]]; then
  echo "recusado: $CONFIG não é um arquivo comum" >&2
  exit 1
else
  echo "   já existe; mantido como está."
fi
# O serviço roda como root: ninguém mais precisa ler a URL.
chown root:root "$CONFIG"
chmod 600 "$CONFIG"

note "Units em ${UNIT_DIR}"
require_root_dir "$UNIT_DIR"
install -m 644 "$staging/units/goblinshift-acceptance.service" "$staging/units/$TIMER" "$UNIT_DIR/"
systemctl daemon-reload

missing=()
for name in "${REQUIRED[@]}"; do
  grep -qE "^${name}=.+" "$CONFIG" || missing+=("$name")
done
if ((${#missing[@]})); then
  echo "   timer não ligado: falta ${missing[*]} em $CONFIG" >&2
  exit 1
fi
systemctl enable --now "$TIMER"
echo "   timer ligado; teste já com: sudo systemctl start goblinshift-acceptance.service"
