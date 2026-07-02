#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_PY="${API_PY:-$ROOT_DIR/apps/api/.venv/bin/python}"

run_step() {
    local label=$1
    shift
    printf '\n==> %s\n' "$label"
    "$@"
}

if [[ ! -x "$API_PY" ]]; then
    printf 'Missing API Python interpreter: %s\n' "$API_PY" >&2
    printf 'Create apps/api/.venv or set API_PY to the Python executable to use.\n' >&2
    exit 1
fi

run_step "API active run and loop gates" \
    bash -lc "cd '$ROOT_DIR/apps/api' && '$API_PY' -m pytest tests/test_agent_runtime_active_runs.py tests/test_agent_chat_runtime_loops.py -q"

run_step "API runtime focused suite" \
    bash -lc "cd '$ROOT_DIR/apps/api' && '$API_PY' -m pytest tests/test_agent_runtime_*.py -q"

run_step "API runtime compile gate" \
    bash -lc "cd '$ROOT_DIR/apps/api' && '$API_PY' -m py_compile services/agent_chat_runtime_impl.py services/agent_runtime_sessions.py services/agent_runtime_streaming.py tests/test_agent_runtime_active_runs.py"

run_step "PDF parser source and artifact gates" \
    bash -lc "cd '$ROOT_DIR/apps/pdf-parser' && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q tests/test_pdf_parser_source_service.py tests/test_pdf_source_viewer.py tests/test_pdf_parser_artifact_orchestrator_service.py"

run_step "PDF parser full suite" \
    bash -lc "cd '$ROOT_DIR/apps/pdf-parser' && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q"

run_step "Web document parser node gates" \
    bash -lc "cd '$ROOT_DIR/apps/web' && npm run test:unit"

run_step "Web frontend check" \
    bash -lc "cd '$ROOT_DIR/apps/web' && npm run check:frontend"

run_step "Git whitespace check" git -C "$ROOT_DIR" diff --check
run_step "Git status review" git -C "$ROOT_DIR" status --short
