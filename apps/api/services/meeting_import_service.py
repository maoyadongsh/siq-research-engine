"""Resumable upload repository and status projection for meeting imports."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import timedelta
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy import delete, func
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from services.auth_service import User
from services.meeting_contracts import MeetingJob, MeetingJobKind, MeetingJobState, MeetingSession, MeetingState, utcnow
from services.meeting_import_config import MeetingImportSettings
from services.meeting_import_contracts import (
    MeetingImportChunk,
    MeetingImportChunkResponse,
    MeetingImportCompleteRequest,
    MeetingImportCreateRequest,
    MeetingImportState,
    MeetingImportStatusResponse,
    MeetingImportStep,
    MeetingImportUpload,
)
from services.meeting_import_storage import MeetingImportStorage, MeetingImportStorageError

ALLOWED_EXTENSIONS = frozenset({"wav", "flac", "mp3", "m4a", "webm", "ogg"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class MeetingImportError(RuntimeError):
    code = "MEETING_IMPORT_ERROR"
    status_code = 400

    def __init__(self, message: str = "meeting import failed", *, code: str | None = None) -> None:
        super().__init__(message)
        if code:
            self.code = code


class MeetingImportNotFound(MeetingImportError):
    code = "MEETING_IMPORT_NOT_FOUND"
    status_code = 404


class MeetingImportConflict(MeetingImportError):
    code = "MEETING_IMPORT_CONFLICT"
    status_code = 409


class MeetingImportQuotaExceeded(MeetingImportError):
    code = "MEETING_IMPORT_QUOTA_EXCEEDED"
    status_code = 413


class MeetingImportInvalid(MeetingImportError):
    code = "MEETING_IMPORT_INVALID"
    status_code = 422


def _request_hash(request: MeetingImportCreateRequest) -> str:
    payload = request.model_dump(mode="json")
    payload["file_sha256"] = (payload.get("file_sha256") or "").lower() or None
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _clean_filename(value: str) -> tuple[str, str]:
    if "\x00" in value or any(ord(character) < 32 for character in value):
        raise MeetingImportInvalid("filename contains invalid characters", code="MEETING_IMPORT_FILENAME_INVALID")
    filename = Path(value.replace("\\", "/")).name.strip()
    if not filename or len(filename) > 255 or "." not in filename:
        raise MeetingImportInvalid("filename is invalid", code="MEETING_IMPORT_FILENAME_INVALID")
    extension = filename.rsplit(".", 1)[1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise MeetingImportInvalid(
            "recording format is not supported", code="MEETING_IMPORT_FORMAT_UNSUPPORTED"
        )
    return filename, extension


class MeetingImportRepository:
    def __init__(
        self,
        session: AsyncSession,
        storage: MeetingImportStorage,
        settings: MeetingImportSettings,
    ) -> None:
        self.session = session
        self.storage = storage
        self.settings = settings

    async def create(
        self,
        owner_user_id: int,
        request: MeetingImportCreateRequest,
        *,
        idempotency_key: str,
    ) -> tuple[MeetingImportStatusResponse, bool]:
        key = idempotency_key.strip()
        if not key:
            raise MeetingImportInvalid("Idempotency-Key is required", code="MEETING_IMPORT_IDEMPOTENCY_REQUIRED")
        filename, extension = _clean_filename(request.filename)
        if request.file_size > self.settings.max_file_bytes:
            raise MeetingImportQuotaExceeded(
                "recording exceeds the configured file-size limit",
                code="MEETING_IMPORT_FILE_TOO_LARGE",
            )
        if not self.settings.min_chunk_bytes <= request.chunk_size <= self.settings.max_chunk_bytes:
            raise MeetingImportInvalid(
                "chunk size is outside the configured bounds", code="MEETING_IMPORT_CHUNK_SIZE_INVALID"
            )
        request_digest = _request_hash(request)
        existing = (
            await self.session.exec(
                select(MeetingImportUpload).where(
                    MeetingImportUpload.owner_user_id == owner_user_id,
                    MeetingImportUpload.idempotency_key == key,
                )
            )
        ).first()
        if existing is not None:
            if existing.request_hash != request_digest:
                raise MeetingImportConflict(
                    "idempotency key was used with different input",
                    code="MEETING_IMPORT_IDEMPOTENCY_CONFLICT",
                )
            return await self.status(existing), True

        # Serialize quota admission for one owner in PostgreSQL. SQLite still
        # serializes the eventual write; the unique idempotency key handles a
        # duplicate create racing before that write lock.
        await self.session.exec(select(User.id).where(User.id == owner_user_id).with_for_update())

        # The first lookup can race another request that held the owner lock.
        # Recheck after admission is serialized so an idempotent replay cannot
        # be rejected by the quota consumed by its own winning request.
        existing = (
            await self.session.exec(
                select(MeetingImportUpload).where(
                    MeetingImportUpload.owner_user_id == owner_user_id,
                    MeetingImportUpload.idempotency_key == key,
                )
            )
        ).first()
        if existing is not None:
            if existing.request_hash != request_digest:
                raise MeetingImportConflict(
                    "idempotency key was used with different input",
                    code="MEETING_IMPORT_IDEMPOTENCY_CONFLICT",
                )
            return await self.status(existing), True

        active_count, active_bytes = (
            await self.session.exec(
                select(func.count(MeetingImportUpload.id), func.coalesce(func.sum(MeetingImportUpload.expected_size), 0))
                .where(
                    MeetingImportUpload.owner_user_id == owner_user_id,
                    MeetingImportUpload.staging_purged_at.is_(None),
                )
            )
        ).one()
        if int(active_count or 0) >= self.settings.max_active_per_owner:
            raise MeetingImportQuotaExceeded(
                "too many active recording imports", code="MEETING_IMPORT_ACTIVE_LIMIT_EXCEEDED"
            )
        if int(active_bytes or 0) + request.file_size > self.settings.owner_quota_bytes:
            raise MeetingImportQuotaExceeded(
                "recording import quota would be exceeded", code="MEETING_IMPORT_OWNER_QUOTA_EXCEEDED"
            )
        selection = request.model_selection
        upload = MeetingImportUpload(
            owner_user_id=owner_user_id,
            idempotency_key=key,
            request_hash=request_digest,
            original_filename=filename,
            extension=extension,
            media_type=request.media_type,
            expected_size=request.file_size,
            expected_sha256=request.file_sha256.lower() if request.file_sha256 else None,
            chunk_size=request.chunk_size,
            total_chunks=math.ceil(request.file_size / request.chunk_size),
            title=request.title.strip(),
            language=request.language,
            voiceprint_enabled=request.voiceprint_enabled,
            ai_enabled=request.ai_enabled,
            selection_mode=str(selection.mode),
            requested_model_ref=selection.model_ref,
            fallback_policy=str(selection.fallback_policy),
            cloud_data_boundary_confirmed_at=(
                utcnow() if selection.cloud_data_boundary_confirmed else None
            ),
            expires_at=utcnow() + timedelta(seconds=self.settings.upload_ttl_seconds),
        )
        self.session.add(upload)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            replay = (
                await self.session.exec(
                    select(MeetingImportUpload).where(
                        MeetingImportUpload.owner_user_id == owner_user_id,
                        MeetingImportUpload.idempotency_key == key,
                    )
                )
            ).first()
            if replay is None or replay.request_hash != request_digest:
                raise MeetingImportConflict(code="MEETING_IMPORT_IDEMPOTENCY_CONFLICT") from exc
            return await self.status(replay), True
        await self.session.refresh(upload)
        return await self.status(upload), False

    async def get_owned(self, upload_id: str, owner_user_id: int, *, lock: bool = False) -> MeetingImportUpload:
        statement = select(MeetingImportUpload).where(
            MeetingImportUpload.id == upload_id,
            MeetingImportUpload.owner_user_id == owner_user_id,
        )
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        value = (await self.session.exec(statement)).first()
        if value is None:
            raise MeetingImportNotFound()
        return value

    async def put_chunk(
        self,
        upload_id: str,
        owner_user_id: int,
        *,
        ordinal: int,
        byte_offset: int,
        sha256: str,
        content_length: int,
        stream: AsyncIterator[bytes],
    ) -> MeetingImportChunkResponse:
        upload = await self.get_owned(upload_id, owner_user_id)
        digest = sha256.strip().lower()
        if not _SHA256.fullmatch(digest):
            raise MeetingImportInvalid("chunk SHA-256 is invalid", code="MEETING_IMPORT_CHUNK_HASH_INVALID")
        expected_offset = ordinal * upload.chunk_size
        expected_length = min(upload.chunk_size, upload.expected_size - expected_offset)
        if ordinal < 0 or ordinal >= upload.total_chunks or byte_offset != expected_offset or expected_length <= 0:
            raise MeetingImportInvalid("chunk coordinates are invalid", code="MEETING_IMPORT_CHUNK_POSITION_INVALID")
        if content_length != expected_length:
            raise MeetingImportInvalid(
                "Content-Length does not match the chunk manifest", code="MEETING_IMPORT_CHUNK_SIZE_MISMATCH"
            )
        if upload.state != MeetingImportState.UPLOADING.value:
            raise MeetingImportConflict(
                "upload no longer accepts chunks", code="MEETING_IMPORT_NOT_ACCEPTING_CHUNKS"
            )
        if upload.expires_at <= utcnow():
            raise MeetingImportConflict("upload session expired", code="MEETING_IMPORT_EXPIRED")

        if ordinal < upload.received_chunks:
            existing = (
                await self.session.exec(
                    select(MeetingImportChunk).where(
                        MeetingImportChunk.upload_id == upload.id,
                        MeetingImportChunk.ordinal == ordinal,
                    )
                )
            ).first()
            if (
                existing is not None
                and existing.byte_offset == byte_offset
                and existing.byte_size == content_length
                and existing.sha256 == digest
            ):
                return _chunk_response(upload, existing, replayed=True)
            raise MeetingImportConflict(
                "chunk ordinal already contains different content", code="MEETING_IMPORT_CHUNK_CONFLICT"
            )
        if ordinal != upload.received_chunks:
            raise MeetingImportConflict(
                f"expected chunk ordinal {upload.received_chunks}", code="MEETING_IMPORT_CHUNK_OUT_OF_ORDER"
            )

        try:
            storage_key, written, actual_hash, created = await self.storage.store_chunk(
                owner_user_id,
                upload.id,
                ordinal,
                digest,
                stream,
                expected_size=content_length,
            )
        except MeetingImportStorageError as exc:
            raise MeetingImportInvalid(str(exc), code=exc.code) from exc

        current = await self.get_owned(upload_id, owner_user_id, lock=True)
        if current.state != MeetingImportState.UPLOADING.value:
            if created:
                self.storage.remove_key(storage_key)
            raise MeetingImportConflict(code="MEETING_IMPORT_NOT_ACCEPTING_CHUNKS")
        if current.received_chunks != ordinal:
            winner = (
                await self.session.exec(
                    select(MeetingImportChunk).where(
                        MeetingImportChunk.upload_id == upload.id,
                        MeetingImportChunk.ordinal == ordinal,
                    )
                )
            ).first()
            if created and (winner is None or winner.storage_key != storage_key):
                self.storage.remove_key(storage_key)
            if winner is not None and winner.sha256 == digest and winner.byte_size == written:
                return _chunk_response(current, winner, replayed=True)
            raise MeetingImportConflict(code="MEETING_IMPORT_CHUNK_CONFLICT")
        chunk = MeetingImportChunk(
            upload_id=upload.id,
            ordinal=ordinal,
            byte_offset=byte_offset,
            byte_size=written,
            sha256=actual_hash,
            storage_key=storage_key,
        )
        current.received_chunks += 1
        current.received_size += written
        current.updated_at = utcnow()
        self.session.add(chunk)
        self.session.add(current)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            winner = (
                await self.session.exec(
                    select(MeetingImportChunk).where(
                        MeetingImportChunk.upload_id == upload.id,
                        MeetingImportChunk.ordinal == ordinal,
                    )
                )
            ).first()
            if created and (winner is None or winner.storage_key != storage_key):
                self.storage.remove_key(storage_key)
            raise MeetingImportConflict(code="MEETING_IMPORT_CHUNK_CONFLICT") from exc
        return _chunk_response(current, chunk)

    async def complete(
        self,
        upload_id: str,
        owner_user_id: int,
        request: MeetingImportCompleteRequest,
    ) -> MeetingImportStatusResponse:
        upload = await self.get_owned(upload_id, owner_user_id, lock=True)
        if upload.state in {
            MeetingImportState.QUEUED.value,
            MeetingImportState.PROCESSING.value,
            MeetingImportState.POSTPROCESS_QUEUED.value,
            MeetingImportState.READY.value,
        }:
            return await self.status(upload)
        if upload.state != MeetingImportState.UPLOADING.value:
            raise MeetingImportConflict("upload cannot be completed", code="MEETING_IMPORT_COMPLETE_INVALID_STATE")
        if upload.received_chunks != upload.total_chunks or upload.received_size != upload.expected_size:
            raise MeetingImportConflict("upload is incomplete", code="MEETING_IMPORT_INCOMPLETE")
        supplied_hash = request.file_sha256.lower() if request.file_sha256 else None
        if upload.expected_sha256 and supplied_hash and upload.expected_sha256 != supplied_hash:
            raise MeetingImportConflict("file hash differs from create request", code="MEETING_IMPORT_FILE_HASH_CONFLICT")
        if supplied_hash:
            upload.expected_sha256 = supplied_hash
        upload.state = MeetingImportState.QUEUED.value
        upload.step = MeetingImportStep.VERIFYING.value
        upload.updated_at = utcnow()
        upload.expires_at = utcnow() + timedelta(seconds=self.settings.upload_ttl_seconds)
        self.session.add(upload)
        await self.session.commit()
        return await self.status(upload)

    async def cancel(self, upload_id: str, owner_user_id: int) -> MeetingImportStatusResponse:
        upload = await self.get_owned(upload_id, owner_user_id, lock=True)
        if upload.state == MeetingImportState.CANCELLED.value:
            return await self.status(upload)
        if upload.meeting_id is not None or upload.state in {
            MeetingImportState.POSTPROCESS_QUEUED.value,
            MeetingImportState.READY.value,
        }:
            raise MeetingImportConflict(
                "the imported meeting already exists; delete it from the meeting detail page",
                code="MEETING_IMPORT_CANCEL_TOO_LATE",
            )
        upload.state = MeetingImportState.CANCELLED.value
        upload.step = MeetingImportStep.CANCELLED.value
        upload.lease_owner = None
        upload.lease_until = None
        upload.retry_after = None
        upload.updated_at = utcnow()
        self.session.add(upload)
        await self.session.exec(delete(MeetingImportChunk).where(MeetingImportChunk.upload_id == upload.id))
        await self.session.commit()
        self.storage.purge_upload(owner_user_id, upload.id)
        upload.staging_purged_at = utcnow()
        self.session.add(upload)
        await self.session.commit()
        return await self.status(upload)

    async def retry(self, upload_id: str, owner_user_id: int) -> MeetingImportStatusResponse:
        upload = await self.get_owned(upload_id, owner_user_id, lock=True)
        downstream_failed = False
        if upload.meeting_id:
            jobs = list(
                (
                    await self.session.exec(
                        select(MeetingJob).where(
                            MeetingJob.meeting_id == upload.meeting_id,
                            col(MeetingJob.job_kind).in_(
                                [
                                    MeetingJobKind.FINAL_TRANSCRIPT.value,
                                    MeetingJobKind.SPEAKER_RECLUSTER.value,
                                    MeetingJobKind.FINAL_MINUTES.value,
                                ]
                            ),
                            MeetingJob.state == MeetingJobState.FAILED.value,
                        )
                    )
                ).all()
            )
            for job in jobs:
                downstream_failed = True
                job.state = MeetingJobState.QUEUED.value
                job.attempt = 0
                job.lease_owner = None
                job.lease_until = None
                job.public_error_code = None
                job.internal_diagnostic = None
                job.updated_at = utcnow()
                self.session.add(job)
        if upload.state == MeetingImportState.FAILED.value:
            upload.state = MeetingImportState.QUEUED.value
            upload.step = MeetingImportStep.VERIFYING.value
            upload.attempt = 0
            upload.lease_owner = None
            upload.lease_until = None
            upload.retry_after = None
            upload.public_error_code = None
            upload.internal_diagnostic = None
            upload.updated_at = utcnow()
            self.session.add(upload)
        elif not downstream_failed:
            raise MeetingImportConflict("import has no retryable failure", code="MEETING_IMPORT_NOT_RETRYABLE")
        await self.session.commit()
        return await self.status(upload)

    async def status(self, upload: MeetingImportUpload) -> MeetingImportStatusResponse:
        state = upload.state
        step = upload.step
        error_code = upload.public_error_code
        retryable = state == MeetingImportState.FAILED.value
        meeting_deleted = False
        if upload.meeting_id:
            meeting = await self.session.get(MeetingSession, upload.meeting_id)
            meeting_deleted = meeting is not None and meeting.state == MeetingState.DELETED.value
            if meeting_deleted:
                state = MeetingImportState.CANCELLED.value
                step = MeetingImportStep.CANCELLED.value
                error_code = None
                retryable = False
        if upload.meeting_id and state in {
            MeetingImportState.POSTPROCESS_QUEUED.value,
            MeetingImportState.READY.value,
        } and not meeting_deleted:
            jobs = list(
                (
                    await self.session.exec(
                        select(MeetingJob)
                        .where(
                            MeetingJob.meeting_id == upload.meeting_id,
                            col(MeetingJob.job_kind).in_(
                                [
                                    MeetingJobKind.FINAL_TRANSCRIPT.value,
                                    MeetingJobKind.SPEAKER_RECLUSTER.value,
                                    MeetingJobKind.FINAL_MINUTES.value,
                                ]
                            ),
                        )
                        .order_by(MeetingJob.created_at)
                    )
                ).all()
            )
            by_kind = {job.job_kind: job for job in jobs}
            final_job = by_kind.get(MeetingJobKind.FINAL_TRANSCRIPT.value)
            speaker_job = by_kind.get(MeetingJobKind.SPEAKER_RECLUSTER.value)
            minutes_job = by_kind.get(MeetingJobKind.FINAL_MINUTES.value)
            selected_job: MeetingJob | None = None
            if final_job is None or final_job.state != MeetingJobState.SUCCEEDED.value:
                step = MeetingImportStep.FINALIZING.value
                selected_job = final_job
            elif speaker_job is None or speaker_job.state != MeetingJobState.SUCCEEDED.value:
                step = MeetingImportStep.RECLUSTERING.value
                selected_job = speaker_job
            elif upload.ai_enabled and (minutes_job is None or minutes_job.state != MeetingJobState.SUCCEEDED.value):
                step = MeetingImportStep.MINUTES.value
                selected_job = minutes_job
            else:
                state = MeetingImportState.READY.value
                step = MeetingImportStep.READY.value
            if selected_job is not None and selected_job.state == MeetingJobState.FAILED.value:
                state = MeetingImportState.FAILED.value
                error_code = selected_job.public_error_code or "MEETING_IMPORT_POSTPROCESS_FAILED"
                retryable = True
        return MeetingImportStatusResponse(
            id=upload.id,
            meeting_id=upload.meeting_id,
            filename=upload.original_filename,
            media_type=upload.media_type,
            expected_size=upload.expected_size,
            received_size=upload.received_size,
            chunk_size=upload.chunk_size,
            total_chunks=upload.total_chunks,
            received_chunks=upload.received_chunks,
            next_ordinal=upload.received_chunks,
            upload_progress=min(1.0, upload.received_size / upload.expected_size),
            state=state,
            ingest_state=upload.state,
            step=step,
            detected_duration_ms=upload.detected_duration_ms,
            public_error_code=error_code,
            retryable=retryable,
            can_resume=upload.state == MeetingImportState.UPLOADING.value,
            can_cancel=upload.meeting_id is None
            and upload.state
            in {
                MeetingImportState.UPLOADING.value,
                MeetingImportState.QUEUED.value,
                MeetingImportState.PROCESSING.value,
                MeetingImportState.RETRY_WAIT.value,
                MeetingImportState.FAILED.value,
            },
            created_at=upload.created_at,
            updated_at=upload.updated_at,
        )


def _chunk_response(
    upload: MeetingImportUpload,
    chunk: MeetingImportChunk,
    *,
    replayed: bool = False,
) -> MeetingImportChunkResponse:
    return MeetingImportChunkResponse(
        upload_id=upload.id,
        ordinal=chunk.ordinal,
        byte_offset=chunk.byte_offset,
        byte_size=chunk.byte_size,
        sha256=chunk.sha256,
        received_size=upload.received_size,
        received_chunks=upload.received_chunks,
        next_ordinal=upload.received_chunks,
        replayed=replayed,
    )
