#!/usr/bin/env bash
# Read-only post-release check of GoblinShift: collects facts, pos_release.py decides.
# Usage: POS_RELEASE_SSH_TARGET=<ssh destination> pos_release.sh <full merge sha>
# Exit: 0 COERENTE, 1 INCOERENTE, 2 DESCONHECIDO, 3 EM ANDAMENTO.
# A fact that cannot be read is not emitted, so the decision becomes DESCONHECIDO, never OK.
set -uo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO=BipoXDXD/GoblinShift
M=${1:-}
[[ $M =~ ^[0-9a-f]{40}$ ]] || { echo "DESCONHECIDO: SHA completo obrigatório"; exit 2; }
target=${POS_RELEASE_SSH_TARGET:-}
[[ $target =~ ^[A-Za-z0-9_][A-Za-z0-9_.@-]*$ ]] \
  || { echo "DESCONHECIDO: defina POS_RELEASE_SSH_TARGET (destino ssh, sem opções)"; exit 2; }

emit() { printf '%s\t%s\n' "$1" "$2"; }

# One ssh session, remote script on stdin. Locks are read from /proc/locks by inode: taking
# them, even with LOCK_NB, could make a real release lose the race.
remote=$(ssh -o BatchMode=yes -o ConnectTimeout=15 -- "$target" bash -s -- "$M" <<'REMOTE'
set -uo pipefail
M=$1; R=/var/lib/coolify-release; G=$R/goblinshift; S="sudo -n"
emit() { printf '%s\t%s\n' "$1" "$2"; }
spaced() { printf '%s' "${1//$'\n'/ }"; }
$S true || exit 2
for p in "$R/pending.json" "$G/pending.json"; do
  if $S test -e "$p"; then emit PENDING "$p"; exit 0; fi
done
emit PENDING_LIDO sim
x=$($S find "$R" -maxdepth 2 -name pending.json) && emit OUTROS_PENDING "$(spaced "$x")"
x=$($S find "$R" "$G" -maxdepth 1 -name 'incoming-*') && emit INCOMING "$(spaced "$x")"
if locks=$($S cat /proc/locks); then
  for pair in "LOCK_HOST:$R/host.lock" "LOCK_PROJETO:$G/release.lock"; do
    inode=$($S stat -c %i "${pair#*:}") || continue
    if grep -q ":$inode " <<<"$locks"; then emit "${pair%%:*}" preso; else emit "${pair%%:*}" livre; fi
  done
fi
x=$(pgrep -f /opt/coolify-release/); [[ $? -le 1 ]] && emit RECEPTOR_ATIVO "$(spaced "$x")"
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
target_receiver=$($S readlink -f /opt/coolify-release/receiver.py) \
  && x=$($S sha256sum "$target_receiver") && emit RECV_INST "${x%% *}"
if ! $S test -d "$G/history"; then
  emit HIST_TOPO ""; emit HIST_N_COMMIT 0
elif history=$($S ls -1t "$G/history"); then
  top=$(sed -n 1p <<<"$history"); previous=$(sed -n 2p <<<"$history")
  emit HIST_TOPO "$top"
  emit HIST_N_COMMIT "$(grep -c "^$M-" <<<"$history")"
  [[ -n $top ]] && x=$($S jq -n --slurpfile a "$G/history/$top" --slurpfile b "$G/current.json" '$a == $b') \
    && emit HIST_IGUAL "$x"
  if [[ -z $previous ]]; then emit EXEC_ANTERIOR ""
  else x=$($S jq -r .release.images.executor "$G/history/$previous") && emit EXEC_ANTERIOR "$x"; fi
fi
x=$($S jq -c .allowed "$G/executor-images.json") && emit ALLOWED "$x"
x=$($S stat -c '%U:%G %a %s %Y' "$G/executor-images.json") && emit ALLOW_META "$x"
x=$($S jq -r .images_file /etc/goblinshift/executor-ctl.json) && emit EXEC_CFG "$x"
x=$($S stat -c %Y "$G/current.json") && emit CUR_MTIME "$x"
if [[ -n ${cur:-} ]] && $S docker version >/dev/null 2>&1 \
  && cfg=$($S jq -c '{uuid, image_roles}' /etc/coolify-release/goblinshift.json); then
  emit UUID "$(jq -r .uuid <<<"$cfg")"
  for role in app web executor; do
    ref=$(jq -r ".release.images.$role // empty" <<<"$cur"); repo=${ref%@*}; digest=${ref#*@}
    x=$($S docker image inspect --format '{{.Id}}' "$repo:release-$M@$digest" 2>/dev/null) || x=""
    emit "IMPORT_${role^^}" "$x"
  done
  for c in $(jq -r '.image_roles | keys[]' <<<"$cfg"); do
    role=$(jq -r --arg c "$c" '.image_roles[$c]' <<<"$cfg")
    x=$($S docker inspect --format '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}nohealth{{end}}|{{.Image}}|{{index .Config.Labels "coolify.applicationUuid"}}' "$c" 2>/dev/null) \
      || x="ausente|-|-|-"
    emit CONTAINER "$c|$role|$x"
  done
  ip=$($S docker inspect --format '{{(index .NetworkSettings.Networks "goblinshift_internal").IPAddress}}' goblinshift-api-1 2>/dev/null)
  emit READYZ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://${ip:-0.0.0.0}:8000/readyz")"
fi
log=/var/log/coolify-release-errors.log
if ! $S test -e "$log"; then emit ERRLOG_MTIME 0
else x=$($S stat -c %Y "$log") && emit ERRLOG_MTIME "$x"; fi
REMOTE
)
rc=$?
[[ $rc == 0 ]] || { echo "DESCONHECIDO: leitura remota falhou (rc=$rc)"; exit 2; }

collect_outside() {
  local x pair key tag run head created status conclusion
  x=$(curl -fsS --max-time 20 "https://raw.githubusercontent.com/$REPO/$M/deploy/coolify/release/receiver-release.json") \
    && x=$(jq -er .sha256 <<<"$x") && emit RECV_REPO "$x"
  for pair in APP:goblinshift WEB:goblinshift-web EXECUTOR:goblinshift-executor; do
    # Independent proof against GHCR: the linux/amd64 manifest that CI published for this commit.
    key=${pair%%:*}; tag="ghcr.io/bipoxdxd/${pair#*:}:sha-${M:0:12}"
    x=$(crane manifest "$tag") && x=$(jq -c . <<<"$x") && emit "MANIFEST_$key" "$x"
    x=$(crane digest "$tag") && emit "TAG_DIGEST_$key" "$x"
  done
  run=$(gh run list -R "$REPO" --workflow coolify.yml --commit "$M" --limit 1 \
    --json createdAt,status,conclusion --jq '.[0] // empty | [(.createdAt | fromdate), .status, .conclusion] | @tsv') \
    && [[ -n $run ]] && IFS=$'\t' read -r created status conclusion <<<"$run" \
    && emit RUN_CRIADO "$created" && emit RUN_STATUS "$status" && emit RUN_CONCLUSAO "$conclusion"
  head=$(git ls-remote "https://github.com/$REPO.git" refs/heads/main) \
    && [[ -n $head ]] && emit MAIN_HEAD "${head%%$'\t'*}"
  return 0
}

facts=$remote
grep -q $'^PENDING\t' <<<"$remote" || facts+=$'\n'$(collect_outside)
python3 -I "$script_dir/pos_release.py" "$M" <<<"$facts"
