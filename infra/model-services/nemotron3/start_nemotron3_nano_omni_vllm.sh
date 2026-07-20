#!/usr/bin/env bash
set -Eeuo pipefail

# NVIDIA Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4 on DGX Spark.
# The model card requires vLLM 0.20.0 (CUDA 13.0, aarch64 image).

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ACTION="${1:-start}"
if [[ $# -gt 0 ]]; then
  shift
fi

MODEL_REPO="${MODEL_REPO:-nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4}"
MODEL_DIR="${MODEL_DIR:-/home/maoyd/models/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4}"
HF_HOME_DIR="${HF_HOME_DIR:-/home/maoyd/models/.hf-home}"
VLLM_CACHE_DIR="${VLLM_CACHE_DIR:-/home/maoyd/models/.vllm-cache/nemotron3-nano-omni}"
IMAGE_BASE="${IMAGE_BASE:-vllm/vllm-openai:v0.20.0}"
IMAGE="${IMAGE:-nemotron3-nano-omni-vllm:0.20.0}"
CONTAINER_NAME="${CONTAINER_NAME:-nemotron3-nano-omni-vllm}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8007}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-nemotron_3_nano_omni}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.27}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-262144}"
MAX_NUM_SEQS="${MAX_NUM_SEQS:-6}"
MAX_NUM_BATCHED_TOKENS="${MAX_NUM_BATCHED_TOKENS:-32768}"
DEFAULT_CHAT_TEMPLATE_KWARGS="${DEFAULT_CHAT_TEMPLATE_KWARGS:-{\"enable_thinking\":true}}"
COMPILATION_CONFIG="${COMPILATION_CONFIG:-{\"cudagraph_mode\":\"FULL_AND_PIECEWISE\"}}"
SHM_SIZE="${SHM_SIZE:-16g}"
HOST_MEDIA_ROOT="${HOST_MEDIA_ROOT:-/home/maoyd}"
LOG_TAIL="${LOG_TAIL:-200}"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'USAGE'
Usage:
  start_nemotron3_nano_omni_vllm.sh download
  start_nemotron3_nano_omni_vllm.sh build
  start_nemotron3_nano_omni_vllm.sh start [vLLM args...]
  start_nemotron3_nano_omni_vllm.sh stop|restart|status|logs|test|shell

Environment overrides:
  MODEL_DIR, HF_HOME_DIR, VLLM_CACHE_DIR, PORT=8007, HOST, SERVED_MODEL_NAME
  GPU_MEMORY_UTILIZATION=0.27, MAX_MODEL_LEN=262144
  MAX_NUM_SEQS=6, MAX_NUM_BATCHED_TOKENS=32768
  DEFAULT_CHAT_TEMPLATE_KWARGS='{"enable_thinking":true}'
  COMPILATION_CONFIG='{"cudagraph_mode":"FULL_AND_PIECEWISE"}'

The default 262144 context is the requested 256k target. If the model's
runtime rejects it, retry with MAX_MODEL_LEN=131072.
USAGE
}

require_tools() {
  command -v docker >/dev/null 2>&1 || die "docker is required"
}

container_exists() {
  docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1
}

container_running() {
  [[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null || true)" == "true" ]]
}

ensure_base_image() {
  if ! docker image inspect "$IMAGE_BASE" >/dev/null 2>&1; then
    echo "Pulling $IMAGE_BASE ..."
    docker pull "$IMAGE_BASE"
  fi
}

ensure_image() {
  ensure_base_image
  if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "Building $IMAGE (vLLM remains pinned at 0.20.0) ..."
    docker build --pull=false -f "$SCRIPT_DIR/Dockerfile.nemotron3-nano-omni-vllm" \
      --build-arg BUILDKIT_INLINE_CACHE=1 -t "$IMAGE" "$SCRIPT_DIR"
  fi
}

ensure_model() {
  [[ -f "$MODEL_DIR/config.json" ]] && return
  command -v hf >/dev/null 2>&1 || die "hf CLI is required; install huggingface_hub first"
  mkdir -p "$MODEL_DIR" "$HF_HOME_DIR"
  echo "Downloading $MODEL_REPO into $MODEL_DIR ..."
  HF_HOME="$HF_HOME_DIR" hf download "$MODEL_REPO" --local-dir "$MODEL_DIR" --max-workers "${HF_MAX_WORKERS:-8}"
  [[ -f "$MODEL_DIR/config.json" ]] || die "model download did not produce $MODEL_DIR/config.json"
}

check_port() {
  if command -v ss >/dev/null 2>&1 && ss -H -ltn "sport = :$PORT" | grep -q .; then
    die "port $PORT is already listening; set PORT to another port"
  fi
}

start() {
  require_tools
  ensure_model
  ensure_image
  if container_running; then
    echo "$CONTAINER_NAME is already running"
    status
    return 0
  fi
  if container_exists; then
    docker rm "$CONTAINER_NAME" >/dev/null
  fi
  check_port
  [[ -d "$HOST_MEDIA_ROOT" ]] || die "HOST_MEDIA_ROOT does not exist: $HOST_MEDIA_ROOT"
  mkdir -p "$HF_HOME_DIR" "$VLLM_CACHE_DIR"
  local -a docker_cmd=(
    docker run -d
    --name "$CONTAINER_NAME"
    --gpus all
    --ipc=host
    --shm-size "$SHM_SIZE"
    -p "$HOST:$PORT:8000"
    -v "$MODEL_DIR:/model:ro"
    -v "$HF_HOME_DIR:/root/.cache/huggingface"
    -v "$VLLM_CACHE_DIR:/root/.cache/vllm"
    -v "$HOST_MEDIA_ROOT:$HOST_MEDIA_ROOT:ro"
    --entrypoint vllm
    "$IMAGE"
    serve /model
    --served-model-name "$SERVED_MODEL_NAME"
    --host 0.0.0.0
    --port 8000
    --tensor-parallel-size 1
    --max-model-len "$MAX_MODEL_LEN"
    --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION"
    --max-num-seqs "$MAX_NUM_SEQS"
    --max-num-batched-tokens "$MAX_NUM_BATCHED_TOKENS"
    --trust-remote-code
    --default-chat-template-kwargs "$DEFAULT_CHAT_TEMPLATE_KWARGS"
    --compilation-config "$COMPILATION_CONFIG"
    --video-pruning-rate 0.5
    --limit-mm-per-prompt '{"video":1,"image":1,"audio":1}'
    --media-io-kwargs '{"video":{"fps":2,"num_frames":256}}'
    --allowed-local-media-path "$HOST_MEDIA_ROOT"
    --enable-prefix-caching
    --reasoning-parser nemotron_v3
    --enable-auto-tool-choice
    --tool-call-parser qwen3_coder
    --kv-cache-dtype fp8
  )
  docker_cmd+=("$@")
  "${docker_cmd[@]}" >/dev/null
  echo "Started $CONTAINER_NAME on http://$HOST:$PORT"
  echo "Check readiness with: $0 status"
}

stop() {
  require_tools
  if container_exists; then
    docker rm -f "$CONTAINER_NAME" >/dev/null
    echo "Stopped $CONTAINER_NAME"
  else
    echo "$CONTAINER_NAME is not present"
  fi
}

status() {
  require_tools
  if ! container_exists; then
    echo "$CONTAINER_NAME: absent"
    return 0
  fi
  docker ps -a --filter "name=^/${CONTAINER_NAME}$" --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
  if container_running && command -v curl >/dev/null 2>&1; then
    curl --fail --silent --show-error --max-time 5 "http://127.0.0.1:$PORT/v1/models" | head -c 1000 || true
    printf '\n'
  fi
}

logs() {
  require_tools
  docker logs --tail "$LOG_TAIL" -f "$CONTAINER_NAME"
}

test_api() {
  command -v curl >/dev/null 2>&1 || die "curl is required"
  curl --fail --silent --show-error "http://127.0.0.1:$PORT/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -d "$(printf '%s' '{"model":"'"$SERVED_MODEL_NAME"'","messages":[{"role":"user","content":"Reply with exactly: nemotron-ok"}],"max_tokens":128,"temperature":0.2}')"
  printf '\n'
}

case "$ACTION" in
  download)
    ensure_model
    echo "Model ready: $MODEL_DIR"
    ;;
  build)
    require_tools
    ensure_image
    echo "Image ready: $IMAGE"
    ;;
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
    test_api
    ;;
  shell)
    require_tools
    docker exec -it "$CONTAINER_NAME" bash
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
