#!/usr/bin/env bash
# Validate API, tool execution, isolation and runtime writes for the Hermes PoC.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SANDBOX_NAME="siq-hermes-minimal-poc"
readonly API_KEY_FILE="$SCRIPT_DIR/../../var/openshell/poc/hermes-minimal/api.key"

python3 "$SCRIPT_DIR/test_hermes_poc_contract.py" --api-key-file "$API_KEY_FILE"

"$SCRIPT_DIR/run_cli.sh" sandbox exec --name "$SANDBOX_NAME" --timeout 10 --no-tty -- \
  /bin/sh -c 'test "$(id -u)" = 10001; test "$(id -g)" = 10001'
"$SCRIPT_DIR/run_cli.sh" sandbox exec --name "$SANDBOX_NAME" --timeout 10 --no-tty -- \
  /bin/sh -c 'test -c /dev/null; test ! -L /dev/null; test -c /dev/urandom; test ! -L /dev/urandom'
"$SCRIPT_DIR/run_cli.sh" sandbox exec --name "$SANDBOX_NAME" --timeout 10 --no-tty -- \
  /bin/sh -c 'test "$(cat /workspace/hermes-shell-proof.txt)" = shell-ok; test "$(cat /workspace/hermes-python-proof.txt)" = python-ok'
"$SCRIPT_DIR/run_cli.sh" sandbox exec --name "$SANDBOX_NAME" --timeout 10 --no-tty -- \
  /bin/sh -c 'test -s "$HERMES_HOME/config.yaml"; test -d "$HERMES_HOME/logs"; touch "$HERMES_HOME/runtime-write-proof"'
"$SCRIPT_DIR/run_cli.sh" sandbox exec --name "$SANDBOX_NAME" --timeout 10 --no-tty -- \
  /bin/sh -c 'if touch /opt/hermes-agent/siq-must-remain-read-only; then exit 1; fi'
"$SCRIPT_DIR/run_cli.sh" sandbox exec --name "$SANDBOX_NAME" --timeout 10 --no-tty -- \
  /bin/sh -c 'if head -c 1 /dev/zero >/dev/null 2>&1; then exit 1; fi'
"$SCRIPT_DIR/run_cli.sh" sandbox exec --name "$SANDBOX_NAME" --timeout 10 --no-tty -- \
  python -c 'import socket; s=socket.socket(); s.settimeout(1); rc=s.connect_ex(("1.1.1.1",443)); s.close(); raise SystemExit(0 if rc else 1)'

container_id="$(docker ps --filter "label=openshell.ai/sandbox-name=$SANDBOX_NAME" --format '{{.ID}}')"
if [[ -z "$container_id" || "$container_id" == *$'\n'* ]]; then
  printf '%s\n' 'Expected exactly one managed PoC container.' >&2
  exit 2
fi
while IFS= read -r bind; do
  [[ -n "$bind" ]] || continue
  source_path="${bind%%:*}"
  destination="${bind#*:}"
  destination="${destination%%:*}"
  [[ "$bind" == *":ro"* || "$bind" == *":ro,"* ]] || {
    printf 'Unexpected writable host bind: %s\n' "$bind" >&2
    exit 2
  }
  case "$source_path" in
    */var/openshell/toolchains/v0.0.83/bin/openshell-sandbox)
      [[ "$destination" == "/opt/openshell/bin/openshell-sandbox" ]] || exit 2
      ;;
    */var/openshell/gateway/siq-openshell-dev/tls/*)
      [[ "$destination" == /etc/openshell/tls/* ]] || exit 2
      ;;
    */var/openshell/xdg/state/openshell/docker-sandbox-tokens/siq-openshell-dev/*)
      [[ "$destination" == "/etc/openshell/auth/sandbox.jwt" ]] || exit 2
      ;;
    *)
      printf 'Unexpected host bind in Hermes PoC container: %s\n' "$source_path" >&2
      exit 2
      ;;
  esac
done < <(docker inspect "$container_id" --format '{{range .HostConfig.Binds}}{{println .}}{{end}}')

mount_count=0
while IFS=$'\t' read -r source_path destination writable; do
  [[ -n "$source_path" ]] || continue
  [[ "$writable" == "false" ]] || {
    printf 'Unexpected writable Docker mount: %s -> %s\n' "$source_path" "$destination" >&2
    exit 2
  }
  case "$source_path:$destination" in
    */var/openshell/toolchains/v0.0.83/bin/openshell-sandbox:/opt/openshell/bin/openshell-sandbox|\
    */var/openshell/gateway/siq-openshell-dev/tls/*:/etc/openshell/tls/*|\
    */var/openshell/xdg/state/openshell/docker-sandbox-tokens/siq-openshell-dev/*:/etc/openshell/auth/sandbox.jwt)
      mount_count=$((mount_count + 1))
      ;;
    *)
      printf 'Unexpected Docker mount: %s -> %s\n' "$source_path" "$destination" >&2
      exit 2
      ;;
  esac
done < <(docker inspect "$container_id" --format '{{range .Mounts}}{{printf "%s\t%s\t%t\n" .Source .Destination .RW}}{{end}}')
[[ "$mount_count" -eq 5 ]] || {
  printf 'Expected five read-only OpenShell control mounts, found %s.\n' "$mount_count" >&2
  exit 2
}

printf '%s\n' 'Hermes PoC isolation and runtime-write checks: PASS'
