import anyio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from models import ChatMessage
from routers import chat
from schemas import ChatRequest
from services.auth_service import User, UserRole
from services.usage_service import AGENT_QUESTION_EVENT, UsageEvent


class _SessionManager:
    def __init__(self):
        self.message_count = 0

    def get_current_session_id(self, *args, **kwargs):
        return None

    def create_session(self, user_id, profile, **kwargs):
        return f"user-{user_id}-{profile}-new"

    def increment_message_count(self, session_id):
        self.message_count += 1


async def _with_chat_db(tmp_path, callback):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'chat-route.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with AsyncSession(engine) as session:
            await callback(session)
    finally:
        await engine.dispose()


async def _add_user(session: AsyncSession) -> User:
    user = User(
        username="analyst",
        email="analyst@example.test",
        full_name="Analyst",
        hashed_password="x",
        role=UserRole.ANALYST,
        is_active=True,
        approval_status="approved",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def test_chat_route_records_usage_without_expiring_current_user(monkeypatch, tmp_path):
    async def run_case(session):
        session_manager = _SessionManager()
        user = await _add_user(session)
        monkeypatch.setattr(chat, "get_session_manager", lambda: session_manager)
        monkeypatch.setattr(chat, "maybe_handle_model_control", lambda message, profile: "模型状态正常")

        response = await chat.chat(
            ChatRequest(message="/model status"),
            current_user=user,
            async_session=session,
        )

        usage = (await session.exec(select(UsageEvent).where(UsageEvent.event_type == AGENT_QUESTION_EVENT))).one()
        messages = (await session.exec(select(ChatMessage).order_by(ChatMessage.id))).all()

        assert response.reply == "模型状态正常"
        assert usage.user_id == user.id
        assert usage.source == "assistant"
        assert [message.role for message in messages] == ["user", "assistant"]
        assert messages[0].session_id == "user-1-assistant-new"
        assert session_manager.message_count == 1

    anyio.run(_with_chat_db, tmp_path, run_case)


def test_chat_stream_records_usage_before_returning_sse(monkeypatch, tmp_path):
    async def run_case(session):
        user = await _add_user(session)
        monkeypatch.setattr(chat, "get_session_manager", lambda: _SessionManager())

        response = await chat.chat_stream(
            ChatRequest(message="请分析财报"),
            request=object(),
            current_user=user,
            async_session=session,
        )

        usage = (await session.exec(select(UsageEvent).where(UsageEvent.event_type == AGENT_QUESTION_EVENT))).one()

        assert response is not None
        assert usage.user_id == user.id
        assert usage.source == "assistant"

    anyio.run(_with_chat_db, tmp_path, run_case)
