#!/usr/bin/env bash
# Verify the live canary mount, write, and immutable boundaries.

set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec "$SCRIPT_DIR/run_siq_analysis_canary_lifecycle.sh" probe "$@"
