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
run_step "Market report finder tests" bash -lc "cd '$ROOT_DIR/services/market-report-finder' && uv sync --extra dev && uv run python -m pytest tests"
run_step "Web lint" bash -lc "cd '$ROOT_DIR/apps/web' && npm run lint"
run_step "Web build" bash -lc "cd '$ROOT_DIR/apps/web' && npm run build"
