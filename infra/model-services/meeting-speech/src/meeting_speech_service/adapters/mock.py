from __future__ import annotations

from typing import Callable

from meeting_speech_service.adapters.base import (
    EngineSnapshot,
    SessionOptions,
    SpeakerEmbedding,
    SpeechEngine,
    SpeechSession,
    build_diarizer_ref,
)
from meeting_speech_service.adapters.pipeline import (
    BufferedRecognitionSession,
    EnergyVad,
    FinalDecode,
    MockSpeakerHook,
    NullSpeakerHook,
    OnlineDecode,
)


class _MockDecoder:
    def __init__(self, prefix: str) -> None:
        self._prefix = prefix.strip()

    def online(self, pcm: bytes, *, cache: dict[str, object], is_final: bool) -> OnlineDecode:
        chunks = int(cache.get("chunks", 0)) + 1
        cache["chunks"] = chunks
        return OnlineDecode(text=f"{self._prefix} {chunks}", is_delta=False)

    def final(self, pcm: bytes) -> FinalDecode:
        return FinalDecode(text=self._prefix)


class MockSpeechEngine(SpeechEngine):
    def __init__(
        self,
        *,
        transcript_prefix: str,
        speaker_adapter: str,
        speaker_metrics_observer: Callable[[str, str | None], None] | None = None,
    ) -> None:
        self._prefix = transcript_prefix
        self._speaker_adapter = speaker_adapter
        self._speaker_metrics_observer = speaker_metrics_observer
        self._state = "initializing"
        effective_adapter = "mock" if speaker_adapter == "mock" else "none"
        self._diarizer_ref = build_diarizer_ref(
            adapter=effective_adapter,
            model_ref="mock-speaker-v1" if effective_adapter == "mock" else "disabled",
            configuration={
                "speaker_adapter": speaker_adapter,
                "clusterer": "mock-single-speaker-v1" if effective_adapter == "mock" else "disabled",
            },
        )

    @property
    def diarizer_ref(self) -> str:
        return self._diarizer_ref

    async def initialize(self) -> None:
        self._state = "degraded"

    def snapshot(self) -> EngineSnapshot:
        return EngineSnapshot(
            adapter="mock",
            state=self._state,  # type: ignore[arg-type]
            accepting_streams=self._state == "degraded",
            production_capable=False,
            reason_code="EXPLICIT_MOCK_ADAPTER",
            components={"asr": "mock", "vad": "energy", "speaker": self._speaker_adapter},
        )

    def create_session(
        self,
        options: SessionOptions,
        *,
        speaker_track_namespace: str | None = None,
    ) -> SpeechSession:
        if self._state != "degraded":
            raise RuntimeError("mock engine is not initialized")
        speaker = (
            MockSpeakerHook(track_namespace=speaker_track_namespace)
            if self._speaker_adapter == "mock"
            else NullSpeakerHook()
        )
        return BufferedRecognitionSession(
            adapter_name="mock",
            options=options,
            vad=EnergyVad(
                sample_rate=options.sample_rate,
                threshold=options.vad_energy_threshold,
                min_speech_ms=options.vad_min_speech_ms,
                endpoint_silence_ms=options.vad_endpoint_silence_ms,
            ),
            decoder=_MockDecoder(self._prefix),
            speaker=speaker,
            speaker_metrics_observer=(
                self._speaker_metrics_observer if self._speaker_adapter == "mock" else None
            ),
        )

    async def speaker_embedding(self, pcm: bytes) -> SpeakerEmbedding:
        if self._state != "degraded":
            raise RuntimeError("mock engine is not initialized")
        return SpeakerEmbedding(encoder_ref="mock", values=(1.0, 0.0))

    async def close(self) -> None:
        self._state = "stopped"
