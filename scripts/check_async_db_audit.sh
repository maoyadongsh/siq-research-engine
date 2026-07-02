#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_PY="${API_PY:-$ROOT_DIR/apps/api/.venv/bin/python}"
AUDIT_SCRIPT="$ROOT_DIR/apps/api/scripts/audit_async_sync_session.py"

if [[ ! -f "$AUDIT_SCRIPT" ]]; then
    printf 'Missing async DB audit script: %s\n' "$AUDIT_SCRIPT" >&2
    exit 1
fi

if [[ "$API_PY" == */* ]]; then
    if [[ ! -x "$API_PY" ]]; then
        printf 'Missing API Python interpreter: %s\n' "$API_PY" >&2
        printf 'Create apps/api/.venv or set API_PY to the Python executable to use.\n' >&2
        exit 1
    fi
elif ! command -v "$API_PY" >/dev/null 2>&1; then
    printf 'Missing API Python interpreter on PATH: %s\n' "$API_PY" >&2
    printf 'Create apps/api/.venv or set API_PY to the Python executable to use.\n' >&2
    exit 1
fi

printf '==> Async DB audit advisory summary\n'
"$API_PY" "$AUDIT_SCRIPT" --summary

printf '\nReport formats, when explicitly needed (redirect output yourself):\n'
printf '  %q %q --markdown --summary\n' "$API_PY" "$AUDIT_SCRIPT"
printf '  %q %q --json --summary\n' "$API_PY" "$AUDIT_SCRIPT"
