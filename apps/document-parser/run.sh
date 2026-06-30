#!/usr/bin/env bash
set -euo pipefail

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

export SIQ_PROJECT_ROOT="${SIQ_PROJECT_ROOT:-$PROJECT_ROOT}"
export SIQ_DATA_ROOT="${SIQ_DATA_ROOT:-$PROJECT_ROOT/data}"
export SIQ_RUNTIME_ROOT="${SIQ_RUNTIME_ROOT:-$PROJECT_ROOT/var}"
export SIQ_ARTIFACTS_ROOT="${SIQ_ARTIFACTS_ROOT:-$PROJECT_ROOT/artifacts}"
export SIQ_DATASETS_ROOT="${SIQ_DATASETS_ROOT:-$PROJECT_ROOT/datasets}"
export SIQ_DOCUMENT_PARSE_DATA_DIR="${SIQ_DOCUMENT_PARSE_DATA_DIR:-$SIQ_DATA_ROOT/document-parser}"
export SIQ_PDF2MD_API_BASE="${SIQ_PDF2MD_API_BASE:-http://127.0.0.1:15000}"
export SIQ_PDF2MD_DATA_DIR="${SIQ_PDF2MD_DATA_DIR:-$SIQ_DATA_ROOT/pdf-parser}"
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
