# ruff: noqa: B008

from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import anyio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers import meeting_stream, meetings
from services.auth_dependencies import get_current_user
from services.auth_service import User, UserRole
from services.meeting_audio_store import MeetingAudioStore
from services.meeting_contracts import (
    MEETING_TABLES,
    MeetingAudioChunk,
    MeetingSession,
    MeetingStreamLease,
    utcnow,
)
from services.meeting_database import get_meeting_async_session as get_async_session
from services.meeting_repository import MeetingRepository
from services.meeting_stop_finalization import (
    lease_is_active,
    stream_lease_for_update_statement,
)
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession


def _user(user_id: int = 7) -> User:
    return User(
        id=user_id,
        username=f"stop-owner-{user_id}",
        email=f"stop-owner-{user_id}@example.test",
        hashed_password="x",
        full_name=f"Stop Owner {user_id}",
        role=UserRole.ANALYST,
        is_active=True,
        approval_status="approved",
    )


def _client(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stop.db'}")

    async def initialize():
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SQLModel.metadata.create_all(
                    sync_connection,
                    tables=[User.__table__, *[model.__table__ for model in MEETING_TABLES]],
                )
            )

    anyio.run(initialize)
    canonical_root = tmp_path / "canonical-audio"
    monkeypatch.setenv("SIQ_MEETINGS_ENABLED", "true")
    monkeypatch.setenv("SIQ_MEETINGS_ASR_ENABLED", "false")
    monkeypatch.setenv("SIQ_MEETING_AUDIO_ROOT", str(canonical_root))
    monkeypatch.setenv("SIQ_MEETINGS_AUDIO_ROOT", str(tmp_path / "legacy-audio"))

    app = FastAPI()
    app.include_router(meetings.router, prefix="/api")
    app.include_router(meeting_stream.router, prefix="/api")
    active_user = {"value": _user()}

    async def current_user():
        return active_user["value"]

    async def session_dependency():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_async_session] = session_dependency
    return TestClient(app), active_user, engine, canonical_root


def _create_meeting(client: TestClient, title: str) -> str:
    response = client.post(
        "/api/meetings/v1/sessions",
        json={"title": title, "ai_enabled": False, "model_selection": {"mode": "none"}},
    )
    assert response.status_code == 201
    return response.json()["id"]


async def _seed_active_lease(engine, meeting_id: str):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        repository = MeetingRepository(session)
        await repository.transition_session(meeting_id, 7, "start")
        meeting, _, _ = await repository.transition_session(meeting_id, 7, "mark_live")
        session.add(
            MeetingStreamLease(
                meeting_id=meeting_id,
                stream_epoch=meeting.stream_epoch,
                connection_id="active-connection",
                owner_user_id=7,
                lease_until=utcnow() + timedelta(seconds=60),
            )
        )
        await session.commit()


async def _expire_lease_and_add_audio(engine, meeting_id: str, payload: bytes):
    audio_store = MeetingAudioStore()
    persisted = audio_store.persist_chunk(7, meeting_id, 1, 0, payload)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        lease = (
            await session.exec(
                stream_lease_for_update_statement(meeting_id)
            )
        ).first()
        assert lease is not None
        lease.lease_until = utcnow() - timedelta(seconds=1)
        session.add(lease)
        session.add(
            MeetingAudioChunk(
                meeting_id=meeting_id,
                stream_epoch=1,
                sequence=0,
                start_ms=0,
                duration_ms=len(payload) // 32,
                storage_key=persisted.storage_key,
                sha256=persisted.sha256,
                byte_size=persisted.byte_size,
            )
        )
        await session.commit()
    return audio_store.resolve_storage_key(persisted.storage_key)


async def _pack_as_gateway(engine, meeting_id: str):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        chunks = await MeetingRepository(session).list_audio_chunks(meeting_id, 7)
    return MeetingAudioStore().pack_wav(7, meeting_id, chunks)


def test_stop_hands_off_to_active_gateway_then_takes_over_expired_lease(tmp_path, monkeypatch):
    client, active_user, engine, canonical_root = _client(tmp_path, monkeypatch)
    meeting_id = _create_meeting(client, "租约接管测试")
    anyio.run(_seed_active_lease, engine, meeting_id)

    first = client.post(f"/api/meetings/v1/sessions/{meeting_id}/stop")
    assert first.status_code == 200
    assert first.json()["session"]["state"] == "stopping"
    assert first.json()["finalization_path"] == "stream_gateway"
    assert first.json()["audio_status"] == "pending"
    assert client.post(f"/api/meetings/v1/sessions/{meeting_id}/finalize").status_code == 409

    repeated = client.post(f"/api/meetings/v1/sessions/{meeting_id}/stop")
    assert repeated.status_code == 200
    assert repeated.json()["session"]["state"] == "stopping"
    assert repeated.json()["idempotent"] is True

    pcm = b"\x01\x00" * 1_600
    anyio.run(_expire_lease_and_add_audio, engine, meeting_id, pcm)
    gateway_packed = anyio.run(_pack_as_gateway, engine, meeting_id)
    gateway_packed_mtime = gateway_packed.stat().st_mtime_ns
    takeover = client.post(f"/api/meetings/v1/sessions/{meeting_id}/stop")
    assert takeover.status_code == 200
    assert takeover.json()["session"]["state"] == "stopped"
    assert takeover.json()["finalization_path"] == "rest_fallback"
    assert takeover.json()["audio_status"] == "available"
    packed = canonical_root / "7" / meeting_id / "audio" / "meeting.wav"
    assert packed.is_file()
    assert packed.stat().st_mtime_ns == gateway_packed_mtime
    assert not (tmp_path / "legacy-audio" / "7" / meeting_id / "audio" / "meeting.wav").exists()

    again = client.post(f"/api/meetings/v1/sessions/{meeting_id}/stop")
    assert again.status_code == 200
    assert again.json()["finalization_path"] == "already_stopped"
    assert again.json()["idempotent"] is True

    ticket = client.post(f"/api/meetings/v1/sessions/{meeting_id}/audio-ticket").json()
    replay_url = urlsplit(ticket["audio_url"])
    replay = client.get(replay_url.path + "?" + replay_url.query)
    assert replay.status_code == 200
    assert replay.headers["content-type"].startswith("audio/wav")

    active_user["value"] = _user(8)
    hidden = client.post(f"/api/meetings/v1/sessions/{meeting_id}/stop")
    assert hidden.status_code == 404
    assert hidden.json()["detail"]["code"] == "MEETING_RESOURCE_NOT_FOUND"
    anyio.run(engine.dispose)


def test_empty_draft_stops_with_explicit_unavailable_replay(tmp_path, monkeypatch):
    client, _, engine, _ = _client(tmp_path, monkeypatch)
    meeting_id = _create_meeting(client, "空草稿")

    stopped = client.post(f"/api/meetings/v1/sessions/{meeting_id}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["session"]["state"] == "stopped"
    assert stopped.json()["finalization_path"] == "rest_fallback"
    assert stopped.json()["audio_status"] == "unavailable"

    ticket = client.post(f"/api/meetings/v1/sessions/{meeting_id}/audio-ticket")
    assert ticket.status_code == 404
    assert ticket.json()["detail"]["code"] == "AUDIO_NOT_AVAILABLE"
    anyio.run(engine.dispose)


def test_corrupt_pcm_returns_conflict_and_retry_can_finish(tmp_path, monkeypatch):
    client, _, engine, _ = _client(tmp_path, monkeypatch)
    meeting_id = _create_meeting(client, "损坏音频")

    async def seed():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            meeting = await session.get(MeetingSession, meeting_id)
            assert meeting is not None
            session.add(
                MeetingStreamLease(
                    meeting_id=meeting_id,
                    stream_epoch=1,
                    connection_id="expired-connection",
                    owner_user_id=7,
                    lease_until=utcnow() - timedelta(seconds=1),
                )
            )
            await session.commit()

    anyio.run(seed)
    pcm = b"\x02\x00" * 1_600
    chunk_path = anyio.run(_expire_lease_and_add_audio, engine, meeting_id, pcm)
    chunk_path.write_bytes(b"tampered")

    failed = client.post(f"/api/meetings/v1/sessions/{meeting_id}/stop")
    assert failed.status_code == 409
    assert failed.json()["detail"]["code"] == "MEETING_STOP_FINALIZATION_CONFLICT"
    assert failed.json()["detail"]["audio_code"] == "AUDIO_INTEGRITY_FAILED"
    assert client.get(f"/api/meetings/v1/sessions/{meeting_id}").json()["state"] == "stopping"

    chunk_path.write_bytes(pcm)
    recovered = client.post(f"/api/meetings/v1/sessions/{meeting_id}/stop")
    assert recovered.status_code == 200
    assert recovered.json()["session"]["state"] == "stopped"
    anyio.run(engine.dispose)


def test_lease_comparison_and_lock_statement_are_portable():
    now = datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc)
    aware = MeetingStreamLease(
        meeting_id="meeting-aware",
        connection_id="connection-aware",
        owner_user_id=7,
        lease_until=now + timedelta(seconds=1),
    )
    naive = MeetingStreamLease(
        meeting_id="meeting-naive",
        connection_id="connection-naive",
        owner_user_id=7,
        lease_until=(now - timedelta(seconds=1)).replace(tzinfo=None),
    )
    assert lease_is_active(aware, now=now) is True
    assert lease_is_active(naive, now=now) is False
    assert lease_is_active(aware, now=now, owner_user_id=8) is False
    assert lease_is_active(aware, now=now, stream_epoch=2) is False

    statement = stream_lease_for_update_statement("meeting-1")
    postgres_sql = str(statement.compile(dialect=postgresql.dialect()))
    sqlite_sql = str(statement.compile(dialect=sqlite.dialect()))
    assert "FOR UPDATE" in postgres_sql
    assert "meeting_stream_leases" in sqlite_sql
