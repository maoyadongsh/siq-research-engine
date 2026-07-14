from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "meeting" / "evaluate_performance_release.py"
TEMPLATE = ROOT / "scripts" / "meeting" / "templates" / "performance-release-evidence.v1.json"
SPEC = importlib.util.spec_from_file_location("evaluate_performance_release", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _payload() -> dict:
    samples = [
        {
            "elapsed_seconds": index * 60,
            "rss_bytes": 100 * 1024 * 1024 + index * 4096,
            "open_handles": 20,
            "queue_depth": index % 2,
        }
        for index in range(241)
    ]
    return {
        "schema_version": MODULE.INPUT_SCHEMA_VERSION,
        "evaluation_id": "performance-eval-2026q3-v1",
        "authorization": {
            "approved": True,
            "all_observations_authorized": True,
            "unapproved_production_or_historical_observations": 0,
            "reference": "privacy-review-2026q3",
        },
        "candidate": {
            "commit_sha": "a" * 40,
            "environment_profile": "release-linux-cpu-v1",
        },
        "concurrency": {
            "release": 5,
            "overload": 6,
            "measurements_at_or_above_overload": True,
        },
        "imports": [
            {
                "audio_duration_seconds": 600,
                "upload_complete_to_ready_seconds": 150,
                "final_asr_audio_seconds": 600,
                "final_asr_processing_seconds": 120,
            }
            for _ in range(3)
        ],
        "ai_pipeline": {
            "enqueue_ms": [40] * 20,
            "job_queue_to_complete_seconds": [150] * 20,
            "rolling_minutes_freshness_seconds": [80] * 20,
            "final_minutes_after_last_stable_seconds": [160] * 20,
        },
        "soak": {
            "duration_seconds": 14_400,
            "expected_sample_interval_seconds": 60,
            "process_memory_limit_bytes": 1024 * 1024 * 1024,
            "open_handle_limit": 100,
            "queue_capacity": 100,
            "samples": samples,
        },
        "hermes_outage": {
            "duration_seconds": 1_800,
            "caption_latency_baseline_p95_seconds": 2.0,
            "caption_latency_outage_p95_seconds": 2.2,
            "stable_segments_lost": 0,
        },
        "recovery": {
            "gateway_restart_runs": 1,
            "gateway_restart_recovered_runs": 1,
            "worker_restart_runs": 1,
            "worker_restart_recovered_runs": 1,
            "database_outage_runs": 1,
            "database_outage_explicit_gap_or_safe_stop_runs": 1,
            "storage_failure_runs": 1,
            "storage_failure_explicit_gap_or_safe_stop_runs": 1,
            "stable_segments_lost": 0,
            "stable_segments_duplicated": 0,
            "duplicate_artifacts": 0,
            "model_cross_use_count": 0,
        },
    }


def _evaluate(payload: dict) -> dict:
    return MODULE.evaluate(payload, source_sha256="b" * 64)


def test_exact_release_boundaries_pass_with_aggregate_only_report():
    payload = _payload()
    payload["evaluation_id"] = "private-evaluation-reference-91"
    payload["authorization"]["reference"] = "private-approval-reference-82"

    report = _evaluate(payload)

    assert report["passed"] is True
    assert report["metrics"]["import_to_ready_rtf_p95"] == 0.25
    assert report["metrics"]["final_asr_rtf_p95"] == 0.2
    assert report["metrics"]["ai_enqueue_p95_ms"] == 40
    assert report["metrics"]["rolling_minutes_freshness_p95_seconds"] == 80
    assert report["metrics"]["final_minutes_after_last_stable_p95_seconds"] == 160
    assert report["metrics"]["soak_observed_duration_seconds"] == 14_400
    serialized = json.dumps(report, sort_keys=True)
    assert "private-evaluation-reference-91" not in serialized
    assert "private-approval-reference-82" not in serialized
    assert report["privacy_boundary"]["raw_sensitive_data_emitted"] is False


@pytest.mark.parametrize(
    ("mutation", "failed_gate"),
    [
        (
            lambda payload: payload["imports"][0].__setitem__("final_asr_processing_seconds", 151),
            "final_asr_rtf_at_most_0_25",
        ),
        (
            lambda payload: payload["ai_pipeline"].__setitem__("enqueue_ms", [40] * 18 + [51, 51]),
            "ai_enqueue_p95_at_most_50ms",
        ),
        (
            lambda payload: payload["ai_pipeline"].__setitem__(
                "rolling_minutes_freshness_seconds", [80] * 18 + [91, 91]
            ),
            "rolling_minutes_freshness_p95_at_most_90s",
        ),
        (
            lambda payload: payload["ai_pipeline"].__setitem__(
                "final_minutes_after_last_stable_seconds", [160] * 18 + [181, 181]
            ),
            "final_minutes_after_last_stable_p95_at_most_180s",
        ),
    ],
)
def test_one_observation_beyond_release_threshold_fails(mutation, failed_gate):
    payload = _payload()
    mutation(payload)

    report = _evaluate(payload)

    assert report["passed"] is False
    assert failed_gate in report["failures"]


def test_unapproved_and_insufficient_template_is_deliberately_blocked():
    report = _evaluate(json.loads(TEMPLATE.read_text(encoding="utf-8")))

    assert report["passed"] is False
    assert "authorization_approved" in report["failures"]
    assert "sample_import_observations" in report["failures"]
    assert "sample_ai_enqueue_observations" in report["failures"]
    assert "soak_observed_duration_at_least_4h" in report["failures"]
    assert "gateway_restart_recovered" in report["failures"]


def test_short_sparse_and_growing_soak_cannot_pass():
    payload = _payload()
    payload["soak"]["duration_seconds"] = 13_000
    payload["soak"]["samples"] = payload["soak"]["samples"][::4]
    for index, sample in enumerate(payload["soak"]["samples"]):
        sample["rss_bytes"] = 100 * 1024 * 1024 + index * 32 * 1024 * 1024
        sample["open_handles"] = 20 + index
        sample["queue_depth"] = min(99, index)

    report = _evaluate(payload)

    assert report["passed"] is False
    assert "soak_declared_duration_at_least_4h" in report["failures"]
    assert "soak_sample_gaps_bounded" in report["failures"]
    assert "rss_slope_bounded" in report["failures"]
    assert "open_handles_slope_bounded" in report["failures"]
    assert "queue_depth_slope_bounded" in report["failures"]


def test_missing_or_sensitive_fields_are_rejected_without_value_leak(tmp_path):
    payload = _payload()
    del payload["ai_pipeline"]["enqueue_ms"]
    with pytest.raises(MODULE.EvaluationInputError, match="missing required fields"):
        _evaluate(payload)

    payload = _payload()
    payload["soak"]["expected_sample_interval_seconds"] = 0.5
    with pytest.raises(MODULE.EvaluationInputError, match="at least one second"):
        _evaluate(payload)

    payload = _payload()
    payload["audio_path"] = "/private/meeting/alice.wav"
    source = tmp_path / "PRIVATE-SOURCE-NAME.json"
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
    assert "/private/meeting/alice.wav" not in diagnostic
    assert "PRIVATE-SOURCE-NAME" not in diagnostic


def test_cli_require_passing_returns_one_and_writes_redacted_reports(tmp_path):
    payload = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    payload["evaluation_id"] = "private-eval-secret-71"
    payload["authorization"]["reference"] = "private-approval-secret-72"
    source = tmp_path / "input.json"
    output = tmp_path / "report.json"
    markdown = tmp_path / "report.md"
    source.write_text(json.dumps(payload), encoding="utf-8")

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
    assert json.loads(output.read_text(encoding="utf-8"))["passed"] is False
    combined = output.read_text(encoding="utf-8") + markdown.read_text(encoding="utf-8")
    assert "private-eval-secret-71" not in combined
    assert "private-approval-secret-72" not in combined
