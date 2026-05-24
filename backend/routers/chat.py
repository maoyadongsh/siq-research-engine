import json
import asyncio
import uuid
from datetime import datetime
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select
from sqlmodel.ext.asyncio.session import AsyncSession
from sse_starlette.sse import EventSourceResponse

from database import get_session, get_async_session
from models import PetState, ChatMessage, InteractionLog
from schemas import ChatRequest, ChatResponse, AchievementResponse
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
from services.achievement_checker import check_achievements
from routers.pet import apply_decay, perform_action

router = APIRouter(tags=["chat"])

HERMES_SESSION_ID = "finsight-assistant-session"
HERMES_SESSION_PREFIX = "finsight-assistant"


def update_pet_and_achievements(sync_session: Session) -> list[AchievementResponse]:
    """Sync helper: update pet state and check achievements."""
    pet = sync_session.get(PetState, 1)
    apply_decay(pet)
    perform_action(pet, "chat")
    sync_session.add(InteractionLog(action="chat"))
    sync_session.add(pet)
    sync_session.commit()
    return check_achievements(sync_session)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    async_session: AsyncSession = Depends(get_async_session),
):
    control_reply = maybe_handle_model_control(req.message, "finsight_assistant")
    if control_reply:
        await save_message(async_session, "user", req.message, HERMES_SESSION_ID)
        await save_message(async_session, "assistant", control_reply, HERMES_SESSION_ID)
        return ChatResponse(reply=control_reply, new_achievements=[])

    reply = await collect_chat_reply(
        req.message,
        async_session,
        session_id=HERMES_SESSION_ID,
        profile="finsight_assistant",
        context=req.context,
    )

    # Update pet & achievements (sync DB)
    loop = asyncio.get_event_loop()
    with next(get_session()) as sync_session:
        new_achs = await loop.run_in_executor(
            None, update_pet_and_achievements, sync_session
        )

    return ChatResponse(reply=reply, new_achievements=[a.model_dump(mode="json") for a in new_achs])


@router.post("/chat/stream")
async def chat_stream(
    req: ChatRequest,
    request: Request,
    async_session: AsyncSession = Depends(get_async_session),
):
    async def done_payload() -> dict:
        loop = asyncio.get_event_loop()
        with next(get_session()) as sync_session:
            new_achs = await loop.run_in_executor(
                None, update_pet_and_achievements, sync_session
            )
        return {"new_achievements": [a.model_dump(mode="json") for a in new_achs]}

    async def event_generator() -> AsyncGenerator[dict, None]:
        control_reply = maybe_handle_model_control(req.message, "finsight_assistant")
        if control_reply:
            await save_message(async_session, "user", req.message, HERMES_SESSION_ID)
            await save_message(async_session, "assistant", control_reply, HERMES_SESSION_ID)
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
            session_id=HERMES_SESSION_ID,
            profile="finsight_assistant",
            context=req.context,
            done_payload_factory=done_payload,
        ):
            yield event

    return EventSourceResponse(event_generator())


@router.post("/chat/stop")
async def stop_chat():
    return await stop_active_run("finsight_assistant", HERMES_SESSION_ID)


@router.get("/chat/active")
async def active_chat():
    return get_active_run_snapshot("finsight_assistant", HERMES_SESSION_ID)


@router.get("/chat/active/stream")
async def active_chat_stream(request: Request, offset: int = 0):
    if not has_active_run("finsight_assistant", HERMES_SESSION_ID):
        raise HTTPException(status_code=404, detail="No active chat run")
    return EventSourceResponse(
        stream_active_run_events(
            request,
            profile="finsight_assistant",
            session_id=HERMES_SESSION_ID,
            offset=offset,
        )
    )


@router.get("/chat/history")
async def chat_history(
    limit: int = HISTORY_LIMIT,
    async_session: AsyncSession = Depends(get_async_session),
):
    result = await async_session.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id == HERMES_SESSION_ID)
        .order_by(ChatMessage.id.desc())
        .limit(limit)
    )
    messages = list(reversed(result.all()))
    return messages


@router.get("/chat/sessions")
async def chat_sessions(
    async_session: AsyncSession = Depends(get_async_session),
):
    result = await async_session.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id.startswith(HERMES_SESSION_PREFIX))
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
                "current": message.session_id == HERMES_SESSION_ID,
            },
        )
        item["message_count"] += 1
        item["last_message_at"] = message.created_at
        item["preview"] = message.content[:120]
        if not item["title"] and message.role == "user":
            item["title"] = message.content[:48]

    sessions.setdefault(
        HERMES_SESSION_ID,
        {
            "session_id": HERMES_SESSION_ID,
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
        item["current"] = item["session_id"] == HERMES_SESSION_ID
    return sorted(
        sessions.values(),
        key=lambda item: item["last_message_at"] or item["first_message_at"] or datetime.min,
        reverse=True,
    )


@router.post("/chat/session")
async def create_session():
    global HERMES_SESSION_ID
    HERMES_SESSION_ID = f"finsight-assistant-{uuid.uuid4().hex[:8]}"
    return {"session_id": HERMES_SESSION_ID, "created": True}


@router.post("/chat/session/{session_id}")
async def switch_session(
    session_id: str,
    async_session: AsyncSession = Depends(get_async_session),
):
    global HERMES_SESSION_ID
    result = await async_session.exec(
        select(ChatMessage).where(ChatMessage.session_id == session_id).limit(1)
    )
    if not result.first() and session_id != HERMES_SESSION_ID:
        raise HTTPException(status_code=404, detail="Session not found")
    HERMES_SESSION_ID = session_id
    return {"session_id": HERMES_SESSION_ID, "current": True}


@router.delete("/chat/session")
async def reset_session(async_session: AsyncSession = Depends(get_async_session)):
    global HERMES_SESSION_ID
    result = await async_session.exec(
        select(ChatMessage).where(ChatMessage.session_id == HERMES_SESSION_ID)
    )
    for message in result.all():
        await async_session.delete(message)
    await async_session.commit()
    HERMES_SESSION_ID = f"finsight-assistant-{uuid.uuid4().hex[:8]}"
    return {"session_id": HERMES_SESSION_ID, "deleted": True}
