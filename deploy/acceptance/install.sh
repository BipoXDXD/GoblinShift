#!/usr/bin/env bash
# Instala o teste de aceitação diário como timer do systemd (fatia 8, F8-9).
# Idempotente e aditivo, como o deploy/backup/install.sh: não sobrescreve a configuração existente
# e só liga o timer quando ela está completa, porque sem a URL do healthchecks.io uma aceitação
# reprovada não avisaria ninguém.
#
# Uso: sudo bash deploy/acceptance/install.sh
set -euo pipefail

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG=/srv/goblinshift/secrets/acceptance.env
UNIT_DIR=/etc/systemd/system
TIMER=goblinshift-acceptance.timer
REQUIRED=(ACCEPTANCE_HEALTHCHECK_URL)
[[ $EUID -eq 0 ]] || { echo "rode com sudo" >&2; exit 1; }
note() { printf '==> %s\n' "$*"; }

note "Configuração ${CONFIG}"
if [[ ! -f "$CONFIG" ]]; then
  {
    echo "# Teste de aceitação diário; preencha e rode este instalador de novo."
    echo "# URL de ping do check \"aceitação diária\" do healthchecks.io (é segredo)."
    for name in "${REQUIRED[@]}"; do echo "${name}="; done
  } >"$CONFIG"
  # O serviço roda como root: ninguém mais precisa ler a URL.
  chown root:root "$CONFIG"
  chmod 600 "$CONFIG"
  echo "   criado vazio."
else
  echo "   já existe; mantido como está."
fi

note "Units em ${UNIT_DIR}"
install -m 644 "$SOURCE/goblinshift-acceptance.service" "$SOURCE/$TIMER" "$UNIT_DIR/"
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
