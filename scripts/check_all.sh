#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

run_step() {
    local label=$1
    shift
    printf '\n==> %s\n' "$label"
    "$@"
}

run_step "API tests" bash -lc "cd '$ROOT_DIR/apps/api' && uv sync --frozen --extra dev && uv run --frozen python -m pytest tests"
run_step "PDF parser tests" bash -lc "cd '$ROOT_DIR/apps/pdf-parser' && python3 -m pytest tests"
run_step "Document parser tests" bash -lc "cd '$ROOT_DIR/apps/document-parser' && python3 -m pytest tests"
run_step "Market report finder tests" bash -lc "cd '$ROOT_DIR/services/market-report-finder' && uv sync --frozen --extra dev && uv run --frozen python -m pytest tests"
run_step "Market report rules tests" bash -lc "cd '$ROOT_DIR/services/market-report-rules' && uv sync --frozen --extra dev && uv run --frozen pytest"
run_step "Market contracts tests" bash -lc "cd '$ROOT_DIR/packages/market-contracts' && uv sync --frozen --extra dev && uv run --frozen python -m pytest tests"
run_step "Large file observe report" python3 "$ROOT_DIR/scripts/maintenance/observe_large_files.py" --root "$ROOT_DIR" --limit 20
run_step "Web dependency install" bash -lc "cd '$ROOT_DIR/apps/web' && npm ci"
run_step "Web unit tests" bash -lc "cd '$ROOT_DIR/apps/web' && npm run test:unit"
run_step "Web frontend check" bash -lc "cd '$ROOT_DIR/apps/web' && npm run check:frontend"
