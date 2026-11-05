#!/usr/bin/env bash
# Sentinela de montagem do storage (docs/GoblinShiftPlan.md, 1.7 e 3.8; files/storage.py,
# MOUNT_MARKER). Sem ela a manutenção não marca objeto nenhum como `missing`. O storage é um
# filesystem próprio montado com `nofail`: desmontado, a pasta seria recriada no disco do sistema
# e todos os objetos pareceriam ausentes. Por isso a sentinela só é gravada depois de conferir a
# montagem, e nunca por um `up` (o serviço `storage-mark` tem perfil próprio).
#
# PERIGO: o modo sem argumento roda `compose.sh run`. Na VPS, não o use a partir da cópia parada em
# /srv/goblinshift/app (deploy/compose.sh explica o risco); só o deploy legado o chamava.
#
# Uso: deploy/storage-mark.sh        confere e grava a sentinela
#      deploy/storage-mark.sh check  só confere a montagem
set -euo pipefail

storage=/srv/goblinshift/storage
if ! mountpoint -q "$storage"; then
  echo "storage não montado em $storage" >&2
  exit 1
fi
[[ "${1:-}" == check ]] && exit 0
"$(dirname "${BASH_SOURCE[0]}")/compose.sh" run --rm --no-TTY storage-mark
