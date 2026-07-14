"""Low-cardinality process and database metrics for the meeting domain."""

from __future__ import annotations

import re
import shutil
import threading
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import inspect
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from services.meeting_config import MeetingSettings
from services.meeting_contracts import (
    MeetingArtifact,
    MeetingASRCorrectionEvent,
    MeetingEvent,
    MeetingJob,
    MeetingJobState,
    MeetingLexiconEntry,
    MeetingModelSnapshot,
    MeetingStreamLease,
    MeetingTermCandidate,
    MeetingVoiceprintMatch,
    utcnow,
)
from services.meeting_native_capture_config import MeetingNativeCaptureSettings
from services.meeting_native_capture_contracts import (
    MeetingNativeCaptureBatch,
    MeetingNativeCaptureFinalization,
    MeetingNativeCaptureGap,
    NativeCaptureFinalizationState,
    NativeCaptureGapReason,
)

_LOCK = threading.Lock()
_ACTIVE_STREAMS = 0
_COUNTERS: Counter[tuple[str, str]] = Counter()
_HISTOGRAM_BUCKETS = (
    0.05,
    0.1,
    0.2,
    0.3,
    0.5,
    1.0,
    1.2,
    2.5,
    5.0,
    10.0,
    15.0,
    30.0,
    60.0,
    120.0,
    300.0,
)
_SUMMARIES: dict[str, dict[str, Any]] = defaultdict(
    lambda: {
        "count": 0.0,
        "sum": 0.0,
        "max": 0.0,
        "buckets": Counter(),
    }
)
_LABEL_RE = re.compile(r"[^a-zA-Z0-9_.:-]+")

# Metric result labels are part of the public monitoring contract. Keeping an
# explicit allowlist prevents error messages, identifiers, paths, or tokens
# from becoming unbounded Prometheus labels.
_COUNTER_LABEL_VALUES: dict[str, frozenset[str]] = {
    "audio_frame": frozenset({"persisted", "persist_failed"}),
    "stable_segment": frozenset({"persisted", "duplicate"}),
    "audio_gap": frozenset(
        {
            "sequence_gap",
            "timestamp_gap",
            "provider_gap",
            "transport_gap",
            "declared_unrecoverable",
        }
    ),
    "audio_storage_failure": frozenset(
        {
            "audio_storage_write_failed",
            "audio_storage_read_failed",
            "audio_storage_unavailable",
            "audio_integrity_failed",
            "audio_sequence_conflict",
            "audio_chunk_truncated",
        }
    ),
    "ws_reconnect": frozenset(
        {
            "speech_stream_closed",
            "browser_transport",
            "speech_transport",
            "asr_unavailable",
            "browser_disconnect",
        }
    ),
    "data_boundary_violation": frozenset({"cloud_without_consent", "locality_mismatch", "provider_mismatch"}),
    "model_isolation_violation": frozenset({"snapshot_mismatch", "unpinned_model", "provider_mismatch"}),
    "voiceprint_policy_violation": frozenset(
        {"consent_missing", "consent_revoked", "key_unavailable", "retention_failed"}
    ),
    "caption_blocked_by_postprocess": frozenset({"ai_dependency", "database_dependency", "queue_dependency"}),
    "native_capture_auth_failure": frozenset(
        {"token_invalid", "token_expired", "token_revoked", "device_mismatch", "scope_denied"}
    ),
    "native_capture_batch": frozenset(
        {"accepted", "replayed", "conflict", "rejected_capacity", "rejected_storage", "invalid"}
    ),
    "native_capture_batch_bytes": frozenset({"accepted", "replayed"}),
    "native_capture_storage_rejection": frozenset({"unavailable", "low_space", "quota", "capacity", "integrity"}),
    "final_asr_window": frozenset({"succeeded", "retryable_failure", "permanent_failure"}),
    "speaker_recluster_run": frozenset({"succeeded", "degraded", "retry_wait", "failed"}),
    "speaker_recluster_decision": frozenset(
        {"auto_merge", "auto_split", "review_proposal", "protected_skip", "unchanged"}
    ),
}


def _label(value: Any) -> str:
    return _LABEL_RE.sub("_", str(value or "unknown").strip())[:64] or "unknown"


def _counter_label(metric: str, value: Any) -> str | None:
    allowed = _COUNTER_LABEL_VALUES.get(metric)
    if allowed is None:
        return None
    candidate = _label(value).lower()
    return candidate if candidate in allowed else "other"


def meeting_stream_opened() -> None:
    global _ACTIVE_STREAMS
    with _LOCK:
        _ACTIVE_STREAMS += 1


def meeting_stream_closed() -> None:
    global _ACTIVE_STREAMS
    with _LOCK:
        _ACTIVE_STREAMS = max(0, _ACTIVE_STREAMS - 1)


def record_meeting_counter(metric: str, result: str, amount: int = 1) -> None:
    if amount <= 0:
        return
    metric_name = str(metric or "").strip()
    result_label = _counter_label(metric_name, result)
    if result_label is None:
        return
    with _LOCK:
        _COUNTERS[(metric_name, result_label)] += amount


def observe_meeting_latency(metric: str, seconds: float | int) -> None:
    value = max(0.0, float(seconds or 0))
    with _LOCK:
        bucket = _SUMMARIES[_label(metric)]
        bucket["count"] += 1
        bucket["sum"] += value
        bucket["max"] = max(bucket["max"], value)
        for upper_bound in _HISTOGRAM_BUCKETS:
            if value <= upper_bound:
                bucket["buckets"][upper_bound] += 1


def render_meeting_process_metrics() -> str:
    with _LOCK:
        active = _ACTIVE_STREAMS
        counters = dict(_COUNTERS)
        summaries = {name: {**value, "buckets": dict(value["buckets"])} for name, value in _SUMMARIES.items()}
    lines = [
        "# HELP meeting_active_sessions Active meeting stream connections in this process.",
        "# TYPE meeting_active_sessions gauge",
        f"meeting_active_sessions {active}",
    ]
    counter_contract = {
        "audio_frame": ("meeting_audio_frame_total", "result"),
        "stable_segment": ("meeting_stable_segment_total", "result"),
        "audio_gap": ("meeting_audio_gap_total", "reason"),
        "audio_storage_failure": ("meeting_audio_storage_failure_total", "reason"),
        "ws_reconnect": ("meeting_ws_reconnect_total", "reason"),
        "data_boundary_violation": ("meeting_data_boundary_violation_total", "kind"),
        "model_isolation_violation": ("meeting_model_isolation_violation_total", "kind"),
        "voiceprint_policy_violation": ("meeting_voiceprint_policy_violation_total", "kind"),
        "caption_blocked_by_postprocess": (
            "meeting_caption_blocked_by_postprocess_total",
            "reason",
        ),
        "native_capture_auth_failure": (
            "meeting_native_capture_auth_failure_total",
            "reason",
        ),
        "native_capture_batch": ("meeting_native_capture_batch_total", "result"),
        "native_capture_batch_bytes": (
            "meeting_native_capture_batch_bytes_total",
            "result",
        ),
        "native_capture_storage_rejection": (
            "meeting_native_capture_storage_rejection_total",
            "reason",
        ),
        "final_asr_window": ("meeting_final_asr_window_total", "result"),
        "speaker_recluster_run": ("meeting_speaker_recluster_total", "result"),
        "speaker_recluster_decision": (
            "meeting_speaker_recluster_decision_total",
            "result",
        ),
    }
    for internal, (public, label_name) in counter_contract.items():
        lines.extend([f"# TYPE {public} counter"])
        values = [(result, count) for (metric, result), count in sorted(counters.items()) if metric == internal]
        if not values and internal in {
            "caption_blocked_by_postprocess",
            "data_boundary_violation",
            "model_isolation_violation",
            "voiceprint_policy_violation",
        }:
            values = [("none", 0)]
        for result, count in values:
            lines.append(f'{public}{{{label_name}="{result}"}} {count}')

    native_settings = MeetingNativeCaptureSettings.from_env()
    native_operational = native_settings.operational and MeetingSettings.from_env().operational
    storage_free_bytes = 0
    storage_probe_success = 0
    storage_required_free_bytes = max(5 * 1024**3, 2 * native_settings.max_total_bytes)
    if native_operational:
        try:
            storage_root = native_settings.root.expanduser()
            probe_root = storage_root if storage_root.exists() else storage_root.parent
            storage_free_bytes = int(shutil.disk_usage(probe_root).free)
            storage_probe_success = 1
        except OSError:
            pass
    lines.extend(
        [
            "# TYPE meeting_native_capture_operational gauge",
            f"meeting_native_capture_operational {int(native_operational)}",
            "# TYPE meeting_native_capture_storage_probe_success gauge",
            f"meeting_native_capture_storage_probe_success {storage_probe_success}",
            "# TYPE meeting_native_capture_storage_free_bytes gauge",
            f"meeting_native_capture_storage_free_bytes {storage_free_bytes}",
            "# TYPE meeting_native_capture_storage_required_free_bytes gauge",
            f"meeting_native_capture_storage_required_free_bytes {storage_required_free_bytes}",
        ]
    )
    summary_contract = {
        "asr_partial_latency_seconds": "meeting_asr_partial_latency_seconds",
        "asr_stable_latency_seconds": "meeting_asr_stable_latency_seconds",
        "segment_persist_latency_seconds": "meeting_segment_persist_latency_seconds",
        "event_publish_latency_seconds": "meeting_event_publish_latency_seconds",
        "final_asr_window_processing_seconds": "meeting_final_asr_window_processing_seconds",
        "final_asr_job_processing_seconds": "meeting_final_asr_job_processing_seconds",
    }
    for internal, public in summary_contract.items():
        value = summaries.get(
            internal,
            {"count": 0.0, "sum": 0.0, "max": 0.0, "buckets": {}},
        )
        bucket_lines = [
            f'{public}_bucket{{le="{upper_bound:g}"}} {int(value["buckets"].get(upper_bound, 0))}'
            for upper_bound in _HISTOGRAM_BUCKETS
        ]
        lines.extend(
            [
                f"# TYPE {public} histogram",
                *bucket_lines,
                f'{public}_bucket{{le="+Inf"}} {int(value["count"])}',
                f"{public}_sum {value['sum']:.9f}",
                f"{public}_count {int(value['count'])}",
                f"{public}_max {value['max']:.9f}",
            ]
        )
    return "\n".join(lines) + "\n"


async def render_meeting_database_metrics(session: AsyncSession) -> str:
    """Render aggregate database state without meeting/user/model identifiers."""

    now = utcnow()
    lines: list[str] = []
    active = (
        await session.exec(
            select(func.count(func.distinct(MeetingStreamLease.meeting_id))).where(MeetingStreamLease.lease_until > now)
        )
    ).one()
    lines.extend(
        [
            "# TYPE meeting_active_leases gauge",
            f"meeting_active_leases {int(active or 0)}",
        ]
    )

    recluster_states = (
        await session.exec(
            select(MeetingJob.state, func.count(MeetingJob.id))
            .where(
                MeetingJob.job_kind == "speaker_recluster",
                MeetingJob.state.in_(
                    {
                        MeetingJobState.SUCCEEDED.value,
                        MeetingJobState.RETRY_WAIT.value,
                        MeetingJobState.FAILED.value,
                    }
                ),
            )
            .group_by(MeetingJob.state)
        )
    ).all()
    recluster_state_counts = Counter({str(state): int(count) for state, count in recluster_states})
    recluster_event_types = {
        "speaker.recluster.degraded": "degraded",
        "speaker.recluster.auto_merge": "auto_merge",
        "speaker.recluster.auto_split": "auto_split",
        "speaker.recluster.review_proposal": "review_proposal",
        "speaker.recluster.protected_skip": "protected_skip",
        "speaker.recluster.unchanged": "unchanged",
    }
    recluster_event_rows = (
        await session.exec(
            select(MeetingEvent.event_type, func.count(MeetingEvent.id))
            .where(MeetingEvent.event_type.in_(set(recluster_event_types)))
            .group_by(MeetingEvent.event_type)
        )
    ).all()
    recluster_event_counts = Counter(
        {recluster_event_types[str(event_type)]: int(count) for event_type, count in recluster_event_rows}
    )
    degraded_count = recluster_event_counts["degraded"]
    clean_success_count = max(
        0,
        recluster_state_counts[MeetingJobState.SUCCEEDED.value] - degraded_count,
    )
    lines.append("# TYPE meeting_speaker_recluster_durable_total gauge")
    for result, count in (
        ("succeeded", clean_success_count),
        ("degraded", degraded_count),
        ("retry_wait", recluster_state_counts[MeetingJobState.RETRY_WAIT.value]),
        ("failed", recluster_state_counts[MeetingJobState.FAILED.value]),
    ):
        lines.append(f'meeting_speaker_recluster_durable_total{{result="{result}"}} {count}')
    lines.append("# TYPE meeting_speaker_recluster_decision_durable_total gauge")
    for result in ("auto_merge", "auto_split", "review_proposal", "protected_skip", "unchanged"):
        lines.append(
            f'meeting_speaker_recluster_decision_durable_total{{result="{result}"}} '
            f"{recluster_event_counts[result]}"
        )

    queued_states = {
        MeetingJobState.QUEUED.value,
        MeetingJobState.RETRY_WAIT.value,
        MeetingJobState.LEASED.value,
        MeetingJobState.RUNNING.value,
    }
    queue_rows = (
        await session.exec(
            select(
                MeetingJob.job_kind,
                func.count(MeetingJob.id),
                func.min(MeetingJob.created_at),
            )
            .where(MeetingJob.state.in_(queued_states))
            .group_by(MeetingJob.job_kind)
        )
    ).all()
    lines.extend(
        [
            "# TYPE meeting_postprocess_queue_depth gauge",
            "# TYPE meeting_postprocess_oldest_age_seconds gauge",
        ]
    )
    for kind, count, oldest in queue_rows:
        kind_label = _label(kind)
        age = max(0.0, (now - oldest).total_seconds()) if oldest else 0.0
        lines.append(f'meeting_postprocess_queue_depth{{kind="{kind_label}"}} {int(count)}')
        lines.append(f'meeting_postprocess_oldest_age_seconds{{kind="{kind_label}"}} {age:.3f}')

    job_rows = (
        await session.exec(
            select(
                MeetingJob.job_kind,
                MeetingJob.state,
                MeetingModelSnapshot.provider_locality,
                func.count(MeetingJob.id),
            )
            .select_from(MeetingJob)
            .join(
                MeetingModelSnapshot,
                MeetingJob.model_snapshot_id == MeetingModelSnapshot.id,
                isouter=True,
            )
            .group_by(
                MeetingJob.job_kind,
                MeetingJob.state,
                MeetingModelSnapshot.provider_locality,
            )
        )
    ).all()
    lines.append("# TYPE meeting_ai_job_total gauge")
    for kind, state, locality, count in job_rows:
        lines.append(
            'meeting_ai_job_total{kind="%s",status="%s",locality="%s"} %d'
            % (_label(kind), _label(state), _label(locality or "none"), int(count))
        )

    await _append_grouped_count(
        session,
        lines,
        "meeting_voice_match_total",
        "decision",
        select(MeetingVoiceprintMatch.decision, func.count(MeetingVoiceprintMatch.id)).group_by(
            MeetingVoiceprintMatch.decision
        ),
    )
    await _append_grouped_count(
        session,
        lines,
        "meeting_term_candidate_total",
        "status",
        select(MeetingTermCandidate.status, func.count(MeetingTermCandidate.id)).group_by(MeetingTermCandidate.status),
    )
    await _append_grouped_count(
        session,
        lines,
        "meeting_lexicon_entry_total",
        "status",
        select(MeetingLexiconEntry.status, func.count(MeetingLexiconEntry.id)).group_by(MeetingLexiconEntry.status),
    )

    correction_rows = (
        await session.exec(
            select(
                MeetingASRCorrectionEvent.edit_intent,
                MeetingASRCorrectionEvent.error_class,
                MeetingASRCorrectionEvent.status,
                func.count(MeetingASRCorrectionEvent.id),
            ).group_by(
                MeetingASRCorrectionEvent.edit_intent,
                MeetingASRCorrectionEvent.error_class,
                MeetingASRCorrectionEvent.status,
            )
        )
    ).all()
    lines.append("# TYPE meeting_asr_correction_total gauge")
    for intent, error_class, status, count in correction_rows:
        lines.append(
            'meeting_asr_correction_total{intent="%s",error_class="%s",status="%s"} %d'
            % (_label(intent), _label(error_class), _label(status), int(count))
        )

    model_rows = (
        await session.exec(
            select(
                MeetingModelSnapshot.selection_mode,
                MeetingModelSnapshot.provider_locality,
                func.count(MeetingModelSnapshot.id),
            ).group_by(
                MeetingModelSnapshot.selection_mode,
                MeetingModelSnapshot.provider_locality,
            )
        )
    ).all()
    lines.append("# TYPE meeting_model_resolution_total gauge")
    for mode, locality, count in model_rows:
        lines.append(
            'meeting_model_resolution_total{mode="%s",locality="%s",result="resolved"} %d'
            % (_label(mode), _label(locality), int(count))
        )

    latest_summary = (
        await session.exec(
            select(func.max(MeetingArtifact.updated_at)).where(
                MeetingArtifact.artifact_type.in_({"rolling_minutes", "final_minutes"}),
                MeetingArtifact.state == "ready",
            )
        )
    ).one()
    freshness = max(0.0, (now - latest_summary).total_seconds()) if latest_summary else 0.0
    lines.extend(
        [
            "# TYPE meeting_summary_freshness_seconds gauge",
            f"meeting_summary_freshness_seconds {freshness:.3f}",
        ]
    )

    event_rows = (
        await session.exec(
            select(MeetingEvent.event_type, func.count(MeetingEvent.id))
            .where(MeetingEvent.event_type.in_({"audio.gap.detected", "pipeline.reconnecting"}))
            .group_by(MeetingEvent.event_type)
        )
    ).all()
    event_counts = {event_type: int(count) for event_type, count in event_rows}
    lines.extend(
        [
            "# TYPE meeting_audio_gap_durable_total gauge",
            f'meeting_audio_gap_durable_total{{reason="recorded"}} {event_counts.get("audio.gap.detected", 0)}',
            "# TYPE meeting_ws_reconnect_durable_total gauge",
            f'meeting_ws_reconnect_durable_total{{reason="pipeline"}} {event_counts.get("pipeline.reconnecting", 0)}',
        ]
    )
    await _append_native_capture_database_metrics(session, lines, now=now)
    return "\n".join(lines) + "\n"


async def _append_native_capture_database_metrics(
    session: AsyncSession,
    lines: list[str],
    *,
    now: datetime,
) -> None:
    if await _table_exists(session, MeetingNativeCaptureBatch.__tablename__):
        batch_count, batch_bytes = (
            await session.exec(
                select(
                    func.count(MeetingNativeCaptureBatch.id),
                    func.coalesce(func.sum(MeetingNativeCaptureBatch.byte_size), 0),
                )
            )
        ).one()
        lines.extend(
            [
                "# TYPE meeting_native_capture_durable_batch_count gauge",
                f"meeting_native_capture_durable_batch_count {int(batch_count or 0)}",
                "# TYPE meeting_native_capture_durable_batch_bytes gauge",
                f"meeting_native_capture_durable_batch_bytes {int(batch_bytes or 0)}",
            ]
        )

    if await _table_exists(session, MeetingNativeCaptureGap.__tablename__):
        rows = (
            await session.exec(
                select(MeetingNativeCaptureGap.reason, func.count(MeetingNativeCaptureGap.id)).group_by(
                    MeetingNativeCaptureGap.reason
                )
            )
        ).all()
        allowed_reasons = {value.value for value in NativeCaptureGapReason}
        counts: Counter[str] = Counter()
        for reason, count in rows:
            label = str(reason) if str(reason) in allowed_reasons else "other"
            counts[label] += int(count)
        lines.append("# TYPE meeting_native_capture_gap_durable_total gauge")
        for reason in sorted((*allowed_reasons, "other")):
            lines.append(f'meeting_native_capture_gap_durable_total{{reason="{reason}"}} {counts[reason]}')

    if not await _table_exists(session, MeetingNativeCaptureFinalization.__tablename__):
        return
    rows = (
        await session.exec(
            select(
                MeetingNativeCaptureFinalization.state,
                func.count(MeetingNativeCaptureFinalization.id),
                func.min(MeetingNativeCaptureFinalization.updated_at),
            ).group_by(MeetingNativeCaptureFinalization.state)
        )
    ).all()
    allowed_states = {value.value for value in NativeCaptureFinalizationState}
    state_counts: Counter[str] = Counter()
    oldest_by_state: dict[str, datetime] = {}
    for state, count, oldest in rows:
        label = str(state) if str(state) in allowed_states else "other"
        state_counts[label] += int(count)
        if oldest is not None:
            previous = oldest_by_state.get(label)
            oldest_by_state[label] = oldest if previous is None or oldest < previous else previous

    backlog_states = (
        NativeCaptureFinalizationState.PENDING_UPLOAD.value,
        NativeCaptureFinalizationState.QUEUED.value,
        NativeCaptureFinalizationState.PROCESSING.value,
        NativeCaptureFinalizationState.RETRY_WAIT.value,
        "other",
    )
    lines.extend(
        [
            "# TYPE meeting_native_capture_pending_upload gauge",
            "meeting_native_capture_pending_upload "
            f"{state_counts[NativeCaptureFinalizationState.PENDING_UPLOAD.value]}",
            "# TYPE meeting_native_capture_finalization_failed gauge",
            f"meeting_native_capture_finalization_failed {state_counts[NativeCaptureFinalizationState.FAILED.value]}",
            "# TYPE meeting_native_capture_finalization_backlog gauge",
            "# TYPE meeting_native_capture_finalization_oldest_age_seconds gauge",
        ]
    )
    for state in backlog_states:
        oldest = oldest_by_state.get(state)
        age = max(0.0, (now - oldest).total_seconds()) if oldest is not None else 0.0
        lines.append(f'meeting_native_capture_finalization_backlog{{state="{state}"}} {state_counts[state]}')
        lines.append(f'meeting_native_capture_finalization_oldest_age_seconds{{state="{state}"}} {age:.3f}')


async def _table_exists(session: AsyncSession, table_name: str) -> bool:
    connection = await session.connection()
    return bool(await connection.run_sync(lambda sync_connection: inspect(sync_connection).has_table(table_name)))


async def _append_grouped_count(
    session: AsyncSession,
    lines: list[str],
    metric: str,
    label_name: str,
    statement: Any,
) -> None:
    rows = (await session.exec(statement)).all()
    lines.append(f"# TYPE {metric} gauge")
    for value, count in rows:
        lines.append(f'{metric}{{{label_name}="{_label(value)}"}} {int(count)}')
