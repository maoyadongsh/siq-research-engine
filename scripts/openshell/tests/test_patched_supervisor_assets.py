from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PATCH_ROOT = ROOT / "infra" / "openshell" / "patches" / "v0.0.83"


def test_landlock_patch_is_pinned_and_applies_to_the_known_release() -> None:
    patch = (PATCH_ROOT / "0001-landlock-mask-file-access.patch").read_text(encoding="utf-8")
    assert "access_for_path_fd" in patch
    assert "AccessFs::from_file(ABI::V2)" in patch
    assert "PathBeneath::new(path_fd, access)" in patch
    assert "nix::sys::stat::fstat" in patch


def test_builder_is_explicit_and_does_not_build_gateway_or_cli() -> None:
    script = (ROOT / "scripts" / "openshell" / "build_patched_supervisor.sh").read_text(encoding="utf-8")
    assert "cargo test --locked" in script
    assert "-p openshell-supervisor-process" in script
    assert "-p openshell-sandbox" in script
    assert "cargo build --locked --release" in script
    assert "-p openshell-gateway" not in script
    assert "-p openshell-cli" not in script
    assert "openshell-sandbox.upstream-v$VERSION" in script
    assert "d94630658eb1e62090281160db7cdc542c8cf6667d0c11ff7d9084251f86cfd6" in script
    assert "SIQ_SUPERVISOR_PATCH_SHA256" in script
    assert "SIQ_OPENSHELL_UPSTREAM_COMMIT" in script
    assert "EXPECTED_SOURCE_DIFF_SHA256" in script
    assert "--network=none" in script
    assert "readelf -l" in script
    assert "docker ps -aq" in script


def test_builder_validates_provenance_before_quiesce_and_verifies_rollback() -> None:
    script = (ROOT / "scripts" / "openshell" / "build_patched_supervisor.sh").read_text(encoding="utf-8")
    validate = script.rindex('validate_current_provenance "$current_sha"')
    quiesce = script.rindex("\nquiesce_gateway\n")
    post_quiesce = script.rindex('sha256_matches "$current_sha" "$SUPERVISOR_BIN"')

    assert validate < quiesce < post_quiesce
    assert 'sha256_matches "$previous_sha" "$rollback_bin"' in script
    assert 'sha256_matches "$previous_sha" "$SUPERVISOR_BIN"' in script
    assert 'runtime_state_matches "$previous_sha"' in script
    assert "supervisor rollback failed verification; the gateway will remain stopped" in script
    assert 'restart_gateway_allowed" -ne 1' in script


def test_builder_checks_state_paths_before_and_after_creation() -> None:
    script = (ROOT / "scripts" / "openshell" / "build_patched_supervisor.sh").read_text(encoding="utf-8")
    function = script.split("ensure_state_dir() {", 1)[1].split("\n}\n", 1)[0]
    checks = [
        index for index in range(len(function)) if function.startswith('siq_openshell_assert_state_path "$path"', index)
    ]
    install = function.index('install -d -m 700 -- "$path"')

    assert len(checks) == 2
    assert checks[0] < install < checks[1]


def test_restore_is_locked_atomic_and_updates_runtime_state() -> None:
    script = (ROOT / "scripts" / "openshell" / "restore_upstream_supervisor.sh").read_text(encoding="utf-8")
    assert "siq_openshell_acquire_maintenance_lock" in script
    assert "mktemp" in script
    assert "active=upstream" in script
    assert "assert_no_managed_sandboxes" in script
    assert "openshell-sandbox.rollback" in script
    assert "proceeding with the verified upstream recovery image" in script
    assert "Current supervisor has no trustworthy runtime record.\n' >&2\n    exit 2" not in script
    current_audit = script.index('current_sha="$(sha256sum -- "$BIN"')
    stop_gateway = script.index('SIQ_OPENSHELL_MAINTENANCE_LOCK_HELD=1 "$SCRIPT_DIR/stop_gateway.sh"')
    final_verify = script.index('sha256_matches "$EXPECTED" "$BIN"')
    write_record = script.index("write_upstream_record", script.index('mv -f -- "$temporary" "$BIN"'))
    assert current_audit < stop_gateway
    assert final_verify < write_record
    assert 'sha256_matches "$previous_sha" "$rollback_bin"' in script
    assert 'sha256_matches "$previous_sha" "$BIN"' in script
    assert 'runtime_state_matches "$previous_sha"' in script
    assert "rollback was not verified; the gateway will remain stopped" in script


def test_builder_base_and_rust_distribution_are_digest_pinned() -> None:
    dockerfile = (PATCH_ROOT / "Dockerfile.builder").read_text(encoding="utf-8")
    assert (
        "python:3.11.15-slim-bookworm@sha256:b18992999dbe963a45a8a4da40ac2b1975be1a776d939d098c647482bcad5cba"
        in dockerfile
    )
    assert "https://static.rust-lang.org/dist/2026-04-16/rust-1.95.0-aarch64-unknown-linux-gnu.tar.xz" in dockerfile
    assert "094c9c36531911c5cc7dd6ab2d3069ab8dcd744d6239b0bda1387b243dfc391e" in dockerfile
    assert "FROM rust:" not in dockerfile


def test_poc_policy_keeps_exact_device_paths() -> None:
    policy = (ROOT / "infra" / "openshell" / "poc" / "hermes-minimal" / "policy.yaml").read_text(encoding="utf-8")
    assert "/dev/urandom" in policy
    assert "/dev/null" in policy
    assert "    - /dev\n" not in policy


def test_poc_start_requires_the_reviewed_supervisor_record() -> None:
    script = (ROOT / "scripts" / "openshell" / "start_hermes_poc.sh").read_text(encoding="utf-8")
    assert "supervisor-patch.runtime" in script
    assert "recorded_active" in script
    assert "recorded_binary" in script
