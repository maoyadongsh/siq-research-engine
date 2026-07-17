#!/usr/bin/env bash
# Stop only PID/cmdline/listener-verified SIQ OpenShell host brokers.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
export PYTHONDONTWRITEBYTECODE=1
exec python3 "$SCRIPT_DIR/broker_lifecycle.py" stop "$@"
