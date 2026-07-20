#!/usr/bin/env bash
set -Eeuo pipefail

# Single-file manager for the local Fun-ASR-Nano vLLM service.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ACTION="${1:-start}"
if [[ $# -gt 0 ]]; then
  shift
fi

PYTHON_BIN="${PYTHON_BIN:-/home/maoyd/miniconda3/envs/funasr-vllm/bin/python}"
FUNASR_APP_DIR="${FUNASR_APP_DIR:-/home/maoyd/services/FunASR/examples/industrial_data_pretraining/fun_asr_nano}"
SERVER_SCRIPT="${SERVER_SCRIPT:-$FUNASR_APP_DIR/serve_vllm.py}"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8899}"
CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"

MODEL="${MODEL:-FunAudioLLM/Fun-ASR-Nano-2512}"
HUB="${HUB:-ms}"
DEVICE="${DEVICE:-cpu}"
DTYPE="${DTYPE:-bf16}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-4096}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.05}"
ENFORCE_EAGER="${ENFORCE_EAGER:-1}"
VAD_MODEL="${VAD_MODEL:-fsmn-vad}"
SPK_MODEL="${SPK_MODEL-iic/speech_eres2netv2_sv_zh-cn-16k-common}"

MODELSCOPE_CACHE="${MODELSCOPE_CACHE:-/home/maoyd/models/modelscope}"
HF_HOME="${HF_HOME:-/home/maoyd/models/huggingface}"
TORCH_HOME="${TORCH_HOME:-/home/maoyd/models/torch}"
XDG_CACHE_HOME="${XDG_CACHE_HOME:-/home/maoyd/models/cache}"
PYTHONNOUSERSITE="${PYTHONNOUSERSITE:-1}"
VLLM_NO_USAGE_STATS="${VLLM_NO_USAGE_STATS:-1}"
VLLM_WORKER_MULTIPROC_METHOD="${VLLM_WORKER_MULTIPROC_METHOD:-spawn}"
VLLM_ENABLE_V1_MULTIPROCESSING="${VLLM_ENABLE_V1_MULTIPROCESSING:-0}"

LOG_DIR="${LOG_DIR:-/home/maoyd/logs}"
PID_FILE="${PID_FILE:-$SCRIPT_DIR/funasr_vllm.pid}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/funasr_vllm.log}"
LOG_TAIL="${LOG_TAIL:-100}"
STARTUP_TIMEOUT_SECONDS="${STARTUP_TIMEOUT_SECONDS:-180}"
STOP_TIMEOUT_SECONDS="${STOP_TIMEOUT_SECONDS:-30}"
WAIT_FOR_READY="${WAIT_FOR_READY:-1}"
DEFAULT_TEST_AUDIO="${DEFAULT_TEST_AUDIO:-/home/maoyd/services/FunASR/runtime/funasr_api/asr_example.wav}"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'USAGE'
Usage:
  start_funasr_vllm.sh start [serve_vllm.py args...]
  start_funasr_vllm.sh stop|restart|status|logs|foreground
  start_funasr_vllm.sh test [audio-file] [language]

Environment overrides:
  PYTHON_BIN, FUNASR_APP_DIR, SERVER_SCRIPT
  HOST=0.0.0.0, PORT=8899, CUDA_VISIBLE_DEVICES=0
  MODEL=FunAudioLLM/Fun-ASR-Nano-2512, HUB=ms, DEVICE=cpu, DTYPE=bf16
  MAX_MODEL_LEN=4096, GPU_MEMORY_UTILIZATION=0.05, ENFORCE_EAGER=1
  VAD_MODEL=fsmn-vad, SPK_MODEL=iic/speech_eres2netv2_sv_zh-cn-16k-common
  LOG_FILE, PID_FILE, STARTUP_TIMEOUT_SECONDS=180, WAIT_FOR_READY=1

Set SPK_MODEL='' to disable speaker diarization. The default action is start.
USAGE
}

require_tools() {
  command -v curl >/dev/null 2>&1 || die "curl is required"
  command -v setsid >/dev/null 2>&1 || die "setsid is required"
  [[ -x "$PYTHON_BIN" ]] || die "Python is not executable: $PYTHON_BIN"
  [[ -f "$SERVER_SCRIPT" ]] || die "FunASR server script not found: $SERVER_SCRIPT"
}

read_pid() {
  [[ -f "$PID_FILE" ]] || return 1
  tr -d '[:space:]' < "$PID_FILE"
}

is_funasr_process() {
  local pid=${1:-}
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  [[ -r "/proc/$pid/cmdline" ]] || return 1
  tr '\0' ' ' < "/proc/$pid/cmdline" | grep -q 'serve_vllm.py'
}

port_is_listening() {
  command -v ss >/dev/null 2>&1 || return 1
  ss -H -ltn "sport = :$PORT" | grep -q .
}

prepare_runtime() {
  require_tools
  mkdir -p "$MODELSCOPE_CACHE" "$HF_HOME" "$TORCH_HOME" "$XDG_CACHE_HOME" "$LOG_DIR"
  export CUDA_VISIBLE_DEVICES MODELSCOPE_CACHE HF_HOME TORCH_HOME XDG_CACHE_HOME
  export PYTHONNOUSERSITE VLLM_NO_USAGE_STATS VLLM_WORKER_MULTIPROC_METHOD
  export VLLM_ENABLE_V1_MULTIPROCESSING
}

build_server_args() {
  SERVER_ARGS=(
    "$SERVER_SCRIPT"
    --host "$HOST"
    --port "$PORT"
    --model "$MODEL"
    --hub "$HUB"
    --device "$DEVICE"
    --dtype "$DTYPE"
    --max-model-len "$MAX_MODEL_LEN"
    --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION"
    --vad-model "$VAD_MODEL"
    --spk-model "$SPK_MODEL"
  )
  if [[ "$ENFORCE_EAGER" == "1" || "$ENFORCE_EAGER" == "true" ]]; then
    SERVER_ARGS+=(--enforce-eager)
  fi
  SERVER_ARGS+=("$@")
}

print_server_config() {
  printf 'Python: %s\n' "$PYTHON_BIN"
  printf 'Endpoint: http://%s:%s\n' "$HOST" "$PORT"
  printf 'Model: %s\n' "$MODEL"
  printf 'Device: %s (vLLM decoder still uses CUDA device %s)\n' "$DEVICE" "$CUDA_VISIBLE_DEVICES"
  printf 'GPU memory utilization: %s\n' "$GPU_MEMORY_UTILIZATION"
  printf 'VAD model: %s\n' "$VAD_MODEL"
  printf 'Speaker model: %s\n' "${SPK_MODEL:-disabled}"
  printf 'Log: %s\n' "$LOG_FILE"
}

wait_until_ready() {
  local pid=$1
  local elapsed=0
  while (( elapsed < STARTUP_TIMEOUT_SECONDS )); do
    if ! is_funasr_process "$pid"; then
      tail -n "$LOG_TAIL" "$LOG_FILE" 2>/dev/null || true
      die "FunASR exited before becoming ready"
    fi
    if curl --fail --silent --max-time 3 "http://127.0.0.1:$PORT/openapi.json" >/dev/null; then
      printf 'FunASR is ready: http://127.0.0.1:%s\n' "$PORT"
      return 0
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
  die "FunASR is still starting after ${STARTUP_TIMEOUT_SECONDS}s; inspect $LOG_FILE"
}

start() {
  local pid
  pid="$(read_pid 2>/dev/null || true)"
  if is_funasr_process "$pid"; then
    printf 'FunASR is already running: PID %s\n' "$pid"
    status
    return 0
  fi
  [[ -z "$pid" ]] || rm -f "$PID_FILE"
  if port_is_listening; then
    die "port $PORT is already listening; set PORT to another port or stop the existing service"
  fi

  prepare_runtime
  build_server_args "$@"
  print_server_config
  {
    printf '\n%s Starting FunASR vLLM service\n' "$(date '+%F %T')"
    printf 'Command:'
    printf ' %q' "$PYTHON_BIN" "${SERVER_ARGS[@]}"
    printf '\n'
  } >> "$LOG_FILE"

  (
    cd "$FUNASR_APP_DIR"
    exec setsid "$PYTHON_BIN" "${SERVER_ARGS[@]}"
  ) >> "$LOG_FILE" 2>&1 < /dev/null &
  pid=$!
  printf '%s\n' "$pid" > "$PID_FILE"
  printf 'Started PID %s\n' "$pid"

  sleep 1
  is_funasr_process "$pid" || {
    tail -n "$LOG_TAIL" "$LOG_FILE" 2>/dev/null || true
    die "FunASR failed during startup"
  }
  if [[ "$WAIT_FOR_READY" == "1" || "$WAIT_FOR_READY" == "true" ]]; then
    wait_until_ready "$pid"
  else
    printf 'Startup is continuing in the background; run: %s status\n' "$0"
  fi
}

stop() {
  local pid
  pid="$(read_pid 2>/dev/null || true)"
  if ! is_funasr_process "$pid"; then
    [[ -z "$pid" ]] || printf 'Removing stale PID file for PID %s\n' "$pid"
    rm -f "$PID_FILE"
    printf 'FunASR is not running\n'
    return 0
  fi

  printf 'Stopping FunASR: PID %s\n' "$pid"
  kill "$pid"
  local elapsed=0
  while (( elapsed < STOP_TIMEOUT_SECONDS )); do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$PID_FILE"
      printf 'Stopped\n'
      return 0
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done

  printf 'Process did not exit after %ss; sending SIGKILL\n' "$STOP_TIMEOUT_SECONDS"
  kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
  rm -f "$PID_FILE"
}

status() {
  local pid
  pid="$(read_pid 2>/dev/null || true)"
  if ! is_funasr_process "$pid"; then
    printf 'FunASR: not running\n'
    [[ -z "$pid" ]] || printf 'Stale PID file: %s\n' "$PID_FILE"
    return 1
  fi

  printf 'FunASR: running (PID %s)\n' "$pid"
  printf 'Endpoint: http://127.0.0.1:%s\n' "$PORT"
  if curl --fail --silent --max-time 5 "http://127.0.0.1:$PORT/openapi.json" >/dev/null; then
    printf 'API: ready\n'
  else
    printf 'API: process is running but not ready\n'
    return 1
  fi
}

logs() {
  mkdir -p "$LOG_DIR"
  touch "$LOG_FILE"
  tail -n "$LOG_TAIL" -f "$LOG_FILE"
}

test_api() {
  local audio_file="${1:-$DEFAULT_TEST_AUDIO}"
  local language="${2:-}"
  [[ -f "$audio_file" ]] || die "audio file not found: $audio_file"
  local -a curl_args=(
    curl --fail --silent --show-error -X POST "http://127.0.0.1:$PORT/asr"
    -F "file=@$audio_file"
    -F 'spk=true'
    -F 'timestamp=true'
  )
  [[ -z "$language" ]] || curl_args+=(-F "language=$language")
  "${curl_args[@]}"
  printf '\n'
}

foreground() {
  prepare_runtime
  build_server_args "$@"
  print_server_config
  cd "$FUNASR_APP_DIR"
  exec "$PYTHON_BIN" "${SERVER_ARGS[@]}"
}

case "$ACTION" in
  start)
    start "$@"
    ;;
  stop)
    stop
    ;;
  restart)
    stop
    start "$@"
    ;;
  status)
    status
    ;;
  logs)
    logs
    ;;
  test)
    test_api "$@"
    ;;
  foreground|run)
    foreground "$@"
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
