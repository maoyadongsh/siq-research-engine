#!/usr/bin/env bash
# Report sanitized SIQ OpenShell host broker state without reading raw logs.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
export PYTHONDONTWRITEBYTECODE=1
exec python3 "$SCRIPT_DIR/broker_lifecycle.py" status "$@"
