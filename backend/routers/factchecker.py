import json
import uuid
from datetime import datetime
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from sse_starlette.sse import EventSourceResponse

from database import get_async_session
from models import ChatMessage
from schemas import ChatRequest, ChatResponse
from services.agent_chat_runtime import (
    HISTORY_LIMIT,
    collect_chat_reply,
    get_active_run_snapshot,
    has_active_run,
    save_message,
    stop_active_run,
    stream_active_run_events,
    stream_chat_reply,
)
from services.hermes_model_control import maybe_handle_model_control

router = APIRouter(prefix="/factchecker", tags=["factchecker"])

FACTCHECKER_SESSION_ID = "finsight-factchecker-session"
FACTCHECKER_SESSION_PREFIX = "finsight-factchecker"


@router.post("/chat", response_model=ChatResponse)
async def factchecker_chat(
    req: ChatRequest,
    async_session: AsyncSession = Depends(get_async_session),
):
    control_reply = maybe_handle_model_control(req.message, "factchecker")
    if control_reply:
        await save_message(async_session, "user", req.message, FACTCHECKER_SESSION_ID)
        await save_message(async_session, "assistant", control_reply, FACTCHECKER_SESSION_ID)
        return ChatResponse(reply=control_reply, new_achievements=[])

    reply = await collect_chat_reply(
        req.message,
        async_session,
        session_id=FACTCHECKER_SESSION_ID,
        profile="factchecker",
        context=req.context,
    )

    return ChatResponse(reply=reply, new_achievements=[])


@router.post("/chat/stream")
async def factchecker_chat_stream(
    req: ChatRequest,
    request: Request,
    async_session: AsyncSession = Depends(get_async_session),
):
    async def event_generator() -> AsyncGenerator[dict, None]:
        control_reply = maybe_handle_model_control(req.message, "factchecker")
        if control_reply:
            await save_message(async_session, "user", req.message, FACTCHECKER_SESSION_ID)
            await save_message(async_session, "assistant", control_reply, FACTCHECKER_SESSION_ID)
            yield {
                "event": "delta",
                "data": json.dumps({"content": control_reply}, ensure_ascii=False),
            }
            yield {
                "event": "done",
                "data": json.dumps({"new_achievements": []}, ensure_ascii=False),
            }
            return

        async for event in stream_chat_reply(
            req.message,
            request,
            async_session,
            session_id=FACTCHECKER_SESSION_ID,
            profile="factchecker",
            context=req.context,
        ):
            yield event

    return EventSourceResponse(event_generator())


@router.post("/chat/stop")
async def factchecker_stop_chat():
    return await stop_active_run("factchecker", FACTCHECKER_SESSION_ID)


@router.get("/chat/active")
async def factchecker_active_chat():
    return get_active_run_snapshot("factchecker", FACTCHECKER_SESSION_ID)


@router.get("/chat/active/stream")
async def factchecker_active_chat_stream(request: Request, offset: int = 0):
    if not has_active_run("factchecker", FACTCHECKER_SESSION_ID):
        raise HTTPException(status_code=404, detail="No active chat run")
    return EventSourceResponse(
        stream_active_run_events(
            request,
            profile="factchecker",
            session_id=FACTCHECKER_SESSION_ID,
            offset=offset,
        )
    )


@router.get("/chat/history")
async def factchecker_chat_history(
    limit: int = HISTORY_LIMIT,
    async_session: AsyncSession = Depends(get_async_session),
):
    result = await async_session.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id == FACTCHECKER_SESSION_ID)
        .order_by(ChatMessage.id.desc())
        .limit(limit)
    )
    messages = list(reversed(result.all()))
    return messages


@router.get("/chat/sessions")
async def factchecker_chat_sessions(
    async_session: AsyncSession = Depends(get_async_session),
):
    result = await async_session.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id.startswith(FACTCHECKER_SESSION_PREFIX))
        .order_by(ChatMessage.id)
    )
    sessions: dict[str, dict] = {}
    for message in result.all():
        item = sessions.setdefault(
            message.session_id,
            {
                "session_id": message.session_id,
                "title": "",
                "preview": "",
                "message_count": 0,
                "first_message_at": message.created_at,
                "last_message_at": message.created_at,
                "current": message.session_id == FACTCHECKER_SESSION_ID,
            },
        )
        item["message_count"] += 1
        item["last_message_at"] = message.created_at
        item["preview"] = message.content[:120]
        if not item["title"] and message.role == "user":
            item["title"] = message.content[:48]

    sessions.setdefault(
        FACTCHECKER_SESSION_ID,
        {
            "session_id": FACTCHECKER_SESSION_ID,
            "title": "当前空会话",
            "preview": "",
            "message_count": 0,
            "first_message_at": None,
            "last_message_at": None,
            "current": True,
        },
    )
    for item in sessions.values():
        item["title"] = item["title"] or item["preview"] or "未命名会话"
        item["current"] = item["session_id"] == FACTCHECKER_SESSION_ID
    return sorted(
        sessions.values(),
        key=lambda item: item["last_message_at"] or item["first_message_at"] or datetime.min,
        reverse=True,
    )


@router.post("/chat/session")
async def factchecker_create_session():
    global FACTCHECKER_SESSION_ID
    FACTCHECKER_SESSION_ID = f"finsight-factchecker-{uuid.uuid4().hex[:8]}"
    return {"session_id": FACTCHECKER_SESSION_ID, "created": True}


@router.post("/chat/session/{session_id}")
async def factchecker_switch_session(
    session_id: str,
    async_session: AsyncSession = Depends(get_async_session),
):
    global FACTCHECKER_SESSION_ID
    result = await async_session.exec(
        select(ChatMessage).where(ChatMessage.session_id == session_id).limit(1)
    )
    if not result.first() and session_id != FACTCHECKER_SESSION_ID:
        raise HTTPException(status_code=404, detail="Session not found")
    FACTCHECKER_SESSION_ID = session_id
    return {"session_id": FACTCHECKER_SESSION_ID, "current": True}


@router.delete("/chat/session")
async def factchecker_reset_session(async_session: AsyncSession = Depends(get_async_session)):
    global FACTCHECKER_SESSION_ID
    result = await async_session.exec(
        select(ChatMessage).where(ChatMessage.session_id == FACTCHECKER_SESSION_ID)
    )
    for message in result.all():
        await async_session.delete(message)
    await async_session.commit()
    FACTCHECKER_SESSION_ID = f"finsight-factchecker-{uuid.uuid4().hex[:8]}"
    return {"session_id": FACTCHECKER_SESSION_ID, "deleted": True}
