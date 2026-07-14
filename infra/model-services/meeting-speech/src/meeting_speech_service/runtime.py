from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from typing import Any, Callable
from uuid import UUID, uuid4

from meeting_speech_service.adapters import (
    FunASREngine,
    MockSpeechEngine,
    Recognition,
    SessionOptions,
    SpeechEngine,
    SpeechSession,
)
from meeting_speech_service.config import Settings
from meeting_speech_service.metrics import Metrics
from meeting_speech_service.protocol import ProtocolError, StreamStart
from meeting_speech_service.sequencing import FrameSequencer


@dataclass(slots=True)
class StreamSession:
    key: tuple[str, str, int]
    meeting_id: str
    client_stream_id: str
    stream_epoch: int
    trace_id: str
    fingerprint: tuple[Any, ...]
    speech: SpeechSession
    sequencer: FrameSequencer
    active: bool
    paused: bool
    created_monotonic: float
    last_activity_monotonic: float
    total_audio_bytes: int = 0

    @property
    def ack_sequence(self) -> int:
        return self.sequencer.ack_sequence


@dataclass(slots=True)
class FinalizationSession:
    run_id: str
    diarizer_ref: str
    speech: SpeechSession
    next_window_index: int
    last_window_index: int
    last_checksum: str | None
    last_result: tuple[Recognition, ...]
    last_activity_monotonic: float
    completed: bool
    fingerprint: tuple[str | None, tuple[str, ...]]
    lock: asyncio.Lock


class SessionRegistry:
    def __init__(self, *, settings: Settings, engine: SpeechEngine, metrics: Metrics) -> None:
        self._settings = settings
        self._engine = engine
        self._metrics = metrics
        self._lock = asyncio.Lock()
        self._sessions: dict[tuple[str, str, int], StreamSession] = {}

    async def acquire(self, meeting_id: UUID, start: StreamStart) -> tuple[StreamSession, bool]:
        expired: list[StreamSession] = []
        try:
            async with self._lock:
                expired = self._evict_expired_locked(time.monotonic())
                active_count = sum(1 for item in self._sessions.values() if item.active)
                if active_count >= self._settings.max_active_sessions:
                    raise ProtocolError(
                        "SESSION_CAPACITY_REACHED",
                        "active speech session capacity reached",
                        close_code=1013,
                    )

                key = (str(meeting_id), str(start.client_stream_id), start.stream_epoch)
                fingerprint = _stream_fingerprint(start)
                entry = self._sessions.get(key)
                resumed = entry is not None
                if entry is not None:
                    if entry.active:
                        raise ProtocolError("SESSION_LEASE_CONFLICT", "stream already has an active producer")
                    if entry.fingerprint != fingerprint:
                        raise ProtocolError(
                            "SESSION_RESUME_MISMATCH", "resumed stream settings differ from retained state"
                        )
                    entry.sequencer.validate_resume(start.last_acked_sequence)
                    entry.active = True
                    entry.paused = False
                    entry.last_activity_monotonic = time.monotonic()
                else:
                    if start.last_acked_sequence != -1:
                        raise ProtocolError(
                            "RESUME_STATE_NOT_FOUND",
                            "retained ASR state is unavailable; start a new epoch and replay persisted audio",
                            close_code=1013,
                        )
                    if len(self._sessions) >= self._settings.max_resident_sessions:
                        raise ProtocolError(
                            "SESSION_RESIDENT_CAPACITY_REACHED",
                            "retained session capacity reached",
                            close_code=1013,
                        )
                    options = SessionOptions(
                        sample_rate=start.audio.sample_rate,
                        hotwords=tuple(start.hotwords),
                        language=start.language,
                        max_segment_bytes=self._settings.max_segment_bytes,
                        pre_roll_ms=self._settings.pre_roll_ms,
                        vad_min_speech_ms=self._settings.vad_min_speech_ms,
                        vad_endpoint_silence_ms=self._settings.vad_endpoint_silence_ms,
                        vad_energy_threshold=self._settings.vad_energy_threshold,
                        inference_timeout_seconds=self._settings.inference_timeout_seconds,
                    )
                    now = time.monotonic()
                    entry = StreamSession(
                        key=key,
                        meeting_id=str(meeting_id),
                        client_stream_id=str(start.client_stream_id),
                        stream_epoch=start.stream_epoch,
                        trace_id=str(start.trace_id or uuid4()),
                        fingerprint=fingerprint,
                        speech=self._engine.create_session(
                            options,
                            speaker_track_namespace=f"epoch-{start.stream_epoch}",
                        ),
                        sequencer=FrameSequencer(
                            last_acked_sequence=-1,
                            max_pending_frames=self._settings.max_pending_frames,
                            max_pending_bytes=self._settings.max_pending_bytes,
                            max_gap_frames=self._settings.max_gap_frames,
                            recent_checksums=self._settings.recent_sequence_checksums,
                        ),
                        active=True,
                        paused=False,
                        created_monotonic=now,
                        last_activity_monotonic=now,
                    )
                    self._sessions[key] = entry
                self._update_metrics_locked()
        except Exception:
            await _close_sessions(expired)
            raise
        await _close_sessions(expired)
        return entry, resumed

    async def release(self, entry: StreamSession, *, retain: bool) -> None:
        close_target: StreamSession | None = None
        async with self._lock:
            current = self._sessions.get(entry.key)
            if current is not entry:
                return
            if retain:
                entry.active = False
                entry.last_activity_monotonic = time.monotonic()
            else:
                close_target = self._sessions.pop(entry.key)
            self._update_metrics_locked()
        if close_target is not None:
            await close_target.speech.close()

    async def shutdown(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._update_metrics_locked()
        await _close_sessions(sessions)

    def validate_audio_rate(self, entry: StreamSession, payload_bytes: int) -> None:
        next_total = entry.total_audio_bytes + payload_bytes
        max_total = self._settings.max_session_seconds * self._settings.sample_rate * 2
        if next_total > max_total:
            raise ProtocolError("SESSION_DURATION_LIMIT", "maximum session audio duration reached", close_code=1008)
        wall_seconds = max(0.0, time.monotonic() - entry.created_monotonic)
        audio_seconds = next_total / (self._settings.sample_rate * 2)
        allowance = wall_seconds * self._settings.max_realtime_factor + self._settings.rate_burst_seconds
        if audio_seconds > allowance:
            raise ProtocolError(
                "AUDIO_RATE_LIMIT", "audio arrived faster than the configured realtime limit", close_code=1013
            )
        entry.total_audio_bytes = next_total
        entry.last_activity_monotonic = time.monotonic()

    async def counts(self) -> tuple[int, int]:
        expired: list[StreamSession]
        async with self._lock:
            expired = self._evict_expired_locked(time.monotonic())
            active = sum(1 for item in self._sessions.values() if item.active)
            resident = len(self._sessions)
            self._update_metrics_locked()
        await _close_sessions(expired)
        return active, resident

    def _evict_expired_locked(self, now: float) -> list[StreamSession]:
        expired_keys = [
            key
            for key, entry in self._sessions.items()
            if not entry.active and now - entry.last_activity_monotonic >= self._settings.resume_ttl_seconds
        ]
        return [self._sessions.pop(key) for key in expired_keys]

    def _update_metrics_locked(self) -> None:
        self._metrics.set_sessions(
            active=sum(1 for item in self._sessions.values() if item.active),
            resident=len(self._sessions),
        )


class SpeechRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.metrics = Metrics()
        self.engine = _build_engine(
            settings,
            speaker_metrics_observer=self.metrics.record_speaker_assignment,
        )
        self.registry = SessionRegistry(settings=settings, engine=self.engine, metrics=self.metrics)
        self._initialization_task: asyncio.Task[None] | None = None
        self._embedding_lock = asyncio.Lock()
        self._active_embedding_requests = 0
        self._finalization_lock = asyncio.Lock()
        self._finalization_sessions: dict[str, FinalizationSession] = {}

    async def start(self) -> None:
        if not self.settings.enabled:
            return
        if self.settings.adapter == "mock":
            await self.engine.initialize()
            return
        self._initialization_task = asyncio.create_task(self.engine.initialize(), name="meeting-speech-model-init")
        await asyncio.sleep(0)

    async def shutdown(self) -> None:
        await self.registry.shutdown()
        async with self._finalization_lock:
            finalization_sessions = list(self._finalization_sessions.values())
            self._finalization_sessions.clear()
        if finalization_sessions:
            await asyncio.gather(
                *(entry.speech.close() for entry in finalization_sessions),
                return_exceptions=True,
            )
        if self._initialization_task is not None and not self._initialization_task.done():
            self._initialization_task.cancel()
            try:
                await self._initialization_task
            except asyncio.CancelledError:
                pass
        await self.engine.close()

    async def health(self) -> dict[str, object]:
        active, resident = await self.registry.counts()
        snapshot = self.engine.snapshot()
        if not self.settings.enabled:
            status = "disabled"
            core_ready = False
        elif snapshot.state == "ready":
            status = "ready"
            core_ready = True
        elif snapshot.state == "degraded" and snapshot.accepting_streams:
            status = "degraded"
            core_ready = True
        elif snapshot.state == "initializing":
            status = "initializing"
            core_ready = False
        else:
            status = "unavailable"
            core_ready = False
        return {
            "service": "siq-meeting-speech-service",
            "status": status,
            "core_ready": core_ready,
            "production_capable": snapshot.production_capable,
            "adapter": snapshot.adapter,
            "diarizer_ref": self.engine.diarizer_ref,
            "reason_code": snapshot.reason_code,
            "components": snapshot.components,
            "active_sessions": active,
            "resident_sessions": resident,
        }

    async def try_acquire_embedding(self) -> bool:
        async with self._embedding_lock:
            if self._active_embedding_requests >= self.settings.embedding_max_concurrency:
                return False
            self._active_embedding_requests += 1
            return True

    async def release_embedding(self) -> None:
        async with self._embedding_lock:
            self._active_embedding_requests = max(0, self._active_embedding_requests - 1)

    async def finalize_window(
        self,
        *,
        run_id: UUID,
        window_index: int,
        pcm: bytes,
        start_ms: int,
        discontinuity: bool,
        final_window: bool,
        language: str | None,
        hotwords: tuple[str, ...],
    ) -> tuple[str, tuple[Recognition, ...]]:
        """Decode one bounded window while retaining diarization state for the run."""

        run_key = str(run_id)
        checksum = hashlib.sha256(
            b"\0".join(
                (
                    str(window_index).encode("ascii"),
                    str(start_ms).encode("ascii"),
                    b"1" if discontinuity else b"0",
                    b"1" if final_window else b"0",
                    (language or "").encode("utf-8"),
                    "\n".join(hotwords).encode("utf-8"),
                    pcm,
                )
            )
        ).hexdigest()
        expired: list[FinalizationSession] = []
        async with self._finalization_lock:
            now = time.monotonic()
            expired_keys = [
                key
                for key, value in self._finalization_sessions.items()
                if now - value.last_activity_monotonic >= self.settings.finalization_session_ttl_seconds
            ]
            expired = [self._finalization_sessions.pop(key) for key in expired_keys]
            entry = self._finalization_sessions.get(run_key)
            if entry is None:
                if window_index != 0:
                    raise ProtocolError(
                        "FINALIZATION_STATE_NOT_FOUND",
                        "finalization state is unavailable; retry the durable job from window zero",
                    )
                active_count = sum(1 for value in self._finalization_sessions.values() if not value.completed)
                if active_count >= self.settings.finalization_max_sessions:
                    raise ProtocolError(
                        "FINALIZATION_CAPACITY_REACHED",
                        "finalization session capacity reached",
                        close_code=1013,
                    )
                completed = sorted(
                    (value for value in self._finalization_sessions.values() if value.completed),
                    key=lambda value: value.last_activity_monotonic,
                )
                while len(self._finalization_sessions) >= self.settings.finalization_max_sessions * 2 and completed:
                    stale = completed.pop(0)
                    self._finalization_sessions.pop(stale.run_id, None)
                options = SessionOptions(
                    sample_rate=self.settings.sample_rate,
                    hotwords=hotwords,
                    language=language,
                    max_segment_bytes=self.settings.max_segment_bytes,
                    pre_roll_ms=self.settings.pre_roll_ms,
                    vad_min_speech_ms=self.settings.vad_min_speech_ms,
                    vad_endpoint_silence_ms=self.settings.vad_endpoint_silence_ms,
                    vad_energy_threshold=self.settings.vad_energy_threshold,
                    inference_timeout_seconds=self.settings.inference_timeout_seconds,
                    online_decode_enabled=False,
                )
                entry = FinalizationSession(
                    run_id=run_key,
                    diarizer_ref=self.engine.diarizer_ref,
                    speech=self.engine.create_session(
                        options,
                        speaker_track_namespace=f"finalization-{run_key}",
                    ),
                    next_window_index=0,
                    last_window_index=-1,
                    last_checksum=None,
                    last_result=(),
                    last_activity_monotonic=now,
                    completed=False,
                    fingerprint=(language, hotwords),
                    lock=asyncio.Lock(),
                )
                self._finalization_sessions[run_key] = entry
        if expired:
            await asyncio.gather(*(value.speech.close() for value in expired), return_exceptions=True)

        async with entry.lock:
            if entry.fingerprint != (language, hotwords):
                raise ProtocolError(
                    "FINALIZATION_OPTIONS_CONFLICT",
                    "finalization language or hotwords changed during the run",
                )
            if window_index == entry.last_window_index:
                if checksum != entry.last_checksum:
                    raise ProtocolError(
                        "FINALIZATION_WINDOW_CONFLICT",
                        "finalization window index was reused with different content",
                    )
                entry.last_activity_monotonic = time.monotonic()
                return entry.diarizer_ref, entry.last_result
            if entry.completed or window_index != entry.next_window_index:
                raise ProtocolError(
                    "FINALIZATION_SEQUENCE_CONFLICT",
                    "finalization windows must be sent in contiguous order",
                )

            results: list[Recognition] = []
            max_frame_bytes = self.settings.max_chunk_ms * self.settings.sample_rate * 2 // 1_000
            try:
                for offset in range(0, len(pcm), max_frame_bytes):
                    block = pcm[offset : offset + max_frame_bytes]
                    is_last_block = offset + len(block) == len(pcm)
                    results.extend(
                        await entry.speech.ingest(
                            block,
                            capture_time_ms=start_ms + offset * 1_000 // (self.settings.sample_rate * 2),
                            end_of_stream=final_window and is_last_block,
                            discontinuity=discontinuity and offset == 0,
                        )
                    )
            except Exception:
                async with self._finalization_lock:
                    self._finalization_sessions.pop(run_key, None)
                await entry.speech.close()
                raise
            entry.last_window_index = window_index
            entry.next_window_index = window_index + 1
            entry.last_checksum = checksum
            entry.last_result = tuple(results)
            entry.last_activity_monotonic = time.monotonic()
            entry.completed = final_window
            if final_window:
                await entry.speech.close()
            return entry.diarizer_ref, entry.last_result


def _build_engine(
    settings: Settings,
    *,
    speaker_metrics_observer: Callable[[str, str | None], None] | None = None,
) -> SpeechEngine:
    if settings.adapter == "mock":
        return MockSpeechEngine(
            transcript_prefix=settings.mock_transcript_prefix,
            speaker_adapter=settings.speaker_adapter,
            speaker_metrics_observer=speaker_metrics_observer,
        )
    return FunASREngine(
        source_root=settings.funasr_source_root,
        device=settings.device,
        online_model=settings.online_model,
        final_model=settings.final_model,
        finalizer=settings.finalizer,
        http_finalizer_url=settings.http_finalizer_url,
        http_finalizer_health_url=settings.http_finalizer_health_url,
        http_finalizer_timeout_seconds=settings.http_finalizer_timeout_seconds,
        http_finalizer_queue_timeout_seconds=settings.http_finalizer_queue_timeout_seconds,
        http_finalizer_max_concurrency=settings.http_finalizer_max_concurrency,
        http_finalizer_max_response_bytes=settings.http_finalizer_max_response_bytes,
        vad_model=settings.vad_model,
        punctuation_model=settings.punctuation_model,
        online_chunk_size=settings.parsed_online_chunk_size,
        encoder_chunk_look_back=settings.encoder_chunk_look_back,
        decoder_chunk_look_back=settings.decoder_chunk_look_back,
        speaker_adapter=settings.speaker_adapter,
        speaker_model=settings.speaker_model,
        speaker_cluster_threshold=settings.speaker_cluster_threshold,
        speaker_cluster_update_threshold=settings.resolved_speaker_cluster_update_threshold,
        speaker_cluster_min_margin=settings.speaker_cluster_min_margin,
        speaker_candidate_threshold=settings.resolved_speaker_candidate_threshold,
        speaker_candidate_confirmations=settings.speaker_candidate_confirmations,
        speaker_candidate_max_gap_ms=settings.speaker_candidate_max_gap_ms,
        speaker_max_tracks=settings.speaker_max_tracks,
        speaker_min_segment_ms=settings.speaker_min_segment_ms,
        speaker_new_track_min_segment_ms=settings.speaker_new_track_min_segment_ms,
        speaker_max_prototypes=settings.speaker_max_prototypes,
        speaker_min_rms=settings.speaker_min_rms,
        speaker_max_clipping_ratio=settings.speaker_max_clipping_ratio,
        speaker_inference_timeout_seconds=settings.speaker_inference_timeout_seconds,
        embedding_endpoint_enabled=settings.embedding_endpoint_enabled,
        speaker_metrics_observer=speaker_metrics_observer,
    )


def _stream_fingerprint(start: StreamStart) -> tuple[Any, ...]:
    return (
        start.audio.encoding,
        start.audio.sample_rate,
        start.audio.channels,
        start.audio.chunk_ms,
        tuple(start.hotwords),
        start.language,
    )


async def _close_sessions(sessions: list[StreamSession]) -> None:
    if sessions:
        await asyncio.gather(*(entry.speech.close() for entry in sessions), return_exceptions=True)
