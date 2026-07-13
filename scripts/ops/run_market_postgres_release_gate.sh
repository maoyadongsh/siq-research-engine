#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

MODE="${SIQ_MARKET_POSTGRES_GATE_MODE:-offline-postgres}"
OUTPUT_DIR="${SIQ_MARKET_POSTGRES_GATE_OUTPUT_DIR:-${REPO_ROOT}/artifacts/eval-runs/release}"
PYTHON_BIN="${PYTHON:-python3}"
DEFAULT_AGENT_MEMORY_RETRIEVAL_CASES="eval_datasets/agent_memory_retrieval_contract/cases.json"
DEFAULT_AGENT_MEMORY_VECTOR_SEED_PROFILES="siq_assistant,siq_ic_legal_scanner,siq_ic_chairman"

load_release_gate_config() {
  local config_file="${SIQ_PRODUCTION_CONFIG_FILE:-}"
  [[ -z "$config_file" ]] && return 0
  [[ -f "$config_file" ]] || return 0
  local raw_line line key value
  while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    line="${raw_line#"${raw_line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    if [[ "$line" == export\ * ]]; then
      line="${line#export }"
    fi
    if [[ ! "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
      continue
    fi
    key="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    case "$key" in
      SIQ_PRODUCTION_CONFIG_REQUIRED|SIQ_PARSER_FINANCIAL_PDF_GATE_MODE|SIQ_PARSER_FINANCIAL_PDF_GATE_REQUIRED|\
      SIQ_LIVE_MODEL_BENCHMARK_MODE|SIQ_LIVE_MODEL_BENCHMARK_REQUIRED|SIQ_LIVE_MODEL_URL|SIQ_LIVE_MODEL_AUTH_TOKEN|\
      SIQ_LIVE_MODEL_PROTOCOL|SIQ_LIVE_MODEL_TIMEOUT|SIQ_LIVE_MODEL_NAME|SIQ_PERMISSION_NEGATIVE_GATE_REQUIRED|\
      SIQ_PERMISSION_NEGATIVE_GATE_SKIP|SIQ_RESTORE_MATRIX_REQUIRED|SIQ_RESTORE_MATRIX_BACKUP_DIR|SIQ_RESTORE_MATRIX_ADMIN_URL)
        ;;
      *) continue ;;
    esac
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    if [[ "$value" == \"*\" && "$value" == *\" ]] || [[ "$value" == \'*\' && "$value" == *\' ]]; then
      value="${value:1:${#value}-2}"
    fi
    if [[ ${!key+x} == x && "${!key}" != "$value" ]]; then
      echo "SIQ_PRODUCTION_CONFIG_FILE conflicts with process environment for ${key}." >&2
      exit 2
    fi
    printf -v "$key" '%s' "$value"
    export "$key"
  done < "$config_file"
}

usage() {
  cat <<'EOF'
Usage: scripts/ops/run_market_postgres_release_gate.sh [--mode contract|offline-postgres] [--output-dir DIR]

Runs the market document_full PostgreSQL release gate and the deterministic
financial QA benchmark modes used by release artifacts.

Defaults:
  --mode offline-postgres
  --output-dir artifacts/eval-runs/release

The offline-postgres mode expects PostgreSQL access via SIQ_PGHOST/SIQ_PGPORT/SIQ_PGUSER/SIQ_PGPASSWORD.
Connection secrets are not accepted as command arguments. The underlying importer rewrites the database path to the fixed
non-A-share market databases: siq_hk, siq_jp, siq_kr, siq_eu, siq_us.
EOF
}

is_truthy() {
  case "${1:-}" in
    1|true|TRUE|True|yes|YES|Yes|on|ON|On) return 0 ;;
    *) return 1 ;;
  esac
}

is_falsey() {
  case "${1:-}" in
    0|false|FALSE|False|no|NO|No|off|OFF|Off) return 0 ;;
    *) return 1 ;;
  esac
}

validate_optional_bool() {
  local name="$1"
  local value="${!name:-}"
  [[ -z "$value" ]] && return 0
  if ! is_truthy "$value" && ! is_falsey "$value"; then
    echo "$name must be an explicit boolean (1/0, true/false, yes/no, on/off)." >&2
    exit 2
  fi
}

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

load_release_gate_config
PARSER_FINANCIAL_PDF_GATE_MODE="${SIQ_PARSER_FINANCIAL_PDF_GATE_MODE:-off}"
LIVE_MODEL_BENCHMARK_MODE="${SIQ_LIVE_MODEL_BENCHMARK_MODE:-disabled}"
RESTORE_MATRIX_BACKUP_DIR="${SIQ_RESTORE_MATRIX_BACKUP_DIR:-}"

for bool_name in \
  SIQ_PARSER_FINANCIAL_PDF_GATE_REQUIRED \
  SIQ_PRODUCTION_CONFIG_REQUIRED \
  SIQ_LIVE_MODEL_BENCHMARK_REQUIRED \
  SIQ_PERMISSION_NEGATIVE_GATE_SKIP \
  SIQ_PERMISSION_NEGATIVE_GATE_REQUIRED \
  SIQ_RESTORE_MATRIX_REQUIRED; do
  validate_optional_bool "$bool_name"
done

case "$MODE" in
  contract|offline-postgres) ;;
  *)
    echo "--mode must be contract or offline-postgres; got: ${MODE}" >&2
    exit 2
    ;;
esac

case "$PARSER_FINANCIAL_PDF_GATE_MODE" in
  off|preflight|live-http) ;;
  *)
    echo "SIQ_PARSER_FINANCIAL_PDF_GATE_MODE must be off, preflight, or live-http; got: ${PARSER_FINANCIAL_PDF_GATE_MODE}" >&2
    exit 2
    ;;
esac

case "$LIVE_MODEL_BENCHMARK_MODE" in
  disabled|live-http) ;;
  *)
    echo "SIQ_LIVE_MODEL_BENCHMARK_MODE must be disabled or live-http; got: ${LIVE_MODEL_BENCHMARK_MODE}" >&2
    exit 2
    ;;
esac

if is_truthy "${SIQ_PARSER_FINANCIAL_PDF_GATE_REQUIRED:-}" && [[ "$PARSER_FINANCIAL_PDF_GATE_MODE" == "off" ]]; then
  echo "SIQ_PARSER_FINANCIAL_PDF_GATE_REQUIRED requires an explicit preflight or live-http mode." >&2
  exit 2
fi

if is_truthy "${SIQ_PRODUCTION_CONFIG_REQUIRED:-}" && [[ -z "${SIQ_PRODUCTION_CONFIG_FILE:-}" ]]; then
  echo "SIQ_PRODUCTION_CONFIG_REQUIRED requires SIQ_PRODUCTION_CONFIG_FILE." >&2
  exit 2
fi

if is_truthy "${SIQ_LIVE_MODEL_BENCHMARK_REQUIRED:-}" && [[ "$LIVE_MODEL_BENCHMARK_MODE" != "live-http" ]]; then
  echo "SIQ_LIVE_MODEL_BENCHMARK_REQUIRED requires SIQ_LIVE_MODEL_BENCHMARK_MODE=live-http." >&2
  exit 2
fi

if is_truthy "${SIQ_PERMISSION_NEGATIVE_GATE_SKIP:-}" && ! is_falsey "${SIQ_PERMISSION_NEGATIVE_GATE_REQUIRED:-1}"; then
  echo "SIQ_PERMISSION_NEGATIVE_GATE_SKIP cannot bypass a required permission negative gate." >&2
  exit 2
fi
if is_truthy "${SIQ_PERMISSION_NEGATIVE_GATE_REQUIRED:-}" && [[ "$MODE" != "offline-postgres" ]]; then
  echo "SIQ_PERMISSION_NEGATIVE_GATE_REQUIRED requires --mode offline-postgres." >&2
  exit 2
fi
if is_truthy "${SIQ_AGENT_MEMORY_VECTOR_PROBES_REQUIRED:-}" && [[ "$MODE" != "offline-postgres" ]]; then
  echo "SIQ_AGENT_MEMORY_VECTOR_PROBES_REQUIRED requires --mode offline-postgres." >&2
  exit 2
fi
if is_truthy "${SIQ_AGENT_MEMORY_VECTOR_SEED:-}" && [[ "$MODE" != "offline-postgres" ]]; then
  echo "SIQ_AGENT_MEMORY_VECTOR_SEED requires --mode offline-postgres." >&2
  exit 2
fi

if is_truthy "${SIQ_RESTORE_MATRIX_REQUIRED:-}" && [[ -z "$RESTORE_MATRIX_BACKUP_DIR" ]]; then
  echo "SIQ_RESTORE_MATRIX_REQUIRED requires SIQ_RESTORE_MATRIX_BACKUP_DIR." >&2
  exit 2
fi
if [[ -n "$RESTORE_MATRIX_BACKUP_DIR" ]] && [[ -z "${SIQ_RESTORE_MATRIX_ADMIN_URL:-}" ]]; then
  echo "SIQ_RESTORE_MATRIX_BACKUP_DIR requires SIQ_RESTORE_MATRIX_ADMIN_URL." >&2
  exit 2
fi

if [[ "$OUTPUT_DIR" != /* ]]; then
  OUTPUT_DIR="${REPO_ROOT}/${OUTPUT_DIR}"
fi

mkdir -p "$OUTPUT_DIR"

cd "$REPO_ROOT"

status=0

if [[ -n "${SIQ_PRODUCTION_CONFIG_FILE:-}" ]]; then
  CONFIG_PREFLIGHT_ARGS=(
    --env-file "$SIQ_PRODUCTION_CONFIG_FILE"
    --json
    --required
  )
  config_status=0
  "$PYTHON_BIN" scripts/maintenance/check_production_config.py \
    "${CONFIG_PREFLIGHT_ARGS[@]}" \
    > "$OUTPUT_DIR/production_config_preflight.json" || config_status=$?
  if [[ "$config_status" -ne 0 ]]; then
    exit "$config_status"
  fi
fi

VECTOR_PROBE_ARGS=()
if is_truthy "${SIQ_AGENT_MEMORY_VECTOR_PROBES_SKIP:-}"; then
  VECTOR_PROBE_ARGS+=(--skip-agent-memory-vector-probes)
fi
if is_truthy "${SIQ_AGENT_MEMORY_VECTOR_PROBES_REQUIRED:-}"; then
  VECTOR_PROBE_ARGS+=(--require-agent-memory-vector-probes)
fi
if [[ -n "${SIQ_AGENT_MEMORY_EMBEDDING_MODEL:-}" ]]; then
  VECTOR_PROBE_ARGS+=(--agent-memory-embedding-model "$SIQ_AGENT_MEMORY_EMBEDDING_MODEL")
fi
if [[ -n "${SIQ_AGENT_MEMORY_EMBEDDING_TIMEOUT:-}" ]]; then
  VECTOR_PROBE_ARGS+=(--agent-memory-embedding-timeout "$SIQ_AGENT_MEMORY_EMBEDDING_TIMEOUT")
fi
if [[ -n "${SIQ_AGENT_MEMORY_EMBEDDING_PROBE_TEXTS:-}" ]]; then
  VECTOR_PROBE_ARGS+=(--agent-memory-embedding-probe-texts "$SIQ_AGENT_MEMORY_EMBEDDING_PROBE_TEXTS")
fi
if [[ -n "${SIQ_AGENT_MEMORY_MILVUS_COLLECTION:-}" ]]; then
  VECTOR_PROBE_ARGS+=(--agent-memory-vector-collection "$SIQ_AGENT_MEMORY_MILVUS_COLLECTION")
fi
AGENT_MEMORY_RETRIEVAL_CASES="${SIQ_AGENT_MEMORY_RETRIEVAL_CASES:-$DEFAULT_AGENT_MEMORY_RETRIEVAL_CASES}"
if [[ -n "$AGENT_MEMORY_RETRIEVAL_CASES" ]]; then
  VECTOR_PROBE_ARGS+=(--agent-memory-retrieval-cases "$AGENT_MEMORY_RETRIEVAL_CASES")
fi
if [[ -n "${SIQ_AGENT_MEMORY_RETRIEVAL_TOP_K:-}" ]]; then
  VECTOR_PROBE_ARGS+=(--agent-memory-retrieval-top-k "$SIQ_AGENT_MEMORY_RETRIEVAL_TOP_K")
fi
if [[ -n "${SIQ_AGENT_MEMORY_RETRIEVAL_MAX_CASES:-}" ]]; then
  VECTOR_PROBE_ARGS+=(--agent-memory-retrieval-max-cases "$SIQ_AGENT_MEMORY_RETRIEVAL_MAX_CASES")
fi

VECTOR_SEED_ARGS=(
  --require-configured-embed-url
  --output "$OUTPUT_DIR/agent_memory_milvus_seed.json"
  --markdown "$OUTPUT_DIR/agent_memory_milvus_seed.md"
)
if [[ -n "${SIQ_AGENT_MEMORY_VECTOR_SEED_PROFILES_ROOT:-}" ]]; then
  VECTOR_SEED_ARGS+=(--profiles-root "$SIQ_AGENT_MEMORY_VECTOR_SEED_PROFILES_ROOT")
fi
if [[ -n "${SIQ_AGENT_MEMORY_VECTOR_SEED_MANIFEST:-}" ]]; then
  VECTOR_SEED_ARGS+=(--manifest "$SIQ_AGENT_MEMORY_VECTOR_SEED_MANIFEST")
fi
AGENT_MEMORY_VECTOR_SEED_PROFILES="${SIQ_AGENT_MEMORY_VECTOR_SEED_PROFILES:-$DEFAULT_AGENT_MEMORY_VECTOR_SEED_PROFILES}"
if [[ -n "$AGENT_MEMORY_VECTOR_SEED_PROFILES" ]]; then
  VECTOR_SEED_ARGS+=(--profiles "$AGENT_MEMORY_VECTOR_SEED_PROFILES")
fi
if [[ -n "${SIQ_AGENT_MEMORY_MILVUS_COLLECTION:-}" ]]; then
  VECTOR_SEED_ARGS+=(--collection "$SIQ_AGENT_MEMORY_MILVUS_COLLECTION")
fi
if [[ -n "${SIQ_AGENT_MEMORY_EMBEDDING_MODEL:-}" ]]; then
  VECTOR_SEED_ARGS+=(--embed-model "$SIQ_AGENT_MEMORY_EMBEDDING_MODEL")
fi
if [[ -n "${SIQ_AGENT_MEMORY_EMBEDDING_DIM:-}" ]]; then
  VECTOR_SEED_ARGS+=(--vector-dim "$SIQ_AGENT_MEMORY_EMBEDDING_DIM")
fi
if [[ -n "${SIQ_AGENT_MEMORY_VECTOR_SEED_BATCH_SIZE:-}" ]]; then
  VECTOR_SEED_ARGS+=(--batch-size "$SIQ_AGENT_MEMORY_VECTOR_SEED_BATCH_SIZE")
fi
if [[ -n "${SIQ_AGENT_MEMORY_VECTOR_SEED_TIMEOUT:-}" ]]; then
  VECTOR_SEED_ARGS+=(--timeout "$SIQ_AGENT_MEMORY_VECTOR_SEED_TIMEOUT")
fi
if ! is_falsey "${SIQ_AGENT_MEMORY_VECTOR_SEED_FLUSH:-1}"; then
  VECTOR_SEED_ARGS+=(--flush)
fi
if is_truthy "${SIQ_AGENT_MEMORY_VECTOR_SEED_DRY_RUN:-}"; then
  VECTOR_SEED_ARGS+=(--dry-run)
fi

VECTOR_HEALTH_ARGS=(
  --output "$OUTPUT_DIR/agent_memory_vector_preflight.json"
  --markdown "$OUTPUT_DIR/agent_memory_vector_preflight.md"
)
if [[ -n "${SIQ_AGENT_MEMORY_MILVUS_COLLECTION:-}" ]]; then
  VECTOR_HEALTH_ARGS+=(--collection "$SIQ_AGENT_MEMORY_MILVUS_COLLECTION")
fi
if is_truthy "${SIQ_AGENT_MEMORY_VECTOR_SEED:-}" || is_truthy "${SIQ_AGENT_MEMORY_VECTOR_PROBES_REQUIRED:-}"; then
  VECTOR_HEALTH_ARGS+=(--require-milvus)
fi
if is_truthy "${SIQ_AGENT_MEMORY_VECTOR_HEALTH_REQUIRE_COLLECTION:-}"; then
  VECTOR_HEALTH_ARGS+=(--require-collection)
fi

VECTOR_POST_SEED_HEALTH_ARGS=(
  --output "$OUTPUT_DIR/agent_memory_vector_post_seed_health.json"
  --markdown "$OUTPUT_DIR/agent_memory_vector_post_seed_health.md"
  --require-milvus
  --require-collection
)
if [[ -n "${SIQ_AGENT_MEMORY_MILVUS_COLLECTION:-}" ]]; then
  VECTOR_POST_SEED_HEALTH_ARGS+=(--collection "$SIQ_AGENT_MEMORY_MILVUS_COLLECTION")
fi

"$PYTHON_BIN" scripts/maintenance/run_market_document_full_postgres_gate.py \
  --mode "$MODE" \
  --output-dir "$OUTPUT_DIR" || status=$?

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

PARSER_GOLDEN_MODE="contract"
if [[ "$MODE" == "offline-postgres" ]]; then
  PARSER_GOLDEN_MODE="offline-samples"
fi
PARSER_GOLDEN_ARGS=(
  --mode "$PARSER_GOLDEN_MODE"
  --output "$OUTPUT_DIR/parser_financial_golden.json"
  --markdown "$OUTPUT_DIR/parser_financial_golden.md"
)
if [[ -n "${SIQ_FINANCIAL_GOLDEN_SAMPLE_ROOT:-}" ]]; then
  PARSER_GOLDEN_ARGS+=(--sample-root "$SIQ_FINANCIAL_GOLDEN_SAMPLE_ROOT")
fi
"$PYTHON_BIN" scripts/maintenance/run_parser_financial_golden_gate.py \
  "${PARSER_GOLDEN_ARGS[@]}" || status=$?

if [[ "$PARSER_FINANCIAL_PDF_GATE_MODE" != "off" ]]; then
  PARSER_FINANCIAL_PDF_ARGS=(
    --mode "$PARSER_FINANCIAL_PDF_GATE_MODE"
    --output "$OUTPUT_DIR/parser_financial_pdf_release.json"
    --markdown "$OUTPUT_DIR/parser_financial_pdf_release.md"
  )
  if [[ -n "${SIQ_FINANCIAL_GOLDEN_PDF_ROOT:-}" ]]; then
    PARSER_FINANCIAL_PDF_ARGS+=(--pdf-root "$SIQ_FINANCIAL_GOLDEN_PDF_ROOT")
  fi
  if [[ -n "${SIQ_PDF_PARSER_URL:-}" ]]; then
    PARSER_FINANCIAL_PDF_ARGS+=(--parser-url "$SIQ_PDF_PARSER_URL")
  fi
  if [[ -n "${SIQ_PARSER_FINANCIAL_PDF_DEADLINE_SECONDS:-}" ]]; then
    PARSER_FINANCIAL_PDF_ARGS+=(--deadline-seconds "$SIQ_PARSER_FINANCIAL_PDF_DEADLINE_SECONDS")
  fi
  if [[ -n "${SIQ_PARSER_FINANCIAL_PDF_POLL_INTERVAL:-}" ]]; then
    PARSER_FINANCIAL_PDF_ARGS+=(--poll-interval "$SIQ_PARSER_FINANCIAL_PDF_POLL_INTERVAL")
  fi
  if [[ -n "${SIQ_PARSER_FINANCIAL_PDF_REQUEST_TIMEOUT:-}" ]]; then
    PARSER_FINANCIAL_PDF_ARGS+=(--request-timeout "$SIQ_PARSER_FINANCIAL_PDF_REQUEST_TIMEOUT")
  fi

  parser_financial_pdf_status=0
  "$PYTHON_BIN" scripts/maintenance/run_parser_financial_pdf_release_gate.py \
    "${PARSER_FINANCIAL_PDF_ARGS[@]}" || parser_financial_pdf_status=$?
  if [[ "$parser_financial_pdf_status" -ne 0 ]]; then
    status="$parser_financial_pdf_status"
  fi
fi

if [[ "$LIVE_MODEL_BENCHMARK_MODE" == "live-http" ]]; then
  LIVE_MODEL_ARGS=(
    --mode live-http
    --protocol "${SIQ_LIVE_MODEL_PROTOCOL:-auto}"
    --timeout "${SIQ_LIVE_MODEL_TIMEOUT:-60}"
    --output "$OUTPUT_DIR/live_financial_qa_benchmark.json"
    --markdown "$OUTPUT_DIR/live_financial_qa_benchmark.md"
  )
  if [[ -n "${SIQ_LIVE_MODEL_URL:-}" ]]; then
    LIVE_MODEL_ARGS+=(--endpoint "$SIQ_LIVE_MODEL_URL")
  fi
  if [[ -n "${SIQ_LIVE_MODEL_NAME:-}" ]]; then
    LIVE_MODEL_ARGS+=(--model "$SIQ_LIVE_MODEL_NAME")
  fi
  live_model_status=0
  "$PYTHON_BIN" scripts/maintenance/run_live_financial_qa_benchmark.py "${LIVE_MODEL_ARGS[@]}" || live_model_status=$?
  if [[ "$live_model_status" -ne 0 ]]; then
    status="$live_model_status"
  fi
fi

if [[ "$MODE" == "offline-postgres" ]]; then
  if ! is_truthy "${SIQ_PERMISSION_NEGATIVE_GATE_SKIP:-}"; then
    permission_status=0
    "$PYTHON_BIN" scripts/maintenance/run_permission_negative_report.py \
      --output "$OUTPUT_DIR/permission-negative-report.json" \
      --markdown "$OUTPUT_DIR/permission-negative-report.md" || permission_status=$?
    if [[ "$permission_status" -ne 0 ]]; then
      status="$permission_status"
    fi
  fi

  "$PYTHON_BIN" scripts/hermes/check_agent_memory_vector_health.py \
    "${VECTOR_HEALTH_ARGS[@]}" || status=$?

  if is_truthy "${SIQ_AGENT_MEMORY_VECTOR_SEED:-}"; then
    "$PYTHON_BIN" scripts/hermes/ingest_agent_memory_to_milvus.py \
      "${VECTOR_SEED_ARGS[@]}" || status=$?
    if ! is_truthy "${SIQ_AGENT_MEMORY_VECTOR_SEED_DRY_RUN:-}"; then
      "$PYTHON_BIN" scripts/hermes/check_agent_memory_vector_health.py \
        "${VECTOR_POST_SEED_HEALTH_ARGS[@]}" || status=$?
    fi
  fi

  "$PYTHON_BIN" scripts/maintenance/run_performance_baseline.py \
    --mode nightly \
    --require-nightly-inputs \
    --repeat 5 \
    --production-sample-manifest eval_datasets/market_document_full_postgres/production_sample_manifest.json \
    "${VECTOR_PROBE_ARGS[@]}" \
    --output "$OUTPUT_DIR/performance_baseline_nightly.json" \
    --markdown "$OUTPUT_DIR/performance_baseline_nightly.md" || status=$?
else
  "$PYTHON_BIN" scripts/maintenance/run_performance_baseline.py \
    --mode contract \
    --repeat 5 \
    --output "$OUTPUT_DIR/performance_baseline_contract.json" \
    --markdown "$OUTPUT_DIR/performance_baseline_contract.md" || status=$?
fi

if [[ -n "$RESTORE_MATRIX_BACKUP_DIR" ]]; then
  restore_matrix_status=0
  "$PYTHON_BIN" scripts/ops/run_restore_matrix.py \
    --backup-dir "$RESTORE_MATRIX_BACKUP_DIR" \
    --output "$OUTPUT_DIR/restore-matrix.json" \
    --markdown "$OUTPUT_DIR/restore-matrix.md" \
    || restore_matrix_status=$?
  if [[ "$restore_matrix_status" -ne 0 ]]; then
    status="$restore_matrix_status"
  fi
fi

if [[ "$MODE" == "offline-postgres" ]]; then
  POSTGRES_GATE_ARTIFACT="market_document_full_postgres_offline_postgres_gate.json"
else
  POSTGRES_GATE_ARTIFACT="market_document_full_postgres_contract_gate.json"
fi
MANIFEST_REQUIRED_ARGS=(
  --required-artifact "$POSTGRES_GATE_ARTIFACT"
  --required-artifact financial_qa_benchmark_trace_offline.json
  --required-artifact financial_qa_benchmark_wiki_static.json
  --required-artifact parser_financial_golden.json
)
if [[ "$MODE" == "offline-postgres" ]]; then
  MANIFEST_REQUIRED_ARGS+=(
    --required-artifact agent_memory_vector_preflight.json
    --required-artifact performance_baseline_nightly.json
  )
else
  MANIFEST_REQUIRED_ARGS+=(--required-artifact performance_baseline_contract.json)
fi
if [[ -n "${SIQ_PRODUCTION_CONFIG_FILE:-}" ]]; then
  MANIFEST_REQUIRED_ARGS+=(--required-artifact production_config_preflight.json)
fi
if [[ "$PARSER_FINANCIAL_PDF_GATE_MODE" != "off" ]]; then
  MANIFEST_REQUIRED_ARGS+=(--required-artifact parser_financial_pdf_release.json)
fi
if [[ "$LIVE_MODEL_BENCHMARK_MODE" == "live-http" ]]; then
  MANIFEST_REQUIRED_ARGS+=(--required-artifact live_financial_qa_benchmark.json)
fi
if [[ "$MODE" == "offline-postgres" ]] && ! is_truthy "${SIQ_PERMISSION_NEGATIVE_GATE_SKIP:-}"; then
  MANIFEST_REQUIRED_ARGS+=(--required-artifact permission-negative-report.json)
fi
if [[ -n "$RESTORE_MATRIX_BACKUP_DIR" ]]; then
  MANIFEST_REQUIRED_ARGS+=(--required-artifact restore-matrix.json)
fi
if [[ "$MODE" == "offline-postgres" ]] && is_truthy "${SIQ_AGENT_MEMORY_VECTOR_SEED:-}"; then
  MANIFEST_REQUIRED_ARGS+=(--required-artifact agent_memory_milvus_seed.json)
  if ! is_truthy "${SIQ_AGENT_MEMORY_VECTOR_SEED_DRY_RUN:-}"; then
    MANIFEST_REQUIRED_ARGS+=(--required-artifact agent_memory_vector_post_seed_health.json)
  fi
fi

"$PYTHON_BIN" scripts/maintenance/write_release_artifact_manifest.py \
  --artifact-dir "$OUTPUT_DIR" \
  --task-id T12 \
  --environment-profile "${SIQ_RELEASE_ENVIRONMENT_PROFILE:-local-production-equivalent}" \
  "${MANIFEST_REQUIRED_ARGS[@]}" \
  || status=$?

exit "$status"
