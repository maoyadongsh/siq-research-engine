#!/bin/sh

# NOT_PRODUCTION: isolated, disposable siq_analysis/OpenShell feasibility path.
set -eu
umask 077

readonly PROJECT_ROOT="/home/maoyd/siq-research-engine"
readonly PROFILE_SOURCE="$PROJECT_ROOT/data/hermes/home/profiles/siq_analysis"
readonly OBSERVE_ROOT="/sandbox/siq-analysis-observe"
readonly EXPECTED_HOME="$OBSERVE_ROOT/hermes-home"
readonly EXPECTED_AUTH_FILE="$OBSERVE_ROOT/runtime-auth/auth.json"
readonly HERMES_BIN="/opt/siq/hermes/venv/bin/hermes"
readonly AUTH_TEMPLATE="/opt/siq/minimax-cn-auth-pool.template.json"
readonly AUTH_VALIDATOR="/opt/siq/validate_placeholder_auth.py"

fail() {
    printf 'siq_analysis observe PoC refused: %s\n' "$1" >&2
    exit 2
}

[ "${SIQ_OBSERVE_ONLY:-0}" = "1" ] || fail "SIQ_OBSERVE_ONLY must be 1"
[ "$(id -u)" -ne 0 ] || fail "root execution is forbidden"
[ "${SIQ_PROJECT_ROOT:-}" = "$PROJECT_ROOT" ] || fail "unexpected project root"
[ "${HERMES_HOME:-}" = "$EXPECTED_HOME" ] || fail "unexpected HERMES_HOME"
[ "${HERMES_AUTH_FILE:-}" = "$EXPECTED_AUTH_FILE" ] || fail "unexpected HERMES_AUTH_FILE"
[ "${API_SERVER_PORT:-}" = "28651" ] || fail "unexpected API port"
[ -x "$HERMES_BIN" ] || fail "Hermes binary is missing"
[ -r "$PROFILE_SOURCE/config.yaml" ] || fail "compiled siq_analysis profile is missing"
[ -z "${API_SERVER_KEY:-}" ] || [ "${#API_SERVER_KEY}" -ge 32 ] \
    || fail "API_SERVER_KEY is too short"
[ -n "${API_SERVER_KEY:-}" ] || fail "API_SERVER_KEY is required"
[ ! -e "$PROFILE_SOURCE/auth.json" ] || fail "credential material exists in the image profile"
[ ! -e "$PROFILE_SOURCE/.env" ] || fail "environment secrets exist in the image profile"

# A fresh OpenShell sandbox gets a disposable Hermes home. Copying the
# secret-free image profile preserves the actual siq_analysis prompt/config,
# while keeping SQLite WAL files and atomic gateway/config replacements away
# from both the image profile and the host profile.
[ ! -e "$EXPECTED_HOME" ] || fail "disposable Hermes home already exists"
mkdir -p "$OBSERVE_ROOT" "$OBSERVE_ROOT/runtime-auth" /workspace /tmp/siq-pycache
cp -R "$PROFILE_SOURCE" "$EXPECTED_HOME"
chmod -R u+rwX,go-rwx "$EXPECTED_HOME"
mkdir -p \
    "$EXPECTED_HOME/cache" \
    "$EXPECTED_HOME/checkpoints" \
    "$EXPECTED_HOME/cron" \
    "$EXPECTED_HOME/logs" \
    "$EXPECTED_HOME/memories" \
    "$EXPECTED_HOME/sessions" \
    "$EXPECTED_HOME/workspace"

install -m 0600 "$AUTH_TEMPLATE" "$EXPECTED_AUTH_FILE"
install -m 0600 /dev/null "$EXPECTED_AUTH_FILE.lock"
"/opt/siq/hermes/venv/bin/python" "$AUTH_VALIDATOR" \
    --auth-file "$EXPECTED_AUTH_FILE" \
    --lock-file "$EXPECTED_AUTH_FILE.lock"

"/opt/siq/hermes/venv/bin/python" - <<'PY' \
    || fail "MiniMax provider placeholders are absent"
import os
import re

for name in ("SIQ_MINIMAX_CN_PRIMARY", "SIQ_MINIMAX_CN_BACKUP"):
    value = os.environ.get(name, "")
    if re.fullmatch(rf"openshell:resolve:env:(?:v[1-9][0-9]*_)?{name}", value) is None:
        raise SystemExit(2)
PY

cd "$PROJECT_ROOT"
exec "$HERMES_BIN" gateway run
