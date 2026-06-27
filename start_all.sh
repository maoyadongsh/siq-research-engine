#!/usr/bin/env bash
set -euo pipefail

# ============================================================
#  一键启动：统一公告下载服务 (18000) + FastAPI 后端 (18081) + PDF 解析 (15000) + 文档解析 (15010) + Vite 前端 (15173)
#  可选：备用市场下载服务 (18010)、市场规则服务 (18020)、Milvus 向量入库控制台 (7862)
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIQ_PROJECT_ROOT="${SIQ_PROJECT_ROOT:-$SCRIPT_DIR}"
export SIQ_PROJECT_ROOT

ENV_FILE="${SIQ_ENV_FILE:-$SIQ_PROJECT_ROOT/env/backend.env}"
if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

FRONTEND_ENV_FILE="${SIQ_FRONTEND_ENV_FILE:-$SIQ_PROJECT_ROOT/env/frontend-dev.env}"
if [[ -f "$FRONTEND_ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$FRONTEND_ENV_FILE"
    set +a
fi

WIKI_ROOT="${SIQ_WIKI_ROOT:-${WIKI_ROOT:-$SIQ_PROJECT_ROOT/data/wiki}}"
export WIKI_ROOT
export SIQ_WIKI_ROOT="${SIQ_WIKI_ROOT:-$WIKI_ROOT}"

BACKEND_DIR="${SIQ_BACKEND_ROOT:-${SIQ_BACKEND_ROOT:-$SIQ_PROJECT_ROOT/apps/api}}"
FRONT_DIR="${SIQ_FRONTEND_ROOT:-${SIQ_FRONTEND_ROOT:-$SIQ_PROJECT_ROOT/apps/web}}"
PDF2MD_DIR="${SIQ_PDF2MD_ROOT:-${PDF2MD_ROOT:-${SIQ_PDF2MD_ROOT:-$SIQ_PROJECT_ROOT/apps/pdf-parser}}}"
DOCUMENT_PARSER_DIR="${SIQ_DOCUMENT_PARSER_ROOT:-${DOCUMENT_PARSER_ROOT:-$SIQ_PROJECT_ROOT/apps/document-parser}}"
MARKET_REPORT_FINDER_DIR="${SIQ_MARKET_REPORT_FINDER_ROOT:-${MARKET_REPORT_FINDER_ROOT:-$SIQ_PROJECT_ROOT/services/market-report-finder}}"
REPORT_FINDER_DIR="${SIQ_REPORT_FINDER_ROOT:-${REPORT_FINDER_ROOT:-$MARKET_REPORT_FINDER_DIR}}"
MARKET_REPORT_RULES_DIR="${SIQ_MARKET_REPORT_RULES_ROOT:-${MARKET_REPORT_RULES_ROOT:-$SIQ_PROJECT_ROOT/services/market-report-rules}}"
BACKEND_PORT="${SIQ_BACKEND_PORT:-${BACKEND_PORT:-18081}}"
FRONTEND_PORT="${SIQ_FRONTEND_PORT:-${FRONTEND_PORT:-15173}}"
PDF2MD_PORT="${SIQ_PDF2MD_PORT:-${PDF2MD_PORT:-15000}}"
DOCUMENT_PARSER_PORT="${SIQ_DOCUMENT_PARSER_PORT:-${DOCUMENT_PARSER_PORT:-15010}}"
REPORT_FINDER_PORT="${SIQ_REPORT_FINDER_PORT:-${REPORT_FINDER_PORT:-18000}}"
MARKET_REPORT_FINDER_PORT="${SIQ_MARKET_REPORT_FINDER_PORT:-${MARKET_REPORT_FINDER_PORT:-18010}}"
MARKET_REPORT_RULES_PORT="${SIQ_MARKET_REPORT_RULES_PORT:-${MARKET_REPORT_RULES_PORT:-18020}}"
HERMES_ASSISTANT_PORT="${SIQ_HERMES_ASSISTANT_PORT:-${HERMES_ASSISTANT_PORT:-18642}}"
HERMES_FACTCHECKER_PORT="${SIQ_HERMES_FACTCHECKER_PORT:-${HERMES_FACTCHECKER_PORT:-18649}}"
HERMES_TRACKING_PORT="${SIQ_HERMES_TRACKING_PORT:-${HERMES_TRACKING_PORT:-18650}}"
HERMES_ANALYSIS_PORT="${SIQ_HERMES_ANALYSIS_PORT:-${HERMES_ANALYSIS_PORT:-18651}}"
HERMES_LEGAL_PORT="${SIQ_HERMES_LEGAL_PORT:-${HERMES_LEGAL_PORT:-18652}}"
START_HERMES_GATEWAYS="${SIQ_START_HERMES_GATEWAYS:-1}"
START_MARKET_REPORT_FINDER="${SIQ_START_MARKET_REPORT_FINDER:-0}"
START_MARKET_REPORT_RULES="${SIQ_START_MARKET_REPORT_RULES:-0}"
START_VECTOR_INGEST="${SIQ_START_VECTOR_INGEST:-0}"
VECTOR_INGEST_PORT="${SIQ_VECTOR_INGEST_PORT:-${VECTOR_INGEST_PORT:-7862}}"
VECTOR_INGEST_DIR="${SIQ_VECTOR_INGEST_ROOT:-${VECTOR_INGEST_ROOT:-$SIQ_PROJECT_ROOT/scripts/vector-index/milvus-ingestion}}"
export SIQ_BACKEND_ROOT="$BACKEND_DIR"
export SIQ_FRONTEND_ROOT="$FRONT_DIR"
export SIQ_PDF2MD_ROOT="$PDF2MD_DIR"
export SIQ_DOCUMENT_PARSER_ROOT="$DOCUMENT_PARSER_DIR"
export SIQ_PDF2MD_DATA_DIR="${SIQ_PDF2MD_DATA_DIR:-$SIQ_PROJECT_ROOT/data/pdf-parser}"
export SIQ_DOCUMENT_PARSE_DATA_DIR="${SIQ_DOCUMENT_PARSE_DATA_DIR:-$SIQ_PROJECT_ROOT/data/document-parser}"
export SIQ_REPORT_FINDER_ROOT="$REPORT_FINDER_DIR"
export SIQ_MARKET_REPORT_FINDER_ROOT="$MARKET_REPORT_FINDER_DIR"
export SIQ_MARKET_REPORT_RULES_ROOT="$MARKET_REPORT_RULES_DIR"
export SIQ_MARKET_REPORT_DOWNLOADS_ROOT="${SIQ_MARKET_REPORT_DOWNLOADS_ROOT:-$SIQ_PROJECT_ROOT/data/market-report-finder/downloads}"
export SIQ_REPORT_DOWNLOADS_ROOT="${SIQ_REPORT_DOWNLOADS_ROOT:-$SIQ_MARKET_REPORT_DOWNLOADS_ROOT}"
export SIQ_DB_ROOT="${SIQ_DB_ROOT:-$SIQ_PROJECT_ROOT/db}"
export SIQ_HERMES_HOME="${SIQ_HERMES_HOME:-$SIQ_PROJECT_ROOT/data/hermes/home}"
export SIQ_HERMES_PROFILES_ROOT="${SIQ_HERMES_PROFILES_ROOT:-$SIQ_HERMES_HOME/profiles}"
export SIQ_MINERU_VENV="${SIQ_MINERU_VENV:-$SIQ_PROJECT_ROOT/runtimes/mineru-native}"
export SIQ_BACKEND_URL="${SIQ_BACKEND_URL:-http://127.0.0.1:$BACKEND_PORT}"
export SIQ_REPORT_FINDER_URL="${SIQ_REPORT_FINDER_URL:-http://127.0.0.1:$REPORT_FINDER_PORT}"
export SIQ_MARKET_REPORT_FINDER_URL="${SIQ_MARKET_REPORT_FINDER_URL:-http://127.0.0.1:$MARKET_REPORT_FINDER_PORT}"
export SIQ_MARKET_REPORT_RULES_URL="${SIQ_MARKET_REPORT_RULES_URL:-http://127.0.0.1:$MARKET_REPORT_RULES_PORT}"
export SIQ_PDFAPI_URL="${SIQ_PDFAPI_URL:-http://127.0.0.1:$PDF2MD_PORT}"
export SIQ_PDF2MD_API_BASE="${SIQ_PDF2MD_API_BASE:-http://127.0.0.1:$PDF2MD_PORT}"
export SIQ_DOCUMENT_PARSER_API_BASE="${SIQ_DOCUMENT_PARSER_API_BASE:-http://127.0.0.1:$DOCUMENT_PARSER_PORT}"
export SIQ_REPORT_FINDER_BASE="${SIQ_REPORT_FINDER_BASE:-http://127.0.0.1:$REPORT_FINDER_PORT}"
export SIQ_MARKET_REPORT_FINDER_BASE="${SIQ_MARKET_REPORT_FINDER_BASE:-http://127.0.0.1:$MARKET_REPORT_FINDER_PORT}"
export SIQ_MARKET_REPORT_RULES_BASE="${SIQ_MARKET_REPORT_RULES_BASE:-http://127.0.0.1:$MARKET_REPORT_RULES_PORT}"
export SIQ_REPORT_FINDER_HEALTH_URL="${SIQ_REPORT_FINDER_HEALTH_URL:-http://127.0.0.1:$REPORT_FINDER_PORT/health}"
export SIQ_MARKET_REPORT_FINDER_HEALTH_URL="${SIQ_MARKET_REPORT_FINDER_HEALTH_URL:-http://127.0.0.1:$MARKET_REPORT_FINDER_PORT/health}"
export SIQ_MARKET_REPORT_RULES_HEALTH_URL="${SIQ_MARKET_REPORT_RULES_HEALTH_URL:-http://127.0.0.1:$MARKET_REPORT_RULES_PORT/healthz}"
export SIQ_PDF2MD_HEALTH_URL="${SIQ_PDF2MD_HEALTH_URL:-http://127.0.0.1:$PDF2MD_PORT/api/health}"
export SIQ_DOCUMENT_PARSER_HEALTH_URL="${SIQ_DOCUMENT_PARSER_HEALTH_URL:-http://127.0.0.1:$DOCUMENT_PARSER_PORT/api/health}"
export SIQ_PUBLIC_ORIGIN="${SIQ_PUBLIC_ORIGIN:-http://localhost:$FRONTEND_PORT}"
export SIQ_HERMES_ASSISTANT_PORT="$HERMES_ASSISTANT_PORT"
export SIQ_HERMES_FACTCHECKER_PORT="$HERMES_FACTCHECKER_PORT"
export SIQ_HERMES_TRACKING_PORT="$HERMES_TRACKING_PORT"
export SIQ_HERMES_ANALYSIS_PORT="$HERMES_ANALYSIS_PORT"
export SIQ_HERMES_LEGAL_PORT="$HERMES_LEGAL_PORT"
export SIQ_VECTOR_INGEST_ROOT="$VECTOR_INGEST_DIR"
export SIQ_VECTOR_INGEST_PORT="$VECTOR_INGEST_PORT"
export SIQ_VECTOR_INGEST_URL="${SIQ_VECTOR_INGEST_URL:-http://127.0.0.1:$VECTOR_INGEST_PORT}"
export SIQ_VECTOR_INGEST_HEALTH_URL="${SIQ_VECTOR_INGEST_HEALTH_URL:-http://127.0.0.1:$VECTOR_INGEST_PORT/}"

# ---------- 工具函数 ----------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${CYAN}[$(date +%H:%M:%S)]${NC} $*"; }
ok()   { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
die()  { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

cleanup() {
    log "正在停止所有子进程 (PID: ${pids[*]})..."
    for pid in "${pids[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null
    log "已退出。"
}
pids=()
trap cleanup EXIT SIGINT SIGTERM

wait_for_http() {
    local url=$1 name=$2 timeout=${3:-30} header=${4:-}
    local i=0
    while ! curl -sf ${header:+-H "$header"} "$url" >/dev/null 2>&1; do
        ((i++)) || true
        if (( i >= timeout )); then
            die "$name 在 ${timeout}s 内未就绪 ($url)"
        fi
        sleep 1
    done
}

port_is_free() {
    local port=$1
    ! ss -ltn "sport = :$port" | tail -n +2 | grep -q .
}

require_free_port() {
    local port=$1 name=$2
    if ! port_is_free "$port"; then
        ss -ltnp "sport = :$port" || true
        die "$name 端口 $port 已被占用。请释放端口或通过 SIQ_*_PORT 覆盖。"
    fi
}

# ---------- 依赖检查 ----------
for cmd in uv node npm; do
    command -v "$cmd" &>/dev/null || die "缺少命令: $cmd"
done

require_free_port "$REPORT_FINDER_PORT" "PDF 下载服务"
if [[ "$START_MARKET_REPORT_FINDER" != "0" ]]; then
    require_free_port "$MARKET_REPORT_FINDER_PORT" "美股/港股下载服务"
fi
if [[ "$START_MARKET_REPORT_RULES" != "0" ]]; then
    require_free_port "$MARKET_REPORT_RULES_PORT" "美股/港股规则服务"
fi
require_free_port "$BACKEND_PORT" "FastAPI 后端"
require_free_port "$PDF2MD_PORT" "PDF 解析服务"
require_free_port "$DOCUMENT_PARSER_PORT" "文档解析服务"
require_free_port "$FRONTEND_PORT" "Vite 前端"
if [[ "$START_HERMES_GATEWAYS" != "0" ]]; then
    command -v hermes &>/dev/null || die "缺少命令: hermes。请安装 Hermes 或设置 SIQ_START_HERMES_GATEWAYS=0 跳过智能体网关。"
    require_free_port "$HERMES_ASSISTANT_PORT" "Hermes 助手"
    require_free_port "$HERMES_FACTCHECKER_PORT" "Hermes 核查"
    require_free_port "$HERMES_TRACKING_PORT" "Hermes 跟踪"
    require_free_port "$HERMES_ANALYSIS_PORT" "Hermes 分析"
    require_free_port "$HERMES_LEGAL_PORT" "Hermes 法务"
fi
if [[ "$START_VECTOR_INGEST" != "0" ]]; then
    command -v python3 &>/dev/null || die "缺少命令: python3。无法启动 Milvus 向量入库控制台。"
    require_free_port "$VECTOR_INGEST_PORT" "Milvus 向量入库控制台"
fi

start_hermes_gateway() {
    local profile=$1 label=$2
    local profile_dir
    profile_dir="$("$SIQ_PROJECT_ROOT/scripts/hermes/profile_dir.sh" "$profile")"
    log "启动 $label Hermes 网关 ($profile_dir)..."
    (
        cd "$profile_dir"
        export HERMES_HOME="$profile_dir"
        exec hermes gateway run --replace --accept-hooks
    ) &
    pids+=($!)
}

if [[ "$START_HERMES_GATEWAYS" != "0" ]]; then
    start_hermes_gateway "siq_assistant" "通用助手"
    start_hermes_gateway "siq_analysis" "智能分析"
    start_hermes_gateway "siq_factchecker" "事实核查"
    start_hermes_gateway "siq_tracking" "持续跟踪"
    start_hermes_gateway "siq_legal" "法务合规"
fi

if [[ "$START_VECTOR_INGEST" != "0" ]]; then
    log "启动 Milvus 向量入库控制台 (端口 $VECTOR_INGEST_PORT)..."
    (
        cd "$VECTOR_INGEST_DIR"
        GRADIO_SERVER_PORT="$VECTOR_INGEST_PORT" GRADIO_SERVER_PORT_MAX="$VECTOR_INGEST_PORT" exec python3 ingest_final.py
    ) &
    pids+=($!)
fi

# ---------- 启动统一公告下载服务 ----------
log "启动统一公告下载服务 (端口 $REPORT_FINDER_PORT)..."
(
    cd "$MARKET_REPORT_FINDER_DIR"
    uv sync
    MARKET_REPORT_DOWNLOAD_DIR="$SIQ_REPORT_DOWNLOADS_ROOT" uv run python -m uvicorn market_report_finder_service.app:app --host 127.0.0.1 --port "$REPORT_FINDER_PORT"
) &
pids+=($!)

if [[ "$START_MARKET_REPORT_FINDER" != "0" ]]; then
    # ---------- 启动备用市场下载服务 ----------
    log "启动备用市场下载服务 (端口 $MARKET_REPORT_FINDER_PORT)..."
    (
        cd "$MARKET_REPORT_FINDER_DIR"
        uv sync
        MARKET_REPORT_DOWNLOAD_DIR="$SIQ_MARKET_REPORT_DOWNLOADS_ROOT" uv run python -m uvicorn market_report_finder_service.app:app --host 127.0.0.1 --port "$MARKET_REPORT_FINDER_PORT"
    ) &
    pids+=($!)
fi

if [[ "$START_MARKET_REPORT_RULES" != "0" ]]; then
    # ---------- 启动美股/港股规则服务 ----------
    log "启动美股/港股规则服务 (端口 $MARKET_REPORT_RULES_PORT)..."
    (
        cd "$MARKET_REPORT_RULES_DIR"
        uv sync
        uv run python -m uvicorn market_report_rules_service.app:app --host 127.0.0.1 --port "$MARKET_REPORT_RULES_PORT"
    ) &
    pids+=($!)
fi

# ---------- 启动后端 ----------
log "启动 FastAPI 后端 (端口 $BACKEND_PORT)..."
(
    cd "$BACKEND_DIR"
    uv sync
    uv run python -m uvicorn main:app --reload --host 0.0.0.0 --port "$BACKEND_PORT"
) &
pids+=($!)

# ---------- 启动 PDF 解析服务 ----------
log "启动 PDF 解析服务 (端口 $PDF2MD_PORT)..."
(
    cd "$PDF2MD_DIR"
    PORT="$PDF2MD_PORT" ./run.sh
) &
pids+=($!)

# ---------- 启动通用文档解析服务 ----------
log "启动通用文档解析服务 (端口 $DOCUMENT_PARSER_PORT)..."
(
    cd "$DOCUMENT_PARSER_DIR"
    PORT="$DOCUMENT_PARSER_PORT" ./run.sh
) &
pids+=($!)

# ---------- 启动前端 ----------
log "启动 Vite 前端 (端口 $FRONTEND_PORT)..."
(
    cd "$FRONT_DIR"
    npm install
    npm run dev -- --host 0.0.0.0 --port "$FRONTEND_PORT"
) &
pids+=($!)

# ---------- 健康检查 ----------
log "等待统一公告下载服务就绪..."
wait_for_http "http://localhost:$REPORT_FINDER_PORT/health" "统一公告下载服务" 30
ok "统一公告下载服务已就绪  -> http://localhost:$REPORT_FINDER_PORT/health"

if [[ "$START_MARKET_REPORT_FINDER" != "0" ]]; then
    log "等待备用市场下载服务就绪..."
    wait_for_http "http://localhost:$MARKET_REPORT_FINDER_PORT/health" "备用市场下载服务" 30
    ok "备用市场下载服务已就绪  -> http://localhost:$MARKET_REPORT_FINDER_PORT/health"
else
    warn "已跳过备用市场下载服务；主入口 18000 已支持 CN/HK/US。"
fi

if [[ "$START_MARKET_REPORT_RULES" != "0" ]]; then
    log "等待美股/港股规则服务就绪..."
    wait_for_http "http://localhost:$MARKET_REPORT_RULES_PORT/healthz" "美股/港股规则服务" 30
    ok "美股/港股规则服务已就绪  -> http://localhost:$MARKET_REPORT_RULES_PORT/healthz"
else
    warn "已跳过美股/港股规则服务；需要时设置 SIQ_START_MARKET_REPORT_RULES=1。"
fi

log "等待后端就绪..."
wait_for_http "http://localhost:$BACKEND_PORT/health" "后端" 30
ok "后端已就绪  -> http://localhost:$BACKEND_PORT/health"

log "等待 PDF 解析服务就绪..."
PDF2MD_HEALTH_HEADER=""
if [[ -n "${PDF2MD_ACCESS_TOKEN:-}" ]]; then
    PDF2MD_HEALTH_HEADER="X-PDF2MD-Token: $PDF2MD_ACCESS_TOKEN"
fi
wait_for_http "http://localhost:$PDF2MD_PORT/api/health" "PDF 解析服务" 30 "$PDF2MD_HEALTH_HEADER"
ok "PDF 解析服务已就绪  -> http://localhost:$PDF2MD_PORT/api/health"

log "等待通用文档解析服务就绪..."
DOCUMENT_PARSER_HEALTH_HEADER=""
if [[ -n "${SIQ_DOCUMENT_PARSER_ACCESS_TOKEN:-}" ]]; then
    DOCUMENT_PARSER_HEALTH_HEADER="X-Document-Parser-Token: $SIQ_DOCUMENT_PARSER_ACCESS_TOKEN"
fi
wait_for_http "http://localhost:$DOCUMENT_PARSER_PORT/api/health" "通用文档解析服务" 30 "$DOCUMENT_PARSER_HEALTH_HEADER"
ok "通用文档解析服务已就绪  -> http://localhost:$DOCUMENT_PARSER_PORT/api/health"

if [[ "$START_HERMES_GATEWAYS" != "0" ]]; then
    log "等待 Hermes 智能体网关就绪..."
    wait_for_http "http://localhost:$HERMES_ASSISTANT_PORT/health" "Hermes 通用助手" 45
    wait_for_http "http://localhost:$HERMES_ANALYSIS_PORT/health" "Hermes 智能分析" 45
    wait_for_http "http://localhost:$HERMES_FACTCHECKER_PORT/health" "Hermes 事实核查" 45
    wait_for_http "http://localhost:$HERMES_TRACKING_PORT/health" "Hermes 持续跟踪" 45
    wait_for_http "http://localhost:$HERMES_LEGAL_PORT/health" "Hermes 法务合规" 45
    ok "Hermes 智能体网关已就绪"
else
    warn "已跳过 Hermes 智能体网关启动，相关智能体聊天将不可用。"
fi

if [[ "$START_VECTOR_INGEST" != "0" ]]; then
    log "等待 Milvus 向量入库控制台就绪..."
    wait_for_http "http://localhost:$VECTOR_INGEST_PORT" "Milvus 向量入库控制台" 45
    ok "Milvus 向量入库控制台已就绪  -> http://localhost:$VECTOR_INGEST_PORT"
else
    warn "已跳过 Milvus 向量入库控制台；需要时设置 SIQ_START_VECTOR_INGEST=1。"
fi

log "等待前端就绪..."
wait_for_http "http://localhost:$FRONTEND_PORT" "前端" 30
ok "前端已就绪  -> http://localhost:$FRONTEND_PORT"

# ---------- 快速验证 ----------
echo ""
log "--- API 快速验证 ---"
curl -s "http://localhost:$REPORT_FINDER_PORT/health"
echo ""
if [[ "$START_MARKET_REPORT_FINDER" != "0" ]]; then
    curl -s "http://localhost:$MARKET_REPORT_FINDER_PORT/health"
    echo ""
fi
if [[ "$START_MARKET_REPORT_RULES" != "0" ]]; then
    curl -s "http://localhost:$MARKET_REPORT_RULES_PORT/healthz"
    echo ""
fi
curl -s "http://localhost:$BACKEND_PORT/health"
echo ""
curl -s ${PDF2MD_HEALTH_HEADER:+-H "$PDF2MD_HEALTH_HEADER"} "http://localhost:$PDF2MD_PORT/api/health"
echo ""
curl -s ${DOCUMENT_PARSER_HEALTH_HEADER:+-H "$DOCUMENT_PARSER_HEALTH_HEADER"} "http://localhost:$DOCUMENT_PARSER_PORT/api/health"
echo ""
curl -s "http://localhost:$BACKEND_PORT/api/wiki/companies/list" | head -c 200
echo ""
echo ""
ok "全部启动完成！浏览器打开: http://localhost:$FRONTEND_PORT"
echo "按 Ctrl+C 停止所有服务"
echo ""

# 保持脚本运行
wait
