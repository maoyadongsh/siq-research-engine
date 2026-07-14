"""Persistence and API contracts for the isolated meeting-transcription domain.

The module intentionally contains no runtime integration with chat voice, the
primary-market meeting room, or Hermes global model settings. Importing it only
registers additive ``meeting_*`` SQLModel metadata.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field as PydanticField, model_validator
from sqlalchemy import CheckConstraint, Column, Index, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

# Register the referenced users table in SQLModel metadata without defining a
# relationship that could accidentally change authentication behavior.
from services.auth_service import User as _User  # noqa: F401

MEETING_SCHEMA_VERSION = "meeting.v1"
MEETING_EVENT_SCHEMA_VERSION = "meeting.event.v1"
MEETING_DIFF_SCHEMA_VERSION = "meeting.diff.v1"
MEETING_LEXICON_SCHEMA_VERSION = "meeting.lexicon.v1"


def utcnow() -> datetime:
    # Meeting DDL uses TIMESTAMP WITHOUT TIME ZONE. Persist canonical naive UTC
    # and add an explicit UTC zone only at external serialization boundaries.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id() -> str:
    return str(uuid4())


class StrEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class MeetingState(StrEnum):
    DRAFT = "draft"
    CONNECTING = "connecting"
    LIVE = "live"
    PAUSED = "paused"
    RECONNECTING = "reconnecting"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ARCHIVED = "archived"
    INTERRUPTED = "interrupted"
    DELETED = "deleted"


class MeetingSessionSort(StrEnum):
    STARTED_AT_DESC = "started_at_desc"
    STARTED_AT_ASC = "started_at_asc"
    UPDATED_AT_DESC = "updated_at_desc"


class MeetingPostprocessState(StrEnum):
    NOT_STARTED = "not_started"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AudioSource(StrEnum):
    MICROPHONE = "microphone"
    TAB = "tab"
    SYSTEM = "system"
    IMPORT = "import"


class ModelSelectionMode(StrEnum):
    NONE = "none"
    AUTO = "auto"
    PINNED = "pinned"


class ModelFallbackPolicy(StrEnum):
    DISABLED = "disabled"
    LOCAL_ONLY = "local_only"
    EXPLICIT_POLICY = "explicit_policy"


class SpeakerLabelSource(StrEnum):
    ANONYMOUS = "anonymous"
    MANUAL = "manual"
    VOICEPRINT_CONFIRMED = "voiceprint_confirmed"
    VOICEPRINT_AUTO = "voiceprint_auto"


class SpeakerRenameScope(StrEnum):
    SEGMENT = "segment"
    SPEAKER = "speaker"


class SegmentRevisionType(StrEnum):
    LLM_CORRECTION = "llm_correction"
    MANUAL = "manual"
    FINAL_ASR_REVIEW = "final_asr_review"
    REVERT = "revert"


class CorrectionEditIntent(StrEnum):
    ASR_ERROR = "asr_error"
    CONTENT_EDIT = "content_edit"
    UNKNOWN = "unknown"


class CorrectionErrorClass(StrEnum):
    LEXICAL = "lexical"
    ENTITY = "entity"
    PUNCTUATION = "punctuation"
    ITN = "itn"
    DELETION = "deletion"
    INSERTION = "insertion"
    REWRITE = "rewrite"


class CorrectionStatus(StrEnum):
    ACTIVE = "active"
    REVERTED = "reverted"
    EXCLUDED = "excluded"
    PROMOTED = "promoted"


class TermCandidateType(StrEnum):
    HOTWORD = "hotword"
    CONFUSION_PAIR = "confusion_pair"
    CONTEXT_TERM = "context_term"


class TermCandidateStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    PROMOTED = "promoted"
    DEPRECATED = "deprecated"


class LexiconEntryType(StrEnum):
    MANUAL = "manual"
    HOTWORD = "hotword"
    CONFUSION_PAIR = "confusion_pair"


class LexiconScope(StrEnum):
    CURRENT_MEETING = "current_meeting"
    USER_FUTURE_MEETINGS = "user_future_meetings"


class LexiconEntryStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    DEPRECATED = "deprecated"
    DELETED = "deleted"


class VoiceProfileStatus(StrEnum):
    COLLECTING = "collecting"
    ACTIVE = "active"
    PAUSED = "paused"
    REVOKED = "revoked"
    DELETED = "deleted"


class VoiceMatchDecision(StrEnum):
    SUGGESTED = "suggested"
    AUTO_APPLIED = "auto_applied"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    UNDONE = "undone"


class ArtifactType(StrEnum):
    ROLLING_MINUTES = "rolling_minutes"
    FINAL_MINUTES = "final_minutes"
    VIEWPOINTS = "viewpoints"
    DECISIONS = "decisions"
    ACTION_ITEMS = "action_items"
    CHAPTERS = "chapters"
    FINAL_TRANSCRIPT_ALIGNMENT = "final_transcript_alignment"
    SPEAKER_RECLUSTER = "speaker_recluster"
    EXPORT = "export"


class ArtifactState(StrEnum):
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"
    STALE = "stale"


class MeetingJobKind(StrEnum):
    CORRECTION = "correction"
    ROLLING_MINUTES = "rolling_minutes"
    FINAL_TRANSCRIPT = "final_transcript"
    SPEAKER_RECLUSTER = "speaker_recluster"
    FINAL_MINUTES = "final_minutes"
    VOICEPRINT_ENROLL = "voiceprint_enroll"
    VOICEPRINT_MATCH = "voiceprint_match"
    EXPORT = "export"
    DELETE = "delete"


class MeetingJobState(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AudioChunkState(StrEnum):
    RECEIVED = "received"
    VERIFIED = "verified"
    PACKED = "packed"
    DELETED = "deleted"


class MeetingExportFormat(StrEnum):
    MARKDOWN = "markdown"
    TXT = "txt"
    SRT = "srt"
    VTT = "vtt"
    JSON = "json"
    DOCX = "docx"
    PDF = "pdf"


class MeetingExportContent(StrEnum):
    TRANSCRIPT = "transcript"
    MINUTES = "minutes"


class MeetingExportTranscriptSource(StrEnum):
    DISPLAY = "display"
    ASR = "asr"


class MeetingSession(SQLModel, table=True):
    __tablename__ = "meeting_sessions"
    __table_args__ = (
        CheckConstraint("settings_version >= 1", name="ck_meeting_sessions_settings_version"),
        CheckConstraint("version >= 1", name="ck_meeting_sessions_version"),
        CheckConstraint(
            "stopped_at IS NULL OR started_at IS NULL OR stopped_at >= started_at",
            name="ck_meeting_sessions_time_order",
        ),
        CheckConstraint(
            "((ai_enabled AND selection_mode IN ('auto', 'pinned')) OR "
            "((NOT ai_enabled) AND selection_mode = 'none' AND requested_model_ref IS NULL))",
            name="ck_meeting_sessions_ai_selection",
        ),
        CheckConstraint(
            "selection_mode <> 'pinned' OR requested_model_ref IS NOT NULL",
            name="ck_meeting_sessions_pinned_model",
        ),
        Index("ix_meeting_sessions_owner_created", "owner_user_id", "created_at"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    owner_user_id: int = Field(foreign_key="users.id", index=True)
    title: str = Field(default="未命名会议", min_length=1, max_length=200)
    language: str = Field(default="zh-CN", max_length=32)
    state: str = Field(default=MeetingState.DRAFT.value, max_length=24, index=True)
    postprocess_state: str = Field(default=MeetingPostprocessState.NOT_STARTED.value, max_length=24)
    audio_source: str = Field(default=AudioSource.MICROPHONE.value, max_length=24)
    voiceprint_enabled: bool = Field(default=False)
    ai_enabled: bool = Field(default=False)
    selection_mode: str = Field(default=ModelSelectionMode.NONE.value, max_length=16)
    requested_model_ref: str | None = Field(default=None, max_length=255)
    fallback_policy: str = Field(default=ModelFallbackPolicy.DISABLED.value, max_length=32)
    settings_version: int = Field(default=1)
    version: int = Field(default=1)
    stream_epoch: int = Field(default=0)
    last_audio_sequence: int = Field(default=-1)
    last_segment_ordinal: int = Field(default=0)
    active_lexicon_version: int | None = Field(default=None)
    started_at: datetime | None = Field(default=None)
    stopped_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


class MeetingStreamLease(SQLModel, table=True):
    __tablename__ = "meeting_stream_leases"
    __table_args__ = (
        UniqueConstraint("meeting_id", name="uq_meeting_stream_lease_meeting"),
        CheckConstraint("stream_epoch >= 0", name="ck_meeting_stream_lease_epoch"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    meeting_id: str = Field(foreign_key="meeting_sessions.id", index=True, max_length=36)
    stream_epoch: int = Field(default=0)
    connection_id: str = Field(max_length=64)
    owner_user_id: int = Field(foreign_key="users.id", index=True)
    lease_until: datetime
    last_acked_sequence: int = Field(default=-1)
    updated_at: datetime = Field(default_factory=utcnow)


class MeetingAudioChunk(SQLModel, table=True):
    __tablename__ = "meeting_audio_chunks"
    __table_args__ = (
        UniqueConstraint("meeting_id", "stream_epoch", "sequence", name="uq_meeting_audio_chunk_sequence"),
        CheckConstraint("sequence >= 0", name="ck_meeting_audio_chunk_sequence"),
        CheckConstraint("start_ms >= 0 AND duration_ms > 0", name="ck_meeting_audio_chunk_time"),
        CheckConstraint("byte_size > 0", name="ck_meeting_audio_chunk_size"),
        Index("ix_meeting_audio_chunks_timeline", "meeting_id", "start_ms"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    meeting_id: str = Field(foreign_key="meeting_sessions.id", index=True, max_length=36)
    stream_epoch: int
    sequence: int
    start_ms: int
    duration_ms: int
    storage_key: str = Field(max_length=500)
    sha256: str = Field(min_length=64, max_length=64)
    byte_size: int
    codec: str = Field(default="pcm_s16le", max_length=32)
    sample_rate: int = Field(default=16000)
    channels: int = Field(default=1)
    state: str = Field(default=AudioChunkState.RECEIVED.value, max_length=24)
    created_at: datetime = Field(default_factory=utcnow)


class MeetingSpeakerTrack(SQLModel, table=True):
    __tablename__ = "meeting_speaker_tracks"
    __table_args__ = (
        UniqueConstraint("meeting_id", "track_key", name="uq_meeting_speaker_track_key"),
        CheckConstraint("version >= 1", name="ck_meeting_speaker_track_version"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    meeting_id: str = Field(foreign_key="meeting_sessions.id", index=True, max_length=36)
    track_key: str = Field(max_length=128)
    anonymous_label: str = Field(max_length=100)
    display_name: str | None = Field(default=None, max_length=100)
    label_source: str = Field(default=SpeakerLabelSource.ANONYMOUS.value, max_length=32)
    voice_profile_id: str | None = Field(default=None, index=True, max_length=36)
    match_confidence: float | None = Field(default=None, ge=0, le=1)
    version: int = Field(default=1)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class MeetingTranscriptSegment(SQLModel, table=True):
    __tablename__ = "meeting_transcript_segments"
    __table_args__ = (
        UniqueConstraint("meeting_id", "ordinal", name="uq_meeting_transcript_ordinal"),
        UniqueConstraint("meeting_id", "provider_segment_key", name="uq_meeting_transcript_provider_key"),
        CheckConstraint("ordinal > 0", name="ck_meeting_transcript_ordinal"),
        CheckConstraint("start_ms >= 0 AND end_ms >= start_ms", name="ck_meeting_transcript_time"),
        Index("ix_meeting_transcript_timeline", "meeting_id", "start_ms"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    meeting_id: str = Field(foreign_key="meeting_sessions.id", index=True, max_length=36)
    ordinal: int
    utterance_id: str = Field(max_length=128)
    provider_segment_key: str = Field(max_length=255)
    start_ms: int
    end_ms: int
    speaker_track_id: str | None = Field(default=None, foreign_key="meeting_speaker_tracks.id", index=True)
    raw_text: str = Field(sa_column=Column(Text, nullable=False))
    asr_final_text: str = Field(sa_column=Column(Text, nullable=False))
    normalized_text: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    asr_confidence: float | None = Field(default=None, ge=0, le=1)
    asr_provider: str = Field(max_length=100)
    asr_model: str = Field(max_length=200)
    asr_version: str = Field(max_length=100)
    hotword_version: int | None = Field(default=None)
    word_timestamps_json: str = Field(default="[]", sa_column=Column(Text, nullable=False))
    asr_metadata_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    overlap: bool = Field(default=False)
    noise_level: float | None = Field(default=None, ge=0, le=1)
    human_locked: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class MeetingSegmentRevision(SQLModel, table=True):
    __tablename__ = "meeting_segment_revisions"
    __table_args__ = (
        UniqueConstraint("segment_id", "revision_no", name="uq_meeting_segment_revision_no"),
        CheckConstraint("revision_no > 0", name="ck_meeting_segment_revision_no"),
        CheckConstraint("base_revision_no >= 0", name="ck_meeting_segment_base_revision_no"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    segment_id: str = Field(foreign_key="meeting_transcript_segments.id", index=True, max_length=36)
    revision_no: int
    revision_type: str = Field(max_length=32)
    text: str = Field(sa_column=Column(Text, nullable=False))
    base_revision_no: int
    reason_codes_json: str = Field(default="[]", sa_column=Column(Text, nullable=False))
    model_snapshot_id: str | None = Field(default=None, index=True, max_length=36)
    created_by: str = Field(max_length=64)
    created_at: datetime = Field(default_factory=utcnow)


class MeetingASRCorrectionEvent(SQLModel, table=True):
    __tablename__ = "meeting_asr_correction_events"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "idempotency_key", name="uq_meeting_correction_idempotency"),
        Index("ix_meeting_correction_owner_created", "owner_user_id", "created_at"),
        Index("ix_meeting_correction_segment", "segment_id", "result_revision_no"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    owner_user_id: int = Field(foreign_key="users.id", index=True)
    meeting_id: str = Field(foreign_key="meeting_sessions.id", index=True, max_length=36)
    segment_id: str = Field(foreign_key="meeting_transcript_segments.id", index=True, max_length=36)
    speaker_track_id: str | None = Field(default=None, index=True, max_length=36)
    voice_profile_id: str | None = Field(default=None, index=True, max_length=36)
    base_revision_no: int
    result_revision_no: int
    original_text: str = Field(sa_column=Column(Text, nullable=False))
    corrected_text: str = Field(sa_column=Column(Text, nullable=False))
    diff_ops_json: str = Field(sa_column=Column(Text, nullable=False))
    edit_intent: str = Field(max_length=24)
    error_class: str = Field(max_length=24)
    contribute_to_accuracy: bool = Field(default=False)
    asr_provider: str = Field(max_length=100)
    asr_model: str = Field(max_length=200)
    asr_version: str = Field(max_length=100)
    hotword_version: int | None = Field(default=None)
    audio_start_ms: int
    audio_end_ms: int
    status: str = Field(default=CorrectionStatus.ACTIVE.value, max_length=24, index=True)
    idempotency_key: str | None = Field(default=None, max_length=128)
    created_by: str = Field(max_length=64)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class MeetingTermCandidate(SQLModel, table=True):
    __tablename__ = "meeting_term_candidates"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id", "language", "canonical_term", "misrecognition",
            name="uq_meeting_term_candidate_identity",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_meeting_term_candidate_confidence"),
        Index("ix_meeting_term_candidate_owner_status", "owner_user_id", "status"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    owner_user_id: int = Field(foreign_key="users.id", index=True)
    canonical_term: str = Field(min_length=1, max_length=200)
    misrecognition: str = Field(default="", max_length=200)
    language: str = Field(default="zh-CN", max_length=32)
    candidate_type: str = Field(default=TermCandidateType.HOTWORD.value, max_length=32)
    source_count: int = Field(default=1, ge=0)
    distinct_meeting_count: int = Field(default=1, ge=0)
    confirmed_count: int = Field(default=0, ge=0)
    reverted_count: int = Field(default=0, ge=0)
    speaker_specific_candidate: str | None = Field(default=None, max_length=36)
    confidence: float = Field(default=0.5, ge=0, le=1)
    status: str = Field(default=TermCandidateStatus.PENDING.value, max_length=24, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class MeetingTermCandidateSource(SQLModel, table=True):
    __tablename__ = "meeting_term_candidate_sources"
    __table_args__ = (
        UniqueConstraint("candidate_id", "correction_event_id", name="uq_meeting_term_candidate_source"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    candidate_id: str = Field(foreign_key="meeting_term_candidates.id", index=True, max_length=36)
    correction_event_id: str = Field(
        foreign_key="meeting_asr_correction_events.id", index=True, max_length=36
    )
    meeting_id: str = Field(foreign_key="meeting_sessions.id", index=True, max_length=36)
    created_at: datetime = Field(default_factory=utcnow)


class MeetingLexiconEntry(SQLModel, table=True):
    __tablename__ = "meeting_lexicon_entries"
    __table_args__ = (
        CheckConstraint("weight >= 0 AND weight <= 10", name="ck_meeting_lexicon_entry_weight"),
        Index("ix_meeting_lexicon_owner_status", "owner_user_id", "language", "status"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    owner_user_id: int = Field(foreign_key="users.id", index=True)
    language: str = Field(default="zh-CN", max_length=32)
    canonical_term: str = Field(min_length=1, max_length=200)
    aliases_json: str = Field(default="[]", sa_column=Column(Text, nullable=False))
    misrecognitions_json: str = Field(default="[]", sa_column=Column(Text, nullable=False))
    entry_type: str = Field(default=LexiconEntryType.MANUAL.value, max_length=32)
    weight: float = Field(default=5.0, ge=0, le=10)
    scope: str = Field(default=LexiconScope.USER_FUTURE_MEETINGS.value, max_length=32)
    meeting_id: str | None = Field(default=None, foreign_key="meeting_sessions.id", index=True)
    speaker_voice_profile_id: str | None = Field(default=None, index=True, max_length=36)
    status: str = Field(default=LexiconEntryStatus.ACTIVE.value, max_length=24, index=True)
    source_candidate_id: str | None = Field(default=None, foreign_key="meeting_term_candidates.id", index=True)
    created_by: str = Field(max_length=64)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class MeetingLexiconVersion(SQLModel, table=True):
    __tablename__ = "meeting_lexicon_versions"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "language", "version", name="uq_meeting_lexicon_version"),
        CheckConstraint("version > 0", name="ck_meeting_lexicon_version_no"),
        Index("ix_meeting_lexicon_version_owner", "owner_user_id", "language", "created_at"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    owner_user_id: int = Field(foreign_key="users.id", index=True)
    meeting_id: str | None = Field(default=None, foreign_key="meeting_sessions.id", index=True)
    version: int
    language: str = Field(default="zh-CN", max_length=32)
    entries_hash: str = Field(min_length=64, max_length=64)
    entry_count: int = Field(default=0, ge=0)
    entries_json: str = Field(default="[]", sa_column=Column(Text, nullable=False))
    change_reason: str = Field(max_length=100)
    created_by: str = Field(max_length=64)
    created_at: datetime = Field(default_factory=utcnow)
    supersedes_version: int | None = Field(default=None)
    is_active: bool = Field(default=True, index=True)


class MeetingVoiceProfile(SQLModel, table=True):
    __tablename__ = "meeting_voice_profiles"
    __table_args__ = (
        CheckConstraint("scope = 'user_private'", name="ck_meeting_voice_profile_scope"),
        CheckConstraint("sample_count >= 0 AND effective_duration_ms >= 0", name="ck_meeting_voice_profile_quality"),
        Index("ix_meeting_voice_profile_owner_status", "owner_user_id", "status"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    owner_user_id: int = Field(foreign_key="users.id", index=True)
    display_name: str = Field(min_length=1, max_length=100)
    scope: str = Field(default="user_private", max_length=32)
    status: str = Field(default=VoiceProfileStatus.COLLECTING.value, max_length=24, index=True)
    encoder_name: str | None = Field(default=None, max_length=100)
    encoder_version: str | None = Field(default=None, max_length=100)
    encrypted_embedding: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    key_id: str | None = Field(default=None, max_length=100)
    sample_count: int = Field(default=0, ge=0)
    effective_duration_ms: int = Field(default=0, ge=0)
    quality_summary_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    deleted_at: datetime | None = Field(default=None)


class MeetingVoiceprintConsent(SQLModel, table=True):
    __tablename__ = "meeting_voiceprint_consents"
    __table_args__ = (
        CheckConstraint(
            "purpose = 'future_meeting_speaker_identification'",
            name="ck_meeting_voiceprint_consent_purpose",
        ),
        CheckConstraint("scope = 'user_private'", name="ck_meeting_voiceprint_consent_scope"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    voice_profile_id: str = Field(foreign_key="meeting_voice_profiles.id", index=True, max_length=36)
    actor_user_id: int = Field(foreign_key="users.id", index=True)
    subject_label: str = Field(max_length=100)
    purpose: str = Field(default="future_meeting_speaker_identification", max_length=64)
    scope: str = Field(default="user_private", max_length=32)
    policy_version: str = Field(max_length=64)
    source_meeting_id: str = Field(foreign_key="meeting_sessions.id", index=True, max_length=36)
    granted_at: datetime = Field(default_factory=utcnow)
    revoked_at: datetime | None = Field(default=None)
    metadata_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))


class MeetingVoiceprintMatch(SQLModel, table=True):
    __tablename__ = "meeting_voiceprint_matches"
    __table_args__ = (
        CheckConstraint("top1_score >= 0 AND top1_score <= 1", name="ck_meeting_voice_match_top1"),
        CheckConstraint("top1_top2_margin >= 0 AND top1_top2_margin <= 1", name="ck_meeting_voice_match_margin"),
        Index("ix_meeting_voice_match_meeting", "meeting_id", "created_at"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    meeting_id: str = Field(foreign_key="meeting_sessions.id", index=True, max_length=36)
    speaker_track_id: str = Field(foreign_key="meeting_speaker_tracks.id", index=True, max_length=36)
    voice_profile_id: str = Field(foreign_key="meeting_voice_profiles.id", index=True, max_length=36)
    encoder_version: str = Field(max_length=100)
    threshold_version: str = Field(max_length=100)
    top1_score: float = Field(ge=0, le=1)
    top1_top2_margin: float = Field(ge=0, le=1)
    effective_duration_ms: int = Field(ge=0)
    quality_grade: str = Field(max_length=24)
    decision: str = Field(default=VoiceMatchDecision.SUGGESTED.value, max_length=24, index=True)
    decided_by: str | None = Field(default=None, max_length=64)
    created_at: datetime = Field(default_factory=utcnow)
    decided_at: datetime | None = Field(default=None)


class MeetingModelSetting(SQLModel, table=True):
    __tablename__ = "meeting_model_settings"
    __table_args__ = (
        UniqueConstraint("meeting_id", "settings_version", name="uq_meeting_model_setting_version"),
        CheckConstraint("settings_version > 0", name="ck_meeting_model_setting_version"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    meeting_id: str = Field(foreign_key="meeting_sessions.id", index=True, max_length=36)
    settings_version: int
    selection_mode: str = Field(max_length=16)
    requested_model_ref: str | None = Field(default=None, max_length=255)
    fallback_policy: str = Field(default=ModelFallbackPolicy.DISABLED.value, max_length=32)
    effective_after_segment_ordinal: int = Field(default=0, ge=0)
    cloud_data_boundary_confirmed_at: datetime | None = Field(default=None)
    changed_by: str = Field(max_length=64)
    created_at: datetime = Field(default_factory=utcnow)


class MeetingModelSnapshot(SQLModel, table=True):
    __tablename__ = "meeting_model_snapshots"

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    meeting_id: str = Field(foreign_key="meeting_sessions.id", index=True, max_length=36)
    model_ref: str = Field(max_length=255)
    selection_mode: str = Field(max_length=16)
    resolved_provider: str = Field(max_length=100)
    resolved_model: str = Field(max_length=200)
    provider_locality: str = Field(max_length=16)
    hermes_target: str = Field(max_length=255)
    meeting_profile_version: str = Field(max_length=64)
    prompt_version: str = Field(max_length=64)
    schema_version: str = Field(default=MEETING_SCHEMA_VERSION, max_length=64)
    settings_version: int
    effective_after_segment_ordinal: int = Field(default=0)
    resolved_at: datetime = Field(default_factory=utcnow)


class MeetingArtifact(SQLModel, table=True):
    __tablename__ = "meeting_artifacts"
    __table_args__ = (
        UniqueConstraint("meeting_id", "artifact_type", "version", name="uq_meeting_artifact_version"),
        CheckConstraint("version > 0", name="ck_meeting_artifact_version"),
        Index("ix_meeting_artifact_meeting_state", "meeting_id", "state"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    meeting_id: str = Field(foreign_key="meeting_sessions.id", index=True, max_length=36)
    artifact_type: str = Field(max_length=32)
    version: int
    state: str = Field(default=ArtifactState.GENERATING.value, max_length=24, index=True)
    content_json: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    content_text: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    input_from_ordinal: int = Field(default=1)
    input_to_ordinal: int = Field(default=0)
    transcript_revision: int = Field(default=0)
    model_snapshot_id: str | None = Field(default=None, foreign_key="meeting_model_snapshots.id", index=True)
    supersedes_id: str | None = Field(default=None, foreign_key="meeting_artifacts.id")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class MeetingJob(SQLModel, table=True):
    __tablename__ = "meeting_jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_meeting_job_idempotency"),
        CheckConstraint("attempt >= 0 AND max_attempts > 0", name="ck_meeting_job_attempts"),
        Index("ix_meeting_jobs_claim", "state", "lease_until", "created_at"),
        Index("ix_meeting_jobs_meeting", "meeting_id", "created_at"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    meeting_id: str = Field(foreign_key="meeting_sessions.id", index=True, max_length=36)
    job_kind: str = Field(max_length=32)
    idempotency_key: str = Field(max_length=255)
    state: str = Field(default=MeetingJobState.QUEUED.value, max_length=24, index=True)
    attempt: int = Field(default=0)
    max_attempts: int = Field(default=3)
    lease_owner: str | None = Field(default=None, max_length=100)
    lease_until: datetime | None = Field(default=None)
    input_watermark: int = Field(default=0)
    settings_version: int = Field(default=1)
    model_snapshot_id: str | None = Field(default=None, foreign_key="meeting_model_snapshots.id", index=True)
    input_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    public_error_code: str | None = Field(default=None, max_length=64)
    internal_diagnostic: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class MeetingEvent(SQLModel, table=True):
    __tablename__ = "meeting_events"
    __table_args__ = (
        UniqueConstraint("meeting_id", "cursor", name="uq_meeting_event_cursor"),
        UniqueConstraint("event_id", name="uq_meeting_event_id"),
        CheckConstraint("cursor > 0", name="ck_meeting_event_cursor"),
        Index("ix_meeting_events_unpublished", "published_at", "created_at"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    meeting_id: str = Field(foreign_key="meeting_sessions.id", index=True, max_length=36)
    cursor: int
    event_id: str = Field(default_factory=new_id, max_length=36)
    event_type: str = Field(max_length=64, index=True)
    schema_version: str = Field(default=MEETING_EVENT_SCHEMA_VERSION, max_length=64)
    payload_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    trace_id: str | None = Field(default=None, max_length=64)
    created_at: datetime = Field(default_factory=utcnow)
    published_at: datetime | None = Field(default=None)


class MeetingStreamTicket(SQLModel, table=True):
    __tablename__ = "meeting_stream_tickets"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_meeting_stream_ticket_hash"),
        Index("ix_meeting_stream_ticket_expiry", "expires_at", "consumed_at"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    token_hash: str = Field(min_length=64, max_length=64)
    meeting_id: str = Field(foreign_key="meeting_sessions.id", index=True, max_length=36)
    owner_user_id: int = Field(foreign_key="users.id", index=True)
    stream_epoch: int = Field(ge=1)
    purpose: str = Field(default="meeting_audio_producer", max_length=64)
    origin: str = Field(max_length=500)
    expires_at: datetime
    consumed_at: datetime | None = Field(default=None)
    connection_id: str | None = Field(default=None, max_length=64)
    created_at: datetime = Field(default_factory=utcnow)


class MeetingIdempotencyRecord(SQLModel, table=True):
    """Replay record for HTTP writes.

    A bounded response snapshot may contain meeting text. Such records bind
    ``resource_id`` to the meeting aggregate so retention deletes them with the
    meeting; audio bytes are never stored here.
    """

    __tablename__ = "meeting_idempotency_records"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "idempotency_key", name="uq_meeting_http_idempotency"),
        Index("ix_meeting_idempotency_created", "created_at"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    owner_user_id: int = Field(foreign_key="users.id", index=True)
    idempotency_key: str = Field(max_length=128)
    operation: str = Field(max_length=128)
    request_hash: str = Field(min_length=64, max_length=64)
    resource_id: str | None = Field(default=None, max_length=64)
    response_status: int
    response_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=utcnow)


MEETING_TABLES = (
    MeetingSession,
    MeetingStreamLease,
    MeetingAudioChunk,
    MeetingSpeakerTrack,
    MeetingTranscriptSegment,
    MeetingSegmentRevision,
    MeetingASRCorrectionEvent,
    MeetingTermCandidate,
    MeetingTermCandidateSource,
    MeetingLexiconEntry,
    MeetingLexiconVersion,
    MeetingVoiceProfile,
    MeetingVoiceprintConsent,
    MeetingVoiceprintMatch,
    MeetingModelSetting,
    MeetingModelSnapshot,
    MeetingArtifact,
    MeetingJob,
    MeetingEvent,
    MeetingStreamTicket,
    MeetingIdempotencyRecord,
)


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, use_enum_values=True)


class ModelSelectionInput(APIModel):
    mode: ModelSelectionMode = ModelSelectionMode.NONE
    model_ref: str | None = PydanticField(default=None, max_length=255)
    fallback_policy: ModelFallbackPolicy = ModelFallbackPolicy.DISABLED
    cloud_data_boundary_confirmed: bool = False

    @model_validator(mode="after")
    def validate_selection(self) -> "ModelSelectionInput":
        if self.mode == ModelSelectionMode.PINNED and not self.model_ref:
            raise ValueError("model_ref is required for pinned selection")
        if self.mode == ModelSelectionMode.NONE and self.model_ref is not None:
            raise ValueError("model_ref must be omitted when mode is none")
        if self.mode == ModelSelectionMode.PINNED and self.fallback_policy != ModelFallbackPolicy.DISABLED:
            raise ValueError("pinned selection must use disabled fallback")
        return self


class MeetingCreateRequest(APIModel):
    title: str = PydanticField(default="未命名会议", min_length=1, max_length=200)
    language: str = PydanticField(default="zh-CN", min_length=2, max_length=32)
    audio_source: AudioSource = AudioSource.MICROPHONE
    voiceprint_enabled: bool = False
    ai_enabled: bool = False
    model_selection: ModelSelectionInput = PydanticField(default_factory=ModelSelectionInput)

    @model_validator(mode="after")
    def validate_ai_selection(self) -> "MeetingCreateRequest":
        if not self.ai_enabled and self.model_selection.mode != ModelSelectionMode.NONE:
            raise ValueError("AI-disabled meetings must use model selection mode none")
        if self.ai_enabled and self.model_selection.mode == ModelSelectionMode.NONE:
            raise ValueError("AI-enabled meetings require auto or pinned model selection")
        return self


class MeetingUpdateRequest(APIModel):
    title: str = PydanticField(min_length=1, max_length=200)
    expected_version: int = PydanticField(ge=1)


class MeetingSessionResponse(APIModel):
    id: str
    owner_user_id: int
    title: str
    language: str
    state: MeetingState
    postprocess_state: MeetingPostprocessState
    audio_source: AudioSource
    voiceprint_enabled: bool
    ai_enabled: bool
    selection_mode: ModelSelectionMode
    requested_model_ref: str | None
    fallback_policy: ModelFallbackPolicy
    settings_version: int
    version: int
    stream_epoch: int
    last_audio_sequence: int
    last_segment_ordinal: int
    active_lexicon_version: int | None
    started_at: datetime | None
    stopped_at: datetime | None
    created_at: datetime
    updated_at: datetime
    speaker_count: int | None = None
    model_label: str | None = None
    model_locality: str | None = None


class MeetingSessionPage(APIModel):
    items: list[MeetingSessionResponse]
    total: int
    offset: int
    limit: int


class MeetingActionResponse(APIModel):
    session: MeetingSessionResponse
    idempotent: bool = False
    event_cursor: int | None = None


class MeetingStopResponse(MeetingActionResponse):
    finalization_path: Literal["stream_gateway", "rest_fallback", "already_stopped"]
    audio_status: Literal["pending", "available", "unavailable"]


class MeetingCapabilitiesResponse(APIModel):
    schema_version: str = MEETING_SCHEMA_VERSION
    enabled: bool
    configuration_errors: list[str] = PydanticField(default_factory=list)
    audio: dict[str, Any]
    asr: dict[str, Any]
    correction_learning: dict[str, Any]
    voiceprint: dict[str, Any]
    ai: dict[str, Any]
    recording_import: dict[str, Any]
    limits: dict[str, int]
    supported_audio_sources: list[AudioSource]


class MeetingModelDescriptor(APIModel):
    model_ref: str
    label: str
    provider_label: str
    locality: str
    configured: bool
    available: bool
    is_default: bool = False
    capabilities: list[str]
    context_window: int | None = None
    data_boundary: str
    reason_code: str | None = None
    checked_at: datetime


class MeetingModelCatalogResponse(APIModel):
    schema_version: str = MEETING_SCHEMA_VERSION
    purpose: str = "meeting_postprocess"
    items: list[MeetingModelDescriptor]


class StableSegmentInput(APIModel):
    utterance_id: str = PydanticField(min_length=1, max_length=128)
    provider_segment_key: str = PydanticField(min_length=1, max_length=255)
    start_ms: int = PydanticField(ge=0)
    end_ms: int = PydanticField(ge=0)
    speaker_track_id: str | None = None
    raw_text: str = PydanticField(min_length=1)
    asr_final_text: str = PydanticField(min_length=1)
    normalized_text: str | None = None
    asr_confidence: float | None = PydanticField(default=None, ge=0, le=1)
    asr_provider: str = PydanticField(min_length=1, max_length=100)
    asr_model: str = PydanticField(min_length=1, max_length=200)
    asr_version: str = PydanticField(min_length=1, max_length=100)
    hotword_version: int | None = PydanticField(default=None, ge=1)
    overlap: bool = False
    noise_level: float | None = PydanticField(default=None, ge=0, le=1)
    word_timestamps: list[dict[str, Any]] = PydanticField(default_factory=list, max_length=10_000)
    asr_metadata: dict[str, Any] = PydanticField(default_factory=dict)

    @model_validator(mode="after")
    def validate_timestamps(self) -> "StableSegmentInput":
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms must be greater than or equal to start_ms")
        return self


class TranscriptSegmentResponse(APIModel):
    id: str
    meeting_id: str
    ordinal: int
    utterance_id: str
    provider_segment_key: str
    start_ms: int
    end_ms: int
    speaker_track_id: str | None
    speaker_label: str | None = None
    raw_text: str
    asr_final_text: str
    normalized_text: str | None
    display_text: str
    current_revision_no: int
    display_layer: str
    asr_confidence: float | None
    asr_provider: str
    asr_model: str
    asr_version: str
    hotword_version: int | None
    word_timestamps: list[dict[str, Any]]
    overlap: bool
    noise_level: float | None
    human_locked: bool
    created_at: datetime
    updated_at: datetime


class TranscriptPage(APIModel):
    items: list[TranscriptSegmentResponse]
    next_ordinal: int | None


class SpeakerTrackResponse(APIModel):
    id: str
    meeting_id: str
    track_key: str
    anonymous_label: str
    display_name: str | None
    resolved_label: str
    label_source: SpeakerLabelSource
    voice_profile_id: str | None
    match_confidence: float | None
    version: int
    created_at: datetime
    updated_at: datetime


class SpeakerRenameRequest(APIModel):
    display_name: str = PydanticField(min_length=1, max_length=100)
    expected_version: int = PydanticField(ge=1)

    @model_validator(mode="after")
    def validate_display_name(self) -> "SpeakerRenameRequest":
        if not self.display_name.strip():
            raise ValueError("display_name cannot be blank")
        return self


class SegmentSpeakerRenameRequest(APIModel):
    display_name: str = PydanticField(min_length=1, max_length=100)
    scope: SpeakerRenameScope
    expected_speaker_version: int = PydanticField(ge=1)

    @model_validator(mode="after")
    def validate_display_name(self) -> "SegmentSpeakerRenameRequest":
        if not self.display_name.strip():
            raise ValueError("display_name cannot be blank")
        return self


class SegmentSpeakerRenameResponse(APIModel):
    operation: Literal["rename_segment", "rename_speaker"]
    scope: SpeakerRenameScope
    segment: TranscriptSegmentResponse
    tracks: list[SpeakerTrackResponse]
    affected_segment_count: int = PydanticField(ge=1)
    event_id: str
    event_cursor: int


class SpeakerMergeRequest(APIModel):
    source_track_ids: list[str] = PydanticField(min_length=1, max_length=63)
    expected_versions: dict[str, int] = PydanticField(min_length=2, max_length=64)

    @model_validator(mode="after")
    def validate_tracks(self) -> "SpeakerMergeRequest":
        if len(set(self.source_track_ids)) != len(self.source_track_ids):
            raise ValueError("source_track_ids must be unique")
        if any(not value.strip() for value in self.source_track_ids):
            raise ValueError("source_track_ids cannot contain blank identifiers")
        if any(version < 1 for version in self.expected_versions.values()):
            raise ValueError("expected speaker versions must be positive")
        return self


class SpeakerSplitRequest(APIModel):
    segment_ids: list[str] = PydanticField(min_length=1, max_length=10_000)
    expected_version: int = PydanticField(ge=1)
    display_name: str | None = PydanticField(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_segments(self) -> "SpeakerSplitRequest":
        if len(set(self.segment_ids)) != len(self.segment_ids):
            raise ValueError("segment_ids must be unique")
        if any(not value.strip() for value in self.segment_ids):
            raise ValueError("segment_ids cannot contain blank identifiers")
        return self


class SpeakerMappingResponse(APIModel):
    operation: str
    meeting_id: str
    source_track_ids: list[str]
    target_track_ids: list[str]
    segment_ids: list[str]
    event_id: str
    event_cursor: int
    tracks: list[SpeakerTrackResponse]


class CandidateTermInput(APIModel):
    canonical_term: str = PydanticField(min_length=1, max_length=200)
    misrecognition: str = PydanticField(default="", max_length=200)
    promote_now: bool = False


class SegmentCorrectionRequest(APIModel):
    text: str = PydanticField(min_length=1)
    expected_revision: int = PydanticField(ge=0)
    edit_intent: CorrectionEditIntent = CorrectionEditIntent.UNKNOWN
    contribute_to_accuracy: bool = False
    candidate_terms: list[CandidateTermInput] = PydanticField(default_factory=list, max_length=20)


class SegmentRevertRequest(APIModel):
    revision_no: int = PydanticField(ge=0)
    expected_revision: int = PydanticField(ge=1)


class DiffOperation(APIModel):
    op: str
    original_start: int
    original_end: int
    corrected_start: int
    corrected_end: int
    original: str
    corrected: str


class SegmentRevisionResponse(APIModel):
    id: str
    segment_id: str
    revision_no: int
    revision_type: SegmentRevisionType
    text: str
    base_revision_no: int
    reason_codes: list[str]
    created_by: str
    created_at: datetime


class CorrectionFeedbackResponse(APIModel):
    id: str
    meeting_id: str
    segment_id: str
    speaker_track_id: str | None
    voice_profile_id: str | None
    base_revision_no: int
    result_revision_no: int
    original_text: str
    corrected_text: str
    diff_ops: list[DiffOperation]
    edit_intent: CorrectionEditIntent
    error_class: CorrectionErrorClass
    contribute_to_accuracy: bool
    status: CorrectionStatus
    asr_provider: str
    asr_model: str
    asr_version: str
    hotword_version: int | None
    audio_start_ms: int
    audio_end_ms: int
    created_at: datetime
    updated_at: datetime


class SegmentCorrectionResponse(APIModel):
    segment: TranscriptSegmentResponse
    revision: SegmentRevisionResponse
    feedback: CorrectionFeedbackResponse
    candidate_ids: list[str] = PydanticField(default_factory=list)


class TermCandidateResponse(APIModel):
    id: str
    canonical_term: str
    misrecognition: str
    language: str
    candidate_type: TermCandidateType
    source_count: int
    distinct_meeting_count: int
    confirmed_count: int
    reverted_count: int
    confidence: float
    status: TermCandidateStatus
    created_at: datetime
    updated_at: datetime


class LexiconEntryCreateRequest(APIModel):
    canonical_term: str = PydanticField(min_length=1, max_length=200)
    aliases: list[str] = PydanticField(default_factory=list, max_length=20)
    misrecognitions: list[str] = PydanticField(default_factory=list, max_length=20)
    language: str = PydanticField(default="zh-CN", max_length=32)
    entry_type: LexiconEntryType = LexiconEntryType.MANUAL
    weight: float = PydanticField(default=5.0, ge=0, le=10)
    scope: LexiconScope = LexiconScope.USER_FUTURE_MEETINGS
    meeting_id: str | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> "LexiconEntryCreateRequest":
        if self.scope == LexiconScope.CURRENT_MEETING and not self.meeting_id:
            raise ValueError("meeting_id is required for current_meeting scope")
        if self.scope == LexiconScope.USER_FUTURE_MEETINGS and self.meeting_id is not None:
            raise ValueError("meeting_id must be omitted for user_future_meetings scope")
        return self


class LexiconEntryUpdateRequest(APIModel):
    weight: float | None = PydanticField(default=None, ge=0, le=10)
    status: LexiconEntryStatus | None = None
    scope: LexiconScope | None = None


class LexiconEntryResponse(APIModel):
    id: str
    language: str
    canonical_term: str
    aliases: list[str]
    misrecognitions: list[str]
    entry_type: LexiconEntryType
    weight: float
    scope: LexiconScope
    meeting_id: str | None
    status: LexiconEntryStatus
    source_candidate_id: str | None
    created_at: datetime
    updated_at: datetime


class LexiconVersionResponse(APIModel):
    id: str
    meeting_id: str | None
    version: int
    language: str
    entries_hash: str
    entry_count: int
    change_reason: str
    supersedes_version: int | None
    is_active: bool
    created_at: datetime


class LexiconResponse(APIModel):
    schema_version: str = MEETING_LEXICON_SCHEMA_VERSION
    language: str
    active_version: LexiconVersionResponse | None
    entries: list[LexiconEntryResponse]


class VoiceProfileCreateRequest(APIModel):
    display_name: str = PydanticField(min_length=1, max_length=100)


class VoiceProfileResponse(APIModel):
    id: str
    display_name: str
    scope: str
    status: VoiceProfileStatus
    encoder_name: str | None
    encoder_version: str | None
    sample_count: int
    effective_duration_ms: int
    quality_summary: dict[str, Any]
    consent_active: bool = False
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class VoiceprintEnrollmentRequest(APIModel):
    consent_accepted: bool
    policy_version: str = PydanticField(min_length=1, max_length=64)
    voice_profile_id: str
    source_track_id: str

    @model_validator(mode="after")
    def require_consent(self) -> "VoiceprintEnrollmentRequest":
        if not self.consent_accepted:
            raise ValueError("explicit voiceprint consent is required")
        return self


class VoiceprintEnrollmentResponse(APIModel):
    voice_profile: VoiceProfileResponse
    consent_id: str
    job_id: str


class VoiceMatchDecisionRequest(APIModel):
    decision: VoiceMatchDecision

    @model_validator(mode="after")
    def validate_decision(self) -> "VoiceMatchDecisionRequest":
        if self.decision not in {
            VoiceMatchDecision.CONFIRMED,
            VoiceMatchDecision.REJECTED,
            VoiceMatchDecision.UNDONE,
        }:
            raise ValueError("decision must be confirmed, rejected, or undone")
        return self


class VoiceMatchResponse(APIModel):
    id: str
    meeting_id: str
    speaker_track_id: str
    voice_profile_id: str
    encoder_version: str
    threshold_version: str
    top1_score: float
    top1_top2_margin: float
    effective_duration_ms: int
    quality_grade: str
    decision: VoiceMatchDecision
    decided_by: str | None
    created_at: datetime
    decided_at: datetime | None


class ModelSelectionUpdateRequest(APIModel):
    expected_settings_version: int = PydanticField(ge=1)
    selection: ModelSelectionInput


class ModelSettingResponse(APIModel):
    meeting_id: str
    settings_version: int
    selection_mode: ModelSelectionMode
    requested_model_ref: str | None
    fallback_policy: ModelFallbackPolicy
    effective_after_segment_ordinal: int
    cloud_data_boundary_confirmed_at: datetime | None
    created_at: datetime


class ArtifactResponse(APIModel):
    id: str
    meeting_id: str
    artifact_type: ArtifactType
    version: int
    state: ArtifactState
    content_json: dict[str, Any] | list[Any] | None
    content_text: str | None
    input_from_ordinal: int
    input_to_ordinal: int
    transcript_revision: int
    model_snapshot_id: str | None
    supersedes_id: str | None
    created_at: datetime
    updated_at: datetime


class ArtifactRegenerateRequest(APIModel):
    expected_settings_version: int = PydanticField(ge=1)


class MeetingExportCreateRequest(APIModel):
    format: MeetingExportFormat
    content: MeetingExportContent = MeetingExportContent.TRANSCRIPT
    transcript_source: MeetingExportTranscriptSource = MeetingExportTranscriptSource.DISPLAY
    artifact_id: str | None = PydanticField(default=None, max_length=36)
    artifact_version: int | None = PydanticField(default=None, ge=1)

    @model_validator(mode="after")
    def validate_export_selection(self) -> "MeetingExportCreateRequest":
        if self.content == MeetingExportContent.TRANSCRIPT:
            if self.artifact_id is not None or self.artifact_version is not None:
                raise ValueError("artifact selection is only valid for minutes exports")
        else:
            if self.format not in {
                MeetingExportFormat.MARKDOWN,
                MeetingExportFormat.JSON,
                MeetingExportFormat.DOCX,
                MeetingExportFormat.PDF,
            }:
                raise ValueError("minutes can only be exported as markdown, JSON, DOCX, or PDF")
            if self.artifact_id is None and self.artifact_version is None:
                raise ValueError("minutes exports require artifact_id or artifact_version")
        if self.format in {MeetingExportFormat.SRT, MeetingExportFormat.VTT} and (
            self.content != MeetingExportContent.TRANSCRIPT
        ):
            raise ValueError("subtitle formats require transcript content")
        return self


class MeetingExportResponse(APIModel):
    id: str
    meeting_id: str
    format: MeetingExportFormat
    content: MeetingExportContent
    transcript_source: MeetingExportTranscriptSource
    state: str
    job_id: str
    artifact_id: str | None = None
    artifact_version: int | None = None
    filename: str | None = None
    media_type: str | None = None
    byte_size: int | None = None
    sha256: str | None = None
    download_url: str | None = None
    download_expires_at: datetime | None = None
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime


class MeetingExportTicketResponse(APIModel):
    schema_version: str = "siq.meeting.export_ticket.v1"
    export_id: str
    meeting_id: str
    download_url: str
    expires_at: datetime


class JobResponse(APIModel):
    id: str
    meeting_id: str
    job_kind: MeetingJobKind
    state: MeetingJobState
    attempt: int
    max_attempts: int
    input_watermark: int
    settings_version: int
    model_snapshot_id: str | None
    public_error_code: str | None
    created_at: datetime
    updated_at: datetime


class MeetingEventResponse(APIModel):
    meeting_id: str
    cursor: int
    event_id: str
    event_type: str
    type: str
    schema_version: str
    payload: dict[str, Any]
    trace_id: str | None
    created_at: datetime
    emitted_at: datetime


class MeetingEventPage(APIModel):
    items: list[MeetingEventResponse]
    next_cursor: int | None


class AudioManifestResponse(APIModel):
    schema_version: str = MEETING_SCHEMA_VERSION
    meeting_id: str
    chunk_count: int
    byte_size: int
    duration_ms: int
    last_sequence: int
    gaps: list[dict[str, int]]
    formats: list[dict[str, Any]]


class StreamTicketResponse(APIModel):
    schema_version: str = "siq.meeting.stream_ticket.v1"
    ticket: str
    meeting_id: str
    stream_epoch: int
    last_acked_sequence: int = PydanticField(ge=-1)
    capture_offset_ms: int = PydanticField(ge=0)
    reconnect_window_seconds: int = PydanticField(ge=1)
    purpose: str = "meeting_audio_producer"
    expires_at: datetime
    ws_url: str
    protocol: str = "siq.meeting.stream.v1"


class AudioPlaybackTicketResponse(APIModel):
    schema_version: str = "siq.meeting.audio_ticket.v1"
    ticket: str
    meeting_id: str
    purpose: str = "meeting_audio_playback"
    expires_at: datetime
    audio_url: str
