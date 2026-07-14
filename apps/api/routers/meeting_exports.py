"""Owner-scoped meeting export creation, status, and private download."""

# FastAPI intentionally declares validated dependencies as defaults.
# ruff: noqa: B008

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from services.auth_dependencies import get_current_user
from services.auth_service import User
from services.meeting_config import MeetingSettings
from services.meeting_contracts import (
    MeetingExportCreateRequest,
    MeetingExportResponse,
    MeetingExportTicketResponse,
)
from services.meeting_database import get_meeting_async_session as get_async_session
from services.meeting_export import MeetingExportError, MeetingExportService
from services.meeting_permissions import (
    MEETING_EXPORT,
    meeting_user_id,
    require_meeting_permission,
)
from services.meeting_repository import (
    MeetingIdempotencyConflict,
    MeetingInvalidOperation,
    MeetingRepository,
    MeetingRepositoryError,
    MeetingResourceNotFound,
    MeetingVersionConflict,
    export_response,
)
from sqlmodel.ext.asyncio.session import AsyncSession

router = APIRouter(prefix="/meetings/v1", tags=["meeting-exports"])


def _enabled() -> None:
    settings = MeetingSettings.from_env()
    if not settings.operational:
        raise HTTPException(status_code=503, detail={"code": "MEETINGS_DISABLED"})


def _owner(user: User) -> int:
    require_meeting_permission(user, MEETING_EXPORT)
    return meeting_user_id(user)


def _origin(request: Request) -> str:
    value = str(request.headers.get("origin") or "").strip()
    if value:
        return value
    return f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, MeetingResourceNotFound):
        return HTTPException(status_code=404, detail={"code": "MEETING_RESOURCE_NOT_FOUND"})
    if isinstance(exc, (MeetingVersionConflict, MeetingIdempotencyConflict)):
        return HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        )
    if isinstance(exc, MeetingInvalidOperation):
        return HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        )
    if isinstance(exc, MeetingExportError):
        status_code = (
            503
            if exc.code
            in {
                "EXPORT_CONFIGURATION_INVALID",
                "EXPORT_STORAGE_UNAVAILABLE",
            }
            else 409
        )
        return HTTPException(
            status_code=status_code,
            detail={"code": exc.code, "message": str(exc)},
        )
    if isinstance(exc, MeetingRepositoryError):
        return HTTPException(status_code=500, detail={"code": exc.code})
    return HTTPException(status_code=500, detail={"code": "MEETING_EXPORT_FAILED"})


def _download_url(request: Request, meeting_id: str, export_id: str, ticket: str) -> str:
    base = str(
        request.url_for(
            "meeting_export_download",
            meeting_id=meeting_id,
            export_id=export_id,
        )
    )
    return f"{base}?ticket={quote(ticket, safe='')}"


async def _response_with_ticket(
    request: Request,
    service: MeetingExportService,
    value: MeetingExportResponse,
    owner_user_id: int,
) -> MeetingExportResponse:
    if value.state != "ready":
        return value
    raw, expires_at, _ = await service.issue_ticket(
        value.meeting_id,
        value.id,
        owner_user_id,
        origin=_origin(request),
    )
    return value.model_copy(
        update={
            "download_url": _download_url(
                request,
                value.meeting_id,
                value.id,
                raw,
            ),
            "download_expires_at": expires_at,
        }
    )


@router.post(
    "/sessions/{meeting_id}/exports",
    response_model=MeetingExportResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_meeting_export(
    meeting_id: str,
    payload: MeetingExportCreateRequest,
    response: Response,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
) -> MeetingExportResponse:
    _enabled()
    owner_id = _owner(current_user)
    try:
        artifact, job, replayed, _ = await MeetingRepository(async_session).create_export(
            meeting_id,
            owner_id,
            payload,
            idempotency_key=idempotency_key,
        )
        value = export_response(artifact, job)
    except Exception as exc:
        raise _error(exc) from exc
    if replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return value


@router.get(
    "/sessions/{meeting_id}/exports",
    response_model=list[MeetingExportResponse],
)
async def list_meeting_exports(
    meeting_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> list[MeetingExportResponse]:
    _enabled()
    owner_id = _owner(current_user)
    try:
        return await MeetingExportService(async_session).list_exports(meeting_id, owner_id)
    except Exception as exc:
        raise _error(exc) from exc


@router.get(
    "/sessions/{meeting_id}/exports/{export_id}",
    response_model=MeetingExportResponse,
)
async def get_meeting_export(
    meeting_id: str,
    export_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> MeetingExportResponse:
    _enabled()
    owner_id = _owner(current_user)
    try:
        artifact, job = await MeetingRepository(async_session).get_export(
            meeting_id,
            export_id,
            owner_id,
        )
        service = MeetingExportService(async_session)
        return await _response_with_ticket(
            request,
            service,
            export_response(artifact, job),
            owner_id,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/sessions/{meeting_id}/exports/{export_id}/ticket",
    response_model=MeetingExportTicketResponse,
)
async def create_meeting_export_ticket(
    meeting_id: str,
    export_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> MeetingExportTicketResponse:
    _enabled()
    owner_id = _owner(current_user)
    service = MeetingExportService(async_session)
    try:
        raw, expires_at, _ = await service.issue_ticket(
            meeting_id,
            export_id,
            owner_id,
            origin=_origin(request),
        )
    except Exception as exc:
        raise _error(exc) from exc
    return MeetingExportTicketResponse(
        export_id=export_id,
        meeting_id=meeting_id,
        download_url=_download_url(request, meeting_id, export_id, raw),
        expires_at=expires_at,
    )


@router.get(
    "/sessions/{meeting_id}/exports/{export_id}/download",
    response_class=FileResponse,
    name="meeting_export_download",
)
async def meeting_export_download(
    meeting_id: str,
    export_id: str,
    ticket: str = Query(min_length=20, max_length=256),
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> FileResponse:
    _enabled()
    owner_id = _owner(current_user)
    try:
        downloadable = await MeetingExportService(async_session).consume_ticket(
            meeting_id,
            export_id,
            owner_id,
            ticket,
        )
    except Exception as exc:
        raise _error(exc) from exc
    return FileResponse(
        downloadable.path,
        media_type=downloadable.media_type,
        filename=downloadable.filename,
        content_disposition_type="attachment",
        headers={
            "Cache-Control": "private, no-store",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "X-Export-SHA256": downloadable.sha256,
        },
    )
