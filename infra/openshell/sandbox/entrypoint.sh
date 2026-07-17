#!/bin/sh

set -eu
umask 077

readonly EXPECTED_PROJECT_ROOT="/home/maoyd/siq-research-engine"
readonly EXPECTED_HOME="$EXPECTED_PROJECT_ROOT/data/hermes/home/profiles/siq_analysis"
readonly EXPECTED_RUNTIME_HOME="/sandbox/siq-analysis-runtime-state"
readonly HERMES_BIN="/opt/siq/hermes/venv/bin/hermes"
readonly EXPECTED_AUTH_FILE="/sandbox/runtime-auth/auth.json"
readonly AUTH_TEMPLATE="/opt/siq/minimax-cn-auth-pool.template.json"
readonly AUTH_VALIDATOR="/opt/siq/validate_placeholder_auth.py"
readonly PROVIDER_VALIDATOR="/opt/siq/validate_provider_placeholders.py"
readonly RUNTIME_LIFECYCLE_SMOKE="/opt/siq/runtime_state_lifecycle_smoke.py"
readonly RUNTIME_LIFECYCLE_SMOKE_ROOT="$EXPECTED_RUNTIME_HOME"

if [ "$(id -u)" -eq 0 ]; then
    printf '%s\n' 'Refusing to run siq_analysis as root.' >&2
    exit 2
fi
if [ "${SIQ_PROJECT_ROOT:-}" != "$EXPECTED_PROJECT_ROOT" ]; then
    printf '%s\n' 'Unexpected SIQ_PROJECT_ROOT.' >&2
    exit 2
fi
if [ "${HERMES_HOME:-}" != "$EXPECTED_HOME" ]; then
    printf '%s\n' 'Unexpected HERMES_HOME.' >&2
    exit 2
fi
if [ "${HERMES_RUNTIME_HOME:-}" != "$EXPECTED_RUNTIME_HOME" ]; then
    printf '%s\n' 'Unexpected HERMES_RUNTIME_HOME.' >&2
    exit 2
fi
if [ "${HERMES_AUTH_FILE:-}" != "$EXPECTED_AUTH_FILE" ]; then
    printf '%s\n' 'Unexpected HERMES_AUTH_FILE.' >&2
    exit 2
fi
if [ ! -x "$HERMES_BIN" ] || [ ! -r "$HERMES_HOME/config.yaml" ]; then
    printf '%s\n' 'Hermes binary or compiled runtime config is missing.' >&2
    exit 2
fi
if [ -e "$HERMES_HOME/auth.json" ] || [ -e "$HERMES_HOME/.env" ]; then
    printf '%s\n' 'Host credential files must not exist inside the sandbox profile.' >&2
    exit 2
fi
if [ ! -d "$EXPECTED_RUNTIME_HOME" ] || [ ! -w "$EXPECTED_RUNTIME_HOME" ]; then
    printf '%s\n' 'Dedicated Hermes runtime state directory is missing or not writable.' >&2
    exit 2
fi

# This branch is intentionally provider- and gateway-independent. It is used
# only by the image smoke with a dedicated directory bind; formal OpenShell
# policy/mount evidence remains a separate gate.
if [ "${SIQ_RUNTIME_LIFECYCLE_SMOKE_ONLY:-0}" = "1" ]; then
    if [ "${SIQ_RUNTIME_LIFECYCLE_SMOKE_ROOT:-}" != "$RUNTIME_LIFECYCLE_SMOKE_ROOT" ]; then
        printf '%s\n' 'Unexpected runtime lifecycle smoke root.' >&2
        exit 2
    fi
    if [ ! -r "$RUNTIME_LIFECYCLE_SMOKE" ]; then
        printf '%s\n' 'Runtime lifecycle smoke helper is missing.' >&2
        exit 2
    fi
    exec "/opt/siq/hermes/venv/bin/python" "$RUNTIME_LIFECYCLE_SMOKE" \
        --runtime-root "$RUNTIME_LIFECYCLE_SMOKE_ROOT"
fi
if [ -z "${API_SERVER_KEY:-}" ] || [ "${#API_SERVER_KEY}" -lt 32 ]; then
    printf '%s\n' 'A strong ephemeral API_SERVER_KEY is required.' >&2
    exit 2
fi

mkdir -p \
    "$HERMES_HOME/cache" \
    "$HERMES_HOME/checkpoints" \
    "$HERMES_HOME/cron" \
    "$HERMES_HOME/logs" \
    "$HERMES_HOME/memories" \
    "$HERMES_HOME/sessions" \
    "$HERMES_HOME/workspace" \
    /sandbox/runtime-auth \
    /tmp/siq-pycache

if [ ! -e "$EXPECTED_AUTH_FILE" ]; then
    install -m 0600 "$AUTH_TEMPLATE" "$EXPECTED_AUTH_FILE"
fi
if [ ! -e "$EXPECTED_AUTH_FILE.lock" ]; then
    install -m 0600 /dev/null "$EXPECTED_AUTH_FILE.lock"
fi
"/opt/siq/hermes/venv/bin/python" "$AUTH_VALIDATOR" \
    --auth-file "$EXPECTED_AUTH_FILE" \
    --lock-file "$EXPECTED_AUTH_FILE.lock"
if [ "${SIQ_REQUIRE_OPENSHELL_PROVIDERS:-0}" = "1" ]; then
    "/opt/siq/hermes/venv/bin/python" "$PROVIDER_VALIDATOR"
fi

cd "$EXPECTED_PROJECT_ROOT"
exec "$HERMES_BIN" gateway run
