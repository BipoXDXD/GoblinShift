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

# Every outside call is bounded: a stuck network must end in DESCONHECIDO, not hang.
# Overridable in seconds, so the tests can prove a stuck call ends without waiting minutes.
SSH_LIMIT_S=${POS_RELEASE_SSH_LIMIT_S:-300}
TOOL_LIMIT_S=${POS_RELEASE_TOOL_LIMIT_S:-60}
[[ $SSH_LIMIT_S =~ ^[1-9][0-9]*$ && $TOOL_LIMIT_S =~ ^[1-9][0-9]*$ ]] \
  || { echo "DESCONHECIDO: limite de tempo inválido"; exit 2; }

# Runs "$@" and kills it after $1 seconds (macOS has no coreutils timeout). The watchdog is
# stopped as soon as the command ends: left polling, it could outlive the command and, once the
# pid is reused, signal an unrelated process.
run_limited() {
  local limit=$1 pid watchdog rc
  shift
  "$@" <&0 &
  pid=$!
  (
    sleep "$limit"
    # Direct children too (a wrapper script, ssh's ProxyCommand): one left alive would keep
    # the output pipe open and the command substitution waiting.
    pkill -TERM -P "$pid"
    kill -TERM "$pid"
  ) >/dev/null 2>&1 &
  watchdog=$!
  wait "$pid"
  rc=$?
  # Its sleep first: killing only the subshell would leave that sleep orphaned.
  pkill -TERM -P "$watchdog" >/dev/null 2>&1
  kill -TERM "$watchdog" >/dev/null 2>&1
  wait "$watchdog" >/dev/null 2>&1
  return "$rc"
}

# One ssh session; the read-only remote half goes on stdin.
remote=$(run_limited "$SSH_LIMIT_S" ssh -o BatchMode=yes -o ConnectTimeout=15 \
  -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -- "$target" bash -s -- "$M" \
  <"$script_dir/pos_release_remote.sh")
rc=$?
[[ $rc == 0 ]] || { echo "DESCONHECIDO: leitura remota falhou (rc=$rc)"; exit 2; }

collect_outside() {
  local x pair key tag run head created status conclusion
  x=$(curl -fsS --max-time 20 "https://raw.githubusercontent.com/$REPO/$M/deploy/coolify/release/receiver-release.json") \
    && x=$(jq -er .sha256 <<<"$x") && emit RECV_REPO "$x"
  for pair in APP:goblinshift WEB:goblinshift-web EXECUTOR:goblinshift-executor; do
    # Independent proof against GHCR: the linux/amd64 manifest that CI published for this commit.
    key=${pair%%:*}; tag="ghcr.io/bipoxdxd/${pair#*:}:sha-${M:0:12}"
    x=$(run_limited "$TOOL_LIMIT_S" crane manifest "$tag") && x=$(jq -c . <<<"$x") && emit "MANIFEST_$key" "$x"
    x=$(run_limited "$TOOL_LIMIT_S" crane digest "$tag") && emit "TAG_DIGEST_$key" "$x"
  done
  run=$(run_limited "$TOOL_LIMIT_S" gh run list -R "$REPO" --workflow coolify.yml --commit "$M" --limit 1 \
    --json createdAt,status,conclusion --jq '.[0] // empty | [(.createdAt | fromdate), .status, .conclusion] | @tsv') \
    && [[ -n $run ]] && IFS=$'\t' read -r created status conclusion <<<"$run" \
    && emit RUN_CRIADO "$created" && emit RUN_STATUS "$status" && emit RUN_CONCLUSAO "$conclusion"
  head=$(run_limited "$TOOL_LIMIT_S" git ls-remote "https://github.com/$REPO.git" refs/heads/main) \
    && [[ -n $head ]] && emit MAIN_HEAD "${head%%$'\t'*}"
  return 0
}

facts=$remote
grep -q $'^PENDING\t' <<<"$remote" || facts+=$'\n'$(collect_outside)
python3 -I "$script_dir/pos_release.py" "$M" <<<"$facts"
