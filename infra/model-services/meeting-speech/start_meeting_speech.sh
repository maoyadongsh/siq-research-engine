#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

is_enabled() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

if ! is_enabled "${SIQ_MEETINGS_ENABLED:-0}" || {
  ! is_enabled "${SIQ_MEETING_REALTIME_ASR_ENABLED:-0}" &&
  ! is_enabled "${SIQ_MEETING_IMPORT_ENABLED:-0}"
}; then
  printf '%s\n' "Meeting speech service remains stopped because realtime ASR and recording import are disabled."
  exit 0
fi

PYTHON_BIN="${SIQ_MEETING_SPEECH_PYTHON_BIN:-/home/maoyd/miniconda3/envs/funasr-vllm/bin/python}"
FUNASR_SOURCE_ROOT="${SIQ_MEETING_SPEECH_FUNASR_SOURCE_ROOT:-/home/maoyd/services/FunASR}"
HOST="${SIQ_MEETING_SPEECH_HOST:-127.0.0.1}"
PORT="${SIQ_MEETING_SPEECH_PORT:-8901}"
MAX_FRAME_BYTES="${SIQ_MEETING_SPEECH_MAX_FRAME_BYTES:-32000}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  printf 'Meeting speech Python is not executable: %s\n' "$PYTHON_BIN" >&2
  exit 1
fi
if [[ "${SIQ_MEETING_SPEECH_ADAPTER:-funasr}" == "funasr" && ! -d "$FUNASR_SOURCE_ROOT/funasr" ]]; then
  printf 'FunASR source root is invalid: %s\n' "$FUNASR_SOURCE_ROOT" >&2
  exit 1
fi
if [[ ! "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  printf 'Meeting speech port is invalid: %s\n' "$PORT" >&2
  exit 1
fi
if [[ ! "$MAX_FRAME_BYTES" =~ ^[0-9]+$ ]] || (( MAX_FRAME_BYTES < 3200 )); then
  printf 'Meeting speech max frame bytes is invalid: %s\n' "$MAX_FRAME_BYTES" >&2
  exit 1
fi
WS_MAX_SIZE=$((MAX_FRAME_BYTES + 32))
if (( WS_MAX_SIZE < 65536 )); then
  WS_MAX_SIZE=65536
fi

export SIQ_MEETING_SPEECH_ENABLED=1
export SIQ_MEETING_SPEECH_FUNASR_SOURCE_ROOT="$FUNASR_SOURCE_ROOT"
export PYTHONPATH="$SCRIPT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

cd "$SCRIPT_DIR"
exec "$PYTHON_BIN" -m uvicorn meeting_speech_service.app:app \
  --app-dir "$SCRIPT_DIR/src" \
  --host "$HOST" \
  --port "$PORT" \
  --ws-max-size "$WS_MAX_SIZE"
