from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal, Mapping


def build_diarizer_ref(
    *,
    adapter: str,
    model_ref: str,
    configuration: Mapping[str, object],
) -> str:
    """Return a stable, non-secret identity for the effective diarizer config."""

    normalized_adapter = adapter.strip().lower()
    if not normalized_adapter or not normalized_adapter.replace("-", "").isalnum():
        raise ValueError("diarizer adapter identity is invalid")
    payload = json.dumps(
        {
            "schema_version": "siq.meeting.diarizer.config.v1",
            "adapter": normalized_adapter,
            "model_ref": model_ref.strip(),
            "configuration": dict(configuration),
        },
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return f"siq.meeting.diarizer.v1/{normalized_adapter}/{digest}"


class AdapterUnavailable(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class EngineSnapshot:
    adapter: str
    state: Literal["initializing", "ready", "degraded", "unavailable", "stopped"]
    accepting_streams: bool
    production_capable: bool
    reason_code: str | None = None
    components: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SessionOptions:
    sample_rate: int
    hotwords: tuple[str, ...]
    language: str | None
    max_segment_bytes: int
    pre_roll_ms: int
    vad_min_speech_ms: int
    vad_endpoint_silence_ms: int
    vad_energy_threshold: float
    inference_timeout_seconds: float
    online_decode_enabled: bool = True


@dataclass(frozen=True, slots=True)
class WordTiming:
    token_index: int
    start_ms: int
    end_ms: int
    text: str | None = None


@dataclass(frozen=True, slots=True)
class SpeakerEmbedding:
    encoder_ref: str
    values: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class Recognition:
    kind: Literal["partial", "final"]
    segment_token: str
    text: str
    start_ms: int
    end_ms: int
    adapter: str
    finalization_reason: str | None = None
    speaker_track_key: str | None = None
    speaker_confidence: float | None = None
    word_timings: tuple[WordTiming, ...] = ()
    degraded_reason: str | None = None
    inference_ms: int | None = None
    source_speaker_hints: tuple[str, ...] = ()


class SpeechSession(ABC):
    @abstractmethod
    async def ingest(
        self,
        pcm: bytes,
        *,
        capture_time_ms: int,
        end_of_stream: bool = False,
        discontinuity: bool = False,
    ) -> tuple[Recognition, ...]:
        raise NotImplementedError

    @abstractmethod
    async def flush(self) -> tuple[Recognition, ...]:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError


class SpeechEngine(ABC):
    @property
    @abstractmethod
    def diarizer_ref(self) -> str:
        raise NotImplementedError

    @abstractmethod
    async def initialize(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def snapshot(self) -> EngineSnapshot:
        raise NotImplementedError

    @abstractmethod
    def create_session(
        self,
        options: SessionOptions,
        *,
        speaker_track_namespace: str | None = None,
    ) -> SpeechSession:
        raise NotImplementedError

    async def speaker_embedding(self, pcm: bytes) -> SpeakerEmbedding:
        raise AdapterUnavailable("SPEAKER_EMBEDDING_UNAVAILABLE")

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError
