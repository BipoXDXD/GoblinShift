#!/usr/bin/env bash
# Instala o backup diário do PostgreSQL como timer do systemd (docs/adr/022-backup-externo.md).
# Idempotente e aditivo, como o deploy/executor-ctl/install.sh: não sobrescreve a configuração
# existente e só liga o timer quando ela está completa, porque um backup sem a URL do
# healthchecks.io falharia em silêncio.
#
# O timer não depende de checkout nenhum: o pg-backup.sh e o que ele usa em tempo de execução
# (lib.sh e age-recipients.txt; o dump sai por `docker exec`, sem Compose) são copiados para PREFIX,
# do root, e a unit executa dali. Uma mudança no backup ou uma chave age nova só chega ao timer
# quando este instalador roda de novo. A unit só é trocada depois de o código estar completo no
# destino: uma falha no meio deixa a unit anterior funcionando.
#
# O backup.env mora em /srv/goblinshift/secrets, que pertence ao usuário da aplicação: tudo o que o
# instalador faz ali roda com os privilégios dele (as_app), nunca como root. Como root, um symlink
# plantado no lugar do arquivo levaria a escrita, o chown e o chmod para qualquer arquivo do sistema.
# Pelo mesmo motivo o checkout é lido com os privilégios dele: rode de um checkout que ele consiga
# ler (README, "Checkout temporário na VPS"), nunca da cópia parada em /srv/goblinshift/app.
#
# Uso: sudo bash <checkout>/deploy/backup/install.sh
set -euo pipefail

APP_USER=goblinshift
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX=/usr/local/lib/goblinshift/backup
CONFIG=/srv/goblinshift/secrets/backup.env
UNIT_DIR=/etc/systemd/system
TIMER=goblinshift-pg-backup.timer
REQUIRED=(BACKUP_S3_ENDPOINT BACKUP_S3_REGION BACKUP_S3_BUCKET BACKUP_S3_KEY_ID BACKUP_S3_SECRET
  BACKUP_HEALTHCHECK_URL)
[[ $EUID -eq 0 ]] || { echo "rode com sudo" >&2; exit 1; }
note() { printf '==> %s\n' "$*"; }
as_app() { (cd / && runuser -u "$APP_USER" -- "$@"); }
if ! id "$APP_USER" >/dev/null 2>&1; then
  echo "usuário $APP_USER não existe; rode o provision.sh antes" >&2
  exit 1
fi

# require_root_dir <diretório>: ele e cada ancestral são diretórios de verdade (não symlinks), do
# root e sem escrita para grupo ou outros. Quem grava num deles troca o que o timer executa.
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

# root_dir <diretório>: cria (root, 755) se faltar e confere com require_root_dir.
root_dir() {
  require_root_dir "$(dirname "$1")"
  [[ -e "$1" || -L "$1" ]] || install -d -m 755 -o root -g root "$1"
  require_root_dir "$1"
}

# create_as_app <arquivo>: grava o stdin num arquivo novo, 600, como o usuário da aplicação. O
# noclobber cria com O_EXCL: falha se o caminho já existir, mesmo como symlink.
create_as_app() {
  # shellcheck disable=SC2016 # o $1 é expandido pelo bash do goblinshift, não por este.
  as_app bash -c 'umask 077 && set -o noclobber && cat >"$1"' _ "$1"
}
# copy_from_checkout <modo> <destino> <arquivo...>: o conteúdo é lido pelo usuário da aplicação,
# então um symlink no checkout só alcança o que ele já lê; o destino é aberto pelo root, num
# diretório que só o root grava.
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

note "Ferramentas (age e curl com SigV4)"
command -v age >/dev/null || apt-get install -yq age
command -v curl >/dev/null || apt-get install -yq curl

note "Código em ${PREFIX}"
root_dir "$(dirname "$PREFIX")"
staging="$(mktemp -d "$(dirname "$PREFIX")/.novo-backup.XXXXXX")"
trap 'rm -rf "$staging"' EXIT
install -d -m 755 "$staging/code" "$staging/units"
copy_from_checkout 755 "$staging/code" "$SOURCE/pg-backup.sh"
copy_from_checkout 644 "$staging/code" "$SOURCE/lib.sh" "$SOURCE/age-recipients.txt"
copy_from_checkout 644 "$staging/units" "$SOURCE/goblinshift-pg-backup.service" "$SOURCE/$TIMER"
root_dir "$PREFIX"
# Arquivo por arquivo, e não o diretório inteiro: o lib.sh é dividido com a aceitação
# (deploy/acceptance/install.sh). O rename é atômico, e o pg-backup.sh, que depende dos outros, vem
# por último. Tudo antes de instalar a unit, que nunca aponta para arquivo que falta.
for file in lib.sh age-recipients.txt pg-backup.sh; do
  mv -f "$staging/code/$file" "$PREFIX/$file"
done
echo "   copiado do checkout."

note "Configuração ${CONFIG}"
if ! as_app test -e "$CONFIG"; then
  {
    echo "# Backup externo do PostgreSQL; preencha e rode este instalador de novo."
    echo "# Chave de aplicação do B2 SÓ DE ESCRITA (writeFiles, sem deleteFiles nem readFiles)."
    for name in "${REQUIRED[@]}"; do echo "${name}="; done
  } | create_as_app "$CONFIG"
  echo "   criado vazio."
else
  echo "   já existe; mantido como está."
fi

note "Units em ${UNIT_DIR}"
require_root_dir "$UNIT_DIR"
install -m 644 "$staging/units/goblinshift-pg-backup.service" "$staging/units/$TIMER" "$UNIT_DIR/"
systemctl daemon-reload

missing=()
for name in "${REQUIRED[@]}"; do
  as_app grep -qE "^${name}=.+" "$CONFIG" || missing+=("$name")
done
if ((${#missing[@]})); then
  echo "   timer não ligado: falta ${missing[*]} em $CONFIG" >&2
  exit 1
fi
systemctl enable --now "$TIMER"
echo "   timer ligado; teste já com: sudo systemctl start goblinshift-pg-backup.service"
