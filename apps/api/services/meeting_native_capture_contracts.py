"""Persistence and API contracts for the optional iOS native capture adapter.

These contracts describe server ingest only. They do not imply that a browser,
PWA, or WebView can continue recording while iOS suspends JavaScript.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field as PydanticField, field_validator, model_validator
from sqlalchemy import BigInteger, CheckConstraint, Column, Index, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from services.auth_service import User as _User  # noqa: F401
from services.meeting_contracts import (
    APIModel,
    MeetingAudioChunk as _MeetingAudioChunk,  # noqa: F401
    MeetingJob as _MeetingJob,  # noqa: F401
    MeetingPostprocessState,
    MeetingSession as _MeetingSession,  # noqa: F401
    new_id,
    utcnow,
)

NATIVE_CAPTURE_SCHEMA_VERSION = "siq.meeting.native_capture.v1"
NATIVE_CAPTURE_INT64_MAX = 9_223_372_036_854_775_807
NATIVE_CAPTURE_INT32_MAX = 2_147_483_647
CAPTURE_TOKEN_PURPOSE = "meeting_native_capture"
CAPTURE_TOKEN_SCOPES = (
    "batch:write",
    "checkpoint:read",
    "capture:seal",
)


class NativeCaptureState(str, Enum):
    ACTIVE = "active"
    SEALED = "sealed"
    REVOKED = "revoked"


class NativeCaptureEpochState(str, Enum):
    ACTIVE = "active"
    ROLLED_OVER = "rolled_over"
    SEALED = "sealed"


class NativeCapturePlaybackState(str, Enum):
    NOT_READY = "not_ready"
    PENDING_UPLOAD = "pending_upload"
    PENDING_PACKAGING = "pending_packaging"
    PACKAGING = "packaging"
    READY = "ready"
    FAILED = "failed"


class NativeCaptureFinalizationState(str, Enum):
    PENDING_UPLOAD = "pending_upload"
    QUEUED = "queued"
    PROCESSING = "processing"
    RETRY_WAIT = "retry_wait"
    READY = "ready"
    FAILED = "failed"


class NativeCaptureGapReason(str, Enum):
    DEVICE_STORAGE_LOST = "device_storage_lost"
    FILE_CORRUPT = "file_corrupt"
    SYSTEM_INTERRUPTION = "system_interruption"
    UPLOAD_UNRECOVERABLE = "upload_unrecoverable"


class MeetingNativeCapture(SQLModel, table=True):
    __tablename__ = "meeting_native_captures"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "create_idempotency_key",
            name="uq_meeting_native_capture_owner_create_key",
        ),
        CheckConstraint("current_epoch > 0", name="ck_meeting_native_capture_epoch"),
        CheckConstraint(
            "max_total_bytes > 0 AND max_duration_samples > 0",
            name="ck_meeting_native_capture_limits",
        ),
        CheckConstraint(
            "total_bytes >= 0 AND total_samples >= 0",
            name="ck_meeting_native_capture_totals",
        ),
        Index("ix_meeting_native_capture_owner_state", "owner_user_id", "state"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    meeting_id: str = Field(foreign_key="meeting_sessions.id", index=True, max_length=36)
    owner_user_id: int = Field(foreign_key="users.id", index=True)
    device_installation_hash: str = Field(min_length=64, max_length=64)
    create_idempotency_key: str = Field(max_length=128)
    create_request_hash: str = Field(min_length=64, max_length=64)
    state: str = Field(default=NativeCaptureState.ACTIVE.value, max_length=24, index=True)
    encoding: str = Field(default="pcm_s16le", max_length=32)
    sample_rate: int = Field(default=16_000)
    channels: int = Field(default=1)
    current_epoch: int = Field(default=1)
    max_total_bytes: int = Field(sa_column=Column(BigInteger, nullable=False))
    max_duration_samples: int = Field(sa_column=Column(BigInteger, nullable=False))
    total_bytes: int = Field(default=0, sa_column=Column(BigInteger, nullable=False, default=0))
    total_samples: int = Field(default=0, sa_column=Column(BigInteger, nullable=False, default=0))
    sealed_through_sample: int | None = Field(default=None, sa_column=Column(BigInteger))
    seal_manifest_revision: int | None = Field(default=None)
    seal_manifest_sha256: str | None = Field(default=None, max_length=64)
    ingest_complete: bool = Field(default=False)
    server_playback_state: str = Field(
        default=NativeCapturePlaybackState.NOT_READY.value,
        max_length=32,
    )
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    sealed_at: datetime | None = Field(default=None)
    revoked_at: datetime | None = Field(default=None)


class MeetingNativeCaptureEpoch(SQLModel, table=True):
    __tablename__ = "meeting_native_capture_epochs"
    __table_args__ = (
        UniqueConstraint("capture_id", "stream_epoch", name="uq_meeting_native_capture_epoch"),
        UniqueConstraint(
            "capture_id",
            "rollover_idempotency_key",
            name="uq_meeting_native_capture_rollover_key",
        ),
        CheckConstraint("stream_epoch > 0", name="ck_meeting_native_capture_epoch_no"),
        CheckConstraint(
            "last_sequence IS NULL OR last_sequence >= -1",
            name="ck_meeting_native_capture_epoch_sequence",
        ),
        CheckConstraint(
            "recorded_through_sample IS NULL OR recorded_through_sample >= 0",
            name="ck_meeting_native_capture_epoch_sample",
        ),
        Index("ix_meeting_native_capture_epoch_state", "capture_id", "state"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    capture_id: str = Field(foreign_key="meeting_native_captures.id", index=True, max_length=36)
    stream_epoch: int
    state: str = Field(default=NativeCaptureEpochState.ACTIVE.value, max_length=24)
    last_sequence: int | None = Field(default=None, sa_column=Column(BigInteger))
    recorded_through_sample: int | None = Field(default=None, sa_column=Column(BigInteger))
    manifest_revision: int | None = Field(default=None)
    manifest_sha256: str | None = Field(default=None, max_length=64)
    rollover_from_epoch: int | None = Field(default=None)
    rollover_idempotency_key: str | None = Field(default=None, max_length=128)
    rollover_request_hash: str | None = Field(default=None, max_length=64)
    created_at: datetime = Field(default_factory=utcnow)
    closed_at: datetime | None = Field(default=None)


class MeetingNativeCaptureBatch(SQLModel, table=True):
    __tablename__ = "meeting_native_capture_batches"
    __table_args__ = (
        UniqueConstraint(
            "capture_id",
            "stream_epoch",
            "sequence",
            name="uq_meeting_native_capture_batch_sequence",
        ),
        UniqueConstraint(
            "capture_id",
            "idempotency_key",
            name="uq_meeting_native_capture_batch_key",
        ),
        CheckConstraint(
            "stream_epoch > 0 AND sequence >= 0",
            name="ck_meeting_native_capture_batch_position",
        ),
        CheckConstraint(
            "first_sample >= 0 AND sample_count > 0 "
            "AND end_sample = first_sample + sample_count "
            "AND captured_monotonic_ns >= 0",
            name="ck_meeting_native_capture_batch_samples",
        ),
        CheckConstraint("byte_size > 0", name="ck_meeting_native_capture_batch_size"),
        Index("ix_meeting_native_capture_batch_samples", "capture_id", "first_sample"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    capture_id: str = Field(foreign_key="meeting_native_captures.id", index=True, max_length=36)
    meeting_id: str = Field(foreign_key="meeting_sessions.id", index=True, max_length=36)
    owner_user_id: int = Field(foreign_key="users.id", index=True)
    stream_epoch: int
    sequence: int = Field(sa_column=Column(BigInteger, nullable=False))
    first_sample: int = Field(sa_column=Column(BigInteger, nullable=False))
    sample_count: int = Field(sa_column=Column(BigInteger, nullable=False))
    end_sample: int = Field(sa_column=Column(BigInteger, nullable=False))
    captured_monotonic_ns: int = Field(sa_column=Column(BigInteger, nullable=False))
    encoding: str = Field(max_length=32)
    sample_rate: int
    channels: int
    byte_size: int = Field(sa_column=Column(BigInteger, nullable=False))
    sha256: str = Field(min_length=64, max_length=64)
    storage_key: str = Field(max_length=500)
    manifest_revision: int
    idempotency_key: str = Field(max_length=128)
    created_at: datetime = Field(default_factory=utcnow)


class MeetingNativeCaptureToken(SQLModel, table=True):
    __tablename__ = "meeting_native_capture_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_meeting_native_capture_token_hash"),
        Index("ix_meeting_native_capture_token_expiry", "expires_at", "revoked_at"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    token_hash: str = Field(min_length=64, max_length=64)
    capture_id: str = Field(foreign_key="meeting_native_captures.id", index=True, max_length=36)
    meeting_id: str = Field(foreign_key="meeting_sessions.id", index=True, max_length=36)
    owner_user_id: int = Field(foreign_key="users.id", index=True)
    purpose: str = Field(default=CAPTURE_TOKEN_PURPOSE, max_length=64)
    scopes_json: str = Field(default="[]", sa_column=Column(Text, nullable=False))
    expires_at: datetime
    revoked_at: datetime | None = Field(default=None)
    last_used_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)


class MeetingNativeCaptureManifestEntry(SQLModel, table=True):
    __tablename__ = "meeting_native_capture_manifest_entries"
    __table_args__ = (
        UniqueConstraint(
            "capture_id",
            "stream_epoch",
            "sequence",
            name="uq_meeting_native_capture_manifest_entry",
        ),
        CheckConstraint(
            "stream_epoch > 0 AND sequence >= 0 AND first_sample >= 0 "
            "AND sample_count > 0 AND end_sample = first_sample + sample_count",
            name="ck_meeting_native_capture_manifest_entry_range",
        ),
        Index(
            "ix_meeting_native_capture_manifest_timeline",
            "capture_id",
            "first_sample",
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    capture_id: str = Field(foreign_key="meeting_native_captures.id", index=True, max_length=36)
    meeting_id: str = Field(foreign_key="meeting_sessions.id", index=True, max_length=36)
    owner_user_id: int = Field(foreign_key="users.id", index=True)
    stream_epoch: int
    sequence: int = Field(sa_column=Column(BigInteger, nullable=False))
    first_sample: int = Field(sa_column=Column(BigInteger, nullable=False))
    sample_count: int = Field(sa_column=Column(BigInteger, nullable=False))
    end_sample: int = Field(sa_column=Column(BigInteger, nullable=False))
    captured_monotonic_ns: int = Field(sa_column=Column(BigInteger, nullable=False))
    encoding: str = Field(max_length=32)
    sample_rate: int
    channels: int
    byte_size: int = Field(sa_column=Column(BigInteger, nullable=False))
    sha256: str = Field(min_length=64, max_length=64)
    manifest_revision: int
    created_at: datetime = Field(default_factory=utcnow)


class MeetingNativeCaptureGap(SQLModel, table=True):
    __tablename__ = "meeting_native_capture_gaps"
    __table_args__ = (
        UniqueConstraint(
            "capture_id",
            "idempotency_key",
            name="uq_meeting_native_capture_gap_key",
        ),
        CheckConstraint(
            "stream_epoch > 0 AND from_sequence >= 0 AND to_sequence >= from_sequence",
            name="ck_meeting_native_capture_gap_sequence",
        ),
        CheckConstraint(
            "start_sample >= 0 AND end_sample > start_sample",
            name="ck_meeting_native_capture_gap_samples",
        ),
        Index("ix_meeting_native_capture_gap_timeline", "capture_id", "start_sample"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    capture_id: str = Field(foreign_key="meeting_native_captures.id", index=True, max_length=36)
    meeting_id: str = Field(foreign_key="meeting_sessions.id", index=True, max_length=36)
    owner_user_id: int = Field(foreign_key="users.id", index=True)
    stream_epoch: int
    from_sequence: int = Field(sa_column=Column(BigInteger, nullable=False))
    to_sequence: int = Field(sa_column=Column(BigInteger, nullable=False))
    start_sample: int = Field(sa_column=Column(BigInteger, nullable=False))
    end_sample: int = Field(sa_column=Column(BigInteger, nullable=False))
    reason: str = Field(max_length=40)
    manifest_revision: int
    idempotency_key: str = Field(max_length=128)
    request_hash: str = Field(min_length=64, max_length=64)
    created_at: datetime = Field(default_factory=utcnow)


class MeetingNativeCaptureFinalization(SQLModel, table=True):
    __tablename__ = "meeting_native_capture_finalizations"
    __table_args__ = (
        UniqueConstraint("capture_id", name="uq_meeting_native_capture_finalization_capture"),
        CheckConstraint(
            "attempt >= 0 AND max_attempts > 0",
            name="ck_meeting_native_capture_finalization_attempts",
        ),
        CheckConstraint(
            "wav_byte_size IS NULL OR wav_byte_size >= 44",
            name="ck_meeting_native_capture_finalization_wav_size",
        ),
        Index(
            "ix_meeting_native_capture_finalization_claim",
            "state",
            "retry_after",
            "lease_until",
            "created_at",
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    capture_id: str = Field(foreign_key="meeting_native_captures.id", index=True, max_length=36)
    meeting_id: str = Field(foreign_key="meeting_sessions.id", index=True, max_length=36)
    owner_user_id: int = Field(foreign_key="users.id", index=True)
    state: str = Field(default=NativeCaptureFinalizationState.PENDING_UPLOAD.value, max_length=24, index=True)
    attempt: int = Field(default=0)
    max_attempts: int = Field(default=5)
    lease_owner: str | None = Field(default=None, max_length=100)
    lease_until: datetime | None = Field(default=None)
    retry_after: datetime | None = Field(default=None)
    public_error_code: str | None = Field(default=None, max_length=64)
    internal_diagnostic: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    wav_sha256: str | None = Field(default=None, max_length=64)
    wav_byte_size: int | None = Field(default=None, sa_column=Column(BigInteger))
    audio_chunk_count: int = Field(default=0)
    accepted_gap_count: int = Field(default=0)
    final_transcript_job_id: str | None = Field(default=None, foreign_key="meeting_jobs.id", index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    ready_at: datetime | None = Field(default=None)


class MeetingNativeCaptureAudioLink(SQLModel, table=True):
    __tablename__ = "meeting_native_capture_audio_links"
    __table_args__ = (
        UniqueConstraint("batch_id", name="uq_meeting_native_capture_audio_link_batch"),
        UniqueConstraint(
            "capture_id",
            "stream_epoch",
            "sequence",
            name="uq_meeting_native_capture_audio_link_sequence",
        ),
        Index("ix_meeting_native_capture_audio_link_chunk", "audio_chunk_id"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    capture_id: str = Field(foreign_key="meeting_native_captures.id", index=True, max_length=36)
    batch_id: str = Field(foreign_key="meeting_native_capture_batches.id", index=True, max_length=36)
    meeting_id: str = Field(foreign_key="meeting_sessions.id", index=True, max_length=36)
    stream_epoch: int
    sequence: int = Field(sa_column=Column(BigInteger, nullable=False))
    audio_chunk_id: str = Field(foreign_key="meeting_audio_chunks.id", max_length=36)
    source_sha256: str = Field(min_length=64, max_length=64)
    created_at: datetime = Field(default_factory=utcnow)


MEETING_NATIVE_CAPTURE_V1_TABLES = (
    MeetingNativeCapture,
    MeetingNativeCaptureEpoch,
    MeetingNativeCaptureBatch,
    MeetingNativeCaptureToken,
)
MEETING_NATIVE_CAPTURE_MANIFEST_TABLES = (MeetingNativeCaptureManifestEntry,)
MEETING_NATIVE_CAPTURE_FINALIZATION_TABLES = (
    MeetingNativeCaptureGap,
    MeetingNativeCaptureFinalization,
    MeetingNativeCaptureAudioLink,
)
MEETING_NATIVE_CAPTURE_TABLES = (
    *MEETING_NATIVE_CAPTURE_V1_TABLES,
    *MEETING_NATIVE_CAPTURE_MANIFEST_TABLES,
    *MEETING_NATIVE_CAPTURE_FINALIZATION_TABLES,
)


class NativeCaptureCreateRequest(APIModel):
    device_installation_id: str = PydanticField(min_length=16, max_length=256)
    encoding: Literal["pcm_s16le"] = "pcm_s16le"
    sample_rate: Literal[16000] = 16_000
    channels: Literal[1] = 1

    @field_validator("device_installation_id")
    @classmethod
    def validate_device_installation_id(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 16:
            raise ValueError("device_installation_id is too short")
        return cleaned


class NativeCaptureStatusResponse(APIModel):
    schema_version: str = NATIVE_CAPTURE_SCHEMA_VERSION
    id: str
    meeting_id: str
    state: NativeCaptureState
    encoding: str
    sample_rate: int
    channels: int
    current_epoch: int
    total_bytes: int
    total_samples: int
    sealed_through_sample: int | None
    ingest_complete: bool
    server_playback_state: NativeCapturePlaybackState
    created_at: datetime
    updated_at: datetime
    sealed_at: datetime | None
    revoked_at: datetime | None


class NativeCaptureCreateResponse(APIModel):
    schema_version: str = NATIVE_CAPTURE_SCHEMA_VERSION
    capture: NativeCaptureStatusResponse
    capture_token: str
    token_type: Literal["Bearer"] = "Bearer"
    token_expires_at: datetime
    scopes: list[str]
    replayed: bool = False
    limits: dict[str, int]


class NativeCaptureTokenResponse(APIModel):
    schema_version: str = NATIVE_CAPTURE_SCHEMA_VERSION
    capture_id: str
    capture_token: str
    token_type: Literal["Bearer"] = "Bearer"
    token_expires_at: datetime
    scopes: list[str]


class NativeCaptureBatchMetadata(APIModel):
    first_sample: int = PydanticField(ge=0, le=NATIVE_CAPTURE_INT64_MAX)
    sample_count: int = PydanticField(gt=0, le=NATIVE_CAPTURE_INT64_MAX)
    captured_monotonic_ns: int = PydanticField(ge=0, le=NATIVE_CAPTURE_INT64_MAX)
    encoding: Literal["pcm_s16le"] = "pcm_s16le"
    sample_rate: Literal[16000] = 16_000
    channels: Literal[1] = 1
    sha256: str = PydanticField(pattern=r"^[0-9a-f]{64}$")
    manifest_revision: int = PydanticField(ge=1, le=NATIVE_CAPTURE_INT32_MAX)
    idempotency_key: str = PydanticField(min_length=1, max_length=128)
    content_length: int = PydanticField(gt=0, le=NATIVE_CAPTURE_INT64_MAX)

    @model_validator(mode="after")
    def validate_millisecond_alignment(self) -> "NativeCaptureBatchMetadata":
        if self.first_sample % 16 or self.sample_count % 16:
            raise ValueError("native PCM batches must align to one millisecond")
        return self


class NativeCaptureManifestEntry(APIModel):
    sequence: int = PydanticField(ge=0, le=NATIVE_CAPTURE_INT64_MAX)
    first_sample: int = PydanticField(ge=0, le=NATIVE_CAPTURE_INT64_MAX)
    sample_count: int = PydanticField(gt=0, le=NATIVE_CAPTURE_INT64_MAX)
    captured_monotonic_ns: int = PydanticField(ge=0, le=NATIVE_CAPTURE_INT64_MAX)
    encoding: Literal["pcm_s16le"] = "pcm_s16le"
    sample_rate: Literal[16000] = 16_000
    channels: Literal[1] = 1
    sha256: str = PydanticField(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_entry(self) -> "NativeCaptureManifestEntry":
        if self.first_sample % 16 or self.sample_count % 16:
            raise ValueError("native capture manifest entries must align to one millisecond")
        if self.first_sample + self.sample_count > NATIVE_CAPTURE_INT64_MAX:
            raise ValueError("native capture manifest entry range overflows")
        return self


def native_capture_manifest_sha256(
    *,
    expected_epoch: int,
    final_sequence: int,
    recorded_through_sample: int,
    manifest_revision: int,
    entries: list[NativeCaptureManifestEntry],
) -> str:
    payload = {
        "schema_version": "siq.meeting.native_capture.manifest.v1",
        "expected_epoch": expected_epoch,
        "final_sequence": final_sequence,
        "recorded_through_sample": recorded_through_sample,
        "manifest_revision": manifest_revision,
        "entries": [entry.model_dump(mode="json") for entry in sorted(entries, key=lambda value: value.sequence)],
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class NativeCaptureEpochCheckpoint(APIModel):
    stream_epoch: int
    state: NativeCaptureEpochState
    highest_contiguous_sequence: int
    highest_received_sequence: int
    declared_last_sequence: int | None
    recorded_through_sample: int | None
    missing_sequence_ranges: list[dict[str, int]]


class NativeCaptureCaptureCheckpoint(APIModel):
    state: NativeCaptureState
    recorded_through_sample: int | None
    last_sealed_epoch: int | None
    manifest_revision: int | None


class NativeCaptureIngestCheckpoint(APIModel):
    persisted_through_sample: int
    accounted_through_sample: int
    highest_received_sample: int
    received_batches: int
    received_bytes: int
    missing_sample_ranges: list[dict[str, int]]
    audio_missing_sample_ranges: list[dict[str, int]]
    accepted_gaps: int
    ingest_complete: bool


class NativeCaptureRealtimeCheckpoint(APIModel):
    stream_epoch: int
    last_acked_sequence: int
    stable_ordinal: int
    event_cursor: int


class NativeCaptureFinalizationCheckpoint(APIModel):
    capture_sealed: bool
    ingest_complete: bool
    has_unrecoverable_gaps: bool
    packaging_state: NativeCaptureFinalizationState | None
    packaging_attempt: int
    packaging_error_code: str | None
    wav_sha256: str | None
    wav_byte_size: int | None
    server_playback_state: NativeCapturePlaybackState
    postprocess_state: MeetingPostprocessState


class NativeCaptureCheckpointResponse(APIModel):
    schema_version: str = NATIVE_CAPTURE_SCHEMA_VERSION
    capture_id: str
    meeting_id: str
    capture_checkpoint: NativeCaptureCaptureCheckpoint
    ingest_checkpoint: NativeCaptureIngestCheckpoint
    realtime_checkpoint: NativeCaptureRealtimeCheckpoint
    finalization_checkpoint: NativeCaptureFinalizationCheckpoint
    epochs: list[NativeCaptureEpochCheckpoint]


class NativeCaptureBatchResponse(APIModel):
    schema_version: str = NATIVE_CAPTURE_SCHEMA_VERSION
    capture_id: str
    stream_epoch: int
    sequence: int
    first_sample: int
    sample_count: int
    sha256: str
    byte_size: int
    replayed: bool
    checkpoint: NativeCaptureCheckpointResponse


class NativeCaptureBoundaryRequest(APIModel):
    expected_epoch: int = PydanticField(ge=1, le=NATIVE_CAPTURE_INT64_MAX)
    final_sequence: int = PydanticField(ge=-1, le=NATIVE_CAPTURE_INT64_MAX)
    recorded_through_sample: int = PydanticField(ge=0, le=NATIVE_CAPTURE_INT64_MAX)
    manifest_revision: int = PydanticField(ge=1, le=NATIVE_CAPTURE_INT32_MAX)
    manifest_sha256: str = PydanticField(pattern=r"^[0-9a-f]{64}$")
    manifest_entries: list[NativeCaptureManifestEntry] = PydanticField(max_length=20_000)

    @field_validator("recorded_through_sample")
    @classmethod
    def validate_sample_alignment(cls, value: int) -> int:
        if value % 16:
            raise ValueError("native capture boundary must align to one millisecond")
        return value

    @model_validator(mode="after")
    def validate_manifest(self) -> "NativeCaptureBoundaryRequest":
        entries = self.manifest_entries
        if self.final_sequence == -1:
            if entries:
                raise ValueError("empty native capture boundary cannot contain manifest entries")
        else:
            if len(entries) != self.final_sequence + 1:
                raise ValueError("native capture manifest must declare every sequence")
            ordered = sorted(entries, key=lambda value: value.sequence)
            if [value.sequence for value in ordered] != list(range(self.final_sequence + 1)):
                raise ValueError("native capture manifest sequences must be contiguous from zero")
            cursor = ordered[0].first_sample
            for entry in ordered:
                if entry.first_sample != cursor:
                    raise ValueError("native capture manifest sample ranges must be contiguous")
                cursor += entry.sample_count
            if cursor != self.recorded_through_sample:
                raise ValueError("native capture manifest does not reach the declared sample boundary")
        expected = native_capture_manifest_sha256(
            expected_epoch=self.expected_epoch,
            final_sequence=self.final_sequence,
            recorded_through_sample=self.recorded_through_sample,
            manifest_revision=self.manifest_revision,
            entries=entries,
        )
        if self.manifest_sha256 != expected:
            raise ValueError("native capture manifest digest does not match its canonical entries")
        return self


class NativeCaptureGapRequest(APIModel):
    stream_epoch: int = PydanticField(ge=1, le=NATIVE_CAPTURE_INT64_MAX)
    from_sequence: int = PydanticField(ge=0, le=NATIVE_CAPTURE_INT64_MAX)
    to_sequence: int = PydanticField(ge=0, le=NATIVE_CAPTURE_INT64_MAX)
    start_sample: int = PydanticField(ge=0, le=NATIVE_CAPTURE_INT64_MAX)
    end_sample: int = PydanticField(gt=0, le=NATIVE_CAPTURE_INT64_MAX)
    reason: NativeCaptureGapReason
    manifest_revision: int = PydanticField(ge=1, le=NATIVE_CAPTURE_INT32_MAX)

    @model_validator(mode="after")
    def validate_gap(self) -> "NativeCaptureGapRequest":
        if self.to_sequence < self.from_sequence:
            raise ValueError("native capture gap sequence range is reversed")
        if self.end_sample <= self.start_sample:
            raise ValueError("native capture gap sample range is reversed")
        if self.start_sample % 16 or self.end_sample % 16:
            raise ValueError("native capture gap must align to one millisecond")
        return self


class NativeCaptureGapResponse(APIModel):
    schema_version: str = NATIVE_CAPTURE_SCHEMA_VERSION
    capture_id: str
    gap_id: str
    replayed: bool = False
    checkpoint: NativeCaptureCheckpointResponse


class NativeCaptureSealResponse(APIModel):
    schema_version: str = NATIVE_CAPTURE_SCHEMA_VERSION
    capture: NativeCaptureStatusResponse
    checkpoint: NativeCaptureCheckpointResponse
    replayed: bool = False


class NativeCaptureRolloverResponse(APIModel):
    schema_version: str = NATIVE_CAPTURE_SCHEMA_VERSION
    capture_id: str
    previous_epoch: int
    stream_epoch: int
    stream_ticket: str
    stream_ticket_expires_at: datetime
    ws_url: str
    last_acked_sequence: int = -1
    capture_offset_ms: int
    reconnect_window_seconds: int
    replayed: bool = False
    checkpoint: NativeCaptureCheckpointResponse
