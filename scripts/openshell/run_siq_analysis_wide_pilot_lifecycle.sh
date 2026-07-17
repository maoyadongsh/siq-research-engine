#!/bin/bash -p
# Execute one isolated NOT_PRODUCTION wide-pilot lifecycle command.

set -euo pipefail
umask 077
IFS=$' \t\n'
readonly SAFE_PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH="$SAFE_PATH"
export LANG=C.UTF-8 LC_ALL=C.UTF-8 TERM=dumb
unset BASH_ENV ENV CDPATH PYTHONPATH PYTHONHOME LD_PRELOAD LD_LIBRARY_PATH
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy
unset OPENSHELL_GATEWAY SIQ_OPENSHELL_MAINTENANCE_FD

readonly COMMAND="${1:-}"
if [[ "$COMMAND" != start && "$COMMAND" != stop && "$COMMAND" != status && "$COMMAND" != probe ]]; then
  printf '%s\n' 'Usage: run_siq_analysis_wide_pilot_lifecycle.sh <start|stop|status|probe> [arguments...]' >&2
  exit 2
fi
shift

SCRIPT_DIR="$(cd -- "$(/usr/bin/dirname -- "${BASH_SOURCE[0]}")" && /bin/pwd -P)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"
if [[ "$COMMAND" == start || "$COMMAND" == stop ]]; then
  siq_openshell_acquire_maintenance_lock
fi

environment=(
  PATH="$SAFE_PATH"
  LANG=C.UTF-8 LC_ALL=C.UTF-8 TERM=dumb NO_COLOR=1
  DOCKER_HOST=unix:///var/run/docker.sock
  DOCKER_CONFIG="$SIQ_OPENSHELL_STATE_ROOT/docker-cli-config"
  SIQ_PROJECT_ROOT="$SIQ_PROJECT_ROOT"
  SIQ_RUNTIME_ROOT="$SIQ_RUNTIME_ROOT"
  SIQ_ARTIFACTS_ROOT="$SIQ_ARTIFACTS_ROOT"
  SIQ_OPENSHELL_STATE_ROOT="$SIQ_OPENSHELL_STATE_ROOT"
  SIQ_OPENSHELL_BIN="$SIQ_OPENSHELL_BIN"
  XDG_CONFIG_HOME="$XDG_CONFIG_HOME"
  XDG_STATE_HOME="$XDG_STATE_HOME"
  XDG_DATA_HOME="$XDG_DATA_HOME"
  XDG_CACHE_HOME="$XDG_CACHE_HOME"
  OPENSHELL_LOCAL_TLS_DIR="$OPENSHELL_LOCAL_TLS_DIR"
  OPENSHELL_SYSTEM_GATEWAY_DIR="$OPENSHELL_SYSTEM_GATEWAY_DIR"
  OPENSHELL_GATEWAY=siq-openshell-dev
)
if [[ -n "${SIQ_OPENSHELL_MAINTENANCE_FD:-}" ]]; then
  environment+=(SIQ_OPENSHELL_MAINTENANCE_FD="$SIQ_OPENSHELL_MAINTENANCE_FD")
fi

exec /usr/bin/env -i "${environment[@]}" \
  /usr/bin/python3 -I -B "$SCRIPT_DIR/siq_analysis_wide_pilot.py" "$COMMAND" "$@"
