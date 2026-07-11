"""Shared authenticated chat routes for SIQ specialist agents."""
import json
import asyncio
import hashlib
from dataclasses import dataclass
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session
from sqlmodel import delete as sql_delete, select
from sqlmodel.ext.asyncio.session import AsyncSession
from sse_starlette.sse import EventSourceResponse

from database import engine, get_async_session
from models import ChatMessage
from schemas import AnswerAuditTraceResponse, ChatRequest, ChatResponse
from services.agent_chat_runtime import (
    HISTORY_LIMIT,
    chat_message_has_visible_payload,
    chat_history_response,
    collect_chat_reply,
    get_active_run_snapshot,
    has_active_run,
    save_message,
    stop_active_run,
    stream_active_run_events,
    stream_chat_reply,
)
from services.auth_dependencies import get_current_user
from services.auth_service import User
from services.agent_runtime_answer_audit import get_answer_audit_trace, is_answer_audit_trace_id
from services.analysis_report_workflow import (
    AnalysisReportWorkflowRequest,
    build_analysis_report_workflow_request,
    run_analysis_report_workflow,
)
from services.factcheck_workflow import (
    FactcheckWorkflowRequest,
    build_factcheck_workflow_request,
    run_factcheck_workflow,
)
from services.hermes_client import HermesProfile
from services.hermes_model_control import maybe_handle_model_control
from services.session_manager import get_session_manager, keeps_sessions_forever
from routers.workspace import _quota_error_payload, record_user_artifact
from routers.workspace import extract_report_artifact_from_text, company_identity_from_dir
from services.usage_service import AGENT_QUESTION_EVENT, ensure_within_quota_async, record_usage_async


@dataclass(frozen=True)
class SpecialistAgentConfig:
    prefix: str
    tag: str
    profile: HermesProfile


AGENT_WORKSPACE_LABELS = {
    "analysis": "智能分析",
    "factchecker": "事实核查",
    "tracking": "持续跟踪",
    "legal": "法务合规",
}


AGENT_WORKSPACE_ROUTES = {
    "analysis": "/analysis",
    "factchecker": "/verify",
    "tracking": "/tracking",
    "legal": "/legal",
}


NON_COMPLETED_REPLY_MARKERS = (
    "[已停止]",
    "[失败]",
    "[已取消]",
    "[错误]",
)

ANALYSIS_REPORT_WORKFLOW_ERROR_PREFIX = "[错误] research-pack 报告生成工作流执行失败"
FACTCHECK_WORKFLOW_ERROR_PREFIX = "[错误] 事实核查工作流执行失败"


def _context_dump(context: object | None) -> dict:
    if context is None:
        return {}
    if hasattr(context, "model_dump"):
        dumped = context.model_dump(exclude_none=True)
        return dumped if isinstance(dumped, dict) else {}
    return context if isinstance(context, dict) else {}


def _artifact_request_hash(req: ChatRequest) -> str:
    payload = {
        "message": (req.display_message or req.message or "").strip(),
        "context": _context_dump(req.context),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _reply_counts_as_completed(reply: str) -> bool:
    text = (reply or "").strip()
    if not text:
        return False
    return not any(marker in text for marker in NON_COMPLETED_REPLY_MARKERS)


def _record_agent_workspace_artifact(
    sync_session: Session,
    *,
    current_user_id: int,
    config: SpecialistAgentConfig,
    session_id: str,
    req: ChatRequest,
    reply: str,
) -> None:
    context = _context_dump(req.context)
    company = context.get("company") if isinstance(context.get("company"), dict) else {}
    report = context.get("report") if isinstance(context.get("report"), dict) else {}
    inferred = (
        extract_report_artifact_from_text(reply, fallback_section=config.tag)
        or extract_report_artifact_from_text(str(report.get("url") or ""), fallback_section=config.tag)
    )

    company_dir = str(inferred.get("company_dir") if inferred else company.get("dir") or "").strip()
    identity = company_identity_from_dir(company_dir)
    company_name = str((inferred or {}).get("company_name") or company.get("name") or identity.get("company_name") or "").strip()
    company_code = str((inferred or {}).get("company_code") or company.get("code") or identity.get("company_code") or "").strip()
    label = AGENT_WORKSPACE_LABELS.get(config.tag, config.tag)
    route = AGENT_WORKSPACE_ROUTES.get(config.tag, config.prefix)
    query = f"?company={company_dir}" if company_dir else ""
    path = str((inferred or {}).get("page_path") or "").strip() or f"{route}{query}"
    title_parts = [part for part in [company_name or company_code, label] if part]
    title = " · ".join(title_parts) or label
    message_hint = (req.display_message or req.message or "").strip()[:80]
    if message_hint and message_hint not in title:
        title = f"{title}：{message_hint}"
    artifact_key = str((inferred or {}).get("artifact_key") or f"agent:{config.tag}:{session_id}:{_artifact_request_hash(req)}")
    global_artifact_id = str(
        (inferred or {}).get("source_path")
        or f"{config.tag}:{company_dir or company_code}:{session_id}:{_artifact_request_hash(req)}"
    )

    try:
        record_user_artifact(
            sync_session,
            user_id=current_user_id,
            artifact_type="report",
            artifact_key=artifact_key,
            title=title[:255],
            path=path,
            source=config.tag,
            global_artifact_id=global_artifact_id,
            company_code=company_code,
            company_name=company_name,
            company_dir=company_dir,
        )
    except Exception as exc:
        print(f"[workspace] failed to record agent artifact for user={current_user_id} tag={config.tag}: {exc}")


async def _record_agent_workspace_artifact_background(
    *,
    current_user_id: int,
    config: SpecialistAgentConfig,
    session_id: str,
    req: ChatRequest,
    reply: str,
) -> dict:
    if not _reply_counts_as_completed(reply):
        return {"workspace_synced": False, "reason": "non_completed_reply"}

    loop = asyncio.get_event_loop()

    def _write() -> None:
        with Session(engine) as sync_session:
            _record_agent_workspace_artifact(
                sync_session,
                current_user_id=current_user_id,
                config=config,
                session_id=session_id,
                req=req,
                reply=reply,
            )

    await loop.run_in_executor(None, _write)
    return {"workspace_synced": True}


def _analysis_report_workflow_request(
    config: SpecialistAgentConfig,
    req: ChatRequest,
) -> AnalysisReportWorkflowRequest | None:
    if config.tag != "analysis" and config.profile != "siq_analysis":
        return None
    return build_analysis_report_workflow_request(req.message, req.context)


async def _run_analysis_report_workflow_reply(
    workflow_request: AnalysisReportWorkflowRequest,
) -> str:
    try:
        response = await asyncio.to_thread(run_analysis_report_workflow, workflow_request)
    except Exception as exc:
        return f"{ANALYSIS_REPORT_WORKFLOW_ERROR_PREFIX}: {exc}"
    return response.reply


def _factcheck_workflow_request(
    config: SpecialistAgentConfig,
    req: ChatRequest,
) -> FactcheckWorkflowRequest | None:
    if config.tag != "factchecker" and config.profile != "siq_factchecker":
        return None
    return build_factcheck_workflow_request(req.message, req.context)


async def _run_factcheck_workflow_reply(
    workflow_request: FactcheckWorkflowRequest,
) -> str:
    try:
        response = await asyncio.to_thread(run_factcheck_workflow, workflow_request)
    except Exception as exc:
        return f"{FACTCHECK_WORKFLOW_ERROR_PREFIX}: {exc}"
    return response.reply


async def resolve_or_create_session(
    async_session: AsyncSession,
    current_user: User,
    profile: str,
    session_id: str | None = None,
    *,
    user_id: str | None = None,
    user_role: object | None = None,
) -> str:
    user_id = user_id or str(current_user.id)
    user_role = current_user.role if user_role is None else user_role
    session_mgr = get_session_manager()
    if session_id:
        _assert_session_belongs_to_user(session_id, user_id, profile)
        try:
            return session_mgr.set_current_session(user_id, profile, session_id)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            summary = await _db_session_summary(async_session, session_id)
            if summary:
                return session_mgr.restore_session(
                    user_id,
                    profile,
                    session_id,
                    user_role=user_role,
                    created_at=summary.get("created_at"),
                    updated_at=summary.get("updated_at"),
                    message_count=int(summary.get("message_count") or 0),
                )
            return session_mgr.create_session(user_id, profile, user_role=user_role)

    current_session_id = session_mgr.get_current_session_id(user_id, profile)
    if current_session_id:
        return current_session_id

    db_sessions = await _db_session_summaries(
        async_session,
        user_id=user_id,
        profile=profile,
        limit=1,
    )
    if db_sessions:
        latest = db_sessions[0]
        restored_session_id = str(latest.get("session_id") or "")
        if restored_session_id:
            return session_mgr.restore_session(
                user_id,
                profile,
                restored_session_id,
                user_role=user_role,
                created_at=latest.get("created_at"),
                updated_at=latest.get("updated_at"),
                message_count=int(latest.get("message_count") or 0),
            )

    return session_mgr.create_session(user_id, profile, user_role=user_role)


def _user_session_prefix(user_id: str, profile: str) -> str:
    return f"user-{user_id}-{profile}-"


def _assert_session_belongs_to_user(session_id: str, user_id: str, profile: str) -> None:
    if not session_id.startswith(_user_session_prefix(user_id, profile)):
        raise HTTPException(status_code=403, detail="Session does not belong to this user")


def _iso_value(value: object | None) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


async def _session_message_summaries(async_session: AsyncSession, session_ids: list[str]) -> dict[str, dict]:
    if not session_ids:
        return {}
    summaries: dict[str, dict] = {}
    for session_id in session_ids:
        if not session_id:
            continue
        latest_result = await async_session.exec(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id.desc())
            .limit(20)
        )
        latest = next((message for message in latest_result.all() if chat_message_has_visible_payload(message)), None)
        first_user_result = await async_session.exec(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id, ChatMessage.role == "user")
            .order_by(ChatMessage.id)
            .limit(20)
        )
        first_user = next((message for message in first_user_result.all() if chat_message_has_visible_payload(message)), None)
        if latest or first_user:
            summaries[session_id] = {
                "title": (first_user.content[:48] if first_user else ""),
                "preview": (latest.content[:120] if latest else ""),
                "first_message_at": first_user.created_at if first_user else latest.created_at,
                "last_message_at": latest.created_at if latest else first_user.created_at,
            }
    return summaries


async def _db_session_summaries(
    async_session: AsyncSession,
    *,
    user_id: str,
    profile: str,
    limit: int | None,
) -> list[dict]:
    prefix = _user_session_prefix(user_id, profile)
    result = await async_session.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id.startswith(prefix))
        .order_by(ChatMessage.id)
    )
    grouped: dict[str, dict] = {}
    for message in result.all():
        session_id = str(message.session_id or "")
        if not session_id.startswith(prefix):
            continue
        if not chat_message_has_visible_payload(message):
            continue
        item = grouped.setdefault(
            session_id,
            {
                "session_id": session_id,
                "user_id": user_id,
                "profile": profile,
                "message_count": 0,
                "created_at": _iso_value(message.created_at),
                "updated_at": _iso_value(message.created_at),
                "title": "",
                "preview": "",
                "first_message_at": _iso_value(message.created_at),
                "last_message_at": _iso_value(message.created_at),
            },
        )
        item["message_count"] += 1
        item["updated_at"] = _iso_value(message.created_at)
        item["last_message_at"] = _iso_value(message.created_at)
        if message.role == "user" and not item["title"]:
            item["title"] = (message.content or "")[:48]
        item["preview"] = (message.content or "")[:120]
    sessions = sorted(grouped.values(), key=lambda item: _iso_value(item.get("last_message_at") or item.get("updated_at")), reverse=True)
    if limit and limit > 0:
        sessions = sessions[:limit]
    return sessions


async def _db_session_summary(async_session: AsyncSession, session_id: str) -> dict | None:
    result = await async_session.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id)
    )
    messages = [message for message in result.all() if chat_message_has_visible_payload(message)]
    if not messages:
        return None
    first = messages[0]
    last = messages[-1]
    return {
        "session_id": session_id,
        "created_at": first.created_at.isoformat() if hasattr(first.created_at, "isoformat") else str(first.created_at),
        "updated_at": last.created_at.isoformat() if hasattr(last.created_at, "isoformat") else str(last.created_at),
        "message_count": len(messages),
    }


def _merge_sessions(redis_sessions: list[dict], db_sessions: list[dict], limit: int | None) -> list[dict]:
    merged: dict[str, dict] = {}
    db_session_ids = {str(item.get("session_id") or "") for item in db_sessions if item.get("session_id")}
    for item in db_sessions:
        session_id = str(item.get("session_id") or "")
        if session_id:
            merged[session_id] = dict(item)
    for item in redis_sessions:
        session_id = str(item.get("session_id") or "")
        if session_id:
            merged[session_id] = {**item, **merged.get(session_id, {})}
    visible_sessions = [
        item for item in merged.values()
        if str(item.get("session_id") or "") in db_session_ids
    ]
    sessions = sorted(
        visible_sessions,
        key=lambda item: _iso_value(item.get("last_message_at") or item.get("updated_at") or item.get("created_at")),
        reverse=True,
    )
    if limit and limit > 0:
        sessions = sessions[:limit]
    return sessions


def _sessions_payload(
    sessions: list[dict],
    current_session_id: str | None = None,
    message_summaries: dict[str, dict] | None = None,
) -> dict:
    message_summaries = message_summaries or {}
    normalized = []
    for item in sessions:
        session_id = str(item.get("session_id") or "")
        message_summary = message_summaries.get(session_id, {})
        message_count = message_summary.get("message_count") or item.get("message_count") or 0
        if int(message_count or 0) <= 0:
            continue
        created_at = item.get("created_at")
        normalized.append({
            "session_id": session_id,
            "title": item.get("title") or message_summary.get("title") or "未命名会话",
            "preview": item.get("preview") or message_summary.get("preview") or "",
            "message_count": message_count,
            "first_message_at": message_summary.get("first_message_at") or item.get("first_message_at") or created_at,
            "last_message_at": message_summary.get("last_message_at") or item.get("last_message_at") or item.get("updated_at") or created_at,
            "current": bool(current_session_id and session_id == current_session_id),
        })
    return {"sessions": normalized}


def _session_limit(current_user: User, requested: int | None) -> int | None:
    if keeps_sessions_forever(getattr(current_user, "role", None)):
        if requested is None or requested <= 0:
            return None
        return requested
    return min(max(int(requested or 100), 1), 100)


def _role_value(user: User) -> str:
    return str(user.role.value if hasattr(user.role, "value") else user.role)


def _detach_current_user(async_session: AsyncSession, current_user: User) -> None:
    try:
        async_session.expunge(current_user)
    except Exception:
        pass


async def enforce_quota_or_429_async(
    async_session: AsyncSession,
    *,
    user_id: int,
    user_role: str,
    event_type: str,
    increment: int = 1,
) -> tuple[int, int | None]:
    try:
        return await ensure_within_quota_async(
            async_session,
            user_id=user_id,
            user_role=user_role,
            event_type=event_type,
            increment=increment,
        )
    except ValueError as exc:
        parts = str(exc).split(":")
        if len(parts) == 4 and parts[0] == "daily_quota_exceeded":
            raise _quota_error_payload(parts[1], int(parts[2]), int(parts[3])) from exc
        raise


async def _delete_chat_messages(async_session: AsyncSession, session_ids: list[str]) -> None:
    if not session_ids:
        return
    statement = sql_delete(ChatMessage).where(ChatMessage.session_id.in_(session_ids))
    await async_session.exec(statement)
    await async_session.commit()


def _set_endpoint_name(func: Callable, name: str) -> Callable:
    func.__name__ = name
    func.__qualname__ = name
    return func


def create_specialist_agent_router(config: SpecialistAgentConfig) -> APIRouter:
    router = APIRouter(prefix=config.prefix, tags=[config.tag])

    async def chat(
        req: ChatRequest,
        current_user: User = Depends(get_current_user),
        async_session: AsyncSession = Depends(get_async_session),
    ):
        current_user_id = int(current_user.id)
        current_user_role = _role_value(current_user)
        _detach_current_user(async_session, current_user)
        await enforce_quota_or_429_async(
            async_session,
            user_id=current_user_id,
            user_role=current_user_role,
            event_type=AGENT_QUESTION_EVENT,
        )
        await record_usage_async(async_session, user_id=current_user_id, event_type=AGENT_QUESTION_EVENT, source=config.tag)
        # 后端重启后内存会话索引可能丢失；优先从数据库历史恢复旧 session。
        session_id = await resolve_or_create_session(
            async_session,
            current_user,
            config.tag,
            getattr(req, "session_id", None),
            user_id=str(current_user_id),
            user_role=current_user_role,
        )
        control_reply = maybe_handle_model_control(req.message, config.profile)
        if control_reply:
            await save_message(
                async_session,
                "user",
                req.display_message or req.message,
                session_id,
                attachments=req.attachments,
            )
            await save_message(async_session, "assistant", control_reply, session_id)
            return ChatResponse(reply=control_reply, new_achievements=[])

        workflow_request = _analysis_report_workflow_request(config, req)
        if workflow_request:
            await save_message(
                async_session,
                "user",
                req.display_message or req.message,
                session_id,
                attachments=req.attachments,
            )
            reply = await _run_analysis_report_workflow_reply(workflow_request)
            await save_message(async_session, "assistant", reply, session_id)
            await _record_agent_workspace_artifact_background(
                current_user_id=current_user_id,
                config=config,
                session_id=session_id,
                req=req,
                reply=reply,
            )
            get_session_manager().increment_message_count(session_id)
            return ChatResponse(reply=reply, new_achievements=[])

        factcheck_request = _factcheck_workflow_request(config, req)
        if factcheck_request:
            await save_message(
                async_session,
                "user",
                req.display_message or req.message,
                session_id,
                attachments=req.attachments,
            )
            reply = await _run_factcheck_workflow_reply(factcheck_request)
            await save_message(async_session, "assistant", reply, session_id)
            await _record_agent_workspace_artifact_background(
                current_user_id=current_user_id,
                config=config,
                session_id=session_id,
                req=req,
                reply=reply,
            )
            get_session_manager().increment_message_count(session_id)
            return ChatResponse(reply=reply, new_achievements=[])

        audit_trace_id: str | None = None

        def _capture_answer_audit(record: dict) -> None:
            nonlocal audit_trace_id
            value = str((record or {}).get("trace_id") or "").strip()
            if value:
                audit_trace_id = value

        reply = await collect_chat_reply(
            req.message,
            async_session,
            session_id=session_id,
            profile=config.profile,
            context=req.context,
            display_message=req.display_message,
            attachments=req.attachments,
            answer_audit_callback=_capture_answer_audit,
        )
        await _record_agent_workspace_artifact_background(
            current_user_id=current_user_id,
            config=config,
            session_id=session_id,
            req=req,
            reply=reply,
        )
        get_session_manager().increment_message_count(session_id)
        return ChatResponse(reply=reply, new_achievements=[], audit_trace_id=audit_trace_id)

    async def chat_stream(
        req: ChatRequest,
        request: Request,
        current_user: User = Depends(get_current_user),
        async_session: AsyncSession = Depends(get_async_session),
    ):
        current_user_id = int(current_user.id)
        current_user_role = _role_value(current_user)
        _detach_current_user(async_session, current_user)
        await enforce_quota_or_429_async(
            async_session,
            user_id=current_user_id,
            user_role=current_user_role,
            event_type=AGENT_QUESTION_EVENT,
        )
        await record_usage_async(async_session, user_id=current_user_id, event_type=AGENT_QUESTION_EVENT, source=config.tag)
        # 后端重启后内存会话索引可能丢失；优先从数据库历史恢复旧 session。
        session_id = await resolve_or_create_session(
            async_session,
            current_user,
            config.tag,
            getattr(req, "session_id", None),
            user_id=str(current_user_id),
            user_role=current_user_role,
        )

        async def event_generator():
            control_reply = maybe_handle_model_control(req.message, config.profile)
            if control_reply:
                await save_message(
                    async_session,
                    "user",
                    req.display_message or req.message,
                    session_id,
                    attachments=req.attachments,
                )
                await save_message(async_session, "assistant", control_reply, session_id)
                yield {"event": "delta", "data": json.dumps({"content": control_reply}, ensure_ascii=False)}
                yield {
                    "event": "done",
                    "data": json.dumps({"new_achievements": [], "content": control_reply}, ensure_ascii=False),
                }
                return

            workflow_request = _analysis_report_workflow_request(config, req)
            if workflow_request:
                await save_message(
                    async_session,
                    "user",
                    req.display_message or req.message,
                    session_id,
                    attachments=req.attachments,
                )
                yield {
                    "event": "progress",
                    "data": json.dumps(
                        {
                            "status": "running",
                            "title": "正在生成正式分析报告",
                            "detail": "已切换到 research-pack 报告流水线，正在准备证据包、子智能体 pack 和 14 章报告。",
                            "percent": 8,
                            "source": "workflow",
                        },
                        ensure_ascii=False,
                    ),
                }
                reply = await _run_analysis_report_workflow_reply(workflow_request)
                await save_message(async_session, "assistant", reply, session_id)
                done_payload = await _record_agent_workspace_artifact_background(
                    current_user_id=current_user_id,
                    config=config,
                    session_id=session_id,
                    req=req,
                    reply=reply,
                )
                yield {
                    "event": "done",
                    "data": json.dumps(
                        {"new_achievements": [], "content": reply, **done_payload},
                        ensure_ascii=False,
                    ),
                }
                get_session_manager().increment_message_count(session_id)
                return

            factcheck_request = _factcheck_workflow_request(config, req)
            if factcheck_request:
                await save_message(
                    async_session,
                    "user",
                    req.display_message or req.message,
                    session_id,
                    attachments=req.attachments,
                )
                yield {
                    "event": "progress",
                    "data": json.dumps(
                        {
                            "status": "running",
                            "title": "正在生成事实核查报告",
                            "detail": "已切换到确定性 factcheck 工作流，正在核对数据、公式、证据链和 A 股风险项。",
                            "percent": 10,
                            "source": "workflow",
                        },
                        ensure_ascii=False,
                    ),
                }
                reply = await _run_factcheck_workflow_reply(factcheck_request)
                await save_message(async_session, "assistant", reply, session_id)
                done_payload = await _record_agent_workspace_artifact_background(
                    current_user_id=current_user_id,
                    config=config,
                    session_id=session_id,
                    req=req,
                    reply=reply,
                )
                yield {
                    "event": "done",
                    "data": json.dumps(
                        {"new_achievements": [], "content": reply, **done_payload},
                        ensure_ascii=False,
                    ),
                }
                get_session_manager().increment_message_count(session_id)
                return

            async for event in stream_chat_reply(
                req.message,
                request,
                async_session,
                session_id=session_id,
                profile=config.profile,
                context=req.context,
                display_message=req.display_message,
                attachments=req.attachments,
                done_payload_factory=lambda reply: _record_agent_workspace_artifact_background(
                    current_user_id=current_user_id,
                    config=config,
                    session_id=session_id,
                    req=req,
                    reply=reply,
                ),
                emit_audit_trace_id=True,
            ):
                yield event
            get_session_manager().increment_message_count(session_id)

        return EventSourceResponse(event_generator())

    async def stop_chat(
        session_id: str | None = None,
        current_user: User = Depends(get_current_user),
        async_session: AsyncSession = Depends(get_async_session),
    ):
        resolved_session_id = await resolve_or_create_session(async_session, current_user, config.tag, session_id)
        return await stop_active_run(config.profile, resolved_session_id)

    async def active_chat(
        session_id: str | None = None,
        current_user: User = Depends(get_current_user),
        async_session: AsyncSession = Depends(get_async_session),
    ):
        resolved_session_id = await resolve_or_create_session(async_session, current_user, config.tag, session_id)
        return get_active_run_snapshot(config.profile, resolved_session_id)

    async def active_chat_stream(
        request: Request,
        session_id: str | None = None,
        offset: int = 0,
        current_user: User = Depends(get_current_user),
        async_session: AsyncSession = Depends(get_async_session),
    ):
        resolved_session_id = await resolve_or_create_session(async_session, current_user, config.tag, session_id)
        if not has_active_run(config.profile, resolved_session_id):
            raise HTTPException(status_code=404, detail="No active chat run")
        return EventSourceResponse(
            stream_active_run_events(
                request,
                profile=config.profile,
                session_id=resolved_session_id,
                offset=offset,
            )
        )

    async def chat_history(
        session_id: str | None = None,
        limit: int = HISTORY_LIMIT,
        current_user: User = Depends(get_current_user),
        async_session: AsyncSession = Depends(get_async_session),
    ):
        resolved_session_id = await resolve_or_create_session(async_session, current_user, config.tag, session_id)
        messages = await chat_history_response(async_session, resolved_session_id, limit=limit)
        return {"messages": messages, "session_id": resolved_session_id}

    async def get_chat_answer_audit_trace(
        trace_id: str,
        current_user: User = Depends(get_current_user),
    ):
        if not is_answer_audit_trace_id(trace_id):
            raise HTTPException(status_code=404, detail="Audit trace not found")
        trace = get_answer_audit_trace(trace_id)
        session_id = str((trace or {}).get("session_id") or "")
        if not trace or not session_id.startswith(_user_session_prefix(str(current_user.id), config.tag)):
            raise HTTPException(status_code=404, detail="Audit trace not found")
        return {"trace_id": trace_id, "trace": trace}

    async def chat_sessions(
        limit: int = 100,
        current_user: User = Depends(get_current_user),
        async_session: AsyncSession = Depends(get_async_session),
    ):
        user_id = str(current_user.id)
        requested_limit = _session_limit(current_user, limit)
        _detach_current_user(async_session, current_user)
        session_mgr = get_session_manager()
        redis_sessions = session_mgr.list_user_sessions(user_id, config.tag, limit=requested_limit)
        db_sessions = await _db_session_summaries(
            async_session,
            user_id=user_id,
            profile=config.tag,
            limit=requested_limit,
        )
        sessions = _merge_sessions(redis_sessions, db_sessions, requested_limit)
        current_session_id = session_mgr.get_current_session_id(user_id, config.tag)
        message_summaries = await _session_message_summaries(
            async_session,
            [str(item.get("session_id") or "") for item in sessions],
        )
        return _sessions_payload(sessions, current_session_id, message_summaries)

    async def create_session(
        current_user: User = Depends(get_current_user),
        async_session: AsyncSession = Depends(get_async_session),
    ):
        user_id = str(current_user.id)
        user_role = current_user.role
        _detach_current_user(async_session, current_user)
        session_id, deleted_session_ids = get_session_manager().create_session(
            user_id,
            config.tag,
            user_role=user_role,
            return_deleted=True,
        )
        await _delete_chat_messages(async_session, deleted_session_ids)
        return {"session_id": session_id, "created": True, "deleted_session_ids": deleted_session_ids}

    async def switch_session(
        session_id: str,
        current_user: User = Depends(get_current_user),
        async_session: AsyncSession = Depends(get_async_session),
    ):
        session_mgr = get_session_manager()
        user_id = str(current_user.id)
        user_role = current_user.role
        _detach_current_user(async_session, current_user)
        try:
            _assert_session_belongs_to_user(session_id, user_id, config.tag)
            session_mgr.set_current_session(user_id, config.tag, session_id)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            summary = await _db_session_summary(async_session, session_id)
            if not summary:
                raise
            session_mgr.restore_session(
                user_id,
                config.tag,
                session_id,
                user_role=user_role,
                created_at=summary.get("created_at"),
                updated_at=summary.get("updated_at"),
                message_count=int(summary.get("message_count") or 0),
            )
        return {"session_id": session_id, "current": True}

    async def reset_session(
        session_id: str | None = None,
        current_user: User = Depends(get_current_user),
        async_session: AsyncSession = Depends(get_async_session),
    ):
        user_id = str(current_user.id)
        user_role = current_user.role
        _detach_current_user(async_session, current_user)
        resolved_session_id = await resolve_or_create_session(
            async_session,
            current_user,
            config.tag,
            session_id,
            user_id=user_id,
            user_role=user_role,
        )
        session_mgr = get_session_manager()
        session_mgr.delete_session(resolved_session_id, user_id)
        await _delete_chat_messages(async_session, [resolved_session_id])
        new_session_id, deleted_session_ids = session_mgr.create_session(
            user_id,
            config.tag,
            user_role=user_role,
            return_deleted=True,
        )
        await _delete_chat_messages(async_session, deleted_session_ids)
        return {"session_id": new_session_id, "deleted_old": resolved_session_id}

    endpoint_prefix = config.tag
    router.add_api_route("/chat", _set_endpoint_name(chat, f"{endpoint_prefix}_chat"), methods=["POST"], response_model=ChatResponse)
    router.add_api_route("/chat/stream", _set_endpoint_name(chat_stream, f"{endpoint_prefix}_chat_stream"), methods=["POST"])
    router.add_api_route("/chat/stop", _set_endpoint_name(stop_chat, f"{endpoint_prefix}_stop_chat"), methods=["POST"])
    router.add_api_route("/chat/active", _set_endpoint_name(active_chat, f"{endpoint_prefix}_active_chat"), methods=["GET"])
    router.add_api_route("/chat/active/stream", _set_endpoint_name(active_chat_stream, f"{endpoint_prefix}_active_chat_stream"), methods=["GET"])
    router.add_api_route("/chat/history", _set_endpoint_name(chat_history, f"{endpoint_prefix}_chat_history"), methods=["GET"])
    router.add_api_route(
        "/chat/audit-traces/{trace_id}",
        _set_endpoint_name(get_chat_answer_audit_trace, f"{endpoint_prefix}_chat_answer_audit_trace"),
        methods=["GET"],
        response_model=AnswerAuditTraceResponse,
    )
    router.add_api_route("/chat/sessions", _set_endpoint_name(chat_sessions, f"{endpoint_prefix}_chat_sessions"), methods=["GET"])
    router.add_api_route("/chat/session", _set_endpoint_name(create_session, f"{endpoint_prefix}_create_session"), methods=["POST"])
    router.add_api_route("/chat/session/{session_id}", _set_endpoint_name(switch_session, f"{endpoint_prefix}_switch_session"), methods=["POST"])
    router.add_api_route("/chat/session", _set_endpoint_name(reset_session, f"{endpoint_prefix}_reset_session"), methods=["DELETE"])
    return router
