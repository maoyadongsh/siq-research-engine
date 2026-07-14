from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "meeting" / "verify_release_evidence_bundle.py"
SPEC = importlib.util.spec_from_file_location("verify_release_evidence_bundle", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

COMMIT = "e" * 40
ENVIRONMENT = "release-linux-cpu-v1"


def _report(name: str) -> dict:
    contract = MODULE.REPORT_CONTRACTS[name]
    privacy = dict(contract["privacy"])
    common = {
        "schema_version": contract["schema_version"],
        "input_schema_version": contract["input_schema_version"],
        "evaluation_policy_version": contract["policy_version"],
        "source_sha256": hashlib.sha256(name.encode()).hexdigest(),
        "candidate": {"commit_sha": COMMIT, "environment_profile": ENVIRONMENT},
        "limits": {},
        "minimum_sample": {},
        "metrics": {},
        "gates": {"release_gate": True},
        "failures": [],
        "passed": True,
        "privacy_boundary": privacy,
    }
    if name == "asr":
        common["coverage"] = {}
    elif name == "voiceprint":
        common.update(
            {
                "suggestion_release_validated": True,
                "auto_match_validated": True,
                "release_mode": "auto_match",
                "threshold_policy": {},
                "environment": {},
            }
        )
    else:
        common["recovery_counts"] = {}
    assert set(common) == contract["keys"]
    return common


def _reports() -> dict[str, tuple[dict, str]]:
    return {name: (_report(name), str(index) * 64) for index, name in enumerate(MODULE.REPORT_CONTRACTS, 1)}


def _fixture_module(filename: str):
    path = SCRIPT.parent / "tests" / filename
    spec = importlib.util.spec_from_file_location(f"bundle_fixture_{path.stem}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_three_passing_redacted_reports_are_bound_to_one_candidate():
    receipt = MODULE.verify(_reports(), expected_commit=COMMIT)

    assert receipt["passed"] is True
    assert receipt["candidate"] == {"commit_sha": COMMIT, "environment_profile": ENVIRONMENT}
    assert set(receipt["reports"]) == {"asr", "voiceprint", "performance"}
    assert receipt["privacy_boundary"]["redacted_reports_only"] is True


def test_actual_evaluator_reports_match_bundle_contract():
    fixture_names = {
        "asr": "test_evaluate_asr_release.py",
        "voiceprint": "test_evaluate_voiceprint_release.py",
        "performance": "test_evaluate_performance_release.py",
    }
    reports = {}
    for index, (name, filename) in enumerate(fixture_names.items(), 1):
        fixture = _fixture_module(filename)
        payload = fixture._payload()
        payload["candidate"] = {"commit_sha": COMMIT, "environment_profile": ENVIRONMENT}
        report = fixture.MODULE.evaluate(payload, source_sha256=str(index) * 64)
        assert report["passed"] is True
        reports[name] = (report, str(index + 3) * 64)

    receipt = MODULE.verify(reports, expected_commit=COMMIT)

    assert receipt["passed"] is True


@pytest.mark.parametrize("name", ["asr", "voiceprint", "performance"])
def test_nonpassing_or_incomplete_component_blocks_bundle(name):
    reports = _reports()
    reports[name][0]["passed"] = False

    with pytest.raises(MODULE.BundleVerificationError, match=f"{name} report has not passed"):
        MODULE.verify(reports, expected_commit=COMMIT)

    reports = _reports()
    reports[name][0]["gates"]["release_gate"] = False
    with pytest.raises(MODULE.BundleVerificationError, match="gates are incomplete"):
        MODULE.verify(reports, expected_commit=COMMIT)


def test_candidate_environment_and_privacy_mismatch_block_bundle():
    reports = _reports()
    reports["asr"][0]["candidate"]["commit_sha"] = "f" * 40
    with pytest.raises(MODULE.BundleVerificationError, match="different candidate"):
        MODULE.verify(reports, expected_commit=COMMIT)

    reports = _reports()
    reports["voiceprint"][0]["candidate"]["environment_profile"] = "release-gpu-v1"
    with pytest.raises(MODULE.BundleVerificationError, match="different environment"):
        MODULE.verify(reports, expected_commit=COMMIT)

    reports = _reports()
    reports["performance"][0]["privacy_boundary"]["raw_sensitive_data_emitted"] = True
    with pytest.raises(MODULE.BundleVerificationError, match="privacy boundary"):
        MODULE.verify(reports, expected_commit=COMMIT)


def test_cli_failure_does_not_echo_sensitive_report_values(tmp_path):
    paths = {}
    for name in MODULE.REPORT_CONTRACTS:
        report = _report(name)
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(report), encoding="utf-8")
    payload = json.loads(paths["asr"].read_text())
    payload["speaker_name"] = "PRIVATE-SPEAKER-ALICE"
    payload["passed"] = False
    paths["asr"].write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "receipt.json"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--asr",
            str(paths["asr"]),
            "--voiceprint",
            str(paths["voiceprint"]),
            "--performance",
            str(paths["performance"]),
            "--candidate-commit",
            COMMIT,
            "--output",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert not output.exists()
    assert "PRIVATE-SPEAKER-ALICE" not in result.stdout + result.stderr
