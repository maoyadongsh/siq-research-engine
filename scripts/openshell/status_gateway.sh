#!/usr/bin/env bash
# Report isolated gateway state without printing credentials or raw logs.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

GATEWAY_ROOT="$SIQ_OPENSHELL_STATE_ROOT/gateway/siq-openshell-dev"
PID_FILE="$GATEWAY_ROOT/gateway.pid"
RUNTIME_FILE="$GATEWAY_ROOT/gateway.runtime.json"
if [[ -e "$PID_FILE" || -L "$PID_FILE" || -e "$RUNTIME_FILE" || -L "$RUNTIME_FILE" ]]; then
  if runtime_output="$(python3 "$SCRIPT_DIR/gateway_runtime_identity.py" \
    --project-root "$SIQ_PROJECT_ROOT" verify 2>&1)"; then
    printf 'Process: running (%s)\n' "${runtime_output#*: }"
  else
    printf 'Process: unverified runtime evidence\n'
  fi
else
  printf 'Process: stopped\n'
fi

if curl --fail --silent --max-time 1 http://127.0.0.1:17672/healthz >/dev/null; then
  printf 'Health: reachable\n'
else
  printf 'Health: unreachable\n'
fi
"$SCRIPT_DIR/run_cli.sh" status || true
