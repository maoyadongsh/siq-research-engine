#!/usr/bin/env bash
set -euo pipefail

ENV_DIR="${ENV_DIR:-/home/maoyd/miniconda3/envs/qwen36_fp8_vllm}"
VLLM_BIN="${VLLM_BIN:-${ENV_DIR}/bin/vllm}"

export HF_HOME="${HF_HOME:-/home/maoyd/hf_cache_new}"
export VLLM_NO_USAGE_STATS="${VLLM_NO_USAGE_STATS:-1}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

CUDA_LIB_DIRS=(
  "${ENV_DIR}/lib/python3.12/site-packages/torch/lib"
  "${ENV_DIR}/lib/python3.12/site-packages/nvidia/cu13/lib"
  "${ENV_DIR}/lib/python3.12/site-packages/nvidia/cudnn/lib"
  "${ENV_DIR}/lib/python3.12/site-packages/nvidia/cuda_runtime/lib"
  "/usr/local/lib/ollama/cuda_v12"
)

VLLM_LD_LIBRARY_PATH=""
for lib_dir in "${CUDA_LIB_DIRS[@]}"; do
  if [[ -d "${lib_dir}" ]]; then
    if [[ -z "${VLLM_LD_LIBRARY_PATH}" ]]; then
      VLLM_LD_LIBRARY_PATH="${lib_dir}"
    else
      VLLM_LD_LIBRARY_PATH="${VLLM_LD_LIBRARY_PATH}:${lib_dir}"
    fi
  fi
done

export LD_LIBRARY_PATH="${VLLM_LD_LIBRARY_PATH}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

export QWEN36_VLLM_BIN="${VLLM_BIN}"
unset VLLM_BIN
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec /usr/bin/env bash "${SCRIPT_DIR}/serve_qwen36_fp8_vllm.sh" "$@"
