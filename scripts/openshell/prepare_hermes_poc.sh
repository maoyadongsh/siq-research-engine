#!/usr/bin/env bash
# Reconstruct the frozen Hermes source into a secret-free, ignored build context.

set -euo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
BACKUP_DIR="$ROOT_DIR/var/openshell/backups/hermes-pre-upgrade-20260715T032730Z"
POC_SOURCE="$ROOT_DIR/infra/openshell/poc/hermes-minimal"
POC_STATE="$ROOT_DIR/var/openshell/poc/hermes-minimal"

readonly HERMES_COMMIT="ddb8d8fa842283ef651a6e4514f8f561f736c72e"
readonly BUNDLE_SHA256="b4625289b6f7d09e59ac16ff9423faf34ce24b327d9f2f4409c4b886f3aaf6c5"
readonly PATCH_SHA256="856d6e1820fe4f41669535a3e21c34a153e98318bcce90a607509c24d423d8c5"
readonly UNTRACKED_SHA256="61dcd829986f4ecb7ccb39f3ed6223d072d208d8fd7b7d1fbc6f4f8aa03614fd"

BUNDLE="$BACKUP_DIR/hermes-repository.bundle"
PATCH_FILE="$BACKUP_DIR/hermes-working-tree.patch"
UNTRACKED_ARCHIVE="$BACKUP_DIR/hermes-untracked-files.tar.gz"
CONTEXTS_DIR="$POC_STATE/contexts"

FIXTURE_SHA256="$({
  cd "$POC_SOURCE"
  sha256sum Dockerfile .dockerignore config.yaml entrypoint.sh model_stub.py
} | sha256sum | awk '{print $1}')"
readonly FIXTURE_SHA256
readonly CONTEXT_ID="${HERMES_COMMIT:0:12}-${PATCH_SHA256:0:12}-${FIXTURE_SHA256:0:12}"
FINAL_CONTEXT="$CONTEXTS_DIR/$CONTEXT_ID"

require_regular_file() {
  local path="$1"
  if [[ ! -f "$path" || -L "$path" ]]; then
    printf 'Required regular file is missing or unsafe: %s\n' "$path" >&2
    exit 2
  fi
}

verify_sha256() {
  local expected="$1"
  local path="$2"
  local actual
  actual="$(sha256sum -- "$path" | awk '{print $1}')"
  if [[ "$actual" != "$expected" ]]; then
    printf 'SHA-256 mismatch for %s\n' "$path" >&2
    exit 2
  fi
}

for path in "$BUNDLE" "$PATCH_FILE" "$UNTRACKED_ARCHIVE"; do
  require_regular_file "$path"
done
for path in Dockerfile .dockerignore config.yaml entrypoint.sh model_stub.py; do
  require_regular_file "$POC_SOURCE/$path"
done
verify_sha256 "$BUNDLE_SHA256" "$BUNDLE"
verify_sha256 "$PATCH_SHA256" "$PATCH_FILE"
verify_sha256 "$UNTRACKED_SHA256" "$UNTRACKED_ARCHIVE"

mkdir -p -- "$CONTEXTS_DIR"
chmod 0700 -- "$POC_STATE" "$CONTEXTS_DIR"

if [[ -e "$FINAL_CONTEXT" ]]; then
  if [[ ! -d "$FINAL_CONTEXT" || -L "$FINAL_CONTEXT" ]]; then
    printf 'Existing PoC context is not a safe directory: %s\n' "$FINAL_CONTEXT" >&2
    exit 2
  fi
  grep -Fxq "hermes_commit=$HERMES_COMMIT" "$FINAL_CONTEXT/SOURCE_BASELINE" \
    && grep -Fxq "patch_sha256=$PATCH_SHA256" "$FINAL_CONTEXT/SOURCE_BASELINE" \
    && grep -Fxq "fixture_sha256=$FIXTURE_SHA256" "$FINAL_CONTEXT/SOURCE_BASELINE" \
    || {
      printf 'Existing PoC context has unexpected provenance: %s\n' "$FINAL_CONTEXT" >&2
      exit 2
    }
  python3 "$SCRIPT_DIR/check_mount_safety.py" --mount-root "$FINAL_CONTEXT" >&2
  printf '%s\n' "$FINAL_CONTEXT"
  exit 0
fi

TMP_ROOT="$(mktemp -d "$POC_STATE/.context.XXXXXX")"
cleanup() {
  if [[ -n "${TMP_ROOT:-}" && "$TMP_ROOT" == "$POC_STATE"/.context.* && -d "$TMP_ROOT" ]]; then
    rm -rf -- "$TMP_ROOT"
  fi
}
trap cleanup EXIT

SOURCE_DIR="$TMP_ROOT/source"
CONTEXT_DIR="$TMP_ROOT/context"
git clone --quiet "$BUNDLE" "$SOURCE_DIR"
git -C "$SOURCE_DIR" checkout --quiet --detach "$HERMES_COMMIT"
if [[ "$(git -C "$SOURCE_DIR" rev-parse HEAD)" != "$HERMES_COMMIT" ]]; then
  printf '%s\n' 'Reconstructed Hermes commit does not match the frozen baseline.' >&2
  exit 2
fi
git -C "$SOURCE_DIR" apply --check "$PATCH_FILE"
git -C "$SOURCE_DIR" apply "$PATCH_FILE"

while IFS= read -r archive_path; do
  case "$archive_path" in
    ""|/*|../*|*/../*|..|*/..)
      printf 'Unsafe path in Hermes untracked archive: %s\n' "$archive_path" >&2
      exit 2
      ;;
  esac
done < <(tar -tzf "$UNTRACKED_ARCHIVE")
tar -xzf "$UNTRACKED_ARCHIVE" --no-same-owner --no-same-permissions -C "$SOURCE_DIR"

mkdir -p -- "$CONTEXT_DIR/hermes-agent" "$CONTEXT_DIR/poc"
root_files=(
  LICENSE MANIFEST.in README.md pyproject.toml uv.lock citation_standards.md
  run_agent.py model_tools.py toolsets.py batch_runner.py trajectory_compressor.py
  toolset_distributions.py cli.py hermes_bootstrap.py hermes_constants.py
  hermes_state.py hermes_time.py hermes_logging.py rl_cli.py utils.py
)
package_dirs=(agent tools hermes_cli gateway tui_gateway cron acp_adapter plugins providers)

for relative in "${root_files[@]}"; do
  require_regular_file "$SOURCE_DIR/$relative"
  cp -a -- "$SOURCE_DIR/$relative" "$CONTEXT_DIR/hermes-agent/$relative"
done
for relative in "${package_dirs[@]}"; do
  if [[ ! -d "$SOURCE_DIR/$relative" || -L "$SOURCE_DIR/$relative" ]]; then
    printf 'Required Hermes package directory is missing or unsafe: %s\n' "$relative" >&2
    exit 2
  fi
  cp -a -- "$SOURCE_DIR/$relative" "$CONTEXT_DIR/hermes-agent/$relative"
done

cp -a -- "$POC_SOURCE/Dockerfile" "$POC_SOURCE/.dockerignore" "$CONTEXT_DIR/"
cp -a -- \
  "$POC_SOURCE/config.yaml" \
  "$POC_SOURCE/entrypoint.sh" \
  "$POC_SOURCE/model_stub.py" \
  "$CONTEXT_DIR/poc/"

{
  printf 'schema=siq.openshell.hermes_poc_source.v1\n'
  printf 'hermes_commit=%s\n' "$HERMES_COMMIT"
  printf 'bundle_sha256=%s\n' "$BUNDLE_SHA256"
  printf 'patch_sha256=%s\n' "$PATCH_SHA256"
  printf 'untracked_sha256=%s\n' "$UNTRACKED_SHA256"
  printf 'fixture_sha256=%s\n' "$FIXTURE_SHA256"
} >"$CONTEXT_DIR/SOURCE_BASELINE"

find "$CONTEXT_DIR" -type d -exec chmod 0700 {} +
find "$CONTEXT_DIR" -type f -exec chmod u+rw,go-rwx {} +
chmod 0700 "$CONTEXT_DIR/poc/entrypoint.sh" "$CONTEXT_DIR/poc/model_stub.py"

python3 "$SCRIPT_DIR/check_mount_safety.py" --mount-root "$CONTEXT_DIR" >&2
(
  cd "$CONTEXT_DIR"
  find . -type f ! -name FILES.sha256 -print0 \
    | sort -z \
    | xargs -0 sha256sum \
    >FILES.sha256
)

mv -- "$CONTEXT_DIR" "$FINAL_CONTEXT"
printf '%s\n' "$FINAL_CONTEXT"
