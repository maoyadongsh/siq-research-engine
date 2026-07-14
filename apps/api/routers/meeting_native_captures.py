"""Isolated native-capture ingest API under the versioned meeting namespace."""

# FastAPI intentionally declares dependencies and validated headers as defaults.
# ruff: noqa: B008

from __future__ import annotations

from typing import Annotated, NoReturn
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError
from services.auth_dependencies import get_current_user
from services.auth_service import User
from services.meeting_config import MeetingSettings
from services.meeting_database import get_meeting_async_session as get_async_session
from services.meeting_metrics import record_meeting_counter
from services.meeting_native_capture_config import MeetingNativeCaptureSettings
from services.meeting_native_capture_contracts import (
    NATIVE_CAPTURE_INT64_MAX,
    NativeCaptureBatchMetadata,
    NativeCaptureBatchResponse,
    NativeCaptureBoundaryRequest,
    NativeCaptureCheckpointResponse,
    NativeCaptureCreateRequest,
    NativeCaptureCreateResponse,
    NativeCaptureGapRequest,
    NativeCaptureGapResponse,
    NativeCaptureRolloverResponse,
    NativeCaptureSealResponse,
    NativeCaptureTokenResponse,
)
from services.meeting_native_capture_limits import (
    MeetingNativeCaptureIngressBusy,
    native_capture_ingress_limiter,
)
from services.meeting_native_capture_service import (
    MeetingNativeCaptureConflict,
    MeetingNativeCaptureError,
    MeetingNativeCaptureForbidden,
    MeetingNativeCaptureInvalid,
    MeetingNativeCaptureNotFound,
    MeetingNativeCaptureRepository,
    MeetingNativeCaptureTooLarge,
    MeetingNativeCaptureUnauthorized,
    MeetingNativeCaptureUnavailable,
)
from services.meeting_native_capture_storage import MeetingNativeCaptureStorage
from services.meeting_permissions import MEETING_UPDATE, meeting_user_id, require_meeting_permission
from services.meeting_stream_ticket import StreamTicketError, normalize_origin
from sqlmodel.ext.asyncio.session import AsyncSession

router = APIRouter(prefix="/meetings/v1", tags=["meeting-native-capture"])
capture_security = HTTPBearer(auto_error=False, scheme_name="NativeCaptureBearer")


def _settings() -> MeetingNativeCaptureSettings:
    return MeetingNativeCaptureSettings.from_env()


def _require_enabled() -> tuple[MeetingNativeCaptureSettings, MeetingSettings]:
    meeting = MeetingSettings.from_env()
    native = _settings()
    if not meeting.operational:
        raise HTTPException(status_code=503, detail={"code": "MEETINGS_DISABLED"})
    if not native.operational:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MEETING_IOS_NATIVE_CAPTURE_DISABLED"
                if not native.enabled
                else "MEETING_IOS_NATIVE_CAPTURE_CONFIGURATION_INVALID",
                "configuration_errors": list(native.errors),
            },
        )
    return native, meeting


def _owner(user: User) -> int:
    require_meeting_permission(user, MEETING_UPDATE)
    return meeting_user_id(user)


def _repository(
    session: AsyncSession,
    native: MeetingNativeCaptureSettings,
    meeting: MeetingSettings,
) -> MeetingNativeCaptureRepository:
    return MeetingNativeCaptureRepository(
        session,
        MeetingNativeCaptureStorage(native.root),
        native,
        meeting_settings=meeting,
    )


def _capture_token(credentials: HTTPAuthorizationCredentials | None) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        record_meeting_counter("native_capture_auth_failure", "token_invalid")
        raise HTTPException(
            status_code=401,
            detail={"code": "NATIVE_CAPTURE_TOKEN_INVALID"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


def _raise_error(exc: Exception) -> NoReturn:
    if isinstance(exc, MeetingNativeCaptureUnauthorized):
        reason = {
            "NATIVE_CAPTURE_TOKEN_EXPIRED": "token_expired",
            "NATIVE_CAPTURE_TOKEN_REVOKED": "token_revoked",
            "NATIVE_CAPTURE_DEVICE_MISMATCH": "device_mismatch",
        }.get(exc.code, "token_invalid")
        record_meeting_counter("native_capture_auth_failure", reason)
        raise HTTPException(
            status_code=401,
            detail={"code": exc.code},
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    if isinstance(exc, MeetingNativeCaptureForbidden):
        record_meeting_counter("native_capture_auth_failure", "scope_denied")
        raise HTTPException(status_code=403, detail={"code": exc.code}) from exc
    if isinstance(exc, MeetingNativeCaptureNotFound):
        raise HTTPException(status_code=404, detail={"code": exc.code}) from exc
    if isinstance(exc, MeetingNativeCaptureConflict):
        raise HTTPException(status_code=409, detail={"code": exc.code}) from exc
    if isinstance(exc, MeetingNativeCaptureTooLarge):
        raise HTTPException(status_code=413, detail={"code": exc.code}) from exc
    if isinstance(exc, MeetingNativeCaptureInvalid):
        raise HTTPException(status_code=400, detail={"code": exc.code}) from exc
    if isinstance(exc, MeetingNativeCaptureUnavailable):
        raise HTTPException(status_code=503, detail={"code": exc.code}) from exc
    if isinstance(exc, MeetingNativeCaptureError):
        raise HTTPException(status_code=500, detail={"code": exc.code}) from exc
    raise HTTPException(status_code=500, detail={"code": "NATIVE_CAPTURE_FAILED"}) from exc


@router.post(
    "/sessions/{meeting_id}/native-captures",
    response_model=NativeCaptureCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_native_capture(
    meeting_id: str,
    payload: NativeCaptureCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> NativeCaptureCreateResponse:
    native, meeting = _require_enabled()
    try:
        return await _repository(session, native, meeting).create(
            meeting_id,
            _owner(current_user),
            payload,
            idempotency_key=idempotency_key,
        )
    except MeetingNativeCaptureError as exc:
        _raise_error(exc)


@router.post(
    "/sessions/{meeting_id}/native-captures/{capture_id}/token",
    response_model=NativeCaptureTokenResponse,
)
async def renew_native_capture_token(
    meeting_id: str,
    capture_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> NativeCaptureTokenResponse:
    native, meeting = _require_enabled()
    try:
        return await _repository(session, native, meeting).renew_token(
            meeting_id,
            capture_id,
            _owner(current_user),
        )
    except MeetingNativeCaptureError as exc:
        _raise_error(exc)


@router.post("/sessions/{meeting_id}/native-captures/{capture_id}/token/revoke", response_model=dict)
async def revoke_native_capture_tokens(
    meeting_id: str,
    capture_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    native, meeting = _require_enabled()
    try:
        count = await _repository(session, native, meeting).revoke_tokens(
            meeting_id,
            capture_id,
            _owner(current_user),
        )
        return {"schema_version": "siq.meeting.native_capture.v1", "capture_id": capture_id, "revoked": count}
    except MeetingNativeCaptureError as exc:
        _raise_error(exc)


@router.put(
    "/sessions/{meeting_id}/native-captures/{capture_id}/batches/{stream_epoch}/{sequence}",
    response_model=NativeCaptureBatchResponse,
)
async def put_native_capture_batch(
    meeting_id: str,
    capture_id: str,
    stream_epoch: Annotated[int, Path(ge=1, le=NATIVE_CAPTURE_INT64_MAX)],
    sequence: Annotated[int, Path(ge=0, le=NATIVE_CAPTURE_INT64_MAX)],
    request: Request,
    first_sample: int = Header(alias="X-SIQ-First-Sample", ge=0),
    sample_count: int = Header(alias="X-SIQ-Sample-Count", gt=0),
    captured_monotonic_ns: int = Header(
        alias="X-SIQ-Captured-Monotonic-Ns",
        ge=0,
        le=NATIVE_CAPTURE_INT64_MAX,
    ),
    encoding: str = Header(alias="X-SIQ-Audio-Encoding"),
    sample_rate: int = Header(alias="X-SIQ-Sample-Rate"),
    channels: int = Header(alias="X-SIQ-Channels"),
    sha256: str = Header(alias="X-SIQ-SHA256", min_length=64, max_length=64),
    manifest_revision: int = Header(alias="X-SIQ-Manifest-Revision", ge=1),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
    device_installation_id: str = Header(
        alias="X-SIQ-Device-Installation-Id",
        min_length=16,
        max_length=256,
    ),
    credentials: HTTPAuthorizationCredentials | None = Depends(capture_security),
    session: AsyncSession = Depends(get_async_session),
) -> NativeCaptureBatchResponse:
    native, meeting = _require_enabled()
    content_type = str(request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if content_type != "application/octet-stream":
        raise HTTPException(status_code=415, detail={"code": "NATIVE_CAPTURE_CONTENT_TYPE_INVALID"})
    raw_length = str(request.headers.get("content-length") or "").strip()
    try:
        content_length = int(raw_length)
    except ValueError as exc:
        raise HTTPException(status_code=411, detail={"code": "NATIVE_CAPTURE_CONTENT_LENGTH_REQUIRED"}) from exc
    try:
        metadata = NativeCaptureBatchMetadata(
            first_sample=first_sample,
            sample_count=sample_count,
            captured_monotonic_ns=captured_monotonic_ns,
            encoding=encoding,
            sample_rate=sample_rate,
            channels=channels,
            sha256=sha256.lower(),
            manifest_revision=manifest_revision,
            idempotency_key=idempotency_key,
            content_length=content_length,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "NATIVE_CAPTURE_BATCH_METADATA_INVALID"},
        ) from exc
    try:
        limiter = native_capture_ingress_limiter(
            native.max_batch_concurrency,
            native.batch_queue_timeout_seconds,
        )
        async with limiter.slot():
            response = await _repository(session, native, meeting).put_batch(
                meeting_id,
                capture_id,
                _capture_token(credentials),
                stream_epoch,
                sequence,
                metadata,
                request.stream(),
                device_installation_id=device_installation_id,
            )
            result = "replayed" if response.replayed else "accepted"
            record_meeting_counter("native_capture_batch", result)
            record_meeting_counter(
                "native_capture_batch_bytes",
                result,
                metadata.content_length,
            )
            return response
    except MeetingNativeCaptureIngressBusy as exc:
        record_meeting_counter("native_capture_batch", "rejected_capacity")
        record_meeting_counter("native_capture_storage_rejection", "capacity")
        raise HTTPException(status_code=429, detail={"code": exc.code}) from exc
    except MeetingNativeCaptureError as exc:
        if isinstance(exc, MeetingNativeCaptureTooLarge):
            record_meeting_counter("native_capture_batch", "rejected_capacity")
            record_meeting_counter("native_capture_storage_rejection", "quota")
        elif isinstance(exc, MeetingNativeCaptureUnavailable):
            record_meeting_counter("native_capture_batch", "rejected_storage")
            reason = "low_space" if "LOW_SPACE" in exc.code else "unavailable"
            record_meeting_counter("native_capture_storage_rejection", reason)
        elif isinstance(exc, MeetingNativeCaptureInvalid):
            record_meeting_counter("native_capture_batch", "invalid")
        elif isinstance(exc, MeetingNativeCaptureConflict):
            record_meeting_counter("native_capture_batch", "conflict")
            if "INTEGRITY" in exc.code:
                record_meeting_counter("native_capture_storage_rejection", "integrity")
        _raise_error(exc)


@router.get(
    "/sessions/{meeting_id}/native-captures/{capture_id}/checkpoint",
    response_model=NativeCaptureCheckpointResponse,
)
async def native_capture_checkpoint(
    meeting_id: str,
    capture_id: str,
    device_installation_id: str = Header(
        alias="X-SIQ-Device-Installation-Id",
        min_length=16,
        max_length=256,
    ),
    credentials: HTTPAuthorizationCredentials | None = Depends(capture_security),
    session: AsyncSession = Depends(get_async_session),
) -> NativeCaptureCheckpointResponse:
    native, meeting = _require_enabled()
    try:
        return await _repository(session, native, meeting).checkpoint(
            meeting_id,
            capture_id,
            _capture_token(credentials),
            device_installation_id=device_installation_id,
        )
    except MeetingNativeCaptureError as exc:
        _raise_error(exc)


@router.post(
    "/sessions/{meeting_id}/native-captures/{capture_id}/gaps",
    response_model=NativeCaptureGapResponse,
)
async def record_native_capture_gap(
    meeting_id: str,
    capture_id: str,
    payload: NativeCaptureGapRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> NativeCaptureGapResponse:
    native, meeting = _require_enabled()
    try:
        return await _repository(session, native, meeting).record_gap(
            meeting_id,
            capture_id,
            _owner(current_user),
            payload,
            idempotency_key=idempotency_key,
        )
    except MeetingNativeCaptureError as exc:
        _raise_error(exc)


@router.post(
    "/sessions/{meeting_id}/native-captures/{capture_id}/seal",
    response_model=NativeCaptureSealResponse,
)
async def seal_native_capture(
    meeting_id: str,
    capture_id: str,
    payload: NativeCaptureBoundaryRequest,
    device_installation_id: str = Header(
        alias="X-SIQ-Device-Installation-Id",
        min_length=16,
        max_length=256,
    ),
    credentials: HTTPAuthorizationCredentials | None = Depends(capture_security),
    session: AsyncSession = Depends(get_async_session),
) -> NativeCaptureSealResponse:
    native, meeting = _require_enabled()
    try:
        return await _repository(session, native, meeting).seal(
            meeting_id,
            capture_id,
            _capture_token(credentials),
            payload,
            device_installation_id=device_installation_id,
        )
    except MeetingNativeCaptureError as exc:
        _raise_error(exc)


@router.post(
    "/sessions/{meeting_id}/native-captures/{capture_id}/rollover",
    response_model=NativeCaptureRolloverResponse,
)
async def rollover_native_capture(
    meeting_id: str,
    capture_id: str,
    payload: NativeCaptureBoundaryRequest,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> NativeCaptureRolloverResponse:
    native, meeting = _require_enabled()
    try:
        origin = normalize_origin(str(request.headers.get("origin") or ""))
    except StreamTicketError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code}) from exc
    try:
        raw_stream, ticket, capture, checkpoint, replayed = await _repository(
            session,
            native,
            meeting,
        ).rollover(
            meeting_id,
            capture_id,
            _owner(current_user),
            payload,
            idempotency_key=idempotency_key,
            origin=origin,
        )
        route_path = f"/api/meetings/v1/sessions/{meeting_id}/audio"
        return NativeCaptureRolloverResponse(
            capture_id=capture.id,
            previous_epoch=payload.expected_epoch,
            stream_epoch=ticket.stream_epoch,
            stream_ticket=raw_stream,
            stream_ticket_expires_at=ticket.expires_at,
            ws_url=f"{route_path}?ticket={quote(raw_stream, safe='')}",
            capture_offset_ms=payload.recorded_through_sample * 1000 // capture.sample_rate,
            reconnect_window_seconds=meeting.reconnect_window_seconds,
            replayed=replayed,
            checkpoint=checkpoint,
        )
    except MeetingNativeCaptureError as exc:
        _raise_error(exc)
