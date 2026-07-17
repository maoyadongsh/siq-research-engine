#!/usr/bin/env bash
# Build and atomically install the SIQ-restricted OpenShell v0.0.83 gateway.

set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

readonly VERSION="0.0.83"
readonly UPSTREAM_COMMIT="e3d26dd3ae0dee247bbc5db368545832757ac493"
readonly PATCH_FILE="$ROOT_DIR/infra/openshell/patches/v$VERSION/0002-siq-strict-bind-mount-contract.patch"
readonly PATCH_SHA256="a877673ef005212049b860168c3401651e189beb96d39489fdea53fac61c2752"
readonly LEGACY_MIGRATION_VERIFIER="$SCRIPT_DIR/gateway_patch_migration.py"
readonly LEGACY_MIGRATION_SOURCE_PATCH_SHA256="64026fc68cdc0177297cfe648cfaf84abcf7630b04fee5280a1491b882d48dc4"
readonly LEGACY_MIGRATION_SOURCE_BINARY_SHA256="9f26b7c3e7af2eefdf0c22eef82472422865aa63c114091ccdc25ea9968cff00"
readonly LEGACY_MIGRATION_RUNTIME_RECORD_SHA256="19fd64bc3f6f384dec7bb462a76a07cf88b87edbb5ca9dbc54ae9e18d800b637"
readonly LEGACY_MIGRATION_TARGET_PATCH_SHA256="a877673ef005212049b860168c3401651e189beb96d39489fdea53fac61c2752"
readonly UPSTREAM_DRIVER_LIB_SHA256="530a0da03c733ff3d6911635af809a97a45e787732e3bc52fd01b8dad0e79e3b"
readonly UPSTREAM_DRIVER_TESTS_SHA256="04f2ddc83df477f1300dbb0c2841892bfdb2c3e51b07fa8ba1ff7b69dd259125"
readonly UPSTREAM_CARGO_TOML_SHA256="7571bc7f6b66b26b31f4db5c87a788f7d931a547b4a72ab9f493c86756920567"
readonly UPSTREAM_CARGO_LOCK_SHA256="d8c5fad7fd234bd9ecdcdbecbb531d5d36535de9298a4703870ddc631ca92ffe"
readonly EXPECTED_PATCH_DIFF_SHA256="a877673ef005212049b860168c3401651e189beb96d39489fdea53fac61c2752"
readonly EXPECTED_PRELOCK_DIFF_SHA256="f01f92271dca8902152957bb88d16b55e9173ed770583d0540cea18a5101b105"
readonly EXPECTED_NORMALIZED_LOCK_SHA256="506ef1a75ad80318bc10725959013366a5b06ec5e8ca2839a2b96b22fb2b0e78"
readonly EXPECTED_NORMALIZED_DIFF_SHA256="c26453717c68741678803452b59e1e5a072395510e9d292e7dc9bc77aa3a9484"
readonly UPSTREAM_GATEWAY_SHA256="198591e1e13b9cee94f0b7eb5875c6db484a3bcc9b371225cebc528c6116a31e"
readonly SUPERVISOR_PATCH_SHA256="f38cdb0788a9c1f2a38c9aa23ab36b33c4cc6faea135bf6f04bf5eb7bbcdd12f"
readonly RUST_DIST_SHA256="094c9c36531911c5cc7dd6ab2d3069ab8dcd744d6239b0bda1387b243dfc391e"
readonly Z3_COMMIT="ddb49568d3520e99799e364fb22f35fc67d887b1"
readonly Z3_ARCHIVE_SHA256="34deac6d0d46002b1040c56a51c4385ebb4ea56baa95fa8dd66e315a25b0cfa6"
readonly Z3_ARCHIVE_URL="https://codeload.github.com/Z3Prover/z3/tar.gz/$Z3_COMMIT"
readonly VERIFIED_BUILDER_ID="sha256:2c2e2bbd5c7f544d0505129a76d0ca6dc51603484c347a2bd5e35e91e67cbcf8"
readonly VERIFIED_BUILDER_IMAGE="siq/openshell-supervisor-builder:v$VERSION-landlock-3a31194c7b9cf7f9"
readonly UBUNTU_BASE_DIGEST="ubuntu@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90"
readonly UBUNTU_BASE_LABEL="ubuntu-24.04-arm64@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90"
readonly UBUNTU_APT_MIRROR="http://mirrors.aliyun.com/ubuntu-ports"
readonly EXPECTED_BUILDER_DOCKERFILE_SHA256="7df3c6f16fa35f344234d10298a868e8d4ff2207d883038808a0f5d316620a18"
readonly EXPECTED_BUILDER_PACKAGES_SHA256="d14a6597abe3bc068a8347ef78312102a0127b7ff8b1f9e453809c61b9c3fb9c"
readonly BUILD_ROOT="$SIQ_OPENSHELL_STATE_ROOT/build/v$VERSION"
readonly CARGO_ROOT="$BUILD_ROOT/cargo"
readonly DOWNLOAD_ROOT="$BUILD_ROOT/downloads"
readonly Z3_ARCHIVE="$DOWNLOAD_ROOT/z3-$Z3_COMMIT.tar.gz"
readonly BIN_ROOT="$SIQ_OPENSHELL_STATE_ROOT/toolchains/v$VERSION/bin"
readonly GATEWAY_BIN="$BIN_ROOT/openshell-gateway"
readonly UPSTREAM_BACKUP="$BIN_ROOT/openshell-gateway.upstream-v$VERSION"
readonly RUNTIME_RECORD="$BUILD_ROOT/gateway-patch.runtime"
readonly BUILDER_DOCKERFILE="$ROOT_DIR/infra/openshell/patches/v$VERSION/Dockerfile.gateway-builder"
readonly BUILDER_DOCKERFILE_SHA256="$(sha256sum -- "$BUILDER_DOCKERFILE" | awk '{print $1}')"
readonly BUILDER_IMAGE="siq/openshell-gateway-builder:v$VERSION-bind-${PATCH_SHA256:0:16}-${BUILDER_DOCKERFILE_SHA256:0:12}"
readonly GATEWAY_ROOT="$SIQ_OPENSHELL_STATE_ROOT/gateway/siq-openshell-dev"
readonly GATEWAY_PID_FILE="$GATEWAY_ROOT/gateway.pid"
readonly GATEWAY_PROCESS_RECORD="$GATEWAY_ROOT/gateway.runtime.json"
readonly GATEWAY_CONFIG="$GATEWAY_ROOT/gateway.toml"
readonly INSTALL_JOURNAL="$BUILD_ROOT/gateway-install.transaction"
readonly RECOVERY_MODE="${SIQ_OPENSHELL_GATEWAY_RECOVERY_MODE:-auto}"

RUN_ROOT=""
BUILT_BIN=""
Z3_SOURCE_ROOT=""
gateway_was_running=0
gateway_stopped=0
staged_gateway_running=0
installed=0
committed=0
restart_gateway_allowed=0
previous_bin=""
previous_record=""
previous_record_existed=0
previous_sha=""
patched_sha=""
preserve_recovery=0
gateway_config_sha=""
runtime_record_snapshot=""
gateway_binary_snapshot=""
gateway_pid_snapshot=""
gateway_start_ticks_snapshot=""
gateway_executable_snapshot=""
gateway_argv_sha_snapshot=""
protected_listener_digest=""
gateway_listener_digest=""
runtime_snapshot_captured=0
journal_phase=""
journal_created_at=""
journal_runtime_record_backup=""
journal_runtime_record_existed=0
journal_gateway_was_running=0
journal_protected_listener_digest=""
journal_gateway_listener_digest=""
transaction_owned=0
cleanup_running=0
recovery_result=""
recovery_gateway_running=0
recovery_gateway_pid=""
declare -A protected_listener_snapshots=()
declare -A gateway_listener_snapshots=()
readonly PROTECTED_PORTS=(8004 8006 8007 8013 18651 18789 18792 18793)
readonly GATEWAY_PORTS=(17671 17672)

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

sha256_matches() {
  local expected="$1" path="$2" actual
  [[ -f "$path" && ! -L "$path" ]] || return 1
  actual="$(sha256sum -- "$path" 2>/dev/null | awk '{print $1}')" || return 1
  [[ "$actual" == "$expected" ]]
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

require_private_regular_file() {
  local path="$1" mode owner
  require_regular_file "$path"
  owner="$(stat -c '%u' -- "$path")"
  mode="$(stat -c '%a' -- "$path")"
  [[ "$owner" == "$(id -u)" && $((8#$mode & 8#077)) -eq 0 ]] || {
    printf 'Private recovery file has unsafe ownership or mode: %s\n' "$path" >&2
    return 2
  }
}

fsync_regular_file() {
  local path="$1"
  python3 - "$path" <<'PY'
import os
import stat
import sys

path = sys.argv[1]
flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
descriptor = os.open(path, flags)
try:
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        raise SystemExit(2)
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
}

fsync_directory() {
  local path="$1"
  python3 - "$path" <<'PY'
import os
import stat
import sys

path = sys.argv[1]
flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
descriptor = os.open(path, flags)
try:
    info = os.fstat(descriptor)
    if not stat.S_ISDIR(info.st_mode):
        raise SystemExit(2)
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
}

utc_timestamp() {
  date -u +'%Y-%m-%dT%H:%M:%SZ'
}

runtime_record_fingerprint() {
  if [[ ! -e "$RUNTIME_RECORD" && ! -L "$RUNTIME_RECORD" ]]; then
    printf '%s' absent
    return 0
  fi
  require_private_regular_file "$RUNTIME_RECORD"
  sha256sum -- "$RUNTIME_RECORD" | awk '{print $1}'
}

process_start_ticks() {
  local pid="$1" stat_line remainder
  local -a stat_fields=()
  stat_line="$(<"/proc/$pid/stat")"
  remainder="${stat_line##*) }"
  read -r -a stat_fields <<<"$remainder"
  [[ "${#stat_fields[@]}" -ge 20 && "${stat_fields[19]}" =~ ^[0-9]+$ ]] || return 2
  printf '%s' "${stat_fields[19]}"
}

read_gateway_pid_file() {
  local pid
  require_private_regular_file "$GATEWAY_PID_FILE"
  pid="$(<"$GATEWAY_PID_FILE")"
  [[ "$pid" =~ ^[0-9]+$ ]] || {
    printf '%s\n' 'Gateway PID file is malformed.' >&2
    return 2
  }
  printf '%s' "$pid"
}

prepare_z3_source() {
  local temporary_archive="" prefix="z3-$Z3_COMMIT"
  ensure_state_dir "$DOWNLOAD_ROOT"
  if [[ ! -e "$Z3_ARCHIVE" && ! -L "$Z3_ARCHIVE" ]]; then
    temporary_archive="$(mktemp "$RUN_ROOT/.z3-$Z3_COMMIT.XXXXXX")"
    chmod 600 -- "$temporary_archive"
    curl --fail --silent --show-error --location --retry 1 \
      --proto '=https' --tlsv1.2 --max-time 1200 \
      --output "$temporary_archive" "$Z3_ARCHIVE_URL"
    verify_sha256 "$Z3_ARCHIVE_SHA256" "$temporary_archive"
    mv -f -- "$temporary_archive" "$Z3_ARCHIVE"
  fi
  require_regular_file "$Z3_ARCHIVE"
  verify_sha256 "$Z3_ARCHIVE_SHA256" "$Z3_ARCHIVE"
  tar -tzf "$Z3_ARCHIVE" | awk -F/ -v prefix="$prefix" '
    $1 != prefix { bad = 1 }
    { for (i = 1; i <= NF; i++) if ($i == "..") bad = 1 }
    END { exit bad }
  ' || {
    printf '%s\n' 'Pinned Z3 archive contains an unsafe path.' >&2
    exit 2
  }
  tar -tvzf "$Z3_ARCHIVE" | awk '
    substr($1, 1, 1) == "l" || substr($1, 1, 1) == "h" { bad = 1 }
    END { exit bad }
  ' || {
    printf '%s\n' 'Pinned Z3 archive contains a link entry.' >&2
    exit 2
  }
  Z3_SOURCE_ROOT="$RUN_ROOT/z3-source"
  install -d -m 700 -- "$Z3_SOURCE_ROOT"
  tar --extract --gzip --file "$Z3_ARCHIVE" --directory "$Z3_SOURCE_ROOT" \
    --strip-components=1 --no-same-owner --no-same-permissions
  require_regular_file "$Z3_SOURCE_ROOT/CMakeLists.txt"
  [[ -z "$(find "$Z3_SOURCE_ROOT" -type l -print -quit)" ]] || {
    printf '%s\n' 'Extracted Z3 source contains an unexpected symlink.' >&2
    exit 2
  }
}

listener_snapshot() {
  local port="$1"
  ss -H -ltnp "sport = :$port" 2>/dev/null | LC_ALL=C sort
}

gateway_listener_snapshot() {
  local port="$1"
  ss -H -ltn "sport = :$port" 2>/dev/null | LC_ALL=C sort
}

listener_set_digest() {
  local snapshot_kind="$1" port
  case "$snapshot_kind" in
    protected)
      for port in "${PROTECTED_PORTS[@]}"; do
        printf '%s\0%s\0' "$port" "$(listener_snapshot "$port")"
      done
      ;;
    gateway)
      for port in "${GATEWAY_PORTS[@]}"; do
        printf '%s\0%s\0' "$port" "$(gateway_listener_snapshot "$port")"
      done
      ;;
    *) return 2 ;;
  esac | sha256sum | awk '{print $1}'
}

# Capture gateway identity plus unrelated model/broker listeners before any
# network fetch, test, or release build can outlive the observed runtime.
capture_protected_runtime() {
  local port pid
  require_private_regular_file "$GATEWAY_CONFIG"
  gateway_was_running=0
  detect_running_gateway
  [[ "$gateway_was_running" -eq 1 ]] || {
    printf '%s\n' 'The isolated gateway must be healthy before the protected runtime snapshot.' >&2
    return 2
  }
  assert_gateway_inventory_empty
  for port in "${PROTECTED_PORTS[@]}"; do
    protected_listener_snapshots["$port"]="$(listener_snapshot "$port")"
  done
  for port in "${GATEWAY_PORTS[@]}"; do
    gateway_listener_snapshots["$port"]="$(gateway_listener_snapshot "$port")"
  done
  gateway_config_sha="$(sha256sum -- "$GATEWAY_CONFIG" | awk '{print $1}')"
  runtime_record_snapshot="$(runtime_record_fingerprint)"
  gateway_binary_snapshot="$(sha256sum -- "$GATEWAY_BIN" | awk '{print $1}')"
  pid="$(read_gateway_pid_file)"
  gateway_pid_snapshot="$pid"
  gateway_start_ticks_snapshot="$(process_start_ticks "$pid")"
  gateway_executable_snapshot="$(readlink -f "/proc/$pid/exe")"
  gateway_argv_sha_snapshot="$(sha256sum -- "/proc/$pid/cmdline" | awk '{print $1}')"
  protected_listener_digest="$(listener_set_digest protected)"
  gateway_listener_digest="$(listener_set_digest gateway)"
  runtime_snapshot_captured=1
}

assert_external_runtime_unchanged() {
  local checkpoint="$1" port current
  [[ "$runtime_snapshot_captured" -eq 1 ]] || {
    printf '%s\n' 'Protected runtime snapshot is unavailable.' >&2
    return 2
  }
  for port in "${PROTECTED_PORTS[@]}"; do
    current="$(listener_snapshot "$port")"
    [[ "$current" == "${protected_listener_snapshots[$port]}" ]] || {
      printf 'Protected listener changed at %s: %s\n' "$checkpoint" "$port" >&2
      return 2
    }
  done
  [[ "$(listener_set_digest protected)" == "$protected_listener_digest" ]] || {
    printf 'Protected listener digest changed at %s.\n' "$checkpoint" >&2
    return 2
  }
  sha256_matches "$gateway_config_sha" "$GATEWAY_CONFIG" || {
    printf 'Gateway configuration changed at %s.\n' "$checkpoint" >&2
    return 2
  }
}

assert_gateway_endpoint_shape_unchanged() {
  local checkpoint="$1" port current
  for port in "${GATEWAY_PORTS[@]}"; do
    current="$(gateway_listener_snapshot "$port")"
    [[ "$current" == "${gateway_listener_snapshots[$port]}" ]] || {
      printf 'Gateway listener changed at %s: %s\n' "$checkpoint" "$port" >&2
      return 2
    }
  done
  [[ "$(listener_set_digest gateway)" == "$gateway_listener_digest" ]] || {
    printf 'Gateway listener digest changed at %s.\n' "$checkpoint" >&2
    return 2
  }
}

assert_preinstall_runtime_unchanged() {
  local checkpoint="$1" pid
  assert_external_runtime_unchanged "$checkpoint"
  sha256_matches "$gateway_binary_snapshot" "$GATEWAY_BIN" || {
    printf 'Gateway binary changed at %s.\n' "$checkpoint" >&2
    return 2
  }
  [[ "$(runtime_record_fingerprint)" == "$runtime_record_snapshot" ]] || {
    printf 'Gateway runtime record changed at %s.\n' "$checkpoint" >&2
    return 2
  }
  pid="$(read_gateway_pid_file)"
  [[ "$pid" == "$gateway_pid_snapshot" \
    && "$(process_start_ticks "$pid")" == "$gateway_start_ticks_snapshot" \
    && "$(readlink -f "/proc/$pid/exe")" == "$gateway_executable_snapshot" \
    && "$(sha256sum -- "/proc/$pid/cmdline" | awk '{print $1}')" == "$gateway_argv_sha_snapshot" ]] || {
    printf 'Gateway process identity changed at %s.\n' "$checkpoint" >&2
    return 2
  }
  curl --fail --silent --max-time 1 http://127.0.0.1:17672/healthz >/dev/null || {
    printf 'Gateway health changed at %s.\n' "$checkpoint" >&2
    return 2
  }
  assert_gateway_endpoint_shape_unchanged "$checkpoint"
  assert_gateway_inventory_empty
}

assert_bind_mounts_inactive() {
  local config="$GATEWAY_ROOT/gateway.toml"
  require_regular_file "$config"
  [[ "$(grep -c '^enable_bind_mounts = false$' "$config" || true)" == 1 ]] || {
    printf '%s\n' 'Gateway binary maintenance requires bind mounts to remain inactive.' >&2
    return 2
  }
  if grep -Eq '^(bind_mount_contract|bind_mount_project_root)[[:space:]]*=' "$config"; then
    printf '%s\n' 'Gateway binary maintenance found an active bind-mount contract.' >&2
    return 2
  fi
}

runtime_record_matches_state() {
  local expected_sha="$1" expected_state="$2" recorded_state recorded_active recorded_sha recorded_active_sha recorded_builder_id
  [[ -f "$RUNTIME_RECORD" && ! -L "$RUNTIME_RECORD" ]] || return 1
  recorded_state="$(awk -F= '$1 == "state" {print $2}' "$RUNTIME_RECORD")"
  recorded_active="$(awk -F= '$1 == "active" {print $2}' "$RUNTIME_RECORD")"
  recorded_sha="$(awk -F= '$1 == "patched_binary_sha256" {print $2}' "$RUNTIME_RECORD")"
  recorded_active_sha="$(awk -F= '$1 == "active_binary_sha256" {print $2}' "$RUNTIME_RECORD")"
  recorded_builder_id="$(awk -F= '$1 == "builder_image_id" {print $2}' "$RUNTIME_RECORD")"
  [[ "$recorded_state" == "$expected_state" \
    && "$recorded_active" == patched \
    && "$recorded_sha" == "$expected_sha" \
    && "$recorded_active_sha" == "$expected_sha" \
    && "$(awk -F= '$1 == "version" {print $2}' "$RUNTIME_RECORD")" == "$VERSION" \
    && "$(awk -F= '$1 == "upstream_commit" {print $2}' "$RUNTIME_RECORD")" == "$UPSTREAM_COMMIT" \
    && "$(awk -F= '$1 == "patch_sha256" {print $2}' "$RUNTIME_RECORD")" == "$PATCH_SHA256" \
    && "$(awk -F= '$1 == "normalized_source_diff_sha256" {print $2}' "$RUNTIME_RECORD")" == "$EXPECTED_NORMALIZED_DIFF_SHA256" \
    && "$recorded_builder_id" =~ ^sha256:[0-9a-f]{64}$ \
    && "$(awk -F= '$1 == "builder_dockerfile_sha256" {print $2}' "$RUNTIME_RECORD")" == "$EXPECTED_BUILDER_DOCKERFILE_SHA256" \
    && "$(awk -F= '$1 == "builder_packages_sha256" {print $2}' "$RUNTIME_RECORD")" == "$EXPECTED_BUILDER_PACKAGES_SHA256" \
    && "$(awk -F= '$1 == "builder_base" {print $2}' "$RUNTIME_RECORD")" == "$UBUNTU_BASE_LABEL" \
    && "$(awk -F= '$1 == "builder_apt_mirror" {print $2}' "$RUNTIME_RECORD")" == "$UBUNTU_APT_MIRROR" \
    && "$(awk -F= '$1 == "z3_commit" {print $2}' "$RUNTIME_RECORD")" == "$Z3_COMMIT" \
    && "$(awk -F= '$1 == "z3_archive_sha256" {print $2}' "$RUNTIME_RECORD")" == "$Z3_ARCHIVE_SHA256" \
    && "$(awk -F= '$1 == "upstream_binary_sha256" {print $2}' "$RUNTIME_RECORD")" == "$UPSTREAM_GATEWAY_SHA256" ]]
}

runtime_record_matches() {
  runtime_record_matches_state "$1" committed
}

runtime_state_matches() {
  local expected_sha="$1" recorded_active recorded_active_sha
  if [[ "$expected_sha" != "$UPSTREAM_GATEWAY_SHA256" ]]; then
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

legacy_runtime_record_allows_upgrade() {
  local current_sha="$1"
  [[ "$PATCH_SHA256" == "$LEGACY_MIGRATION_TARGET_PATCH_SHA256" \
    && "$current_sha" == "$LEGACY_MIGRATION_SOURCE_BINARY_SHA256" ]] || return 1
  require_regular_file "$LEGACY_MIGRATION_VERIFIER"
  sha256_matches "$LEGACY_MIGRATION_RUNTIME_RECORD_SHA256" "$RUNTIME_RECORD" || return 1
  grep -Fxq "patch_sha256=$LEGACY_MIGRATION_SOURCE_PATCH_SHA256" "$RUNTIME_RECORD" || return 1
  sha256_matches "$LEGACY_MIGRATION_SOURCE_BINARY_SHA256" "$GATEWAY_BIN" || return 1
  sha256_matches "$UPSTREAM_GATEWAY_SHA256" "$UPSTREAM_BACKUP" || return 1
  python3 "$LEGACY_MIGRATION_VERIFIER" \
    --project-root "$SIQ_PROJECT_ROOT" \
    --current-binary-sha256 "$current_sha" \
    --target-patch-sha256 "$PATCH_SHA256" >/dev/null
}

validate_current_provenance() {
  local current_sha="$1"
  if [[ "$current_sha" == "$UPSTREAM_GATEWAY_SHA256" ]]; then
    runtime_state_matches "$current_sha" || {
      printf '%s\n' 'Upstream gateway binary has a conflicting runtime record.' >&2
      return 2
    }
    return 0
  fi
  runtime_record_matches "$current_sha" || legacy_runtime_record_allows_upgrade "$current_sha" || {
    printf '%s\n' 'Current gateway runtime record does not match its binary; refusing maintenance.' >&2
    return 2
  }
}

assert_no_managed_sandboxes() {
  local ids
  ids="$(docker ps -aq \
    --filter 'label=openshell.ai/managed-by=openshell' \
    --filter 'label=openshell.ai/sandbox-namespace=siq-openshell-dev')"
  [[ -z "$ids" ]] || {
    printf 'Refusing gateway replacement while managed sandbox containers exist: %s\n' \
      "$(printf '%s' "$ids" | tr '\n' ' ')" >&2
    exit 2
  }
}

assert_gateway_inventory_empty() {
  local sandbox_names
  sandbox_names="$("$SCRIPT_DIR/run_cli.sh" sandbox list | sed -r 's/\x1B\[[0-9;]*[mK]//g' | awk 'NR > 1 && $1 != "No" {print $1}')"
  [[ -z "$sandbox_names" ]] || {
    printf 'Refusing gateway replacement while OpenShell sandboxes exist: %s\n' \
      "$(printf '%s' "$sandbox_names" | tr '\n' ' ')" >&2
    return 2
  }
  assert_no_managed_sandboxes
}

gateway_pid_is_ours() {
  local pid="$1"
  [[ "$pid" =~ ^[0-9]+$ && -e "/proc/$pid/exe" ]] || return 1
  [[ "$(readlink -f "/proc/$pid/exe")" == "$(readlink -f "$GATEWAY_BIN")" ]]
}

detect_running_gateway() {
  local pid=""
  gateway_was_running=0
  if [[ -e "$GATEWAY_PID_FILE" || -L "$GATEWAY_PID_FILE" \
    || -e "$GATEWAY_PROCESS_RECORD" || -L "$GATEWAY_PROCESS_RECORD" ]]; then
    python3 "$SCRIPT_DIR/gateway_runtime_identity.py" \
      --project-root "$SIQ_PROJECT_ROOT" verify >/dev/null || {
      printf '%s\n' 'Gateway process runtime identity failed verification; refusing maintenance.' >&2
      exit 2
    }
    pid="$(read_gateway_pid_file)"
    gateway_was_running=1
  elif curl --fail --silent --max-time 1 http://127.0.0.1:17672/healthz >/dev/null 2>&1; then
    printf '%s\n' 'Gateway health is reachable but its attested process identity is missing.' >&2
    exit 2
  fi
}

quiesce_gateway() {
  detect_running_gateway
  [[ "$gateway_was_running" -eq 1 ]] || {
    printf '%s\n' 'The isolated gateway must be running so its sandbox inventory can be verified.' >&2
    exit 2
  }
  assert_gateway_inventory_empty
  gateway_stopped=1
  SIQ_OPENSHELL_MAINTENANCE_LOCK_HELD=1 "$SCRIPT_DIR/stop_gateway.sh"
  assert_no_managed_sandboxes
}

write_runtime_record() {
  local patched_sha="$1" builder_id="$2" state="$3" temporary_record
  [[ "$state" == staged || "$state" == committed ]] || return 2
  temporary_record="$(mktemp "$BUILD_ROOT/.gateway-patch.runtime.XXXXXX")"
  chmod 600 -- "$temporary_record"
  {
    printf 'schema=siq.openshell.gateway_patch.v1\n'
    printf 'state=%s\n' "$state"
    printf 'active=patched\n'
    printf 'version=%s\n' "$VERSION"
    printf 'upstream_commit=%s\n' "$UPSTREAM_COMMIT"
    printf 'patch_sha256=%s\n' "$PATCH_SHA256"
    printf 'normalized_source_diff_sha256=%s\n' "$EXPECTED_NORMALIZED_DIFF_SHA256"
    printf 'builder_image_id=%s\n' "$builder_id"
    printf 'builder_dockerfile_sha256=%s\n' "$BUILDER_DOCKERFILE_SHA256"
    printf 'builder_packages_sha256=%s\n' "$EXPECTED_BUILDER_PACKAGES_SHA256"
    printf 'builder_base=%s\n' "$UBUNTU_BASE_LABEL"
    printf 'builder_apt_mirror=%s\n' "$UBUNTU_APT_MIRROR"
    printf 'z3_commit=%s\n' "$Z3_COMMIT"
    printf 'z3_archive_sha256=%s\n' "$Z3_ARCHIVE_SHA256"
    printf 'upstream_binary_sha256=%s\n' "$UPSTREAM_GATEWAY_SHA256"
    printf 'patched_binary_sha256=%s\n' "$patched_sha"
    printf 'active_binary_sha256=%s\n' "$patched_sha"
    printf 'installed_path=%s\n' "var/openshell/toolchains/v$VERSION/bin/openshell-gateway"
  } >"$temporary_record"
  fsync_regular_file "$temporary_record"
  mv -fT -- "$temporary_record" "$RUNTIME_RECORD"
  fsync_regular_file "$RUNTIME_RECORD"
  fsync_directory "$BUILD_ROOT"
}

restore_previous_record() {
  local temporary_record=""
  if [[ "$previous_record_existed" -eq 1 ]]; then
    [[ -f "$previous_record" && ! -L "$previous_record" ]] || return 1
    temporary_record="$(mktemp "$BUILD_ROOT/.gateway-patch.restore.XXXXXX")" || return 1
    install -m 0600 -- "$previous_record" "$temporary_record" || return 1
    fsync_regular_file "$temporary_record" || return 1
    mv -fT -- "$temporary_record" "$RUNTIME_RECORD" || return 1
    fsync_regular_file "$RUNTIME_RECORD" || return 1
  else
    rm -f -- "$RUNTIME_RECORD"
  fi
  fsync_directory "$BUILD_ROOT"
}

journal_phase_allowed() {
  case "$1" in
    prepared|gateway_stopped|binary_installed|runtime_record_staged|gateway_started|runtime_record_committed|committed|rollback_incomplete) return 0 ;;
    *) return 1 ;;
  esac
}

# Each journal transition is durable before the following external side effect.
# An older durable phase is intentionally recoverable by restoring the old copy.
write_install_journal() {
  local phase="$1" temporary_journal now
  journal_phase_allowed "$phase" || return 2
  [[ "$previous_sha" =~ ^[0-9a-f]{64}$ \
    && "$patched_sha" =~ ^[0-9a-f]{64}$ \
    && "$gateway_config_sha" =~ ^[0-9a-f]{64}$ \
    && "$gateway_pid_snapshot" =~ ^[1-9][0-9]*$ \
    && "$gateway_start_ticks_snapshot" =~ ^[1-9][0-9]*$ \
    && "$gateway_executable_snapshot" == "$GATEWAY_BIN" \
    && "$gateway_argv_sha_snapshot" =~ ^[0-9a-f]{64}$ \
    && "$protected_listener_digest" =~ ^[0-9a-f]{64}$ \
    && "$gateway_listener_digest" =~ ^[0-9a-f]{64}$ \
    && "$previous_bin" == "$BIN_ROOT"/.openshell-gateway.previous.* ]] || return 2
  require_private_regular_file "$previous_bin"
  if [[ "$previous_record_existed" -eq 1 ]]; then
    [[ "$previous_record" == "$BUILD_ROOT"/.gateway-patch.previous.* ]] || return 2
    require_private_regular_file "$previous_record"
  else
    [[ -z "$previous_record" ]] || return 2
  fi
  now="$(utc_timestamp)"
  [[ -n "$journal_created_at" ]] || journal_created_at="$now"
  temporary_journal="$(mktemp "$BUILD_ROOT/.gateway-install.transaction.XXXXXX")"
  chmod 600 -- "$temporary_journal"
  {
    printf 'schema=siq.openshell.gateway_install_transaction.v1\n'
    printf 'phase=%s\n' "$phase"
    printf 'old_binary_sha256=%s\n' "$previous_sha"
    printf 'backup_path=%s\n' "$previous_bin"
    printf 'new_binary_sha256=%s\n' "$patched_sha"
    printf 'gateway_config_path=%s\n' "$GATEWAY_CONFIG"
    printf 'gateway_config_sha256=%s\n' "$gateway_config_sha"
    printf 'runtime_record_path=%s\n' "$RUNTIME_RECORD"
    printf 'runtime_record_sha256=%s\n' "$runtime_record_snapshot"
    printf 'runtime_record_backup_path=%s\n' "$previous_record"
    printf 'runtime_record_existed=%s\n' "$previous_record_existed"
    printf 'gateway_was_running=%s\n' "$journal_gateway_was_running"
    printf 'gateway_pid=%s\n' "$gateway_pid_snapshot"
    printf 'gateway_start_ticks=%s\n' "$gateway_start_ticks_snapshot"
    printf 'gateway_executable=%s\n' "$gateway_executable_snapshot"
    printf 'gateway_argv_sha256=%s\n' "$gateway_argv_sha_snapshot"
    printf 'protected_listeners_sha256=%s\n' "$protected_listener_digest"
    printf 'gateway_listeners_sha256=%s\n' "$gateway_listener_digest"
    printf 'created_at=%s\n' "$journal_created_at"
    printf 'updated_at=%s\n' "$now"
  } >"$temporary_journal"
  fsync_regular_file "$temporary_journal"
  mv -fT -- "$temporary_journal" "$INSTALL_JOURNAL"
  fsync_regular_file "$INSTALL_JOURNAL"
  fsync_directory "$BUILD_ROOT"
  journal_phase="$phase"
  transaction_owned=1
}

load_install_journal() {
  local line key value required_key
  local -A values=()
  require_private_regular_file "$INSTALL_JOURNAL"
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" == *=* ]] || return 2
    key="${line%%=*}"
    value="${line#*=}"
    case "$key" in
      schema|phase|old_binary_sha256|backup_path|new_binary_sha256|gateway_config_path|gateway_config_sha256|runtime_record_path|runtime_record_sha256|runtime_record_backup_path|runtime_record_existed|gateway_was_running|gateway_pid|gateway_start_ticks|gateway_executable|gateway_argv_sha256|protected_listeners_sha256|gateway_listeners_sha256|created_at|updated_at) ;;
      *) return 2 ;;
    esac
    [[ -z "${values[$key]+present}" ]] || return 2
    values["$key"]="$value"
  done <"$INSTALL_JOURNAL"
  for required_key in schema phase old_binary_sha256 backup_path new_binary_sha256 \
    gateway_config_path gateway_config_sha256 runtime_record_path runtime_record_sha256 \
    runtime_record_backup_path runtime_record_existed gateway_was_running gateway_pid \
    gateway_start_ticks gateway_executable gateway_argv_sha256 \
    protected_listeners_sha256 gateway_listeners_sha256 created_at updated_at; do
    [[ -n "${values[$required_key]+present}" ]] || return 2
  done
  [[ "${values[schema]}" == siq.openshell.gateway_install_transaction.v1 ]] || return 2
  journal_phase_allowed "${values[phase]}" || return 2
  [[ "${values[old_binary_sha256]}" =~ ^[0-9a-f]{64}$ \
    && "${values[new_binary_sha256]}" =~ ^[0-9a-f]{64}$ \
    && "${values[gateway_config_sha256]}" =~ ^[0-9a-f]{64}$ \
    && "${values[gateway_pid]}" =~ ^[1-9][0-9]*$ \
    && "${values[gateway_start_ticks]}" =~ ^[1-9][0-9]*$ \
    && "${values[gateway_argv_sha256]}" =~ ^[0-9a-f]{64}$ \
    && "${values[protected_listeners_sha256]}" =~ ^[0-9a-f]{64}$ \
    && "${values[gateway_listeners_sha256]}" =~ ^[0-9a-f]{64}$ \
    && ( "${values[runtime_record_sha256]}" == absent || "${values[runtime_record_sha256]}" =~ ^[0-9a-f]{64}$ ) ]] || return 2
  [[ "${values[backup_path]}" == "$BIN_ROOT"/.openshell-gateway.previous.* \
    && "${values[gateway_config_path]}" == "$GATEWAY_CONFIG" \
    && "${values[runtime_record_path]}" == "$RUNTIME_RECORD" \
    && "${values[runtime_record_existed]}" =~ ^[01]$ \
    && "${values[gateway_was_running]}" == 1 \
    && "${values[gateway_executable]}" == "$GATEWAY_BIN" \
    && "${values[created_at]}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ \
    && "${values[updated_at]}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] || return 2
  if [[ "${values[runtime_record_existed]}" == 1 ]]; then
    [[ "${values[runtime_record_backup_path]}" == "$BUILD_ROOT"/.gateway-patch.previous.* ]] || return 2
  else
    [[ -z "${values[runtime_record_backup_path]}" ]] || return 2
  fi
  journal_phase="${values[phase]}"
  previous_sha="${values[old_binary_sha256]}"
  previous_bin="${values[backup_path]}"
  patched_sha="${values[new_binary_sha256]}"
  gateway_config_sha="${values[gateway_config_sha256]}"
  runtime_record_snapshot="${values[runtime_record_sha256]}"
  previous_record="${values[runtime_record_backup_path]}"
  previous_record_existed="${values[runtime_record_existed]}"
  journal_gateway_was_running="${values[gateway_was_running]}"
  gateway_pid_snapshot="${values[gateway_pid]}"
  gateway_start_ticks_snapshot="${values[gateway_start_ticks]}"
  gateway_executable_snapshot="${values[gateway_executable]}"
  gateway_argv_sha_snapshot="${values[gateway_argv_sha256]}"
  protected_listener_digest="${values[protected_listeners_sha256]}"
  gateway_listener_digest="${values[gateway_listeners_sha256]}"
  journal_created_at="${values[created_at]}"
  journal_protected_listener_digest="$protected_listener_digest"
  journal_gateway_listener_digest="$gateway_listener_digest"
}

clear_install_transaction() {
  [[ "$previous_bin" == "$BIN_ROOT"/.openshell-gateway.previous.* ]] || return 2
  if [[ -n "$previous_record" ]]; then
    [[ "$previous_record" == "$BUILD_ROOT"/.gateway-patch.previous.* ]] || return 2
  fi
  rm -f -- "$INSTALL_JOURNAL"
  fsync_directory "$BUILD_ROOT"
  rm -f -- "$previous_bin"
  fsync_directory "$BIN_ROOT"
  if [[ -n "$previous_record" ]]; then
    rm -f -- "$previous_record"
    fsync_directory "$BUILD_ROOT"
  fi
  previous_bin=""
  previous_record=""
  transaction_owned=0
}

inspect_gateway_for_recovery() {
  local pid="" health_reachable=0
  recovery_gateway_running=0
  recovery_gateway_pid=""
  if curl --fail --silent --max-time 1 http://127.0.0.1:17672/healthz >/dev/null 2>&1; then
    health_reachable=1
  fi
  if [[ -e "$GATEWAY_PID_FILE" || -L "$GATEWAY_PID_FILE" \
    || -e "$GATEWAY_PROCESS_RECORD" || -L "$GATEWAY_PROCESS_RECORD" ]]; then
    python3 "$SCRIPT_DIR/gateway_runtime_identity.py" \
      --project-root "$SIQ_PROJECT_ROOT" verify >/dev/null || {
      printf '%s\n' 'Recovery found unverified gateway process runtime evidence.' >&2
      return 2
    }
    pid="$(read_gateway_pid_file)"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 2
    [[ "$health_reachable" -eq 1 ]] || {
      printf '%s\n' 'Recovery found a live gateway without a healthy control endpoint.' >&2
      return 2
    }
    recovery_gateway_running=1
    recovery_gateway_pid="$pid"
    return 0
  fi
  [[ "$health_reachable" -eq 0 ]] || {
    printf '%s\n' 'Recovery found gateway health without a verified live PID.' >&2
    return 2
  }
}

stop_gateway_for_recovery() {
  inspect_gateway_for_recovery
  if [[ "$recovery_gateway_running" -eq 1 ]]; then
    assert_gateway_inventory_empty
    SIQ_OPENSHELL_MAINTENANCE_LOCK_HELD=1 "$SCRIPT_DIR/stop_gateway.sh"
    staged_gateway_running=0
    gateway_stopped=1
  else
    assert_no_managed_sandboxes
  fi
}

restore_previous_binary() {
  local temporary_bin
  require_private_regular_file "$previous_bin"
  sha256_matches "$previous_sha" "$previous_bin" || return 2
  temporary_bin="$(mktemp "$BIN_ROOT/.openshell-gateway.rollback.XXXXXX")"
  install -m 0700 -- "$previous_bin" "$temporary_bin"
  fsync_regular_file "$temporary_bin"
  sha256_matches "$previous_sha" "$temporary_bin" || return 2
  mv -fT -- "$temporary_bin" "$GATEWAY_BIN"
  fsync_regular_file "$GATEWAY_BIN"
  fsync_directory "$BIN_ROOT"
  sha256_matches "$previous_sha" "$GATEWAY_BIN"
}

mark_rollback_incomplete() {
  preserve_recovery=1
  write_install_journal rollback_incomplete || true
  printf '%s\n' 'ERROR: gateway transaction recovery is incomplete; journal and backups were preserved.' >&2
}

restore_loaded_transaction() {
  local current_sha config_ok=1 runtime_drift=0
  require_private_regular_file "$previous_bin"
  sha256_matches "$previous_sha" "$previous_bin" || return 2
  if [[ "$previous_record_existed" -eq 1 ]]; then
    require_private_regular_file "$previous_record"
    [[ "$(sha256sum -- "$previous_record" | awk '{print $1}')" == "$runtime_record_snapshot" ]] || return 2
  else
    [[ "$runtime_record_snapshot" == absent ]] || return 2
  fi
  sha256_matches "$gateway_config_sha" "$GATEWAY_CONFIG" || config_ok=0
  current_sha="$(sha256sum -- "$GATEWAY_BIN" | awk '{print $1}')"
  [[ "$current_sha" == "$previous_sha" || "$current_sha" == "$patched_sha" ]] || {
    printf '%s\n' 'Recovery found an active gateway binary outside the journal contract.' >&2
    return 2
  }

  inspect_gateway_for_recovery
  if [[ "$journal_phase" == prepared \
    && "$current_sha" == "$previous_sha" \
    && "$config_ok" -eq 1 \
    && "$recovery_gateway_running" -eq 1 \
    && "$recovery_gateway_pid" == "$gateway_pid_snapshot" \
    && "$(process_start_ticks "$recovery_gateway_pid")" == "$gateway_start_ticks_snapshot" \
    && "$(readlink -f "/proc/$recovery_gateway_pid/exe")" == "$gateway_executable_snapshot" \
    && "$(sha256sum -- "/proc/$recovery_gateway_pid/cmdline" | awk '{print $1}')" == "$gateway_argv_sha_snapshot" ]] \
    && runtime_state_matches "$previous_sha" \
    ; then
    [[ "$(listener_set_digest protected)" == "$journal_protected_listener_digest" ]] || runtime_drift=1
    [[ "$(listener_set_digest gateway)" == "$journal_gateway_listener_digest" ]] || runtime_drift=1
    clear_install_transaction
    recovery_result=aborted_before_install
    [[ "$runtime_drift" -eq 0 ]] || recovery_result=aborted_before_install_with_runtime_drift
    return 0
  fi
  if [[ "$current_sha" == "$previous_sha" \
    && "$config_ok" -eq 1 \
    && "$recovery_gateway_running" -eq "$journal_gateway_was_running" \
    && "$(sha256sum -- "/proc/$recovery_gateway_pid/cmdline" 2>/dev/null | awk '{print $1}')" == "$gateway_argv_sha_snapshot" ]] \
    && runtime_state_matches "$previous_sha"; then
    [[ "$(listener_set_digest protected)" == "$journal_protected_listener_digest" ]] || runtime_drift=1
    [[ "$(listener_set_digest gateway)" == "$journal_gateway_listener_digest" ]] || runtime_drift=1
    clear_install_transaction
    recovery_result=restored
    [[ "$runtime_drift" -eq 0 ]] || recovery_result=restored_with_runtime_drift
    return 0
  fi

  stop_gateway_for_recovery
  restore_previous_binary || return 2
  restore_previous_record || return 2
  sha256_matches "$previous_sha" "$GATEWAY_BIN" || return 2
  runtime_state_matches "$previous_sha" || return 2
  if [[ "$config_ok" -ne 1 ]]; then
    printf '%s\n' 'Gateway configuration changed; the previous binary was restored but the gateway remains stopped.' >&2
    return 2
  fi
  if [[ "$journal_gateway_was_running" -eq 1 ]]; then
    SIQ_OPENSHELL_MAINTENANCE_LOCK_HELD=1 "$SCRIPT_DIR/start_gateway.sh"
    gateway_stopped=0
    inspect_gateway_for_recovery
    [[ "$recovery_gateway_running" -eq 1 ]] || return 2
    [[ "$(sha256sum -- "/proc/$recovery_gateway_pid/cmdline" | awk '{print $1}')" == "$gateway_argv_sha_snapshot" ]] || return 2
  fi
  sha256_matches "$previous_sha" "$GATEWAY_BIN" || return 2
  runtime_state_matches "$previous_sha" || return 2
  [[ "$(listener_set_digest protected)" == "$journal_protected_listener_digest" ]] || runtime_drift=1
  [[ "$(listener_set_digest gateway)" == "$journal_gateway_listener_digest" ]] || runtime_drift=1
  clear_install_transaction
  recovery_result=restored
  [[ "$runtime_drift" -eq 0 ]] || recovery_result=restored_with_runtime_drift
  printf '%s\n' 'Restored and verified the previous gateway from the durable install journal.' >&2
}

verify_committed_transaction() {
  sha256_matches "$patched_sha" "$GATEWAY_BIN" || return 1
  sha256_matches "$gateway_config_sha" "$GATEWAY_CONFIG" || return 1
  runtime_record_matches "$patched_sha" || return 1
  inspect_gateway_for_recovery || return 1
  [[ "$recovery_gateway_running" -eq 1 \
    && "$(sha256sum -- "/proc/$recovery_gateway_pid/cmdline" | awk '{print $1}')" == "$gateway_argv_sha_snapshot" \
    && "$(listener_set_digest protected)" == "$journal_protected_listener_digest" \
    && "$(listener_set_digest gateway)" == "$journal_gateway_listener_digest" ]] || return 1
  assert_gateway_inventory_empty
}

recover_incomplete_transaction() {
  [[ -e "$INSTALL_JOURNAL" || -L "$INSTALL_JOURNAL" ]] || return 0
  case "$RECOVERY_MODE" in
    auto|restore) ;;
    manual)
      printf 'Incomplete gateway install journal detected: %s\n' "$INSTALL_JOURNAL" >&2
      printf '%s\n' 'Re-run with SIQ_OPENSHELL_GATEWAY_RECOVERY_MODE=restore after review.' >&2
      recovery_result="manual_required"
      return 75
      ;;
    *)
      printf '%s\n' 'SIQ_OPENSHELL_GATEWAY_RECOVERY_MODE must be auto, restore, or manual.' >&2
      return 2
      ;;
  esac
  load_install_journal || {
    printf '%s\n' 'Incomplete gateway install journal is malformed; refusing automatic recovery.' >&2
    recovery_result="invalid_journal"
    return 2
  }
  require_private_regular_file "$previous_bin" || return 2
  if [[ "$journal_phase" == committed ]] && verify_committed_transaction; then
    clear_install_transaction
    recovery_result="committed_finalized"
    printf '%s\n' 'Finalized a previously committed gateway installation journal.' >&2
    return 0
  fi
  if ! restore_loaded_transaction; then
    mark_rollback_incomplete
    transaction_owned=0
    recovery_result="rollback_incomplete"
    return 2
  fi
}

assert_post_commit_runtime() {
  local checkpoint="$1" pid
  assert_external_runtime_unchanged "$checkpoint"
  assert_gateway_endpoint_shape_unchanged "$checkpoint"
  sha256_matches "$patched_sha" "$GATEWAY_BIN" || return 2
  runtime_record_matches "$patched_sha" || return 2
  pid="$(read_gateway_pid_file)"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 2
  gateway_pid_is_ours "$pid" || return 2
  [[ "$(sha256sum -- "/proc/$pid/cmdline" | awk '{print $1}')" == "$gateway_argv_sha_snapshot" ]] || return 2
  curl --fail --silent --max-time 2 http://127.0.0.1:17672/healthz >/dev/null
  assert_gateway_inventory_empty
}

handle_signal() {
  local signal_name="$1" exit_status="$2"
  trap - INT TERM
  printf 'Received %s; rolling back any active gateway install transaction.\n' "$signal_name" >&2
  exit "$exit_status"
}

handle_error() {
  local exit_status="$1"
  trap - ERR
  exit "$exit_status"
}

cleanup() {
  local status=$?
  [[ "$cleanup_running" -eq 0 ]] || exit "$status"
  cleanup_running=1
  trap - EXIT ERR INT TERM
  set +e
  if [[ "$status" -ne 0 && "$transaction_owned" -eq 1 && "$committed" -eq 0 \
    && ( -e "$INSTALL_JOURNAL" || -L "$INSTALL_JOURNAL" ) ]]; then
    if load_install_journal && restore_loaded_transaction; then
      preserve_recovery=0
    else
      mark_rollback_incomplete
      status=2
    fi
  elif [[ "$committed" -eq 1 && ( -e "$INSTALL_JOURNAL" || -L "$INSTALL_JOURNAL" ) ]]; then
    preserve_recovery=1
    printf '%s\n' 'Committed gateway transaction journal remains for next-run finalization.' >&2
  elif [[ -e "$INSTALL_JOURNAL" || -L "$INSTALL_JOURNAL" ]]; then
    preserve_recovery=1
    printf '%s\n' 'Gateway transaction journal remains for next-run recovery.' >&2
  fi
  if [[ ! -e "$INSTALL_JOURNAL" && ! -L "$INSTALL_JOURNAL" ]]; then
    if [[ -n "$previous_bin" && "$previous_bin" == "$BIN_ROOT"/.openshell-gateway.previous.* ]]; then
      rm -f -- "$previous_bin"
      fsync_directory "$BIN_ROOT"
      previous_bin=""
    fi
    if [[ -n "$previous_record" && "$previous_record" == "$BUILD_ROOT"/.gateway-patch.previous.* ]]; then
      rm -f -- "$previous_record"
      fsync_directory "$BUILD_ROOT"
      previous_record=""
    fi
  fi
  if [[ "$preserve_recovery" -eq 0 && -n "$RUN_ROOT" && -d "$RUN_ROOT" && ! -L "$RUN_ROOT" ]]; then
    rm -rf -- "$RUN_ROOT"
  elif [[ "$preserve_recovery" -eq 1 ]]; then
    printf 'Recovery material preserved under %s and %s\n' "$BIN_ROOT" "$BUILD_ROOT" >&2
  fi
  exit "$status"
}
trap 'handle_signal INT 130' INT
trap 'handle_signal TERM 143' TERM
trap 'handle_error $?' ERR
trap cleanup EXIT

require_regular_file "$PATCH_FILE"
require_regular_file "$BUILDER_DOCKERFILE"
require_regular_file "$LEGACY_MIGRATION_VERIFIER"
verify_sha256 "$PATCH_SHA256" "$PATCH_FILE"
verify_sha256 "$EXPECTED_BUILDER_DOCKERFILE_SHA256" "$BUILDER_DOCKERFILE"

siq_openshell_acquire_maintenance_lock
ensure_state_dir "$BUILD_ROOT"
ensure_state_dir "$CARGO_ROOT"
siq_openshell_assert_state_path "$BIN_ROOT"
require_regular_file "$GATEWAY_BIN"

recovery_status=0
recover_incomplete_transaction || recovery_status=$?
if [[ "$recovery_status" -ne 0 ]]; then
  exit "$recovery_status"
fi
case "$recovery_result" in
  committed_finalized)
    exit 0
    ;;
  restored|restored_with_runtime_drift|aborted_before_install|aborted_before_install_with_runtime_drift)
    printf '%s\n' 'An incomplete gateway installation was restored; re-run the build explicitly.' >&2
    exit 75
    ;;
esac

current_sha="$(sha256sum -- "$GATEWAY_BIN" | awk '{print $1}')"
validate_current_provenance "$current_sha"
previous_sha="$current_sha"
assert_bind_mounts_inactive
capture_protected_runtime
[[ "$gateway_binary_snapshot" == "$current_sha" ]] || {
  printf '%s\n' 'Gateway binary changed while the protected runtime snapshot was captured.' >&2
  exit 2
}

RUN_ROOT="$(mktemp -d "$BUILD_ROOT/gateway-run.XXXXXX")"
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
verify_sha256 "$UPSTREAM_DRIVER_LIB_SHA256" "$SOURCE_DIR/crates/openshell-driver-docker/src/lib.rs"
verify_sha256 "$UPSTREAM_DRIVER_TESTS_SHA256" "$SOURCE_DIR/crates/openshell-driver-docker/src/tests.rs"
verify_sha256 "$UPSTREAM_CARGO_TOML_SHA256" "$SOURCE_DIR/Cargo.toml"
verify_sha256 "$UPSTREAM_CARGO_LOCK_SHA256" "$SOURCE_DIR/Cargo.lock"
git -C "$SOURCE_DIR" apply --check "$PATCH_FILE"
git -C "$SOURCE_DIR" apply "$PATCH_FILE"
git -C "$SOURCE_DIR" diff --check
changed_files="$(git -C "$SOURCE_DIR" diff --name-only)"
expected_changed_files=$'crates/openshell-driver-docker/src/lib.rs\ncrates/openshell-driver-docker/src/tests.rs'
[[ "$changed_files" == "$expected_changed_files" ]] || {
  printf '%s\n' 'OpenShell gateway patch changed unexpected files.' >&2
  exit 2
}
patch_diff_sha="$(git -C "$SOURCE_DIR" diff --binary | sha256sum | awk '{print $1}')"
[[ "$patch_diff_sha" == "$EXPECTED_PATCH_DIFF_SHA256" ]] || {
  printf '%s\n' 'OpenShell gateway patch does not match the reviewed source diff.' >&2
  exit 2
}
[[ -z "$(git -C "$SOURCE_DIR" status --porcelain=v1 --untracked-files=all | grep '^??' || true)" ]] || {
  printf '%s\n' 'Unexpected untracked files appeared in the OpenShell source.' >&2
  exit 2
}

verified_builder_actual_id="$(docker image inspect "$VERIFIED_BUILDER_IMAGE" --format '{{.Id}}' 2>/dev/null || true)"
[[ "$verified_builder_actual_id" == "$VERIFIED_BUILDER_ID" ]] || {
  printf '%s\n' 'Verified supervisor builder image is missing or has an unexpected ID.' >&2
  exit 2
}

if ! docker image inspect "$BUILDER_IMAGE" >/dev/null 2>&1; then
  docker build --pull=false --platform linux/arm64 --tag "$BUILDER_IMAGE" \
    --build-arg "SIQ_VERIFIED_BUILDER=$VERIFIED_BUILDER_IMAGE" \
    --build-arg "SIQ_UBUNTU_BASE=$UBUNTU_BASE_DIGEST" \
    --build-arg "SIQ_UBUNTU_MIRROR=$UBUNTU_APT_MIRROR" \
    --build-arg "SIQ_GATEWAY_PATCH_SHA256=$PATCH_SHA256" \
    --build-arg "SIQ_OPENSHELL_UPSTREAM_COMMIT=$UPSTREAM_COMMIT" \
    --build-arg "SIQ_OPENSHELL_GATEWAY_BUILDER_DOCKERFILE_SHA256=$BUILDER_DOCKERFILE_SHA256" \
    --build-arg "SIQ_OPENSHELL_VERIFIED_BUILDER_ID=$VERIFIED_BUILDER_ID" \
    --build-arg "SIQ_SUPERVISOR_PATCH_SHA256=$SUPERVISOR_PATCH_SHA256" \
    --file "$BUILDER_DOCKERFILE" \
    "$ROOT_DIR/infra/openshell/patches/v$VERSION" >&2
fi

builder_id="$(docker image inspect "$BUILDER_IMAGE" --format '{{.Id}}')"
builder_arch="$(docker image inspect "$BUILDER_IMAGE" --format '{{.Architecture}}')"
builder_os="$(docker image inspect "$BUILDER_IMAGE" --format '{{.Os}}')"
builder_patch_label="$(docker image inspect "$BUILDER_IMAGE" --format '{{index .Config.Labels "ai.siq.openshell.gateway-patch-sha256"}}')"
builder_commit_label="$(docker image inspect "$BUILDER_IMAGE" --format '{{index .Config.Labels "ai.siq.openshell.upstream-commit"}}')"
builder_rust_label="$(docker image inspect "$BUILDER_IMAGE" --format '{{index .Config.Labels "ai.siq.openshell.rust-dist-sha256"}}')"
builder_dockerfile_label="$(docker image inspect "$BUILDER_IMAGE" --format '{{index .Config.Labels "ai.siq.openshell.gateway-builder-dockerfile-sha256"}}')"
builder_parent_label="$(docker image inspect "$BUILDER_IMAGE" --format '{{index .Config.Labels "ai.siq.openshell.verified-builder-id"}}')"
builder_supervisor_patch_label="$(docker image inspect "$BUILDER_IMAGE" --format '{{index .Config.Labels "ai.siq.openshell.supervisor-patch-sha256"}}')"
builder_base_label="$(docker image inspect "$BUILDER_IMAGE" --format '{{index .Config.Labels "ai.siq.openshell.gateway-builder-base"}}')"
builder_mirror_label="$(docker image inspect "$BUILDER_IMAGE" --format '{{index .Config.Labels "ai.siq.openshell.gateway-builder-apt-mirror"}}')"
[[ "$builder_id" =~ ^sha256:[0-9a-f]{64}$ ]] || { printf '%s\n' 'Gateway builder has an invalid image ID.' >&2; exit 2; }
[[ "$builder_arch" == arm64 && "$builder_os" == linux ]] || { printf '%s\n' 'Gateway builder has an unexpected platform.' >&2; exit 2; }
[[ "$builder_patch_label" == "$PATCH_SHA256" ]] || { printf '%s\n' 'Gateway builder has an unexpected patch label.' >&2; exit 2; }
[[ "$builder_commit_label" == "$UPSTREAM_COMMIT" ]] || { printf '%s\n' 'Gateway builder has an unexpected upstream commit label.' >&2; exit 2; }
[[ "$builder_rust_label" == "$RUST_DIST_SHA256" ]] || { printf '%s\n' 'Gateway builder has an unexpected Rust distribution label.' >&2; exit 2; }
[[ "$builder_dockerfile_label" == "$BUILDER_DOCKERFILE_SHA256" ]] || { printf '%s\n' 'Gateway builder has an unexpected Dockerfile label.' >&2; exit 2; }
[[ "$builder_parent_label" == "$VERIFIED_BUILDER_ID" ]] || { printf '%s\n' 'Gateway builder has an unexpected parent builder label.' >&2; exit 2; }
[[ "$builder_supervisor_patch_label" == "$SUPERVISOR_PATCH_SHA256" ]] || { printf '%s\n' 'Gateway builder has an unexpected supervisor patch label.' >&2; exit 2; }
[[ "$builder_base_label" == "$UBUNTU_BASE_LABEL" ]] || { printf '%s\n' 'Gateway builder has an unexpected Ubuntu base label.' >&2; exit 2; }
[[ "$builder_mirror_label" == "$UBUNTU_APT_MIRROR" ]] || { printf '%s\n' 'Gateway builder has an unexpected APT mirror label.' >&2; exit 2; }
builder_packages_sha="$(docker run --rm --platform linux/arm64 --network=none \
  --cap-drop=ALL --security-opt=no-new-privileges --read-only \
  "$BUILDER_IMAGE" sha256sum /usr/local/share/siq-gateway-builder-packages.txt | awk '{print $1}')"
[[ "$builder_packages_sha" == "$EXPECTED_BUILDER_PACKAGES_SHA256" ]] || {
  printf '%s\n' 'Gateway builder package manifest differs from the reviewed toolchain.' >&2
  exit 2
}
docker run --rm --platform linux/arm64 --network=none \
  --cap-drop=ALL --security-opt=no-new-privileges --read-only \
  "$BUILDER_IMAGE" bash -lc \
  'rustc --version | grep -F "rustc 1.95.0" \
    && cargo --version | grep -F "cargo 1.95.0" \
    && test "$(gcc-13 -dumpfullversion -dumpversion)" = 13.3.0 \
    && test "$(g++-13 -dumpfullversion -dumpversion)" = 13.3.0' >&2

sed -i -E '/^\[workspace\.package\]/,/^\[/{s/^version[[:space:]]*=[[:space:]]*".*"/version = "0.0.83"/}' "$SOURCE_DIR/Cargo.toml"
grep -q '^version = "0.0.83"' "$SOURCE_DIR/Cargo.toml"
prelock_changed_files="$(git -C "$SOURCE_DIR" diff --name-only)"
expected_prelock_files=$'Cargo.toml\ncrates/openshell-driver-docker/src/lib.rs\ncrates/openshell-driver-docker/src/tests.rs'
[[ "$prelock_changed_files" == "$expected_prelock_files" ]] || {
  printf '%s\n' 'Workspace version normalization changed unexpected files.' >&2
  exit 2
}
prelock_diff_sha="$(git -C "$SOURCE_DIR" diff --binary | sha256sum | awk '{print $1}')"
[[ "$prelock_diff_sha" == "$EXPECTED_PRELOCK_DIFF_SHA256" ]] || {
  printf '%s\n' 'Workspace version normalization does not match the reviewed pre-lock diff.' >&2
  exit 2
}

# Cargo must see the normalized workspace version before it rewrites Cargo.lock.
docker run --rm --platform linux/arm64 --network=none \
  --cap-drop=ALL --security-opt=no-new-privileges --read-only \
  --tmpfs /tmp:rw,nosuid,nodev --user "$(id -u):$(id -g)" \
  --volume "$SOURCE_DIR:/src" --volume "$CARGO_ROOT:/cargo" \
  --workdir /src --env HOME=/tmp --env CARGO_HOME=/cargo \
  --env CARGO_NET_OFFLINE=true \
  "$BUILDER_IMAGE" cargo update --workspace --offline >&2
verify_sha256 "$EXPECTED_NORMALIZED_LOCK_SHA256" "$SOURCE_DIR/Cargo.lock"
normalized_changed_files="$(git -C "$SOURCE_DIR" diff --name-only)"
expected_normalized_files=$'Cargo.lock\nCargo.toml\ncrates/openshell-driver-docker/src/lib.rs\ncrates/openshell-driver-docker/src/tests.rs'
[[ "$normalized_changed_files" == "$expected_normalized_files" ]] || {
  printf '%s\n' 'Cargo version normalization changed unexpected files.' >&2
  exit 2
}
git -C "$SOURCE_DIR" diff --check
normalized_diff_sha="$(git -C "$SOURCE_DIR" diff --binary | sha256sum | awk '{print $1}')"
[[ "$normalized_diff_sha" == "$EXPECTED_NORMALIZED_DIFF_SHA256" ]] || {
  printf '%s\n' 'Cargo version normalization does not match the reviewed gateway source diff.' >&2
  exit 2
}
[[ -z "$(git -C "$SOURCE_DIR" status --porcelain=v1 --untracked-files=all | grep '^??' || true)" ]] || {
  printf '%s\n' 'Unexpected untracked files appeared during Cargo version normalization.' >&2
  exit 2
}

prepare_z3_source

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
  --volume "$Z3_SOURCE_ROOT:/z3:ro"
  --workdir /src
  --env HOME=/tmp
  --env CARGO_HOME=/cargo
  --env CARGO_TARGET_DIR=/target
  --env CARGO_BUILD_JOBS=4
  --env Z3_SYS_BUNDLED_DIR_OVERRIDE=/z3
  --env CARGO_TERM_COLOR=never
  --env GIT_DIR=/nonexistent
)

# Cargo fetch and the separately pinned Z3 archive download are the only
# networked phases. Formatting, tests, and release build run fully offline.
docker run "${common_docker_args[@]}" \
  "$BUILDER_IMAGE" bash -lc 'cargo fetch --locked --target aarch64-unknown-linux-gnu' >&2
docker run "${common_docker_args[@]}" --network=none \
  --env CARGO_NET_OFFLINE=true \
  "$BUILDER_IMAGE" bash -lc \
  'cargo fmt --all -- --check && cargo test --locked --target aarch64-unknown-linux-gnu -p openshell-driver-docker && cargo build --locked --release --target aarch64-unknown-linux-gnu -p openshell-server --bin openshell-gateway --no-default-features --features bundled-z3' >&2

BUILT_BIN="$TARGET_ROOT/aarch64-unknown-linux-gnu/release/openshell-gateway"
require_regular_file "$BUILT_BIN"
file_output="$(file -b "$BUILT_BIN")"
[[ "$file_output" == *'ELF 64-bit LSB pie executable, ARM aarch64'* ]] || {
  printf 'Patched gateway is not an ARM64 ELF: %s\n' "$file_output" >&2
  exit 2
}
readelf -l "$BUILT_BIN" | grep -q 'Requesting program interpreter: /lib/ld-linux-aarch64.so.1' || {
  printf '%s\n' 'Patched gateway is not the expected GNU dynamic binary.' >&2
  exit 2
}
dependency_report="$(ldd "$BUILT_BIN")"
if grep -Eq 'libz3\.so|not found' <<<"$dependency_report"; then
  printf '%s\n' "$dependency_report" >&2
  printf '%s\n' 'Patched gateway has an unapproved host runtime dependency.' >&2
  exit 2
fi
host_version_output="$("$BUILT_BIN" --version)"
[[ "$host_version_output" == "openshell-gateway $VERSION" ]] || {
  printf 'Patched gateway version mismatch on the host: %s\n' "$host_version_output" >&2
  exit 2
}

assert_preinstall_runtime_unchanged post-build
patched_sha="$(sha256sum -- "$BUILT_BIN" | awk '{print $1}')"

if [[ -e "$UPSTREAM_BACKUP" || -L "$UPSTREAM_BACKUP" ]]; then
  require_regular_file "$UPSTREAM_BACKUP"
else
  [[ "$current_sha" == "$UPSTREAM_GATEWAY_SHA256" ]] || {
    printf '%s\n' 'Cannot create an upstream backup from a non-upstream gateway.' >&2
    exit 2
  }
  temporary_backup="$(mktemp "$BIN_ROOT/.openshell-gateway.upstream.XXXXXX")"
  install -m 0700 -- "$GATEWAY_BIN" "$temporary_backup"
  fsync_regular_file "$temporary_backup"
  verify_sha256 "$UPSTREAM_GATEWAY_SHA256" "$temporary_backup"
  mv -fT -- "$temporary_backup" "$UPSTREAM_BACKUP"
  fsync_regular_file "$UPSTREAM_BACKUP"
  fsync_directory "$BIN_ROOT"
fi
verify_sha256 "$UPSTREAM_GATEWAY_SHA256" "$UPSTREAM_BACKUP"

previous_bin="$(mktemp "$BIN_ROOT/.openshell-gateway.previous.XXXXXX")"
install -m 0700 -- "$GATEWAY_BIN" "$previous_bin"
fsync_regular_file "$previous_bin"
fsync_directory "$BIN_ROOT"
verify_sha256 "$current_sha" "$previous_bin"
if [[ -e "$RUNTIME_RECORD" || -L "$RUNTIME_RECORD" ]]; then
  require_private_regular_file "$RUNTIME_RECORD"
  previous_record="$(mktemp "$BUILD_ROOT/.gateway-patch.previous.XXXXXX")"
  install -m 0600 -- "$RUNTIME_RECORD" "$previous_record"
  fsync_regular_file "$previous_record"
  fsync_directory "$BUILD_ROOT"
  previous_record_existed=1
fi

journal_gateway_was_running="$gateway_was_running"
write_install_journal prepared
assert_preinstall_runtime_unchanged pre-install

quiesce_gateway
write_install_journal gateway_stopped
assert_external_runtime_unchanged post-quiesce
sha256_matches "$current_sha" "$GATEWAY_BIN" || {
  printf '%s\n' 'Gateway binary changed while it was being quiesced; leaving recovery to the journal.' >&2
  exit 2
}
[[ "$(runtime_record_fingerprint)" == "$runtime_record_snapshot" ]] || {
  printf '%s\n' 'Gateway runtime record changed while it was being quiesced.' >&2
  exit 2
}

temporary_bin="$(mktemp "$BIN_ROOT/.openshell-gateway.patched.XXXXXX")"
install -m 0700 -- "$BUILT_BIN" "$temporary_bin"
fsync_regular_file "$temporary_bin"
verify_sha256 "$patched_sha" "$temporary_bin"
installed=1
mv -fT -- "$temporary_bin" "$GATEWAY_BIN"
fsync_regular_file "$GATEWAY_BIN"
fsync_directory "$BIN_ROOT"
sha256_matches "$patched_sha" "$GATEWAY_BIN" || {
  printf '%s\n' 'Installed patched gateway failed final SHA-256 verification.' >&2
  exit 2
}
write_install_journal binary_installed
write_runtime_record "$patched_sha" "$builder_id" staged
runtime_record_matches_state "$patched_sha" staged || {
  printf '%s\n' 'Staged patched gateway failed runtime provenance verification.' >&2
  exit 2
}
write_install_journal runtime_record_staged

SIQ_OPENSHELL_MAINTENANCE_LOCK_HELD=1 "$SCRIPT_DIR/start_gateway.sh"
staged_gateway_running=1
gateway_stopped=0
write_install_journal gateway_started
sha256_matches "$patched_sha" "$GATEWAY_BIN" || {
  printf '%s\n' 'Started gateway binary differs from the staged artifact.' >&2
  exit 2
}
curl --fail --silent --max-time 2 http://127.0.0.1:17672/healthz >/dev/null || {
  printf '%s\n' 'Staged gateway health verification failed.' >&2
  exit 2
}
assert_bind_mounts_inactive
assert_external_runtime_unchanged post-start
assert_gateway_endpoint_shape_unchanged post-start
assert_gateway_inventory_empty

write_runtime_record "$patched_sha" "$builder_id" committed
runtime_record_matches "$patched_sha" || {
  printf '%s\n' 'Committed patched gateway failed runtime provenance verification.' >&2
  exit 2
}
write_install_journal runtime_record_committed
assert_post_commit_runtime post-commit
write_install_journal committed
committed=1
staged_gateway_running=0
gateway_stopped=0
restart_gateway_allowed=0
clear_install_transaction

printf 'Patched OpenShell gateway installed: %s\n' "$patched_sha"
