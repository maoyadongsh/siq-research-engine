#!/usr/bin/env bash
# Restore the verified upstream supervisor without touching legacy gateways.

set -euo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

readonly VERSION="0.0.83"
readonly UPSTREAM_COMMIT="e3d26dd3ae0dee247bbc5db368545832757ac493"
readonly PATCH_SHA256="f38cdb0788a9c1f2a38c9aa23ab36b33c4cc6faea135bf6f04bf5eb7bbcdd12f"
readonly EXPECTED="d94630658eb1e62090281160db7cdc542c8cf6667d0c11ff7d9084251f86cfd6"
readonly BIN_ROOT="$SIQ_OPENSHELL_STATE_ROOT/toolchains/v$VERSION/bin"
readonly BIN="$BIN_ROOT/openshell-sandbox"
readonly BACKUP="$BIN.upstream-v$VERSION"
readonly BUILD_ROOT="$SIQ_OPENSHELL_STATE_ROOT/build/v$VERSION"
readonly RUNTIME_RECORD="$BUILD_ROOT/supervisor-patch.runtime"
readonly GATEWAY_ROOT="$SIQ_OPENSHELL_STATE_ROOT/gateway/siq-openshell-dev"
readonly GATEWAY_PID_FILE="$GATEWAY_ROOT/gateway.pid"
readonly GATEWAY_BIN="$BIN_ROOT/openshell-gateway"

gateway_stopped=0
installed=0
committed=0
restart_gateway_allowed=0
current_is_trusted=0
previous_bin=""
previous_sha=""

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
  if [[ "$expected_sha" != "$EXPECTED" ]]; then
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

assert_no_managed_sandboxes() {
  local ids
  ids="$(docker ps -aq \
    --filter 'label=openshell.ai/managed-by=openshell' \
    --filter 'label=openshell.ai/sandbox-namespace=siq-openshell-dev')"
  [[ -z "$ids" ]] || {
    printf 'Refusing supervisor restore while managed sandbox containers exist: %s\n' \
      "$(printf '%s' "$ids" | tr '\n' ' ')" >&2
    exit 2
  }
}

write_upstream_record() {
  local temporary_record
  temporary_record="$(mktemp "$BUILD_ROOT/.supervisor-patch.runtime.XXXXXX")"
  chmod 600 -- "$temporary_record"
  {
    printf 'schema=siq.openshell.supervisor_patch.v1\n'
    printf 'active=upstream\n'
    printf 'version=%s\n' "$VERSION"
    printf 'upstream_commit=%s\n' "$UPSTREAM_COMMIT"
    printf 'patch_sha256=%s\n' "$PATCH_SHA256"
    printf 'upstream_binary_sha256=%s\n' "$EXPECTED"
    printf 'active_binary_sha256=%s\n' "$EXPECTED"
    printf 'installed_path=%s\n' "var/openshell/toolchains/v$VERSION/bin/openshell-sandbox"
  } >"$temporary_record"
  sync -f -- "$temporary_record" 2>/dev/null || true
  mv -f -- "$temporary_record" "$RUNTIME_RECORD"
}

cleanup() {
  local status=$? rollback_bin="" rollback_ok=1
  set +e
  if [[ "$status" -ne 0 && "$installed" -eq 1 && "$committed" -eq 0 && -n "$previous_bin" && -f "$previous_bin" ]]; then
    if [[ "$current_is_trusted" -eq 1 ]]; then
      rollback_bin="$(mktemp "$BIN_ROOT/.openshell-sandbox.rollback.XXXXXX")" || rollback_ok=0
      if [[ "$rollback_ok" -eq 1 ]]; then
        install -m 0700 -- "$previous_bin" "$rollback_bin" || rollback_ok=0
      fi
      if [[ "$rollback_ok" -eq 1 ]] && ! sha256_matches "$previous_sha" "$rollback_bin"; then
        rollback_ok=0
      fi
      if [[ "$rollback_ok" -eq 1 ]]; then
        mv -f -- "$rollback_bin" "$BIN" || rollback_ok=0
      fi
      if [[ "$rollback_ok" -eq 1 ]] && ! sha256_matches "$previous_sha" "$BIN"; then
        rollback_ok=0
      fi
      if [[ "$rollback_ok" -eq 1 ]] && ! runtime_state_matches "$previous_sha"; then
        rollback_ok=0
      fi
    else
      rollback_ok=0
    fi
    if [[ "$rollback_ok" -eq 1 ]]; then
      restart_gateway_allowed=1
      printf '%s\n' 'Restored and verified the previous supervisor after an incomplete upstream restore.' >&2
    else
      restart_gateway_allowed=0
      status=2
      printf '%s\n' 'ERROR: upstream restore did not commit and rollback was not verified; the gateway will remain stopped.' >&2
    fi
  fi
  [[ -n "$rollback_bin" && -f "$rollback_bin" ]] && rm -f -- "$rollback_bin"
  [[ -n "$previous_bin" && -f "$previous_bin" ]] && rm -f -- "$previous_bin"
  if [[ "$gateway_stopped" -eq 1 ]]; then
    if [[ "$restart_gateway_allowed" -ne 1 ]]; then
      printf '%s\n' 'WARNING: isolated gateway remains stopped because supervisor recovery was not verified.' >&2
      status=2
    elif ! SIQ_OPENSHELL_MAINTENANCE_LOCK_HELD=1 "$SCRIPT_DIR/start_gateway.sh"; then
      printf '%s\n' 'WARNING: isolated gateway could not be restarted automatically.' >&2
      status=2
    fi
  fi
  exit "$status"
}
trap cleanup EXIT

siq_openshell_acquire_maintenance_lock
siq_openshell_assert_state_path "$BIN_ROOT"
siq_openshell_assert_state_path "$BUILD_ROOT"
install -d -m 700 -- "$BUILD_ROOT"
siq_openshell_assert_state_path "$BUILD_ROOT"
[[ -f "$BACKUP" && ! -L "$BACKUP" ]] || {
  printf 'Verified upstream backup is missing.\n' >&2
  exit 2
}
verify_sha256 "$EXPECTED" "$BACKUP"
[[ -f "$BIN" && ! -L "$BIN" ]] || { printf 'Current supervisor is missing or unsafe.\n' >&2; exit 2; }

current_sha="$(sha256sum -- "$BIN" | awk '{print $1}')"
if [[ "$current_sha" == "$EXPECTED" ]]; then
  current_is_trusted=1
  restart_gateway_allowed=1
elif runtime_record_matches "$current_sha"; then
  current_is_trusted=1
  restart_gateway_allowed=1
else
  printf '%s\n' \
    'WARNING: current supervisor has no matching runtime provenance; proceeding with the verified upstream recovery image.' >&2
fi

pid=""
if [[ -f "$GATEWAY_PID_FILE" && ! -L "$GATEWAY_PID_FILE" ]]; then
  pid="$(tr -cd '0-9' <"$GATEWAY_PID_FILE")"
fi
[[ "$pid" =~ ^[0-9]+$ && -e "/proc/$pid/exe" ]] || {
  printf '%s\n' 'The isolated gateway must be running for a verified restore.' >&2
  exit 2
}
[[ "$(readlink -f "/proc/$pid/exe")" == "$(readlink -f "$GATEWAY_BIN")" ]] || {
  printf '%s\n' 'Gateway PID file points to an unrelated process.' >&2
  exit 2
}
curl --fail --silent --max-time 1 http://127.0.0.1:17672/healthz >/dev/null || {
  printf '%s\n' 'Gateway health endpoint is unavailable.' >&2
  exit 2
}

sandbox_names="$("$SCRIPT_DIR/run_cli.sh" sandbox list | sed -r 's/\x1B\[[0-9;]*[mK]//g' | awk 'NR > 1 && $1 != "No" {print $1}')"
[[ -z "$sandbox_names" ]] || {
  printf 'Refusing restore while OpenShell sandboxes exist: %s\n' \
    "$(printf '%s' "$sandbox_names" | tr '\n' ' ')" >&2
  exit 2
}
assert_no_managed_sandboxes
SIQ_OPENSHELL_MAINTENANCE_LOCK_HELD=1 "$SCRIPT_DIR/stop_gateway.sh"
gateway_stopped=1
assert_no_managed_sandboxes

post_quiesce_sha="$(sha256sum -- "$BIN" | awk '{print $1}')"
if [[ "$post_quiesce_sha" != "$current_sha" ]]; then
  current_is_trusted=0
  restart_gateway_allowed=0
  current_sha="$post_quiesce_sha"
  printf '%s\n' \
    'WARNING: current supervisor changed while the gateway was being quiesced; continuing only with verified upstream recovery.' >&2
fi

previous_bin="$(mktemp "$BIN_ROOT/.openshell-sandbox.previous.XXXXXX")"
install -m 0700 -- "$BIN" "$previous_bin"
verify_sha256 "$current_sha" "$previous_bin"
previous_sha="$current_sha"
temporary="$(mktemp "$BIN_ROOT/.openshell-sandbox.upstream.XXXXXX")"
install -m 0700 -- "$BACKUP" "$temporary"
verify_sha256 "$EXPECTED" "$temporary"
installed=1
mv -f -- "$temporary" "$BIN"
sha256_matches "$EXPECTED" "$BIN" || {
  printf '%s\n' 'Installed upstream supervisor failed final SHA-256 verification.' >&2
  exit 2
}
write_upstream_record
committed=1
restart_gateway_allowed=1
rm -f -- "$previous_bin"
previous_bin=""

printf 'Restored upstream supervisor %s\n' "$EXPECTED"
