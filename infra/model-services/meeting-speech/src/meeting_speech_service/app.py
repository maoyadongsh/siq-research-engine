from __future__ import annotations

import asyncio
import io
import json
import logging
import secrets
import time
import wave
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse

from meeting_speech_service import __version__
from meeting_speech_service.adapters import AdapterUnavailable, Recognition
from meeting_speech_service.config import Settings
from meeting_speech_service.protocol import (
    SPEECH_EVENT_SCHEMA_VERSION,
    AudioFlags,
    ProtocolError,
    StreamHeartbeat,
    StreamPause,
    StreamResume,
    StreamResumeRequest,
    StreamStop,
    decode_audio_frame,
    parse_control_message,
    parse_stream_start,
)
from meeting_speech_service.runtime import SpeechRuntime, StreamSession

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    service_settings = settings or Settings()
    runtime = SpeechRuntime(service_settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await runtime.start()
        try:
            yield
        finally:
            await runtime.shutdown()

    app = FastAPI(
        title="SIQ Meeting Speech Service",
        version=__version__,
        description="Internal bounded streaming ASR service. Browsers must connect through the SIQ meeting gateway.",
        lifespan=lifespan,
    )
    app.state.runtime = runtime

    @app.get("/")
    async def index() -> dict[str, object]:
        return {
            "service": "siq-meeting-speech-service",
            "version": __version__,
            "health": "/health",
            "protocol": "siq.meeting.stream.v1",
        }

    @app.get("/health/live")
    async def liveness() -> dict[str, object]:
        return {"status": "alive", "service": "siq-meeting-speech-service", "version": __version__}

    @app.get("/health")
    async def health() -> JSONResponse:
        snapshot = await runtime.health()
        return JSONResponse(snapshot)

    @app.get("/health/ready")
    async def readiness() -> JSONResponse:
        snapshot = await runtime.health()
        status_code = 200 if snapshot["core_ready"] else 503
        return JSONResponse(snapshot, status_code=status_code)

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics() -> PlainTextResponse:
        snapshot = runtime.engine.snapshot()
        return PlainTextResponse(
            runtime.metrics.render(asr_ready=snapshot.accepting_streams, adapter=snapshot.adapter),
            media_type="text/plain; version=0.0.4",
        )

    @app.post("/v1/speaker/embedding")
    async def speaker_embedding(request: Request) -> JSONResponse:
        return await _serve_speaker_embedding(request, runtime)

    @app.post("/v1/finalize-window")
    async def finalize_window(request: Request) -> JSONResponse:
        return await _serve_finalize_window(request, runtime)

    @app.websocket("/v1/stream/{meeting_id}")
    async def speech_stream(websocket: WebSocket, meeting_id: UUID) -> None:
        await _serve_websocket(websocket, meeting_id, runtime)

    return app


app = create_app()


async def _serve_websocket(websocket: WebSocket, meeting_id: UUID, runtime: SpeechRuntime) -> None:
    if not _authorized(websocket, runtime.settings):
        await websocket.close(code=1008, reason="internal service authentication failed")
        return
    origin = websocket.headers.get("origin")
    if origin and origin not in runtime.settings.allowed_origins:
        await websocket.close(code=1008, reason="origin is not allowed")
        return

    await websocket.accept()
    entry: StreamSession | None = None
    retain_session = True
    try:
        snapshot = runtime.engine.snapshot()
        if not runtime.settings.enabled:
            raise ProtocolError("SERVICE_DISABLED", "meeting speech service is disabled", close_code=1013)
        if not snapshot.accepting_streams:
            raise ProtocolError(snapshot.reason_code or "ASR_NOT_READY", "ASR adapter is not ready", close_code=1013)

        try:
            first_message = await asyncio.wait_for(
                websocket.receive(),
                timeout=runtime.settings.handshake_timeout_seconds,
            )
        except TimeoutError as exc:
            raise ProtocolError("HANDSHAKE_TIMEOUT", "stream.start was not received in time") from exc
        if first_message.get("type") == "websocket.disconnect":
            return
        raw_start = first_message.get("text")
        if not isinstance(raw_start, str):
            raise ProtocolError("STREAM_START_REQUIRED", "first WebSocket message must be stream.start JSON")
        start = parse_stream_start(raw_start)
        if start.meeting_id is not None and start.meeting_id != meeting_id:
            raise ProtocolError("MEETING_ID_MISMATCH", "stream.start meeting_id does not match the URL")
        if not runtime.settings.min_chunk_ms <= start.audio.chunk_ms <= runtime.settings.max_chunk_ms:
            raise ProtocolError("AUDIO_CHUNK_DURATION_INVALID", "requested chunk duration is outside configured bounds")
        if len(start.hotwords) > runtime.settings.max_hotwords:
            raise ProtocolError("HOTWORD_LIMIT", "hotword count exceeds the configured limit")
        if any(len(word) > runtime.settings.max_hotword_chars for word in start.hotwords):
            raise ProtocolError("HOTWORD_LENGTH_LIMIT", "hotword length exceeds the configured limit")

        entry, resumed = await runtime.registry.acquire(meeting_id, start)
        await _send_event(
            websocket,
            entry,
            "stream.ready",
            {
                "resumed": resumed,
                "ack_sequence": entry.ack_sequence,
                "audio": start.audio.model_dump(),
                "adapter": snapshot.adapter,
                "production_capable": snapshot.production_capable,
                "resume_ttl_seconds": runtime.settings.resume_ttl_seconds,
            },
        )
        if snapshot.state == "degraded":
            degraded_scope = "speaker" if snapshot.reason_code and "SPEAKER" in snapshot.reason_code else "asr"
            await _send_event(
                websocket,
                entry,
                "pipeline.degraded",
                {"scope": degraded_scope, "reason_code": snapshot.reason_code, "adapter": snapshot.adapter},
            )

        should_close = await _message_loop(websocket, entry, runtime)
        if should_close:
            retain_session = False
    except ProtocolError as exc:
        retain_session = False
        runtime.metrics.increment("audio_frame", "rejected")
        await _send_error(websocket, entry, meeting_id, exc.code, exc.message)
        await _safe_close(websocket, exc.close_code)
    except WebSocketDisconnect:
        retain_session = True
    except TimeoutError:
        retain_session = False
        await _send_error(websocket, entry, meeting_id, "ASR_TIMEOUT", "speech inference exceeded its time limit")
        await _safe_close(websocket, 1013)
    except Exception as exc:
        retain_session = False
        logger.exception("meeting speech stream failed: %s", type(exc).__name__)
        await _send_error(websocket, entry, meeting_id, "INTERNAL_ERROR", "speech stream failed safely")
        await _safe_close(websocket, 1011)
    finally:
        if entry is not None:
            await runtime.registry.release(entry, retain=retain_session)


async def _message_loop(websocket: WebSocket, entry: StreamSession, runtime: SpeechRuntime) -> bool:
    while True:
        message = await websocket.receive()
        if message.get("type") == "websocket.disconnect":
            raise WebSocketDisconnect(message.get("code", 1006))
        binary = message.get("bytes")
        text = message.get("text")
        if isinstance(binary, bytes):
            if entry.paused:
                raise ProtocolError("AUDIO_WHILE_PAUSED", "audio is not accepted while the stream is paused")
            frame = decode_audio_frame(binary, max_payload_bytes=runtime.settings.max_frame_bytes)
            if frame.stream_epoch != entry.stream_epoch:
                raise ProtocolError("STREAM_EPOCH_MISMATCH", "binary frame epoch does not match stream.start")
            if frame.capture_time_ms > runtime.settings.max_session_seconds * 1_000:
                raise ProtocolError("CAPTURE_TIME_LIMIT", "capture time exceeds maximum meeting duration")
            frame_duration_ms = len(frame.payload) * 1_000 // (runtime.settings.sample_rate * 2)
            if frame_duration_ms > runtime.settings.max_chunk_ms:
                raise ProtocolError("AUDIO_CHUNK_TOO_LONG", "PCM frame duration exceeds the configured limit")

            offer = entry.sequencer.offer(frame)
            if offer.gap is not None:
                missing_from, missing_to = offer.gap
                runtime.metrics.increment("audio_frame", "gap")
                await _send_event(
                    websocket,
                    entry,
                    "audio.gap.detected",
                    {
                        "stream_epoch": entry.stream_epoch,
                        "expected_sequence": offer.expected_before,
                        "received_sequence": offer.received_sequence,
                        "missing_from": missing_from,
                        "missing_to": missing_to,
                        "retryable": True,
                    },
                )
            if offer.pending_frames * 4 >= runtime.settings.max_pending_frames * 3:
                await _send_event(
                    websocket,
                    entry,
                    "flow.control",
                    {
                        "action": "slow_down",
                        "buffered_frames": offer.pending_frames,
                        "buffered_bytes": offer.pending_bytes,
                    },
                )

            saw_end = False
            end_positions = [
                index for index, ready_frame in enumerate(offer.ready) if ready_frame.flags & AudioFlags.END_OF_STREAM
            ]
            if end_positions and (len(end_positions) > 1 or end_positions[0] != len(offer.ready) - 1):
                raise ProtocolError(
                    "AUDIO_AFTER_END_OF_STREAM",
                    "end-of-stream must be the final contiguous audio sequence",
                )
            for ready_frame in offer.ready:
                runtime.registry.validate_audio_rate(entry, len(ready_frame.payload))
                started = time.perf_counter()
                recognitions = await entry.speech.ingest(
                    ready_frame.payload,
                    capture_time_ms=ready_frame.capture_time_ms,
                    end_of_stream=bool(ready_frame.flags & AudioFlags.END_OF_STREAM),
                    discontinuity=bool(ready_frame.flags & AudioFlags.DISCONTINUITY),
                )
                elapsed = time.perf_counter() - started
                await _send_recognitions(websocket, entry, runtime, recognitions, elapsed)
                runtime.metrics.increment("audio_frame", "accepted")
                saw_end = bool(ready_frame.flags & AudioFlags.END_OF_STREAM)
                if saw_end:
                    break
            if offer.duplicate:
                runtime.metrics.increment("audio_frame", "duplicate")
            await _send_ack(websocket, entry, duplicate=offer.duplicate)
            if saw_end:
                await _send_event(websocket, entry, "stream.stopped", {"ack_sequence": entry.ack_sequence})
                await _safe_close(websocket, 1000)
                return True
            continue

        if not isinstance(text, str):
            raise ProtocolError("WEBSOCKET_MESSAGE_INVALID", "WebSocket message must be text or binary")
        control = parse_control_message(text)
        if isinstance(control, StreamPause):
            if not entry.paused:
                recognitions = await entry.speech.flush()
                await _send_recognitions(websocket, entry, runtime, recognitions, 0.0)
                entry.paused = True
            await _send_event(websocket, entry, "stream.paused", {"ack_sequence": entry.ack_sequence})
        elif isinstance(control, StreamResume):
            entry.paused = False
            await _send_event(websocket, entry, "stream.resumed", {"ack_sequence": entry.ack_sequence})
        elif isinstance(control, StreamResumeRequest):
            entry.sequencer.validate_resume(control.last_acked_sequence)
            await _send_ack(websocket, entry, duplicate=True)
        elif isinstance(control, StreamHeartbeat):
            await _send_event(
                websocket,
                entry,
                "stream.heartbeat",
                {"ack_sequence": entry.ack_sequence, "server_monotonic_ms": int(time.monotonic() * 1_000)},
            )
        elif isinstance(control, StreamStop):
            recognitions = await entry.speech.flush()
            await _send_recognitions(websocket, entry, runtime, recognitions, 0.0)
            await _send_event(websocket, entry, "stream.stopped", {"ack_sequence": entry.ack_sequence})
            await _safe_close(websocket, 1000)
            return True


async def _send_recognitions(
    websocket: WebSocket,
    entry: StreamSession,
    runtime: SpeechRuntime,
    recognitions: tuple[Recognition, ...],
    elapsed_seconds: float,
) -> None:
    for recognition in recognitions:
        runtime.metrics.observe_asr_latency(recognition.kind, elapsed_seconds)
        event_type = "asr.partial" if recognition.kind == "partial" else "asr.final"
        payload: dict[str, Any] = {
            "segment_token": recognition.segment_token,
            "text": recognition.text,
            "start_ms": recognition.start_ms,
            "end_ms": recognition.end_ms,
            "adapter": recognition.adapter,
        }
        if recognition.kind == "final":
            payload.update(
                {
                    "finalization_reason": recognition.finalization_reason,
                    "inference_ms": recognition.inference_ms,
                    "speaker_track_key": recognition.speaker_track_key,
                    "speaker_confidence": recognition.speaker_confidence,
                    "source_speaker_hints": list(recognition.source_speaker_hints),
                    "degraded_reason": recognition.degraded_reason,
                    "word_timestamps": [
                        {
                            "token_index": word.token_index,
                            "start_ms": recognition.start_ms + word.start_ms,
                            "end_ms": recognition.start_ms + word.end_ms,
                            "text": word.text,
                        }
                        for word in recognition.word_timings
                    ],
                    "durability": "gateway_pending",
                }
            )
        await _send_event(websocket, entry, event_type, payload)
        if recognition.kind == "final" and recognition.speaker_track_key is not None:
            await _send_event(
                websocket,
                entry,
                "speaker.track.observed",
                {
                    "segment_token": recognition.segment_token,
                    "track_key": recognition.speaker_track_key,
                    "confidence": recognition.speaker_confidence,
                },
            )


async def _send_ack(websocket: WebSocket, entry: StreamSession, *, duplicate: bool) -> None:
    await _send_event(
        websocket,
        entry,
        "audio.ack",
        {
            "stream_epoch": entry.stream_epoch,
            "ack_sequence": entry.ack_sequence,
            "duplicate": duplicate,
            "buffered_frames": entry.sequencer.pending_frames,
            "buffered_bytes": entry.sequencer.pending_bytes,
        },
    )


async def _send_event(websocket: WebSocket, entry: StreamSession, event_type: str, payload: dict[str, Any]) -> None:
    await websocket.send_json(
        {
            "schema_version": SPEECH_EVENT_SCHEMA_VERSION,
            "event_id": str(uuid4()),
            "meeting_id": entry.meeting_id,
            "stream_epoch": entry.stream_epoch,
            "type": event_type,
            "cursor": None,
            "emitted_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "trace_id": entry.trace_id,
            "payload": payload,
        }
    )


async def _send_error(
    websocket: WebSocket,
    entry: StreamSession | None,
    meeting_id: UUID,
    code: str,
    message: str,
) -> None:
    envelope = {
        "schema_version": SPEECH_EVENT_SCHEMA_VERSION,
        "event_id": str(uuid4()),
        "meeting_id": entry.meeting_id if entry else str(meeting_id),
        "stream_epoch": entry.stream_epoch if entry else None,
        "type": "error",
        "cursor": None,
        "emitted_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "trace_id": entry.trace_id if entry else str(uuid4()),
        "payload": {"scope": "asr", "code": code, "message": message, "retryable": code.endswith("TIMEOUT")},
    }
    try:
        await websocket.send_json(envelope)
    except Exception:
        return


def _authorized(websocket: WebSocket, settings: Settings) -> bool:
    configured = settings.internal_service_token
    if configured is None or not configured.get_secret_value().strip():
        return not settings.protected_deployment
    return _token_authorized(websocket.headers.get("x-siq-service-token", ""), settings)


async def _serve_speaker_embedding(request: Request, runtime: SpeechRuntime) -> JSONResponse:
    settings = runtime.settings
    if not settings.embedding_endpoint_enabled:
        return JSONResponse({"detail": "Not found"}, status_code=404)
    if not settings.enabled:
        return JSONResponse({"detail": "Speaker embedding is unavailable", "code": "SERVICE_DISABLED"}, status_code=503)
    if not _token_authorized(request.headers.get("x-siq-service-token", ""), settings):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    speaker_purpose = request.headers.get("x-siq-speaker-purpose", "").strip()
    scope: dict[str, str] | None = None
    if speaker_purpose:
        if speaker_purpose != "diarization":
            return JSONResponse(
                {"detail": "Speaker purpose must be diarization", "code": "EMBEDDING_PURPOSE_INVALID"},
                status_code=400,
            )
        if request.headers.get("x-siq-voiceprint-consent") or request.headers.get("x-siq-voiceprint-purpose"):
            return JSONResponse(
                {
                    "detail": "Diarization and voiceprint request scopes cannot be mixed",
                    "code": "EMBEDDING_SCOPE_CONFLICT",
                },
                status_code=400,
            )
        try:
            meeting_id = UUID(request.headers.get("x-siq-meeting-id", ""))
            run_id = UUID(request.headers.get("x-siq-diarization-run-id", ""))
        except (ValueError, TypeError):
            return JSONResponse(
                {
                    "detail": "Valid meeting and diarization run references are required",
                    "code": "DIARIZATION_SCOPE_INVALID",
                },
                status_code=400,
            )
        purpose = "diarization"
        scope = {"meeting_id": str(meeting_id), "run_id": str(run_id)}
    else:
        consent_reference = request.headers.get("x-siq-voiceprint-consent", "")
        try:
            UUID(consent_reference)
        except (ValueError, TypeError):
            return JSONResponse({"detail": "A valid voiceprint consent reference is required"}, status_code=400)
        purpose = request.headers.get("x-siq-voiceprint-purpose", "")
        if purpose not in {"enrollment", "match"}:
            return JSONResponse({"detail": "Voiceprint purpose must be enrollment or match"}, status_code=400)

    max_pcm_bytes = round(settings.embedding_max_seconds * settings.sample_rate * 2)
    try:
        body = await _read_bounded_request(request, max_pcm_bytes + 65_536)
        pcm = _decode_embedding_audio(body, request.headers.get("x-siq-audio-encoding", "pcm_s16le"))
    except ProtocolError as exc:
        return JSONResponse({"detail": exc.message, "code": exc.code}, status_code=400)
    duration_seconds = len(pcm) / (settings.sample_rate * 2)
    if duration_seconds < settings.embedding_min_seconds or duration_seconds > settings.embedding_max_seconds:
        return JSONResponse(
            {"detail": "Voice sample duration is outside configured bounds", "code": "EMBEDDING_DURATION_INVALID"},
            status_code=400,
        )
    if not await runtime.try_acquire_embedding():
        return JSONResponse(
            {"detail": "Speaker embedding capacity reached", "code": "EMBEDDING_CAPACITY_REACHED"},
            status_code=429,
        )
    try:
        embedding = await runtime.engine.speaker_embedding(pcm)
    except AdapterUnavailable as exc:
        return JSONResponse({"detail": "Speaker embedding is unavailable", "code": exc.code}, status_code=503)
    except Exception as exc:
        logger.error("speaker embedding request failed: %s", type(exc).__name__)
        return JSONResponse(
            {"detail": "Speaker embedding failed safely", "code": "SPEAKER_EMBEDDING_FAILED"},
            status_code=503,
        )
    finally:
        await runtime.release_embedding()
    response: dict[str, object] = {
        "schema_version": "siq.meeting.speaker_embedding.v1",
        "encoder_ref": embedding.encoder_ref,
        "dimension": len(embedding.values),
        "embedding": embedding.values,
        "duration_ms": round(duration_seconds * 1_000),
        "purpose": purpose,
        "persisted": False,
    }
    if scope is not None:
        response["scope"] = scope
    return JSONResponse(response)


async def _serve_finalize_window(request: Request, runtime: SpeechRuntime) -> JSONResponse:
    settings = runtime.settings
    if not settings.finalization_endpoint_enabled:
        return JSONResponse({"detail": "Not found"}, status_code=404)
    if not settings.enabled:
        return JSONResponse(
            {"detail": "Final ASR is unavailable", "code": "SERVICE_DISABLED"},
            status_code=503,
        )
    if not _token_authorized(request.headers.get("x-siq-service-token", ""), settings):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    snapshot = runtime.engine.snapshot()
    if not snapshot.accepting_streams:
        return JSONResponse(
            {
                "detail": "Final ASR is unavailable",
                "code": snapshot.reason_code or "ASR_NOT_READY",
            },
            status_code=503,
        )
    try:
        run_id = UUID(request.headers.get("x-siq-finalization-id", ""))
        window_index = _bounded_int_header(request, "x-siq-window-index", 0, 1_000_000)
        start_ms = _bounded_int_header(
            request,
            "x-siq-window-start-ms",
            0,
            settings.max_session_seconds * 1_000,
        )
        discontinuity = _boolean_header(request, "x-siq-discontinuity", default=False)
        final_window = _boolean_header(request, "x-siq-final-window", default=False)
        language = request.headers.get("x-siq-language", "").strip() or None
        if language is not None and len(language) > 32:
            raise ProtocolError("FINALIZATION_LANGUAGE_INVALID", "finalization language is invalid")
        hotwords = _hotwords_header(request, settings)
        max_pcm_bytes = settings.finalization_max_window_seconds * settings.sample_rate * 2
        pcm = await _read_bounded_request(
            request,
            max_pcm_bytes,
            code="FINALIZATION_WINDOW_TOO_LARGE",
            message="finalization window exceeds its configured byte limit",
        )
        if not pcm or len(pcm) % 2:
            raise ProtocolError(
                "FINALIZATION_PCM_INVALID",
                "finalization audio must contain aligned PCM16 samples",
            )
        diarizer_ref, results = await runtime.finalize_window(
            run_id=run_id,
            window_index=window_index,
            pcm=pcm,
            start_ms=start_ms,
            discontinuity=discontinuity,
            final_window=final_window,
            language=language,
            hotwords=hotwords,
        )
    except (TypeError, ValueError):
        return JSONResponse(
            {"detail": "Finalization headers are invalid", "code": "FINALIZATION_HEADERS_INVALID"},
            status_code=400,
        )
    except ProtocolError as exc:
        status_code = (
            429
            if exc.code == "FINALIZATION_CAPACITY_REACHED"
            else 409
            if exc.code
            in {
                "FINALIZATION_STATE_NOT_FOUND",
                "FINALIZATION_SEQUENCE_CONFLICT",
                "FINALIZATION_WINDOW_CONFLICT",
                "FINALIZATION_OPTIONS_CONFLICT",
            }
            else 400
        )
        return JSONResponse({"detail": exc.message, "code": exc.code}, status_code=status_code)
    except TimeoutError:
        return JSONResponse(
            {"detail": "Final ASR timed out", "code": "FINALIZATION_TIMEOUT"},
            status_code=503,
        )
    except Exception as exc:
        logger.error("meeting finalization window failed: %s", type(exc).__name__)
        return JSONResponse(
            {"detail": "Final ASR failed safely", "code": "FINALIZATION_FAILED"},
            status_code=503,
        )

    return JSONResponse(
        {
            "schema_version": "siq.meeting.final_asr_window.v1",
            "finalization_id": str(run_id),
            "diarizer_ref": diarizer_ref,
            "window_index": window_index,
            "window_start_ms": start_ms,
            "final_window": final_window,
            "segments": [
                {
                    "segment_token": recognition.segment_token,
                    "text": recognition.text,
                    "start_ms": recognition.start_ms,
                    "end_ms": recognition.end_ms,
                    "adapter": recognition.adapter,
                    "speaker_track_key": recognition.speaker_track_key,
                    "speaker_confidence": recognition.speaker_confidence,
                    "word_timestamps": [
                        {
                            "token_index": word.token_index,
                            "start_ms": recognition.start_ms + word.start_ms,
                            "end_ms": recognition.start_ms + word.end_ms,
                            "text": word.text,
                        }
                        for word in recognition.word_timings
                    ],
                    "degraded_reason": recognition.degraded_reason,
                }
                for recognition in results
                if recognition.kind == "final"
            ],
        }
    )


def _token_authorized(actual: str, settings: Settings) -> bool:
    configured = settings.internal_service_token
    if configured is None or not configured.get_secret_value().strip():
        return False
    return secrets.compare_digest(actual, configured.get_secret_value().strip())


async def _read_bounded_request(
    request: Request,
    max_bytes: int,
    *,
    code: str = "EMBEDDING_AUDIO_TOO_LARGE",
    message: str = "Voice sample exceeds the configured byte limit",
) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise ProtocolError(code, message)
        except ValueError as exc:
            raise ProtocolError("CONTENT_LENGTH_INVALID", "Content-Length is invalid") from exc
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise ProtocolError(code, message)
        chunks.append(chunk)
    return b"".join(chunks)


def _bounded_int_header(request: Request, name: str, minimum: int, maximum: int) -> int:
    value = int(request.headers.get(name, ""))
    if value < minimum or value > maximum:
        raise ValueError(name)
    return value


def _boolean_header(request: Request, name: str, *, default: bool) -> bool:
    raw = request.headers.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true"}:
        return True
    if normalized in {"0", "false"}:
        return False
    raise ValueError(name)


def _hotwords_header(request: Request, settings: Settings) -> tuple[str, ...]:
    raw = request.headers.get("x-siq-hotwords", "[]")
    if len(raw) > 8_192:
        raise ProtocolError("HOTWORD_LIMIT", "hotword header exceeds its byte limit")
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError("HOTWORD_INVALID", "hotword header is invalid JSON") from exc
    if (
        not isinstance(values, list)
        or len(values) > settings.max_hotwords
        or any(not isinstance(value, str) or not value or len(value) > settings.max_hotword_chars for value in values)
    ):
        raise ProtocolError("HOTWORD_LIMIT", "hotword header violates configured limits")
    return tuple(values)


def _decode_embedding_audio(body: bytes, encoding: str) -> bytes:
    if encoding == "pcm_s16le":
        if not body or len(body) % 2:
            raise ProtocolError("EMBEDDING_PCM_INVALID", "PCM16LE voice sample is empty or misaligned")
        return body
    if encoding != "wav":
        raise ProtocolError("EMBEDDING_ENCODING_UNSUPPORTED", "Voice sample encoding must be pcm_s16le or wav")
    try:
        with wave.open(io.BytesIO(body), "rb") as wav_file:
            if (
                wav_file.getnchannels() != 1
                or wav_file.getsampwidth() != 2
                or wav_file.getframerate() != 16_000
                or wav_file.getcomptype() != "NONE"
            ):
                raise ProtocolError(
                    "EMBEDDING_WAV_FORMAT_INVALID",
                    "WAV voice sample must be uncompressed 16 kHz mono PCM16",
                )
            pcm = wav_file.readframes(wav_file.getnframes())
    except (EOFError, wave.Error) as exc:
        raise ProtocolError("EMBEDDING_WAV_INVALID", "Voice sample is not a valid WAV file") from exc
    if not pcm or len(pcm) % 2:
        raise ProtocolError("EMBEDDING_WAV_INVALID", "WAV voice sample contains no complete PCM16 samples")
    return pcm


async def _safe_close(websocket: WebSocket, code: int) -> None:
    try:
        await websocket.close(code=code)
    except Exception:
        return
