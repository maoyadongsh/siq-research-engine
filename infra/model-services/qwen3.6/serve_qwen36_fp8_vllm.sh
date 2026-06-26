#!/usr/bin/env bash
set -euo pipefail

MODEL_DIR="${MODEL_DIR:-/home/maoyd/models/Qwen3.6-35B-A3B-FP8-modelscope}"
VLLM_BIN="${QWEN36_VLLM_BIN:-/home/maoyd/miniconda3/envs/qwen36_fp8_vllm/bin/vllm}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8004}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-Qwen3.6-35B-A3B-FP8}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-262144}"
DTYPE="${DTYPE:-bfloat16}"
KV_CACHE_DTYPE="${KV_CACHE_DTYPE:-fp8}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.39}"
MAX_NUM_BATCHED_TOKENS="${MAX_NUM_BATCHED_TOKENS:-32768}"
MAX_NUM_SEQS="${MAX_NUM_SEQS:-2}"
REASONING_PARSER="${REASONING_PARSER:-qwen3}"
# Default to native multimodal mode. Set LANGUAGE_MODEL_ONLY=1 to force the
# older text-only launch path if a vLLM build or checkpoint regresses.
LANGUAGE_MODEL_ONLY="${LANGUAGE_MODEL_ONLY:-0}"
ENABLE_AUTO_TOOL_CHOICE="${ENABLE_AUTO_TOOL_CHOICE:-1}"
TOOL_CALL_PARSER="${TOOL_CALL_PARSER:-qwen3_coder}"
ENABLE_PREFIX_CACHING="${ENABLE_PREFIX_CACHING:-1}"
DEFAULT_CHAT_TEMPLATE_KWARGS="${DEFAULT_CHAT_TEMPLATE_KWARGS:-{\"enable_thinking\":true}}"

if [[ ! -d "${MODEL_DIR}" ]]; then
  echo "Model directory not found: ${MODEL_DIR}" >&2
  exit 1
fi

if [[ ! -x "${VLLM_BIN}" ]]; then
  echo "vLLM binary not found or not executable: ${VLLM_BIN}" >&2
  exit 1
fi

cmd=(
  "${VLLM_BIN}" serve "${MODEL_DIR}"
  --served-model-name "${SERVED_MODEL_NAME}"
  --host "${HOST}"
  --port "${PORT}"
  --max-model-len "${MAX_MODEL_LEN}"
  --dtype "${DTYPE}"
  --reasoning-parser "${REASONING_PARSER}"
  --default-chat-template-kwargs "${DEFAULT_CHAT_TEMPLATE_KWARGS}"
  --trust-remote-code
)

if [[ -n "${GPU_MEMORY_UTILIZATION}" ]]; then
  cmd+=(--gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}")
fi

if [[ -n "${KV_CACHE_DTYPE}" && "${KV_CACHE_DTYPE}" != "auto" ]]; then
  cmd+=(--kv-cache-dtype "${KV_CACHE_DTYPE}")
fi

if [[ -n "${MAX_NUM_BATCHED_TOKENS}" ]]; then
  cmd+=(--max-num-batched-tokens "${MAX_NUM_BATCHED_TOKENS}")
fi

if [[ -n "${MAX_NUM_SEQS}" ]]; then
  cmd+=(--max-num-seqs "${MAX_NUM_SEQS}")
fi

if [[ "${LANGUAGE_MODEL_ONLY}" == "1" ]]; then
  cmd+=(--language-model-only)
fi

if [[ "${ENABLE_AUTO_TOOL_CHOICE}" == "1" ]]; then
  cmd+=(--enable-auto-tool-choice)
elif [[ "${ENABLE_AUTO_TOOL_CHOICE}" == "0" ]]; then
  :
fi

if [[ -n "${TOOL_CALL_PARSER}" ]]; then
  cmd+=(--tool-call-parser "${TOOL_CALL_PARSER}")
fi

if [[ "${ENABLE_PREFIX_CACHING}" == "1" ]]; then
  cmd+=(--enable-prefix-caching)
fi

exec "${cmd[@]}" "$@"
