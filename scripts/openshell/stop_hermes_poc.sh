#!/usr/bin/env bash
# Stop only the explicitly named Hermes PoC forward and sandbox.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"
# shellcheck source=process_helpers.sh
source "$SCRIPT_DIR/process_helpers.sh"
siq_openshell_acquire_maintenance_lock

readonly SANDBOX_NAME="siq-hermes-minimal-poc"
readonly PORT="28642"
readonly STATE_DIR="$SIQ_OPENSHELL_STATE_ROOT/poc/hermes-minimal"
readonly FORWARD_PID_FILE="$STATE_DIR/forward.pid"
readonly API_KEY_FILE="$STATE_DIR/api.key"
readonly RUN_NONCE_FILE="$STATE_DIR/run.nonce"

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

forward_pid=""
if [[ -e "$FORWARD_PID_FILE" ]]; then
  [[ -f "$FORWARD_PID_FILE" && ! -L "$FORWARD_PID_FILE" ]] || {
    printf 'Forward PID state is unsafe: %s\n' "$FORWARD_PID_FILE" >&2
    exit 2
  }
  recorded_forward_pid="$(<"$FORWARD_PID_FILE")"
  if forward_pid_matches "$recorded_forward_pid"; then
    forward_pid="$recorded_forward_pid"
  elif [[ "$recorded_forward_pid" =~ ^[0-9]+$ ]] && kill -0 "$recorded_forward_pid" 2>/dev/null; then
    printf 'Refusing to signal unrelated PID from %s\n' "$FORWARD_PID_FILE" >&2
    exit 2
  fi
fi

forward_candidates="$(find_forward_pids)"
if [[ "$forward_candidates" == *$'\n'* ]]; then
  printf '%s\n' 'More than one matching OpenShell forward process was found; refusing cleanup.' >&2
  exit 2
elif [[ -n "$forward_candidates" ]]; then
  if [[ -n "$forward_pid" && "$forward_pid" != "$forward_candidates" ]]; then
    printf '%s\n' 'Forward PID state conflicts with the uniquely discovered OpenShell forward.' >&2
    exit 2
  fi
  forward_pid="$forward_candidates"
fi
if [[ -n "$forward_pid" ]]; then
  siq_openshell_terminate_matching_pid "$forward_pid" forward_pid_matches 'OpenShell forward'
fi
rm -f -- "$FORWARD_PID_FILE"

sandbox_name_status=0
if sandbox_name_exists; then
  sandbox_name_status=0
else
  sandbox_name_status=$?
fi
if [[ "$sandbox_name_status" -eq 2 ]]; then
  printf '%s\n' 'Could not verify the fixed PoC sandbox name through the gateway.' >&2
  exit 2
fi
if [[ "$sandbox_name_status" -eq 0 ]]; then
  [[ -f "$RUN_NONCE_FILE" && ! -L "$RUN_NONCE_FILE" ]] || {
    printf '%s\n' 'Refusing to delete the PoC sandbox without a trustworthy run nonce.' >&2
    exit 2
  }
  run_nonce="$(<"$RUN_NONCE_FILE")"
  [[ "$run_nonce" =~ ^[0-9a-f]{48}$ ]] || {
    printf '%s\n' 'Refusing to delete the PoC sandbox with an invalid run nonce.' >&2
    exit 2
  }
  container_id="$(siq_openshell_verified_sandbox_container_id \
    "$SCRIPT_DIR/run_cli.sh" "$SANDBOX_NAME" siq-openshell-dev ai.siq.poc-run "$run_nonce")" || {
    printf '%s\n' 'PoC sandbox name, namespace, ID and run nonce did not resolve to one identity.' >&2
    exit 2
  }
  "$SCRIPT_DIR/run_cli.sh" sandbox delete "$SANDBOX_NAME"
  [[ -z "$(siq_openshell_managed_sandbox_container_ids "$SANDBOX_NAME" siq-openshell-dev)" ]] || {
    printf '%s\n' 'The managed PoC container still exists after sandbox deletion.' >&2
    exit 2
  }
fi

if ss -ltnH "sport = :$PORT" | grep -q .; then
  printf 'Port %s is still in use after PoC stop.\n' "$PORT" >&2
  exit 2
fi
if [[ -L "$API_KEY_FILE" ]]; then
  printf 'Refusing to remove symlinked PoC API key state: %s\n' "$API_KEY_FILE" >&2
  exit 2
fi
rm -f -- "$API_KEY_FILE"
if [[ -L "$RUN_NONCE_FILE" ]]; then
  printf 'Refusing to remove symlinked PoC run nonce state: %s\n' "$RUN_NONCE_FILE" >&2
  exit 2
fi
rm -f -- "$RUN_NONCE_FILE"
printf '%s\n' 'Hermes PoC stopped; isolated OpenShell gateway was left running.'
