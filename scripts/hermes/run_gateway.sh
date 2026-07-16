#!/usr/bin/env bash
set -euo pipefail

profile="${1:-}"
if [[ -z "$profile" ]]; then
    echo "usage: $0 <siq_assistant|assistant|siq_analysis|analysis|siq_factchecker|factchecker|siq_tracking|tracking|siq_legal|legal|siq_ic_*|ic_*>" >&2
    exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SIQ_PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
SOURCE_PROFILES_ROOT="$PROJECT_ROOT/agents/hermes/profiles"

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
export SIQ_DATA_ROOT="${SIQ_DATA_ROOT:-$PROJECT_ROOT/data}"
export SIQ_RUNTIME_ROOT="${SIQ_RUNTIME_ROOT:-$PROJECT_ROOT/var}"
export SIQ_ARTIFACTS_ROOT="${SIQ_ARTIFACTS_ROOT:-$PROJECT_ROOT/artifacts}"
export SIQ_DATASETS_ROOT="${SIQ_DATASETS_ROOT:-$PROJECT_ROOT/datasets}"

case "$profile" in
    assistant|siq_assistant) canonical="siq_assistant" ;;
    analysis|siq_analysis) canonical="siq_analysis" ;;
    factchecker|siq_factchecker) canonical="siq_factchecker" ;;
    tracking|siq_tracking) canonical="siq_tracking" ;;
    legal|siq_legal) canonical="siq_legal" ;;
    ic_master|ic_coordinator|siq_ic_master_coordinator) canonical="siq_ic_master_coordinator" ;;
    ic_chairman|siq_ic_chairman) canonical="siq_ic_chairman" ;;
    ic_strategy|ic_strategist|siq_ic_strategist) canonical="siq_ic_strategist" ;;
    ic_sector|siq_ic_sector_expert) canonical="siq_ic_sector_expert" ;;
    ic_finance|siq_ic_finance_auditor) canonical="siq_ic_finance_auditor" ;;
    ic_legal|siq_ic_legal_scanner) canonical="siq_ic_legal_scanner" ;;
    ic_risk|siq_ic_risk_controller) canonical="siq_ic_risk_controller" ;;
    *)
        echo "Unknown Hermes profile: $profile" >&2
        exit 2
        ;;
esac

source_profile_dir="$SOURCE_PROFILES_ROOT/$canonical"
if [[ ! -f "$source_profile_dir/config.yaml" ]]; then
    echo "Hermes source profile config not found: $source_profile_dir/config.yaml" >&2
    exit 1
fi
if ! command -v rsync >/dev/null 2>&1; then
    echo "Hermes profile sync requires rsync" >&2
    exit 1
fi
runtime_profiles_root="${SIQ_HERMES_PROFILES_ROOT:-${HERMES_PROFILES_ROOT:-${SIQ_HERMES_HOME:-$SIQ_DATA_ROOT/hermes/home}/profiles}}"
profile_dir="$runtime_profiles_root/$canonical"

mkdir -p "$profile_dir"
rsync_excludes=(
    --exclude '.git/'
    --exclude '.venv/'
    --exclude '__pycache__/'
    --exclude '.pytest_cache/'
    --exclude 'cache/'
    --exclude 'logs/'
    --exclude 'sessions/'
    --exclude 'cron/'
    --exclude 'memories/'
    --exclude 'sandboxes/'
    --exclude 'workspace/'
    --exclude 'backups/'
    --exclude 'skills/'
    --exclude 'state.db*'
    --exclude 'response_store.db*'
    --exclude 'gateway.pid'
    --exclude 'gateway.lock'
    --exclude 'gateway_state.json'
    --exclude 'auth.json'
    --exclude 'auth.lock'
    --exclude 'channel_directory.json'
    --exclude 'models_dev_cache.json'
    --exclude '.skills_prompt_snapshot.json'
    --exclude '.no-bundled-skills'
    --exclude '.siq-empty-bundled-skills/'
    --exclude '.clean_shutdown'
)
if [[ -f "$profile_dir/config.yaml" && ( "$canonical" == siq_ic_* || "${SIQ_HERMES_FORCE_PROFILE_SYNC:-0}" != "1" ) ]]; then
    rsync_excludes+=(--exclude 'config.yaml')
fi

rsync -a --delete "${rsync_excludes[@]}" "$source_profile_dir/" "$profile_dir/"

if [[ "$canonical" == siq_ic_* ]]; then
    python3 "$SCRIPT_DIR/sync_profile_runtime_config.py" \
        "$source_profile_dir/config.yaml" \
        "$profile_dir/config.yaml"
fi

mkdir -p "$runtime_profiles_root"
(
    flock -x 9
    if [[ -d "$SOURCE_PROFILES_ROOT/shared" ]]; then
        mkdir -p "$runtime_profiles_root/shared"
        rsync -a --delete \
            --exclude '__pycache__/' \
            --exclude '.pytest_cache/' \
            "$SOURCE_PROFILES_ROOT/shared/" "$runtime_profiles_root/shared/"
    fi

    if [[ -d "$SOURCE_PROFILES_ROOT/siq_ic_shared" ]]; then
        mkdir -p "$runtime_profiles_root/siq_ic_shared"
        rsync -a --delete \
            --exclude '__pycache__/' \
            --exclude '.pytest_cache/' \
            --exclude 'logs/' \
            --exclude 'sessions/' \
            --exclude 'state.db*' \
            --exclude 'response_store.db*' \
            "$SOURCE_PROFILES_ROOT/siq_ic_shared/" "$runtime_profiles_root/siq_ic_shared/"
    fi
) 9>"$runtime_profiles_root/.shared-profile-sync.lock"

if [[ "$canonical" == siq_ic_* ]]; then
    ic_shared_skills_dir="$SOURCE_PROFILES_ROOT/siq_ic_shared/skills"
    ic_profile_matrix="$SOURCE_PROFILES_ROOT/siq_ic_shared/ic_profile_matrix.json"
    if [[ -d "$ic_shared_skills_dir" ]]; then
        skill_ids_output="$(
            python3 - "$ic_profile_matrix" "$canonical" <<'PY'
import json
import re
import sys
from pathlib import Path

matrix_path = Path(sys.argv[1])
profile_id = sys.argv[2]
matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
profile = next(
    (item for item in matrix.get("profiles", []) if item.get("id") == profile_id),
    None,
)
if not isinstance(profile, dict):
    raise SystemExit(f"IC profile missing from matrix: {profile_id}")
skill_ids = profile.get("skill_ids")
if not isinstance(skill_ids, list) or not skill_ids:
    raise SystemExit(f"IC profile has no skill_ids whitelist: {profile_id}")
for skill_id in skill_ids:
    if not isinstance(skill_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", skill_id):
        raise SystemExit(f"Invalid IC skill id for {profile_id}: {skill_id!r}")
    if not (matrix_path.parent / "skills" / skill_id).is_dir():
        raise SystemExit(f"IC skill directory not found for {profile_id}: {skill_id}")
    print(skill_id)
PY
        )" || {
            echo "Failed to resolve IC skill whitelist for $canonical" >&2
            exit 1
        }
        mapfile -t ic_skill_ids <<<"$skill_ids_output"
        skill_rsync_args=(
            -a
            --delete
            --delete-excluded
            --exclude '__pycache__/'
            --exclude '.pytest_cache/'
            --exclude '.clawhub/'
        )
        for skill_id in "${ic_skill_ids[@]}"; do
            skill_rsync_args+=(--include "/$skill_id/" --include "/$skill_id/***")
        done
        skill_rsync_args+=(--exclude '*')
        mkdir -p "$profile_dir/skills"
        rsync "${skill_rsync_args[@]}" \
            "$ic_shared_skills_dir/" "$profile_dir/skills/"
    fi
    touch "$profile_dir/.no-bundled-skills"
    mkdir -p "$profile_dir/.siq-empty-bundled-skills"
fi

cd "$profile_dir"

export HERMES_HOME="$profile_dir"
if [[ "$canonical" == siq_ic_* ]]; then
    # Hermes otherwise seeds its generic bundled catalog on gateway startup,
    # which would silently bypass the role-specific skill whitelist above.
    export HERMES_BUNDLED_SKILLS="$profile_dir/.siq-empty-bundled-skills"
fi
export API_SERVER_MODEL_NAME="${API_SERVER_MODEL_NAME:-$canonical}"
export API_SERVER_KEY="${API_SERVER_KEY:-${HERMES_API_KEY:-${HERMES_TOKEN:-}}}"

exec hermes gateway run --replace --accept-hooks
