#!/usr/bin/env bash
set -euo pipefail

MODEL_DIR="${MODEL_DIR:-/home/maoyd/hf_cache_new/hub/models--bg-digitalservices--Gemma-4-26B-A4B-it-NVFP4/snapshots/a15dd6f161881b62db952303a5bfb7be118ed15e}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-Gemma-4-26B-A4B-it-NVFP4}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8006}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-262144}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.31}"
MAX_NUM_BATCHED_TOKENS="${MAX_NUM_BATCHED_TOKENS:-8192}"
MAX_NUM_SEQS="${MAX_NUM_SEQS:-4}"
DTYPE="${DTYPE:-bfloat16}"
QUANTIZATION="${QUANTIZATION:-petit_nvfp4}"
MOE_BACKEND="${MOE_BACKEND:-marlin}"
ENFORCE_EAGER="${ENFORCE_EAGER:-0}"
ENABLE_AUTO_TOOL_CHOICE="${ENABLE_AUTO_TOOL_CHOICE:-1}"
TOOL_CALL_PARSER="${TOOL_CALL_PARSER:-gemma4}"
ENABLE_THINKING="${ENABLE_THINKING:-0}"
REASONING_PARSER="${REASONING_PARSER:-}"
DEFAULT_CHAT_TEMPLATE_KWARGS="${DEFAULT_CHAT_TEMPLATE_KWARGS:-}"
LOG_FILE="${LOG_FILE:-/home/maoyd/logs/gemma4_26b_a4b_nvfp4_vllm.log}"
CONDA_ENV="${CONDA_ENV:-/home/maoyd/miniconda3/envs/vllm-gemma4-nvfp4}"
VLLM_BIN="${VLLM_BIN:-$CONDA_ENV/bin/vllm}"
PYTHON_OVERRIDE_DIR="${PYTHON_OVERRIDE_DIR:-}"

mkdir -p "$(dirname "$LOG_FILE")"

export HF_HOME="${HF_HOME:-/home/maoyd/hf_cache_new}"
export VLLM_NVFP4_GEMM_BACKEND="${VLLM_NVFP4_GEMM_BACKEND:-marlin}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

CUDA_LIB_DIRS=(
  "$CONDA_ENV/lib/python3.12/site-packages/torch/lib"
  "$CONDA_ENV/lib/python3.12/site-packages/nvidia/cu13/lib"
  "$CONDA_ENV/lib/python3.12/site-packages/nvidia/cudnn/lib"
  "/usr/local/lib/ollama/cuda_v12"
)

VLLM_LD_LIBRARY_PATH=""
for lib_dir in "${CUDA_LIB_DIRS[@]}"; do
  if [[ -d "$lib_dir" ]]; then
    if [[ -z "$VLLM_LD_LIBRARY_PATH" ]]; then
      VLLM_LD_LIBRARY_PATH="$lib_dir"
    else
      VLLM_LD_LIBRARY_PATH="${VLLM_LD_LIBRARY_PATH}:$lib_dir"
    fi
  fi
done
export LD_LIBRARY_PATH="${VLLM_LD_LIBRARY_PATH}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

if [[ -n "$PYTHON_OVERRIDE_DIR" ]]; then
  export PYTHONPATH="${PYTHON_OVERRIDE_DIR}:${PYTHONPATH:-}"
fi

if [[ "$ENABLE_THINKING" == "1" || "$ENABLE_THINKING" == "true" || "$ENABLE_THINKING" == "TRUE" ]]; then
  if [[ -z "$REASONING_PARSER" ]]; then
    REASONING_PARSER="gemma4"
  fi
  if [[ -z "$DEFAULT_CHAT_TEMPLATE_KWARGS" ]]; then
    DEFAULT_CHAT_TEMPLATE_KWARGS='{"enable_thinking": true}'
  fi
fi

VLLM_EXTRA_ARGS=()
if [[ -n "$MOE_BACKEND" ]]; then
  VLLM_EXTRA_ARGS+=(--moe-backend "$MOE_BACKEND")
fi
if [[ "$ENFORCE_EAGER" == "1" || "$ENFORCE_EAGER" == "true" || "$ENFORCE_EAGER" == "TRUE" ]]; then
  VLLM_EXTRA_ARGS+=(--enforce-eager)
fi
if [[ "$ENABLE_AUTO_TOOL_CHOICE" == "1" || "$ENABLE_AUTO_TOOL_CHOICE" == "true" || "$ENABLE_AUTO_TOOL_CHOICE" == "TRUE" ]]; then
  VLLM_EXTRA_ARGS+=(--enable-auto-tool-choice)
fi
if [[ -n "$TOOL_CALL_PARSER" ]]; then
  VLLM_EXTRA_ARGS+=(--tool-call-parser "$TOOL_CALL_PARSER")
fi
if [[ -n "$REASONING_PARSER" ]]; then
  VLLM_EXTRA_ARGS+=(--reasoning-parser "$REASONING_PARSER")
fi
if [[ -n "$DEFAULT_CHAT_TEMPLATE_KWARGS" ]]; then
  VLLM_EXTRA_ARGS+=(--default-chat-template-kwargs "$DEFAULT_CHAT_TEMPLATE_KWARGS")
fi

VLLM_CMD=(
  "$VLLM_BIN" serve "$MODEL_DIR"
  --served-model-name "$SERVED_MODEL_NAME"
  --host "$HOST"
  --port "$PORT"
  --max-model-len "$MAX_MODEL_LEN"
  --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION"
  --max-num-batched-tokens "$MAX_NUM_BATCHED_TOKENS"
  --max-num-seqs "$MAX_NUM_SEQS"
  --dtype "$DTYPE"
  --quantization "$QUANTIZATION"
  --trust-remote-code
  "${VLLM_EXTRA_ARGS[@]}"
)

if command -v setsid >/dev/null 2>&1; then
  nohup setsid "${VLLM_CMD[@]}" >> "$LOG_FILE" 2>&1 &
else
  nohup "${VLLM_CMD[@]}" >> "$LOG_FILE" 2>&1 &
fi

echo "vLLM server started in background, PID: $!"
