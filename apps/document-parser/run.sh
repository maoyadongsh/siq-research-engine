#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

export SIQ_PROJECT_ROOT="${SIQ_PROJECT_ROOT:-$PROJECT_ROOT}"
export SIQ_DOCUMENT_PARSE_DATA_DIR="${SIQ_DOCUMENT_PARSE_DATA_DIR:-$PROJECT_ROOT/data/document-parser}"
export FLASK_APP="${FLASK_APP:-app.py}"
export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-15010}"

cd "$SCRIPT_DIR"

echo "======================================"
echo "  SIQ Generic Document Parser"
echo "======================================"
echo "  Flask bind: $HOST:$PORT"
echo "  Data dir:   $SIQ_DOCUMENT_PARSE_DATA_DIR"
echo "======================================"
echo ""

if command -v uv >/dev/null 2>&1; then
  exec uv run --with-requirements requirements.txt python app.py
fi

exec python app.py
