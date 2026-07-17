#!/usr/bin/env bash
# Rotate the private broker request-identity key only while brokers and formal runs are stopped.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
export PYTHONDONTWRITEBYTECODE=1
exec python3 "$SCRIPT_DIR/broker_lifecycle.py" rotate-identity-key "$@"
