#!/usr/bin/env bash
set -euo pipefail

RUNTIME_SCRIPT="${NEMOTRON3_RUNTIME_SCRIPT:-/home/maoyd/modles_setup/start_nemotron3_nano_omni_vllm.sh}"

if [[ ! -x "$RUNTIME_SCRIPT" ]]; then
  echo "Nemotron runtime script is missing or not executable: $RUNTIME_SCRIPT" >&2
  exit 1
fi

exec "$RUNTIME_SCRIPT" "$@"
