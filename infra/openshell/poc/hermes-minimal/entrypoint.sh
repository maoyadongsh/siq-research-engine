#!/bin/sh

set -eu
umask 077

readonly EXPECTED_HOME="/home/sandbox/.hermes"
readonly MODEL_PORT="19000"
export HERMES_STREAM_RETRIES=0

if [ "$(id -u)" -eq 0 ]; then
    printf '%s\n' 'Refusing to run Hermes PoC as root.' >&2
    exit 2
fi

if [ "${HERMES_HOME:-}" != "$EXPECTED_HOME" ]; then
    printf 'Unexpected HERMES_HOME: %s\n' "${HERMES_HOME:-unset}" >&2
    exit 2
fi
if [ -z "${API_SERVER_KEY:-}" ] || [ "${#API_SERVER_KEY}" -lt 32 ]; then
    printf '%s\n' 'A strong ephemeral API_SERVER_KEY is required.' >&2
    exit 2
fi

mkdir -p "$HERMES_HOME/logs" "$HERMES_HOME/sessions" /workspace
cp /opt/siq-poc/config.yaml "$HERMES_HOME/config.yaml.tmp"
chmod 0600 "$HERMES_HOME/config.yaml.tmp"
mv "$HERMES_HOME/config.yaml.tmp" "$HERMES_HOME/config.yaml"

python /opt/siq-poc/model_stub.py \
    --host 127.0.0.1 \
    --port "$MODEL_PORT" \
    >"$HERMES_HOME/logs/model-stub.log" 2>&1 &
stub_pid=$!

cleanup() {
    kill "$stub_pid" 2>/dev/null || true
    wait "$stub_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

python - "$MODEL_PORT" <<'PY'
import socket
import sys
import time

port = int(sys.argv[1])
for _ in range(100):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            break
    except OSError:
        time.sleep(0.05)
else:
    raise SystemExit("local model stub did not become ready")
PY

hermes gateway run
