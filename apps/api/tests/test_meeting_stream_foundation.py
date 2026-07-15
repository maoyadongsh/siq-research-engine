import json
import struct
import wave
from datetime import timedelta

import anyio
import pytest
from routers import meeting_stream
from services.auth_service import User
from services.meeting_audio_store import MeetingAudioStore, MeetingAudioStoreError
from services.meeting_config import MeetingSettings
from services.meeting_contracts import (
    MEETING_TABLES,
    MeetingAudioChunk,
    MeetingCreateRequest,
    MeetingSession,
    MeetingState,
    MeetingStreamLease,
    StreamTicketResponse,
    utcnow,
)
from services.meeting_repository import MeetingInvalidOperation, MeetingRepository, MeetingVersionConflict
from services.meeting_stream_limits import MeetingAudioRateLimiter
from services.meeting_stream_protocol import MeetingStreamProtocolError, decode_audio_frame, parse_control
from services.meeting_stream_ticket import MeetingStreamTicketService, StreamTicketError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, func, select
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.websockets import WebSocketDisconnect


async def _database():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=[User.__table__, *[model.__table__ for model in MEETING_TABLES]],
            )
        )
    return engine


def _frame(*, epoch: int = 1, sequence: int = 0, capture_ms: int = 0, payload: bytes = b"\0\0" * 1600):
    return (
        struct.pack(
            "!4sBBHIQQI",
            b"SIQA",
            1,
            0,
            32,
            epoch,
            sequence,
            capture_ms,
            len(payload),
        )
        + payload
    )


def test_gateway_audio_frame_contract_is_strict():
    decoded = decode_audio_frame(_frame(), max_payload_bytes=10_000)
    assert decoded.stream_epoch == 1
    assert decoded.sequence == 0
    assert decoded.payload == b"\0\0" * 1600

    broken = bytearray(_frame())
    broken[0:4] = b"NOPE"
    with pytest.raises(MeetingStreamProtocolError) as error:
        decode_audio_frame(bytes(broken), max_payload_bytes=10_000)
    assert error.value.code == "AUDIO_MAGIC_INVALID"


def test_gateway_hotword_control_requires_version_request_and_sequence_boundary():
    value = parse_control(
        """{
          "type":"stream.hotwords.update",
          "schema_version":"siq.meeting.stream.v1",
          "request_id":"11111111-1111-4111-8111-111111111111",
          "hotword_version":4,
          "effective_sequence":18,
          "hotwords":[" 海光信息 "]
        }"""
    )
    assert value["hotword_version"] == 4
    assert value["effective_sequence"] == 18
    assert value["hotwords"] == ["海光信息"]

    with pytest.raises(MeetingStreamProtocolError) as error:
        parse_control(
            """{
              "type":"stream.hotwords.update",
              "schema_version":"siq.meeting.stream.v1",
              "request_id":"11111111-1111-4111-8111-111111111111",
              "hotword_version":4,
              "hotwords":["海光信息"]
            }"""
        )
    assert error.value.code == "CONTROL_MESSAGE_INVALID"


def test_gateway_heartbeat_queues_consecutive_lexicon_versions_at_next_audio_sequence(
    tmp_path,
    monkeypatch,
):
    class Browser:
        def __init__(self):
            self.messages = iter(
                [
                    {
                        "type": "websocket.receive",
                        "text": '{"type":"stream.heartbeat","schema_version":"siq.meeting.stream.v1","next_sequence":9}',
                    },
                    {
                        "type": "websocket.receive",
                        "text": '{"type":"stream.heartbeat","schema_version":"siq.meeting.stream.v1","next_sequence":9}',
                    },
                    {"type": "websocket.disconnect", "code": 1000},
                ]
            )

        async def receive(self):
            return next(self.messages)

    class Speech:
        def __init__(self):
            self.sent = []

        async def send(self, message):
            self.sent.append(message)

    versions = iter([(["v2"], 2), (["v3"], 3)])

    async def load_hotwords(_owner_user_id, _meeting_id):
        return next(versions)

    async def renew_lease(*_args, **_kwargs):
        return None

    monkeypatch.setattr(meeting_stream, "_load_hotwords", load_hotwords)
    monkeypatch.setattr(MeetingStreamTicketService, "renew_lease", renew_lease)

    async def run():
        speech = Speech()
        with pytest.raises(WebSocketDisconnect):
            await meeting_stream._browser_to_speech(
                Browser(),
                speech,
                meeting_id="meeting-1",
                owner_user_id=7,
                stream_epoch=1,
                connection_id="connection-1",
                settings=MeetingSettings(enabled=True, asr_enabled=True),
                audio_store=MeetingAudioStore(tmp_path / "audio"),
                rate_limiter=MeetingAudioRateLimiter(
                    max_frames_per_second=20,
                    max_bytes_per_second=128_000,
                    burst_seconds=1,
                ),
                initial_acked_sequence=4,
                initial_hotword_version=1,
            )
        sent = [json.loads(message) for message in speech.sent]
        assert [message["type"] for message in sent] == [
            "stream.hotwords.update",
            "stream.heartbeat",
            "stream.hotwords.update",
            "stream.heartbeat",
        ]
        assert [sent[0]["hotword_version"], sent[2]["hotword_version"]] == [2, 3]
        assert sent[0]["effective_sequence"] == sent[2]["effective_sequence"] == 9

    anyio.run(run)


def test_stream_ticket_contract_requires_resume_checkpoint_fields():
    required = set(StreamTicketResponse.model_json_schema()["required"])
    assert {
        "last_acked_sequence",
        "capture_offset_ms",
        "reconnect_window_seconds",
    }.issubset(required)

    value = StreamTicketResponse.model_validate(
        {
            "ticket": "ticket",
            "meeting_id": "meeting-1",
            "stream_epoch": 2,
            "last_acked_sequence": 8,
            "capture_offset_ms": 4_500,
            "reconnect_window_seconds": 90,
            "expires_at": utcnow(),
            "ws_url": "/api/meetings/v1/sessions/meeting-1/audio?ticket=ticket",
        }
    )
    assert value.last_acked_sequence == 8
    assert value.capture_offset_ms == 4_500
    assert value.reconnect_window_seconds == 90


def test_stream_ticket_is_origin_bound_one_time_and_single_producer():
    async def run():
        engine = await _database()
        settings = MeetingSettings(
            enabled=True,
            asr_enabled=True,
            stream_ticket_ttl_seconds=45,
            stream_lease_ttl_seconds=20,
        )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            repository = MeetingRepository(session)
            meeting, _, _ = await repository.create_session(7, MeetingCreateRequest(title="流测试"))
            await repository.transition_session(meeting.id, 7, "start")
            service = MeetingStreamTicketService(session, settings)
            raw, ticket, _ = await service.issue(meeting.id, 7, origin="https://siq.example")
            assert ticket.stream_epoch == 1
            consumed, _ = await service.consume(
                meeting.id,
                raw,
                origin="https://siq.example",
                connection_id="connection-a",
            )
            assert consumed.consumed_at is not None
            with pytest.raises(StreamTicketError):
                await service.consume(
                    meeting.id,
                    raw,
                    origin="https://siq.example",
                    connection_id="connection-a",
                )

            second_raw, _, _ = await service.issue(meeting.id, 7, origin="https://siq.example")
            with pytest.raises(MeetingVersionConflict):
                await service.consume(
                    meeting.id,
                    second_raw,
                    origin="https://siq.example",
                    connection_id="connection-b",
                )
            third_raw, _, _ = await service.issue(meeting.id, 7, origin="https://siq.example")
            with pytest.raises(StreamTicketError):
                await service.consume(
                    meeting.id,
                    third_raw,
                    origin="https://other.example",
                    connection_id="connection-a",
                )
        await engine.dispose()

    anyio.run(run)


def test_stream_ticket_response_is_loaded_with_expire_on_commit_session():
    async def run():
        engine = await _database()
        settings = MeetingSettings(enabled=True, asr_enabled=True)
        async with AsyncSession(engine, expire_on_commit=False) as setup_session:
            repository = MeetingRepository(setup_session)
            meeting, _, _ = await repository.create_session(7, MeetingCreateRequest(title="票据响应"))
            started, _, _ = await repository.transition_session(meeting.id, 7, "start")
            meeting_id = started.id

        async with AsyncSession(engine, expire_on_commit=True) as request_session:
            raw, ticket, loaded_meeting = await MeetingStreamTicketService(
                request_session,
                settings,
            ).issue(meeting_id, 7, origin="https://siq.example")

            assert raw
            assert ticket.stream_epoch == 1
            assert ticket.expires_at is not None
            assert loaded_meeting.id == meeting_id
        await engine.dispose()

    anyio.run(run)


def test_active_meeting_capacity_is_owner_scoped_global_and_reconnect_safe():
    async def run():
        engine = await _database()
        settings = MeetingSettings(
            enabled=True,
            asr_enabled=True,
            max_active_per_user=1,
            max_active_total=2,
            stream_ticket_ttl_seconds=45,
            stream_lease_ttl_seconds=20,
        )
        async with AsyncSession(engine, expire_on_commit=False) as session:

            async def started(owner_id: int, title: str):
                meeting = MeetingSession(
                    owner_user_id=owner_id,
                    title=title,
                    state=MeetingState.CONNECTING.value,
                )
                session.add(meeting)
                await session.commit()
                return meeting

            owner_first = await started(7, "用户一会议一")
            owner_second = await started(7, "用户一会议二")
            other_owner = await started(8, "用户二会议")
            global_overflow = await started(9, "用户三会议")
            owner_first_id = owner_first.id
            owner_second_id = owner_second.id
            other_owner_id = other_owner.id
            global_overflow_id = global_overflow.id
            service = MeetingStreamTicketService(session, settings)

            raw_first, _, _ = await service.issue(owner_first_id, 7, origin="https://siq.example")
            await service.consume(
                owner_first_id,
                raw_first,
                origin="https://siq.example",
                connection_id="owner-first-a",
            )

            raw_second, _, _ = await service.issue(owner_second_id, 7, origin="https://siq.example")
            with pytest.raises(StreamTicketError) as per_user_error:
                await service.consume(
                    owner_second_id,
                    raw_second,
                    origin="https://siq.example",
                    connection_id="owner-second-a",
                )
            assert per_user_error.value.code == "MEETING_ACTIVE_LIMIT_PER_USER"
            await session.rollback()

            raw_other, _, _ = await service.issue(other_owner_id, 8, origin="https://siq.example")
            await service.consume(
                other_owner_id,
                raw_other,
                origin="https://siq.example",
                connection_id="other-owner-a",
            )

            raw_overflow, _, _ = await service.issue(global_overflow_id, 9, origin="https://siq.example")
            with pytest.raises(StreamTicketError) as global_error:
                await service.consume(
                    global_overflow_id,
                    raw_overflow,
                    origin="https://siq.example",
                    connection_id="global-overflow-a",
                )
            assert global_error.value.code == "MEETING_ACTIVE_LIMIT_TOTAL"
            await session.rollback()

            reconnecting = await session.get(MeetingSession, owner_first_id)
            assert reconnecting is not None
            reconnecting.state = MeetingState.RECONNECTING.value
            session.add(reconnecting)
            await session.commit()
            reconnect_raw, reconnect_ticket, _ = await service.issue(
                owner_first_id,
                7,
                origin="https://siq.example",
            )
            await service.consume(
                owner_first_id,
                reconnect_raw,
                origin="https://siq.example",
                connection_id="owner-first-b",
            )
            leases = list((await session.exec(select(MeetingStreamLease))).all())
            assert len(leases) == 2
            first_lease = next(value for value in leases if value.meeting_id == owner_first_id)
            assert first_lease.connection_id == "owner-first-b"
            assert first_lease.stream_epoch == reconnect_ticket.stream_epoch

            # Simulate a gateway crash: no release call, only an expired DB lease.
            other_lease = next(value for value in leases if value.meeting_id == other_owner_id)
            other_lease.lease_until = utcnow() - timedelta(seconds=1)
            session.add(other_lease)
            await session.commit()
            await service.consume(
                global_overflow_id,
                raw_overflow,
                origin="https://siq.example",
                connection_id="global-overflow-b",
            )

            await service.release_lease(owner_first_id, "owner-first-b")
            await service.consume(
                owner_second_id,
                raw_second,
                origin="https://siq.example",
                connection_id="owner-second-b",
            )
        await engine.dispose()

    anyio.run(run)


def test_audio_rate_limiter_enforces_exact_frame_and_byte_boundaries():
    now = [0.0]
    limiter = MeetingAudioRateLimiter(
        max_frames_per_second=2,
        max_bytes_per_second=32_000,
        burst_seconds=1,
        clock=lambda: now[0],
    )
    limiter.check(16_000)
    limiter.check(16_000)
    with pytest.raises(MeetingStreamProtocolError) as frame_error:
        limiter.check(0)
    assert frame_error.value.code == "AUDIO_FRAME_RATE_LIMIT"

    now[0] = 0.5
    limiter.check(16_000)
    independent_connection = MeetingAudioRateLimiter(
        max_frames_per_second=100,
        max_bytes_per_second=32_000,
        burst_seconds=1,
        clock=lambda: now[0],
    )
    independent_connection.check(32_000)
    with pytest.raises(MeetingStreamProtocolError) as byte_error:
        independent_connection.check(2)
    assert byte_error.value.code == "AUDIO_BYTE_RATE_LIMIT"


@pytest.mark.parametrize(
    ("max_frames", "max_bytes", "payload_bytes", "expected_code"),
    [
        (1, 128_000, 3_200, "AUDIO_FRAME_RATE_LIMIT"),
        (100, 32_000, 20_000, "AUDIO_BYTE_RATE_LIMIT"),
    ],
)
def test_browser_gateway_applies_per_connection_audio_rate_limits(
    tmp_path,
    monkeypatch,
    max_frames,
    max_bytes,
    payload_bytes,
    expected_code,
):
    class Browser:
        def __init__(self, messages):
            self.messages = iter(messages)

        async def receive(self):
            return next(self.messages)

    class Speech:
        def __init__(self):
            self.sent = []

        async def send(self, message):
            self.sent.append(message)

    async def run():
        engine = await _database()
        monkeypatch.setattr(meeting_stream, "async_engine", engine)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            meeting = MeetingSession(
                owner_user_id=7,
                title="速率限制",
                state=MeetingState.CONNECTING.value,
                stream_epoch=1,
            )
            session.add(meeting)
            await session.commit()

        payload = b"\x01\x00" * (payload_bytes // 2)
        browser = Browser(
            [
                {"type": "websocket.receive", "bytes": _frame(sequence=0, payload=payload)},
                {
                    "type": "websocket.receive",
                    "bytes": _frame(sequence=1, capture_ms=625, payload=payload),
                },
            ]
        )
        speech = Speech()
        limiter = MeetingAudioRateLimiter(
            max_frames_per_second=max_frames,
            max_bytes_per_second=max_bytes,
            burst_seconds=1,
            clock=lambda: 0.0,
        )
        with pytest.raises(MeetingStreamProtocolError) as error:
            await meeting_stream._browser_to_speech(
                browser,
                speech,
                meeting_id=meeting.id,
                owner_user_id=7,
                stream_epoch=1,
                connection_id="rate-test",
                settings=MeetingSettings(enabled=True, asr_enabled=True),
                audio_store=MeetingAudioStore(tmp_path / expected_code),
                rate_limiter=limiter,
            )
        assert error.value.code == expected_code
        assert len(speech.sent) == 1
        async with AsyncSession(engine) as session:
            count = (await session.exec(select(func.count()).select_from(MeetingAudioChunk))).one()
            assert count == 1
        await engine.dispose()

    anyio.run(run)


def test_audio_store_is_atomic_idempotent_and_packs_timeline_gaps(tmp_path):
    store = MeetingAudioStore(tmp_path / "audio")
    first_pcm = b"\x01\x00" * 1600  # 100 ms
    second_pcm = b"\x02\x00" * 1600
    first = store.persist_chunk(7, "meeting-1", 1, 0, first_pcm)
    duplicate = store.persist_chunk(7, "meeting-1", 1, 0, first_pcm)
    assert first.created is True
    assert duplicate.created is False
    with pytest.raises(MeetingAudioStoreError) as error:
        store.persist_chunk(7, "meeting-1", 1, 0, second_pcm)
    assert error.value.code == "AUDIO_SEQUENCE_CONFLICT"

    second = store.persist_chunk(7, "meeting-1", 1, 1, second_pcm)
    chunks = [
        MeetingAudioChunk(
            meeting_id="meeting-1",
            stream_epoch=1,
            sequence=0,
            start_ms=0,
            duration_ms=100,
            storage_key=first.storage_key,
            sha256=first.sha256,
            byte_size=first.byte_size,
        ),
        MeetingAudioChunk(
            meeting_id="meeting-1",
            stream_epoch=1,
            sequence=1,
            start_ms=200,
            duration_ms=100,
            storage_key=second.storage_key,
            sha256=second.sha256,
            byte_size=second.byte_size,
        ),
    ]
    packed = store.pack_wav(7, "meeting-1", chunks)
    with wave.open(str(packed), "rb") as source:
        assert source.getframerate() == 16_000
        assert source.getnchannels() == 1
        assert source.getnframes() == 4_800  # 100 ms + 100 ms silence + 100 ms

    # A published WAV is immutable until persist_chunk invalidates it. Safari
    # may request several ranges concurrently, so replay must not hash every
    # source chunk again for each request.
    store.resolve_storage_key(first.storage_key).unlink()
    assert store.pack_wav(7, "meeting-1", chunks) == packed


def test_audio_pack_rejects_tampered_chunk_without_publishing_output(tmp_path):
    store = MeetingAudioStore(tmp_path / "audio")
    payload = b"\x01\x00" * 1_600
    persisted = store.persist_chunk(7, "meeting-2", 1, 0, payload)
    chunk = MeetingAudioChunk(
        meeting_id="meeting-2",
        stream_epoch=1,
        sequence=0,
        start_ms=0,
        duration_ms=100,
        storage_key=persisted.storage_key,
        sha256=persisted.sha256,
        byte_size=persisted.byte_size,
    )
    store.resolve_storage_key(persisted.storage_key).write_bytes(b"\x02\x00" * 1_600)

    with pytest.raises(MeetingAudioStoreError) as error:
        store.pack_wav(7, "meeting-2", [chunk])

    assert error.value.code == "AUDIO_INTEGRITY_FAILED"
    assert not store.packed_audio_path(7, "meeting-2").exists()


def test_pcm_range_is_bound_to_owner_meeting_and_verified_manifest(tmp_path):
    store = MeetingAudioStore(tmp_path / "audio")
    payload = b"\x01\x00" * 1_600
    persisted = store.persist_chunk(7, "meeting-3", 1, 0, payload)
    chunk = MeetingAudioChunk(
        meeting_id="meeting-3",
        stream_epoch=1,
        sequence=0,
        start_ms=0,
        duration_ms=100,
        storage_key=persisted.storage_key,
        sha256=persisted.sha256,
        byte_size=persisted.byte_size,
    )
    assert store.read_pcm_range(7, "meeting-3", [chunk], 10, 20, 320) == payload[320:640]

    cross_meeting = MeetingAudioChunk(
        meeting_id="meeting-3",
        stream_epoch=1,
        sequence=0,
        start_ms=0,
        duration_ms=100,
        storage_key=persisted.storage_key,
        sha256=persisted.sha256,
        byte_size=persisted.byte_size,
    )
    with pytest.raises(MeetingAudioStoreError) as cross_error:
        store.read_pcm_range(7, "meeting-other", [cross_meeting], 0, 100, 3_200)
    assert cross_error.value.code == "AUDIO_STORAGE_KEY_INVALID"

    store.resolve_storage_key(persisted.storage_key).write_bytes(b"\x02\x00" * 1_600)
    with pytest.raises(MeetingAudioStoreError) as integrity_error:
        store.read_pcm_range(7, "meeting-3", [chunk], 0, 100, 3_200)
    assert integrity_error.value.code == "AUDIO_INTEGRITY_FAILED"


def test_audio_ack_advances_only_after_every_chunk_is_registered(tmp_path):
    async def run():
        engine = await _database()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            repository = MeetingRepository(session)
            meeting, _, _ = await repository.create_session(7, MeetingCreateRequest(title="ACK 测试"))
            meeting, _, _ = await repository.transition_session(meeting.id, 7, "start")
            # Ticket allocation normally advances the first epoch.
            meeting.stream_epoch = 1
            session.add(meeting)
            await session.commit()
            await repository.register_audio_chunk(
                meeting.id,
                7,
                stream_epoch=1,
                sequence=0,
                start_ms=0,
                duration_ms=100,
                storage_key="7/meeting/chunks/1/0.pcm",
                sha256="0" * 64,
                byte_size=3_200,
            )
            with pytest.raises(MeetingInvalidOperation):
                await repository.acknowledge_audio_sequence(meeting.id, 7, stream_epoch=1, ack_sequence=1)
            acknowledged = await repository.acknowledge_audio_sequence(meeting.id, 7, stream_epoch=1, ack_sequence=0)
            assert acknowledged.last_audio_sequence == 0
        await engine.dispose()

    anyio.run(run)
