#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_DIR="${SIQ_SECURITY_ARTIFACT_DIR:-artifacts/security}"
TRIVY_IMAGE="${SIQ_TRIVY_IMAGE:-aquasec/trivy:0.58.2}"

if [[ "$OUT_DIR" != /* ]]; then
    OUT_DIR="$ROOT_DIR/$OUT_DIR"
fi

mkdir -p "$OUT_DIR"

SUMMARY_FILE="$OUT_DIR/observe-summary.txt"
TRIVY_HIGH_REPORT="$OUT_DIR/trivy-high-observe.sarif"
SBOM_REPORT="$OUT_DIR/siq-fs-sbom.cyclonedx.json"

: >"$SUMMARY_FILE"

record() {
    printf '%s\n' "$*" | tee -a "$SUMMARY_FILE"
}

have_docker() {
    command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1
}

write_skip_artifacts() {
    local reason=$1
    printf 'Trivy HIGH observe scan skipped: %s\n' "$reason" >"$OUT_DIR/trivy-high-observe.skipped.txt"
    printf '{\n  "status": "skipped",\n  "reason": "%s"\n}\n' "$reason" >"$OUT_DIR/siq-fs-sbom.skipped.json"
    record "Skipped observe artifacts: $reason"
}

run_local_trivy_high() {
    trivy fs \
        --scanners vuln,secret,misconfig \
        --severity HIGH,CRITICAL \
        --ignore-unfixed \
        --skip-dirs "$ROOT_DIR/data" \
        --skip-dirs "$ROOT_DIR/var" \
        --skip-dirs "$ROOT_DIR/artifacts" \
        --skip-dirs "$ROOT_DIR/runtimes" \
        --exit-code 0 \
        --format sarif \
        --output "$TRIVY_HIGH_REPORT" \
        "$ROOT_DIR"
}

run_local_sbom() {
    trivy fs \
        --skip-dirs "$ROOT_DIR/data" \
        --skip-dirs "$ROOT_DIR/var" \
        --skip-dirs "$ROOT_DIR/artifacts" \
        --skip-dirs "$ROOT_DIR/runtimes" \
        --format cyclonedx \
        --output "$SBOM_REPORT" \
        "$ROOT_DIR"
}

run_docker_trivy_high() {
    docker run --rm \
        --user "$(id -u):$(id -g)" \
        --env TRIVY_CACHE_DIR=/tmp/trivy-cache \
        -v "$ROOT_DIR:/repo:ro" \
        -v "$OUT_DIR:/out" \
        "$TRIVY_IMAGE" fs \
        --scanners vuln,secret,misconfig \
        --severity HIGH,CRITICAL \
        --ignore-unfixed \
        --skip-dirs /repo/data \
        --skip-dirs /repo/var \
        --skip-dirs /repo/artifacts \
        --skip-dirs /repo/runtimes \
        --exit-code 0 \
        --format sarif \
        --output /out/trivy-high-observe.sarif \
        /repo
}

run_docker_sbom() {
    docker run --rm \
        --user "$(id -u):$(id -g)" \
        --env TRIVY_CACHE_DIR=/tmp/trivy-cache \
        -v "$ROOT_DIR:/repo:ro" \
        -v "$OUT_DIR:/out" \
        "$TRIVY_IMAGE" fs \
        --skip-dirs /repo/data \
        --skip-dirs /repo/var \
        --skip-dirs /repo/artifacts \
        --skip-dirs /repo/runtimes \
        --format cyclonedx \
        --output /out/siq-fs-sbom.cyclonedx.json \
        /repo
}

record_observe_status() {
    local label=$1
    local status=$2

    if [[ "$status" -eq 0 ]]; then
        record "$label: generated"
        return 0
    fi

    record "$label: observe command failed with exit $status"
    printf '%s failed with exit %s; observe-only step did not fail CI.\n' "$label" "$status" >"$OUT_DIR/${label// /-}.error.txt"
    return 0
}

run_local_observe() {
    run_local_trivy_high
    record_observe_status "trivy-high-observe" "$?"

    run_local_sbom
    record_observe_status "sbom-cyclonedx" "$?"
}

run_docker_observe() {
    run_docker_trivy_high
    record_observe_status "trivy-high-observe" "$?"

    run_docker_sbom
    record_observe_status "sbom-cyclonedx" "$?"
}

record "SIQ DevOps observe artifacts"
record "output_dir=$OUT_DIR"

if command -v trivy >/dev/null 2>&1; then
    record "scanner=local trivy"
    run_local_observe
elif have_docker; then
    record "scanner=docker $TRIVY_IMAGE"
    run_docker_observe
else
    write_skip_artifacts "neither trivy nor a usable Docker daemon is available"
fi

record "observe_status=complete"
exit 0
