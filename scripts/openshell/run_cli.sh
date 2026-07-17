#!/usr/bin/env bash
# Execute only the project-local OpenShell CLI with isolated XDG state.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

# Never inherit selectors or server-side state from the user's global OpenShell setup.
unset OPENSHELL_GATEWAY_ENDPOINT OPENSHELL_GATEWAY_CONFIG OPENSHELL_DB_URL OPENSHELL_GATEWAY_INSECURE
unset OPENSHELL_GATEWAY_URL OPENSHELL_CONFIG OPENSHELL_STATE_DIR

if [[ $# -eq 0 ]]; then
  siq_openshell_print_context
  exit 0
fi

previous_selector=""
for argument in "$@"; do
  if [[ "$argument" == *"nemoclaw"* || "$argument" == *"18789"* ]]; then
    printf 'Refusing legacy nemoclaw gateway/port through SIQ wrapper.\n' >&2
    exit 2
  fi
  case "$previous_selector" in
    gateway|endpoint)
      printf 'Explicit gateway selectors are disabled; use the isolated project gateway.\n' >&2
      exit 2
      ;;
  esac
  case "$argument" in
    --gateway|--gateway-endpoint|-g)
      previous_selector="${argument#--}"
      [[ "$argument" == "-g" ]] && previous_selector="gateway"
      [[ "$argument" == "--gateway-endpoint" ]] && previous_selector="endpoint"
      ;;
    --gateway=*|--gateway-endpoint=*|-g*)
      printf 'Explicit gateway selectors are disabled; use the isolated project gateway.\n' >&2
      exit 2
      ;;
    *)
      previous_selector=""
      ;;
  esac
done

if [[ "${1:-}" == "gateway" && "${2:-}" == "destroy" ]] || {
  [[ "${1:-}" == "sandbox" && "${2:-}" == "delete" && "${3:-}" == "--all" ]];
}; then
  if [[ "${SIQ_OPENSHELL_ALLOW_DESTRUCTIVE:-}" != "1" ]]; then
    printf 'Destructive OpenShell action requires SIQ_OPENSHELL_ALLOW_DESTRUCTIVE=1.\n' >&2
    exit 2
  fi
fi

needs_maintenance_lock=0
sandbox_scope_seen=0
for argument in "$@"; do
  case "$argument" in
    sandbox|sb)
      sandbox_scope_seen=1
      ;;
    create|delete)
      if [[ "$sandbox_scope_seen" -eq 1 ]]; then
        needs_maintenance_lock=1
        break
      fi
      ;;
  esac
done
if [[ "$needs_maintenance_lock" -eq 1 ]]; then
  siq_openshell_acquire_maintenance_lock
fi

if [[ ! -x "$SIQ_OPENSHELL_BIN" ]]; then
  printf 'Project-local OpenShell binary not installed: %s\n' "$SIQ_OPENSHELL_BIN" >&2
  printf 'Refusing to fall back to a system or legacy OpenShell binary.\n' >&2
  exit 2
fi

exec "$SIQ_OPENSHELL_BIN" "$@"
