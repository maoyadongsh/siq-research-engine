"""Durable ingest and checkpoint service for optional iOS native capture."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, delete, func, or_, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from services.auth_service import User
from services.meeting_config import MeetingSettings
from services.meeting_contracts import (
    MeetingEvent,
    MeetingSession,
    MeetingState,
    MeetingStreamLease,
    MeetingStreamTicket,
    utcnow,
)
from services.meeting_event_store import MeetingEventStore
from services.meeting_native_capture_config import MeetingNativeCaptureSettings
from services.meeting_native_capture_contracts import (
    CAPTURE_TOKEN_PURPOSE,
    CAPTURE_TOKEN_SCOPES,
    MeetingNativeCapture,
    MeetingNativeCaptureBatch,
    MeetingNativeCaptureEpoch,
    MeetingNativeCaptureFinalization,
    MeetingNativeCaptureGap,
    MeetingNativeCaptureManifestEntry,
    MeetingNativeCaptureToken,
    NativeCaptureBatchMetadata,
    NativeCaptureBatchResponse,
    NativeCaptureBoundaryRequest,
    NativeCaptureCaptureCheckpoint,
    NativeCaptureCheckpointResponse,
    NativeCaptureCreateRequest,
    NativeCaptureCreateResponse,
    NativeCaptureEpochCheckpoint,
    NativeCaptureEpochState,
    NativeCaptureFinalizationCheckpoint,
    NativeCaptureFinalizationState,
    NativeCaptureGapRequest,
    NativeCaptureGapResponse,
    NativeCaptureIngestCheckpoint,
    NativeCaptureManifestEntry,
    NativeCapturePlaybackState,
    NativeCaptureRealtimeCheckpoint,
    NativeCaptureSealResponse,
    NativeCaptureState,
    NativeCaptureStatusResponse,
    NativeCaptureTokenResponse,
)
from services.meeting_native_capture_storage import (
    MeetingNativeCaptureStorage,
    MeetingNativeCaptureStorageError,
)
from services.meeting_stream_ticket import normalize_origin


class MeetingNativeCaptureError(RuntimeError):
    code = "NATIVE_CAPTURE_FAILED"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class MeetingNativeCaptureNotFound(MeetingNativeCaptureError):
    code = "NATIVE_CAPTURE_NOT_FOUND"


class MeetingNativeCaptureUnauthorized(MeetingNativeCaptureError):
    code = "NATIVE_CAPTURE_TOKEN_INVALID"


class MeetingNativeCaptureForbidden(MeetingNativeCaptureError):
    code = "NATIVE_CAPTURE_SCOPE_DENIED"


class MeetingNativeCaptureConflict(MeetingNativeCaptureError):
    code = "NATIVE_CAPTURE_CONFLICT"


class MeetingNativeCaptureInvalid(MeetingNativeCaptureError):
    code = "NATIVE_CAPTURE_INVALID"


class MeetingNativeCaptureTooLarge(MeetingNativeCaptureError):
    code = "NATIVE_CAPTURE_LIMIT_EXCEEDED"


class MeetingNativeCaptureUnavailable(MeetingNativeCaptureError):
    code = "NATIVE_CAPTURE_UNAVAILABLE"


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _json_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _token_hash(value: str) -> str:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise MeetingNativeCaptureUnauthorized("native capture token is invalid") from exc
    if len(encoded) < 32 or len(encoded) > 512:
        raise MeetingNativeCaptureUnauthorized("native capture token is invalid")
    return hashlib.sha256(encoded).hexdigest()


def _device_installation_hash(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) < 16 or len(cleaned) > 256:
        raise MeetingNativeCaptureUnauthorized("native capture device binding is invalid")
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


def native_capture_status(value: MeetingNativeCapture) -> NativeCaptureStatusResponse:
    return NativeCaptureStatusResponse(
        id=value.id,
        meeting_id=value.meeting_id,
        state=value.state,
        encoding=value.encoding,
        sample_rate=value.sample_rate,
        channels=value.channels,
        current_epoch=value.current_epoch,
        total_bytes=value.total_bytes,
        total_samples=value.total_samples,
        sealed_through_sample=value.sealed_through_sample,
        ingest_complete=value.ingest_complete,
        server_playback_state=value.server_playback_state,
        created_at=value.created_at,
        updated_at=value.updated_at,
        sealed_at=value.sealed_at,
        revoked_at=value.revoked_at,
    )


def _missing_integer_ranges(values: set[int], end_inclusive: int) -> list[dict[str, int]]:
    if end_inclusive < 0:
        return []
    ranges: list[dict[str, int]] = []
    cursor = 0
    for value in sorted(item for item in values if 0 <= item <= end_inclusive):
        if value > cursor:
            ranges.append({"start": cursor, "end": value - 1})
        cursor = value + 1
    if cursor <= end_inclusive:
        ranges.append({"start": cursor, "end": end_inclusive})
    return ranges


def _missing_half_open_ranges(
    intervals: list[tuple[int, int]],
    end_exclusive: int,
) -> list[dict[str, int]]:
    if end_exclusive <= 0:
        return []
    cursor = 0
    missing: list[dict[str, int]] = []
    for start, end in sorted(intervals):
        if end <= 0 or start >= end_exclusive:
            continue
        start = max(0, start)
        end = min(end_exclusive, end)
        if start > cursor:
            missing.append({"start": cursor, "end": start})
        cursor = max(cursor, end)
        if cursor >= end_exclusive:
            break
    if cursor < end_exclusive:
        missing.append({"start": cursor, "end": end_exclusive})
    return missing


@dataclass(frozen=True, slots=True)
class NativeCaptureCoverage:
    missing_sequences: dict[int, list[dict[str, int]]]
    missing_sample_ranges: list[dict[str, int]]
    audio_missing_sample_ranges: list[dict[str, int]]
    persisted_through_sample: int
    accounted_through_sample: int
    highest_received_sample: int
    complete: bool
    accepted_gap_count: int


def native_capture_coverage(
    capture: MeetingNativeCapture,
    epochs: list[MeetingNativeCaptureEpoch],
    batches: list[MeetingNativeCaptureBatch],
    gaps: list[MeetingNativeCaptureGap],
) -> NativeCaptureCoverage:
    batches_by_epoch: dict[int, list[MeetingNativeCaptureBatch]] = {}
    gaps_by_epoch: dict[int, list[MeetingNativeCaptureGap]] = {}
    for batch in batches:
        batches_by_epoch.setdefault(batch.stream_epoch, []).append(batch)
    for gap in gaps:
        gaps_by_epoch.setdefault(gap.stream_epoch, []).append(gap)

    missing_sequences: dict[int, list[dict[str, int]]] = {}
    for epoch in epochs:
        epoch_batches = batches_by_epoch.get(epoch.stream_epoch, [])
        epoch_gaps = gaps_by_epoch.get(epoch.stream_epoch, [])
        highest_received = max((int(value.sequence) for value in epoch_batches), default=-1)
        expected_last = epoch.last_sequence if epoch.last_sequence is not None else highest_received
        intervals = [(int(value.sequence), int(value.sequence) + 1) for value in epoch_batches]
        intervals.extend((int(value.from_sequence), int(value.to_sequence) + 1) for value in epoch_gaps)
        missing_sequences[epoch.stream_epoch] = [
            {"start": value["start"], "end": value["end"] - 1}
            for value in _missing_half_open_ranges(intervals, expected_last + 1)
        ]

    highest_received_sample = max((int(value.end_sample) for value in batches), default=0)
    target_sample = capture.sealed_through_sample
    if target_sample is None:
        target_sample = highest_received_sample
    audio_intervals = [(int(value.first_sample), int(value.end_sample)) for value in batches]
    accounted_intervals = [*audio_intervals]
    accounted_intervals.extend((int(value.start_sample), int(value.end_sample)) for value in gaps)
    audio_missing = _missing_half_open_ranges(audio_intervals, target_sample)
    missing_samples = _missing_half_open_ranges(accounted_intervals, target_sample)
    persisted_through = audio_missing[0]["start"] if audio_missing else target_sample
    accounted_through = missing_samples[0]["start"] if missing_samples else target_sample
    complete = (
        capture.state == NativeCaptureState.SEALED.value and not missing_samples and not any(missing_sequences.values())
    )
    return NativeCaptureCoverage(
        missing_sequences=missing_sequences,
        missing_sample_ranges=missing_samples,
        audio_missing_sample_ranges=audio_missing,
        persisted_through_sample=persisted_through,
        accounted_through_sample=accounted_through,
        highest_received_sample=highest_received_sample,
        complete=complete,
        accepted_gap_count=len(gaps),
    )


class MeetingNativeCaptureRepository:
    def __init__(
        self,
        session: AsyncSession,
        storage: MeetingNativeCaptureStorage,
        settings: MeetingNativeCaptureSettings,
        *,
        meeting_settings: MeetingSettings | None = None,
    ) -> None:
        self.session = session
        self.storage = storage
        self.settings = settings
        self.meeting_settings = meeting_settings or MeetingSettings.from_env()
        self.events = MeetingEventStore(session)

    @staticmethod
    def _validate_token_grant(
        token: MeetingNativeCaptureToken,
        scope: str,
        now: datetime,
    ) -> None:
        if token.revoked_at is not None:
            raise MeetingNativeCaptureUnauthorized(
                "native capture token is revoked",
                code="NATIVE_CAPTURE_TOKEN_REVOKED",
            )
        if _aware(token.expires_at) <= _aware(now):
            raise MeetingNativeCaptureUnauthorized(
                "native capture token is expired",
                code="NATIVE_CAPTURE_TOKEN_EXPIRED",
            )
        try:
            scopes = set(json.loads(token.scopes_json))
        except (TypeError, json.JSONDecodeError):
            scopes = set()
        if scope not in scopes:
            raise MeetingNativeCaptureForbidden("native capture token does not grant this operation")

    async def _owned_capture(
        self,
        meeting_id: str,
        capture_id: str,
        owner_user_id: int,
        *,
        lock: bool = False,
    ) -> MeetingNativeCapture:
        if lock:
            await self.session.exec(select(User.id).where(User.id == owner_user_id).with_for_update())
            meeting = (
                await self.session.exec(
                    select(MeetingSession.id)
                    .where(
                        MeetingSession.id == meeting_id,
                        MeetingSession.owner_user_id == owner_user_id,
                        MeetingSession.state != MeetingState.DELETED.value,
                    )
                    .with_for_update()
                )
            ).first()
            if meeting is None:
                raise MeetingNativeCaptureNotFound("native capture was not found")
            value = (
                await self.session.exec(
                    select(MeetingNativeCapture)
                    .where(
                        MeetingNativeCapture.id == capture_id,
                        MeetingNativeCapture.meeting_id == meeting_id,
                        MeetingNativeCapture.owner_user_id == owner_user_id,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).first()
            if value is None:
                raise MeetingNativeCaptureNotFound("native capture was not found")
            return value
        statement = (
            select(MeetingNativeCapture)
            .join(MeetingSession, MeetingSession.id == MeetingNativeCapture.meeting_id)
            .where(
                MeetingNativeCapture.id == capture_id,
                MeetingNativeCapture.meeting_id == meeting_id,
                MeetingNativeCapture.owner_user_id == owner_user_id,
                MeetingSession.state != MeetingState.DELETED.value,
            )
        )
        value = (await self.session.exec(statement)).first()
        if value is None:
            raise MeetingNativeCaptureNotFound("native capture was not found")
        return value

    async def _authorized_capture(
        self,
        meeting_id: str,
        capture_id: str,
        raw_token: str,
        scope: str,
        *,
        device_installation_id: str | None = None,
    ) -> tuple[MeetingNativeCapture, MeetingNativeCaptureToken]:
        token_hash = _token_hash(raw_token)
        initial_token = (
            await self.session.exec(
                select(MeetingNativeCaptureToken).where(
                    MeetingNativeCaptureToken.token_hash == token_hash,
                    MeetingNativeCaptureToken.purpose == CAPTURE_TOKEN_PURPOSE,
                )
            )
        ).first()
        if initial_token is None:
            raise MeetingNativeCaptureUnauthorized("native capture token is invalid")
        if initial_token.meeting_id != meeting_id or initial_token.capture_id != capture_id:
            raise MeetingNativeCaptureNotFound("native capture was not found")
        self._validate_token_grant(initial_token, scope, utcnow())

        # Keep the owner -> meeting -> capture -> token order used by all
        # native admission, deletion, and retention paths.
        # The first token read only discovers the owner. Authorization is decided
        # from a fresh locked token row after the capture lock, so a concurrent
        # renewal or revocation cannot race an upload ACK.
        capture = await self._owned_capture(
            meeting_id,
            capture_id,
            initial_token.owner_user_id,
            lock=True,
        )
        token = (
            await self.session.exec(
                select(MeetingNativeCaptureToken)
                .where(
                    MeetingNativeCaptureToken.id == initial_token.id,
                    MeetingNativeCaptureToken.token_hash == token_hash,
                    MeetingNativeCaptureToken.purpose == CAPTURE_TOKEN_PURPOSE,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).first()
        if token is None:
            raise MeetingNativeCaptureUnauthorized("native capture token is invalid")
        now = utcnow()
        self._validate_token_grant(token, scope, now)
        if (
            token.meeting_id != meeting_id
            or token.capture_id != capture_id
            or token.owner_user_id != capture.owner_user_id
        ):
            raise MeetingNativeCaptureNotFound("native capture was not found")
        if capture.state == NativeCaptureState.REVOKED.value:
            raise MeetingNativeCaptureUnauthorized(
                "native capture has been revoked",
                code="NATIVE_CAPTURE_TOKEN_REVOKED",
            )
        if device_installation_id is not None and not secrets.compare_digest(
            capture.device_installation_hash,
            _device_installation_hash(device_installation_id),
        ):
            raise MeetingNativeCaptureUnauthorized(
                "native capture device binding does not match",
                code="NATIVE_CAPTURE_DEVICE_MISMATCH",
            )
        token.last_used_at = now
        self.session.add(token)
        return capture, token

    async def _issue_capture_token(
        self,
        capture: MeetingNativeCapture,
        *,
        revoke_existing: bool,
    ) -> tuple[str, MeetingNativeCaptureToken]:
        now = utcnow()
        if revoke_existing:
            await self.session.exec(
                update(MeetingNativeCaptureToken)
                .where(
                    MeetingNativeCaptureToken.capture_id == capture.id,
                    MeetingNativeCaptureToken.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
        raw = secrets.token_urlsafe(48)
        token = MeetingNativeCaptureToken(
            token_hash=_token_hash(raw),
            capture_id=capture.id,
            meeting_id=capture.meeting_id,
            owner_user_id=capture.owner_user_id,
            scopes_json=json.dumps(CAPTURE_TOKEN_SCOPES, separators=(",", ":")),
            expires_at=now + timedelta(seconds=self.settings.token_ttl_seconds),
        )
        self.session.add(token)
        await self.session.flush()
        return raw, token

    async def create(
        self,
        meeting_id: str,
        owner_user_id: int,
        request: NativeCaptureCreateRequest,
        *,
        idempotency_key: str,
    ) -> NativeCaptureCreateResponse:
        device_hash = _device_installation_hash(request.device_installation_id)
        request_hash = _json_hash(
            {
                "meeting_id": meeting_id,
                "device_installation_hash": device_hash,
                "encoding": request.encoding,
                "sample_rate": request.sample_rate,
                "channels": request.channels,
            }
        )
        # Serialize all active-capture quota admissions and idempotent token
        # rotation for one owner, including requests targeting different
        # meetings. PostgreSQL row locking supplies the cross-process boundary.
        await self.session.exec(select(User.id).where(User.id == owner_user_id).with_for_update())
        existing = (
            await self.session.exec(
                select(MeetingNativeCapture)
                .where(
                    MeetingNativeCapture.owner_user_id == owner_user_id,
                    MeetingNativeCapture.create_idempotency_key == idempotency_key,
                )
                .with_for_update()
            )
        ).first()
        replayed = existing is not None
        if existing is not None:
            if existing.meeting_id != meeting_id or existing.create_request_hash != request_hash:
                raise MeetingNativeCaptureConflict(
                    "native capture idempotency key was reused with a different request",
                    code="NATIVE_CAPTURE_IDEMPOTENCY_CONFLICT",
                )
            if existing.state == NativeCaptureState.REVOKED.value:
                raise MeetingNativeCaptureConflict("native capture has been revoked")
            capture = existing
        else:
            meeting = (
                await self.session.exec(
                    select(MeetingSession)
                    .where(
                        MeetingSession.id == meeting_id,
                        MeetingSession.owner_user_id == owner_user_id,
                    )
                    .with_for_update()
                )
            ).first()
            if meeting is None:
                raise MeetingNativeCaptureNotFound("meeting was not found")
            if meeting.state not in {
                MeetingState.CONNECTING.value,
                MeetingState.LIVE.value,
                MeetingState.PAUSED.value,
                MeetingState.RECONNECTING.value,
            }:
                raise MeetingNativeCaptureConflict(
                    "meeting is not accepting a native capture",
                    code="NATIVE_CAPTURE_MEETING_STATE_INVALID",
                )
            active_count = int(
                (
                    await self.session.exec(
                        select(func.count())
                        .select_from(MeetingNativeCapture)
                        .where(
                            MeetingNativeCapture.owner_user_id == owner_user_id,
                            MeetingNativeCapture.state == NativeCaptureState.ACTIVE.value,
                        )
                    )
                ).one()
                or 0
            )
            if active_count >= self.settings.max_active_per_owner:
                raise MeetingNativeCaptureTooLarge(
                    "native capture active limit was reached",
                    code="NATIVE_CAPTURE_ACTIVE_LIMIT",
                )
            initial_epoch = max(1, meeting.stream_epoch)
            if meeting.stream_epoch < initial_epoch:
                meeting.stream_epoch = initial_epoch
                meeting.last_audio_sequence = -1
                meeting.updated_at = utcnow()
                self.session.add(meeting)
            capture = MeetingNativeCapture(
                meeting_id=meeting_id,
                owner_user_id=owner_user_id,
                device_installation_hash=device_hash,
                create_idempotency_key=idempotency_key,
                create_request_hash=request_hash,
                encoding=request.encoding,
                sample_rate=request.sample_rate,
                channels=request.channels,
                current_epoch=initial_epoch,
                max_total_bytes=self.settings.max_total_bytes,
                max_duration_samples=self.settings.max_duration_seconds * request.sample_rate,
            )
            self.session.add(capture)
            try:
                await self.session.flush()
            except IntegrityError as exc:
                await self.session.rollback()
                raced = (
                    await self.session.exec(
                        select(MeetingNativeCapture)
                        .where(
                            MeetingNativeCapture.owner_user_id == owner_user_id,
                            MeetingNativeCapture.create_idempotency_key == idempotency_key,
                        )
                        .with_for_update()
                    )
                ).first()
                if raced is None or raced.meeting_id != meeting_id or raced.create_request_hash != request_hash:
                    raise MeetingNativeCaptureConflict(
                        "native capture creation conflicted",
                        code="NATIVE_CAPTURE_IDEMPOTENCY_CONFLICT",
                    ) from exc
                if raced.state == NativeCaptureState.REVOKED.value:
                    raise MeetingNativeCaptureConflict("native capture has been revoked") from exc
                capture = raced
                replayed = True
            else:
                self.session.add(
                    MeetingNativeCaptureEpoch(
                        capture_id=capture.id,
                        stream_epoch=initial_epoch,
                    )
                )
                await self.events.append(
                    meeting_id,
                    "native_capture.created",
                    {
                        "capture_id": capture.id,
                        "stream_epoch": initial_epoch,
                        "encoding": request.encoding,
                        "sample_rate": request.sample_rate,
                        "channels": request.channels,
                    },
                )
        raw_token, token = await self._issue_capture_token(capture, revoke_existing=replayed)
        capture.updated_at = utcnow()
        self.session.add(capture)
        await self.session.commit()
        return NativeCaptureCreateResponse(
            capture=native_capture_status(capture),
            capture_token=raw_token,
            token_expires_at=token.expires_at,
            scopes=list(CAPTURE_TOKEN_SCOPES),
            replayed=replayed,
            limits={
                "max_batch_bytes": self.settings.max_batch_bytes,
                "max_total_bytes": capture.max_total_bytes,
                "max_retained_bytes_per_owner": self.settings.max_retained_bytes_per_owner,
                "max_duration_seconds": capture.max_duration_samples // capture.sample_rate,
            },
        )

    async def renew_token(
        self,
        meeting_id: str,
        capture_id: str,
        owner_user_id: int,
    ) -> NativeCaptureTokenResponse:
        capture = await self._owned_capture(meeting_id, capture_id, owner_user_id, lock=True)
        if capture.state == NativeCaptureState.REVOKED.value:
            raise MeetingNativeCaptureConflict("native capture has been revoked")
        raw, token = await self._issue_capture_token(capture, revoke_existing=True)
        await self.session.commit()
        return NativeCaptureTokenResponse(
            capture_id=capture.id,
            capture_token=raw,
            token_expires_at=token.expires_at,
            scopes=list(CAPTURE_TOKEN_SCOPES),
        )

    async def revoke_tokens(
        self,
        meeting_id: str,
        capture_id: str,
        owner_user_id: int,
    ) -> int:
        await self._owned_capture(meeting_id, capture_id, owner_user_id, lock=True)
        now = utcnow()
        result = await self.session.exec(
            update(MeetingNativeCaptureToken)
            .where(
                MeetingNativeCaptureToken.capture_id == capture_id,
                MeetingNativeCaptureToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        await self.session.commit()
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def _epoch(
        self,
        capture_id: str,
        stream_epoch: int,
        *,
        lock: bool = False,
    ) -> MeetingNativeCaptureEpoch:
        statement = select(MeetingNativeCaptureEpoch).where(
            MeetingNativeCaptureEpoch.capture_id == capture_id,
            MeetingNativeCaptureEpoch.stream_epoch == stream_epoch,
        )
        if lock:
            statement = statement.with_for_update()
        value = (await self.session.exec(statement)).first()
        if value is None:
            raise MeetingNativeCaptureConflict(
                "native capture epoch is not available",
                code="NATIVE_CAPTURE_EPOCH_INVALID",
            )
        return value

    @staticmethod
    def _same_batch(
        value: MeetingNativeCaptureBatch,
        *,
        stream_epoch: int,
        sequence: int,
        metadata: NativeCaptureBatchMetadata,
    ) -> bool:
        return (
            value.stream_epoch == stream_epoch
            and value.sequence == sequence
            and value.first_sample == metadata.first_sample
            and value.sample_count == metadata.sample_count
            and value.captured_monotonic_ns == metadata.captured_monotonic_ns
            and value.encoding == metadata.encoding
            and value.sample_rate == metadata.sample_rate
            and value.channels == metadata.channels
            and value.byte_size == metadata.content_length
            and value.sha256 == metadata.sha256
            and value.manifest_revision == metadata.manifest_revision
        )

    async def put_batch(
        self,
        meeting_id: str,
        capture_id: str,
        raw_token: str,
        stream_epoch: int,
        sequence: int,
        metadata: NativeCaptureBatchMetadata,
        stream: AsyncIterator[bytes],
        *,
        device_installation_id: str | None = None,
    ) -> NativeCaptureBatchResponse:
        capture, _ = await self._authorized_capture(
            meeting_id,
            capture_id,
            raw_token,
            "batch:write",
            device_installation_id=device_installation_id,
        )
        if capture.state not in {NativeCaptureState.ACTIVE.value, NativeCaptureState.SEALED.value}:
            raise MeetingNativeCaptureConflict("native capture is not accepting batches")
        if (
            metadata.encoding != capture.encoding
            or metadata.sample_rate != capture.sample_rate
            or metadata.channels != capture.channels
        ):
            raise MeetingNativeCaptureInvalid(
                "native capture audio format does not match the capture contract",
                code="NATIVE_CAPTURE_AUDIO_FORMAT_INVALID",
            )
        expected_size = metadata.sample_count * metadata.channels * 2
        if metadata.content_length != expected_size:
            raise MeetingNativeCaptureInvalid(
                "native capture PCM byte count does not match sample_count",
                code="NATIVE_CAPTURE_SAMPLE_SIZE_MISMATCH",
            )
        if metadata.content_length > self.settings.max_batch_bytes:
            raise MeetingNativeCaptureTooLarge("native capture batch exceeds its byte limit")
        end_sample = metadata.first_sample + metadata.sample_count
        if end_sample > capture.max_duration_samples:
            raise MeetingNativeCaptureTooLarge("native capture duration limit was reached")
        if capture.sealed_through_sample is not None and end_sample > capture.sealed_through_sample:
            raise MeetingNativeCaptureConflict(
                "native capture batch exceeds the sealed sample boundary",
                code="NATIVE_CAPTURE_SEAL_BOUNDARY_CONFLICT",
            )
        epoch = await self._epoch(capture_id, stream_epoch, lock=True)
        frozen_revision = epoch.manifest_revision
        if frozen_revision is None and capture.state == NativeCaptureState.SEALED.value:
            frozen_revision = capture.seal_manifest_revision
        if frozen_revision is not None and metadata.manifest_revision > frozen_revision:
            raise MeetingNativeCaptureConflict(
                "native capture batch exceeds the frozen manifest revision",
                code="NATIVE_CAPTURE_MANIFEST_REVISION_CONFLICT",
            )
        if epoch.last_sequence is not None and sequence > epoch.last_sequence:
            raise MeetingNativeCaptureConflict(
                "native capture batch exceeds the closed epoch sequence",
                code="NATIVE_CAPTURE_EPOCH_BOUNDARY_CONFLICT",
            )
        if epoch.recorded_through_sample is not None and end_sample > epoch.recorded_through_sample:
            raise MeetingNativeCaptureConflict(
                "native capture batch exceeds the closed epoch sample boundary",
                code="NATIVE_CAPTURE_EPOCH_BOUNDARY_CONFLICT",
            )
        declared_gap = (
            await self.session.exec(
                select(MeetingNativeCaptureGap).where(
                    MeetingNativeCaptureGap.capture_id == capture_id,
                    MeetingNativeCaptureGap.stream_epoch == stream_epoch,
                    or_(
                        and_(
                            MeetingNativeCaptureGap.from_sequence <= sequence,
                            MeetingNativeCaptureGap.to_sequence >= sequence,
                        ),
                        and_(
                            MeetingNativeCaptureGap.start_sample < end_sample,
                            MeetingNativeCaptureGap.end_sample > metadata.first_sample,
                        ),
                    ),
                )
            )
        ).first()
        if declared_gap is not None and (
            declared_gap.from_sequence <= sequence <= declared_gap.to_sequence
            or (declared_gap.start_sample < end_sample and declared_gap.end_sample > metadata.first_sample)
        ):
            raise MeetingNativeCaptureConflict(
                "native capture batch overlaps an unrecoverable gap",
                code="NATIVE_CAPTURE_GAP_CONFLICT",
            )
        declaration = (
            await self.session.exec(
                select(MeetingNativeCaptureManifestEntry).where(
                    MeetingNativeCaptureManifestEntry.capture_id == capture_id,
                    MeetingNativeCaptureManifestEntry.stream_epoch == stream_epoch,
                    MeetingNativeCaptureManifestEntry.sequence == sequence,
                )
            )
        ).first()
        if declaration is None and epoch.manifest_revision is not None:
            raise MeetingNativeCaptureConflict(
                "native capture batch is absent from the frozen manifest",
                code="NATIVE_CAPTURE_MANIFEST_ENTRY_MISSING",
            )
        if declaration is not None and not self._batch_matches_manifest(metadata, declaration):
            raise MeetingNativeCaptureConflict(
                "native capture batch does not match the frozen manifest",
                code="NATIVE_CAPTURE_MANIFEST_ENTRY_CONFLICT",
            )
        coordinate = (
            await self.session.exec(
                select(MeetingNativeCaptureBatch).where(
                    MeetingNativeCaptureBatch.capture_id == capture_id,
                    MeetingNativeCaptureBatch.stream_epoch == stream_epoch,
                    MeetingNativeCaptureBatch.sequence == sequence,
                )
            )
        ).first()
        keyed = (
            await self.session.exec(
                select(MeetingNativeCaptureBatch).where(
                    MeetingNativeCaptureBatch.capture_id == capture_id,
                    MeetingNativeCaptureBatch.idempotency_key == metadata.idempotency_key,
                )
            )
        ).first()
        if coordinate is not None and keyed is not None and coordinate.id != keyed.id:
            raise MeetingNativeCaptureConflict(
                "native capture idempotency key belongs to another batch",
                code="NATIVE_CAPTURE_IDEMPOTENCY_CONFLICT",
            )
        existing = coordinate or keyed
        if existing is not None and not self._same_batch(
            existing,
            stream_epoch=stream_epoch,
            sequence=sequence,
            metadata=metadata,
        ):
            raise MeetingNativeCaptureConflict(
                "native capture batch identity was reused with different metadata",
                code="NATIVE_CAPTURE_BATCH_CONFLICT",
            )
        if existing is None:
            overlap = (
                await self.session.exec(
                    select(MeetingNativeCaptureBatch).where(
                        MeetingNativeCaptureBatch.capture_id == capture_id,
                        MeetingNativeCaptureBatch.first_sample < end_sample,
                        MeetingNativeCaptureBatch.end_sample > metadata.first_sample,
                    )
                )
            ).first()
            if overlap is not None:
                raise MeetingNativeCaptureConflict(
                    "native capture sample range overlaps an existing batch",
                    code="NATIVE_CAPTURE_SAMPLE_RANGE_CONFLICT",
                )
            if capture.total_bytes + metadata.content_length > capture.max_total_bytes:
                raise MeetingNativeCaptureTooLarge("native capture total byte limit was reached")
            retained_bytes = int(
                (
                    await self.session.exec(
                        select(func.coalesce(func.sum(MeetingNativeCapture.total_bytes), 0)).where(
                            MeetingNativeCapture.owner_user_id == capture.owner_user_id
                        )
                    )
                ).one()
                or 0
            )
            if retained_bytes + metadata.content_length > self.settings.max_retained_bytes_per_owner:
                raise MeetingNativeCaptureTooLarge(
                    "native capture owner retained-byte limit was reached",
                    code="NATIVE_CAPTURE_OWNER_RETAINED_BYTES_LIMIT",
                )
        try:
            persisted = await self.storage.persist_batch(
                capture.owner_user_id,
                capture.id,
                stream_epoch,
                sequence,
                stream,
                expected_size=metadata.content_length,
                expected_sha256=metadata.sha256,
                max_bytes=self.settings.max_batch_bytes,
                min_free_bytes=self.settings.min_storage_free_bytes,
            )
        except MeetingNativeCaptureStorageError as exc:
            if exc.code in {"NATIVE_CAPTURE_BATCH_TOO_LARGE"}:
                raise MeetingNativeCaptureTooLarge(str(exc), code=exc.code) from exc
            if exc.code.endswith("CONFLICT") or exc.code.endswith("INTEGRITY_FAILED"):
                raise MeetingNativeCaptureConflict(str(exc), code=exc.code) from exc
            raise MeetingNativeCaptureUnavailable(str(exc), code=exc.code) from exc
        if existing is not None:
            checkpoint = await self._checkpoint(capture)
            await self.session.commit()
            return NativeCaptureBatchResponse(
                capture_id=capture.id,
                stream_epoch=stream_epoch,
                sequence=sequence,
                first_sample=metadata.first_sample,
                sample_count=metadata.sample_count,
                sha256=metadata.sha256,
                byte_size=metadata.content_length,
                replayed=True,
                checkpoint=checkpoint,
            )
        batch = MeetingNativeCaptureBatch(
            capture_id=capture.id,
            meeting_id=meeting_id,
            owner_user_id=capture.owner_user_id,
            stream_epoch=stream_epoch,
            sequence=sequence,
            first_sample=metadata.first_sample,
            sample_count=metadata.sample_count,
            end_sample=end_sample,
            captured_monotonic_ns=metadata.captured_monotonic_ns,
            encoding=metadata.encoding,
            sample_rate=metadata.sample_rate,
            channels=metadata.channels,
            byte_size=metadata.content_length,
            sha256=metadata.sha256,
            storage_key=persisted.storage_key,
            manifest_revision=metadata.manifest_revision,
            idempotency_key=metadata.idempotency_key,
        )
        capture.total_bytes += metadata.content_length
        capture.total_samples += metadata.sample_count
        capture.updated_at = utcnow()
        self.session.add(batch)
        self.session.add(capture)
        try:
            await self.session.flush()
            checkpoint = await self._checkpoint(capture)
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raced = (
                await self.session.exec(
                    select(MeetingNativeCaptureBatch).where(
                        MeetingNativeCaptureBatch.capture_id == capture_id,
                        MeetingNativeCaptureBatch.stream_epoch == stream_epoch,
                        MeetingNativeCaptureBatch.sequence == sequence,
                    )
                )
            ).first()
            if raced is None or not self._same_batch(
                raced,
                stream_epoch=stream_epoch,
                sequence=sequence,
                metadata=metadata,
            ):
                self.storage.remove_if_created(persisted)
                raise MeetingNativeCaptureConflict("native capture batch write conflicted") from exc
            capture = await self._owned_capture(meeting_id, capture_id, raced.owner_user_id)
            checkpoint = await self._checkpoint(capture)
            await self.session.commit()
            return NativeCaptureBatchResponse(
                capture_id=capture.id,
                stream_epoch=stream_epoch,
                sequence=sequence,
                first_sample=metadata.first_sample,
                sample_count=metadata.sample_count,
                sha256=metadata.sha256,
                byte_size=metadata.content_length,
                replayed=True,
                checkpoint=checkpoint,
            )
        except Exception:
            await self.session.rollback()
            self.storage.remove_if_created(persisted)
            raise
        return NativeCaptureBatchResponse(
            capture_id=capture.id,
            stream_epoch=stream_epoch,
            sequence=sequence,
            first_sample=metadata.first_sample,
            sample_count=metadata.sample_count,
            sha256=metadata.sha256,
            byte_size=metadata.content_length,
            replayed=False,
            checkpoint=checkpoint,
        )

    @staticmethod
    def _batch_matches_manifest(
        metadata: NativeCaptureBatchMetadata,
        declaration: MeetingNativeCaptureManifestEntry,
    ) -> bool:
        return (
            declaration.first_sample == metadata.first_sample
            and declaration.sample_count == metadata.sample_count
            and declaration.captured_monotonic_ns == metadata.captured_monotonic_ns
            and declaration.encoding == metadata.encoding
            and declaration.sample_rate == metadata.sample_rate
            and declaration.channels == metadata.channels
            and declaration.byte_size == metadata.content_length
            and declaration.sha256 == metadata.sha256
        )

    async def _sync_finalization(
        self,
        capture: MeetingNativeCapture,
        coverage: NativeCaptureCoverage,
    ) -> MeetingNativeCaptureFinalization | None:
        if capture.state != NativeCaptureState.SEALED.value:
            return None
        value = (
            await self.session.exec(
                select(MeetingNativeCaptureFinalization).where(
                    MeetingNativeCaptureFinalization.capture_id == capture.id
                )
            )
        ).first()
        target_state = (
            NativeCaptureFinalizationState.QUEUED.value
            if coverage.complete
            else NativeCaptureFinalizationState.PENDING_UPLOAD.value
        )
        if value is None:
            value = MeetingNativeCaptureFinalization(
                capture_id=capture.id,
                meeting_id=capture.meeting_id,
                owner_user_id=capture.owner_user_id,
                state=target_state,
                max_attempts=self.settings.finalization_max_attempts,
                accepted_gap_count=coverage.accepted_gap_count,
            )
            self.session.add(value)
            await self.session.flush()
        elif value.state == NativeCaptureFinalizationState.PENDING_UPLOAD.value and coverage.complete:
            value.state = NativeCaptureFinalizationState.QUEUED.value
            value.retry_after = None
            value.public_error_code = None
            value.internal_diagnostic = None
            value.accepted_gap_count = coverage.accepted_gap_count
            value.updated_at = utcnow()
            self.session.add(value)

        if value.state == NativeCaptureFinalizationState.READY.value:
            capture.server_playback_state = NativeCapturePlaybackState.READY.value
        elif value.state == NativeCaptureFinalizationState.PROCESSING.value:
            capture.server_playback_state = NativeCapturePlaybackState.PACKAGING.value
        elif value.state == NativeCaptureFinalizationState.FAILED.value:
            capture.server_playback_state = NativeCapturePlaybackState.FAILED.value
        elif coverage.complete:
            capture.server_playback_state = NativeCapturePlaybackState.PENDING_PACKAGING.value
        else:
            capture.server_playback_state = NativeCapturePlaybackState.PENDING_UPLOAD.value
        return value

    async def _checkpoint(self, capture: MeetingNativeCapture) -> NativeCaptureCheckpointResponse:
        epochs = list(
            (
                await self.session.exec(
                    select(MeetingNativeCaptureEpoch)
                    .where(MeetingNativeCaptureEpoch.capture_id == capture.id)
                    .order_by(MeetingNativeCaptureEpoch.stream_epoch)
                )
            ).all()
        )
        batches = list(
            (
                await self.session.exec(
                    select(MeetingNativeCaptureBatch)
                    .where(MeetingNativeCaptureBatch.capture_id == capture.id)
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
                await self.session.exec(
                    select(MeetingNativeCaptureGap)
                    .where(MeetingNativeCaptureGap.capture_id == capture.id)
                    .order_by(
                        MeetingNativeCaptureGap.start_sample,
                        MeetingNativeCaptureGap.stream_epoch,
                        MeetingNativeCaptureGap.from_sequence,
                    )
                )
            ).all()
        )
        coverage = native_capture_coverage(capture, epochs, batches, gaps)
        by_epoch: dict[int, list[MeetingNativeCaptureBatch]] = {}
        for batch in batches:
            by_epoch.setdefault(batch.stream_epoch, []).append(batch)
        epoch_responses: list[NativeCaptureEpochCheckpoint] = []
        for epoch in epochs:
            epoch_batches = by_epoch.get(epoch.stream_epoch, [])
            sequences = {int(value.sequence) for value in epoch_batches}
            highest_received = max(sequences, default=-1)
            missing = coverage.missing_sequences.get(epoch.stream_epoch, [])
            contiguous = -1
            while contiguous + 1 in sequences:
                contiguous += 1
            epoch_responses.append(
                NativeCaptureEpochCheckpoint(
                    stream_epoch=epoch.stream_epoch,
                    state=epoch.state,
                    highest_contiguous_sequence=contiguous,
                    highest_received_sequence=highest_received,
                    declared_last_sequence=epoch.last_sequence,
                    recorded_through_sample=epoch.recorded_through_sample,
                    missing_sequence_ranges=missing,
                )
            )
        capture.ingest_complete = coverage.complete
        finalization = await self._sync_finalization(capture, coverage)
        self.session.add(capture)
        meeting = await self.session.get(MeetingSession, capture.meeting_id)
        if meeting is None:
            raise MeetingNativeCaptureNotFound("meeting was not found")
        event_cursor = int(
            (
                await self.session.exec(
                    select(func.max(MeetingEvent.cursor)).where(MeetingEvent.meeting_id == capture.meeting_id)
                )
            ).one()
            or 0
        )
        closed_epochs = [value for value in epochs if value.state != NativeCaptureEpochState.ACTIVE.value]
        declared_samples = [
            value.recorded_through_sample for value in closed_epochs if value.recorded_through_sample is not None
        ]
        revisions = [value.manifest_revision for value in epochs if value.manifest_revision is not None]
        return NativeCaptureCheckpointResponse(
            capture_id=capture.id,
            meeting_id=capture.meeting_id,
            capture_checkpoint=NativeCaptureCaptureCheckpoint(
                state=capture.state,
                recorded_through_sample=max(declared_samples, default=None),
                last_sealed_epoch=max((value.stream_epoch for value in closed_epochs), default=None),
                manifest_revision=max(revisions, default=None),
            ),
            ingest_checkpoint=NativeCaptureIngestCheckpoint(
                persisted_through_sample=coverage.persisted_through_sample,
                accounted_through_sample=coverage.accounted_through_sample,
                highest_received_sample=coverage.highest_received_sample,
                received_batches=len(batches),
                received_bytes=sum(value.byte_size for value in batches),
                missing_sample_ranges=coverage.missing_sample_ranges,
                audio_missing_sample_ranges=coverage.audio_missing_sample_ranges,
                accepted_gaps=len(gaps),
                ingest_complete=coverage.complete,
            ),
            realtime_checkpoint=NativeCaptureRealtimeCheckpoint(
                stream_epoch=meeting.stream_epoch,
                last_acked_sequence=meeting.last_audio_sequence,
                stable_ordinal=meeting.last_segment_ordinal,
                event_cursor=event_cursor,
            ),
            finalization_checkpoint=NativeCaptureFinalizationCheckpoint(
                capture_sealed=capture.state == NativeCaptureState.SEALED.value,
                ingest_complete=coverage.complete,
                has_unrecoverable_gaps=bool(gaps),
                packaging_state=finalization.state if finalization is not None else None,
                packaging_attempt=finalization.attempt if finalization is not None else 0,
                packaging_error_code=(finalization.public_error_code if finalization is not None else None),
                wav_sha256=finalization.wav_sha256 if finalization is not None else None,
                wav_byte_size=finalization.wav_byte_size if finalization is not None else None,
                server_playback_state=capture.server_playback_state,
                postprocess_state=meeting.postprocess_state,
            ),
            epochs=epoch_responses,
        )

    async def checkpoint(
        self,
        meeting_id: str,
        capture_id: str,
        raw_token: str,
        *,
        device_installation_id: str | None = None,
    ) -> NativeCaptureCheckpointResponse:
        capture, _ = await self._authorized_capture(
            meeting_id,
            capture_id,
            raw_token,
            "checkpoint:read",
            device_installation_id=device_installation_id,
        )
        result = await self._checkpoint(capture)
        await self.session.commit()
        return result

    async def record_gap(
        self,
        meeting_id: str,
        capture_id: str,
        owner_user_id: int,
        request: NativeCaptureGapRequest,
        *,
        idempotency_key: str,
    ) -> NativeCaptureGapResponse:
        capture = await self._owned_capture(
            meeting_id,
            capture_id,
            owner_user_id,
            lock=True,
        )
        if capture.state != NativeCaptureState.SEALED.value:
            raise MeetingNativeCaptureConflict(
                "native capture gaps can only be declared after seal",
                code="NATIVE_CAPTURE_GAP_STATE_INVALID",
            )
        request_hash = _json_hash(
            {
                **request.model_dump(mode="json"),
                "idempotency_key": idempotency_key,
            }
        )
        existing = (
            await self.session.exec(
                select(MeetingNativeCaptureGap).where(
                    MeetingNativeCaptureGap.capture_id == capture_id,
                    MeetingNativeCaptureGap.idempotency_key == idempotency_key,
                )
            )
        ).first()
        if existing is not None:
            if existing.request_hash != request_hash:
                raise MeetingNativeCaptureConflict(
                    "native capture gap key was reused with a different declaration",
                    code="NATIVE_CAPTURE_IDEMPOTENCY_CONFLICT",
                )
            checkpoint = await self._checkpoint(capture)
            await self.session.commit()
            return NativeCaptureGapResponse(
                capture_id=capture.id,
                gap_id=existing.id,
                replayed=True,
                checkpoint=checkpoint,
            )

        epoch = await self._epoch(capture_id, request.stream_epoch, lock=True)
        if (
            epoch.last_sequence is None
            or epoch.recorded_through_sample is None
            or request.to_sequence > epoch.last_sequence
            or request.end_sample > epoch.recorded_through_sample
            or request.end_sample > (capture.sealed_through_sample or 0)
            or request.manifest_revision != epoch.manifest_revision
        ):
            raise MeetingNativeCaptureConflict(
                "native capture gap exceeds the sealed manifest",
                code="NATIVE_CAPTURE_GAP_BOUNDARY_CONFLICT",
            )
        declarations = list(
            (
                await self.session.exec(
                    select(MeetingNativeCaptureManifestEntry)
                    .where(
                        MeetingNativeCaptureManifestEntry.capture_id == capture_id,
                        MeetingNativeCaptureManifestEntry.stream_epoch == request.stream_epoch,
                        MeetingNativeCaptureManifestEntry.sequence >= request.from_sequence,
                        MeetingNativeCaptureManifestEntry.sequence <= request.to_sequence,
                    )
                    .order_by(MeetingNativeCaptureManifestEntry.sequence)
                )
            ).all()
        )
        expected_sequences = list(range(request.from_sequence, request.to_sequence + 1))
        if (
            [int(value.sequence) for value in declarations] != expected_sequences
            or not declarations
            or declarations[0].first_sample != request.start_sample
            or declarations[-1].end_sample != request.end_sample
            or any(value.manifest_revision != request.manifest_revision for value in declarations)
            or any(
                left.end_sample != right.first_sample
                for left, right in zip(declarations, declarations[1:], strict=False)
            )
        ):
            raise MeetingNativeCaptureConflict(
                "native capture gap does not match the frozen manifest",
                code="NATIVE_CAPTURE_GAP_MANIFEST_CONFLICT",
            )
        batch_overlap = (
            await self.session.exec(
                select(MeetingNativeCaptureBatch).where(
                    MeetingNativeCaptureBatch.capture_id == capture_id,
                    or_(
                        and_(
                            MeetingNativeCaptureBatch.stream_epoch == request.stream_epoch,
                            MeetingNativeCaptureBatch.sequence >= request.from_sequence,
                            MeetingNativeCaptureBatch.sequence <= request.to_sequence,
                        ),
                        and_(
                            MeetingNativeCaptureBatch.first_sample < request.end_sample,
                            MeetingNativeCaptureBatch.end_sample > request.start_sample,
                        ),
                    ),
                )
            )
        ).first()
        if batch_overlap is not None:
            raise MeetingNativeCaptureConflict(
                "native capture gap overlaps persisted audio",
                code="NATIVE_CAPTURE_GAP_CONFLICT",
            )
        gap_overlap = (
            await self.session.exec(
                select(MeetingNativeCaptureGap).where(
                    MeetingNativeCaptureGap.capture_id == capture_id,
                    or_(
                        and_(
                            MeetingNativeCaptureGap.stream_epoch == request.stream_epoch,
                            MeetingNativeCaptureGap.from_sequence <= request.to_sequence,
                            MeetingNativeCaptureGap.to_sequence >= request.from_sequence,
                        ),
                        and_(
                            MeetingNativeCaptureGap.start_sample < request.end_sample,
                            MeetingNativeCaptureGap.end_sample > request.start_sample,
                        ),
                    ),
                )
            )
        ).first()
        if gap_overlap is not None:
            raise MeetingNativeCaptureConflict(
                "native capture gap overlaps an existing declaration",
                code="NATIVE_CAPTURE_GAP_CONFLICT",
            )
        reason = str(request.reason)
        gap = MeetingNativeCaptureGap(
            capture_id=capture.id,
            meeting_id=meeting_id,
            owner_user_id=capture.owner_user_id,
            stream_epoch=request.stream_epoch,
            from_sequence=request.from_sequence,
            to_sequence=request.to_sequence,
            start_sample=request.start_sample,
            end_sample=request.end_sample,
            reason=reason,
            manifest_revision=request.manifest_revision,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        self.session.add(gap)
        await self.session.flush()
        await self.events.append(
            meeting_id,
            "audio.gap.detected",
            {
                "capture_id": capture.id,
                "stream_epoch": request.stream_epoch,
                "from_sequence": request.from_sequence,
                "to_sequence": request.to_sequence,
                "start_sample": request.start_sample,
                "end_sample": request.end_sample,
                "reason": reason,
            },
        )
        checkpoint = await self._checkpoint(capture)
        await self.session.commit()
        return NativeCaptureGapResponse(
            capture_id=capture.id,
            gap_id=gap.id,
            replayed=False,
            checkpoint=checkpoint,
        )

    async def _validate_boundary(
        self,
        capture: MeetingNativeCapture,
        epoch: MeetingNativeCaptureEpoch,
        request: NativeCaptureBoundaryRequest,
    ) -> None:
        if request.recorded_through_sample > capture.max_duration_samples:
            raise MeetingNativeCaptureTooLarge("native capture duration limit was reached")
        earlier_epochs = list(
            (
                await self.session.exec(
                    select(MeetingNativeCaptureEpoch)
                    .where(
                        MeetingNativeCaptureEpoch.capture_id == capture.id,
                        MeetingNativeCaptureEpoch.stream_epoch < epoch.stream_epoch,
                    )
                    .order_by(MeetingNativeCaptureEpoch.stream_epoch)
                )
            ).all()
        )
        expected_start = max(
            (
                int(value.recorded_through_sample)
                for value in earlier_epochs
                if value.recorded_through_sample is not None
            ),
            default=0,
        )
        first_declared_sample = (
            min(value.first_sample for value in request.manifest_entries)
            if request.manifest_entries
            else request.recorded_through_sample
        )
        if first_declared_sample != expected_start:
            raise MeetingNativeCaptureConflict(
                "native capture epoch does not continue the frozen timeline",
                code="NATIVE_CAPTURE_MANIFEST_TIMELINE_CONFLICT",
            )
        rows = list(
            (
                await self.session.exec(
                    select(MeetingNativeCaptureBatch).where(
                        MeetingNativeCaptureBatch.capture_id == capture.id,
                        MeetingNativeCaptureBatch.stream_epoch == epoch.stream_epoch,
                    )
                )
            ).all()
        )
        if any(value.sequence > request.final_sequence for value in rows):
            raise MeetingNativeCaptureConflict(
                "declared final sequence is behind a persisted batch",
                code="NATIVE_CAPTURE_EPOCH_BOUNDARY_CONFLICT",
            )
        if any(value.end_sample > request.recorded_through_sample for value in rows):
            raise MeetingNativeCaptureConflict(
                "declared sample boundary is behind a persisted batch",
                code="NATIVE_CAPTURE_EPOCH_BOUNDARY_CONFLICT",
            )
        if any(value.manifest_revision > request.manifest_revision for value in rows):
            raise MeetingNativeCaptureConflict(
                "declared manifest revision is behind a persisted batch",
                code="NATIVE_CAPTURE_MANIFEST_REVISION_CONFLICT",
            )
        declarations = {value.sequence: value for value in request.manifest_entries}
        if any(
            value.encoding != capture.encoding
            or value.sample_rate != capture.sample_rate
            or value.channels != capture.channels
            for value in declarations.values()
        ):
            raise MeetingNativeCaptureInvalid(
                "native capture manifest audio format does not match the capture contract",
                code="NATIVE_CAPTURE_MANIFEST_FORMAT_INVALID",
            )
        for row in rows:
            declaration = declarations.get(row.sequence)
            if declaration is None or not self._batch_row_matches_manifest(row, declaration):
                raise MeetingNativeCaptureConflict(
                    "persisted batch does not match the submitted manifest",
                    code="NATIVE_CAPTURE_MANIFEST_ENTRY_CONFLICT",
                )
        existing = list(
            (
                await self.session.exec(
                    select(MeetingNativeCaptureManifestEntry).where(
                        MeetingNativeCaptureManifestEntry.capture_id == capture.id,
                        MeetingNativeCaptureManifestEntry.stream_epoch == epoch.stream_epoch,
                    )
                )
            ).all()
        )
        if existing:
            if len(existing) != len(declarations) or any(
                (declaration := declarations.get(value.sequence)) is None
                or not self._stored_manifest_entry_matches(value, declaration, request.manifest_revision)
                for value in existing
            ):
                raise MeetingNativeCaptureConflict(
                    "native capture manifest conflicts with its durable declaration",
                    code="NATIVE_CAPTURE_MANIFEST_CONFLICT",
                )
            return
        for value in request.manifest_entries:
            self.session.add(
                MeetingNativeCaptureManifestEntry(
                    capture_id=capture.id,
                    meeting_id=capture.meeting_id,
                    owner_user_id=capture.owner_user_id,
                    stream_epoch=epoch.stream_epoch,
                    sequence=value.sequence,
                    first_sample=value.first_sample,
                    sample_count=value.sample_count,
                    end_sample=value.first_sample + value.sample_count,
                    captured_monotonic_ns=value.captured_monotonic_ns,
                    encoding=value.encoding,
                    sample_rate=value.sample_rate,
                    channels=value.channels,
                    byte_size=value.sample_count * value.channels * 2,
                    sha256=value.sha256,
                    manifest_revision=request.manifest_revision,
                )
            )
        await self.session.flush()

    @staticmethod
    def _batch_row_matches_manifest(
        batch: MeetingNativeCaptureBatch,
        declaration: NativeCaptureManifestEntry,
    ) -> bool:
        return (
            batch.first_sample == declaration.first_sample
            and batch.sample_count == declaration.sample_count
            and batch.captured_monotonic_ns == declaration.captured_monotonic_ns
            and batch.encoding == declaration.encoding
            and batch.sample_rate == declaration.sample_rate
            and batch.channels == declaration.channels
            and batch.byte_size == declaration.sample_count * declaration.channels * 2
            and batch.sha256 == declaration.sha256
        )

    @staticmethod
    def _stored_manifest_entry_matches(
        stored: MeetingNativeCaptureManifestEntry,
        declaration: NativeCaptureManifestEntry,
        manifest_revision: int,
    ) -> bool:
        return (
            stored.sequence == declaration.sequence
            and stored.first_sample == declaration.first_sample
            and stored.sample_count == declaration.sample_count
            and stored.captured_monotonic_ns == declaration.captured_monotonic_ns
            and stored.encoding == declaration.encoding
            and stored.sample_rate == declaration.sample_rate
            and stored.channels == declaration.channels
            and stored.byte_size == declaration.sample_count * declaration.channels * 2
            and stored.sha256 == declaration.sha256
            and stored.manifest_revision == manifest_revision
        )

    async def seal(
        self,
        meeting_id: str,
        capture_id: str,
        raw_token: str,
        request: NativeCaptureBoundaryRequest,
        *,
        device_installation_id: str | None = None,
    ) -> NativeCaptureSealResponse:
        capture, _ = await self._authorized_capture(
            meeting_id,
            capture_id,
            raw_token,
            "capture:seal",
            device_installation_id=device_installation_id,
        )
        if capture.state == NativeCaptureState.SEALED.value:
            epoch = await self._epoch(capture_id, request.expected_epoch)
            same = (
                capture.current_epoch == request.expected_epoch
                and capture.sealed_through_sample == request.recorded_through_sample
                and capture.seal_manifest_revision == request.manifest_revision
                and capture.seal_manifest_sha256 == request.manifest_sha256
                and epoch.last_sequence == request.final_sequence
            )
            if not same:
                raise MeetingNativeCaptureConflict(
                    "native capture seal conflicts with the existing boundary",
                    code="NATIVE_CAPTURE_SEAL_CONFLICT",
                )
            checkpoint = await self._checkpoint(capture)
            await self.session.commit()
            return NativeCaptureSealResponse(
                capture=native_capture_status(capture),
                checkpoint=checkpoint,
                replayed=True,
            )
        if capture.state != NativeCaptureState.ACTIVE.value or capture.current_epoch != request.expected_epoch:
            raise MeetingNativeCaptureConflict(
                "native capture epoch changed before seal",
                code="NATIVE_CAPTURE_EPOCH_CONFLICT",
            )
        epoch = await self._epoch(capture_id, request.expected_epoch, lock=True)
        if epoch.state != NativeCaptureEpochState.ACTIVE.value:
            raise MeetingNativeCaptureConflict("native capture epoch is already closed")
        await self._validate_boundary(capture, epoch, request)
        now = utcnow()
        epoch.state = NativeCaptureEpochState.SEALED.value
        epoch.last_sequence = request.final_sequence
        epoch.recorded_through_sample = request.recorded_through_sample
        epoch.manifest_revision = request.manifest_revision
        epoch.manifest_sha256 = request.manifest_sha256
        epoch.closed_at = now
        capture.state = NativeCaptureState.SEALED.value
        capture.sealed_through_sample = request.recorded_through_sample
        capture.seal_manifest_revision = request.manifest_revision
        capture.seal_manifest_sha256 = request.manifest_sha256
        capture.sealed_at = now
        capture.updated_at = now
        self.session.add(epoch)
        self.session.add(capture)
        await self.events.append(
            meeting_id,
            "native_capture.sealed",
            {
                "capture_id": capture.id,
                "stream_epoch": epoch.stream_epoch,
                "final_sequence": request.final_sequence,
                "recorded_through_sample": request.recorded_through_sample,
                "manifest_revision": request.manifest_revision,
            },
        )
        checkpoint = await self._checkpoint(capture)
        await self.session.commit()
        return NativeCaptureSealResponse(
            capture=native_capture_status(capture),
            checkpoint=checkpoint,
            replayed=False,
        )

    async def _stream_ticket(
        self,
        meeting: MeetingSession,
        owner_user_id: int,
        origin: str,
    ) -> tuple[str, MeetingStreamTicket]:
        if not self.meeting_settings.operational or not self.meeting_settings.asr_enabled:
            raise MeetingNativeCaptureUnavailable(
                "realtime meeting stream is unavailable",
                code="NATIVE_CAPTURE_REALTIME_UNAVAILABLE",
            )
        # A rollover replay may replace an unconsumed ticket, but it must not
        # accumulate multiple simultaneously valid producer credentials.
        await self.session.exec(
            delete(MeetingStreamTicket).where(
                MeetingStreamTicket.meeting_id == meeting.id,
                MeetingStreamTicket.stream_epoch == meeting.stream_epoch,
                MeetingStreamTicket.purpose == "meeting_audio_producer",
                MeetingStreamTicket.consumed_at.is_(None),
            )
        )
        raw = secrets.token_urlsafe(32)
        ticket = MeetingStreamTicket(
            token_hash=hashlib.sha256(raw.encode("ascii")).hexdigest(),
            meeting_id=meeting.id,
            owner_user_id=owner_user_id,
            stream_epoch=meeting.stream_epoch,
            purpose="meeting_audio_producer",
            origin=origin,
            expires_at=utcnow() + timedelta(seconds=self.meeting_settings.stream_ticket_ttl_seconds),
        )
        self.session.add(ticket)
        await self.session.flush()
        return raw, ticket

    async def rollover(
        self,
        meeting_id: str,
        capture_id: str,
        owner_user_id: int,
        request: NativeCaptureBoundaryRequest,
        *,
        idempotency_key: str,
        origin: str,
    ) -> tuple[str, MeetingStreamTicket, MeetingNativeCapture, NativeCaptureCheckpointResponse, bool]:
        normalized_origin = normalize_origin(origin)
        capture = await self._owned_capture(
            meeting_id,
            capture_id,
            owner_user_id,
            lock=True,
        )
        if capture.state != NativeCaptureState.ACTIVE.value:
            raise MeetingNativeCaptureConflict("sealed native capture cannot roll over")
        request_hash = _json_hash(
            {
                **request.model_dump(mode="json"),
                "origin": normalized_origin,
            }
        )
        replay_epoch = (
            await self.session.exec(
                select(MeetingNativeCaptureEpoch).where(
                    MeetingNativeCaptureEpoch.capture_id == capture_id,
                    MeetingNativeCaptureEpoch.rollover_idempotency_key == idempotency_key,
                )
            )
        ).first()
        meeting = (
            await self.session.exec(
                select(MeetingSession)
                .where(
                    MeetingSession.id == meeting_id,
                    MeetingSession.owner_user_id == capture.owner_user_id,
                )
                .with_for_update()
            )
        ).first()
        if meeting is None:
            raise MeetingNativeCaptureNotFound("meeting was not found")
        if replay_epoch is not None:
            if replay_epoch.rollover_request_hash != request_hash:
                raise MeetingNativeCaptureConflict(
                    "native capture rollover key was reused with a different request",
                    code="NATIVE_CAPTURE_IDEMPOTENCY_CONFLICT",
                )
            if capture.current_epoch != replay_epoch.stream_epoch or meeting.stream_epoch != replay_epoch.stream_epoch:
                raise MeetingNativeCaptureConflict(
                    "native capture rollover replay is stale",
                    code="NATIVE_CAPTURE_EPOCH_CONFLICT",
                )
            raw_stream, ticket = await self._stream_ticket(meeting, capture.owner_user_id, normalized_origin)
            checkpoint = await self._checkpoint(capture)
            await self.session.commit()
            return raw_stream, ticket, capture, checkpoint, True
        if capture.current_epoch != request.expected_epoch or meeting.stream_epoch != request.expected_epoch:
            raise MeetingNativeCaptureConflict(
                "native capture epoch changed before rollover",
                code="NATIVE_CAPTURE_EPOCH_CONFLICT",
            )
        if meeting.state not in {
            MeetingState.CONNECTING.value,
            MeetingState.LIVE.value,
            MeetingState.RECONNECTING.value,
        }:
            raise MeetingNativeCaptureConflict(
                "meeting is not ready for stream rollover",
                code="NATIVE_CAPTURE_MEETING_STATE_INVALID",
            )
        previous = await self._epoch(capture_id, request.expected_epoch, lock=True)
        if previous.state != NativeCaptureEpochState.ACTIVE.value:
            raise MeetingNativeCaptureConflict("native capture epoch is already closed")
        await self._validate_boundary(capture, previous, request)
        now = utcnow()
        previous.state = NativeCaptureEpochState.ROLLED_OVER.value
        previous.last_sequence = request.final_sequence
        previous.recorded_through_sample = request.recorded_through_sample
        previous.manifest_revision = request.manifest_revision
        previous.manifest_sha256 = request.manifest_sha256
        previous.closed_at = now
        next_epoch = request.expected_epoch + 1
        capture.current_epoch = next_epoch
        capture.updated_at = now
        meeting.stream_epoch = next_epoch
        meeting.last_audio_sequence = -1
        meeting.updated_at = now
        await self.session.exec(
            update(MeetingStreamLease)
            .where(MeetingStreamLease.meeting_id == meeting_id)
            .values(lease_until=now, updated_at=now)
        )
        opened = MeetingNativeCaptureEpoch(
            capture_id=capture.id,
            stream_epoch=next_epoch,
            rollover_from_epoch=request.expected_epoch,
            rollover_idempotency_key=idempotency_key,
            rollover_request_hash=request_hash,
        )
        self.session.add(previous)
        self.session.add(opened)
        self.session.add(capture)
        self.session.add(meeting)
        await self.session.flush()
        raw_stream, ticket = await self._stream_ticket(meeting, capture.owner_user_id, normalized_origin)
        await self.events.append(
            meeting_id,
            "native_capture.stream_rolled_over",
            {
                "capture_id": capture.id,
                "previous_epoch": request.expected_epoch,
                "stream_epoch": next_epoch,
                "recorded_through_sample": request.recorded_through_sample,
            },
        )
        checkpoint = await self._checkpoint(capture)
        await self.session.commit()
        return raw_stream, ticket, capture, checkpoint, False


__all__ = [
    "MeetingNativeCaptureConflict",
    "MeetingNativeCaptureError",
    "MeetingNativeCaptureForbidden",
    "MeetingNativeCaptureInvalid",
    "MeetingNativeCaptureNotFound",
    "MeetingNativeCaptureRepository",
    "MeetingNativeCaptureTooLarge",
    "MeetingNativeCaptureUnauthorized",
    "MeetingNativeCaptureUnavailable",
    "native_capture_status",
]
