#!/usr/bin/env bash
set -euo pipefail

profile="${1:-}"
if [[ -z "$profile" ]]; then
    echo "usage: $0 <siq_assistant|assistant|siq_analysis|analysis|siq_factchecker|factchecker|siq_tracking|tracking|siq_legal|legal|siq_ic_*|ic_*>" >&2
    exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SIQ_PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
HERMES_HOME="${SIQ_HERMES_HOME:-${HERMES_HOME:-$PROJECT_ROOT/data/hermes/home}}"
PROFILES_ROOT="${SIQ_HERMES_PROFILES_ROOT:-${HERMES_PROFILES_ROOT:-$HERMES_HOME/profiles}}"

case "$profile" in
    assistant|siq_assistant)
        canonical="siq_assistant"
        env_prefix="ASSISTANT"
        ;;
    analysis|siq_analysis)
        canonical="siq_analysis"
        env_prefix="ANALYSIS"
        ;;
    factchecker|siq_factchecker)
        canonical="siq_factchecker"
        env_prefix="FACTCHECKER"
        ;;
    tracking|siq_tracking)
        canonical="siq_tracking"
        env_prefix="TRACKING"
        ;;
    legal|siq_legal)
        canonical="siq_legal"
        env_prefix="LEGAL"
        ;;
    ic_master|ic_coordinator|siq_ic_master_coordinator)
        canonical="siq_ic_master_coordinator"
        env_prefix="IC_MASTER"
        ;;
    ic_chairman|siq_ic_chairman)
        canonical="siq_ic_chairman"
        env_prefix="IC_CHAIRMAN"
        ;;
    ic_strategy|ic_strategist|siq_ic_strategist)
        canonical="siq_ic_strategist"
        env_prefix="IC_STRATEGIST"
        ;;
    ic_sector|siq_ic_sector_expert)
        canonical="siq_ic_sector_expert"
        env_prefix="IC_SECTOR"
        ;;
    ic_finance|siq_ic_finance_auditor)
        canonical="siq_ic_finance_auditor"
        env_prefix="IC_FINANCE"
        ;;
    ic_legal|siq_ic_legal_scanner)
        canonical="siq_ic_legal_scanner"
        env_prefix="IC_LEGAL"
        ;;
    ic_risk|siq_ic_risk_controller)
        canonical="siq_ic_risk_controller"
        env_prefix="IC_RISK"
        ;;
    *)
        echo "Unknown Hermes profile: $profile" >&2
        exit 2
        ;;
esac

siq_env="SIQ_HERMES_${env_prefix}_PROFILE_ROOT"
legacy_env="HERMES_${env_prefix}_PROFILE_ROOT"
for env_name in "$siq_env" "$legacy_env"; do
    value="${!env_name:-}"
    if [[ -n "$value" && -f "$value/config.yaml" ]]; then
        cd "$value"
        pwd
        exit 0
    fi
done

candidates=(
    "$PROFILES_ROOT/$canonical"
    "$PROJECT_ROOT/agents/hermes/profiles/$canonical"
)

for dir in "${candidates[@]}"; do
    if [[ -f "$dir/config.yaml" ]]; then
        cd "$dir"
        pwd
        exit 0
    fi
done

echo "Hermes profile config not found for $canonical. Checked: ${candidates[*]}" >&2
exit 1
