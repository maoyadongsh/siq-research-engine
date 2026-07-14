"""Bounded, ephemeral whole-meeting speaker-track reclustering.

Embeddings exist only inside this worker call. The module persists neither
audio nor vectors and emits only track mappings plus low-cardinality counts.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
from array import array
from bisect import bisect_left
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Sequence
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import httpx

from services.meeting_audio_store import MeetingAudioStore, MeetingAudioStoreError
from services.meeting_contracts import (
    MeetingAudioChunk,
    MeetingSession,
    MeetingSpeakerTrack,
    MeetingTranscriptSegment,
    SpeakerLabelSource,
)

EMBEDDING_SCHEMA = "siq.meeting.speaker_embedding.v1"
RECLUSTER_POLICY_SCHEMA = "siq.meeting.speaker_recluster_policy.v1"
DIARIZATION_REPORT_PRIVACY_BOUNDARY = {
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
DIARIZATION_REPORT_LIMITS = {
    "diarization_error_rate_max": 0.15,
    "fragmentation_rate_max": 0.25,
    "over_merge_rate_max": 0.05,
    "track_purity_min": 0.90,
}
DIARIZATION_REPORT_MINIMUM_SAMPLE = {
    "recordings": 14,
    "unique_reference_speakers": 14,
    "reference_speaker_time_ms": 3_600_000,
}
DIARIZATION_REPORT_SCORING_PROTOCOL = {
    "collar_ms": 0,
    "overlap_scored": True,
    "speaker_mapping": "per_recording_maximum_overlap_one_to_one",
    "time_resolution_ms": 1,
    "fragmentation_association": "each_hypothesis_track_to_highest_overlap_reference",
    "over_merge_association": "each_reference_to_highest_overlap_hypothesis_track",
}
DIARIZATION_REPORT_GATE_KEYS = frozenset(
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
DIARIZATION_REPORT_METRIC_KEYS = frozenset(
    {
        "recording_count",
        "reference_speaker_count",
        "unique_reference_speaker_count",
        "hypothesis_track_count",
        "unapproved_production_or_historical_recordings",
        "speaker_split_overlap_count",
        "recording_split_overlap_count",
        "reference_speaker_time_ms",
        "hypothesis_speaker_time_ms",
        "missed_speech_ms",
        "false_alarm_speech_ms",
        "speaker_confusion_ms",
        "diarization_error_ms",
        "missed_speech_rate",
        "false_alarm_speech_rate",
        "speaker_confusion_rate",
        "diarization_error_rate",
        "fragmented_reference_speakers",
        "fragmentation_excess_tracks",
        "fragmentation_rate",
        "predicted_tracks_per_reference_histogram",
        "over_merged_hypothesis_tracks",
        "over_merge_excess_speakers",
        "references_on_over_merged_tracks",
        "over_merge_rate",
        "purity_numerator_ms",
        "purity_denominator_ms",
        "track_purity",
    }
)
DIARIZATION_REPORT_TOP_LEVEL_KEYS = frozenset(
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
)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _reject_report_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("speaker validation report contains duplicate JSON keys")
        value[key] = item
    return value


def _reject_report_nonfinite(value: str) -> object:
    raise ValueError(f"speaker validation report contains a non-finite value: {value}")


def _verify_validation_report(
    expected_sha256: str | None,
    *,
    policy: SpeakerReclusterPolicy,
    encoder_ref: str,
) -> None:
    """Require an operator-supplied, passing, hashed offline evaluation report."""

    report_path = os.getenv("SIQ_MEETING_SPEAKER_RECLUSTER_VALIDATION_REPORT", "").strip()
    if not report_path:
        raise ValueError("speaker auto-apply requires a validation report path")
    if not isinstance(expected_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise ValueError("speaker validation report hash is invalid")
    path = Path(report_path).expanduser()
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("speaker validation report is not a bounded regular file")
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError("speaker validation report is unavailable") from exc
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("speaker validation report hash does not match policy")
    try:
        report = json.loads(
            raw,
            object_pairs_hook=_reject_report_duplicate_keys,
            parse_constant=_reject_report_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("speaker validation report is not valid JSON") from exc

    expected_thresholds = {
        "review_min_score": policy.review_min_score,
        "merge_min_score": policy.merge_min_score,
        "singleton_merge_min_score": policy.singleton_merge_min_score,
        "min_top2_margin": policy.min_top2_margin,
        "min_segment_ms": policy.min_segment_ms,
        "max_segment_ms": policy.max_segment_ms,
        "max_samples_per_track": policy.max_samples_per_track,
        "max_total_samples": policy.max_total_samples,
        "max_tracks": policy.max_tracks,
        "max_noise_level": policy.max_noise_level,
        "min_asr_confidence": policy.min_asr_confidence,
        "min_rms": policy.min_rms,
        "max_clipping_ratio": policy.max_clipping_ratio,
    }
    report_policy = report.get("policy") if isinstance(report, dict) else None
    gates = report.get("gates") if isinstance(report, dict) else None
    metrics = report.get("metrics") if isinstance(report, dict) else None
    candidate = report.get("candidate") if isinstance(report, dict) else None
    coverage = report.get("coverage") if isinstance(report, dict) else None
    policy_ok = (
        isinstance(report_policy, dict)
        and set(report_policy) == {"schema_version", "version", "final_diarizer_ref", "encoder_ref", "thresholds"}
        and report_policy.get("schema_version") == RECLUSTER_POLICY_SCHEMA
        and report_policy.get("version") == policy.version
        and report_policy.get("final_diarizer_ref") == policy.final_diarizer_ref
        and report_policy.get("encoder_ref") == encoder_ref
        and report_policy.get("thresholds") == expected_thresholds
    )
    hashes_ok = all(
        isinstance(report.get(field), str) and re.fullmatch(r"[0-9a-f]{64}", report[field]) is not None
        for field in ("source_sha256", "evidence_manifest_sha256")
    ) if isinstance(report, dict) else False
    candidate_ok = (
        isinstance(candidate, dict)
        and set(candidate) == {"commit_sha", "environment_profile"}
        and isinstance(candidate.get("commit_sha"), str)
        and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", candidate["commit_sha"]) is not None
        and isinstance(candidate.get("environment_profile"), str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}", candidate["environment_profile"]) is not None
    )
    metrics_ok = isinstance(metrics, dict) and set(metrics) == DIARIZATION_REPORT_METRIC_KEYS
    integer_metric_keys = DIARIZATION_REPORT_METRIC_KEYS - {
        "missed_speech_rate",
        "false_alarm_speech_rate",
        "speaker_confusion_rate",
        "diarization_error_rate",
        "fragmentation_rate",
        "predicted_tracks_per_reference_histogram",
        "over_merge_rate",
        "track_purity",
    }
    if metrics_ok:
        if metrics["reference_speaker_time_ms"] <= 0 or metrics["reference_speaker_count"] <= 0:
            metrics_ok = False
        else:
            metrics_ok = all(
                not isinstance(metrics[key], bool) and isinstance(metrics[key], int) and metrics[key] >= 0
                for key in integer_metric_keys
            )
    if metrics_ok:
        rate_keys = (
            "missed_speech_rate",
            "false_alarm_speech_rate",
            "speaker_confusion_rate",
            "diarization_error_rate",
            "fragmentation_rate",
            "over_merge_rate",
            "track_purity",
        )
        metrics_ok = all(
            not isinstance(metrics[key], bool)
            and isinstance(metrics[key], (int, float))
            and math.isfinite(metrics[key])
            and 0 <= metrics[key] <= 1
            for key in rate_keys
        )
    if metrics_ok:
        histogram = metrics["predicted_tracks_per_reference_histogram"]
        metrics_ok = (
            isinstance(histogram, dict)
            and bool(histogram)
            and all(
                isinstance(key, str)
                and key.isdigit()
                and not isinstance(value, bool)
                and isinstance(value, int)
                and value >= 0
                for key, value in histogram.items()
            )
            and sum(histogram.values()) == metrics["reference_speaker_count"]
            and metrics["diarization_error_ms"]
            == metrics["missed_speech_ms"] + metrics["false_alarm_speech_ms"] + metrics["speaker_confusion_ms"]
            and metrics["recording_count"] >= DIARIZATION_REPORT_MINIMUM_SAMPLE["recordings"]
            and 2 * metrics["recording_count"]
            <= metrics["reference_speaker_count"]
            <= 8 * metrics["recording_count"]
            and metrics["unique_reference_speaker_count"] <= metrics["reference_speaker_count"]
            and metrics["unique_reference_speaker_count"]
            >= DIARIZATION_REPORT_MINIMUM_SAMPLE["unique_reference_speakers"]
            and metrics["reference_speaker_time_ms"]
            >= DIARIZATION_REPORT_MINIMUM_SAMPLE["reference_speaker_time_ms"]
            and metrics["unapproved_production_or_historical_recordings"] == 0
            and metrics["speaker_split_overlap_count"] == 0
            and metrics["recording_split_overlap_count"] == 0
        )
    if metrics_ok:
        reference_time_ms = metrics["reference_speaker_time_ms"]
        reference_speaker_count = metrics["reference_speaker_count"]
        histogram = metrics["predicted_tracks_per_reference_histogram"]
        fragmentation_excess = sum(
            max(int(track_count) - 1, 0) * count for track_count, count in histogram.items()
        )
        fragmented_count = sum(count for track_count, count in histogram.items() if int(track_count) > 1)
        expected_rates = {
            "missed_speech_rate": metrics["missed_speech_ms"] / reference_time_ms,
            "false_alarm_speech_rate": metrics["false_alarm_speech_ms"] / reference_time_ms,
            "speaker_confusion_rate": metrics["speaker_confusion_ms"] / reference_time_ms,
            "diarization_error_rate": metrics["diarization_error_ms"] / reference_time_ms,
            "fragmentation_rate": metrics["fragmentation_excess_tracks"] / reference_speaker_count,
            "over_merge_rate": metrics["references_on_over_merged_tracks"] / reference_speaker_count,
        }
        metrics_ok = (
            reference_time_ms > 0
            and reference_speaker_count > 0
            and all(math.isclose(metrics[key], value, rel_tol=1e-9, abs_tol=1e-12) for key, value in expected_rates.items())
            and metrics["fragmentation_excess_tracks"] == fragmentation_excess
            and metrics["fragmented_reference_speakers"] == fragmented_count
            and metrics["references_on_over_merged_tracks"]
            == metrics["over_merge_excess_speakers"] + metrics["over_merged_hypothesis_tracks"]
            and metrics["hypothesis_speaker_time_ms"]
            == reference_time_ms - metrics["missed_speech_ms"] + metrics["false_alarm_speech_ms"]
            and metrics["hypothesis_track_count"]
            >= sum(int(track_count) * count for track_count, count in histogram.items())
            and metrics["purity_denominator_ms"] > 0
            and metrics["purity_numerator_ms"] <= metrics["purity_denominator_ms"]
            and math.isclose(
                metrics["track_purity"],
                metrics["purity_numerator_ms"] / metrics["purity_denominator_ms"],
                rel_tol=1e-9,
                abs_tol=1e-12,
            )
            and metrics["track_purity"] >= DIARIZATION_REPORT_LIMITS["track_purity_min"]
        )
    gate_conditions = {
        "sample_recordings_at_least_14": bool(metrics_ok and metrics["recording_count"] >= 14),
        "sample_unique_reference_speakers_at_least_14": bool(
            metrics_ok and metrics["unique_reference_speaker_count"] >= 14
        ),
        "sample_reference_speaker_time_at_least_1h": bool(
            metrics_ok and metrics["reference_speaker_time_ms"] >= 3_600_000
        ),
        "diarization_error_rate_at_most_15_percent": bool(
            metrics_ok and metrics["diarization_error_rate"] <= DIARIZATION_REPORT_LIMITS["diarization_error_rate_max"]
        ),
        "fragmentation_rate_at_most_25_percent": bool(
            metrics_ok and metrics["fragmentation_rate"] <= DIARIZATION_REPORT_LIMITS["fragmentation_rate_max"]
        ),
        "over_merge_rate_at_most_5_percent": bool(
            metrics_ok and metrics["over_merge_rate"] <= DIARIZATION_REPORT_LIMITS["over_merge_rate_max"]
        ),
        "track_purity_at_least_90_percent": bool(
            metrics_ok and metrics["track_purity"] >= DIARIZATION_REPORT_LIMITS["track_purity_min"]
        ),
    }
    gates_ok = (
        isinstance(gates, dict)
        and set(gates) == DIARIZATION_REPORT_GATE_KEYS
        and all(value is True for value in gates.values())
        and all(gates.get(key) is value for key, value in gate_conditions.items())
    )
    if (
        not isinstance(report, dict)
        or set(report) != DIARIZATION_REPORT_TOP_LEVEL_KEYS
        or report.get("schema_version") != "siq.meeting.diarization-release-evaluation.v1"
        or report.get("input_schema_version") != "siq.meeting.diarization-annotation.v1"
        or report.get("evidence_manifest_schema_version") != "siq.meeting.diarization-release-evidence.v1"
        or report.get("scoring_policy_version") != "siq.meeting.diarization-scoring.v1"
        or report.get("passed") is not True
        or report.get("failures") != []
        or not hashes_ok
        or not candidate_ok
        or not policy_ok
        or not gates_ok
        or report.get("scoring_protocol") != DIARIZATION_REPORT_SCORING_PROTOCOL
        or report.get("limits") != DIARIZATION_REPORT_LIMITS
        or report.get("minimum_sample") != DIARIZATION_REPORT_MINIMUM_SAMPLE
        or coverage != {"reference_speaker_counts_covered": list(range(2, 9))}
        or not metrics_ok
        or report.get("privacy_boundary") != DIARIZATION_REPORT_PRIVACY_BOUNDARY
    ):
        raise ValueError("speaker validation report is not a passing diarization evaluation")


class MeetingSpeakerReclusterError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class SpeakerReclusterPolicy:
    version: str = "speaker-recluster.unvalidated.v1"
    final_diarizer_ref: str = ""
    auto_apply_validated: bool = False
    validation_artifact_sha256: str | None = None
    operator_enabled: bool = False
    review_min_score: float = 0.72
    merge_min_score: float = 0.82
    singleton_merge_min_score: float = 0.92
    min_top2_margin: float = 0.04
    min_segment_ms: int = 1_500
    max_segment_ms: int = 8_000
    max_samples_per_track: int = 4
    max_total_samples: int = 256
    max_tracks: int = 64
    max_noise_level: float = 0.65
    min_asr_confidence: float = 0.45
    min_rms: float = 0.003
    max_clipping_ratio: float = 0.01

    def __post_init__(self) -> None:
        scores = (
            self.review_min_score,
            self.merge_min_score,
            self.singleton_merge_min_score,
            self.min_top2_margin,
            self.max_noise_level,
            self.min_asr_confidence,
            self.min_rms,
            self.max_clipping_ratio,
        )
        if not self.version.strip() or any(not 0 <= value <= 1 for value in scores):
            raise ValueError("speaker recluster policy values are invalid")
        if not self.review_min_score <= self.merge_min_score <= self.singleton_merge_min_score:
            raise ValueError("speaker recluster thresholds are not monotonic")
        if not 1_000 <= self.min_segment_ms <= self.max_segment_ms <= 15_000:
            raise ValueError("speaker recluster sample duration is invalid")
        if not 1 <= self.max_samples_per_track <= 16:
            raise ValueError("speaker recluster per-track sample bound is invalid")
        if not 2 <= self.max_tracks <= 256 or not self.max_tracks <= self.max_total_samples <= 4_096:
            raise ValueError("speaker recluster global bounds are invalid")
        if self.auto_apply_validated:
            if (
                ".validated." not in self.version
                or re.fullmatch(r"speaker-recluster\.[a-z0-9][a-z0-9._-]{0,80}\.v[1-9][0-9]*", self.version)
                is None
            ):
                raise ValueError("an unvalidated speaker policy cannot auto-apply")
            if not isinstance(self.final_diarizer_ref, str) or re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:/-]{2,191}", self.final_diarizer_ref
            ) is None:
                raise ValueError("speaker auto-apply requires a final diarizer identity")
            if not self.operator_enabled:
                raise ValueError("speaker auto-apply requires an explicit operator gate")
            if not isinstance(self.validation_artifact_sha256, str) or not re.fullmatch(
                r"[0-9a-f]{64}", self.validation_artifact_sha256
            ):
                raise ValueError("speaker auto-apply requires a validation artifact hash")

    @classmethod
    def from_env(cls) -> "SpeakerReclusterPolicy":
        raw = os.getenv("SIQ_MEETING_SPEAKER_RECLUSTER_POLICY_JSON", "").strip()
        configured_final_diarizer = os.getenv("SIQ_MEETING_SPEAKER_RECLUSTER_FINAL_DIARIZER_REF", "").strip()
        if not raw:
            return cls(final_diarizer_ref=configured_final_diarizer)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("speaker recluster policy is invalid JSON") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != RECLUSTER_POLICY_SCHEMA:
            raise ValueError("speaker recluster policy schema is invalid")
        allowed = {
            "schema_version",
            "version",
            "final_diarizer_ref",
            "auto_apply_validated",
            "validation_artifact_sha256",
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
        if set(payload) - allowed:
            raise ValueError("speaker recluster policy contains unknown fields")
        values = {key: value for key, value in payload.items() if key != "schema_version"}
        payload_final_diarizer = values.pop("final_diarizer_ref", None)
        if payload_final_diarizer is not None and payload_final_diarizer != configured_final_diarizer:
            raise ValueError("speaker policy final diarizer does not match the configured final diarizer")
        values["final_diarizer_ref"] = configured_final_diarizer
        values["operator_enabled"] = _env_bool("SIQ_MEETING_SPEAKER_RECLUSTER_AUTO_APPLY_ENABLED", False)
        policy = cls(**values)
        if policy.auto_apply_validated:
            _verify_validation_report(
                policy.validation_artifact_sha256,
                policy=policy,
                encoder_ref=os.getenv(
                    "SIQ_MEETING_SPEAKER_RECLUSTER_ENCODER_REF",
                    "iic/speech_eres2netv2_sv_zh-cn_16k-common",
                ).strip(),
            )
        return policy


@dataclass(frozen=True, slots=True)
class ReclusterSampleWindow:
    segment_id: str
    track_id: str
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass(frozen=True, slots=True)
class TrackEmbedding:
    track_id: str
    values: tuple[float, ...]
    sample_count: int
    effective_duration_ms: int


@dataclass(frozen=True, slots=True)
class SpeakerMergeProposal:
    source_track_ids: tuple[str, ...]
    target_track_id: str
    score: float
    auto_apply: bool
    reason_code: str


@dataclass(frozen=True, slots=True)
class SpeakerReclusterPlan:
    track_targets: dict[str, str] = field(default_factory=dict)
    proposals: tuple[SpeakerMergeProposal, ...] = ()
    embedded_track_count: int = 0
    selected_sample_count: int = 0
    skipped_sample_count: int = 0
    encoder_ref: str | None = None
    final_diarizer_ref: str | None = None
    policy_version: str = "speaker-recluster.unvalidated.v1"
    validation_artifact_sha256: str | None = None
    automatic_enabled: bool = False
    degraded_reason: str | None = None


@dataclass(frozen=True, slots=True)
class DiarizationEmbedding:
    encoder_ref: str
    values: tuple[float, ...]
    duration_ms: int


class DiarizationEmbeddingClient(Protocol):
    async def embed(
        self,
        pcm: bytes,
        *,
        meeting_id: str,
        run_id: str,
    ) -> DiarizationEmbedding: ...


class HttpDiarizationEmbeddingClient:
    def __init__(
        self,
        *,
        endpoint: str,
        service_token: str,
        expected_encoder_ref: str,
        timeout_seconds: float = 20.0,
        max_response_bytes: int = 262_144,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("diarization embedding endpoint is invalid")
        if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("diarization embedding endpoint must use HTTPS or loopback")
        if not service_token.strip() or not expected_encoder_ref.strip():
            raise ValueError("diarization embedding identity is required")
        if timeout_seconds <= 0 or not 1_024 <= max_response_bytes <= 1_048_576:
            raise ValueError("diarization embedding HTTP bounds are invalid")
        self.endpoint = endpoint
        self.service_token = service_token
        self.expected_encoder_ref = expected_encoder_ref
        self.max_response_bytes = max_response_bytes
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            trust_env=False,
        )
        self._owns_client = client is None

    async def embed(self, pcm: bytes, *, meeting_id: str, run_id: str) -> DiarizationEmbedding:
        try:
            UUID(meeting_id)
            UUID(run_id)
        except (TypeError, ValueError) as exc:
            raise MeetingSpeakerReclusterError("SPEAKER_RECLUSTER_SCOPE_INVALID", "recluster scope is invalid") from exc
        try:
            async with self.client.stream(
                "POST",
                self.endpoint,
                content=pcm,
                headers={
                    "X-SIQ-Service-Token": self.service_token,
                    "X-SIQ-Speaker-Purpose": "diarization",
                    "X-SIQ-Meeting-ID": meeting_id,
                    "X-SIQ-Diarization-Run-ID": run_id,
                    "X-SIQ-Audio-Encoding": "pcm_s16le",
                    "Content-Type": "application/octet-stream",
                },
            ) as response:
                if response.status_code != 200:
                    raise MeetingSpeakerReclusterError(
                        "SPEAKER_RECLUSTER_ENCODER_UNAVAILABLE",
                        "diarization embedding service rejected the request",
                        retryable=response.status_code >= 500 or response.status_code == 429,
                    )
                body = await _read_bounded_response(response, self.max_response_bytes)
        except httpx.HTTPError as exc:
            raise MeetingSpeakerReclusterError(
                "SPEAKER_RECLUSTER_ENCODER_UNAVAILABLE",
                "diarization embedding service is unavailable",
                retryable=True,
            ) from exc
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MeetingSpeakerReclusterError(
                "SPEAKER_RECLUSTER_ENCODER_RESPONSE_INVALID",
                "diarization embedding response is invalid",
                retryable=True,
            ) from exc
        scope = payload.get("scope") if isinstance(payload, dict) else None
        values = payload.get("embedding") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != EMBEDDING_SCHEMA
            or payload.get("purpose") != "diarization"
            or payload.get("persisted") is not False
            or payload.get("encoder_ref") != self.expected_encoder_ref
            or not isinstance(scope, dict)
            or scope.get("meeting_id") != meeting_id
            or scope.get("run_id") != run_id
            or not isinstance(values, list)
            or payload.get("dimension") != len(values)
            or not isinstance(payload.get("duration_ms"), int)
        ):
            raise MeetingSpeakerReclusterError(
                "SPEAKER_RECLUSTER_ENCODER_RESPONSE_INVALID",
                "diarization embedding response contract is invalid",
            )
        try:
            vector = l2_normalize(tuple(float(value) for value in values))
        except (TypeError, ValueError) as exc:
            raise MeetingSpeakerReclusterError(
                "SPEAKER_RECLUSTER_ENCODER_RESPONSE_INVALID",
                "diarization embedding vector is invalid",
            ) from exc
        duration_ms = int(payload["duration_ms"])
        if len(vector) < 2 or len(vector) > 16_384 or abs(duration_ms - len(pcm) // 32) > 100:
            raise MeetingSpeakerReclusterError(
                "SPEAKER_RECLUSTER_ENCODER_RESPONSE_INVALID",
                "diarization embedding identity is invalid",
            )
        return DiarizationEmbedding(self.expected_encoder_ref, vector, duration_ms)

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()


class MeetingSpeakerReclusterService:
    def __init__(
        self,
        *,
        policy: SpeakerReclusterPolicy,
        embedding_client: DiarizationEmbeddingClient | None,
        audio_store: MeetingAudioStore | None = None,
    ) -> None:
        self.policy = policy
        self.embedding_client = embedding_client
        self.audio_store = audio_store or MeetingAudioStore()

    @classmethod
    def from_env(cls) -> "MeetingSpeakerReclusterService":
        policy = SpeakerReclusterPolicy.from_env()
        endpoint = os.getenv("SIQ_MEETING_SPEAKER_RECLUSTER_EMBEDDING_URL", "").strip()
        if not endpoint:
            endpoint = _derived_embedding_url(os.getenv("SIQ_MEETING_FINAL_ASR_URL", "").strip()) or ""
        token = (
            os.getenv("SIQ_MEETING_SPEECH_INTERNAL_SERVICE_TOKEN")
            or os.getenv("SIQ_MEETING_ASR_SERVICE_TOKEN")
            or ""
        ).strip()
        encoder_ref = os.getenv(
            "SIQ_MEETING_SPEAKER_RECLUSTER_ENCODER_REF",
            "iic/speech_eres2netv2_sv_zh-cn_16k-common",
        ).strip()
        client = None
        if endpoint and token:
            client = HttpDiarizationEmbeddingClient(
                endpoint=endpoint,
                service_token=token,
                expected_encoder_ref=encoder_ref,
            )
        return cls(policy=policy, embedding_client=client)

    async def plan(
        self,
        *,
        meeting: MeetingSession,
        run_id: str,
        tracks: Sequence[MeetingSpeakerTrack],
        segments: Sequence[MeetingTranscriptSegment],
        chunks: Sequence[MeetingAudioChunk],
        protected_track_ids: set[str] | None = None,
    ) -> SpeakerReclusterPlan:
        if self.embedding_client is None:
            return SpeakerReclusterPlan(
                policy_version=self.policy.version,
                final_diarizer_ref=self.policy.final_diarizer_ref,
                validation_artifact_sha256=self.policy.validation_artifact_sha256,
                automatic_enabled=self.policy.auto_apply_validated,
                degraded_reason="SPEAKER_RECLUSTER_EMBEDDING_DISABLED",
            )
        if len(tracks) > self.policy.max_tracks:
            return SpeakerReclusterPlan(
                policy_version=self.policy.version,
                final_diarizer_ref=self.policy.final_diarizer_ref,
                validation_artifact_sha256=self.policy.validation_artifact_sha256,
                automatic_enabled=self.policy.auto_apply_validated,
                degraded_reason="SPEAKER_RECLUSTER_TRACK_LIMIT",
            )
        windows = select_sample_windows(segments, policy=self.policy)
        ordered_chunks = sorted(chunks, key=lambda item: (item.start_ms, item.stream_epoch, item.sequence))
        chunk_starts = [item.start_ms for item in ordered_chunks]
        vectors_by_track: dict[str, list[tuple[float, ...]]] = {}
        durations_by_track: dict[str, int] = {}
        skipped = 0
        encoder_ref: str | None = None
        embedding_dimension: int | None = None
        for window in windows:
            try:
                pcm = await asyncio.to_thread(
                    self.audio_store.read_pcm_range,
                    meeting.owner_user_id,
                    meeting.id,
                    _chunks_covering_window(ordered_chunks, chunk_starts, window.start_ms, window.end_ms),
                    window.start_ms,
                    window.end_ms,
                    window.duration_ms * 32,
                )
                if not _pcm_quality_ok(pcm, self.policy):
                    skipped += 1
                    continue
                embedded = await self.embedding_client.embed(
                    pcm,
                    meeting_id=meeting.id,
                    run_id=run_id,
                )
            except MeetingAudioStoreError:
                skipped += 1
                continue
            if encoder_ref is not None and embedded.encoder_ref != encoder_ref:
                raise MeetingSpeakerReclusterError(
                    "SPEAKER_RECLUSTER_ENCODER_CHANGED",
                    "speaker encoder changed during reclustering",
                )
            try:
                normalized_values = l2_normalize(embedded.values)
            except ValueError as exc:
                raise MeetingSpeakerReclusterError(
                    "SPEAKER_RECLUSTER_ENCODER_RESPONSE_INVALID",
                    "speaker encoder returned an invalid vector",
                ) from exc
            if (
                not isinstance(embedded.encoder_ref, str)
                or not embedded.encoder_ref.strip()
                or not 2 <= len(normalized_values) <= 16_384
                or not isinstance(embedded.duration_ms, int)
                or embedded.duration_ms <= 0
                or abs(embedded.duration_ms - window.duration_ms) > 100
            ):
                raise MeetingSpeakerReclusterError(
                    "SPEAKER_RECLUSTER_ENCODER_RESPONSE_INVALID",
                    "speaker encoder response identity is invalid",
                )
            if embedding_dimension is not None and len(normalized_values) != embedding_dimension:
                raise MeetingSpeakerReclusterError(
                    "SPEAKER_RECLUSTER_ENCODER_CHANGED",
                    "speaker encoder dimension changed during reclustering",
                )
            encoder_ref = embedded.encoder_ref
            embedding_dimension = len(normalized_values)
            vectors_by_track.setdefault(window.track_id, []).append(normalized_values)
            durations_by_track[window.track_id] = durations_by_track.get(window.track_id, 0) + embedded.duration_ms
        embedded_tracks = tuple(
            TrackEmbedding(
                track_id=track_id,
                values=aggregate_embeddings(values),
                sample_count=len(values),
                effective_duration_ms=durations_by_track[track_id],
            )
            for track_id, values in sorted(vectors_by_track.items())
        )
        protected = {
            track.id
            for track in tracks
            if track.label_source
            in {
                SpeakerLabelSource.MANUAL.value,
                SpeakerLabelSource.VOICEPRINT_CONFIRMED.value,
                SpeakerLabelSource.VOICEPRINT_AUTO.value,
            }
        }
        protected.update(protected_track_ids or set())
        plan = plan_track_merges(embedded_tracks, protected_track_ids=protected, policy=self.policy)
        return SpeakerReclusterPlan(
            track_targets=plan.track_targets,
            proposals=plan.proposals,
            embedded_track_count=len(embedded_tracks),
            selected_sample_count=sum(len(values) for values in vectors_by_track.values()),
            skipped_sample_count=skipped,
            encoder_ref=encoder_ref,
            final_diarizer_ref=self.policy.final_diarizer_ref,
            policy_version=self.policy.version,
            validation_artifact_sha256=self.policy.validation_artifact_sha256,
            automatic_enabled=self.policy.auto_apply_validated,
            degraded_reason=None if len(embedded_tracks) >= 2 else "SPEAKER_RECLUSTER_EMBEDDINGS_INSUFFICIENT",
        )


def select_sample_windows(
    segments: Sequence[MeetingTranscriptSegment],
    *,
    policy: SpeakerReclusterPolicy,
) -> tuple[ReclusterSampleWindow, ...]:
    candidates: dict[str, list[MeetingTranscriptSegment]] = {}
    for segment in segments:
        duration = segment.end_ms - segment.start_ms
        if (
            not segment.speaker_track_id
            or duration < policy.min_segment_ms
            or segment.overlap
            or (segment.noise_level is not None and segment.noise_level > policy.max_noise_level)
            or (segment.asr_confidence is not None and segment.asr_confidence < policy.min_asr_confidence)
        ):
            continue
        candidates.setdefault(segment.speaker_track_id, []).append(segment)
    per_track: dict[str, list[ReclusterSampleWindow]] = {}
    for track_id in sorted(candidates):
        ordered = sorted(
            candidates[track_id],
            key=lambda value: (
                value.noise_level if value.noise_level is not None else policy.max_noise_level,
                -(value.asr_confidence if value.asr_confidence is not None else policy.min_asr_confidence),
                -(value.end_ms - value.start_ms),
                value.start_ms,
                value.id,
            ),
        )
        windows: list[ReclusterSampleWindow] = []
        for segment in ordered[: policy.max_samples_per_track]:
            duration = segment.end_ms - segment.start_ms
            if duration > policy.max_segment_ms:
                inset = (duration - policy.max_segment_ms) // 2
                start_ms = segment.start_ms + inset
                end_ms = start_ms + policy.max_segment_ms
            else:
                start_ms = segment.start_ms
                end_ms = segment.end_ms
            windows.append(ReclusterSampleWindow(segment.id, track_id, start_ms, end_ms))
        per_track[track_id] = windows

    # Round-robin across tracks so a global cap cannot starve later tracks.
    selected: list[ReclusterSampleWindow] = []
    for sample_index in range(policy.max_samples_per_track):
        for track_id in sorted(per_track):
            windows = per_track[track_id]
            if sample_index >= len(windows):
                continue
            selected.append(windows[sample_index])
            if len(selected) >= policy.max_total_samples:
                return tuple(selected)
    return tuple(selected)


def _chunks_covering_window(
    chunks: Sequence[MeetingAudioChunk],
    starts: Sequence[int],
    start_ms: int,
    end_ms: int,
) -> list[MeetingAudioChunk]:
    """Select only nearby manifest rows instead of rescanning a four-hour meeting."""

    index = bisect_left(starts, start_ms)
    while index > 0 and chunks[index - 1].start_ms + chunks[index - 1].duration_ms > start_ms:
        index -= 1
    end_index = index
    while end_index < len(chunks) and chunks[end_index].start_ms < end_ms:
        end_index += 1
    return list(chunks[index:end_index])


def plan_track_merges(
    embeddings: Sequence[TrackEmbedding],
    *,
    protected_track_ids: set[str],
    policy: SpeakerReclusterPolicy,
) -> SpeakerReclusterPlan:
    """Build complete-link components, then apply a separation margin.

    The margin is deliberately evaluated after component construction.  Applying
    it to every pair before merging makes three fragments of the same speaker
    suppress one another (each fragment becomes the other's ``top2`` rival).
    Complete-link construction keeps all within-component pairs above the
    configured threshold; the final margin only compares that component with
    tracks outside it.
    """
    by_id = {value.track_id: value for value in embeddings}
    if len(by_id) < 2:
        return SpeakerReclusterPlan(
            policy_version=policy.version,
            final_diarizer_ref=policy.final_diarizer_ref,
            validation_artifact_sha256=policy.validation_artifact_sha256,
            automatic_enabled=policy.auto_apply_validated,
        )
    similarities: dict[tuple[str, str], float] = {}
    candidates: list[tuple[float, str, str]] = []
    ids = sorted(by_id)
    for index, left_id in enumerate(ids):
        for right_id in ids[index + 1 :]:
            score = cosine_similarity(by_id[left_id].values, by_id[right_id].values)
            similarities[(left_id, right_id)] = score
            if score >= policy.review_min_score:
                candidates.append((score, left_id, right_id))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    components: dict[str, set[str]] = {track_id: {track_id} for track_id in ids}
    owner: dict[str, str] = {track_id: track_id for track_id in ids}
    rejected: list[SpeakerMergeProposal] = []
    for _score, left_id, right_id in candidates:
        left_root = owner[left_id]
        right_root = owner[right_id]
        if left_root == right_root:
            continue
        left_members = components[left_root]
        right_members = components[right_root]
        protected = (left_members | right_members) & protected_track_ids
        component_score = min(
            similarities[_pair(left, right)]
            for left in left_members
            for right in right_members
        )
        support_ok = all(by_id[track_id].sample_count >= 2 for track_id in left_members | right_members)
        threshold = policy.merge_min_score if support_ok else policy.singleton_merge_min_score
        protected_conflict = len(protected) > 1
        if protected_conflict or component_score < threshold:
            combined = left_members | right_members
            target = _choose_target(combined, by_id, protected)
            rejected.append(
                SpeakerMergeProposal(
                    source_track_ids=tuple(sorted(combined - {target})),
                    target_track_id=target,
                    score=round(component_score, 6),
                    auto_apply=False,
                    reason_code=(
                        "PROTECTED_TRACK_CONFLICT"
                        if protected_conflict
                        else "SCORE_BELOW_AUTO_THRESHOLD"
                    ),
                )
            )
            continue
        root = min(left_root, right_root)
        other = right_root if root == left_root else left_root
        components[root] = left_members | right_members
        components.pop(other, None)
        for track_id in left_members | right_members:
            owner[track_id] = root

    proposals: list[SpeakerMergeProposal] = []
    targets: dict[str, str] = {}
    for members in components.values():
        if len(members) < 2:
            continue
        protected = members & protected_track_ids
        target = _choose_target(members, by_id, protected)
        within_score = min(
            similarities[_pair(left, right)]
            for left in members
            for right in members
            if left < right
        )
        outside_scores = [
            similarities[_pair(member, outside)]
            for member in members
            for outside in by_id
            if outside not in members
        ]
        separation = within_score - max(outside_scores) if outside_scores else 1.0
        margin_ok = separation >= policy.min_top2_margin
        support_ok = all(by_id[track_id].sample_count >= 2 for track_id in members)
        threshold = policy.merge_min_score if support_ok else policy.singleton_merge_min_score
        protected_conflict = len(protected) > 1
        protected_identity_attribution = bool(protected) and len(members) > 1
        auto_apply = (
            policy.auto_apply_validated
            and within_score >= threshold
            and margin_ok
            and not protected_conflict
            and not protected_identity_attribution
        )
        reason = (
            "PROTECTED_TRACK_CONFLICT"
            if protected_conflict
            else "PROTECTED_IDENTITY_REVIEW_REQUIRED"
            if protected_identity_attribution
            else "LOW_TOP2_MARGIN"
            if not margin_ok
            else "SCORE_BELOW_AUTO_THRESHOLD"
            if within_score < threshold
            else "POLICY_NOT_VALIDATED"
            if not policy.auto_apply_validated
            else "AUTO_MERGE"
        )
        proposal = SpeakerMergeProposal(
            source_track_ids=tuple(sorted(members - {target})),
            target_track_id=target,
            score=round(within_score, 6),
            auto_apply=auto_apply,
            reason_code=reason,
        )
        proposals.append(proposal)
        if auto_apply:
            for track_id in members:
                if track_id != target:
                    targets[track_id] = target
    proposals.extend(rejected)
    return SpeakerReclusterPlan(
        track_targets=targets,
        proposals=tuple(proposals),
        embedded_track_count=len(by_id),
        final_diarizer_ref=policy.final_diarizer_ref,
        policy_version=policy.version,
        validation_artifact_sha256=policy.validation_artifact_sha256,
        automatic_enabled=policy.auto_apply_validated,
    )


def aggregate_embeddings(values: Sequence[Sequence[float]]) -> tuple[float, ...]:
    if not values:
        raise ValueError("at least one embedding is required")
    normalized = [l2_normalize(value) for value in values]
    dimension = len(normalized[0])
    if any(len(value) != dimension for value in normalized):
        raise ValueError("embedding dimensions differ")
    mean = tuple(math.fsum(value[index] for value in normalized) / len(normalized) for index in range(dimension))
    return l2_normalize(mean)


def l2_normalize(values: Sequence[float]) -> tuple[float, ...]:
    vector = tuple(float(value) for value in values)
    if not vector or any(not math.isfinite(value) for value in vector):
        raise ValueError("embedding contains invalid values")
    norm = math.sqrt(math.fsum(value * value for value in vector))
    if not math.isfinite(norm) or norm <= 1e-12:
        raise ValueError("embedding norm is zero")
    return tuple(value / norm for value in vector)


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    normalized_left = l2_normalize(left)
    normalized_right = l2_normalize(right)
    if len(normalized_left) != len(normalized_right):
        raise ValueError("embedding dimensions differ")
    return math.fsum(a * b for a, b in zip(normalized_left, normalized_right, strict=True))


def _pcm_quality_ok(pcm: bytes, policy: SpeakerReclusterPolicy) -> bool:
    if not pcm or len(pcm) % 2:
        return False
    values = array("h")
    values.frombytes(pcm)
    if not values:
        return False
    square_sum = math.fsum(float(value) * float(value) for value in values)
    rms = math.sqrt(square_sum / len(values)) / 32768.0
    clipped = sum(1 for value in values if abs(value) >= 32700) / len(values)
    return rms >= policy.min_rms and clipped <= policy.max_clipping_ratio


def _pair(left: str, right: str) -> tuple[str, str]:
    return (left, right) if left < right else (right, left)


def _top2_margin(scores: Sequence[float]) -> float:
    if not scores:
        return 0.0
    ordered = sorted(scores, reverse=True)
    return ordered[0] - ordered[1] if len(ordered) > 1 else 1.0


def _choose_target(
    members: set[str],
    embeddings: dict[str, TrackEmbedding],
    protected: set[str],
) -> str:
    candidates = protected or members
    return sorted(
        candidates,
        key=lambda track_id: (
            -embeddings[track_id].effective_duration_ms,
            -embeddings[track_id].sample_count,
            track_id,
        ),
    )[0]


def _derived_embedding_url(finalization_url: str) -> str | None:
    if not finalization_url:
        asr_ws = os.getenv("SIQ_MEETING_ASR_WS_URL", "").strip()
        parsed_ws = urlsplit(asr_ws)
        if parsed_ws.scheme not in {"ws", "wss"} or not parsed_ws.hostname:
            return None
        scheme = "https" if parsed_ws.scheme == "wss" else "http"
        return urlunsplit((scheme, parsed_ws.netloc, "/v1/speaker/embedding", "", ""))
    parsed = urlsplit(finalization_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, "/v1/speaker/embedding", "", ""))


async def _read_bounded_response(response: httpx.Response, maximum: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > maximum:
            raise MeetingSpeakerReclusterError(
                "SPEAKER_RECLUSTER_ENCODER_RESPONSE_TOO_LARGE",
                "diarization embedding response exceeded its limit",
            )
        chunks.append(chunk)
    return b"".join(chunks)


__all__ = [
    "DiarizationEmbedding",
    "DiarizationEmbeddingClient",
    "HttpDiarizationEmbeddingClient",
    "MeetingSpeakerReclusterError",
    "MeetingSpeakerReclusterService",
    "ReclusterSampleWindow",
    "SpeakerMergeProposal",
    "SpeakerReclusterPlan",
    "SpeakerReclusterPolicy",
    "TrackEmbedding",
    "aggregate_embeddings",
    "cosine_similarity",
    "plan_track_merges",
    "select_sample_windows",
]
