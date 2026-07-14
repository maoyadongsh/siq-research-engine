# ruff: noqa: B008

import asyncio
import json
import struct
from urllib.parse import urlsplit
from uuid import uuid4

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers import meeting_stream, meetings
from services.auth_dependencies import get_current_user
from services.auth_service import User, UserRole
from services.meeting_contracts import (
    MEETING_TABLES,
    MeetingAudioChunk,
    MeetingSpeakerTrack,
    MeetingStreamLease,
    MeetingTranscriptSegment,
    utcnow,
)
from services.meeting_database import get_meeting_async_session as get_async_session
from services.meeting_stream_protocol import MeetingStreamProtocolError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, func, select
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.websockets import WebSocketDisconnect


def _user() -> User:
    return User(
        id=7,
        username="stream-owner",
        email="stream-owner@example.test",
        hashed_password="x",
        full_name="Stream Owner",
        role=UserRole.ANALYST,
        is_active=True,
        approval_status="approved",
    )


def test_speaker_track_keys_are_epoch_scoped_and_bounded() -> None:
    assert meeting_stream._epoch_scoped_speaker_track_key(3, "speaker-1") == "epoch-3:speaker-1"
    assert meeting_stream._epoch_scoped_speaker_track_key(3, "epoch-3:speaker-1") == "epoch-3:speaker-1"
    assert meeting_stream._epoch_scoped_speaker_track_key(3, None) is None
    with pytest.raises(MeetingStreamProtocolError, match="speaker track epoch"):
        meeting_stream._epoch_scoped_speaker_track_key(3, "epoch-2:speaker-1")
    with pytest.raises(MeetingStreamProtocolError, match="configured bound"):
        meeting_stream._epoch_scoped_speaker_track_key(3, "x" * 128)


class _FakeSpeech:
    def __init__(self, meeting_id: str, *, close_after_ready: bool = False):
        self.meeting_id = meeting_id
        self.stream_epoch = 1
        self.events: asyncio.Queue[str | None] = asyncio.Queue()
        self.trace_id = str(uuid4())
        self.close_after_ready = close_after_ready

    def _event(self, event_type: str, payload: dict) -> str:
        return json.dumps(
            {
                "schema_version": "siq.meeting.speech.event.v1",
                "event_id": str(uuid4()),
                "meeting_id": self.meeting_id,
                "stream_epoch": self.stream_epoch,
                "type": event_type,
                "cursor": None,
                "emitted_at": "2026-07-13T08:00:00Z",
                "trace_id": self.trace_id,
                "payload": payload,
            }
        )

    async def send(self, message):
        if isinstance(message, str):
            payload = json.loads(message)
            if payload["type"] == "stream.start":
                assert payload["meeting_id"] == self.meeting_id
                assert payload["stream_epoch"] == 1
                await self.events.put(
                    self._event(
                        "stream.ready",
                        {
                            "resumed": False,
                            "ack_sequence": -1,
                            "audio": payload["audio"],
                            "adapter": "fake",
                        },
                    )
                )
                if self.close_after_ready:
                    await self.events.put(None)
            elif payload["type"] == "stream.stop":
                await self.events.put(self._event("stream.stopped", {"ack_sequence": 0}))
                await self.events.put(None)
            return

        _, _, _, _, epoch, sequence, capture_ms, size = struct.unpack("!4sBBHIQQI", message[:32])
        assert epoch == 1
        assert sequence == 0
        await self.events.put(
            self._event(
                "asr.final",
                {
                    "segment_token": "segment-1",
                    "text": "欢迎张江介绍项目",
                    "start_ms": capture_ms,
                    "end_ms": capture_ms + 100,
                    "adapter": "fake",
                    "finalization_reason": "vad",
                    "inference_ms": 12,
                    "speaker_track_key": "speaker-1",
                    "speaker_confidence": 0.91,
                    "source_speaker_hints": [],
                    "degraded_reason": None,
                    "word_timestamps": [
                        {"token_index": 0, "start_ms": capture_ms, "end_ms": capture_ms + 100, "text": "欢迎"}
                    ],
                    "durability": "gateway_pending",
                },
            )
        )
        await self.events.put(
            self._event(
                "audio.ack",
                {
                    "stream_epoch": 1,
                    "ack_sequence": sequence,
                    "duplicate": False,
                    "buffered_frames": 0,
                    "buffered_bytes": 0,
                },
            )
        )
        assert size == len(message) - 32

    def __aiter__(self):
        return self

    async def __anext__(self):
        event = await self.events.get()
        if event is None:
            raise StopAsyncIteration
        return event


class _FakeConnectContext:
    def __init__(self, meeting_id: str, *, close_after_ready: bool = False):
        self.connection = _FakeSpeech(meeting_id, close_after_ready=close_after_ready)

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return False


def test_stream_gateway_persists_before_ack_and_durabilizes_final(tmp_path, monkeypatch):
    database_path = tmp_path / "gateway.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")

    async def initialize():
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SQLModel.metadata.create_all(
                    sync_connection,
                    tables=[User.__table__, *[model.__table__ for model in MEETING_TABLES]],
                )
            )

    anyio.run(initialize)
    monkeypatch.setenv("SIQ_MEETINGS_ENABLED", "true")
    monkeypatch.setenv("SIQ_MEETINGS_ASR_ENABLED", "true")
    monkeypatch.setenv("SIQ_MEETING_ASR_WS_URL", "ws://speech.internal/v1/stream/{meeting_id}")
    monkeypatch.setenv("SIQ_MEETINGS_AUDIO_ROOT", str(tmp_path / "audio"))
    monkeypatch.setenv("SIQ_MEETING_RECONNECT_BUFFER_SECONDS", "75")
    monkeypatch.setattr(meeting_stream, "async_engine", engine)

    disconnect_meetings: set[str] = set()

    def fake_connect(url, **_kwargs):
        meeting_id = url.rsplit("/", 1)[-1]
        return _FakeConnectContext(meeting_id, close_after_ready=meeting_id in disconnect_meetings)

    monkeypatch.setattr(meeting_stream, "connect", fake_connect)
    app = FastAPI()
    app.include_router(meetings.router, prefix="/api")
    app.include_router(meeting_stream.router, prefix="/api")

    async def current_user():
        return _user()

    async def session_dependency():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_async_session] = session_dependency
    client = TestClient(app)

    created = client.post(
        "/api/meetings/v1/sessions",
        json={"title": "网关测试", "ai_enabled": False, "model_selection": {"mode": "none"}},
    )
    meeting_id = created.json()["id"]
    assert client.post(f"/api/meetings/v1/sessions/{meeting_id}/start").status_code == 200
    ticket = client.post(
        f"/api/meetings/v1/sessions/{meeting_id}/stream-ticket",
        headers={"Origin": "http://testserver"},
    ).json()
    assert ticket["ws_url"].startswith(f"/api/meetings/v1/sessions/{meeting_id}/audio?")
    assert "127.0.0.1" not in ticket["ws_url"]
    assert ticket["last_acked_sequence"] == -1
    assert ticket["capture_offset_ms"] == 0
    assert ticket["reconnect_window_seconds"] == 75
    websocket_url = urlsplit(ticket["ws_url"])
    target = websocket_url.path + "?" + websocket_url.query

    with client.websocket_connect(target, headers={"Origin": "http://testserver"}) as websocket:
        websocket.send_json(
            {
                "type": "stream.start",
                "schema_version": "siq.meeting.stream.v1",
                "meeting_id": meeting_id,
                "client_stream_id": str(uuid4()),
                "stream_epoch": 1,
                "audio": {
                    "encoding": "pcm_s16le",
                    "sample_rate": 16000,
                    "channels": 1,
                    "chunk_ms": 100,
                },
                "last_server_cursor": 0,
                "last_acked_sequence": -1,
            }
        )
        handshake_events = []
        while not handshake_events or handshake_events[-1]["type"] != "stream.ready":
            handshake_events.append(websocket.receive_json())
        durable_handshake = [event for event in handshake_events if event["cursor"] is not None]
        assert [event["cursor"] for event in durable_handshake] == [1, 2, 3]
        assert [event["type"] for event in durable_handshake] == [
            "session.created",
            "session.state.changed",
            "session.state.changed",
        ]
        assert [event["payload"].get("action") for event in durable_handshake[1:]] == [
            "start",
            "mark_live",
        ]
        assert handshake_events[-1]["type"] == "stream.ready"

        pcm = b"\x01\x00" * 1_600
        frame = struct.pack("!4sBBHIQQI", b"SIQA", 1, 0, 32, 1, 0, 0, len(pcm)) + pcm
        websocket.send_bytes(frame)
        received: dict[str, dict] = {}
        while {"speaker.track.created", "transcript.segment.stable", "audio.ack"} - set(received):
            event = websocket.receive_json()
            received[event["type"]] = event
        assert received["transcript.segment.stable"]["cursor"] is not None
        assert received["speaker.track.created"]["cursor"] < received["transcript.segment.stable"]["cursor"]
        assert received["transcript.segment.stable"]["payload"]["text"] == "欢迎张江介绍项目"
        assert received["audio.ack"]["payload"]["ack_sequence"] == 0

        checkpoint_ticket = client.post(
            f"/api/meetings/v1/sessions/{meeting_id}/stream-ticket",
            headers={"Origin": "http://testserver"},
        )
        assert checkpoint_ticket.status_code == 200
        assert checkpoint_ticket.json()["last_acked_sequence"] == 0
        assert checkpoint_ticket.json()["capture_offset_ms"] == 100
        assert checkpoint_ticket.json()["reconnect_window_seconds"] == 75

        websocket.send_json({"type": "stream.stop", "schema_version": "siq.meeting.stream.v1"})
        stop_types = set()
        stop_actions = set()
        while "stream.stopped" not in stop_types:
            stop_event = websocket.receive_json()
            stop_types.add(stop_event["type"])
            if stop_event["type"] == "session.state.changed":
                stop_actions.add(stop_event["payload"].get("action"))
        assert "stream.stopped" in stop_types
        assert {"stop", "mark_stopped"}.issubset(stop_actions)
        with pytest.raises(WebSocketDisconnect):
            websocket.receive_json()

    detail = client.get(f"/api/meetings/v1/sessions/{meeting_id}")
    assert detail.json()["state"] == "stopped"
    assert detail.json()["last_audio_sequence"] == 0

    async def assert_database():
        async with AsyncSession(engine) as session:
            segment_count = (await session.exec(select(func.count()).select_from(MeetingTranscriptSegment))).one()
            chunk_count = (await session.exec(select(func.count()).select_from(MeetingAudioChunk))).one()
            active_lease_count = (
                await session.exec(
                    select(func.count())
                    .select_from(MeetingStreamLease)
                    .where(MeetingStreamLease.lease_until > utcnow())
                )
            ).one()
            segment = (await session.exec(select(MeetingTranscriptSegment))).one()
            speaker = (await session.exec(select(MeetingSpeakerTrack))).one()
            assert segment_count == 1
            assert chunk_count == 1
            assert active_lease_count == 0
            assert segment.human_locked is False
            assert speaker.track_key == "epoch-1:speaker-1"
            assert json.loads(segment.word_timestamps_json)[0]["text"] == "欢迎"

    anyio.run(assert_database)

    reconnect_created = client.post(
        "/api/meetings/v1/sessions",
        json={"title": "重连测试", "ai_enabled": False, "model_selection": {"mode": "none"}},
    )
    reconnect_id = reconnect_created.json()["id"]
    client.post(f"/api/meetings/v1/sessions/{reconnect_id}/start")
    disconnect_meetings.add(reconnect_id)

    def connect_until_transport_close(ticket_payload):
        url = urlsplit(ticket_payload["ws_url"])
        with client.websocket_connect(
            url.path + "?" + url.query,
            headers={"Origin": "http://testserver"},
        ) as websocket:
            websocket.send_json(
                {
                    "type": "stream.start",
                    "schema_version": "siq.meeting.stream.v1",
                    "meeting_id": reconnect_id,
                    "client_stream_id": str(uuid4()),
                    "stream_epoch": 1,
                    "audio": {
                        "encoding": "pcm_s16le",
                        "sample_rate": 16000,
                        "channels": 1,
                        "chunk_ms": 100,
                    },
                    "last_server_cursor": 0,
                    "last_acked_sequence": -1,
                }
            )
            with pytest.raises(WebSocketDisconnect):
                while True:
                    websocket.receive_json()

    first_reconnect_ticket = client.post(
        f"/api/meetings/v1/sessions/{reconnect_id}/stream-ticket",
        headers={"Origin": "http://testserver"},
    ).json()
    assert first_reconnect_ticket["stream_epoch"] == 1
    connect_until_transport_close(first_reconnect_ticket)
    assert client.get(f"/api/meetings/v1/sessions/{reconnect_id}").json()["state"] == "reconnecting"

    # Reconnect reuses the same meeting capacity slot without inventing a new epoch.
    second_reconnect_ticket = client.post(
        f"/api/meetings/v1/sessions/{reconnect_id}/stream-ticket",
        headers={"Origin": "http://testserver"},
    )
    assert second_reconnect_ticket.status_code == 200
    assert second_reconnect_ticket.json()["stream_epoch"] == 1
    connect_until_transport_close(second_reconnect_ticket.json())

    async def assert_reconnect_lease_reused():
        async with AsyncSession(engine) as session:
            leases = list(
                (
                    await session.exec(select(MeetingStreamLease).where(MeetingStreamLease.meeting_id == reconnect_id))
                ).all()
            )
            assert len(leases) == 1
            assert leases[0].lease_until <= utcnow().replace(tzinfo=None)

    anyio.run(assert_reconnect_lease_reused)
    anyio.run(engine.dispose)
