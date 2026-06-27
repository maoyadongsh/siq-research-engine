#!/usr/bin/env bash

# SIQ Research Engine 后端启动脚本
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$SCRIPT_DIR"

export SIQ_PROJECT_ROOT="${SIQ_PROJECT_ROOT:-$PROJECT_ROOT}"

ENV_FILE="${SIQ_ENV_FILE:-$PROJECT_ROOT/env/backend.env}"
if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

export SIQ_BACKEND_ROOT="${SIQ_BACKEND_ROOT:-$SCRIPT_DIR}"
export WIKI_ROOT="${SIQ_WIKI_ROOT:-${WIKI_ROOT:-$PROJECT_ROOT/data/wiki}}"
export SIQ_WIKI_ROOT="${SIQ_WIKI_ROOT:-$WIKI_ROOT}"
export SIQ_DB_ROOT="${SIQ_DB_ROOT:-$PROJECT_ROOT/db}"
export SIQ_HERMES_HOME="${SIQ_HERMES_HOME:-$PROJECT_ROOT/data/hermes/home}"
export SIQ_HERMES_PROFILES_ROOT="${SIQ_HERMES_PROFILES_ROOT:-$SIQ_HERMES_HOME/profiles}"
export SIQ_REPORT_FINDER_ROOT="${SIQ_REPORT_FINDER_ROOT:-$PROJECT_ROOT/services/market-report-finder}"
export SIQ_REPORT_DOWNLOADS_ROOT="${SIQ_REPORT_DOWNLOADS_ROOT:-$PROJECT_ROOT/data/market-report-finder/downloads}"
export SIQ_BACKEND_PORT="${SIQ_BACKEND_PORT:-18081}"
export SIQ_PDF2MD_API_BASE="${SIQ_PDF2MD_API_BASE:-http://127.0.0.1:15000}"
export SIQ_REPORT_FINDER_BASE="${SIQ_REPORT_FINDER_BASE:-http://127.0.0.1:18000}"
export SIQ_REPORT_FINDER_HEALTH_URL="${SIQ_REPORT_FINDER_HEALTH_URL:-http://127.0.0.1:18000/health}"
export SIQ_PDF2MD_HEALTH_URL="${SIQ_PDF2MD_HEALTH_URL:-http://127.0.0.1:15000/api/health}"
export REDIS_URL="${REDIS_URL:-redis://localhost:16379/0}"
export SIQ_ALLOW_REGISTRATION="${SIQ_ALLOW_REGISTRATION:-0}"
export SIQ_DEMO_MODE="${SIQ_DEMO_MODE:-0}"

if [ -z "${SIQ_AUTH_SECRET_KEY:-}" ]; then
    echo "❌ 缺少 SIQ_AUTH_SECRET_KEY。请先执行："
    echo "   export SIQ_AUTH_SECRET_KEY=\"$(openssl rand -hex 32)\""
    exit 1
fi

echo "🚀 启动 SIQ Research Engine 后端服务..."
echo "   监听地址: 0.0.0.0:$SIQ_BACKEND_PORT"
echo "   允许注册: $SIQ_ALLOW_REGISTRATION"
echo "   演示登录: $SIQ_DEMO_MODE"
echo "   WIKI_ROOT: $WIKI_ROOT"
echo "   DATABASE_URL: ${DATABASE_URL:-sqlite fallback}"
echo ""

uv run python -m uvicorn main:app --host 0.0.0.0 --port "$SIQ_BACKEND_PORT" --reload
