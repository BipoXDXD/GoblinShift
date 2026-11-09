#!/usr/bin/env bash
# Hosted CI runner only; the VPS receives OCI data, never registry credentials.
set -euo pipefail
umask 077
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ ${RELEASE_COMMIT:?} =~ ^[0-9a-f]{40}$ ]]
case "${RELEASE_PROJECT:?}" in duora|goblinshift|stillon) ;; *) exit 2 ;; esac
[[ ${DEPLOY_TARGET:?} =~ ^cfrelease-${RELEASE_PROJECT}@[a-zA-Z0-9.-]+$ ]]
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
mkdir "$stage/images"
python3 "$script_dir/send.py" requested > "$stage/requested.tsv"
while IFS=$'\t' read -r role reference; do
  crane manifest "$reference" > "$stage/index.json"
  # Exit 1 is jq's only "not an index" answer; any other failure means the manifest was unreadable.
  has_manifests_rc=0
  jq -e 'has("manifests")' "$stage/index.json" >/dev/null 2>&1 || has_manifests_rc=$?
  case "$has_manifests_rc" in
    0)
      digest=$(jq -er '[.manifests[] | select(.platform.os == "linux" and .platform.architecture == "amd64")] | if length == 1 then .[0].digest else error("ambiguous platform") end' "$stage/index.json")
      ;;
    1)
      digest=$(crane digest "$reference")
      ;;
    *)
      printf 'Cannot classify registry manifest for role %s (jq exit %s)\n' "$role" "$has_manifests_rc" >&2
      exit 1
      ;;
  esac
  [[ $digest =~ ^sha256:[0-9a-f]{64}$ ]]
  repository=${reference%@*}
  repository=${repository%:*}
  resolved="$repository@$digest"
  printf '%s\t%s\n' "$role" "$resolved" >> "$stage/resolved.tsv"
  crane pull --platform linux/amd64 --format oci "$resolved" "$stage/images/$role"
  jq -c 'del(.manifests[]?.artifactType)' "$stage/images/$role/index.json" > "$stage/normalized.json"
  mv "$stage/normalized.json" "$stage/images/$role/index.json"
done < "$stage/requested.tsv"
python3 "$script_dir/send.py" manifest "$stage/resolved.tsv" "$stage/images"
tar -C "$stage/images" -cf "$stage/release.tar" .
ssh -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
  -i "${DEPLOY_KEY:?}" -o "UserKnownHostsFile=${DEPLOY_KNOWN_HOSTS:?}" \
  "$DEPLOY_TARGET" "release $RELEASE_COMMIT" < "$stage/release.tar"
