#!/usr/bin/env bash
# NOT_PRODUCTION: start an isolated siq_analysis/OpenShell feasibility sandbox.

set -euo pipefail
umask 077

if [[ "${1:-}" != "--acknowledge-not-production" || "$#" -ne 1 ]]; then
  printf '%s\n' 'Usage: start_siq_analysis_observe_poc.sh --acknowledge-not-production' >&2
  exit 2
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"
# shellcheck source=process_helpers.sh
source "$SCRIPT_DIR/process_helpers.sh"
siq_openshell_acquire_maintenance_lock

readonly SANDBOX_NAME="siq-analysis-observe-poc"
readonly PORT="28651"
readonly PROVIDER="siq-minimax-cn-pool"
readonly POLICY="$ROOT_DIR/infra/openshell/poc/siq-analysis-observe/policy.yaml"
readonly STATE_DIR="$SIQ_OPENSHELL_STATE_ROOT/poc/siq-analysis-observe"
readonly FORWARD_PID_FILE="$STATE_DIR/forward.pid"
readonly FORWARD_LOG="$STATE_DIR/forward.log"
readonly API_KEY_FILE="$STATE_DIR/api.key"
readonly RUN_NONCE_FILE="$STATE_DIR/run.nonce"
readonly SUPERVISOR_BIN="$SIQ_OPENSHELL_STATE_ROOT/toolchains/v0.0.83/bin/openshell-sandbox"
readonly SUPERVISOR_RECORD="$SIQ_OPENSHELL_STATE_ROOT/build/v0.0.83/supervisor-patch.runtime"
readonly SUPERVISOR_PATCH_SHA256="f38cdb0788a9c1f2a38c9aa23ab36b33c4cc6faea135bf6f04bf5eb7bbcdd12f"

siq_openshell_assert_state_path "$STATE_DIR"
install -d -m 0700 -- "$STATE_DIR"
[[ -f "$POLICY" && ! -L "$POLICY" ]] || {
  printf 'Observe policy is missing or unsafe: %s\n' "$POLICY" >&2
  exit 2
}

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

status_output="$($SCRIPT_DIR/status_gateway.sh 2>&1 | sed -r 's/\x1B\[[0-9;]*[mK]//g')"
grep -Fq 'Status: Connected' <<<"$status_output" \
  && grep -Fq 'Version: 0.0.83' <<<"$status_output" \
  || {
    printf '%s\n' 'The isolated OpenShell gateway is not connected at version 0.0.83.' >&2
    exit 2
  }

provider_names="$($SCRIPT_DIR/run_cli.sh provider list --names)" || {
  printf '%s\n' 'Could not read the isolated gateway provider inventory.' >&2
  exit 2
}
grep -Fxq "$PROVIDER" <<<"$provider_names" || {
  printf 'Required observe provider is not configured: %s\n' "$PROVIDER" >&2
  exit 2
}

if ss -ltnH "sport = :$PORT" | grep -q .; then
  printf 'Host port %s is already in use; host/formal traffic was not disturbed.\n' "$PORT" >&2
  exit 2
fi
sandbox_name_status=0
if siq_openshell_sandbox_name_exists "$SCRIPT_DIR/run_cli.sh" "$SANDBOX_NAME"; then
  sandbox_name_status=0
else
  sandbox_name_status=$?
fi
[[ "$sandbox_name_status" -ne 2 ]] || {
  printf '%s\n' 'Could not verify the fixed observe sandbox name through the gateway.' >&2
  exit 2
}
if [[ "$sandbox_name_status" -eq 0 ]]; then
  printf 'Observe sandbox already exists: %s\n' "$SANDBOX_NAME" >&2
  exit 2
fi
for state_file in "$FORWARD_PID_FILE" "$API_KEY_FILE" "$RUN_NONCE_FILE"; do
  [[ ! -e "$state_file" ]] || {
    printf 'Observe state already exists; inspect or stop the PoC first: %s\n' "$state_file" >&2
    exit 2
  }
done

IMAGE_REF="$($SCRIPT_DIR/build_siq_analysis_image.sh)"
[[ "$IMAGE_REF" =~ ^siq/hermes-openshell-siq-analysis:[0-9a-f]{24}$ ]] || {
  printf '%s\n' 'The SIQ candidate image reference is invalid.' >&2
  exit 2
}

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
    forward_pid_matches "$pid" && printf '%s\n' "$pid"
  done
  return 0
}

sandbox_name_exists() {
  siq_openshell_sandbox_name_exists "$SCRIPT_DIR/run_cli.sh" "$SANDBOX_NAME"
}

delete_verified_observe_sandbox() {
  local nonce="$1" name_status ids
  if sandbox_name_exists; then
    name_status=0
  else
    name_status=$?
  fi
  [[ "$name_status" -ne 2 ]] || return 2
  if [[ "$name_status" -eq 1 ]]; then
    ids="$(siq_openshell_managed_sandbox_container_ids "$SANDBOX_NAME" siq-openshell-dev)" || return 2
    [[ -z "$ids" ]] || return 2
    return 0
  fi
  siq_openshell_verified_sandbox_container_id \
    "$SCRIPT_DIR/run_cli.sh" "$SANDBOX_NAME" siq-openshell-dev ai.siq.observe-run "$nonce" \
    >/dev/null || return 2
  "$SCRIPT_DIR/run_cli.sh" sandbox delete "$SANDBOX_NAME" || return 2
  [[ -z "$(siq_openshell_managed_sandbox_container_ids "$SANDBOX_NAME" siq-openshell-dev)" ]]
}

sandbox_create_attempted=0
completed=0
api_key_created=0
run_nonce_created=0
forward_pid=""

rollback_on_error() {
  local status=$? cleanup_failed=0 candidates="" nonce=""
  [[ "$completed" -eq 1 ]] && return "$status"
  set +e
  candidates="$(find_forward_pids)"
  if [[ "$candidates" == *$'\n'* ]]; then
    cleanup_failed=1
  elif [[ -n "$candidates" ]]; then
    forward_pid="$candidates"
    siq_openshell_terminate_matching_pid "$forward_pid" forward_pid_matches 'observe OpenShell forward' \
      || cleanup_failed=1
  fi
  [[ "$cleanup_failed" -ne 0 ]] || rm -f -- "$FORWARD_PID_FILE"

  if [[ "$sandbox_create_attempted" -eq 1 ]]; then
    if [[ -f "$RUN_NONCE_FILE" && ! -L "$RUN_NONCE_FILE" ]]; then
      nonce="$(<"$RUN_NONCE_FILE")"
      [[ "$nonce" =~ ^[0-9a-f]{48}$ ]] \
        && delete_verified_observe_sandbox "$nonce" \
        || cleanup_failed=1
    else
      cleanup_failed=1
    fi
  fi
  if [[ "$cleanup_failed" -eq 0 ]]; then
    [[ "$api_key_created" -eq 0 ]] || rm -f -- "$API_KEY_FILE"
    [[ "$run_nonce_created" -eq 0 ]] || rm -f -- "$RUN_NONCE_FILE"
  else
    printf '%s\n' 'Observe rollback was incomplete; retained identity state for a verified stop.' >&2
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
mv -fT -- "$temporary_key" "$API_KEY_FILE"
api_key_created=1

run_nonce="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
temporary_nonce="$(mktemp "$STATE_DIR/.run-nonce.XXXXXX")"
printf '%s\n' "$run_nonce" >"$temporary_nonce"
chmod 0600 -- "$temporary_nonce"
mv -fT -- "$temporary_nonce" "$RUN_NONCE_FILE"
run_nonce_created=1

sandbox_create_attempted=1
"$SCRIPT_DIR/run_cli.sh" sandbox create \
  --name "$SANDBOX_NAME" \
  --from "$IMAGE_REF" \
  --cpu 2 \
  --memory 4Gi \
  --policy "$POLICY" \
  --label "ai.siq.observe-run=$run_nonce" \
  --label "ai.siq.profile=siq_analysis" \
  --label "ai.siq.lifecycle=observe-only-not-production-v1" \
  --env "HOME=/home/sandbox" \
  --env "SIQ_PROJECT_ROOT=/home/maoyd/siq-research-engine" \
  --env "HERMES_HOME=/sandbox/siq-analysis-observe/hermes-home" \
  --env "HERMES_AUTH_FILE=/sandbox/siq-analysis-observe/runtime-auth/auth.json" \
  --env "API_SERVER_ENABLED=true" \
  --env "API_SERVER_HOST=127.0.0.1" \
  --env "API_SERVER_PORT=$PORT" \
  --env "API_SERVER_MODEL_NAME=siq_analysis_observe" \
  --env "API_SERVER_KEY=$api_key" \
  --env "SIQ_OBSERVE_ONLY=1" \
  --env "SIQ_OPENSHELL_SANDBOX=1" \
  --env "NO_PROXY=127.0.0.1,localhost,::1" \
  --env "no_proxy=127.0.0.1,localhost,::1" \
  --provider "$PROVIDER" \
  --no-auto-providers \
  --no-tty \
  -- /bin/sh -c \
  'nohup setsid /opt/siq/observe-entrypoint.sh >/tmp/siq-observe-entrypoint.log 2>&1 </dev/null &'

for _ in $(seq 1 160); do
  if "$SCRIPT_DIR/run_cli.sh" sandbox exec --name "$SANDBOX_NAME" --timeout 2 --no-tty -- \
    /opt/siq/hermes/venv/bin/python -c \
    "import socket; socket.create_connection(('127.0.0.1', $PORT), 1).close()" \
    >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done
"$SCRIPT_DIR/run_cli.sh" sandbox exec --name "$SANDBOX_NAME" --timeout 2 --no-tty -- \
  /opt/siq/hermes/venv/bin/python -c \
  "import socket; socket.create_connection(('127.0.0.1', $PORT), 1).close()"

(
  siq_openshell_close_maintenance_lock_copy
  exec nohup setsid "$SCRIPT_DIR/run_cli.sh" forward service "$SANDBOX_NAME" \
    --target-port "$PORT" \
    --local "127.0.0.1:$PORT"
) >"$FORWARD_LOG" 2>&1 </dev/null &

for _ in $(seq 1 50); do
  forward_candidates="$(find_forward_pids)"
  if [[ -n "$forward_candidates" ]]; then
    [[ "$forward_candidates" != *$'\n'* ]] || {
      printf '%s\n' 'More than one matching observe forward was found.' >&2
      exit 2
    }
    forward_pid="$forward_candidates"
    break
  fi
  sleep 0.1
done
[[ -n "$forward_pid" ]] || {
  printf '%s\n' 'Observe forward did not reach its executable state.' >&2
  exit 2
}
printf '%s\n' "$forward_pid" >"$FORWARD_PID_FILE"
chmod 0600 -- "$FORWARD_PID_FILE" "$FORWARD_LOG"

for _ in $(seq 1 120); do
  kill -0 "$forward_pid" 2>/dev/null || {
    printf '%s\n' 'Observe forward exited before becoming healthy.' >&2
    exit 2
  }
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
    printf 'NOT_PRODUCTION observe endpoint ready: http://127.0.0.1:%s\n' "$PORT"
    exit 0
  fi
  sleep 0.25
done

printf '%s\n' 'Observe endpoint did not become healthy.' >&2
exit 2
