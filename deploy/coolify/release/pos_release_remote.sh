#!/usr/bin/env bash
# Read-only half of pos_release.sh that runs on the VPS (sent over ssh on stdin).
# Usage: bash -s -- <full merge sha> [root] — root only prefixes the paths, for the local tests.
# Emits `KEY<TAB>value`; a fact that cannot be read is not emitted. Never writes, moves,
# deletes, restarts or takes a lock: locks are read from /proc/locks by inode, because taking
# them, even with LOCK_NB, could make a real release lose the race.
set -uo pipefail
M=${1:-}
[[ $M =~ ^[0-9a-f]{40}$ ]] || exit 2
ROOT=${2:-}
R=$ROOT/var/lib/coolify-release
G=$R/goblinshift
S="sudo -n"
READYZ_TIMEOUT_S=10

# Newlines are flattened so that a value can never inject another fact line.
emit() { printf '%s\t%s\n' "$1" "${2//$'\n'/ }"; }

pending_found() {
  local p
  for p in "$R/pending.json" "$G/pending.json"; do
    if $S test -e "$p"; then emit PENDING "${p#"$ROOT"}"; return 0; fi
  done
  return 1
}

$S true || exit 2
pending_found && exit 0
emit PENDING_LIDO sim
x=$($S find "$R" -maxdepth 2 -name pending.json) && emit OUTROS_PENDING "$x"
x=$($S find "$R" "$G" -maxdepth 1 -name 'incoming-*') && emit INCOMING "$x"
if locks=$($S cat "$ROOT/proc/locks"); then
  for pair in "LOCK_HOST:$R/host.lock" "LOCK_PROJETO:$G/release.lock"; do
    inode=$($S stat -c %i "${pair#*:}") || continue
    if grep -q ":$inode " <<<"$locks"; then emit "${pair%%:*}" preso; else emit "${pair%%:*}" livre; fi
  done
fi
# With hidepid on /proc only root sees the receiver. The [/] keeps sudo's own command line
# (which carries the pattern as text) from matching.
x=$($S pgrep -af '[/]opt/coolify-release/'); [[ $? -le 1 ]] && emit RECEPTOR_ATIVO "$x"
if cur=$($S cat "$G/current.json"); then
  field() { local v; v=$(jq -r "$2" <<<"$cur") && emit "$1" "$v"; }
  field COMMIT .release.commit
  field PROJETO .release.project
  field VERSAO .release.version
  field RECV_SHA .release.receiver_sha256
  field DEPLOY_UUID '.deployment_uuid // ""'
  field FINISHED '.finished_at | floor'
  x=$(jq -c .release.images <<<"$cur") && emit IMAGES "$x"
fi
target_receiver=$($S readlink -f "$ROOT/opt/coolify-release/receiver.py") \
  && x=$($S sha256sum "$target_receiver") && emit RECV_INST "${x%% *}"
if ! $S test -d "$G/history"; then
  emit HIST_TOPO ""; emit HIST_N_COMMIT 0
elif history=$($S ls -1t "$G/history"); then
  top=$(sed -n 1p <<<"$history"); previous=$(sed -n 2p <<<"$history")
  emit HIST_TOPO "$top"
  emit HIST_N_COMMIT "$(grep -c "^$M-" <<<"$history")"
  # shellcheck disable=SC2016 # $a and $b are jq variables, not shell ones
  [[ -n $top ]] && x=$($S jq -n --slurpfile a "$G/history/$top" --slurpfile b "$G/current.json" '$a == $b') \
    && emit HIST_IGUAL "$x"
  if [[ -z $previous ]]; then emit EXEC_ANTERIOR ""
  else x=$($S jq -r .release.images.executor "$G/history/$previous") && emit EXEC_ANTERIOR "$x"; fi
fi
x=$($S jq -c .allowed "$G/executor-images.json") && emit ALLOWED "$x"
x=$($S stat -c '%U:%G %a %s %Y' "$G/executor-images.json") && emit ALLOW_META "$x"
x=$($S jq -r .images_file "$ROOT/etc/goblinshift/executor-ctl.json") && emit EXEC_CFG "$x"
x=$($S stat -c %Y "$G/current.json") && emit CUR_MTIME "$x"
if [[ -n ${cur:-} ]] && $S docker version >/dev/null 2>&1 \
  && cfg=$($S jq -c '{uuid, image_roles}' "$ROOT/etc/coolify-release/goblinshift.json"); then
  emit UUID "$(jq -r .uuid <<<"$cfg")"
  for role in app web executor; do
    ref=$(jq -r ".release.images.$role // empty" <<<"$cur"); repo=${ref%@*}; digest=${ref#*@}
    x=$($S docker image inspect --format '{{.Id}}' "$repo:release-$M@$digest" 2>/dev/null) || x=""
    emit "IMPORT_${role^^}" "$x"
  done
  for c in $(jq -r '.image_roles | keys[]' <<<"$cfg"); do
    role=$(jq -r --arg c "$c" '.image_roles[$c]' <<<"$cfg")
    x=$($S docker inspect --format '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}nohealth{{end}}|{{.Image}}|{{index .Config.Labels "coolify.applicationUuid"}}|{{.Config.Image}}' "$c" 2>/dev/null) \
      || x="ausente|-|-|-|-"
    emit CONTAINER "$c|$role|$x"
  done
  # Only the API's own address: any fallback (0.0.0.0, localhost) could reach another service
  # of the host and turn its 200 into a false OK. Without the IP, READYZ stays unknown.
  ip=$($S docker inspect --format '{{(index .NetworkSettings.Networks "goblinshift_internal").IPAddress}}' goblinshift-api-1 2>/dev/null)
  if [[ $ip =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
    x=$(curl -s -o /dev/null -w '%{http_code}' --max-time "$READYZ_TIMEOUT_S" "http://$ip:8000/readyz")
    emit READYZ "$x"
  fi
fi
log=$ROOT/var/log/coolify-release-errors.log
if ! $S test -e "$log"; then emit ERRLOG_MTIME 0
else x=$($S stat -c %Y "$log") && emit ERRLOG_MTIME "$x"; fi
# A release that started during the reads makes them a mix of two states: in progress, not wrong.
pending_found
exit 0
