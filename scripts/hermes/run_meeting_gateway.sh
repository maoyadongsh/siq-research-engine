#!/usr/bin/env bash
set -euo pipefail

selector="${1:-}"
if [[ -z "$selector" ]]; then
    echo "usage: $0 <meeting-model-ref-or-target-id>" >&2
    exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SIQ_PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
DEFAULT_ENV_FILE="$PROJECT_ROOT/infra/env/local.env"
LEGACY_ENV_FILE="$PROJECT_ROOT/env/backend.env"
ENV_FILE="${SIQ_ENV_FILE:-$DEFAULT_ENV_FILE}"

source_env_if_exists() {
    local env_file=$1
    [[ -f "$env_file" ]] || return 1
    set -a
    # shellcheck disable=SC1090
    source "$env_file"
    set +a
}

if ! source_env_if_exists "$ENV_FILE" && [[ -z "${SIQ_ENV_FILE:-}" ]]; then
    source_env_if_exists "$LEGACY_ENV_FILE" || true
fi

export SIQ_PROJECT_ROOT="$PROJECT_ROOT"
export SIQ_RUNTIME_ROOT="${SIQ_RUNTIME_ROOT:-$PROJECT_ROOT/var}"
export SIQ_MEETINGS_HERMES_TARGETS_FILE="${SIQ_MEETINGS_HERMES_TARGETS_FILE:-$SIQ_RUNTIME_ROOT/meetings/hermes-targets.json}"

exec python3 "$SCRIPT_DIR/meeting_targets.py" launch \
    "$selector" \
    --targets-file "$SIQ_MEETINGS_HERMES_TARGETS_FILE"
