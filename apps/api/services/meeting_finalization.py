"""Bounded final-ASR adapter for durable meeting post-processing.

Only a configured, bounded number of verified PCM windows are resident at a
time. The adapter never sends filesystem paths to the speech service and never
persists speaker embeddings.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import time
from dataclasses import dataclass, field, replace
from typing import Any, AsyncIterator, Protocol, Sequence
from urllib.parse import urlsplit, urlunsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

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
FINAL_ASR_INDEPENDENT_PROTOCOL = "siq.meeting.final_asr.independent_window.v1"
_DIARIZER_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{2,191}")


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
    max_concurrency: int = 2
    window_overlap_ms: int = 2_000

    @property
    def max_window_bytes(self) -> int:
        return self.window_seconds * 16_000 * 2

    @property
    def overlap_bytes(self) -> int:
        return self.window_overlap_ms * 32

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
            max_concurrency=_env_int("SIQ_MEETING_FINAL_ASR_MAX_CONCURRENCY", 2, 1, 8),
            window_overlap_ms=_env_int("SIQ_MEETING_FINAL_ASR_WINDOW_OVERLAP_MS", 2_000, 0, 30_000),
        )
        value.validate()
        return value

    def validate(self) -> None:
        if self.max_chunk_bytes > self.max_window_bytes:
            raise ValueError("final ASR chunk bound cannot exceed the window bound")
        if not 1 <= self.max_concurrency <= 8:
            raise ValueError("final ASR concurrency must be between 1 and 8")
        if not 0 <= self.window_overlap_ms <= min(30_000, self.window_seconds * 1_000 // 2):
            raise ValueError("final ASR window overlap must be at most half the window")
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
    diarizer_ref: str

    def __post_init__(self) -> None:
        _validate_diarizer_ref(self.diarizer_ref)


@dataclass(frozen=True, slots=True)
class FinalASRWindowResult:
    diarizer_ref: str
    segments: tuple[FinalASRSegment, ...]
    protocol_version: str = FINAL_ASR_INDEPENDENT_PROTOCOL

    def __post_init__(self) -> None:
        _validate_diarizer_ref(self.diarizer_ref)
        if not self.protocol_version or len(self.protocol_version) > 128:
            raise MeetingFinalizationOutputInvalid("final ASR protocol identity is invalid")
        if any(value.diarizer_ref != self.diarizer_ref for value in self.segments):
            raise MeetingFinalizationOutputInvalid("final ASR window diarizer identity is inconsistent")


@dataclass(frozen=True, slots=True)
class FinalizationAnalysis:
    mode: str
    chunk_count: int
    total_audio_bytes: int
    window_count: int
    gaps: tuple[tuple[int, int], ...]
    segments: tuple[FinalASRSegment, ...]
    diarizer_ref: str | None
    protocol_version: str | None = None
    window_overlap_ms: int = 0
    max_concurrency: int = 1
    boundary_trimmed_segment_count: int = 0

    def __post_init__(self) -> None:
        if self.mode == "stable_transcript_passthrough":
            if self.diarizer_ref is not None:
                raise MeetingFinalizationOutputInvalid("passthrough analysis cannot claim a diarizer identity")
            return
        if self.mode != "final_asr" or self.diarizer_ref is None:
            raise MeetingFinalizationOutputInvalid("final ASR analysis diarizer identity is missing")
        _validate_diarizer_ref(self.diarizer_ref)
        if any(value.diarizer_ref != self.diarizer_ref for value in self.segments):
            raise MeetingFinalizationOutputInvalid("final ASR analysis contains mixed diarizer identities")
        if self.protocol_version != FINAL_ASR_INDEPENDENT_PROTOCOL:
            raise MeetingFinalizationOutputInvalid("final ASR analysis protocol identity is invalid")
        if self.window_overlap_ms < 0 or self.max_concurrency < 1 or self.boundary_trimmed_segment_count < 0:
            raise MeetingFinalizationOutputInvalid("final ASR analysis bounds are invalid")


class FinalASRClient(Protocol):
    async def finalize_window(
        self,
        window: FinalizationWindow,
        *,
        run_id: str,
        language: str,
        hotwords: Sequence[str],
        final_window: bool,
    ) -> FinalASRWindowResult: ...


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
    ) -> FinalASRWindowResult:
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
            "x-siq-finalization-protocol": FINAL_ASR_INDEPENDENT_PROTOCOL,
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
        return _parse_final_asr_response(payload_bytes, window.index, FINAL_ASR_INDEPENDENT_PROTOCOL)


@dataclass(slots=True)
class _ManifestStats:
    chunk_count: int = 0
    total_audio_bytes: int = 0
    gaps: list[tuple[int, int]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _WindowBoundary:
    index: int
    start_ms: int
    end_ms: int
    discontinuity: bool


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

    async def analyze(self, meeting_id: str, *, run_id: str | None = None) -> FinalizationAnalysis:
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
        boundaries: list[_WindowBoundary] = []
        results_by_window: dict[int, FinalASRWindowResult] = {}
        observed_diarizer_ref: str | None = None
        raw_segment_count = 0
        finalization_id = _normalize_run_id(run_id)
        in_flight: dict[asyncio.Task[FinalASRWindowResult], int] = {}
        processing_started = time.perf_counter()

        async def collect_completed(*, wait_for_one: bool) -> None:
            nonlocal raw_segment_count
            if not in_flight:
                return
            done, _ = await asyncio.wait(
                tuple(in_flight),
                return_when=asyncio.FIRST_COMPLETED if wait_for_one else asyncio.ALL_COMPLETED,
            )
            for task in done:
                index = in_flight.pop(task)
                result = await task
                results_by_window[index] = result
                raw_segment_count += len(result.segments)
                if raw_segment_count > self.settings.max_result_segments:
                    raise MeetingFinalizationOutputInvalid("final ASR segment limit exceeded")

        try:
            async for window in self._windows(meeting_id, owner_user_id, stats):
                if self.client is None:
                    raise MeetingFinalizationUnavailable("final ASR endpoint is not configured")
                boundaries.append(
                    _WindowBoundary(
                        index=window.index,
                        start_ms=window.start_ms,
                        end_ms=window.end_ms,
                        discontinuity=window.discontinuity,
                    )
                )
                task = asyncio.create_task(
                    self._finalize_window(
                        window,
                        run_id=finalization_id,
                        language=language,
                        hotwords=hotwords,
                        final_window=True,
                    ),
                    name=f"meeting-final-asr-window-{window.index}",
                )
                in_flight[task] = window.index
                if len(in_flight) >= self.settings.max_concurrency:
                    await collect_completed(wait_for_one=True)
            while in_flight:
                await collect_completed(wait_for_one=False)
        except BaseException:
            for task in in_flight:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*in_flight, return_exceptions=True)
            raise

        if not boundaries:
            return FinalizationAnalysis(
                mode="stable_transcript_passthrough",
                chunk_count=0,
                total_audio_bytes=0,
                window_count=0,
                gaps=(),
                segments=(),
                diarizer_ref=None,
            )

        for boundary in boundaries:
            result = results_by_window[boundary.index]
            observed_diarizer_ref = _bind_diarizer_ref(observed_diarizer_ref, result.diarizer_ref)
            if result.protocol_version != FINAL_ASR_INDEPENDENT_PROTOCOL:
                raise MeetingFinalizationOutputInvalid("final ASR protocol identity is invalid")
        results, trimmed_count = _deduplicate_window_segments(boundaries, results_by_window)
        observe_meeting_latency(
            "final_asr_job_processing_seconds",
            time.perf_counter() - processing_started,
        )
        return FinalizationAnalysis(
            mode="final_asr",
            chunk_count=stats.chunk_count,
            total_audio_bytes=stats.total_audio_bytes,
            window_count=len(boundaries),
            gaps=tuple(stats.gaps),
            segments=tuple(results),
            diarizer_ref=observed_diarizer_ref,
            protocol_version=FINAL_ASR_INDEPENDENT_PROTOCOL,
            window_overlap_ms=self.settings.window_overlap_ms,
            max_concurrency=self.settings.max_concurrency,
            boundary_trimmed_segment_count=trimmed_count,
        )

    async def _finalize_window(
        self,
        window: FinalizationWindow,
        *,
        run_id: str,
        language: str,
        hotwords: Sequence[str],
        final_window: bool,
    ) -> FinalASRWindowResult:
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
        buffer_discontinuity = False
        buffer_has_new_audio = False
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
                    if buffer and buffer_has_new_audio:
                        yield FinalizationWindow(
                            index=window_index,
                            start_ms=window_start,
                            end_ms=window_end,
                            pcm=bytes(buffer),
                            discontinuity=buffer_discontinuity,
                        )
                        window_index += 1
                    buffer.clear()
                    buffer_has_new_audio = False
                    stats.gaps.append((previous_audio_end, effective_start))
                    next_discontinuity = True
                elif previous_audio_end is not None and effective_start < previous_audio_end:
                    overlap_ms = min(chunk.duration_ms, previous_audio_end - effective_start)
                    payload = payload[overlap_ms * 32 :]
                    effective_start += overlap_ms
                    if not payload:
                        continue

                payload_offset = 0
                while payload_offset < len(payload):
                    if not buffer:
                        window_start = effective_start + payload_offset // 32
                        buffer_discontinuity = next_discontinuity
                        next_discontinuity = False
                    capacity = self.settings.max_window_bytes - len(buffer)
                    take = min(capacity, len(payload) - payload_offset)
                    buffer.extend(payload[payload_offset : payload_offset + take])
                    buffer_has_new_audio = True
                    payload_offset += take
                    window_end = window_start + len(buffer) // 32
                    if len(buffer) == self.settings.max_window_bytes:
                        yield FinalizationWindow(
                            index=window_index,
                            start_ms=window_start,
                            end_ms=window_end,
                            pcm=bytes(buffer),
                            discontinuity=buffer_discontinuity,
                        )
                        window_index += 1
                        retained = (
                            bytes(buffer[-self.settings.overlap_bytes :])
                            if self.settings.overlap_bytes
                            else b""
                        )
                        buffer = bytearray(retained)
                        window_start = window_end - len(retained) // 32
                        window_end = window_start + len(retained) // 32
                        buffer_discontinuity = False
                        buffer_has_new_audio = False
                previous_audio_end = max(previous_audio_end or 0, chunk.start_ms + chunk.duration_ms)
        if buffer and buffer_has_new_audio:
            yield FinalizationWindow(
                index=window_index,
                start_ms=window_start,
                end_ms=window_end,
                pcm=bytes(buffer),
                discontinuity=buffer_discontinuity,
            )


def _deduplicate_window_segments(
    boundaries: Sequence[_WindowBoundary],
    results_by_window: dict[int, FinalASRWindowResult],
) -> tuple[list[FinalASRSegment], int]:
    """Keep each timestamp on one deterministic side of an overlap boundary."""

    output: list[FinalASRSegment] = []
    trimmed_count = 0
    for position, boundary in enumerate(boundaries):
        if position and not boundary.discontinuity:
            previous = boundaries[position - 1]
            ownership_start = (previous.end_ms + boundary.start_ms) // 2
        else:
            ownership_start = boundary.start_ms
        if position + 1 < len(boundaries) and not boundaries[position + 1].discontinuity:
            following = boundaries[position + 1]
            ownership_end = (boundary.end_ms + following.start_ms) // 2
        else:
            ownership_end = boundary.end_ms

        for segment in results_by_window[boundary.index].segments:
            owned = _trim_segment_to_boundary(
                segment,
                ownership_start=ownership_start,
                ownership_end=ownership_end,
                include_end=position + 1 == len(boundaries),
            )
            if owned is None:
                trimmed_count += 1
                continue
            if owned is not segment:
                trimmed_count += 1
            output.append(owned)

    # Window order is the primary key; the stable sort retains adapter order for
    # equal timestamps without relying on random segment UUIDs.
    output.sort(key=lambda value: (value.start_ms, value.end_ms, value.window_index))
    return output, trimmed_count


def _trim_segment_to_boundary(
    segment: FinalASRSegment,
    *,
    ownership_start: int,
    ownership_end: int,
    include_end: bool,
) -> FinalASRSegment | None:
    def owns(timestamp: int) -> bool:
        return timestamp >= ownership_start and (timestamp <= ownership_end if include_end else timestamp < ownership_end)

    if segment.word_timestamps:
        words = tuple(
            word
            for word in segment.word_timestamps
            if owns((word.start_ms + word.end_ms) // 2)
        )
        if not words:
            return None
        if len(words) == len(segment.word_timestamps):
            return segment
        text = _join_words(words).strip()
        if not text:
            return None
        token_suffix = (
            f":w{segment.window_index}-boundary-"
            f"{words[0].token_index}-{words[-1].token_index}"
        )
        return replace(
            segment,
            segment_token=(segment.segment_token[: max(1, 128 - len(token_suffix))] + token_suffix)[:128],
            text=text,
            start_ms=words[0].start_ms,
            end_ms=words[-1].end_ms,
            word_timestamps=words,
        )

    midpoint = (segment.start_ms + segment.end_ms) // 2
    return segment if owns(midpoint) else None


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


def _parse_final_asr_response(
    payload_bytes: bytes,
    window_index: int,
    expected_protocol: str,
) -> FinalASRWindowResult:
    try:
        payload = json.loads(payload_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MeetingFinalizationOutputInvalid("final ASR response is not valid JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != FINAL_ASR_WINDOW_SCHEMA:
        raise MeetingFinalizationOutputInvalid("final ASR schema is invalid")
    if payload.get("protocol_version") != expected_protocol:
        raise MeetingFinalizationOutputInvalid("final ASR protocol identity is invalid")
    try:
        diarizer_ref = _validate_diarizer_ref(payload.get("diarizer_ref"))
    except (TypeError, ValueError) as exc:
        raise MeetingFinalizationOutputInvalid("final ASR diarizer identity is invalid") from exc
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
                diarizer_ref=diarizer_ref,
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
    return FinalASRWindowResult(
        diarizer_ref=diarizer_ref,
        segments=tuple(parsed),
        protocol_version=expected_protocol,
    )


def _validate_diarizer_ref(value: Any) -> str:
    if not isinstance(value, str) or _DIARIZER_REF_RE.fullmatch(value) is None:
        raise ValueError("diarizer identity is invalid")
    return value


def _bind_diarizer_ref(current: str | None, observed: str) -> str:
    _validate_diarizer_ref(observed)
    if current is not None and current != observed:
        raise MeetingFinalizationOutputInvalid("final ASR diarizer identity changed between windows")
    return observed


def _normalize_run_id(value: str | None) -> str:
    if not value:
        return str(uuid4())
    try:
        return str(UUID(value))
    except (TypeError, ValueError):
        return str(uuid5(NAMESPACE_URL, f"siq.meeting.finalization:{value}"))


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
    "FINAL_ASR_INDEPENDENT_PROTOCOL",
    "FinalASRClient",
    "FinalASRSegment",
    "FinalASRWindowResult",
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
