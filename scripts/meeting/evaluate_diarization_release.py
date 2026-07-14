#!/usr/bin/env python3
"""Score diarization annotations without emitting recording or speaker identities."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Sequence

ANNOTATION_SCHEMA_VERSION = "siq.meeting.diarization-annotation.v1"
EVIDENCE_MANIFEST_SCHEMA_VERSION = "siq.meeting.diarization-release-evidence.v1"
REPORT_SCHEMA_VERSION = "siq.meeting.diarization-release-evaluation.v1"
SCORING_POLICY_VERSION = "siq.meeting.diarization-scoring.v1"
RECLUSTER_POLICY_SCHEMA_VERSION = "siq.meeting.speaker_recluster_policy.v1"
MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_SEGMENTS = 500_000
MAX_RECORDING_DURATION_MS = 24 * 60 * 60 * 1000

LIMITS = {
    "diarization_error_rate_max": 0.15,
    "fragmentation_rate_max": 0.25,
    "over_merge_rate_max": 0.05,
    "track_purity_min": 0.90,
}

MINIMUM_SAMPLE = {
    "recordings": 14,
    "unique_reference_speakers": 14,
    "reference_speaker_time_ms": 3_600_000,
}

SCORING_PROTOCOL = {
    "collar_ms": 0,
    "overlap_scored": True,
    "speaker_mapping": "per_recording_maximum_overlap_one_to_one",
    "time_resolution_ms": 1,
    "fragmentation_association": "each_hypothesis_track_to_highest_overlap_reference",
    "over_merge_association": "each_reference_to_highest_overlap_hypothesis_track",
}

PRIVACY_BOUNDARY = {
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

_EXPECTED_SPEAKER_COUNTS = frozenset(range(2, 9))

_OPAQUE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_OPAQUE_REFERENCE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}\Z")
_COMMIT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_POLICY_VERSION_RE = re.compile(r"speaker-recluster\.[a-z0-9][a-z0-9._-]{0,80}\.v[1-9][0-9]*\Z")
_ENCODER_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{2,191}\Z")

_RECLUSTER_THRESHOLD_KEYS = frozenset(
    {
        "review_min_score",
        "merge_min_score",
        "singleton_merge_min_score",
        "min_top2_margin",
        "min_segment_ms",
        "max_segment_ms",
        "max_samples_per_track",
        "max_total_samples",
        "max_tracks",
        "max_noise_level",
        "min_asr_confidence",
        "min_rms",
        "max_clipping_ratio",
    }
)


class EvaluationInputError(ValueError):
    """Raised when an annotation is unsafe or cannot be scored."""


@dataclass(frozen=True, slots=True)
class Segment:
    start_ms: int
    end_ms: int
    speaker_id: str


@dataclass(frozen=True, slots=True)
class RecordingScore:
    reference_speaker_time_ms: int
    hypothesis_speaker_time_ms: int
    missed_speech_ms: int
    false_alarm_speech_ms: int
    speaker_confusion_ms: int
    reference_speakers: tuple[str, ...]
    hypothesis_speakers: tuple[str, ...]
    predicted_track_counts: dict[str, int]
    primary_track_by_reference: dict[str, str]
    reference_count_by_primary_track: dict[str, int]
    purity_numerator_ms: int
    purity_denominator_ms: int


def _exact_object(value: Any, *, keys: frozenset[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvaluationInputError(f"{context} must be an object")
    if set(value) != keys:
        raise EvaluationInputError(f"{context} has an invalid field set")
    return value


def _opaque_id(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or _OPAQUE_ID_RE.fullmatch(value) is None:
        raise EvaluationInputError(f"{context} must be an opaque ASCII identifier")
    return value


def _opaque_reference(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or _OPAQUE_REFERENCE_RE.fullmatch(value) is None:
        raise EvaluationInputError(f"{context} must be an opaque ASCII reference")
    return value


def _boolean(value: Any, *, context: str) -> bool:
    if not isinstance(value, bool):
        raise EvaluationInputError(f"{context} must be a boolean")
    return value


def _count(value: Any, *, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EvaluationInputError(f"{context} must be a non-negative integer")
    return value


def _unit_float(value: Any, *, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationInputError(f"{context} must be a finite number from zero through one")
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise EvaluationInputError(f"{context} must be a finite number from zero through one")
    return result


def _bounded_integer(value: Any, *, minimum: int, maximum: int, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise EvaluationInputError(f"{context} is outside its fixed safety bound")
    return value


def _millisecond(value: Any, *, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EvaluationInputError(f"{context} must be a non-negative integer")
    if value > MAX_RECORDING_DURATION_MS:
        raise EvaluationInputError(f"{context} exceeds the maximum recording duration")
    return value


def _validate_segments(segments: Sequence[Segment], *, context: str) -> tuple[Segment, ...]:
    ordered = tuple(sorted(segments, key=lambda item: (item.start_ms, item.end_ms, item.speaker_id)))
    last_end_by_speaker: dict[str, int] = {}
    for segment in ordered:
        if segment.end_ms <= segment.start_ms:
            raise EvaluationInputError(f"{context} contains a non-positive segment")
        previous_end = last_end_by_speaker.get(segment.speaker_id)
        if previous_end is not None and segment.start_ms < previous_end:
            raise EvaluationInputError(f"{context} contains overlapping segments for one speaker")
        last_end_by_speaker[segment.speaker_id] = segment.end_ms
    return ordered


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvaluationInputError("input contains duplicate JSON object keys")
        result[key] = value
    return result


def _parse_recluster_policy(value: Any) -> dict[str, Any]:
    policy = _exact_object(
        value,
        keys=frozenset({"schema_version", "version", "final_diarizer_ref", "encoder_ref", "thresholds"}),
        context="policy",
    )
    if policy["schema_version"] != RECLUSTER_POLICY_SCHEMA_VERSION:
        raise EvaluationInputError(f"policy.schema_version must be {RECLUSTER_POLICY_SCHEMA_VERSION}")
    version = policy["version"]
    if not isinstance(version, str) or _POLICY_VERSION_RE.fullmatch(version) is None or ".validated." not in version:
        raise EvaluationInputError("policy.version must be a versioned speaker-recluster.validated.*.vN identifier")
    encoder_ref = policy["encoder_ref"]
    if not isinstance(encoder_ref, str) or _ENCODER_REF_RE.fullmatch(encoder_ref) is None:
        raise EvaluationInputError("policy.encoder_ref must be a non-secret encoder identity")
    final_diarizer_ref = policy["final_diarizer_ref"]
    if not isinstance(final_diarizer_ref, str) or _ENCODER_REF_RE.fullmatch(final_diarizer_ref) is None:
        raise EvaluationInputError("policy.final_diarizer_ref must be a non-secret final diarizer identity")
    raw_thresholds = _exact_object(
        policy["thresholds"],
        keys=_RECLUSTER_THRESHOLD_KEYS,
        context="policy.thresholds",
    )
    thresholds = {
        "review_min_score": _unit_float(raw_thresholds["review_min_score"], context="review_min_score"),
        "merge_min_score": _unit_float(raw_thresholds["merge_min_score"], context="merge_min_score"),
        "singleton_merge_min_score": _unit_float(
            raw_thresholds["singleton_merge_min_score"], context="singleton_merge_min_score"
        ),
        "min_top2_margin": _unit_float(raw_thresholds["min_top2_margin"], context="min_top2_margin"),
        "min_segment_ms": _bounded_integer(
            raw_thresholds["min_segment_ms"], minimum=1_000, maximum=15_000, context="min_segment_ms"
        ),
        "max_segment_ms": _bounded_integer(
            raw_thresholds["max_segment_ms"], minimum=1_000, maximum=15_000, context="max_segment_ms"
        ),
        "max_samples_per_track": _bounded_integer(
            raw_thresholds["max_samples_per_track"],
            minimum=1,
            maximum=16,
            context="max_samples_per_track",
        ),
        "max_total_samples": _bounded_integer(
            raw_thresholds["max_total_samples"], minimum=2, maximum=4_096, context="max_total_samples"
        ),
        "max_tracks": _bounded_integer(raw_thresholds["max_tracks"], minimum=2, maximum=256, context="max_tracks"),
        "max_noise_level": _unit_float(raw_thresholds["max_noise_level"], context="max_noise_level"),
        "min_asr_confidence": _unit_float(raw_thresholds["min_asr_confidence"], context="min_asr_confidence"),
        "min_rms": _unit_float(raw_thresholds["min_rms"], context="min_rms"),
        "max_clipping_ratio": _unit_float(raw_thresholds["max_clipping_ratio"], context="max_clipping_ratio"),
    }
    if not (thresholds["review_min_score"] <= thresholds["merge_min_score"] <= thresholds["singleton_merge_min_score"]):
        raise EvaluationInputError("policy speaker thresholds must be monotonic")
    if thresholds["min_segment_ms"] > thresholds["max_segment_ms"]:
        raise EvaluationInputError("policy segment duration bounds are invalid")
    if not thresholds["max_tracks"] <= thresholds["max_total_samples"]:
        raise EvaluationInputError("policy global sample bounds are invalid")
    return {
        "schema_version": RECLUSTER_POLICY_SCHEMA_VERSION,
        "version": version,
        "final_diarizer_ref": final_diarizer_ref,
        "encoder_ref": encoder_ref,
        "thresholds": thresholds,
    }


def _parse_speaker_counts(value: Any) -> list[int]:
    if not isinstance(value, list):
        raise EvaluationInputError("dataset.speaker_counts_covered must be an array")
    counts: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or not 1 <= item <= 256:
            raise EvaluationInputError("dataset.speaker_counts_covered must contain bounded positive integers")
        counts.append(item)
    if counts != sorted(set(counts)):
        raise EvaluationInputError("dataset.speaker_counts_covered must be sorted and unique")
    return counts


def parse_evidence_manifest(raw: bytes) -> dict[str, Any]:
    """Parse release authorization, independence, input binding, and policy metadata."""

    if len(raw) > MAX_MANIFEST_BYTES:
        raise EvaluationInputError("evidence manifest exceeds the input size limit")
    try:
        text = raw.decode("utf-8")
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except EvaluationInputError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvaluationInputError("evidence manifest must be valid UTF-8 JSON") from exc
    root = _exact_object(
        payload,
        keys=frozenset({"schema_version", "authorization", "candidate", "split", "inputs", "dataset", "policy"}),
        context="evidence manifest",
    )
    if root["schema_version"] != EVIDENCE_MANIFEST_SCHEMA_VERSION:
        raise EvaluationInputError(f"evidence manifest schema_version must be {EVIDENCE_MANIFEST_SCHEMA_VERSION}")

    authorization = _exact_object(
        root["authorization"],
        keys=frozenset(
            {"approved", "all_recordings_authorized", "unapproved_production_or_historical_recordings", "reference"}
        ),
        context="authorization",
    )
    candidate = _exact_object(
        root["candidate"],
        keys=frozenset({"commit_sha", "environment_profile"}),
        context="candidate",
    )
    if not isinstance(candidate["commit_sha"], str) or _COMMIT_RE.fullmatch(candidate["commit_sha"]) is None:
        raise EvaluationInputError("candidate.commit_sha must be a lowercase Git commit digest")
    split = _exact_object(
        root["split"],
        keys=frozenset(
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
    if split["kind"] not in {"independent_holdout", "other"}:
        raise EvaluationInputError("split.kind is invalid")
    inputs = _exact_object(
        root["inputs"],
        keys=frozenset({"reference_sha256", "hypothesis_sha256"}),
        context="inputs",
    )
    for key in ("reference_sha256", "hypothesis_sha256"):
        if not isinstance(inputs[key], str) or _SHA256_RE.fullmatch(inputs[key]) is None:
            raise EvaluationInputError(f"inputs.{key} must be a lowercase SHA-256 digest")
    dataset = _exact_object(
        root["dataset"],
        keys=frozenset(
            {"recording_count", "unique_reference_speaker_count", "reference_speaker_time_ms", "speaker_counts_covered"}
        ),
        context="dataset",
    )
    return {
        "schema_version": EVIDENCE_MANIFEST_SCHEMA_VERSION,
        "authorization": {
            "approved": _boolean(authorization["approved"], context="authorization.approved"),
            "all_recordings_authorized": _boolean(
                authorization["all_recordings_authorized"], context="authorization.all_recordings_authorized"
            ),
            "unapproved_production_or_historical_recordings": _count(
                authorization["unapproved_production_or_historical_recordings"],
                context="authorization.unapproved_production_or_historical_recordings",
            ),
            "reference": _opaque_reference(authorization["reference"], context="authorization.reference"),
        },
        "candidate": {
            "commit_sha": candidate["commit_sha"],
            "environment_profile": _opaque_reference(
                candidate["environment_profile"], context="candidate.environment_profile"
            ),
        },
        "split": {
            "kind": split["kind"],
            "independent_from_training": _boolean(
                split["independent_from_training"], context="split.independent_from_training"
            ),
            "independent_from_threshold_tuning": _boolean(
                split["independent_from_threshold_tuning"],
                context="split.independent_from_threshold_tuning",
            ),
            "speaker_overlap_count": _count(split["speaker_overlap_count"], context="split.speaker_overlap_count"),
            "recording_overlap_count": _count(
                split["recording_overlap_count"], context="split.recording_overlap_count"
            ),
        },
        "inputs": dict(inputs),
        "dataset": {
            "recording_count": _count(dataset["recording_count"], context="dataset.recording_count"),
            "unique_reference_speaker_count": _count(
                dataset["unique_reference_speaker_count"], context="dataset.unique_reference_speaker_count"
            ),
            "reference_speaker_time_ms": _count(
                dataset["reference_speaker_time_ms"], context="dataset.reference_speaker_time_ms"
            ),
            "speaker_counts_covered": _parse_speaker_counts(dataset["speaker_counts_covered"]),
        },
        "policy": _parse_recluster_policy(root["policy"]),
    }


def parse_json_annotation(raw: bytes) -> dict[str, tuple[Segment, ...]]:
    """Parse the strict time-segment JSON annotation contract."""

    if len(raw) > MAX_INPUT_BYTES:
        raise EvaluationInputError("annotation exceeds the input size limit")
    try:
        text = raw.decode("utf-8")
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except EvaluationInputError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvaluationInputError("annotation must be valid UTF-8 JSON") from exc
    root = _exact_object(
        payload,
        keys=frozenset({"schema_version", "recordings"}),
        context="annotation",
    )
    if root["schema_version"] != ANNOTATION_SCHEMA_VERSION:
        raise EvaluationInputError(f"schema_version must be {ANNOTATION_SCHEMA_VERSION}")
    recordings = root["recordings"]
    if not isinstance(recordings, list):
        raise EvaluationInputError("annotation.recordings must be an array")

    result: dict[str, tuple[Segment, ...]] = {}
    segment_count = 0
    for recording_index, raw_recording in enumerate(recordings):
        recording = _exact_object(
            raw_recording,
            keys=frozenset({"recording_id", "segments"}),
            context=f"recordings[{recording_index}]",
        )
        recording_id = _opaque_id(
            recording["recording_id"],
            context=f"recordings[{recording_index}].recording_id",
        )
        if recording_id in result:
            raise EvaluationInputError("annotation contains a duplicate recording identifier")
        raw_segments = recording["segments"]
        if not isinstance(raw_segments, list):
            raise EvaluationInputError(f"recordings[{recording_index}].segments must be an array")
        parsed: list[Segment] = []
        for segment_index, raw_segment in enumerate(raw_segments):
            segment = _exact_object(
                raw_segment,
                keys=frozenset({"start_ms", "end_ms", "speaker_id"}),
                context=f"recordings[{recording_index}].segments[{segment_index}]",
            )
            parsed.append(
                Segment(
                    start_ms=_millisecond(segment["start_ms"], context="segment.start_ms"),
                    end_ms=_millisecond(segment["end_ms"], context="segment.end_ms"),
                    speaker_id=_opaque_id(segment["speaker_id"], context="segment.speaker_id"),
                )
            )
        segment_count += len(parsed)
        if segment_count > MAX_SEGMENTS:
            raise EvaluationInputError("annotation exceeds the segment count limit")
        result[recording_id] = _validate_segments(parsed, context=f"recordings[{recording_index}]")
    return result


def _rttm_ms(value: str, *, context: str) -> int:
    try:
        seconds = Decimal(value)
    except InvalidOperation as exc:
        raise EvaluationInputError(f"{context} must be a finite decimal") from exc
    if not seconds.is_finite() or seconds < 0:
        raise EvaluationInputError(f"{context} must be a non-negative finite decimal")
    milliseconds = int((seconds * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if milliseconds > MAX_RECORDING_DURATION_MS:
        raise EvaluationInputError(f"{context} exceeds the maximum recording duration")
    return milliseconds


def parse_rttm_annotation(raw: bytes) -> dict[str, tuple[Segment, ...]]:
    """Parse RTTM SPEAKER rows, rounding boundaries to the nearest millisecond."""

    if len(raw) > MAX_INPUT_BYTES:
        raise EvaluationInputError("annotation exceeds the input size limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvaluationInputError("RTTM annotation must be valid UTF-8") from exc
    result: dict[str, list[Segment]] = defaultdict(list)
    segment_count = 0
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if len(fields) < 9 or fields[0] != "SPEAKER":
            raise EvaluationInputError(f"RTTM line {line_number} is not a valid SPEAKER row")
        recording_id = _opaque_id(fields[1], context=f"RTTM line {line_number} recording_id")
        speaker_id = _opaque_id(fields[7], context=f"RTTM line {line_number} speaker_id")
        start_ms = _rttm_ms(fields[3], context=f"RTTM line {line_number} onset")
        duration_ms = _rttm_ms(fields[4], context=f"RTTM line {line_number} duration")
        if duration_ms <= 0 or start_ms + duration_ms > MAX_RECORDING_DURATION_MS:
            raise EvaluationInputError(f"RTTM line {line_number} has an invalid duration")
        result[recording_id].append(Segment(start_ms, start_ms + duration_ms, speaker_id))
        segment_count += 1
        if segment_count > MAX_SEGMENTS:
            raise EvaluationInputError("annotation exceeds the segment count limit")
    return {
        recording_id: _validate_segments(segments, context="RTTM recording")
        for recording_id, segments in result.items()
    }


def _timeline_slices(
    reference: Sequence[Segment],
    hypothesis: Sequence[Segment],
) -> list[tuple[int, frozenset[str], frozenset[str]]]:
    starts_reference: dict[int, list[str]] = defaultdict(list)
    ends_reference: dict[int, list[str]] = defaultdict(list)
    starts_hypothesis: dict[int, list[str]] = defaultdict(list)
    ends_hypothesis: dict[int, list[str]] = defaultdict(list)
    boundaries: set[int] = set()
    for segment in reference:
        starts_reference[segment.start_ms].append(segment.speaker_id)
        ends_reference[segment.end_ms].append(segment.speaker_id)
        boundaries.update((segment.start_ms, segment.end_ms))
    for segment in hypothesis:
        starts_hypothesis[segment.start_ms].append(segment.speaker_id)
        ends_hypothesis[segment.end_ms].append(segment.speaker_id)
        boundaries.update((segment.start_ms, segment.end_ms))

    points = sorted(boundaries)
    active_reference: set[str] = set()
    active_hypothesis: set[str] = set()
    slices: list[tuple[int, frozenset[str], frozenset[str]]] = []
    for index, point in enumerate(points[:-1]):
        active_reference.difference_update(ends_reference[point])
        active_hypothesis.difference_update(ends_hypothesis[point])
        active_reference.update(starts_reference[point])
        active_hypothesis.update(starts_hypothesis[point])
        duration_ms = points[index + 1] - point
        if duration_ms:
            slices.append((duration_ms, frozenset(active_reference), frozenset(active_hypothesis)))
    return slices


def _maximum_weight_mapping(
    hypothesis_speakers: Sequence[str],
    reference_speakers: Sequence[str],
    overlap_ms: dict[tuple[str, str], int],
) -> dict[str, str]:
    """Return an exact maximum-overlap one-to-one mapping via Hungarian assignment."""

    if not hypothesis_speakers or not reference_speakers:
        return {}
    transpose = len(hypothesis_speakers) > len(reference_speakers)
    rows = list(reference_speakers if transpose else hypothesis_speakers)
    columns = list(hypothesis_speakers if transpose else reference_speakers)

    weights: list[list[int]] = []
    for row in rows:
        weights.append(
            [overlap_ms.get((column, row), 0) if transpose else overlap_ms.get((row, column), 0) for column in columns]
        )
    max_weight = max(max(row) for row in weights)
    costs = [[max_weight - weight for weight in row] for row in weights]

    row_count = len(rows)
    column_count = len(columns)
    u = [0] * (row_count + 1)
    v = [0] * (column_count + 1)
    matched_row = [0] * (column_count + 1)
    previous_column = [0] * (column_count + 1)
    for row_index in range(1, row_count + 1):
        matched_row[0] = row_index
        minimum = [math.inf] * (column_count + 1)
        used = [False] * (column_count + 1)
        column = 0
        while True:
            used[column] = True
            current_row = matched_row[column]
            delta = math.inf
            next_column = 0
            for candidate in range(1, column_count + 1):
                if used[candidate]:
                    continue
                reduced = costs[current_row - 1][candidate - 1] - u[current_row] - v[candidate]
                if reduced < minimum[candidate]:
                    minimum[candidate] = reduced
                    previous_column[candidate] = column
                if minimum[candidate] < delta:
                    delta = minimum[candidate]
                    next_column = candidate
            for candidate in range(column_count + 1):
                if used[candidate]:
                    u[matched_row[candidate]] += int(delta)
                    v[candidate] -= int(delta)
                else:
                    minimum[candidate] -= delta
            column = next_column
            if matched_row[column] == 0:
                break
        while True:
            prior = previous_column[column]
            matched_row[column] = matched_row[prior]
            column = prior
            if column == 0:
                break

    row_to_column: dict[int, int] = {}
    for column in range(1, column_count + 1):
        if matched_row[column]:
            row_to_column[matched_row[column] - 1] = column - 1
    mapping: dict[str, str] = {}
    for row_index, column_index in row_to_column.items():
        if weights[row_index][column_index] <= 0:
            continue
        if transpose:
            mapping[columns[column_index]] = rows[row_index]
        else:
            mapping[rows[row_index]] = columns[column_index]
    return mapping


def _largest_overlap(
    candidates: Iterable[str],
    *,
    overlap_for: Any,
) -> str | None:
    ranked = sorted(
        ((int(overlap_for(candidate)), candidate) for candidate in candidates), key=lambda item: (-item[0], item[1])
    )
    if not ranked or ranked[0][0] <= 0:
        return None
    return ranked[0][1]


def score_recording(reference: Sequence[Segment], hypothesis: Sequence[Segment]) -> RecordingScore:
    """Score one recording using 0ms collar and overlap-aware speaker time."""

    reference = _validate_segments(reference, context="reference")
    hypothesis = _validate_segments(hypothesis, context="hypothesis")
    reference_speakers = tuple(sorted({segment.speaker_id for segment in reference}))
    hypothesis_speakers = tuple(sorted({segment.speaker_id for segment in hypothesis}))
    slices = _timeline_slices(reference, hypothesis)
    overlap_ms: dict[tuple[str, str], int] = defaultdict(int)
    reference_time_ms = 0
    hypothesis_time_ms = 0
    for duration_ms, active_reference, active_hypothesis in slices:
        reference_time_ms += duration_ms * len(active_reference)
        hypothesis_time_ms += duration_ms * len(active_hypothesis)
        for hypothesis_speaker in active_hypothesis:
            for reference_speaker in active_reference:
                overlap_ms[(hypothesis_speaker, reference_speaker)] += duration_ms

    mapping = _maximum_weight_mapping(hypothesis_speakers, reference_speakers, overlap_ms)
    missed_speech_ms = 0
    false_alarm_speech_ms = 0
    speaker_confusion_ms = 0
    for duration_ms, active_reference, active_hypothesis in slices:
        reference_count = len(active_reference)
        hypothesis_count = len(active_hypothesis)
        missed_speech_ms += duration_ms * max(0, reference_count - hypothesis_count)
        false_alarm_speech_ms += duration_ms * max(0, hypothesis_count - reference_count)
        correct = sum(1 for speaker in active_hypothesis if mapping.get(speaker) in active_reference)
        speaker_confusion_ms += duration_ms * (min(reference_count, hypothesis_count) - correct)

    primary_reference_by_track: dict[str, str] = {}
    purity_numerator_ms = 0
    purity_denominator_ms = 0
    for hypothesis_speaker in hypothesis_speakers:
        primary = _largest_overlap(
            reference_speakers,
            overlap_for=lambda reference_speaker, hypothesis_speaker=hypothesis_speaker: overlap_ms.get(
                (hypothesis_speaker, reference_speaker), 0
            ),
        )
        overlaps = sum(overlap_ms.get((hypothesis_speaker, speaker), 0) for speaker in reference_speakers)
        purity_denominator_ms += overlaps
        if primary is not None:
            primary_reference_by_track[hypothesis_speaker] = primary
            purity_numerator_ms += overlap_ms[(hypothesis_speaker, primary)]

    predicted_track_counts = {speaker: 0 for speaker in reference_speakers}
    for primary in primary_reference_by_track.values():
        predicted_track_counts[primary] += 1

    primary_track_by_reference: dict[str, str] = {}
    for reference_speaker in reference_speakers:
        primary = _largest_overlap(
            hypothesis_speakers,
            overlap_for=lambda hypothesis_speaker, reference_speaker=reference_speaker: overlap_ms.get(
                (hypothesis_speaker, reference_speaker), 0
            ),
        )
        if primary is not None:
            primary_track_by_reference[reference_speaker] = primary
    reference_count_by_primary_track: dict[str, int] = defaultdict(int)
    for primary in primary_track_by_reference.values():
        reference_count_by_primary_track[primary] += 1

    return RecordingScore(
        reference_speaker_time_ms=reference_time_ms,
        hypothesis_speaker_time_ms=hypothesis_time_ms,
        missed_speech_ms=missed_speech_ms,
        false_alarm_speech_ms=false_alarm_speech_ms,
        speaker_confusion_ms=speaker_confusion_ms,
        reference_speakers=reference_speakers,
        hypothesis_speakers=hypothesis_speakers,
        predicted_track_counts=predicted_track_counts,
        primary_track_by_reference=primary_track_by_reference,
        reference_count_by_primary_track=dict(reference_count_by_primary_track),
        purity_numerator_ms=purity_numerator_ms,
        purity_denominator_ms=purity_denominator_ms,
    )


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def evaluate(
    reference: dict[str, Sequence[Segment]],
    hypothesis: dict[str, Sequence[Segment]],
    *,
    evidence_manifest: dict[str, Any],
    evidence_manifest_sha256: str,
    reference_sha256: str,
    hypothesis_sha256: str,
    source_sha256: str,
) -> dict[str, Any]:
    """Aggregate recordings into a redacted, deterministic release report."""

    for name, digest in (
        ("evidence_manifest_sha256", evidence_manifest_sha256),
        ("reference_sha256", reference_sha256),
        ("hypothesis_sha256", hypothesis_sha256),
        ("source_sha256", source_sha256),
    ):
        if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
            raise EvaluationInputError(f"{name} must be a lowercase SHA-256 digest")
    if evidence_manifest.get("schema_version") != EVIDENCE_MANIFEST_SCHEMA_VERSION:
        raise EvaluationInputError("evidence manifest was not parsed with the required schema")
    if not reference or not any(reference.values()):
        raise EvaluationInputError("reference annotation must contain speech")
    if any(not segments for segments in reference.values()):
        raise EvaluationInputError("every reference recording must contain speech")
    unknown_recordings = set(hypothesis) - set(reference)
    if unknown_recordings:
        raise EvaluationInputError("hypothesis contains recordings absent from the reference")

    totals = {
        "reference_speaker_time_ms": 0,
        "hypothesis_speaker_time_ms": 0,
        "missed_speech_ms": 0,
        "false_alarm_speech_ms": 0,
        "speaker_confusion_ms": 0,
        "reference_speaker_count": 0,
        "hypothesis_track_count": 0,
        "fragmented_reference_speakers": 0,
        "fragmentation_excess_tracks": 0,
        "over_merged_hypothesis_tracks": 0,
        "over_merge_excess_speakers": 0,
        "references_on_over_merged_tracks": 0,
        "purity_numerator_ms": 0,
        "purity_denominator_ms": 0,
    }
    predicted_track_histogram: dict[str, int] = defaultdict(int)
    speaker_counts_covered: set[int] = set()
    unique_reference_speakers: set[str] = set()
    for recording_id in sorted(reference):
        score = score_recording(reference[recording_id], hypothesis.get(recording_id, ()))
        speaker_counts_covered.add(len(score.reference_speakers))
        unique_reference_speakers.update(score.reference_speakers)
        totals["reference_speaker_time_ms"] += score.reference_speaker_time_ms
        totals["hypothesis_speaker_time_ms"] += score.hypothesis_speaker_time_ms
        totals["missed_speech_ms"] += score.missed_speech_ms
        totals["false_alarm_speech_ms"] += score.false_alarm_speech_ms
        totals["speaker_confusion_ms"] += score.speaker_confusion_ms
        totals["reference_speaker_count"] += len(score.reference_speakers)
        totals["hypothesis_track_count"] += len(score.hypothesis_speakers)
        totals["purity_numerator_ms"] += score.purity_numerator_ms
        totals["purity_denominator_ms"] += score.purity_denominator_ms

        over_merged_tracks = {track for track, count in score.reference_count_by_primary_track.items() if count > 1}
        totals["over_merged_hypothesis_tracks"] += len(over_merged_tracks)
        totals["over_merge_excess_speakers"] += sum(
            score.reference_count_by_primary_track[track] - 1 for track in over_merged_tracks
        )
        totals["references_on_over_merged_tracks"] += sum(
            score.reference_count_by_primary_track[track] for track in over_merged_tracks
        )
        for reference_speaker in score.reference_speakers:
            track_count = score.predicted_track_counts[reference_speaker]
            predicted_track_histogram[str(track_count)] += 1
            if track_count > 1:
                totals["fragmented_reference_speakers"] += 1
                totals["fragmentation_excess_tracks"] += track_count - 1

    reference_time_ms = totals["reference_speaker_time_ms"]
    reference_speaker_count = totals["reference_speaker_count"]
    unique_reference_speaker_count = len(unique_reference_speakers)
    total_error_ms = totals["missed_speech_ms"] + totals["false_alarm_speech_ms"] + totals["speaker_confusion_ms"]
    diarization_error_rate = _rate(total_error_ms, reference_time_ms)
    fragmentation_rate = _rate(totals["fragmentation_excess_tracks"], reference_speaker_count)
    over_merge_rate = _rate(totals["references_on_over_merged_tracks"], reference_speaker_count)
    track_purity = _rate(totals["purity_numerator_ms"], totals["purity_denominator_ms"])

    authorization = evidence_manifest["authorization"]
    split = evidence_manifest["split"]
    inputs = evidence_manifest["inputs"]
    declared_dataset = evidence_manifest["dataset"]
    gates: dict[str, bool] = {
        "authorization_approved": authorization["approved"],
        "all_recordings_authorized": authorization["all_recordings_authorized"],
        "no_unapproved_production_or_historical_recordings": (
            authorization["unapproved_production_or_historical_recordings"] == 0
        ),
        "independent_holdout_kind": split["kind"] == "independent_holdout",
        "independent_from_training": split["independent_from_training"],
        "independent_from_threshold_tuning": split["independent_from_threshold_tuning"],
        "no_speaker_split_overlap": split["speaker_overlap_count"] == 0,
        "no_recording_split_overlap": split["recording_overlap_count"] == 0,
        "reference_annotation_sha256_matches_manifest": inputs["reference_sha256"] == reference_sha256,
        "hypothesis_annotation_sha256_matches_manifest": inputs["hypothesis_sha256"] == hypothesis_sha256,
        "manifest_recording_count_matches": declared_dataset["recording_count"] == len(reference),
        "manifest_unique_reference_speaker_count_matches": (
            declared_dataset["unique_reference_speaker_count"] == unique_reference_speaker_count
        ),
        "manifest_reference_speaker_time_matches": (declared_dataset["reference_speaker_time_ms"] == reference_time_ms),
        "manifest_speaker_counts_match": (declared_dataset["speaker_counts_covered"] == sorted(speaker_counts_covered)),
        "sample_recordings_at_least_14": len(reference) >= MINIMUM_SAMPLE["recordings"],
        "sample_unique_reference_speakers_at_least_14": (
            unique_reference_speaker_count >= MINIMUM_SAMPLE["unique_reference_speakers"]
        ),
        "sample_reference_speaker_time_at_least_1h": (reference_time_ms >= MINIMUM_SAMPLE["reference_speaker_time_ms"]),
        "speaker_counts_2_through_8_covered": _EXPECTED_SPEAKER_COUNTS <= speaker_counts_covered,
        "all_recordings_have_2_to_8_speakers": (
            bool(speaker_counts_covered) and speaker_counts_covered <= _EXPECTED_SPEAKER_COUNTS
        ),
        "diarization_error_rate_at_most_15_percent": reference_time_ms > 0
        and total_error_ms * 100 <= reference_time_ms * 15,
        "fragmentation_rate_at_most_25_percent": (
            reference_speaker_count > 0 and totals["fragmentation_excess_tracks"] * 100 <= reference_speaker_count * 25
        ),
        "over_merge_rate_at_most_5_percent": (
            reference_speaker_count > 0
            and totals["references_on_over_merged_tracks"] * 100 <= reference_speaker_count * 5
        ),
        "track_purity_at_least_90_percent": (
            totals["purity_denominator_ms"] > 0
            and totals["purity_numerator_ms"] * 100 >= totals["purity_denominator_ms"] * 90
        ),
    }
    failures = [code for code, passed in gates.items() if not passed]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "input_schema_version": ANNOTATION_SCHEMA_VERSION,
        "evidence_manifest_schema_version": EVIDENCE_MANIFEST_SCHEMA_VERSION,
        "scoring_policy_version": SCORING_POLICY_VERSION,
        "source_sha256": source_sha256,
        "evidence_manifest_sha256": evidence_manifest_sha256,
        "candidate": evidence_manifest["candidate"],
        "policy": evidence_manifest["policy"],
        "scoring_protocol": SCORING_PROTOCOL,
        "limits": LIMITS,
        "minimum_sample": MINIMUM_SAMPLE,
        "coverage": {"reference_speaker_counts_covered": sorted(speaker_counts_covered)},
        "metrics": {
            "recording_count": len(reference),
            "reference_speaker_count": reference_speaker_count,
            "unique_reference_speaker_count": unique_reference_speaker_count,
            "hypothesis_track_count": totals["hypothesis_track_count"],
            "unapproved_production_or_historical_recordings": authorization[
                "unapproved_production_or_historical_recordings"
            ],
            "speaker_split_overlap_count": split["speaker_overlap_count"],
            "recording_split_overlap_count": split["recording_overlap_count"],
            "reference_speaker_time_ms": reference_time_ms,
            "hypothesis_speaker_time_ms": totals["hypothesis_speaker_time_ms"],
            "missed_speech_ms": totals["missed_speech_ms"],
            "false_alarm_speech_ms": totals["false_alarm_speech_ms"],
            "speaker_confusion_ms": totals["speaker_confusion_ms"],
            "diarization_error_ms": total_error_ms,
            "missed_speech_rate": _rate(totals["missed_speech_ms"], reference_time_ms),
            "false_alarm_speech_rate": _rate(totals["false_alarm_speech_ms"], reference_time_ms),
            "speaker_confusion_rate": _rate(totals["speaker_confusion_ms"], reference_time_ms),
            "diarization_error_rate": diarization_error_rate,
            "fragmented_reference_speakers": totals["fragmented_reference_speakers"],
            "fragmentation_excess_tracks": totals["fragmentation_excess_tracks"],
            "fragmentation_rate": fragmentation_rate,
            "predicted_tracks_per_reference_histogram": dict(
                sorted(predicted_track_histogram.items(), key=lambda item: int(item[0]))
            ),
            "over_merged_hypothesis_tracks": totals["over_merged_hypothesis_tracks"],
            "over_merge_excess_speakers": totals["over_merge_excess_speakers"],
            "references_on_over_merged_tracks": totals["references_on_over_merged_tracks"],
            "over_merge_rate": over_merge_rate,
            "purity_numerator_ms": totals["purity_numerator_ms"],
            "purity_denominator_ms": totals["purity_denominator_ms"],
            "track_purity": track_purity,
        },
        "gates": gates,
        "failures": failures,
        "passed": not failures,
        "privacy_boundary": PRIVACY_BOUNDARY,
    }


def _load_annotation(path: Path) -> tuple[dict[str, tuple[Segment, ...]], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise EvaluationInputError("unable to read annotation input") from exc
    if path.suffix.lower() == ".rttm":
        return parse_rttm_annotation(raw), raw
    if path.suffix.lower() == ".json":
        return parse_json_annotation(raw), raw
    raise EvaluationInputError("annotation input must use a .rttm or .json extension")


def _load_evidence_manifest(path: Path) -> tuple[dict[str, Any], bytes]:
    if path.suffix.lower() != ".json":
        raise EvaluationInputError("evidence manifest must use a .json extension")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise EvaluationInputError("unable to read evidence manifest") from exc
    return parse_evidence_manifest(raw), raw


def _source_digest(reference_raw: bytes, hypothesis_raw: bytes) -> str:
    digest = hashlib.sha256()
    for raw in (reference_raw, hypothesis_raw):
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    return digest.hexdigest()


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Score RTTM or strict JSON diarization annotations and emit a redacted aggregate report."
    )
    parser.add_argument(
        "--evidence-manifest",
        type=Path,
        required=True,
        help="strict authorization, holdout, input digest, sample count, and policy manifest",
    )
    parser.add_argument("--reference", type=Path, required=True, help="reference .rttm or .json annotation")
    parser.add_argument("--hypothesis", type=Path, required=True, help="hypothesis .rttm or .json annotation")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--require-passing",
        action="store_true",
        help="exit nonzero when any fixed release threshold fails",
    )
    args = parser.parse_args()
    try:
        evidence_manifest, evidence_manifest_raw = _load_evidence_manifest(args.evidence_manifest)
        reference, reference_raw = _load_annotation(args.reference)
        hypothesis, hypothesis_raw = _load_annotation(args.hypothesis)
        report = evaluate(
            reference,
            hypothesis,
            evidence_manifest=evidence_manifest,
            evidence_manifest_sha256=hashlib.sha256(evidence_manifest_raw).hexdigest(),
            reference_sha256=hashlib.sha256(reference_raw).hexdigest(),
            hypothesis_sha256=hashlib.sha256(hypothesis_raw).hexdigest(),
            source_sha256=_source_digest(reference_raw, hypothesis_raw),
        )
        _write_report(args.output, report)
    except EvaluationInputError as exc:
        parser.exit(2, f"diarization release evidence rejected: {exc}\n")
    return 1 if args.require_passing and not report["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
