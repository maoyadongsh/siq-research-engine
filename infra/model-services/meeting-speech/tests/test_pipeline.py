import asyncio
import threading

import pytest
from meeting_speech_service.adapters.base import SessionOptions
from meeting_speech_service.adapters.pipeline import (
    BufferedRecognitionSession,
    EnergyVad,
    FinalDecode,
    MockSpeakerHook,
    OnlineDecode,
)


class _BlockingDecoder:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.finish = threading.Event()

    def online(self, pcm: bytes, *, cache: dict[str, object], is_final: bool) -> OnlineDecode:
        self.started.set()
        self.finish.wait(timeout=2)
        return OnlineDecode(text="partial", is_delta=False)

    def final(self, pcm: bytes) -> FinalDecode:
        return FinalDecode(text="final")


class _CountingDecoder:
    def __init__(self) -> None:
        self.online_calls = 0
        self.final_calls = 0

    def online(self, pcm: bytes, *, cache: dict[str, object], is_final: bool) -> OnlineDecode:
        self.online_calls += 1
        return OnlineDecode(text="partial", is_delta=False)

    def final(self, pcm: bytes) -> FinalDecode:
        self.final_calls += 1
        return FinalDecode(text="final")


def _options() -> SessionOptions:
    return SessionOptions(
        sample_rate=16_000,
        hotwords=(),
        language=None,
        max_segment_bytes=320_000,
        pre_roll_ms=0,
        vad_min_speech_ms=20,
        vad_endpoint_silence_ms=200,
        vad_energy_threshold=0.01,
        inference_timeout_seconds=0.01,
    )


def test_timed_out_worker_is_not_mutated_during_close() -> None:
    async def scenario() -> None:
        decoder = _BlockingDecoder()
        session = BufferedRecognitionSession(
            adapter_name="blocking-test",
            options=_options(),
            vad=EnergyVad(sample_rate=16_000, threshold=0.01, min_speech_ms=20, endpoint_silence_ms=200),
            decoder=decoder,
        )
        pcm = (12_000).to_bytes(2, "little", signed=True) * 1_600
        with pytest.raises(TimeoutError):
            await session.ingest(pcm, capture_time_ms=0)
        assert decoder.started.is_set()

        await session.close()
        decoder.finish.set()
        await asyncio.sleep(0.02)

    asyncio.run(scenario())


def test_offline_session_skips_online_decode_but_keeps_final_decode() -> None:
    async def scenario() -> None:
        decoder = _CountingDecoder()
        options = SessionOptions(
            sample_rate=16_000,
            hotwords=(),
            language=None,
            max_segment_bytes=320_000,
            pre_roll_ms=0,
            vad_min_speech_ms=20,
            vad_endpoint_silence_ms=200,
            vad_energy_threshold=0.01,
            inference_timeout_seconds=1,
            online_decode_enabled=False,
        )
        session = BufferedRecognitionSession(
            adapter_name="offline-test",
            options=options,
            vad=EnergyVad(sample_rate=16_000, threshold=0.01, min_speech_ms=20, endpoint_silence_ms=200),
            decoder=decoder,
        )
        pcm = (12_000).to_bytes(2, "little", signed=True) * 1_600
        results = await session.ingest(pcm, capture_time_ms=0, end_of_stream=True)

        assert decoder.online_calls == 0
        assert decoder.final_calls == 1
        assert [result.kind for result in results] == ["final"]
        assert results[0].text == "final"
        await session.close()

    asyncio.run(scenario())


def test_energy_onset_emits_real_partial_before_model_vad_confirms_speech() -> None:
    class _DelayedVad:
        speaking = False

        def process(self, pcm: bytes, *, is_final: bool):
            from meeting_speech_service.adapters.pipeline import VadDecision

            return VadDecision(started=False, speaking=False, endpoint=is_final)

        def reset(self) -> None:
            return None

    async def scenario() -> None:
        decoder = _CountingDecoder()
        session = BufferedRecognitionSession(
            adapter_name="low-latency-test",
            options=_options(),
            vad=_DelayedVad(),
            decoder=decoder,
        )
        pcm = (12_000).to_bytes(2, "little", signed=True) * 3_200
        results = await session.ingest(pcm, capture_time_ms=0)

        assert decoder.online_calls == 1
        assert [result.kind for result in results] == ["partial"]
        assert results[0].text == "partial"
        assert results[0].end_ms == 200
        await session.close()

    asyncio.run(scenario())


def test_speaker_metrics_distinguish_created_reused_unassigned_and_failed() -> None:
    class _NoAssignment:
        def assign(self, pcm: bytes, *, start_ms: int, end_ms: int):
            return None

    class _FailedAssignment:
        def assign(self, pcm: bytes, *, start_ms: int, end_ms: int):
            raise RuntimeError("speaker model failed")

    async def finalize(
        speaker,
        observations: list[tuple[str, str | None]],
        *,
        capture_time_ms: int = 0,
    ) -> BufferedRecognitionSession:
        session = BufferedRecognitionSession(
            adapter_name="speaker-metrics-test",
            options=_options(),
            vad=EnergyVad(sample_rate=16_000, threshold=0.01, min_speech_ms=20, endpoint_silence_ms=200),
            decoder=_CountingDecoder(),
            speaker=speaker,
            speaker_metrics_observer=lambda result, track_result: observations.append((result, track_result)),
        )
        pcm = (12_000).to_bytes(2, "little", signed=True) * 1_600
        await session.ingest(pcm, capture_time_ms=capture_time_ms, end_of_stream=True)
        return session

    async def scenario() -> None:
        observations: list[tuple[str, str | None]] = []
        session = await finalize(MockSpeakerHook(), observations)
        pcm = (12_000).to_bytes(2, "little", signed=True) * 1_600
        await session.ingest(pcm, capture_time_ms=100, end_of_stream=True)
        await session.close()

        no_assignment = await finalize(_NoAssignment(), observations)
        await no_assignment.close()
        failed = await finalize(_FailedAssignment(), observations)
        await failed.close()

        assert observations == [
            ("assigned", "created"),
            ("assigned", "reused"),
            ("unassigned", None),
            ("failed", None),
        ]

    asyncio.run(scenario())
