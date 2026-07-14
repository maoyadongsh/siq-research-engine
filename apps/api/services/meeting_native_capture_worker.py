"""Lease-based native capture packaging outside the API request path."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import stat
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import AsyncContextManager

from sqlalchemy import and_, or_, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from services.meeting_audio_store import MeetingAudioStore, MeetingAudioStoreError
from services.meeting_contracts import (
    AudioChunkState,
    MeetingAudioChunk,
    MeetingSession,
    MeetingState,
    MeetingStreamLease,
    utcnow,
)
from services.meeting_event_store import MeetingEventStore
from services.meeting_native_capture_config import MeetingNativeCaptureSettings
from services.meeting_native_capture_contracts import (
    MeetingNativeCapture,
    MeetingNativeCaptureAudioLink,
    MeetingNativeCaptureBatch,
    MeetingNativeCaptureEpoch,
    MeetingNativeCaptureFinalization,
    MeetingNativeCaptureGap,
    MeetingNativeCaptureManifestEntry,
    NativeCaptureFinalizationState,
    NativeCaptureGapReason,
    NativeCaptureManifestEntry,
    NativeCapturePlaybackState,
    NativeCaptureState,
    native_capture_manifest_sha256,
)
from services.meeting_native_capture_service import native_capture_coverage
from services.meeting_native_capture_storage import (
    MeetingNativeCaptureStorage,
    MeetingNativeCaptureStorageError,
)
from services.meeting_repository import MeetingInvalidOperation, MeetingRepository

SessionFactory = Callable[[], AsyncContextManager[AsyncSession]]
logger = logging.getLogger(__name__)


class NativeCaptureFinalizationError(RuntimeError):
    public_code = "NATIVE_CAPTURE_FINALIZATION_FAILED"
    retryable = False


class NativeCaptureFinalizationRetryable(NativeCaptureFinalizationError):
    public_code = "NATIVE_CAPTURE_FINALIZATION_TEMPORARILY_UNAVAILABLE"
    retryable = True


class NativeCaptureFinalizationIntegrityError(NativeCaptureFinalizationRetryable):
    public_code = "NATIVE_CAPTURE_FINALIZATION_INTEGRITY_FAILED"


class NativeCaptureFinalizationConflict(NativeCaptureFinalizationError):
    public_code = "NATIVE_CAPTURE_FINALIZATION_PROVENANCE_CONFLICT"


class NativeCaptureFinalizationLeaseLost(NativeCaptureFinalizationRetryable):
    public_code = "NATIVE_CAPTURE_FINALIZATION_LEASE_LOST"


class MeetingNativeCaptureFinalizationWorker:
    def __init__(
        self,
        session_factory: SessionFactory,
        native_storage: MeetingNativeCaptureStorage,
        audio_store: MeetingAudioStore,
        settings: MeetingNativeCaptureSettings,
        *,
        worker_id: str,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("native capture finalization worker_id is required")
        self.session_factory = session_factory
        self.native_storage = native_storage
        self.audio_store = audio_store
        self.settings = settings
        self.worker_id = worker_id.strip()[:100]

    @staticmethod
    def _eligible(now):
        return or_(
            and_(
                MeetingNativeCaptureFinalization.state == NativeCaptureFinalizationState.QUEUED.value,
                MeetingNativeCaptureFinalization.attempt < MeetingNativeCaptureFinalization.max_attempts,
            ),
            and_(
                MeetingNativeCaptureFinalization.state == NativeCaptureFinalizationState.RETRY_WAIT.value,
                MeetingNativeCaptureFinalization.attempt < MeetingNativeCaptureFinalization.max_attempts,
                or_(
                    MeetingNativeCaptureFinalization.retry_after.is_(None),
                    MeetingNativeCaptureFinalization.retry_after <= now,
                ),
            ),
            and_(
                MeetingNativeCaptureFinalization.state == NativeCaptureFinalizationState.PROCESSING.value,
                MeetingNativeCaptureFinalization.lease_until.is_not(None),
                MeetingNativeCaptureFinalization.lease_until <= now,
            ),
        )

    async def claim_next(self) -> MeetingNativeCaptureFinalization | None:
        now = utcnow()
        eligible = self._eligible(now)
        candidate = (
            select(MeetingNativeCaptureFinalization.id)
            .where(eligible)
            .order_by(
                MeetingNativeCaptureFinalization.created_at,
                MeetingNativeCaptureFinalization.id,
            )
            .limit(1)
            .scalar_subquery()
        )
        statement = (
            update(MeetingNativeCaptureFinalization)
            .where(MeetingNativeCaptureFinalization.id == candidate)
            .where(eligible)
            .values(
                state=NativeCaptureFinalizationState.PROCESSING.value,
                attempt=MeetingNativeCaptureFinalization.attempt + 1,
                lease_owner=self.worker_id,
                lease_until=now + timedelta(seconds=self.settings.finalization_lease_seconds),
                retry_after=None,
                public_error_code=None,
                internal_diagnostic=None,
                updated_at=now,
            )
            .returning(MeetingNativeCaptureFinalization.id)
        )
        async with self.session_factory() as session:
            result = await session.exec(statement)
            finalization_id = result.scalar_one_or_none()
            if finalization_id is None:
                await session.rollback()
                return None
            value = await session.get(MeetingNativeCaptureFinalization, finalization_id)
            capture = await session.get(MeetingNativeCapture, value.capture_id) if value else None
            if value is None or capture is None:
                await session.rollback()
                return None
            capture.server_playback_state = NativeCapturePlaybackState.PACKAGING.value
            capture.updated_at = now
            session.add(capture)
            await session.commit()
            return value

    async def run_once(self) -> bool:
        recovered = await self._recover_missing_finalization()
        handoff = await self._handoff_one_ready()
        claimed = await self.claim_next()
        if claimed is None:
            return recovered or handoff
        await self._process_with_heartbeat(claimed.id, claimed.attempt)
        return True

    async def run_forever(self, stop_event: asyncio.Event | None = None) -> None:
        stop = stop_event or asyncio.Event()
        while not stop.is_set():
            try:
                worked = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("native capture finalization iteration failed")
                worked = False
            if worked:
                continue
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=self.settings.finalization_poll_seconds,
                )
            except TimeoutError:
                pass

    async def retry_capture(self, capture_id: str) -> bool:
        async with self.session_factory() as session:
            finalization = (
                await session.exec(
                    select(MeetingNativeCaptureFinalization)
                    .where(MeetingNativeCaptureFinalization.capture_id == capture_id)
                    .with_for_update()
                )
            ).first()
            capture = await session.get(MeetingNativeCapture, capture_id)
            if finalization is None or capture is None:
                return False
            if finalization.state not in {
                NativeCaptureFinalizationState.FAILED.value,
                NativeCaptureFinalizationState.RETRY_WAIT.value,
            }:
                return False
            epochs, batches, gaps = await self._manifest_rows(session, capture.id)
            coverage = native_capture_coverage(capture, epochs, batches, gaps)
            finalization.state = (
                NativeCaptureFinalizationState.QUEUED.value
                if coverage.complete
                else NativeCaptureFinalizationState.PENDING_UPLOAD.value
            )
            finalization.attempt = 0
            finalization.lease_owner = None
            finalization.lease_until = None
            finalization.retry_after = None
            finalization.public_error_code = None
            finalization.internal_diagnostic = None
            finalization.updated_at = utcnow()
            capture.server_playback_state = (
                NativeCapturePlaybackState.PENDING_PACKAGING.value
                if coverage.complete
                else NativeCapturePlaybackState.PENDING_UPLOAD.value
            )
            capture.updated_at = utcnow()
            session.add(finalization)
            session.add(capture)
            await MeetingEventStore(session).append(
                capture.meeting_id,
                "native_capture.finalization.retry.queued",
                {"capture_id": capture.id, "packaging_state": finalization.state},
            )
            await session.commit()
            return True

    async def _recover_missing_finalization(self) -> bool:
        # Resolve a candidate without a lock, then take the meeting lock first.
        # Retention/deletion use the same lock order, so their revocation fence
        # is visible before an orphan finalization can be reconstructed.
        async with self.session_factory() as session:
            candidate = (
                await session.exec(
                    select(
                        MeetingNativeCapture.id,
                        MeetingNativeCapture.meeting_id,
                        MeetingNativeCapture.owner_user_id,
                    )
                    .where(
                        MeetingNativeCapture.state == NativeCaptureState.SEALED.value,
                        ~select(MeetingNativeCaptureFinalization.id)
                        .where(MeetingNativeCaptureFinalization.capture_id == MeetingNativeCapture.id)
                        .exists(),
                    )
                    .order_by(MeetingNativeCapture.sealed_at, MeetingNativeCapture.id)
                    .limit(1)
                )
            ).first()
        if candidate is None:
            return False
        capture_id, meeting_id, owner_user_id = candidate
        async with self.session_factory() as session:
            meeting = (
                await session.exec(
                    select(MeetingSession)
                    .where(
                        MeetingSession.id == meeting_id,
                        MeetingSession.owner_user_id == owner_user_id,
                        MeetingSession.state != MeetingState.DELETED.value,
                    )
                    .with_for_update()
                )
            ).first()
            if meeting is None:
                return False
            capture = (
                await session.exec(
                    select(MeetingNativeCapture)
                    .where(
                        MeetingNativeCapture.id == capture_id,
                        MeetingNativeCapture.meeting_id == meeting.id,
                        MeetingNativeCapture.owner_user_id == owner_user_id,
                        MeetingNativeCapture.state == NativeCaptureState.SEALED.value,
                        ~select(MeetingNativeCaptureFinalization.id)
                        .where(MeetingNativeCaptureFinalization.capture_id == MeetingNativeCapture.id)
                        .exists(),
                    )
                    .with_for_update()
                )
            ).first()
            if capture is None:
                return False
            epochs, batches, gaps = await self._manifest_rows(session, capture.id)
            coverage = native_capture_coverage(capture, epochs, batches, gaps)
            state = (
                NativeCaptureFinalizationState.QUEUED.value
                if coverage.complete
                else NativeCaptureFinalizationState.PENDING_UPLOAD.value
            )
            session.add(
                MeetingNativeCaptureFinalization(
                    capture_id=capture.id,
                    meeting_id=capture.meeting_id,
                    owner_user_id=capture.owner_user_id,
                    state=state,
                    max_attempts=self.settings.finalization_max_attempts,
                    accepted_gap_count=coverage.accepted_gap_count,
                )
            )
            capture.ingest_complete = coverage.complete
            capture.server_playback_state = (
                NativeCapturePlaybackState.PENDING_PACKAGING.value
                if coverage.complete
                else NativeCapturePlaybackState.PENDING_UPLOAD.value
            )
            capture.updated_at = utcnow()
            session.add(capture)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
            return True

    async def _handoff_one_ready(self) -> bool:
        async with self.session_factory() as session:
            value = (
                await session.exec(
                    select(MeetingNativeCaptureFinalization)
                    .where(
                        MeetingNativeCaptureFinalization.state == NativeCaptureFinalizationState.READY.value,
                        MeetingNativeCaptureFinalization.final_transcript_job_id.is_(None),
                    )
                    .order_by(
                        MeetingNativeCaptureFinalization.ready_at,
                        MeetingNativeCaptureFinalization.id,
                    )
                    .limit(1)
                )
            ).first()
            finalization_id = value.id if value is not None else None
        if finalization_id is None:
            return False
        await self._handoff_final_transcript(finalization_id)
        return True

    async def _process_with_heartbeat(self, finalization_id: str, expected_attempt: int) -> None:
        stop = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(finalization_id, expected_attempt, stop))
        try:
            await self._process(finalization_id, expected_attempt)
        except NativeCaptureFinalizationError as exc:
            await self._fail(
                finalization_id,
                expected_attempt,
                exc.public_code,
                retryable=exc.retryable,
            )
        except (MeetingNativeCaptureStorageError, MeetingAudioStoreError, OSError) as exc:
            code = getattr(exc, "code", "NATIVE_CAPTURE_FINALIZATION_STORAGE_UNAVAILABLE")
            await self._fail(finalization_id, expected_attempt, str(code)[:64], retryable=True)
        except Exception:
            await self._fail(
                finalization_id,
                expected_attempt,
                "NATIVE_CAPTURE_FINALIZATION_FAILED",
                retryable=False,
            )
        finally:
            stop.set()
            lease_valid = await heartbeat
            if not lease_valid and not await self._is_terminal_or_pending(finalization_id, expected_attempt):
                await self._fail(
                    finalization_id,
                    expected_attempt,
                    NativeCaptureFinalizationLeaseLost.public_code,
                    retryable=True,
                )

    async def _process(self, finalization_id: str, expected_attempt: int) -> None:
        capture, batches, gaps = await self._load_complete_manifest(finalization_id, expected_attempt)
        if capture is None:
            return
        for batch in batches:
            payload = await asyncio.to_thread(
                self.native_storage.read_verified_batch,
                capture.owner_user_id,
                capture.id,
                batch.storage_key,
                expected_size=batch.byte_size,
                expected_sha256=batch.sha256,
                max_bytes=self.settings.max_batch_bytes,
            )
            await self._register_batch(finalization_id, expected_attempt, capture, batch, payload)
        chunks = await self._linked_chunks(finalization_id, expected_attempt, capture.id)
        if len(chunks) != len(batches):
            raise NativeCaptureFinalizationConflict("native capture provenance is incomplete")
        target_duration_ms = int(capture.sealed_through_sample or 0) // 16
        wav_identity: tuple[Path, int, int] | None = None
        published = False
        try:
            await self._assert_processing_capture(
                finalization_id,
                expected_attempt,
                capture.id,
            )
            wav_path = await asyncio.to_thread(
                self.audio_store.pack_wav,
                capture.owner_user_id,
                capture.meeting_id,
                chunks,
                total_duration_ms=target_duration_ms,
                force_repack=True,
            )
            wav_sha256, wav_size = await asyncio.to_thread(self._hash_file, wav_path)
            wav_stat = wav_path.stat(follow_symlinks=False)
            wav_identity = (wav_path, wav_stat.st_dev, wav_stat.st_ino)
            await self._assert_processing_capture(
                finalization_id,
                expected_attempt,
                capture.id,
            )
            await self._stop_meeting(
                finalization_id,
                expected_attempt,
                capture.id,
                capture.meeting_id,
                capture.owner_user_id,
            )
            await self._mark_ready(
                finalization_id,
                expected_attempt,
                capture.id,
                meeting_id=capture.meeting_id,
                owner_user_id=capture.owner_user_id,
                wav_sha256=wav_sha256,
                wav_size=wav_size,
                audio_chunk_count=len(chunks),
                accepted_gap_count=len(gaps),
            )
            published = True
        finally:
            if wav_identity is not None and not published:
                await asyncio.to_thread(self._remove_if_same_file, *wav_identity)
        await self._handoff_final_transcript(finalization_id)

    async def _load_complete_manifest(
        self,
        finalization_id: str,
        expected_attempt: int,
    ) -> tuple[MeetingNativeCapture | None, list[MeetingNativeCaptureBatch], list[MeetingNativeCaptureGap]]:
        async with self.session_factory() as session:
            identity = (
                await session.exec(
                    select(
                        MeetingNativeCaptureFinalization.meeting_id,
                        MeetingNativeCaptureFinalization.owner_user_id,
                        MeetingNativeCaptureFinalization.capture_id,
                    ).where(MeetingNativeCaptureFinalization.id == finalization_id)
                )
            ).first()
            if identity is None:
                raise NativeCaptureFinalizationLeaseLost("native capture finalization disappeared")
            meeting_id, owner_user_id, capture_id = identity
            meeting = (
                await session.exec(
                    select(MeetingSession)
                    .where(
                        MeetingSession.id == meeting_id,
                        MeetingSession.owner_user_id == owner_user_id,
                        MeetingSession.state != MeetingState.DELETED.value,
                    )
                    .with_for_update()
                )
            ).first()
            if meeting is None:
                raise NativeCaptureFinalizationLeaseLost("native capture meeting was revoked")
            finalization = await self._owned_processing(
                session,
                finalization_id,
                expected_attempt,
                lock=True,
            )
            if finalization is None:
                raise NativeCaptureFinalizationLeaseLost("native capture finalization lease was lost")
            capture = await session.get(MeetingNativeCapture, capture_id)
            if (
                capture is None
                or finalization.meeting_id != meeting.id
                or finalization.owner_user_id != owner_user_id
                or finalization.capture_id != capture.id
                or capture.meeting_id != finalization.meeting_id
                or capture.owner_user_id != finalization.owner_user_id
                or capture.state != NativeCaptureState.SEALED.value
            ):
                raise NativeCaptureFinalizationConflict("native capture finalization input disappeared")
            epochs, batches, gaps = await self._manifest_rows(session, capture.id)
            coverage = native_capture_coverage(capture, epochs, batches, gaps)
            if not coverage.complete:
                await self._mark_pending_upload(session, finalization, capture)
                return None, [], []
            declarations = list(
                (
                    await session.exec(
                        select(MeetingNativeCaptureManifestEntry)
                        .where(MeetingNativeCaptureManifestEntry.capture_id == capture.id)
                        .order_by(
                            MeetingNativeCaptureManifestEntry.stream_epoch,
                            MeetingNativeCaptureManifestEntry.sequence,
                        )
                    )
                ).all()
            )
            self._validate_frozen_manifest(
                finalization,
                capture,
                epochs,
                batches,
                gaps,
                declarations,
            )
            expected_count = sum(
                int(epoch.last_sequence) + 1
                for epoch in epochs
                if epoch.last_sequence is not None and epoch.last_sequence >= 0
            )
            if len(declarations) != expected_count:
                raise NativeCaptureFinalizationConflict("native capture manifest declaration is incomplete")
            declared = {(value.stream_epoch, value.sequence): value for value in declarations}
            if any(
                not self._batch_matches_manifest(batch, declared.get((batch.stream_epoch, batch.sequence)))
                for batch in batches
            ):
                raise NativeCaptureFinalizationConflict("native capture batch diverges from its frozen manifest")
            if capture.sealed_through_sample is None or capture.sealed_through_sample % 16:
                raise NativeCaptureFinalizationConflict("native capture boundary is not millisecond aligned")
            if any(value.first_sample % 16 or value.sample_count % 16 for value in batches):
                raise NativeCaptureFinalizationConflict("native capture batch is not millisecond aligned")
            return capture, batches, gaps

    @staticmethod
    def _validate_frozen_manifest(
        finalization: MeetingNativeCaptureFinalization,
        capture: MeetingNativeCapture,
        epochs: list[MeetingNativeCaptureEpoch],
        batches: list[MeetingNativeCaptureBatch],
        gaps: list[MeetingNativeCaptureGap],
        declarations: list[MeetingNativeCaptureManifestEntry],
    ) -> None:
        if (
            capture.state != NativeCaptureState.SEALED.value
            or finalization.capture_id != capture.id
            or finalization.meeting_id != capture.meeting_id
            or finalization.owner_user_id != capture.owner_user_id
            or not epochs
            or epochs[-1].stream_epoch != capture.current_epoch
        ):
            raise NativeCaptureFinalizationConflict("native capture ownership or seal state is invalid")
        if (
            sum(int(value.byte_size) for value in batches) != capture.total_bytes
            or sum(int(value.sample_count) for value in batches) != capture.total_samples
        ):
            raise NativeCaptureFinalizationConflict("native capture persisted totals diverge")

        declarations_by_epoch: dict[int, list[MeetingNativeCaptureManifestEntry]] = {}
        for value in declarations:
            if (
                value.capture_id != capture.id
                or value.meeting_id != capture.meeting_id
                or value.owner_user_id != capture.owner_user_id
            ):
                raise NativeCaptureFinalizationConflict("native capture manifest ownership diverges")
            declarations_by_epoch.setdefault(value.stream_epoch, []).append(value)

        first_epoch = int(epochs[0].stream_epoch)
        if first_epoch <= 0:
            raise NativeCaptureFinalizationConflict("native capture epoch ownership diverges")
        cursor = 0
        for index, epoch in enumerate(epochs):
            expected_epoch = first_epoch + index
            expected_rollover_from = None if index == 0 else expected_epoch - 1
            if (
                epoch.capture_id != capture.id
                or epoch.stream_epoch != expected_epoch
                or epoch.rollover_from_epoch != expected_rollover_from
            ):
                raise NativeCaptureFinalizationConflict("native capture epoch ownership diverges")
            expected_state = "sealed" if index == len(epochs) - 1 else "rolled_over"
            if (
                epoch.state != expected_state
                or epoch.last_sequence is None
                or epoch.recorded_through_sample is None
                or epoch.manifest_revision is None
                or epoch.manifest_sha256 is None
            ):
                raise NativeCaptureFinalizationConflict("native capture epoch boundary is incomplete")
            values = sorted(declarations_by_epoch.get(epoch.stream_epoch, []), key=lambda item: item.sequence)
            if [int(value.sequence) for value in values] != list(range(int(epoch.last_sequence) + 1)):
                raise NativeCaptureFinalizationConflict("native capture epoch manifest is incomplete")
            entries = [
                NativeCaptureManifestEntry(
                    sequence=int(value.sequence),
                    first_sample=int(value.first_sample),
                    sample_count=int(value.sample_count),
                    captured_monotonic_ns=int(value.captured_monotonic_ns),
                    encoding=value.encoding,
                    sample_rate=value.sample_rate,
                    channels=value.channels,
                    sha256=value.sha256,
                )
                for value in values
            ]
            if entries and entries[0].first_sample != cursor:
                raise NativeCaptureFinalizationConflict("native capture manifest timeline is discontinuous")
            for entry in entries:
                if entry.first_sample != cursor:
                    raise NativeCaptureFinalizationConflict("native capture manifest timeline is discontinuous")
                cursor += entry.sample_count
            if cursor != epoch.recorded_through_sample:
                raise NativeCaptureFinalizationConflict("native capture epoch boundary diverges from its manifest")
            digest = native_capture_manifest_sha256(
                expected_epoch=epoch.stream_epoch,
                final_sequence=int(epoch.last_sequence),
                recorded_through_sample=int(epoch.recorded_through_sample),
                manifest_revision=epoch.manifest_revision,
                entries=entries,
            )
            if digest != epoch.manifest_sha256:
                raise NativeCaptureFinalizationConflict("native capture manifest authentication failed")

        last_epoch = epochs[-1]
        if (
            capture.sealed_through_sample != cursor
            or capture.seal_manifest_revision != last_epoch.manifest_revision
            or capture.seal_manifest_sha256 != last_epoch.manifest_sha256
        ):
            raise NativeCaptureFinalizationConflict("native capture seal diverges from its final epoch")

        declared = {(value.stream_epoch, value.sequence): value for value in declarations}
        for batch in batches:
            if (
                batch.capture_id != capture.id
                or batch.meeting_id != capture.meeting_id
                or batch.owner_user_id != capture.owner_user_id
                or (batch.stream_epoch, batch.sequence) not in declared
            ):
                raise NativeCaptureFinalizationConflict("native capture batch ownership diverges")
        valid_reasons = {value.value for value in NativeCaptureGapReason}
        for gap in gaps:
            values = [
                declared.get((gap.stream_epoch, sequence))
                for sequence in range(int(gap.from_sequence), int(gap.to_sequence) + 1)
            ]
            if (
                gap.capture_id != capture.id
                or gap.meeting_id != capture.meeting_id
                or gap.owner_user_id != capture.owner_user_id
                or gap.reason not in valid_reasons
                or not values
                or any(value is None for value in values)
                or values[0].first_sample != gap.start_sample  # type: ignore[union-attr]
                or values[-1].end_sample != gap.end_sample  # type: ignore[union-attr]
                or any(value.manifest_revision != gap.manifest_revision for value in values if value is not None)
            ):
                raise NativeCaptureFinalizationConflict("native capture gap diverges from its frozen manifest")

    @staticmethod
    def _batch_matches_manifest(
        batch: MeetingNativeCaptureBatch,
        declaration: MeetingNativeCaptureManifestEntry | None,
    ) -> bool:
        return bool(
            declaration is not None
            and declaration.first_sample == batch.first_sample
            and declaration.sample_count == batch.sample_count
            and declaration.captured_monotonic_ns == batch.captured_monotonic_ns
            and declaration.encoding == batch.encoding
            and declaration.sample_rate == batch.sample_rate
            and declaration.channels == batch.channels
            and declaration.byte_size == batch.byte_size
            and declaration.sha256 == batch.sha256
        )

    async def _register_batch(
        self,
        finalization_id: str,
        expected_attempt: int,
        capture: MeetingNativeCapture,
        batch: MeetingNativeCaptureBatch,
        payload: bytes,
    ) -> None:
        start_ms = batch.first_sample // 16
        duration_ms = batch.sample_count // 16
        end_ms = start_ms + duration_ms
        async with self.session_factory() as session:
            if (
                await self._owned_processing(
                    session,
                    finalization_id,
                    expected_attempt,
                    lock=True,
                )
                is None
            ):
                raise NativeCaptureFinalizationLeaseLost("native capture finalization lease was lost")
            current_capture = await session.get(MeetingNativeCapture, capture.id)
            if current_capture is None or current_capture.state != NativeCaptureState.SEALED.value:
                raise NativeCaptureFinalizationLeaseLost("native capture finalization was revoked")
            current_batch = await session.get(MeetingNativeCaptureBatch, batch.id)
            if (
                current_batch is None
                or current_batch.capture_id != capture.id
                or current_batch.sha256 != hashlib.sha256(payload).hexdigest()
                or current_batch.byte_size != len(payload)
            ):
                raise NativeCaptureFinalizationIntegrityError("native capture batch changed during packaging")
            link = (
                await session.exec(
                    select(MeetingNativeCaptureAudioLink).where(MeetingNativeCaptureAudioLink.batch_id == batch.id)
                )
            ).first()
            if link is not None:
                chunk = await session.get(MeetingAudioChunk, link.audio_chunk_id)
                if (
                    link.capture_id != capture.id
                    or link.meeting_id != capture.meeting_id
                    or link.stream_epoch != batch.stream_epoch
                    or link.sequence != batch.sequence
                    or link.source_sha256 != batch.sha256
                    or chunk is None
                    or not self._same_chunk(chunk, current_batch, start_ms, duration_ms)
                ):
                    raise NativeCaptureFinalizationConflict("native capture provenance link conflicts")
                await asyncio.to_thread(
                    self.audio_store.read_verified_chunk,
                    capture.owner_user_id,
                    capture.meeting_id,
                    chunk,
                    max_bytes=self.settings.max_batch_bytes,
                )
                return

            coordinate = (
                await session.exec(
                    select(MeetingAudioChunk).where(
                        MeetingAudioChunk.meeting_id == capture.meeting_id,
                        MeetingAudioChunk.stream_epoch == batch.stream_epoch,
                        MeetingAudioChunk.sequence == batch.sequence,
                    )
                )
            ).first()
            if coordinate is not None and not self._same_chunk(coordinate, current_batch, start_ms, duration_ms):
                raise NativeCaptureFinalizationConflict("meeting audio coordinate conflicts with native capture")
            chunk = coordinate
            if chunk is None:
                chunk = (
                    await session.exec(
                        select(MeetingAudioChunk).where(
                            MeetingAudioChunk.meeting_id == capture.meeting_id,
                            MeetingAudioChunk.start_ms == start_ms,
                            MeetingAudioChunk.duration_ms == duration_ms,
                            MeetingAudioChunk.sha256 == batch.sha256,
                            MeetingAudioChunk.byte_size == batch.byte_size,
                            MeetingAudioChunk.state != AudioChunkState.DELETED.value,
                        )
                    )
                ).first()
            if chunk is None:
                overlap = (
                    await session.exec(
                        select(MeetingAudioChunk).where(
                            MeetingAudioChunk.meeting_id == capture.meeting_id,
                            MeetingAudioChunk.state != AudioChunkState.DELETED.value,
                            MeetingAudioChunk.start_ms < end_ms,
                            MeetingAudioChunk.start_ms + MeetingAudioChunk.duration_ms > start_ms,
                        )
                    )
                ).first()
                if overlap is not None:
                    raise NativeCaptureFinalizationConflict("meeting audio timeline conflicts with native capture")
                persisted = await asyncio.to_thread(
                    self.audio_store.persist_chunk,
                    capture.owner_user_id,
                    capture.meeting_id,
                    batch.stream_epoch,
                    batch.sequence,
                    payload,
                )
                chunk = MeetingAudioChunk(
                    meeting_id=capture.meeting_id,
                    stream_epoch=batch.stream_epoch,
                    sequence=batch.sequence,
                    start_ms=start_ms,
                    duration_ms=duration_ms,
                    storage_key=persisted.storage_key,
                    sha256=persisted.sha256,
                    byte_size=persisted.byte_size,
                    codec="pcm_s16le",
                    sample_rate=16_000,
                    channels=1,
                    state=AudioChunkState.VERIFIED.value,
                )
                session.add(chunk)
                await session.flush()
            else:
                persisted = None
                await asyncio.to_thread(
                    self.audio_store.read_verified_chunk,
                    capture.owner_user_id,
                    capture.meeting_id,
                    chunk,
                    max_bytes=self.settings.max_batch_bytes,
                )
            session.add(
                MeetingNativeCaptureAudioLink(
                    capture_id=capture.id,
                    batch_id=batch.id,
                    meeting_id=capture.meeting_id,
                    stream_epoch=batch.stream_epoch,
                    sequence=batch.sequence,
                    audio_chunk_id=chunk.id,
                    source_sha256=batch.sha256,
                )
            )
            if (
                await self._owned_processing(
                    session,
                    finalization_id,
                    expected_attempt,
                )
                is None
            ):
                await session.rollback()
                if persisted is not None:
                    await asyncio.to_thread(self.audio_store.remove_chunk_if_created, persisted)
                raise NativeCaptureFinalizationLeaseLost("native capture finalization lease was lost after write")
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise NativeCaptureFinalizationRetryable("native capture provenance registration raced") from exc

    @staticmethod
    def _same_chunk(
        chunk: MeetingAudioChunk,
        batch: MeetingNativeCaptureBatch,
        start_ms: int,
        duration_ms: int,
    ) -> bool:
        return (
            chunk.start_ms == start_ms
            and chunk.duration_ms == duration_ms
            and chunk.sha256 == batch.sha256
            and chunk.byte_size == batch.byte_size
            and chunk.codec == "pcm_s16le"
            and chunk.sample_rate == 16_000
            and chunk.channels == 1
            and chunk.state != AudioChunkState.DELETED.value
        )

    async def _linked_chunks(
        self,
        finalization_id: str,
        expected_attempt: int,
        capture_id: str,
    ) -> list[MeetingAudioChunk]:
        async with self.session_factory() as session:
            if await self._owned_processing(session, finalization_id, expected_attempt) is None:
                raise NativeCaptureFinalizationLeaseLost("native capture finalization lease was lost")
            return list(
                (
                    await session.exec(
                        select(MeetingAudioChunk)
                        .join(
                            MeetingNativeCaptureAudioLink,
                            MeetingNativeCaptureAudioLink.audio_chunk_id == MeetingAudioChunk.id,
                        )
                        .where(
                            MeetingNativeCaptureAudioLink.capture_id == capture_id,
                            MeetingAudioChunk.state != AudioChunkState.DELETED.value,
                        )
                        .order_by(
                            MeetingAudioChunk.start_ms,
                            MeetingAudioChunk.stream_epoch,
                            MeetingAudioChunk.sequence,
                        )
                    )
                ).all()
            )

    async def _stop_meeting(
        self,
        finalization_id: str,
        expected_attempt: int,
        capture_id: str,
        meeting_id: str,
        owner_user_id: int,
    ) -> None:
        await self._assert_processing_capture(finalization_id, expected_attempt, capture_id)
        async with self.session_factory() as session:
            meeting = await session.get(MeetingSession, meeting_id)
            if meeting is None or meeting.owner_user_id != owner_user_id:
                raise NativeCaptureFinalizationConflict("native capture meeting was not found")
            if meeting.state == MeetingState.DELETED.value:
                raise NativeCaptureFinalizationConflict("deleted meeting cannot publish native audio")
            await session.exec(
                update(MeetingStreamLease)
                .where(MeetingStreamLease.meeting_id == meeting_id)
                .values(lease_until=utcnow(), updated_at=utcnow())
            )
            await session.commit()
        async with self.session_factory() as session:
            repository = MeetingRepository(session)
            try:
                meeting, _, _ = await repository.transition_session(meeting_id, owner_user_id, "stop")
                if meeting.state not in {MeetingState.STOPPED.value, MeetingState.ARCHIVED.value}:
                    await repository.transition_session(meeting_id, owner_user_id, "mark_stopped")
            except MeetingInvalidOperation as exc:
                raise NativeCaptureFinalizationConflict("meeting cannot be stopped after native seal") from exc

    async def _mark_ready(
        self,
        finalization_id: str,
        expected_attempt: int,
        capture_id: str,
        *,
        meeting_id: str,
        owner_user_id: int,
        wav_sha256: str,
        wav_size: int,
        audio_chunk_count: int,
        accepted_gap_count: int,
    ) -> None:
        async with self.session_factory() as session:
            meeting = (
                await session.exec(
                    select(MeetingSession)
                    .where(
                        MeetingSession.id == meeting_id,
                        MeetingSession.owner_user_id == owner_user_id,
                        MeetingSession.state != MeetingState.DELETED.value,
                    )
                    .with_for_update()
                )
            ).first()
            if meeting is None:
                raise NativeCaptureFinalizationLeaseLost("native capture meeting was revoked")
            finalization = await self._owned_processing(
                session,
                finalization_id,
                expected_attempt,
                lock=True,
            )
            capture = await session.get(MeetingNativeCapture, capture_id)
            if (
                finalization is None
                or capture is None
                or finalization.meeting_id != meeting.id
                or finalization.owner_user_id != owner_user_id
                or finalization.capture_id != capture.id
                or capture.state != NativeCaptureState.SEALED.value
            ):
                raise NativeCaptureFinalizationLeaseLost("native capture finalization lease was lost")
            now = utcnow()
            finalization.state = NativeCaptureFinalizationState.READY.value
            finalization.lease_owner = None
            finalization.lease_until = None
            finalization.retry_after = None
            finalization.public_error_code = None
            finalization.internal_diagnostic = None
            finalization.wav_sha256 = wav_sha256
            finalization.wav_byte_size = wav_size
            finalization.audio_chunk_count = audio_chunk_count
            finalization.accepted_gap_count = accepted_gap_count
            finalization.ready_at = now
            finalization.updated_at = now
            capture.server_playback_state = NativeCapturePlaybackState.READY.value
            capture.ingest_complete = True
            capture.updated_at = now
            session.add(finalization)
            session.add(capture)
            await MeetingEventStore(session).append(
                capture.meeting_id,
                "native_capture.server_playback.ready",
                {
                    "capture_id": capture.id,
                    "audio_chunk_count": audio_chunk_count,
                    "accepted_gap_count": accepted_gap_count,
                    "wav_byte_size": wav_size,
                    "wav_sha256": wav_sha256,
                },
            )
            await session.commit()

    async def _handoff_final_transcript(self, finalization_id: str) -> None:
        async with self.session_factory() as session:
            finalization = await session.get(MeetingNativeCaptureFinalization, finalization_id)
            if finalization is None or finalization.state != NativeCaptureFinalizationState.READY.value:
                return
            if finalization.final_transcript_job_id is not None:
                return
            meeting_id = finalization.meeting_id
            owner_user_id = finalization.owner_user_id
        async with self.session_factory() as session:
            meeting = (
                await session.exec(
                    select(MeetingSession)
                    .where(
                        MeetingSession.id == meeting_id,
                        MeetingSession.owner_user_id == owner_user_id,
                        MeetingSession.state != MeetingState.DELETED.value,
                    )
                    .with_for_update()
                )
            ).first()
            finalization = (
                await session.exec(
                    select(MeetingNativeCaptureFinalization)
                    .where(
                        MeetingNativeCaptureFinalization.id == finalization_id,
                        MeetingNativeCaptureFinalization.state == NativeCaptureFinalizationState.READY.value,
                        MeetingNativeCaptureFinalization.final_transcript_job_id.is_(None),
                    )
                    .with_for_update()
                )
            ).first()
            capture = (
                (
                    await session.exec(
                        select(MeetingNativeCapture).where(
                            MeetingNativeCapture.id == finalization.capture_id,
                            MeetingNativeCapture.state == NativeCaptureState.SEALED.value,
                        )
                    )
                ).first()
                if finalization is not None
                else None
            )
            if meeting is None or finalization is None or capture is None:
                return
            _, _, job, _ = await MeetingRepository(session).finalize_session(
                meeting_id,
                owner_user_id,
            )
            job_id = job.id if job is not None else None
        if job_id is None:
            raise NativeCaptureFinalizationRetryable("final transcript handoff did not return a job")
        async with self.session_factory() as session:
            finalization = await session.get(MeetingNativeCaptureFinalization, finalization_id)
            if finalization is None or finalization.state != NativeCaptureFinalizationState.READY.value:
                return
            finalization.final_transcript_job_id = job_id
            finalization.updated_at = utcnow()
            session.add(finalization)
            await session.commit()

    async def _fail(
        self,
        finalization_id: str,
        expected_attempt: int,
        code: str,
        *,
        retryable: bool,
    ) -> None:
        async with self.session_factory() as session:
            value = await session.get(MeetingNativeCaptureFinalization, finalization_id)
            if value is None or value.state != NativeCaptureFinalizationState.PROCESSING.value:
                return
            if value.lease_owner != self.worker_id or value.attempt != expected_attempt:
                return
            capture = await session.get(MeetingNativeCapture, value.capture_id)
            can_retry = retryable and value.attempt < value.max_attempts
            value.state = (
                NativeCaptureFinalizationState.RETRY_WAIT.value
                if can_retry
                else NativeCaptureFinalizationState.FAILED.value
            )
            value.lease_owner = None
            value.lease_until = None
            value.retry_after = (
                utcnow() + timedelta(seconds=self.settings.finalization_retry_delay_seconds) if can_retry else None
            )
            value.public_error_code = code[:64]
            value.internal_diagnostic = code[:64]
            value.updated_at = utcnow()
            session.add(value)
            if capture is not None:
                capture.server_playback_state = (
                    NativeCapturePlaybackState.PENDING_PACKAGING.value
                    if can_retry
                    else NativeCapturePlaybackState.FAILED.value
                )
                capture.updated_at = utcnow()
                session.add(capture)
            await session.commit()

    async def _heartbeat(
        self,
        finalization_id: str,
        expected_attempt: int,
        stop: asyncio.Event,
    ) -> bool:
        interval = max(1.0, self.settings.finalization_lease_seconds / 3)
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                break
            except TimeoutError:
                pass
            async with self.session_factory() as session:
                result = await session.exec(
                    update(MeetingNativeCaptureFinalization)
                    .where(
                        MeetingNativeCaptureFinalization.id == finalization_id,
                        MeetingNativeCaptureFinalization.state == NativeCaptureFinalizationState.PROCESSING.value,
                        MeetingNativeCaptureFinalization.lease_owner == self.worker_id,
                        MeetingNativeCaptureFinalization.attempt == expected_attempt,
                    )
                    .values(
                        lease_until=utcnow() + timedelta(seconds=self.settings.finalization_lease_seconds),
                        updated_at=utcnow(),
                    )
                    .returning(MeetingNativeCaptureFinalization.id)
                )
                owned = result.scalar_one_or_none() is not None
                await session.commit()
                if not owned:
                    return False
        async with self.session_factory() as session:
            value = await session.get(MeetingNativeCaptureFinalization, finalization_id)
            return bool(
                value is not None
                and (
                    value.attempt != expected_attempt
                    or value.state
                    in {
                        NativeCaptureFinalizationState.READY.value,
                        NativeCaptureFinalizationState.RETRY_WAIT.value,
                        NativeCaptureFinalizationState.FAILED.value,
                        NativeCaptureFinalizationState.PENDING_UPLOAD.value,
                    }
                    or value.lease_owner == self.worker_id
                )
            )

    async def _is_terminal_or_pending(self, finalization_id: str, expected_attempt: int) -> bool:
        async with self.session_factory() as session:
            value = await session.get(MeetingNativeCaptureFinalization, finalization_id)
            return bool(
                value is None
                or value.attempt != expected_attempt
                or value.state
                in {
                    NativeCaptureFinalizationState.PENDING_UPLOAD.value,
                    NativeCaptureFinalizationState.RETRY_WAIT.value,
                    NativeCaptureFinalizationState.READY.value,
                    NativeCaptureFinalizationState.FAILED.value,
                }
            )

    async def _owned_processing(
        self,
        session: AsyncSession,
        finalization_id: str,
        expected_attempt: int,
        *,
        lock: bool = False,
    ) -> MeetingNativeCaptureFinalization | None:
        statement = select(MeetingNativeCaptureFinalization).where(
            MeetingNativeCaptureFinalization.id == finalization_id,
            MeetingNativeCaptureFinalization.state == NativeCaptureFinalizationState.PROCESSING.value,
            MeetingNativeCaptureFinalization.lease_owner == self.worker_id,
            MeetingNativeCaptureFinalization.attempt == expected_attempt,
            MeetingNativeCaptureFinalization.lease_until.is_not(None),
            MeetingNativeCaptureFinalization.lease_until > utcnow(),
        )
        if lock:
            statement = statement.with_for_update()
        return (await session.exec(statement)).first()

    async def _assert_processing_capture(
        self,
        finalization_id: str,
        expected_attempt: int,
        capture_id: str,
    ) -> None:
        async with self.session_factory() as session:
            finalization = await self._owned_processing(
                session,
                finalization_id,
                expected_attempt,
            )
            capture = await session.get(MeetingNativeCapture, capture_id)
            if (
                finalization is None
                or finalization.capture_id != capture_id
                or capture is None
                or capture.state != NativeCaptureState.SEALED.value
            ):
                raise NativeCaptureFinalizationLeaseLost("native capture finalization fence was lost")

    async def _mark_pending_upload(
        self,
        session: AsyncSession,
        finalization: MeetingNativeCaptureFinalization,
        capture: MeetingNativeCapture,
    ) -> None:
        finalization.state = NativeCaptureFinalizationState.PENDING_UPLOAD.value
        finalization.lease_owner = None
        finalization.lease_until = None
        finalization.retry_after = None
        finalization.public_error_code = None
        finalization.internal_diagnostic = None
        finalization.updated_at = utcnow()
        capture.ingest_complete = False
        capture.server_playback_state = NativeCapturePlaybackState.PENDING_UPLOAD.value
        capture.updated_at = utcnow()
        session.add(finalization)
        session.add(capture)
        await session.commit()

    @staticmethod
    async def _manifest_rows(
        session: AsyncSession,
        capture_id: str,
    ) -> tuple[
        list[MeetingNativeCaptureEpoch],
        list[MeetingNativeCaptureBatch],
        list[MeetingNativeCaptureGap],
    ]:
        epochs = list(
            (
                await session.exec(
                    select(MeetingNativeCaptureEpoch)
                    .where(MeetingNativeCaptureEpoch.capture_id == capture_id)
                    .order_by(MeetingNativeCaptureEpoch.stream_epoch)
                )
            ).all()
        )
        batches = list(
            (
                await session.exec(
                    select(MeetingNativeCaptureBatch)
                    .where(MeetingNativeCaptureBatch.capture_id == capture_id)
                    .order_by(
                        MeetingNativeCaptureBatch.first_sample,
                        MeetingNativeCaptureBatch.stream_epoch,
                        MeetingNativeCaptureBatch.sequence,
                    )
                )
            ).all()
        )
        gaps = list(
            (
                await session.exec(
                    select(MeetingNativeCaptureGap)
                    .where(MeetingNativeCaptureGap.capture_id == capture_id)
                    .order_by(MeetingNativeCaptureGap.start_sample)
                )
            ).all()
        )
        return epochs, batches, gaps

    @staticmethod
    def _hash_file(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
                size += len(block)
        return digest.hexdigest(), size

    @staticmethod
    def _remove_if_same_file(path: Path, expected_device: int, expected_inode: int) -> None:
        try:
            current = path.stat(follow_symlinks=False)
            if stat.S_ISREG(current.st_mode) and current.st_dev == expected_device and current.st_ino == expected_inode:
                path.unlink(missing_ok=True)
        except OSError:
            return


__all__ = [
    "MeetingNativeCaptureFinalizationWorker",
    "NativeCaptureFinalizationConflict",
    "NativeCaptureFinalizationError",
    "NativeCaptureFinalizationIntegrityError",
    "NativeCaptureFinalizationLeaseLost",
    "NativeCaptureFinalizationRetryable",
]
