#!/usr/bin/env bash
# Build the frozen, secret-free Hermes PoC image under an immutable local tag.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly EXPECTED_COMMIT="ddb8d8fa842283ef651a6e4514f8f561f736c72e"
readonly EXPECTED_PATCH="856d6e1820fe4f41669535a3e21c34a153e98318bcce90a607509c24d423d8c5"

CONTEXT="$($SCRIPT_DIR/prepare_hermes_poc.sh)"
CONTEXT_ID="$(basename -- "$CONTEXT")"
FIXTURE_SHA256="$(awk -F= '$1 == "fixture_sha256" {print $2}' "$CONTEXT/SOURCE_BASELINE")"
if [[ ! "$CONTEXT_ID" =~ ^[0-9a-f]{12}-[0-9a-f]{12}-[0-9a-f]{12}$ || ! "$FIXTURE_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
  printf 'Invalid PoC build-context identity: %s\n' "$CONTEXT_ID" >&2
  exit 2
fi
readonly IMAGE_REF="siq/hermes-openshell-poc:$CONTEXT_ID"
python3 "$SCRIPT_DIR/check_mount_safety.py" --mount-root "$CONTEXT" >&2

verify_image() {
  local revision patch fixture architecture
  revision="$(docker image inspect "$IMAGE_REF" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
  patch="$(docker image inspect "$IMAGE_REF" --format '{{index .Config.Labels "ai.siq.hermes.patch-sha256"}}')"
  fixture="$(docker image inspect "$IMAGE_REF" --format '{{index .Config.Labels "ai.siq.poc-fixture-sha256"}}')"
  architecture="$(docker image inspect "$IMAGE_REF" --format '{{.Architecture}}')"
  if [[ "$revision" != "$EXPECTED_COMMIT" || "$patch" != "$EXPECTED_PATCH" || "$fixture" != "$FIXTURE_SHA256" || "$architecture" != "arm64" ]]; then
    printf 'Existing PoC image does not match the frozen baseline: %s\n' "$IMAGE_REF" >&2
    exit 2
  fi
}

if docker image inspect "$IMAGE_REF" >/dev/null 2>&1; then
  verify_image
  printf '%s\n' "$IMAGE_REF"
  exit 0
fi

docker build \
  --pull=false \
  --build-arg "SIQ_POC_FIXTURE_SHA256=$FIXTURE_SHA256" \
  --file "$CONTEXT/Dockerfile" \
  --tag "$IMAGE_REF" \
  "$CONTEXT" >&2
verify_image
printf '%s\n' "$IMAGE_REF"
