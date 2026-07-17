#!/usr/bin/env bash
# Validate the observe API and prove host profile/immutable state was unchanged.

set -euo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
readonly SANDBOX_NAME="siq-analysis-observe-poc"
readonly STATE_DIR="$ROOT_DIR/var/openshell/poc/siq-analysis-observe"
readonly API_KEY_FILE="$STATE_DIR/api.key"

install -d -m 0700 -- "$STATE_DIR"
before="$(mktemp "$STATE_DIR/.host-before.XXXXXX")"
after="$(mktemp "$STATE_DIR/.host-after.XXXXXX")"
cleanup() {
  rm -f -- "$before" "$after"
}
trap cleanup EXIT

python3 "$SCRIPT_DIR/snapshot_observe_host_invariants.py" --output "$before"

set +e
python3 "$SCRIPT_DIR/test_siq_analysis_observe_contract.py" \
  --base-url http://127.0.0.1:28651 \
  --api-key-file "$API_KEY_FILE"
contract_status=$?
set -e

python3 "$SCRIPT_DIR/snapshot_observe_host_invariants.py" --output "$after"
cmp -s -- "$before" "$after" || {
  printf '%s\n' 'Host profile or immutable data changed during the observe smoke.' >&2
  exit 2
}
[[ "$contract_status" -eq 0 ]] || exit "$contract_status"

"$SCRIPT_DIR/run_cli.sh" sandbox exec --name "$SANDBOX_NAME" --timeout 10 --no-tty -- \
  /bin/sh -c 'test "$(id -u)" = 1000; test "$(id -g)" = 1000; test "$HERMES_HOME" = /sandbox/siq-analysis-observe/hermes-home'
"$SCRIPT_DIR/run_cli.sh" sandbox exec --name "$SANDBOX_NAME" --timeout 10 --no-tty -- \
  /bin/sh -c 'test -s "$HERMES_HOME/config.yaml"; test -d "$HERMES_HOME/sessions"; touch "$HERMES_HOME/observe-write-proof"'
"$SCRIPT_DIR/run_cli.sh" sandbox exec --name "$SANDBOX_NAME" --timeout 10 --no-tty -- \
  /bin/sh -c 'if touch /home/maoyd/siq-research-engine/siq-observe-must-not-write; then exit 1; fi'

container_id="$(docker ps \
  --filter 'label=openshell.ai/managed-by=openshell' \
  --filter 'label=openshell.ai/sandbox-namespace=siq-openshell-dev' \
  --filter "label=openshell.ai/sandbox-name=$SANDBOX_NAME" \
  --format '{{.ID}}')"
if [[ -z "$container_id" || "$container_id" == *$'\n'* ]]; then
  printf '%s\n' 'Expected exactly one managed observe container.' >&2
  exit 2
fi

mount_count=0
while IFS=$'\t' read -r source_path destination writable; do
  [[ -n "$source_path" ]] || continue
  [[ "$writable" == false ]] || {
    printf 'Unexpected writable host mount: %s -> %s\n' "$source_path" "$destination" >&2
    exit 2
  }
  case "$source_path:$destination" in
    */var/openshell/toolchains/v0.0.83/bin/openshell-sandbox:/opt/openshell/bin/openshell-sandbox|\
    */var/openshell/gateway/siq-openshell-dev/tls/*:/etc/openshell/tls/*|\
    */var/openshell/xdg/state/openshell/docker-sandbox-tokens/siq-openshell-dev/*:/etc/openshell/auth/sandbox.jwt)
      mount_count=$((mount_count + 1))
      ;;
    *)
      printf 'Unexpected observe host mount: %s -> %s\n' "$source_path" "$destination" >&2
      exit 2
      ;;
  esac
done < <(docker inspect "$container_id" --format '{{range .Mounts}}{{printf "%s\t%s\t%t\n" .Source .Destination .RW}}{{end}}')
[[ "$mount_count" -eq 5 ]] || {
  printf 'Expected five read-only OpenShell control mounts, found %s.\n' "$mount_count" >&2
  exit 2
}

printf '%s\n' 'NOT_PRODUCTION siq_analysis OpenShell observe contract: PASS'
