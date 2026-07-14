# ruff: noqa: B008

import anyio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers.meeting_stream import router
from services.auth_dependencies import get_current_user
from services.auth_service import User, UserRole
from services.meeting_audio_store import MeetingAudioStore
from services.meeting_contracts import MEETING_TABLES, MeetingCreateRequest
from services.meeting_database import get_meeting_async_session as get_async_session
from services.meeting_repository import MeetingRepository
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession


def _user(user_id: int) -> User:
    return User(
        id=user_id,
        username=f"audio-{user_id}",
        email=f"audio-{user_id}@example.test",
        hashed_password="x",
        full_name=f"Audio {user_id}",
        role=UserRole.ANALYST,
        is_active=True,
        approval_status="approved",
    )


def test_audio_playback_ticket_supports_range_without_bearer(tmp_path, monkeypatch):
    database_path = tmp_path / "audio-replay.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    audio_root = tmp_path / "meeting-audio"
    monkeypatch.setenv("SIQ_MEETINGS_ENABLED", "true")
    monkeypatch.setenv("SIQ_MEETINGS_ASR_ENABLED", "false")
    monkeypatch.setenv("SIQ_MEETINGS_AUDIO_ROOT", str(audio_root))

    async def prepare():
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SQLModel.metadata.create_all(
                    sync_connection,
                    tables=[User.__table__, *[model.__table__ for model in MEETING_TABLES]],
                )
            )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            repository = MeetingRepository(session)
            meeting, _, _ = await repository.create_session(7, MeetingCreateRequest(title="回放会议"))
            meeting, _, _ = await repository.transition_session(meeting.id, 7, "start")
            meeting.stream_epoch = 1
            session.add(meeting)
            await session.commit()
            await repository.transition_session(meeting.id, 7, "mark_live")
            payload = b"\x01\x00" * 1_600
            persisted = MeetingAudioStore(audio_root).persist_chunk(7, meeting.id, 1, 0, payload)
            await repository.register_audio_chunk(
                meeting.id,
                7,
                stream_epoch=1,
                sequence=0,
                start_ms=0,
                duration_ms=100,
                storage_key=persisted.storage_key,
                sha256=persisted.sha256,
                byte_size=persisted.byte_size,
            )
            await repository.transition_session(meeting.id, 7, "stop")
            await repository.transition_session(meeting.id, 7, "mark_stopped")
            return meeting.id

    meeting_id = anyio.run(prepare)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    active_user = {"value": _user(7)}

    async def current_user():
        return active_user["value"]

    async def session_dependency():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_async_session] = session_dependency
    client = TestClient(app)

    # Playback requests never package a potentially multi-hour recording in
    # the API process. The stop/finalization worker must publish it first.
    unavailable = client.post(
        f"/api/meetings/v1/sessions/{meeting_id}/audio-ticket",
        headers={"Origin": "http://testserver"},
    )
    assert unavailable.status_code == 404
    assert unavailable.json()["detail"]["code"] == "AUDIO_NOT_AVAILABLE"
    assert MeetingAudioStore(audio_root).ready_packed_audio_path(7, meeting_id) is None

    async def package_audio():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            chunks = await MeetingRepository(session).list_audio_chunks(meeting_id, 7)
        MeetingAudioStore(audio_root).pack_wav(7, meeting_id, chunks)

    anyio.run(package_audio)

    issued = client.post(
        f"/api/meetings/v1/sessions/{meeting_id}/audio-ticket",
        headers={"Origin": "http://testserver"},
    )
    assert issued.status_code == 200
    assert MeetingAudioStore(audio_root).ready_packed_audio_path(7, meeting_id) is not None
    audio_url = issued.json()["audio_url"]
    assert audio_url.startswith(f"/api/meetings/v1/sessions/{meeting_id}/audio?")
    assert "127.0.0.1" not in audio_url
    assert "playback_ticket=" in audio_url

    ranged = client.get(audio_url, headers={"Range": "bytes=0-31"})
    assert ranged.status_code == 206
    assert ranged.headers["accept-ranges"] == "bytes"
    assert ranged.headers["content-range"].startswith("bytes 0-31/")
    assert len(ranged.content) == 32
    assert ranged.headers["cache-control"] == "private, no-store"
    assert ranged.headers["content-disposition"].startswith("inline;")

    # The playback URL remains usable for later seeks during its short TTL.
    second_range = client.get(audio_url, headers={"Range": "bytes=32-63"})
    assert second_range.status_code == 206
    assert len(second_range.content) == 32

    active_user["value"] = _user(8)
    hidden = client.post(
        f"/api/meetings/v1/sessions/{meeting_id}/audio-ticket",
        headers={"Origin": "http://testserver"},
    )
    assert hidden.status_code == 404
    invalid = client.get(
        f"/api/meetings/v1/sessions/{meeting_id}/audio",
        params={"playback_ticket": "x" * 32},
    )
    assert invalid.status_code == 404
    anyio.run(engine.dispose)


def test_packed_audio_never_follows_symlink(tmp_path):
    root = tmp_path / "meeting-audio"
    store = MeetingAudioStore(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "meeting.wav"
    secret.write_bytes(b"RIFF" + bytes(4) + b"WAVE" + bytes(64))
    meeting_root = root / "7" / "meeting-safe"
    meeting_root.mkdir(parents=True)
    (meeting_root / "audio").symlink_to(outside, target_is_directory=True)

    assert store.ready_packed_audio_path(7, "meeting-safe") is None
    assert secret.exists()
