#!/usr/bin/env bash
# Source this file before any SIQ OpenShell command.

SIQ_OPENSHELL_SCRIPT_DIR="$(cd -- "$(/usr/bin/dirname -- "${BASH_SOURCE[0]}")" && /bin/pwd -P)"
SIQ_PROJECT_ROOT="$(cd -- "$SIQ_OPENSHELL_SCRIPT_DIR/../.." && /bin/pwd -P)"
export SIQ_PROJECT_ROOT
export SIQ_RUNTIME_ROOT="$SIQ_PROJECT_ROOT/var"
export SIQ_ARTIFACTS_ROOT="$SIQ_PROJECT_ROOT/artifacts"
export SIQ_OPENSHELL_STATE_ROOT="$SIQ_PROJECT_ROOT/var/openshell"
export XDG_CONFIG_HOME="$SIQ_OPENSHELL_STATE_ROOT/xdg/config"
export XDG_STATE_HOME="$SIQ_OPENSHELL_STATE_ROOT/xdg/state"
export XDG_DATA_HOME="$SIQ_OPENSHELL_STATE_ROOT/xdg/data"
export XDG_CACHE_HOME="$SIQ_OPENSHELL_STATE_ROOT/xdg/cache"
export OPENSHELL_LOCAL_TLS_DIR="$XDG_STATE_HOME/openshell/tls"
export OPENSHELL_SYSTEM_GATEWAY_DIR="$XDG_STATE_HOME/openshell/system"
if [[ ! -v OPENSHELL_GATEWAY ]]; then
  export OPENSHELL_GATEWAY="siq-openshell-dev"
fi
export SIQ_OPENSHELL_BIN="$SIQ_OPENSHELL_STATE_ROOT/toolchains/v0.0.83/bin/openshell"

if [[ "$OPENSHELL_GATEWAY" != "siq-openshell-dev" ]]; then
  printf 'Refusing non-isolated OpenShell gateway name: %s\n' "$OPENSHELL_GATEWAY" >&2
  if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    exit 2
  fi
  return 2
fi

# Generated OpenShell state is intentionally confined to var/openshell. These
# checks are shared by lifecycle scripts before they create locks, mounts, or
# replace project-local binaries.
siq_openshell_assert_state_path() {
  local path="$1" component current relative mode owner canonical_root canonical_path
  [[ "$path" == "$SIQ_OPENSHELL_STATE_ROOT" || "$path" == "$SIQ_OPENSHELL_STATE_ROOT"/* ]] || {
    printf 'OpenShell state path escapes the project state root: %s\n' "$path" >&2
    return 2
  }

  canonical_root="$(realpath -e -- "$SIQ_OPENSHELL_STATE_ROOT" 2>/dev/null)" || {
    printf 'OpenShell state root is not a real directory: %s\n' "$SIQ_OPENSHELL_STATE_ROOT" >&2
    return 2
  }
  canonical_path="$(realpath -m -- "$path")"
  [[ "$canonical_path" == "$canonical_root" || "$canonical_path" == "$canonical_root"/* ]] || {
    printf 'OpenShell state path resolves outside the project state root: %s\n' "$path" >&2
    return 2
  }

  relative="${path#"$SIQ_PROJECT_ROOT"/}"
  current="$SIQ_PROJECT_ROOT"
  IFS='/' read -r -a components <<<"$relative"
  for component in "${components[@]}"; do
    [[ -n "$component" && "$component" != "." && "$component" != ".." ]] || continue
    current="$current/$component"
    [[ ! -L "$current" ]] || {
      printf 'OpenShell state path contains a symlink: %s\n' "$current" >&2
      return 2
    }
    if [[ -e "$current" ]]; then
      owner="$(stat -c '%u' -- "$current")"
      [[ "$owner" == "$(id -u)" ]] || {
        printf 'OpenShell state path is not owned by the current user: %s\n' "$current" >&2
        return 2
      }
      if [[ -d "$current" && ( "$current" == "$SIQ_OPENSHELL_STATE_ROOT" || "$current" == "$SIQ_OPENSHELL_STATE_ROOT"/* ) ]]; then
        mode="$(stat -c '%a' -- "$current")"
        (( (8#$mode & 8#022) == 0 )) || {
          printf 'OpenShell state directory is group/world writable: %s\n' "$current" >&2
          return 2
        }
      fi
    fi
  done
}

siq_openshell_acquire_maintenance_lock() {
  local lock_dir lock_path inherited_fd lock_fd inherited_target canonical_lock
  siq_openshell_assert_state_path "$SIQ_OPENSHELL_STATE_ROOT" || return
  lock_dir="$SIQ_OPENSHELL_STATE_ROOT/locks"
  siq_openshell_assert_state_path "$lock_dir" || return
  install -d -m 700 -- "$lock_dir" || return
  siq_openshell_assert_state_path "$lock_dir" || return
  lock_path="$lock_dir/maintenance.lock"
  siq_openshell_assert_state_path "$lock_path" || return

  inherited_fd="${SIQ_OPENSHELL_MAINTENANCE_FD:-}"
  canonical_lock="$(realpath -m -- "$lock_path")"
  if [[ "$inherited_fd" =~ ^[0-9]+$ && -e "/proc/$$/fd/$inherited_fd" ]]; then
    inherited_target="$(readlink -f "/proc/$$/fd/$inherited_fd" 2>/dev/null || true)"
    if [[ "$inherited_target" == "$canonical_lock" ]]; then
      return 0
    fi
  fi

  exec {lock_fd}>>"$lock_path" || return
  chmod 600 -- "$lock_path" || return
  flock -n "$lock_fd" || {
    eval "exec ${lock_fd}>&-"
    printf '%s\n' 'Another SIQ OpenShell lifecycle operation is in progress.' >&2
    return 75
  }
  export SIQ_OPENSHELL_MAINTENANCE_FD="$lock_fd"
}

siq_openshell_close_maintenance_lock_copy() {
  local lock_fd="${SIQ_OPENSHELL_MAINTENANCE_FD:-}"
  if [[ "$lock_fd" =~ ^[0-9]+$ && -e "/proc/$$/fd/$lock_fd" ]]; then
    eval "exec ${lock_fd}>&-"
  fi
  unset SIQ_OPENSHELL_MAINTENANCE_FD
}

siq_openshell_print_context() {
  printf 'SIQ_PROJECT_ROOT=%s\n' "$SIQ_PROJECT_ROOT"
  printf 'SIQ_OPENSHELL_STATE_ROOT=%s\n' "$SIQ_OPENSHELL_STATE_ROOT"
  printf 'XDG_CONFIG_HOME=%s\n' "$XDG_CONFIG_HOME"
  printf 'XDG_STATE_HOME=%s\n' "$XDG_STATE_HOME"
  printf 'XDG_DATA_HOME=%s\n' "$XDG_DATA_HOME"
  printf 'XDG_CACHE_HOME=%s\n' "$XDG_CACHE_HOME"
  printf 'OPENSHELL_LOCAL_TLS_DIR=%s\n' "$OPENSHELL_LOCAL_TLS_DIR"
  printf 'OPENSHELL_SYSTEM_GATEWAY_DIR=%s\n' "$OPENSHELL_SYSTEM_GATEWAY_DIR"
  printf 'OPENSHELL_GATEWAY=%s\n' "$OPENSHELL_GATEWAY"
  printf 'SIQ_OPENSHELL_BIN=%s\n' "$SIQ_OPENSHELL_BIN"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  siq_openshell_print_context
fi
