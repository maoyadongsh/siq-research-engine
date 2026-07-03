import anyio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from models import Achievement, AgentState, InteractionLog
from routers import chat


async def _seed_chat_achievement_db(engine):
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)

    async with AsyncSession(engine) as session:
        session.add(AgentState(id=1))
        session.add(
            Achievement(
                id="first_chat",
                name="First chat",
                description="Send one chat message",
                target=1,
            )
        )
        await session.commit()


async def _chat_achievement_snapshot(engine):
    async with AsyncSession(engine) as session:
        chat_count = (
            await session.exec(
                select(func.count()).where(InteractionLog.action == "chat")
            )
        ).one()
        first_chat = (
            await session.exec(
                select(Achievement).where(Achievement.id == "first_chat")
            )
        ).one()
        agent = await session.get(AgentState, 1)
        return chat_count, first_chat, agent


def test_chat_achievement_update_uses_async_session(tmp_path):
    async def run_case():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'chat-achievements.db'}")
        try:
            await _seed_chat_achievement_db(engine)
            async with AsyncSession(engine) as session:
                new_achievements = await chat.update_agent_and_achievements(session)

            chat_count, first_chat, agent = await _chat_achievement_snapshot(engine)

            assert chat_count == 1
            assert agent is not None
            assert agent.xp == 10
            assert first_chat.progress == 1
            assert first_chat.unlocked_at is not None
            assert [item.id for item in new_achievements] == ["first_chat"]
        finally:
            await engine.dispose()

    anyio.run(run_case)


def test_stream_done_payload_achievement_update_opens_fresh_async_session(tmp_path, monkeypatch):
    async def run_case():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stream-done-achievements.db'}")
        monkeypatch.setattr(chat, "async_engine", engine)
        try:
            await _seed_chat_achievement_db(engine)

            new_achievements = await chat.update_agent_and_achievements_in_new_session()
            chat_count, first_chat, agent = await _chat_achievement_snapshot(engine)

            assert chat_count == 1
            assert agent is not None
            assert agent.xp == 10
            assert first_chat.progress == 1
            assert first_chat.unlocked_at is not None
            assert [item.id for item in new_achievements] == ["first_chat"]
        finally:
            await engine.dispose()

    anyio.run(run_case)
