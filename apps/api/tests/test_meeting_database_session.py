import anyio
import database
from sqlalchemy.ext.asyncio import create_async_engine

from services import meeting_database


def test_meeting_async_session_keeps_committed_values_loaded_without_changing_global_default(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        monkeypatch.setattr(meeting_database, "async_engine", engine)
        meeting_dependency = meeting_database.get_meeting_async_session()
        meeting_session = await anext(meeting_dependency)
        try:
            assert meeting_session.sync_session.expire_on_commit is False
        finally:
            await meeting_dependency.aclose()

        monkeypatch.setattr(database, "async_engine", engine)
        global_dependency = database.get_async_session()
        global_session = await anext(global_dependency)
        try:
            assert global_session.sync_session.expire_on_commit is True
        finally:
            await global_dependency.aclose()
            await engine.dispose()

    anyio.run(run)
