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
        contract["policy_key"]: contract["policy_version"],
        "source_sha256": hashlib.sha256(name.encode()).hexdigest(),
        "candidate": {"commit_sha": COMMIT, "environment_profile": ENVIRONMENT},
        "limits": {},
        "minimum_sample": {},
        "metrics": {},
        "gates": {
            key: True
            for key in contract.get("required_gate_keys", {"release_gate"})
        },
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
    elif name == "performance":
        common["recovery_counts"] = {}
    else:
        common.update(
            {
                "evidence_manifest_schema_version": "siq.meeting.diarization-release-evidence.v1",
                "evidence_manifest_sha256": "a" * 64,
                "policy": {
                    "schema_version": "siq.meeting.speaker_recluster_policy.v1",
                    "version": "speaker-recluster.validated.release-test.v1",
                    "final_diarizer_ref": "final-diarizer-release-test-v1",
                    "encoder_ref": "encoder-release-test-v1",
                    "thresholds": {
                        "review_min_score": 0.72,
                        "merge_min_score": 0.82,
                        "singleton_merge_min_score": 0.92,
                        "min_top2_margin": 0.04,
                        "min_segment_ms": 1_500,
                        "max_segment_ms": 8_000,
                        "max_samples_per_track": 4,
                        "max_total_samples": 256,
                        "max_tracks": 64,
                        "max_noise_level": 0.65,
                        "min_asr_confidence": 0.45,
                        "min_rms": 0.003,
                        "max_clipping_ratio": 0.01,
                    },
                },
                "scoring_protocol": dict(MODULE._DIARIZATION_SCORING_PROTOCOL),
                "limits": dict(MODULE._DIARIZATION_LIMITS),
                "minimum_sample": dict(MODULE._DIARIZATION_MINIMUM_SAMPLE),
                "coverage": {"reference_speaker_counts_covered": list(range(2, 9))},
                "metrics": {
                    "recording_count": 14,
                    "reference_speaker_count": 70,
                    "unique_reference_speaker_count": 70,
                    "hypothesis_track_count": 70,
                    "unapproved_production_or_historical_recordings": 0,
                    "speaker_split_overlap_count": 0,
                    "recording_split_overlap_count": 0,
                    "reference_speaker_time_ms": 4_200_000,
                    "hypothesis_speaker_time_ms": 4_200_000,
                    "missed_speech_ms": 0,
                    "false_alarm_speech_ms": 0,
                    "speaker_confusion_ms": 0,
                    "diarization_error_ms": 0,
                    "missed_speech_rate": 0,
                    "false_alarm_speech_rate": 0,
                    "speaker_confusion_rate": 0,
                    "diarization_error_rate": 0,
                    "fragmented_reference_speakers": 0,
                    "fragmentation_excess_tracks": 0,
                    "fragmentation_rate": 0,
                    "predicted_tracks_per_reference_histogram": {"1": 70},
                    "over_merged_hypothesis_tracks": 0,
                    "over_merge_excess_speakers": 0,
                    "references_on_over_merged_tracks": 0,
                    "over_merge_rate": 0,
                    "purity_numerator_ms": 4_200_000,
                    "purity_denominator_ms": 4_200_000,
                    "track_purity": 1,
                },
            }
        )
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


def test_four_passing_redacted_reports_are_bound_to_one_candidate():
    receipt = MODULE.verify(_reports(), expected_commit=COMMIT, expected_environment=ENVIRONMENT)

    assert receipt["passed"] is True
    assert receipt["candidate"] == {"commit_sha": COMMIT, "environment_profile": ENVIRONMENT}
    assert set(receipt["reports"]) == {"asr", "voiceprint", "performance", "diarization"}
    assert receipt["privacy_boundary"]["redacted_reports_only"] is True


def test_actual_evaluator_reports_match_bundle_contract():
    fixture_names = {
        "asr": "test_evaluate_asr_release.py",
        "voiceprint": "test_evaluate_voiceprint_release.py",
        "performance": "test_evaluate_performance_release.py",
        "diarization": "test_evaluate_diarization_release.py",
    }
    reports = {}
    for index, (name, filename) in enumerate(fixture_names.items(), 1):
        fixture = _fixture_module(filename)
        if name == "diarization":
            reference, hypothesis = fixture._release_sized_annotations()
            manifest = fixture._manifest_payload(reference)
            manifest["candidate"] = {"commit_sha": COMMIT, "environment_profile": ENVIRONMENT}
            report = fixture._evaluate(reference, hypothesis, manifest_payload=manifest)
        else:
            payload = fixture._payload()
            payload["candidate"] = {"commit_sha": COMMIT, "environment_profile": ENVIRONMENT}
            report = fixture.MODULE.evaluate(payload, source_sha256=str(index) * 64)
        assert report["passed"] is True
        reports[name] = (report, str(index + 3) * 64)

    receipt = MODULE.verify(reports, expected_commit=COMMIT)

    assert receipt["passed"] is True


@pytest.mark.parametrize("name", ["asr", "voiceprint", "performance", "diarization"])
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

    with pytest.raises(MODULE.BundleVerificationError, match="approved environment"):
        MODULE.verify(_reports(), expected_commit=COMMIT, expected_environment="release-gpu-v1")

    reports = _reports()
    reports["performance"][0]["privacy_boundary"]["raw_sensitive_data_emitted"] = True
    with pytest.raises(MODULE.BundleVerificationError, match="privacy boundary"):
        MODULE.verify(reports, expected_commit=COMMIT)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda report: report["metrics"].__setitem__("reference_speaker_time_ms", 1),
        lambda report: report["metrics"].__setitem__("unapproved_production_or_historical_recordings", 1),
        lambda report: report["metrics"].__setitem__("speaker_split_overlap_count", 1),
        lambda report: report["metrics"].__setitem__("hypothesis_track_count", 0),
        lambda report: report["policy"]["thresholds"].__setitem__("review_min_score", True),
        lambda report: report["policy"].__setitem__("version", ".validated."),
    ],
    ids=(
        "insufficient-reference-time",
        "unauthorized-recording",
        "speaker-split-overlap",
        "impossible-hypothesis-count",
        "boolean-threshold",
        "invalid-policy-version",
    ),
)
def test_diarization_internal_inconsistency_blocks_bundle_without_raw_exception(mutation):
    reports = _reports()
    mutation(reports["diarization"][0])

    with pytest.raises(MODULE.BundleVerificationError):
        MODULE.verify(reports, expected_commit=COMMIT, expected_environment=ENVIRONMENT)


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
            "--diarization",
            str(paths["diarization"]),
            "--candidate-commit",
            COMMIT,
            "--expected-environment",
            ENVIRONMENT,
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
