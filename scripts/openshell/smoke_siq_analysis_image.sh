#!/usr/bin/env bash
# Exercise the formal siq_analysis image without external network access.

set -euo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
STATE_FILE="$ROOT_DIR/var/openshell/siq-analysis/current-image.json"
SMOKE_STATE_FILE="$ROOT_DIR/var/openshell/siq-analysis/current-image.smoke.json"
CONTAINER="siq-openshell-image-smoke-$$"
RUNTIME_SMOKE_HOST_ROOT=""
RUNTIME_SMOKE_RESULT=""
RUNTIME_LIFECYCLE_ONLY=0
readonly EXPECTED_HERMES_VERSION_LINE="Hermes Agent v0.13.0 (2026.5.7)"
readonly SANDBOX_HERMES_HOME="/home/maoyd/siq-research-engine/data/hermes/home/profiles/siq_analysis"
readonly SANDBOX_RUNTIME_HOME="/sandbox/siq-analysis-runtime-state"

if [[ "${1:-}" == "--runtime-lifecycle-only" ]]; then
  RUNTIME_LIFECYCLE_ONLY=1
  shift
fi
[[ "$#" -eq 0 ]] || {
  printf 'Usage: %s [--runtime-lifecycle-only]\n' "$0" >&2
  exit 2
}

cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  if [[ -n "${RUNTIME_SMOKE_HOST_ROOT:-}" && "$RUNTIME_SMOKE_HOST_ROOT" == "$ROOT_DIR"/var/openshell/siq-analysis/.runtime-lifecycle-smoke.* ]]; then
    rm -rf -- "$RUNTIME_SMOKE_HOST_ROOT"
  fi
  if [[ -n "${RUNTIME_SMOKE_RESULT:-}" && "$RUNTIME_SMOKE_RESULT" == "$ROOT_DIR"/var/openshell/siq-analysis/.runtime-lifecycle-result.* ]]; then
    rm -f -- "$RUNTIME_SMOKE_RESULT"
  fi
}
trap cleanup EXIT INT TERM

[[ -f "$STATE_FILE" && ! -L "$STATE_FILE" ]] || {
  printf '%s\n' 'No verified siq_analysis candidate image. Run build_siq_analysis_image.sh first.' >&2
  exit 2
}

[[ ! -L "$SMOKE_STATE_FILE" ]] || {
  printf '%s\n' 'Refusing unsafe candidate smoke state symlink.' >&2
  exit 2
}
# A failed or interrupted full smoke must revoke any older proof for this
# candidate. Lifecycle-only mode emits standalone evidence and does not alter
# the full image attestation.
if [[ "$RUNTIME_LIFECYCLE_ONLY" -eq 0 ]]; then
  rm -f -- "$SMOKE_STATE_FILE"
fi

IMAGE_REF="$(python3 - "$STATE_FILE" <<'PY'
import json
import re
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
image_ref = payload.get("image_ref")
if (
    payload.get("schema_version") != "siq.openshell.candidate_image.v1"
    or payload.get("architecture") != "arm64"
    or payload.get("user") != "sandbox:sandbox"
    or not isinstance(image_ref, str)
    or not re.fullmatch(r"siq/hermes-openshell-siq-analysis:[0-9a-f]{24}", image_ref)
):
    raise SystemExit("Invalid candidate image state")
print(image_ref)
PY
)"

docker image inspect "$IMAGE_REF" >/dev/null 2>&1 || {
  printf 'Candidate image is not present locally: %s\n' "$IMAGE_REF" >&2
  exit 2
}
[[ "$(docker image inspect "$IMAGE_REF" --format '{{.Config.User}}')" == sandbox:sandbox ]] || {
  printf '%s\n' 'Candidate image does not run as sandbox:sandbox.' >&2
  exit 2
}

HERMES_VERSION_LINE="$(
  docker run --rm --network none \
    --entrypoint /opt/siq/hermes/venv/bin/hermes \
    "$IMAGE_REF" --version \
    | sed -n '1p'
)"
[[ "$HERMES_VERSION_LINE" == "$EXPECTED_HERMES_VERSION_LINE" ]] || {
  printf '%s\n' 'Candidate image does not contain the frozen Hermes 0.13.0 runtime.' >&2
  exit 2
}

prebuilt_logs="$(docker run --rm --network none --entrypoint sh "$IMAGE_REF" \
  -c 'find "$HERMES_HOME/logs" -type f 2>/dev/null | wc -l')"
[[ "$prebuilt_logs" == 0 ]] || {
  printf '%s\n' 'Candidate image contains build-time Hermes logs.' >&2
  exit 2
}

docker run --rm --network none --entrypoint sh "$IMAGE_REF" -c '
  set -eu
  test "$HERMES_RUNTIME_HOME" = /sandbox/siq-analysis-runtime-state
  test -d "$HERMES_RUNTIME_HOME"
  test -w "$HERMES_RUNTIME_HOME"
  test ! -w "$HERMES_HOME/config.yaml"
  for file in \
      .clean_shutdown .skills_prompt_snapshot.json channel_directory.json \
      gateway.lock gateway.pid gateway_state.json models_dev_cache.json processes.json \
      response_store.db response_store.db-shm response_store.db-wal \
      state.db state.db-shm state.db-wal; do
    test ! -e "$HERMES_HOME/$file"
    test ! -e "$HERMES_RUNTIME_HOME/$file"
  done
'

# Exercise the state paths through the image entrypoint without starting
# Hermes or contacting a provider. The host directory is a private, empty
# directory bind so create/unlink/rename semantics are observable.
RUNTIME_SMOKE_HOST_ROOT="$(mktemp -d "$ROOT_DIR/var/openshell/siq-analysis/.runtime-lifecycle-smoke.XXXXXX")"
chmod 0700 -- "$RUNTIME_SMOKE_HOST_ROOT"
RUNTIME_SMOKE_RESULT="$(mktemp "$ROOT_DIR/var/openshell/siq-analysis/.runtime-lifecycle-result.XXXXXX")"
chmod 0600 -- "$RUNTIME_SMOKE_RESULT"
docker run --rm \
  --network none \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m \
  --mount "type=bind,source=$RUNTIME_SMOKE_HOST_ROOT,target=/sandbox/siq-analysis-runtime-state" \
  --env SIQ_RUNTIME_LIFECYCLE_SMOKE_ONLY=1 \
  --env SIQ_RUNTIME_LIFECYCLE_SMOKE_ROOT=/sandbox/siq-analysis-runtime-state \
  "$IMAGE_REF" >"$RUNTIME_SMOKE_RESULT"

python3 - "$RUNTIME_SMOKE_RESULT" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
if (
    payload.get("schema_version") != "siq.openshell.runtime_state_lifecycle_smoke.v1"
    or payload.get("status") != "passed"
    or payload.get("scope") != "candidate_image_directory_bind_without_openshell_policy_or_gateway"
    or payload.get("readiness_effect") != "none"
    or payload.get("gateway_started") is not False
    or payload.get("provider_contacted") is not False
    or payload.get("rounds_completed") != 2
    or payload.get("final_cleanup") is not True
):
    raise SystemExit("runtime lifecycle smoke returned an invalid result")
formal = payload.get("formal_sandbox_evidence")
if (
    not isinstance(formal, dict)
    or formal.get("status") != "pending_live_validation"
    or formal.get("reason_codes") != ["formal_runtime_directory_bind_requires_live_sandbox_evidence"]
    or formal.get("resolved_design_blockers") != [
        "sqlite_sidecars_are_not_file_bind_mounts",
        "gateway_metadata_parent_allows_atomic_replace",
        "hermes_control_home_remains_outside_runtime_state_mount",
    ]
):
    raise SystemExit("runtime lifecycle smoke did not preserve the formal live-validation gate")
rounds = payload.get("rounds")
if not isinstance(rounds, list) or [item.get("generation") for item in rounds] != [1, 2]:
    raise SystemExit("runtime lifecycle smoke did not complete two generations")
PY
[[ -z "$(find "$RUNTIME_SMOKE_HOST_ROOT" -mindepth 1 -maxdepth 1 -print -quit)" ]] || {
  printf '%s\n' 'Runtime lifecycle smoke left files in its private bind directory.' >&2
  exit 1
}
if [[ "$RUNTIME_LIFECYCLE_ONLY" -eq 1 ]]; then
  cat -- "$RUNTIME_SMOKE_RESULT"
  exit 0
fi

docker run -d \
  --name "$CONTAINER" \
  --network none \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m \
  --tmpfs /sandbox/runtime-auth:rw,nosuid,nodev,noexec,size=1m,uid=1000,gid=1000,mode=0700 \
  --tmpfs "$SANDBOX_HERMES_HOME/cache:rw,nosuid,nodev,noexec,size=16m,uid=1000,gid=1000,mode=0700" \
  --tmpfs "$SANDBOX_HERMES_HOME/checkpoints:rw,nosuid,nodev,noexec,size=16m,uid=1000,gid=1000,mode=0700" \
  --tmpfs "$SANDBOX_HERMES_HOME/cron:rw,nosuid,nodev,noexec,size=4m,uid=1000,gid=1000,mode=0700" \
  --tmpfs "$SANDBOX_HERMES_HOME/logs:rw,nosuid,nodev,noexec,size=32m,uid=1000,gid=1000,mode=0700" \
  --tmpfs "$SANDBOX_HERMES_HOME/memories:rw,nosuid,nodev,noexec,size=16m,uid=1000,gid=1000,mode=0700" \
  --tmpfs "$SANDBOX_HERMES_HOME/sessions:rw,nosuid,nodev,noexec,size=32m,uid=1000,gid=1000,mode=0700" \
  --tmpfs "$SANDBOX_HERMES_HOME/workspace:rw,nosuid,nodev,noexec,size=32m,uid=1000,gid=1000,mode=0700" \
  --mount "type=bind,source=$RUNTIME_SMOKE_HOST_ROOT,target=$SANDBOX_RUNTIME_HOME" \
  --env "API_SERVER_KEY=$(printf '%064d' 0)" \
  "$IMAGE_REF" >/dev/null

status=starting
for _ in $(seq 1 40); do
  status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CONTAINER")"
  [[ "$status" == healthy || "$status" == exited ]] && break
  sleep 1
done
[[ "$status" == healthy ]] || {
  exit_code="$(docker inspect --format '{{.State.ExitCode}}' "$CONTAINER")"
  printf 'Candidate image failed isolated startup: status=%s exit=%s\n' "$status" "$exit_code" >&2
  exit 1
}

docker exec "$CONTAINER" sh -c '
  set -eu
  test "$(id -u):$(id -g)" = 1000:1000
  test ! -e "$HERMES_HOME/auth.json"
  test ! -e "$HERMES_HOME/.env"
  test ! -w /home/maoyd/siq-research-engine/agents/hermes/profiles/siq_analysis/SOUL.md
  test ! -w "$HERMES_HOME/config.yaml"
  test -w "$HERMES_RUNTIME_HOME"
  test -w "$HERMES_RUNTIME_HOME/state.db"
  test -w "$HERMES_HOME/logs/agent.log"
  for file in \
      .clean_shutdown .skills_prompt_snapshot.json channel_directory.json \
      gateway.lock gateway.pid gateway_state.json models_dev_cache.json processes.json \
      response_store.db response_store.db-shm response_store.db-wal \
      state.db state.db-shm state.db-wal; do
    test ! -e "$HERMES_HOME/$file"
  done
  test "$(find /home/maoyd/siq-research-engine -type f \( -name .env -o -name auth.json -o -name "*.pem" -o -name "*.key" \) | wc -l)" = 0
  /opt/siq/hermes/venv/bin/python /opt/siq/validate_placeholder_auth.py \
    --auth-file "$HERMES_AUTH_FILE" \
    --lock-file "$HERMES_AUTH_FILE.lock"
'

docker exec -i "$CONTAINER" /opt/siq/hermes/venv/bin/python - <<'PY'
import json
import os
from pathlib import Path

import agent.models_dev as models_dev
import agent.prompt_builder as prompt_builder
import gateway.channel_directory as channel_directory
import gateway.pairing as gateway_pairing
import gateway.status as gateway_status
import hermes_state
import tools.process_registry as process_registry
from hermes_cli.auth import _auth_file_path, read_credential_pool, write_credential_pool
from hermes_constants import get_hermes_home, get_hermes_runtime_home

home = Path("/home/maoyd/siq-research-engine/data/hermes/home/profiles/siq_analysis")
runtime = Path("/sandbox/siq-analysis-runtime-state")
assert get_hermes_home() == home
assert get_hermes_runtime_home() == runtime
assert hermes_state.DEFAULT_DB_PATH == runtime / "state.db"
assert gateway_status._get_pid_path() == runtime / "gateway.pid"
assert gateway_status._get_gateway_lock_path() == runtime / "gateway.lock"
assert gateway_status._get_runtime_status_path() == runtime / "gateway_state.json"
assert process_registry.CHECKPOINT_PATH == runtime / "processes.json"
assert models_dev._get_cache_path() == runtime / "models_dev_cache.json"
assert prompt_builder._skills_prompt_snapshot_path() == runtime / ".skills_prompt_snapshot.json"
assert channel_directory.DIRECTORY_PATH == runtime / "channel_directory.json"
assert gateway_pairing.PAIRING_DIR == runtime / "platforms" / "pairing"
assert gateway_pairing.PAIRING_DIR.is_dir()

# Exercise the parent-directory semantics used by Hermes atomic metadata
# writes. The runtime mount must allow replace/unlink/recreate while the
# static profile parent remains read-only.
for path in (
    process_registry.CHECKPOINT_PATH,
    models_dev._get_cache_path(),
    prompt_builder._skills_prompt_snapshot_path(),
    channel_directory.DIRECTORY_PATH,
):
    temporary = path.with_name(f".{path.name}.siq-smoke.tmp")
    temporary.write_text('{"siq_smoke":true}\n', encoding="utf-8")
    os.replace(temporary, path)
    assert json.loads(path.read_text(encoding="utf-8")) == {"siq_smoke": True}
    path.unlink()
    path.write_text('{"siq_smoke":2}\n', encoding="utf-8")

try:
    (home / ".siq-control-parent-write-probe").write_text("denied\n", encoding="ascii")
except OSError:
    pass
else:
    raise AssertionError("Hermes control home parent is writable")

path = Path(_auth_file_path())
assert path == Path("/sandbox/runtime-auth/auth.json")
entries = read_credential_pool("minimax-cn")
assert isinstance(entries, list) and len(entries) == 2
entries[0]["request_count"] += 1
assert write_credential_pool("minimax-cn", entries) == path
assert json.loads(path.read_text(encoding="utf-8"))["credential_pool"]["minimax-cn"][0]["request_count"] == 1
PY

docker exec "$CONTAINER" sh -c '
  set -eu
  /opt/siq/hermes/venv/bin/python /opt/siq/validate_placeholder_auth.py \
    --auth-file "$HERMES_AUTH_FILE" \
    --lock-file "$HERMES_AUTH_FILE.lock"
  test ! -e "$HERMES_HOME/auth.json"
'

# A normal container stop must let Hermes persist the clean-shutdown marker in
# the dedicated runtime-state bind, proving the actual gateway uses the route.
docker stop --time 30 "$CONTAINER" >/dev/null
test -f "$RUNTIME_SMOKE_HOST_ROOT/.clean_shutdown"
test ! -e "$RUNTIME_SMOKE_HOST_ROOT/.siq-control-parent-write-probe"

IMAGE_ID="$(docker image inspect "$IMAGE_REF" --format '{{.Id}}')"
CANDIDATE_STATE_SHA256="$(sha256sum -- "$STATE_FILE" | awk '{print $1}')"
SMOKE_SCRIPT_SHA256="$(sha256sum -- "$0" | awk '{print $1}')"
temporary_smoke_state="$(mktemp "${SMOKE_STATE_FILE}.XXXXXX")"
trap 'rm -f -- "${temporary_smoke_state:-}"; cleanup' EXIT INT TERM
python3 - "$temporary_smoke_state" "$IMAGE_REF" "$IMAGE_ID" \
  "$CANDIDATE_STATE_SHA256" "$SMOKE_SCRIPT_SHA256" "$RUNTIME_SMOKE_RESULT" <<'PY'
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

output, image_ref, image_id, candidate_sha256, script_sha256, runtime_result = sys.argv[1:]
runtime_lifecycle = json.load(open(runtime_result, encoding="utf-8"))
payload = {
    "schema_version": "siq.openshell.candidate_image_smoke.v1",
    "status": "passed",
    "profile": "siq_analysis",
    "image_ref": image_ref,
    "image_id": image_id,
    "candidate_state_sha256": candidate_sha256,
    "smoke_script_sha256": script_sha256,
    "readiness_effect": "none",
    "runtime_lifecycle": runtime_lifecycle,
    "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    "checks": [
        "network_none",
        "non_root_user",
        "hermes_version_exact",
        "credential_absence",
        "runtime_state_writable",
        "runtime_metadata_materialized",
        "api_key_required",
        "hermes_auth_placeholder_persistence",
        "healthcheck",
        "runtime_lifecycle_two_rounds",
        "runtime_lifecycle_directory_bind",
    ],
}
with open(output, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
PY
chmod 0600 -- "$temporary_smoke_state"
mv -fT -- "$temporary_smoke_state" "$SMOKE_STATE_FILE"
temporary_smoke_state=""

printf 'SIQ siq_analysis image smoke: PASS (%s)\n' "$IMAGE_REF"
