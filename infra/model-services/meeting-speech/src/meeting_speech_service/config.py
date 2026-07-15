from __future__ import annotations

import math
from functools import cached_property
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SIQ_MEETING_SPEECH_",
        case_sensitive=False,
        extra="ignore",
    )

    enabled: bool = False
    deployment_profile: str = "local"
    host: str = "127.0.0.1"
    port: int = Field(default=8901, ge=1, le=65535)
    internal_service_token: SecretStr | None = None
    allowed_origins_csv: str = ""

    adapter: Literal["funasr", "mock"] = "funasr"
    allow_degraded_mock: bool = False
    funasr_source_root: Path | None = None
    device: str = "cpu"
    online_model: str = "iic/speech_paraformer_asr_nat-zh-cn-16k-common-vocab8404-online"
    final_model: str = "paraformer-zh"
    finalizer: Literal["local", "funasr_http"] = "local"
    http_finalizer_url: str | None = None
    http_finalizer_health_url: str | None = None
    http_finalizer_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    http_finalizer_queue_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    http_finalizer_max_concurrency: int = Field(default=2, ge=1, le=16)
    http_finalizer_max_response_bytes: int = Field(default=1_048_576, ge=1_024, le=16 * 1024 * 1024)
    vad_model: str = "fsmn-vad"
    punctuation_model: str = "ct-punc"
    # Favor recognition context over minimum first-token latency. Ten encoder
    # frames (~600 ms for Paraformer online) is the pre-acceleration baseline.
    online_chunk_size: str = "0,10,5"
    encoder_chunk_look_back: int = Field(default=4, ge=0, le=16)
    decoder_chunk_look_back: int = Field(default=1, ge=0, le=16)
    inference_timeout_seconds: float = Field(default=15.0, gt=0, le=120)

    speaker_adapter: Literal["none", "mock", "funasr"] = "none"
    speaker_model: str = "iic/speech_eres2netv2_sv_zh-cn_16k-common"
    speaker_cluster_threshold: float = Field(default=0.72, ge=0.0, le=1.0)
    speaker_cluster_update_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    speaker_cluster_min_margin: float = Field(default=0.04, ge=0.0, le=1.0)
    speaker_candidate_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    speaker_candidate_confirmations: int = Field(default=2, ge=1, le=8)
    speaker_candidate_max_gap_ms: int = Field(default=30_000, ge=0, le=300_000)
    speaker_max_tracks: int = Field(default=16, ge=1, le=64)
    speaker_min_segment_ms: int = Field(default=1_000, ge=300, le=10_000)
    speaker_new_track_min_segment_ms: int = Field(default=1_500, ge=300, le=15_000)
    speaker_max_prototypes: int = Field(default=8, ge=1, le=64)
    speaker_min_rms: float = Field(default=0.005, ge=0.0, le=1.0)
    speaker_max_clipping_ratio: float = Field(default=0.2, ge=0.0, le=1.0)
    speaker_inference_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    embedding_endpoint_enabled: bool = False
    embedding_min_seconds: float = Field(default=1.0, ge=0.3, le=10.0)
    embedding_max_seconds: float = Field(default=15.0, ge=1.0, le=60.0)
    embedding_max_concurrency: int = Field(default=1, ge=1, le=16)
    finalization_endpoint_enabled: bool = True
    finalization_max_window_seconds: int = Field(default=60, ge=2, le=120)
    finalization_max_sessions: int = Field(default=2, ge=1, le=32)
    finalization_max_cached_windows: int = Field(default=2_048, ge=16, le=10_000)
    finalization_session_ttl_seconds: int = Field(default=300, ge=30, le=3_600)
    mock_transcript_prefix: str = "[mock speech]"

    sample_rate: int = 16_000
    channels: int = 1
    min_chunk_ms: int = Field(default=100, ge=20, le=1_000)
    max_chunk_ms: int = Field(default=1_000, ge=100, le=2_000)
    max_frame_bytes: int = Field(default=32_000, ge=3_200, le=1_048_576)
    max_segment_seconds: int = Field(default=30, ge=2, le=120)
    pre_roll_ms: int = Field(default=240, ge=0, le=2_000)
    vad_min_speech_ms: int = Field(default=180, ge=20, le=2_000)
    vad_endpoint_silence_ms: int = Field(default=800, ge=100, le=5_000)
    vad_energy_threshold: float = Field(default=0.012, gt=0, lt=1)

    max_pending_frames: int = Field(default=16, ge=1, le=512)
    max_pending_bytes: int = Field(default=512_000, ge=3_200, le=64 * 1024 * 1024)
    max_gap_frames: int = Field(default=64, ge=1, le=4_096)
    recent_sequence_checksums: int = Field(default=256, ge=16, le=8_192)
    max_active_sessions: int = Field(default=4, ge=1, le=128)
    max_resident_sessions: int = Field(default=8, ge=1, le=256)
    resume_ttl_seconds: int = Field(default=60, ge=1, le=600)
    handshake_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    max_session_seconds: int = Field(default=14_400, ge=60, le=86_400)
    max_realtime_factor: float = Field(default=2.0, ge=1.0, le=20.0)
    rate_burst_seconds: float = Field(default=5.0, ge=0.5, le=60.0)

    max_hotwords: int = Field(default=100, ge=0, le=1_000)
    max_hotword_chars: int = Field(default=64, ge=1, le=256)

    @cached_property
    def allowed_origins(self) -> frozenset[str]:
        return frozenset(part.strip() for part in self.allowed_origins_csv.split(",") if part.strip())

    @cached_property
    def parsed_online_chunk_size(self) -> tuple[int, int, int]:
        values = tuple(int(part.strip()) for part in self.online_chunk_size.split(","))
        if len(values) != 3 or any(value < 0 for value in values) or values[1] == 0:
            raise ValueError("online_chunk_size must contain three non-negative integers with a positive stride")
        return values

    def first_partial_audio_budget_ms(self, input_chunk_ms: int) -> int:
        """Audio accumulation bound before one configured online decode window."""
        if input_chunk_ms <= 0:
            raise ValueError("input_chunk_ms must be positive")
        online_window_ms = self.parsed_online_chunk_size[1] * 60
        return math.ceil(online_window_ms / input_chunk_ms) * input_chunk_ms

    @property
    def protected_deployment(self) -> bool:
        return self.deployment_profile.strip().lower() in {"production", "prod", "docker"}

    @property
    def max_segment_bytes(self) -> int:
        return self.max_segment_seconds * self.sample_rate * self.channels * 2

    @property
    def resolved_speaker_cluster_update_threshold(self) -> float:
        if self.speaker_cluster_update_threshold is not None:
            return self.speaker_cluster_update_threshold
        return min(1.0, self.speaker_cluster_threshold + 0.1)

    @property
    def resolved_speaker_candidate_threshold(self) -> float:
        if self.speaker_candidate_threshold is not None:
            return self.speaker_candidate_threshold
        return min(1.0, self.speaker_cluster_threshold + 0.06)

    @model_validator(mode="after")
    def validate_boundaries(self) -> "Settings":
        if self.sample_rate != 16_000 or self.channels != 1:
            raise ValueError("meeting speech v1 only supports 16 kHz mono audio")
        if self.min_chunk_ms > self.max_chunk_ms:
            raise ValueError("min_chunk_ms cannot exceed max_chunk_ms")
        required_frame_bytes = self.max_chunk_ms * self.sample_rate * self.channels * 2 // 1_000
        if self.max_frame_bytes < required_frame_bytes:
            raise ValueError("max_frame_bytes is smaller than max_chunk_ms PCM payload")
        if self.max_pending_bytes < self.max_frame_bytes:
            raise ValueError("max_pending_bytes must hold at least one maximum-sized frame")
        if self.max_resident_sessions < self.max_active_sessions:
            raise ValueError("max_resident_sessions cannot be lower than max_active_sessions")
        if self.enabled and self.protected_deployment:
            token = self.internal_service_token.get_secret_value().strip() if self.internal_service_token else ""
            if not token:
                raise ValueError("internal_service_token is required in protected deployments")
            if self.adapter == "mock" or self.allow_degraded_mock:
                raise ValueError("mock speech adapters are forbidden in protected deployments")
            if self.speaker_adapter == "mock":
                raise ValueError("mock speaker adapters are forbidden in protected deployments")
        if self.adapter == "mock" and not self.allow_degraded_mock:
            raise ValueError("adapter=mock requires allow_degraded_mock=true")
        if self.speaker_adapter == "mock" and not self.allow_degraded_mock:
            raise ValueError("speaker_adapter=mock requires allow_degraded_mock=true")
        if self.adapter == "mock" and self.speaker_adapter == "funasr":
            raise ValueError("mock ASR cannot load the FunASR speaker adapter")
        if (
            self.speaker_cluster_update_threshold is not None
            and self.speaker_cluster_update_threshold < self.speaker_cluster_threshold
        ):
            raise ValueError("speaker_cluster_update_threshold cannot be lower than speaker_cluster_threshold")
        if (
            self.speaker_candidate_threshold is not None
            and self.speaker_candidate_threshold < self.speaker_cluster_threshold
        ):
            raise ValueError("speaker_candidate_threshold cannot be lower than speaker_cluster_threshold")
        if self.speaker_new_track_min_segment_ms < self.speaker_min_segment_ms:
            raise ValueError("speaker_new_track_min_segment_ms cannot be lower than speaker_min_segment_ms")
        if self.embedding_min_seconds > self.embedding_max_seconds:
            raise ValueError("embedding_min_seconds cannot exceed embedding_max_seconds")
        if self.finalization_session_ttl_seconds <= self.inference_timeout_seconds:
            raise ValueError("finalization_session_ttl_seconds must exceed inference_timeout_seconds")
        if self.embedding_endpoint_enabled:
            token = self.internal_service_token.get_secret_value().strip() if self.internal_service_token else ""
            if not token:
                raise ValueError("internal_service_token is required when the embedding endpoint is enabled")
        if not self.mock_transcript_prefix.strip():
            raise ValueError("mock_transcript_prefix cannot be empty")
        if self.finalizer == "funasr_http":
            if not self.http_finalizer_url:
                raise ValueError("http_finalizer_url is required when finalizer=funasr_http")
            self._validate_finalizer_url(self.http_finalizer_url, "http_finalizer_url")
            if self.http_finalizer_health_url:
                self._validate_finalizer_url(self.http_finalizer_health_url, "http_finalizer_health_url")
        # Force validation at configuration load rather than first inference.
        _ = self.parsed_online_chunk_size
        return self

    def _validate_finalizer_url(self, value: str, field_name: str) -> None:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(f"{field_name} must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(f"{field_name} cannot contain credentials, a query, or a fragment")
        if (
            self.protected_deployment
            and parsed.scheme != "https"
            and parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
        ):
            raise ValueError(f"{field_name} must use HTTPS or a loopback host in protected deployments")
