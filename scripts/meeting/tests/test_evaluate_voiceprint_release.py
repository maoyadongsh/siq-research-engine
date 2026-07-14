from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "meeting" / "evaluate_voiceprint_release.py"
TEMPLATE = ROOT / "scripts" / "meeting" / "templates" / "voiceprint-release-evidence.v1.json"
SPEC = importlib.util.spec_from_file_location("evaluate_voiceprint_release", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _payload() -> dict:
    return {
        "schema_version": MODULE.INPUT_SCHEMA_VERSION,
        "evaluation_id": "voiceprint-eval-2026q3-v1",
        "authorization": {
            "approved": True,
            "all_trials_authorized": True,
            "unapproved_production_or_historical_trials": 0,
            "reference": "privacy-review-2026q3",
        },
        "candidate": {
            "commit_sha": "d" * 40,
            "environment_profile": "release-linux-cpu-v1",
        },
        "split": {
            "kind": "independent_holdout",
            "independent_from_training": True,
            "independent_from_threshold_tuning": True,
            "speaker_overlap_count": 0,
            "recording_overlap_count": 0,
        },
        "threshold_policy": {
            "version": "voiceprint-thresholds.zh-cn.v1",
            "suggestion_min_score": 0.8,
            "suggestion_min_margin": 0.12,
            "auto_min_score": 0.92,
            "auto_min_margin": 0.2,
            "min_effective_duration_ms": 6_000,
            "allowed_quality_grades": ["good"],
        },
        "aggregates": {
            "diarization": {
                "session_count": 14,
                "speaker_counts_covered": [2, 3, 4, 5, 6, 7, 8],
                "clean_condition_only": True,
                "reference_speaker_time_ms": 3_600_000,
                "missed_speech_ms": 180_000,
                "false_alarm_speech_ms": 180_000,
                "speaker_confusion_ms": 180_000,
            },
            "matching": {
                "genuine_trials": 100,
                "suggestion_top1_predictions": 100,
                "suggestion_top1_correct": 95,
                "auto_impostor_trials": 3_000,
                "auto_false_accepts": 3,
                "threshold_policy_applied_to_all_trials": True,
            },
            "revocation": {
                "post_revoke_trials": 100,
                "post_revoke_new_matches": 0,
            },
            "template_authorization": {
                "persistent_templates_audited": 50,
                "unauthorized_templates": 0,
                "inventory_complete": True,
            },
        },
    }


def _evaluate(payload: dict) -> dict:
    return MODULE.evaluate(payload, source_sha256="a" * 64)


def test_exact_release_boundaries_pass_and_emit_runtime_env_json():
    report = _evaluate(_payload())

    assert report["metrics"]["diarization_error_rate"] == 0.15
    assert report["metrics"]["suggestion_top1_precision"] == 0.95
    assert report["metrics"]["auto_false_accept_rate"] == 0.001
    assert report["suggestion_release_validated"] is True
    assert report["auto_match_validated"] is True
    assert report["release_mode"] == "auto_match"
    assert report["passed"] is True

    env_policy = json.loads(report["environment"]["SIQ_MEETING_VOICEPRINT_THRESHOLDS_JSON"])
    assert env_policy == report["threshold_policy"]
    assert env_policy == {
        "version": "voiceprint-thresholds.zh-cn.v1",
        "suggestion_min_score": 0.8,
        "suggestion_min_margin": 0.12,
        "auto_min_score": 0.92,
        "auto_min_margin": 0.2,
        "min_effective_duration_ms": 6_000,
        "allowed_quality_grades": ["good"],
        "auto_match_validated": True,
    }


@pytest.mark.parametrize(
    ("mutation", "failed_gate"),
    [
        (
            lambda payload: payload["aggregates"]["diarization"].__setitem__("speaker_confusion_ms", 180_001),
            "diarization_error_rate_at_most_15_percent",
        ),
        (
            lambda payload: payload["aggregates"]["matching"].__setitem__("suggestion_top1_correct", 94),
            "suggestion_top1_precision_at_least_95_percent",
        ),
        (
            lambda payload: payload["aggregates"]["matching"].__setitem__("auto_false_accepts", 4),
            "auto_false_accept_rate_at_most_0_1_percent",
        ),
    ],
)
def test_one_unit_beyond_each_quality_boundary_fails(mutation, failed_gate):
    payload = _payload()
    mutation(payload)

    report = _evaluate(payload)

    assert report["auto_match_validated"] is False
    assert failed_gate in report["failures"]


def test_insufficient_impostor_trials_fall_back_to_suggestion_only():
    payload = _payload()
    payload["aggregates"]["matching"]["auto_impostor_trials"] = 2_999
    payload["aggregates"]["matching"]["auto_false_accepts"] = 0

    report = _evaluate(payload)

    assert report["gates"]["auto_false_accept_rate_at_most_0_1_percent"] is True
    assert report["gates"]["sample_auto_impostor_trials"] is False
    assert report["suggestion_release_validated"] is True
    assert report["auto_match_validated"] is False
    assert report["release_mode"] == "suggestion_only"
    assert json.loads(report["environment"]["SIQ_MEETING_VOICEPRINT_THRESHOLDS_JSON"])["auto_match_validated"] is False


def test_non_independent_split_blocks_suggestion_and_auto_match():
    payload = _payload()
    payload["split"]["independent_from_threshold_tuning"] = False
    payload["split"]["speaker_overlap_count"] = 1

    report = _evaluate(payload)

    assert report["release_mode"] == "blocked"
    assert report["suggestion_release_validated"] is False
    assert report["auto_match_validated"] is False
    assert "independent_from_threshold_tuning" in report["failures"]
    assert "no_speaker_split_overlap" in report["failures"]


def test_authorization_revoke_and_template_failures_cannot_validate_policy():
    payload = _payload()
    payload["authorization"]["approved"] = False
    payload["authorization"]["all_trials_authorized"] = False
    payload["authorization"]["unapproved_production_or_historical_trials"] = 1
    payload["aggregates"]["revocation"]["post_revoke_new_matches"] = 1
    payload["aggregates"]["template_authorization"]["unauthorized_templates"] = 1

    report = _evaluate(payload)

    assert report["release_mode"] == "blocked"
    assert report["auto_match_validated"] is False
    assert {
        "authorization_approved",
        "all_trials_authorized",
        "no_unapproved_production_or_historical_trials",
        "post_revoke_new_matches_zero",
        "unauthorized_templates_zero",
    } <= set(report["failures"])


def test_cli_rejects_sensitive_fields_without_leaking_values(tmp_path):
    payload = _payload()
    payload["speaker_name"] = "PRIVATE-SPEAKER-ALICE"
    payload["audio_payload"] = "PRIVATE-AUDIO-BYTES"
    payload["embedding"] = ["PRIVATE-EMBEDDING-VALUE"]
    source = tmp_path / "evidence.json"
    output = tmp_path / "report.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(source), "--output", str(output)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert not output.exists()
    diagnostic = result.stdout + result.stderr
    assert "PRIVATE-SPEAKER-ALICE" not in diagnostic
    assert "PRIVATE-AUDIO-BYTES" not in diagnostic
    assert "PRIVATE-EMBEDDING-VALUE" not in diagnostic


def test_valid_report_does_not_propagate_input_references_or_sensitive_material():
    payload = _payload()
    payload["evaluation_id"] = "opaque-evaluation-secret-47"
    payload["authorization"]["reference"] = "opaque-approval-secret-83"

    serialized = json.dumps(_evaluate(payload), sort_keys=True)

    assert "opaque-evaluation-secret-47" not in serialized
    assert "opaque-approval-secret-83" not in serialized
    assert "speaker_name" not in serialized
    assert "audio_payload" not in serialized
    assert "embedding" not in serialized


def test_checked_in_template_is_deliberately_non_passing():
    payload = json.loads(TEMPLATE.read_text(encoding="utf-8"))

    report = _evaluate(payload)

    assert report["release_mode"] == "blocked"
    assert report["auto_match_validated"] is False
    assert report["passed"] is False
