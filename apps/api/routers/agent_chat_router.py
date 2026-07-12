import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncGenerator, Callable

from database import get_async_session
from fastapi import APIRouter, Depends, HTTPException, Request
from models import ChatMessage
from schemas import ChatHistoryMessageResponse, ChatRequest, ChatResponse
from services.agent_chat_runtime import (
    HISTORY_LIMIT,
    chat_history_response,
    chat_message_has_visible_payload,
    collect_chat_reply,
    get_active_run_snapshot,
    has_active_run,
    save_message,
    stop_active_run,
    stream_active_run_events,
    stream_chat_reply,
)
from services.agent_runtime_context import research_identity
from services.hermes_model_control import maybe_handle_model_control
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from sse_starlette.sse import EventSourceResponse


@dataclass(frozen=True)
class AgentChatRouterConfig:
    prefix: str
    tag: str
    profile: str
    initial_session_id: str
    session_prefix: str
    endpoint_name_prefix: str
    on_session_id_change: Callable[[str], None] | None = None


@dataclass
class AgentChatRouterBundle:
    router: APIRouter
    chat: Callable
    chat_stream: Callable
    stop_chat: Callable
    active_chat: Callable
    active_chat_stream: Callable
    chat_history: Callable
    chat_sessions: Callable
    create_session: Callable
    switch_session: Callable
    reset_session: Callable
    get_session_id: Callable[[], str]


class AgentChatSessionState:
    def __init__(
        self,
        initial_session_id: str,
        session_prefix: str,
        on_session_id_change: Callable[[str], None] | None = None,
    ):
        self.session_id = initial_session_id
        self.session_prefix = session_prefix
        self.on_session_id_change = on_session_id_change

    def set_session_id(self, session_id: str) -> str:
        self.session_id = session_id
        if self.on_session_id_change:
            self.on_session_id_change(session_id)
        return self.session_id

    def create_session_id(self) -> str:
        return self.set_session_id(f"{self.session_prefix}-{uuid.uuid4().hex[:8]}")


def _set_endpoint_name(func: Callable, name: str) -> Callable:
    func.__name__ = name
    func.__qualname__ = name
    return func


def create_agent_chat_router(config: AgentChatRouterConfig) -> AgentChatRouterBundle:
    router = APIRouter(prefix=config.prefix, tags=[config.tag])
    state = AgentChatSessionState(config.initial_session_id, config.session_prefix, config.on_session_id_change)

    async def chat(
        req: ChatRequest,
        async_session: AsyncSession = Depends(get_async_session),
    ):
        control_reply = maybe_handle_model_control(req.message, config.profile)
        if control_reply:
            identity = research_identity(req.context)
            identity_kwargs = {"research_identity": identity} if identity else {}
            await save_message(
                async_session,
                "user",
                req.display_message or req.message,
                state.session_id,
                attachments=req.attachments,
                **identity_kwargs,
            )
            await save_message(
                async_session,
                "assistant",
                control_reply,
                state.session_id,
                **identity_kwargs,
            )
            return ChatResponse(reply=control_reply, new_achievements=[])

        reply = await collect_chat_reply(
            req.message,
            async_session,
            session_id=state.session_id,
            profile=config.profile,
            context=req.context,
            display_message=req.display_message,
            attachments=req.attachments,
        )

        return ChatResponse(reply=reply, new_achievements=[])

    async def chat_stream(
        req: ChatRequest,
        request: Request,
        async_session: AsyncSession = Depends(get_async_session),
    ):
        async def event_generator() -> AsyncGenerator[dict, None]:
            control_reply = maybe_handle_model_control(req.message, config.profile)
            if control_reply:
                identity = research_identity(req.context)
                identity_kwargs = {"research_identity": identity} if identity else {}
                await save_message(
                    async_session,
                    "user",
                    req.display_message or req.message,
                    state.session_id,
                    attachments=req.attachments,
                    **identity_kwargs,
                )
                await save_message(
                    async_session,
                    "assistant",
                    control_reply,
                    state.session_id,
                    **identity_kwargs,
                )
                yield {
                    "event": "delta",
                    "data": json.dumps({"content": control_reply}, ensure_ascii=False),
                }
                yield {
                    "event": "done",
                    "data": json.dumps({"new_achievements": [], "content": control_reply}, ensure_ascii=False),
                }
                return

            async for event in stream_chat_reply(
                req.message,
                request,
                async_session,
                session_id=state.session_id,
                profile=config.profile,
                context=req.context,
                display_message=req.display_message,
                attachments=req.attachments,
            ):
                yield event

        return EventSourceResponse(event_generator())

    async def stop_chat():
        return await stop_active_run(config.profile, state.session_id)

    async def active_chat():
        return get_active_run_snapshot(config.profile, state.session_id)

    async def active_chat_stream(request: Request, offset: int = 0):
        if not has_active_run(config.profile, state.session_id):
            raise HTTPException(status_code=404, detail="No active chat run")
        return EventSourceResponse(
            stream_active_run_events(
                request,
                profile=config.profile,
                session_id=state.session_id,
                offset=offset,
            )
        )

    async def chat_history(
        limit: int = HISTORY_LIMIT,
        async_session: AsyncSession = Depends(get_async_session),
    ):
        return await chat_history_response(async_session, state.session_id, limit=limit)

    async def chat_sessions(
        async_session: AsyncSession = Depends(get_async_session),
    ):
        result = await async_session.exec(
            select(ChatMessage)
            .where(ChatMessage.session_id.startswith(state.session_prefix))
            .order_by(ChatMessage.id)
        )
        sessions: dict[str, dict] = {}
        for message in result.all():
            if not chat_message_has_visible_payload(message):
                continue
            item = sessions.setdefault(
                message.session_id,
                {
                    "session_id": message.session_id,
                    "title": "",
                    "preview": "",
                    "message_count": 0,
                    "first_message_at": message.created_at,
                    "last_message_at": message.created_at,
                    "current": message.session_id == state.session_id,
                },
            )
            item["message_count"] += 1
            item["last_message_at"] = message.created_at
            item["preview"] = message.content[:120]
            if not item["title"] and message.role == "user":
                item["title"] = message.content[:48]

        for item in sessions.values():
            item["title"] = item["title"] or item["preview"] or "未命名会话"
            item["current"] = item["session_id"] == state.session_id
        return sorted(
            sessions.values(),
            key=lambda item: item["last_message_at"] or item["first_message_at"] or datetime.min,
            reverse=True,
        )

    async def create_session():
        return {"session_id": state.create_session_id(), "created": True}

    async def switch_session(
        session_id: str,
        async_session: AsyncSession = Depends(get_async_session),
    ):
        result = await async_session.exec(
            select(ChatMessage).where(ChatMessage.session_id == session_id).limit(1)
        )
        if not result.first() and session_id != state.session_id:
            raise HTTPException(status_code=404, detail="Session not found")
        return {"session_id": state.set_session_id(session_id), "current": True}

    async def reset_session(async_session: AsyncSession = Depends(get_async_session)):
        result = await async_session.exec(
            select(ChatMessage).where(ChatMessage.session_id == state.session_id)
        )
        for message in result.all():
            await async_session.delete(message)
        await async_session.commit()
        return {"session_id": state.create_session_id(), "deleted": True}

    endpoint_prefix = config.endpoint_name_prefix
    chat = _set_endpoint_name(chat, f"{endpoint_prefix}_chat")
    chat_stream = _set_endpoint_name(chat_stream, f"{endpoint_prefix}_chat_stream")
    stop_chat = _set_endpoint_name(stop_chat, f"{endpoint_prefix}_stop_chat")
    active_chat = _set_endpoint_name(active_chat, f"{endpoint_prefix}_active_chat")
    active_chat_stream = _set_endpoint_name(active_chat_stream, f"{endpoint_prefix}_active_chat_stream")
    chat_history = _set_endpoint_name(chat_history, f"{endpoint_prefix}_chat_history")
    chat_sessions = _set_endpoint_name(chat_sessions, f"{endpoint_prefix}_chat_sessions")
    create_session = _set_endpoint_name(create_session, f"{endpoint_prefix}_create_session")
    switch_session = _set_endpoint_name(switch_session, f"{endpoint_prefix}_switch_session")
    reset_session = _set_endpoint_name(reset_session, f"{endpoint_prefix}_reset_session")

    router.add_api_route("/chat", chat, methods=["POST"], response_model=ChatResponse)
    router.add_api_route("/chat/stream", chat_stream, methods=["POST"])
    router.add_api_route("/chat/stop", stop_chat, methods=["POST"])
    router.add_api_route("/chat/active", active_chat, methods=["GET"])
    router.add_api_route("/chat/active/stream", active_chat_stream, methods=["GET"])
    router.add_api_route(
        "/chat/history",
        chat_history,
        methods=["GET"],
        response_model=list[ChatHistoryMessageResponse],
    )
    router.add_api_route("/chat/sessions", chat_sessions, methods=["GET"])
    router.add_api_route("/chat/session", create_session, methods=["POST"])
    router.add_api_route("/chat/session/{session_id}", switch_session, methods=["POST"])
    router.add_api_route("/chat/session", reset_session, methods=["DELETE"])

    return AgentChatRouterBundle(
        router=router,
        chat=chat,
        chat_stream=chat_stream,
        stop_chat=stop_chat,
        active_chat=active_chat,
        active_chat_stream=active_chat_stream,
        chat_history=chat_history,
        chat_sessions=chat_sessions,
        create_session=create_session,
        switch_session=switch_session,
        reset_session=reset_session,
        get_session_id=lambda: state.session_id,
    )
