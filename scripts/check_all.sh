#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

run_step() {
    local label=$1
    shift
    printf '\n==> %s\n' "$label"
    "$@"
}

run_step "API tests" bash -lc "cd '$ROOT_DIR/apps/api' && uv sync --extra dev && uv run python -m pytest tests"
run_step "PDF parser tests" bash -lc "cd '$ROOT_DIR/apps/pdf-parser' && python3 -m pytest tests"
run_step "Document parser tests" bash -lc "cd '$ROOT_DIR/apps/document-parser' && python3 -m pytest tests"
run_step "Market report finder tests" bash -lc "cd '$ROOT_DIR/services/market-report-finder' && uv sync --extra dev && uv run python -m pytest tests"
run_step "Market report rules tests" bash -lc "cd '$ROOT_DIR/services/market-report-rules' && uv run --extra dev pytest"
run_step "Market contracts tests" bash -lc "cd '$ROOT_DIR/packages/market-contracts' && uv run python -m pytest tests"
run_step "Web unit tests" bash -lc "cd '$ROOT_DIR/apps/web' && npm run test:unit"
run_step "Web frontend check" bash -lc "cd '$ROOT_DIR/apps/web' && npm run check:frontend"
