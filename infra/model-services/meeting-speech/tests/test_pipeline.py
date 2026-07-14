import asyncio
import threading

import pytest
from meeting_speech_service.adapters.base import SessionOptions
from meeting_speech_service.adapters.pipeline import (
    BufferedRecognitionSession,
    EnergyVad,
    FinalDecode,
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
