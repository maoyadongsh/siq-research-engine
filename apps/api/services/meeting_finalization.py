"""Bounded final-ASR adapter for durable meeting post-processing.

Only one verified PCM window is resident at a time. The adapter never sends
filesystem paths to the speech service and never persists speaker embeddings.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol, Sequence
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
from sqlalchemy import or_
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from services.meeting_audio_store import MeetingAudioStore, MeetingAudioStoreError
from services.meeting_contracts import (
    LexiconEntryStatus,
    LexiconScope,
    MeetingAudioChunk,
    MeetingLexiconEntry,
    MeetingSession,
)
from services.meeting_metrics import observe_meeting_latency, record_meeting_counter

FINAL_ASR_WINDOW_SCHEMA = "siq.meeting.final_asr_window.v1"
FINAL_ALIGNMENT_SCHEMA = "siq.meeting.final_transcript_alignment.v1"


class MeetingFinalizationError(RuntimeError):
    public_code = "MEETING_FINALIZATION_FAILED"
    retryable = False


class MeetingFinalizationUnavailable(MeetingFinalizationError):
    public_code = "MEETING_FINAL_ASR_UNAVAILABLE"
    retryable = True


class MeetingFinalizationInputInvalid(MeetingFinalizationError):
    public_code = "MEETING_AUDIO_MANIFEST_INVALID"


class MeetingFinalizationOutputInvalid(MeetingFinalizationError):
    public_code = "MEETING_FINAL_ASR_OUTPUT_INVALID"


@dataclass(frozen=True, slots=True)
class MeetingFinalizationSettings:
    endpoint: str | None
    service_token: str | None
    chunk_page_size: int = 64
    window_seconds: int = 30
    max_chunk_bytes: int = 640_000
    timeout_seconds: float = 60.0
    max_response_bytes: int = 2 * 1024 * 1024
    max_result_segments: int = 50_000

    @property
    def max_window_bytes(self) -> int:
        return self.window_seconds * 16_000 * 2

    @classmethod
    def from_env(cls) -> "MeetingFinalizationSettings":
        endpoint = os.getenv("SIQ_MEETING_FINAL_ASR_URL", "").strip() or None
        if endpoint is None:
            endpoint = _derive_finalization_url(os.getenv("SIQ_MEETING_ASR_WS_URL", "").strip())
        token = (
            os.getenv("SIQ_MEETING_SPEECH_INTERNAL_SERVICE_TOKEN") or os.getenv("SIQ_MEETING_ASR_SERVICE_TOKEN") or ""
        ).strip() or None
        value = cls(
            endpoint=endpoint,
            service_token=token,
            chunk_page_size=_env_int("SIQ_MEETING_FINAL_ASR_CHUNK_PAGE_SIZE", 64, 1, 500),
            window_seconds=_env_int("SIQ_MEETING_FINAL_ASR_WINDOW_SECONDS", 30, 2, 120),
            max_chunk_bytes=_env_int(
                "SIQ_MEETING_FINAL_ASR_MAX_CHUNK_BYTES",
                640_000,
                3_200,
                8 * 1024 * 1024,
            ),
            timeout_seconds=_env_float("SIQ_MEETING_FINAL_ASR_TIMEOUT_SECONDS", 60.0, 1, 300),
            max_response_bytes=_env_int(
                "SIQ_MEETING_FINAL_ASR_MAX_RESPONSE_BYTES",
                2 * 1024 * 1024,
                1_024,
                16 * 1024 * 1024,
            ),
            max_result_segments=_env_int(
                "SIQ_MEETING_FINAL_ASR_MAX_SEGMENTS",
                50_000,
                1,
                200_000,
            ),
        )
        value.validate()
        return value

    def validate(self) -> None:
        if self.max_chunk_bytes > self.max_window_bytes:
            raise ValueError("final ASR chunk bound cannot exceed the window bound")
        if self.endpoint is None:
            return
        parsed = urlsplit(self.endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("final ASR endpoint must be an absolute credential-free HTTP(S) URL")
        if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("final ASR endpoint must use HTTPS or a loopback host")
        if not self.service_token:
            raise ValueError("final ASR endpoint requires the internal speech service token")


@dataclass(frozen=True, slots=True)
class FinalizationWindow:
    index: int
    start_ms: int
    end_ms: int
    pcm: bytes
    discontinuity: bool


@dataclass(frozen=True, slots=True)
class FinalWord:
    token_index: int
    start_ms: int
    end_ms: int
    text: str | None


@dataclass(frozen=True, slots=True)
class FinalASRSegment:
    segment_token: str
    text: str
    start_ms: int
    end_ms: int
    adapter: str
    speaker_track_key: str | None
    speaker_confidence: float | None
    word_timestamps: tuple[FinalWord, ...]
    degraded_reason: str | None
    window_index: int


@dataclass(frozen=True, slots=True)
class FinalizationAnalysis:
    mode: str
    chunk_count: int
    total_audio_bytes: int
    window_count: int
    gaps: tuple[tuple[int, int], ...]
    segments: tuple[FinalASRSegment, ...]


class FinalASRClient(Protocol):
    async def finalize_window(
        self,
        window: FinalizationWindow,
        *,
        run_id: str,
        language: str,
        hotwords: Sequence[str],
        final_window: bool,
    ) -> tuple[FinalASRSegment, ...]: ...


class HttpFinalASRClient:
    def __init__(
        self,
        settings: MeetingFinalizationSettings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        settings.validate()
        if settings.endpoint is None or settings.service_token is None:
            raise ValueError("final ASR HTTP client is not configured")
        self.settings = settings
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.timeout_seconds),
            follow_redirects=False,
            trust_env=False,
        )

    async def finalize_window(
        self,
        window: FinalizationWindow,
        *,
        run_id: str,
        language: str,
        hotwords: Sequence[str],
        final_window: bool,
    ) -> tuple[FinalASRSegment, ...]:
        headers = {
            "x-siq-service-token": self.settings.service_token or "",
            "x-siq-finalization-id": run_id,
            "x-siq-window-index": str(window.index),
            "x-siq-window-start-ms": str(window.start_ms),
            "x-siq-discontinuity": "true" if window.discontinuity else "false",
            "x-siq-final-window": "true" if final_window else "false",
            "x-siq-language": language,
            "x-siq-hotwords": json.dumps(list(hotwords), ensure_ascii=True, separators=(",", ":")),
            "content-type": "application/octet-stream",
        }
        try:
            async with self._client.stream(
                "POST",
                self.settings.endpoint,
                headers=headers,
                content=window.pcm,
            ) as response:
                payload_bytes = await _read_bounded_response(response, self.settings.max_response_bytes)
                if response.status_code != 200:
                    code = _safe_error_code(payload_bytes)
                    if response.status_code in {408, 409, 429} or response.status_code >= 500:
                        raise MeetingFinalizationUnavailable(code)
                    raise MeetingFinalizationOutputInvalid(code)
        except MeetingFinalizationError:
            raise
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise MeetingFinalizationUnavailable("final ASR transport is unavailable") from exc
        return _parse_final_asr_response(payload_bytes, window.index)


@dataclass(slots=True)
class _ManifestStats:
    chunk_count: int = 0
    total_audio_bytes: int = 0
    gaps: list[tuple[int, int]] = field(default_factory=list)


class MeetingFinalizationService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        audio_store: MeetingAudioStore | None = None,
        client: FinalASRClient | None = None,
        settings: MeetingFinalizationSettings | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings or MeetingFinalizationSettings.from_env()
        self.settings.validate()
        self.audio_store = audio_store or MeetingAudioStore()
        self.client = client
        if self.client is None and self.settings.endpoint is not None:
            self.client = HttpFinalASRClient(self.settings)

    async def analyze(self, meeting_id: str) -> FinalizationAnalysis:
        async with self.session_factory() as session:
            meeting = await session.get(MeetingSession, meeting_id)
            if meeting is None:
                raise MeetingFinalizationInputInvalid("meeting session is missing")
            owner_user_id = meeting.owner_user_id
            language = meeting.language
            hotwords = tuple(
                value
                for value in (
                    await session.exec(
                        select(MeetingLexiconEntry.canonical_term)
                        .where(
                            MeetingLexiconEntry.owner_user_id == owner_user_id,
                            MeetingLexiconEntry.language == language,
                            MeetingLexiconEntry.status == LexiconEntryStatus.ACTIVE.value,
                            or_(
                                MeetingLexiconEntry.scope == LexiconScope.USER_FUTURE_MEETINGS.value,
                                MeetingLexiconEntry.meeting_id == meeting_id,
                            ),
                        )
                        .order_by(MeetingLexiconEntry.weight.desc())
                        .limit(100)
                    )
                ).all()
                if value
            )

        stats = _ManifestStats()
        pending: FinalizationWindow | None = None
        results: list[FinalASRSegment] = []
        window_count = 0
        run_id = str(uuid4())
        processing_started = time.perf_counter()
        async for window in self._windows(meeting_id, owner_user_id, stats):
            if self.client is None:
                raise MeetingFinalizationUnavailable("final ASR endpoint is not configured")
            if pending is not None:
                values = await self._finalize_window(
                    pending,
                    run_id=run_id,
                    language=language,
                    hotwords=hotwords,
                    final_window=False,
                )
                results.extend(values)
                window_count += 1
                self._check_result_bound(results)
            pending = window
        if pending is None:
            return FinalizationAnalysis(
                mode="stable_transcript_passthrough",
                chunk_count=0,
                total_audio_bytes=0,
                window_count=0,
                gaps=(),
                segments=(),
            )
        if self.client is None:
            raise MeetingFinalizationUnavailable("final ASR endpoint is not configured")
        values = await self._finalize_window(
            pending,
            run_id=run_id,
            language=language,
            hotwords=hotwords,
            final_window=True,
        )
        results.extend(values)
        window_count += 1
        self._check_result_bound(results)
        observe_meeting_latency(
            "final_asr_job_processing_seconds",
            time.perf_counter() - processing_started,
        )
        return FinalizationAnalysis(
            mode="final_asr",
            chunk_count=stats.chunk_count,
            total_audio_bytes=stats.total_audio_bytes,
            window_count=window_count,
            gaps=tuple(stats.gaps),
            segments=tuple(results),
        )

    async def _finalize_window(
        self,
        window: FinalizationWindow,
        *,
        run_id: str,
        language: str,
        hotwords: Sequence[str],
        final_window: bool,
    ) -> tuple[FinalASRSegment, ...]:
        if self.client is None:
            raise MeetingFinalizationUnavailable("final ASR endpoint is not configured")
        started = time.perf_counter()
        try:
            values = await self.client.finalize_window(
                window,
                run_id=run_id,
                language=language,
                hotwords=hotwords,
                final_window=final_window,
            )
        except MeetingFinalizationError as exc:
            record_meeting_counter(
                "final_asr_window",
                "retryable_failure" if exc.retryable else "permanent_failure",
            )
            raise
        except Exception:
            record_meeting_counter("final_asr_window", "retryable_failure")
            raise
        observe_meeting_latency(
            "final_asr_window_processing_seconds",
            time.perf_counter() - started,
        )
        record_meeting_counter("final_asr_window", "succeeded")
        return values

    def _check_result_bound(self, values: Sequence[FinalASRSegment]) -> None:
        if len(values) > self.settings.max_result_segments:
            raise MeetingFinalizationOutputInvalid("final ASR segment limit exceeded")

    async def _windows(
        self,
        meeting_id: str,
        owner_user_id: int,
        stats: _ManifestStats,
    ) -> AsyncIterator[FinalizationWindow]:
        buffer = bytearray()
        window_start = 0
        window_end = 0
        window_index = 0
        next_discontinuity = False
        previous_audio_end: int | None = None
        offset = 0
        while True:
            async with self.session_factory() as session:
                chunks = list(
                    (
                        await session.exec(
                            select(MeetingAudioChunk)
                            .where(MeetingAudioChunk.meeting_id == meeting_id)
                            .order_by(
                                MeetingAudioChunk.start_ms,
                                MeetingAudioChunk.stream_epoch,
                                MeetingAudioChunk.sequence,
                                MeetingAudioChunk.id,
                            )
                            .offset(offset)
                            .limit(self.settings.chunk_page_size)
                        )
                    ).all()
                )
            if not chunks:
                break
            offset += len(chunks)
            for chunk in chunks:
                try:
                    payload = await asyncio.to_thread(
                        self.audio_store.read_verified_chunk,
                        owner_user_id,
                        meeting_id,
                        chunk,
                        max_bytes=self.settings.max_chunk_bytes,
                    )
                except MeetingAudioStoreError as exc:
                    raise MeetingFinalizationInputInvalid(exc.code) from exc
                stats.chunk_count += 1
                stats.total_audio_bytes += len(payload)
                effective_start = chunk.start_ms
                if previous_audio_end is not None and effective_start > previous_audio_end:
                    if buffer:
                        yield FinalizationWindow(
                            index=window_index,
                            start_ms=window_start,
                            end_ms=window_end,
                            pcm=bytes(buffer),
                            discontinuity=next_discontinuity,
                        )
                        window_index += 1
                        buffer.clear()
                    stats.gaps.append((previous_audio_end, effective_start))
                    next_discontinuity = True
                elif previous_audio_end is not None and effective_start < previous_audio_end:
                    overlap_ms = min(chunk.duration_ms, previous_audio_end - effective_start)
                    payload = payload[overlap_ms * 32 :]
                    effective_start += overlap_ms
                    if not payload:
                        continue

                if buffer and len(buffer) + len(payload) > self.settings.max_window_bytes:
                    yield FinalizationWindow(
                        index=window_index,
                        start_ms=window_start,
                        end_ms=window_end,
                        pcm=bytes(buffer),
                        discontinuity=next_discontinuity,
                    )
                    window_index += 1
                    buffer.clear()
                    next_discontinuity = False
                if not buffer:
                    window_start = effective_start
                buffer.extend(payload)
                window_end = effective_start + len(payload) // 32
                previous_audio_end = max(previous_audio_end or 0, chunk.start_ms + chunk.duration_ms)
        if buffer:
            yield FinalizationWindow(
                index=window_index,
                start_ms=window_start,
                end_ms=window_end,
                pcm=bytes(buffer),
                discontinuity=next_discontinuity,
            )


def align_final_segments(
    stable_segments: Sequence[Any],
    final_segments: Sequence[FinalASRSegment],
) -> list[dict[str, Any]]:
    """Map final decoder output to stable IDs without mutating either layer."""

    alignments: list[dict[str, Any]] = []
    for final in final_segments:
        overlaps = [
            (max(0, min(stable.end_ms, final.end_ms) - max(stable.start_ms, final.start_ms)), stable)
            for stable in stable_segments
            if stable.end_ms > final.start_ms and stable.start_ms < final.end_ms
        ]
        if not overlaps:
            continue
        words_by_segment: dict[str, list[FinalWord]] = {}
        if final.word_timestamps:
            for word in final.word_timestamps:
                midpoint = (word.start_ms + word.end_ms) // 2
                target = next(
                    (stable for _, stable in overlaps if stable.start_ms <= midpoint <= stable.end_ms),
                    None,
                )
                if target is not None:
                    words_by_segment.setdefault(target.id, []).append(word)
        if words_by_segment:
            targets = [stable for _, stable in overlaps if stable.id in words_by_segment]
        else:
            targets = [max(overlaps, key=lambda item: (item[0], -item[1].ordinal))[1]]
        for target in targets:
            words = words_by_segment.get(target.id, [])
            text = _join_words(words) if words else final.text
            if not text.strip():
                continue
            alignments.append(
                {
                    "stable_segment_id": target.id,
                    "stable_ordinal": target.ordinal,
                    "final_segment_token": final.segment_token,
                    "final_text": text.strip(),
                    "final_start_ms": final.start_ms,
                    "final_end_ms": final.end_ms,
                    "speaker_track_key": final.speaker_track_key,
                    "speaker_confidence": final.speaker_confidence,
                    "window_index": final.window_index,
                    "word_timestamps": [
                        {
                            "token_index": word.token_index,
                            "start_ms": word.start_ms,
                            "end_ms": word.end_ms,
                            "text": word.text,
                        }
                        for word in words
                    ],
                }
            )
    return alignments


def _join_words(words: Sequence[FinalWord]) -> str:
    values = [word.text for word in words if word.text]
    if not values:
        return ""
    if all(len(value) == 1 and "\u4e00" <= value <= "\u9fff" for value in values):
        return "".join(values)
    return " ".join(values)


def _parse_final_asr_response(payload_bytes: bytes, window_index: int) -> tuple[FinalASRSegment, ...]:
    try:
        payload = json.loads(payload_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MeetingFinalizationOutputInvalid("final ASR response is not valid JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != FINAL_ASR_WINDOW_SCHEMA:
        raise MeetingFinalizationOutputInvalid("final ASR schema is invalid")
    values = payload.get("segments")
    if not isinstance(values, list) or len(values) > 10_000:
        raise MeetingFinalizationOutputInvalid("final ASR segments are invalid")
    parsed: list[FinalASRSegment] = []
    for item in values:
        if not isinstance(item, dict):
            raise MeetingFinalizationOutputInvalid("final ASR segment is invalid")
        try:
            start_ms = int(item["start_ms"])
            end_ms = int(item["end_ms"])
            confidence = item.get("speaker_confidence")
            confidence = None if confidence is None else float(confidence)
            words = _parse_words(item.get("word_timestamps", []))
            value = FinalASRSegment(
                segment_token=_bounded_string(item["segment_token"], 128),
                text=_bounded_string(item["text"], 100_000),
                start_ms=start_ms,
                end_ms=end_ms,
                adapter=_bounded_string(item["adapter"], 100),
                speaker_track_key=_optional_string(item.get("speaker_track_key"), 128),
                speaker_confidence=confidence,
                word_timestamps=words,
                degraded_reason=_optional_string(item.get("degraded_reason"), 100),
                window_index=window_index,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MeetingFinalizationOutputInvalid("final ASR segment fields are invalid") from exc
        if (
            value.start_ms < 0
            or value.end_ms < value.start_ms
            or not value.text.strip()
            or (value.speaker_confidence is not None and not 0 <= value.speaker_confidence <= 1)
        ):
            raise MeetingFinalizationOutputInvalid("final ASR segment bounds are invalid")
        parsed.append(value)
    return tuple(parsed)


def _parse_words(value: Any) -> tuple[FinalWord, ...]:
    if not isinstance(value, list) or len(value) > 10_000:
        raise ValueError("word timestamps are invalid")
    words: list[FinalWord] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("word timestamp is invalid")
        word = FinalWord(
            token_index=int(item["token_index"]),
            start_ms=int(item["start_ms"]),
            end_ms=int(item["end_ms"]),
            text=_optional_string(item.get("text"), 1_000),
        )
        if word.token_index < 0 or word.start_ms < 0 or word.end_ms < word.start_ms:
            raise ValueError("word timestamp bounds are invalid")
        words.append(word)
    return tuple(words)


def _bounded_string(value: Any, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError("string field is invalid")
    return value


def _optional_string(value: Any, maximum: int) -> str | None:
    if value is None:
        return None
    return _bounded_string(value, maximum)


async def _read_bounded_response(response: httpx.Response, maximum: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > maximum:
            raise MeetingFinalizationOutputInvalid("final ASR response exceeded its byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _safe_error_code(payload: bytes) -> str:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "FINAL_ASR_SERVICE_ERROR"
    code = value.get("code") if isinstance(value, dict) else None
    if isinstance(code, str) and code and len(code) <= 64 and code.replace("_", "").isalnum():
        return code
    return "FINAL_ASR_SERVICE_ERROR"


def _derive_finalization_url(websocket_url: str) -> str | None:
    if not websocket_url:
        return None
    parsed = urlsplit(websocket_url)
    if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
        return None
    path = parsed.path
    marker = "/v1/stream/{meeting_id}"
    if not path.endswith(marker):
        return None
    return urlunsplit(
        (
            "https" if parsed.scheme == "wss" else "http",
            parsed.netloc,
            path[: -len(marker)] + "/v1/finalize-window",
            "",
            "",
        )
    )


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(value) or value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


__all__ = [
    "FINAL_ALIGNMENT_SCHEMA",
    "FinalASRClient",
    "FinalASRSegment",
    "FinalWord",
    "FinalizationAnalysis",
    "FinalizationWindow",
    "HttpFinalASRClient",
    "MeetingFinalizationError",
    "MeetingFinalizationInputInvalid",
    "MeetingFinalizationOutputInvalid",
    "MeetingFinalizationService",
    "MeetingFinalizationSettings",
    "MeetingFinalizationUnavailable",
    "align_final_segments",
]
