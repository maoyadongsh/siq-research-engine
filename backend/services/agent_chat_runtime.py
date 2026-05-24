import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

import httpx
from fastapi import Request
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from database import async_engine
from models import ChatMessage
from services.citation_links import append_missing_pdf_source_links
from services.hermes_client import HermesProfile, collect_run_result, create_run, stop_run, stream_run

HISTORY_LIMIT = 10
STREAM_TIMEOUT_SECONDS = 480
READ_TIMEOUT_SECONDS = 120
STOPPED_MESSAGE = "[已停止] 本次对话已停止，后台 Hermes run 已收到停止请求。"
TIMEOUT_MESSAGE = "[已停止] 本次对话超过网页聊天时限，已自动停止。长时间任务建议改用后台工作流。"
CONTEXT_HEADER = (
    "以下是本会话的默认上下文，只用于用户没有明确指定公司、证券代码、报告或主题时补全指代。"
    "如果用户问题或会话历史里指定了其他公司/代码/报告/行业/主题，或明显是在问通用问题，必须优先按用户问题和会话历史回答，不要强行套用默认公司。"
)

@dataclass
class ActiveRunState:
    profile: HermesProfile
    session_id: str
    run_id: str
    status: str = "running"
    content: str = ""
    events: list[dict[str, str]] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    error: str | None = None
    done_payload: dict[str, Any] | None = None
    stop_requested: bool = False
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task | None = None


ACTIVE_RUNS: dict[tuple[HermesProfile, str], ActiveRunState] = {}
SESSION_DEFAULT_CONTEXTS: dict[tuple[HermesProfile, str], str] = {}


def _active_key(profile: HermesProfile, session_id: str) -> tuple[HermesProfile, str]:
    return (profile, session_id)


async def _append_state_event(
    state: ActiveRunState,
    event_name: str,
    payload: dict[str, Any],
) -> None:
    if event_name == "delta":
        state.content += str(payload.get("content", ""))
    elif event_name == "done":
        state.status = "completed"
        state.done_payload = payload
    elif event_name == "error":
        state.status = "failed"
        state.error = str(payload.get("message") or payload.get("content") or "Unknown error")

    state.updated_at = datetime.utcnow()
    event = {
        "event": event_name,
        "data": json.dumps(payload, ensure_ascii=False),
    }
    async with state.condition:
        state.events.append(event)
        state.condition.notify_all()


def get_active_run_snapshot(profile: HermesProfile, session_id: str) -> dict[str, Any]:
    state = ACTIVE_RUNS.get(_active_key(profile, session_id))
    if not state:
        return {"running": False}

    return {
        "running": state.status == "running",
        "status": state.status,
        "run_id": state.run_id,
        "session_id": state.session_id,
        "content": state.content,
        "event_count": len(state.events),
        "started_at": state.started_at.isoformat(),
        "updated_at": state.updated_at.isoformat(),
        "error": state.error,
    }


def has_active_run(profile: HermesProfile, session_id: str) -> bool:
    state = ACTIVE_RUNS.get(_active_key(profile, session_id))
    return bool(state and state.status == "running")


async def stream_active_run_events(
    request: Request,
    *,
    profile: HermesProfile,
    session_id: str,
    offset: int = 0,
) -> AsyncGenerator[dict[str, str], None]:
    state = ACTIVE_RUNS.get(_active_key(profile, session_id))
    if not state:
        return

    next_index = max(0, offset)
    while True:
        if await request.is_disconnected():
            return

        async with state.condition:
            if next_index >= len(state.events) and state.status == "running":
                try:
                    await asyncio.wait_for(state.condition.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass

            pending = state.events[next_index:]
            is_terminal = state.status != "running"

        for event in pending:
            yield event
            next_index += 1

        if is_terminal and next_index >= len(state.events):
            return


def normalize_history(messages: list[ChatMessage], limit: int = HISTORY_LIMIT) -> list[dict]:
    normalized: list[dict] = []
    for message in messages:
        if message.role not in {"user", "assistant"}:
            continue
        item = {"role": message.role, "content": message.content}
        if normalized and normalized[-1]["role"] == message.role:
            normalized[-1] = item
        else:
            normalized.append(item)

    while normalized and normalized[0]["role"] != "user":
        normalized.pop(0)
    return normalized[-limit:]


async def load_history(
    async_session: AsyncSession,
    session_id: str,
    *,
    limit: int = HISTORY_LIMIT,
) -> list[dict]:
    result = await async_session.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id.desc())
        .limit(limit * 3)
    )
    return normalize_history(list(reversed(result.all())), limit=limit)


async def save_message(
    async_session: AsyncSession,
    role: str,
    content: str,
    session_id: str,
) -> None:
    msg = ChatMessage(role=role, content=content, session_id=session_id)
    async_session.add(msg)
    await async_session.commit()


async def save_message_in_background(role: str, content: str, session_id: str) -> None:
    async with AsyncSession(async_engine) as async_session:
        await save_message(async_session, role, content, session_id)


def _clean_context_value(value: Any) -> str:
    return str(value).replace("\n", " ").strip()


def format_chat_context(context: Any | None) -> str | None:
    if not context:
        return None

    if hasattr(context, "model_dump"):
        raw = context.model_dump(exclude_none=True)
    elif isinstance(context, dict):
        raw = context
    else:
        return None

    lines: list[str] = []
    company = raw.get("company") or {}
    report = raw.get("report") or {}
    page = raw.get("page") or {}

    company_parts: list[str] = []
    if company.get("name"):
        company_parts.append(_clean_context_value(company["name"]))
    if company.get("code"):
        company_parts.append(f"代码 {_clean_context_value(company['code'])}")
    if company.get("dir"):
        company_parts.append(f"目录 {_clean_context_value(company['dir'])}")
    if company_parts:
        lines.append(f"- 当前公司: {' / '.join(company_parts)}")

    report_parts: list[str] = []
    if report.get("title"):
        report_parts.append(_clean_context_value(report["title"]))
    if report.get("type"):
        report_parts.append(f"类型 {_clean_context_value(report['type'])}")
    if report.get("filename"):
        report_parts.append(f"文件 {_clean_context_value(report['filename'])}")
    if report.get("mtime"):
        report_parts.append(f"更新时间 {_clean_context_value(report['mtime'])}")
    if report.get("url"):
        report_parts.append(f"URL {_clean_context_value(report['url'])}")
    if report_parts:
        lines.append(f"- 当前报告: {' / '.join(report_parts)}")

    if page.get("title"):
        lines.append(f"- 当前页面: {_clean_context_value(page['title'])}")

    if not lines:
        return None

    return "\n".join([CONTEXT_HEADER, *lines])


def get_session_default_context(
    profile: HermesProfile,
    session_id: str,
    context: Any | None = None,
    *,
    allow_initialize: bool = False,
) -> str | None:
    key = _active_key(profile, session_id)
    if key in SESSION_DEFAULT_CONTEXTS:
        return SESSION_DEFAULT_CONTEXTS[key]

    if not allow_initialize:
        return None

    formatted_context = format_chat_context(context)
    if formatted_context:
        SESSION_DEFAULT_CONTEXTS[key] = formatted_context
    return formatted_context


def build_session_contextual_input(
    message: str,
    *,
    profile: HermesProfile,
    session_id: str,
    context: Any | None = None,
    allow_initialize: bool = False,
) -> str:
    default_context = get_session_default_context(
        profile,
        session_id,
        context,
        allow_initialize=allow_initialize,
    )
    if not default_context:
        return message
    return f"{default_context}\n\n用户问题：{message}"


def hermes_timeout() -> httpx.Timeout:
    return httpx.Timeout(
        READ_TIMEOUT_SECONDS,
        connect=10.0,
        read=READ_TIMEOUT_SECONDS,
    )


async def collect_chat_reply(
    message: str,
    async_session: AsyncSession,
    *,
    session_id: str,
    profile: HermesProfile,
    context: Any | None = None,
    history_limit: int = HISTORY_LIMIT,
) -> str:
    history = await load_history(async_session, session_id, limit=history_limit)
    await save_message(async_session, "user", message, session_id)

    run_id = await create_run(
        build_session_contextual_input(
            message,
            profile=profile,
            session_id=session_id,
            context=context,
            allow_initialize=not history,
        ),
        history,
        profile=profile,
    )
    state = ActiveRunState(profile=profile, session_id=session_id, run_id=run_id)
    ACTIVE_RUNS[_active_key(profile, session_id)] = state
    try:
        reply = await asyncio.wait_for(
            collect_run_result(run_id, profile=profile, timeout=hermes_timeout()),
            timeout=STREAM_TIMEOUT_SECONDS,
        )
    except (asyncio.TimeoutError, httpx.TimeoutException):
        await stop_run(run_id, profile=profile)
        reply = TIMEOUT_MESSAGE
    finally:
        ACTIVE_RUNS.pop(_active_key(profile, session_id), None)

    reply = append_missing_pdf_source_links(reply)
    await save_message(async_session, "assistant", reply, session_id)
    return reply


async def _collect_stream_run(
    state: ActiveRunState,
    done_payload_factory: Callable[[], Awaitable[dict]] | None,
) -> None:
    full_reply = ""
    failed = False
    try:
        async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
            async for ev in stream_run(state.run_id, profile=state.profile, timeout=hermes_timeout()):
                if ev.type == "delta":
                    full_reply += ev.text
                    await _append_state_event(state, "delta", {"content": ev.text})
                elif ev.type == "tool.started":
                    await _append_state_event(
                        state,
                        "tool",
                        {"status": "started", "tool": ev.tool, "preview": ev.preview},
                    )
                elif ev.type == "tool.completed":
                    await _append_state_event(
                        state,
                        "tool",
                        {
                            "status": "completed",
                            "tool": ev.tool,
                            "duration": ev.duration,
                            "error": ev.error,
                        },
                    )
                elif ev.type == "reasoning":
                    await _append_state_event(state, "reasoning", {"text": ev.text})
                elif ev.type == "done":
                    if ev.text and not full_reply:
                        full_reply = ev.text
                        await _append_state_event(state, "delta", {"content": ev.text})
                    elif ev.text and ev.text.startswith(full_reply):
                        suffix = ev.text[len(full_reply):]
                        if suffix:
                            full_reply = ev.text
                            await _append_state_event(state, "delta", {"content": suffix})
                    break
    except asyncio.TimeoutError:
        await stop_run(state.run_id, profile=state.profile)
        timeout_delta = f"\n\n{TIMEOUT_MESSAGE}" if full_reply else TIMEOUT_MESSAGE
        full_reply = f"{full_reply}{timeout_delta}" if full_reply else timeout_delta
        await _append_state_event(state, "delta", {"content": timeout_delta})
    except httpx.TimeoutException:
        await stop_run(state.run_id, profile=state.profile)
        timeout_delta = f"\n\n{TIMEOUT_MESSAGE}" if full_reply else TIMEOUT_MESSAGE
        full_reply = f"{full_reply}{timeout_delta}" if full_reply else timeout_delta
        await _append_state_event(state, "delta", {"content": timeout_delta})
    except Exception as exc:
        failed = True
        error_text = f"\n\n[错误] {exc}"
        full_reply = f"{full_reply}{error_text}" if full_reply else error_text.strip()
        await _append_state_event(state, "delta", {"content": error_text})
        await _append_state_event(state, "error", {"message": str(exc)})
    finally:
        try:
            if state.stop_requested and not full_reply:
                full_reply = STOPPED_MESSAGE
                await _append_state_event(state, "delta", {"content": STOPPED_MESSAGE})

            if full_reply:
                reply = append_missing_pdf_source_links(full_reply)
                citation_addition = reply[len(full_reply):]
                if citation_addition:
                    full_reply = reply
                    await _append_state_event(state, "delta", {"content": citation_addition})
                await save_message_in_background("assistant", reply, state.session_id)

            if not failed:
                try:
                    done_payload = await done_payload_factory() if done_payload_factory else {"new_achievements": []}
                except Exception as exc:
                    done_payload = {"new_achievements": [], "warning": str(exc)}
                await _append_state_event(state, "done", done_payload)
        finally:
            ACTIVE_RUNS.pop(_active_key(state.profile, state.session_id), None)


async def stream_chat_reply(
    message: str,
    request: Request,
    async_session: AsyncSession,
    *,
    session_id: str,
    profile: HermesProfile,
    context: Any | None = None,
    history_limit: int = HISTORY_LIMIT,
    done_payload_factory: Callable[[], Awaitable[dict]] | None = None,
) -> AsyncGenerator[dict, None]:
    if has_active_run(profile, session_id):
        async for event in stream_active_run_events(
            request,
            profile=profile,
            session_id=session_id,
        ):
            yield event
        return

    history = await load_history(async_session, session_id, limit=history_limit)
    await save_message(async_session, "user", message, session_id)

    run_id = await create_run(
        build_session_contextual_input(
            message,
            profile=profile,
            session_id=session_id,
            context=context,
            allow_initialize=not history,
        ),
        history,
        profile=profile,
    )
    state = ActiveRunState(profile=profile, session_id=session_id, run_id=run_id)
    ACTIVE_RUNS[_active_key(profile, session_id)] = state
    await _append_state_event(state, "run", {"run_id": run_id})
    state.task = asyncio.create_task(_collect_stream_run(state, done_payload_factory))

    async for event in stream_active_run_events(
        request,
        profile=profile,
        session_id=session_id,
    ):
        yield event


async def stop_active_run(profile: HermesProfile, session_id: str) -> dict:
    state = ACTIVE_RUNS.get(_active_key(profile, session_id))
    if not state:
        return {"stopped": False, "detail": "No active Hermes run"}
    state.stop_requested = True
    result = await stop_run(state.run_id, profile=profile)
    return {"stopped": True, "run_id": state.run_id, "hermes": result}
