#!/usr/bin/env bash
set -euo pipefail

profile="${1:-}"
timeout_seconds="${2:-45}"
if [[ -z "$profile" ]]; then
    echo "usage: $0 <siq_ic_chairman|siq_ic_*|siq_assistant|...> [timeout_seconds]" >&2
    exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SIQ_PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
export SIQ_PROJECT_ROOT="$PROJECT_ROOT"

profile_dir="$("$PROJECT_ROOT/scripts/hermes/profile_dir.sh" "$profile")"
config_file="$profile_dir/config.yaml"
port="$(awk '/^[[:space:]]+port:[[:space:]]*[0-9]+/ {print $2; exit}' "$config_file")"
host="$(awk '/^[[:space:]]+host:[[:space:]]*/ {print $2; exit}' "$config_file")"
host="${host:-127.0.0.1}"
if [[ -z "$port" ]]; then
    echo "Unable to parse api_server port from $config_file" >&2
    exit 1
fi

if ss -ltn "sport = :$port" | tail -n +2 | grep -q .; then
    echo "Port $port is already listening; refusing to replace an existing gateway." >&2
    ss -ltnp "sport = :$port" || true
    exit 1
fi

runtime_root="$(mktemp -d "/tmp/siq-hermes-${profile}.XXXXXX")"
log_file="$runtime_root/gateway.log"
pid=""

cleanup() {
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    fi
    if [[ "${SIQ_HERMES_SMOKE_KEEP_RUNTIME:-0}" != "1" ]]; then
        rm -rf "$runtime_root"
    else
        echo "Keeping smoke runtime at $runtime_root"
    fi
}
trap cleanup EXIT INT TERM

export SIQ_HERMES_HOME="$runtime_root/home"
export SIQ_HERMES_PROFILES_ROOT="$SIQ_HERMES_HOME/profiles"
export HERMES_API_KEY="${HERMES_API_KEY:-local-smoke-token}"

echo "Starting Hermes gateway smoke: profile=$profile host=$host port=$port"
"$PROJECT_ROOT/scripts/hermes/run_gateway.sh" "$profile" >"$log_file" 2>&1 &
pid=$!

for _ in $(seq 1 "$timeout_seconds"); do
    if curl -fsS --max-time 1 "http://$host:$port/health" >/tmp/siq-hermes-smoke-health.$$ 2>/dev/null; then
        echo "Health OK: $(cat /tmp/siq-hermes-smoke-health.$$)"
        rm -f /tmp/siq-hermes-smoke-health.$$
        exit 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "Gateway exited before health became ready. Last log lines:" >&2
        tail -80 "$log_file" >&2 || true
        exit 1
    fi
    sleep 1
done

echo "Gateway did not become healthy within ${timeout_seconds}s. Last log lines:" >&2
tail -80 "$log_file" >&2 || true
exit 1
