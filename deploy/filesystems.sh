#!/usr/bin/env bash
# Filesystems de tamanho fixo do GoblinShift (docs/GoblinShiftPlan.md, 3.8; ADR 005). Idempotente;
# chamado pelo provision.sh, como root.
#
# A área do executor (exec-work) monta com nosuid,nodev,noexec (item 36b, D12): o que o sandbox
# escreve ali não vira binário, setuid nem dispositivo no host. O executor roda o Python e o FFmpeg
# da imagem, nunca um arquivo da área. Uma linha já presente no fstab não é reescrita: se faltar
# alguma opção, o script só avisa, e a troca no host é passo do operador (Tarefa 26).
#
# GS_ROOT e FSTAB existem para os testes (tests/test_host_scripts.py); na VPS valem os padrões.
set -euo pipefail

ROOT="${GS_ROOT:-/srv/goblinshift}"
FSTAB="${FSTAB:-/etc/fstab}"
BASE_OPTIONS=loop,noatime,nofail
declare -A sizes=([pg]=10G [storage]=24G [exec-work]=6G [telemetry]=10G)
declare -A extra_options=([exec-work]="nosuid,nodev,noexec")

# Opções de $1 que faltam em $2 (coluna do fstab), separadas por vírgula; vazio se nenhuma.
missing_options() {
  local wanted="$1" current="$2" option absent=()
  local IFS=,
  for option in $wanted; do
    [[ ",$current," == *",$option,"* ]] || absent+=("$option")
  done
  printf '%s' "${absent[*]}"
}

install -d -m 755 "$ROOT" "$ROOT/images"
for name in pg storage exec-work telemetry; do
  img="$ROOT/images/$name.img"
  mnt="$ROOT/$name"
  options="$BASE_OPTIONS${extra_options[$name]:+,${extra_options[$name]}}"
  if [[ ! -f "$img" ]]; then
    fallocate -l "${sizes[$name]}" "$img"
    mkfs.ext4 -q -m 0 -L "gs-$name" "$img"
  fi
  install -d "$mnt"
  current="$(awk -v mnt="$mnt" '$1 !~ /^#/ && $2 == mnt { print $4; exit }' "$FSTAB")"
  if [[ -z "$current" ]]; then
    echo "$img $mnt ext4 $options 0 2" >> "$FSTAB"
  else
    missing="$(missing_options "$options" "$current")"
    if [[ -n "$missing" ]]; then
      echo "aviso: $mnt já está em $FSTAB sem $missing; linha mantida (Tarefa 26, operador)" >&2
    fi
  fi
  mountpoint -q "$mnt" || mount "$mnt"
done
install -d "$ROOT/pg/data" "$ROOT/telemetry/data"
