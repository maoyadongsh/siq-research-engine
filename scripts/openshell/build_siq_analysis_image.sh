#!/usr/bin/env bash
# Build and verify the production-shaped siq_analysis BYOC image.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
STATE_ROOT="$ROOT_DIR/var/openshell/siq-analysis"
readonly EXPECTED_COMMIT="ddb8d8fa842283ef651a6e4514f8f561f736c72e"
readonly EXPECTED_PATCH="856d6e1820fe4f41669535a3e21c34a153e98318bcce90a607509c24d423d8c5"
readonly EXPECTED_INTEGRATION_PATCH="aabc1d6fdd252acc4131bf6b843c96c43f875267c5b08100cdc94700c762a242"

CONTEXT="$($SCRIPT_DIR/prepare_siq_analysis_context.sh)"
CONTEXT_SHA256="$(basename -- "$CONTEXT")"
BASELINE="$CONTEXT/SOURCE_BASELINE.json"
RUNTIME_CONFIG_SHA256="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["runtime_config_sha256"])' "$BASELINE")"
if [[ ! "$CONTEXT_SHA256" =~ ^[0-9a-f]{64}$ || ! "$RUNTIME_CONFIG_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
  printf '%s\n' 'Invalid siq_analysis context identity.' >&2
  exit 2
fi
readonly IMAGE_REF="siq/hermes-openshell-siq-analysis:${CONTEXT_SHA256:0:24}"

python3 "$SCRIPT_DIR/check_mount_safety.py" --mount-root "$CONTEXT" >&2

verify_image() {
  local revision patch integration_patch context config architecture user command
  revision="$(docker image inspect "$IMAGE_REF" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
  patch="$(docker image inspect "$IMAGE_REF" --format '{{index .Config.Labels "ai.siq.hermes.patch-sha256"}}')"
  integration_patch="$(docker image inspect "$IMAGE_REF" --format '{{index .Config.Labels "ai.siq.hermes.integration-patch-sha256"}}')"
  context="$(docker image inspect "$IMAGE_REF" --format '{{index .Config.Labels "ai.siq.openshell.context-sha256"}}')"
  config="$(docker image inspect "$IMAGE_REF" --format '{{index .Config.Labels "ai.siq.openshell.runtime-config-sha256"}}')"
  architecture="$(docker image inspect "$IMAGE_REF" --format '{{.Architecture}}')"
  user="$(docker image inspect "$IMAGE_REF" --format '{{.Config.User}}')"
  command="$(docker image inspect "$IMAGE_REF" --format '{{json .Config.Cmd}}')"
  [[ "$revision" == "$EXPECTED_COMMIT" \
    && "$patch" == "$EXPECTED_PATCH" \
    && "$integration_patch" == "$EXPECTED_INTEGRATION_PATCH" \
    && "$context" == "$CONTEXT_SHA256" \
    && "$config" == "$RUNTIME_CONFIG_SHA256" \
    && "$architecture" == arm64 \
    && "$user" == sandbox:sandbox \
    && "$command" == '["/opt/siq/entrypoint.sh"]' ]] || {
      printf 'Image provenance mismatch: %s\n' "$IMAGE_REF" >&2
      exit 2
    }
}

record_candidate_image() {
  local target tmp image_id created
  target="$STATE_ROOT/current-image.json"
  mkdir -p -- "$STATE_ROOT"
  chmod 0700 -- "$STATE_ROOT"
  [[ ! -L "$target" ]] || {
    printf 'Refusing unsafe image state symlink: %s\n' "$target" >&2
    exit 2
  }
  image_id="$(docker image inspect "$IMAGE_REF" --format '{{.Id}}')"
  created="$(docker image inspect "$IMAGE_REF" --format '{{.Created}}')"
  tmp="$(mktemp "$STATE_ROOT/.current-image.XXXXXX")"
  trap 'rm -f -- "${tmp:-}"' RETURN
  python3 - "$tmp" <<PY
import json
import sys

payload = {
    "schema_version": "siq.openshell.candidate_image.v1",
    "image_ref": "$IMAGE_REF",
    "image_id": "$image_id",
    "architecture": "arm64",
    "user": "sandbox:sandbox",
    "hermes_commit": "$EXPECTED_COMMIT",
    "context_sha256": "$CONTEXT_SHA256",
    "runtime_config_sha256": "$RUNTIME_CONFIG_SHA256",
    "created": "$created",
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
    handle.write("\n")
PY
  chmod 0600 -- "$tmp"
  mv -fT -- "$tmp" "$target"
  trap - RETURN
}

if docker image inspect "$IMAGE_REF" >/dev/null 2>&1; then
  verify_image
  record_candidate_image
  printf '%s\n' "$IMAGE_REF"
  exit 0
fi

docker build \
  --pull=false \
  --build-arg "SIQ_HERMES_COMMIT=$EXPECTED_COMMIT" \
  --build-arg "SIQ_HERMES_PATCH_SHA256=$EXPECTED_PATCH" \
  --build-arg "SIQ_HERMES_INTEGRATION_PATCH_SHA256=$EXPECTED_INTEGRATION_PATCH" \
  --build-arg "SIQ_CONTEXT_SHA256=$CONTEXT_SHA256" \
  --build-arg "SIQ_RUNTIME_CONFIG_SHA256=$RUNTIME_CONFIG_SHA256" \
  --file "$CONTEXT/Dockerfile" \
  --tag "$IMAGE_REF" \
  "$CONTEXT" >&2
verify_image
record_candidate_image
printf '%s\n' "$IMAGE_REF"
