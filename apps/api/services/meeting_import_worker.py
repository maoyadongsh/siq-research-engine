"""Durable worker for verified meeting recording imports."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from contextlib import suppress
from datetime import timedelta
from pathlib import Path
from typing import Any, AsyncContextManager, Callable

from sqlalchemy import and_, delete, or_, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from services.meeting_audio_store import MeetingAudioStore, MeetingAudioStoreError
from services.meeting_contracts import (
    AudioChunkState,
    AudioSource,
    MeetingAudioChunk,
    MeetingCreateRequest,
    MeetingJob,
    MeetingJobKind,
    MeetingJobState,
    MeetingPostprocessState,
    MeetingSession,
    MeetingState,
    ModelSelectionInput,
    utcnow,
)
from services.meeting_event_store import MeetingEventStore
from services.meeting_import_config import MeetingImportSettings
from services.meeting_import_contracts import (
    MeetingImportChunk,
    MeetingImportState,
    MeetingImportStep,
    MeetingImportUpload,
)
from services.meeting_import_storage import MeetingImportStorage, MeetingImportStorageError
from services.meeting_repository import MeetingRepository

SessionFactory = Callable[[], AsyncContextManager[AsyncSession]]
logger = logging.getLogger(__name__)

_CONTAINER_FORMATS = {
    "wav": frozenset({"wav"}),
    "flac": frozenset({"flac"}),
    "mp3": frozenset({"mp3"}),
    "m4a": frozenset({"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}),
    "webm": frozenset({"matroska", "webm"}),
    "ogg": frozenset({"ogg"}),
}


class MeetingImportWorkerError(RuntimeError):
    public_code = "MEETING_IMPORT_PROCESSING_FAILED"
    retryable = False


class MeetingImportTransientError(MeetingImportWorkerError):
    public_code = "MEETING_IMPORT_TEMPORARILY_UNAVAILABLE"
    retryable = True


class MeetingImportCancelled(MeetingImportWorkerError):
    public_code = "MEETING_IMPORT_CANCELLED"


class MeetingImportLeaseLost(MeetingImportTransientError):
    public_code = "MEETING_IMPORT_LEASE_LOST"


class MeetingImportWorker:
    def __init__(
        self,
        session_factory: SessionFactory,
        storage: MeetingImportStorage,
        audio_store: MeetingAudioStore,
        settings: MeetingImportSettings,
        *,
        worker_id: str,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("meeting import worker_id is required")
        self.session_factory = session_factory
        self.storage = storage
        self.audio_store = audio_store
        self.settings = settings
        self.worker_id = worker_id.strip()[:100]

    async def claim_next(self) -> MeetingImportUpload | None:
        now = utcnow()
        eligible = or_(
            and_(
                MeetingImportUpload.state == MeetingImportState.QUEUED.value,
                MeetingImportUpload.attempt < MeetingImportUpload.max_attempts,
            ),
            and_(
                MeetingImportUpload.state == MeetingImportState.RETRY_WAIT.value,
                MeetingImportUpload.attempt < MeetingImportUpload.max_attempts,
                or_(MeetingImportUpload.retry_after.is_(None), MeetingImportUpload.retry_after <= now),
            ),
            and_(
                MeetingImportUpload.state == MeetingImportState.PROCESSING.value,
                MeetingImportUpload.lease_until.is_not(None),
                MeetingImportUpload.lease_until <= now,
            ),
        )
        candidate = (
            select(MeetingImportUpload.id)
            .where(eligible)
            .order_by(MeetingImportUpload.created_at, MeetingImportUpload.id)
            .limit(1)
            .scalar_subquery()
        )
        statement = (
            update(MeetingImportUpload)
            .where(MeetingImportUpload.id == candidate)
            .where(eligible)
            .values(
                state=MeetingImportState.PROCESSING.value,
                attempt=MeetingImportUpload.attempt + 1,
                lease_owner=self.worker_id,
                lease_until=now + timedelta(seconds=self.settings.lease_seconds),
                retry_after=None,
                public_error_code=None,
                internal_diagnostic=None,
                updated_at=now,
            )
            .returning(MeetingImportUpload.id)
        )
        async with self.session_factory() as session:
            result = await session.exec(statement)
            upload_id = result.scalar_one_or_none()
            await session.commit()
            if upload_id is None:
                return None
            return (await session.exec(select(MeetingImportUpload).where(MeetingImportUpload.id == upload_id))).one()

    async def run_once(self) -> bool:
        upload = await self.claim_next()
        if upload is None:
            return await self.cleanup_once()
        await self._process_with_heartbeat(upload.id)
        return True

    async def cleanup_once(self) -> bool:
        """Recover staging cleanup after API or worker process crashes."""

        now = utcnow()
        async with self.session_factory() as session:
            upload = (
                await session.exec(
                    select(MeetingImportUpload)
                    .where(
                        MeetingImportUpload.staging_purged_at.is_(None),
                        or_(
                            MeetingImportUpload.state.in_(
                                [
                                    MeetingImportState.POSTPROCESS_QUEUED.value,
                                    MeetingImportState.READY.value,
                                    MeetingImportState.CANCELLED.value,
                                ]
                            ),
                            and_(
                                MeetingImportUpload.meeting_id.is_(None),
                                MeetingImportUpload.expires_at <= now,
                                MeetingImportUpload.state.in_(
                                    [
                                        MeetingImportState.UPLOADING.value,
                                        MeetingImportState.FAILED.value,
                                        MeetingImportState.RETRY_WAIT.value,
                                    ]
                                ),
                            ),
                        ),
                    )
                    .order_by(MeetingImportUpload.updated_at, MeetingImportUpload.id)
                    .limit(1)
                )
            ).first()
            if upload is None:
                return False
            owner_user_id = upload.owner_user_id
            upload_id = upload.id
            if upload.state not in {
                MeetingImportState.POSTPROCESS_QUEUED.value,
                MeetingImportState.READY.value,
                MeetingImportState.CANCELLED.value,
            }:
                upload.state = MeetingImportState.CANCELLED.value
                upload.step = MeetingImportStep.CANCELLED.value
                upload.lease_owner = None
                upload.lease_until = None
                upload.retry_after = None
                upload.updated_at = now
                session.add(upload)
                await session.commit()
        try:
            await asyncio.to_thread(self.storage.purge_upload, owner_user_id, upload_id)
        except (OSError, MeetingImportStorageError):
            logger.exception("meeting import recovery cleanup failed for %s", upload_id)
            return False
        async with self.session_factory() as session:
            current = await session.get(MeetingImportUpload, upload_id)
            if current is None:
                return True
            await session.exec(delete(MeetingImportChunk).where(MeetingImportChunk.upload_id == upload_id))
            current.staging_purged_at = utcnow()
            current.updated_at = utcnow()
            session.add(current)
            await session.commit()
        return True

    async def run_forever(self, stop_event: asyncio.Event | None = None, *, poll_seconds: float = 1.0) -> None:
        stop = stop_event or asyncio.Event()
        while not stop.is_set():
            try:
                worked = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("meeting import worker iteration failed")
                worked = False
            if worked:
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
            except TimeoutError:
                pass

    async def _process_with_heartbeat(self, upload_id: str) -> None:
        stop = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(upload_id, stop))
        try:
            await self._process(upload_id)
        except MeetingImportCancelled:
            return
        except MeetingImportWorkerError as exc:
            await self._fail(upload_id, exc.public_code, str(exc), retryable=exc.retryable)
        except (OSError, MeetingImportStorageError, MeetingAudioStoreError) as exc:
            await self._fail(
                upload_id,
                getattr(exc, "code", "MEETING_IMPORT_STORAGE_UNAVAILABLE"),
                f"{type(exc).__name__}: {exc}",
                retryable=True,
            )
        except Exception as exc:
            await self._fail(
                upload_id,
                "MEETING_IMPORT_PROCESSING_FAILED",
                f"{type(exc).__name__}: {exc}",
                retryable=False,
            )
        finally:
            stop.set()
            lease_valid = await heartbeat
            if not lease_valid and not await self._is_terminal(upload_id):
                await self._fail(
                    upload_id,
                    MeetingImportLeaseLost.public_code,
                    "meeting import lease expired",
                    retryable=True,
                )

    async def _process(self, upload_id: str) -> None:
        upload, chunks = await self._load_input(upload_id)
        await self._set_step(upload_id, MeetingImportStep.VERIFYING)
        source, assembled_hash = await asyncio.to_thread(
            self.storage.assemble_source,
            upload.owner_user_id,
            upload.id,
            chunks,
            extension=upload.extension,
            expected_size=upload.expected_size,
            expected_sha256=upload.expected_sha256,
        )
        await self._record_assembled_hash(upload_id, assembled_hash)

        await self._set_step(upload_id, MeetingImportStep.PROBING)
        duration_ms, format_name = await self._probe(source, upload.extension)
        await self._record_probe(upload_id, duration_ms, format_name)

        await self._set_step(upload_id, MeetingImportStep.TRANSCODING)
        normalized = self.storage.normalized_path(upload.owner_user_id, upload.id)
        await self._transcode(source, normalized)
        normalized_size = normalized.stat().st_size
        if normalized_size <= 0 or normalized_size % 2 != 0:
            raise MeetingImportWorkerError("normalized PCM has an invalid byte length")
        normalized_duration_ms = math.ceil(normalized_size / 32)
        if normalized_duration_ms > self.settings.max_duration_seconds * 1000:
            raise _permanent("recording exceeds the configured duration", "MEETING_IMPORT_DURATION_EXCEEDED")

        meeting_id = await self._ensure_meeting(upload_id)
        await self._set_step(upload_id, MeetingImportStep.PERSISTING)
        await self._persist_audio_and_queue(
            upload_id,
            meeting_id,
            normalized,
            normalized_duration_ms,
        )
        await self._purge_staging(upload_id)

    async def _load_input(self, upload_id: str) -> tuple[MeetingImportUpload, list[MeetingImportChunk]]:
        async with self.session_factory() as session:
            upload = await session.get(MeetingImportUpload, upload_id)
            if upload is None:
                raise MeetingImportCancelled("upload no longer exists")
            if upload.state == MeetingImportState.CANCELLED.value:
                raise MeetingImportCancelled("upload was cancelled")
            if upload.lease_owner != self.worker_id or upload.state != MeetingImportState.PROCESSING.value:
                raise MeetingImportLeaseLost("upload lease is not owned by this worker")
            chunks = list(
                (
                    await session.exec(
                        select(MeetingImportChunk)
                        .where(MeetingImportChunk.upload_id == upload.id)
                        .order_by(MeetingImportChunk.ordinal)
                    )
                ).all()
            )
            if (
                len(chunks) != upload.total_chunks
                or sum(item.byte_size for item in chunks) != upload.expected_size
                or any(item.ordinal != index for index, item in enumerate(chunks))
                or any(item.byte_offset != item.ordinal * upload.chunk_size for item in chunks)
            ):
                raise _permanent("upload chunk manifest is incomplete", "MEETING_IMPORT_MANIFEST_INVALID")
            return upload, chunks

    async def _probe(self, source: Path, extension: str) -> tuple[int, str]:
        stdout, stderr, returncode = await _run_process(
            [
                self.settings.ffprobe_bin,
                "-v",
                "error",
                "-show_entries",
                "format=duration,format_name:stream=codec_type",
                "-of",
                "json",
                "-protocol_whitelist",
                "file,pipe",
                str(source),
            ],
            timeout=self.settings.ffprobe_timeout_seconds,
        )
        if returncode != 0:
            raise _permanent(
                f"ffprobe rejected the recording: {_bounded_text(stderr)}",
                "MEETING_IMPORT_MEDIA_INVALID",
            )
        try:
            payload = json.loads(stdout.decode("utf-8"))
            duration = float(payload["format"]["duration"])
            format_name = str(payload["format"]["format_name"])
            streams = payload.get("streams") or []
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise _permanent("ffprobe returned invalid metadata", "MEETING_IMPORT_MEDIA_INVALID") from exc
        if not math.isfinite(duration) or duration <= 0:
            raise _permanent("recording duration is invalid", "MEETING_IMPORT_DURATION_INVALID")
        duration_ms = math.ceil(duration * 1000)
        if duration_ms > self.settings.max_duration_seconds * 1000:
            raise _permanent("recording exceeds the configured duration", "MEETING_IMPORT_DURATION_EXCEEDED")
        detected_formats = {item.strip().lower() for item in format_name.split(",") if item.strip()}
        if not detected_formats.intersection(_CONTAINER_FORMATS[extension]):
            raise _permanent(
                "recording container does not match its allowed extension",
                "MEETING_IMPORT_FORMAT_MISMATCH",
            )
        if not any(isinstance(item, dict) and item.get("codec_type") == "audio" for item in streams):
            raise _permanent("recording has no audio stream", "MEETING_IMPORT_AUDIO_STREAM_MISSING")
        return duration_ms, format_name[:100]

    async def _transcode(self, source: Path, target: Path) -> None:
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        temporary.unlink(missing_ok=True)
        try:
            with temporary.open("xb") as output:
                os.chmod(temporary, 0o600)
                _, stderr, returncode = await _run_process(
                    [
                        self.settings.ffmpeg_bin,
                        "-nostdin",
                        "-hide_banner",
                        "-v",
                        "error",
                        "-xerror",
                        "-protocol_whitelist",
                        "file,pipe",
                        "-i",
                        str(source),
                        "-map_metadata",
                        "-1",
                        "-vn",
                        "-ac",
                        "1",
                        "-ar",
                        "16000",
                        "-t",
                        str(self.settings.max_duration_seconds + 1),
                        "-f",
                        "s16le",
                        "pipe:1",
                    ],
                    timeout=self.settings.ffmpeg_timeout_seconds,
                    stdout_file=output,
                )
                output.flush()
                os.fsync(output.fileno())
            if returncode != 0:
                raise _permanent(
                    f"ffmpeg could not decode the recording: {_bounded_text(stderr)}",
                    "MEETING_IMPORT_TRANSCODE_FAILED",
                )
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    async def _ensure_meeting(self, upload_id: str) -> str:
        async with self.session_factory() as session:
            upload = await session.get(MeetingImportUpload, upload_id)
            if upload is None or upload.state == MeetingImportState.CANCELLED.value:
                raise MeetingImportCancelled("upload was cancelled")
            if upload.meeting_id:
                return upload.meeting_id
            request = MeetingCreateRequest(
                title=upload.title,
                language=upload.language,
                audio_source=AudioSource.IMPORT,
                voiceprint_enabled=upload.voiceprint_enabled,
                ai_enabled=upload.ai_enabled,
                model_selection=ModelSelectionInput(
                    mode=upload.selection_mode,
                    model_ref=upload.requested_model_ref,
                    fallback_policy=upload.fallback_policy,
                    cloud_data_boundary_confirmed=upload.cloud_data_boundary_confirmed_at is not None,
                ),
            )
            meeting, _, _ = await MeetingRepository(session).create_session(
                upload.owner_user_id,
                request,
                idempotency_key=f"meeting-import:{upload.id}",
            )
            meeting_id = meeting.id

        async with self.session_factory() as session:
            current = (
                await session.exec(
                    select(MeetingImportUpload).where(MeetingImportUpload.id == upload_id).with_for_update()
                )
            ).one()
            if current.state == MeetingImportState.CANCELLED.value:
                # Cancellation can win in the narrow window after the ordinary
                # meeting is created but before it is attached to the upload.
                # Link it and use the standard deletion workflow so no model
                # settings, events, or idempotency metadata are orphaned.
                current.meeting_id = meeting_id
                current.updated_at = utcnow()
                session.add(current)
                await MeetingRepository(session).request_delete(meeting_id, current.owner_user_id)
                raise MeetingImportCancelled("upload was cancelled")
            if current.lease_owner != self.worker_id:
                raise MeetingImportLeaseLost("upload lease changed before meeting attachment")
            current.meeting_id = meeting_id
            current.updated_at = utcnow()
            session.add(current)
            await session.commit()
            return meeting_id

    async def _persist_audio_and_queue(
        self,
        upload_id: str,
        meeting_id: str,
        normalized: Path,
        duration_ms: int,
    ) -> None:
        async with self.session_factory() as session:
            upload = await session.get(MeetingImportUpload, upload_id)
            meeting = await session.get(MeetingSession, meeting_id)
            if upload is None or meeting is None:
                raise MeetingImportWorkerError("import meeting disappeared")
            existing = {
                item.sequence: item
                for item in (
                    await session.exec(
                        select(MeetingAudioChunk).where(
                            MeetingAudioChunk.meeting_id == meeting_id,
                            MeetingAudioChunk.stream_epoch == 1,
                        )
                    )
                ).all()
            }
            sequence = 0
            start_ms = 0
            block_bytes = 10 * 1000 * 32
            packed_chunks: list[MeetingAudioChunk] = []
            with normalized.open("rb") as source:
                while True:
                    block = source.read(block_bytes)
                    if not block:
                        break
                    if len(block) % 32:
                        block += bytes(32 - len(block) % 32)
                    chunk_duration_ms = len(block) // 32
                    persisted = await asyncio.to_thread(
                        self.audio_store.persist_chunk,
                        upload.owner_user_id,
                        meeting_id,
                        1,
                        sequence,
                        block,
                    )
                    manifest = existing.get(sequence)
                    if manifest is None:
                        manifest = MeetingAudioChunk(
                            meeting_id=meeting_id,
                            stream_epoch=1,
                            sequence=sequence,
                            start_ms=start_ms,
                            duration_ms=chunk_duration_ms,
                            storage_key=persisted.storage_key,
                            sha256=persisted.sha256,
                            byte_size=persisted.byte_size,
                            codec="pcm_s16le",
                            sample_rate=16_000,
                            channels=1,
                            state=AudioChunkState.VERIFIED.value,
                        )
                        session.add(manifest)
                    elif (
                        manifest.start_ms != start_ms
                        or manifest.duration_ms != chunk_duration_ms
                        or manifest.storage_key != persisted.storage_key
                        or manifest.sha256 != persisted.sha256
                        or manifest.byte_size != persisted.byte_size
                    ):
                        raise MeetingImportWorkerError("existing meeting audio manifest conflicts with import")
                    packed_chunks.append(manifest)
                    sequence += 1
                    start_ms += chunk_duration_ms
            if sequence == 0:
                raise MeetingImportWorkerError("normalized recording is empty")
            await session.flush()
            await asyncio.to_thread(
                self.audio_store.pack_wav,
                upload.owner_user_id,
                meeting_id,
                packed_chunks,
            )
            meeting.state = MeetingState.STOPPED.value
            meeting.postprocess_state = MeetingPostprocessState.QUEUED.value
            meeting.stream_epoch = 1
            meeting.last_audio_sequence = sequence - 1
            meeting.started_at = upload.created_at
            meeting.stopped_at = upload.created_at + timedelta(milliseconds=duration_ms)
            meeting.updated_at = utcnow()
            session.add(meeting)
            key = f"{meeting.id}:finalize:v1"
            job = (await session.exec(select(MeetingJob).where(MeetingJob.idempotency_key == key))).first()
            if job is None:
                job = MeetingJob(
                    meeting_id=meeting.id,
                    job_kind=MeetingJobKind.FINAL_TRANSCRIPT.value,
                    idempotency_key=key,
                    state=MeetingJobState.QUEUED.value,
                    input_watermark=0,
                    settings_version=meeting.settings_version,
                    input_json=json.dumps(
                        {"schema_version": "siq.meeting.import.finalization.v1", "upload_id": upload.id},
                        ensure_ascii=True,
                        separators=(",", ":"),
                    ),
                )
                session.add(job)
                await session.flush()
                await MeetingEventStore(session).append(
                    meeting.id,
                    "postprocess.queued",
                    {
                        "session_id": meeting.id,
                        "job_id": job.id,
                        "input_watermark": 0,
                        "source": "recording_import",
                    },
                )
            upload.state = MeetingImportState.POSTPROCESS_QUEUED.value
            upload.step = MeetingImportStep.FINALIZING.value
            upload.lease_owner = None
            upload.lease_until = None
            upload.retry_after = None
            upload.public_error_code = None
            upload.internal_diagnostic = None
            upload.updated_at = utcnow()
            session.add(upload)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise MeetingImportTransientError("meeting audio manifest conflicted during commit") from exc

    async def _purge_staging(self, upload_id: str) -> None:
        async with self.session_factory() as session:
            upload = await session.get(MeetingImportUpload, upload_id)
            if upload is None:
                return
            owner_id = upload.owner_user_id
        try:
            await asyncio.to_thread(self.storage.purge_upload, owner_id, upload_id)
        except (OSError, MeetingImportStorageError):
            logger.exception("meeting import staging cleanup failed for %s", upload_id)
            return
        async with self.session_factory() as session:
            upload = await session.get(MeetingImportUpload, upload_id)
            if upload is None:
                return
            await session.exec(delete(MeetingImportChunk).where(MeetingImportChunk.upload_id == upload_id))
            upload.staging_purged_at = utcnow()
            upload.updated_at = utcnow()
            session.add(upload)
            await session.commit()

    async def _set_step(self, upload_id: str, step: MeetingImportStep) -> None:
        async with self.session_factory() as session:
            result = await session.exec(
                update(MeetingImportUpload)
                .where(
                    MeetingImportUpload.id == upload_id,
                    MeetingImportUpload.state == MeetingImportState.PROCESSING.value,
                    MeetingImportUpload.lease_owner == self.worker_id,
                )
                .values(step=step.value, updated_at=utcnow())
            )
            await session.commit()
            if not result.rowcount:
                current = await session.get(MeetingImportUpload, upload_id)
                if current is not None and current.state == MeetingImportState.CANCELLED.value:
                    raise MeetingImportCancelled("upload was cancelled")
                raise MeetingImportLeaseLost("upload lease was lost")

    async def _record_assembled_hash(self, upload_id: str, digest: str) -> None:
        async with self.session_factory() as session:
            upload = await session.get(MeetingImportUpload, upload_id)
            if upload is None or upload.lease_owner != self.worker_id:
                raise MeetingImportLeaseLost()
            upload.assembled_sha256 = digest
            upload.updated_at = utcnow()
            session.add(upload)
            await session.commit()

    async def _record_probe(self, upload_id: str, duration_ms: int, format_name: str) -> None:
        async with self.session_factory() as session:
            upload = await session.get(MeetingImportUpload, upload_id)
            if upload is None or upload.lease_owner != self.worker_id:
                raise MeetingImportLeaseLost()
            upload.detected_duration_ms = duration_ms
            upload.detected_format = format_name
            upload.updated_at = utcnow()
            session.add(upload)
            await session.commit()

    async def _heartbeat(self, upload_id: str, stop: asyncio.Event) -> bool:
        interval = max(5.0, self.settings.lease_seconds / 3)
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return True
            except TimeoutError:
                pass
            async with self.session_factory() as session:
                result = await session.exec(
                    update(MeetingImportUpload)
                    .where(
                        MeetingImportUpload.id == upload_id,
                        MeetingImportUpload.state == MeetingImportState.PROCESSING.value,
                        MeetingImportUpload.lease_owner == self.worker_id,
                    )
                    .values(
                        lease_until=utcnow() + timedelta(seconds=self.settings.lease_seconds),
                        updated_at=utcnow(),
                    )
                )
                await session.commit()
                if not result.rowcount:
                    return False
        return True

    async def _fail(self, upload_id: str, code: str, diagnostic: str, *, retryable: bool) -> None:
        async with self.session_factory() as session:
            upload = await session.get(MeetingImportUpload, upload_id)
            if upload is None or upload.state in {
                MeetingImportState.CANCELLED.value,
                MeetingImportState.POSTPROCESS_QUEUED.value,
                MeetingImportState.READY.value,
            }:
                return
            can_retry = retryable and upload.attempt < upload.max_attempts
            upload.state = (
                MeetingImportState.RETRY_WAIT.value if can_retry else MeetingImportState.FAILED.value
            )
            upload.step = MeetingImportStep.FAILED.value
            upload.retry_after = (
                utcnow() + timedelta(seconds=self.settings.retry_base_seconds * max(1, upload.attempt))
                if can_retry
                else None
            )
            upload.lease_owner = None
            upload.lease_until = None
            upload.public_error_code = code[:64]
            upload.internal_diagnostic = diagnostic[:1000]
            upload.updated_at = utcnow()
            session.add(upload)
            await session.commit()

    async def _is_terminal(self, upload_id: str) -> bool:
        async with self.session_factory() as session:
            upload = await session.get(MeetingImportUpload, upload_id)
            return upload is None or upload.state in {
                MeetingImportState.CANCELLED.value,
                MeetingImportState.POSTPROCESS_QUEUED.value,
                MeetingImportState.READY.value,
                MeetingImportState.FAILED.value,
                MeetingImportState.RETRY_WAIT.value,
            }


def _permanent(message: str, code: str) -> MeetingImportWorkerError:
    error = MeetingImportWorkerError(message)
    error.public_code = code
    return error


async def _run_process(
    argv: list[str],
    *,
    timeout: int,
    stdout_file: Any | None = None,
) -> tuple[bytes, bytes, int]:
    stdout_target: Any = stdout_file if stdout_file is not None else asyncio.subprocess.PIPE
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=stdout_target,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError as exc:
        process.kill()
        with suppress(Exception):
            await process.wait()
        raise MeetingImportTransientError("media processing timed out") from exc
    stdout_bytes = stdout if isinstance(stdout, bytes) else b""
    stderr_bytes = stderr if isinstance(stderr, bytes) else b""
    if len(stdout_bytes) > 1024 * 1024 or len(stderr_bytes) > 1024 * 1024:
        raise MeetingImportWorkerError("media tool output exceeded its safety bound")
    return stdout_bytes, stderr_bytes, int(process.returncode or 0)


def _bounded_text(value: bytes) -> str:
    return value[:1000].decode("utf-8", errors="replace").replace("\n", " ")
