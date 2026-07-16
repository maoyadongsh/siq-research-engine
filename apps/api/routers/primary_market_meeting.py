"""Primary-market meeting room chat routes for SIQ IC Hermes agents."""

import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
from database import get_async_session
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from schemas import (
    ChatAttachment,
    ChatAttachmentUploadRequest,
    ChatAttachmentUploadResponse,
    ChatContext,
    ChatContextPage,
    ChatResponse,
)
from services.agent_chat_runtime import (
    HISTORY_LIMIT,
    chat_history_response,
    collect_chat_reply,
    get_active_run_snapshot,
    has_active_run,
    save_message,
    stop_active_run,
    stream_active_run_events,
    stream_chat_reply,
)
from services.auth_dependencies import get_current_user, require_permission
from services.auth_service import User
from services.hermes_client import HermesProfile, collect_run_result, create_run
from services.hermes_model_control import apply_profile_model_mode, maybe_handle_model_control, model_catalog
from services.path_config import BACKEND_DATA_ROOT
from services.session_manager import get_session_manager
from services.usage_service import AGENT_QUESTION_EVENT, record_usage_async
from sqlmodel.ext.asyncio.session import AsyncSession
from sse_starlette.sse import EventSourceResponse

from routers import chat as chat_router
from routers.agent_user_router import enforce_quota_or_429_async
from services import (
    deal_decision,
    deal_evidence,
    deal_phase_artifacts,
    deal_reports,
    deal_retrieval,
    deal_status,
    deal_store,
    ic_agent_output_quality,
    ic_agent_runtime,
    ic_policy,
    ic_profile_contract,
    ic_startup_retrieval,
    primary_market_agent_runtime,
    primary_market_meeting_readiness,
)

IC_MEETING_PROFILES: tuple[HermesProfile, ...] = (
    "siq_ic_master_coordinator",
    "siq_ic_chairman",
    "siq_ic_strategist",
    "siq_ic_sector_expert",
    "siq_ic_finance_auditor",
    "siq_ic_legal_scanner",
    "siq_ic_risk_controller",
)

_PROFILE_SET = set(IC_MEETING_PROFILES)
_SESSION_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")
_LANE_SAFE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
MEETING_TRANSCRIPT_SCHEMA = "siq_primary_market_meeting_transcript_v1"
MEETING_QUALITY_SCHEMA = "siq_primary_market_meeting_quality_v1"
PRIMARY_MARKET_ATTACHMENT_DIR = BACKEND_DATA_ROOT / "chat_uploads" / "primary_market_projects"
PRIMARY_MARKET_PDF_PARSE_DIR = BACKEND_DATA_ROOT / "chat_uploads" / "pdf_parses" / "primary_market_projects"

router = APIRouter()


class PrimaryMarketMeetingAgentContext(BaseModel):
    id: str
    label: str = ""


class PrimaryMarketMeetingContext(BaseModel):
    deal_id: str
    company_name: str | None = None
    phase: str | None = None
    lane: str = "main"
    agent: PrimaryMarketMeetingAgentContext | None = None
    page: ChatContextPage | None = None


class PrimaryMarketMeetingChatRequest(BaseModel):
    message: str
    retrieval_query: str | None = None
    session_id: str | None = None
    display_message: str | None = None
    deal_id: str | None = None
    company_name: str | None = None
    phase: str | None = None
    lane: str = "main"
    model_mode: str | None = None
    context: PrimaryMarketMeetingContext | None = None
    attachments: list[ChatAttachment] = Field(default_factory=list)


def _apply_meeting_model_mode(req: PrimaryMarketMeetingChatRequest, profile: str) -> dict[str, Any] | None:
    if not req.model_mode:
        return None
    try:
        return apply_profile_model_mode(cast(HermesProfile, profile), req.model_mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/primary-market/meeting/models")
def get_primary_market_meeting_models(
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    del current_user
    return model_catalog()


class PrimaryMarketMeetingChatResponse(ChatResponse):
    session_id: str
    quality: dict[str, Any] | None = None


class PrimaryMarketMeetingTranscriptEventInput(BaseModel):
    id: str | None = None
    event_type: str = "message"
    speaker: str | None = None
    title: str | None = None
    body: str = ""
    tone: str | None = None
    meta: dict[str, Any] | str | None = Field(default_factory=dict)
    phase: str | None = None
    agent_id: str | None = None
    attachments: list[ChatAttachment] = Field(default_factory=list)
    lane: str | None = None
    created_at: str | None = None


class PrimaryMarketMeetingTranscriptAppendRequest(BaseModel):
    lane: str = "main"
    event: PrimaryMarketMeetingTranscriptEventInput


class PrimaryMarketMeetingSuggestedQuestion(BaseModel):
    key: str
    label: str
    prompt: str


class PrimaryMarketMeetingSuggestionsResponse(BaseModel):
    deal_id: str
    lane: str
    profile: str
    intro: str = ""
    questions: list[PrimaryMarketMeetingSuggestedQuestion] = Field(default_factory=list)
    source: str = "model"
    error: str | None = None


class PrimaryMarketMeetingPrepareRequest(BaseModel):
    round_name: str = "R1"
    query: str | None = None
    limit: int = Field(default=10, ge=1, le=50)
    include_external: bool = False
    external_providers: list[str] | None = None
    include_vector: bool = True
    include_rerank: bool = True
    vector_collections: list[str] | None = None


class PrimaryMarketMeetingPrepareAllRequest(PrimaryMarketMeetingPrepareRequest):
    profile_ids: list[str] | None = None


class PrimaryMarketMeetingWorkflowAdvanceRequest(BaseModel):
    dry_run: bool = True
    allow_hermes: bool = False
    max_agents: int = Field(default=1, ge=1, le=6)
    r3_skip: bool = False
    r3_skip_reason: str | None = None
    r4_overwrite: bool = False


class PrimaryMarketMeetingR1AgentRunRequest(BaseModel):
    dry_run: bool = True
    allow_hermes: bool = False
    round_name: str = "R1"
    lane: str | None = None
    timeout: float | None = Field(default=None, gt=0)


class PrimaryMarketMeetingR1SerialRunRequest(BaseModel):
    dry_run: bool = True
    allow_hermes: bool = False
    round_name: str = "R1"
    max_agents: int = Field(default=6, ge=1, le=6)
    lane: str | None = None
    timeout: float | None = Field(default=None, gt=0)


class PrimaryMarketMeetingDecisionHumanConfirmRequest(BaseModel):
    status: str = Field(..., min_length=3)
    override_reason: str | None = None
    override_decision: str | None = None
    override_score: float | str | None = None
    dry_run: bool = True


def _not_found(deal_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Deal not found: {deal_id}")


def _user_payload(user: User) -> dict[str, Any]:
    return {
        "id": getattr(user, "id", None),
        "username": getattr(user, "username", None),
    }


def _role_value(user: User) -> str:
    return str(user.role.value if hasattr(user.role, "value") else user.role)


def _record_deal_access_decision(
    deal_id: str,
    *,
    action: str,
    decision: str,
    current_user: User,
    reason: str | None = None,
) -> None:
    try:
        deal_store.append_access_decision(
            deal_id,
            action=f"primary_market.{action}",
            decision=decision,
            actor=_user_payload(current_user),
            reason=reason,
        )
    except Exception:
        return


def _require_deal_access(deal_id: str, action: str, current_user: User) -> None:
    try:
        allowed = deal_store.user_can_access_deal(deal_id, current_user, action=action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc
    if not allowed:
        _record_deal_access_decision(
            deal_id,
            action=action,
            decision="denied",
            current_user=current_user,
            reason="deal_object_access_denied",
        )
        raise _not_found(deal_id)
    if action not in {"view", "read", "list"}:
        _record_deal_access_decision(
            deal_id,
            action=action,
            decision="allowed",
            current_user=current_user,
        )


def _detach_current_user(async_session: AsyncSession, current_user: User) -> None:
    try:
        async_session.expunge(current_user)
    except Exception:
        pass


def canonical_meeting_profile(profile: str) -> HermesProfile:
    if profile not in _PROFILE_SET:
        raise HTTPException(status_code=404, detail="Unknown primary-market meeting agent")
    return cast(HermesProfile, profile)


def deal_id_from_request(req: PrimaryMarketMeetingChatRequest) -> str:
    raw_deal_id = req.deal_id or (req.context.deal_id if req.context else "") or ""
    deal_id = raw_deal_id.strip()
    if not deal_id:
        raise HTTPException(status_code=400, detail="deal_id is required for primary-market meeting chat")
    try:
        return deal_store.validate_deal_id(deal_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _load_deal_summary(deal_id: str, *, current_user: User | None = None) -> dict:
    if current_user is not None:
        _require_deal_access(deal_id, "view", current_user)
    try:
        return deal_store.read_deal_detail(deal_id).get("summary", {})
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


def _validated_deal_dir(
    deal_id: str,
    *,
    current_user: User | None = None,
    action: str = "view",
) -> tuple[str, Path]:
    try:
        normalized = deal_store.validate_deal_id(deal_id)
        package_dir = deal_store.safe_deal_dir(normalized)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not (package_dir / "manifest.json").is_file():
        raise _not_found(normalized)
    if current_user is not None:
        _require_deal_access(normalized, action, current_user)
    return normalized, package_dir


def _normalize_lane(lane: str | None) -> str:
    normalized = str(lane or "").strip() or "main"
    if not _LANE_SAFE.fullmatch(normalized):
        raise HTTPException(status_code=400, detail="lane must be 1-64 chars of A-Z, a-z, 0-9, underscore, dot, or dash")
    return normalized


def _normalize_limit(limit: int | None) -> int:
    if limit is None:
        return 100
    if limit < 1:
        raise HTTPException(status_code=400, detail="limit must be at least 1")
    return min(limit, 500)


def _meeting_transcript_path(package_dir: Path) -> Path:
    return package_dir / "discussion" / "meeting_transcript.json"


def _meeting_quality_path(package_dir: Path) -> Path:
    return package_dir / "discussion" / "meeting_quality.json"


def _safe_attachment_name(stored_name: str) -> str:
    value = str(stored_name or "").strip()
    if not value or "/" in value or "\\" in value:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return value


def _attachment_download_url(deal_id: str, stored_name: str) -> str:
    return f"/api/primary-market/projects/{deal_id}/meeting/attachments/{stored_name}"


def _project_attachment_dir(deal_id: str) -> Path:
    return PRIMARY_MARKET_ATTACHMENT_DIR / deal_id


def _project_pdf_parse_dir(deal_id: str, attachment_id: str) -> Path:
    return PRIMARY_MARKET_PDF_PARSE_DIR / deal_id / attachment_id


def _read_transcript_payload(package_dir: Path, deal_id: str) -> dict[str, Any]:
    path = _meeting_transcript_path(package_dir)
    raw = deal_store.read_json(path, {"events": []}) or {"events": []}
    if isinstance(raw, list):
        events = raw
    elif isinstance(raw, dict) and isinstance(raw.get("events"), list):
        events = raw["events"]
    else:
        events = []
    return {
        "schema_version": MEETING_TRANSCRIPT_SCHEMA,
        "deal_id": deal_id,
        "events": [_normalize_stored_event(event) for event in events if isinstance(event, dict)],
    }


def _write_transcript_payload(package_dir: Path, payload: dict[str, Any]) -> None:
    now = deal_store.utc_now_iso()
    stored = {
        "schema_version": MEETING_TRANSCRIPT_SCHEMA,
        "deal_id": payload["deal_id"],
        "updated_at": now,
        "events": payload["events"],
    }
    deal_store.write_json(_meeting_transcript_path(package_dir), stored)


def _read_quality_payload(package_dir: Path, deal_id: str) -> dict[str, Any]:
    raw = deal_store.read_json(_meeting_quality_path(package_dir), {"events": []}) or {"events": []}
    events = raw if isinstance(raw, list) else raw.get("events") if isinstance(raw, dict) else []
    return {
        "schema_version": MEETING_QUALITY_SCHEMA,
        "deal_id": deal_id,
        "events": [event for event in events if isinstance(event, dict)],
    }


def _write_quality_payload(package_dir: Path, payload: dict[str, Any]) -> None:
    now = deal_store.utc_now_iso()
    deal_store.write_json(
        _meeting_quality_path(package_dir),
        {
            "schema_version": MEETING_QUALITY_SCHEMA,
            "deal_id": payload["deal_id"],
            "updated_at": now,
            "events": payload["events"],
        },
    )


def _redact_meeting_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], deal_store.redact_public_payload(payload))


def _event_lane(event: dict[str, Any]) -> str:
    return str(event.get("lane") or "main").strip() or "main"


def _normalize_stored_event(event: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(event)
    normalized["event_type"] = str(normalized.get("event_type") or normalized.get("type") or "message")
    normalized.pop("type", None)
    normalized["lane"] = _event_lane(normalized)
    normalized["meta"] = normalized.get("meta") or {}
    return normalized


def _build_transcript_event(
    req: PrimaryMarketMeetingTranscriptEventInput,
    *,
    default_lane: str,
) -> dict[str, Any]:
    event_id = str(req.id or "").strip() or f"evt-{uuid4().hex}"
    event_type = str(req.event_type or "").strip() or "message"
    return {
        "id": event_id,
        "event_type": event_type,
        "speaker": req.speaker,
        "title": req.title,
        "body": req.body or "",
        "tone": req.tone,
        "meta": req.meta or {},
        "phase": req.phase,
        "agent_id": req.agent_id,
        "attachments": [attachment.model_dump() for attachment in req.attachments],
        "lane": _normalize_lane(req.lane or default_lane),
        "created_at": req.created_at or deal_store.utc_now_iso(),
    }


@router.get("/primary-market/projects")
def list_projects(
    q: str | None = None,
    status: str | None = None,
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=100),
    include_status: bool = False,
    current_user: User = Depends(require_permission("report.view")),
) -> dict:
    needle = (q or "").strip().lower()
    deals = deal_store.filter_deals_for_user(deal_store.list_deals(), current_user)
    if needle:
        deals = [
            item
            for item in deals
            if needle in str(item.get("deal_id") or "").lower()
            or needle in str(item.get("company_name") or "").lower()
            or needle in str(item.get("industry") or "").lower()
        ]
    if status:
        deals = [item for item in deals if str(item.get("status") or "") == status]
    total = len(deals)
    stats = {
        "total": total,
        "active": sum(1 for item in deals if str(item.get("status") or "") not in {"r4_completed", "archived", "closed"}),
        "diligence": sum(1 for item in deals if str(item.get("status") or "") in {"r1_in_progress", "r0_ready"}),
        "highRisk": sum(1 for item in deals if str(item.get("status") or "") in {"blocked", "fail"}),
    }
    selected = deals
    pagination: dict[str, Any] = {}
    if page is not None or page_size is not None:
        current_page = page or 1
        current_size = page_size or 25
        start = (current_page - 1) * current_size
        selected = deals[start:start + current_size]
        pagination = {"page": current_page, "page_size": current_size, "total": total, "has_more": start + current_size < total}
    payload: dict[str, Any] = {
        "deals": deal_store.redact_public_payload(selected),
        "stats": stats,
        "status_summary": {"by_status": {
            state: sum(1 for item in deals if str(item.get("status") or "unknown") == state)
            for state in sorted({str(item.get("status") or "unknown") for item in deals})
        }},
    }
    if pagination:
        payload["pagination"] = pagination
    if include_status:
        summaries: dict[str, dict[str, Any]] = {}
        for item in selected:
            deal_id = str(item.get("deal_id") or "").strip()
            if not deal_id:
                continue
            try:
                summaries[deal_id] = deal_status.summarize_deal_status(deal_id)
            except (FileNotFoundError, ValueError):
                summaries[deal_id] = {"status": "missing", "ready_for_next_action": False, "counts": {"missing": 1}}
        payload["status_summaries"] = summaries
    return payload


@router.get("/primary-market/projects/{deal_id}")
def get_project(
    deal_id: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict:
    _require_deal_access(deal_id, "view", current_user)
    try:
        return deal_store.read_deal_detail(deal_id)
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.get("/primary-market/projects/{deal_id}/status")
def get_project_status(
    deal_id: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict:
    from services import deal_status

    try:
        normalized_deal_id, _package_dir = _validated_deal_dir(deal_id, current_user=current_user, action="view")
        return deal_status.summarize_deal_status(normalized_deal_id)
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.get("/primary-market/projects/{deal_id}/meeting-transcript")
def get_meeting_transcript(
    deal_id: str,
    lane: str = "main",
    limit: int | None = None,
    current_user: User = Depends(require_permission("report.view")),
) -> dict:
    normalized_deal_id, package_dir = _validated_deal_dir(deal_id, current_user=current_user, action="view")
    normalized_lane = _normalize_lane(lane)
    normalized_limit = _normalize_limit(limit)
    payload = _read_transcript_payload(package_dir, normalized_deal_id)
    matching_events = [event for event in payload["events"] if _event_lane(event) == normalized_lane]
    limited_events = matching_events[-normalized_limit:]
    return _redact_meeting_payload({
        "deal_id": normalized_deal_id,
        "lane": normalized_lane,
        "limit": normalized_limit,
        "total": len(matching_events),
        "events": limited_events,
    })


@router.post("/primary-market/projects/{deal_id}/meeting-transcript/events")
def append_meeting_transcript_event(
    deal_id: str,
    req: PrimaryMarketMeetingTranscriptAppendRequest,
    current_user: User = Depends(require_permission("report.create")),
) -> dict:
    normalized_deal_id, package_dir = _validated_deal_dir(deal_id, current_user=current_user, action="write")
    event = _build_transcript_event(req.event, default_lane=_normalize_lane(req.lane))
    payload = _read_transcript_payload(package_dir, normalized_deal_id)
    payload["events"].append(event)
    _write_transcript_payload(package_dir, payload)
    lane_total = sum(1 for item in payload["events"] if _event_lane(item) == event["lane"])
    lane_events = [item for item in payload["events"] if _event_lane(item) == event["lane"]]
    return _redact_meeting_payload({
        "deal_id": normalized_deal_id,
        "lane": event["lane"],
        "event": event,
        "events": lane_events,
        "total": lane_total,
    })


@router.get("/primary-market/meeting/{deal_id}/quality")
def get_meeting_quality(
    deal_id: str,
    lane: str | None = None,
    profile_id: str | None = None,
    limit: int | None = None,
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    normalized_deal_id, package_dir = _validated_deal_dir(deal_id, current_user=current_user, action="view")
    normalized_lane = _normalize_lane(lane) if lane else None
    normalized_profile = None
    if profile_id:
        normalized_profile = ic_policy.canonical_ic_profile_id(profile_id)
        if normalized_profile not in _PROFILE_SET:
            raise HTTPException(status_code=400, detail="profile_id must be a primary-market IC profile")
    normalized_limit = _normalize_limit(limit)
    payload = _read_quality_payload(package_dir, normalized_deal_id)
    events = payload["events"]
    if normalized_lane:
        events = [event for event in events if str(event.get("lane") or "") == normalized_lane]
    if normalized_profile:
        events = [event for event in events if str(event.get("profile_id") or event.get("agent_id") or "") == normalized_profile]
    return _redact_meeting_payload({
        "schema_version": payload["schema_version"],
        "deal_id": normalized_deal_id,
        "lane": normalized_lane,
        "profile_id": normalized_profile,
        "limit": normalized_limit,
        "total": len(events),
        "events": events[-normalized_limit:],
    })


def _append_meeting_system_event(
    deal_id: str,
    *,
    lane: str,
    event_type: str,
    speaker: str,
    title: str,
    body: str,
    tone: str = "info",
    phase: str | None = None,
    agent_id: str | None = None,
    meta: dict[str, Any] | str | None = None,
) -> dict[str, Any]:
    normalized_deal_id, package_dir = _validated_deal_dir(deal_id)
    normalized_lane = _normalize_lane(lane)
    event = {
        "id": f"evt-{uuid4().hex}",
        "event_type": event_type,
        "speaker": speaker,
        "title": title,
        "body": body,
        "tone": tone,
        "meta": meta or {},
        "phase": phase,
        "agent_id": agent_id,
        "attachments": [],
        "lane": normalized_lane,
        "created_at": deal_store.utc_now_iso(),
    }
    payload = _read_transcript_payload(package_dir, normalized_deal_id)
    payload["events"].append(event)
    _write_transcript_payload(package_dir, payload)
    return _redact_meeting_payload(event)


@router.post("/primary-market/projects/{deal_id}/meeting/attachments", response_model=ChatAttachmentUploadResponse)
async def upload_meeting_attachments(
    deal_id: str,
    req: ChatAttachmentUploadRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_permission("report.create")),
    async_session: AsyncSession = Depends(get_async_session),
) -> ChatAttachmentUploadResponse:
    files = req.files or []
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    if len(files) > chat_router.MAX_CHAT_ATTACHMENT_COUNT:
        raise HTTPException(status_code=400, detail=f"Upload up to {chat_router.MAX_CHAT_ATTACHMENT_COUNT} files per message")

    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id, current_user=current_user, action="write")
    current_user_id = int(current_user.id)
    current_user_role = _role_value(current_user)
    _detach_current_user(async_session, current_user)

    upload_dir = _project_attachment_dir(normalized_deal_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    attachments: list[ChatAttachment] = []
    for item in files:
        raw, content_type, normalized_name, kind = chat_router._decode_chat_attachment(
            item.filename,
            item.content_type,
            item.data_url,
        )
        attachment_id = uuid4().hex
        stored_name = f"{attachment_id}_{normalized_name}"
        target = upload_dir / stored_name
        target.write_bytes(raw)
        metadata = None
        if kind == "document" and content_type == "application/pdf":
            metadata = await chat_router._submit_pdf_attachment_to_mineru(
                target,
                stored_name,
                _project_pdf_parse_dir(normalized_deal_id, attachment_id),
                background_tasks,
                current_user_id=current_user_id,
                current_user_role=current_user_role,
                async_session=async_session,
            )
        attachments.append(
            ChatAttachment(
                id=attachment_id,
                filename=item.filename or normalized_name,
                content_type=content_type,
                size=len(raw),
                path=str(target),
                url=_attachment_download_url(normalized_deal_id, stored_name),
                kind=kind,
                metadata=metadata,
            )
        )
    return ChatAttachmentUploadResponse(attachments=attachments)


@router.get("/primary-market/projects/{deal_id}/meeting/attachments/{stored_name}")
def get_meeting_attachment(
    deal_id: str,
    stored_name: str,
    current_user: User = Depends(require_permission("report.view")),
) -> FileResponse:
    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id, current_user=current_user, action="view")
    safe_name = _safe_attachment_name(stored_name)
    path = _project_attachment_dir(normalized_deal_id) / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Attachment not found")
    return FileResponse(path)


def primary_market_runtime_context(
    *,
    req: PrimaryMarketMeetingChatRequest,
    deal_id: str,
    profile: str,
    deal_summary: dict,
) -> ChatContext:
    company_name = (
        req.company_name
        or (req.context.company_name if req.context else None)
        or str(deal_summary.get("company_name") or "")
        or deal_id
    )
    phase = req.phase or (req.context.phase if req.context else None) or str(deal_summary.get("current_phase") or "")
    lane = req.lane or (req.context.lane if req.context else None) or "main"
    agent_label = req.context.agent.label if req.context and req.context.agent else profile
    metadata = "\n".join(
        [
            "一级市场会议室上下文:",
            f"- deal_id: {deal_id}",
            f"- company_name: {company_name}",
            f"- phase: {phase or '-'}",
            f"- lane: {lane}",
            f"- agent_id: {profile}",
            f"- agent_label: {agent_label}",
            "- 注意: 这是一级市场项目，不是二级市场股票代码上下文；请优先依据 Deal OS evidence、R0-R4 产物和用户提供材料回答。",
        ]
    )
    return ChatContext(
        domain="primary_market",
        deal_id=deal_id,
        profile_id=profile,
        retrieval_query=_meeting_retrieval_query(req.message, req.retrieval_query),
        company_name=company_name,
        phase=phase or None,
        lane=lane,
        page=ChatContextPage(title=metadata),
    )


def _primary_market_memory_scope(profile: str, deal_id: str) -> dict[str, str]:
    return {
        "profile": profile,
        "deal_id": deal_id,
        "visibility": "project_shared",
    }


_IC_PROFILE_LABELS = {
    "siq_ic_master_coordinator": "总协调员",
    "siq_ic_chairman": "投委会主席",
    "siq_ic_strategist": "战略专家",
    "siq_ic_sector_expert": "行业专家",
    "siq_ic_finance_auditor": "财务审计委员",
    "siq_ic_legal_scanner": "法务合规委员",
    "siq_ic_risk_controller": "风险管理委员",
}
_PROFILE_SERVICE_QUESTIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "siq_ic_master_coordinator": (
        ("职责与协作", "请基于你的总协调职责，介绍你能独立提供的服务、不能替代的专家判断，以及你如何编排其他委员。"),
        ("材料盘点", "请核对当前项目材料范围、证据快照和缺口，并给出按优先级排序的补证清单。"),
        ("流程门禁", "请检查当前 R0-R4 所处阶段、可推进动作、阻断项和需要人工确认的门禁。"),
        ("任务拆解", "请把这个投研问题拆成各委员可独立执行的任务、交接条件和验收产物。"),
        ("产物审计", "请检查当前智能体产物是否齐备、是否过期，以及引用身份和审计链是否一致。"),
    ),
    "siq_ic_chairman": (
        ("职责与服务", "请以投委会主席身份介绍你的职责边界、独立服务能力、所需材料和标准输出。"),
        ("投决框架", "请为当前项目建立投决判断框架，列出必须回答的核心问题和否决条件。"),
        ("综合分歧", "请综合现有委员意见，保留关键分歧，并指出进入裁决前仍需补齐的证据。"),
        ("交易条件", "请从投决角度提出估值、交割、治理和风险缓释条件，但明确哪些部分需专业委员核验。"),
        ("结论压力测", "请对当前支持、观察或反对意见做一次反事实压力测试，并说明什么新证据会改变结论。"),
    ),
    "siq_ic_strategist": (
        ("职责与服务", "请以战略委员身份介绍你的职责边界、可独立提供的战略研究服务、所需材料和输出。"),
        ("战略匹配", "请分析当前项目与基金策略、赛道周期、资本流向和退出窗口的匹配度。"),
        ("增长假设", "请拆解项目增长逻辑，列出可证伪的关键假设、领先指标和下行触发点。"),
        ("竞争路径", "请评估公司的战略定位、扩张路径和竞争反应，并区分已核实事实与待验证假设。"),
        ("退出情景", "请设计三种退出情景，说明实现条件、时间窗口和最需要补证的变量。"),
    ),
    "siq_ic_sector_expert": (
        ("职责与服务", "请以行业专家身份介绍你的职责边界、可独立提供的行业研究服务、所需材料和输出。"),
        ("市场空间", "请建立当前项目的 TAM/SAM/SOM 口径，指出数据来源、计算假设和验证方式。"),
        ("竞争格局", "请分析竞争格局、技术路线和产业链位置，并指出项目护城河最需要核验的证据。"),
        ("客户验证", "请设计客户、供应商和行业专家访谈提纲，用于验证需求真实性与产品替代性。"),
        ("行业风险", "请列出可能改变行业判断的政策、技术和供需变量，以及可持续监测指标。"),
    ),
    "siq_ic_finance_auditor": (
        ("职责与服务", "请以财务审计委员身份介绍你的职责边界、可独立提供的财务核验服务、所需材料和输出。"),
        ("收入质量", "请核验收入确认、客户集中、回款和毛利质量，逐项列出需要的底稿与勾稽关系。"),
        ("三表勾稽", "请设计历史三表与预测模型的勾稽检查，并保留期间、币种、单位和公式轨迹。"),
        ("估值敏感性", "请建立估值与回报敏感性框架，说明关键驱动变量和不能由现有证据支持的数字。"),
        ("财务红旗", "请扫描当前材料中的财务红旗、数据断点和管理层口径矛盾，并给出补证清单。"),
    ),
    "siq_ic_legal_scanner": (
        ("职责与服务", "请以法务合规委员身份介绍你的职责边界、可独立提供的法律尽调服务、所需材料和输出。"),
        ("权属资质", "请检查股权权属、核心资质、知识产权和历史沿革，列出证据要求与待核验事项。"),
        ("合同风险", "请扫描重大合同、关联交易、诉讼和数据合规风险，并标明法律依据和项目证据。"),
        ("交割条件", "请把可整改风险转化为先决条件、陈述保证、赔偿或投后承诺，并说明验收标准。"),
        ("法务红旗", "请区分否决性、可缓释和一般性法律风险，列出下一步法律尽调动作。"),
    ),
    "siq_ic_risk_controller": (
        ("职责与服务", "请以风险管理委员身份介绍你的职责边界、可独立提供的压力测试服务、所需材料和输出。"),
        ("反方论证", "请针对当前投资逻辑建立最强反方论证，列出反证、传导路径和可能的相关性风险。"),
        ("压力情景", "请设计基准、下行和极端情景，给出需要监测的指标、阈值和止损触发条件。"),
        ("否决扫描", "请扫描当前项目的重大与关键风险，区分否决项、条件通过项和可投后监控项。"),
        ("假设关联", "请检查各委员结论是否依赖同一组未经验证的假设，并提出最小补证方案。"),
    ),
}
_SUGGESTIONS_TIMEOUT_SECONDS = 18


def _profile_label(profile: str) -> str:
    return _IC_PROFILE_LABELS.get(profile, profile)


def _ic_profile_role_contract(profile: str) -> str:
    try:
        return ic_profile_contract.render_meeting_role_guard(profile)
    except (FileNotFoundError, ValueError):
        return ""


def _receipt_context_for(profile: str, deal_id: str) -> dict[str, Any]:
    try:
        payload = ic_startup_retrieval.read_startup_retrieval_receipt(deal_id, profile)
    except (FileNotFoundError, ValueError):
        return {"required": True, "present": False}
    receipt = payload.get("receipt") if isinstance(payload.get("receipt"), dict) else None
    gate = receipt.get("gate") if isinstance(receipt, dict) and isinstance(receipt.get("gate"), dict) else {}
    readiness_status = str(receipt.get("readiness_status") or "unknown") if isinstance(receipt, dict) else "missing"
    retrieval_status = str(receipt.get("retrieval_status") or "unknown") if isinstance(receipt, dict) else "missing"
    ready = bool(
        isinstance(receipt, dict)
        and readiness_status == "current"
        and retrieval_status == "ready"
        and gate.get("allowed_to_speak") is True
    )
    return {
        "required": True,
        "present": receipt is not None,
        "ready": ready,
        "receipt_id": receipt.get("receipt_id") if isinstance(receipt, dict) else None,
        "shared_hits": receipt.get("shared_hits") if isinstance(receipt, dict) else None,
        "shared_ready": receipt.get("shared_ready") if isinstance(receipt, dict) else None,
        "shared_vector_hit_count": receipt.get("shared_vector_hit_count") if isinstance(receipt, dict) else None,
        "private_hits": receipt.get("private_hits") if isinstance(receipt, dict) else None,
        "private_ready": receipt.get("private_ready") if isinstance(receipt, dict) else None,
        "rerank_ready": receipt.get("rerank_ready") if isinstance(receipt, dict) else None,
        "rerank_status": (
            (receipt.get("rerank") or {}).get("status")
            if isinstance(receipt, dict) and isinstance(receipt.get("rerank"), dict)
            else None
        ),
        "readiness_status": readiness_status,
        "retrieval_status": retrieval_status,
        "allowed_to_speak": gate.get("allowed_to_speak"),
        "blocking_reasons": gate.get("blocking_reasons") if isinstance(gate.get("blocking_reasons"), list) else [],
        "gaps": receipt.get("gaps") if isinstance(receipt, dict) else [],
    }


def _r1_report_context_for(profile: str, deal_id: str) -> dict[str, Any]:
    if profile not in ic_policy.R1_AGENT_SEQUENCE:
        return {"required": False, "present": False, "skipped": True}
    try:
        reports = deal_reports.list_r1_agent_reports(deal_id)
    except (FileNotFoundError, ValueError):
        return {"required": True, "present": False}
    agents = reports.get("agents") if isinstance(reports.get("agents"), list) else []
    report = next(
        (item for item in agents if isinstance(item, dict) and item.get("agent_id") == profile),
        {},
    )
    return {
        "required": True,
        "present": bool(report.get("has_report")),
        "status": report.get("status"),
        "score": report.get("score"),
        "recommendation": report.get("recommendation"),
        "artifact_path": report.get("artifact_path"),
    }


def _meeting_retrieval_query(message: str, explicit_query: str | None = None) -> str:
    explicit = " ".join(str(explicit_query or "").split()).strip()
    if explicit:
        return explicit[:600]
    raw = str(message or "").strip()
    match = re.search(r"(?:人类主持人问题|主持人原始问题)[：:]\s*(.+)\s*$", raw, flags=re.DOTALL)
    if match:
        raw = match.group(1).strip()
    return " ".join(raw.split())[:600]


def _meeting_model_control_message(req: PrimaryMarketMeetingChatRequest) -> str:
    """Return only the human's raw query, excluding UI prompt/context expansion."""

    return _meeting_retrieval_query(req.message, req.retrieval_query)


def _live_meeting_retrieval_context(profile: str, deal_id: str, query: str) -> str:
    try:
        retrieval = deal_retrieval.retrieve_for_agent(
            deal_id,
            profile,
            query=query,
            limit=8,
            include_vector=True,
            include_rerank=True,
        )
    except Exception as exc:
        return "\n".join(
            [
                "本轮一级市场实时检索:",
                f"- status: unavailable; reason={type(exc).__name__}: {str(exc)[:180]}",
                "- 降级规则: 只能提供职责范围内的方法论咨询；不得声称项目材料或知识库支持了具体事实。",
            ]
        )

    vector = retrieval.get("vector_retrieval") if isinstance(retrieval.get("vector_retrieval"), dict) else {}
    rerank = retrieval.get("rerank") if isinstance(retrieval.get("rerank"), dict) else {}
    observability = (
        retrieval.get("retrieval_observability")
        if isinstance(retrieval.get("retrieval_observability"), dict)
        else {}
    )
    candidate_counts = (
        observability.get("collection_candidate_counts")
        if isinstance(observability.get("collection_candidate_counts"), dict)
        else {}
    )
    project_hits = [
        item
        for item in [
            *(retrieval.get("evidence_hits") or []),
            *(retrieval.get("shared_vector_hits") or []),
        ]
        if isinstance(item, dict)
    ]
    private_hits = [
        item for item in retrieval.get("background_knowledge_hits") or [] if isinstance(item, dict)
    ]
    lines = [
        "本轮一级市场实时检索:",
        f"- query: {query or '-'}",
        f"- namespace: primary_market; project_tag={deal_id}",
        "- shared_collection: siq_deal_shared -> ic_collaboration_shared (仅允许当前 project_tag)",
        f"- private_collection: {profile} -> {(vector.get('physical_collections') or {}).get(profile, profile)}",
        f"- status: {vector.get('status') or 'unknown'}; milvus_used={str(bool(retrieval.get('milvus_used'))).lower()}; project_hits={len(project_hits)}; private_hits={len(private_hits)}",
        (
            f"- retrieval_strategy: {(vector.get('retrieval_strategy') or {}).get('mode') or '-'}; "
            f"candidates={sum(int(value or 0) for value in candidate_counts.values())}; "
            f"rerank_status={rerank.get('status') or 'unknown'}; rerank_candidates={rerank.get('candidate_count', 0)}; "
            f"rerank_results={rerank.get('result_count', 0)}; degraded_reason={rerank.get('reason') or '-'}"
        ),
    ]
    for index, item in enumerate(project_hits[:6], start=1):
        evidence_id = item.get("evidence_id") or item.get("source_id") or f"PROJECT-{index}"
        citation = item.get("citation") or (item.get("metadata") or {}).get("citation") or item.get("document_id") or "-"
        quote = str(item.get("quote_preview") or item.get("text") or "").replace("\n", " ")[:420]
        lines.append(f"- [PROJECT:{evidence_id}] citation={citation}; quote={quote or '-'}")
    for index, item in enumerate(private_hits[:4], start=1):
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        ref_id = item.get("ref_id") or item.get("source_id") or f"KBREF-{index}"
        title = item.get("title") or metadata.get("title") or profile
        usage = item.get("knowledge_lane") or metadata.get("knowledge_type") or "background"
        quote = str(item.get("quote_preview") or item.get("text") or "").replace("\n", " ")[:420]
        lines.append(f"- [KBREF:{ref_id}] title={title}; usage={usage}; quote={quote or '-'}")
    if not project_hits:
        lines.append("- 项目证据命中为空：不得把私库方法论或行业背景写成该项目已核实事实。")
    if not private_hits:
        lines.append("- 角色私库命中为空：明确说明未获得私有知识库支持，仍可按角色职责提供基础咨询。")
    lines.append("- 严禁读取或引用 data/wiki/companies 及任何二级市场财报命名空间。")
    return "\n".join(lines)


def _meeting_evidence_context(profile: str, deal_id: str | None, retrieval_query: str = "") -> str:
    if not deal_id:
        return ""
    try:
        normalized_deal_id = deal_store.validate_deal_id(deal_id)
    except ValueError:
        return ""
    receipt = _receipt_context_for(profile, normalized_deal_id)
    report = _r1_report_context_for(profile, normalized_deal_id)
    project_evidence_available = bool(
        int(receipt.get("shared_vector_hit_count") or 0)
        or int(receipt.get("shared_hits") or 0)
    )
    lines = [
        "一级市场项目证据上下文:",
        f"- deal_id: {normalized_deal_id}",
        (
            "- startup_retrieval_receipt: not_required"
            if not receipt.get("required")
            else (
                f"- startup_retrieval_receipt: present; ready={str(bool(receipt.get('ready'))).lower()}; receipt_id={receipt.get('receipt_id') or '-'}; "
                f"shared_hits={receipt.get('shared_hits') if receipt.get('shared_hits') is not None else '-'}; "
                f"shared_ready={receipt.get('shared_ready') if receipt.get('shared_ready') is not None else '-'}; "
                f"shared_vector_hits={receipt.get('shared_vector_hit_count') if receipt.get('shared_vector_hit_count') is not None else '-'}; "
                f"private_hits={receipt.get('private_hits') if receipt.get('private_hits') is not None else '-'}; "
                f"private_ready={receipt.get('private_ready') if receipt.get('private_ready') is not None else '-'}; "
                f"rerank_ready={receipt.get('rerank_ready') if receipt.get('rerank_ready') is not None else '-'}; "
                f"rerank_status={receipt.get('rerank_status') or '-'}; "
                f"readiness_status={receipt.get('readiness_status') or '-'}; retrieval_status={receipt.get('retrieval_status') or '-'}; "
                f"blocking_reasons={', '.join(str(item) for item in receipt.get('blocking_reasons', [])[:5]) if isinstance(receipt.get('blocking_reasons'), list) else '-'}; "
                f"gaps={', '.join(str(item) for item in receipt.get('gaps', [])[:5]) if isinstance(receipt.get('gaps'), list) else '-'}"
                if receipt.get("present")
                else "- startup_retrieval_receipt: missing; 普通聊天可继续，但本轮只能作为临时咨询，正式任务请先运行准备智能体。"
            )
        ),
        (
            "- r1_report: not_required"
            if not report.get("required")
            else (
                (
                    f"- r1_report: present; status={report.get('status') or '-'}; score={report.get('score') if report.get('score') is not None else '-'}; "
                    f"recommendation={report.get('recommendation') or '-'}; artifact_path={report.get('artifact_path') or '-'}"
                    if project_evidence_available
                    else (
                        f"- r1_report: historical_process_artifact_only; artifact_path={report.get('artifact_path') or '-'}; "
                        "当前项目 Evidence 为空，不得复述其分数、风险评级或 recommendation 作为当前结论。"
                    )
                )
                if report.get("present")
                else "- r1_report: missing; 当前聊天输出不会自动成为正式 R1 产物。"
            )
        ),
        "- 证据分类: PROJECT/EVIDENCE 命中可支持项目事实；KBREF/角色私库只能支持方法论或法律框架；历史 report 只表示流程中曾有该产物。",
        "- 回答要求: 使用项目证据时标注 evidence/source；Evidence 为空时明确说尚无项目底稿，只给核验框架与材料清单，不得生成项目专属评分、风险等级或已核实结论。",
    ]
    if retrieval_query:
        lines.extend(["", _live_meeting_retrieval_context(profile, normalized_deal_id, retrieval_query)])
    return "\n".join(lines)


def _quality_event_body(quality: dict[str, Any]) -> str:
    checks = quality.get("checks") if isinstance(quality.get("checks"), list) else []
    compact = [
        f"{item.get('id')}={item.get('status')}"
        for item in checks
        if isinstance(item, dict) and item.get("id") in {
            "evidence.reference",
            "verified_assumed",
            "next_action",
            "role.boundary",
        }
    ]
    return f"status={quality.get('status')}; " + "; ".join(compact)


def _append_meeting_quality_event(
    deal_id: str,
    *,
    lane: str,
    profile: str,
    quality: dict[str, Any],
    transcript_event_id: str | None = None,
) -> dict[str, Any]:
    normalized_deal_id, package_dir = _validated_deal_dir(deal_id)
    event = {
        "event_id": f"quality-{uuid4().hex}",
        "transcript_event_id": transcript_event_id,
        "profile_id": profile,
        "lane": _normalize_lane(lane),
        "quality": quality,
        "created_at": deal_store.utc_now_iso(),
    }
    payload = _read_quality_payload(package_dir, normalized_deal_id)
    payload["events"].append(event)
    _write_quality_payload(package_dir, payload)
    return _redact_meeting_payload(event)


def _compact_event_paths(paths: list[Any]) -> str:
    values = [str(item).strip() for item in paths if str(item or "").strip()]
    if not values:
        return "-"
    return ", ".join(values[:6])


def _r1_agent_task_event_payload(
    *,
    result: dict[str, Any],
    profile_id: str,
    dry_run: bool,
) -> tuple[str, str, str, dict[str, Any]]:
    report = result.get("report") if isinstance(result.get("report"), dict) else {}
    artifact_path = (
        result.get("markdown_path")
        or report.get("artifact_path")
        or report.get("markdown_path")
        or result.get("report_path")
    )
    score = report.get("score")
    recommendation = report.get("recommendation")
    title = f"R1 {_profile_label(profile_id)}报告已生成"
    body = (
        f"artifact_path: {artifact_path or '-'}; "
        f"score: {score if score is not None else '-'}; "
        f"recommendation: {recommendation or '-'}"
    )
    meta = {
        "source": "workflow.run_r1_agent",
        "dry_run": dry_run,
        "workflow_action": result.get("workflow_action"),
        "schema_version": result.get("schema_version"),
        "agent_id": profile_id,
        "artifact_path": artifact_path,
        "json_path": result.get("json_path"),
        "score": score,
        "recommendation": recommendation,
        "hermes_run_id": result.get("hermes_run_id"),
    }
    return "artifact_written", title, body, meta


def _r1_serial_task_event_payload(
    *,
    result: dict[str, Any],
    dry_run: bool,
) -> tuple[str, str, str, dict[str, Any]]:
    agent_runs = result.get("agent_runs") if isinstance(result.get("agent_runs"), list) else []
    paths = []
    for run in agent_runs:
        if not isinstance(run, dict):
            continue
        report = run.get("report") if isinstance(run.get("report"), dict) else {}
        paths.append(run.get("markdown_path") or report.get("artifact_path") or report.get("markdown_path"))
    executed_agent_ids = [str(item) for item in result.get("executed_agent_ids") or []]
    report_written = bool(result.get("report_written"))
    event_type = "artifact_written" if report_written else "audit_event"
    title = "R1 串行执行完成" if report_written else "R1 串行执行未写入新报告"
    body = (
        f"executed_count: {result.get('executed_count', len(executed_agent_ids))}; "
        f"executed_agent_ids: {', '.join(executed_agent_ids) or '-'}; "
        f"artifact_paths: {_compact_event_paths(paths)}"
    )
    meta = {
        "source": "workflow.run_r1_serial",
        "dry_run": dry_run,
        "workflow_action": result.get("workflow_action"),
        "schema_version": result.get("schema_version"),
        "planned_agent_ids": result.get("planned_agent_ids") or [],
        "executed_agent_ids": executed_agent_ids,
        "artifact_paths": [str(item) for item in paths if str(item or "").strip()],
        "status": result.get("status"),
    }
    return event_type, title, body, meta


def _evaluate_and_store_reply_quality(
    *,
    deal_id: str,
    lane: str,
    profile: str,
    message: str,
    reply: str,
) -> dict[str, Any] | None:
    try:
        receipt = _receipt_context_for(profile, deal_id)
        report = _r1_report_context_for(profile, deal_id)
        quality = ic_agent_output_quality.evaluate_ic_agent_reply(
            profile,
            message,
            reply,
            context={
                "deal_id": deal_id,
                "lane": lane,
                "startup_receipt": receipt,
                "r1_report": report,
            },
        )
    except Exception:
        return None
    try:
        status = str(quality.get("status") or "warn")
        transcript_event = _append_meeting_system_event(
            deal_id,
            lane=lane,
            event_type="quality_check",
            speaker=_profile_label(profile),
            title="回答质量检查",
            body=_quality_event_body(quality),
            tone="success" if status == "pass" else "warning" if status == "warn" else "error",
            agent_id=profile,
            meta={"source": "ic_agent_output_quality", "quality": quality},
        )
        _append_meeting_quality_event(
            deal_id,
            lane=lane,
            profile=profile,
            quality=quality,
            transcript_event_id=str(transcript_event.get("id") or ""),
        )
    except Exception:
        pass
    return quality


def _profile_scoped_meeting_message(
    profile: str,
    message: str,
    deal_id: str | None = None,
    *,
    retrieval_query: str | None = None,
) -> str:
    raw = str(message or "").strip()
    if "一级市场 IC profile 职责护栏:" in raw:
        return raw
    contract = _ic_profile_role_contract(profile)
    query = _meeting_retrieval_query(raw, retrieval_query)
    evidence_context = _meeting_evidence_context(profile, deal_id, query)
    parts = [
        part
        for part in (
            contract,
            evidence_context,
            primary_market_agent_runtime.PRIMARY_MARKET_RESPONSE_FORMAT_CONTRACT,
        )
        if part
    ]
    if not parts:
        return raw
    return "\n\n".join([*parts, "主持人原始问题:", raw])


def _compact_json(value: Any, *, max_chars: int = 7200) -> str:
    text = json.dumps(deal_store.redact_public_payload(value), ensure_ascii=False, indent=2, default=str)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n...<truncated>"


def _safe_context(label: str, loader) -> dict[str, Any]:
    try:
        value = loader()
    except Exception as exc:
        return {"status": "unavailable", "error": f"{label}: {exc}"}
    return value if isinstance(value, dict) else {"value": value}


def _recent_transcript_for_suggestions(package_dir: Path, deal_id: str, lane: str, limit: int = 8) -> list[dict[str, Any]]:
    payload = _read_transcript_payload(package_dir, deal_id)
    events = [event for event in payload.get("events", []) if _event_lane(event) == lane and str(event.get("body") or "").strip()]
    return [
        {
            "phase": event.get("phase"),
            "speaker": event.get("speaker"),
            "title": event.get("title"),
            "body": str(event.get("body") or "").replace("\n", " ")[:420],
            "agent_id": event.get("agent_id"),
        }
        for event in events[-limit:]
    ]


def _suggestion_context(deal_id: str, lane: str, mode: str, profile: str) -> dict[str, Any]:
    normalized_deal_id, package_dir = _validated_deal_dir(deal_id)
    detail = deal_store.read_deal_detail(normalized_deal_id)
    return {
        "deal": {
            "summary": detail.get("summary") or {},
            "project_meta": detail.get("project_meta") or {},
            "workflow": detail.get("workflow") or {},
        },
        "meeting": {
            "lane": lane,
            "mode": mode,
            "profile": profile,
            "profile_label": _profile_label(profile),
            "recent_transcript": _recent_transcript_for_suggestions(package_dir, normalized_deal_id, lane),
        },
        "status": _safe_context("deal_status", lambda: deal_status.summarize_deal_status(normalized_deal_id)),
        "phase_artifacts": _safe_context("phase_artifacts", lambda: deal_phase_artifacts.summarize_deal_phase_artifacts(normalized_deal_id)),
        "evidence": _safe_context("evidence", lambda: deal_evidence.read_deal_evidence_package(normalized_deal_id, preview_limit=5)),
    }


def _suggestions_prompt(deal_id: str, lane: str, mode: str, profile: str) -> str:
    context = _suggestion_context(deal_id, lane, mode, profile)
    role_contract = ic_profile_contract.get_ic_profile_contract(profile)
    return "\n".join([
        "你正在为 SIQ 一级市场投委会会议室生成空状态建议问题。",
        f"当前智能体: {_profile_label(profile)} ({profile})",
        f"会议模式: {mode}; lane: {lane}",
        "",
        _ic_profile_role_contract(profile),
        "",
        "请根据下方真实项目上下文，生成当前最适合用户点击的建议问题。",
        "要求:",
        "1. 只输出一个 JSON 对象，不要 Markdown，不要代码块，不要解释。",
        "2. JSON 格式必须为: {\"intro\":\"...\", \"questions\":[{\"label\":\"...\", \"prompt\":\"...\"}]}。",
        "3. intro 为当前智能体第一人称简介，40-72 个中文字符，必须结合当前项目阶段或上下文。",
        "4. questions 必须正好 5 个；label 为 2-8 个中文字符；prompt 是点击后发给该智能体的完整中文问题。",
        "5. 问题必须贴合当前项目的阶段、证据状态、材料缺口、分歧或投决动作；不要输出泛泛的模板问题。",
        "6. 不要虚构数据、文件路径、证据 id 或结论；信息不足时，问题应引导补证、核验或确认。",
        "7. intro 和每个问题必须体现该 profile 的职责、边界或独立服务能力，不能写成通用聊天助手。",
        "8. 这是一级市场命名空间，禁止读取或暗示使用二级市场公司 Wiki、财报问答或股票研究资料。",
        "",
        "角色契约 JSON:",
        _compact_json(role_contract, max_chars=5200),
        "",
        "项目上下文 JSON:",
        _compact_json(context),
    ])


def _profile_contract_suggestions(
    profile: str,
    mode: str,
) -> tuple[str, list[PrimaryMarketMeetingSuggestedQuestion]]:
    contract = ic_profile_contract.get_ic_profile_contract(profile)
    role_name = str(contract.get("role_name") or contract.get("label") or _profile_label(profile))
    focus = str(contract.get("mission") or contract.get("core_focus") or "").strip()
    if mode == "committee":
        intro = "这里是一级市场投委会协同窗口。各委员会保留各自职责边界，基于同一 Deal 证据链独立发言并暴露分歧。"
    elif mode == "workflow":
        intro = "我是一级市场投委会总协调员，负责核验证据快照、编排 R0-R4 交接和门禁，不替代专业委员作出判断。"
    else:
        intro = f"我是{role_name}，{focus or '只在本角色职责内提供独立研究、核验与建议'}。我会区分项目证据、私库方法论和待核验假设。"
    definitions = _PROFILE_SERVICE_QUESTIONS.get(profile) or _PROFILE_SERVICE_QUESTIONS["siq_ic_master_coordinator"]
    questions = [
        PrimaryMarketMeetingSuggestedQuestion(key=f"role-{index}", label=label, prompt=prompt)
        for index, (label, prompt) in enumerate(definitions, start=1)
    ]
    return intro[:180], questions


def _extract_suggestions_json(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("suggestions model output must be a JSON object")
    return payload


def _normalize_suggested_questions(payload: dict[str, Any]) -> tuple[str, list[PrimaryMarketMeetingSuggestedQuestion]]:
    intro = str(payload.get("intro") or "").strip()
    raw_questions = payload.get("questions")
    if not isinstance(raw_questions, list):
        raw_questions = []
    questions: list[PrimaryMarketMeetingSuggestedQuestion] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_questions, start=1):
        if not isinstance(item, dict):
            continue
        prompt = str(item.get("prompt") or item.get("question") or "").strip()
        label = str(item.get("label") or item.get("title") or "").strip()
        if not prompt:
            continue
        if not label:
            label = prompt[:8]
        label = re.sub(r"\s+", "", label)[:10] or f"问题{index}"
        key_base = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "-", label).strip("-") or f"q{index}"
        key = key_base
        suffix = 2
        while key in seen:
            key = f"{key_base}-{suffix}"
            suffix += 1
        seen.add(key)
        questions.append(PrimaryMarketMeetingSuggestedQuestion(key=key, label=label, prompt=prompt))
        if len(questions) >= 5:
            break
    return intro, questions


async def _generate_meeting_suggestions(deal_id: str, lane: str, mode: str, profile: str) -> tuple[str, list[PrimaryMarketMeetingSuggestedQuestion]]:
    prompt = _suggestions_prompt(deal_id, lane, mode, profile)

    async def _call_model() -> str:
        run_id = await create_run(prompt, [], profile=cast(HermesProfile, profile))
        return await collect_run_result(run_id, profile=cast(HermesProfile, profile), timeout=_SUGGESTIONS_TIMEOUT_SECONDS)

    raw = await asyncio.wait_for(_call_model(), timeout=_SUGGESTIONS_TIMEOUT_SECONDS + 5)
    payload = _extract_suggestions_json(raw)
    return _normalize_suggested_questions(payload)


def primary_market_session_id(*, user_id: int | str, profile: str, deal_id: str, lane: str = "main") -> str:
    safe_deal_id = _SESSION_SAFE.sub("-", deal_id.strip()).strip("-._") or "deal"
    safe_deal_id = safe_deal_id[:80]
    deal_hash = hashlib.sha1(deal_id.strip().encode("utf-8")).hexdigest()[:8]
    safe_lane = _SESSION_SAFE.sub("-", lane.strip()).strip("-._") or "main"
    return f"user-{user_id}-{profile}-primary-market-{safe_deal_id}-{deal_hash}-{safe_lane}"


def primary_market_session_profile(*, deal_id: str, lane: str = "main") -> str:
    safe_deal_id = _SESSION_SAFE.sub("-", deal_id.strip()).strip("-._") or "deal"
    safe_deal_id = safe_deal_id[:56]
    deal_hash = hashlib.sha1(deal_id.strip().encode("utf-8")).hexdigest()[:8]
    safe_lane = _SESSION_SAFE.sub("-", _normalize_lane(lane)).strip("-._") or "main"
    return f"primary-market-{safe_deal_id}-{deal_hash}-{safe_lane}"


def _assert_profile_allowed_for_lane(profile: str, lane: str) -> None:
    if lane.startswith("agent-") and lane != f"agent-{profile}":
        raise HTTPException(status_code=400, detail="single-agent lane does not match requested IC profile")
    if lane == "workflow-main" and profile != "siq_ic_master_coordinator":
        raise HTTPException(status_code=400, detail="workflow lane must use siq_ic_master_coordinator")


def _meeting_session_scope(profile: str, deal_id: str, lane: str) -> str:
    normalized_profile = canonical_meeting_profile(profile)
    normalized_lane = _normalize_lane(lane)
    _assert_profile_allowed_for_lane(normalized_profile, normalized_lane)
    return primary_market_session_profile(deal_id=deal_id, lane=normalized_lane)


async def resolve_or_create_meeting_session(
    async_session: AsyncSession,
    current_user: User,
    *,
    profile: str,
    deal_id: str,
    lane: str = "main",
    session_id: str | None = None,
    user_id: str | None = None,
    user_role: object | None = None,
) -> str:
    user_id = user_id or str(current_user.id)
    user_role = current_user.role if user_role is None else user_role
    session_profile = _meeting_session_scope(profile, deal_id, lane)
    session_mgr = get_session_manager()
    if session_id:
        chat_router._assert_session_belongs_to_user(session_id, user_id, session_profile)
        try:
            return session_mgr.set_current_session(user_id, session_profile, session_id)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            summary = await chat_router._db_session_summary(async_session, session_id)
            if summary:
                return session_mgr.restore_session(
                    user_id,
                    session_profile,
                    session_id,
                    user_role=user_role,
                    created_at=summary.get("created_at"),
                    updated_at=summary.get("updated_at"),
                    message_count=int(summary.get("message_count") or 0),
                )
            return session_mgr.create_session(user_id, session_profile, user_role=user_role)

    current_session_id = session_mgr.get_current_session_id(user_id, session_profile)
    if current_session_id:
        return current_session_id

    db_sessions = await chat_router._db_session_summaries(
        async_session,
        user_id=user_id,
        profile=session_profile,
        limit=1,
    )
    if db_sessions:
        latest = db_sessions[0]
        restored_session_id = str(latest.get("session_id") or "")
        if restored_session_id:
            return session_mgr.restore_session(
                user_id,
                session_profile,
                restored_session_id,
                user_role=user_role,
                created_at=latest.get("created_at"),
                updated_at=latest.get("updated_at"),
                message_count=int(latest.get("message_count") or 0),
            )

    return session_mgr.create_session(user_id, session_profile, user_role=user_role)


def ensure_meeting_session(
    *,
    user_id: int,
    user_role: str,
    profile: str,
    deal_id: str,
    lane: str = "main",
) -> str:
    session_id = primary_market_session_id(user_id=user_id, profile=profile, deal_id=deal_id, lane=lane)
    session_manager = get_session_manager()
    try:
        return session_manager.set_current_session(str(user_id), profile, session_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
    return session_manager.restore_session(
        str(user_id),
        profile,
        session_id,
        user_role=user_role,
        message_count=0,
    )


@router.get("/primary-market/meeting/{profile}/chat/history")
async def chat_history(
    profile: str,
    deal_id: str,
    lane: str = "main",
    session_id: str | None = None,
    limit: int = HISTORY_LIMIT,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> dict:
    normalized_profile = canonical_meeting_profile(profile)
    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id, current_user=current_user, action="view")
    normalized_lane = _normalize_lane(lane)
    _assert_profile_allowed_for_lane(normalized_profile, normalized_lane)
    resolved_session_id = await resolve_or_create_meeting_session(
        async_session,
        current_user,
        profile=normalized_profile,
        deal_id=normalized_deal_id,
        lane=normalized_lane,
        session_id=session_id,
    )
    messages = await chat_history_response(async_session, resolved_session_id, limit=limit)
    return {
        "messages": messages,
        "session_id": resolved_session_id,
        "deal_id": normalized_deal_id,
        "lane": normalized_lane,
    }


@router.get("/primary-market/meeting/{profile}/chat/sessions")
async def chat_sessions(
    profile: str,
    deal_id: str,
    lane: str = "main",
    limit: int = 100,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> dict:
    normalized_profile = canonical_meeting_profile(profile)
    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id, current_user=current_user, action="view")
    normalized_lane = _normalize_lane(lane)
    _assert_profile_allowed_for_lane(normalized_profile, normalized_lane)
    user_id = str(current_user.id)
    requested_limit = chat_router._session_limit(current_user, limit)
    session_profile = primary_market_session_profile(deal_id=normalized_deal_id, lane=normalized_lane)
    _detach_current_user(async_session, current_user)
    session_mgr = get_session_manager()
    redis_sessions = session_mgr.list_user_sessions(user_id, session_profile, limit=requested_limit)
    db_sessions = await chat_router._db_session_summaries(
        async_session,
        user_id=user_id,
        profile=session_profile,
        limit=requested_limit,
    )
    sessions = chat_router._merge_sessions(redis_sessions, db_sessions, requested_limit)
    current_session_id = session_mgr.get_current_session_id(user_id, session_profile)
    message_summaries = await chat_router._session_message_summaries(
        async_session,
        [str(item.get("session_id") or "") for item in sessions],
    )
    payload = chat_router._sessions_payload(sessions, current_session_id, message_summaries)
    payload.update({"deal_id": normalized_deal_id, "lane": normalized_lane})
    return payload


@router.get("/primary-market/meeting/{profile}/suggestions", response_model=PrimaryMarketMeetingSuggestionsResponse)
async def meeting_suggestions(
    profile: str,
    deal_id: str,
    lane: str = "main",
    mode: str = "single",
    current_user: User = Depends(require_permission("report.view")),
) -> PrimaryMarketMeetingSuggestionsResponse:
    normalized_profile = canonical_meeting_profile(profile)
    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id, current_user=current_user, action="view")
    normalized_lane = _normalize_lane(lane)
    _assert_profile_allowed_for_lane(normalized_profile, normalized_lane)
    normalized_mode = str(mode or "single").strip() or "single"
    try:
        intro, questions = await _generate_meeting_suggestions(
            normalized_deal_id,
            normalized_lane,
            normalized_mode,
            normalized_profile,
        )
        fallback_intro, fallback_questions = _profile_contract_suggestions(normalized_profile, normalized_mode)
        intro = intro or fallback_intro
        if len(questions) < 5:
            existing = {item.label for item in questions}
            questions.extend(item for item in fallback_questions if item.label not in existing)
            questions = questions[:5]
        source = "model+profile_contract"
        error = None
    except asyncio.TimeoutError:
        intro, questions = _profile_contract_suggestions(normalized_profile, normalized_mode)
        source = "profile_contract_fallback"
        error = "智能体建议生成超时，请确认对应 IC Hermes 网关已启动后重试。"
    except Exception as exc:
        intro, questions = _profile_contract_suggestions(normalized_profile, normalized_mode)
        source = "profile_contract_fallback"
        error = str(exc) or exc.__class__.__name__
    return PrimaryMarketMeetingSuggestionsResponse(
        deal_id=normalized_deal_id,
        lane=normalized_lane,
        profile=normalized_profile,
        intro=intro,
        questions=questions,
        source=source,
        error=error,
    )


@router.get("/primary-market/meeting/{deal_id}/agents/readiness")
def meeting_agents_readiness(
    deal_id: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict[str, Any]:
    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id, current_user=current_user, action="view")
    try:
        return deal_store.redact_public_payload(
            primary_market_meeting_readiness.build_meeting_readiness(normalized_deal_id)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(normalized_deal_id) from exc


@router.post("/primary-market/meeting/{deal_id}/agents/{profile_id}/prepare")
def prepare_meeting_agent(
    deal_id: str,
    profile_id: str,
    req: PrimaryMarketMeetingPrepareRequest | None = None,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    request_payload = req or PrimaryMarketMeetingPrepareRequest()
    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id, current_user=current_user, action="write")
    canonical_profile = ic_policy.canonical_ic_profile_id(profile_id)
    try:
        receipt = ic_startup_retrieval.generate_startup_retrieval_receipt(
            normalized_deal_id,
            canonical_profile,
            round_name=request_payload.round_name,
            query=request_payload.query,
            limit=request_payload.limit,
            include_external=request_payload.include_external,
            external_providers=request_payload.external_providers,
            include_vector=request_payload.include_vector,
            include_rerank=request_payload.include_rerank,
            vector_collections=request_payload.vector_collections,
            created_by=_user_payload(current_user),
        )
        event = _append_meeting_system_event(
            normalized_deal_id,
            lane=f"agent-{canonical_profile}",
            event_type="receipt_generated",
            speaker=_profile_label(canonical_profile),
            title="Startup Retrieval 已生成",
            body=(
                f"receipt_id: {receipt.get('receipt_id')}; "
                f"shared_hits: {receipt.get('shared_hits')}; "
                f"private_hits: {receipt.get('private_hits')}; "
                f"gaps: {', '.join(str(item) for item in receipt.get('gaps', [])[:5]) if isinstance(receipt.get('gaps'), list) else '-'}"
            ),
            tone="success",
            phase=str(request_payload.round_name or "R1").upper(),
            agent_id=canonical_profile,
            meta={"source": "startup_retrieval", "receipt_id": receipt.get("receipt_id")},
        )
        return deal_store.redact_public_payload({
            "deal_id": normalized_deal_id,
            "agent_id": canonical_profile,
            "receipt": receipt,
            "event": event,
            "readiness": primary_market_meeting_readiness.build_meeting_readiness(normalized_deal_id),
        })
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(normalized_deal_id) from exc


@router.post("/primary-market/meeting/{deal_id}/agents/prepare-all")
def prepare_all_meeting_agents(
    deal_id: str,
    req: PrimaryMarketMeetingPrepareAllRequest | None = None,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    request_payload = req or PrimaryMarketMeetingPrepareAllRequest()
    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id, current_user=current_user, action="write")
    raw_profile_ids = request_payload.profile_ids or list(ic_policy.R1_AGENT_SEQUENCE)
    profile_ids = [
        ic_policy.canonical_ic_profile_id(str(profile_id))
        for profile_id in raw_profile_ids
        if str(profile_id or "").strip()
    ]
    results: list[dict[str, Any]] = []
    for profile_id in profile_ids:
        try:
            receipt = ic_startup_retrieval.generate_startup_retrieval_receipt(
                normalized_deal_id,
                profile_id,
                round_name=request_payload.round_name,
                query=request_payload.query,
                limit=request_payload.limit,
                include_external=request_payload.include_external,
                external_providers=request_payload.external_providers,
                include_vector=request_payload.include_vector,
                include_rerank=request_payload.include_rerank,
                vector_collections=request_payload.vector_collections,
                created_by=_user_payload(current_user),
            )
            results.append({
                "agent_id": profile_id,
                "status": "completed",
                "receipt_id": receipt.get("receipt_id"),
                "shared_hits": receipt.get("shared_hits"),
                "private_hits": receipt.get("private_hits"),
                "gaps": receipt.get("gaps") or [],
            })
        except Exception as exc:  # keep preparing the rest of the committee
            results.append({
                "agent_id": profile_id,
                "status": "failed",
                "error": str(exc) or exc.__class__.__name__,
            })
    completed = [item for item in results if item.get("status") == "completed"]
    failed = [item for item in results if item.get("status") == "failed"]
    event = _append_meeting_system_event(
        normalized_deal_id,
        lane="committee-main",
        event_type="receipt_generated",
        speaker=_profile_label("siq_ic_master_coordinator"),
        title="全体委员 Startup Retrieval 准备完成",
        body=f"completed={len(completed)}; failed={len(failed)}; agents={', '.join(item.get('agent_id', '-') for item in results)}",
        tone="warning" if failed else "success",
        phase=str(request_payload.round_name or "R1").upper(),
        agent_id="siq_ic_master_coordinator",
        meta={"source": "startup_retrieval_prepare_all", "results": results},
    )
    return deal_store.redact_public_payload({
        "deal_id": normalized_deal_id,
        "results": results,
        "event": event,
        "readiness": primary_market_meeting_readiness.build_meeting_readiness(normalized_deal_id),
    })


@router.post("/primary-market/meeting/{deal_id}/agents/{profile_id}/run-r1")
async def run_meeting_r1_agent(
    deal_id: str,
    profile_id: str,
    req: PrimaryMarketMeetingR1AgentRunRequest | None = None,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    request_payload = req or PrimaryMarketMeetingR1AgentRunRequest()
    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id, current_user=current_user, action="write")
    canonical_profile = ic_policy.canonical_ic_profile_id(profile_id)
    if canonical_profile not in ic_policy.R1_AGENT_SEQUENCE:
        raise HTTPException(status_code=400, detail="R1 agent run requires an R1 IC profile")
    try:
        if request_payload.dry_run:
            result = ic_agent_runtime.build_workflow_r1_agent_run_dry_run(
                normalized_deal_id,
                canonical_profile,
                round_name=request_payload.round_name,
            )
            return deal_store.redact_public_payload({
                "deal_id": normalized_deal_id,
                "agent_id": canonical_profile,
                "dry_run": True,
                "result": result,
            })
        if not request_payload.allow_hermes:
            raise HTTPException(status_code=400, detail="allow_hermes must be true for non-dry-run R1 agent execution")
        result = await ic_agent_runtime.run_workflow_r1_agent(
            normalized_deal_id,
            canonical_profile,
            round_name=request_payload.round_name,
            created_by=_user_payload(current_user),
            timeout=request_payload.timeout,
        )
        event_type, title, body, meta = _r1_agent_task_event_payload(
            result=result,
            profile_id=canonical_profile,
            dry_run=False,
        )
        event = _append_meeting_system_event(
            normalized_deal_id,
            lane=request_payload.lane or f"agent-{canonical_profile}",
            event_type=event_type,
            speaker=_profile_label(canonical_profile),
            title=title,
            body=body,
            tone="success",
            phase="R1",
            agent_id=canonical_profile,
            meta=meta,
        )
        return deal_store.redact_public_payload({
            "deal_id": normalized_deal_id,
            "agent_id": canonical_profile,
            "dry_run": False,
            "result": result,
            "event": event,
            "readiness": primary_market_meeting_readiness.build_meeting_readiness(normalized_deal_id),
        })
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(normalized_deal_id) from exc
    except ic_agent_runtime.ICTaskAlreadyClaimedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=f"Meeting R1 agent run failed: {exc}") from exc


@router.post("/primary-market/meeting/{deal_id}/workflow/run-r1-serial")
async def run_meeting_r1_serial(
    deal_id: str,
    req: PrimaryMarketMeetingR1SerialRunRequest | None = None,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    request_payload = req or PrimaryMarketMeetingR1SerialRunRequest()
    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id, current_user=current_user, action="write")
    try:
        if request_payload.dry_run:
            result = ic_agent_runtime.build_workflow_r1_serial_run_dry_run(
                normalized_deal_id,
                round_name=request_payload.round_name,
                max_agents=request_payload.max_agents,
            )
            return deal_store.redact_public_payload({
                "deal_id": normalized_deal_id,
                "dry_run": True,
                "result": result,
            })
        if not request_payload.allow_hermes:
            raise HTTPException(status_code=400, detail="allow_hermes must be true for non-dry-run R1 serial execution")
        result = await ic_agent_runtime.run_workflow_r1_serial(
            normalized_deal_id,
            round_name=request_payload.round_name,
            max_agents=request_payload.max_agents,
            created_by=_user_payload(current_user),
            timeout=request_payload.timeout,
        )
        event_type, title, body, meta = _r1_serial_task_event_payload(
            result=result,
            dry_run=False,
        )
        event = _append_meeting_system_event(
            normalized_deal_id,
            lane=request_payload.lane or "workflow-main",
            event_type=event_type,
            speaker=_profile_label("siq_ic_master_coordinator"),
            title=title,
            body=body,
            tone="success" if event_type == "artifact_written" else "info",
            phase="R1",
            agent_id="siq_ic_master_coordinator",
            meta=meta,
        )
        return deal_store.redact_public_payload({
            "deal_id": normalized_deal_id,
            "dry_run": False,
            "result": result,
            "event": event,
            "readiness": primary_market_meeting_readiness.build_meeting_readiness(normalized_deal_id),
        })
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(normalized_deal_id) from exc
    except ic_agent_runtime.ICTaskAlreadyClaimedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=f"Meeting R1 serial run failed: {exc}") from exc


@router.post("/primary-market/meeting/{deal_id}/workflow/advance")
async def advance_meeting_workflow(
    deal_id: str,
    req: PrimaryMarketMeetingWorkflowAdvanceRequest | None = None,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    request_payload = req or PrimaryMarketMeetingWorkflowAdvanceRequest()
    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id, current_user=current_user, action="write")
    try:
        if request_payload.dry_run:
            result = ic_agent_runtime.build_workflow_advance_next_dry_run(
                normalized_deal_id,
                allow_hermes=request_payload.allow_hermes,
                max_agents=request_payload.max_agents,
                r3_skip=request_payload.r3_skip,
                r3_skip_reason=request_payload.r3_skip_reason,
                r4_overwrite=request_payload.r4_overwrite,
            )
            event = _append_meeting_system_event(
                normalized_deal_id,
                lane="workflow-main",
                event_type="audit_event",
                speaker=_profile_label("siq_ic_master_coordinator"),
                title="Workflow 下一步预演",
                body=json.dumps(deal_store.redact_public_payload(result), ensure_ascii=False, default=str)[:1200],
                tone="info",
                phase=str(result.get("current_phase") or result.get("phase") or ""),
                agent_id="siq_ic_master_coordinator",
                meta={"source": "workflow.advance", "dry_run": True},
            )
            return deal_store.redact_public_payload({
                "deal_id": normalized_deal_id,
                "dry_run": True,
                "result": result,
                "event": event,
            })
        result = await ic_agent_runtime.run_workflow_advance_next(
            normalized_deal_id,
            allow_hermes=request_payload.allow_hermes,
            max_agents=request_payload.max_agents,
            r3_skip=request_payload.r3_skip,
            r3_skip_reason=request_payload.r3_skip_reason,
            r4_overwrite=request_payload.r4_overwrite,
            created_by=_user_payload(current_user),
        )
        event = _append_meeting_system_event(
            normalized_deal_id,
            lane="workflow-main",
            event_type="artifact_written",
            speaker=_profile_label("siq_ic_master_coordinator"),
            title="Workflow 已推进",
            body=json.dumps(deal_store.redact_public_payload(result), ensure_ascii=False, default=str)[:1200],
            tone="success",
            phase=str((result.get("workflow") or {}).get("current_phase") or ""),
            agent_id="siq_ic_master_coordinator",
            meta={"source": "workflow.advance", "dry_run": False},
        )
        return deal_store.redact_public_payload({
            "deal_id": normalized_deal_id,
            "dry_run": False,
            "result": result,
            "event": event,
            "readiness": primary_market_meeting_readiness.build_meeting_readiness(normalized_deal_id),
        })
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(normalized_deal_id) from exc
    except ic_agent_runtime.ICTaskAlreadyClaimedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=f"Meeting workflow advance failed: {exc}") from exc


@router.post("/primary-market/meeting/{deal_id}/decision/human-confirm")
def confirm_meeting_decision(
    deal_id: str,
    req: PrimaryMarketMeetingDecisionHumanConfirmRequest,
    current_user: User = Depends(require_permission("report.create")),
) -> dict[str, Any]:
    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id, current_user=current_user, action="write")
    try:
        result = deal_decision.update_human_confirmation(
            normalized_deal_id,
            status=req.status,
            confirmed_by=_user_payload(current_user),
            override_reason=req.override_reason,
            override_decision=req.override_decision,
            override_score=req.override_score,
            dry_run=req.dry_run,
        )
        event = None
        if not req.dry_run:
            event = _append_meeting_system_event(
                normalized_deal_id,
                lane="workflow-main",
                event_type="decision_draft",
                speaker=_profile_label("siq_ic_chairman"),
                title="R4 人工确认已更新",
                body=f"status={req.status}; override_decision={req.override_decision or '-'}; override_reason={req.override_reason or '-'}",
                tone="success" if req.status == "confirmed" else "warning",
                phase="R4",
                agent_id="siq_ic_chairman",
                meta={"source": "decision.human_confirm", "dry_run": False},
            )
        return deal_store.redact_public_payload({
            "deal_id": normalized_deal_id,
            "dry_run": req.dry_run,
            "result": result,
            "event": event,
            "readiness": primary_market_meeting_readiness.build_meeting_readiness(normalized_deal_id),
        })
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise _not_found(normalized_deal_id) from exc


@router.post("/primary-market/meeting/{profile}/chat/stream")
async def chat_stream(
    profile: str,
    req: PrimaryMarketMeetingChatRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> EventSourceResponse:
    normalized_profile = canonical_meeting_profile(profile)
    deal_id = deal_id_from_request(req)
    deal_summary = _load_deal_summary(deal_id, current_user=current_user)
    lane = _normalize_lane(req.lane or (req.context.lane if req.context else None) or "main")
    _assert_profile_allowed_for_lane(normalized_profile, lane)
    _apply_meeting_model_mode(req, normalized_profile)
    current_user_id = int(current_user.id)
    current_user_role = _role_value(current_user)
    _detach_current_user(async_session, current_user)

    await enforce_quota_or_429_async(
        async_session,
        user_id=current_user_id,
        user_role=current_user_role,
        event_type=AGENT_QUESTION_EVENT,
    )
    await record_usage_async(
        async_session,
        user_id=current_user_id,
        event_type=AGENT_QUESTION_EVENT,
        source=normalized_profile,
    )
    session_id = await resolve_or_create_meeting_session(
        async_session,
        current_user,
        profile=normalized_profile,
        deal_id=deal_id,
        lane=lane,
        session_id=req.session_id,
        user_id=str(current_user_id),
        user_role=current_user_role,
    )

    async def done_payload(reply: str) -> dict:
        quality = _evaluate_and_store_reply_quality(
            deal_id=deal_id,
            lane=lane,
            profile=normalized_profile,
            message=req.message,
            reply=reply,
        )
        return {"new_achievements": [], "session_id": session_id, "quality": quality}

    async def event_generator():
        control_reply = maybe_handle_model_control(_meeting_model_control_message(req), normalized_profile)
        if control_reply:
            memory_scope = _primary_market_memory_scope(normalized_profile, deal_id)
            await save_message(
                async_session,
                "user",
                req.display_message or req.message,
                session_id,
                attachments=req.attachments,
                **memory_scope,
            )
            await save_message(
                async_session,
                "assistant",
                control_reply,
                session_id,
                **memory_scope,
            )
            get_session_manager().increment_message_count(session_id)
            yield {
                "event": "run",
                "data": json.dumps({"run_id": f"control-{uuid4().hex}", "session_id": session_id}, ensure_ascii=False),
            }
            yield {"event": "delta", "data": json.dumps({"content": control_reply}, ensure_ascii=False)}
            yield {
                "event": "done",
                "data": json.dumps(
                    {"new_achievements": [], "content": control_reply, "session_id": session_id},
                    ensure_ascii=False,
                ),
            }
            return

        runtime_context = primary_market_runtime_context(
            req=req,
            deal_id=deal_id,
            profile=normalized_profile,
            deal_summary=deal_summary,
        )
        scoped_message = await asyncio.to_thread(
            _profile_scoped_meeting_message,
            normalized_profile,
            req.message,
            deal_id,
            retrieval_query=req.retrieval_query,
        )
        async for event in stream_chat_reply(
            scoped_message,
            request,
            async_session,
            session_id=session_id,
            profile=cast(HermesProfile, normalized_profile),
            context=runtime_context,
            display_message=req.display_message,
            attachments=req.attachments,
            done_payload_factory=done_payload,
            enforce_evidence_contract=False,
        ):
            yield event
        get_session_manager().increment_message_count(session_id)

    return EventSourceResponse(event_generator())


@router.post("/primary-market/meeting/{profile}/chat/stop")
async def stop_chat(
    profile: str,
    deal_id: str,
    lane: str = "main",
    session_id: str | None = None,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> dict:
    normalized_profile = canonical_meeting_profile(profile)
    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id, current_user=current_user, action="write")
    normalized_lane = _normalize_lane(lane)
    resolved_session_id = await resolve_or_create_meeting_session(
        async_session,
        current_user,
        profile=normalized_profile,
        deal_id=normalized_deal_id,
        lane=normalized_lane,
        session_id=session_id,
    )
    return await stop_active_run(cast(HermesProfile, normalized_profile), resolved_session_id)


@router.get("/primary-market/meeting/{profile}/chat/active")
async def active_chat(
    profile: str,
    deal_id: str,
    lane: str = "main",
    session_id: str | None = None,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> dict:
    normalized_profile = canonical_meeting_profile(profile)
    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id, current_user=current_user, action="view")
    normalized_lane = _normalize_lane(lane)
    resolved_session_id = await resolve_or_create_meeting_session(
        async_session,
        current_user,
        profile=normalized_profile,
        deal_id=normalized_deal_id,
        lane=normalized_lane,
        session_id=session_id,
    )
    return get_active_run_snapshot(cast(HermesProfile, normalized_profile), resolved_session_id)


@router.get("/primary-market/meeting/{profile}/chat/active/stream")
async def active_chat_stream(
    profile: str,
    request: Request,
    deal_id: str,
    lane: str = "main",
    session_id: str | None = None,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> EventSourceResponse:
    normalized_profile = canonical_meeting_profile(profile)
    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id, current_user=current_user, action="view")
    normalized_lane = _normalize_lane(lane)
    resolved_session_id = await resolve_or_create_meeting_session(
        async_session,
        current_user,
        profile=normalized_profile,
        deal_id=normalized_deal_id,
        lane=normalized_lane,
        session_id=session_id,
    )
    if not has_active_run(cast(HermesProfile, normalized_profile), resolved_session_id):
        raise HTTPException(status_code=404, detail="No active chat run")
    return EventSourceResponse(
        stream_active_run_events(
            request,
            profile=cast(HermesProfile, normalized_profile),
            session_id=resolved_session_id,
            offset=offset,
        )
    )


@router.post("/primary-market/meeting/{profile}/chat/session")
async def create_session(
    profile: str,
    deal_id: str,
    lane: str = "main",
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> dict:
    normalized_profile = canonical_meeting_profile(profile)
    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id, current_user=current_user, action="view")
    normalized_lane = _normalize_lane(lane)
    _assert_profile_allowed_for_lane(normalized_profile, normalized_lane)
    user_id = str(current_user.id)
    user_role = current_user.role
    session_profile = primary_market_session_profile(deal_id=normalized_deal_id, lane=normalized_lane)
    _detach_current_user(async_session, current_user)
    session_id, deleted_session_ids = get_session_manager().create_session(
        user_id,
        session_profile,
        user_role=user_role,
        return_deleted=True,
    )
    await chat_router._delete_chat_messages(async_session, deleted_session_ids)
    return {
        "session_id": session_id,
        "created": True,
        "deleted_session_ids": deleted_session_ids,
        "deal_id": normalized_deal_id,
        "lane": normalized_lane,
    }


@router.post("/primary-market/meeting/{profile}/chat/session/{session_id}")
async def switch_session(
    profile: str,
    session_id: str,
    deal_id: str,
    lane: str = "main",
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> dict:
    normalized_profile = canonical_meeting_profile(profile)
    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id, current_user=current_user, action="view")
    normalized_lane = _normalize_lane(lane)
    session_profile = _meeting_session_scope(normalized_profile, normalized_deal_id, normalized_lane)
    user_id = str(current_user.id)
    user_role = current_user.role
    _detach_current_user(async_session, current_user)
    try:
        chat_router._assert_session_belongs_to_user(session_id, user_id, session_profile)
        get_session_manager().set_current_session(user_id, session_profile, session_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        summary = await chat_router._db_session_summary(async_session, session_id)
        if not summary:
            raise
        get_session_manager().restore_session(
            user_id,
            session_profile,
            session_id,
            user_role=user_role,
            created_at=summary.get("created_at"),
            updated_at=summary.get("updated_at"),
            message_count=int(summary.get("message_count") or 0),
        )
    return {"session_id": session_id, "current": True, "deal_id": normalized_deal_id, "lane": normalized_lane}


@router.delete("/primary-market/meeting/{profile}/chat/session")
async def reset_session(
    profile: str,
    deal_id: str,
    lane: str = "main",
    session_id: str | None = None,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> dict:
    normalized_profile = canonical_meeting_profile(profile)
    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id, current_user=current_user, action="view")
    normalized_lane = _normalize_lane(lane)
    session_profile = _meeting_session_scope(normalized_profile, normalized_deal_id, normalized_lane)
    user_id = str(current_user.id)
    user_role = current_user.role
    _detach_current_user(async_session, current_user)
    resolved_session_id = await resolve_or_create_meeting_session(
        async_session,
        current_user,
        profile=normalized_profile,
        deal_id=normalized_deal_id,
        lane=normalized_lane,
        session_id=session_id,
        user_id=user_id,
        user_role=user_role,
    )
    session_mgr = get_session_manager()
    session_mgr.delete_session(resolved_session_id, user_id)
    await chat_router._delete_chat_messages(async_session, [resolved_session_id])
    new_session_id, deleted_session_ids = session_mgr.create_session(
        user_id,
        session_profile,
        user_role=user_role,
        return_deleted=True,
    )
    await chat_router._delete_chat_messages(async_session, deleted_session_ids)
    return {
        "session_id": new_session_id,
        "deleted_old": resolved_session_id,
        "deal_id": normalized_deal_id,
        "lane": normalized_lane,
    }


@router.post("/primary-market/meeting/{profile}/chat", response_model=PrimaryMarketMeetingChatResponse)
async def chat(
    profile: str,
    req: PrimaryMarketMeetingChatRequest,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> ChatResponse:
    normalized_profile = canonical_meeting_profile(profile)
    deal_id = deal_id_from_request(req)
    deal_summary = _load_deal_summary(deal_id, current_user=current_user)
    lane = _normalize_lane(req.lane or (req.context.lane if req.context else None) or "main")
    _assert_profile_allowed_for_lane(normalized_profile, lane)
    _apply_meeting_model_mode(req, normalized_profile)
    current_user_id = int(current_user.id)
    current_user_role = _role_value(current_user)
    _detach_current_user(async_session, current_user)

    await enforce_quota_or_429_async(
        async_session,
        user_id=current_user_id,
        user_role=current_user_role,
        event_type=AGENT_QUESTION_EVENT,
    )
    await record_usage_async(
        async_session,
        user_id=current_user_id,
        event_type=AGENT_QUESTION_EVENT,
        source=normalized_profile,
    )

    session_id = await resolve_or_create_meeting_session(
        async_session,
        current_user,
        profile=normalized_profile,
        deal_id=deal_id,
        lane=lane,
        session_id=req.session_id,
        user_id=str(current_user_id),
        user_role=current_user_role,
    )

    control_reply = maybe_handle_model_control(_meeting_model_control_message(req), normalized_profile)
    if control_reply:
        memory_scope = _primary_market_memory_scope(normalized_profile, deal_id)
        await save_message(
            async_session,
            "user",
            req.display_message or req.message,
            session_id,
            attachments=req.attachments,
            **memory_scope,
        )
        await save_message(
            async_session,
            "assistant",
            control_reply,
            session_id,
            **memory_scope,
        )
        get_session_manager().increment_message_count(session_id)
        return PrimaryMarketMeetingChatResponse(reply=control_reply, new_achievements=[], session_id=session_id)

    runtime_context = primary_market_runtime_context(
        req=req,
        deal_id=deal_id,
        profile=normalized_profile,
        deal_summary=deal_summary,
    )
    scoped_message = await asyncio.to_thread(
        _profile_scoped_meeting_message,
        normalized_profile,
        req.message,
        deal_id,
        retrieval_query=req.retrieval_query,
    )
    reply = await collect_chat_reply(
        scoped_message,
        async_session,
        session_id=session_id,
        profile=cast(HermesProfile, normalized_profile),
        context=runtime_context,
        display_message=req.display_message,
        attachments=req.attachments,
        enforce_evidence_contract=False,
    )
    quality = _evaluate_and_store_reply_quality(
        deal_id=deal_id,
        lane=lane,
        profile=normalized_profile,
        message=req.message,
        reply=reply,
    )
    get_session_manager().increment_message_count(session_id)
    return PrimaryMarketMeetingChatResponse(reply=reply, new_achievements=[], session_id=session_id, quality=quality)
