#!/usr/bin/env bash

# SIQ Research Engine 后端启动脚本
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$SCRIPT_DIR"

export SIQ_PROJECT_ROOT="${SIQ_PROJECT_ROOT:-$PROJECT_ROOT}"

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

database_url_log_status() {
    local value="${1:-}"
    if [[ -n "$value" ]]; then
        printf '%s\n' "configured"
    else
        printf '%s\n' "not configured"
    fi
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
export SIQ_BACKEND_ROOT="${SIQ_BACKEND_ROOT:-$SCRIPT_DIR}"
export WIKI_ROOT="${SIQ_WIKI_ROOT:-${WIKI_ROOT:-$SIQ_DATA_ROOT/wiki}}"
export SIQ_WIKI_ROOT="${SIQ_WIKI_ROOT:-$WIKI_ROOT}"
export SIQ_DB_ROOT="${SIQ_DB_ROOT:-$PROJECT_ROOT/db}"
export SIQ_HERMES_HOME="${SIQ_HERMES_HOME:-$SIQ_DATA_ROOT/hermes/home}"
export SIQ_HERMES_PROFILES_ROOT="${SIQ_HERMES_PROFILES_ROOT:-$SIQ_HERMES_HOME/profiles}"
export SIQ_REPORT_FINDER_ROOT="${SIQ_REPORT_FINDER_ROOT:-$PROJECT_ROOT/services/market-report-finder}"
export SIQ_REPORT_DOWNLOADS_ROOT="${SIQ_REPORT_DOWNLOADS_ROOT:-$SIQ_DATA_ROOT/market-report-finder/downloads}"
export SIQ_BACKEND_PORT="${SIQ_BACKEND_PORT:-18081}"
export SIQ_PDF2MD_API_BASE="${SIQ_PDF2MD_API_BASE:-http://127.0.0.1:15000}"
export SIQ_DOCUMENT_PARSER_API_BASE="${SIQ_DOCUMENT_PARSER_API_BASE:-http://127.0.0.1:15010}"
export SIQ_REPORT_FINDER_BASE="${SIQ_REPORT_FINDER_BASE:-http://127.0.0.1:18000}"
export SIQ_REPORT_FINDER_HEALTH_URL="${SIQ_REPORT_FINDER_HEALTH_URL:-http://127.0.0.1:18000/health}"
export SIQ_PDF2MD_HEALTH_URL="${SIQ_PDF2MD_HEALTH_URL:-http://127.0.0.1:15000/api/health}"
export SIQ_DOCUMENT_PARSER_HEALTH_URL="${SIQ_DOCUMENT_PARSER_HEALTH_URL:-http://127.0.0.1:15010/api/health}"
export REDIS_URL="${REDIS_URL:-redis://localhost:16379/0}"
export SIQ_ALLOW_REGISTRATION="${SIQ_ALLOW_REGISTRATION:-0}"
export SIQ_DEMO_MODE="${SIQ_DEMO_MODE:-0}"
DEPLOYMENT_PROFILE="$(printf '%s' "${SIQ_DEPLOYMENT_PROFILE:-development}" | tr '[:upper:]' '[:lower:]')"
IS_PRODUCTION=0
if [[ "$DEPLOYMENT_PROFILE" == "production" || "$DEPLOYMENT_PROFILE" == "prod" ]]; then
    IS_PRODUCTION=1
fi
UVICORN_HOST="${SIQ_BACKEND_HOST:-}"
UVICORN_RELOAD="${SIQ_UVICORN_RELOAD:-}"
if [[ -z "$UVICORN_HOST" ]]; then
    if [[ "$IS_PRODUCTION" == "1" ]]; then
        UVICORN_HOST="127.0.0.1"
    else
        UVICORN_HOST="0.0.0.0"
    fi
fi
if [[ -z "$UVICORN_RELOAD" ]]; then
    if [[ "$IS_PRODUCTION" == "1" ]]; then
        UVICORN_RELOAD="0"
    else
        UVICORN_RELOAD="1"
    fi
fi
if [[ "$IS_PRODUCTION" == "1" && "$UVICORN_RELOAD" =~ ^(1|true|yes|on)$ ]]; then
    echo "❌ SIQ_UVICORN_RELOAD must not be enabled when SIQ_DEPLOYMENT_PROFILE=production."
    exit 1
fi
if [[ "$IS_PRODUCTION" == "1" && "${FLASK_DEBUG:-}" =~ ^(1|true|yes|on)$ ]]; then
    echo "❌ FLASK_DEBUG must not be enabled when SIQ_DEPLOYMENT_PROFILE=production."
    exit 1
fi

if [ -z "${SIQ_AUTH_SECRET_KEY:-}" ]; then
    echo "❌ 缺少 SIQ_AUTH_SECRET_KEY。请先执行："
    echo "   export SIQ_AUTH_SECRET_KEY=\"$(openssl rand -hex 32)\""
    exit 1
fi

echo "🚀 启动 SIQ Research Engine 后端服务..."
echo "   部署模式: $DEPLOYMENT_PROFILE"
echo "   监听地址: $UVICORN_HOST:$SIQ_BACKEND_PORT"
echo "   允许注册: $SIQ_ALLOW_REGISTRATION"
echo "   演示登录: $SIQ_DEMO_MODE"
echo "   WIKI_ROOT: $WIKI_ROOT"
database_url_for_log="${SIQ_APP_DATABASE_URL:-${DATABASE_URL:-}}"
echo "   SIQ_APP_DATABASE_URL: $(database_url_log_status "$database_url_for_log")"
echo ""

uvicorn_args=(main:app --host "$UVICORN_HOST" --port "$SIQ_BACKEND_PORT")
if [[ "$UVICORN_RELOAD" =~ ^(1|true|yes|on)$ ]]; then
    uvicorn_args+=(--reload)
fi

uv run python -m uvicorn "${uvicorn_args[@]}"
