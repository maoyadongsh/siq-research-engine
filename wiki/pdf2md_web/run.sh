#!/bin/bash
# Start the PDF to Markdown Flask application

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PATH="/home/maoyd/.venvs/mineru_native"

if [ ! -d "$VENV_PATH" ]; then
    echo "ERROR: Virtual environment not found at $VENV_PATH"
    exit 1
fi

cd "$SCRIPT_DIR"

# Activate virtual environment
source "$VENV_PATH/bin/activate"

# Set defaults
export FLASK_APP="${FLASK_APP:-app.py}"
export PORT="${PORT:-5000}"
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
