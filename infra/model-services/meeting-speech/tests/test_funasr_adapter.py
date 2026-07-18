import asyncio
import sys
from types import SimpleNamespace

from meeting_speech_service.adapters.funasr import FunASREngine


def _engine(**overrides) -> FunASREngine:
    values = {
        "source_root": None,
        "device": "cuda:0",
        "online_model": "online",
        "final_model": "final",
        "finalizer": "local",
        "http_finalizer_url": None,
        "http_finalizer_health_url": None,
        "http_finalizer_timeout_seconds": 1.0,
        "http_finalizer_queue_timeout_seconds": 1.0,
        "http_finalizer_max_concurrency": 1,
        "http_finalizer_max_response_bytes": 10_000,
        "http_finalizer_speaker_hints_enabled": False,
        "vad_model": "vad",
        "punctuation_model": "",
        "online_chunk_size": (0, 10, 5),
        "encoder_chunk_look_back": 4,
        "decoder_chunk_look_back": 1,
        "speaker_adapter": "none",
        "speaker_model": "speaker",
        "speaker_cluster_threshold": 0.72,
        "speaker_cluster_update_threshold": 0.82,
        "speaker_cluster_min_margin": 0.04,
        "speaker_candidate_threshold": 0.78,
        "speaker_candidate_confirmations": 2,
        "speaker_candidate_max_gap_ms": 30_000,
        "speaker_max_tracks": 16,
        "speaker_min_segment_ms": 1_000,
        "speaker_new_track_min_segment_ms": 1_500,
        "speaker_max_prototypes": 8,
        "speaker_min_rms": 0.005,
        "speaker_max_clipping_ratio": 0.2,
        "speaker_inference_timeout_seconds": 1.0,
        "embedding_endpoint_enabled": False,
        "speaker_global_cluster_enabled": False,
        "speaker_global_cluster_merge_threshold": 0.8,
        "speaker_global_cluster_max_speakers": 15,
    }
    values.update(overrides)
    return FunASREngine(**values)


def test_funasr_core_cuda_oom_falls_back_to_cpu(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    class FakeAutoModel:
        def __init__(self, *, model: str, device: str, **_kwargs) -> None:
            calls.append((model, device))
            if model == "online" and device == "cuda:0":
                raise RuntimeError("CUDA error: out of memory")

    monkeypatch.setitem(sys.modules, "funasr", SimpleNamespace(AutoModel=FakeAutoModel))

    engine = _engine()
    asyncio.run(engine.initialize())

    snapshot = engine.snapshot()
    assert snapshot.state == "degraded"
    assert snapshot.accepting_streams is True
    assert snapshot.reason_code == "FUNASR_CUDA_OOM_CPU_FALLBACK"
    assert snapshot.components["runtime_device"] == "cpu"
    assert calls == [
        ("online", "cuda:0"),
        ("online", "cpu"),
        ("final", "cpu"),
        ("vad", "cpu"),
    ]


def test_funasr_core_non_oom_failure_stays_unavailable(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    class FakeAutoModel:
        def __init__(self, *, model: str, device: str, **_kwargs) -> None:
            calls.append((model, device))
            raise RuntimeError("model artifact missing")

    monkeypatch.setitem(sys.modules, "funasr", SimpleNamespace(AutoModel=FakeAutoModel))

    engine = _engine()
    asyncio.run(engine.initialize())

    snapshot = engine.snapshot()
    assert snapshot.state == "unavailable"
    assert snapshot.accepting_streams is False
    assert snapshot.reason_code == "FUNASR_MODEL_LOAD_FAILED"
    assert calls == [("online", "cuda:0")]
