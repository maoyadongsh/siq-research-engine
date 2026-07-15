import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

import httpx
import pytest
from meeting_speech_service.adapters.base import AdapterUnavailable
from meeting_speech_service.adapters.http_finalizer import FunASRHttpFinalizer


def _finalizer(
    handler,
    *,
    max_response_bytes: int = 10_000,
    max_concurrency: int = 1,
) -> FunASRHttpFinalizer:
    return FunASRHttpFinalizer(
        url="http://127.0.0.1:8899/asr",
        health_url="http://127.0.0.1:8899/openapi.json",
        timeout_seconds=1,
        queue_timeout_seconds=1,
        max_concurrency=max_concurrency,
        max_response_bytes=max_response_bytes,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_existing_funasr_contract_is_called_with_bounded_wav() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"openapi": "3.0.0"})
        body = request.read()
        assert b'filename="segment.wav"' in body
        assert b"RIFF" in body
        assert b'name="spk"' in body
        assert b"true" in body
        return httpx.Response(
            200,
            json={
                "text": "test final",
                "segments": [
                    {
                        "text": "test final",
                        "start": 0.0,
                        "end": 0.1,
                        "words": [{"word": "test", "start": 0.0, "end": 0.1}],
                        "speaker": "SPK0",
                    }
                ],
                "duration": 0.1,
            },
        )

    finalizer = _finalizer(handler)
    finalizer.probe()
    result = finalizer.decode(b"\x00\x00" * 1_600, hotwords=("SIQ",), language="zh")

    assert result.text == "test final"
    assert result.word_timings[0].start_ms == 0
    assert result.word_timings[0].end_ms == 100
    assert result.source_speaker_hints == ("SPK0",)


def test_http_finalizer_rejects_oversized_response() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 2_000)

    finalizer = _finalizer(handler, max_response_bytes=1_024)
    with pytest.raises(AdapterUnavailable, match="RESPONSE_TOO_LARGE"):
        finalizer.decode(b"\x00\x00" * 100, hotwords=(), language=None)


def test_http_finalizer_runs_with_bounded_real_concurrency() -> None:
    lock = Lock()
    active = 0
    peak = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.2)
            return httpx.Response(200, json={"text": "ok", "segments": []})
        finally:
            with lock:
                active -= 1

    finalizer = _finalizer(handler, max_concurrency=2)
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(
            executor.map(
                lambda _: finalizer.decode(b"\x00\x00" * 1_600, hotwords=(), language="zh"),
                range(3),
            )
        )
    elapsed = time.perf_counter() - started

    assert [result.text for result in results] == ["ok", "ok", "ok"]
    assert peak == 2
    assert elapsed < 0.55
