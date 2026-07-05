"""Primary-market meeting room chat routes for SIQ IC Hermes agents."""

import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel, Field
from sqlmodel.ext.asyncio.session import AsyncSession

from database import get_async_session
from schemas import (
    ChatAttachment,
    ChatAttachmentUploadRequest,
    ChatAttachmentUploadResponse,
    ChatContext,
    ChatContextPage,
    ChatResponse,
)
from services import deal_evidence
from services import deal_phase_artifacts
from services import deal_status
from services import deal_store
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
from services.hermes_model_control import maybe_handle_model_control
from services.path_config import BACKEND_DATA_ROOT
from services.session_manager import get_session_manager
from services.usage_service import AGENT_QUESTION_EVENT, record_usage_async
from routers import chat as chat_router
from routers.agent_user_router import enforce_quota_or_429_async


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
    session_id: str | None = None
    display_message: str | None = None
    deal_id: str | None = None
    company_name: str | None = None
    phase: str | None = None
    lane: str = "main"
    context: PrimaryMarketMeetingContext | None = None
    attachments: list[ChatAttachment] = Field(default_factory=list)


class PrimaryMarketMeetingChatResponse(ChatResponse):
    session_id: str


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


def _not_found(deal_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Deal not found: {deal_id}")


def _role_value(user: User) -> str:
    return str(user.role.value if hasattr(user.role, "value") else user.role)


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


def _load_deal_summary(deal_id: str) -> dict:
    try:
        return deal_store.read_deal_detail(deal_id).get("summary", {})
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


def _validated_deal_dir(deal_id: str) -> tuple[str, Path]:
    try:
        normalized = deal_store.validate_deal_id(deal_id)
        package_dir = deal_store.safe_deal_dir(normalized)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not (package_dir / "manifest.json").is_file():
        raise _not_found(normalized)
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
    current_user: User = Depends(require_permission("report.view")),
) -> dict:
    del current_user
    needle = (q or "").strip().lower()
    deals = deal_store.list_deals()
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
    return {"deals": deal_store.redact_public_payload(deals)}


@router.get("/primary-market/projects/{deal_id}")
def get_project(
    deal_id: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict:
    del current_user
    try:
        return deal_store.read_deal_detail(deal_id)
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.get("/primary-market/projects/{deal_id}/status")
def get_project_status(
    deal_id: str,
    current_user: User = Depends(require_permission("report.view")),
) -> dict:
    del current_user
    from services import deal_status

    try:
        return deal_status.summarize_deal_status(deal_id)
    except FileNotFoundError as exc:
        raise _not_found(deal_id) from exc


@router.get("/primary-market/projects/{deal_id}/meeting-transcript")
def get_meeting_transcript(
    deal_id: str,
    lane: str = "main",
    limit: int | None = None,
    current_user: User = Depends(require_permission("report.view")),
) -> dict:
    del current_user
    normalized_deal_id, package_dir = _validated_deal_dir(deal_id)
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
    del current_user
    normalized_deal_id, package_dir = _validated_deal_dir(deal_id)
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

    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id)
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
    del current_user
    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id)
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
    return ChatContext(page=ChatContextPage(title=metadata))


_IC_PROFILE_LABELS = {
    "siq_ic_master_coordinator": "总协调员",
    "siq_ic_chairman": "投委会主席",
    "siq_ic_strategist": "战略专家",
    "siq_ic_sector_expert": "行业专家",
    "siq_ic_finance_auditor": "财务审计委员",
    "siq_ic_legal_scanner": "法务合规委员",
    "siq_ic_risk_controller": "风险管理委员",
}
_IC_PROFILE_ROLE_CONTRACTS: dict[str, dict[str, str]] = {
    "siq_ic_master_coordinator": {
        "role": "SIQ 投委会秘书/协调者",
        "focus": "事实核验、R0-R4 流程推进、争议整理、专家调度、审计留痕和报告汇总。",
        "outputs": "协调结论、下一步发言安排、证据缺口、争议点和可追溯审计链。",
        "boundaries": "不得代替专家输出行业、财务、法律、风控或最终投资观点；不得跳过证据核验和双库检索。",
    },
    "siq_ic_chairman": {
        "role": "SIQ 投委会主席",
        "focus": "综合各专家意见、冲突裁决、战略对齐、Go/No-Go 判断和最终投决建议。",
        "outputs": "投决建议书、置信度、表决前主席意见、裁决理由和可执行建议。",
        "boundaries": "不得替代财务建模、法律审查、行业技术评估、风险清单或宏观政策深挖。",
    },
    "siq_ic_strategist": {
        "role": "SIQ 投委会宏观战略专家",
        "focus": "宏观政策、资本流向、经济周期、地缘政治、赛道配置和投资时机。",
        "outputs": "赛道配置建议、政策/周期影响、资本市场偏好、战略层风险和投资窗口判断。",
        "boundaries": "不得替代行业技术路线、财务估值、法律合规、操作层风控或最终投决。",
    },
    "siq_ic_sector_expert": {
        "role": "SIQ 投委会行业专家",
        "focus": "TAM/SAM/SOM、竞争格局、技术路线、国产替代、商业模式和行业生命周期。",
        "outputs": "赛道深度研究、市场规模测算、竞争/技术壁垒、客户需求验证和行业推荐指数。",
        "boundaries": "不得替代宏观政策分析、财务估值、法律合规、风险清单或最终投决。",
    },
    "siq_ic_finance_auditor": {
        "role": "SIQ 投委会财务专家",
        "focus": "财务分析、估值模型、现金流、盈利模式、收入质量、压力测试和敏感性分析。",
        "outputs": "财务尽调 memo、估值区间、verified/assumed 区分、财务疑点、对价建议和置信度。",
        "boundaries": "不得替代行业技术判断、法律合规审查、宏观政策分析、风险清单或最终投决。",
    },
    "siq_ic_legal_scanner": {
        "role": "SIQ 投委会法务专家",
        "focus": "法律合规、主体/股权、重大合同、知识产权、诉讼处罚、监管审批和 TS 条款。",
        "outputs": "法律尽调报告、合规指数、红线问题、条款风险、交割条件和整改建议。",
        "boundaries": "不得替代宏观政策判断、市场环境评估、财务计算、行业技术分析或最终投决。",
    },
    "siq_ic_risk_controller": {
        "role": "SIQ 投委会风控委员",
        "focus": "市场风险、ESG、舆情、供应链、行业周期、压力测试、红黄线和投后监控指标。",
        "outputs": "风险扫描报告、风险等级、红旗项、监控指标、触发阈值和风控条款建议。",
        "boundaries": "不得替代宏观政策判断、法律合同审查、公司内部运营分析、财务计算或最终投决。",
    },
}
_SUGGESTIONS_TIMEOUT_SECONDS = 18


def _profile_label(profile: str) -> str:
    return _IC_PROFILE_LABELS.get(profile, profile)


def _ic_profile_role_contract(profile: str) -> str:
    contract = _IC_PROFILE_ROLE_CONTRACTS.get(profile)
    if not contract:
        return ""
    source_dir = f"agents/hermes/profiles/{profile}"
    return "\n".join(
        [
            "一级市场 IC profile 职责护栏:",
            f"- profile_id: {profile}",
            f"- 角色名称: {contract['role']}",
            f"- 职责来源: {source_dir}/IDENTITY.md、{source_dir}/AGENTS.md、{source_dir}/SOUL.md、{source_dir}/USER.md",
            f"- 本轮只按该 profile 的职责回答: {contract['focus']}",
            f"- 应输出: {contract['outputs']}",
            f"- 角色边界: {contract['boundaries']}",
            "- 若主持人问题要求越权，先声明角色边界，再只回答本 profile 职责内的部分，并建议应由哪个 SIQ IC profile 接手。",
            "- 涉及事实、评分、投决或风险判断时，必须优先使用 Deal OS evidence、startup-retrieval receipt、R0-R4 产物和用户附件；信息不足时标注 assumed/待核验，不得编造。",
        ]
    )


def _profile_scoped_meeting_message(profile: str, message: str) -> str:
    raw = str(message or "").strip()
    if "一级市场 IC profile 职责护栏:" in raw:
        return raw
    contract = _ic_profile_role_contract(profile)
    if not contract:
        return raw
    return "\n\n".join([contract, "主持人原始问题:", raw])


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
    return "\n".join([
        "你正在为 SIQ 一级市场投委会会议室生成空状态建议问题。",
        f"当前智能体: {_profile_label(profile)} ({profile})",
        f"会议模式: {mode}; lane: {lane}",
        "",
        "请根据下方真实项目上下文，生成当前最适合用户点击的建议问题。",
        "要求:",
        "1. 只输出一个 JSON 对象，不要 Markdown，不要代码块，不要解释。",
        "2. JSON 格式必须为: {\"intro\":\"...\", \"questions\":[{\"label\":\"...\", \"prompt\":\"...\"}]}。",
        "3. intro 为当前智能体第一人称简介，40-72 个中文字符，必须结合当前项目阶段或上下文。",
        "4. questions 必须正好 5 个；label 为 2-8 个中文字符；prompt 是点击后发给该智能体的完整中文问题。",
        "5. 问题必须贴合当前项目的阶段、证据状态、材料缺口、分歧或投决动作；不要输出泛泛的模板问题。",
        "6. 不要虚构数据、文件路径、证据 id 或结论；信息不足时，问题应引导补证、核验或确认。",
        "",
        "项目上下文 JSON:",
        _compact_json(context),
    ])


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
    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id)
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
    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id)
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
    del current_user
    normalized_profile = canonical_meeting_profile(profile)
    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id)
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
        source = "model"
        error = None
    except asyncio.TimeoutError:
        intro, questions = "", []
        source = "model_error"
        error = "智能体建议生成超时，请确认对应 IC Hermes 网关已启动后重试。"
    except Exception as exc:
        intro, questions = "", []
        source = "model_error"
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
    deal_summary = _load_deal_summary(deal_id)
    lane = _normalize_lane(req.lane or (req.context.lane if req.context else None) or "main")
    _assert_profile_allowed_for_lane(normalized_profile, lane)
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

    async def done_payload(_reply: str) -> dict:
        return {"new_achievements": [], "session_id": session_id}

    async def event_generator():
        control_reply = maybe_handle_model_control(req.message, normalized_profile)
        if control_reply:
            await save_message(
                async_session,
                "user",
                req.display_message or req.message,
                session_id,
                attachments=req.attachments,
            )
            await save_message(async_session, "assistant", control_reply, session_id)
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
        scoped_message = _profile_scoped_meeting_message(normalized_profile, req.message)
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
    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id)
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
    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id)
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
    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id)
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
    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id)
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
    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id)
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
    normalized_deal_id, _package_dir = _validated_deal_dir(deal_id)
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
    deal_summary = _load_deal_summary(deal_id)
    lane = _normalize_lane(req.lane or (req.context.lane if req.context else None) or "main")
    _assert_profile_allowed_for_lane(normalized_profile, lane)
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

    control_reply = maybe_handle_model_control(req.message, normalized_profile)
    if control_reply:
        await save_message(
            async_session,
            "user",
            req.display_message or req.message,
            session_id,
            attachments=req.attachments,
        )
        await save_message(async_session, "assistant", control_reply, session_id)
        get_session_manager().increment_message_count(session_id)
        return PrimaryMarketMeetingChatResponse(reply=control_reply, new_achievements=[], session_id=session_id)

    runtime_context = primary_market_runtime_context(
        req=req,
        deal_id=deal_id,
        profile=normalized_profile,
        deal_summary=deal_summary,
    )
    scoped_message = _profile_scoped_meeting_message(normalized_profile, req.message)
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
    get_session_manager().increment_message_count(session_id)
    return PrimaryMarketMeetingChatResponse(reply=reply, new_achievements=[], session_id=session_id)
