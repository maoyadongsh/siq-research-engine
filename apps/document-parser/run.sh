#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

ENV_FILE="${SIQ_ENV_FILE:-$PROJECT_ROOT/env/backend.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

export SIQ_PROJECT_ROOT="${SIQ_PROJECT_ROOT:-$PROJECT_ROOT}"
export SIQ_DOCUMENT_PARSE_DATA_DIR="${SIQ_DOCUMENT_PARSE_DATA_DIR:-$PROJECT_ROOT/data/document-parser}"
export SIQ_PDF2MD_API_BASE="${SIQ_PDF2MD_API_BASE:-http://127.0.0.1:15000}"
export SIQ_PDF2MD_DATA_DIR="${SIQ_PDF2MD_DATA_DIR:-$PROJECT_ROOT/data/pdf-parser}"
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
