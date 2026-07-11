#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

MODE="${SIQ_MARKET_POSTGRES_GATE_MODE:-offline-postgres}"
OUTPUT_DIR="${SIQ_MARKET_POSTGRES_GATE_OUTPUT_DIR:-${REPO_ROOT}/artifacts/eval-runs/release}"
PYTHON_BIN="${PYTHON:-python3}"

usage() {
  cat <<'EOF'
Usage: scripts/ops/run_market_postgres_release_gate.sh [--mode contract|offline-postgres] [--output-dir DIR] [--database-url URL]

Runs the market document_full PostgreSQL release gate and the deterministic
financial QA benchmark modes used by release artifacts.

Defaults:
  --mode offline-postgres
  --output-dir artifacts/eval-runs/release

The offline-postgres mode expects PostgreSQL access via SIQ_PGHOST/SIQ_PGPORT/SIQ_PGUSER/SIQ_PGPASSWORD
or an explicit --database-url. The underlying importer rewrites the database path to the fixed
non-A-share market databases: siq_hk, siq_jp, siq_kr, siq_eu, siq_us.
EOF
}

DATABASE_URL_ARG=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      MODE="${2:-}"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="${2:-}"
      shift 2
      ;;
    --database-url)
      DATABASE_URL_ARG=(--database-url "${2:-}")
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "$MODE" in
  contract|offline-postgres) ;;
  *)
    echo "--mode must be contract or offline-postgres; got: ${MODE}" >&2
    exit 2
    ;;
esac

if [[ "$OUTPUT_DIR" != /* ]]; then
  OUTPUT_DIR="${REPO_ROOT}/${OUTPUT_DIR}"
fi

mkdir -p "$OUTPUT_DIR"

cd "$REPO_ROOT"

status=0

"$PYTHON_BIN" scripts/maintenance/run_market_document_full_postgres_gate.py \
  --mode "$MODE" \
  --output-dir "$OUTPUT_DIR" \
  "${DATABASE_URL_ARG[@]}" || status=$?

"$PYTHON_BIN" scripts/maintenance/run_financial_qa_benchmark.py \
  --mode trace-offline \
  --case-root datasets/eval/financial_qa_benchmark/v1 \
  --trace-log datasets/eval/financial_qa_benchmark/v1/traces/p0_golden_traces.jsonl \
  --output "$OUTPUT_DIR/financial_qa_benchmark_trace_offline.json" \
  --markdown "$OUTPUT_DIR/financial_qa_benchmark_trace_offline.md" || status=$?

"$PYTHON_BIN" scripts/maintenance/run_financial_qa_benchmark.py \
  --mode wiki-static \
  --case-root datasets/eval/financial_qa_benchmark/v1 \
  --output "$OUTPUT_DIR/financial_qa_benchmark_wiki_static.json" \
  --markdown "$OUTPUT_DIR/financial_qa_benchmark_wiki_static.md" || status=$?

exit "$status"
