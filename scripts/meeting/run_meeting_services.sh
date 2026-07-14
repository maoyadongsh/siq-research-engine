#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SIQ_PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
ENV_FILE="${SIQ_ENV_FILE:-$PROJECT_ROOT/infra/env/local.env}"
LEGACY_ENV_FILE="$PROJECT_ROOT/env/backend.env"

source_env_if_exists() {
    local env_file=$1
    [[ -f "$env_file" ]] || return 1
    set -a
    # shellcheck disable=SC1090
    source "$env_file"
    set +a
}

if ! source_env_if_exists "$ENV_FILE" && [[ -z "${SIQ_ENV_FILE:-}" ]]; then
    source_env_if_exists "$LEGACY_ENV_FILE" || true
fi

is_enabled() {
    case "${1:-0}" in
        1|true|TRUE|yes|YES|on|ON) return 0 ;;
        *) return 1 ;;
    esac
}

if ! is_enabled "${SIQ_MEETINGS_ENABLED:-0}"; then
    echo "meeting services are disabled (SIQ_MEETINGS_ENABLED=0)" >&2
    exit 0
fi

export SIQ_PROJECT_ROOT="$PROJECT_ROOT"
export SIQ_RUNTIME_ROOT="${SIQ_RUNTIME_ROOT:-$PROJECT_ROOT/var}"
export SIQ_DATA_ROOT="${SIQ_DATA_ROOT:-$PROJECT_ROOT/data}"
export SIQ_ARTIFACTS_ROOT="${SIQ_ARTIFACTS_ROOT:-$PROJECT_ROOT/artifacts}"
export SIQ_MEETINGS_HERMES_TARGETS_FILE="${SIQ_MEETINGS_HERMES_TARGETS_FILE:-$SIQ_RUNTIME_ROOT/meetings/hermes-targets.json}"

realtime_asr_flag="${SIQ_MEETING_REALTIME_ASR_ENABLED:-${SIQ_MEETINGS_ASR_ENABLED:-0}}"
import_flag="${SIQ_MEETING_IMPORT_ENABLED:-0}"
native_capture_flag="${SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED:-0}"
ai_flag="${SIQ_MEETING_AI_ENABLED:-${SIQ_MEETINGS_AI_ENABLED:-0}}"
voiceprint_flag="${SIQ_MEETING_VOICEPRINT_ENABLED:-${SIQ_MEETINGS_VOICEPRINT_ENABLED:-0}}"
delete_worker_flag="${SIQ_MEETING_DELETE_WORKER_ENABLED:-0}"
gateway_mode="${SIQ_MEETING_STREAM_GATEWAY_MODE:-}"
deployment_profile="$(printf '%s' "${SIQ_DEPLOYMENT_PROFILE:-development}" | tr '[:upper:]' '[:lower:]')"
if [[ -z "$gateway_mode" ]]; then
    case "$deployment_profile" in
        docker|prod|production) gateway_mode=external ;;
        *) gateway_mode=embedded ;;
    esac
fi
if [[ "$gateway_mode" != "embedded" && "$gateway_mode" != "external" ]]; then
    echo "SIQ_MEETING_STREAM_GATEWAY_MODE must be embedded or external" >&2
    exit 2
fi
if [[ "$gateway_mode" == "embedded" && "$deployment_profile" =~ ^(docker|prod|production)$ ]]; then
    echo "protected deployments require SIQ_MEETING_STREAM_GATEWAY_MODE=external" >&2
    exit 2
fi

pids=()
names=()

cleanup() {
    local pid
    trap - EXIT INT TERM
    for pid in "${pids[@]:-}"; do
        kill -TERM "$pid" 2>/dev/null || true
    done
    for pid in "${pids[@]:-}"; do
        wait "$pid" 2>/dev/null || true
    done
}
trap cleanup EXIT INT TERM

start_child() {
    local name=$1
    shift
    echo "starting meeting component: $name"
    "$@" &
    pids+=("$!")
    names+=("$name")
}

start_child "export-worker" \
    /usr/bin/env bash -lc "cd '$PROJECT_ROOT/apps/api' && exec uv run --frozen python scripts/meeting_export_worker.py"

if is_enabled "$native_capture_flag"; then
    start_child "native-capture-finalization-worker" \
        /usr/bin/env bash -lc "cd '$PROJECT_ROOT/apps/api' && exec uv run --frozen python scripts/meeting_native_capture_worker.py"
fi

if [[ "$gateway_mode" == "external" ]]; then
    gateway_host="${SIQ_MEETING_STREAM_GATEWAY_HOST:-127.0.0.1}"
    gateway_port="${SIQ_MEETING_STREAM_GATEWAY_PORT:-18082}"
    start_child "stream-gateway" \
        /usr/bin/env bash -lc "cd '$PROJECT_ROOT/apps/api' && exec uv run --frozen uvicorn meeting_stream_gateway:app --host '$gateway_host' --port '$gateway_port' --no-access-log"
fi

if is_enabled "$delete_worker_flag"; then
    start_child "retention-worker" \
        /usr/bin/env bash -lc "cd '$PROJECT_ROOT/apps/api' && exec uv run --frozen python scripts/meeting_retention_worker.py"
fi

if is_enabled "$realtime_asr_flag" || is_enabled "$import_flag" || is_enabled "$native_capture_flag"; then
    start_child "speech" \
        "$PROJECT_ROOT/infra/model-services/meeting-speech/start_meeting_speech.sh"
fi

if is_enabled "$import_flag"; then
    start_child "import-ingest-worker" \
        /usr/bin/env bash -lc "cd '$PROJECT_ROOT/apps/api' && exec uv run --frozen python scripts/meeting_import_worker.py --mode ingest"
fi

if is_enabled "$realtime_asr_flag" || is_enabled "$import_flag" || is_enabled "$native_capture_flag"; then
    start_child "finalization-worker" \
        /usr/bin/env bash -lc "cd '$PROJECT_ROOT/apps/api' && exec uv run --frozen python scripts/meeting_ai_worker.py --lane finalization"
fi

if is_enabled "$ai_flag"; then
    if [[ -z "${SIQ_MEETINGS_HERMES_API_KEY:-}" ]]; then
        echo "SIQ_MEETINGS_HERMES_API_KEY is required when meeting AI is enabled" >&2
        exit 2
    fi
    sync_args=(
        sync
        --output "$SIQ_MEETINGS_HERMES_TARGETS_FILE"
        --port-base "${SIQ_MEETINGS_HERMES_PORT_BASE:-18710}"
    )
    if [[ -n "${SIQ_MEETINGS_MODEL_ALLOWLIST:-}" ]]; then
        sync_args+=(--allowlist "$SIQ_MEETINGS_MODEL_ALLOWLIST")
    fi
    python3 "$PROJECT_ROOT/scripts/hermes/meeting_targets.py" "${sync_args[@]}"

    mapfile -t target_ids < <(
        python3 - "$SIQ_MEETINGS_HERMES_TARGETS_FILE" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    targets = json.load(handle)
for target in targets:
    if target.get("enabled", True):
        print(target["target_id"])
PY
    )
    if [[ "${#target_ids[@]}" -eq 0 ]]; then
        echo "meeting AI is enabled but the immutable target pool is empty" >&2
        exit 2
    fi
    for target_id in "${target_ids[@]}"; do
        start_child "hermes:$target_id" \
            "$PROJECT_ROOT/scripts/hermes/run_meeting_gateway.sh" "$target_id"
    done
    start_child "minutes-worker" \
        /usr/bin/env bash -lc "cd '$PROJECT_ROOT/apps/api' && exec uv run --frozen python scripts/meeting_ai_worker.py --lane minutes"
    start_child "correction-worker" \
        /usr/bin/env bash -lc "cd '$PROJECT_ROOT/apps/api' && exec uv run --frozen python scripts/meeting_ai_worker.py --lane correction"
fi

if is_enabled "$voiceprint_flag"; then
    start_child "voiceprint-worker" \
        "$PROJECT_ROOT/apps/api/scripts/start_meeting_voiceprint_worker.sh"
fi

if [[ "${#pids[@]}" -eq 0 ]]; then
    echo "meeting domain is enabled, but no optional meeting service is enabled" >&2
    exit 2
fi

echo "meeting services started: ${names[*]}"
set +e
wait -n "${pids[@]}"
exit_code=$?
set -e
echo "a meeting component exited (status $exit_code); stopping the meeting service group" >&2
exit "$exit_code"
