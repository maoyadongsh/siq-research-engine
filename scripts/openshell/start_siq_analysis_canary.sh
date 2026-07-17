#!/usr/bin/env bash
# Start one explicitly acknowledged NOT_PRODUCTION OpenShell canary.

set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec "$SCRIPT_DIR/run_siq_analysis_canary_lifecycle.sh" start "$@"
