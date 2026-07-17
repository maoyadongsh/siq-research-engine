#!/usr/bin/env bash
# Build a secret-free, deterministic siq_analysis Docker context.

set -euo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
FIXTURE_DIR="$ROOT_DIR/infra/openshell/sandbox"
AUTH_TEMPLATE="$ROOT_DIR/infra/openshell/providers/hermes/minimax-cn-auth-pool.template.json"
HERMES_AUTH_PATCH="$ROOT_DIR/infra/openshell/patches/hermes-0.13.0/0001-runtime-auth-file-override.patch"
HERMES_RUNTIME_STATE_PATCH="$ROOT_DIR/infra/openshell/patches/hermes-0.13.0/0002-runtime-state-home-override.patch"
HERMES_RUN_QUIESCENCE_PATCH="$ROOT_DIR/infra/openshell/patches/hermes-0.13.0/0003-api-run-stop-quiescence.patch"
STATE_ROOT="$ROOT_DIR/var/openshell/siq-analysis"
CONTEXTS_DIR="$STATE_ROOT/contexts"
RUNTIME_CONFIG="$ROOT_DIR/data/hermes/home/profiles/siq_analysis/config.yaml"
NODE_DOWNLOAD_ROOT="$ROOT_DIR/var/openshell/toolchains/node/v20.20.2"
NODE_ARCHIVE="$NODE_DOWNLOAD_ROOT/node-v20.20.2-linux-arm64.tar.xz"

readonly HERMES_COMMIT="ddb8d8fa842283ef651a6e4514f8f561f736c72e"
readonly HERMES_PATCH_SHA256="856d6e1820fe4f41669535a3e21c34a153e98318bcce90a607509c24d423d8c5"
readonly HERMES_PATCH_ONE_SHA256="d785126cfdd00b870f4cdaf7396edfd2632f4011b6a071d9116f7dcea9afe902"
readonly HERMES_PATCH_TWO_SHA256="d9be84e03aebab771659d63b62a29e67ec3264187a5b0562a6cafdbf5cfdc146"
readonly HERMES_PATCH_THREE_SHA256="84555a500afd0c7cacb37acbafab55a1cc06867c21aa30a97c78b93420f8a17c"
readonly HERMES_INTEGRATION_PATCH_SHA256="aabc1d6fdd252acc4131bf6b843c96c43f875267c5b08100cdc94700c762a242"
readonly NODE_ARCHIVE_SHA256="73093db209e4e9e09dd7d15a47aeaab1b74833830df03efa5f942a1122c5fa71"
readonly NODE_ARCHIVE_URL="https://nodejs.org/dist/v20.20.2/node-v20.20.2-linux-arm64.tar.xz"

require_regular_file() {
  local path="$1"
  [[ -f "$path" && ! -L "$path" ]] || {
    printf 'Required regular file is missing or unsafe: %s\n' "$path" >&2
    exit 2
  }
}

tree_digest() {
  local root="$1"
  (
    cd "$root"
    find . -type f \
      ! -path '*/__pycache__/*' \
      ! -path '*/.pytest_cache/*' \
      ! -name '*.pyc' \
      ! -name 'FILES.sha256' \
      -print0 \
      | sort -z \
      | xargs -0 sha256sum \
      | sha256sum \
      | awk '{print $1}'
  )
}

copy_profile_tree() {
  local source="$1" target="$2"
  [[ -d "$source" && ! -L "$source" ]] || {
    printf 'Profile source is missing or unsafe: %s\n' "$source" >&2
    exit 2
  }
  mkdir -p -- "$target"
  rsync -a --delete \
    --exclude '.env' \
    --exclude '.env.*' \
    --exclude '.git/' \
    --exclude '.pytest_cache/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude 'auth.json' \
    --exclude 'cache/' \
    --exclude 'logs/' \
    --exclude 'sessions/' \
    --exclude 'workspace/' \
    --exclude 'state.db*' \
    --exclude 'response_store.db*' \
    "$source/" "$target/"
}

for file in Dockerfile .dockerignore entrypoint.sh observe-entrypoint.sh healthcheck.py requirements-siq-analysis.txt siq-fetch validate_placeholder_auth.py validate_provider_placeholders.py; do
  require_regular_file "$FIXTURE_DIR/$file"
done
require_regular_file "$ROOT_DIR/scripts/openshell/runtime_state_lifecycle_smoke.py"
require_regular_file "$ROOT_DIR/scripts/openshell/probe_milvus_sandbox_boundary.py"
require_regular_file "$AUTH_TEMPLATE"
require_regular_file "$HERMES_AUTH_PATCH"
require_regular_file "$HERMES_RUNTIME_STATE_PATCH"
require_regular_file "$HERMES_RUN_QUIESCENCE_PATCH"
[[ "$(sha256sum "$HERMES_AUTH_PATCH" | awk '{print $1}')" == "$HERMES_PATCH_ONE_SHA256" \
  && "$(sha256sum "$HERMES_RUNTIME_STATE_PATCH" | awk '{print $1}')" == "$HERMES_PATCH_TWO_SHA256" \
  && "$(sha256sum "$HERMES_RUN_QUIESCENCE_PATCH" | awk '{print $1}')" == "$HERMES_PATCH_THREE_SHA256" ]] || {
  printf '%s\n' 'Frozen Hermes integration patch mismatch.' >&2
  exit 2
}
[[ "$(printf '%s\n%s\n%s\n' "$HERMES_PATCH_ONE_SHA256" "$HERMES_PATCH_TWO_SHA256" "$HERMES_PATCH_THREE_SHA256" | sha256sum | awk '{print $1}')" == "$HERMES_INTEGRATION_PATCH_SHA256" ]] || {
  printf '%s\n' 'Frozen Hermes integration patch bundle mismatch.' >&2
  exit 2
}
require_regular_file "$RUNTIME_CONFIG"
require_regular_file "$ROOT_DIR/AGENTS.md"
for file in \
  scripts/openshell/egress_decision.py \
  scripts/openshell/egress_guard.py \
  scripts/openshell/broker_request_identity.py \
  scripts/openshell/security_audit.py \
  scripts/openshell/siq_fetch.py \
  infra/openshell/egress/allowlist.json; do
  require_regular_file "$ROOT_DIR/$file"
done

siq_openshell_acquire_context_lock() {
  mkdir -p -- "$STATE_ROOT"
  chmod 0700 -- "$STATE_ROOT"
  exec 9>>"$STATE_ROOT/context.lock"
  chmod 0600 "$STATE_ROOT/context.lock"
  flock -n 9 || {
    printf '%s\n' 'Another siq_analysis context operation is in progress.' >&2
    exit 75
  }
}
siq_openshell_acquire_context_lock

mkdir -p -- "$NODE_DOWNLOAD_ROOT"
chmod 0700 -- "$NODE_DOWNLOAD_ROOT"
[[ ! -L "$NODE_DOWNLOAD_ROOT" && ! -L "$NODE_ARCHIVE" ]] || {
  printf '%s\n' 'Node archive path is unsafe.' >&2
  exit 2
}
if [[ ! -e "$NODE_ARCHIVE" ]]; then
  NODE_TMP="$(mktemp "$NODE_DOWNLOAD_ROOT/.node-v20.20.2.XXXXXX")"
  if ! curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error \
      --retry 3 --connect-timeout 15 --max-time 300 \
      --output "$NODE_TMP" "$NODE_ARCHIVE_URL"; then
    rm -f -- "$NODE_TMP"
    exit 2
  fi
  if ! printf '%s  %s\n' "$NODE_ARCHIVE_SHA256" "$NODE_TMP" | sha256sum -c - >/dev/null; then
    rm -f -- "$NODE_TMP"
    exit 2
  fi
  chmod 0600 -- "$NODE_TMP"
  if ! mv -fT -- "$NODE_TMP" "$NODE_ARCHIVE"; then
    rm -f -- "$NODE_TMP"
    exit 2
  fi
fi
require_regular_file "$NODE_ARCHIVE"
[[ "$(sha256sum "$NODE_ARCHIVE" | awk '{print $1}')" == "$NODE_ARCHIVE_SHA256" ]] || {
  printf '%s\n' 'Pinned Node archive checksum mismatch.' >&2
  exit 2
}

HERMES_CONTEXT="$($SCRIPT_DIR/prepare_hermes_poc.sh)"
require_regular_file "$HERMES_CONTEXT/SOURCE_BASELINE"
grep -Fxq "hermes_commit=$HERMES_COMMIT" "$HERMES_CONTEXT/SOURCE_BASELINE" || {
  printf '%s\n' 'Frozen Hermes context commit mismatch.' >&2
  exit 2
}
grep -Fxq "patch_sha256=$HERMES_PATCH_SHA256" "$HERMES_CONTEXT/SOURCE_BASELINE" || {
  printf '%s\n' 'Frozen Hermes context patch mismatch.' >&2
  exit 2
}

mkdir -p -- "$CONTEXTS_DIR"
chmod 0700 -- "$CONTEXTS_DIR"
TMP_ROOT="$(mktemp -d "$STATE_ROOT/.context.XXXXXX")"
cleanup() {
  if [[ -n "${TMP_ROOT:-}" && "$TMP_ROOT" == "$STATE_ROOT"/.context.* && -d "$TMP_ROOT" ]]; then
    rm -rf -- "$TMP_ROOT"
  fi
}
trap cleanup EXIT

CONTEXT_DIR="$TMP_ROOT/context"
PROJECT_DIR="$CONTEXT_DIR/project"
mkdir -p -- \
  "$CONTEXT_DIR/hermes-agent" \
  "$CONTEXT_DIR/openshell-client/scripts/openshell" \
  "$CONTEXT_DIR/openshell-client/infra/openshell/egress" \
  "$PROJECT_DIR/agents/hermes/profiles" \
  "$PROJECT_DIR/data/hermes/home/profiles" \
  "$PROJECT_DIR/data/wiki"

rsync -a --delete \
  --exclude '.git/' \
  --exclude '.pytest_cache/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  "$HERMES_CONTEXT/hermes-agent/" "$CONTEXT_DIR/hermes-agent/"
patch --directory="$CONTEXT_DIR/hermes-agent" --strip=1 --forward --batch \
  --dry-run --input="$HERMES_AUTH_PATCH" >/dev/null
patch --directory="$CONTEXT_DIR/hermes-agent" --strip=1 --forward --batch \
  --input="$HERMES_AUTH_PATCH" >/dev/null
patch --directory="$CONTEXT_DIR/hermes-agent" --strip=1 --forward --batch \
  --dry-run --input="$HERMES_RUNTIME_STATE_PATCH" >/dev/null
patch --directory="$CONTEXT_DIR/hermes-agent" --strip=1 --forward --batch \
  --input="$HERMES_RUNTIME_STATE_PATCH" >/dev/null
patch --directory="$CONTEXT_DIR/hermes-agent" --strip=1 --forward --batch \
  --dry-run --input="$HERMES_RUN_QUIESCENCE_PATCH" >/dev/null
patch --directory="$CONTEXT_DIR/hermes-agent" --strip=1 --forward --batch \
  --input="$HERMES_RUN_QUIESCENCE_PATCH" >/dev/null
grep -Fq 'os.environ.get("HERMES_AUTH_FILE", "").strip()' \
  "$CONTEXT_DIR/hermes-agent/hermes_cli/auth.py" \
  && grep -Fq 'def get_hermes_runtime_home() -> Path:' \
    "$CONTEXT_DIR/hermes-agent/hermes_constants.py" \
  && grep -Fq 'self._run_stop_requests: set[str] = set()' \
    "$CONTEXT_DIR/hermes-agent/gateway/platforms/api_server.py" \
  && grep -Fq 'await asyncio.wait_for(asyncio.shield(task), timeout=5.0)' \
    "$CONTEXT_DIR/hermes-agent/gateway/platforms/api_server.py" || {
  printf '%s\n' 'Frozen Hermes integration patch was not materialized.' >&2
  exit 2
}
copy_profile_tree \
  "$ROOT_DIR/agents/hermes/profiles/siq_analysis" \
  "$PROJECT_DIR/agents/hermes/profiles/siq_analysis"
copy_profile_tree \
  "$ROOT_DIR/agents/hermes/profiles/shared" \
  "$PROJECT_DIR/agents/hermes/profiles/shared"
copy_profile_tree \
  "$ROOT_DIR/agents/hermes/profiles/siq_analysis" \
  "$PROJECT_DIR/data/hermes/home/profiles/siq_analysis"
copy_profile_tree \
  "$ROOT_DIR/agents/hermes/profiles/shared" \
  "$PROJECT_DIR/data/hermes/home/profiles/shared"
cp -a -- "$ROOT_DIR/AGENTS.md" "$PROJECT_DIR/AGENTS.md"
cp -a -- \
  "$FIXTURE_DIR/Dockerfile" \
  "$FIXTURE_DIR/.dockerignore" \
  "$FIXTURE_DIR/entrypoint.sh" \
  "$FIXTURE_DIR/observe-entrypoint.sh" \
  "$FIXTURE_DIR/healthcheck.py" \
  "$FIXTURE_DIR/requirements-siq-analysis.txt" \
  "$FIXTURE_DIR/siq-fetch" \
  "$FIXTURE_DIR/validate_placeholder_auth.py" \
  "$FIXTURE_DIR/validate_provider_placeholders.py" \
  "$ROOT_DIR/scripts/openshell/runtime_state_lifecycle_smoke.py" \
  "$ROOT_DIR/scripts/openshell/probe_milvus_sandbox_boundary.py" \
  "$CONTEXT_DIR/"
cp -a -- "$AUTH_TEMPLATE" "$CONTEXT_DIR/minimax-cn-auth-pool.template.json"
cp -a -- "$NODE_ARCHIVE" "$CONTEXT_DIR/node-v20.20.2-linux-arm64.tar.xz"
cp -a -- \
  "$ROOT_DIR/scripts/openshell/egress_decision.py" \
  "$ROOT_DIR/scripts/openshell/egress_guard.py" \
  "$ROOT_DIR/scripts/openshell/broker_request_identity.py" \
  "$ROOT_DIR/scripts/openshell/security_audit.py" \
  "$ROOT_DIR/scripts/openshell/siq_fetch.py" \
  "$CONTEXT_DIR/openshell-client/scripts/openshell/"
cp -a -- "$ROOT_DIR/infra/openshell/egress/allowlist.json" \
  "$CONTEXT_DIR/openshell-client/infra/openshell/egress/allowlist.json"

COMPILED_CONFIG="$PROJECT_DIR/data/hermes/home/profiles/siq_analysis/config.yaml"
CONFIG_SUMMARY="$CONTEXT_DIR/runtime-config.summary.json"
"$SCRIPT_DIR/build_siq_analysis_runtime_config.py" \
  --input "$RUNTIME_CONFIG" \
  --output "$COMPILED_CONFIG" \
  --summary-output "$CONFIG_SUMMARY" \
  >/dev/null

RUNTIME_CONFIG_SHA256="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["output_sha256"])' "$CONFIG_SUMMARY")"
SOURCE_CONFIG_SHA256="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["source_sha256"])' "$CONFIG_SUMMARY")"
PROFILE_TREE_SHA256="$(tree_digest "$PROJECT_DIR/agents/hermes/profiles/siq_analysis")"
SHARED_TREE_SHA256="$(tree_digest "$PROJECT_DIR/agents/hermes/profiles/shared")"
FIXTURE_SHA256="$({
  cd "$FIXTURE_DIR"
  sha256sum Dockerfile .dockerignore entrypoint.sh observe-entrypoint.sh healthcheck.py requirements-siq-analysis.txt siq-fetch validate_placeholder_auth.py validate_provider_placeholders.py
  (
    cd "$ROOT_DIR"
    sha256sum scripts/openshell/egress_decision.py scripts/openshell/egress_guard.py scripts/openshell/broker_request_identity.py scripts/openshell/security_audit.py scripts/openshell/siq_fetch.py
    sha256sum infra/openshell/egress/allowlist.json
    sha256sum scripts/openshell/runtime_state_lifecycle_smoke.py scripts/openshell/probe_milvus_sandbox_boundary.py
  )
  printf '%s  %s\n' "$(sha256sum "$AUTH_TEMPLATE" | awk '{print $1}')" minimax-cn-auth-pool.template.json
  printf '%s  %s\n' "$NODE_ARCHIVE_SHA256" node-v20.20.2-linux-arm64.tar.xz
} | sha256sum | awk '{print $1}')"

python3 - "$CONTEXT_DIR/SOURCE_BASELINE.json" <<PY
import json
import sys

payload = {
    "schema_version": "siq.openshell.siq_analysis_context.v1",
    "hermes_commit": "$HERMES_COMMIT",
    "hermes_patch_sha256": "$HERMES_PATCH_SHA256",
    "hermes_auth_patch_sha256": "$HERMES_PATCH_ONE_SHA256",
    "hermes_runtime_state_patch_sha256": "$HERMES_PATCH_TWO_SHA256",
    "hermes_run_quiescence_patch_sha256": "$HERMES_PATCH_THREE_SHA256",
    "hermes_integration_patch_sha256": "$HERMES_INTEGRATION_PATCH_SHA256",
    "runtime_source_config_sha256": "$SOURCE_CONFIG_SHA256",
    "runtime_config_sha256": "$RUNTIME_CONFIG_SHA256",
    "profile_tree_sha256": "$PROFILE_TREE_SHA256",
    "shared_tree_sha256": "$SHARED_TREE_SHA256",
    "fixture_sha256": "$FIXTURE_SHA256",
    "node_archive_sha256": "$NODE_ARCHIVE_SHA256",
    "node_version": "v20.20.2",
    "contains_credentials": False,
    "contains_credential_placeholders_only": True,
    "contains_wiki_data": False,
    "contains_host_runtime_state": False,
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
    handle.write("\n")
PY

python3 "$SCRIPT_DIR/check_mount_safety.py" --mount-root "$CONTEXT_DIR" >&2
(
  cd "$CONTEXT_DIR"
  find . -type f ! -name FILES.sha256 -print0 \
    | sort -z \
    | xargs -0 sha256sum \
    >FILES.sha256
)
CONTEXT_SHA256="$(sha256sum "$CONTEXT_DIR/FILES.sha256" | awk '{print $1}')"
FINAL_CONTEXT="$CONTEXTS_DIR/$CONTEXT_SHA256"

if [[ -e "$FINAL_CONTEXT" ]]; then
  [[ -d "$FINAL_CONTEXT" && ! -L "$FINAL_CONTEXT" ]] || {
    printf 'Existing context path is unsafe: %s\n' "$FINAL_CONTEXT" >&2
    exit 2
  }
  cmp -s "$CONTEXT_DIR/FILES.sha256" "$FINAL_CONTEXT/FILES.sha256" || {
    printf 'Existing context digest collision: %s\n' "$FINAL_CONTEXT" >&2
    exit 2
  }
else
  find "$CONTEXT_DIR" -type d -exec chmod 0700 {} +
  find "$CONTEXT_DIR" -type f -exec chmod 0600 {} +
  chmod 0700 "$CONTEXT_DIR/entrypoint.sh" "$CONTEXT_DIR/observe-entrypoint.sh" "$CONTEXT_DIR/healthcheck.py" "$CONTEXT_DIR/validate_placeholder_auth.py" "$CONTEXT_DIR/validate_provider_placeholders.py" "$CONTEXT_DIR/runtime_state_lifecycle_smoke.py" "$CONTEXT_DIR/probe_milvus_sandbox_boundary.py"
  mv -- "$CONTEXT_DIR" "$FINAL_CONTEXT"
fi

python3 "$SCRIPT_DIR/check_mount_safety.py" --mount-root "$FINAL_CONTEXT" >&2
printf '%s\n' "$FINAL_CONTEXT"
