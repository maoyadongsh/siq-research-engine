from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/openshell/build_patched_gateway.sh"


def test_gateway_maintenance_resets_and_attests_running_process_state() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    function = script[script.index("detect_running_gateway()") : script.index("quiesce_gateway()")]

    assert function.index("gateway_was_running=0") < function.index("GATEWAY_PID_FILE")
    assert "GATEWAY_PROCESS_RECORD" in function
    assert "gateway_runtime_identity.py" in function
    assert "verified PID" not in function


def test_install_journal_persists_original_process_identity() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    for field in ("gateway_pid", "gateway_start_ticks", "gateway_executable", "gateway_argv_sha256"):
        assert f"printf '{field}=%s" in script
        assert field in script[script.index("load_install_journal()") : script.index("clear_install_transaction()")]


def test_prepared_recovery_only_clears_after_full_original_runtime_match() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    recovery = script[script.index("restore_loaded_transaction()") : script.index("verify_committed_transaction()")]
    prepared = recovery[
        recovery.index('"$journal_phase" == prepared') : recovery.index("recovery_result=aborted_before_install")
    ]

    assert '"$config_ok" -eq 1' in prepared
    assert '"$recovery_gateway_running" -eq 1' in prepared
    assert '"$recovery_gateway_pid" == "$gateway_pid_snapshot"' in prepared
    assert "gateway_start_ticks_snapshot" in prepared
    assert "gateway_executable_snapshot" in prepared
    assert "gateway_argv_sha_snapshot" in prepared
    assert '"$config_ok" -ne 1' not in prepared


def test_config_drift_keeps_gateway_stopped_for_verified_recovery() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    recovery = script[script.index("restore_loaded_transaction()") : script.index("verify_committed_transaction()")]

    drift = recovery.index("Gateway configuration changed; the previous binary was restored")
    clear = recovery.index("clear_install_transaction", drift)
    assert "return 2" in recovery[drift:clear]


def test_only_the_reviewed_legacy_gateway_artifact_can_enter_upgrade_path() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    validate = script.index("validate_current_provenance()")
    capture = script.index('capture_protected_runtime\n')
    legacy = script[script.index("legacy_runtime_record_allows_upgrade()") : validate]

    assert validate < capture
    assert "64026fc68cdc0177297cfe648cfaf84abcf7630b04fee5280a1491b882d48dc4" in script
    assert "9f26b7c3e7af2eefdf0c22eef82472422865aa63c114091ccdc25ea9968cff00" in script
    assert "19fd64bc3f6f384dec7bb462a76a07cf88b87edbb5ca9dbc54ae9e18d800b637" in script
    assert 'grep -Fxq "patch_sha256=$LEGACY_MIGRATION_SOURCE_PATCH_SHA256"' in legacy
    assert 'sha256_matches "$UPSTREAM_GATEWAY_SHA256" "$UPSTREAM_BACKUP"' in legacy
    assert 'python3 "$LEGACY_MIGRATION_VERIFIER"' in legacy
    assert 'runtime_record_matches "$current_sha" || legacy_runtime_record_allows_upgrade "$current_sha"' in script
