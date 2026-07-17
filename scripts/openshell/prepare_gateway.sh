#!/usr/bin/env bash
# Prepare isolated gateway state without starting a process.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"
siq_openshell_acquire_maintenance_lock

VERSION="0.0.83"
GATEWAY_NAME="siq-openshell-dev"
BIN_ROOT="$SIQ_OPENSHELL_STATE_ROOT/toolchains/v$VERSION/bin"
GATEWAY_ROOT="$SIQ_OPENSHELL_STATE_ROOT/gateway/$GATEWAY_NAME"
TLS_ROOT="$GATEWAY_ROOT/tls"
CLIENT_TLS_ROOT="$OPENSHELL_LOCAL_TLS_DIR"
ACTIVATION_RECORD="$GATEWAY_ROOT/bind-contract.activation.json"
BIND_TRANSACTION="$GATEWAY_ROOT/bind-contract.transaction"
START_INTENT="$GATEWAY_ROOT/gateway.start.intent.json"
STARTING_RECORD="$GATEWAY_ROOT/gateway.starting.json"

allow_owned_bind_transaction() {
  local requested_id="${SIQ_OPENSHELL_BIND_TRANSACTION_ID:-}" active_id count lock_fd lock_target expected_lock
  [[ -f "$BIND_TRANSACTION" && ! -L "$BIND_TRANSACTION" ]] || return 1
  [[ "$(stat -c '%u' -- "$BIND_TRANSACTION")" == "$(id -u)" ]] || return 1
  (( (8#$(stat -c '%a' -- "$BIND_TRANSACTION") & 8#077) == 0 )) || return 1
  [[ "$requested_id" =~ ^[0-9a-f]{32}$ ]] || return 1
  count="$(awk -F= '$1 == "transaction_id" {count++} END {print count + 0}' "$BIND_TRANSACTION")"
  active_id="$(awk -F= '$1 == "transaction_id" {print $2}' "$BIND_TRANSACTION")"
  [[ "$count" == 1 && "$active_id" == "$requested_id" ]] || return 1

  lock_fd="${SIQ_OPENSHELL_MAINTENANCE_FD:-}"
  [[ "$lock_fd" =~ ^[0-9]+$ && -e "/proc/$$/fd/$lock_fd" ]] || return 1
  lock_target="$(readlink -f "/proc/$$/fd/$lock_fd" 2>/dev/null || true)"
  expected_lock="$(realpath -m -- "$SIQ_OPENSHELL_STATE_ROOT/locks/maintenance.lock")"
  [[ "$lock_target" == "$expected_lock" ]]
}

for component in openshell openshell-gateway openshell-sandbox; do
  binary="$BIN_ROOT/$component"
  if [[ ! -x "$binary" ]] || [[ "$($binary --version)" != "$component $VERSION" ]]; then
    printf 'Missing or mismatched project toolchain component: %s\n' "$binary" >&2
    exit 2
  fi
done

install -d -m 700 \
  "$GATEWAY_ROOT" \
  "$TLS_ROOT" \
  "$CLIENT_TLS_ROOT" \
  "$CLIENT_TLS_ROOT/client" \
  "$CLIENT_TLS_ROOT/jwt" \
  "$CLIENT_TLS_ROOT/server"
if [[ ! -f "$TLS_ROOT/server/tls.crt" ]]; then
  "$BIN_ROOT/openshell-gateway" generate-certs \
    --output-dir "$TLS_ROOT" \
    --server-san 127.0.0.1 \
    --server-san host.openshell.internal
fi

required_tls=(
  ca.crt
  client/tls.crt
  client/tls.key
  jwt/kid
  jwt/public.pem
  jwt/signing.pem
  server/tls.crt
  server/tls.key
)
for relative in "${required_tls[@]}"; do
  path="$TLS_ROOT/$relative"
  if [[ ! -f "$path" || -L "$path" ]]; then
    printf 'Gateway TLS material is missing or unsafe: %s\n' "$path" >&2
    exit 2
  fi
  chmod 600 "$path"
done

for relative in "${required_tls[@]}"; do
  install -m 600 "$TLS_ROOT/$relative" "$CLIENT_TLS_ROOT/$relative"
done

if [[ -e "$BIND_TRANSACTION" || -L "$BIND_TRANSACTION" ]]; then
  if ! allow_owned_bind_transaction; then
    printf 'Incomplete or foreign bind-contract transaction requires recovery: %s\n' \
      "$BIND_TRANSACTION" >&2
    exit 75
  fi
fi

if [[ -e "$START_INTENT" || -L "$START_INTENT" \
  || -e "$STARTING_RECORD" || -L "$STARTING_RECORD" ]]; then
  printf 'Interrupted gateway start requires verified recovery before preparation.\n' >&2
  exit 75
fi

if [[ -e "$ACTIVATION_RECORD" || -L "$ACTIVATION_RECORD" ]]; then
  python3 "$SCRIPT_DIR/gateway_bind_contract.py" \
    --project-root "$SIQ_PROJECT_ROOT" \
    verify-activation \
    --activation-record "$ACTIVATION_RECORD"
fi

python3 "$SCRIPT_DIR/render_gateway_config.py" --project-root "$SIQ_PROJECT_ROOT"
python3 "$SCRIPT_DIR/render_gateway_config.py" --project-root "$SIQ_PROJECT_ROOT" --check

printf 'Prepared OpenShell gateway %s at %s\n' "$GATEWAY_NAME" "$GATEWAY_ROOT"
