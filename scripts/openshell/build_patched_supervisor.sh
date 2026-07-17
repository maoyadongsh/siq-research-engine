#!/usr/bin/env bash
# Build and atomically install the narrowly patched v0.0.83 supervisor.

set -euo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

readonly VERSION="0.0.83"
readonly UPSTREAM_COMMIT="e3d26dd3ae0dee247bbc5db368545832757ac493"
readonly PATCH_FILE="$ROOT_DIR/infra/openshell/patches/v$VERSION/0001-landlock-mask-file-access.patch"
readonly PATCH_SHA256="f38cdb0788a9c1f2a38c9aa23ab36b33c4cc6faea135bf6f04bf5eb7bbcdd12f"
readonly UPSTREAM_SOURCE_FILE_SHA256="2c2305fabdd66a42a6c2c5969dc38a9054d42e8978f09d845f62a17264ac1aa0"
readonly EXPECTED_SOURCE_DIFF_SHA256="54ffab6c665acc93d72ccd5be6087a1a93f46a4a3647e8b4be696c37aa23b929"
readonly EXPECTED_NORMALIZED_LOCK_SHA256="506ef1a75ad80318bc10725959013366a5b06ec5e8ca2839a2b96b22fb2b0e78"
readonly EXPECTED_NORMALIZED_DIFF_SHA256="b4b141f545682419620a98b96ebd46f7ffb440d2f4f3eed9f14c9f318d1dacbe"
readonly UPSTREAM_SUPERVISOR_SHA256="d94630658eb1e62090281160db7cdc542c8cf6667d0c11ff7d9084251f86cfd6"
readonly RUST_DIST_SHA256="094c9c36531911c5cc7dd6ab2d3069ab8dcd744d6239b0bda1387b243dfc391e"
readonly BUILD_ROOT="$SIQ_OPENSHELL_STATE_ROOT/build/v$VERSION"
readonly CARGO_ROOT="$BUILD_ROOT/cargo"
readonly BIN_ROOT="$SIQ_OPENSHELL_STATE_ROOT/toolchains/v$VERSION/bin"
readonly SUPERVISOR_BIN="$BIN_ROOT/openshell-sandbox"
readonly UPSTREAM_BACKUP="$BIN_ROOT/openshell-sandbox.upstream-v$VERSION"
readonly RUNTIME_RECORD="$BUILD_ROOT/supervisor-patch.runtime"
readonly BUILDER_DOCKERFILE="$ROOT_DIR/infra/openshell/patches/v$VERSION/Dockerfile.builder"
readonly BUILDER_DOCKERFILE_SHA256="$(sha256sum -- "$BUILDER_DOCKERFILE" | awk '{print $1}')"
readonly BUILDER_IMAGE="siq/openshell-supervisor-builder:v$VERSION-landlock-${BUILDER_DOCKERFILE_SHA256:0:16}"
readonly GATEWAY_ROOT="$SIQ_OPENSHELL_STATE_ROOT/gateway/siq-openshell-dev"
readonly GATEWAY_PID_FILE="$GATEWAY_ROOT/gateway.pid"
readonly GATEWAY_BIN="$SIQ_OPENSHELL_STATE_ROOT/toolchains/v$VERSION/bin/openshell-gateway"

RUN_ROOT=""
BUILT_BIN=""
gateway_was_running=0
gateway_stopped=0
installed=0
committed=0
restart_gateway_allowed=0
previous_bin=""
previous_sha=""

require_regular_file() {
  local path="$1"
  [[ -f "$path" && ! -L "$path" ]] || {
    printf 'Missing or unsafe file: %s\n' "$path" >&2
    exit 2
  }
}

verify_sha256() {
  local expected="$1" path="$2" actual
  actual="$(sha256sum -- "$path" | awk '{print $1}')"
  [[ "$actual" == "$expected" ]] || {
    printf 'SHA-256 mismatch: %s\n' "$path" >&2
    exit 2
  }
}

ensure_state_dir() {
  local path="$1"
  siq_openshell_assert_state_path "$path"
  if [[ -e "$path" && ! -d "$path" ]]; then
    printf 'Expected a directory but found another file type: %s\n' "$path" >&2
    exit 2
  fi
  install -d -m 700 -- "$path"
  siq_openshell_assert_state_path "$path"
}

sha256_matches() {
  local expected="$1" path="$2" actual
  [[ -f "$path" && ! -L "$path" ]] || return 1
  actual="$(sha256sum -- "$path" 2>/dev/null | awk '{print $1}')" || return 1
  [[ "$actual" == "$expected" ]]
}

runtime_record_matches() {
  local expected_sha="$1" recorded_active recorded_sha recorded_active_sha
  [[ -f "$RUNTIME_RECORD" && ! -L "$RUNTIME_RECORD" ]] || return 1
  recorded_active="$(awk -F= '$1 == "active" {print $2}' "$RUNTIME_RECORD")"
  recorded_sha="$(awk -F= '$1 == "patched_binary_sha256" {print $2}' "$RUNTIME_RECORD")"
  recorded_active_sha="$(awk -F= '$1 == "active_binary_sha256" {print $2}' "$RUNTIME_RECORD")"
  [[ "$recorded_active" == patched && "$recorded_sha" == "$expected_sha" && "$recorded_active_sha" == "$expected_sha" ]]
}

runtime_state_matches() {
  local expected_sha="$1" recorded_active recorded_active_sha
  if [[ "$expected_sha" != "$UPSTREAM_SUPERVISOR_SHA256" ]]; then
    runtime_record_matches "$expected_sha"
    return
  fi
  if [[ ! -e "$RUNTIME_RECORD" && ! -L "$RUNTIME_RECORD" ]]; then
    return 0
  fi
  [[ -f "$RUNTIME_RECORD" && ! -L "$RUNTIME_RECORD" ]] || return 1
  recorded_active="$(awk -F= '$1 == "active" {print $2}' "$RUNTIME_RECORD")"
  recorded_active_sha="$(awk -F= '$1 == "active_binary_sha256" {print $2}' "$RUNTIME_RECORD")"
  [[ "$recorded_active" == upstream && "$recorded_active_sha" == "$expected_sha" ]]
}

validate_current_provenance() {
  local current_sha="$1"
  if [[ "$current_sha" == "$UPSTREAM_SUPERVISOR_SHA256" ]]; then
    return 0
  fi
  runtime_record_matches "$current_sha" || {
    printf '%s\n' 'Current supervisor runtime record does not match its binary; refusing maintenance before gateway shutdown.' >&2
    return 2
  }
}

assert_no_managed_sandboxes() {
  local ids
  ids="$(docker ps -aq \
    --filter 'label=openshell.ai/managed-by=openshell' \
    --filter 'label=openshell.ai/sandbox-namespace=siq-openshell-dev')"
  [[ -z "$ids" ]] || {
    printf 'Refusing supervisor replacement while managed sandbox containers exist: %s\n' \
      "$(printf '%s' "$ids" | tr '\n' ' ')" >&2
    exit 2
  }
}

gateway_pid_is_ours() {
  local pid="$1"
  [[ "$pid" =~ ^[0-9]+$ && -e "/proc/$pid/exe" ]] || return 1
  [[ "$(readlink -f "/proc/$pid/exe")" == "$(readlink -f "$GATEWAY_BIN")" ]]
}

detect_running_gateway() {
  local pid=""
  if [[ -f "$GATEWAY_PID_FILE" && ! -L "$GATEWAY_PID_FILE" ]]; then
    pid="$(tr -cd '0-9' <"$GATEWAY_PID_FILE")"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      gateway_pid_is_ours "$pid" || {
        printf 'Gateway PID file points to an unrelated process; refusing maintenance.\n' >&2
        exit 2
      }
      gateway_was_running=1
    elif [[ -n "$pid" ]]; then
      printf 'Gateway PID file is stale; refusing maintenance.\n' >&2
      exit 2
    fi
  fi
  if curl --fail --silent --max-time 1 http://127.0.0.1:17672/healthz >/dev/null 2>&1; then
    [[ "$gateway_was_running" -eq 1 ]] || {
      printf 'Gateway health is reachable but its verified PID is missing.\n' >&2
      exit 2
    }
  else
    [[ "$gateway_was_running" -eq 0 ]] || {
      printf 'Gateway PID is live but health endpoint is unavailable.\n' >&2
      exit 2
    }
  fi
}

quiesce_gateway() {
  detect_running_gateway
  [[ "$gateway_was_running" -eq 1 ]] || {
    printf '%s\n' 'The isolated gateway must be running so its sandbox inventory can be verified.' >&2
    exit 2
  }

  local sandbox_names
  sandbox_names="$("$SCRIPT_DIR/run_cli.sh" sandbox list | sed -r 's/\x1B\[[0-9;]*[mK]//g' | awk 'NR > 1 && $1 != "No" {print $1}')"
  [[ -z "$sandbox_names" ]] || {
    printf 'Refusing supervisor replacement while OpenShell sandboxes exist: %s\n' \
      "$(printf '%s' "$sandbox_names" | tr '\n' ' ')" >&2
    exit 2
  }
  assert_no_managed_sandboxes

  SIQ_OPENSHELL_MAINTENANCE_LOCK_HELD=1 "$SCRIPT_DIR/stop_gateway.sh"
  gateway_stopped=1
  # A request already in flight cannot leave a managed container after the
  # gateway has stopped without appearing in Docker's namespace inventory.
  assert_no_managed_sandboxes
}

write_runtime_record() {
  local patched_sha="$1" temporary_record
  temporary_record="$(mktemp "$BUILD_ROOT/.supervisor-patch.runtime.XXXXXX")"
  chmod 600 -- "$temporary_record"
  {
    printf 'schema=siq.openshell.supervisor_patch.v1\n'
    printf 'active=patched\n'
    printf 'version=%s\n' "$VERSION"
    printf 'upstream_commit=%s\n' "$UPSTREAM_COMMIT"
    printf 'patch_sha256=%s\n' "$PATCH_SHA256"
    printf 'upstream_binary_sha256=%s\n' "$UPSTREAM_SUPERVISOR_SHA256"
    printf 'patched_binary_sha256=%s\n' "$patched_sha"
    printf 'active_binary_sha256=%s\n' "$patched_sha"
    printf 'installed_path=%s\n' "var/openshell/toolchains/v$VERSION/bin/openshell-sandbox"
  } >"$temporary_record"
  sync -f -- "$temporary_record" 2>/dev/null || true
  mv -f -- "$temporary_record" "$RUNTIME_RECORD"
}

restart_gateway_on_exit() {
  local status="$1"
  if [[ "$gateway_stopped" -eq 1 ]]; then
    if [[ "$restart_gateway_allowed" -ne 1 ]]; then
      printf '%s\n' 'WARNING: isolated gateway remains stopped because supervisor recovery was not verified.' >&2
      return 2
    fi
    if ! SIQ_OPENSHELL_MAINTENANCE_LOCK_HELD=1 "$SCRIPT_DIR/start_gateway.sh"; then
      printf '%s\n' 'WARNING: isolated gateway could not be restarted automatically.' >&2
      status=2
    fi
  fi
  return "$status"
}

cleanup() {
  local status=$? rollback_bin="" rollback_ok=1
  set +e
  if [[ "$status" -ne 0 && "$installed" -eq 1 && "$committed" -eq 0 && -n "$previous_bin" && -f "$previous_bin" ]]; then
    rollback_bin="$(mktemp "$BIN_ROOT/.openshell-sandbox.rollback.XXXXXX")" || rollback_ok=0
    if [[ "$rollback_ok" -eq 1 ]]; then
      install -m 0700 -- "$previous_bin" "$rollback_bin" || rollback_ok=0
    fi
    if [[ "$rollback_ok" -eq 1 ]] && ! sha256_matches "$previous_sha" "$rollback_bin"; then
      rollback_ok=0
    fi
    if [[ "$rollback_ok" -eq 1 ]]; then
      mv -f -- "$rollback_bin" "$SUPERVISOR_BIN" || rollback_ok=0
    fi
    if [[ "$rollback_ok" -eq 1 ]] && ! sha256_matches "$previous_sha" "$SUPERVISOR_BIN"; then
      rollback_ok=0
    fi
    if [[ "$rollback_ok" -eq 1 ]] && ! runtime_state_matches "$previous_sha"; then
      rollback_ok=0
    fi
    if [[ "$rollback_ok" -eq 1 ]]; then
      restart_gateway_allowed=1
      printf '%s\n' 'Restored and verified the previous supervisor after an incomplete installation.' >&2
    else
      restart_gateway_allowed=0
      status=2
      printf '%s\n' 'ERROR: supervisor rollback failed verification; the gateway will remain stopped.' >&2
    fi
  fi
  [[ -n "$rollback_bin" && -f "$rollback_bin" ]] && rm -f -- "$rollback_bin"
  [[ -n "$previous_bin" && -f "$previous_bin" ]] && rm -f -- "$previous_bin"
  if [[ -n "$RUN_ROOT" && -d "$RUN_ROOT" && ! -L "$RUN_ROOT" ]]; then
    rm -rf -- "$RUN_ROOT"
  fi
  restart_gateway_on_exit "$status"
  exit $?
}
trap cleanup EXIT

require_regular_file "$PATCH_FILE"
require_regular_file "$BUILDER_DOCKERFILE"
verify_sha256 "$PATCH_SHA256" "$PATCH_FILE"

siq_openshell_acquire_maintenance_lock
ensure_state_dir "$BUILD_ROOT"
ensure_state_dir "$CARGO_ROOT"
siq_openshell_assert_state_path "$BIN_ROOT"
require_regular_file "$SUPERVISOR_BIN"

RUN_ROOT="$(mktemp -d "$BUILD_ROOT/run.XXXXXX")"
chmod 700 -- "$RUN_ROOT"
SOURCE_DIR="$RUN_ROOT/OpenShell"
TARGET_ROOT="$RUN_ROOT/target"
ensure_state_dir "$TARGET_ROOT"

git clone --quiet --depth 1 --branch "v$VERSION" \
  https://github.com/NVIDIA/OpenShell.git "$SOURCE_DIR"
[[ "$(git -C "$SOURCE_DIR" rev-parse HEAD)" == "$UPSTREAM_COMMIT" ]] || {
  printf '%s\n' 'OpenShell tag resolved to an unexpected commit.' >&2
  exit 2
}
source_file="$SOURCE_DIR/crates/openshell-supervisor-process/src/sandbox/linux/landlock.rs"
verify_sha256 "$UPSTREAM_SOURCE_FILE_SHA256" "$source_file"
git -C "$SOURCE_DIR" apply --check "$PATCH_FILE"
git -C "$SOURCE_DIR" apply "$PATCH_FILE"
sed -i -E '/^\[workspace\.package\]/,/^\[/{s/^version[[:space:]]*=[[:space:]]*".*"/version = "0.0.83"/}' "$SOURCE_DIR/Cargo.toml"
grep -q '^version = "0.0.83"' "$SOURCE_DIR/Cargo.toml"
git -C "$SOURCE_DIR" diff --check
changed_files="$(git -C "$SOURCE_DIR" diff --name-only)"
expected_changed_files=$'Cargo.toml\ncrates/openshell-supervisor-process/src/sandbox/linux/landlock.rs'
[[ "$changed_files" == "$expected_changed_files" ]] || {
  printf '%s\n' 'OpenShell source contains an unexpected tracked change.' >&2
  exit 2
}
source_diff_sha="$(git -C "$SOURCE_DIR" diff --binary | sha256sum | awk '{print $1}')"
[[ "$source_diff_sha" == "$EXPECTED_SOURCE_DIFF_SHA256" ]] || {
  printf '%s\n' 'OpenShell patched source diff does not match the reviewed baseline.' >&2
  exit 2
}
[[ -z "$(git -C "$SOURCE_DIR" status --porcelain=v1 --untracked-files=all | grep '^??' || true)" ]] || {
  printf '%s\n' 'Unexpected untracked files appeared in the OpenShell source.' >&2
  exit 2
}

builder_patch_label=""
if ! docker image inspect "$BUILDER_IMAGE" >/dev/null 2>&1; then
  docker build --pull=false --platform linux/arm64 --tag "$BUILDER_IMAGE" \
    --build-arg "SIQ_SUPERVISOR_PATCH_SHA256=$PATCH_SHA256" \
    --build-arg "SIQ_OPENSHELL_UPSTREAM_COMMIT=$UPSTREAM_COMMIT" \
    --build-arg "SIQ_OPENSHELL_BUILDER_DOCKERFILE_SHA256=$BUILDER_DOCKERFILE_SHA256" \
    --file "$BUILDER_DOCKERFILE" \
    "$ROOT_DIR/infra/openshell/patches/v$VERSION" >&2
fi

builder_patch_label="$(docker image inspect "$BUILDER_IMAGE" --format '{{index .Config.Labels "ai.siq.openshell.supervisor-patch-sha256"}}')"
builder_commit_label="$(docker image inspect "$BUILDER_IMAGE" --format '{{index .Config.Labels "ai.siq.openshell.upstream-commit"}}')"
builder_rust_label="$(docker image inspect "$BUILDER_IMAGE" --format '{{index .Config.Labels "ai.siq.openshell.rust-dist-sha256"}}')"
builder_dockerfile_label="$(docker image inspect "$BUILDER_IMAGE" --format '{{index .Config.Labels "ai.siq.openshell.builder-dockerfile-sha256"}}')"
[[ "$builder_patch_label" == "$PATCH_SHA256" ]] || { printf '%s\n' 'Builder image has an unexpected patch label.' >&2; exit 2; }
[[ "$builder_commit_label" == "$UPSTREAM_COMMIT" ]] || { printf '%s\n' 'Builder image has an unexpected upstream commit label.' >&2; exit 2; }
[[ "$builder_rust_label" == "$RUST_DIST_SHA256" ]] || { printf '%s\n' 'Builder image has an unexpected Rust distribution label.' >&2; exit 2; }
[[ "$builder_dockerfile_label" == "$BUILDER_DOCKERFILE_SHA256" ]] || { printf '%s\n' 'Builder image has an unexpected Dockerfile label.' >&2; exit 2; }

# NVIDIA injects the release version into Cargo.toml during packaging. Let
# Cargo update only workspace package versions, then require the reviewed
# Cargo.lock and full source diff hashes before any dependency is fetched.
docker run --rm --platform linux/arm64 --network=none \
  --cap-drop=ALL --security-opt=no-new-privileges --read-only \
  --tmpfs /tmp:rw,nosuid,nodev --user "$(id -u):$(id -g)" \
  --volume "$SOURCE_DIR:/src" --volume "$CARGO_ROOT:/cargo" \
  --workdir /src --env HOME=/tmp --env CARGO_HOME=/cargo \
  --env CARGO_NET_OFFLINE=true \
  "$BUILDER_IMAGE" cargo update --workspace --offline >&2
verify_sha256 "$EXPECTED_NORMALIZED_LOCK_SHA256" "$SOURCE_DIR/Cargo.lock"
normalized_changed_files="$(git -C "$SOURCE_DIR" diff --name-only)"
expected_normalized_files=$'Cargo.lock\nCargo.toml\ncrates/openshell-supervisor-process/src/sandbox/linux/landlock.rs'
[[ "$normalized_changed_files" == "$expected_normalized_files" ]] || {
  printf '%s\n' 'Cargo version normalization changed unexpected files.' >&2
  exit 2
}
normalized_diff_sha="$(git -C "$SOURCE_DIR" diff --binary | sha256sum | awk '{print $1}')"
[[ "$normalized_diff_sha" == "$EXPECTED_NORMALIZED_DIFF_SHA256" ]] || {
  printf '%s\n' 'Cargo version normalization does not match the reviewed source diff.' >&2
  exit 2
}
[[ -z "$(git -C "$SOURCE_DIR" status --porcelain=v1 --untracked-files=all | grep '^??' || true)" ]] || {
  printf '%s\n' 'Unexpected untracked files appeared during Cargo version normalization.' >&2
  exit 2
}

common_docker_args=(
  --rm --platform linux/arm64
  --cap-drop=ALL
  --security-opt=no-new-privileges
  --read-only
  --tmpfs /tmp:rw,nosuid,nodev
  --user "$(id -u):$(id -g)"
  --volume "$SOURCE_DIR:/src:ro"
  --volume "$CARGO_ROOT:/cargo"
  --volume "$TARGET_ROOT:/target"
  --workdir /src
  --env HOME=/tmp
  --env CARGO_HOME=/cargo
  --env CARGO_TARGET_DIR=/target
  --env CARGO_TERM_COLOR=never
  --env GIT_DIR=/nonexistent
)

# Fetch is the only networked build phase. The actual tests and release build
# run offline, so no dependency can change after Cargo.lock is checked.
docker run "${common_docker_args[@]}" \
  "$BUILDER_IMAGE" bash -lc 'cargo fetch --locked --target aarch64-unknown-linux-gnu' >&2
docker run "${common_docker_args[@]}" --network=none \
  --env CARGO_NET_OFFLINE=true \
  "$BUILDER_IMAGE" bash -lc 'cargo test --locked --target aarch64-unknown-linux-gnu -p openshell-supervisor-process sandbox::linux::landlock::tests && cargo build --locked --release --target aarch64-unknown-linux-gnu -p openshell-sandbox --bin openshell-sandbox' >&2

BUILT_BIN="$TARGET_ROOT/aarch64-unknown-linux-gnu/release/openshell-sandbox"
require_regular_file "$BUILT_BIN"
file_output="$(file -b "$BUILT_BIN")"
[[ "$file_output" == *'ELF 64-bit LSB pie executable, ARM aarch64'* ]] || { printf 'Patched supervisor is not an ARM64 ELF: %s\n' "$file_output" >&2; exit 2; }
readelf -l "$BUILT_BIN" | grep -q 'Requesting program interpreter: /lib/ld-linux-aarch64.so.1' || {
  printf '%s\n' 'Patched supervisor is not the expected GNU dynamic binary.' >&2
  exit 2
}
# Execute only inside the isolated builder image, with no network/capabilities.
version_output="$(docker run --rm --platform linux/arm64 --network=none --cap-drop=ALL \
  --security-opt=no-new-privileges --read-only --tmpfs /tmp:rw,nosuid,nodev \
  --user 65534:65534 --volume "$BUILT_BIN:/usr/local/bin/openshell-sandbox:ro" \
  "$BUILDER_IMAGE" /usr/local/bin/openshell-sandbox --version)"
[[ "$version_output" == "openshell-sandbox $VERSION" ]] || { printf 'Patched supervisor version mismatch: %s\n' "$version_output" >&2; exit 2; }

current_sha="$(sha256sum -- "$SUPERVISOR_BIN" | awk '{print $1}')"
validate_current_provenance "$current_sha"
restart_gateway_allowed=1
quiesce_gateway
restart_gateway_allowed=0
sha256_matches "$current_sha" "$SUPERVISOR_BIN" || {
  printf '%s\n' 'Supervisor binary changed while the gateway was being quiesced; leaving the gateway stopped.' >&2
  exit 2
}
restart_gateway_allowed=1

if [[ -e "$UPSTREAM_BACKUP" || -L "$UPSTREAM_BACKUP" ]]; then
  require_regular_file "$UPSTREAM_BACKUP"
else
  [[ "$current_sha" == "$UPSTREAM_SUPERVISOR_SHA256" ]] || {
    printf '%s\n' 'Cannot create an upstream backup from a non-upstream supervisor.' >&2
    exit 2
  }
  temporary_backup="$(mktemp "$BIN_ROOT/.openshell-sandbox.upstream.XXXXXX")"
  install -m 0700 -- "$SUPERVISOR_BIN" "$temporary_backup"
  verify_sha256 "$UPSTREAM_SUPERVISOR_SHA256" "$temporary_backup"
  mv -f -- "$temporary_backup" "$UPSTREAM_BACKUP"
fi
verify_sha256 "$UPSTREAM_SUPERVISOR_SHA256" "$UPSTREAM_BACKUP"

patched_sha="$(sha256sum -- "$BUILT_BIN" | awk '{print $1}')"
previous_bin="$(mktemp "$BIN_ROOT/.openshell-sandbox.previous.XXXXXX")"
install -m 0700 -- "$SUPERVISOR_BIN" "$previous_bin"
verify_sha256 "$current_sha" "$previous_bin"
previous_sha="$current_sha"
temporary_bin="$(mktemp "$BIN_ROOT/.openshell-sandbox.patched.XXXXXX")"
install -m 0700 -- "$BUILT_BIN" "$temporary_bin"
verify_sha256 "$patched_sha" "$temporary_bin"
installed=1
mv -f -- "$temporary_bin" "$SUPERVISOR_BIN"
sha256_matches "$patched_sha" "$SUPERVISOR_BIN" || {
  printf '%s\n' 'Installed patched supervisor failed final SHA-256 verification.' >&2
  exit 2
}
write_runtime_record "$patched_sha"
committed=1
rm -f -- "$previous_bin"
previous_bin=""

printf 'Patched OpenShell supervisor installed: %s\n' "$patched_sha"
