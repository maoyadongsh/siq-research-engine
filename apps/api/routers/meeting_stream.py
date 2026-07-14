"""Authenticated meeting audio WebSocket gateway and private audio replay."""

# FastAPI intentionally declares dependencies and validated parameters as defaults.
# ruff: noqa: B008

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import timezone
from typing import Any, AsyncIterator
from urllib.parse import quote, urlparse
from uuid import uuid4

from database import async_engine
from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from services.auth_dependencies import get_current_user
from services.auth_service import User
from services.meeting_audio_store import MeetingAudioStore, MeetingAudioStoreError
from services.meeting_config import MeetingSettings
from services.meeting_contracts import (
    AudioChunkState,
    AudioPlaybackTicketResponse,
    MeetingAudioChunk,
    MeetingEvent,
    StableSegmentInput,
    StreamTicketResponse,
    utcnow,
)
from services.meeting_database import get_meeting_async_session as get_async_session
from services.meeting_metrics import (
    meeting_stream_closed,
    meeting_stream_opened,
    observe_meeting_latency,
    record_meeting_counter,
)
from services.meeting_permissions import MEETING_READ, MEETING_UPDATE, meeting_user_id, require_meeting_permission
from services.meeting_repository import (
    MeetingIdempotencyConflict,
    MeetingInvalidOperation,
    MeetingRepository,
    MeetingResourceNotFound,
    MeetingVersionConflict,
)
from services.meeting_stream_limits import MeetingAudioRateLimiter
from services.meeting_stream_protocol import (
    PUBLIC_EVENT_SCHEMA_VERSION,
    MeetingStreamProtocolError,
    decode_audio_frame,
    parse_control,
    parse_speech_event,
    parse_stream_start,
)
from services.meeting_stream_ticket import MeetingStreamTicketService, StreamTicketError
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

router = APIRouter(prefix="/meetings/v1", tags=["meeting-stream"])


def _settings() -> MeetingSettings:
    value = MeetingSettings.from_env()
    if not value.operational:
        raise HTTPException(status_code=503, detail={"code": "MEETINGS_DISABLED"})
    if not value.asr_enabled:
        raise HTTPException(status_code=503, detail={"code": "MEETING_ASR_DISABLED"})
    return value


def _owner(user: User, permission: str) -> int:
    require_meeting_permission(user, permission)
    return meeting_user_id(user)


def _origin_for_ticket(request: Request) -> str:
    origin = str(request.headers.get("origin") or "").strip()
    if origin:
        return origin
    return f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"


def _websocket_url(request: Request, meeting_id: str, ticket: str) -> str:
    # Keep the browser on its current public origin. Building an absolute URL
    # here leaks the API's private 127.0.0.1 address when SIQ is behind Vite,
    # Nginx, or a NAS reverse proxy.
    route_path = f"{request.url.path.rsplit('/', 1)[0]}/audio"
    return f"{route_path}?ticket={quote(ticket, safe='')}"


def _audio_url(request: Request, meeting_id: str, ticket: str) -> str:
    route_path = f"{request.url.path.rsplit('/', 1)[0]}/audio"
    return f"{route_path}?playback_ticket={quote(ticket, safe='')}"


def _map_ticket_error(exc: Exception) -> HTTPException:
    if isinstance(exc, MeetingResourceNotFound):
        return HTTPException(status_code=404, detail={"code": exc.code})
    if isinstance(exc, MeetingVersionConflict):
        return HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc), "current": exc.current},
        )
    if isinstance(exc, StreamTicketError):
        return HTTPException(status_code=400, detail={"code": exc.code, "message": str(exc)})
    return HTTPException(status_code=500, detail={"code": "STREAM_TICKET_FAILED"})


@router.post("/sessions/{meeting_id}/stream-ticket", response_model=StreamTicketResponse)
async def create_stream_ticket(
    meeting_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> StreamTicketResponse:
    settings = _settings()
    owner_id = _owner(current_user, MEETING_UPDATE)
    try:
        raw, ticket, meeting = await MeetingStreamTicketService(async_session, settings).issue(
            meeting_id,
            owner_id,
            origin=_origin_for_ticket(request),
        )
    except (MeetingResourceNotFound, MeetingVersionConflict, StreamTicketError) as exc:
        raise _map_ticket_error(exc) from exc
    capture_offset_ms = (
        await async_session.exec(
            select(func.max(MeetingAudioChunk.start_ms + MeetingAudioChunk.duration_ms)).where(
                MeetingAudioChunk.meeting_id == meeting.id,
                MeetingAudioChunk.state != AudioChunkState.DELETED.value,
            )
        )
    ).one()
    return StreamTicketResponse(
        ticket=raw,
        meeting_id=meeting_id,
        stream_epoch=ticket.stream_epoch,
        last_acked_sequence=meeting.last_audio_sequence,
        capture_offset_ms=int(capture_offset_ms or 0),
        reconnect_window_seconds=settings.reconnect_window_seconds,
        expires_at=ticket.expires_at,
        ws_url=_websocket_url(request, meeting_id, raw),
    )


@router.post("/sessions/{meeting_id}/audio-ticket", response_model=AudioPlaybackTicketResponse)
async def create_audio_ticket(
    meeting_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
) -> AudioPlaybackTicketResponse:
    settings = MeetingSettings.from_env()
    if not settings.operational:
        raise HTTPException(status_code=503, detail={"code": "MEETINGS_DISABLED"})
    owner_id = _owner(current_user, MEETING_READ)
    audio_store = MeetingAudioStore()
    try:
        await MeetingRepository(async_session).get_session(meeting_id, owner_id)
    except MeetingResourceNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": exc.code}) from exc
    if audio_store.ready_packed_audio_path(owner_id, meeting_id) is None:
        raise HTTPException(status_code=404, detail={"code": "AUDIO_NOT_AVAILABLE"})
    try:
        raw, ticket, _ = await MeetingStreamTicketService(async_session, settings).issue_playback(
            meeting_id,
            owner_id,
            origin=_origin_for_ticket(request),
        )
    except (MeetingResourceNotFound, StreamTicketError) as exc:
        raise _map_ticket_error(exc) from exc
    return AudioPlaybackTicketResponse(
        ticket=raw,
        meeting_id=meeting_id,
        expires_at=ticket.expires_at,
        audio_url=_audio_url(request, meeting_id, raw),
    )


@asynccontextmanager
async def _database_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSession(async_engine, expire_on_commit=False) as session:
        yield session


def _speech_url(meeting_id: str) -> str:
    template = os.getenv("SIQ_MEETING_ASR_WS_URL", "").strip()
    if not template or "{meeting_id}" not in template:
        raise MeetingStreamProtocolError(
            "ASR_GATEWAY_NOT_CONFIGURED",
            "meeting ASR WebSocket URL is not configured",
            close_code=1013,
        )
    value = template.replace("{meeting_id}", meeting_id)
    parsed = urlparse(value)
    if parsed.scheme not in {"ws", "wss"} or not parsed.hostname or parsed.username or parsed.password:
        raise MeetingStreamProtocolError(
            "ASR_GATEWAY_URL_INVALID",
            "meeting ASR WebSocket URL is invalid",
            close_code=1013,
        )
    return value


def _public_ephemeral(event: dict[str, Any], *, event_type: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": PUBLIC_EVENT_SCHEMA_VERSION,
        "event_id": str(event.get("event_id") or uuid4()),
        "meeting_id": str(event["meeting_id"]),
        "stream_epoch": event.get("stream_epoch"),
        "type": event_type or event["type"],
        "cursor": None,
        "emitted_at": event.get("emitted_at") or f"{utcnow().isoformat()}Z",
        "trace_id": event.get("trace_id"),
        "payload": event["payload"],
    }


def _public_durable(event: MeetingEvent) -> dict[str, Any]:
    try:
        payload = json.loads(event.payload_json)
    except json.JSONDecodeError:
        payload = {}
    emitted_at = event.created_at
    if emitted_at.tzinfo is None:
        emitted_at = emitted_at.replace(tzinfo=timezone.utc)
    return {
        "schema_version": PUBLIC_EVENT_SCHEMA_VERSION,
        "event_id": event.event_id,
        "meeting_id": event.meeting_id,
        "type": event.event_type,
        "cursor": event.cursor,
        "emitted_at": emitted_at.isoformat().replace("+00:00", "Z"),
        "trace_id": event.trace_id,
        "payload": payload,
    }


async def _load_hotwords(owner_user_id: int, meeting_id: str) -> tuple[list[str], int | None]:
    async with _database_session() as session:
        version, entries = await MeetingRepository(session).get_meeting_lexicon_snapshot(
            meeting_id,
            owner_user_id,
        )
    terms = list(
        dict.fromkeys(
            str(entry.get("canonical_term") or "").strip()
            for entry in entries
            if str(entry.get("canonical_term") or "").strip()
        )
    )
    return terms[:1_000], version


async def _replay_durable_events(
    websocket: WebSocket,
    meeting_id: str,
    owner_user_id: int,
    *,
    after_cursor: int,
    max_events: int = 10_000,
) -> int:
    cursor = after_cursor
    replayed = 0
    while True:
        async with _database_session() as session:
            events, next_cursor = await MeetingRepository(session).list_events(
                meeting_id,
                owner_user_id,
                after_cursor=cursor,
                limit=500,
            )
        for event in events:
            await websocket.send_json(_public_durable(event))
            cursor = event.cursor
            replayed += 1
            if replayed > max_events:
                raise MeetingStreamProtocolError(
                    "EVENT_REPLAY_LIMIT",
                    "durable event replay exceeds the connection limit",
                    close_code=1009,
                )
        if next_cursor is None:
            return cursor
        cursor = next_cursor


async def _transition(
    meeting_id: str,
    owner_user_id: int,
    action: str,
) -> MeetingEvent | None:
    async with _database_session() as session:
        _, _, event = await MeetingRepository(session).transition_session(
            meeting_id,
            owner_user_id,
            action,
        )
        return event


async def _release_stream_lease(
    meeting_id: str,
    connection_id: str,
    settings: MeetingSettings,
) -> None:
    async with _database_session() as session:
        await MeetingStreamTicketService(session, settings).release_lease(
            meeting_id,
            connection_id,
        )


async def _browser_to_speech(
    browser: WebSocket,
    speech: ClientConnection,
    *,
    meeting_id: str,
    owner_user_id: int,
    stream_epoch: int,
    connection_id: str,
    settings: MeetingSettings,
    audio_store: MeetingAudioStore,
    rate_limiter: MeetingAudioRateLimiter,
) -> str:
    while True:
        message = await browser.receive()
        if message.get("type") == "websocket.disconnect":
            raise WebSocketDisconnect(message.get("code", 1006))
        binary = message.get("bytes")
        text = message.get("text")
        if isinstance(binary, bytes):
            frame = decode_audio_frame(binary, max_payload_bytes=settings.max_chunk_bytes)
            if frame.stream_epoch != stream_epoch:
                raise MeetingStreamProtocolError("STREAM_EPOCH_MISMATCH", "audio epoch does not match the ticket")
            if frame.capture_time_ms > settings.max_duration_seconds * 1_000:
                raise MeetingStreamProtocolError("MEETING_DURATION_LIMIT", "meeting duration limit was reached")
            rate_limiter.check(len(frame.payload))
            if frame.payload:
                duration_ms = len(frame.payload) * 1_000 // 32_000
                if duration_ms <= 0 or duration_ms > 1_000:
                    raise MeetingStreamProtocolError("AUDIO_CHUNK_DURATION_INVALID", "audio duration is invalid")
                persisted = await asyncio.to_thread(
                    audio_store.persist_chunk,
                    owner_user_id,
                    meeting_id,
                    stream_epoch,
                    frame.sequence,
                    frame.payload,
                )
                try:
                    async with _database_session() as session:
                        await MeetingRepository(session).register_audio_chunk(
                            meeting_id,
                            owner_user_id,
                            stream_epoch=stream_epoch,
                            sequence=frame.sequence,
                            start_ms=frame.capture_time_ms,
                            duration_ms=duration_ms,
                            storage_key=persisted.storage_key,
                            sha256=persisted.sha256,
                            byte_size=persisted.byte_size,
                        )
                except Exception:
                    await asyncio.to_thread(audio_store.remove_chunk_if_created, persisted)
                    record_meeting_counter("audio_frame", "persist_failed")
                    raise
                record_meeting_counter("audio_frame", "persisted")
            await speech.send(binary)
            continue
        if not isinstance(text, str):
            raise MeetingStreamProtocolError("WEBSOCKET_MESSAGE_INVALID", "message must be text or binary")
        control = parse_control(text)
        if control["type"] == "stream.heartbeat":
            async with _database_session() as session:
                await MeetingStreamTicketService(session, settings).renew_lease(
                    meeting_id,
                    connection_id,
                    -1,
                )
        await speech.send(json.dumps(control, separators=(",", ":")))
        if control["type"] == "stream.stop":
            return "stop_requested"


async def _persist_final(
    event: dict[str, Any],
    *,
    meeting_id: str,
    owner_user_id: int,
    stream_epoch: int,
    hotword_version: int | None,
) -> list[MeetingEvent]:
    persist_started = time.perf_counter()
    payload = event["payload"]
    segment_token = str(payload.get("segment_token") or "").strip()
    text = str(payload.get("text") or "").strip()
    if not segment_token or not text:
        raise MeetingStreamProtocolError("ASR_FINAL_INVALID", "ASR final is missing text or identity", close_code=1011)
    metadata = {
        key: payload.get(key)
        for key in (
            "adapter",
            "finalization_reason",
            "inference_ms",
            "speaker_confidence",
            "source_speaker_hints",
            "degraded_reason",
        )
    }
    request = StableSegmentInput(
        utterance_id=segment_token,
        provider_segment_key=f"{stream_epoch}:{segment_token}",
        start_ms=int(payload.get("start_ms", 0)),
        end_ms=int(payload.get("end_ms", 0)),
        raw_text=text,
        asr_final_text=text,
        asr_provider="meeting-speech",
        asr_model=str(payload.get("adapter") or "configured-adapter"),
        asr_version=str(event.get("schema_version") or "siq.meeting.speech.event.v1"),
        hotword_version=hotword_version,
        word_timestamps=list(payload.get("word_timestamps") or []),
        asr_metadata=metadata,
    )
    speaker_track_key = _epoch_scoped_speaker_track_key(stream_epoch, payload.get("speaker_track_key"))
    async with _database_session() as session:
        before_cursor = int(
            (
                await session.exec(select(func.max(MeetingEvent.cursor)).where(MeetingEvent.meeting_id == meeting_id))
            ).one()
            or 0
        )
        repository = MeetingRepository(session)
        _, duplicate, _ = await repository.append_stable_segment(
            meeting_id,
            owner_user_id,
            request,
            trace_id=str(event.get("trace_id") or "") or None,
            speaker_track_key=speaker_track_key,
            speaker_confidence=payload.get("speaker_confidence"),
        )
        if duplicate:
            record_meeting_counter("stable_segment", "duplicate")
            return []
        record_meeting_counter("stable_segment", "persisted")
        result = await repository.events.list_after(meeting_id, after_cursor=before_cursor, limit=20)
        observe_meeting_latency("segment_persist_latency_seconds", time.perf_counter() - persist_started)
        return result


def _epoch_scoped_speaker_track_key(stream_epoch: int, value: object) -> str | None:
    raw_key = str(value or "").strip()
    if not raw_key:
        return None
    prefix = f"epoch-{stream_epoch}:"
    if raw_key.startswith("epoch-") and not raw_key.startswith(prefix):
        raise MeetingStreamProtocolError(
            "SPEAKER_TRACK_SCOPE_INVALID",
            "speaker track epoch does not match the stream",
            close_code=1011,
        )
    scoped_key = raw_key if raw_key.startswith(prefix) else f"{prefix}{raw_key}"
    if len(scoped_key) > 128:
        raise MeetingStreamProtocolError(
            "SPEAKER_TRACK_KEY_INVALID",
            "speaker track key exceeds its configured bound",
            close_code=1011,
        )
    return scoped_key


async def _pack_audio(
    meeting_id: str,
    owner_user_id: int,
    audio_store: MeetingAudioStore,
) -> None:
    async with _database_session() as session:
        chunks = await MeetingRepository(session).list_audio_chunks(meeting_id, owner_user_id)
    await asyncio.to_thread(audio_store.pack_wav, owner_user_id, meeting_id, chunks)


async def _speech_to_browser(
    speech: ClientConnection,
    browser: WebSocket,
    *,
    meeting_id: str,
    owner_user_id: int,
    stream_epoch: int,
    hotword_version: int | None,
    connection_id: str,
    settings: MeetingSettings,
    audio_store: MeetingAudioStore,
) -> bool:
    async for raw in speech:
        if not isinstance(raw, str):
            raise MeetingStreamProtocolError(
                "SPEECH_EVENT_INVALID", "speech service sent binary output", close_code=1011
            )
        event = parse_speech_event(raw, meeting_id=meeting_id, stream_epoch=stream_epoch)
        event_type = event["type"]
        if event_type == "asr.final":
            observe_meeting_latency(
                "asr_stable_latency_seconds",
                max(0.0, float(event["payload"].get("inference_ms") or 0) / 1_000),
            )
            durable_events = await _persist_final(
                event,
                meeting_id=meeting_id,
                owner_user_id=owner_user_id,
                stream_epoch=stream_epoch,
                hotword_version=hotword_version,
            )
            publish_started = time.perf_counter()
            for durable in durable_events:
                await browser.send_json(_public_durable(durable))
            observe_meeting_latency("event_publish_latency_seconds", time.perf_counter() - publish_started)
            continue
        if event_type == "speaker.track.observed":
            continue
        if event_type == "asr.partial":
            observe_meeting_latency(
                "asr_partial_latency_seconds",
                max(0.0, float(event["payload"].get("inference_ms") or 0) / 1_000),
            )
            await browser.send_json(_public_ephemeral(event, event_type="transcript.partial"))
            continue
        if event_type == "audio.gap.detected":
            record_meeting_counter(
                "audio_gap",
                str(event["payload"].get("reason") or "sequence_gap"),
            )
        if event_type == "audio.ack":
            ack_sequence = int(event["payload"].get("ack_sequence", -1))
            async with _database_session() as session:
                repository = MeetingRepository(session)
                await repository.acknowledge_audio_sequence(
                    meeting_id,
                    owner_user_id,
                    stream_epoch=stream_epoch,
                    ack_sequence=ack_sequence,
                )
                await MeetingStreamTicketService(session, settings).renew_lease(
                    meeting_id,
                    connection_id,
                    ack_sequence,
                )
            await browser.send_json(_public_ephemeral(event))
            continue
        if event_type == "stream.ready":
            durable = await _transition(meeting_id, owner_user_id, "mark_live")
            if durable is not None:
                await browser.send_json(_public_durable(durable))
            await browser.send_json(_public_ephemeral(event))
            continue
        if event_type == "stream.stopped":
            try:
                await _pack_audio(meeting_id, owner_user_id, audio_store)
                stopping = await _transition(meeting_id, owner_user_id, "stop")
                stopped = await _transition(meeting_id, owner_user_id, "mark_stopped")
            except MeetingAudioStoreError as exc:
                await browser.send_json(
                    _public_ephemeral(
                        {
                            **event,
                            "type": "error",
                            "payload": {
                                "scope": "storage",
                                "code": exc.code,
                                "message": "meeting audio could not be finalized",
                                "retryable": True,
                            },
                        }
                    )
                )
                raise
            for durable in (stopping, stopped):
                if durable is not None:
                    await browser.send_json(_public_durable(durable))
            await browser.send_json(_public_ephemeral(event))
            return True
        await browser.send_json(_public_ephemeral(event))
    reconnecting = await _transition(meeting_id, owner_user_id, "mark_reconnecting")
    record_meeting_counter("ws_reconnect", "speech_stream_closed")
    if reconnecting is not None:
        await browser.send_json(_public_durable(reconnecting))
    return False


async def _send_stream_error(
    websocket: WebSocket,
    meeting_id: str,
    stream_epoch: int | None,
    code: str,
    message: str,
    *,
    scope: str = "stream",
) -> None:
    try:
        await websocket.send_json(
            {
                "schema_version": PUBLIC_EVENT_SCHEMA_VERSION,
                "event_id": str(uuid4()),
                "meeting_id": meeting_id,
                "stream_epoch": stream_epoch,
                "type": "error",
                "cursor": None,
                "emitted_at": f"{utcnow().isoformat()}Z",
                "trace_id": None,
                "payload": {
                    "scope": scope,
                    "code": code,
                    "message": message,
                    "retryable": code.endswith(("TIMEOUT", "UNAVAILABLE"))
                    or code
                    in {
                        "MEETING_ACTIVE_LIMIT_PER_USER",
                        "MEETING_ACTIVE_LIMIT_TOTAL",
                    },
                },
            }
        )
    except Exception:
        return


@router.websocket("/sessions/{meeting_id}/audio", name="meeting_audio_stream")
async def meeting_audio_stream(
    websocket: WebSocket,
    meeting_id: str,
    ticket: str = Query(min_length=20, max_length=256),
) -> None:
    settings = MeetingSettings.from_env()
    if not settings.operational or not settings.asr_enabled:
        await websocket.close(code=1013, reason="meeting streaming is unavailable")
        return
    origin = str(websocket.headers.get("origin") or "")
    connection_id = str(uuid4())
    owner_user_id: int | None = None
    stream_epoch: int | None = None
    audio_store = MeetingAudioStore()
    accepted = False
    normal_stop = False
    lease_released = False
    connection_counted = False

    async def release_current_lease() -> None:
        nonlocal lease_released
        if owner_user_id is None or lease_released:
            return
        try:
            await _release_stream_lease(meeting_id, connection_id, settings)
        except Exception:
            # An unavailable database leaves a bounded TTL lease; capacity
            # checks never treat an expired lease as active.
            return
        lease_released = True

    try:
        async with _database_session() as session:
            consumed, meeting = await MeetingStreamTicketService(session, settings).consume(
                meeting_id,
                ticket,
                origin=origin,
                connection_id=connection_id,
            )
        owner_user_id = consumed.owner_user_id
        stream_epoch = consumed.stream_epoch
        await websocket.accept()
        accepted = True
        meeting_stream_opened()
        connection_counted = True
        try:
            first = await asyncio.wait_for(
                websocket.receive_text(),
                timeout=settings.stream_handshake_timeout_seconds,
            )
        except TimeoutError as exc:
            raise MeetingStreamProtocolError("HANDSHAKE_TIMEOUT", "stream.start was not received") from exc
        start = parse_stream_start(first)
        if start.meeting_id is not None and str(start.meeting_id) != meeting_id:
            raise MeetingStreamProtocolError("MEETING_ID_MISMATCH", "stream.start meeting ID is invalid")
        if start.stream_epoch != stream_epoch:
            raise MeetingStreamProtocolError("STREAM_EPOCH_MISMATCH", "stream.start epoch is invalid")
        if start.last_acked_sequence > meeting.last_audio_sequence:
            raise MeetingStreamProtocolError("ACK_SEQUENCE_AHEAD", "client ACK is ahead of durable state")
        await _replay_durable_events(
            websocket,
            meeting_id,
            owner_user_id,
            after_cursor=start.last_server_cursor or 0,
        )
        hotwords, hotword_version = await _load_hotwords(owner_user_id, meeting_id)
        internal_start = start.model_dump(mode="json")
        internal_start["meeting_id"] = meeting_id
        internal_start["language"] = meeting.language
        internal_start["hotwords"] = hotwords
        internal_start["last_acked_sequence"] = meeting.last_audio_sequence
        rate_limiter = MeetingAudioRateLimiter(
            max_frames_per_second=settings.audio_max_frames_per_second,
            max_bytes_per_second=settings.audio_max_bytes_per_second,
            burst_seconds=settings.audio_rate_burst_seconds,
        )

        headers: dict[str, str] = {}
        service_token = (
            os.getenv("SIQ_MEETING_SPEECH_INTERNAL_SERVICE_TOKEN") or os.getenv("SIQ_MEETING_ASR_SERVICE_TOKEN") or ""
        ).strip()
        if service_token:
            headers["X-SIQ-Service-Token"] = service_token
        async with connect(
            _speech_url(meeting_id),
            additional_headers=headers,
            # Browser Origin is validated by the one-time gateway ticket. The
            # loopback speech hop is authenticated separately and must not
            # inherit an unconfigured public Origin allowlist.
            origin=os.getenv("SIQ_MEETING_SPEECH_ORIGIN", "").strip() or None,
            max_size=settings.max_chunk_bytes + 65_536,
            open_timeout=settings.stream_handshake_timeout_seconds,
        ) as speech:
            await speech.send(json.dumps(internal_start, separators=(",", ":")))
            browser_task = asyncio.create_task(
                _browser_to_speech(
                    websocket,
                    speech,
                    meeting_id=meeting_id,
                    owner_user_id=owner_user_id,
                    stream_epoch=stream_epoch,
                    connection_id=connection_id,
                    settings=settings,
                    audio_store=audio_store,
                    rate_limiter=rate_limiter,
                )
            )
            speech_task = asyncio.create_task(
                _speech_to_browser(
                    speech,
                    websocket,
                    meeting_id=meeting_id,
                    owner_user_id=owner_user_id,
                    stream_epoch=stream_epoch,
                    hotword_version=hotword_version,
                    connection_id=connection_id,
                    settings=settings,
                    audio_store=audio_store,
                )
            )
            done, pending = await asyncio.wait(
                {browser_task, speech_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if browser_task in done and browser_task.exception() is not None:
                record_meeting_counter("ws_reconnect", "browser_transport")
                reconnecting = await _transition(meeting_id, owner_user_id, "mark_reconnecting")
                if reconnecting is not None:
                    await websocket.send_json(_public_durable(reconnecting))
            if speech_task in done and speech_task.exception() is not None:
                record_meeting_counter("ws_reconnect", "speech_transport")
                reconnecting = await _transition(meeting_id, owner_user_id, "mark_reconnecting")
                if reconnecting is not None:
                    await websocket.send_json(_public_durable(reconnecting))
            if browser_task in done and browser_task.exception() is None and browser_task.result() == "stop_requested":
                normal_stop = await asyncio.wait_for(
                    speech_task,
                    timeout=settings.stream_handshake_timeout_seconds,
                )
                done.add(speech_task)
                pending.discard(speech_task)
            elif speech_task in done:
                normal_stop = speech_task.result()
                await release_current_lease()
                await websocket.close(code=1000 if normal_stop else 1012)
                try:
                    await asyncio.wait_for(
                        browser_task,
                        timeout=settings.stream_handshake_timeout_seconds,
                    )
                except (WebSocketDisconnect, TimeoutError, asyncio.CancelledError):
                    pass
                pending.discard(browser_task)
            for task in pending:
                task.cancel()
            for task in done:
                if task is not speech_task or not normal_stop:
                    task.result()
            if normal_stop:
                await release_current_lease()
                await websocket.close(code=1000)
    except StreamTicketError as exc:
        capacity_error = exc.code in {
            "MEETING_ACTIVE_LIMIT_PER_USER",
            "MEETING_ACTIVE_LIMIT_TOTAL",
        }
        if accepted:
            await _send_stream_error(
                websocket,
                meeting_id,
                stream_epoch,
                exc.code if capacity_error else "STREAM_TICKET_INVALID",
                str(exc) if capacity_error else "stream ticket is invalid",
            )
        await release_current_lease()
        await websocket.close(
            code=1013 if capacity_error else 1008,
            reason=exc.code if capacity_error else "stream ticket is invalid",
        )
    except (MeetingResourceNotFound, MeetingVersionConflict):
        if accepted:
            await _send_stream_error(
                websocket, meeting_id, stream_epoch, "STREAM_TICKET_INVALID", "stream ticket is invalid"
            )
        await release_current_lease()
        await websocket.close(code=1008, reason="stream ticket is invalid")
    except MeetingStreamProtocolError as exc:
        if accepted:
            await _send_stream_error(websocket, meeting_id, stream_epoch, exc.code, exc.message)
        await release_current_lease()
        await websocket.close(code=exc.close_code, reason=exc.code)
    except (MeetingAudioStoreError, MeetingIdempotencyConflict, MeetingInvalidOperation) as exc:
        code = getattr(exc, "code", "AUDIO_STORAGE_FAILED")
        record_meeting_counter("audio_storage_failure", code)
        if accepted:
            await _send_stream_error(
                websocket, meeting_id, stream_epoch, code, "meeting audio storage failed", scope="storage"
            )
        await release_current_lease()
        await websocket.close(code=1011, reason=code)
    except (ConnectionClosed, OSError, TimeoutError):
        record_meeting_counter("ws_reconnect", "asr_unavailable")
        if accepted:
            await _send_stream_error(
                websocket, meeting_id, stream_epoch, "ASR_UNAVAILABLE", "meeting ASR is unavailable", scope="asr"
            )
        await release_current_lease()
        await websocket.close(code=1013, reason="ASR_UNAVAILABLE")
    except WebSocketDisconnect:
        record_meeting_counter("ws_reconnect", "browser_disconnect")
        await release_current_lease()
    finally:
        if connection_counted:
            meeting_stream_closed()


@router.get("/sessions/{meeting_id}/audio", response_class=FileResponse, name="meeting_audio_replay")
async def meeting_audio_replay(
    meeting_id: str,
    playback_ticket: str = Query(min_length=20, max_length=256),
    async_session: AsyncSession = Depends(get_async_session),
) -> FileResponse:
    settings = MeetingSettings.from_env()
    if not settings.operational:
        raise HTTPException(status_code=503, detail={"code": "MEETINGS_DISABLED"})
    try:
        ticket_record, _ = await MeetingStreamTicketService(
            async_session,
            settings,
        ).validate_playback(meeting_id, playback_ticket)
    except StreamTicketError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.code}) from exc
    owner_id = ticket_record.owner_user_id
    audio_store = MeetingAudioStore()
    path = audio_store.ready_packed_audio_path(owner_id, meeting_id)
    if path is None:
        raise HTTPException(status_code=404, detail={"code": "AUDIO_NOT_AVAILABLE"})
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=f"meeting-{meeting_id}.wav",
        content_disposition_type="inline",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


# The aggregate router preserves the development/test contract. Protected
# deployments mount only the control plane in the API process and expose this
# data-plane router from ``meeting_stream_gateway:app``.
control_router = APIRouter(prefix="/meetings/v1", tags=["meeting-stream-control"])
control_router.add_api_route(
    "/sessions/{meeting_id}/stream-ticket",
    create_stream_ticket,
    methods=["POST"],
    response_model=StreamTicketResponse,
)
control_router.add_api_route(
    "/sessions/{meeting_id}/audio-ticket",
    create_audio_ticket,
    methods=["POST"],
    response_model=AudioPlaybackTicketResponse,
)

gateway_router = APIRouter(prefix="/meetings/v1", tags=["meeting-stream-gateway"])
gateway_router.add_api_websocket_route(
    "/sessions/{meeting_id}/audio",
    meeting_audio_stream,
    name="meeting_audio_stream",
)
gateway_router.add_api_route(
    "/sessions/{meeting_id}/audio",
    meeting_audio_replay,
    methods=["GET"],
    response_class=FileResponse,
    name="meeting_audio_replay",
)
