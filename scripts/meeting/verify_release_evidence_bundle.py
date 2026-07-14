#!/usr/bin/env python3
"""Verify redacted meeting release reports and bind them to one candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

BUNDLE_SCHEMA_VERSION = "siq.meeting.release-evidence-bundle.v2"
MAX_REPORT_BYTES = 4 * 1024 * 1024
_COMMIT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_OPAQUE_REFERENCE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_POLICY_VERSION_RE = re.compile(r"speaker-recluster\.[a-z0-9][a-z0-9._-]{0,80}\.v[1-9][0-9]*\Z")

_DIARIZATION_PRIVACY = {
    "aggregate_or_fixed_metadata_only": True,
    "approval_reference_emitted": False,
    "candidate_metadata_limited_to_commit_and_environment": True,
    "recording_identifiers_emitted": False,
    "source_speaker_identifiers_emitted": False,
    "hypothesis_track_identifiers_emitted": False,
    "transcript_text_emitted": False,
    "audio_paths_emitted": False,
    "embeddings_emitted": False,
}
_DIARIZATION_LIMITS = {
    "diarization_error_rate_max": 0.15,
    "fragmentation_rate_max": 0.25,
    "over_merge_rate_max": 0.05,
    "track_purity_min": 0.90,
}
_DIARIZATION_MINIMUM_SAMPLE = {
    "recordings": 14,
    "unique_reference_speakers": 14,
    "reference_speaker_time_ms": 3_600_000,
}
_DIARIZATION_SCORING_PROTOCOL = {
    "collar_ms": 0,
    "overlap_scored": True,
    "speaker_mapping": "per_recording_maximum_overlap_one_to_one",
    "time_resolution_ms": 1,
    "fragmentation_association": "each_hypothesis_track_to_highest_overlap_reference",
    "over_merge_association": "each_reference_to_highest_overlap_hypothesis_track",
}
_DIARIZATION_GATES = frozenset(
    {
        "authorization_approved",
        "all_recordings_authorized",
        "no_unapproved_production_or_historical_recordings",
        "independent_holdout_kind",
        "independent_from_training",
        "independent_from_threshold_tuning",
        "no_speaker_split_overlap",
        "no_recording_split_overlap",
        "reference_annotation_sha256_matches_manifest",
        "hypothesis_annotation_sha256_matches_manifest",
        "manifest_recording_count_matches",
        "manifest_unique_reference_speaker_count_matches",
        "manifest_reference_speaker_time_matches",
        "manifest_speaker_counts_match",
        "sample_recordings_at_least_14",
        "sample_unique_reference_speakers_at_least_14",
        "sample_reference_speaker_time_at_least_1h",
        "speaker_counts_2_through_8_covered",
        "all_recordings_have_2_to_8_speakers",
        "diarization_error_rate_at_most_15_percent",
        "fragmentation_rate_at_most_25_percent",
        "over_merge_rate_at_most_5_percent",
        "track_purity_at_least_90_percent",
    }
)

REPORT_CONTRACTS = {
    "asr": {
        "schema_version": "siq.meeting.asr-release-evaluation.v2",
        "input_schema_version": "siq.meeting.asr-release-evidence.v2",
        "policy_version": "siq.meeting.asr-release-gates.v2",
        "policy_key": "evaluation_policy_version",
        "privacy": {"contains_transcript_text": False, "raw_sensitive_data_emitted": False},
        "keys": frozenset(
            {
                "schema_version",
                "input_schema_version",
                "evaluation_policy_version",
                "source_sha256",
                "candidate",
                "limits",
                "minimum_sample",
                "coverage",
                "metrics",
                "gates",
                "failures",
                "passed",
                "privacy_boundary",
            }
        ),
    },
    "voiceprint": {
        "schema_version": "siq.meeting.voiceprint-release-evaluation.v1",
        "input_schema_version": "siq.meeting.voiceprint-release-evidence.v1",
        "policy_version": "siq.meeting.voiceprint-release-gates.v1",
        "policy_key": "evaluation_policy_version",
        "privacy": {"aggregate_only": True, "raw_sensitive_data_emitted": False},
        "keys": frozenset(
            {
                "schema_version",
                "input_schema_version",
                "evaluation_policy_version",
                "source_sha256",
                "candidate",
                "limits",
                "minimum_sample",
                "metrics",
                "gates",
                "failures",
                "suggestion_release_validated",
                "auto_match_validated",
                "release_mode",
                "passed",
                "threshold_policy",
                "environment",
                "privacy_boundary",
            }
        ),
    },
    "performance": {
        "schema_version": "siq.meeting.performance-release-evaluation.v1",
        "input_schema_version": "siq.meeting.performance-release-evidence.v1",
        "policy_version": "siq.meeting.performance-release-gates.v1",
        "policy_key": "evaluation_policy_version",
        "privacy": {"aggregate_only": True, "raw_sensitive_data_emitted": False},
        "keys": frozenset(
            {
                "schema_version",
                "input_schema_version",
                "evaluation_policy_version",
                "source_sha256",
                "candidate",
                "limits",
                "minimum_sample",
                "metrics",
                "recovery_counts",
                "gates",
                "failures",
                "passed",
                "privacy_boundary",
            }
        ),
    },
    "diarization": {
        "schema_version": "siq.meeting.diarization-release-evaluation.v1",
        "input_schema_version": "siq.meeting.diarization-annotation.v1",
        "policy_version": "siq.meeting.diarization-scoring.v1",
        "policy_key": "scoring_policy_version",
        "privacy": _DIARIZATION_PRIVACY,
        "required_gate_keys": _DIARIZATION_GATES,
        "keys": frozenset(
            {
                "schema_version",
                "input_schema_version",
                "evidence_manifest_schema_version",
                "scoring_policy_version",
                "source_sha256",
                "evidence_manifest_sha256",
                "candidate",
                "policy",
                "scoring_protocol",
                "limits",
                "minimum_sample",
                "coverage",
                "metrics",
                "gates",
                "failures",
                "passed",
                "privacy_boundary",
            }
        ),
    },
}

_SENSITIVE_KEYS = frozenset(
    {
        "audio_path",
        "storage_path",
        "transcript",
        "reference",
        "hypothesis",
        "speaker_name",
        "user_name",
        "identity",
        "embedding",
        "token",
        "credential",
        "secret",
        "endpoint",
        "url",
    }
)


class BundleVerificationError(ValueError):
    """Raised when a redacted report cannot be accepted as release evidence."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BundleVerificationError("report contains duplicate JSON object keys")
        result[key] = value
    return result


def _load_report(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BundleVerificationError("unable to read a required release report") from exc
    if len(raw) > MAX_REPORT_BYTES:
        raise BundleVerificationError("release report exceeds the size limit")
    try:
        payload = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                BundleVerificationError(f"report contains non-finite JSON constant: {value}")
            ),
        )
    except BundleVerificationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleVerificationError("release report must be valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise BundleVerificationError("release report must be a JSON object")
    return payload, hashlib.sha256(raw).hexdigest()


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(key.casefold() in _SENSITIVE_KEYS or _contains_sensitive_key(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _validate_diarization_report(report: dict[str, Any]) -> None:
    if report.get("evidence_manifest_schema_version") != "siq.meeting.diarization-release-evidence.v1":
        raise BundleVerificationError("diarization evidence manifest schema is not accepted")
    manifest_sha256 = report.get("evidence_manifest_sha256")
    if not isinstance(manifest_sha256, str) or _SHA256_RE.fullmatch(manifest_sha256) is None:
        raise BundleVerificationError("diarization evidence manifest digest is invalid")
    if report.get("policy") is None or not isinstance(report["policy"], dict):
        raise BundleVerificationError("diarization policy binding is invalid")
    policy = report["policy"]
    if set(policy) != {"schema_version", "version", "final_diarizer_ref", "encoder_ref", "thresholds"}:
        raise BundleVerificationError("diarization policy fields do not match the redacted schema")
    if policy["schema_version"] != "siq.meeting.speaker_recluster_policy.v1":
        raise BundleVerificationError("diarization policy schema is not accepted")
    if (
        not isinstance(policy["version"], str)
        or _POLICY_VERSION_RE.fullmatch(policy["version"]) is None
        or ".validated." not in policy["version"]
    ):
        raise BundleVerificationError("diarization policy is not a validated policy")
    for field in ("final_diarizer_ref", "encoder_ref"):
        if not isinstance(policy[field], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{2,191}\Z", policy[field]):
            raise BundleVerificationError(f"diarization policy {field} is invalid")
    thresholds = policy["thresholds"]
    if not isinstance(thresholds, dict) or set(thresholds) != {
        "review_min_score", "merge_min_score", "singleton_merge_min_score", "min_top2_margin",
        "min_segment_ms", "max_segment_ms", "max_samples_per_track", "max_total_samples", "max_tracks",
        "max_noise_level", "min_asr_confidence", "min_rms", "max_clipping_ratio",
    }:
        raise BundleVerificationError("diarization policy thresholds are incomplete")
    unit_fields = {
        "review_min_score", "merge_min_score", "singleton_merge_min_score", "min_top2_margin",
        "max_noise_level", "min_asr_confidence", "min_rms", "max_clipping_ratio",
    }
    integer_bounds = {
        "min_segment_ms": (1_000, 15_000),
        "max_segment_ms": (1_000, 15_000),
        "max_samples_per_track": (1, 16),
        "max_total_samples": (2, 4_096),
        "max_tracks": (2, 256),
    }
    if (
        any(
            isinstance(thresholds[field], bool)
            or not isinstance(thresholds[field], (int, float))
            or not math.isfinite(thresholds[field])
            or not 0 <= thresholds[field] <= 1
            for field in unit_fields
        )
        or any(
            isinstance(thresholds[field], bool)
            or not isinstance(thresholds[field], int)
            or not minimum <= thresholds[field] <= maximum
            for field, (minimum, maximum) in integer_bounds.items()
        )
        or not thresholds["review_min_score"]
        <= thresholds["merge_min_score"]
        <= thresholds["singleton_merge_min_score"]
        or thresholds["min_segment_ms"] > thresholds["max_segment_ms"]
        or thresholds["max_tracks"] > thresholds["max_total_samples"]
    ):
        raise BundleVerificationError("diarization policy thresholds are invalid")
    if report.get("scoring_protocol") != _DIARIZATION_SCORING_PROTOCOL:
        raise BundleVerificationError("diarization scoring protocol is not accepted")
    if report.get("limits") != _DIARIZATION_LIMITS:
        raise BundleVerificationError("diarization limits are not accepted")
    if report.get("minimum_sample") != _DIARIZATION_MINIMUM_SAMPLE:
        raise BundleVerificationError("diarization minimum sample is not accepted")
    coverage = report.get("coverage")
    if (
        not isinstance(coverage, dict)
        or set(coverage) != {"reference_speaker_counts_covered"}
        or coverage["reference_speaker_counts_covered"] != list(range(2, 9))
    ):
        raise BundleVerificationError("diarization coverage is incomplete")
    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        raise BundleVerificationError("diarization metrics are invalid")
    expected_metric_keys = {
        "recording_count", "reference_speaker_count", "unique_reference_speaker_count", "hypothesis_track_count",
        "unapproved_production_or_historical_recordings", "speaker_split_overlap_count", "recording_split_overlap_count",
        "reference_speaker_time_ms", "hypothesis_speaker_time_ms", "missed_speech_ms", "false_alarm_speech_ms",
        "speaker_confusion_ms", "diarization_error_ms", "missed_speech_rate", "false_alarm_speech_rate",
        "speaker_confusion_rate", "diarization_error_rate", "fragmented_reference_speakers",
        "fragmentation_excess_tracks", "fragmentation_rate", "predicted_tracks_per_reference_histogram",
        "over_merged_hypothesis_tracks", "over_merge_excess_speakers", "references_on_over_merged_tracks",
        "over_merge_rate", "purity_numerator_ms", "purity_denominator_ms", "track_purity",
    }
    if set(metrics) != expected_metric_keys:
        raise BundleVerificationError("diarization metric fields do not match the redacted schema")
    integer_metrics = (
        "recording_count", "reference_speaker_count", "unique_reference_speaker_count", "hypothesis_track_count",
        "unapproved_production_or_historical_recordings", "speaker_split_overlap_count", "recording_split_overlap_count",
        "reference_speaker_time_ms", "hypothesis_speaker_time_ms", "missed_speech_ms", "false_alarm_speech_ms",
        "speaker_confusion_ms", "diarization_error_ms", "fragmented_reference_speakers",
        "fragmentation_excess_tracks", "over_merged_hypothesis_tracks", "over_merge_excess_speakers",
        "references_on_over_merged_tracks", "purity_numerator_ms", "purity_denominator_ms",
    )
    for field in integer_metrics:
        value = metrics.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise BundleVerificationError(f"diarization metric {field} is invalid")
    if (
        metrics["recording_count"] < 14
        or metrics["unique_reference_speaker_count"] < 14
        or metrics["reference_speaker_time_ms"] < 3_600_000
    ):
        raise BundleVerificationError("diarization report does not meet the minimum sample")
    if (
        metrics["unapproved_production_or_historical_recordings"] != 0
        or metrics["speaker_split_overlap_count"] != 0
        or metrics["recording_split_overlap_count"] != 0
    ):
        raise BundleVerificationError("diarization authorization or holdout metrics are not passing")
    if (
        not 2 * metrics["recording_count"] <= metrics["reference_speaker_count"] <= 8 * metrics["recording_count"]
        or metrics["unique_reference_speaker_count"] > metrics["reference_speaker_count"]
    ):
        raise BundleVerificationError("diarization speaker counts are inconsistent")
    rate_fields = (
        "missed_speech_rate",
        "false_alarm_speech_rate",
        "speaker_confusion_rate",
        "diarization_error_rate",
        "fragmentation_rate",
        "over_merge_rate",
        "track_purity",
    )
    if any(
        isinstance(metrics.get(field), bool)
        or not isinstance(metrics.get(field), (int, float))
        or not math.isfinite(metrics[field])
        or not 0 <= metrics[field] <= 1
        for field in rate_fields
    ):
        raise BundleVerificationError("diarization rates are invalid")
    reference_time_ms = metrics["reference_speaker_time_ms"]
    reference_speakers = metrics["reference_speaker_count"]
    if reference_time_ms <= 0 or reference_speakers <= 0:
        raise BundleVerificationError("diarization metric denominators are invalid")
    expected_rates = {
        "missed_speech_rate": metrics["missed_speech_ms"] / reference_time_ms,
        "false_alarm_speech_rate": metrics["false_alarm_speech_ms"] / reference_time_ms,
        "speaker_confusion_rate": metrics["speaker_confusion_ms"] / reference_time_ms,
        "diarization_error_rate": metrics["diarization_error_ms"] / reference_time_ms,
        "fragmentation_rate": metrics["fragmentation_excess_tracks"] / reference_speakers,
        "over_merge_rate": metrics["references_on_over_merged_tracks"] / reference_speakers,
    }
    if any(not math.isclose(metrics[key], value, rel_tol=1e-9, abs_tol=1e-12) for key, value in expected_rates.items()):
        raise BundleVerificationError("diarization rates do not match aggregate counts")
    histogram = metrics.get("predicted_tracks_per_reference_histogram")
    if (
        not isinstance(histogram, dict)
        or not histogram
        or any(
            not isinstance(key, str)
            or not key.isdigit()
            or isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for key, value in histogram.items()
        )
        or sum(histogram.values()) != reference_speakers
        or metrics["fragmentation_excess_tracks"]
        != sum(max(int(key) - 1, 0) * value for key, value in histogram.items())
        or metrics["fragmented_reference_speakers"]
        != sum(value for key, value in histogram.items() if int(key) > 1)
        or metrics["references_on_over_merged_tracks"]
        != metrics["over_merge_excess_speakers"] + metrics["over_merged_hypothesis_tracks"]
        or metrics["diarization_error_ms"]
        != metrics["missed_speech_ms"] + metrics["false_alarm_speech_ms"] + metrics["speaker_confusion_ms"]
        or metrics["hypothesis_speaker_time_ms"]
        != reference_time_ms - metrics["missed_speech_ms"] + metrics["false_alarm_speech_ms"]
        or metrics["hypothesis_track_count"] < sum(int(key) * value for key, value in histogram.items())
        or metrics["purity_denominator_ms"] <= 0
        or metrics["purity_numerator_ms"] > metrics["purity_denominator_ms"]
        or not math.isclose(
            metrics["track_purity"],
            metrics["purity_numerator_ms"] / metrics["purity_denominator_ms"],
            rel_tol=1e-9,
            abs_tol=1e-12,
        )
        or metrics["diarization_error_rate"] > _DIARIZATION_LIMITS["diarization_error_rate_max"]
        or metrics["fragmentation_rate"] > _DIARIZATION_LIMITS["fragmentation_rate_max"]
        or metrics["over_merge_rate"] > _DIARIZATION_LIMITS["over_merge_rate_max"]
        or metrics["track_purity"] < _DIARIZATION_LIMITS["track_purity_min"]
    ):
        raise BundleVerificationError("diarization aggregate metrics are inconsistent")


def verify(
    reports: dict[str, tuple[dict[str, Any], str]],
    *,
    expected_commit: str,
    expected_environment: str | None = None,
) -> dict[str, Any]:
    if _COMMIT_RE.fullmatch(expected_commit) is None:
        raise BundleVerificationError("expected candidate must be a lowercase 40- or 64-character commit digest")
    if set(reports) != set(REPORT_CONTRACTS):
        raise BundleVerificationError("ASR, voiceprint, performance, and diarization reports are all required")
    if expected_environment is not None and _OPAQUE_REFERENCE_RE.fullmatch(expected_environment) is None:
        raise BundleVerificationError("expected environment profile is invalid")

    environments: set[str] = set()
    receipt_reports: dict[str, dict[str, str]] = {}
    for name, contract in REPORT_CONTRACTS.items():
        report, report_sha256 = reports[name]
        if not isinstance(report_sha256, str) or _SHA256_RE.fullmatch(report_sha256) is None:
            raise BundleVerificationError(f"{name} report digest is invalid")
        if set(report) != contract["keys"]:
            raise BundleVerificationError(f"{name} report fields do not match the redacted schema")
        if _contains_sensitive_key(report):
            raise BundleVerificationError(f"{name} report contains a prohibited sensitive field")
        if report.get("schema_version") != contract["schema_version"]:
            raise BundleVerificationError(f"{name} report schema version is not accepted")
        if report.get("input_schema_version") != contract["input_schema_version"]:
            raise BundleVerificationError(f"{name} input schema version is not accepted")
        if report.get(contract["policy_key"]) != contract["policy_version"]:
            raise BundleVerificationError(f"{name} evaluation policy version is not accepted")
        if report.get("passed") is not True:
            raise BundleVerificationError(f"{name} report has not passed")
        if report.get("failures") != []:
            raise BundleVerificationError(f"{name} report contains blocking failures")
        gates = report.get("gates")
        if not isinstance(gates, dict) or not gates or any(value is not True for value in gates.values()):
            raise BundleVerificationError(f"{name} report gates are incomplete or non-passing")
        required_gate_keys = contract.get("required_gate_keys")
        if required_gate_keys is not None and set(gates) != required_gate_keys:
            raise BundleVerificationError(f"{name} report gates do not match the release contract")
        if report.get("privacy_boundary") != contract["privacy"]:
            raise BundleVerificationError(f"{name} report privacy boundary is not accepted")
        source_sha256 = report.get("source_sha256")
        if not isinstance(source_sha256, str) or _SHA256_RE.fullmatch(source_sha256) is None:
            raise BundleVerificationError(f"{name} report source digest is invalid")
        candidate = report.get("candidate")
        if not isinstance(candidate, dict) or set(candidate) != {"commit_sha", "environment_profile"}:
            raise BundleVerificationError(f"{name} report candidate binding is invalid")
        if candidate["commit_sha"] != expected_commit:
            raise BundleVerificationError(f"{name} report belongs to a different candidate")
        environment = candidate["environment_profile"]
        if not isinstance(environment, str) or _OPAQUE_REFERENCE_RE.fullmatch(environment) is None:
            raise BundleVerificationError(f"{name} environment profile is invalid")
        if name == "diarization":
            _validate_diarization_report(report)
        environments.add(environment)
        receipt_reports[name] = {
            "schema_version": contract["schema_version"],
            "input_schema_version": contract["input_schema_version"],
            contract["policy_key"]: contract["policy_version"],
            "report_sha256": report_sha256,
            "source_sha256": source_sha256,
            **(
                {"evidence_manifest_sha256": report["evidence_manifest_sha256"]}
                if name == "diarization"
                else {}
            ),
        }

    if len(environments) != 1:
        raise BundleVerificationError("release reports were produced for different environment profiles")
    environment_profile = environments.pop()
    if expected_environment is not None and environment_profile != expected_environment:
        raise BundleVerificationError("release reports do not match the approved environment profile")
    return {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "candidate": {
            "commit_sha": expected_commit,
            "environment_profile": environment_profile,
        },
        "reports": receipt_reports,
        "passed": True,
        "privacy_boundary": {"redacted_reports_only": True, "raw_sensitive_data_emitted": False},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify redacted meeting release reports for one candidate.")
    parser.add_argument("--asr", type=Path, required=True)
    parser.add_argument("--voiceprint", type=Path, required=True)
    parser.add_argument("--performance", type=Path, required=True)
    parser.add_argument("--diarization", type=Path, required=True)
    parser.add_argument("--candidate-commit", required=True)
    parser.add_argument("--expected-environment", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        reports = {
            "asr": _load_report(args.asr),
            "voiceprint": _load_report(args.voiceprint),
            "performance": _load_report(args.performance),
            "diarization": _load_report(args.diarization),
        }
        receipt = verify(
            reports,
            expected_commit=args.candidate_commit,
            expected_environment=args.expected_environment,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except BundleVerificationError as exc:
        parser.exit(1, f"meeting release evidence blocked: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
