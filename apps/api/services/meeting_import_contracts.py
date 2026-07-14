"""Persistence and API contracts for resumable meeting recording imports.

The import domain is deliberately separate from chat voice input. Uploaded
recordings eventually become ordinary ``MeetingSession`` rows so every
downstream transcript, speaker, artifact, and export API remains reusable.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field as PydanticField, model_validator
from sqlalchemy import BigInteger, CheckConstraint, Column, Index, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from services.auth_service import User as _User  # noqa: F401
from services.meeting_contracts import (
    APIModel,
    MeetingSession as _MeetingSession,  # noqa: F401
    ModelSelectionInput,
    new_id,
    utcnow,
)

MEETING_IMPORT_SCHEMA_VERSION = "siq.meeting.import.v1"


class MeetingImportState(str, Enum):
    UPLOADING = "uploading"
    QUEUED = "queued"
    PROCESSING = "processing"
    POSTPROCESS_QUEUED = "postprocess_queued"
    READY = "ready"
    RETRY_WAIT = "retry_wait"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MeetingImportStep(str, Enum):
    UPLOADING = "uploading"
    VERIFYING = "verifying"
    PROBING = "probing"
    TRANSCODING = "transcoding"
    PERSISTING = "persisting"
    FINALIZING = "finalizing"
    RECLUSTERING = "reclustering"
    MINUTES = "minutes"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MeetingImportUpload(SQLModel, table=True):
    __tablename__ = "meeting_import_uploads"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "idempotency_key", name="uq_meeting_import_owner_key"),
        CheckConstraint("expected_size > 0", name="ck_meeting_import_expected_size"),
        CheckConstraint("chunk_size > 0 AND total_chunks > 0", name="ck_meeting_import_chunk_shape"),
        CheckConstraint("received_size >= 0 AND received_chunks >= 0", name="ck_meeting_import_progress"),
        CheckConstraint("attempt >= 0 AND max_attempts > 0", name="ck_meeting_import_attempts"),
        Index("ix_meeting_import_claim", "state", "lease_until", "created_at"),
        Index("ix_meeting_import_owner_created", "owner_user_id", "created_at"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    owner_user_id: int = Field(foreign_key="users.id", index=True)
    meeting_id: str | None = Field(default=None, foreign_key="meeting_sessions.id", index=True, max_length=36)
    idempotency_key: str = Field(max_length=128)
    request_hash: str = Field(min_length=64, max_length=64)
    original_filename: str = Field(max_length=255)
    extension: str = Field(max_length=16)
    media_type: str | None = Field(default=None, max_length=100)
    expected_size: int = Field(sa_column=Column(BigInteger, nullable=False))
    expected_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    chunk_size: int
    total_chunks: int
    received_size: int = Field(default=0, sa_column=Column(BigInteger, nullable=False, default=0))
    received_chunks: int = Field(default=0)
    detected_duration_ms: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    detected_format: str | None = Field(default=None, max_length=100)
    assembled_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    title: str = Field(max_length=200)
    language: str = Field(default="zh-CN", max_length=32)
    voiceprint_enabled: bool = Field(default=False)
    ai_enabled: bool = Field(default=False)
    selection_mode: str = Field(default="none", max_length=16)
    requested_model_ref: str | None = Field(default=None, max_length=255)
    fallback_policy: str = Field(default="disabled", max_length=32)
    cloud_data_boundary_confirmed_at: datetime | None = Field(default=None)
    state: str = Field(default=MeetingImportState.UPLOADING.value, max_length=32, index=True)
    step: str = Field(default=MeetingImportStep.UPLOADING.value, max_length=32)
    attempt: int = Field(default=0)
    max_attempts: int = Field(default=3)
    lease_owner: str | None = Field(default=None, max_length=100)
    lease_until: datetime | None = Field(default=None)
    retry_after: datetime | None = Field(default=None)
    public_error_code: str | None = Field(default=None, max_length=64)
    internal_diagnostic: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    expires_at: datetime
    staging_purged_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class MeetingImportChunk(SQLModel, table=True):
    __tablename__ = "meeting_import_chunks"
    __table_args__ = (
        UniqueConstraint("upload_id", "ordinal", name="uq_meeting_import_chunk_ordinal"),
        CheckConstraint("ordinal >= 0 AND byte_offset >= 0", name="ck_meeting_import_chunk_position"),
        CheckConstraint("byte_size > 0", name="ck_meeting_import_chunk_size"),
        Index("ix_meeting_import_chunks_upload", "upload_id", "ordinal"),
    )

    id: str = Field(default_factory=new_id, primary_key=True, max_length=36)
    upload_id: str = Field(foreign_key="meeting_import_uploads.id", index=True, max_length=36)
    ordinal: int
    byte_offset: int = Field(sa_column=Column(BigInteger, nullable=False))
    byte_size: int
    sha256: str = Field(min_length=64, max_length=64)
    storage_key: str = Field(max_length=500)
    created_at: datetime = Field(default_factory=utcnow)


MEETING_IMPORT_TABLES = (MeetingImportUpload, MeetingImportChunk)


class MeetingImportCreateRequest(APIModel):
    filename: str = PydanticField(min_length=1, max_length=255)
    media_type: str | None = PydanticField(default=None, max_length=100)
    file_size: int = PydanticField(gt=0)
    file_sha256: str | None = PydanticField(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    chunk_size: int = PydanticField(default=4 * 1024 * 1024, gt=0)
    title: str = PydanticField(default="导入的会议录音", min_length=1, max_length=200)
    language: str = PydanticField(default="zh-CN", min_length=2, max_length=32)
    voiceprint_enabled: bool = False
    ai_enabled: bool = False
    model_selection: ModelSelectionInput = PydanticField(default_factory=ModelSelectionInput)

    @model_validator(mode="after")
    def validate_ai_selection(self) -> "MeetingImportCreateRequest":
        mode = getattr(self.model_selection.mode, "value", self.model_selection.mode)
        if self.ai_enabled and mode == "none":
            raise ValueError("model selection is required when AI is enabled")
        if not self.ai_enabled and mode != "none":
            raise ValueError("model selection must be none when AI is disabled")
        return self


class MeetingImportCompleteRequest(APIModel):
    file_sha256: str | None = PydanticField(default=None, pattern=r"^[0-9a-fA-F]{64}$")


class MeetingImportChunkResponse(APIModel):
    upload_id: str
    ordinal: int
    byte_offset: int
    byte_size: int
    sha256: str
    received_size: int
    received_chunks: int
    next_ordinal: int
    replayed: bool = False


class MeetingImportStatusResponse(APIModel):
    schema_version: str = MEETING_IMPORT_SCHEMA_VERSION
    id: str
    meeting_id: str | None = None
    filename: str
    media_type: str | None = None
    expected_size: int
    received_size: int
    chunk_size: int
    total_chunks: int
    received_chunks: int
    next_ordinal: int
    upload_progress: float
    state: str
    ingest_state: str
    step: str
    detected_duration_ms: int | None = None
    public_error_code: str | None = None
    retryable: bool = False
    can_resume: bool = False
    can_cancel: bool = False
    created_at: datetime
    updated_at: datetime
