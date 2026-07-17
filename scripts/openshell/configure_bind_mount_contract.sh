#!/usr/bin/env bash
# Atomically enable, disable, or recover the strict SIQ bind-mount contract.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"
siq_openshell_acquire_maintenance_lock

readonly ACTION="${1:-status}"
readonly GATEWAY_NAME="siq-openshell-dev"
readonly GATEWAY_ROOT="$SIQ_OPENSHELL_STATE_ROOT/gateway/$GATEWAY_NAME"
readonly CONFIG="$GATEWAY_ROOT/gateway.toml"
readonly PID_FILE="$GATEWAY_ROOT/gateway.pid"
readonly RUNTIME_FILE="$GATEWAY_ROOT/gateway.runtime.json"
readonly ACTIVATION_RECORD="$GATEWAY_ROOT/bind-contract.activation.json"
readonly JOURNAL="$GATEWAY_ROOT/bind-contract.transaction"
readonly PREVIOUS_ACTIVATION="$GATEWAY_ROOT/.bind-contract.previous.activation"
readonly PREVIOUS_CONFIG="$GATEWAY_ROOT/.bind-contract.previous.gateway.toml"
readonly TARGET_ACTIVATION="$GATEWAY_ROOT/.bind-contract.target.activation"
readonly PROTECTED_PORTS_REGEX=':(8004|8006|8007|8013|18651|18789|18792|18793)$'
readonly TARGET_BIND_PATCH_SHA256='a877673ef005212049b860168c3401651e189beb96d39489fdea53fac61c2752'

transaction_id=""
target_state=""
journal_phase=""
previous_activation_existed=""
previous_activation_sha256=""
previous_config_sha256=""
protected_listener_sha256=""
transaction_owned=0
recovering=0
legacy_active=0

usage() {
  printf 'Usage: %s {enable|disable|recover|status}\n' "$0" >&2
}

require_private_regular_file() {
  local path="$1" mode owner
  [[ -f "$path" && ! -L "$path" ]] || {
    printf 'Missing or unsafe private file: %s\n' "$path" >&2
    return 2
  }
  owner="$(stat -c '%u' -- "$path")"
  mode="$(stat -c '%a' -- "$path")"
  [[ "$owner" == "$(id -u)" && $((8#$mode & 8#077)) -eq 0 ]] || {
    printf 'Unsafe owner or mode on private file: %s\n' "$path" >&2
    return 2
  }
}

fsync_regular_file() {
  python3 - "$1" <<'PY'
import os
import stat
import sys

flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
descriptor = os.open(sys.argv[1], flags)
try:
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        raise SystemExit(2)
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
}

fsync_directory() {
  python3 - "$1" <<'PY'
import os
import stat
import sys

flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
descriptor = os.open(sys.argv[1], flags)
try:
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        raise SystemExit(2)
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
}

sha256_file() {
  sha256sum -- "$1" | awk '{print $1}'
}

verify_legacy_active_contract() {
  local binary="$SIQ_OPENSHELL_STATE_ROOT/toolchains/v0.0.83/bin/openshell-gateway" binary_sha
  require_private_regular_file "$binary"
  binary_sha="$(sha256_file "$binary")"
  python3 "$SCRIPT_DIR/gateway_patch_migration.py" \
    --project-root "$SIQ_PROJECT_ROOT" \
    --current-binary-sha256 "$binary_sha" \
    --target-patch-sha256 "$TARGET_BIND_PATCH_SHA256" \
    --require-active-legacy-contract
}

protected_listener_digest() {
  ss -H -ltnp \
    | awk -v pattern="$PROTECTED_PORTS_REGEX" '$4 ~ pattern {print}' \
    | LC_ALL=C sort \
    | sha256sum \
    | awk '{print $1}'
}

managed_sandbox_container_ids() {
  docker ps -aq \
    --filter 'label=openshell.ai/managed-by=openshell' \
    --filter 'label=openshell.ai/sandbox-namespace=siq-openshell-dev'
}

assert_no_managed_sandbox_containers() {
  local ids
  ids="$(managed_sandbox_container_ids)"
  [[ -z "$ids" ]] || {
    printf 'Refusing bind-contract maintenance while managed sandbox containers exist: %s\n' \
      "$(tr '\n' ' ' <<<"$ids")" >&2
    return 2
  }
}

gateway_is_running() {
  if [[ ! -e "$PID_FILE" && ! -L "$PID_FILE" && ! -e "$RUNTIME_FILE" && ! -L "$RUNTIME_FILE" ]]; then
    return 1
  fi
  python3 "$SCRIPT_DIR/gateway_runtime_identity.py" \
    --project-root "$SIQ_PROJECT_ROOT" verify >/dev/null || {
    printf 'SIQ gateway runtime identity is stale or mismatched.\n' >&2
    return 2
  }
}

assert_gateway_inventory_empty() {
  local sandbox_names
  gateway_is_running
  sandbox_names="$("$SCRIPT_DIR/run_cli.sh" sandbox list \
    | sed -r 's/\x1B\[[0-9;]*[mK]//g' \
    | awk 'NR > 1 && $1 != "No" {print $1}')"
  [[ -z "$sandbox_names" ]] || {
    printf 'Refusing bind-contract maintenance while sandboxes exist: %s\n' \
      "$(printf '%s' "$sandbox_names" | tr '\n' ' ')" >&2
    return 2
  }
  assert_no_managed_sandbox_containers
}

atomic_copy() {
  local source="$1" destination="$2" temporary
  require_private_regular_file "$source"
  temporary="$(mktemp "$GATEWAY_ROOT/.bind-contract.copy.XXXXXX")"
  install -m 0600 -- "$source" "$temporary"
  fsync_regular_file "$temporary"
  mv -fT -- "$temporary" "$destination"
  fsync_regular_file "$destination"
  fsync_directory "$GATEWAY_ROOT"
}

write_journal() {
  local phase="$1" temporary
  case "$phase" in
    prepared|gateway_stopped|config_switched|gateway_started|committed|rollback_incomplete) ;;
    *) printf 'Invalid bind-contract journal phase: %s\n' "$phase" >&2; return 2 ;;
  esac
  temporary="$(mktemp "$GATEWAY_ROOT/.bind-contract.transaction.XXXXXX")"
  chmod 0600 -- "$temporary"
  {
    printf 'schema=siq.openshell.bind_contract_transaction.v1\n'
    printf 'transaction_id=%s\n' "$transaction_id"
    printf 'phase=%s\n' "$phase"
    printf 'target_state=%s\n' "$target_state"
    printf 'previous_activation_existed=%s\n' "$previous_activation_existed"
    printf 'previous_activation_sha256=%s\n' "$previous_activation_sha256"
    printf 'previous_config_sha256=%s\n' "$previous_config_sha256"
    printf 'protected_listener_sha256=%s\n' "$protected_listener_sha256"
    printf 'gateway_was_running=1\n'
  } >"$temporary"
  fsync_regular_file "$temporary"
  mv -fT -- "$temporary" "$JOURNAL"
  fsync_regular_file "$JOURNAL"
  fsync_directory "$GATEWAY_ROOT"
  journal_phase="$phase"
}

load_journal() {
  local key value
  declare -A values=()
  require_private_regular_file "$JOURNAL" || return
  while IFS='=' read -r key value; do
    [[ -n "$key" && "$key" =~ ^[a-z0-9_]+$ && ! -v "values[$key]" ]] || {
      printf 'Malformed bind-contract transaction journal.\n' >&2
      return 2
    }
    values[$key]="$value"
  done <"$JOURNAL"
  [[ "${#values[@]}" -eq 9 \
    && "${values[schema]:-}" == siq.openshell.bind_contract_transaction.v1 \
    && "${values[transaction_id]:-}" =~ ^[0-9a-f]{32}$ \
    && "${values[phase]:-}" =~ ^(prepared|gateway_stopped|config_switched|gateway_started|committed|rollback_incomplete)$ \
    && "${values[target_state]:-}" =~ ^(enabled|disabled)$ \
    && "${values[previous_activation_existed]:-}" =~ ^(0|1)$ \
    && "${values[previous_activation_sha256]:-}" =~ ^(absent|[0-9a-f]{64})$ \
    && "${values[previous_config_sha256]:-}" =~ ^[0-9a-f]{64}$ \
    && "${values[protected_listener_sha256]:-}" =~ ^[0-9a-f]{64}$ \
    && "${values[gateway_was_running]:-}" == 1 ]] || {
    printf 'Bind-contract transaction journal failed schema validation.\n' >&2
    return 2
  }
  transaction_id="${values[transaction_id]}"
  journal_phase="${values[phase]}"
  target_state="${values[target_state]}"
  previous_activation_existed="${values[previous_activation_existed]}"
  previous_activation_sha256="${values[previous_activation_sha256]}"
  previous_config_sha256="${values[previous_config_sha256]}"
  protected_listener_sha256="${values[protected_listener_sha256]}"
}

clear_transaction_files() {
  rm -f -- "$JOURNAL"
  fsync_directory "$GATEWAY_ROOT"
  rm -f -- "$PREVIOUS_ACTIVATION" "$PREVIOUS_CONFIG" "$TARGET_ACTIVATION"
  fsync_directory "$GATEWAY_ROOT"
  transaction_owned=0
  transaction_id=""
}

stop_running_gateway_for_recovery() {
  local state
  python3 "$SCRIPT_DIR/gateway_start_recovery.py" \
    --project-root "$SIQ_PROJECT_ROOT" recover >/dev/null
  if gateway_is_running; then
    assert_gateway_inventory_empty
    SIQ_OPENSHELL_BIND_TRANSACTION_ID="$transaction_id" "$SCRIPT_DIR/stop_gateway.sh"
    return 0
  else
    state=$?
  fi
  [[ "$state" -eq 1 ]] || return "$state"
  if curl --fail --silent --max-time 1 http://127.0.0.1:17672/healthz >/dev/null 2>&1; then
    printf 'Gateway health is reachable without a verified project PID; refusing recovery.\n' >&2
    return 2
  fi
  assert_no_managed_sandbox_containers
}

restore_previous_state() {
  local current_listener_sha
  recovering=1
  load_journal || return
  require_private_regular_file "$PREVIOUS_CONFIG" || return
  [[ "$(sha256_file "$PREVIOUS_CONFIG")" == "$previous_config_sha256" ]] || {
    printf 'Previous gateway configuration backup failed SHA-256 verification.\n' >&2
    return 2
  }
  if [[ "$previous_activation_existed" == 1 ]]; then
    require_private_regular_file "$PREVIOUS_ACTIVATION" || return
    [[ "$(sha256_file "$PREVIOUS_ACTIVATION")" == "$previous_activation_sha256" ]] || {
      printf 'Previous activation backup failed SHA-256 verification.\n' >&2
      return 2
    }
  fi

  stop_running_gateway_for_recovery || return
  if [[ "$previous_activation_existed" == 1 ]]; then
    atomic_copy "$PREVIOUS_ACTIVATION" "$ACTIVATION_RECORD"
  else
    rm -f -- "$ACTIVATION_RECORD"
    fsync_directory "$GATEWAY_ROOT"
  fi
  atomic_copy "$PREVIOUS_CONFIG" "$CONFIG"

  if python3 "$SCRIPT_DIR/gateway_bind_contract.py" \
      --project-root "$SIQ_PROJECT_ROOT" verify-activation \
      --activation-record "$ACTIVATION_RECORD" >/dev/null 2>&1; then
    SIQ_OPENSHELL_BIND_TRANSACTION_ID="$transaction_id" \
      python3 "$SCRIPT_DIR/render_gateway_config.py" --project-root "$SIQ_PROJECT_ROOT"
    [[ "$(sha256_file "$CONFIG")" == "$previous_config_sha256" ]] || {
      printf 'Restored activation no longer renders the previous gateway configuration.\n' >&2
      write_journal rollback_incomplete
      return 2
    }
  elif ! verify_legacy_active_contract >/dev/null; then
    printf 'Restored activation is neither the current nor reviewed legacy contract.\n' >&2
    write_journal rollback_incomplete
    return 2
  fi
  SIQ_OPENSHELL_BIND_TRANSACTION_ID="$transaction_id" "$SCRIPT_DIR/start_gateway.sh"
  gateway_is_running
  assert_gateway_inventory_empty
  current_listener_sha="$(protected_listener_digest)"
  clear_transaction_files
  recovering=0
  if [[ "$current_listener_sha" != "$protected_listener_sha256" ]]; then
    printf 'WARNING: protected non-gateway listeners changed while bind-contract recovery was pending.\n' >&2
  fi
  printf 'Restored the previous disabled/enabled gateway state from the bind-contract journal.\n' >&2
}

begin_transaction() {
  local requested_state="$1"
  assert_gateway_inventory_empty
  legacy_active=0
  if ! python3 "$SCRIPT_DIR/gateway_bind_contract.py" \
      --project-root "$SIQ_PROJECT_ROOT" verify-runtime >/dev/null 2>&1; then
    if [[ "$requested_state" == disabled ]] && verify_legacy_active_contract >/dev/null; then
      legacy_active=1
    else
      printf 'Gateway runtime is neither the current nor reviewed legacy contract.\n' >&2
      return 2
    fi
  fi
  require_private_regular_file "$CONFIG"
  if [[ "$legacy_active" -eq 0 ]]; then
    python3 "$SCRIPT_DIR/render_gateway_config.py" --project-root "$SIQ_PROJECT_ROOT" --check || {
      printf 'Current gateway configuration is not reproducible from its activation state.\n' >&2
      return 2
    }
  fi

  rm -f -- "$PREVIOUS_ACTIVATION" "$PREVIOUS_CONFIG" "$TARGET_ACTIVATION"
  atomic_copy "$CONFIG" "$PREVIOUS_CONFIG"
  previous_config_sha256="$(sha256_file "$PREVIOUS_CONFIG")"
  if [[ -e "$ACTIVATION_RECORD" || -L "$ACTIVATION_RECORD" ]]; then
    if [[ "$legacy_active" -eq 0 ]]; then
      python3 "$SCRIPT_DIR/gateway_bind_contract.py" \
        --project-root "$SIQ_PROJECT_ROOT" verify-activation \
        --activation-record "$ACTIVATION_RECORD"
    else
      verify_legacy_active_contract
    fi
    atomic_copy "$ACTIVATION_RECORD" "$PREVIOUS_ACTIVATION"
    previous_activation_existed=1
    previous_activation_sha256="$(sha256_file "$PREVIOUS_ACTIVATION")"
  else
    previous_activation_existed=0
    previous_activation_sha256=absent
  fi
  if [[ "$requested_state" == enabled ]]; then
    python3 "$SCRIPT_DIR/gateway_bind_contract.py" \
      --project-root "$SIQ_PROJECT_ROOT" create --output "$TARGET_ACTIVATION"
    require_private_regular_file "$TARGET_ACTIVATION"
  fi

  transaction_id="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
  [[ "$transaction_id" =~ ^[0-9a-f]{32}$ ]] || return 2
  target_state="$requested_state"
  protected_listener_sha256="$(protected_listener_digest)"
  write_journal prepared
  transaction_owned=1
}

apply_target_state() {
  SIQ_OPENSHELL_BIND_TRANSACTION_ID="$transaction_id" "$SCRIPT_DIR/stop_gateway.sh"
  write_journal gateway_stopped

  if [[ "$target_state" == enabled ]]; then
    mv -fT -- "$TARGET_ACTIVATION" "$ACTIVATION_RECORD"
    chmod 0600 -- "$ACTIVATION_RECORD"
    fsync_regular_file "$ACTIVATION_RECORD"
  else
    rm -f -- "$ACTIVATION_RECORD"
  fi
  fsync_directory "$GATEWAY_ROOT"
  python3 "$SCRIPT_DIR/render_gateway_config.py" --project-root "$SIQ_PROJECT_ROOT"
  python3 "$SCRIPT_DIR/render_gateway_config.py" --project-root "$SIQ_PROJECT_ROOT" --check
  write_journal config_switched

  SIQ_OPENSHELL_BIND_TRANSACTION_ID="$transaction_id" "$SCRIPT_DIR/start_gateway.sh"
  write_journal gateway_started
  gateway_is_running
  assert_gateway_inventory_empty
  if [[ "$target_state" == enabled ]]; then
    python3 "$SCRIPT_DIR/gateway_bind_contract.py" \
      --project-root "$SIQ_PROJECT_ROOT" verify-activation \
      --activation-record "$ACTIVATION_RECORD"
    grep -Fxq 'enable_bind_mounts = true' "$CONFIG"
    grep -Fxq 'bind_mount_contract = "siq_analysis_v2"' "$CONFIG"
    grep -Fxq "bind_mount_project_root = \"$SIQ_PROJECT_ROOT\"" "$CONFIG"
  else
    [[ ! -e "$ACTIVATION_RECORD" && ! -L "$ACTIVATION_RECORD" ]]
    grep -Fxq 'enable_bind_mounts = false' "$CONFIG"
    ! grep -Eq '^(bind_mount_contract|bind_mount_project_root)[[:space:]]*=' "$CONFIG"
  fi
  [[ "$(protected_listener_digest)" == "$protected_listener_sha256" ]] || {
    printf 'A protected non-gateway listener changed during bind-contract activation.\n' >&2
    return 2
  }
  write_journal committed
  clear_transaction_files
  printf 'SIQ strict bind-mount contract is now %s; gateway restart verified.\n' "$target_state"
}

status() {
  local state=disabled runtime=stopped
  if [[ -e "$JOURNAL" || -L "$JOURNAL" ]]; then
    printf 'Bind-contract transaction requires recovery: %s\n' "$JOURNAL" >&2
    return 75
  fi
  if [[ -e "$ACTIVATION_RECORD" || -L "$ACTIVATION_RECORD" ]]; then
    python3 "$SCRIPT_DIR/gateway_bind_contract.py" \
      --project-root "$SIQ_PROJECT_ROOT" verify-activation \
      --activation-record "$ACTIVATION_RECORD" >/dev/null
    state=enabled
  fi
  python3 "$SCRIPT_DIR/render_gateway_config.py" --project-root "$SIQ_PROJECT_ROOT" --check || {
    printf 'Bind-contract state and rendered gateway configuration differ.\n' >&2
    return 2
  }
  if gateway_is_running; then
    runtime=running
  else
    local result=$?
    [[ "$result" -eq 1 ]] || return "$result"
  fi
  printf 'bind_contract=%s gateway=%s\n' "$state" "$runtime"
}

on_exit() {
  local exit_status=$?
  trap - EXIT INT TERM
  if [[ "$exit_status" -ne 0 && "$transaction_owned" -eq 1 && "$recovering" -eq 0 \
    && ( -e "$JOURNAL" || -L "$JOURNAL" ) ]]; then
    printf 'Bind-contract change failed; restoring the previous gateway state.\n' >&2
    if ! restore_previous_state; then
      printf 'ERROR: bind-contract rollback is incomplete; gateway state requires review.\n' >&2
      exit 2
    fi
  fi
  exit "$exit_status"
}

trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

case "$ACTION" in
  enable|disable|recover|status) ;;
  *) usage; exit 2 ;;
esac

install -d -m 0700 -- "$GATEWAY_ROOT"
siq_openshell_assert_state_path "$GATEWAY_ROOT"

if [[ "$ACTION" == status ]]; then
  status
  exit 0
fi

python3 "$SCRIPT_DIR/gateway_start_recovery.py" \
  --project-root "$SIQ_PROJECT_ROOT" recover >/dev/null

if [[ -e "$JOURNAL" || -L "$JOURNAL" ]]; then
  restore_previous_state
  if [[ "$ACTION" == recover ]]; then
    exit 0
  fi
elif [[ "$ACTION" == recover ]]; then
  printf 'No bind-contract transaction requires recovery.\n'
  exit 0
fi

if [[ "$ACTION" == enable ]]; then
  if [[ -e "$ACTIVATION_RECORD" && ! -L "$ACTIVATION_RECORD" ]] \
    && python3 "$SCRIPT_DIR/gateway_bind_contract.py" \
      --project-root "$SIQ_PROJECT_ROOT" verify-activation \
      --activation-record "$ACTIVATION_RECORD" >/dev/null \
    && python3 "$SCRIPT_DIR/render_gateway_config.py" --project-root "$SIQ_PROJECT_ROOT" --check; then
    assert_gateway_inventory_empty
    printf 'SIQ strict bind-mount contract is already enabled and verified.\n'
    exit 0
  fi
  begin_transaction enabled
else
  if [[ ! -e "$ACTIVATION_RECORD" && ! -L "$ACTIVATION_RECORD" ]] \
    && python3 "$SCRIPT_DIR/render_gateway_config.py" --project-root "$SIQ_PROJECT_ROOT" --check; then
    assert_gateway_inventory_empty
    printf 'SIQ strict bind-mount contract is already disabled and verified.\n'
    exit 0
  fi
  begin_transaction disabled
fi

apply_target_state
