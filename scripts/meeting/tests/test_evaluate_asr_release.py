from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "meeting" / "evaluate_asr_release.py"
TEMPLATE = ROOT / "scripts" / "meeting" / "templates" / "asr-release-evidence.v2.json"
SPEC = importlib.util.spec_from_file_location("evaluate_asr_release", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _payload() -> dict:
    cases = []
    for index in range(20):
        common = {
            "case_id": f"term-{index:02d}",
            "reference": "海光信息收入增长",
            "baseline": "海光新息收入增长",
            "final_candidate": "海光信息收入增长",
            "terms": ["海光信息"],
            "entities": {"company": ["海光信息"]},
        }
        cases.extend(
            [
                {**common, "variant": "lexicon_off", "candidate": "海光新息收入增长"},
                {
                    **common,
                    "variant": "lexicon_on",
                    "candidate": "海光信息收入增长",
                    "non_target_opportunities": 100,
                    "false_hotword_hits": 1 if index < 4 else 0,
                },
            ]
        )
    latencies = [
        {
            "partial_seconds": 1.0,
            "stable_seconds": 2.1,
            "stable_db_commit_ms": 180,
            "stable_to_visible_ms": 220,
            "ack_seconds": 0.25,
            "alignment_error_ms": 350,
        }
        for _ in range(100)
    ]
    return {
        "schema_version": MODULE.INPUT_SCHEMA_VERSION,
        "authorization": {
            "approved": True,
            "all_cases_authorized": True,
            "unapproved_production_or_historical_cases": 0,
            "reference": "privacy-review-2026q3",
        },
        "candidate": {
            "commit_sha": "c" * 40,
            "environment_profile": "release-linux-cpu-v1",
        },
        "dataset": {
            "name": "authorized-financial-holdout",
            "version": "version-v1",
            "independent_from_training": True,
            "independent_from_lexicon_tuning": True,
        },
        "coverage": {
            "authorized_audio_seconds": 1_800,
            "session_count": 3,
            "speaker_counts_covered": [2, 4, 8],
            "conditions_covered": [
                "quiet_room",
                "far_field",
                "light_accent",
                "speech_rate_variation",
                "overlap",
                "network_reconnect",
            ],
        },
        "cases": cases,
        "latencies": latencies,
    }


def _evaluate(payload: dict) -> dict:
    return MODULE.evaluate(payload, source_sha256="a" * 64)


def test_release_eval_passes_strict_limits_and_excludes_sensitive_text():
    payload = _payload()
    payload["dataset"]["name"] = "private-dataset-secret-73"
    payload["authorization"]["reference"] = "private-approval-secret-74"

    report = _evaluate(payload)

    assert report["passed"] is True
    assert report["metrics"]["partial_p95_seconds"] == 1.0
    assert report["metrics"]["stable_db_commit_p95_ms"] == 180
    assert report["metrics"]["lexicon"]["term_recall_on"] == 1.0
    assert report["metrics"]["lexicon"]["false_trigger_rate"] == 0.002
    serialized = json.dumps(report, ensure_ascii=False)
    assert "海光信息" not in serialized
    assert "海光新息" not in serialized
    assert "private-dataset-secret-73" not in serialized
    assert "private-approval-secret-74" not in serialized
    assert report["privacy_boundary"]["contains_transcript_text"] is False


@pytest.mark.parametrize(
    ("field", "value", "failed_gate"),
    [
        ("stable_seconds", 2.6, "stable_p95_at_most_2_5s"),
        ("stable_db_commit_ms", 201, "stable_db_commit_p95_at_most_200ms"),
        ("stable_to_visible_ms", 251, "stable_to_visible_p95_at_most_250ms"),
        ("ack_seconds", 0.31, "ack_p95_at_most_300ms"),
    ],
)
def test_latency_boundaries_fail(field, value, failed_gate):
    payload = _payload()
    for row in payload["latencies"][-6:]:
        row[field] = value

    report = _evaluate(payload)

    assert report["passed"] is False
    assert failed_gate in report["failures"]


def test_unapproved_and_insufficient_evidence_fails_closed():
    payload = json.loads(TEMPLATE.read_text(encoding="utf-8"))

    report = _evaluate(payload)

    assert report["passed"] is False
    assert "authorization_approved" in report["failures"]
    assert "sample_authorized_audio_at_least_30m" in report["failures"]
    assert "sample_distinct_asr_cases" in report["failures"]
    assert "sample_latency_observations" in report["failures"]
    assert "sample_lexicon_paired_cases" in report["failures"]


def test_final_transcript_worse_than_streaming_and_non_independent_split_fail():
    payload = _payload()
    payload["dataset"]["independent_from_training"] = False
    for case in payload["cases"]:
        case["final_candidate"] = "完全错误"

    report = _evaluate(payload)

    assert report["passed"] is False
    assert "dataset_independent_from_training" in report["failures"]
    assert "final_transcript_not_worse_than_streaming_final" in report["failures"]


def test_lexicon_pairs_must_use_the_same_holdout_material():
    payload = _payload()
    payload["cases"][1]["terms"] = ["不同术语"]

    with pytest.raises(MODULE.EvaluationInputError, match="lexicon on/off pairs"):
        _evaluate(payload)


def test_unknown_sensitive_field_and_malformed_values_are_rejected_without_leak(tmp_path):
    payload = _payload()
    payload["audio_path"] = "/protected/alice/meeting.wav"
    source = tmp_path / "PRIVATE-ASR-SOURCE.json"
    output = tmp_path / "report.json"
    source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

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
    assert "/protected/alice/meeting.wav" not in diagnostic
    assert "PRIVATE-ASR-SOURCE" not in diagnostic

    payload = _payload()
    payload["latencies"][0]["ack_seconds"] = -1
    with pytest.raises(MODULE.EvaluationInputError, match="non-negative"):
        _evaluate(payload)


def test_cli_require_passing_returns_one_but_writes_redacted_report(tmp_path):
    payload = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    payload["dataset"]["name"] = "private-dataset-secret-77"
    payload["authorization"]["reference"] = "private-approval-secret-78"
    source = tmp_path / "input.json"
    output = tmp_path / "report.json"
    markdown = tmp_path / "report.md"
    source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--input",
            str(source),
            "--output",
            str(output),
            "--markdown",
            str(markdown),
            "--require-passing",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["passed"] is False
    combined = output.read_text(encoding="utf-8") + markdown.read_text(encoding="utf-8")
    assert "private-dataset-secret-77" not in combined
    assert "private-approval-secret-78" not in combined
