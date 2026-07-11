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
run_step "Workflow security and artifact hygiene" python3 "$ROOT_DIR/scripts/maintenance/check_local_security_hygiene.py" --repo-root "$ROOT_DIR" --scope workflow
run_step "Changed large-file gate" python3 "$ROOT_DIR/scripts/maintenance/check_large_file_changes.py" --repo-root "$ROOT_DIR"
run_step "Touched Python quality gate" python3 "$ROOT_DIR/scripts/maintenance/check_python_quality_touched.py" --repo-root "$ROOT_DIR"
run_step "Market document_full PostgreSQL contract gate" python3 "$ROOT_DIR/scripts/maintenance/run_market_document_full_postgres_gate.py" --mode contract --output-dir "$ROOT_DIR/artifacts/eval-runs/local-check-all"
run_step "Large file observe report" python3 "$ROOT_DIR/scripts/maintenance/observe_large_files.py" --root "$ROOT_DIR" --limit 20
run_step "Web dependency install" bash -lc "cd '$ROOT_DIR/apps/web' && npm ci"
run_step "Web unit tests" bash -lc "cd '$ROOT_DIR/apps/web' && npm run test:unit"
run_step "Web frontend check" bash -lc "cd '$ROOT_DIR/apps/web' && npm run check:frontend"
