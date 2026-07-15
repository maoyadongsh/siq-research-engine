"""Durable, isolated AI post-processing for meeting transcripts.

The worker deliberately lives outside the API process.  It consumes only
stable transcript text, resolves an immutable Hermes target snapshot once per
job, and writes results together with the job terminal state.  It never
changes a Hermes profile or the meeting capture/subtitle state.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncContextManager, Callable, Iterable
from uuid import uuid4

from sqlalchemy import and_, case, func, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from services.meeting_contracts import (
    ArtifactState,
    ArtifactType,
    AudioSource,
    MeetingArtifact,
    MeetingAudioChunk,
    MeetingEvent,
    MeetingJob,
    MeetingJobKind,
    MeetingJobState,
    MeetingLexiconVersion,
    MeetingModelSetting,
    MeetingModelSnapshot,
    MeetingPostprocessState,
    MeetingSegmentRevision,
    MeetingSession,
    MeetingSpeakerTrack,
    MeetingState,
    MeetingTranscriptSegment,
    ModelFallbackPolicy,
    ModelSelectionMode,
    SegmentRevisionType,
    SpeakerLabelSource,
    utcnow,
)
from services.meeting_correction_feedback import calculate_diff
from services.meeting_event_store import MeetingEventStore, decode_json, encode_json
from services.meeting_finalization import (
    FINAL_ALIGNMENT_SCHEMA,
    FinalizationAnalysis,
    MeetingFinalizationError,
    MeetingFinalizationService,
    align_final_segments,
)
from services.meeting_hermes_runner import (
    CORRECTION_SCHEMA_VERSION,
    FINAL_SCHEMA_VERSION,
    ROLLING_SCHEMA_VERSION,
    MeetingAITask,
    MeetingHermesConfigurationError,
    MeetingHermesExecutionSnapshot,
    MeetingHermesOutputError,
    MeetingHermesProtocolError,
    MeetingHermesRunner,
    MeetingHermesRunResult,
    MeetingHermesTarget,
    MeetingHermesTargetUnavailable,
)
from services.meeting_metrics import record_meeting_counter
from services.meeting_speaker_recluster import (
    MeetingSpeakerReclusterError,
    MeetingSpeakerReclusterService,
    SpeakerReclusterPlan,
)

SessionFactory = Callable[[], AsyncContextManager[AsyncSession]]
logger = logging.getLogger(__name__)

PROMPT_VERSIONS = {
    MeetingJobKind.CORRECTION.value: "meeting.correction.v1",
    MeetingJobKind.ROLLING_MINUTES.value: "meeting.rolling-minutes.v1",
    MeetingJobKind.FINAL_MINUTES.value: "meeting.final-minutes.v1",
}
AI_JOB_KINDS = frozenset(PROMPT_VERSIONS)
MINUTES_JOB_KINDS = frozenset({MeetingJobKind.ROLLING_MINUTES.value, MeetingJobKind.FINAL_MINUTES.value})

_PRIORITY = {
    MeetingJobKind.FINAL_TRANSCRIPT.value: 10,
    MeetingJobKind.SPEAKER_RECLUSTER.value: 15,
    MeetingJobKind.FINAL_MINUTES.value: 20,
    MeetingJobKind.CORRECTION.value: 30,
    MeetingJobKind.ROLLING_MINUTES.value: 40,
}
_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_SECRET_RE = re.compile(r"(?i)(authorization|api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+")
_CRITICAL_PATTERNS = (
    re.compile(
        r"(?:[A-Z]{1,8}[-.]?)?\d{2,}(?:[.,]\d+)?"
        r"(?:%|％|万元|亿元|万|亿|元)?"
    ),
    re.compile(r"\d{1,4}[年/-]\d{1,2}(?:[月/-]\d{1,2}日?)?"),
    re.compile(r"(?:[$¥￥€£]|人民币|美元|欧元)\s*[\d,.]+(?:万|亿)?"),
    re.compile(r"[零〇一二三四五六七八九十百千万亿两]+(?:点[零〇一二三四五六七八九]+)?(?:%|％|元|万|亿)?"),
    re.compile(r"[A-Za-z0-9\u4e00-\u9fff]{2,40}(?:股份有限公司|有限责任公司|有限公司|集团|银行|证券|基金|法院)"),
)


class MeetingAIWorkerError(RuntimeError):
    public_code = "MEETING_AI_FAILED"
    retryable = False


class MeetingAILeaseLost(MeetingAIWorkerError):
    public_code = "MEETING_AI_LEASE_LOST"


class MeetingAIInputChanged(MeetingAIWorkerError):
    public_code = "MEETING_TRANSCRIPT_CHANGED"
    retryable = True


class MeetingAIOutputInvalid(MeetingAIWorkerError):
    public_code = "MEETING_AI_OUTPUT_INVALID"


class MeetingAIConfigurationInvalid(MeetingAIWorkerError):
    public_code = "MEETING_AI_CONFIGURATION_INVALID"


@dataclass(frozen=True)
class MeetingAIWorkerConfig:
    lease_seconds: int = 300
    retry_delay_seconds: int = 20
    poll_interval_seconds: float = 1.0
    correction_confidence: float = 0.85
    correction_debounce_seconds: int = 20
    rolling_debounce_seconds: int = 45
    correction_window_segments: int = 5
    import_correction_window_segments: int = 50
    rolling_min_new_segments: int = 3

    @classmethod
    def from_env(cls) -> "MeetingAIWorkerConfig":
        value = cls(
            lease_seconds=int(os.getenv("SIQ_MEETING_AI_LEASE_SECONDS", "300")),
            retry_delay_seconds=int(os.getenv("SIQ_MEETING_AI_RETRY_DELAY_SECONDS", "20")),
            poll_interval_seconds=float(os.getenv("SIQ_MEETING_AI_POLL_SECONDS", "1")),
            correction_confidence=float(os.getenv("SIQ_MEETING_AI_CORRECTION_CONFIDENCE", "0.85")),
            correction_debounce_seconds=int(os.getenv("SIQ_MEETING_AI_CORRECTION_DEBOUNCE_SECONDS", "20")),
            rolling_debounce_seconds=int(os.getenv("SIQ_MEETING_AI_ROLLING_DEBOUNCE_SECONDS", "45")),
            correction_window_segments=int(os.getenv("SIQ_MEETING_AI_CORRECTION_WINDOW_SEGMENTS", "5")),
            import_correction_window_segments=int(os.getenv("SIQ_MEETING_AI_IMPORT_CORRECTION_WINDOW_SEGMENTS", "50")),
            rolling_min_new_segments=int(os.getenv("SIQ_MEETING_AI_ROLLING_MIN_NEW_SEGMENTS", "3")),
        )
        if value.lease_seconds < 30:
            raise ValueError("SIQ_MEETING_AI_LEASE_SECONDS must be at least 30")
        if value.retry_delay_seconds < 0 or value.poll_interval_seconds <= 0:
            raise ValueError("meeting AI retry and poll settings are invalid")
        if not 0 <= value.correction_confidence <= 1:
            raise ValueError("meeting AI correction confidence must be between 0 and 1")
        if not 3 <= value.correction_window_segments <= 5:
            raise ValueError("meeting AI correction windows must contain 3 to 5 segments")
        if not 5 <= value.import_correction_window_segments <= 200:
            raise ValueError("meeting import AI correction windows must contain 5 to 200 segments")
        if (
            value.correction_debounce_seconds < 1
            or value.rolling_debounce_seconds < 1
            or value.rolling_min_new_segments < 1
        ):
            raise ValueError("meeting AI scheduling settings are invalid")
        return value


@dataclass(frozen=True)
class TranscriptInput:
    segments: list[dict[str, Any]]
    segment_ids: frozenset[str]
    revision_vector: dict[str, int]
    transcript_revision: int
    from_ordinal: int
    to_ordinal: int
    glossary: list[str]
    participants: list[str]
    language: str


def _eligible_jobs(now: Any) -> Any:
    return or_(
        and_(
            MeetingJob.state == MeetingJobState.QUEUED.value,
            MeetingJob.attempt < MeetingJob.max_attempts,
        ),
        and_(
            MeetingJob.state == MeetingJobState.RETRY_WAIT.value,
            MeetingJob.attempt < MeetingJob.max_attempts,
            or_(MeetingJob.lease_until.is_(None), MeetingJob.lease_until <= now),
        ),
        # A process can die during its last configured attempt. Expired active
        # leases remain recoverable because every meeting job is idempotent;
        # explicit failures still stop at max_attempts.
        and_(
            MeetingJob.state.in_([MeetingJobState.LEASED.value, MeetingJobState.RUNNING.value]),
            MeetingJob.lease_until.is_not(None),
            MeetingJob.lease_until <= now,
        ),
    )


def _priority_expression() -> Any:
    return case(_PRIORITY, value=MeetingJob.job_kind, else_=100)


def _diagnostic(exc: BaseException) -> str:
    value = f"{type(exc).__name__}: {exc}"
    value = _URL_RE.sub("[redacted-url]", value)
    value = _SECRET_RE.sub(r"\1=[redacted]", value)
    return value[:1000]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _critical_entities(value: str) -> tuple[str, ...]:
    entities: list[str] = []
    for pattern in _CRITICAL_PATTERNS:
        entities.extend(match.group(0) for match in pattern.finditer(value))
    return tuple(entities)


_MINUTES_EVIDENCE_FIELDS = (
    "agenda_topics",
    "chapters",
    "decisions",
    "open_questions",
    "risks",
    "action_items",
    "speaker_viewpoints",
    "keywords",
)


def _normalize_minutes_evidence(
    payload: dict[str, Any],
    segments: list[dict[str, Any]],
) -> dict[str, Any]:
    allowed_ids = {
        value
        for segment in segments
        if isinstance((value := segment.get("segment_id")), str) and value
    }
    ordinal_ids = {
        str(ordinal): segment_id
        for segment in segments
        if isinstance((ordinal := segment.get("ordinal")), int)
        and not isinstance(ordinal, bool)
        and isinstance((segment_id := segment.get("segment_id")), str)
        and segment_id
    }
    normalized = dict(payload)
    for field in _MINUTES_EVIDENCE_FIELDS:
        values = payload.get(field)
        if not isinstance(values, list):
            continue
        normalized_items: list[Any] = []
        for item in values:
            if not isinstance(item, dict):
                normalized_items.append(item)
                continue
            normalized_item = dict(item)
            source_ids = item.get("source_segment_ids")
            if isinstance(source_ids, list):
                canonical_ids: list[Any] = []
                for source_id in source_ids:
                    if not isinstance(source_id, str):
                        canonical_ids.append(source_id)
                        continue
                    stripped = source_id.strip()
                    canonical = stripped if stripped in allowed_ids else ordinal_ids.get(stripped, stripped)
                    if canonical not in canonical_ids:
                        canonical_ids.append(canonical)
                normalized_item["source_segment_ids"] = canonical_ids
            normalized_items.append(normalized_item)
        normalized[field] = normalized_items
    return normalized


def _evidence_is_valid(payload: dict[str, Any], allowed_ids: frozenset[str]) -> bool:
    for field in _MINUTES_EVIDENCE_FIELDS:
        values = payload.get(field, [])
        if not isinstance(values, list):
            return False
        for item in values:
            if not isinstance(item, dict):
                return False
            source_ids = item.get("source_segment_ids")
            if (
                not isinstance(source_ids, list)
                or not source_ids
                or any(not isinstance(source_id, str) for source_id in source_ids)
                or not set(source_ids).issubset(allowed_ids)
            ):
                return False
    return True


def _minutes_markdown(payload: dict[str, Any]) -> str:
    lines = ["# Meeting minutes", "", str(payload.get("overview") or "").strip()]
    sections = (
        ("Agenda", "agenda_topics"),
        ("Chapters", "chapters"),
        ("Decisions", "decisions"),
        ("Open questions", "open_questions"),
        ("Risks", "risks"),
        ("Action items", "action_items"),
        ("Speaker viewpoints", "speaker_viewpoints"),
        ("Keywords", "keywords"),
    )
    for title, key in sections:
        values = payload.get(key) or []
        if not values:
            continue
        lines.extend(["", f"## {title}", ""])
        for item in values:
            evidence = ", ".join(item.get("source_segment_ids") or [])
            lines.append(f"- {item.get('text', '')} [{evidence}]")
    return "\n".join(lines).strip() + "\n"


class MeetingAIWorker:
    """Claims and executes meeting AI jobs with database-backed leases."""

    def __init__(
        self,
        session_factory: SessionFactory,
        runner: MeetingHermesRunner,
        *,
        worker_id: str,
        config: MeetingAIWorkerConfig | None = None,
        finalization_service: MeetingFinalizationService | None = None,
        speaker_recluster_service: MeetingSpeakerReclusterService | None = None,
        job_kinds: Iterable[str] | None = None,
        audio_sources: Iterable[str] | None = None,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("meeting AI worker_id is required")
        self.session_factory = session_factory
        self.runner = runner
        self.worker_id = worker_id.strip()[:100]
        self.config = config or MeetingAIWorkerConfig.from_env()
        self.finalization_service = finalization_service or MeetingFinalizationService(session_factory)  # type: ignore[arg-type]
        self.speaker_recluster_service = speaker_recluster_service or MeetingSpeakerReclusterService.from_env()
        default_kinds = {
            MeetingJobKind.CORRECTION.value,
            MeetingJobKind.ROLLING_MINUTES.value,
            MeetingJobKind.FINAL_TRANSCRIPT.value,
            MeetingJobKind.SPEAKER_RECLUSTER.value,
            MeetingJobKind.FINAL_MINUTES.value,
        }
        selected_kinds = set(job_kinds) if job_kinds is not None else default_kinds
        if not selected_kinds or not selected_kinds <= default_kinds:
            raise ValueError("meeting AI worker job kinds are invalid")
        self.job_kinds = tuple(sorted(selected_kinds))
        self.audio_sources = tuple(sorted(set(audio_sources or ())))

    async def claim_next(self) -> MeetingJob | None:
        """Atomically claim one eligible job, including expired leases.

        The candidate selection is embedded in the UPDATE.  SQLite serializes
        the write and re-evaluates the eligibility predicate, so two worker
        processes cannot both receive the same lease.
        """

        now = utcnow()
        lease_until = now + timedelta(seconds=self.config.lease_seconds)
        eligible = _eligible_jobs(now)
        pending_voiceprint = aliased(MeetingJob)
        candidate_query = (
            select(MeetingJob.id)
            .join(MeetingSession, MeetingSession.id == MeetingJob.meeting_id)
            .where(eligible)
            .where(MeetingJob.job_kind.in_(self.job_kinds))
            .where(
                or_(
                    MeetingJob.job_kind != MeetingJobKind.FINAL_MINUTES.value,
                    MeetingSession.voiceprint_enabled.is_(False),
                    ~select(pending_voiceprint.id)
                    .where(
                        pending_voiceprint.meeting_id == MeetingJob.meeting_id,
                        pending_voiceprint.job_kind == MeetingJobKind.VOICEPRINT_MATCH.value,
                        pending_voiceprint.state.in_(
                            [
                                MeetingJobState.QUEUED.value,
                                MeetingJobState.LEASED.value,
                                MeetingJobState.RUNNING.value,
                                MeetingJobState.RETRY_WAIT.value,
                            ]
                        ),
                    )
                    .exists(),
                )
            )
        )
        if self.audio_sources:
            candidate_query = candidate_query.where(MeetingSession.audio_source.in_(self.audio_sources))
        candidate = (
            candidate_query.order_by(_priority_expression(), MeetingJob.created_at, MeetingJob.id)
            .limit(1)
            .scalar_subquery()
        )
        statement = (
            update(MeetingJob)
            .where(MeetingJob.id == candidate)
            .where(eligible)
            .values(
                state=MeetingJobState.LEASED.value,
                attempt=MeetingJob.attempt + 1,
                lease_owner=self.worker_id,
                lease_until=lease_until,
                public_error_code=None,
                internal_diagnostic=None,
                updated_at=now,
            )
            .returning(MeetingJob.id)
        )
        async with self.session_factory() as session:
            result = await session.exec(statement)
            job_id = result.scalar_one_or_none()
            await session.commit()
            if job_id is None:
                return None
            return (await session.exec(select(MeetingJob).where(MeetingJob.id == job_id))).one()

    async def renew_lease(self, job_id: str) -> bool:
        now = utcnow()
        statement = (
            update(MeetingJob)
            .where(
                MeetingJob.id == job_id,
                MeetingJob.lease_owner == self.worker_id,
                MeetingJob.state.in_([MeetingJobState.LEASED.value, MeetingJobState.RUNNING.value]),
            )
            .values(
                lease_until=now + timedelta(seconds=self.config.lease_seconds),
                updated_at=now,
            )
        )
        async with self.session_factory() as session:
            result = await session.exec(statement)
            await session.commit()
            return bool(result.rowcount)

    async def run_once(self) -> bool:
        job = await self.claim_next()
        if job is None:
            return False
        await self._process(job.id)
        return True

    async def run_forever(self, stop_event: asyncio.Event | None = None) -> None:
        stop = stop_event or asyncio.Event()
        while not stop.is_set():
            try:
                if {
                    MeetingJobKind.CORRECTION.value,
                    MeetingJobKind.ROLLING_MINUTES.value,
                }.intersection(self.job_kinds):
                    await self.schedule_incremental_jobs()
                worked = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("meeting AI loop iteration failed: %s", _diagnostic(exc))
                worked = False
            if worked:
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.config.poll_interval_seconds)
            except TimeoutError:
                pass

    async def schedule_incremental_jobs(self) -> int:
        """Create debounced correction and rolling jobs from stable watermarks."""

        now = utcnow()
        created = 0
        async with self.session_factory() as session:
            meetings = list(
                (
                    await session.exec(
                        select(MeetingSession)
                        .where(
                            MeetingSession.ai_enabled.is_(True),
                            MeetingSession.selection_mode != ModelSelectionMode.NONE.value,
                            MeetingSession.state.in_(
                                [
                                    MeetingState.LIVE.value,
                                    MeetingState.PAUSED.value,
                                    MeetingState.STOPPED.value,
                                ]
                            ),
                            MeetingSession.last_segment_ordinal > 0,
                        )
                        .order_by(MeetingSession.updated_at)
                        .limit(100)
                    )
                ).all()
            )
            event_store = MeetingEventStore(session)
            for meeting in meetings:
                pending_correction = (
                    await session.exec(
                        select(MeetingJob.id)
                        .where(
                            MeetingJob.meeting_id == meeting.id,
                            MeetingJob.job_kind == MeetingJobKind.CORRECTION.value,
                            MeetingJob.state.in_(
                                [
                                    MeetingJobState.QUEUED.value,
                                    MeetingJobState.LEASED.value,
                                    MeetingJobState.RUNNING.value,
                                    MeetingJobState.RETRY_WAIT.value,
                                ]
                            ),
                        )
                        .limit(1)
                    )
                ).first()
                correction_watermark = int(
                    (
                        await session.exec(
                            select(func.max(MeetingJob.input_watermark)).where(
                                MeetingJob.meeting_id == meeting.id,
                                MeetingJob.job_kind == MeetingJobKind.CORRECTION.value,
                                MeetingJob.state != MeetingJobState.CANCELLED.value,
                            )
                        )
                    ).one()
                    or 0
                )
                remaining = meeting.last_segment_ordinal - correction_watermark
                oldest_unscheduled = None
                if remaining > 0:
                    oldest_unscheduled = (
                        await session.exec(
                            select(func.min(MeetingTranscriptSegment.created_at)).where(
                                MeetingTranscriptSegment.meeting_id == meeting.id,
                                MeetingTranscriptSegment.ordinal > correction_watermark,
                            )
                        )
                    ).one()
                correction_due = (
                    MeetingJobKind.CORRECTION.value in self.job_kinds
                    and pending_correction is None
                    and (
                        remaining >= 3
                        or (
                            remaining > 0
                            and oldest_unscheduled is not None
                            and _utc(oldest_unscheduled)
                            <= now - timedelta(seconds=self.config.correction_debounce_seconds)
                        )
                    )
                )
                if correction_due:
                    input_from = correction_watermark + 1
                    correction_window = (
                        self.config.import_correction_window_segments
                        if meeting.audio_source == AudioSource.IMPORT.value
                        else self.config.correction_window_segments
                    )
                    input_to = min(
                        meeting.last_segment_ordinal,
                        correction_watermark + correction_window,
                    )
                    key = (
                        f"{meeting.id}:correction:range:{input_from}-{input_to}:"
                        f"settings:{meeting.settings_version}:"
                        f"prompt:{PROMPT_VERSIONS[MeetingJobKind.CORRECTION.value]}"
                    )
                    if not await self._job_exists(session, key):
                        job = MeetingJob(
                            meeting_id=meeting.id,
                            job_kind=MeetingJobKind.CORRECTION.value,
                            idempotency_key=key,
                            input_watermark=input_to,
                            settings_version=meeting.settings_version,
                        )
                        session.add(job)
                        await session.flush()
                        await event_store.append(
                            meeting.id,
                            "meeting.ai.job.queued",
                            {
                                "job_id": job.id,
                                "job_kind": job.job_kind,
                                "input_from_ordinal": input_from,
                                "input_to_ordinal": input_to,
                                "settings_version": job.settings_version,
                            },
                        )
                        created += 1

                pending_rolling = (
                    await session.exec(
                        select(MeetingJob.id)
                        .where(
                            MeetingJob.meeting_id == meeting.id,
                            MeetingJob.job_kind == MeetingJobKind.ROLLING_MINUTES.value,
                            MeetingJob.state.in_(
                                [
                                    MeetingJobState.QUEUED.value,
                                    MeetingJobState.LEASED.value,
                                    MeetingJobState.RUNNING.value,
                                    MeetingJobState.RETRY_WAIT.value,
                                ]
                            ),
                        )
                        .limit(1)
                    )
                ).first()
                latest_rolling = (
                    await session.exec(
                        select(MeetingJob)
                        .where(
                            MeetingJob.meeting_id == meeting.id,
                            MeetingJob.job_kind == MeetingJobKind.ROLLING_MINUTES.value,
                        )
                        .order_by(
                            col(MeetingJob.input_watermark).desc(),
                            col(MeetingJob.updated_at).desc(),
                        )
                        .limit(1)
                    )
                ).first()
                rolling_watermark = latest_rolling.input_watermark if latest_rolling else 0
                enough_new = meeting.last_segment_ordinal - rolling_watermark >= self.config.rolling_min_new_segments
                if latest_rolling is None:
                    first_segment_at = (
                        await session.exec(
                            select(func.min(MeetingTranscriptSegment.created_at)).where(
                                MeetingTranscriptSegment.meeting_id == meeting.id
                            )
                        )
                    ).one()
                    debounce_ready = first_segment_at is not None and _utc(first_segment_at) <= now - timedelta(
                        seconds=self.config.rolling_debounce_seconds
                    )
                else:
                    debounce_ready = _utc(latest_rolling.updated_at) <= (
                        now - timedelta(seconds=self.config.rolling_debounce_seconds)
                    )
                if (
                    MeetingJobKind.ROLLING_MINUTES.value in self.job_kinds
                    and meeting.state in {MeetingState.LIVE.value, MeetingState.PAUSED.value}
                    and pending_rolling is None
                    and enough_new
                    and debounce_ready
                ):
                    key = (
                        f"{meeting.id}:rolling_minutes:1-{meeting.last_segment_ordinal}:"
                        f"settings:{meeting.settings_version}:"
                        f"prompt:{PROMPT_VERSIONS[MeetingJobKind.ROLLING_MINUTES.value]}"
                    )
                    if not await self._job_exists(session, key):
                        job = MeetingJob(
                            meeting_id=meeting.id,
                            job_kind=MeetingJobKind.ROLLING_MINUTES.value,
                            idempotency_key=key,
                            input_watermark=meeting.last_segment_ordinal,
                            settings_version=meeting.settings_version,
                        )
                        session.add(job)
                        await session.flush()
                        await event_store.append(
                            meeting.id,
                            "meeting.ai.job.queued",
                            {
                                "job_id": job.id,
                                "job_kind": job.job_kind,
                                "input_from_ordinal": 1,
                                "input_to_ordinal": meeting.last_segment_ordinal,
                                "settings_version": job.settings_version,
                            },
                        )
                        created += 1
            try:
                await session.commit()
            except IntegrityError:
                # Another worker published the same idempotency key first.
                await session.rollback()
                return 0
        return created

    @staticmethod
    async def _job_exists(session: AsyncSession, idempotency_key: str) -> bool:
        return (
            await session.exec(select(MeetingJob.id).where(MeetingJob.idempotency_key == idempotency_key).limit(1))
        ).first() is not None

    async def _process(self, job_id: str) -> None:
        try:
            job = await self._mark_running(job_id)
            if job.job_kind == MeetingJobKind.FINAL_TRANSCRIPT.value:
                await self._process_final_transcript(job)
                return
            if job.job_kind == MeetingJobKind.SPEAKER_RECLUSTER.value:
                await self._process_speaker_recluster(job)
                return
            if job.job_kind not in AI_JOB_KINDS:
                raise MeetingAIConfigurationInvalid("unsupported meeting AI job kind")

            snapshot = await self._load_or_create_snapshot(job)
            if snapshot is None:
                await self._complete_skipped(job)
                return
            transcript = await self._load_transcript(job)
            task = self._task_for_job(job.job_kind)
            if not transcript.segments:
                if task == MeetingAITask.CORRECTION:
                    await self._complete_empty_correction(job)
                else:
                    await self._apply_minutes(
                        job,
                        snapshot,
                        transcript,
                        self._empty_minutes(task),
                    )
                return

            result = await self._execute_with_heartbeat(
                job,
                snapshot=snapshot,
                task=task,
                transcript=transcript,
            )
            if result.snapshot != snapshot or result.task != task:
                raise MeetingHermesProtocolError("meeting AI result provenance does not match the execution snapshot")
            if task == MeetingAITask.CORRECTION:
                await self._apply_correction(job, result, transcript)
            else:
                await self._apply_minutes(
                    job,
                    snapshot,
                    transcript,
                    result.output,
                    hermes_run_id=result.run_id,
                )
        except MeetingAILeaseLost:
            return
        except Exception as exc:
            await self._fail_job(job_id, exc)

    async def _execute_with_heartbeat(
        self,
        job: MeetingJob,
        *,
        snapshot: MeetingHermesExecutionSnapshot,
        task: MeetingAITask,
        transcript: TranscriptInput,
    ) -> MeetingHermesRunResult:
        stop = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(job.id, stop))
        try:
            result = await self.runner.execute(
                snapshot=snapshot,
                task=task,
                job_id=f"{job.id}:attempt:{job.attempt}",
                segments=transcript.segments,
                glossary=transcript.glossary,
                participants=transcript.participants,
                language=transcript.language,
            )
        finally:
            stop.set()
            lease_valid = await heartbeat
            if not lease_valid:
                raise MeetingAILeaseLost("meeting AI lease expired during model execution")
        return result

    async def _heartbeat(self, job_id: str, stop: asyncio.Event) -> bool:
        interval = max(10.0, self.config.lease_seconds / 3)
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return True
            except TimeoutError:
                if not await self.renew_lease(job_id):
                    return False

    async def _mark_running(self, job_id: str) -> MeetingJob:
        async with self.session_factory() as session:
            job = (
                await session.exec(
                    select(MeetingJob).where(
                        MeetingJob.id == job_id,
                        MeetingJob.state == MeetingJobState.LEASED.value,
                        MeetingJob.lease_owner == self.worker_id,
                    )
                )
            ).first()
            if job is None:
                raise MeetingAILeaseLost("meeting AI lease is no longer owned")
            job.state = MeetingJobState.RUNNING.value
            job.updated_at = utcnow()
            session.add(job)
            await session.commit()
            await session.refresh(job)
            return job

    @staticmethod
    def _task_for_job(job_kind: str) -> MeetingAITask:
        if job_kind == MeetingJobKind.CORRECTION.value:
            return MeetingAITask.CORRECTION
        if job_kind == MeetingJobKind.ROLLING_MINUTES.value:
            return MeetingAITask.ROLLING_MINUTES
        if job_kind == MeetingJobKind.FINAL_MINUTES.value:
            return MeetingAITask.FINAL_MINUTES
        raise MeetingAIConfigurationInvalid("unsupported meeting AI task")

    def _select_auto_target(self, setting: MeetingModelSetting) -> MeetingHermesTarget:
        configured = sorted(
            (target for target in self.runner.pool.list_targets() if target.enabled),
            key=lambda target: (target.locality != "local", target.model_ref),
        )
        requested: MeetingHermesTarget | None = None
        if setting.requested_model_ref:
            try:
                requested = self.runner.pool.require_model(setting.requested_model_ref)
            except MeetingHermesTargetUnavailable:
                if setting.fallback_policy == ModelFallbackPolicy.DISABLED.value:
                    raise
            if requested is not None:
                if requested.locality == "cloud" and setting.cloud_data_boundary_confirmed_at is None:
                    raise MeetingHermesTargetUnavailable(
                        "cloud data boundary was not confirmed for this meeting setting"
                    )
                return requested

        if setting.fallback_policy in {
            ModelFallbackPolicy.DISABLED.value,
            ModelFallbackPolicy.LOCAL_ONLY.value,
        }:
            configured = [target for target in configured if target.locality == "local"]
        elif setting.fallback_policy == ModelFallbackPolicy.EXPLICIT_POLICY.value:
            if setting.cloud_data_boundary_confirmed_at is None:
                configured = [target for target in configured if target.locality == "local"]
        else:
            raise MeetingAIConfigurationInvalid("unknown meeting model fallback policy")
        if not configured:
            raise MeetingHermesTargetUnavailable("no eligible meeting model target is available")
        default_ref = os.getenv("SIQ_MEETING_DEFAULT_MODEL_REF", "").strip()
        if default_ref:
            default_target = next(
                (target for target in configured if target.model_ref == default_ref),
                None,
            )
            if default_target is not None:
                return default_target
        return configured[0]

    async def _load_or_create_snapshot(self, job: MeetingJob) -> MeetingHermesExecutionSnapshot | None:
        async with self.session_factory() as session:
            current = (
                await session.exec(
                    select(MeetingJob).where(
                        MeetingJob.id == job.id,
                        MeetingJob.state == MeetingJobState.RUNNING.value,
                        MeetingJob.lease_owner == self.worker_id,
                    )
                )
            ).first()
            if current is None:
                raise MeetingAILeaseLost("meeting AI lease was lost before snapshot resolution")
            if current.model_snapshot_id:
                stored = await session.get(MeetingModelSnapshot, current.model_snapshot_id)
                if stored is None:
                    raise MeetingAIConfigurationInvalid("meeting model snapshot is missing")
                if stored.meeting_id != current.meeting_id or stored.settings_version != current.settings_version:
                    raise MeetingAIConfigurationInvalid("meeting model snapshot does not match job")
                return self._execution_snapshot(stored)

            setting = (
                await session.exec(
                    select(MeetingModelSetting).where(
                        MeetingModelSetting.meeting_id == current.meeting_id,
                        MeetingModelSetting.settings_version == current.settings_version,
                    )
                )
            ).first()
            if setting is None:
                raise MeetingAIConfigurationInvalid("meeting model setting version is missing")
            if setting.selection_mode == ModelSelectionMode.NONE.value:
                return None
            if setting.selection_mode == ModelSelectionMode.PINNED.value:
                if not setting.requested_model_ref:
                    raise MeetingAIConfigurationInvalid("pinned meeting model is missing")
                target = self.runner.pool.require_model(setting.requested_model_ref)
                if target.locality == "cloud" and setting.cloud_data_boundary_confirmed_at is None:
                    raise MeetingHermesTargetUnavailable(
                        "cloud data boundary was not confirmed for this meeting setting"
                    )
            elif setting.selection_mode == ModelSelectionMode.AUTO.value:
                target = self._select_auto_target(setting)
            else:
                raise MeetingAIConfigurationInvalid("meeting model selection mode is invalid")

            resolved = self.runner.resolve_snapshot(
                meeting_id=current.meeting_id,
                model_ref=target.model_ref,
                selection_mode=setting.selection_mode,  # type: ignore[arg-type]
                settings_version=setting.settings_version,
                effective_after_segment_ordinal=setting.effective_after_segment_ordinal,
                prompt_version=PROMPT_VERSIONS[current.job_kind],
            )
            stored = MeetingModelSnapshot(
                meeting_id=resolved.meeting_id,
                model_ref=resolved.model_ref,
                selection_mode=resolved.selection_mode,
                resolved_provider=resolved.resolved_provider,
                resolved_model=resolved.resolved_model,
                provider_locality=resolved.provider_locality,
                hermes_target=resolved.target_id,
                meeting_profile_version=resolved.meeting_profile_version,
                prompt_version=resolved.prompt_version,
                schema_version=self._schema_for_job(current.job_kind),
                settings_version=resolved.settings_version,
                effective_after_segment_ordinal=resolved.effective_after_segment_ordinal,
            )
            session.add(stored)
            await session.flush()
            current.model_snapshot_id = stored.id
            current.updated_at = utcnow()
            session.add(current)
            await session.commit()
            return resolved

    @staticmethod
    def _schema_for_job(job_kind: str) -> str:
        if job_kind == MeetingJobKind.CORRECTION.value:
            return CORRECTION_SCHEMA_VERSION
        if job_kind == MeetingJobKind.ROLLING_MINUTES.value:
            return ROLLING_SCHEMA_VERSION
        return FINAL_SCHEMA_VERSION

    @staticmethod
    def _execution_snapshot(stored: MeetingModelSnapshot) -> MeetingHermesExecutionSnapshot:
        if stored.selection_mode not in {
            ModelSelectionMode.PINNED.value,
            ModelSelectionMode.AUTO.value,
        }:
            raise MeetingAIConfigurationInvalid("stored model snapshot selection is invalid")
        if stored.provider_locality not in {"local", "cloud"}:
            raise MeetingAIConfigurationInvalid("stored model snapshot locality is invalid")
        return MeetingHermesExecutionSnapshot(
            meeting_id=stored.meeting_id,
            model_ref=stored.model_ref,
            target_id=stored.hermes_target,
            selection_mode=stored.selection_mode,  # type: ignore[arg-type]
            resolved_provider=stored.resolved_provider,
            resolved_model=stored.resolved_model,
            provider_locality=stored.provider_locality,  # type: ignore[arg-type]
            settings_version=stored.settings_version,
            effective_after_segment_ordinal=stored.effective_after_segment_ordinal,
            prompt_version=stored.prompt_version,
            meeting_profile_version=stored.meeting_profile_version,
        )

    async def _load_transcript(self, job: MeetingJob) -> TranscriptInput:
        async with self.session_factory() as session:
            meeting = await session.get(MeetingSession, job.meeting_id)
            if meeting is None:
                raise MeetingAIConfigurationInvalid("meeting session is missing")
            watermark = job.input_watermark or meeting.last_segment_ordinal
            input_from = self._input_from_ordinal(job)
            values = list(
                (
                    await session.exec(
                        select(MeetingTranscriptSegment)
                        .where(
                            MeetingTranscriptSegment.meeting_id == job.meeting_id,
                            MeetingTranscriptSegment.ordinal >= input_from,
                            MeetingTranscriptSegment.ordinal <= watermark,
                        )
                        .order_by(MeetingTranscriptSegment.ordinal)
                    )
                ).all()
            )
            revisions = await self._all_revisions(session, [value.id for value in values])
            latest: dict[str, MeetingSegmentRevision] = {}
            for revision in revisions:
                if revision.segment_id not in latest or revision.revision_no > latest[revision.segment_id].revision_no:
                    latest[revision.segment_id] = revision
            track_ids = {value.speaker_track_id for value in values if value.speaker_track_id}
            tracks: dict[str, MeetingSpeakerTrack] = {}
            if track_ids:
                track_values = (
                    await session.exec(select(MeetingSpeakerTrack).where(MeetingSpeakerTrack.id.in_(track_ids)))
                ).all()
                tracks = {value.id: value for value in track_values}

            segments: list[dict[str, Any]] = []
            revision_vector: dict[str, int] = {}
            for value in values:
                revision = latest.get(value.id)
                revision_no = revision.revision_no if revision else 0
                text = revision.text if revision else (value.normalized_text or value.asr_final_text)
                track = tracks.get(value.speaker_track_id or "")
                speaker = (track.display_name or track.anonymous_label) if track else None
                segments.append(
                    {
                        "segment_id": value.id,
                        "ordinal": value.ordinal,
                        "revision": revision_no,
                        "start_ms": value.start_ms,
                        "end_ms": value.end_ms,
                        "speaker_label": speaker,
                        "text": text,
                        "human_locked": value.human_locked,
                    }
                )
                revision_vector[value.id] = revision_no

            glossary = await self._load_glossary(session, meeting)
            participants = sorted(
                {value.display_name for value in tracks.values() if value.display_name and value.display_name.strip()}
            )
            return TranscriptInput(
                segments=segments,
                segment_ids=frozenset(value.id for value in values),
                revision_vector=revision_vector,
                transcript_revision=len(revisions),
                from_ordinal=values[0].ordinal if values else 1,
                to_ordinal=values[-1].ordinal if values else watermark,
                glossary=glossary,
                participants=participants,
                language=meeting.language,
            )

    @staticmethod
    def _input_from_ordinal(job: MeetingJob) -> int:
        if job.job_kind != MeetingJobKind.CORRECTION.value:
            return 1
        marker = re.search(r":correction:range:(\d+)-(\d+):", job.idempotency_key)
        if marker is None:
            return 1
        start = int(marker.group(1))
        end = int(marker.group(2))
        if start < 1 or end != job.input_watermark or start > end:
            raise MeetingAIConfigurationInvalid("correction job input range is invalid")
        return start

    @staticmethod
    async def _all_revisions(session: AsyncSession, segment_ids: Iterable[str]) -> list[MeetingSegmentRevision]:
        identifiers = list(segment_ids)
        if not identifiers:
            return []
        return list(
            (
                await session.exec(
                    select(MeetingSegmentRevision)
                    .where(MeetingSegmentRevision.segment_id.in_(identifiers))
                    .order_by(
                        MeetingSegmentRevision.segment_id,
                        MeetingSegmentRevision.revision_no,
                    )
                )
            ).all()
        )

    @staticmethod
    async def _load_glossary(session: AsyncSession, meeting: MeetingSession) -> list[str]:
        statement = select(MeetingLexiconVersion).where(
            MeetingLexiconVersion.owner_user_id == meeting.owner_user_id,
            MeetingLexiconVersion.language == meeting.language,
        )
        if meeting.active_lexicon_version:
            statement = statement.where(MeetingLexiconVersion.version == meeting.active_lexicon_version)
        else:
            statement = statement.where(MeetingLexiconVersion.is_active.is_(True)).order_by(
                col(MeetingLexiconVersion.version).desc()
            )
        version = (await session.exec(statement.limit(1))).first()
        if version is None:
            return []
        entries = decode_json(version.entries_json, [])
        terms: list[str] = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            canonical = entry.get("canonical_term")
            if isinstance(canonical, str) and canonical.strip():
                terms.append(canonical.strip())
        return list(dict.fromkeys(terms))[:1000]

    async def _apply_correction(
        self,
        job: MeetingJob,
        result: MeetingHermesRunResult,
        transcript: TranscriptInput,
    ) -> None:
        patches = result.output.get("patches")
        review_flags = result.output.get("review_flags") or []
        if not isinstance(patches, list) or not isinstance(review_flags, list):
            raise MeetingAIOutputInvalid("meeting correction output shape is invalid")
        expected = {value["segment_id"]: value for value in transcript.segments}
        async with self.session_factory() as session:
            current_job = await self._owned_running_job(session, job.id)
            if current_job is None:
                raise MeetingAILeaseLost("meeting correction lease was lost")
            current_segments = list(
                (
                    await session.exec(
                        select(MeetingTranscriptSegment).where(
                            MeetingTranscriptSegment.id.in_(list(transcript.segment_ids))
                        )
                    )
                ).all()
            )
            by_id = {value.id: value for value in current_segments}
            revisions = await self._all_revisions(session, transcript.segment_ids)
            latest: dict[str, MeetingSegmentRevision] = {}
            for revision in revisions:
                previous = latest.get(revision.segment_id)
                if previous is None or revision.revision_no > previous.revision_no:
                    latest[revision.segment_id] = revision

            applied: list[str] = []
            rejected: list[dict[str, str]] = []
            seen: set[str] = set()
            event_store = MeetingEventStore(session)
            for raw_patch in patches:
                if not isinstance(raw_patch, dict):
                    rejected.append({"segment_id": "", "reason": "patch_not_object"})
                    continue
                segment_id = str(raw_patch.get("segment_id") or "")
                base = raw_patch.get("base_revision")
                original = raw_patch.get("original")
                replacement = str(raw_patch.get("replacement") or "").strip()
                confidence = raw_patch.get("confidence")
                reason_code = str(raw_patch.get("reason_code") or "unknown")
                source = expected.get(segment_id)
                segment = by_id.get(segment_id)
                current_revision = latest.get(segment_id)
                current_revision_no = current_revision.revision_no if current_revision else 0
                current_text = (
                    current_revision.text
                    if current_revision
                    else ((segment.normalized_text or segment.asr_final_text) if segment is not None else "")
                )
                rejection: str | None = None
                if not source or segment is None or segment_id in seen:
                    rejection = "segment_not_unique_in_input"
                elif not isinstance(base, int) or base != source["revision"]:
                    rejection = "base_revision_mismatch"
                elif base != current_revision_no:
                    rejection = "revision_changed"
                elif not isinstance(original, str) or original != source["text"]:
                    rejection = "original_mismatch"
                elif original != current_text:
                    rejection = "current_text_changed"
                elif segment.human_locked:
                    rejection = "human_locked"
                elif not isinstance(confidence, (int, float)) or confidence < self.config.correction_confidence:
                    rejection = "confidence_below_threshold"
                elif not replacement or replacement == current_text:
                    rejection = "replacement_unchanged"
                elif _critical_entities(current_text) != _critical_entities(replacement):
                    rejection = "critical_entity_changed"
                if rejection:
                    rejected.append({"segment_id": segment_id, "reason": rejection})
                    continue
                seen.add(segment_id)
                diff = calculate_diff(current_text, replacement)
                revision = MeetingSegmentRevision(
                    segment_id=segment_id,
                    revision_no=current_revision_no + 1,
                    revision_type=SegmentRevisionType.LLM_CORRECTION.value,
                    text=replacement,
                    base_revision_no=current_revision_no,
                    reason_codes_json=encode_json([reason_code, f"confidence:{float(confidence):.4f}"]),
                    model_snapshot_id=current_job.model_snapshot_id,
                    created_by=f"meeting-ai:{self.worker_id}",
                )
                segment.updated_at = utcnow()
                session.add(segment)
                session.add(revision)
                await session.flush()
                segment_snapshot = {
                    "id": segment.id,
                    "meeting_id": segment.meeting_id,
                    "ordinal": segment.ordinal,
                    "utterance_id": segment.utterance_id,
                    "start_ms": segment.start_ms,
                    "end_ms": segment.end_ms,
                    "speaker_track_id": segment.speaker_track_id,
                    "speaker_display_name": source.get("speaker_label"),
                    "raw_text": segment.raw_text,
                    "asr_final_text": segment.asr_final_text,
                    "normalized_text": segment.normalized_text,
                    "display_text": replacement,
                    "text": replacement,
                    "revision_no": revision.revision_no,
                    "current_revision_no": revision.revision_no,
                    "display_layer": "llm_corrected",
                    "text_state": "optimized",
                    "human_locked": False,
                    "asr_confidence": segment.asr_confidence,
                    "overlap": segment.overlap,
                    "diff_ops": diff["operations"],
                    "updated_at": segment.updated_at.isoformat(),
                }
                await event_store.append(
                    job.meeting_id,
                    "transcript.segment.corrected",
                    {
                        "segment_id": segment_id,
                        "revision_no": revision.revision_no,
                        "base_revision_no": current_revision_no,
                        "display_layer": "llm_corrected",
                        "text": replacement,
                        "human_locked": False,
                        "text_state": "optimized",
                        "reason_code": reason_code,
                        "confidence": confidence,
                        "model_snapshot_id": current_job.model_snapshot_id,
                        "hermes_run_id": result.run_id,
                        "diff": diff,
                        "segment": segment_snapshot,
                    },
                )
                applied.append(segment_id)

            if applied:
                ready_artifacts = (
                    await session.exec(
                        select(MeetingArtifact).where(
                            MeetingArtifact.meeting_id == job.meeting_id,
                            MeetingArtifact.state == ArtifactState.READY.value,
                        )
                    )
                ).all()
                for artifact in ready_artifacts:
                    artifact.state = ArtifactState.STALE.value
                    artifact.updated_at = utcnow()
                    session.add(artifact)
            if rejected or review_flags:
                await event_store.append(
                    job.meeting_id,
                    "transcript.correction.review_required",
                    {
                        "job_id": job.id,
                        "rejected_patches": rejected,
                        "review_flags": review_flags,
                    },
                )
            self._mark_job_succeeded(current_job)
            await event_store.append(
                job.meeting_id,
                "meeting.ai.job.succeeded",
                {
                    "job_id": job.id,
                    "job_kind": job.job_kind,
                    "applied_segment_ids": applied,
                    "rejected_patch_count": len(rejected),
                },
            )
            await session.commit()

    async def _apply_minutes(
        self,
        job: MeetingJob,
        snapshot: MeetingHermesExecutionSnapshot,
        transcript: TranscriptInput,
        output: dict[str, Any],
        *,
        hermes_run_id: str | None = None,
    ) -> None:
        output = _normalize_minutes_evidence(output, transcript.segments)
        if not _evidence_is_valid(output, transcript.segment_ids):
            raise MeetingAIOutputInvalid("meeting minutes cite evidence outside the transcript input")
        artifact_type = (
            ArtifactType.ROLLING_MINUTES.value
            if job.job_kind == MeetingJobKind.ROLLING_MINUTES.value
            else ArtifactType.FINAL_MINUTES.value
        )
        async with self.session_factory() as session:
            current_job = await self._owned_running_job(session, job.id)
            if current_job is None:
                raise MeetingAILeaseLost("meeting minutes lease was lost")
            current_vector, current_revision = await self._revision_state(session, transcript.segment_ids)
            if current_vector != transcript.revision_vector:
                raise MeetingAIInputChanged("transcript changed while minutes were generated")
            existing = (
                await session.exec(
                    select(MeetingArtifact).where(
                        MeetingArtifact.meeting_id == job.meeting_id,
                        MeetingArtifact.artifact_type == artifact_type,
                        MeetingArtifact.model_snapshot_id == current_job.model_snapshot_id,
                        MeetingArtifact.input_from_ordinal == transcript.from_ordinal,
                        MeetingArtifact.input_to_ordinal == transcript.to_ordinal,
                        MeetingArtifact.transcript_revision == current_revision,
                        MeetingArtifact.state == ArtifactState.READY.value,
                    )
                )
            ).first()
            if existing is None:
                artifact = await self._prepared_artifact(session, current_job, artifact_type)
                previous = (
                    await session.exec(
                        select(MeetingArtifact)
                        .where(
                            MeetingArtifact.meeting_id == job.meeting_id,
                            MeetingArtifact.artifact_type == artifact_type,
                            MeetingArtifact.id != (artifact.id if artifact else ""),
                            MeetingArtifact.state.in_([ArtifactState.READY.value, ArtifactState.STALE.value]),
                        )
                        .order_by(col(MeetingArtifact.version).desc())
                        .limit(1)
                    )
                ).first()
                if artifact is None:
                    max_version = int(
                        (
                            await session.exec(
                                select(func.max(MeetingArtifact.version)).where(
                                    MeetingArtifact.meeting_id == job.meeting_id,
                                    MeetingArtifact.artifact_type == artifact_type,
                                )
                            )
                        ).one()
                        or 0
                    )
                    artifact = MeetingArtifact(
                        meeting_id=job.meeting_id,
                        artifact_type=artifact_type,
                        version=max_version + 1,
                        supersedes_id=previous.id if previous else None,
                    )
                elif artifact.supersedes_id is None and previous is not None:
                    artifact.supersedes_id = previous.id
                if previous is not None and previous.state == ArtifactState.READY.value:
                    previous.state = ArtifactState.STALE.value
                    previous.updated_at = utcnow()
                    session.add(previous)
                artifact.state = ArtifactState.READY.value
                artifact.content_json = encode_json(output)
                artifact.content_text = _minutes_markdown(output)
                artifact.input_from_ordinal = transcript.from_ordinal
                artifact.input_to_ordinal = transcript.to_ordinal
                artifact.transcript_revision = current_revision
                artifact.model_snapshot_id = current_job.model_snapshot_id
                artifact.updated_at = utcnow()
                session.add(artifact)
                await session.flush()
                existing = artifact

            self._mark_job_succeeded(current_job)
            if artifact_type == ArtifactType.FINAL_MINUTES.value:
                meeting = await session.get(MeetingSession, job.meeting_id)
                if meeting is not None:
                    meeting.postprocess_state = MeetingPostprocessState.SUCCEEDED.value
                    meeting.updated_at = utcnow()
                    session.add(meeting)
            event_type = (
                "minutes.rolling.updated"
                if artifact_type == ArtifactType.ROLLING_MINUTES.value
                else "minutes.final.ready"
            )
            await MeetingEventStore(session).append(
                job.meeting_id,
                event_type,
                {
                    "job_id": job.id,
                    "artifact_id": existing.id,
                    "artifact_version": existing.version,
                    "input_from_ordinal": transcript.from_ordinal,
                    "input_to_ordinal": transcript.to_ordinal,
                    "transcript_revision": current_revision,
                    "model_snapshot_id": current_job.model_snapshot_id,
                    "hermes_run_id": hermes_run_id,
                    "temporary": artifact_type == ArtifactType.ROLLING_MINUTES.value,
                },
            )
            await session.commit()

    async def _prepared_artifact(
        self,
        session: AsyncSession,
        job: MeetingJob,
        artifact_type: str,
    ) -> MeetingArtifact | None:
        marker = re.search(r":artifact:[^:]+:(\d+):settings:", job.idempotency_key)
        if marker:
            return (
                await session.exec(
                    select(MeetingArtifact).where(
                        MeetingArtifact.meeting_id == job.meeting_id,
                        MeetingArtifact.artifact_type == artifact_type,
                        MeetingArtifact.version == int(marker.group(1)),
                        MeetingArtifact.state == ArtifactState.GENERATING.value,
                    )
                )
            ).first()
        return None

    @staticmethod
    async def _revision_state(session: AsyncSession, segment_ids: Iterable[str]) -> tuple[dict[str, int], int]:
        identifiers = list(segment_ids)
        vector = {identifier: 0 for identifier in identifiers}
        if not identifiers:
            return vector, 0
        revisions = list(
            (
                await session.exec(
                    select(MeetingSegmentRevision).where(MeetingSegmentRevision.segment_id.in_(identifiers))
                )
            ).all()
        )
        for revision in revisions:
            vector[revision.segment_id] = max(vector.get(revision.segment_id, 0), revision.revision_no)
        return vector, len(revisions)

    async def _process_final_transcript(self, job: MeetingJob) -> None:
        existing = await self._existing_final_alignment(job)
        if existing is not None:
            await self._reuse_final_alignment(job, existing.id)
            return
        stop = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(job.id, stop))
        try:
            analysis = await self.finalization_service.analyze(job.meeting_id, run_id=job.id)
            await self._apply_final_transcript(job, analysis)
        finally:
            stop.set()
            lease_valid = await heartbeat
            if not lease_valid and not await self._job_succeeded(job.id):
                raise MeetingAILeaseLost("final transcript lease expired during final ASR")

    async def _existing_final_alignment(self, job: MeetingJob) -> MeetingArtifact | None:
        async with self.session_factory() as session:
            return (
                await session.exec(
                    select(MeetingArtifact)
                    .where(
                        MeetingArtifact.meeting_id == job.meeting_id,
                        MeetingArtifact.artifact_type == ArtifactType.FINAL_TRANSCRIPT_ALIGNMENT.value,
                        MeetingArtifact.state == ArtifactState.READY.value,
                        MeetingArtifact.input_to_ordinal == job.input_watermark,
                    )
                    .order_by(col(MeetingArtifact.version).desc())
                    .limit(1)
                )
            ).first()

    async def _reuse_final_alignment(self, job: MeetingJob, artifact_id: str) -> None:
        async with self.session_factory() as session:
            current = await self._owned_running_job(session, job.id)
            artifact = await session.get(MeetingArtifact, artifact_id)
            if current is None:
                raise MeetingAILeaseLost("final transcript lease was lost")
            if (
                artifact is None
                or artifact.meeting_id != job.meeting_id
                or artifact.artifact_type != ArtifactType.FINAL_TRANSCRIPT_ALIGNMENT.value
                or artifact.state != ArtifactState.READY.value
            ):
                raise MeetingAIInputChanged("final transcript alignment changed")
            await self._ensure_speaker_recluster_job(session, current, artifact)
            self._mark_job_succeeded(current)
            await MeetingEventStore(session).append(
                job.meeting_id,
                "transcript.finalization.reused",
                {"job_id": job.id, "alignment_artifact_id": artifact.id},
            )
            await session.commit()

    async def _apply_final_transcript(
        self,
        job: MeetingJob,
        analysis: FinalizationAnalysis,
    ) -> None:
        async with self.session_factory() as session:
            current = await self._owned_running_job(session, job.id)
            if current is None:
                raise MeetingAILeaseLost("final transcript lease was lost")
            meeting = await session.get(MeetingSession, job.meeting_id)
            if meeting is None:
                raise MeetingAIConfigurationInvalid("meeting session is missing")
            existing = (
                await session.exec(
                    select(MeetingArtifact)
                    .where(
                        MeetingArtifact.meeting_id == job.meeting_id,
                        MeetingArtifact.artifact_type == ArtifactType.FINAL_TRANSCRIPT_ALIGNMENT.value,
                        MeetingArtifact.state == ArtifactState.READY.value,
                        MeetingArtifact.input_to_ordinal == job.input_watermark,
                    )
                    .order_by(col(MeetingArtifact.version).desc())
                    .limit(1)
                )
            ).first()
            if existing is not None:
                await self._ensure_speaker_recluster_job(session, current, existing)
                self._mark_job_succeeded(current)
                await session.commit()
                return

            segments = list(
                (
                    await session.exec(
                        select(MeetingTranscriptSegment)
                        .where(
                            MeetingTranscriptSegment.meeting_id == job.meeting_id,
                            MeetingTranscriptSegment.ordinal <= job.input_watermark,
                        )
                        .order_by(MeetingTranscriptSegment.ordinal)
                    )
                ).all()
            )
            if (
                not segments
                and meeting.audio_source == AudioSource.IMPORT.value
                and job.input_watermark == 0
                and analysis.segments
            ):
                # Imported recordings have no realtime stable transcript to
                # align against. Materialize the final-ASR result as the
                # canonical stable layer once, then reuse the normal alignment,
                # speaker reclustering, correction, and artifact pipeline.
                for ordinal, final_segment in enumerate(analysis.segments, start=1):
                    token = re.sub(r"[^A-Za-z0-9_.:-]", "-", final_segment.segment_token)[:96]
                    segment = MeetingTranscriptSegment(
                        meeting_id=job.meeting_id,
                        ordinal=ordinal,
                        utterance_id=f"import-{token or ordinal}"[:128],
                        provider_segment_key=f"import-final:{token or ordinal}"[:255],
                        start_ms=final_segment.start_ms,
                        end_ms=final_segment.end_ms,
                        raw_text=final_segment.text,
                        asr_final_text=final_segment.text,
                        normalized_text=None,
                        asr_confidence=None,
                        asr_provider=final_segment.adapter[:100],
                        asr_model="meeting-final-asr",
                        asr_version="siq.meeting.final_asr_window.v1",
                        word_timestamps_json=encode_json(
                            [
                                {
                                    "token_index": word.token_index,
                                    "start_ms": word.start_ms,
                                    "end_ms": word.end_ms,
                                    "text": word.text,
                                }
                                for word in final_segment.word_timestamps
                            ]
                        ),
                        asr_metadata_json=encode_json(
                            {
                                "source": "recording_import",
                                "final_segment_token": final_segment.segment_token,
                                "speaker_track_key": final_segment.speaker_track_key,
                                "speaker_confidence": final_segment.speaker_confidence,
                                "degraded_reason": final_segment.degraded_reason,
                                "window_index": final_segment.window_index,
                            }
                        ),
                    )
                    session.add(segment)
                    segments.append(segment)
                await session.flush()
                meeting.last_segment_ordinal = len(segments)
                meeting.updated_at = utcnow()
                current.input_watermark = len(segments)
                current.updated_at = utcnow()
                job.input_watermark = len(segments)
                session.add(meeting)
                session.add(current)
                await MeetingEventStore(session).append(
                    job.meeting_id,
                    "transcript.imported",
                    {
                        "job_id": job.id,
                        "segment_count": len(segments),
                        "input_watermark": len(segments),
                        "source": "recording_import",
                    },
                )
            alignments = align_final_segments(segments, analysis.segments)
            latest_revisions: dict[str, MeetingSegmentRevision] = {}
            revision_count = 0
            if segments:
                revisions = list(
                    (
                        await session.exec(
                            select(MeetingSegmentRevision)
                            .where(col(MeetingSegmentRevision.segment_id).in_([segment.id for segment in segments]))
                            .order_by(
                                MeetingSegmentRevision.segment_id,
                                MeetingSegmentRevision.revision_no,
                            )
                        )
                    ).all()
                )
                revision_count = len(revisions)
                for revision in revisions:
                    latest_revisions[revision.segment_id] = revision

            grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for alignment in alignments:
                grouped[str(alignment["stable_segment_id"])].append(alignment)
            segment_by_id = {segment.id: segment for segment in segments}
            revised_count = 0
            protected_count = 0
            for segment_id, values in grouped.items():
                segment = segment_by_id.get(segment_id)
                if segment is None:
                    continue
                latest = latest_revisions.get(segment_id)
                if segment.human_locked or (
                    latest is not None
                    and latest.revision_type in {SegmentRevisionType.MANUAL.value, SegmentRevisionType.REVERT.value}
                ):
                    protected_count += 1
                    for value in values:
                        value["application"] = "human_locked"
                    continue
                final_text = " ".join(
                    str(value["final_text"]).strip()
                    for value in sorted(values, key=lambda item: int(item["final_start_ms"]))
                    if str(value["final_text"]).strip()
                ).strip()
                current_text = latest.text if latest is not None else segment.normalized_text or segment.asr_final_text
                if not final_text or final_text == current_text:
                    for value in values:
                        value["application"] = "unchanged"
                    continue
                base_revision = latest.revision_no if latest is not None else 0
                revision = MeetingSegmentRevision(
                    segment_id=segment.id,
                    revision_no=base_revision + 1,
                    revision_type=SegmentRevisionType.FINAL_ASR_REVIEW.value,
                    text=final_text,
                    base_revision_no=base_revision,
                    reason_codes_json=encode_json(["final_asr", "timestamp_aligned"]),
                    created_by=f"meeting-final-asr:{job.id}"[:64],
                )
                session.add(revision)
                latest_revisions[segment.id] = revision
                revised_count += 1
                revision_count += 1
                for value in values:
                    value["application"] = "final_revision"

            previous = (
                await session.exec(
                    select(MeetingArtifact)
                    .where(
                        MeetingArtifact.meeting_id == job.meeting_id,
                        MeetingArtifact.artifact_type == ArtifactType.FINAL_TRANSCRIPT_ALIGNMENT.value,
                        MeetingArtifact.state == ArtifactState.READY.value,
                    )
                    .order_by(col(MeetingArtifact.version).desc())
                    .limit(1)
                )
            ).first()
            next_version = (
                int(
                    (
                        await session.exec(
                            select(func.max(MeetingArtifact.version)).where(
                                MeetingArtifact.meeting_id == job.meeting_id,
                                MeetingArtifact.artifact_type == ArtifactType.FINAL_TRANSCRIPT_ALIGNMENT.value,
                            )
                        )
                    ).one()
                    or 0
                )
                + 1
            )
            if previous is not None:
                previous.state = ArtifactState.STALE.value
                previous.updated_at = utcnow()
                session.add(previous)
            artifact = MeetingArtifact(
                meeting_id=job.meeting_id,
                artifact_type=ArtifactType.FINAL_TRANSCRIPT_ALIGNMENT.value,
                version=next_version,
                state=ArtifactState.READY.value,
                content_json=encode_json(
                    {
                        "schema_version": FINAL_ALIGNMENT_SCHEMA,
                        "job_id": job.id,
                        "mode": analysis.mode,
                        "diarizer_ref": analysis.diarizer_ref,
                        "manifest": {
                            "chunk_count": analysis.chunk_count,
                            "total_audio_bytes": analysis.total_audio_bytes,
                            "window_count": analysis.window_count,
                            "protocol_version": analysis.protocol_version,
                            "window_overlap_ms": analysis.window_overlap_ms,
                            "max_concurrency": analysis.max_concurrency,
                            "boundary_trimmed_segment_count": analysis.boundary_trimmed_segment_count,
                            "gaps": [{"start_ms": start_ms, "end_ms": end_ms} for start_ms, end_ms in analysis.gaps],
                        },
                        "alignments": alignments,
                        "revised_segment_count": revised_count,
                        "human_protected_segment_count": protected_count,
                    }
                ),
                input_from_ordinal=1,
                input_to_ordinal=job.input_watermark,
                transcript_revision=revision_count,
                supersedes_id=previous.id if previous else None,
            )
            session.add(artifact)
            await session.flush()
            event_store = MeetingEventStore(session)
            await event_store.append(
                job.meeting_id,
                "transcript.finalized",
                {
                    "job_id": job.id,
                    "input_watermark": job.input_watermark,
                    "mode": analysis.mode,
                    "alignment_artifact_id": artifact.id,
                    "chunk_count": analysis.chunk_count,
                    "window_count": analysis.window_count,
                    "gap_count": len(analysis.gaps),
                    "revised_segment_count": revised_count,
                    "human_protected_segment_count": protected_count,
                },
            )
            await self._ensure_speaker_recluster_job(session, current, artifact)
            self._mark_job_succeeded(current)
            await session.commit()

    async def _ensure_speaker_recluster_job(
        self,
        session: AsyncSession,
        source_job: MeetingJob,
        artifact: MeetingArtifact,
    ) -> MeetingJob:
        key = f"{source_job.meeting_id}:speaker_recluster:{source_job.input_watermark}:alignment:{artifact.id}"
        derived = (await session.exec(select(MeetingJob).where(MeetingJob.idempotency_key == key))).first()
        if derived is None:
            derived = MeetingJob(
                meeting_id=source_job.meeting_id,
                job_kind=MeetingJobKind.SPEAKER_RECLUSTER.value,
                idempotency_key=key,
                input_watermark=source_job.input_watermark,
                settings_version=source_job.settings_version,
                input_json=encode_json({"alignment_artifact_id": artifact.id}),
            )
            session.add(derived)
            await session.flush()
            await MeetingEventStore(session).append(
                source_job.meeting_id,
                "postprocess.speaker_recluster.queued",
                {
                    "job_id": derived.id,
                    "source_job_id": source_job.id,
                    "alignment_artifact_id": artifact.id,
                    "input_watermark": source_job.input_watermark,
                },
            )
        return derived

    async def _complete_speaker_recluster(self, job: MeetingJob) -> None:
        async with self.session_factory() as session:
            current = await self._owned_running_job(session, job.id)
            if current is None:
                raise MeetingAILeaseLost("speaker recluster lease was lost")
            meeting = await session.get(MeetingSession, job.meeting_id)
            if meeting is None:
                raise MeetingAIConfigurationInvalid("meeting session is missing")
            job_input = decode_json(current.input_json, {})
            artifact_id = job_input.get("alignment_artifact_id")
            if not isinstance(artifact_id, str):
                raise MeetingAIConfigurationInvalid("speaker recluster alignment is missing")
            alignment_artifact = await session.get(MeetingArtifact, artifact_id)
            if (
                alignment_artifact is None
                or alignment_artifact.meeting_id != job.meeting_id
                or alignment_artifact.artifact_type != ArtifactType.FINAL_TRANSCRIPT_ALIGNMENT.value
                or alignment_artifact.state != ArtifactState.READY.value
            ):
                raise MeetingAIInputChanged("speaker recluster alignment changed")
            payload = decode_json(alignment_artifact.content_json, {})
            if payload.get("schema_version") != FINAL_ALIGNMENT_SCHEMA or not isinstance(
                payload.get("alignments"), list
            ):
                raise MeetingAIOutputInvalid("speaker recluster alignment is invalid")
            raw_observed_diarizer_ref = payload.get("diarizer_ref")
            observed_diarizer_ref = (
                raw_observed_diarizer_ref
                if isinstance(raw_observed_diarizer_ref, str)
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{2,191}", raw_observed_diarizer_ref)
                else None
            )

            segments = list(
                (
                    await session.exec(
                        select(MeetingTranscriptSegment)
                        .where(
                            MeetingTranscriptSegment.meeting_id == job.meeting_id,
                            MeetingTranscriptSegment.ordinal <= job.input_watermark,
                        )
                        .order_by(MeetingTranscriptSegment.ordinal)
                    )
                ).all()
            )
            tracks = list(
                (
                    await session.exec(
                        select(MeetingSpeakerTrack)
                        .where(MeetingSpeakerTrack.meeting_id == job.meeting_id)
                    )
                ).all()
            )
            chunks = list(
                (
                    await session.exec(
                        select(MeetingAudioChunk)
                        .where(
                            MeetingAudioChunk.meeting_id == job.meeting_id,
                            MeetingAudioChunk.state != "deleted",
                        )
                        .order_by(
                            MeetingAudioChunk.start_ms,
                            MeetingAudioChunk.stream_epoch,
                            MeetingAudioChunk.sequence,
                        )
                    )
                ).all()
            )
            mapping_events = list(
                (
                    await session.exec(
                        select(MeetingEvent.payload_json)
                        .where(
                            MeetingEvent.meeting_id == job.meeting_id,
                            MeetingEvent.event_type.in_(
                                ["speaker.track.merged", "speaker.track.split"]
                            ),
                        )
                        .order_by(MeetingEvent.cursor)
                    )
                ).all()
            )
            manual_mapping_track_ids: set[str] = set()
            manual_mapping_segment_ids: set[str] = set()
            for raw_payload in mapping_events:
                mapping_payload = decode_json(raw_payload, {})
                if mapping_payload.get("automatic") is not False:
                    continue
                for field_name in ("source_track_id", "target_track_id"):
                    value = mapping_payload.get(field_name)
                    if isinstance(value, str) and value:
                        manual_mapping_track_ids.add(value)
                for field_name in ("source_track_ids", "target_track_ids"):
                    values = mapping_payload.get(field_name)
                    if isinstance(values, list):
                        manual_mapping_track_ids.update(
                            value for value in values if isinstance(value, str) and value
                        )
                values = mapping_payload.get("segment_ids")
                if isinstance(values, list):
                    manual_mapping_segment_ids.update(
                        value for value in values if isinstance(value, str) and value
                    )
            segment_by_id = {segment.id: segment for segment in segments}
            original_track_by_segment = {segment.id: segment.speaker_track_id for segment in segments}
            track_by_id = {track.id: track for track in tracks}
            original_track_state = {
                track.id: (
                    track.version,
                    track.label_source,
                    track.display_name,
                    track.voice_profile_id,
                )
                for track in tracks
            }
            original_human_locks = {segment.id: segment.human_locked for segment in segments}
            final_keys: dict[str, str] = {}
            conflicting_segments: set[str] = set()
            for value in payload["alignments"]:
                if not isinstance(value, dict):
                    raise MeetingAIOutputInvalid("speaker recluster mapping is invalid")
                segment_id = value.get("stable_segment_id")
                final_key = value.get("speaker_track_key")
                if not isinstance(segment_id, str) or segment_id not in segment_by_id:
                    raise MeetingAIInputChanged("speaker recluster segment set changed")
                if final_key is None:
                    continue
                if not isinstance(final_key, str) or not final_key or len(final_key) > 128:
                    raise MeetingAIOutputInvalid("speaker recluster key is invalid")
                previous_key = final_keys.get(segment_id)
                if previous_key is not None and previous_key != final_key:
                    conflicting_segments.add(segment_id)
                    final_keys.pop(segment_id, None)
                    continue
                if segment_id not in conflicting_segments:
                    final_keys[segment_id] = final_key

            protected_sources = {
                SpeakerLabelSource.MANUAL.value,
                SpeakerLabelSource.VOICEPRINT_CONFIRMED.value,
                SpeakerLabelSource.VOICEPRINT_AUTO.value,
            }
            clusters: dict[str, list[MeetingTranscriptSegment]] = defaultdict(list)
            for segment_id, final_key in final_keys.items():
                clusters[final_key].append(segment_by_id[segment_id])
            protected_segment_ids: list[str] = []
            review_clusters: list[dict[str, Any]] = []
            claimed_target_keys: dict[str, str] = {}
            for final_key, members in sorted(clusters.items()):
                current_ids = [member.speaker_track_id for member in members if member.speaker_track_id]
                protected_ids = {
                    track_id
                    for track_id in current_ids
                    if track_id in manual_mapping_track_ids
                    or (
                        track_id in track_by_id
                        and track_by_id[track_id].label_source in protected_sources
                    )
                }
                if len(protected_ids) > 1:
                    review_clusters.append(
                        {
                            "final_track_key": final_key,
                            "protected_track_ids": sorted(protected_ids),
                            "segment_ids": [member.id for member in members],
                        }
                    )
                    continue
                if protected_ids:
                    if set(current_ids) - protected_ids:
                        review_clusters.append(
                            {
                                "final_track_key": final_key,
                                "protected_track_ids": sorted(protected_ids),
                                "segment_ids": [member.id for member in members],
                                "reason_code": "PROTECTED_TRACK_MERGE_REQUIRES_REVIEW",
                            }
                        )
                        continue
                    target_id = next(iter(protected_ids))
                    if target_id in claimed_target_keys:
                        review_clusters.append(
                            {
                                "final_track_key": final_key,
                                "protected_track_ids": [target_id],
                                "segment_ids": [member.id for member in members],
                                "reason_code": "PROTECTED_TRACK_SPLIT_REQUIRES_REVIEW",
                            }
                        )
                        continue
                else:
                    available_ids = [track_id for track_id in current_ids if track_id not in claimed_target_keys]
                    target_id = Counter(available_ids).most_common(1)[0][0] if available_ids else ""
                if not target_id:
                    target = MeetingSpeakerTrack(
                        meeting_id=job.meeting_id,
                        track_key=f"recluster-{uuid4().hex}",
                        anonymous_label=f"发言人 {len(track_by_id) + 1}",
                    )
                    track_by_id[target.id] = target
                    tracks.append(target)
                    target_id = target.id
                claimed_target_keys[target_id] = final_key
                for member in members:
                    source_id = member.speaker_track_id
                    if source_id == target_id:
                        continue
                    source = track_by_id.get(source_id or "")
                    if (
                        member.id in manual_mapping_segment_ids
                        or source_id in manual_mapping_track_ids
                        or (source is not None and source.label_source in protected_sources)
                    ):
                        protected_segment_ids.append(member.id)
                        continue
                    member.speaker_track_id = target_id
                    member.updated_at = utcnow()

            # Final-ASR keys are the provisional whole-meeting partition.  The
            # embedding pass must run after that partition exists (imports have
            # no live speaker tracks), and it is the last automatic mapping
            # layer so a fragmented final key cannot undo a confident merge.
            # Detach the provisional objects and end the read transaction before
            # touching audio or the embedding service.  The second phase below
            # re-locks and validates every user-controlled mapping field.
            session.expunge_all()
            await session.rollback()
            try:
                global_plan = await self.speaker_recluster_service.plan(
                    meeting=meeting,
                    run_id=current.id,
                    tracks=list(track_by_id.values()),
                    segments=segments,
                    chunks=chunks,
                    protected_track_ids=manual_mapping_track_ids,
                )
            except MeetingSpeakerReclusterError as exc:
                global_plan = SpeakerReclusterPlan(
                    policy_version=self.speaker_recluster_service.policy.version,
                    final_diarizer_ref=self.speaker_recluster_service.policy.final_diarizer_ref,
                    validation_artifact_sha256=self.speaker_recluster_service.policy.validation_artifact_sha256,
                    automatic_enabled=self.speaker_recluster_service.policy.auto_apply_validated,
                    degraded_reason=exc.code,
                )

            if global_plan.track_targets or global_plan.segment_cluster_keys:
                configured_policy = self.speaker_recluster_service.policy
                if global_plan.authoritative_global_clustering:
                    policy_gate_valid = (
                        global_plan.automatic_enabled
                        and global_plan.policy_version == "speaker-recluster.funasr-global-spectral.v1"
                        and isinstance(global_plan.final_diarizer_ref, str)
                        and re.fullmatch(
                            r"[A-Za-z0-9][A-Za-z0-9._:/-]{2,191}",
                            global_plan.final_diarizer_ref,
                        )
                        is not None
                    )
                else:
                    policy_gate_valid = (
                        global_plan.automatic_enabled
                        and configured_policy.auto_apply_validated
                        and global_plan.policy_version == configured_policy.version
                        and global_plan.final_diarizer_ref == configured_policy.final_diarizer_ref
                        and global_plan.validation_artifact_sha256 == configured_policy.validation_artifact_sha256
                        and ".validated." in global_plan.policy_version
                        and isinstance(global_plan.validation_artifact_sha256, str)
                        and re.fullmatch(r"[0-9a-f]{64}", global_plan.validation_artifact_sha256) is not None
                    )
                diarizer_binding_valid = (
                    global_plan.authoritative_global_clustering
                    or (
                        observed_diarizer_ref is not None
                        and observed_diarizer_ref == configured_policy.final_diarizer_ref
                    )
                )
                if not policy_gate_valid or not diarizer_binding_valid:
                    global_plan = replace(
                        global_plan,
                        track_targets={},
                        segment_cluster_keys={},
                        automatic_enabled=False,
                        degraded_reason=(
                            "SPEAKER_RECLUSTER_FINAL_DIARIZER_MISMATCH"
                            if policy_gate_valid and not diarizer_binding_valid
                            else "SPEAKER_RECLUSTER_POLICY_GATE_INVALID"
                        ),
                    )

            # Embedding may take seconds, so the initial reads intentionally do
            # not lock rows.  Re-lock and compare only the user-controlled
            # mapping state before applying the plan.  Pending provisional
            # objects remain unflushed while these database rows are checked.
            with session.no_autoflush:
                # All speaker mutations lock the meeting before tracks and
                # segments. Keep the worker's commit phase in that same order.
                locked_meeting = (
                    await session.exec(
                        select(MeetingSession)
                        .where(MeetingSession.id == job.meeting_id)
                        .with_for_update()
                    )
                ).first()
                if locked_meeting is None:
                    raise MeetingAIInputChanged("meeting session changed during global reclustering")
                locked_job = (
                    await session.exec(
                        select(MeetingJob)
                        .where(
                            MeetingJob.id == job.id,
                            MeetingJob.state == MeetingJobState.RUNNING.value,
                            MeetingJob.lease_owner == self.worker_id,
                            MeetingJob.lease_until > utcnow(),
                        )
                        .with_for_update()
                    )
                ).first()
                if locked_job is None:
                    raise MeetingAILeaseLost("speaker recluster lease was lost during embedding")
                locked_segments = list(
                    (
                        await session.exec(
                            select(
                                MeetingTranscriptSegment.id,
                                MeetingTranscriptSegment.speaker_track_id,
                                MeetingTranscriptSegment.human_locked,
                            )
                            .where(
                                MeetingTranscriptSegment.meeting_id == job.meeting_id,
                                MeetingTranscriptSegment.ordinal <= job.input_watermark,
                            )
                            .with_for_update()
                        )
                    ).all()
                )
                locked_tracks = list(
                    (
                        await session.exec(
                            select(
                                MeetingSpeakerTrack.id,
                                MeetingSpeakerTrack.version,
                                MeetingSpeakerTrack.label_source,
                                MeetingSpeakerTrack.display_name,
                                MeetingSpeakerTrack.voice_profile_id,
                            )
                            .where(MeetingSpeakerTrack.id.in_(set(original_track_state)))
                            .with_for_update()
                        )
                    ).all()
                )
            locked_segment_state = {
                segment_id: (speaker_track_id, human_locked)
                for segment_id, speaker_track_id, human_locked in locked_segments
            }
            expected_segment_state = {
                segment_id: (track_id, original_human_locks[segment_id])
                for segment_id, track_id in original_track_by_segment.items()
            }
            locked_track_state = {
                track_id: (version, label_source, display_name, voice_profile_id)
                for track_id, version, label_source, display_name, voice_profile_id in locked_tracks
            }
            if locked_segment_state != expected_segment_state or locked_track_state != original_track_state:
                raise MeetingAIInputChanged("speaker mapping changed during global reclustering")
            meeting = locked_meeting
            current = locked_job

            global_clusters: dict[str, list[MeetingTranscriptSegment]] = defaultdict(list)
            for segment_id, cluster_key in global_plan.segment_cluster_keys.items():
                member = segment_by_id.get(segment_id)
                if member is None or not cluster_key or len(cluster_key) > 128:
                    raise MeetingAIOutputInvalid("global speaker cluster mapping is invalid")
                global_clusters[cluster_key].append(member)
            global_target_by_cluster: dict[str, str] = {}
            claimed_global_targets: set[str] = set()
            for cluster_key, members in sorted(global_clusters.items()):
                available_ids = [
                    member.speaker_track_id
                    for member in members
                    if member.speaker_track_id
                    and member.speaker_track_id not in claimed_global_targets
                    and member.speaker_track_id not in manual_mapping_track_ids
                    and member.speaker_track_id in track_by_id
                    and track_by_id[member.speaker_track_id].label_source not in protected_sources
                ]
                target_id = Counter(available_ids).most_common(1)[0][0] if available_ids else ""
                if not target_id:
                    target = MeetingSpeakerTrack(
                        meeting_id=job.meeting_id,
                        track_key=f"global-{uuid4().hex}",
                        anonymous_label=f"发言人 {len(track_by_id) + 1}",
                    )
                    track_by_id[target.id] = target
                    tracks.append(target)
                    target_id = target.id
                claimed_global_targets.add(target_id)
                global_target_by_cluster[cluster_key] = target_id

            for member in segments:
                source_id = member.speaker_track_id
                cluster_key = global_plan.segment_cluster_keys.get(member.id)
                target_id = (
                    global_target_by_cluster.get(cluster_key, "")
                    if cluster_key is not None
                    else global_plan.track_targets.get(source_id or "", "")
                )
                if not target_id or source_id == target_id:
                    continue
                if source_id is None and cluster_key is None:
                    continue
                source = track_by_id.get(source_id)
                target = track_by_id.get(target_id)
                if target is None or (source_id is not None and source is None):
                    raise MeetingAIOutputInvalid("global speaker recluster target is invalid")
                if (
                    member.human_locked
                    or member.id in manual_mapping_segment_ids
                    or source_id in manual_mapping_track_ids
                    or (source is not None and source.label_source in protected_sources)
                ):
                    protected_segment_ids.append(member.id)
                    continue
                member.speaker_track_id = target_id
                member.updated_at = utcnow()
                session.add(member)

            applied = [
                (original_track_by_segment[member.id], member.speaker_track_id, member.id)
                for member in segments
                if member.speaker_track_id is not None
                and original_track_by_segment[member.id] != member.speaker_track_id
            ]
            for member in segments:
                if original_track_by_segment[member.id] != member.speaker_track_id:
                    session.add(member)

            affected_track_ids = {
                track_id
                for source_id, target_id, _ in applied
                for track_id in (source_id, target_id)
                if track_id is not None
            }
            for track_id in affected_track_ids:
                track = track_by_id.get(track_id)
                if track is not None:
                    track.version += 1
                    track.updated_at = utcnow()
                    session.add(track)

            source_targets: dict[str, set[str]] = defaultdict(set)
            source_segments: dict[tuple[str, str], list[str]] = defaultdict(list)
            for segment in segments:
                source_id = original_track_by_segment.get(segment.id)
                target_id = segment.speaker_track_id
                if source_id is not None and target_id is not None:
                    source_targets[source_id].add(target_id)
                    source_segments[(source_id, target_id)].append(segment.id)

            merge_sources_by_target: dict[str, set[str]] = defaultdict(set)
            for source_id, target_ids in source_targets.items():
                if len(target_ids) != 1:
                    continue
                target_id = next(iter(target_ids))
                if source_id != target_id:
                    merge_sources_by_target[target_id].add(source_id)

            merges = [
                {
                    "source_track_ids": sorted(source_ids),
                    "target_track_id": target_id,
                    "segment_ids": [
                        segment_id
                        for source_id in sorted(source_ids)
                        for segment_id in source_segments[(source_id, target_id)]
                    ],
                }
                for target_id, source_ids in merge_sources_by_target.items()
                if source_ids
            ]
            splits = [
                {
                    "source_track_id": source_id,
                    "target_track_ids": sorted(target_ids),
                    "segment_ids_by_target": {
                        target_id: source_segments.get((source_id, target_id), []) for target_id in sorted(target_ids)
                    },
                }
                for source_id, target_ids in source_targets.items()
                if len(target_ids) > 1
            ]
            event_store = MeetingEventStore(session)
            for mapping in merges:
                await event_store.append(
                    job.meeting_id,
                    "speaker.track.merged",
                    {
                        "schema_version": "siq.meeting.speaker_mapping.v1",
                        "operation": "merge",
                        "automatic": True,
                        **mapping,
                    },
                )
            for mapping in splits:
                await event_store.append(
                    job.meeting_id,
                    "speaker.track.split",
                    {
                        "schema_version": "siq.meeting.speaker_mapping.v1",
                        "operation": "split",
                        "automatic": True,
                        **mapping,
                    },
                )

            next_version = (
                int(
                    (
                        await session.exec(
                            select(func.max(MeetingArtifact.version)).where(
                                MeetingArtifact.meeting_id == job.meeting_id,
                                MeetingArtifact.artifact_type == ArtifactType.SPEAKER_RECLUSTER.value,
                            )
                        )
                    ).one()
                    or 0
                )
                + 1
            )
            recluster_artifact = MeetingArtifact(
                meeting_id=job.meeting_id,
                artifact_type=ArtifactType.SPEAKER_RECLUSTER.value,
                version=next_version,
                state=ArtifactState.READY.value,
                content_json=encode_json(
                    {
                        "schema_version": "siq.meeting.speaker_recluster.v1",
                        "job_id": job.id,
                        "alignment_artifact_id": alignment_artifact.id,
                        "applied_segment_count": len(applied),
                        "human_protected_segment_ids": sorted(protected_segment_ids),
                        "conflicting_segment_ids": sorted(conflicting_segments),
                        "review_clusters": review_clusters,
                        "merges": merges,
                        "splits": splits,
                        "global_embedding_recluster": {
                            "policy_version": global_plan.policy_version,
                            "final_diarizer_ref": global_plan.final_diarizer_ref,
                            "observed_final_diarizer_ref": observed_diarizer_ref,
                            "validation_artifact_sha256": global_plan.validation_artifact_sha256,
                            "automatic_enabled": global_plan.automatic_enabled,
                            "authoritative_global_clustering": global_plan.authoritative_global_clustering,
                            "cluster_count": global_plan.cluster_count,
                            "clustered_segment_count": len(global_plan.segment_cluster_keys),
                            "encoder_ref": global_plan.encoder_ref,
                            "embedded_track_count": global_plan.embedded_track_count,
                            "selected_sample_count": global_plan.selected_sample_count,
                            "skipped_sample_count": global_plan.skipped_sample_count,
                            "degraded_reason": global_plan.degraded_reason,
                            "proposals": [
                                {
                                    "source_track_ids": list(proposal.source_track_ids),
                                    "target_track_id": proposal.target_track_id,
                                    "score": proposal.score,
                                    "auto_apply": proposal.auto_apply,
                                    "reason_code": proposal.reason_code,
                                }
                                for proposal in global_plan.proposals
                            ],
                        },
                    }
                ),
                input_from_ordinal=1,
                input_to_ordinal=job.input_watermark,
                transcript_revision=alignment_artifact.transcript_revision,
            )
            session.add(recluster_artifact)
            await session.flush()
            await event_store.append(
                job.meeting_id,
                "speaker.recluster.completed",
                {
                    "job_id": job.id,
                    "artifact_id": recluster_artifact.id,
                    "alignment_artifact_id": alignment_artifact.id,
                    "applied_segment_count": len(applied),
                    "protected_segment_count": len(protected_segment_ids),
                    "review_cluster_count": len(review_clusters),
                    "global_embedded_track_count": global_plan.embedded_track_count,
                    "global_proposal_count": len(global_plan.proposals),
                    "global_degraded_reason": global_plan.degraded_reason,
                },
            )
            decision_counts = Counter(
                {
                    "auto_merge": len(merges),
                    "auto_split": len(splits),
                    "review_proposal": sum(
                        1
                        for proposal in global_plan.proposals
                        if not proposal.auto_apply and proposal.reason_code != "PROTECTED_TRACK_CONFLICT"
                    ),
                    "protected_skip": (
                        sum(
                            1
                            for proposal in global_plan.proposals
                            if proposal.reason_code == "PROTECTED_TRACK_CONFLICT"
                        )
                        + len(review_clusters)
                        + len(set(protected_segment_ids))
                    ),
                }
            )
            if not any(decision_counts.values()):
                decision_counts["unchanged"] = 1
            if global_plan.degraded_reason:
                await event_store.append(
                    job.meeting_id,
                    "speaker.recluster.degraded",
                    {
                        "job_id": job.id,
                        "reason_code": global_plan.degraded_reason,
                    },
                )
            for result, count in sorted(decision_counts.items()):
                if count <= 0:
                    continue
                await event_store.append(
                    job.meeting_id,
                    f"speaker.recluster.{result}",
                    {
                        "job_id": job.id,
                        "count": count,
                    },
                )
            await self._requeue_voiceprint_matches_after_recluster(
                session,
                meeting,
                segments,
                list(track_by_id.values()),
                recluster_artifact,
            )
            await self._queue_final_minutes_after_recluster(session, current, meeting)
            self._mark_job_succeeded(current)
            session.add(current)
            await session.commit()
            record_meeting_counter(
                "speaker_recluster_run",
                "degraded" if global_plan.degraded_reason else "succeeded",
            )
            for result, count in decision_counts.items():
                record_meeting_counter("speaker_recluster_decision", result, amount=count)

    async def _process_speaker_recluster(self, job: MeetingJob) -> None:
        stop = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(job.id, stop))
        try:
            await self._complete_speaker_recluster(job)
        finally:
            stop.set()
            lease_valid = await heartbeat
            if not lease_valid and not await self._job_succeeded(job.id):
                raise MeetingAILeaseLost("speaker recluster lease expired")

    async def _requeue_voiceprint_matches_after_recluster(
        self,
        session: AsyncSession,
        meeting: MeetingSession,
        segments: list[MeetingTranscriptSegment],
        tracks: list[MeetingSpeakerTrack],
        recluster_artifact: MeetingArtifact,
    ) -> int:
        if not meeting.voiceprint_enabled:
            return 0
        now = utcnow()
        pending = list(
            (
                await session.exec(
                    select(MeetingJob).where(
                        MeetingJob.meeting_id == meeting.id,
                        MeetingJob.job_kind == MeetingJobKind.VOICEPRINT_MATCH.value,
                        MeetingJob.state.in_(
                            [
                                MeetingJobState.QUEUED.value,
                                MeetingJobState.LEASED.value,
                                MeetingJobState.RUNNING.value,
                                MeetingJobState.RETRY_WAIT.value,
                            ]
                        ),
                    ).with_for_update()
                )
            ).all()
        )
        for value in pending:
            value.state = MeetingJobState.CANCELLED.value
            value.public_error_code = "SPEAKER_RECLUSTER_SUPERSEDED"
            value.internal_diagnostic = None
            value.lease_owner = None
            value.lease_until = None
            value.updated_at = now
            session.add(value)

        active_track_ids = {segment.speaker_track_id for segment in segments if segment.speaker_track_id}
        event_store = MeetingEventStore(session)
        created = 0
        for track in sorted(tracks, key=lambda value: (value.created_at, value.id)):
            if (
                track.id not in active_track_ids
                or track.label_source != SpeakerLabelSource.ANONYMOUS.value
                or track.display_name is not None
                or track.voice_profile_id is not None
            ):
                continue
            key = f"{meeting.id}:voiceprint-match:{track.id}:recluster:{recluster_artifact.id}:v2"
            if await self._job_exists(session, key):
                continue
            derived = MeetingJob(
                meeting_id=meeting.id,
                job_kind=MeetingJobKind.VOICEPRINT_MATCH.value,
                idempotency_key=key,
                input_watermark=meeting.last_segment_ordinal,
                settings_version=meeting.settings_version,
                input_json=encode_json(
                    {
                        "schema_version": "meeting.voiceprint_match.input.v1",
                        "task": "voiceprint_match",
                        "speaker_track_id": track.id,
                        "source_recluster_artifact_id": recluster_artifact.id,
                    }
                ),
            )
            session.add(derived)
            await session.flush()
            await event_store.append(
                meeting.id,
                "voiceprint.match.queued",
                {
                    "job_id": derived.id,
                    "speaker_track_id": track.id,
                    "reason": "speaker_recluster_completed",
                    "source_recluster_artifact_id": recluster_artifact.id,
                },
            )
            created += 1
        return created

    async def _job_succeeded(self, job_id: str) -> bool:
        async with self.session_factory() as session:
            job = await session.get(MeetingJob, job_id)
            return bool(job is not None and job.state == MeetingJobState.SUCCEEDED.value)

    async def _queue_final_minutes_after_recluster(
        self,
        session: AsyncSession,
        source_job: MeetingJob,
        meeting: MeetingSession,
    ) -> MeetingJob | None:
        if not meeting.ai_enabled:
            meeting.postprocess_state = MeetingPostprocessState.SUCCEEDED.value
            meeting.updated_at = utcnow()
            session.add(meeting)
            return None
        setting = (
            await session.exec(
                select(MeetingModelSetting).where(
                    MeetingModelSetting.meeting_id == source_job.meeting_id,
                    MeetingModelSetting.settings_version == source_job.settings_version,
                )
            )
        ).first()
        if setting is None:
            raise MeetingAIConfigurationInvalid("meeting model setting version is missing")
        if setting.selection_mode == ModelSelectionMode.NONE.value:
            meeting.postprocess_state = MeetingPostprocessState.SUCCEEDED.value
            meeting.updated_at = utcnow()
            session.add(meeting)
            return None
        key = (
            f"{source_job.meeting_id}:final_minutes:{source_job.input_watermark}:"
            f"settings:{source_job.settings_version}:"
            f"prompt:{PROMPT_VERSIONS[MeetingJobKind.FINAL_MINUTES.value]}"
        )
        derived = (await session.exec(select(MeetingJob).where(MeetingJob.idempotency_key == key))).first()
        if derived is None:
            derived = MeetingJob(
                meeting_id=source_job.meeting_id,
                job_kind=MeetingJobKind.FINAL_MINUTES.value,
                idempotency_key=key,
                input_watermark=source_job.input_watermark,
                settings_version=source_job.settings_version,
            )
            session.add(derived)
            await session.flush()
            await MeetingEventStore(session).append(
                source_job.meeting_id,
                "postprocess.final_minutes.queued",
                {
                    "job_id": derived.id,
                    "source_job_id": source_job.id,
                    "input_watermark": source_job.input_watermark,
                },
            )
        return derived

    async def _complete_empty_correction(self, job: MeetingJob) -> None:
        async with self.session_factory() as session:
            current = await self._owned_running_job(session, job.id)
            if current is None:
                raise MeetingAILeaseLost("empty correction lease was lost")
            self._mark_job_succeeded(current)
            await MeetingEventStore(session).append(
                job.meeting_id,
                "meeting.ai.job.succeeded",
                {"job_id": job.id, "job_kind": job.job_kind, "empty_input": True},
            )
            await session.commit()

    async def _complete_skipped(self, job: MeetingJob) -> None:
        async with self.session_factory() as session:
            current = await self._owned_running_job(session, job.id)
            if current is None:
                raise MeetingAILeaseLost("skipped meeting AI lease was lost")
            artifact_type = (
                ArtifactType.ROLLING_MINUTES.value
                if job.job_kind == MeetingJobKind.ROLLING_MINUTES.value
                else ArtifactType.FINAL_MINUTES.value
            )
            if job.job_kind in MINUTES_JOB_KINDS:
                prepared = await self._prepared_artifact(session, current, artifact_type)
                if prepared is not None:
                    prepared.state = ArtifactState.FAILED.value
                    prepared.content_json = encode_json({"reason_code": "AI_DISABLED"})
                    prepared.updated_at = utcnow()
                    session.add(prepared)
            self._mark_job_succeeded(current)
            await MeetingEventStore(session).append(
                job.meeting_id,
                "meeting.ai.job.skipped",
                {"job_id": job.id, "job_kind": job.job_kind, "reason_code": "AI_DISABLED"},
            )
            await session.commit()

    @staticmethod
    def _empty_minutes(task: MeetingAITask) -> dict[str, Any]:
        return {
            "schema_version": (
                ROLLING_SCHEMA_VERSION if task == MeetingAITask.ROLLING_MINUTES else FINAL_SCHEMA_VERSION
            ),
            **({"temporary": True} if task == MeetingAITask.ROLLING_MINUTES else {}),
            "overview": "",
            "agenda_topics": [],
            "chapters": [],
            "decisions": [],
            "open_questions": [],
            "risks": [],
            "action_items": [],
            "speaker_viewpoints": [],
            "keywords": [],
        }

    async def _owned_running_job(self, session: AsyncSession, job_id: str) -> MeetingJob | None:
        return (
            await session.exec(
                select(MeetingJob).where(
                    MeetingJob.id == job_id,
                    MeetingJob.state == MeetingJobState.RUNNING.value,
                    MeetingJob.lease_owner == self.worker_id,
                )
            )
        ).first()

    @staticmethod
    def _mark_job_succeeded(job: MeetingJob) -> None:
        job.state = MeetingJobState.SUCCEEDED.value
        job.lease_owner = None
        job.lease_until = None
        job.public_error_code = None
        job.internal_diagnostic = None
        job.updated_at = utcnow()

    async def _fail_job(self, job_id: str, exc: BaseException) -> None:
        retryable = isinstance(exc, MeetingAIInputChanged) or isinstance(exc, MeetingHermesTargetUnavailable)
        if isinstance(exc, MeetingAIWorkerError):
            public_code = exc.public_code
            retryable = retryable or exc.retryable
        elif isinstance(exc, MeetingFinalizationError):
            public_code = exc.public_code
            retryable = exc.retryable
        elif isinstance(exc, MeetingHermesTargetUnavailable):
            public_code = exc.public_code
        elif isinstance(exc, MeetingHermesConfigurationError):
            public_code = MeetingHermesConfigurationError.public_code
        elif isinstance(exc, (MeetingHermesOutputError, MeetingHermesProtocolError)):
            public_code = exc.public_code
        else:
            public_code = "MEETING_AI_FAILED"
            retryable = True

        async with self.session_factory() as session:
            job = (
                await session.exec(
                    select(MeetingJob).where(
                        MeetingJob.id == job_id,
                        MeetingJob.lease_owner == self.worker_id,
                        MeetingJob.state.in_([MeetingJobState.LEASED.value, MeetingJobState.RUNNING.value]),
                    )
                )
            ).first()
            if job is None:
                return
            will_retry = retryable and job.attempt < job.max_attempts
            job.state = MeetingJobState.RETRY_WAIT.value if will_retry else MeetingJobState.FAILED.value
            job.lease_owner = None
            job.lease_until = utcnow() + timedelta(seconds=self.config.retry_delay_seconds) if will_retry else None
            job.public_error_code = public_code
            job.internal_diagnostic = _diagnostic(exc)
            job.updated_at = utcnow()
            session.add(job)
            if not will_retry and job.job_kind in MINUTES_JOB_KINDS:
                artifact_type = (
                    ArtifactType.ROLLING_MINUTES.value
                    if job.job_kind == MeetingJobKind.ROLLING_MINUTES.value
                    else ArtifactType.FINAL_MINUTES.value
                )
                prepared = await self._prepared_artifact(session, job, artifact_type)
                if prepared is not None:
                    prepared.state = ArtifactState.FAILED.value
                    prepared.updated_at = utcnow()
                    session.add(prepared)
            await MeetingEventStore(session).append(
                job.meeting_id,
                ("meeting.ai.job.retry_wait" if will_retry else "meeting.ai.job.failed"),
                {
                    "job_id": job.id,
                    "job_kind": job.job_kind,
                    "attempt": job.attempt,
                    "max_attempts": job.max_attempts,
                    "public_error_code": public_code,
                },
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                raise
            if job.job_kind == MeetingJobKind.SPEAKER_RECLUSTER.value:
                record_meeting_counter(
                    "speaker_recluster_run",
                    "retry_wait" if will_retry else "failed",
                )


__all__ = [
    "MeetingAIConfigurationInvalid",
    "MeetingAIInputChanged",
    "MeetingAILeaseLost",
    "MeetingAIOutputInvalid",
    "MeetingAIWorker",
    "MeetingAIWorkerConfig",
    "TranscriptInput",
]
