#!/usr/bin/env bash
# Report sanitized NOT_PRODUCTION canary state.

set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec "$SCRIPT_DIR/run_siq_analysis_canary_lifecycle.sh" status "$@"
