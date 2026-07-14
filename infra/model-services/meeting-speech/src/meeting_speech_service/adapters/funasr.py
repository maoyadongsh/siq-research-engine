from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from threading import Lock
from typing import Any, Callable

import numpy as np

from meeting_speech_service.adapters.base import (
    AdapterUnavailable,
    EngineSnapshot,
    SessionOptions,
    SpeakerEmbedding,
    SpeechEngine,
    SpeechSession,
    WordTiming,
    build_diarizer_ref,
)
from meeting_speech_service.adapters.http_finalizer import FunASRHttpFinalizer
from meeting_speech_service.adapters.pipeline import (
    BufferedRecognitionSession,
    FinalDecode,
    NullSpeakerHook,
    OnlineDecode,
    VadDecision,
)
from meeting_speech_service.adapters.speaker import AnonymousSpeakerCluster


class FunASREngine(SpeechEngine):
    def __init__(
        self,
        *,
        source_root: Path | None,
        device: str,
        online_model: str,
        final_model: str,
        finalizer: str,
        http_finalizer_url: str | None,
        http_finalizer_health_url: str | None,
        http_finalizer_timeout_seconds: float,
        http_finalizer_queue_timeout_seconds: float,
        http_finalizer_max_concurrency: int,
        http_finalizer_max_response_bytes: int,
        vad_model: str,
        punctuation_model: str,
        online_chunk_size: tuple[int, int, int],
        encoder_chunk_look_back: int,
        decoder_chunk_look_back: int,
        speaker_adapter: str,
        speaker_model: str,
        speaker_cluster_threshold: float,
        speaker_cluster_update_threshold: float,
        speaker_cluster_min_margin: float,
        speaker_candidate_threshold: float,
        speaker_candidate_confirmations: int,
        speaker_candidate_max_gap_ms: int,
        speaker_max_tracks: int,
        speaker_min_segment_ms: int,
        speaker_new_track_min_segment_ms: int,
        speaker_max_prototypes: int,
        speaker_min_rms: float,
        speaker_max_clipping_ratio: float,
        speaker_inference_timeout_seconds: float,
        embedding_endpoint_enabled: bool,
        speaker_metrics_observer: Callable[[str, str | None], None] | None = None,
    ) -> None:
        self._source_root = source_root
        self._device = device
        self._online_model_ref = online_model
        self._final_model_ref = final_model
        self._finalizer_mode = finalizer
        self._http_finalizer_url = http_finalizer_url
        self._http_finalizer_health_url = http_finalizer_health_url
        self._http_finalizer_timeout_seconds = http_finalizer_timeout_seconds
        self._http_finalizer_queue_timeout_seconds = http_finalizer_queue_timeout_seconds
        self._http_finalizer_max_concurrency = http_finalizer_max_concurrency
        self._http_finalizer_max_response_bytes = http_finalizer_max_response_bytes
        self._vad_model_ref = vad_model
        self._punctuation_model_ref = punctuation_model
        self._chunk_size = list(online_chunk_size)
        self._encoder_look_back = encoder_chunk_look_back
        self._decoder_look_back = decoder_chunk_look_back
        self._speaker_adapter = speaker_adapter
        self._speaker_model_ref = speaker_model
        self._speaker_cluster_threshold = speaker_cluster_threshold
        self._speaker_cluster_update_threshold = speaker_cluster_update_threshold
        self._speaker_cluster_min_margin = speaker_cluster_min_margin
        self._speaker_candidate_threshold = speaker_candidate_threshold
        self._speaker_candidate_confirmations = speaker_candidate_confirmations
        self._speaker_candidate_max_gap_ms = speaker_candidate_max_gap_ms
        self._speaker_max_tracks = speaker_max_tracks
        self._speaker_min_segment_ms = speaker_min_segment_ms
        self._speaker_new_track_min_segment_ms = speaker_new_track_min_segment_ms
        self._speaker_max_prototypes = speaker_max_prototypes
        self._speaker_min_rms = speaker_min_rms
        self._speaker_max_clipping_ratio = speaker_max_clipping_ratio
        self._speaker_inference_timeout_seconds = speaker_inference_timeout_seconds
        self._embedding_endpoint_enabled = embedding_endpoint_enabled
        self._speaker_metrics_observer = speaker_metrics_observer
        self._configured_diarizer_ref = build_diarizer_ref(
            adapter="funasr",
            model_ref=speaker_model,
            configuration={
                "cluster_threshold": speaker_cluster_threshold,
                "cluster_update_threshold": speaker_cluster_update_threshold,
                "cluster_min_margin": speaker_cluster_min_margin,
                "candidate_threshold": speaker_candidate_threshold,
                "candidate_confirmations": speaker_candidate_confirmations,
                "candidate_max_gap_ms": speaker_candidate_max_gap_ms,
                "max_tracks": speaker_max_tracks,
                "min_segment_ms": speaker_min_segment_ms,
                "new_track_min_segment_ms": speaker_new_track_min_segment_ms,
                "max_prototypes": speaker_max_prototypes,
                "min_rms": speaker_min_rms,
                "max_clipping_ratio": speaker_max_clipping_ratio,
            },
        )
        self._disabled_diarizer_ref = build_diarizer_ref(
            adapter="none",
            model_ref="disabled",
            configuration={"speaker_adapter": speaker_adapter},
        )
        self._state = "initializing"
        self._reason_code: str | None = None
        self._online_model: Any = None
        self._final_model: Any = None
        self._vad_model: Any = None
        self._http_finalizer: FunASRHttpFinalizer | None = None
        self._speaker_model: Any = None
        self._speaker_reason_code: str | None = None
        self._speaker_timed_out = False
        self._online_lock = Lock()
        self._final_lock = Lock()
        self._vad_lock = Lock()
        self._speaker_lock = Lock()
        self._closing = False

    @property
    def diarizer_ref(self) -> str:
        if (
            self._speaker_adapter == "funasr"
            and self._speaker_model is not None
            and not self._speaker_timed_out
        ):
            return self._configured_diarizer_ref
        return self._disabled_diarizer_ref

    async def initialize(self) -> None:
        self._state = "initializing"
        try:
            await asyncio.to_thread(self._load_models)
        except AdapterUnavailable as exc:
            self._state = "unavailable"
            self._reason_code = exc.code
        except Exception:
            self._state = "unavailable"
            self._reason_code = "FUNASR_MODEL_LOAD_FAILED"
        else:
            if self._closing:
                self._state = "stopped"
            elif self._speaker_reason_code is not None:
                self._state = "degraded"
                self._reason_code = self._speaker_reason_code
            else:
                self._state = "ready"
                self._reason_code = None

    def snapshot(self) -> EngineSnapshot:
        return EngineSnapshot(
            adapter="funasr",
            state=self._state,  # type: ignore[arg-type]
            accepting_streams=self._state in {"ready", "degraded"},
            production_capable=self._state in {"ready", "degraded"},
            reason_code=self._reason_code,
            components={
                "asr_online": "ready" if self._online_model is not None else "unavailable",
                "asr_final": "ready"
                if self._final_model is not None or self._http_finalizer is not None
                else "unavailable",
                "asr_final_mode": self._finalizer_mode,
                "vad": "ready" if self._vad_model is not None else "unavailable",
                "speaker": "ready"
                if self._speaker_model is not None and not self._speaker_timed_out
                else ("disabled" if not self._speaker_required else "unavailable"),
                "speaker_mode": self._speaker_adapter,
                "embedding_endpoint": "ready"
                if self._embedding_endpoint_enabled and self._speaker_model is not None and not self._speaker_timed_out
                else ("disabled" if not self._embedding_endpoint_enabled else "unavailable"),
            },
        )

    def create_session(
        self,
        options: SessionOptions,
        *,
        speaker_track_namespace: str | None = None,
    ) -> SpeechSession:
        if self._state not in {"ready", "degraded"}:
            raise AdapterUnavailable(self._reason_code or "FUNASR_NOT_READY")
        speaker = NullSpeakerHook()
        if self._speaker_adapter == "funasr" and self._speaker_model is not None and not self._speaker_timed_out:
            speaker = AnonymousSpeakerCluster(
                encoder=self._encode_speaker_sync,
                threshold=self._speaker_cluster_threshold,
                update_threshold=self._speaker_cluster_update_threshold,
                min_margin=self._speaker_cluster_min_margin,
                candidate_threshold=self._speaker_candidate_threshold,
                candidate_confirmations=self._speaker_candidate_confirmations,
                candidate_max_gap_ms=self._speaker_candidate_max_gap_ms,
                max_tracks=self._speaker_max_tracks,
                min_segment_ms=self._speaker_min_segment_ms,
                new_track_min_segment_ms=self._speaker_new_track_min_segment_ms,
                max_prototypes=self._speaker_max_prototypes,
                min_rms=self._speaker_min_rms,
                max_clipping_ratio=self._speaker_max_clipping_ratio,
                track_namespace=speaker_track_namespace,
            )
        return BufferedRecognitionSession(
            adapter_name="funasr",
            options=options,
            vad=_FunASRVad(self),
            decoder=_FunASRDecoder(self, options),
            speaker=speaker,
            speaker_metrics_observer=(
                self._speaker_metrics_observer
                if isinstance(speaker, AnonymousSpeakerCluster)
                else None
            ),
        )

    @property
    def _speaker_required(self) -> bool:
        return self._speaker_adapter == "funasr" or self._embedding_endpoint_enabled

    async def speaker_embedding(self, pcm: bytes) -> SpeakerEmbedding:
        if self._speaker_model is None:
            raise AdapterUnavailable(self._speaker_reason_code or "SPEAKER_EMBEDDING_UNAVAILABLE")
        try:
            vector = await asyncio.wait_for(
                asyncio.to_thread(self._encode_speaker_sync, bytes(pcm)),
                timeout=self._speaker_inference_timeout_seconds,
            )
        except TimeoutError as exc:
            self._speaker_timed_out = True
            self._speaker_reason_code = "SPEAKER_EMBEDDING_TIMEOUT"
            self._reason_code = self._speaker_reason_code
            if self._state == "ready":
                self._state = "degraded"
            raise AdapterUnavailable("SPEAKER_EMBEDDING_TIMEOUT") from exc
        return SpeakerEmbedding(
            encoder_ref=self._speaker_model_ref,
            values=tuple(float(value) for value in vector),
        )

    async def close(self) -> None:
        self._closing = True
        self._state = "stopped"
        if self._http_finalizer is not None:
            self._http_finalizer.close()

    def _load_models(self) -> None:
        if self._source_root is not None:
            root = self._source_root.expanduser().resolve()
            if not root.is_dir():
                raise AdapterUnavailable("FUNASR_SOURCE_ROOT_MISSING")
            root_text = str(root)
            if root_text not in sys.path:
                sys.path.insert(0, root_text)
        try:
            from funasr import AutoModel
        except (ImportError, FileNotFoundError) as exc:
            raise AdapterUnavailable("FUNASR_IMPORT_FAILED") from exc

        self._online_model = AutoModel(
            model=self._online_model_ref,
            device=self._device,
            disable_update=True,
        )
        if self._finalizer_mode == "local":
            self._final_model = AutoModel(
                model=self._final_model_ref,
                punc_model=self._punctuation_model_ref or None,
                device=self._device,
                disable_update=True,
            )
        else:
            if self._http_finalizer_url is None:
                raise AdapterUnavailable("FUNASR_HTTP_FINALIZER_URL_MISSING")
            self._http_finalizer = FunASRHttpFinalizer(
                url=self._http_finalizer_url,
                health_url=self._http_finalizer_health_url,
                timeout_seconds=self._http_finalizer_timeout_seconds,
                queue_timeout_seconds=self._http_finalizer_queue_timeout_seconds,
                max_concurrency=self._http_finalizer_max_concurrency,
                max_response_bytes=self._http_finalizer_max_response_bytes,
            )
            self._http_finalizer.probe()
        self._vad_model = AutoModel(
            model=self._vad_model_ref,
            device=self._device,
            disable_update=True,
        )
        if self._speaker_required:
            try:
                self._speaker_model = AutoModel(
                    model=self._speaker_model_ref,
                    device=self._device,
                    disable_update=True,
                )
            except Exception:
                self._speaker_model = None
                self._speaker_reason_code = "FUNASR_SPEAKER_LOAD_FAILED"

    def run_online(self, samples: np.ndarray, cache: dict[str, object], *, is_final: bool, hotword: str | None) -> Any:
        kwargs: dict[str, object] = {
            "input": samples,
            "cache": cache,
            "is_final": is_final,
            "chunk_size": self._chunk_size,
            "encoder_chunk_look_back": self._encoder_look_back,
            "decoder_chunk_look_back": self._decoder_look_back,
        }
        if hotword:
            kwargs["hotword"] = hotword
        with self._online_lock:
            return self._online_model.generate(**kwargs)

    def run_final(
        self,
        pcm: bytes,
        *,
        hotwords: tuple[str, ...],
        language: str | None,
    ) -> Any:
        if self._http_finalizer is not None:
            return self._http_finalizer.decode(pcm, hotwords=hotwords, language=language)
        samples = _pcm_to_float32(pcm)
        kwargs: dict[str, object] = {"input": samples, "cache": {}, "is_final": True}
        if hotwords:
            kwargs["hotword"] = " ".join(hotwords)
        if language:
            kwargs["language"] = language
        with self._final_lock:
            return self._final_model.generate(**kwargs)

    def run_vad(self, samples: np.ndarray, cache: dict[str, object], *, is_final: bool) -> Any:
        with self._vad_lock:
            return self._vad_model.generate(
                input=samples,
                cache=cache,
                is_final=is_final,
                chunk_size=200,
            )

    def _encode_speaker_sync(self, pcm: bytes) -> np.ndarray:
        if self._speaker_model is None or self._speaker_timed_out:
            raise AdapterUnavailable("SPEAKER_EMBEDDING_UNAVAILABLE")
        samples = _pcm_to_float32(pcm)
        with self._speaker_lock:
            result = self._speaker_model.generate(input=[samples], cache={}, is_final=True)
        embedding = _extract_speaker_embedding(result)
        norm = float(np.linalg.norm(embedding))
        if embedding.size == 0 or not np.isfinite(norm) or norm <= 1e-12:
            raise AdapterUnavailable("SPEAKER_EMBEDDING_INVALID")
        normalized = embedding / norm
        if not np.all(np.isfinite(normalized)):
            raise AdapterUnavailable("SPEAKER_EMBEDDING_INVALID")
        return normalized.astype(np.float32, copy=False)


class _FunASRVad:
    def __init__(self, engine: FunASREngine) -> None:
        self._engine = engine
        self._cache: dict[str, object] = {}
        self._speaking = False
        self._stream_ms = 0

    @property
    def speaking(self) -> bool:
        return self._speaking

    def process(self, pcm: bytes, *, is_final: bool) -> VadDecision:
        samples = _pcm_to_float32(pcm)
        if is_final and samples.size == 0:
            samples = np.zeros(160, dtype=np.float32)
        duration_ms = len(pcm) * 1_000 // 32_000
        self._stream_ms += duration_ms
        result = self._engine.run_vad(samples, self._cache, is_final=is_final)
        started = False
        endpoint = False
        speech_end_ms: int | None = None
        for start_ms, end_ms in _extract_vad_signals(result):
            if start_ms >= 0 and end_ms == -1:
                self._speaking = True
                started = True
            elif start_ms == -1 and end_ms >= 0:
                self._speaking = False
                endpoint = True
                speech_end_ms = end_ms
            elif start_ms >= 0 and end_ms >= 0:
                started = True
                endpoint = True
                self._speaking = False
                speech_end_ms = end_ms
        if is_final and self._speaking:
            self._speaking = False
            endpoint = True
            speech_end_ms = self._stream_ms
        trailing = max(0, self._stream_ms - speech_end_ms) if endpoint and speech_end_ms is not None else 0
        return VadDecision(started=started, speaking=self._speaking, endpoint=endpoint, trailing_silence_ms=trailing)

    def reset(self) -> None:
        self._cache = {}
        self._speaking = False
        self._stream_ms = 0


class _FunASRDecoder:
    def __init__(self, engine: FunASREngine, options: SessionOptions) -> None:
        self._engine = engine
        self._hotwords = options.hotwords
        self._hotword = " ".join(self._hotwords) or None
        self._language = options.language

    def online(self, pcm: bytes, *, cache: dict[str, object], is_final: bool) -> OnlineDecode:
        result = self._engine.run_online(_pcm_to_float32(pcm), cache, is_final=is_final, hotword=self._hotword)
        return OnlineDecode(text=_extract_text(result), is_delta=True)

    def final(self, pcm: bytes) -> FinalDecode:
        result = self._engine.run_final(pcm, hotwords=self._hotwords, language=self._language)
        if isinstance(result, FinalDecode):
            return result
        return FinalDecode(text=_extract_text(result), word_timings=_extract_word_timings(result))


def _pcm_to_float32(pcm: bytes) -> np.ndarray:
    if not pcm:
        return np.empty(0, dtype=np.float32)
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


def _first_result(value: Any) -> dict[str, Any]:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    if isinstance(value, dict):
        return value
    return {}


def _extract_text(value: Any) -> str:
    text = _first_result(value).get("text", "")
    return text.strip() if isinstance(text, str) else ""


def _extract_vad_signals(value: Any) -> tuple[tuple[int, int], ...]:
    signals = _first_result(value).get("value", [])
    parsed: list[tuple[int, int]] = []
    if isinstance(signals, list):
        for signal in signals:
            if isinstance(signal, (list, tuple)) and len(signal) >= 2:
                try:
                    parsed.append((int(signal[0]), int(signal[1])))
                except (TypeError, ValueError):
                    continue
    return tuple(parsed)


def _extract_word_timings(value: Any) -> tuple[WordTiming, ...]:
    raw = _first_result(value).get("timestamp", [])
    timings: list[WordTiming] = []
    if isinstance(raw, list):
        for index, item in enumerate(raw):
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                try:
                    start_ms = int(item[0])
                    end_ms = int(item[1])
                except (TypeError, ValueError):
                    continue
                if 0 <= start_ms <= end_ms:
                    timings.append(WordTiming(token_index=index, start_ms=start_ms, end_ms=end_ms))
    return tuple(timings)


def _extract_speaker_embedding(value: Any) -> np.ndarray:
    raw = _first_result(value).get("spk_embedding")
    if raw is None:
        return np.empty(0, dtype=np.float32)
    if hasattr(raw, "detach"):
        raw = raw.detach()
    if hasattr(raw, "cpu"):
        raw = raw.cpu()
    if hasattr(raw, "numpy"):
        raw = raw.numpy()
    try:
        return np.asarray(raw, dtype=np.float32).reshape(-1)
    except (TypeError, ValueError):
        return np.empty(0, dtype=np.float32)
