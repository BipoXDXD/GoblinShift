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
  # Slurp so the whole file decides, not its last document; exit 1 alone means "not an index".
  has_manifests_rc=0
  jq -se 'if length == 1 and (.[0] | type == "object") then .[0] | has("manifests") else "" | halt_error(10) end' "$stage/index.json" >/dev/null 2>&1 || has_manifests_rc=$?
  case "$has_manifests_rc" in
    0)
      digest_rc=0
      digest=$(jq -er 'if (.manifests | type) != "array" then "" | halt_error(11) else . end | [.manifests[] | select(.platform.os == "linux" and .platform.architecture == "amd64")] | if length == 0 then "" | halt_error(12) elif length > 1 then "" | halt_error(13) else .[0].digest end' "$stage/index.json" 2>/dev/null) || digest_rc=$?
      if [ "$digest_rc" -ne 0 ]; then
        case "$digest_rc" in
          11) reason=manifests-not-list ;;
          12) reason=no-amd64 ;;
          13) reason=multiple-amd64 ;;
          *) reason=invalid-entry ;;
        esac
        printf 'Cannot resolve the linux/amd64 digest from the index for role %s (%s)\n' "$role" "$reason" >&2
        exit 1
      fi
      ;;
    1)
      digest=$(crane digest "$reference")
      ;;
    *)
      case "$has_manifests_rc" in
        10) reason=not-one-object ;;
        127) reason=jq-missing ;;
        *) reason=unreadable ;;
      esac
      printf 'Cannot classify registry manifest for role %s (%s, jq exit %s)\n' "$role" "$reason" "$has_manifests_rc" >&2
      exit 1
      ;;
  esac
  if ! [[ $digest =~ ^sha256:[0-9a-f]{64}$ ]]; then
    printf 'Refusing digest for role %s (invalid-digest)\n' "$role" >&2
    exit 1
  fi
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
