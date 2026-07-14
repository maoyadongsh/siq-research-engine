"""Meeting-only database dependency with stable post-commit DTO values."""

from collections.abc import AsyncIterator

from database import async_engine
from sqlmodel.ext.asyncio.session import AsyncSession


async def get_meeting_async_session() -> AsyncIterator[AsyncSession]:
    # Meeting repositories commit inside request handlers before DTO mapping.
    # Keep this behavior isolated from every pre-existing API dependency.
    async with AsyncSession(async_engine, expire_on_commit=False) as session:
        yield session


__all__ = ["get_meeting_async_session"]
