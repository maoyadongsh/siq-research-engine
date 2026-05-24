#!/usr/bin/env bash
set -euo pipefail

# ============================================================
#  一键启动：PDF 下载服务 (8000) + FastAPI 后端 (10081) + Vite 前端 (5173)
# ============================================================

WIKI_ROOT="${WIKI_ROOT:-/home/maoyd/wiki}"
export WIKI_ROOT

BACKEND_DIR="/home/maoyd/finsight/backend"
FRONT_DIR="/home/maoyd/finsight/finall_all_front_0516/front"
REPORT_FINDER_DIR="/home/maoyd/report-finder-service"
BACKEND_PORT=10081
FRONTEND_PORT=5173
REPORT_FINDER_PORT=8000

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
    local url=$1 name=$2 timeout=${3:-30}
    local i=0
    while ! curl -sf "$url" >/dev/null 2>&1; do
        ((i++)) || true
        if (( i >= timeout )); then
            die "$name 在 ${timeout}s 内未就绪 ($url)"
        fi
        sleep 1
    done
}

# ---------- 依赖检查 ----------
for cmd in uv node npm; do
    command -v "$cmd" &>/dev/null || die "缺少命令: $cmd"
done

# ---------- 启动 PDF 下载服务 ----------
log "启动 PDF 下载服务 (端口 $REPORT_FINDER_PORT)..."
(
    cd "$REPORT_FINDER_DIR"
    .venv/bin/python -m uvicorn report_finder_service.app:app --host 127.0.0.1 --port "$REPORT_FINDER_PORT"
) &
pids+=($!)

# ---------- 启动后端 ----------
log "启动 FastAPI 后端 (端口 $BACKEND_PORT)..."
(
    cd "$BACKEND_DIR"
    uv sync
    uv run uvicorn main:app --reload --host 0.0.0.0 --port "$BACKEND_PORT"
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
log "等待 PDF 下载服务就绪..."
wait_for_http "http://localhost:$REPORT_FINDER_PORT/health" "PDF 下载服务" 30
ok "PDF 下载服务已就绪  -> http://localhost:$REPORT_FINDER_PORT/health"

log "等待后端就绪..."
wait_for_http "http://localhost:$BACKEND_PORT/health" "后端" 30
ok "后端已就绪  -> http://localhost:$BACKEND_PORT/health"

log "等待前端就绪..."
wait_for_http "http://localhost:$FRONTEND_PORT" "前端" 30
ok "前端已就绪  -> http://localhost:$FRONTEND_PORT"

# ---------- 快速验证 ----------
echo ""
log "--- API 快速验证 ---"
curl -s "http://localhost:$REPORT_FINDER_PORT/health"
echo ""
curl -s "http://localhost:$BACKEND_PORT/health"
echo ""
curl -s "http://localhost:$BACKEND_PORT/api/wiki/companies/list" | head -c 200
echo ""
echo ""
ok "全部启动完成！浏览器打开: http://localhost:$FRONTEND_PORT"
echo "按 Ctrl+C 停止所有服务"
echo ""

# 保持脚本运行
wait
