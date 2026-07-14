#!/usr/bin/env python3
"""Evaluate aggregate meeting performance and four-hour soak evidence.

The input contract accepts numeric observations only. Reports never include
meeting identifiers, transcript text, audio locations, user identities, model
credentials, or authorization references.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

INPUT_SCHEMA_VERSION = "siq.meeting.performance-release-evidence.v1"
REPORT_SCHEMA_VERSION = "siq.meeting.performance-release-evaluation.v1"
EVALUATION_POLICY_VERSION = "siq.meeting.performance-release-gates.v1"
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_NUMERIC_VALUE = float(2**63 - 1)

LIMITS = {
    "import_to_ready_rtf_max": 0.30,
    "final_asr_rtf_max": 0.25,
    "ai_enqueue_p95_ms_max": 50.0,
    "ai_job_queue_to_complete_p95_seconds_max": 180.0,
    "rolling_minutes_freshness_p95_seconds_max": 90.0,
    "final_minutes_after_last_stable_p95_seconds_max": 180.0,
    "soak_duration_seconds_min": 14_400.0,
    "soak_sample_interval_seconds_max": 60.0,
    "steady_state_start_seconds": 1_800.0,
    "rss_slope_bytes_per_hour_max": 64 * 1024 * 1024,
    "rss_steady_net_growth_bytes_max": 256 * 1024 * 1024,
    "open_handles_slope_per_hour_max": 2.0,
    "open_handles_steady_net_growth_max": 8,
    "queue_depth_slope_per_hour_max": 1.0,
    "queue_depth_steady_net_growth_max": 2,
    "resource_peak_to_limit_ratio_max": 0.80,
    "hermes_outage_seconds_min": 1_800.0,
    "hermes_caption_latency_degradation_max": 0.10,
}

MINIMUM_SAMPLE = {
    "import_observations": 3,
    "import_audio_seconds": 1_800.0,
    "ai_enqueue_observations": 20,
    "ai_job_observations": 20,
    "rolling_minutes_observations": 20,
    "final_minutes_observations": 20,
}

_OPAQUE_REFERENCE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}\Z")
_COMMIT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class EvaluationInputError(ValueError):
    """Raised when evidence is malformed or outside the aggregate-only schema."""


def _exact_object(
    value: Any,
    *,
    required: frozenset[str],
    context: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvaluationInputError(f"{context} must be an object")
    keys = set(value)
    if not keys <= required:
        raise EvaluationInputError("input contains fields outside the aggregate-only schema")
    missing = sorted(required - keys)
    if missing:
        raise EvaluationInputError(f"{context} is missing required fields: {', '.join(missing)}")
    return value


def _boolean(value: Any, *, context: str) -> bool:
    if not isinstance(value, bool):
        raise EvaluationInputError(f"{context} must be a boolean")
    return value


def _count(value: Any, *, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 2**63 - 1:
        raise EvaluationInputError(f"{context} must be a non-negative integer")
    return value


def _number(value: Any, *, context: str, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationInputError(f"{context} must be a finite non-negative number")
    result = float(value)
    if not math.isfinite(result) or result < 0 or result > MAX_NUMERIC_VALUE or (positive and result == 0):
        qualifier = "positive" if positive else "non-negative"
        raise EvaluationInputError(f"{context} must be a finite {qualifier} number")
    return result


def _opaque_reference(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or _OPAQUE_REFERENCE_RE.fullmatch(value) is None:
        raise EvaluationInputError(f"{context} must be an opaque ASCII reference")
    return value


def _number_array(value: Any, *, context: str) -> list[float]:
    if not isinstance(value, list):
        raise EvaluationInputError(f"{context} must be an array")
    return [_number(item, context=f"{context}[]") for item in value]


def _percentile(values: list[float], quantile: float = 0.95) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator > 0 else None


def _slope_per_hour(samples: list[tuple[float, float]]) -> float | None:
    if len(samples) < 2:
        return None
    mean_x = sum(item[0] for item in samples) / len(samples)
    mean_y = sum(item[1] for item in samples) / len(samples)
    denominator = sum((item[0] - mean_x) ** 2 for item in samples)
    if denominator == 0:
        return None
    per_second = sum((x - mean_x) * (y - mean_y) for x, y in samples) / denominator
    return per_second * 3_600


def _at_most(value: float | None, limit: float) -> bool:
    return value is not None and value <= limit


def _at_least(value: float | None, limit: float) -> bool:
    return value is not None and value >= limit


def evaluate(payload: dict[str, Any], *, source_sha256: str) -> dict[str, Any]:
    if _SHA256_RE.fullmatch(source_sha256) is None:
        raise EvaluationInputError("source_sha256 must be a lowercase SHA-256 digest")
    root = _exact_object(
        payload,
        required=frozenset(
            {
                "schema_version",
                "evaluation_id",
                "authorization",
                "candidate",
                "concurrency",
                "imports",
                "ai_pipeline",
                "soak",
                "hermes_outage",
                "recovery",
            }
        ),
        context="input",
    )
    if root["schema_version"] != INPUT_SCHEMA_VERSION:
        raise EvaluationInputError(f"schema_version must be {INPUT_SCHEMA_VERSION}")
    _opaque_reference(root["evaluation_id"], context="evaluation_id")

    authorization = _exact_object(
        root["authorization"],
        required=frozenset(
            {
                "approved",
                "all_observations_authorized",
                "unapproved_production_or_historical_observations",
                "reference",
            }
        ),
        context="authorization",
    )
    authorization_approved = _boolean(authorization["approved"], context="authorization.approved")
    all_observations_authorized = _boolean(
        authorization["all_observations_authorized"],
        context="authorization.all_observations_authorized",
    )
    unapproved_observations = _count(
        authorization["unapproved_production_or_historical_observations"],
        context="authorization.unapproved_production_or_historical_observations",
    )
    _opaque_reference(authorization["reference"], context="authorization.reference")

    candidate = _exact_object(
        root["candidate"],
        required=frozenset({"commit_sha", "environment_profile"}),
        context="candidate",
    )
    commit_sha = candidate["commit_sha"]
    if not isinstance(commit_sha, str) or _COMMIT_RE.fullmatch(commit_sha) is None:
        raise EvaluationInputError("candidate.commit_sha must be a lowercase 40- or 64-character commit digest")
    environment_profile = _opaque_reference(
        candidate["environment_profile"],
        context="candidate.environment_profile",
    )

    concurrency = _exact_object(
        root["concurrency"],
        required=frozenset({"release", "overload", "measurements_at_or_above_overload"}),
        context="concurrency",
    )
    release_concurrency = _count(concurrency["release"], context="concurrency.release")
    overload_concurrency = _count(concurrency["overload"], context="concurrency.overload")
    measurements_at_overload = _boolean(
        concurrency["measurements_at_or_above_overload"],
        context="concurrency.measurements_at_or_above_overload",
    )

    raw_imports = root["imports"]
    if not isinstance(raw_imports, list):
        raise EvaluationInputError("imports must be an array")
    imports: list[dict[str, float]] = []
    for index, value in enumerate(raw_imports):
        row = _exact_object(
            value,
            required=frozenset(
                {
                    "audio_duration_seconds",
                    "upload_complete_to_ready_seconds",
                    "final_asr_audio_seconds",
                    "final_asr_processing_seconds",
                }
            ),
            context=f"imports[{index}]",
        )
        parsed = {
            "audio_duration_seconds": _number(
                row["audio_duration_seconds"],
                context=f"imports[{index}].audio_duration_seconds",
                positive=True,
            ),
            "upload_complete_to_ready_seconds": _number(
                row["upload_complete_to_ready_seconds"],
                context=f"imports[{index}].upload_complete_to_ready_seconds",
            ),
            "final_asr_audio_seconds": _number(
                row["final_asr_audio_seconds"],
                context=f"imports[{index}].final_asr_audio_seconds",
                positive=True,
            ),
            "final_asr_processing_seconds": _number(
                row["final_asr_processing_seconds"],
                context=f"imports[{index}].final_asr_processing_seconds",
            ),
        }
        imports.append(parsed)

    ai_pipeline = _exact_object(
        root["ai_pipeline"],
        required=frozenset(
            {
                "enqueue_ms",
                "job_queue_to_complete_seconds",
                "rolling_minutes_freshness_seconds",
                "final_minutes_after_last_stable_seconds",
            }
        ),
        context="ai_pipeline",
    )
    enqueue_ms = _number_array(ai_pipeline["enqueue_ms"], context="ai_pipeline.enqueue_ms")
    job_seconds = _number_array(
        ai_pipeline["job_queue_to_complete_seconds"],
        context="ai_pipeline.job_queue_to_complete_seconds",
    )
    rolling_seconds = _number_array(
        ai_pipeline["rolling_minutes_freshness_seconds"],
        context="ai_pipeline.rolling_minutes_freshness_seconds",
    )
    final_minutes_seconds = _number_array(
        ai_pipeline["final_minutes_after_last_stable_seconds"],
        context="ai_pipeline.final_minutes_after_last_stable_seconds",
    )

    soak = _exact_object(
        root["soak"],
        required=frozenset(
            {
                "duration_seconds",
                "expected_sample_interval_seconds",
                "process_memory_limit_bytes",
                "open_handle_limit",
                "queue_capacity",
                "samples",
            }
        ),
        context="soak",
    )
    declared_soak_duration = _number(soak["duration_seconds"], context="soak.duration_seconds")
    expected_interval = _number(
        soak["expected_sample_interval_seconds"],
        context="soak.expected_sample_interval_seconds",
        positive=True,
    )
    if expected_interval < 1:
        raise EvaluationInputError("soak.expected_sample_interval_seconds must be at least one second")
    memory_limit = _number(
        soak["process_memory_limit_bytes"],
        context="soak.process_memory_limit_bytes",
        positive=True,
    )
    handle_limit = _count(soak["open_handle_limit"], context="soak.open_handle_limit")
    queue_capacity = _count(soak["queue_capacity"], context="soak.queue_capacity")
    if handle_limit == 0 or queue_capacity == 0:
        raise EvaluationInputError("soak handle and queue limits must be positive")
    raw_samples = soak["samples"]
    if not isinstance(raw_samples, list):
        raise EvaluationInputError("soak.samples must be an array")
    samples: list[dict[str, float]] = []
    previous_elapsed: float | None = None
    for index, value in enumerate(raw_samples):
        row = _exact_object(
            value,
            required=frozenset({"elapsed_seconds", "rss_bytes", "open_handles", "queue_depth"}),
            context=f"soak.samples[{index}]",
        )
        elapsed = _number(row["elapsed_seconds"], context=f"soak.samples[{index}].elapsed_seconds")
        if previous_elapsed is not None and elapsed <= previous_elapsed:
            raise EvaluationInputError("soak sample elapsed_seconds must be strictly increasing")
        previous_elapsed = elapsed
        samples.append(
            {
                "elapsed_seconds": elapsed,
                "rss_bytes": _number(row["rss_bytes"], context=f"soak.samples[{index}].rss_bytes"),
                "open_handles": float(_count(row["open_handles"], context=f"soak.samples[{index}].open_handles")),
                "queue_depth": float(_count(row["queue_depth"], context=f"soak.samples[{index}].queue_depth")),
            }
        )

    hermes_outage = _exact_object(
        root["hermes_outage"],
        required=frozenset(
            {
                "duration_seconds",
                "caption_latency_baseline_p95_seconds",
                "caption_latency_outage_p95_seconds",
                "stable_segments_lost",
            }
        ),
        context="hermes_outage",
    )
    hermes_duration = _number(hermes_outage["duration_seconds"], context="hermes_outage.duration_seconds")
    caption_baseline_p95 = _number(
        hermes_outage["caption_latency_baseline_p95_seconds"],
        context="hermes_outage.caption_latency_baseline_p95_seconds",
    )
    caption_outage_p95 = _number(
        hermes_outage["caption_latency_outage_p95_seconds"],
        context="hermes_outage.caption_latency_outage_p95_seconds",
    )
    hermes_stable_loss = _count(
        hermes_outage["stable_segments_lost"],
        context="hermes_outage.stable_segments_lost",
    )

    recovery = _exact_object(
        root["recovery"],
        required=frozenset(
            {
                "gateway_restart_runs",
                "gateway_restart_recovered_runs",
                "worker_restart_runs",
                "worker_restart_recovered_runs",
                "database_outage_runs",
                "database_outage_explicit_gap_or_safe_stop_runs",
                "storage_failure_runs",
                "storage_failure_explicit_gap_or_safe_stop_runs",
                "stable_segments_lost",
                "stable_segments_duplicated",
                "duplicate_artifacts",
                "model_cross_use_count",
            }
        ),
        context="recovery",
    )
    recovery_counts = {key: _count(value, context=f"recovery.{key}") for key, value in recovery.items()}

    import_audio_seconds = sum(row["audio_duration_seconds"] for row in imports)
    final_asr_audio_seconds = sum(row["final_asr_audio_seconds"] for row in imports)
    ready_seconds = sum(row["upload_complete_to_ready_seconds"] for row in imports)
    final_asr_seconds = sum(row["final_asr_processing_seconds"] for row in imports)
    import_ready_rtfs = [row["upload_complete_to_ready_seconds"] / row["audio_duration_seconds"] for row in imports]
    final_asr_rtfs = [row["final_asr_processing_seconds"] / row["final_asr_audio_seconds"] for row in imports]

    actual_soak_duration = samples[-1]["elapsed_seconds"] - samples[0]["elapsed_seconds"] if len(samples) >= 2 else 0.0
    sample_gaps = [
        samples[index]["elapsed_seconds"] - samples[index - 1]["elapsed_seconds"] for index in range(1, len(samples))
    ]
    max_sample_gap = max(sample_gaps, default=None)
    theoretical_samples = math.floor(declared_soak_duration / expected_interval) + 1
    minimum_soak_samples = max(2, math.ceil(theoretical_samples * 0.95))
    steady_start = (samples[0]["elapsed_seconds"] if samples else 0.0) + LIMITS["steady_state_start_seconds"]
    steady_samples = [sample for sample in samples if sample["elapsed_seconds"] >= steady_start]

    def resource_slope(field: str) -> float | None:
        return _slope_per_hour([(sample["elapsed_seconds"], sample[field]) for sample in steady_samples])

    def resource_peak(field: str) -> float | None:
        return max((sample[field] for sample in samples), default=None)

    def steady_net_growth(field: str) -> float | None:
        if len(steady_samples) < 2:
            return None
        return steady_samples[-1][field] - steady_samples[0][field]

    rss_slope = resource_slope("rss_bytes")
    handles_slope = resource_slope("open_handles")
    queue_slope = resource_slope("queue_depth")
    rss_peak = resource_peak("rss_bytes")
    handles_peak = resource_peak("open_handles")
    queue_peak = resource_peak("queue_depth")
    rss_net = steady_net_growth("rss_bytes")
    handles_net = steady_net_growth("open_handles")
    queue_net = steady_net_growth("queue_depth")
    caption_degradation = (
        (caption_outage_p95 - caption_baseline_p95) / caption_baseline_p95 if caption_baseline_p95 > 0 else None
    )

    metrics = {
        "import_observation_count": len(imports),
        "import_audio_seconds": import_audio_seconds,
        "upload_complete_to_ready_p95_seconds": _percentile(
            [row["upload_complete_to_ready_seconds"] for row in imports]
        ),
        "import_to_ready_rtf": _ratio(ready_seconds, import_audio_seconds),
        "import_to_ready_rtf_p95": _percentile(import_ready_rtfs),
        "final_asr_audio_seconds": final_asr_audio_seconds,
        "final_asr_processing_seconds": final_asr_seconds,
        "final_asr_rtf": _ratio(final_asr_seconds, final_asr_audio_seconds),
        "final_asr_rtf_p95": _percentile(final_asr_rtfs),
        "ai_enqueue_observation_count": len(enqueue_ms),
        "ai_enqueue_p95_ms": _percentile(enqueue_ms),
        "ai_job_observation_count": len(job_seconds),
        "ai_job_queue_to_complete_p95_seconds": _percentile(job_seconds),
        "rolling_minutes_observation_count": len(rolling_seconds),
        "rolling_minutes_freshness_p95_seconds": _percentile(rolling_seconds),
        "final_minutes_observation_count": len(final_minutes_seconds),
        "final_minutes_after_last_stable_p95_seconds": _percentile(final_minutes_seconds),
        "soak_declared_duration_seconds": declared_soak_duration,
        "soak_observed_duration_seconds": actual_soak_duration,
        "soak_sample_count": len(samples),
        "soak_minimum_sample_count": minimum_soak_samples,
        "soak_max_sample_gap_seconds": max_sample_gap,
        "soak_steady_sample_count": len(steady_samples),
        "rss_peak_bytes": rss_peak,
        "rss_slope_bytes_per_hour": rss_slope,
        "rss_steady_net_growth_bytes": rss_net,
        "open_handles_peak": handles_peak,
        "open_handles_slope_per_hour": handles_slope,
        "open_handles_steady_net_growth": handles_net,
        "queue_depth_peak": queue_peak,
        "queue_depth_slope_per_hour": queue_slope,
        "queue_depth_steady_net_growth": queue_net,
        "hermes_outage_seconds": hermes_duration,
        "hermes_caption_latency_degradation": caption_degradation,
        "hermes_outage_stable_segments_lost": hermes_stable_loss,
    }

    gates = {
        "authorization_approved": authorization_approved,
        "all_observations_authorized": all_observations_authorized,
        "no_unapproved_production_or_historical_observations": unapproved_observations == 0,
        "release_concurrency_frozen": release_concurrency > 0,
        "overload_concurrency_at_least_120_percent": (
            release_concurrency > 0 and overload_concurrency * 5 >= release_concurrency * 6
        ),
        "measurements_at_or_above_overload": measurements_at_overload,
        "sample_import_observations": len(imports) >= MINIMUM_SAMPLE["import_observations"],
        "sample_import_audio_duration": import_audio_seconds >= MINIMUM_SAMPLE["import_audio_seconds"],
        "import_to_ready_rtf_at_most_0_30": _at_most(
            metrics["import_to_ready_rtf_p95"], LIMITS["import_to_ready_rtf_max"]
        ),
        "final_asr_rtf_at_most_0_25": _at_most(metrics["final_asr_rtf_p95"], LIMITS["final_asr_rtf_max"]),
        "sample_ai_enqueue_observations": len(enqueue_ms) >= MINIMUM_SAMPLE["ai_enqueue_observations"],
        "ai_enqueue_p95_at_most_50ms": _at_most(metrics["ai_enqueue_p95_ms"], LIMITS["ai_enqueue_p95_ms_max"]),
        "sample_ai_job_observations": len(job_seconds) >= MINIMUM_SAMPLE["ai_job_observations"],
        "ai_job_queue_to_complete_p95_at_most_180s": _at_most(
            metrics["ai_job_queue_to_complete_p95_seconds"],
            LIMITS["ai_job_queue_to_complete_p95_seconds_max"],
        ),
        "sample_rolling_minutes_observations": (len(rolling_seconds) >= MINIMUM_SAMPLE["rolling_minutes_observations"]),
        "rolling_minutes_freshness_p95_at_most_90s": _at_most(
            metrics["rolling_minutes_freshness_p95_seconds"],
            LIMITS["rolling_minutes_freshness_p95_seconds_max"],
        ),
        "sample_final_minutes_observations": (
            len(final_minutes_seconds) >= MINIMUM_SAMPLE["final_minutes_observations"]
        ),
        "final_minutes_after_last_stable_p95_at_most_180s": _at_most(
            metrics["final_minutes_after_last_stable_p95_seconds"],
            LIMITS["final_minutes_after_last_stable_p95_seconds_max"],
        ),
        "soak_declared_duration_at_least_4h": declared_soak_duration >= LIMITS["soak_duration_seconds_min"],
        "soak_observed_duration_at_least_4h": actual_soak_duration >= LIMITS["soak_duration_seconds_min"],
        "soak_declared_duration_matches_samples": (
            len(samples) >= 2 and abs(actual_soak_duration - declared_soak_duration) <= expected_interval
        ),
        "soak_interval_at_most_60s": expected_interval <= LIMITS["soak_sample_interval_seconds_max"],
        "soak_sample_count_sufficient": len(samples) >= minimum_soak_samples,
        "soak_sample_gaps_bounded": (max_sample_gap is not None and max_sample_gap <= expected_interval * 1.5),
        "soak_steady_state_samples_sufficient": len(steady_samples) >= 2,
        "rss_peak_below_80_percent_limit": (
            rss_peak is not None and rss_peak <= memory_limit * LIMITS["resource_peak_to_limit_ratio_max"]
        ),
        "rss_slope_bounded": _at_most(rss_slope, LIMITS["rss_slope_bytes_per_hour_max"]),
        "rss_steady_net_growth_bounded": _at_most(rss_net, LIMITS["rss_steady_net_growth_bytes_max"]),
        "open_handles_peak_below_80_percent_limit": (
            handles_peak is not None and handles_peak <= handle_limit * LIMITS["resource_peak_to_limit_ratio_max"]
        ),
        "open_handles_slope_bounded": _at_most(handles_slope, LIMITS["open_handles_slope_per_hour_max"]),
        "open_handles_steady_net_growth_bounded": _at_most(handles_net, LIMITS["open_handles_steady_net_growth_max"]),
        "queue_depth_peak_within_capacity": queue_peak is not None and queue_peak <= queue_capacity,
        "queue_depth_slope_bounded": _at_most(queue_slope, LIMITS["queue_depth_slope_per_hour_max"]),
        "queue_depth_steady_net_growth_bounded": _at_most(queue_net, LIMITS["queue_depth_steady_net_growth_max"]),
        "hermes_outage_at_least_30m": hermes_duration >= LIMITS["hermes_outage_seconds_min"],
        "hermes_caption_latency_degradation_at_most_10_percent": (
            caption_baseline_p95 > 0
            and caption_outage_p95 <= caption_baseline_p95 * (1 + LIMITS["hermes_caption_latency_degradation_max"])
        ),
        "hermes_outage_stable_segment_loss_zero": hermes_stable_loss == 0,
        "gateway_restart_recovered": (
            recovery_counts["gateway_restart_runs"] > 0
            and recovery_counts["gateway_restart_recovered_runs"] == recovery_counts["gateway_restart_runs"]
        ),
        "worker_restart_recovered": (
            recovery_counts["worker_restart_runs"] > 0
            and recovery_counts["worker_restart_recovered_runs"] == recovery_counts["worker_restart_runs"]
        ),
        "database_outage_explicit": (
            recovery_counts["database_outage_runs"] > 0
            and recovery_counts["database_outage_explicit_gap_or_safe_stop_runs"]
            == recovery_counts["database_outage_runs"]
        ),
        "storage_failure_explicit": (
            recovery_counts["storage_failure_runs"] > 0
            and recovery_counts["storage_failure_explicit_gap_or_safe_stop_runs"]
            == recovery_counts["storage_failure_runs"]
        ),
        "recovery_stable_segment_loss_zero": recovery_counts["stable_segments_lost"] == 0,
        "recovery_stable_segment_duplicate_zero": recovery_counts["stable_segments_duplicated"] == 0,
        "recovery_duplicate_artifacts_zero": recovery_counts["duplicate_artifacts"] == 0,
        "model_cross_use_zero": recovery_counts["model_cross_use_count"] == 0,
    }
    failures = [code for code, passed in gates.items() if not passed]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "evaluation_policy_version": EVALUATION_POLICY_VERSION,
        "source_sha256": source_sha256,
        "candidate": {"commit_sha": commit_sha, "environment_profile": environment_profile},
        "limits": LIMITS,
        "minimum_sample": MINIMUM_SAMPLE,
        "metrics": metrics,
        "recovery_counts": recovery_counts,
        "gates": gates,
        "failures": failures,
        "passed": not failures,
        "privacy_boundary": {"aggregate_only": True, "raw_sensitive_data_emitted": False},
    }


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvaluationInputError("input contains duplicate JSON object keys")
        result[key] = value
    return result


def _load_input(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise EvaluationInputError("unable to read evidence input") from exc
    if len(raw) > MAX_INPUT_BYTES:
        raise EvaluationInputError("evidence input exceeds the aggregate report size limit")
    try:
        payload = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except EvaluationInputError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvaluationInputError("evidence input must be valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise EvaluationInputError("evidence input must be a JSON object")
    return payload, raw


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    status = "PASS" if report["passed"] else "FAIL"
    lines = [
        "# Meeting Performance Release Evaluation",
        "",
        f"- Status: **{status}**",
        f"- Candidate: `{report['candidate']['commit_sha']}`",
        f"- Environment profile: `{report['candidate']['environment_profile']}`",
        f"- Source SHA-256: `{report['source_sha256']}`",
        f"- Import observations: `{metrics['import_observation_count']}`",
        f"- Final ASR RTF P95: `{metrics['final_asr_rtf_p95']}`",
        f"- Upload-to-ready RTF P95: `{metrics['import_to_ready_rtf_p95']}`",
        f"- AI enqueue P95: `{metrics['ai_enqueue_p95_ms']}` ms",
        f"- Rolling minutes freshness P95: `{metrics['rolling_minutes_freshness_p95_seconds']}` s",
        f"- Final minutes after stable P95: `{metrics['final_minutes_after_last_stable_p95_seconds']}` s",
        f"- Soak observed: `{metrics['soak_observed_duration_seconds']}` s / `{metrics['soak_sample_count']}` samples",
        "",
        "The report contains aggregate numeric evidence only and excludes audio paths, transcript text, identities, and authorization references.",
    ]
    if report["failures"]:
        lines.extend(["", "## Blocking Gates", "", *[f"- `{code}`" for code in report["failures"]]])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate authorized aggregate meeting performance and four-hour soak evidence."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--require-passing", action="store_true")
    args = parser.parse_args()
    try:
        payload, raw = _load_input(args.input)
        report = evaluate(payload, source_sha256=hashlib.sha256(raw).hexdigest())
        _write_report(args.output, report)
        if args.markdown:
            args.markdown.parent.mkdir(parents=True, exist_ok=True)
            args.markdown.write_text(_markdown(report), encoding="utf-8")
    except EvaluationInputError as exc:
        parser.exit(2, f"performance release evidence rejected: {exc}\n")
    return 1 if args.require_passing and not report["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
