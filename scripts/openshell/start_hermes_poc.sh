#!/usr/bin/env bash
# Start the minimal Hermes sandbox and a loopback-only host forward.

set -euo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"
# shellcheck source=process_helpers.sh
source "$SCRIPT_DIR/process_helpers.sh"
siq_openshell_acquire_maintenance_lock

readonly SANDBOX_NAME="siq-hermes-minimal-poc"
readonly PORT="28642"
readonly POLICY="$ROOT_DIR/infra/openshell/poc/hermes-minimal/policy.yaml"
readonly STATE_DIR="$SIQ_OPENSHELL_STATE_ROOT/poc/hermes-minimal"
readonly FORWARD_PID_FILE="$STATE_DIR/forward.pid"
readonly FORWARD_LOG="$STATE_DIR/forward.log"
readonly API_KEY_FILE="$STATE_DIR/api.key"
readonly RUN_NONCE_FILE="$STATE_DIR/run.nonce"
readonly SUPERVISOR_BIN="$SIQ_OPENSHELL_STATE_ROOT/toolchains/v0.0.83/bin/openshell-sandbox"
readonly SUPERVISOR_RECORD="$SIQ_OPENSHELL_STATE_ROOT/build/v0.0.83/supervisor-patch.runtime"
readonly SUPERVISOR_PATCH_SHA256="f38cdb0788a9c1f2a38c9aa23ab36b33c4cc6faea135bf6f04bf5eb7bbcdd12f"

siq_openshell_assert_state_path "$STATE_DIR"
mkdir -p -- "$STATE_DIR"
chmod 0700 -- "$STATE_DIR"

if [[ ! -f "$SUPERVISOR_BIN" || -L "$SUPERVISOR_BIN" || ! -f "$SUPERVISOR_RECORD" || -L "$SUPERVISOR_RECORD" ]]; then
  printf '%s\n' 'The reviewed OpenShell supervisor patch is not installed.' >&2
  exit 2
fi
recorded_active="$(awk -F= '$1 == "active" {print $2}' "$SUPERVISOR_RECORD")"
recorded_patch="$(awk -F= '$1 == "patch_sha256" {print $2}' "$SUPERVISOR_RECORD")"
recorded_binary="$(awk -F= '$1 == "patched_binary_sha256" {print $2}' "$SUPERVISOR_RECORD")"
installed_binary="$(sha256sum -- "$SUPERVISOR_BIN" | awk '{print $1}')"
if [[ "$recorded_active" != patched || "$recorded_patch" != "$SUPERVISOR_PATCH_SHA256" || "$recorded_binary" != "$installed_binary" ]]; then
  printf '%s\n' 'The installed supervisor does not match the reviewed patch record.' >&2
  exit 2
fi

if ss -ltnH "sport = :$PORT" | grep -q .; then
  printf 'Host port %s is already in use; refusing to disturb its owner.\n' "$PORT" >&2
  exit 2
fi
if "$SCRIPT_DIR/run_cli.sh" sandbox list | sed -r 's/\x1B\[[0-9;]*[mK]//g' | awk '{print $1}' | grep -Fxq "$SANDBOX_NAME"; then
  printf 'Sandbox already exists: %s\n' "$SANDBOX_NAME" >&2
  exit 2
fi
if [[ -e "$FORWARD_PID_FILE" ]]; then
  printf 'Forward PID state already exists; inspect or stop the PoC first: %s\n' "$FORWARD_PID_FILE" >&2
  exit 2
fi
if [[ -e "$API_KEY_FILE" ]]; then
  printf 'PoC API key state already exists; stop or inspect the PoC first: %s\n' "$API_KEY_FILE" >&2
  exit 2
fi
if [[ -e "$RUN_NONCE_FILE" ]]; then
  printf 'PoC run nonce state already exists; stop or inspect the PoC first: %s\n' "$RUN_NONCE_FILE" >&2
  exit 2
fi

status_output="$($SCRIPT_DIR/status_gateway.sh 2>&1 | sed -r 's/\x1B\[[0-9;]*[mK]//g')"
grep -Fq 'Status: Connected' <<<"$status_output" \
  && grep -Fq 'Version: 0.0.83' <<<"$status_output" \
  || {
    printf '%s\n' 'The isolated OpenShell gateway is not connected at version 0.0.83.' >&2
    exit 2
  }

IMAGE_REF="$($SCRIPT_DIR/build_hermes_poc.sh)"
sandbox_created=0
sandbox_create_attempted=0
forward_pid=""
forward_launcher_pid=""
completed=0
api_key_created=0
run_nonce_created=0

forward_pid_matches() {
  local pid="$1"
  [[ "$pid" =~ ^[0-9]+$ && -r "/proc/$pid/cmdline" ]] || return 1
  [[ "$(readlink -f "/proc/$pid/exe" 2>/dev/null || true)" == "$(readlink -f "$SIQ_OPENSHELL_BIN")" ]] || return 1
  tr '\0' ' ' <"/proc/$pid/cmdline" \
    | grep -Fq "forward service $SANDBOX_NAME --target-port $PORT"
}

find_forward_pids() {
  local process pid
  for process in /proc/[0-9]*; do
    pid="${process##*/}"
    if forward_pid_matches "$pid"; then
      printf '%s\n' "$pid"
    fi
  done
  return 0
}

sandbox_name_exists() {
  siq_openshell_sandbox_name_exists "$SCRIPT_DIR/run_cli.sh" "$SANDBOX_NAME"
}

delete_verified_poc_sandbox() {
  local nonce="$1" ids name_status
  if sandbox_name_exists; then
    name_status=0
  else
    name_status=$?
  fi
  [[ "$name_status" -ne 2 ]] || {
    printf '%s\n' 'Could not verify the fixed PoC sandbox name through the gateway.' >&2
    return 2
  }
  if [[ "$name_status" -eq 1 ]]; then
    ids="$(siq_openshell_managed_sandbox_container_ids "$SANDBOX_NAME" siq-openshell-dev)" || return 2
    [[ -z "$ids" ]] || {
      printf '%s\n' 'The gateway name is absent but a matching managed PoC container remains.' >&2
      return 2
    }
    return 0
  fi
  ids="$(siq_openshell_verified_sandbox_container_id \
    "$SCRIPT_DIR/run_cli.sh" "$SANDBOX_NAME" siq-openshell-dev ai.siq.poc-run "$nonce")" || {
    printf '%s\n' 'The fixed PoC name, gateway nonce and Docker sandbox identity do not match.' >&2
    return 2
  }
  "$SCRIPT_DIR/run_cli.sh" sandbox delete "$SANDBOX_NAME" || return 2
  [[ -z "$(siq_openshell_managed_sandbox_container_ids "$SANDBOX_NAME" siq-openshell-dev)" ]] || {
    printf '%s\n' 'The managed PoC container still exists after sandbox deletion.' >&2
    return 2
  }
}

rollback_on_error() {
  local status=$? cleanup_failed=0 candidates="" nonce=""
  [[ "$completed" -eq 1 ]] && return "$status"
  set +e

  candidates="$(find_forward_pids)"
  if [[ "$candidates" == *$'\n'* ]]; then
    printf '%s\n' 'More than one matching OpenShell forward process remains during rollback.' >&2
    cleanup_failed=1
  elif [[ -n "$candidates" ]]; then
    forward_pid="$candidates"
    if ! siq_openshell_terminate_matching_pid "$forward_pid" forward_pid_matches 'OpenShell forward'; then
      printf '%s\n' "$forward_pid" >"$FORWARD_PID_FILE"
      chmod 0600 -- "$FORWARD_PID_FILE"
      cleanup_failed=1
    fi
  fi
  if [[ "$cleanup_failed" -eq 0 ]]; then
    rm -f -- "$FORWARD_PID_FILE"
  fi

  if [[ "$sandbox_create_attempted" -eq 1 ]]; then
    if [[ -f "$RUN_NONCE_FILE" && ! -L "$RUN_NONCE_FILE" ]]; then
      nonce="$(<"$RUN_NONCE_FILE")"
      [[ "$nonce" =~ ^[0-9a-f]{48}$ ]] \
        && delete_verified_poc_sandbox "$nonce" \
        || cleanup_failed=1
    else
      printf '%s\n' 'Cannot verify the PoC sandbox identity because its run nonce is missing or unsafe.' >&2
      cleanup_failed=1
    fi
  fi

  if [[ "$cleanup_failed" -eq 0 ]]; then
    [[ "$api_key_created" -eq 1 ]] && rm -f -- "$API_KEY_FILE"
    [[ "$run_nonce_created" -eq 1 ]] && rm -f -- "$RUN_NONCE_FILE"
  else
    printf '%s\n' 'PoC rollback was incomplete; retained PID/key/nonce state for a verified retry.' >&2
    status=2
  fi
  trap - EXIT
  exit "$status"
}
trap rollback_on_error EXIT

api_key="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
temporary_key="$(mktemp "$STATE_DIR/.api-key.XXXXXX")"
printf '%s\n' "$api_key" >"$temporary_key"
chmod 0600 -- "$temporary_key"
mv -f -- "$temporary_key" "$API_KEY_FILE"
api_key_created=1

run_nonce="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
temporary_nonce="$(mktemp "$STATE_DIR/.run-nonce.XXXXXX")"
printf '%s\n' "$run_nonce" >"$temporary_nonce"
chmod 0600 -- "$temporary_nonce"
mv -f -- "$temporary_nonce" "$RUN_NONCE_FILE"
run_nonce_created=1

sandbox_create_attempted=1
"$SCRIPT_DIR/run_cli.sh" sandbox create \
  --name "$SANDBOX_NAME" \
  --from "$IMAGE_REF" \
  --cpu 1 \
  --memory 2Gi \
  --policy "$POLICY" \
  --label "ai.siq.poc-run=$run_nonce" \
  --env "HOME=/home/sandbox" \
  --env "HERMES_HOME=/home/sandbox/.hermes" \
  --env "API_SERVER_KEY=$api_key" \
  --env "NO_PROXY=127.0.0.1,localhost,::1" \
  --env "no_proxy=127.0.0.1,localhost,::1" \
  --no-auto-providers \
  --no-tty \
  -- /bin/sh -c \
  'mkdir -p /home/sandbox/.hermes/logs; nohup setsid /opt/siq-poc/entrypoint.sh >/home/sandbox/.hermes/logs/entrypoint.log 2>&1 </dev/null &'
sandbox_created=1

for _ in $(seq 1 120); do
  if "$SCRIPT_DIR/run_cli.sh" sandbox exec --name "$SANDBOX_NAME" --timeout 2 --no-tty -- \
    python -c "import socket; socket.create_connection(('127.0.0.1', $PORT), 1).close()" \
    >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done
"$SCRIPT_DIR/run_cli.sh" sandbox exec --name "$SANDBOX_NAME" --timeout 2 --no-tty -- \
  python -c "import socket; socket.create_connection(('127.0.0.1', $PORT), 1).close()"

(
  siq_openshell_close_maintenance_lock_copy
  exec nohup setsid "$SCRIPT_DIR/run_cli.sh" forward service "$SANDBOX_NAME" \
    --target-port "$PORT" \
    --local "127.0.0.1:$PORT"
) >"$FORWARD_LOG" 2>&1 </dev/null &
forward_launcher_pid=$!

for _ in $(seq 1 50); do
  forward_candidates="$(find_forward_pids)"
  if [[ -n "$forward_candidates" ]]; then
    if [[ "$forward_candidates" == *$'\n'* ]]; then
      printf '%s\n' 'More than one matching OpenShell forward process was found.' >&2
      exit 2
    fi
    forward_pid="$forward_candidates"
    break
  fi
  sleep 0.1
done
if [[ -z "$forward_pid" ]]; then
  printf '%s\n' 'OpenShell forward process did not reach its executable state.' >&2
  exit 2
fi
printf '%s\n' "$forward_pid" >"$FORWARD_PID_FILE"
chmod 0600 "$FORWARD_PID_FILE" "$FORWARD_LOG"

for _ in $(seq 1 80); do
  if ! kill -0 "$forward_pid" 2>/dev/null; then
    printf '%s\n' 'OpenShell forward process exited before becoming ready.' >&2
    exit 2
  fi
  if forward_pid_matches "$forward_pid" && python3 - "$PORT" "$API_KEY_FILE" <<'PY' >/dev/null 2>&1
import json
import sys
import urllib.request
from pathlib import Path

key = Path(sys.argv[2]).read_text(encoding="utf-8").strip()
request = urllib.request.Request(
    f"http://127.0.0.1:{sys.argv[1]}/health",
    headers={"Authorization": f"Bearer {key}"},
)
with urllib.request.urlopen(request, timeout=1) as response:
    assert response.status == 200
    assert json.load(response)["status"] == "ok"
PY
  then
    completed=1
    printf 'Hermes PoC ready: http://127.0.0.1:%s\n' "$PORT"
    exit 0
  fi
  sleep 0.25
done

printf '%s\n' 'Hermes PoC host forward did not become healthy.' >&2
exit 2
