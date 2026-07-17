#!/usr/bin/env bash
# Start only the fixed SIQ OpenShell host brokers; credentials remain inherited environment state.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
export PYTHONDONTWRITEBYTECODE=1
exec python3 "$SCRIPT_DIR/broker_lifecycle.py" start "$@"
