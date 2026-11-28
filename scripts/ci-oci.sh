#!/usr/bin/env bash
# Move verified multiarch layouts between the CI registry and the trusted GHCR job.
set -euo pipefail
cd "$(dirname "$0")/.."

REGISTRY=127.0.0.1:5000
REGISTRY_IMAGE=registry:3.0.0@sha256:6c5666b861f3505b116bb9aa9b25175e71210414bd010d92035ff64018f9457e
TRIVY_IMAGE=ghcr.io/aquasecurity/trivy:0.75.0@sha256:af6acf9a6b85dfe389a1941505c0ce9efef52a4719635e1a962f022a3d855daa

fail() { echo "::error::$*" >&2; exit 1; }
[[ "${GITHUB_ACTIONS:-}" == true ]] || fail 'Use only on the hosted CI runner'

if [[ "$1" == registry ]]; then
  docker run --detach --name gs-ci-registry --publish "$REGISTRY:5000" "$REGISTRY_IMAGE"
  for _ in {1..20}; do
    if curl --silent --fail --max-time 2 "http://$REGISTRY/v2/" >/dev/null; then exit 0; fi
    sleep 1
  done
  fail 'Disposable OCI registry did not start'
fi

operation=$1
case "$2" in
  app) name=goblinshift ;;
  executor) name=goblinshift-executor ;;
  web) name=goblinshift-web ;;
  *) fail 'Unknown release role' ;;
esac
digest=$3
[[ "$digest" =~ ^sha256:[a-f0-9]{64}$ ]] || fail 'Invalid verified OCI index digest'
if [[ "$operation" == publish-* ]]; then
  [[ "$GITHUB_REPOSITORY" == BipoXDXD/GoblinShift && "$GITHUB_REF" == refs/heads/main ]] ||
    fail 'Publication requires the canonical main branch'
  [[ "$GITHUB_EVENT_NAME" == workflow_run || "$GITHUB_EVENT_NAME" == workflow_dispatch ]] ||
    fail 'Publication requires the trusted release workflow'
  [[ "$RELEASE_COMMIT" =~ ^[a-f0-9]{40}$ ]] || fail 'Invalid release commit'
  repository="ghcr.io/bipoxdxd/$name"
  commit=$RELEASE_COMMIT
  client=(crane)
else
  repository="$REGISTRY/$name"
  commit=$GITHUB_SHA
  client=(crane --insecure)
fi
tag="sha-${commit:0:12}"

verify_tag() {
  local actual
  actual=$("${client[@]}" digest "$repository:$1")
  [[ "$actual" == "$digest" ]] || fail "Unexpected index on $repository:$1"
}

release_tag_state() {
  local current
  if current=$("${client[@]}" digest "$repository:$tag" 2>&1); then
    [[ "$current" == "$digest" ]] || fail "Existing release tag differs for $repository:$tag"
    printf 'present\n'
    return
  fi
  [[ "$current" == *MANIFEST_UNKNOWN* || "$current" == *NAME_UNKNOWN* ]] ||
    fail "Unable to establish the existing release tag for $repository:$tag"
  printf 'missing\n'
}

case "$operation" in
  publish-check)
    release_tag_state >/dev/null
    ;;
  stage|publish-sha)
    [[ "$#" -eq 4 ]] || fail 'Expected one OCI layout for the role'
    layout=$4
    python3 deploy/coolify/release/ci_oci.py verify "$layout" "$digest"
    if [[ "$operation" == publish-sha ]]; then
      state=$(release_tag_state)
      [[ "$state" == missing ]] || exit 0
    fi
    "${client[@]}" push "$layout" "$repository:$tag"
    verify_tag "$tag"
    ;;
  scan)
    for platform in linux/amd64 linux/arm64; do
      docker run --rm --network host "$TRIVY_IMAGE" image --image-src remote \
        --platform "$platform" --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 \
        "$repository@$digest"
    done
    ;;
  sbom)
    # SPDX of one platform of the scanned index, attested by actions/attest-sbom (ADR 075).
    [[ "$#" -eq 4 ]] || fail 'Expected one platform for the SBOM'
    case "$4" in
      linux/amd64|linux/arm64) arch=${4#linux/} ;;
      *) fail 'Unknown release platform' ;;
    esac
    file="sbom-$2-$arch.spdx.json"
    rm -f "$RUNNER_TEMP/$file"
    docker run --rm --network host --volume "$RUNNER_TEMP:/out" "$TRIVY_IMAGE" image \
      --image-src remote --platform "$4" --format spdx-json --output "/out/$file" \
      "$repository@$digest"
    [[ -s "$RUNNER_TEMP/$file" ]] || fail "Trivy wrote no SBOM for $4"
    # actions/attest-sbom rejects predicates above 16 MiB; fail here with a clear message.
    (( $(wc -c < "$RUNNER_TEMP/$file") < 16 * 1024 * 1024 )) || fail "SBOM for $4 exceeds 16 MiB"
    jq -e '(.packages | length) >= 2' "$RUNNER_TEMP/$file" >/dev/null ||
      fail "SBOM for $4 lists fewer than two packages"
    ;;
  promote|publish-main)
    verify_tag "sha-${commit:0:12}"
    "${client[@]}" tag "$repository@$digest" main
    verify_tag main
    ;;
  *) fail 'Unknown OCI operation' ;;
esac
