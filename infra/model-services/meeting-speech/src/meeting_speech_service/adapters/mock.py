from __future__ import annotations

from meeting_speech_service.adapters.base import (
    EngineSnapshot,
    SessionOptions,
    SpeakerEmbedding,
    SpeechEngine,
    SpeechSession,
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
    def __init__(self, *, transcript_prefix: str, speaker_adapter: str) -> None:
        self._prefix = transcript_prefix
        self._speaker_adapter = speaker_adapter
        self._state = "initializing"

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

    def create_session(self, options: SessionOptions) -> SpeechSession:
        if self._state != "degraded":
            raise RuntimeError("mock engine is not initialized")
        speaker = MockSpeakerHook() if self._speaker_adapter == "mock" else NullSpeakerHook()
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
        )

    async def speaker_embedding(self, pcm: bytes) -> SpeakerEmbedding:
        if self._state != "degraded":
            raise RuntimeError("mock engine is not initialized")
        return SpeakerEmbedding(encoder_ref="mock", values=(1.0, 0.0))

    async def close(self) -> None:
        self._state = "stopped"
