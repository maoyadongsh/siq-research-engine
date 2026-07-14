#!/usr/bin/env bash
set -euo pipefail

umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SIQ_PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../../.." && pwd)}"
DEFAULT_ENV_FILE="$PROJECT_ROOT/infra/env/local.env"
ENV_FILE="${SIQ_ENV_FILE:-$DEFAULT_ENV_FILE}"

if [[ -n "${SIQ_ENV_FILE:-}" && ! -f "$ENV_FILE" ]]; then
    echo "meeting voiceprint worker environment file does not exist" >&2
    exit 2
fi

if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

export SIQ_PROJECT_ROOT="$PROJECT_ROOT"
export SIQ_BACKEND_ROOT="${SIQ_BACKEND_ROOT:-$PROJECT_ROOT/apps/api}"

cd "$SIQ_BACKEND_ROOT"
exec uv run --frozen python scripts/run_meeting_voiceprint_worker.py "$@"
