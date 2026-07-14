#!/usr/bin/env python3
"""Score authorized meeting ASR evidence without emitting transcript text."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

INPUT_SCHEMA_VERSION = "siq.meeting.asr-release-evidence.v2"
REPORT_SCHEMA_VERSION = "siq.meeting.asr-release-evaluation.v2"
EVALUATION_POLICY_VERSION = "siq.meeting.asr-release-gates.v2"
SCHEMA_VERSION = INPUT_SCHEMA_VERSION
MAX_INPUT_BYTES = 16 * 1024 * 1024

HARD_LIMITS = {
    "partial_p95_seconds": 1.2,
    "stable_p95_seconds": 2.5,
    "stable_db_commit_p95_ms": 200.0,
    "stable_to_visible_p95_ms": 250.0,
    "ack_p95_seconds": 0.3,
    "alignment_p95_ms": 500.0,
    "cer_degradation": 0.02,
    "lexicon_cer_degradation": 0.002,
    "false_hotword_trigger_rate": 0.005,
}

MINIMUM_SAMPLE = {
    "authorized_audio_seconds": 1_800.0,
    "sessions": 3,
    "distinct_asr_cases": 20,
    "latency_observations": 100,
    "lexicon_paired_cases": 20,
    "lexicon_term_occurrences": 20,
    "lexicon_non_target_opportunities": 1_000,
}

_EXPECTED_SPEAKER_COUNTS = frozenset({2, 4, 8})
_EXPECTED_CONDITIONS = frozenset(
    {
        "quiet_room",
        "far_field",
        "light_accent",
        "speech_rate_variation",
        "overlap",
        "network_reconnect",
    }
)
_ENTITY_CATEGORIES = frozenset({"amount", "date", "percentage", "ticker", "company", "project", "proper_noun"})
_OPAQUE_REFERENCE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}\Z")
_COMMIT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class EvaluationInputError(ValueError):
    """Raised when evidence is malformed or violates the redaction schema."""


def _exact_object(value: Any, *, required: frozenset[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvaluationInputError(f"{context} must be an object")
    keys = set(value)
    if not keys <= required:
        raise EvaluationInputError("input contains fields outside the ASR evidence schema")
    missing = sorted(required - keys)
    if missing:
        raise EvaluationInputError(f"{context} is missing required fields: {', '.join(missing)}")
    return value


def _object_with_allowed(
    value: Any,
    *,
    required: frozenset[str],
    allowed: frozenset[str],
    context: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvaluationInputError(f"{context} must be an object")
    if not set(value) <= allowed:
        raise EvaluationInputError("input contains fields outside the ASR evidence schema")
    missing = sorted(required - set(value))
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


def _number(value: Any, *, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationInputError(f"{context} must be a finite non-negative number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise EvaluationInputError(f"{context} must be a finite non-negative number")
    return result


def _opaque_reference(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or _OPAQUE_REFERENCE_RE.fullmatch(value) is None:
        raise EvaluationInputError(f"{context} must be an opaque ASCII reference")
    return value


def _normalized(text: Any) -> str:
    value = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return "".join(
        char for char in value if not unicodedata.category(char).startswith(("P", "Z")) and not char.isspace()
    )


def _edit_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def _cer(cases: list[dict[str, Any]], field: str) -> float | None:
    if not cases:
        return None
    edits = 0
    characters = 0
    for case in cases:
        reference = _normalized(case["reference"])
        hypothesis = _normalized(case[field])
        edits += _edit_distance(reference, hypothesis)
        characters += len(reference)
    return edits / characters if characters else None


def _percentile(values: list[float], quantile: float = 0.95) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def _entity_recall(cases: list[dict[str, Any]], field: str) -> dict[str, dict[str, float | int]]:
    totals: dict[str, int] = defaultdict(int)
    hits: dict[str, int] = defaultdict(int)
    for case in cases:
        hypothesis = _normalized(case[field])
        for category, values in case.get("entities", {}).items():
            for entity in values:
                normalized = _normalized(entity)
                if normalized:
                    totals[category] += 1
                    hits[category] += int(normalized in hypothesis)
    return {
        category: {
            "hits": hits[category],
            "total": total,
            "recall": hits[category] / total if total else 0.0,
        }
        for category, total in sorted(totals.items())
    }


def _lexicon_metrics(cases: list[dict[str, Any]]) -> dict[str, float | int | None]:
    off_by_id = {case["case_id"]: case for case in cases if case["variant"] == "lexicon_off"}
    on_by_id = {case["case_id"]: case for case in cases if case["variant"] == "lexicon_on"}
    paired_ids = sorted(set(off_by_id) & set(on_by_id))
    for case_id in paired_ids:
        off = off_by_id[case_id]
        on = on_by_id[case_id]
        if any(off[field] != on[field] for field in ("reference", "baseline", "terms", "entities")):
            raise EvaluationInputError(
                "lexicon on/off pairs must use identical references, baselines, terms, and entities"
            )
    paired_off = [off_by_id[key] for key in paired_ids]
    paired_on = [on_by_id[key] for key in paired_ids]

    def term_recall(rows: list[dict[str, Any]]) -> tuple[float | None, int]:
        hits = total = 0
        for row in rows:
            hypothesis = _normalized(row["candidate"])
            for term in row.get("terms", []):
                normalized = _normalized(term)
                if normalized:
                    total += 1
                    hits += int(normalized in hypothesis)
        return (hits / total if total else None, total)

    off_recall, off_terms = term_recall(paired_off)
    on_recall, on_terms = term_recall(paired_on)
    opportunities = sum(row.get("non_target_opportunities", 0) for row in paired_on)
    false_hits = sum(row.get("false_hotword_hits", 0) for row in paired_on)
    off_cer = _cer(paired_off, "candidate")
    on_cer = _cer(paired_on, "candidate")
    return {
        "paired_cases": len(paired_ids),
        "off_cer": off_cer,
        "on_cer": on_cer,
        "cer_degradation": on_cer - off_cer if on_cer is not None and off_cer is not None else None,
        "term_occurrences": min(off_terms, on_terms),
        "term_recall_off": off_recall,
        "term_recall_on": on_recall,
        "non_target_opportunities": opportunities,
        "false_trigger_rate": false_hits / opportunities if opportunities else None,
    }


def evaluate(payload: dict[str, Any], *, source_sha256: str) -> dict[str, Any]:
    if _SHA256_RE.fullmatch(source_sha256) is None:
        raise EvaluationInputError("source_sha256 must be a lowercase SHA-256 digest")
    root = _exact_object(
        payload,
        required=frozenset(
            {"schema_version", "authorization", "candidate", "dataset", "coverage", "cases", "latencies"}
        ),
        context="input",
    )
    if root["schema_version"] != INPUT_SCHEMA_VERSION:
        raise EvaluationInputError(f"schema_version must be {INPUT_SCHEMA_VERSION}")

    authorization = _exact_object(
        root["authorization"],
        required=frozenset(
            {
                "approved",
                "all_cases_authorized",
                "unapproved_production_or_historical_cases",
                "reference",
            }
        ),
        context="authorization",
    )
    authorization_approved = _boolean(authorization["approved"], context="authorization.approved")
    all_cases_authorized = _boolean(authorization["all_cases_authorized"], context="authorization.all_cases_authorized")
    unapproved_cases = _count(
        authorization["unapproved_production_or_historical_cases"],
        context="authorization.unapproved_production_or_historical_cases",
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
    environment_profile = _opaque_reference(candidate["environment_profile"], context="candidate.environment_profile")

    dataset = _exact_object(
        root["dataset"],
        required=frozenset({"name", "version", "independent_from_training", "independent_from_lexicon_tuning"}),
        context="dataset",
    )
    _opaque_reference(dataset["name"], context="dataset.name")
    _opaque_reference(dataset["version"], context="dataset.version")
    independent_training = _boolean(dataset["independent_from_training"], context="dataset.independent_from_training")
    independent_lexicon = _boolean(
        dataset["independent_from_lexicon_tuning"], context="dataset.independent_from_lexicon_tuning"
    )

    coverage = _exact_object(
        root["coverage"],
        required=frozenset(
            {"authorized_audio_seconds", "session_count", "speaker_counts_covered", "conditions_covered"}
        ),
        context="coverage",
    )
    authorized_audio_seconds = _number(
        coverage["authorized_audio_seconds"], context="coverage.authorized_audio_seconds"
    )
    session_count = _count(coverage["session_count"], context="coverage.session_count")
    speaker_counts_raw = coverage["speaker_counts_covered"]
    if not isinstance(speaker_counts_raw, list):
        raise EvaluationInputError("coverage.speaker_counts_covered must be an array")
    speaker_counts = frozenset(
        _count(value, context="coverage.speaker_counts_covered[]") for value in speaker_counts_raw
    )
    if len(speaker_counts) != len(speaker_counts_raw):
        raise EvaluationInputError("coverage.speaker_counts_covered must not contain duplicates")
    conditions_raw = coverage["conditions_covered"]
    if not isinstance(conditions_raw, list) or any(not isinstance(value, str) for value in conditions_raw):
        raise EvaluationInputError("coverage.conditions_covered must be an array of strings")
    conditions = frozenset(conditions_raw)
    if len(conditions) != len(conditions_raw) or not conditions <= _EXPECTED_CONDITIONS:
        raise EvaluationInputError("coverage.conditions_covered contains duplicates or unsupported values")

    raw_cases = root["cases"]
    if not isinstance(raw_cases, list):
        raise EvaluationInputError("cases must be an array")
    cases: list[dict[str, Any]] = []
    seen_variants: set[tuple[str, str]] = set()
    allowed_case_fields = frozenset(
        {
            "case_id",
            "variant",
            "reference",
            "baseline",
            "candidate",
            "final_candidate",
            "terms",
            "entities",
            "non_target_opportunities",
            "false_hotword_hits",
        }
    )
    required_case_fields = frozenset(
        {"case_id", "variant", "reference", "baseline", "candidate", "final_candidate", "terms", "entities"}
    )
    for index, value in enumerate(raw_cases):
        case = _object_with_allowed(
            value,
            required=required_case_fields,
            allowed=allowed_case_fields,
            context=f"cases[{index}]",
        )
        case_id = _opaque_reference(case["case_id"], context=f"cases[{index}].case_id")
        variant = case["variant"]
        if variant not in {"standard", "lexicon_off", "lexicon_on"}:
            raise EvaluationInputError("case variant must be standard, lexicon_off, or lexicon_on")
        if (case_id, variant) in seen_variants:
            raise EvaluationInputError("case_id and variant pairs must be unique")
        seen_variants.add((case_id, variant))
        for field in ("reference", "baseline", "candidate", "final_candidate"):
            if not isinstance(case[field], str) or (field == "reference" and not _normalized(case[field])):
                raise EvaluationInputError(f"cases[{index}].{field} must be a valid string")
        terms = case["terms"]
        if not isinstance(terms, list) or any(not isinstance(term, str) for term in terms):
            raise EvaluationInputError(f"cases[{index}].terms must be an array of strings")
        entities = case["entities"]
        if not isinstance(entities, dict) or not set(entities) <= _ENTITY_CATEGORIES:
            raise EvaluationInputError("case entities must use the fixed aggregate category set")
        if any(
            not isinstance(items, list) or any(not isinstance(item, str) for item in items)
            for items in entities.values()
        ):
            raise EvaluationInputError("case entity category values must be arrays of strings")
        opportunities = _count(
            case.get("non_target_opportunities", 0), context=f"cases[{index}].non_target_opportunities"
        )
        false_hits = _count(case.get("false_hotword_hits", 0), context=f"cases[{index}].false_hotword_hits")
        if false_hits > opportunities:
            raise EvaluationInputError("false hotword hits cannot exceed non-target opportunities")
        cases.append(
            {
                **case,
                "case_id": case_id,
                "variant": variant,
                "non_target_opportunities": opportunities,
                "false_hotword_hits": false_hits,
            }
        )

    raw_latencies = root["latencies"]
    if not isinstance(raw_latencies, list):
        raise EvaluationInputError("latencies must be an array")
    latency_fields = frozenset(
        {
            "partial_seconds",
            "stable_seconds",
            "stable_db_commit_ms",
            "stable_to_visible_ms",
            "ack_seconds",
            "alignment_error_ms",
        }
    )
    latencies: list[dict[str, float]] = []
    for index, value in enumerate(raw_latencies):
        row = _exact_object(value, required=latency_fields, context=f"latencies[{index}]")
        latencies.append(
            {field: _number(row[field], context=f"latencies[{index}].{field}") for field in latency_fields}
        )

    baseline_cer = _cer(cases, "baseline")
    candidate_cer = _cer(cases, "candidate")
    final_candidate_cer = _cer(cases, "final_candidate")
    lexicon = _lexicon_metrics(cases)
    distinct_case_ids = len({case["case_id"] for case in cases})
    metrics = {
        "case_count": len(cases),
        "distinct_case_count": distinct_case_ids,
        "latency_observation_count": len(latencies),
        "authorized_audio_seconds": authorized_audio_seconds,
        "session_count": session_count,
        "baseline_cer": baseline_cer,
        "candidate_cer": candidate_cer,
        "final_candidate_cer": final_candidate_cer,
        "cer_degradation": (
            candidate_cer - baseline_cer if candidate_cer is not None and baseline_cer is not None else None
        ),
        "final_cer_degradation_from_streaming": (
            final_candidate_cer - candidate_cer
            if final_candidate_cer is not None and candidate_cer is not None
            else None
        ),
        "partial_p95_seconds": _percentile([row["partial_seconds"] for row in latencies]),
        "stable_p95_seconds": _percentile([row["stable_seconds"] for row in latencies]),
        "stable_db_commit_p95_ms": _percentile([row["stable_db_commit_ms"] for row in latencies]),
        "stable_to_visible_p95_ms": _percentile([row["stable_to_visible_ms"] for row in latencies]),
        "ack_p95_seconds": _percentile([row["ack_seconds"] for row in latencies]),
        "alignment_p95_ms": _percentile([row["alignment_error_ms"] for row in latencies]),
        "entity_recall": _entity_recall(cases, "candidate"),
        "lexicon": lexicon,
    }

    def at_most(field: str) -> bool:
        value = metrics[field]
        return value is not None and float(value) <= HARD_LIMITS[field]

    gates = {
        "authorization_approved": authorization_approved,
        "all_cases_authorized": all_cases_authorized,
        "no_unapproved_production_or_historical_cases": unapproved_cases == 0,
        "dataset_independent_from_training": independent_training,
        "dataset_independent_from_lexicon_tuning": independent_lexicon,
        "sample_authorized_audio_at_least_30m": authorized_audio_seconds >= MINIMUM_SAMPLE["authorized_audio_seconds"],
        "sample_sessions_sufficient": session_count >= MINIMUM_SAMPLE["sessions"],
        "speaker_counts_2_4_8_covered": _EXPECTED_SPEAKER_COUNTS <= speaker_counts,
        "required_conditions_covered": _EXPECTED_CONDITIONS <= conditions,
        "sample_distinct_asr_cases": distinct_case_ids >= MINIMUM_SAMPLE["distinct_asr_cases"],
        "sample_latency_observations": len(latencies) >= MINIMUM_SAMPLE["latency_observations"],
        "partial_p95_at_most_1_2s": at_most("partial_p95_seconds"),
        "stable_p95_at_most_2_5s": at_most("stable_p95_seconds"),
        "stable_db_commit_p95_at_most_200ms": at_most("stable_db_commit_p95_ms"),
        "stable_to_visible_p95_at_most_250ms": at_most("stable_to_visible_p95_ms"),
        "ack_p95_at_most_300ms": at_most("ack_p95_seconds"),
        "alignment_p95_at_most_500ms": at_most("alignment_p95_ms"),
        "candidate_cer_degradation_at_most_2pp": (
            metrics["cer_degradation"] is not None
            and float(metrics["cer_degradation"]) <= HARD_LIMITS["cer_degradation"]
        ),
        "final_transcript_not_worse_than_streaming_final": (
            metrics["final_cer_degradation_from_streaming"] is not None
            and float(metrics["final_cer_degradation_from_streaming"]) <= 0
        ),
        "sample_lexicon_paired_cases": lexicon["paired_cases"] >= MINIMUM_SAMPLE["lexicon_paired_cases"],
        "sample_lexicon_term_occurrences": lexicon["term_occurrences"] >= MINIMUM_SAMPLE["lexicon_term_occurrences"],
        "sample_lexicon_non_target_opportunities": (
            lexicon["non_target_opportunities"] >= MINIMUM_SAMPLE["lexicon_non_target_opportunities"]
        ),
        "lexicon_cer_degradation_at_most_0_2pp": (
            lexicon["cer_degradation"] is not None
            and float(lexicon["cer_degradation"]) <= HARD_LIMITS["lexicon_cer_degradation"]
        ),
        "lexicon_false_trigger_rate_below_0_5_percent": (
            lexicon["false_trigger_rate"] is not None
            and float(lexicon["false_trigger_rate"]) < HARD_LIMITS["false_hotword_trigger_rate"]
        ),
        "lexicon_term_recall_improves": (
            lexicon["term_recall_off"] is not None
            and lexicon["term_recall_on"] is not None
            and float(lexicon["term_recall_on"]) > float(lexicon["term_recall_off"])
        ),
    }
    failures = [code for code, passed in gates.items() if not passed]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "evaluation_policy_version": EVALUATION_POLICY_VERSION,
        "source_sha256": source_sha256,
        "candidate": {"commit_sha": commit_sha, "environment_profile": environment_profile},
        "limits": HARD_LIMITS,
        "minimum_sample": MINIMUM_SAMPLE,
        "coverage": {
            "speaker_counts_covered": sorted(speaker_counts),
            "conditions_covered": sorted(conditions),
        },
        "metrics": metrics,
        "gates": gates,
        "failures": failures,
        "passed": not failures,
        "privacy_boundary": {"contains_transcript_text": False, "raw_sensitive_data_emitted": False},
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
        raise EvaluationInputError("unable to read ASR evidence input") from exc
    if len(raw) > MAX_INPUT_BYTES:
        raise EvaluationInputError("ASR evidence input exceeds the size limit")
    try:
        payload = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except EvaluationInputError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvaluationInputError("ASR evidence input must be valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise EvaluationInputError("ASR evidence input must be a JSON object")
    return payload, raw


def _display(value: Any, suffix: str = "") -> str:
    return "not measured" if value is None else f"{float(value):.6f}{suffix}"


def _markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lexicon = metrics["lexicon"]
    status = "PASS" if report["passed"] else "FAIL"
    lines = [
        "# Meeting ASR Release Evaluation",
        "",
        f"- Status: **{status}**",
        f"- Candidate: `{report['candidate']['commit_sha']}`",
        f"- Environment profile: `{report['candidate']['environment_profile']}`",
        f"- Source SHA-256: `{report['source_sha256']}`",
        f"- Distinct cases: `{metrics['distinct_case_count']}`",
        f"- Authorized audio: `{metrics['authorized_audio_seconds']}` seconds",
        f"- Baseline CER: `{_display(metrics['baseline_cer'])}`",
        f"- Streaming candidate CER: `{_display(metrics['candidate_cer'])}`",
        f"- Final candidate CER: `{_display(metrics['final_candidate_cer'])}`",
        f"- Partial P95: `{_display(metrics['partial_p95_seconds'], 's')}`",
        f"- Stable P95: `{_display(metrics['stable_p95_seconds'], 's')}`",
        f"- Stable DB commit P95: `{_display(metrics['stable_db_commit_p95_ms'], 'ms')}`",
        f"- Stable-to-visible P95: `{_display(metrics['stable_to_visible_p95_ms'], 'ms')}`",
        f"- Lexicon paired cases: `{lexicon['paired_cases']}`",
        "",
        "The report intentionally excludes transcript text, audio paths, user identities, dataset labels, and authorization references.",
    ]
    if report["failures"]:
        lines.extend(["", "## Blocking Gates", "", *[f"- `{code}`" for code in report["failures"]]])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate authorized meeting ASR release evidence.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--require-passing", action="store_true")
    args = parser.parse_args()
    try:
        payload, raw = _load_input(args.input)
        report = evaluate(payload, source_sha256=hashlib.sha256(raw).hexdigest())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if args.markdown:
            args.markdown.parent.mkdir(parents=True, exist_ok=True)
            args.markdown.write_text(_markdown(report), encoding="utf-8")
    except EvaluationInputError as exc:
        parser.exit(2, f"ASR release evidence rejected: {exc}\n")
    return 1 if args.require_passing and not report["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
