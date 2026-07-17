#!/usr/bin/env bash
# Start only the isolated SIQ OpenShell gateway; never touch legacy gateways.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"
siq_openshell_acquire_maintenance_lock

VERSION="0.0.83"
GATEWAY_NAME="siq-openshell-dev"
GATEWAY_PORT=17671
HEALTH_PORT=17672
GATEWAY_ROOT="$SIQ_OPENSHELL_STATE_ROOT/gateway/$GATEWAY_NAME"
GATEWAY_BIN="$SIQ_OPENSHELL_STATE_ROOT/toolchains/v$VERSION/bin/openshell-gateway"
CONFIG="$GATEWAY_ROOT/gateway.toml"
PID_FILE="$GATEWAY_ROOT/gateway.pid"
RUNTIME_FILE="$GATEWAY_ROOT/gateway.runtime.json"
LOG_FILE="$GATEWAY_ROOT/gateway.log"
DB_PATH="$GATEWAY_ROOT/openshell.db"
START_INTENT="$GATEWAY_ROOT/gateway.start.intent.json"
STARTING_FILE="$GATEWAY_ROOT/gateway.starting.json"

python3 "$SCRIPT_DIR/gateway_start_recovery.py" \
  --project-root "$SIQ_PROJECT_ROOT" recover >/dev/null
"$SCRIPT_DIR/prepare_gateway.sh"

if [[ -e "$PID_FILE" || -L "$PID_FILE" || -e "$RUNTIME_FILE" || -L "$RUNTIME_FILE" ]]; then
  if python3 "$SCRIPT_DIR/gateway_runtime_identity.py" \
    --project-root "$SIQ_PROJECT_ROOT" verify >/dev/null; then
    existing_pid="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["pid"])' \
      "$RUNTIME_FILE")"
    printf 'Gateway already running with PID %s (verified runtime identity).\n' "$existing_pid"
    exit 0
  fi
  printf 'SIQ gateway PID/runtime evidence is stale or mismatched; refusing automatic cleanup.\n' >&2
  exit 2
fi

if ss -ltnH "sport = :$GATEWAY_PORT" | grep -q . || ss -ltnH "sport = :$HEALTH_PORT" | grep -q .; then
  printf 'SIQ OpenShell gateway or health port is already occupied.\n' >&2
  exit 2
fi

available_kib="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
if [[ -z "$available_kib" || "$available_kib" -lt 2097152 ]]; then
  printf 'Refusing gateway start with less than 2 GiB available memory.\n' >&2
  exit 2
fi

gateway_pid=""
launcher_pid=""
cleanup_needed=0
cleanup_failed_start() {
  if [[ -e "$START_INTENT" || -L "$START_INTENT" \
    || -e "$STARTING_FILE" || -L "$STARTING_FILE" \
    || -e "$PID_FILE" || -L "$PID_FILE" \
    || -e "$RUNTIME_FILE" || -L "$RUNTIME_FILE" ]]; then
    if ! python3 "$SCRIPT_DIR/gateway_start_recovery.py" \
      --project-root "$SIQ_PROJECT_ROOT" recover --reap >/dev/null; then
      printf 'Gateway failed-start cleanup could not verify recovery; evidence was preserved.\n' >&2
      return 2
    fi
  fi
  if [[ "$gateway_pid" =~ ^[1-9][0-9]*$ ]]; then
    wait "$gateway_pid" 2>/dev/null || true
  fi
  if [[ "$launcher_pid" =~ ^[1-9][0-9]*$ && "$launcher_pid" != "$gateway_pid" ]]; then
    wait "$launcher_pid" 2>/dev/null || true
  fi
}
cleanup_on_exit() {
  exit_status=$?
  if [[ "$cleanup_needed" == "1" && "$exit_status" -ne 0 ]]; then
    cleanup_failed_start || exit_status=2
  fi
  trap - EXIT
  exit "$exit_status"
}
trap cleanup_on_exit EXIT

python3 "$SCRIPT_DIR/gateway_start_recovery.py" \
  --project-root "$SIQ_PROJECT_ROOT" prepare >/dev/null
cleanup_needed=1

umask 077
(
  ulimit -v 2097152
  export OPENSHELL_GATEWAY_CONFIG="$CONFIG"
  export OPENSHELL_DB_URL="sqlite:$DB_PATH"
  export OPENSHELL_TELEMETRY_ENABLED=false
  siq_openshell_close_maintenance_lock_copy
  exec nohup setsid "$GATEWAY_BIN"
) </dev/null >>"$LOG_FILE" 2>&1 &
launcher_pid=$!
gateway_pid="$(python3 "$SCRIPT_DIR/gateway_start_recovery.py" \
  --project-root "$SIQ_PROJECT_ROOT" attach --pid "$launcher_pid")"
[[ "$gateway_pid" =~ ^[1-9][0-9]*$ ]] || {
  printf 'Gateway provisional identity returned an invalid PID.\n' >&2
  exit 2
}

for _ in $(seq 1 30); do
  if curl --fail --silent --max-time 1 "http://127.0.0.1:$HEALTH_PORT/healthz" >/dev/null; then
    break
  fi
  if ! kill -0 "$gateway_pid" 2>/dev/null; then
    printf 'SIQ OpenShell gateway exited during startup.\n' >&2
    exit 2
  fi
  sleep 1
done
if ! curl --fail --silent --max-time 1 "http://127.0.0.1:$HEALTH_PORT/healthz" >/dev/null; then
  printf 'SIQ OpenShell gateway health check timed out.\n' >&2
  exit 2
fi

python3 "$SCRIPT_DIR/gateway_start_recovery.py" \
  --project-root "$SIQ_PROJECT_ROOT" commit --pid "$gateway_pid" || {
  printf 'Gateway health passed but runtime identity attestation failed.\n' >&2
  exit 2
}

gateway_info="$("$SCRIPT_DIR/run_cli.sh" gateway info --name "$GATEWAY_NAME" 2>&1 || true)"
if [[ "$gateway_info" == *"Gateway endpoint:"* ]]; then
  if [[ "$gateway_info" != *"https://127.0.0.1:$GATEWAY_PORT"* ]]; then
    printf 'Existing SIQ gateway registration points to an unexpected endpoint.\n' >&2
    exit 2
  fi
elif ! "$SCRIPT_DIR/run_cli.sh" gateway add "https://127.0.0.1:$GATEWAY_PORT" --local --name "$GATEWAY_NAME" >/dev/null 2>&1; then
  printf 'Failed to register the isolated SIQ gateway.\n' >&2
  exit 2
fi

status_output="$("$SCRIPT_DIR/run_cli.sh" status 2>&1)"
status_plain="$(printf '%s\n' "$status_output" | sed -E $'s/\x1B\\[[0-9;?]*[ -\\/]*[@-~]//g')"
if [[ "$status_plain" != *"Status: Connected"* || "$status_plain" != *"Version: $VERSION"* ]]; then
  printf '%s\n' "$status_plain" >&2
  printf 'Gateway started but CLI version/connection verification failed.\n' >&2
  exit 2
fi

python3 "$SCRIPT_DIR/gateway_runtime_identity.py" \
  --project-root "$SIQ_PROJECT_ROOT" verify >/dev/null || {
  printf 'Gateway runtime identity drifted during registration.\n' >&2
  exit 2
}

cleanup_needed=0
printf 'OpenShell gateway %s connected on 127.0.0.1:%s (PID %s)\n' "$GATEWAY_NAME" "$GATEWAY_PORT" "$gateway_pid"
