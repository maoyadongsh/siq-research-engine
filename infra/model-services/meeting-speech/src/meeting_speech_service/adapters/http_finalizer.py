from __future__ import annotations

import io
import json
import wave
from threading import BoundedSemaphore
from urllib.parse import urlsplit, urlunsplit

import httpx

from meeting_speech_service.adapters.base import AdapterUnavailable, WordTiming
from meeting_speech_service.adapters.pipeline import FinalDecode


class FunASRHttpFinalizer:
    """Bounded sentence-final adapter for the existing FunASR `/asr` contract."""

    def __init__(
        self,
        *,
        url: str,
        health_url: str | None,
        timeout_seconds: float,
        queue_timeout_seconds: float,
        max_concurrency: int,
        max_response_bytes: int,
        client: httpx.Client | None = None,
    ) -> None:
        self._url = url
        self._health_url = health_url or _default_health_url(url)
        self._queue_timeout_seconds = queue_timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._slots = BoundedSemaphore(max_concurrency)
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            trust_env=False,
        )
        self._owns_client = client is None

    def probe(self) -> None:
        try:
            with self._client.stream("GET", self._health_url) as response:
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AdapterUnavailable("FUNASR_HTTP_FINALIZER_UNAVAILABLE") from exc

    def decode(self, pcm: bytes, *, hotwords: tuple[str, ...], language: str | None) -> FinalDecode:
        if not self._slots.acquire(timeout=self._queue_timeout_seconds):
            raise AdapterUnavailable("FUNASR_HTTP_FINALIZER_BUSY")
        try:
            wav_bytes = _pcm_to_wav(pcm)
            form = {
                "hotwords": ",".join(hotwords),
                "spk": "true",
                "timestamp": "true",
            }
            if language:
                form["language"] = language
            try:
                with self._client.stream(
                    "POST",
                    self._url,
                    files={"file": ("segment.wav", wav_bytes, "audio/wav")},
                    data=form,
                ) as response:
                    response.raise_for_status()
                    body = _read_bounded(response, self._max_response_bytes)
            except httpx.HTTPError as exc:
                raise AdapterUnavailable("FUNASR_HTTP_FINALIZER_FAILED") from exc
            try:
                payload = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise AdapterUnavailable("FUNASR_HTTP_FINALIZER_RESPONSE_INVALID") from exc
            if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
                raise AdapterUnavailable("FUNASR_HTTP_FINALIZER_RESPONSE_INVALID")
            return FinalDecode(
                text=payload["text"].strip(),
                word_timings=_word_timings(payload),
                source_speaker_hints=_speaker_hints(payload),
            )
        finally:
            self._slots.release()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def _pcm_to_wav(pcm: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(pcm)
    return output.getvalue()


def _default_health_url(finalizer_url: str) -> str:
    parsed = urlsplit(finalizer_url)
    return urlunsplit((parsed.scheme, parsed.netloc, "/openapi.json", "", ""))


def _read_bounded(response: httpx.Response, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise AdapterUnavailable("FUNASR_HTTP_FINALIZER_RESPONSE_TOO_LARGE")
        chunks.append(chunk)
    return b"".join(chunks)


def _word_timings(payload: dict[str, object]) -> tuple[WordTiming, ...]:
    parsed: list[WordTiming] = []
    segments = payload.get("segments")
    if not isinstance(segments, list):
        return ()
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get("words"), list):
            continue
        for word in segment["words"]:
            if not isinstance(word, dict):
                continue
            try:
                start_ms = round(float(word["start"]) * 1_000)
                end_ms = round(float(word["end"]) * 1_000)
            except (KeyError, TypeError, ValueError):
                continue
            text = word.get("word")
            parsed.append(
                WordTiming(
                    token_index=len(parsed),
                    start_ms=start_ms,
                    end_ms=end_ms,
                    text=text if isinstance(text, str) else None,
                )
            )
    return tuple(parsed)


def _speaker_hints(payload: dict[str, object]) -> tuple[str, ...]:
    segments = payload.get("segments")
    if not isinstance(segments, list):
        return ()
    hints: list[str] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        speaker = segment.get("speaker")
        if isinstance(speaker, str) and speaker and speaker not in hints:
            hints.append(speaker[:64])
    return tuple(hints)
