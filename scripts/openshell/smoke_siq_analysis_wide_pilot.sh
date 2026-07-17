#!/usr/bin/env bash
# Exercise the real-path pilot and leave its one output directory removed.

set -euo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PILOT_ID=""
MARKET=""
COMPANY=""
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --pilot-id) PILOT_ID="${2:-}"; shift 2 ;;
    --market) MARKET="${2:-}"; shift 2 ;;
    --company) COMPANY="${2:-}"; shift 2 ;;
    *) printf 'Unknown wide-pilot smoke argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
[[ "$PILOT_ID" =~ ^pilot-[0-9a-f]{12}$ && -n "$MARKET" && -n "$COMPANY" ]] || {
  printf '%s\n' 'Usage: smoke_siq_analysis_wide_pilot.sh --pilot-id pilot-<12hex> --market <market> --company <company>' >&2
  exit 2
}

"$SCRIPT_DIR/run_siq_analysis_wide_pilot_lifecycle.sh" status --pilot-id "$PILOT_ID" >/dev/null
"$SCRIPT_DIR/run_siq_analysis_wide_pilot_lifecycle.sh" probe --pilot-id "$PILOT_ID"
python3 "$SCRIPT_DIR/test_siq_analysis_wide_pilot_contract.py" \
  --base-url http://127.0.0.1:28651 \
  --api-key-file "$SCRIPT_DIR/../../var/openshell/poc/siq-analysis-wide/runs/$PILOT_ID/api.key" \
  --market "$MARKET" \
  --company "$COMPANY" \
  --pilot-id "$PILOT_ID"
"$SCRIPT_DIR/run_siq_analysis_wide_pilot_lifecycle.sh" status --pilot-id "$PILOT_ID" >/dev/null
printf '%s\n' 'NOT_PRODUCTION siq_analysis wide business pilot: PASS'
