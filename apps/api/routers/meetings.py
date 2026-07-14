"""Versioned REST API for the isolated meeting-transcription domain."""

# FastAPI intentionally declares dependencies and validated parameters as defaults.
# ruff: noqa: B008

from __future__ import annotations

from typing import Any, Awaitable, TypeVar

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from services.auth_dependencies import get_current_user
from services.auth_service import User
from services.meeting_config import MeetingSettings, meeting_capabilities
from services.meeting_contracts import (
    ArtifactRegenerateRequest,
    ArtifactResponse,
    AudioManifestResponse,
    CorrectionFeedbackResponse,
    LexiconEntryCreateRequest,
    LexiconEntryUpdateRequest,
    LexiconResponse,
    LexiconVersionResponse,
    MeetingActionResponse,
    MeetingCapabilitiesResponse,
    MeetingCreateRequest,
    MeetingEventPage,
    MeetingModelCatalogResponse,
    MeetingSessionPage,
    MeetingSessionResponse,
    MeetingSessionSort,
    MeetingState,
    MeetingStopResponse,
    MeetingUpdateRequest,
    ModelSelectionUpdateRequest,
    ModelSettingResponse,
    SegmentCorrectionRequest,
    SegmentCorrectionResponse,
    SegmentRevertRequest,
    SegmentSpeakerRenameRequest,
    SegmentSpeakerRenameResponse,
    SpeakerMappingResponse,
    SpeakerMergeRequest,
    SpeakerRenameRequest,
    SpeakerSplitRequest,
    SpeakerTrackResponse,
    TermCandidateResponse,
    TranscriptPage,
    VoiceMatchDecisionRequest,
    VoiceMatchResponse,
    VoiceprintEnrollmentRequest,
    VoiceprintEnrollmentResponse,
    VoiceProfileCreateRequest,
    VoiceProfileResponse,
    VoiceProfileStatus,
)
from services.meeting_database import get_meeting_async_session as get_async_session
from services.meeting_model_catalog import MeetingModelCatalog, MeetingModelCatalogError
from services.meeting_permissions import (
    MEETING_ADMIN,
    MEETING_CREATE,
    MEETING_DELETE,
    MEETING_READ,
    MEETING_UPDATE,
    MEETING_VOICEPRINT_MANAGE,
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
    artifact_response,
    correction_response,
    event_response,
    job_response,
    lexicon_entry_response,
    lexicon_version_response,
    model_setting_response,
    session_response,
    speaker_response,
    term_candidate_response,
    voice_match_response,
    voice_profile_response,
)
from services.meeting_stop_finalization import (
    MeetingStopFinalizationConflict,
    MeetingStopFinalizationService,
)
from sqlmodel.ext.asyncio.session import AsyncSession

router = APIRouter(prefix="/meetings/v1", tags=["meetings"])
T = TypeVar("T")


def _settings() -> MeetingSettings:
    return MeetingSettings.from_env()


def _require_enabled(*, capability: str | None = None) -> MeetingSettings:
    value = _settings()
    if not value.operational:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MEETINGS_DISABLED" if not value.enabled else "MEETINGS_CONFIGURATION_INVALID",
                "configuration_errors": list(value.errors),
            },
        )
    if capability == "ai" and not value.ai_enabled:
        raise HTTPException(status_code=503, detail={"code": "MEETING_AI_DISABLED"})
    if capability == "voiceprint" and not value.voiceprint_enabled:
        raise HTTPException(status_code=503, detail={"code": "MEETING_VOICEPRINT_DISABLED"})
    return value


def _authorize(user: User, permission: str) -> int:
    require_meeting_permission(user, permission)
    return meeting_user_id(user)


async def _repo_call(awaitable: Awaitable[T]) -> T:
    try:
        return await awaitable
    except MeetingResourceNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": exc.code}) from exc
    except (MeetingVersionConflict, MeetingIdempotencyConflict) as exc:
        detail: dict[str, Any] = {"code": exc.code, "message": str(exc)}
        current = getattr(exc, "current", None)
        if current:
            detail["current"] = current
        raise HTTPException(status_code=409, detail=detail) from exc
    except MeetingStopFinalizationConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "audio_code": exc.audio_code},
        ) from exc
    except MeetingInvalidOperation as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except MeetingRepositoryError as exc:
        raise HTTPException(status_code=500, detail={"code": exc.code}) from exc


_MEETING_MODEL_CATALOG = MeetingModelCatalog()


def _catalog() -> MeetingModelCatalog:
    return _MEETING_MODEL_CATALOG


def _validate_model_request(request: MeetingCreateRequest) -> str | None:
    if not request.ai_enabled:
        return None
    _require_enabled(capability="ai")
    if request.model_selection.mode != "pinned":
        return None
    try:
        model = _catalog().require_available(request.model_selection.model_ref or "")
    except MeetingModelCatalogError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "MEETING_MODEL_INVALID", "message": str(exc)},
        ) from exc
    if model.locality == "cloud" and not request.model_selection.cloud_data_boundary_confirmed:
        raise HTTPException(
            status_code=422,
            detail={"code": "MEETING_CLOUD_CONFIRMATION_REQUIRED"},
        )
    return model.locality


@router.get("/capabilities", response_model=MeetingCapabilitiesResponse)
async def capabilities(current_user: User = Depends(get_current_user)) -> MeetingCapabilitiesResponse:
    _authorize(current_user, MEETING_READ)
    return MeetingCapabilitiesResponse.model_validate(meeting_capabilities())


@router.get("/models", response_model=MeetingModelCatalogResponse)
async def models(
    purpose: str = Query(default="meeting_postprocess"),
    current_user: User = Depends(get_current_user),
) -> MeetingModelCatalogResponse:
    _authorize(current_user, MEETING_READ)
    _require_enabled(capability="ai")
    try:
        items = _catalog().list_models(purpose)
    except MeetingModelCatalogError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "MEETING_MODEL_CATALOG_UNAVAILABLE", "message": str(exc)},
        ) from exc
    return MeetingModelCatalogResponse(purpose=purpose, items=items)


@router.post("/models/refresh", response_model=MeetingModelCatalogResponse)
async def refresh_models(
    purpose: str = Query(default="meeting_postprocess"),
    current_user: User = Depends(get_current_user),
) -> MeetingModelCatalogResponse:
    _authorize(current_user, MEETING_ADMIN)
    _require_enabled(capability="ai")
    try:
        items = _catalog().list_models(purpose, refresh=True)
    except MeetingModelCatalogError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "MEETING_MODEL_CATALOG_UNAVAILABLE", "message": str(exc)},
        ) from exc
    return MeetingModelCatalogResponse(purpose=purpose, items=items)


@router.post("/sessions", response_model=MeetingSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    request: MeetingCreateRequest,
    response: Response,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128),
) -> MeetingSessionResponse:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_CREATE)
    _validate_model_request(request)
    value, replayed, _ = await _repo_call(
        MeetingRepository(async_session).create_session(
            owner_id,
            request,
            idempotency_key=idempotency_key,
        )
    )
    if replayed:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotency-Replayed"] = "true"
    return session_response(value)


@router.get("/sessions", response_model=MeetingSessionPage)
async def list_sessions(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    state_filter: MeetingState | None = Query(default=None, alias="state"),
    q: str | None = Query(default=None, max_length=200),
    sort: MeetingSessionSort = Query(default=MeetingSessionSort.STARTED_AT_DESC),
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> MeetingSessionPage:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_READ)
    items, total = await _repo_call(
        MeetingRepository(async_session).list_sessions(
            owner_id,
            offset=offset,
            limit=limit,
            state=getattr(state_filter, "value", state_filter) if state_filter else None,
            q=q,
            sort=getattr(sort, "value", sort),
        )
    )
    return MeetingSessionPage(
        items=[
            session_response(
                item.session,
                speaker_count=item.speaker_count,
                model_label=item.model_label,
                model_locality=item.model_locality,
            )
            for item in items
        ],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/sessions/{meeting_id}", response_model=MeetingSessionResponse)
async def get_session(
    meeting_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> MeetingSessionResponse:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_READ)
    value = await _repo_call(MeetingRepository(async_session).get_session(meeting_id, owner_id))
    return session_response(value)


@router.patch("/sessions/{meeting_id}", response_model=MeetingSessionResponse)
async def update_session(
    meeting_id: str,
    request: MeetingUpdateRequest,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> MeetingSessionResponse:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_UPDATE)
    value, _ = await _repo_call(MeetingRepository(async_session).update_session(meeting_id, owner_id, request))
    return session_response(value)


async def _capture_action(
    meeting_id: str,
    action: str,
    current_user: User,
    async_session: AsyncSession,
) -> MeetingActionResponse:
    _require_enabled()
    permission = MEETING_DELETE if action == "delete" else MEETING_UPDATE
    owner_id = _authorize(current_user, permission)
    value, idempotent, event = await _repo_call(
        MeetingRepository(async_session).transition_session(meeting_id, owner_id, action)
    )
    return MeetingActionResponse(
        session=session_response(value),
        idempotent=idempotent,
        event_cursor=event.cursor if event else None,
    )


@router.post("/sessions/{meeting_id}/start", response_model=MeetingActionResponse)
async def start_session(
    meeting_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> MeetingActionResponse:
    return await _capture_action(meeting_id, "start", current_user, async_session)


@router.post("/sessions/{meeting_id}/pause", response_model=MeetingActionResponse)
async def pause_session(
    meeting_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> MeetingActionResponse:
    return await _capture_action(meeting_id, "pause", current_user, async_session)


@router.post("/sessions/{meeting_id}/resume", response_model=MeetingActionResponse)
async def resume_session(
    meeting_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> MeetingActionResponse:
    return await _capture_action(meeting_id, "resume", current_user, async_session)


@router.post("/sessions/{meeting_id}/stop", response_model=MeetingStopResponse)
async def stop_session(
    meeting_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> MeetingStopResponse:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_UPDATE)
    result = await _repo_call(
        MeetingStopFinalizationService(async_session).stop(meeting_id, owner_id)
    )
    return MeetingStopResponse(
        session=session_response(result.session),
        idempotent=result.idempotent,
        event_cursor=result.event.cursor if result.event else None,
        finalization_path=result.finalization_path,
        audio_status=result.audio_status,
    )


@router.post("/sessions/{meeting_id}/finalize")
async def finalize_session(
    meeting_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_UPDATE)
    value, idempotent, job, event = await _repo_call(
        MeetingRepository(async_session).finalize_session(meeting_id, owner_id)
    )
    return {
        "session": session_response(value),
        "idempotent": idempotent,
        "job": job_response(job) if job else None,
        "event_cursor": event.cursor if event else None,
    }


@router.delete("/sessions/{meeting_id}", status_code=status.HTTP_202_ACCEPTED)
async def delete_session(
    meeting_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_DELETE)
    value, idempotent, job = await _repo_call(MeetingRepository(async_session).request_delete(meeting_id, owner_id))
    return {
        "session": session_response(value),
        "idempotent": idempotent,
        "job": job_response(job) if job else None,
    }


@router.get("/sessions/{meeting_id}/events", response_model=MeetingEventPage)
async def list_events(
    meeting_id: str,
    after_cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> MeetingEventPage:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_READ)
    items, next_cursor = await _repo_call(
        MeetingRepository(async_session).list_events(
            meeting_id,
            owner_id,
            after_cursor=after_cursor,
            limit=limit,
        )
    )
    return MeetingEventPage(
        items=[event_response(item) for item in items],
        next_cursor=next_cursor,
    )


@router.get("/sessions/{meeting_id}/transcript", response_model=TranscriptPage)
async def transcript(
    meeting_id: str,
    after_ordinal: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> TranscriptPage:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_READ)
    items, next_ordinal = await _repo_call(
        MeetingRepository(async_session).transcript_page(
            meeting_id,
            owner_id,
            after_ordinal=after_ordinal,
            limit=limit,
        )
    )
    return TranscriptPage(items=items, next_ordinal=next_ordinal)


@router.get("/sessions/{meeting_id}/speakers", response_model=list[SpeakerTrackResponse])
async def speakers(
    meeting_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> list[SpeakerTrackResponse]:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_READ)
    items = await _repo_call(MeetingRepository(async_session).list_speakers(meeting_id, owner_id))
    return [speaker_response(item) for item in items]


@router.patch(
    "/sessions/{meeting_id}/speakers/{track_id}",
    response_model=SpeakerTrackResponse,
)
async def rename_speaker(
    meeting_id: str,
    track_id: str,
    request: SpeakerRenameRequest,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128),
) -> SpeakerTrackResponse:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_UPDATE)
    response, _ = await _repo_call(
        MeetingRepository(async_session).rename_speaker(
            meeting_id,
            track_id,
            owner_id,
            request,
            idempotency_key=idempotency_key,
        )
    )
    return response


@router.post(
    "/sessions/{meeting_id}/speakers/{track_id}/merge",
    response_model=SpeakerMappingResponse,
)
async def merge_speakers(
    meeting_id: str,
    track_id: str,
    request: SpeakerMergeRequest,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128),
) -> SpeakerMappingResponse:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_UPDATE)
    return await _repo_call(
        MeetingRepository(async_session).merge_speakers(
            meeting_id,
            track_id,
            owner_id,
            request,
            idempotency_key=idempotency_key,
        )
    )


@router.post(
    "/sessions/{meeting_id}/speakers/{track_id}/split",
    response_model=SpeakerMappingResponse,
)
async def split_speaker(
    meeting_id: str,
    track_id: str,
    request: SpeakerSplitRequest,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> SpeakerMappingResponse:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_UPDATE)
    return await _repo_call(
        MeetingRepository(async_session).split_speaker(
            meeting_id,
            track_id,
            owner_id,
            request,
        )
    )


@router.patch(
    "/sessions/{meeting_id}/segments/{segment_id}/speaker",
    response_model=SegmentSpeakerRenameResponse,
)
async def rename_segment_speaker(
    meeting_id: str,
    segment_id: str,
    request: SegmentSpeakerRenameRequest,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128),
) -> SegmentSpeakerRenameResponse:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_UPDATE)
    return await _repo_call(
        MeetingRepository(async_session).rename_segment_speaker(
            meeting_id,
            segment_id,
            owner_id,
            request,
            idempotency_key=idempotency_key,
        )
    )


@router.patch(
    "/sessions/{meeting_id}/segments/{segment_id}",
    response_model=SegmentCorrectionResponse,
)
async def correct_segment(
    meeting_id: str,
    segment_id: str,
    request: SegmentCorrectionRequest,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128),
) -> SegmentCorrectionResponse:
    settings = _require_enabled()
    owner_id = _authorize(current_user, MEETING_UPDATE)
    return await _repo_call(
        MeetingRepository(async_session).correct_segment(
            meeting_id,
            segment_id,
            owner_id,
            request,
            idempotency_key=idempotency_key,
            correction_learning_enabled=settings.correction_learning_enabled,
        )
    )


@router.post("/sessions/{meeting_id}/segments/{segment_id}/revert", response_model=dict)
async def revert_segment(
    meeting_id: str,
    segment_id: str,
    request: SegmentRevertRequest,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_UPDATE)
    value = await _repo_call(
        MeetingRepository(async_session).revert_segment(
            meeting_id,
            segment_id,
            owner_id,
            revision_no=request.revision_no,
            expected_revision=request.expected_revision,
        )
    )
    return value.model_dump(mode="json")


@router.get("/correction-feedback", response_model=list[CorrectionFeedbackResponse])
async def correction_feedback_list(
    status_filter: str | None = Query(default=None, alias="status"),
    meeting_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> list[CorrectionFeedbackResponse]:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_READ)
    values = await _repo_call(
        MeetingRepository(async_session).list_correction_feedback(
            owner_id,
            status=status_filter,
            meeting_id=meeting_id,
            limit=limit,
        )
    )
    return [correction_response(value) for value in values]


@router.get("/correction-feedback/{feedback_id}", response_model=CorrectionFeedbackResponse)
async def correction_feedback_get(
    feedback_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> CorrectionFeedbackResponse:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_READ)
    value = await _repo_call(MeetingRepository(async_session).get_correction_feedback(feedback_id, owner_id))
    return correction_response(value)


async def _set_feedback_excluded(
    feedback_id: str,
    excluded: bool,
    current_user: User,
    async_session: AsyncSession,
) -> CorrectionFeedbackResponse:
    settings = _require_enabled()
    owner_id = _authorize(current_user, MEETING_UPDATE)
    value = await _repo_call(
        MeetingRepository(async_session).set_correction_feedback_excluded(
            feedback_id,
            owner_id,
            excluded=excluded,
            correction_learning_enabled=settings.correction_learning_enabled,
        )
    )
    return correction_response(value)


@router.post("/correction-feedback/{feedback_id}/exclude", response_model=CorrectionFeedbackResponse)
async def correction_feedback_exclude(
    feedback_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> CorrectionFeedbackResponse:
    return await _set_feedback_excluded(feedback_id, True, current_user, async_session)


@router.post("/correction-feedback/{feedback_id}/restore", response_model=CorrectionFeedbackResponse)
async def correction_feedback_restore(
    feedback_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> CorrectionFeedbackResponse:
    return await _set_feedback_excluded(feedback_id, False, current_user, async_session)


@router.get("/term-candidates", response_model=list[TermCandidateResponse])
async def term_candidates(
    status_filter: str | None = Query(default=None, alias="status"),
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> list[TermCandidateResponse]:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_READ)
    values = await _repo_call(MeetingRepository(async_session).list_term_candidates(owner_id, status=status_filter))
    return [term_candidate_response(value) for value in values]


@router.post("/term-candidates/{candidate_id}/confirm")
async def confirm_term_candidate(
    candidate_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    settings = _require_enabled()
    owner_id = _authorize(current_user, MEETING_UPDATE)
    candidate, entry, version = await _repo_call(
        MeetingRepository(async_session).confirm_term_candidate(
            candidate_id,
            owner_id,
            correction_learning_enabled=settings.correction_learning_enabled,
        )
    )
    return {
        "candidate": term_candidate_response(candidate),
        "entry": lexicon_entry_response(entry),
        "version": lexicon_version_response(version),
    }


@router.post("/term-candidates/{candidate_id}/reject", response_model=TermCandidateResponse)
async def reject_term_candidate(
    candidate_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> TermCandidateResponse:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_UPDATE)
    value = await _repo_call(MeetingRepository(async_session).reject_term_candidate(candidate_id, owner_id))
    return term_candidate_response(value)


@router.get("/lexicon", response_model=LexiconResponse)
async def get_lexicon(
    language: str = Query(default="zh-CN", min_length=2, max_length=32),
    meeting_id: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> LexiconResponse:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_READ)
    return await _repo_call(
        MeetingRepository(async_session).get_lexicon(
            owner_id,
            language=language,
            meeting_id=meeting_id,
        )
    )


@router.post("/lexicon", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_lexicon_entry(
    request: LexiconEntryCreateRequest,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_UPDATE)
    entry, version = await _repo_call(MeetingRepository(async_session).create_lexicon_entry(owner_id, request))
    return {
        "entry": lexicon_entry_response(entry),
        "version": lexicon_version_response(version),
    }


@router.patch("/lexicon/{entry_id}", response_model=dict)
async def update_lexicon_entry(
    entry_id: str,
    request: LexiconEntryUpdateRequest,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_UPDATE)
    entry, version = await _repo_call(
        MeetingRepository(async_session).update_lexicon_entry(entry_id, owner_id, request)
    )
    return {
        "entry": lexicon_entry_response(entry),
        "version": lexicon_version_response(version),
    }


@router.delete("/lexicon/{entry_id}", response_model=dict)
async def delete_lexicon_entry(
    entry_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_UPDATE)
    entry, version = await _repo_call(MeetingRepository(async_session).delete_lexicon_entry(entry_id, owner_id))
    return {
        "entry": lexicon_entry_response(entry),
        "version": lexicon_version_response(version),
    }


@router.get("/lexicon/versions", response_model=list[LexiconVersionResponse])
async def lexicon_versions(
    language: str = Query(default="zh-CN", min_length=2, max_length=32),
    meeting_id: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> list[LexiconVersionResponse]:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_READ)
    values = await _repo_call(
        MeetingRepository(async_session).list_lexicon_versions(
            owner_id,
            language=language,
            meeting_id=meeting_id,
        )
    )
    return [lexicon_version_response(value) for value in values]


@router.post("/lexicon/versions/{version}/activate", response_model=LexiconVersionResponse)
async def activate_lexicon_version(
    version: int,
    language: str = Query(default="zh-CN", min_length=2, max_length=32),
    meeting_id: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> LexiconVersionResponse:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_UPDATE)
    value = await _repo_call(
        MeetingRepository(async_session).activate_lexicon_version(
            owner_id,
            version,
            language=language,
            meeting_id=meeting_id,
        )
    )
    return lexicon_version_response(value)


@router.get("/voiceprints", response_model=list[VoiceProfileResponse])
async def voiceprints(
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> list[VoiceProfileResponse]:
    _require_enabled(capability="voiceprint")
    owner_id = _authorize(current_user, MEETING_READ)
    values = await _repo_call(MeetingRepository(async_session).list_voice_profiles(owner_id))
    return [voice_profile_response(profile, consent_active=consent) for profile, consent in values]


@router.post("/voiceprints", response_model=VoiceProfileResponse, status_code=status.HTTP_201_CREATED)
async def create_voiceprint(
    request: VoiceProfileCreateRequest,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> VoiceProfileResponse:
    _require_enabled(capability="voiceprint")
    owner_id = _authorize(current_user, MEETING_VOICEPRINT_MANAGE)
    value = await _repo_call(MeetingRepository(async_session).create_voice_profile(owner_id, request.display_name))
    return voice_profile_response(value)


async def _voice_profile_status(
    profile_id: str,
    target: VoiceProfileStatus,
    current_user: User,
    async_session: AsyncSession,
) -> VoiceProfileResponse:
    _require_enabled(capability="voiceprint")
    owner_id = _authorize(current_user, MEETING_VOICEPRINT_MANAGE)
    value, _ = await _repo_call(MeetingRepository(async_session).set_voice_profile_status(profile_id, owner_id, target))
    return voice_profile_response(value)


@router.post("/voiceprints/{profile_id}/pause", response_model=VoiceProfileResponse)
async def pause_voiceprint(
    profile_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> VoiceProfileResponse:
    return await _voice_profile_status(profile_id, VoiceProfileStatus.PAUSED, current_user, async_session)


@router.post("/voiceprints/{profile_id}/resume", response_model=VoiceProfileResponse)
async def resume_voiceprint(
    profile_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> VoiceProfileResponse:
    return await _voice_profile_status(profile_id, VoiceProfileStatus.ACTIVE, current_user, async_session)


@router.post("/voiceprints/{profile_id}/re-enroll", response_model=VoiceProfileResponse)
async def reenroll_voiceprint(
    profile_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> VoiceProfileResponse:
    return await _voice_profile_status(profile_id, VoiceProfileStatus.COLLECTING, current_user, async_session)


@router.post("/voiceprints/{profile_id}/consent/revoke", response_model=VoiceProfileResponse)
async def revoke_voiceprint(
    profile_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> VoiceProfileResponse:
    _require_enabled(capability="voiceprint")
    owner_id = _authorize(current_user, MEETING_VOICEPRINT_MANAGE)
    value = await _repo_call(MeetingRepository(async_session).revoke_voiceprint_consent(profile_id, owner_id))
    return voice_profile_response(value, consent_active=False)


@router.delete("/voiceprints/{profile_id}", response_model=VoiceProfileResponse)
async def delete_voiceprint(
    profile_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> VoiceProfileResponse:
    return await _voice_profile_status(profile_id, VoiceProfileStatus.DELETED, current_user, async_session)


@router.post(
    "/sessions/{meeting_id}/speakers/{track_id}/voiceprint-enrollment",
    response_model=VoiceprintEnrollmentResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enroll_voiceprint(
    meeting_id: str,
    track_id: str,
    request: VoiceprintEnrollmentRequest,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
) -> VoiceprintEnrollmentResponse:
    _require_enabled(capability="voiceprint")
    owner_id = _authorize(current_user, MEETING_VOICEPRINT_MANAGE)
    return await _repo_call(
        MeetingRepository(async_session).enroll_voiceprint(
            meeting_id,
            track_id,
            owner_id,
            request,
            idempotency_key=idempotency_key,
        )
    )


@router.post(
    "/sessions/{meeting_id}/voiceprint-matches/{match_id}/decision",
    response_model=VoiceMatchResponse,
)
async def decide_voice_match(
    meeting_id: str,
    match_id: str,
    request: VoiceMatchDecisionRequest,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> VoiceMatchResponse:
    _require_enabled(capability="voiceprint")
    owner_id = _authorize(current_user, MEETING_VOICEPRINT_MANAGE)
    value, _ = await _repo_call(
        MeetingRepository(async_session).decide_voice_match(
            meeting_id,
            match_id,
            owner_id,
            request.decision,
        )
    )
    return voice_match_response(value)


@router.put("/sessions/{meeting_id}/model-selection", response_model=ModelSettingResponse)
async def update_model_selection(
    meeting_id: str,
    request: ModelSelectionUpdateRequest,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> ModelSettingResponse:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_UPDATE)
    locality: str | None = None
    if request.selection.mode != "none":
        _require_enabled(capability="ai")
    if request.selection.mode == "pinned":
        try:
            descriptor = _catalog().require_available(request.selection.model_ref or "")
        except MeetingModelCatalogError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "MEETING_MODEL_INVALID", "message": str(exc)},
            ) from exc
        locality = descriptor.locality
    _, setting, _ = await _repo_call(
        MeetingRepository(async_session).update_model_selection(
            meeting_id,
            owner_id,
            request.selection,
            expected_settings_version=request.expected_settings_version,
            provider_locality=locality,
        )
    )
    return model_setting_response(setting)


@router.get("/sessions/{meeting_id}/artifacts", response_model=list[ArtifactResponse])
async def artifacts(
    meeting_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> list[ArtifactResponse]:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_READ)
    values = await _repo_call(MeetingRepository(async_session).list_artifacts(meeting_id, owner_id))
    return [artifact_response(value) for value in values]


@router.get("/sessions/{meeting_id}/artifacts/{artifact_id}", response_model=ArtifactResponse)
async def artifact(
    meeting_id: str,
    artifact_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> ArtifactResponse:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_READ)
    value = await _repo_call(MeetingRepository(async_session).get_artifact(meeting_id, artifact_id, owner_id))
    return artifact_response(value)


@router.post("/sessions/{meeting_id}/artifacts/{artifact_id}/regenerate")
async def regenerate_artifact(
    meeting_id: str,
    artifact_id: str,
    request: ArtifactRegenerateRequest,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    _require_enabled(capability="ai")
    owner_id = _authorize(current_user, MEETING_UPDATE)
    artifact_value, job = await _repo_call(
        MeetingRepository(async_session).regenerate_artifact(
            meeting_id,
            artifact_id,
            owner_id,
            expected_settings_version=request.expected_settings_version,
        )
    )
    return {"artifact": artifact_response(artifact_value), "job": job_response(job)}


@router.get("/sessions/{meeting_id}/jobs")
async def jobs(
    meeting_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> list[dict[str, Any]]:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_READ)
    values = await _repo_call(MeetingRepository(async_session).list_jobs(meeting_id, owner_id))
    return [job_response(value).model_dump(mode="json") for value in values]


@router.post("/sessions/{meeting_id}/jobs/{job_id}/retry")
async def retry_job(
    meeting_id: str,
    job_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_UPDATE)
    value = await _repo_call(MeetingRepository(async_session).retry_job(meeting_id, job_id, owner_id))
    return job_response(value).model_dump(mode="json")


@router.get("/sessions/{meeting_id}/audio/manifest", response_model=AudioManifestResponse)
async def audio_manifest(
    meeting_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> AudioManifestResponse:
    _require_enabled()
    owner_id = _authorize(current_user, MEETING_READ)
    return await _repo_call(MeetingRepository(async_session).audio_manifest(meeting_id, owner_id))
