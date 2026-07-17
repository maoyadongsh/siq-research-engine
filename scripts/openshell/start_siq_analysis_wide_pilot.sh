#!/usr/bin/env bash
# Start one explicitly acknowledged NOT_PRODUCTION wide pilot.

set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec "$SCRIPT_DIR/run_siq_analysis_wide_pilot_lifecycle.sh" start "$@"
