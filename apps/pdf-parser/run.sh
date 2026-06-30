#!/bin/bash
# Start the PDF to Markdown Flask application

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

source_env_if_exists() {
    local env_file=$1
    if [[ ! -f "$env_file" ]]; then
        return 1
    fi
    set -a
    # shellcheck disable=SC1090
    source "$env_file"
    set +a
}

DEFAULT_ENV_FILE="$PROJECT_ROOT/infra/env/local.env"
LEGACY_ENV_FILE="$PROJECT_ROOT/env/backend.env"
ENV_FILE="${SIQ_ENV_FILE:-$DEFAULT_ENV_FILE}"
if ! source_env_if_exists "$ENV_FILE" && [[ -z "${SIQ_ENV_FILE:-}" ]]; then
    source_env_if_exists "$LEGACY_ENV_FILE" || true
fi

VENV_PATH="${SIQ_MINERU_VENV:-${MINERU_VENV:-$PROJECT_ROOT/runtimes/mineru-native}}"

if [ ! -d "$VENV_PATH" ]; then
    echo "ERROR: Virtual environment not found at $VENV_PATH"
    exit 1
fi

cd "$SCRIPT_DIR"

# Activate virtual environment
source "$VENV_PATH/bin/activate"

# Set defaults
export SIQ_PROJECT_ROOT="${SIQ_PROJECT_ROOT:-$PROJECT_ROOT}"
export SIQ_DATA_ROOT="${SIQ_DATA_ROOT:-$PROJECT_ROOT/data}"
export SIQ_RUNTIME_ROOT="${SIQ_RUNTIME_ROOT:-$PROJECT_ROOT/var}"
export SIQ_ARTIFACTS_ROOT="${SIQ_ARTIFACTS_ROOT:-$PROJECT_ROOT/artifacts}"
export SIQ_DATASETS_ROOT="${SIQ_DATASETS_ROOT:-$PROJECT_ROOT/datasets}"
export SIQ_PDF2MD_ROOT="${SIQ_PDF2MD_ROOT:-$SCRIPT_DIR}"
export SIQ_PDF2MD_DATA_DIR="${SIQ_PDF2MD_DATA_DIR:-$SIQ_DATA_ROOT/pdf-parser}"
export FLASK_APP="${FLASK_APP:-app.py}"
export PORT="${PORT:-15000}"
export HOST="${HOST:-127.0.0.1}"
export MINERU_API_URL="${MINERU_API_URL:-http://127.0.0.1:8003}"
export VLM_API_URL="${VLM_API_URL:-http://127.0.0.1:8002}"
export TASK_RETENTION_HOURS="${TASK_RETENTION_HOURS:-0}"
export CLEANUP_OUTPUT_FOLDER="${CLEANUP_OUTPUT_FOLDER:-0}"

echo "======================================"
echo "  PDF to Markdown Web App"
echo "======================================"
echo "  Flask bind:   $HOST:$PORT"
echo "  MinerU API:   $MINERU_API_URL"
echo "  VLM API:      $VLM_API_URL"
echo "======================================"
echo "  Open http://localhost:$PORT in your browser"
echo "======================================"
echo ""

exec python "$SCRIPT_DIR/app.py"
