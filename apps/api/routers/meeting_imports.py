"""Resumable long-recording import API for the meeting domain."""

# FastAPI dependency declarations intentionally use call expressions.
# ruff: noqa: B008

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from services.auth_dependencies import get_current_user
from services.auth_service import User
from services.meeting_config import MeetingSettings
from services.meeting_database import get_meeting_async_session as get_async_session
from services.meeting_import_config import MeetingImportSettings
from services.meeting_import_contracts import (
    MeetingImportChunkResponse,
    MeetingImportCompleteRequest,
    MeetingImportCreateRequest,
    MeetingImportStatusResponse,
)
from services.meeting_import_service import MeetingImportError, MeetingImportRepository
from services.meeting_import_storage import MeetingImportStorage
from services.meeting_model_catalog import MeetingModelCatalog, MeetingModelCatalogError
from services.meeting_permissions import (
    MEETING_CREATE,
    MEETING_DELETE,
    MEETING_READ,
    MEETING_UPDATE,
    meeting_user_id,
    require_meeting_permission,
)
from sqlmodel.ext.asyncio.session import AsyncSession

router = APIRouter(prefix="/meetings/v1/imports", tags=["meeting-imports"])
_MODEL_CATALOG = MeetingModelCatalog()


def _settings() -> MeetingImportSettings:
    meeting = MeetingSettings.from_env()
    value = MeetingImportSettings.from_env()
    if not meeting.operational:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MEETINGS_DISABLED" if not meeting.enabled else "MEETINGS_CONFIGURATION_INVALID",
                "configuration_errors": list(meeting.errors),
            },
        )
    if not value.operational:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MEETING_IMPORT_DISABLED" if not value.enabled else "MEETING_IMPORT_CONFIGURATION_INVALID",
                "configuration_errors": list(value.errors),
            },
        )
    return value


def _authorize(user: User, permission: str) -> int:
    require_meeting_permission(user, permission)
    return meeting_user_id(user)


def _repository(session: AsyncSession, settings: MeetingImportSettings) -> MeetingImportRepository:
    return MeetingImportRepository(session, MeetingImportStorage(settings.root), settings)


def _raise_import_error(exc: MeetingImportError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": str(exc)},
    ) from exc


def _validate_model(request: MeetingImportCreateRequest) -> None:
    meeting = MeetingSettings.from_env()
    if request.voiceprint_enabled and not meeting.voiceprint_enabled:
        raise HTTPException(status_code=503, detail={"code": "MEETING_VOICEPRINT_DISABLED"})
    if not request.ai_enabled:
        return
    if not meeting.ai_enabled:
        raise HTTPException(status_code=503, detail={"code": "MEETING_AI_DISABLED"})
    if request.model_selection.mode != "pinned":
        return
    try:
        model = _MODEL_CATALOG.require_available(request.model_selection.model_ref or "")
    except MeetingModelCatalogError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "MEETING_MODEL_INVALID", "message": str(exc)},
        ) from exc
    if model.locality == "cloud" and not request.model_selection.cloud_data_boundary_confirmed:
        raise HTTPException(status_code=422, detail={"code": "MEETING_CLOUD_CONFIRMATION_REQUIRED"})


@router.post("", response_model=MeetingImportStatusResponse, status_code=status.HTTP_201_CREATED)
async def create_import(
    body: MeetingImportCreateRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> MeetingImportStatusResponse:
    settings = _settings()
    owner_id = _authorize(current_user, MEETING_CREATE)
    _validate_model(body)
    try:
        result, replayed = await _repository(session, settings).create(
            owner_id,
            body,
            idempotency_key=idempotency_key,
        )
    except MeetingImportError as exc:
        _raise_import_error(exc)
    if replayed:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotency-Replayed"] = "true"
    return result


@router.get("/{upload_id}", response_model=MeetingImportStatusResponse)
async def get_import(
    upload_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> MeetingImportStatusResponse:
    settings = _settings()
    owner_id = _authorize(current_user, MEETING_READ)
    repository = _repository(session, settings)
    try:
        return await repository.status(await repository.get_owned(upload_id, owner_id))
    except MeetingImportError as exc:
        _raise_import_error(exc)


@router.put("/{upload_id}/chunks/{ordinal}", response_model=MeetingImportChunkResponse)
async def put_import_chunk(
    upload_id: str,
    ordinal: int,
    request: Request,
    x_chunk_offset: int = Header(alias="X-Chunk-Offset", ge=0),
    x_chunk_sha256: str = Header(alias="X-Chunk-SHA256", min_length=64, max_length=64),
    content_length: int | None = Header(default=None, alias="Content-Length", ge=0),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> MeetingImportChunkResponse:
    settings = _settings()
    owner_id = _authorize(current_user, MEETING_CREATE)
    if content_length is None:
        raise HTTPException(
            status_code=411,
            detail={"code": "MEETING_IMPORT_CONTENT_LENGTH_REQUIRED"},
        )
    if content_length > settings.max_chunk_bytes:
        raise HTTPException(
            status_code=413,
            detail={"code": "MEETING_IMPORT_CHUNK_TOO_LARGE"},
        )
    try:
        return await _repository(session, settings).put_chunk(
            upload_id,
            owner_id,
            ordinal=ordinal,
            byte_offset=x_chunk_offset,
            sha256=x_chunk_sha256,
            content_length=content_length,
            stream=request.stream(),
        )
    except MeetingImportError as exc:
        _raise_import_error(exc)


@router.post("/{upload_id}/complete", response_model=MeetingImportStatusResponse)
async def complete_import(
    upload_id: str,
    body: MeetingImportCompleteRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> MeetingImportStatusResponse:
    settings = _settings()
    owner_id = _authorize(current_user, MEETING_CREATE)
    try:
        return await _repository(session, settings).complete(upload_id, owner_id, body)
    except MeetingImportError as exc:
        _raise_import_error(exc)


@router.post("/{upload_id}/retry", response_model=MeetingImportStatusResponse)
async def retry_import(
    upload_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> MeetingImportStatusResponse:
    settings = _settings()
    owner_id = _authorize(current_user, MEETING_UPDATE)
    try:
        return await _repository(session, settings).retry(upload_id, owner_id)
    except MeetingImportError as exc:
        _raise_import_error(exc)


@router.delete("/{upload_id}", response_model=MeetingImportStatusResponse)
async def cancel_import(
    upload_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> MeetingImportStatusResponse:
    settings = _settings()
    owner_id = _authorize(current_user, MEETING_DELETE)
    try:
        return await _repository(session, settings).cancel(upload_id, owner_id)
    except MeetingImportError as exc:
        _raise_import_error(exc)
