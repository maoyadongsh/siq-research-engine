#!/usr/bin/env python3
"""Evaluate anonymized aggregate voiceprint evidence and emit a runtime policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

INPUT_SCHEMA_VERSION = "siq.meeting.voiceprint-release-evidence.v1"
REPORT_SCHEMA_VERSION = "siq.meeting.voiceprint-release-evaluation.v1"
EVALUATION_POLICY_VERSION = "siq.meeting.voiceprint-release-gates.v1"
SCHEMA_VERSION = INPUT_SCHEMA_VERSION
MAX_INPUT_BYTES = 1024 * 1024

LIMITS = {
    "diarization_error_rate_max": 0.15,
    "suggestion_top1_precision_min": 0.95,
    "auto_false_accept_rate_max": 0.001,
    "post_revoke_new_matches_max": 0,
    "unauthorized_templates_max": 0,
}

MINIMUM_SAMPLE = {
    "diarization_sessions": 14,
    "diarization_reference_speaker_time_ms": 3_600_000,
    "genuine_trials": 100,
    "suggestion_top1_predictions": 100,
    # At least 3,000 independent impostor trials prevents a zero-of-a-handful
    # result from being treated as evidence for the 0.1% release target.
    "auto_impostor_trials": 3_000,
    "post_revoke_trials": 100,
}

_EXPECTED_SPEAKER_COUNTS = frozenset(range(2, 9))
_OPAQUE_REFERENCE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}\Z")
_POLICY_VERSION_RE = re.compile(r"voiceprint-thresholds\.[a-z0-9][a-z0-9._-]{0,80}\.v[1-9][0-9]*\Z")
_COMMIT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class EvaluationInputError(ValueError):
    """Raised when evidence is malformed or exceeds the aggregate-only schema."""


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
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EvaluationInputError(f"{context} must be a non-negative integer")
    return value


def _positive_integer(value: Any, *, context: str) -> int:
    result = _count(value, context=context)
    if result == 0:
        raise EvaluationInputError(f"{context} must be positive")
    return result


def _finite_unit_interval(value: Any, *, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationInputError(f"{context} must be a number between 0 and 1")
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise EvaluationInputError(f"{context} must be a number between 0 and 1")
    return result


def _opaque_reference(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or _OPAQUE_REFERENCE_RE.fullmatch(value) is None:
        raise EvaluationInputError(f"{context} must be an opaque ASCII reference")
    return value


def _parse_policy(value: Any) -> dict[str, Any]:
    payload = _exact_object(
        value,
        required=frozenset(
            {
                "version",
                "suggestion_min_score",
                "suggestion_min_margin",
                "auto_min_score",
                "auto_min_margin",
                "min_effective_duration_ms",
                "allowed_quality_grades",
            }
        ),
        context="threshold_policy",
    )
    version = payload["version"]
    if not isinstance(version, str) or _POLICY_VERSION_RE.fullmatch(version) is None:
        raise EvaluationInputError("threshold_policy.version must be a versioned voiceprint-thresholds.*.vN identifier")
    suggestion_min_score = _finite_unit_interval(
        payload["suggestion_min_score"],
        context="threshold_policy.suggestion_min_score",
    )
    suggestion_min_margin = _finite_unit_interval(
        payload["suggestion_min_margin"],
        context="threshold_policy.suggestion_min_margin",
    )
    auto_min_score = _finite_unit_interval(
        payload["auto_min_score"],
        context="threshold_policy.auto_min_score",
    )
    auto_min_margin = _finite_unit_interval(
        payload["auto_min_margin"],
        context="threshold_policy.auto_min_margin",
    )
    if auto_min_score < suggestion_min_score or auto_min_margin < suggestion_min_margin:
        raise EvaluationInputError("auto-match thresholds cannot be weaker than suggestion thresholds")

    quality_grades = payload["allowed_quality_grades"]
    if (
        not isinstance(quality_grades, list)
        or any(not isinstance(item, str) for item in quality_grades)
        or len(quality_grades) != len(set(quality_grades))
        or set(quality_grades) != {"good"}
    ):
        raise EvaluationInputError("aggregate release evidence currently supports allowed_quality_grades=['good'] only")
    return {
        "version": version,
        "suggestion_min_score": suggestion_min_score,
        "suggestion_min_margin": suggestion_min_margin,
        "auto_min_score": auto_min_score,
        "auto_min_margin": auto_min_margin,
        "min_effective_duration_ms": _positive_integer(
            payload["min_effective_duration_ms"],
            context="threshold_policy.min_effective_duration_ms",
        ),
        "allowed_quality_grades": ["good"],
    }


def _parse_speaker_counts(value: Any) -> frozenset[int]:
    if not isinstance(value, list) or not value:
        raise EvaluationInputError("diarization.speaker_counts_covered must be a non-empty array")
    counts: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise EvaluationInputError("diarization.speaker_counts_covered must contain positive integers")
        counts.append(item)
    if len(counts) != len(set(counts)):
        raise EvaluationInputError("diarization.speaker_counts_covered must not contain duplicates")
    return frozenset(counts)


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _register_gate(gates: dict[str, bool], code: str, passed: bool) -> None:
    gates[code] = bool(passed)


def evaluate(payload: dict[str, Any], *, source_sha256: str) -> dict[str, Any]:
    """Evaluate one strict aggregate evidence object.

    The returned object contains counts, rates, fixed decision codes, and a
    runtime threshold policy. It intentionally does not propagate arbitrary
    input labels or authorization references.
    """

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
                "split",
                "threshold_policy",
                "aggregates",
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
                "all_trials_authorized",
                "unapproved_production_or_historical_trials",
                "reference",
            }
        ),
        context="authorization",
    )
    authorization_approved = _boolean(
        authorization["approved"],
        context="authorization.approved",
    )
    all_trials_authorized = _boolean(
        authorization["all_trials_authorized"],
        context="authorization.all_trials_authorized",
    )
    unapproved_trials = _count(
        authorization["unapproved_production_or_historical_trials"],
        context="authorization.unapproved_production_or_historical_trials",
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

    split = _exact_object(
        root["split"],
        required=frozenset(
            {
                "kind",
                "independent_from_training",
                "independent_from_threshold_tuning",
                "speaker_overlap_count",
                "recording_overlap_count",
            }
        ),
        context="split",
    )
    if not isinstance(split["kind"], str):
        raise EvaluationInputError("split.kind must be a string")
    independent_from_training = _boolean(
        split["independent_from_training"],
        context="split.independent_from_training",
    )
    independent_from_tuning = _boolean(
        split["independent_from_threshold_tuning"],
        context="split.independent_from_threshold_tuning",
    )
    speaker_overlap = _count(
        split["speaker_overlap_count"],
        context="split.speaker_overlap_count",
    )
    recording_overlap = _count(
        split["recording_overlap_count"],
        context="split.recording_overlap_count",
    )
    proposed_policy = _parse_policy(root["threshold_policy"])

    aggregates = _exact_object(
        root["aggregates"],
        required=frozenset({"diarization", "matching", "revocation", "template_authorization"}),
        context="aggregates",
    )
    diarization = _exact_object(
        aggregates["diarization"],
        required=frozenset(
            {
                "session_count",
                "speaker_counts_covered",
                "clean_condition_only",
                "reference_speaker_time_ms",
                "missed_speech_ms",
                "false_alarm_speech_ms",
                "speaker_confusion_ms",
            }
        ),
        context="aggregates.diarization",
    )
    diarization_sessions = _count(
        diarization["session_count"],
        context="diarization.session_count",
    )
    speaker_counts = _parse_speaker_counts(diarization["speaker_counts_covered"])
    clean_condition_only = _boolean(
        diarization["clean_condition_only"],
        context="diarization.clean_condition_only",
    )
    reference_time_ms = _count(
        diarization["reference_speaker_time_ms"],
        context="diarization.reference_speaker_time_ms",
    )
    missed_speech_ms = _count(
        diarization["missed_speech_ms"],
        context="diarization.missed_speech_ms",
    )
    false_alarm_speech_ms = _count(
        diarization["false_alarm_speech_ms"],
        context="diarization.false_alarm_speech_ms",
    )
    speaker_confusion_ms = _count(
        diarization["speaker_confusion_ms"],
        context="diarization.speaker_confusion_ms",
    )
    total_diarization_error_ms = missed_speech_ms + false_alarm_speech_ms + speaker_confusion_ms

    matching = _exact_object(
        aggregates["matching"],
        required=frozenset(
            {
                "genuine_trials",
                "suggestion_top1_predictions",
                "suggestion_top1_correct",
                "auto_impostor_trials",
                "auto_false_accepts",
                "threshold_policy_applied_to_all_trials",
            }
        ),
        context="aggregates.matching",
    )
    genuine_trials = _count(matching["genuine_trials"], context="matching.genuine_trials")
    suggestion_predictions = _count(
        matching["suggestion_top1_predictions"],
        context="matching.suggestion_top1_predictions",
    )
    suggestion_correct = _count(
        matching["suggestion_top1_correct"],
        context="matching.suggestion_top1_correct",
    )
    impostor_trials = _count(
        matching["auto_impostor_trials"],
        context="matching.auto_impostor_trials",
    )
    false_accepts = _count(
        matching["auto_false_accepts"],
        context="matching.auto_false_accepts",
    )
    policy_applied_to_all_trials = _boolean(
        matching["threshold_policy_applied_to_all_trials"],
        context="matching.threshold_policy_applied_to_all_trials",
    )
    if suggestion_correct > suggestion_predictions:
        raise EvaluationInputError("suggestion correct count cannot exceed prediction count")
    if false_accepts > impostor_trials:
        raise EvaluationInputError("auto false accepts cannot exceed impostor trials")

    revocation = _exact_object(
        aggregates["revocation"],
        required=frozenset({"post_revoke_trials", "post_revoke_new_matches"}),
        context="aggregates.revocation",
    )
    post_revoke_trials = _count(
        revocation["post_revoke_trials"],
        context="revocation.post_revoke_trials",
    )
    post_revoke_new_matches = _count(
        revocation["post_revoke_new_matches"],
        context="revocation.post_revoke_new_matches",
    )
    if post_revoke_new_matches > post_revoke_trials:
        raise EvaluationInputError("post-revoke matches cannot exceed post-revoke trials")

    template_authorization = _exact_object(
        aggregates["template_authorization"],
        required=frozenset({"persistent_templates_audited", "unauthorized_templates", "inventory_complete"}),
        context="aggregates.template_authorization",
    )
    templates_audited = _count(
        template_authorization["persistent_templates_audited"],
        context="template_authorization.persistent_templates_audited",
    )
    unauthorized_templates = _count(
        template_authorization["unauthorized_templates"],
        context="template_authorization.unauthorized_templates",
    )
    inventory_complete = _boolean(
        template_authorization["inventory_complete"],
        context="template_authorization.inventory_complete",
    )
    if unauthorized_templates > templates_audited:
        raise EvaluationInputError("unauthorized templates cannot exceed audited templates")

    diarization_error_rate = _rate(total_diarization_error_ms, reference_time_ms)
    suggestion_top1_precision = _rate(suggestion_correct, suggestion_predictions)
    auto_false_accept_rate = _rate(false_accepts, impostor_trials)

    gates: dict[str, bool] = {}
    _register_gate(gates, "authorization_approved", authorization_approved)
    _register_gate(gates, "all_trials_authorized", all_trials_authorized)
    _register_gate(gates, "no_unapproved_production_or_historical_trials", unapproved_trials == 0)
    _register_gate(gates, "independent_holdout_kind", split["kind"] == "independent_holdout")
    _register_gate(gates, "independent_from_training", independent_from_training)
    _register_gate(gates, "independent_from_threshold_tuning", independent_from_tuning)
    _register_gate(gates, "no_speaker_split_overlap", speaker_overlap == 0)
    _register_gate(gates, "no_recording_split_overlap", recording_overlap == 0)
    _register_gate(gates, "threshold_policy_applied_to_all_trials", policy_applied_to_all_trials)
    _register_gate(gates, "clean_diarization_condition", clean_condition_only)
    _register_gate(gates, "speaker_counts_2_through_8_covered", speaker_counts == _EXPECTED_SPEAKER_COUNTS)
    _register_gate(
        gates,
        "sample_diarization_sessions",
        diarization_sessions >= MINIMUM_SAMPLE["diarization_sessions"],
    )
    _register_gate(
        gates,
        "sample_diarization_reference_time",
        reference_time_ms >= MINIMUM_SAMPLE["diarization_reference_speaker_time_ms"],
    )
    _register_gate(
        gates,
        "sample_genuine_trials",
        genuine_trials >= MINIMUM_SAMPLE["genuine_trials"],
    )
    _register_gate(
        gates,
        "sample_suggestion_predictions",
        suggestion_predictions >= MINIMUM_SAMPLE["suggestion_top1_predictions"],
    )
    _register_gate(
        gates,
        "sample_auto_impostor_trials",
        impostor_trials >= MINIMUM_SAMPLE["auto_impostor_trials"],
    )
    _register_gate(
        gates,
        "sample_post_revoke_trials",
        post_revoke_trials >= MINIMUM_SAMPLE["post_revoke_trials"],
    )
    _register_gate(
        gates,
        "diarization_error_rate_at_most_15_percent",
        reference_time_ms > 0 and total_diarization_error_ms * 100 <= reference_time_ms * 15,
    )
    _register_gate(
        gates,
        "suggestion_top1_precision_at_least_95_percent",
        suggestion_predictions > 0 and suggestion_correct * 100 >= suggestion_predictions * 95,
    )
    _register_gate(
        gates,
        "auto_false_accept_rate_at_most_0_1_percent",
        impostor_trials > 0 and false_accepts * 1_000 <= impostor_trials,
    )
    _register_gate(gates, "post_revoke_new_matches_zero", post_revoke_new_matches == 0)
    _register_gate(gates, "template_inventory_complete", inventory_complete)
    _register_gate(gates, "unauthorized_templates_zero", unauthorized_templates == 0)

    auto_only_gate_codes = frozenset({"sample_auto_impostor_trials", "auto_false_accept_rate_at_most_0_1_percent"})
    suggestion_release_validated = all(passed for code, passed in gates.items() if code not in auto_only_gate_codes)
    auto_match_validated = suggestion_release_validated and all(gates[code] for code in auto_only_gate_codes)
    release_mode = (
        "auto_match" if auto_match_validated else "suggestion_only" if suggestion_release_validated else "blocked"
    )

    runtime_policy = {**proposed_policy, "auto_match_validated": auto_match_validated}
    runtime_policy_json = json.dumps(runtime_policy, sort_keys=True, separators=(",", ":"))
    failures = [code for code, passed in gates.items() if not passed]

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "evaluation_policy_version": EVALUATION_POLICY_VERSION,
        "source_sha256": source_sha256,
        "candidate": {"commit_sha": commit_sha, "environment_profile": environment_profile},
        "limits": LIMITS,
        "minimum_sample": MINIMUM_SAMPLE,
        "metrics": {
            "diarization_sessions": diarization_sessions,
            "diarization_reference_speaker_time_ms": reference_time_ms,
            "diarization_error_time_ms": total_diarization_error_ms,
            "diarization_error_rate": diarization_error_rate,
            "genuine_trials": genuine_trials,
            "suggestion_top1_predictions": suggestion_predictions,
            "suggestion_top1_correct": suggestion_correct,
            "suggestion_top1_precision": suggestion_top1_precision,
            "auto_impostor_trials": impostor_trials,
            "auto_false_accepts": false_accepts,
            "auto_false_accept_rate": auto_false_accept_rate,
            "post_revoke_trials": post_revoke_trials,
            "post_revoke_new_matches": post_revoke_new_matches,
            "persistent_templates_audited": templates_audited,
            "unauthorized_templates": unauthorized_templates,
        },
        "gates": gates,
        "failures": failures,
        "suggestion_release_validated": suggestion_release_validated,
        "auto_match_validated": auto_match_validated,
        "release_mode": release_mode,
        "passed": auto_match_validated,
        "threshold_policy": runtime_policy,
        "environment": {
            "SIQ_MEETING_VOICEPRINT_THRESHOLDS_JSON": runtime_policy_json,
        },
        "privacy_boundary": {
            "aggregate_only": True,
            "raw_sensitive_data_emitted": False,
        },
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate authorized aggregate voiceprint evidence and emit a runtime threshold policy."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--require-passing",
        action="store_true",
        help="exit nonzero unless every auto-match release gate passes",
    )
    args = parser.parse_args()
    try:
        payload, raw = _load_input(args.input)
        report = evaluate(payload, source_sha256=hashlib.sha256(raw).hexdigest())
        _write_report(args.output, report)
    except EvaluationInputError as exc:
        parser.exit(2, f"voiceprint release evidence rejected: {exc}\n")
    return 1 if args.require_passing and not report["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
