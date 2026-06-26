#!/bin/bash
# ============================================================
# 一键启动本地 PDF 解析服务栈
#   - 8002: VLLM (MinerU2.5-Pro-2604-1.2B)
#   - 8003: MinerU API
#   - 15000: SIQ PDF parser (Flask)
#
# 用法:
#   ./start_pdf2md_services.sh start    # 启动所有服务
#   ./start_pdf2md_services.sh stop     # 停止所有服务
#   ./start_pdf2md_services.sh restart  # 重启所有服务
#   ./start_pdf2md_services.sh status   # 查看服务状态
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIQ_PROJECT_ROOT="${SIQ_PROJECT_ROOT:-$(cd "${SCRIPT_DIR}/../../.." && pwd)}"

# --------------------------- 配置 ---------------------------
VLLM_PORT="${MINERU_VLLM_PORT:-8002}"
MINERU_PORT="${MINERU_API_PORT:-8003}"
WEB_PORT="${SIQ_PDF2MD_PORT:-15000}"

VLLM_LOG="/tmp/vllm_mineru.log"
MINERU_LOG="/tmp/mineru_api.log"
WEB_LOG="${SIQ_PDF2MD_LOG:-/tmp/siq_pdf_parser.log}"

VLLM_PID_FILE="/tmp/vllm_service.pid"
MINERU_PID_FILE="/tmp/mineru_api.pid"
WEB_PID_FILE="${SIQ_PDF2MD_PID_FILE:-/tmp/siq_pdf_parser.pid}"

VLLM_MODEL_PATH="${MINERU_VLLM_MODEL_DIR:-/home/maoyd/models/mineru-modelscope/MinerU2.5-Pro-2604-1.2B}"
VENV_MINERU="${SIQ_MINERU_VENV:-/home/maoyd/.venvs/mineru_native}"
WEB_DIR="${SIQ_PDF2MD_ROOT:-${SIQ_PROJECT_ROOT}/apps/pdf-parser}"
WEB_DATA_DIR="${SIQ_PDF2MD_DATA_DIR:-${SIQ_PROJECT_ROOT}/data/pdf-parser}"
CONDA_ENV="${MINERU_VLLM_CONDA_ENV:-mineru_vllm_clean}"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
WEB_SERVICE_NAME="${SIQ_PDF2MD_SERVICE_NAME:-siq-pdf-parser.service}"
WEB_SERVICE_PATH="${SYSTEMD_USER_DIR}/${WEB_SERVICE_NAME}"

# --------------------------- 工具函数 ---------------------------
log_info() { echo -e "\033[32m[INFO]\033[0m  $*"; }
log_warn() { echo -e "\033[33m[WARN]\033[0m  $*"; }
log_err()  { echo -e "\033[31m[ERROR]\033[0m $*"; }

systemctl_user() {
    systemctl --user "$@"
}

check_port() {
    local port=$1
    ss -tlnp 2>/dev/null | grep -q ":${port} " || \
    netstat -tlnp 2>/dev/null | grep -q ":${port} " || \
    lsof -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
}

port_pids() {
    local port=$1
    ss -tlnp 2>/dev/null | grep ":${port} " | grep -oP 'pid=\K[0-9]+' | sort -u || true
}

show_port_owner() {
    local port=$1
    local pids
    pids=$(port_pids "${port}" | tr '\n' ' ')
    if [[ -n "${pids}" ]]; then
        ps -fp ${pids} || true
    fi
}

stop_port_processes() {
    local port=$1
    local name=$2
    local pids
    pids=$(port_pids "${port}")
    if [[ -z "${pids}" ]]; then
        return 0
    fi

    for pid in ${pids}; do
        if kill -0 "${pid}" >/dev/null 2>&1; then
            log_warn "停止 ${name} 占用端口 ${port} 的进程 (PID: ${pid})"
            kill "${pid}" 2>/dev/null || true
        fi
    done

    sleep 1
    pids=$(port_pids "${port}")
    if [[ -n "${pids}" ]]; then
        for pid in ${pids}; do
            if kill -0 "${pid}" >/dev/null 2>&1; then
                log_warn "强制终止 ${name} 占用端口 ${port} 的残余进程 (PID: ${pid})"
                kill -9 "${pid}" 2>/dev/null || true
            fi
        done
    fi
}

web_health_ok() {
    curl -fs "http://127.0.0.1:${WEB_PORT}/api/health" 2>/dev/null | grep -q '"flask"[[:space:]]*:[[:space:]]*true'
}

mineru_health_ok() {
    curl -fs "http://127.0.0.1:${MINERU_PORT}/health" 2>/dev/null | grep -q '"status"[[:space:]]*:[[:space:]]*"healthy"'
}

vllm_health_ok() {
    curl -fs "http://127.0.0.1:${VLLM_PORT}/v1/models" 2>/dev/null | grep -q '"object"[[:space:]]*:[[:space:]]*"list"'
}

ensure_web_service_unit() {
    mkdir -p "${SYSTEMD_USER_DIR}"
    local tmp_unit
    tmp_unit=$(mktemp)

    cat > "${tmp_unit}" <<EOF
[Unit]
Description=SIQ PDF parser Flask service
After=network.target

[Service]
Type=simple
WorkingDirectory=${WEB_DIR}
Environment=PORT=${WEB_PORT}
Environment=HOST=0.0.0.0
Environment=TASK_RETENTION_HOURS=0
Environment=SIQ_PROJECT_ROOT=${SIQ_PROJECT_ROOT}
Environment=SIQ_PDF2MD_ROOT=${WEB_DIR}
Environment=SIQ_PDF2MD_DATA_DIR=${WEB_DATA_DIR}
Environment=MINERU_API_URL=http://127.0.0.1:${MINERU_PORT}
Environment=VLM_API_URL=http://127.0.0.1:${VLLM_PORT}
Environment=PYTHONUNBUFFERED=1
ExecStart=${VENV_MINERU}/bin/python ${WEB_DIR}/app.py
Restart=always
RestartSec=1
KillMode=mixed
TimeoutStopSec=20
StandardOutput=append:${WEB_LOG}
StandardError=append:${WEB_LOG}

[Install]
WantedBy=default.target
EOF

    if [[ ! -f "${WEB_SERVICE_PATH}" ]] || ! cmp -s "${tmp_unit}" "${WEB_SERVICE_PATH}"; then
        install -m 0644 "${tmp_unit}" "${WEB_SERVICE_PATH}"
        systemctl_user daemon-reload
        log_info "已安装/更新 Web 用户服务: ${WEB_SERVICE_NAME}"
    fi

    rm -f "${tmp_unit}"
}

wait_for_url() {
    local url=$1
    local name=$2
    local max_wait=${3:-120}
    local interval=${4:-2}
    local waited=0

    log_info "等待 ${name} 就绪 (最长 ${max_wait}s)..."
    while true; do
        if curl -sf "${url}" >/dev/null 2>&1; then
            log_info "${name} 已就绪"
            return 0
        fi
        if (( waited >= max_wait )); then
            log_err "${name} 在 ${max_wait}s 内未就绪，请检查日志: ${5:-}"
            return 1
        fi
        sleep "${interval}"
        ((waited+=interval))
    done
}

kill_by_pidfile() {
    local pid_file=$1
    local name=$2
    if [[ -f "${pid_file}" ]]; then
        local pid
        pid=$(cat "${pid_file}")
        if kill -0 "${pid}" >/dev/null 2>&1; then
            log_info "停止 ${name} (PID: ${pid})"
            kill "${pid}" 2>/dev/null || true
            sleep 1
            # 强制终止如果还在
            if kill -0 "${pid}" >/dev/null 2>&1; then
                kill -9 "${pid}" 2>/dev/null || true
            fi
        fi
        rm -f "${pid_file}"
    fi
}

# --------------------------- 启动函数 ---------------------------
start_vllm() {
    if check_port "${VLLM_PORT}"; then
        log_warn "VLLM (port ${VLLM_PORT}) 已在运行"
        return 0
    fi

    log_info "启动 VLLM 服务 (port ${VLLM_PORT})..."
    nohup conda run -n "${CONDA_ENV}" --no-capture-output \
        vllm serve "${VLLM_MODEL_PATH}" \
        --served-model-name MinerU2.5-Pro-2604-1.2B \
        --trust-remote-code \
        --host 127.0.0.1 \
        --port "${VLLM_PORT}" \
        --gpu-memory-utilization 0.12 \
        --max-model-len 4096 \
        > "${VLLM_LOG}" 2>&1 &

    echo $! > "${VLLM_PID_FILE}"
    wait_for_url "http://127.0.0.1:${VLLM_PORT}/v1/models" "VLLM" 120 || { log_err "VLLM 启动失败，查看日志: ${VLLM_LOG}"; return 1; }
}

start_mineru_api() {
    if check_port "${MINERU_PORT}"; then
        log_warn "MinerU API (port ${MINERU_PORT}) 已在运行"
        return 0
    fi

    log_info "启动 MinerU API (port ${MINERU_PORT})..."
    # 使用本地模型，避免联网下载
    export MINERU_MODEL_SOURCE="local"
    nohup "${VENV_MINERU}/bin/mineru-api" \
        --host 127.0.0.1 \
        --port "${MINERU_PORT}" \
        > "${MINERU_LOG}" 2>&1 &

    echo $! > "${MINERU_PID_FILE}"
    wait_for_url "http://127.0.0.1:${MINERU_PORT}/health" "MinerU API" 120 || { log_err "MinerU API 启动失败，查看日志: ${MINERU_LOG}"; return 1; }
}

start_web() {
    ensure_web_service_unit

    if web_health_ok && systemctl_user is-active --quiet "${WEB_SERVICE_NAME}"; then
        log_warn "Web 应用 (port ${WEB_PORT}) 已由 systemd 运行"
        return 0
    fi

    if web_health_ok; then
        log_warn "检测到已有可用 Web 进程，切换为 systemd 托管以提升稳定性"
        stop_port_processes "${WEB_PORT}" "Web 应用"
    elif check_port "${WEB_PORT}"; then
        log_err "端口 ${WEB_PORT} 已被其他进程占用，无法启动 Web 应用"
        show_port_owner "${WEB_PORT}"
        return 1
    fi

    log_info "启动 Web 应用 (port ${WEB_PORT})..."
    systemctl_user enable --now "${WEB_SERVICE_NAME}" >/dev/null
    wait_for_url "http://127.0.0.1:${WEB_PORT}/api/health" "Web 应用" 60 || { log_err "Web 应用启动失败，查看日志: ${WEB_LOG}"; return 1; }

    local main_pid
    main_pid=$(systemctl_user show "${WEB_SERVICE_NAME}" --property=MainPID --value 2>/dev/null || true)
    if [[ -n "${main_pid}" && "${main_pid}" != "0" ]]; then
        echo "${main_pid}" > "${WEB_PID_FILE}"
    fi
}

# --------------------------- 停止函数 ---------------------------
stop_services() {
    log_info "停止所有服务..."
    if [[ -f "${WEB_SERVICE_PATH}" ]]; then
        systemctl_user stop "${WEB_SERVICE_NAME}" >/dev/null 2>&1 || true
    fi
    kill_by_pidfile "${WEB_PID_FILE}" "Web 应用"
    kill_by_pidfile "${MINERU_PID_FILE}" "MinerU API"
    kill_by_pidfile "${VLLM_PID_FILE}" "VLLM"

    # 兜底：按端口查找并终止残余进程
    for port in "${VLLM_PORT}" "${MINERU_PORT}" "${WEB_PORT}"; do
        stop_port_processes "${port}" "服务"
    done

    log_info "所有服务已停止"
}

# --------------------------- 状态函数 ---------------------------
show_status() {
    echo ""
    echo "======================== 服务状态 ========================"
    if vllm_health_ok; then
        echo -e "  VLLM\t\033[32m运行中\033[0m (port ${VLLM_PORT})"
    else
        echo -e "  VLLM\t\033[31m未运行\033[0m (port ${VLLM_PORT})"
    fi

    if mineru_health_ok; then
        echo -e "  MinerU API\t\033[32m运行中\033[0m (port ${MINERU_PORT})"
    else
        echo -e "  MinerU API\t\033[31m未运行\033[0m (port ${MINERU_PORT})"
    fi

    if web_health_ok; then
        local web_state="运行中"
        local web_note="manual"
        if systemctl_user is-active --quiet "${WEB_SERVICE_NAME}" 2>/dev/null; then
            web_note="systemd"
        fi
        echo -e "  Web 应用\t\033[32m${web_state}\033[0m (port ${WEB_PORT}, ${web_note})"
    else
        echo -e "  Web 应用\t\033[31m未运行\033[0m (port ${WEB_PORT})"
    fi
    echo "=========================================================="
    echo ""
    echo "日志路径:"
    echo "  VLLM:      ${VLLM_LOG}"
    echo "  MinerU API: ${MINERU_LOG}"
    echo "  Web 应用:   ${WEB_LOG}"
    if [[ -f "${WEB_SERVICE_PATH}" ]]; then
        echo "  Web systemd: ${WEB_SERVICE_NAME}"
    fi
    echo ""
}

# --------------------------- 启动全部 ---------------------------
start_all() {
    log_info "开始启动服务栈..."

    # 1. 并行启动 8002 和 8003
    start_vllm &
    local vllm_pid=$!
    start_mineru_api &
    local mineru_pid=$!

    # 等待两个基础服务都就绪
    wait "${vllm_pid}" || { log_err "VLLM 启动失败"; exit 1; }
    wait "${mineru_pid}" || { log_err "MinerU API 启动失败"; exit 1; }

    # 2. 启动 Web 应用（依赖上面两个）
    start_web || exit 1

    echo ""
    log_info "所有服务启动完成！"
    show_status
    echo "访问地址:"
    echo "  Web 界面:   http://127.0.0.1:${WEB_PORT}"
    echo "  VLLM API:   http://127.0.0.1:${VLLM_PORT}/v1/models"
    echo "  MinerU API: http://127.0.0.1:${MINERU_PORT}/health"
}

# --------------------------- 主入口 ---------------------------
case "${1:-start}" in
    start)
        start_all
        ;;
    stop)
        stop_services
        ;;
    restart)
        stop_services
        sleep 2
        start_all
        ;;
    status)
        show_status
        ;;
    *)
        echo "用法: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac
