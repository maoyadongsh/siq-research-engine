from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Literal, Protocol
from uuid import uuid4

import numpy as np

from meeting_speech_service.adapters.base import Recognition, SessionOptions, SpeechSession, WordTiming
from meeting_speech_service.protocol import ProtocolError


@dataclass(frozen=True, slots=True)
class VadDecision:
    started: bool
    speaking: bool
    endpoint: bool
    trailing_silence_ms: int = 0


@dataclass(frozen=True, slots=True)
class OnlineDecode:
    text: str
    is_delta: bool = True


@dataclass(frozen=True, slots=True)
class FinalDecode:
    text: str
    word_timings: tuple[WordTiming, ...] = ()
    source_speaker_hints: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SpeakerAssignment:
    track_key: str
    confidence: float | None = None
    track_result: Literal["created", "reused"] = "reused"


class Vad(Protocol):
    @property
    def speaking(self) -> bool: ...

    def process(self, pcm: bytes, *, is_final: bool) -> VadDecision: ...

    def reset(self) -> None: ...


class Decoder(Protocol):
    def online(self, pcm: bytes, *, cache: dict[str, object], is_final: bool) -> OnlineDecode: ...

    def final(self, pcm: bytes) -> FinalDecode: ...


class SpeakerHook(Protocol):
    def assign(self, pcm: bytes, *, start_ms: int, end_ms: int) -> SpeakerAssignment | None: ...


class NullSpeakerHook:
    def assign(self, pcm: bytes, *, start_ms: int, end_ms: int) -> SpeakerAssignment | None:
        return None


class MockSpeakerHook:
    def __init__(self, *, track_namespace: str | None = None) -> None:
        self._track_namespace = track_namespace.strip() if track_namespace else None
        self._created = False

    def assign(self, pcm: bytes, *, start_ms: int, end_ms: int) -> SpeakerAssignment | None:
        local_key = "mock-speaker-0"
        track_key = f"{self._track_namespace}:{local_key}" if self._track_namespace else local_key
        track_result = "reused" if self._created else "created"
        self._created = True
        return SpeakerAssignment(track_key=track_key, confidence=1.0, track_result=track_result)


class EnergyVad:
    def __init__(self, *, sample_rate: int, threshold: float, min_speech_ms: int, endpoint_silence_ms: int) -> None:
        self._sample_rate = sample_rate
        self._threshold = threshold
        self._min_speech_ms = min_speech_ms
        self._endpoint_silence_ms = endpoint_silence_ms
        self._speaking = False
        self._speech_candidate_ms = 0
        self._silence_ms = 0

    @property
    def speaking(self) -> bool:
        return self._speaking

    def process(self, pcm: bytes, *, is_final: bool) -> VadDecision:
        duration_ms = _pcm_duration_ms(pcm, self._sample_rate)
        samples = _pcm_to_float32(pcm)
        rms = float(np.sqrt(np.mean(np.square(samples), dtype=np.float64))) if samples.size else 0.0
        voiced = rms >= self._threshold
        started = False
        endpoint = False
        if voiced:
            self._silence_ms = 0
            if not self._speaking:
                self._speech_candidate_ms += duration_ms
                if self._speech_candidate_ms >= self._min_speech_ms:
                    self._speaking = True
                    started = True
        elif self._speaking:
            self._silence_ms += duration_ms
            if self._silence_ms >= self._endpoint_silence_ms:
                self._speaking = False
                endpoint = True
        else:
            self._speech_candidate_ms = 0
        if is_final and self._speaking:
            self._speaking = False
            endpoint = True
        trailing = self._silence_ms if endpoint else 0
        if endpoint:
            self._speech_candidate_ms = 0
            self._silence_ms = 0
        return VadDecision(started=started, speaking=self._speaking, endpoint=endpoint, trailing_silence_ms=trailing)

    def reset(self) -> None:
        self._speaking = False
        self._speech_candidate_ms = 0
        self._silence_ms = 0


class BufferedRecognitionSession(SpeechSession):
    """Runs synchronous model adapters off-loop while keeping all audio buffers bounded."""

    def __init__(
        self,
        *,
        adapter_name: str,
        options: SessionOptions,
        vad: Vad,
        decoder: Decoder,
        speaker: SpeakerHook | None = None,
        speaker_metrics_observer: Callable[[str, str | None], None] | None = None,
    ) -> None:
        self._adapter_name = adapter_name
        self._options = options
        self._vad = vad
        self._decoder = decoder
        self._speaker = speaker or NullSpeakerHook()
        self._speaker_metrics_observer = speaker_metrics_observer
        self._segment = bytearray()
        self._segment_start_ms: int | None = None
        self._segment_token: str | None = None
        self._online_cache: dict[str, object] = {}
        self._partial_text = ""
        self._last_partial_emitted = ""
        self._last_capture_end_ms = 0
        self._closed = False
        self._inflight: asyncio.Future[tuple[Recognition, ...]] | None = None
        self._pre_roll: deque[tuple[int, bytes]] = deque()
        self._pre_roll_bytes = 0
        self._max_pre_roll_bytes = options.pre_roll_ms * options.sample_rate * 2 // 1_000
        self._async_lock = asyncio.Lock()

    async def ingest(
        self,
        pcm: bytes,
        *,
        capture_time_ms: int,
        end_of_stream: bool = False,
        discontinuity: bool = False,
    ) -> tuple[Recognition, ...]:
        async with self._async_lock:
            if self._closed:
                raise RuntimeError("speech session is closed")
            loop = asyncio.get_running_loop()
            operation = loop.run_in_executor(
                None,
                self._ingest_sync,
                bytes(pcm),
                capture_time_ms,
                end_of_stream,
                discontinuity,
            )
            self._inflight = operation
            try:
                return await asyncio.wait_for(
                    asyncio.shield(operation),
                    timeout=self._options.inference_timeout_seconds,
                )
            finally:
                if operation.done():
                    self._inflight = None

    async def flush(self) -> tuple[Recognition, ...]:
        return await self.ingest(b"", capture_time_ms=self._last_capture_end_ms, end_of_stream=True)

    async def close(self) -> None:
        async with self._async_lock:
            self._closed = True
            if self._inflight is not None and not self._inflight.done():
                self._inflight.add_done_callback(lambda _future: self._cleanup_after_inflight())
            else:
                self._cleanup_after_inflight()

    def _cleanup_after_inflight(self) -> None:
        self._inflight = None
        self._clear_segment()
        self._pre_roll.clear()
        self._pre_roll_bytes = 0
        self._vad.reset()

    def _ingest_sync(
        self,
        pcm: bytes,
        capture_time_ms: int,
        end_of_stream: bool,
        discontinuity: bool,
    ) -> tuple[Recognition, ...]:
        results: list[Recognition] = []
        if discontinuity:
            results.extend(self._finalize_segment("discontinuity", self._last_capture_end_ms))
            self._vad.reset()
            self._pre_roll.clear()
            self._pre_roll_bytes = 0

        duration_ms = _pcm_duration_ms(pcm, self._options.sample_rate)
        capture_end_ms = capture_time_ms + duration_ms
        if pcm and capture_time_ms < self._last_capture_end_ms:
            raise ProtocolError(
                "AUDIO_CAPTURE_TIME_REGRESSION",
                "audio capture timestamps must be monotonic and non-overlapping",
            )
        self._last_capture_end_ms = max(self._last_capture_end_ms, capture_end_ms)

        was_speaking = self._vad.speaking
        decision = self._vad.process(pcm, is_final=end_of_stream)
        belongs_to_segment = bool(pcm) and (was_speaking or decision.started or decision.speaking or decision.endpoint)

        if belongs_to_segment:
            prefix = b""
            segment_start_ms = capture_time_ms
            if not self._segment:
                prefix, segment_start_ms = self._consume_pre_roll(capture_time_ms)
                self._begin_segment(segment_start_ms)
            incoming = prefix + pcm
            if self._segment and len(self._segment) + len(incoming) > self._options.max_segment_bytes:
                results.extend(self._finalize_segment("max_segment_duration", capture_time_ms))
                self._begin_segment(capture_time_ms)
                incoming = pcm
            if len(incoming) > self._options.max_segment_bytes:
                incoming = incoming[-self._options.max_segment_bytes :]
                self._segment_start_ms = max(0, capture_end_ms - _pcm_duration_ms(incoming, self._options.sample_rate))
            self._segment.extend(incoming)
            if self._options.online_decode_enabled:
                online = self._decoder.online(incoming, cache=self._online_cache, is_final=False)
                if online.text:
                    self._partial_text = self._partial_text + online.text if online.is_delta else online.text
                    if self._partial_text != self._last_partial_emitted:
                        results.append(self._partial_result(capture_end_ms))
                        self._last_partial_emitted = self._partial_text
        elif pcm:
            self._remember_pre_roll(capture_time_ms, pcm)

        if decision.endpoint and self._segment:
            speech_end_ms = max(
                self._segment_start_ms or 0,
                capture_end_ms - decision.trailing_silence_ms,
            )
            results.extend(self._finalize_segment("vad_endpoint", speech_end_ms))
        elif end_of_stream and self._segment:
            results.extend(self._finalize_segment("stream_stop", capture_end_ms))
        return tuple(results)

    def _begin_segment(self, start_ms: int) -> None:
        self._segment_start_ms = max(0, start_ms)
        self._segment_token = str(uuid4())
        self._online_cache = {}
        self._partial_text = ""
        self._last_partial_emitted = ""

    def _partial_result(self, end_ms: int) -> Recognition:
        return Recognition(
            kind="partial",
            segment_token=self._segment_token or str(uuid4()),
            text=self._partial_text,
            start_ms=self._segment_start_ms or 0,
            end_ms=max(self._segment_start_ms or 0, end_ms),
            adapter=self._adapter_name,
        )

    def _finalize_segment(self, reason: str, end_ms: int) -> tuple[Recognition, ...]:
        if not self._segment or self._segment_token is None:
            self._clear_segment()
            return ()
        started = time.perf_counter()
        decoded = self._decoder.final(bytes(self._segment))
        inference_ms = int((time.perf_counter() - started) * 1_000)
        text = decoded.text.strip()
        degraded_reason = None
        if not text and self._partial_text.strip():
            text = self._partial_text.strip()
            degraded_reason = "FINAL_EMPTY_ONLINE_FALLBACK"
        result: Recognition | None = None
        if text:
            start_ms = self._segment_start_ms or 0
            try:
                assignment = self._speaker.assign(
                    bytes(self._segment),
                    start_ms=start_ms,
                    end_ms=max(start_ms, end_ms),
                )
            except Exception:
                assignment = None
                degraded_reason = degraded_reason or "SPEAKER_ASSIGNMENT_FAILED"
                self._observe_speaker_result("failed", None)
            else:
                if assignment is None:
                    self._observe_speaker_result("unassigned", None)
                else:
                    self._observe_speaker_result("assigned", assignment.track_result)
            result = Recognition(
                kind="final",
                segment_token=self._segment_token,
                text=text,
                start_ms=start_ms,
                end_ms=max(start_ms, end_ms),
                adapter=self._adapter_name,
                finalization_reason=reason,
                speaker_track_key=assignment.track_key if assignment else None,
                speaker_confidence=assignment.confidence if assignment else None,
                word_timings=decoded.word_timings,
                degraded_reason=degraded_reason,
                inference_ms=inference_ms,
                source_speaker_hints=decoded.source_speaker_hints,
            )
        self._clear_segment()
        if result is None:
            return ()
        return (result,)

    def _observe_speaker_result(self, result: str, track_result: str | None) -> None:
        if self._speaker_metrics_observer is None:
            return
        try:
            self._speaker_metrics_observer(result, track_result)
        except Exception:
            # Telemetry must never change transcription availability.
            return

    def _clear_segment(self) -> None:
        self._segment.clear()
        self._segment_start_ms = None
        self._segment_token = None
        self._online_cache = {}
        self._partial_text = ""
        self._last_partial_emitted = ""

    def _remember_pre_roll(self, capture_time_ms: int, pcm: bytes) -> None:
        if self._max_pre_roll_bytes == 0:
            return
        data = pcm
        start_ms = capture_time_ms
        if len(data) > self._max_pre_roll_bytes:
            data = data[-self._max_pre_roll_bytes :]
            start_ms = capture_time_ms + _pcm_duration_ms(pcm[: -self._max_pre_roll_bytes], self._options.sample_rate)
        self._pre_roll.append((start_ms, data))
        self._pre_roll_bytes += len(data)
        while self._pre_roll and self._pre_roll_bytes > self._max_pre_roll_bytes:
            _, removed = self._pre_roll.popleft()
            self._pre_roll_bytes -= len(removed)

    def _consume_pre_roll(self, fallback_start_ms: int) -> tuple[bytes, int]:
        if not self._pre_roll:
            return b"", fallback_start_ms
        start_ms = self._pre_roll[0][0]
        data = b"".join(item[1] for item in self._pre_roll)
        self._pre_roll.clear()
        self._pre_roll_bytes = 0
        return data, start_ms


def _pcm_to_float32(pcm: bytes) -> np.ndarray:
    if not pcm:
        return np.empty(0, dtype=np.float32)
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


def _pcm_duration_ms(pcm: bytes, sample_rate: int) -> int:
    return len(pcm) * 1_000 // (sample_rate * 2)
