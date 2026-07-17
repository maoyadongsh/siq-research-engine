#!/usr/bin/env bash
# Stop only the project-local SIQ OpenShell gateway process.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"
siq_openshell_acquire_maintenance_lock

GATEWAY_ROOT="$SIQ_OPENSHELL_STATE_ROOT/gateway/siq-openshell-dev"
PID_FILE="$GATEWAY_ROOT/gateway.pid"
RUNTIME_FILE="$GATEWAY_ROOT/gateway.runtime.json"
START_INTENT="$GATEWAY_ROOT/gateway.start.intent.json"
STARTING_FILE="$GATEWAY_ROOT/gateway.starting.json"

if [[ ! -e "$PID_FILE" && ! -L "$PID_FILE" \
  && ! -e "$RUNTIME_FILE" && ! -L "$RUNTIME_FILE" \
  && ! -e "$START_INTENT" && ! -L "$START_INTENT" \
  && ! -e "$STARTING_FILE" && ! -L "$STARTING_FILE" ]]; then
  printf 'SIQ OpenShell gateway is not running.\n'
  exit 0
fi

# Reconcile an interrupted start or reap, and attest a running gateway before
# contacting it for the sandbox safety check.
if ! python3 "$SCRIPT_DIR/gateway_start_recovery.py" \
  --project-root "$SIQ_PROJECT_ROOT" recover >/dev/null; then
  printf 'SIQ gateway recovery requires manual review; evidence was preserved.\n' >&2
  exit 2
fi
if [[ ! -e "$PID_FILE" && ! -L "$PID_FILE" \
  && ! -e "$RUNTIME_FILE" && ! -L "$RUNTIME_FILE" \
  && ! -e "$START_INTENT" && ! -L "$START_INTENT" \
  && ! -e "$STARTING_FILE" && ! -L "$STARTING_FILE" ]]; then
  printf 'SIQ OpenShell gateway is not running.\n'
  exit 0
fi

sandbox_names="$("$SCRIPT_DIR/run_cli.sh" sandbox list | sed -r 's/\x1B\[[0-9;]*[mK]//g' | awk 'NR > 1 && $1 != "No" {print $1}')"
if [[ -n "$sandbox_names" ]]; then
  printf 'Refusing gateway stop while OpenShell sandboxes exist: %s\n' \
    "$(printf '%s' "$sandbox_names" | tr '\n' ' ')" >&2
  exit 2
fi

if ! python3 "$SCRIPT_DIR/gateway_start_recovery.py" \
  --project-root "$SIQ_PROJECT_ROOT" recover --reap >/dev/null; then
  printf 'SIQ gateway recovery could not verify a safe stop; evidence was preserved.\n' >&2
  exit 2
fi
printf 'Stopped SIQ OpenShell gateway.\n'
