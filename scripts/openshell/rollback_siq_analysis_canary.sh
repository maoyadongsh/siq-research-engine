#!/usr/bin/env bash
# Remove canary execution resources and retain the healthy host runtime.

set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec "$SCRIPT_DIR/run_siq_analysis_canary_lifecycle.sh" rollback "$@"
