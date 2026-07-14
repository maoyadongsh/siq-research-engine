"""Consent-bound voiceprint enrollment and owner-private matching worker.

The worker never accepts filesystem paths, never logs or returns embeddings, and
does not make ASR depend on voiceprint availability. Repository operations are
kept behind a protocol so final authorization checks can happen transactionally.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import inspect
import json
import math
import os
import re
import secrets
import struct
import sys
from array import array
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Protocol, Sequence
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from services.meeting_voiceprint_tombstone import VoiceprintTombstoneLedger

VOICEPRINT_ENROLL_JOB_KIND = "voiceprint_enroll"
VOICEPRINT_MATCH_JOB_KIND = "voiceprint_match"
VOICEPRINT_PURPOSE = "future_meeting_speaker_identification"
VOICEPRINT_SCOPE = "user_private"
VOICEPRINT_CIPHERTEXT_SCHEMA = "siq.voiceprint.ciphertext.v2"
VOICEPRINT_LEGACY_CIPHERTEXT_SCHEMA = "siq.voiceprint.ciphertext.v1"
VOICEPRINT_EMBEDDING_SCHEMA = "siq.meeting.speaker_embedding.v1"
VOICEPRINT_AAD_SCHEMA = "siq.voiceprint.aad.v1"
VOICEPRINT_PLAINTEXT_MAGIC = b"SIQVP001"
_PLAINTEXT_HEADER = struct.Struct("!8sH")
_SAFE_KEY_ID = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_SAFE_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


class VoiceprintWorkerError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class VoiceprintMatchLevel(StrEnum):
    UNKNOWN = "unknown"
    SUGGESTION = "suggestion"
    AUTO_MATCH = "auto_match"


@dataclass(frozen=True, slots=True)
class VoiceprintJob:
    id: str
    meeting_id: str
    job_kind: str
    state: str
    lease_owner: str | None


@dataclass(frozen=True, slots=True)
class MeetingSnapshot:
    id: str
    owner_user_id: int
    voiceprint_enabled: bool
    state: str


@dataclass(frozen=True, slots=True)
class VoiceProfileSnapshot:
    id: str
    owner_user_id: int
    status: str
    encoder_name: str | None = None
    encoder_version: str | None = None
    encrypted_embedding: str | None = None
    key_id: str | None = None
    consent_active: bool = False
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ConsentSnapshot:
    id: str
    voice_profile_id: str
    actor_user_id: int
    purpose: str
    scope: str
    policy_version: str
    source_meeting_id: str
    granted_at: datetime
    revoked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SpeakerTrackSnapshot:
    id: str
    meeting_id: str
    label_source: str
    voice_profile_id: str | None = None
    display_name: str | None = None


@dataclass(frozen=True, slots=True)
class AudioChunkRef:
    id: str
    meeting_id: str
    stream_epoch: int
    sequence: int
    start_ms: int
    duration_ms: int
    storage_key: str
    sha256: str
    byte_size: int
    codec: str
    sample_rate: int
    channels: int
    state: str


@dataclass(frozen=True, slots=True)
class TrackSegment:
    id: str
    meeting_id: str
    speaker_track_id: str
    start_ms: int
    end_ms: int
    overlap: bool
    noise_level: float | None
    asr_confidence: float | None

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass(frozen=True, slots=True)
class EnrollmentContext:
    job: VoiceprintJob
    meeting: MeetingSnapshot
    profile: VoiceProfileSnapshot
    consent: ConsentSnapshot
    track: SpeakerTrackSnapshot
    chunks: tuple[AudioChunkRef, ...]
    segments: tuple[TrackSegment, ...]


@dataclass(frozen=True, slots=True)
class MatchContext:
    meeting: MeetingSnapshot
    track: SpeakerTrackSnapshot
    chunks: tuple[AudioChunkRef, ...]
    segments: tuple[TrackSegment, ...]


@dataclass(frozen=True, slots=True)
class MatchJobContext:
    job: VoiceprintJob
    context: MatchContext


@dataclass(frozen=True, slots=True)
class ActiveVoiceTemplate:
    profile: VoiceProfileSnapshot
    consent: ConsentSnapshot
    candidate_set_fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    encoder_ref: str
    values: tuple[float, ...]
    duration_ms: int


@dataclass(frozen=True, slots=True)
class SelectedSample:
    segment: TrackSegment
    pcm: bytes
    duration_ms: int
    rms: float
    clipping_ratio: float


@dataclass(frozen=True, slots=True)
class EnrollmentCompletion:
    job_id: str
    worker_id: str
    owner_user_id: int
    profile_id: str
    consent_id: str
    source_meeting_id: str
    source_track_id: str
    encoder_name: str
    encoder_version: str
    encrypted_embedding: str
    key_id: str
    sample_count: int
    effective_duration_ms: int
    quality_summary: dict[str, Any]


@dataclass(frozen=True, slots=True)
class MatchRecord:
    owner_user_id: int
    meeting_id: str
    speaker_track_id: str
    voice_profile_id: str
    encoder_name: str
    encoder_version: str
    threshold_version: str
    top1_score: float
    top1_top2_margin: float
    effective_duration_ms: int
    quality_grade: str
    decision: str
    validated_auto_match_gate: bool = False
    job_id: str | None = None
    worker_id: str | None = None
    expected_profile_updated_at: datetime | None = None
    expected_key_id: str | None = None
    expected_encrypted_embedding_sha256: str | None = None
    expected_candidate_set_fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class DeleteResult:
    profile_id: str
    owner_user_id: int
    ciphertext_cleared: bool
    key_id_cleared: bool
    temporary_samples_deleted: bool


@dataclass(frozen=True, slots=True)
class WorkerRunResult:
    status: str
    job_id: str | None = None
    public_error_code: str | None = None


@dataclass(frozen=True, slots=True)
class MatchOutcome:
    level: VoiceprintMatchLevel
    voice_profile_id: str | None
    top1_score: float
    top1_top2_margin: float
    effective_duration_ms: int
    quality_grade: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class VoiceprintQualityPolicy:
    version: str = "voiceprint-sample-quality.v1"
    min_sample_count: int = 3
    max_sample_count: int = 8
    min_effective_duration_ms: int = 6_000
    min_segment_duration_ms: int = 1_000
    max_segment_duration_ms: int = 12_000
    max_noise_level: float = 0.35
    min_asr_confidence: float = 0.55
    min_rms: float = 0.008
    max_clipping_ratio: float = 0.01
    max_mean_noise_level: float = 0.20
    min_aggregate_asr_confidence: float = 0.75
    min_mean_rms: float = 0.015
    max_aggregate_clipping_ratio: float = 0.005
    max_pcm_bytes_per_sample: int = 384_000

    def __post_init__(self) -> None:
        if self.min_sample_count < 2 or self.max_sample_count < self.min_sample_count:
            raise ValueError("voiceprint sample count bounds are invalid")
        if self.min_effective_duration_ms <= 0:
            raise ValueError("voiceprint minimum duration must be positive")
        if not 0 <= self.max_noise_level <= 1 or not 0 <= self.min_asr_confidence <= 1:
            raise ValueError("voiceprint metadata quality bounds are invalid")
        if not 0 < self.min_rms < 1 or not 0 <= self.max_clipping_ratio <= 1:
            raise ValueError("voiceprint PCM quality bounds are invalid")
        if (
            not 0 <= self.max_mean_noise_level <= self.max_noise_level
            or not self.min_asr_confidence <= self.min_aggregate_asr_confidence <= 1
            or not self.min_rms <= self.min_mean_rms < 1
            or not 0 <= self.max_aggregate_clipping_ratio <= self.max_clipping_ratio
        ):
            raise ValueError("voiceprint aggregate quality bounds are invalid")


@dataclass(frozen=True, slots=True)
class VoiceprintThresholdPolicy:
    version: str
    suggestion_min_score: float
    suggestion_min_margin: float
    auto_min_score: float
    auto_min_margin: float
    min_effective_duration_ms: int
    allowed_quality_grades: frozenset[str] = frozenset({"good"})
    auto_match_validated: bool = False

    def __post_init__(self) -> None:
        for value in (
            self.suggestion_min_score,
            self.suggestion_min_margin,
            self.auto_min_score,
            self.auto_min_margin,
        ):
            if not 0 <= value <= 1:
                raise ValueError("voiceprint threshold values must be between 0 and 1")
        if self.auto_min_score < self.suggestion_min_score or self.auto_min_margin < self.suggestion_min_margin:
            raise ValueError("auto-match thresholds cannot be weaker than suggestion thresholds")
        if self.min_effective_duration_ms <= 0 or not self.version.strip():
            raise ValueError("voiceprint threshold version and duration are required")

    @classmethod
    def from_json(cls, value: str) -> "VoiceprintThresholdPolicy":
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("voiceprint threshold policy is invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("voiceprint threshold policy must be an object")
        return cls(
            version=str(payload["version"]),
            suggestion_min_score=float(payload["suggestion_min_score"]),
            suggestion_min_margin=float(payload["suggestion_min_margin"]),
            auto_min_score=float(payload["auto_min_score"]),
            auto_min_margin=float(payload["auto_min_margin"]),
            min_effective_duration_ms=int(payload["min_effective_duration_ms"]),
            allowed_quality_grades=frozenset(str(item) for item in payload.get("allowed_quality_grades", ["good"])),
            auto_match_validated=payload.get("auto_match_validated") is True,
        )


@dataclass(frozen=True, slots=True)
class VoiceprintWorkerSettings:
    worker_id: str
    lease_seconds: int
    encoder_name: str
    encoder_version: str
    expected_encoder_ref: str
    auto_match_enabled: bool = False
    quality: VoiceprintQualityPolicy = field(default_factory=VoiceprintQualityPolicy)

    def __post_init__(self) -> None:
        if not self.worker_id.strip() or len(self.worker_id) > 100:
            raise ValueError("voiceprint worker_id is invalid")
        if not 180 <= self.lease_seconds <= 3_600:
            raise ValueError("voiceprint lease_seconds must be between 180 and 3600")
        if not self.encoder_name.strip() or not self.encoder_version.strip() or not self.expected_encoder_ref.strip():
            raise ValueError("voiceprint encoder identity is required")

    @classmethod
    def from_env(cls, *, worker_id: str) -> "VoiceprintWorkerSettings":
        encoder_ref = os.getenv(
            "SIQ_MEETING_VOICEPRINT_ENCODER_REF",
            "iic/speech_eres2netv2_sv_zh-cn_16k-common",
        ).strip()
        return cls(
            worker_id=worker_id,
            lease_seconds=_env_int("SIQ_MEETING_VOICEPRINT_LEASE_SECONDS", 300, 180, 3_600),
            encoder_name=os.getenv("SIQ_MEETING_VOICEPRINT_ENCODER_NAME", "funasr-eres2netv2").strip(),
            encoder_version=os.getenv("SIQ_MEETING_VOICEPRINT_ENCODER_VERSION", encoder_ref).strip(),
            expected_encoder_ref=encoder_ref,
            auto_match_enabled=_env_bool("SIQ_MEETING_VOICEPRINT_AUTO_MATCH_ENABLED", False),
        )


class VoiceprintRepository(Protocol):
    async def claim_job(
        self,
        worker_id: str,
        kinds: set[str],
        lease_seconds: int,
    ) -> VoiceprintJob | None: ...

    async def voiceprint_enrollment_context(self, job_id: str, worker_id: str) -> EnrollmentContext: ...

    async def complete_voiceprint_enrollment(self, completion: EnrollmentCompletion) -> Any: ...

    async def fail_job(
        self,
        job_id: str,
        worker_id: str,
        public_error_code: str,
        *,
        retryable: bool,
        internal_diagnostic: str,
    ) -> None: ...

    async def voiceprint_match_context(
        self,
        meeting_id: str,
        track_id: str,
        owner_user_id: int,
    ) -> MatchContext: ...

    async def voiceprint_match_job_context(
        self,
        job_id: str,
        worker_id: str,
    ) -> MatchJobContext: ...

    async def active_voiceprint_profiles(
        self,
        owner_user_id: int,
        encoder_name: str,
        encoder_version: str,
    ) -> Sequence[ActiveVoiceTemplate]: ...

    async def record_voiceprint_match(self, record: MatchRecord) -> Any: ...

    async def complete_voiceprint_match_job(
        self,
        job_id: str,
        worker_id: str,
        *,
        reason_code: str,
        effective_duration_ms: int,
        quality_grade: str,
    ) -> Any: ...

    async def delete_voiceprint_profile(
        self,
        profile_id: str,
        owner_user_id: int,
    ) -> DeleteResult: ...


class ControlledAudioReader(Protocol):
    async def read_pcm_range(
        self,
        *,
        owner_user_id: int,
        meeting_id: str,
        chunks: Sequence[AudioChunkRef],
        start_ms: int,
        end_ms: int,
        max_bytes: int,
    ) -> bytes: ...


class SpeakerEmbeddingClient(Protocol):
    async def embed(
        self,
        pcm: bytes,
        *,
        authorization_id: str,
        purpose: str,
    ) -> EmbeddingResult: ...


class HttpSpeakerEmbeddingClient:
    def __init__(
        self,
        *,
        endpoint: str,
        service_token: str,
        expected_encoder_ref: str,
        timeout_seconds: float = 15.0,
        max_response_bytes: int = 262_144,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        parsed_endpoint = urlsplit(endpoint)
        if (
            parsed_endpoint.scheme not in {"http", "https"}
            or not parsed_endpoint.hostname
            or parsed_endpoint.username
            or parsed_endpoint.password
            or parsed_endpoint.query
            or parsed_endpoint.fragment
        ):
            raise ValueError("speaker embedding endpoint must be an absolute credential-free HTTP(S) URL")
        if parsed_endpoint.scheme != "https" and parsed_endpoint.hostname not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("speaker embedding endpoint must use HTTPS or a loopback host")
        if not service_token.strip():
            raise ValueError("speaker embedding service token is required")
        if not expected_encoder_ref.strip():
            raise ValueError("speaker embedding encoder reference is required")
        if timeout_seconds <= 0 or not 1_024 <= max_response_bytes <= 1_048_576:
            raise ValueError("speaker embedding HTTP limits are invalid")
        self._endpoint = endpoint
        self._service_token = service_token
        self._expected_encoder_ref = expected_encoder_ref
        self._max_response_bytes = max_response_bytes
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            trust_env=False,
        )
        self._owns_client = client is None

    async def embed(
        self,
        pcm: bytes,
        *,
        authorization_id: str,
        purpose: str,
    ) -> EmbeddingResult:
        if purpose not in {"enrollment", "match"}:
            raise VoiceprintWorkerError("VOICEPRINT_PURPOSE_INVALID", "embedding purpose is invalid")
        try:
            UUID(authorization_id)
        except (TypeError, ValueError) as exc:
            raise VoiceprintWorkerError(
                "VOICEPRINT_AUTHORIZATION_REFERENCE_INVALID",
                "embedding authorization reference is invalid",
            ) from exc
        try:
            async with self._client.stream(
                "POST",
                self._endpoint,
                content=pcm,
                headers={
                    "X-SIQ-Service-Token": self._service_token,
                    "X-SIQ-Voiceprint-Consent": authorization_id,
                    "X-SIQ-Voiceprint-Purpose": purpose,
                    "X-SIQ-Audio-Encoding": "pcm_s16le",
                    "Content-Type": "application/octet-stream",
                },
            ) as response:
                if response.status_code != 200:
                    raise VoiceprintWorkerError(
                        "VOICEPRINT_ENCODER_UNAVAILABLE",
                        "speaker embedding service rejected the request",
                        retryable=response.status_code >= 500 or response.status_code == 429,
                    )
                body = await _read_bounded_response(response, self._max_response_bytes)
        except httpx.HTTPError as exc:
            raise VoiceprintWorkerError(
                "VOICEPRINT_ENCODER_UNAVAILABLE",
                "speaker embedding service is unavailable",
                retryable=True,
            ) from exc
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VoiceprintWorkerError(
                "VOICEPRINT_ENCODER_RESPONSE_INVALID",
                "speaker embedding response is invalid",
                retryable=True,
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != VOICEPRINT_EMBEDDING_SCHEMA
            or payload.get("persisted") is not False
            or payload.get("purpose") != purpose
        ):
            raise VoiceprintWorkerError(
                "VOICEPRINT_ENCODER_RESPONSE_INVALID",
                "speaker embedding schema is invalid",
                retryable=True,
            )
        encoder_ref = payload.get("encoder_ref")
        values = payload.get("embedding")
        duration_ms = payload.get("duration_ms")
        if (
            encoder_ref != self._expected_encoder_ref
            or not isinstance(values, list)
            or not isinstance(duration_ms, int)
            or duration_ms <= 0
            or payload.get("dimension") != len(values)
            or abs(duration_ms - round(len(pcm) / 32)) > 100
        ):
            raise VoiceprintWorkerError(
                "VOICEPRINT_ENCODER_MISMATCH",
                "speaker embedding identity or output is invalid",
            )
        try:
            vector = l2_normalize(tuple(float(item) for item in values))
        except (TypeError, ValueError) as exc:
            raise VoiceprintWorkerError(
                "VOICEPRINT_ENCODER_RESPONSE_INVALID",
                "speaker embedding vector is invalid",
            ) from exc
        if len(vector) < 2 or len(vector) > 16_384:
            raise VoiceprintWorkerError(
                "VOICEPRINT_ENCODER_RESPONSE_INVALID",
                "speaker embedding dimension is invalid",
            )
        return EmbeddingResult(encoder_ref=encoder_ref, values=vector, duration_ms=duration_ms)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class VoiceprintKeyring:
    def __init__(
        self,
        *,
        active_key_id: str,
        keys: dict[str, bytes],
        tombstones: VoiceprintTombstoneLedger | None = None,
    ) -> None:
        if not _SAFE_KEY_ID.fullmatch(active_key_id) or active_key_id not in keys:
            raise ValueError("voiceprint active key id is invalid")
        if not keys or any(not _SAFE_KEY_ID.fullmatch(key_id) for key_id in keys):
            raise ValueError("voiceprint keyring ids are invalid")
        if any(len(key) != 32 for key in keys.values()):
            raise ValueError("voiceprint AES-GCM keys must be 32 bytes")
        self.active_key_id = active_key_id
        self._keys = dict(keys)
        self.tombstones = tombstones

    @classmethod
    def from_env(cls) -> "VoiceprintKeyring":
        active_key_id = os.getenv("SIQ_MEETINGS_VOICEPRINT_KEY_ID", "").strip()
        raw_keyring = os.getenv("SIQ_MEETINGS_VOICEPRINT_KEYRING_JSON", "").strip()
        if not active_key_id or not raw_keyring or len(raw_keyring) > 8_192:
            raise ValueError("voiceprint key id and keyring configuration are required")
        try:
            mapping = json.loads(raw_keyring)
        except json.JSONDecodeError as exc:
            raise ValueError("voiceprint keyring configuration is invalid") from exc
        if not isinstance(mapping, dict) or not mapping:
            raise ValueError("voiceprint keyring configuration must map key ids to env names")
        keys: dict[str, bytes] = {}
        for key_id, env_name in mapping.items():
            if not isinstance(key_id, str) or not isinstance(env_name, str) or not _SAFE_ENV_NAME.fullmatch(env_name):
                raise ValueError("voiceprint keyring configuration contains an invalid entry")
            encoded = os.getenv(env_name, "").strip()
            if not encoded:
                raise ValueError("a configured voiceprint key is unavailable")
            keys[key_id] = _decode_key(encoded)
        return cls(
            active_key_id=active_key_id,
            keys=keys,
            tombstones=VoiceprintTombstoneLedger.from_env(),
        )

    def encrypt(
        self,
        values: Sequence[float],
        *,
        owner_user_id: int,
        profile_id: str,
        encoder_name: str,
        encoder_version: str,
    ) -> tuple[str, str]:
        self._assert_not_tombstoned(owner_user_id=owner_user_id, profile_id=profile_id)
        vector = l2_normalize(values)
        plaintext = _encode_vector(vector)
        aad = voiceprint_aad(
            owner_user_id=owner_user_id,
            profile_id=profile_id,
            encoder_name=encoder_name,
            encoder_version=encoder_version,
        )
        nonce = secrets.token_bytes(12)
        template_key = _derive_template_key(self._keys[self.active_key_id], aad)
        ciphertext = AESGCM(template_key).encrypt(nonce, plaintext, aad)
        envelope = json.dumps(
            {
                "schema_version": VOICEPRINT_CIPHERTEXT_SCHEMA,
                "algorithm": "AES-256-GCM+HKDF-SHA256",
                "nonce": _b64encode(nonce),
                "ciphertext": _b64encode(ciphertext),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return envelope, self.active_key_id

    def decrypt(
        self,
        envelope: str,
        *,
        key_id: str,
        owner_user_id: int,
        profile_id: str,
        encoder_name: str,
        encoder_version: str,
    ) -> tuple[float, ...]:
        self._assert_not_tombstoned(owner_user_id=owner_user_id, profile_id=profile_id)
        key = self._keys.get(key_id)
        if key is None:
            raise VoiceprintWorkerError(
                "VOICEPRINT_KEY_UNAVAILABLE",
                "voiceprint decryption key is unavailable",
                retryable=True,
            )
        try:
            payload = json.loads(envelope)
            if not isinstance(payload, dict) or set(payload) != {
                "schema_version",
                "algorithm",
                "nonce",
                "ciphertext",
            }:
                raise ValueError
            schema_version = payload["schema_version"]
            algorithm = payload["algorithm"]
            if (schema_version, algorithm) not in {
                (VOICEPRINT_CIPHERTEXT_SCHEMA, "AES-256-GCM+HKDF-SHA256"),
                (VOICEPRINT_LEGACY_CIPHERTEXT_SCHEMA, "AES-256-GCM"),
            }:
                raise ValueError
            nonce = _b64decode(payload["nonce"])
            ciphertext = _b64decode(payload["ciphertext"])
            if len(nonce) != 12 or len(ciphertext) < 16:
                raise ValueError
            aad = voiceprint_aad(
                owner_user_id=owner_user_id,
                profile_id=profile_id,
                encoder_name=encoder_name,
                encoder_version=encoder_version,
            )
            decryption_key = (
                _derive_template_key(key, aad)
                if schema_version == VOICEPRINT_CIPHERTEXT_SCHEMA
                else key
            )
            plaintext = AESGCM(decryption_key).decrypt(nonce, ciphertext, aad)
            return _decode_vector(plaintext)
        except (InvalidTag, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise VoiceprintWorkerError(
                "VOICEPRINT_CIPHERTEXT_INVALID",
                "voiceprint ciphertext authentication failed",
            ) from exc

    def _assert_not_tombstoned(self, *, owner_user_id: int, profile_id: str) -> None:
        if self.tombstones is not None and self.tombstones.is_tombstoned(
            owner_user_id=owner_user_id,
            profile_id=profile_id,
        ):
            raise VoiceprintWorkerError(
                "VOICEPRINT_TEMPLATE_DESTROYED",
                "voiceprint template key derivation is permanently disabled",
            )


class MeetingVoiceprintWorker:
    def __init__(
        self,
        *,
        repository: VoiceprintRepository,
        audio_reader: ControlledAudioReader,
        embedding_client: SpeakerEmbeddingClient,
        keyring: VoiceprintKeyring,
        settings: VoiceprintWorkerSettings,
        thresholds: VoiceprintThresholdPolicy | None = None,
    ) -> None:
        self.repository = repository
        self.audio_reader = audio_reader
        self.embedding_client = embedding_client
        self.keyring = keyring
        self.settings = settings
        self.thresholds = thresholds

    async def run_once(self) -> WorkerRunResult:
        kinds = {VOICEPRINT_ENROLL_JOB_KIND}
        if self.thresholds is not None:
            kinds.add(VOICEPRINT_MATCH_JOB_KIND)
        try:
            job = await self.repository.claim_job(
                self.settings.worker_id,
                kinds,
                self.settings.lease_seconds,
            )
        except VoiceprintWorkerError as exc:
            return WorkerRunResult(status="failed", public_error_code=exc.code)
        except Exception:
            return WorkerRunResult(
                status="failed",
                public_error_code="VOICEPRINT_REPOSITORY_UNAVAILABLE",
            )
        if job is None:
            return WorkerRunResult(status="idle")
        if job.job_kind not in {VOICEPRINT_ENROLL_JOB_KIND, VOICEPRINT_MATCH_JOB_KIND}:
            await self._fail_job(job, "VOICEPRINT_JOB_KIND_INVALID", retryable=False)
            return WorkerRunResult(status="failed", job_id=job.id, public_error_code="VOICEPRINT_JOB_KIND_INVALID")
        try:
            if job.job_kind == VOICEPRINT_ENROLL_JOB_KIND:
                await self._enroll(job)
            else:
                await self._match_job(job)
            return WorkerRunResult(status="succeeded", job_id=job.id)
        except VoiceprintWorkerError as exc:
            await self._fail_job(job, exc.code, retryable=exc.retryable)
            return WorkerRunResult(status="failed", job_id=job.id, public_error_code=exc.code)
        except Exception as exc:
            await self._fail_job(
                job,
                "VOICEPRINT_WORKER_FAILED",
                retryable=True,
                internal_diagnostic=type(exc).__name__,
            )
            return WorkerRunResult(status="failed", job_id=job.id, public_error_code="VOICEPRINT_WORKER_FAILED")

    async def _enroll(self, job: VoiceprintJob) -> None:
        context = await self.repository.voiceprint_enrollment_context(job.id, self.settings.worker_id)
        _validate_enrollment_context(context, worker_id=self.settings.worker_id)
        samples = await self._select_samples(
            context.meeting.owner_user_id, context.meeting.id, context.chunks, context.segments
        )
        embeddings: list[tuple[float, ...]] = []
        for sample in samples:
            result = await self.embedding_client.embed(
                sample.pcm,
                authorization_id=context.consent.id,
                purpose="enrollment",
            )
            if result.encoder_ref != self.settings.expected_encoder_ref:
                raise VoiceprintWorkerError("VOICEPRINT_ENCODER_MISMATCH", "speaker encoder changed during enrollment")
            embeddings.append(result.values)
        aggregate = aggregate_embeddings(embeddings)

        # Consent, owner, source track and lease are checked again after all model calls.
        fresh = await self.repository.voiceprint_enrollment_context(job.id, self.settings.worker_id)
        _validate_enrollment_context(fresh, worker_id=self.settings.worker_id)
        if _enrollment_identity(fresh) != _enrollment_identity(context):
            raise VoiceprintWorkerError("VOICEPRINT_CONTEXT_CHANGED", "voiceprint enrollment context changed")

        encrypted_embedding, key_id = self.keyring.encrypt(
            aggregate,
            owner_user_id=fresh.meeting.owner_user_id,
            profile_id=fresh.profile.id,
            encoder_name=self.settings.encoder_name,
            encoder_version=self.settings.encoder_version,
        )
        quality_summary = _quality_summary(samples, self.settings.quality)
        await self.repository.complete_voiceprint_enrollment(
            EnrollmentCompletion(
                job_id=job.id,
                worker_id=self.settings.worker_id,
                owner_user_id=fresh.meeting.owner_user_id,
                profile_id=fresh.profile.id,
                consent_id=fresh.consent.id,
                source_meeting_id=fresh.meeting.id,
                source_track_id=fresh.track.id,
                encoder_name=self.settings.encoder_name,
                encoder_version=self.settings.encoder_version,
                encrypted_embedding=encrypted_embedding,
                key_id=key_id,
                sample_count=len(samples),
                effective_duration_ms=sum(item.duration_ms for item in samples),
                quality_summary=quality_summary,
            )
        )

    async def match_track(
        self,
        *,
        meeting_id: str,
        track_id: str,
        owner_user_id: int,
    ) -> MatchOutcome:
        if self.thresholds is None:
            raise VoiceprintWorkerError(
                "VOICEPRINT_THRESHOLDS_UNAVAILABLE",
                "versioned voiceprint thresholds are not configured",
            )
        context = await self.repository.voiceprint_match_context(meeting_id, track_id, owner_user_id)
        return await self._match_track_context(
            context,
            owner_user_id=owner_user_id,
            match_job=None,
        )

    async def _match_job(self, job: VoiceprintJob) -> None:
        if self.thresholds is None:
            raise VoiceprintWorkerError(
                "VOICEPRINT_THRESHOLDS_UNAVAILABLE",
                "versioned voiceprint thresholds are not configured",
            )
        job_context = await self.repository.voiceprint_match_job_context(
            job.id,
            self.settings.worker_id,
        )
        _validate_match_job_context(job_context, worker_id=self.settings.worker_id)
        outcome = await self._match_track_context(
            job_context.context,
            owner_user_id=job_context.context.meeting.owner_user_id,
            match_job=job_context,
        )
        if outcome.level == VoiceprintMatchLevel.UNKNOWN:
            await self.repository.complete_voiceprint_match_job(
                job.id,
                self.settings.worker_id,
                reason_code=outcome.reason_code,
                effective_duration_ms=outcome.effective_duration_ms,
                quality_grade=outcome.quality_grade,
            )

    async def _match_track_context(
        self,
        context: MatchContext,
        *,
        owner_user_id: int,
        match_job: MatchJobContext | None,
    ) -> MatchOutcome:
        meeting_id = context.meeting.id
        track_id = context.track.id
        _validate_match_context(context, owner_user_id=owner_user_id)
        if context.track.label_source == "manual" or context.track.display_name:
            return _unknown_outcome("VOICEPRINT_MANUAL_LABEL_LOCKED")
        try:
            samples = await self._select_samples(
                owner_user_id,
                meeting_id,
                context.chunks,
                context.segments,
            )
        except VoiceprintWorkerError as exc:
            if exc.code != "VOICEPRINT_SAMPLES_INSUFFICIENT":
                raise
            return _unknown_outcome(
                "VOICEPRINT_DURATION_OR_QUALITY_INSUFFICIENT",
                quality_grade="insufficient",
            )
        embeddings: list[tuple[float, ...]] = []
        for sample in samples:
            value = await self.embedding_client.embed(
                sample.pcm,
                authorization_id=meeting_id,
                purpose="match",
            )
            if value.encoder_ref != self.settings.expected_encoder_ref:
                raise VoiceprintWorkerError("VOICEPRINT_ENCODER_MISMATCH", "speaker encoder changed during matching")
            embeddings.append(value.values)
        aggregate = aggregate_embeddings(embeddings)
        effective_duration_ms = sum(item.duration_ms for item in samples)
        quality_grade = _quality_grade(samples, self.settings.quality)
        templates = await self.repository.active_voiceprint_profiles(
            owner_user_id,
            self.settings.encoder_name,
            self.settings.encoder_version,
        )
        try:
            scored = self._score_templates(owner_user_id, aggregate, templates)
        except VoiceprintWorkerError as exc:
            if exc.code != "VOICEPRINT_TEMPLATE_SET_INCOMPLETE":
                raise
            return _unknown_outcome(
                exc.code,
                effective_duration_ms=effective_duration_ms,
                quality_grade=quality_grade,
            )
        outcome = classify_voiceprint_match(
            scored,
            effective_duration_ms=effective_duration_ms,
            quality_grade=quality_grade,
            thresholds=self.thresholds,
            auto_match_enabled=self.settings.auto_match_enabled,
        )
        if outcome.level == VoiceprintMatchLevel.UNKNOWN or outcome.voice_profile_id is None:
            return outcome

        if match_job is None:
            fresh = await self.repository.voiceprint_match_context(meeting_id, track_id, owner_user_id)
        else:
            fresh_job = await self.repository.voiceprint_match_job_context(
                match_job.job.id,
                self.settings.worker_id,
            )
            _validate_match_job_context(fresh_job, worker_id=self.settings.worker_id)
            if _match_job_identity(fresh_job) != _match_job_identity(match_job):
                raise VoiceprintWorkerError(
                    "VOICEPRINT_CONTEXT_CHANGED",
                    "voiceprint match job context changed",
                )
            fresh = fresh_job.context
        _validate_match_context(fresh, owner_user_id=owner_user_id)
        if fresh.track.label_source == "manual" or fresh.track.display_name:
            return _unknown_outcome(
                "VOICEPRINT_MANUAL_LABEL_LOCKED",
                effective_duration_ms=effective_duration_ms,
                quality_grade=quality_grade,
            )
        final_templates = await self.repository.active_voiceprint_profiles(
            owner_user_id,
            self.settings.encoder_name,
            self.settings.encoder_version,
        )
        try:
            final_scored = self._score_templates(owner_user_id, aggregate, final_templates)
        except VoiceprintWorkerError as exc:
            if exc.code != "VOICEPRINT_TEMPLATE_SET_INCOMPLETE":
                raise
            return _unknown_outcome(
                exc.code,
                effective_duration_ms=effective_duration_ms,
                quality_grade=quality_grade,
            )
        outcome = classify_voiceprint_match(
            final_scored,
            effective_duration_ms=effective_duration_ms,
            quality_grade=quality_grade,
            thresholds=self.thresholds,
            auto_match_enabled=self.settings.auto_match_enabled,
        )
        if outcome.level == VoiceprintMatchLevel.UNKNOWN or outcome.voice_profile_id is None:
            return outcome
        selected_template = next(
            (item for item in final_templates if item.profile.id == outcome.voice_profile_id),
            None,
        )
        if selected_template is None or not selected_template.profile.encrypted_embedding:
            raise VoiceprintWorkerError(
                "VOICEPRINT_CONTEXT_CHANGED",
                "voiceprint winning template changed before publication",
                retryable=True,
            )
        decision = "auto_applied" if outcome.level == VoiceprintMatchLevel.AUTO_MATCH else "suggested"
        if outcome.level == VoiceprintMatchLevel.AUTO_MATCH and (
            selected_template.profile.updated_at is None
            or not selected_template.profile.key_id
            or not selected_template.candidate_set_fingerprint
        ):
            raise VoiceprintWorkerError(
                "VOICEPRINT_AUTO_MATCH_GUARD_UNAVAILABLE",
                "automatic voiceprint matching concurrency guard is unavailable",
            )
        await self.repository.record_voiceprint_match(
            MatchRecord(
                owner_user_id=owner_user_id,
                meeting_id=meeting_id,
                speaker_track_id=track_id,
                voice_profile_id=outcome.voice_profile_id,
                encoder_name=self.settings.encoder_name,
                encoder_version=self.settings.encoder_version,
                threshold_version=self.thresholds.version,
                top1_score=outcome.top1_score,
                top1_top2_margin=outcome.top1_top2_margin,
                effective_duration_ms=effective_duration_ms,
                quality_grade=quality_grade,
                decision=decision,
                validated_auto_match_gate=(
                    outcome.level == VoiceprintMatchLevel.AUTO_MATCH
                    and self.settings.auto_match_enabled
                    and self.thresholds.auto_match_validated
                ),
                job_id=match_job.job.id if match_job is not None else None,
                worker_id=self.settings.worker_id if match_job is not None else None,
                expected_profile_updated_at=selected_template.profile.updated_at,
                expected_key_id=selected_template.profile.key_id,
                expected_encrypted_embedding_sha256=hashlib.sha256(
                    selected_template.profile.encrypted_embedding.encode("utf-8")
                ).hexdigest(),
                expected_candidate_set_fingerprint=selected_template.candidate_set_fingerprint,
            )
        )
        return outcome

    async def delete_profile(self, *, profile_id: str, owner_user_id: int) -> DeleteResult:
        result = await self.repository.delete_voiceprint_profile(profile_id, owner_user_id)
        if not (result.ciphertext_cleared and result.key_id_cleared and result.temporary_samples_deleted):
            raise VoiceprintWorkerError(
                "VOICEPRINT_DELETE_INCOMPLETE",
                "voiceprint deletion did not clear every managed copy",
                retryable=True,
            )
        return result

    async def _select_samples(
        self,
        owner_user_id: int,
        meeting_id: str,
        chunks: Sequence[AudioChunkRef],
        segments: Sequence[TrackSegment],
    ) -> tuple[SelectedSample, ...]:
        policy = self.settings.quality
        candidates = select_non_overlapping_segments(segments, meeting_id=meeting_id, policy=policy)
        accepted: list[SelectedSample] = []
        for segment in candidates:
            pcm = await self.audio_reader.read_pcm_range(
                owner_user_id=owner_user_id,
                meeting_id=meeting_id,
                chunks=chunks,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                max_bytes=policy.max_pcm_bytes_per_sample,
            )
            sample = inspect_pcm_sample(segment, pcm)
            if sample.rms < policy.min_rms or sample.clipping_ratio > policy.max_clipping_ratio:
                continue
            accepted.append(sample)
            if len(accepted) >= policy.max_sample_count:
                break
        total_duration = sum(item.duration_ms for item in accepted)
        if (
            len(accepted) < policy.min_sample_count
            or total_duration < policy.min_effective_duration_ms
            or _quality_grade(accepted, policy) != "good"
        ):
            raise VoiceprintWorkerError(
                "VOICEPRINT_SAMPLES_INSUFFICIENT",
                "voiceprint enrollment needs more clear non-overlapping samples",
            )
        return tuple(accepted)

    def _score_templates(
        self,
        owner_user_id: int,
        probe: Sequence[float],
        templates: Sequence[ActiveVoiceTemplate],
    ) -> list[tuple[str, float]]:
        scored: list[tuple[str, float]] = []
        for item in templates:
            profile = item.profile
            consent = item.consent
            if (
                profile.owner_user_id != owner_user_id
                or profile.status != "active"
                or not profile.consent_active
                or consent.actor_user_id != owner_user_id
                or consent.voice_profile_id != profile.id
                or consent.revoked_at is not None
                or not consent.policy_version.strip()
                or consent.purpose != VOICEPRINT_PURPOSE
                or consent.scope != VOICEPRINT_SCOPE
                or profile.encoder_name != self.settings.encoder_name
                or profile.encoder_version != self.settings.encoder_version
                or not profile.encrypted_embedding
                or not profile.key_id
            ):
                continue
            try:
                candidate = self.keyring.decrypt(
                    profile.encrypted_embedding,
                    key_id=profile.key_id,
                    owner_user_id=owner_user_id,
                    profile_id=profile.id,
                    encoder_name=self.settings.encoder_name,
                    encoder_version=self.settings.encoder_version,
                )
            except VoiceprintWorkerError as exc:
                if exc.code == "VOICEPRINT_KEY_UNAVAILABLE":
                    raise VoiceprintWorkerError(
                        "VOICEPRINT_TEMPLATE_KEY_UNAVAILABLE",
                        "an active voiceprint template key is unavailable",
                        retryable=True,
                    ) from exc
                raise VoiceprintWorkerError(
                    "VOICEPRINT_TEMPLATE_SET_INCOMPLETE",
                    "an active voiceprint template failed authentication",
                ) from exc
            score = max(0.0, min(1.0, cosine_similarity(probe, candidate)))
            scored.append((profile.id, score))
        return sorted(scored, key=lambda item: (-item[1], item[0]))

    async def _fail_job(
        self,
        job: VoiceprintJob,
        code: str,
        *,
        retryable: bool,
        internal_diagnostic: str | None = None,
    ) -> None:
        try:
            await self.repository.fail_job(
                job.id,
                self.settings.worker_id,
                code,
                retryable=retryable,
                internal_diagnostic=internal_diagnostic or code,
            )
        except Exception:
            # A lost lease or unavailable database must not escape into ASR/capture.
            return


class MeetingVoiceprintRepositoryAdapter:
    """Map the meeting repository's durable models onto the worker boundary."""

    def __init__(self, repository: Any) -> None:
        self._repository = repository

    async def claim_job(
        self,
        worker_id: str,
        kinds: set[str],
        lease_seconds: int,
    ) -> VoiceprintJob | None:
        try:
            value = await self._repository.claim_job(worker_id, kinds, lease_seconds)
        except Exception as exc:
            raise _repository_error(exc, "claim") from exc
        return None if value is None else _job_snapshot(value)

    async def voiceprint_enrollment_context(
        self,
        job_id: str,
        worker_id: str,
    ) -> EnrollmentContext:
        try:
            value = await self._repository.voiceprint_enrollment_context(job_id, worker_id)
            context = _enrollment_context(value)
        except Exception as exc:
            await self._release_read_transaction()
            raise _repository_error(exc, "enrollment") from exc
        await self._release_read_transaction()
        return context

    async def voiceprint_match_job_context(
        self,
        job_id: str,
        worker_id: str,
    ) -> MatchJobContext:
        method = getattr(self._repository, "voiceprint_match_job_context", None)
        if method is None:
            raise VoiceprintWorkerError(
                "VOICEPRINT_MATCH_REPOSITORY_UNAVAILABLE",
                "durable voiceprint matching repository support is unavailable",
                retryable=True,
            )
        try:
            value = await method(job_id, worker_id)
            context = _match_job_context(value)
        except Exception as exc:
            await self._release_read_transaction()
            raise _repository_error(exc, "match_job") from exc
        await self._release_read_transaction()
        return context

    async def complete_voiceprint_enrollment(self, completion: EnrollmentCompletion) -> Any:
        try:
            return await self._repository.complete_voiceprint_enrollment(
                completion.job_id,
                completion.worker_id,
                encoder_name=completion.encoder_name,
                encoder_version=completion.encoder_version,
                encrypted_embedding=completion.encrypted_embedding,
                key_id=completion.key_id,
                sample_count=completion.sample_count,
                effective_duration_ms=completion.effective_duration_ms,
                quality_summary=completion.quality_summary,
            )
        except Exception as exc:
            raise _repository_error(exc, "enrollment") from exc

    async def fail_job(
        self,
        job_id: str,
        worker_id: str,
        public_error_code: str,
        *,
        retryable: bool,
        internal_diagnostic: str,
    ) -> None:
        try:
            await self._repository.fail_job(
                job_id,
                worker_id,
                public_error_code=public_error_code,
                retryable=retryable,
                internal_diagnostic=internal_diagnostic,
            )
        except Exception as exc:
            raise _repository_error(exc, "fail") from exc

    async def voiceprint_match_context(
        self,
        meeting_id: str,
        track_id: str,
        owner_user_id: int,
    ) -> MatchContext:
        method = getattr(self._repository, "voiceprint_match_context", None)
        if method is None:
            raise VoiceprintWorkerError(
                "VOICEPRINT_MATCH_REPOSITORY_UNAVAILABLE",
                "voiceprint matching repository support is unavailable",
                retryable=True,
            )
        try:
            value = await method(meeting_id, track_id, owner_user_id)
            context = _match_context(value)
        except Exception as exc:
            await self._release_read_transaction()
            raise _repository_error(exc, "match") from exc
        await self._release_read_transaction()
        return context

    async def active_voiceprint_profiles(
        self,
        owner_user_id: int,
        encoder_name: str,
        encoder_version: str,
    ) -> Sequence[ActiveVoiceTemplate]:
        try:
            values = await self._repository.active_voiceprint_profiles(
                owner_user_id,
                encoder_name,
                encoder_version,
            )
        except Exception as exc:
            await self._release_read_transaction()
            raise _repository_error(exc, "match") from exc
        templates: list[ActiveVoiceTemplate] = []
        try:
            for item in values:
                if not isinstance(item, dict):
                    raise VoiceprintWorkerError(
                        "VOICEPRINT_REPOSITORY_RESPONSE_INVALID",
                        "voiceprint profile repository response is invalid",
                    )
                candidate_set_fingerprint = item.get("candidate_set_fingerprint")
                if candidate_set_fingerprint is not None and (
                    not isinstance(candidate_set_fingerprint, str)
                    or not candidate_set_fingerprint.strip()
                    or len(candidate_set_fingerprint) > 128
                ):
                    raise VoiceprintWorkerError(
                        "VOICEPRINT_REPOSITORY_RESPONSE_INVALID",
                        "voiceprint candidate-set fingerprint is invalid",
                    )
                templates.append(
                    ActiveVoiceTemplate(
                        profile=_profile_snapshot(item["profile"], consent_active=True),
                        consent=_consent_snapshot(item["consent"]),
                        candidate_set_fingerprint=candidate_set_fingerprint,
                    )
                )
        except Exception:
            await self._release_read_transaction()
            raise
        await self._release_read_transaction()
        fingerprints = {
            item.candidate_set_fingerprint for item in templates if item.candidate_set_fingerprint is not None
        }
        if len(fingerprints) > 1:
            raise VoiceprintWorkerError(
                "VOICEPRINT_REPOSITORY_RESPONSE_INVALID",
                "voiceprint candidate-set fingerprints disagree",
            )
        return templates

    async def record_voiceprint_match(self, record: MatchRecord) -> Any:
        method = self._repository.record_voiceprint_match
        parameters = inspect.signature(method).parameters
        kwargs = {
            "speaker_track_id": record.speaker_track_id,
            "voice_profile_id": record.voice_profile_id,
            "encoder_version": record.encoder_version,
            "threshold_version": record.threshold_version,
            "top1_score": record.top1_score,
            "top1_top2_margin": record.top1_top2_margin,
            "effective_duration_ms": record.effective_duration_ms,
            "quality_grade": record.quality_grade,
        }
        if "encoder_name" in parameters:
            kwargs["encoder_name"] = record.encoder_name
        if "decision" in parameters:
            kwargs["decision"] = record.decision
        if "validated_auto_match_gate" in parameters:
            kwargs["validated_auto_match_gate"] = record.validated_auto_match_gate
        elif record.decision != "suggested":
            raise VoiceprintWorkerError(
                "VOICEPRINT_AUTO_MATCH_UNSUPPORTED",
                "automatic voiceprint application is not supported by the repository",
            )
        if record.job_id is not None and not {"job_id", "worker_id"}.issubset(parameters):
            raise VoiceprintWorkerError(
                "VOICEPRINT_MATCH_JOB_ATOMICITY_UNSUPPORTED",
                "voiceprint repository cannot atomically complete match jobs",
                retryable=True,
            )
        if "job_id" in parameters:
            kwargs["job_id"] = record.job_id
        if "worker_id" in parameters:
            kwargs["worker_id"] = record.worker_id
        guard_values = {
            "expected_profile_updated_at": record.expected_profile_updated_at,
            "expected_key_id": record.expected_key_id,
            "expected_encrypted_embedding_sha256": record.expected_encrypted_embedding_sha256,
            "expected_candidate_set_fingerprint": record.expected_candidate_set_fingerprint,
        }
        if record.decision == "auto_applied" and (
            not set(guard_values).issubset(parameters) or any(value is None for value in guard_values.values())
        ):
            raise VoiceprintWorkerError(
                "VOICEPRINT_AUTO_MATCH_GUARD_UNAVAILABLE",
                "automatic voiceprint matching concurrency guard is unavailable",
            )
        for name, value in guard_values.items():
            if name in parameters:
                kwargs[name] = value
        try:
            return await method(
                record.meeting_id,
                record.owner_user_id,
                **kwargs,
            )
        except Exception as exc:
            raise _repository_error(exc, "match") from exc

    async def complete_voiceprint_match_job(
        self,
        job_id: str,
        worker_id: str,
        *,
        reason_code: str,
        effective_duration_ms: int,
        quality_grade: str,
    ) -> Any:
        method = getattr(self._repository, "complete_voiceprint_match_job", None)
        if method is None:
            raise VoiceprintWorkerError(
                "VOICEPRINT_MATCH_REPOSITORY_UNAVAILABLE",
                "durable voiceprint matching repository support is unavailable",
                retryable=True,
            )
        try:
            return await method(
                job_id,
                worker_id,
                reason_code=reason_code,
                effective_duration_ms=effective_duration_ms,
                quality_grade=quality_grade,
            )
        except Exception as exc:
            raise _repository_error(exc, "match_job") from exc

    async def delete_voiceprint_profile(
        self,
        profile_id: str,
        owner_user_id: int,
    ) -> DeleteResult:
        from services.meeting_contracts import VoiceProfileStatus

        try:
            profile, _ = await self._repository.set_voice_profile_status(
                profile_id,
                owner_user_id,
                VoiceProfileStatus.DELETED,
            )
        except Exception as exc:
            raise _repository_error(exc, "delete") from exc
        return DeleteResult(
            profile_id=profile.id,
            owner_user_id=profile.owner_user_id,
            ciphertext_cleared=profile.encrypted_embedding is None,
            key_id_cleared=profile.key_id is None,
            # This worker deliberately creates no temporary sample files.
            temporary_samples_deleted=True,
        )

    async def _release_read_transaction(self) -> None:
        session = getattr(self._repository, "session", None)
        if session is not None and session.in_transaction():
            try:
                await session.commit()
            except Exception:
                await session.rollback()


class MeetingAudioStoreReader:
    """Bounded async facade over the contained meeting audio store."""

    def __init__(self, store: Any) -> None:
        self._store = store

    async def read_pcm_range(
        self,
        *,
        owner_user_id: int,
        meeting_id: str,
        chunks: Sequence[AudioChunkRef],
        start_ms: int,
        end_ms: int,
        max_bytes: int,
    ) -> bytes:
        if any(
            PurePosixPath(chunk.storage_key).parts
            != (
                str(owner_user_id),
                meeting_id,
                "chunks",
                str(chunk.stream_epoch),
                f"{chunk.sequence}.pcm",
            )
            for chunk in chunks
        ):
            raise VoiceprintWorkerError(
                "VOICEPRINT_AUDIO_NOT_AUTHORIZED",
                "voiceprint audio manifest is outside the authorized meeting",
            )
        try:
            return await asyncio.to_thread(
                _read_verified_pcm_range,
                self._store,
                owner_user_id,
                meeting_id,
                list(chunks),
                start_ms,
                end_ms,
                max_bytes,
            )
        except Exception as exc:
            code = getattr(exc, "code", None)
            if not isinstance(code, str) or not code.startswith("AUDIO_"):
                raise
            raise VoiceprintWorkerError(
                "VOICEPRINT_AUDIO_UNAVAILABLE",
                "voiceprint source audio is unavailable",
                retryable=code in {"AUDIO_STORAGE_READ_FAILED", "AUDIO_CHUNK_TRUNCATED"},
            ) from exc


def _read_verified_pcm_range(
    store: Any,
    owner_user_id: int,
    meeting_id: str,
    chunks: list[AudioChunkRef],
    start_ms: int,
    end_ms: int,
    max_bytes: int,
) -> bytes:
    for chunk in chunks:
        chunk_end = chunk.start_ms + chunk.duration_ms
        if chunk_end <= start_ms or chunk.start_ms >= end_ms:
            continue
        try:
            path = store.resolve_storage_key(chunk.storage_key)
            if path.stat().st_size != chunk.byte_size:
                raise VoiceprintWorkerError(
                    "VOICEPRINT_AUDIO_INTEGRITY_FAILED",
                    "voiceprint audio size does not match its manifest",
                )
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
        except OSError as exc:
            raise VoiceprintWorkerError(
                "VOICEPRINT_AUDIO_UNAVAILABLE",
                "voiceprint source audio is unavailable",
                retryable=True,
            ) from exc
        if not secrets.compare_digest(digest.hexdigest(), chunk.sha256):
            raise VoiceprintWorkerError(
                "VOICEPRINT_AUDIO_INTEGRITY_FAILED",
                "voiceprint audio does not match its manifest",
            )
    return store.read_pcm_range(
        owner_user_id,
        meeting_id,
        chunks,
        start_ms,
        end_ms,
        max_bytes,
    )


def _job_snapshot(value: Any) -> VoiceprintJob:
    return VoiceprintJob(
        id=str(value.id),
        meeting_id=str(value.meeting_id),
        job_kind=_enum_string(value.job_kind),
        state=_enum_string(value.state),
        lease_owner=value.lease_owner,
    )


def _meeting_snapshot(value: Any) -> MeetingSnapshot:
    return MeetingSnapshot(
        id=str(value.id),
        owner_user_id=int(value.owner_user_id),
        voiceprint_enabled=value.voiceprint_enabled is True,
        state=_enum_string(value.state),
    )


def _profile_snapshot(value: Any, *, consent_active: bool = False) -> VoiceProfileSnapshot:
    return VoiceProfileSnapshot(
        id=str(value.id),
        owner_user_id=int(value.owner_user_id),
        status=_enum_string(value.status),
        encoder_name=value.encoder_name,
        encoder_version=value.encoder_version,
        encrypted_embedding=value.encrypted_embedding,
        key_id=value.key_id,
        consent_active=consent_active,
        updated_at=getattr(value, "updated_at", None),
    )


def _consent_snapshot(value: Any) -> ConsentSnapshot:
    return ConsentSnapshot(
        id=str(value.id),
        voice_profile_id=str(value.voice_profile_id),
        actor_user_id=int(value.actor_user_id),
        purpose=str(value.purpose),
        scope=str(value.scope),
        policy_version=str(value.policy_version),
        source_meeting_id=str(value.source_meeting_id),
        granted_at=value.granted_at,
        revoked_at=value.revoked_at,
    )


def _track_snapshot(value: Any) -> SpeakerTrackSnapshot:
    return SpeakerTrackSnapshot(
        id=str(value.id),
        meeting_id=str(value.meeting_id),
        label_source=_enum_string(value.label_source),
        voice_profile_id=value.voice_profile_id,
        display_name=value.display_name,
    )


def _chunk_snapshot(value: Any) -> AudioChunkRef:
    return AudioChunkRef(
        id=str(value.id),
        meeting_id=str(value.meeting_id),
        stream_epoch=int(value.stream_epoch),
        sequence=int(value.sequence),
        start_ms=int(value.start_ms),
        duration_ms=int(value.duration_ms),
        storage_key=str(value.storage_key),
        sha256=str(value.sha256),
        byte_size=int(value.byte_size),
        codec=str(value.codec),
        sample_rate=int(value.sample_rate),
        channels=int(value.channels),
        state=_enum_string(value.state),
    )


def _segment_snapshot(value: Any, *, meeting_id: str, track_id: str) -> TrackSegment:
    if not isinstance(value, dict):
        raise VoiceprintWorkerError(
            "VOICEPRINT_REPOSITORY_RESPONSE_INVALID",
            "voiceprint segment repository response is invalid",
        )
    return TrackSegment(
        id=str(value["id"]),
        meeting_id=meeting_id,
        speaker_track_id=track_id,
        start_ms=int(value["start_ms"]),
        end_ms=int(value["end_ms"]),
        overlap=value["overlap"] is True,
        noise_level=float(value["noise_level"]) if value.get("noise_level") is not None else None,
        asr_confidence=(float(value["asr_confidence"]) if value.get("asr_confidence") is not None else None),
    )


def _enrollment_context(value: Any) -> EnrollmentContext:
    if not isinstance(value, dict):
        raise VoiceprintWorkerError(
            "VOICEPRINT_REPOSITORY_RESPONSE_INVALID",
            "voiceprint enrollment repository response is invalid",
        )
    try:
        job = _job_snapshot(value["job"])
        meeting = _meeting_snapshot(value["meeting"])
        profile = _profile_snapshot(value["profile"])
        consent = _consent_snapshot(value["consent"])
        track = _track_snapshot(value["track"])
        chunks = tuple(_chunk_snapshot(item) for item in value["chunks"])
        segments = tuple(
            _segment_snapshot(item, meeting_id=meeting.id, track_id=track.id) for item in value["segments"]
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise VoiceprintWorkerError(
            "VOICEPRINT_REPOSITORY_RESPONSE_INVALID",
            "voiceprint enrollment repository response is invalid",
        ) from exc
    if int(value.get("owner_user_id", meeting.owner_user_id)) != meeting.owner_user_id:
        raise VoiceprintWorkerError(
            "VOICEPRINT_REPOSITORY_RESPONSE_INVALID",
            "voiceprint enrollment owner response is invalid",
        )
    return EnrollmentContext(
        job=job,
        meeting=meeting,
        profile=profile,
        consent=consent,
        track=track,
        chunks=chunks,
        segments=segments,
    )


def _match_context(value: Any) -> MatchContext:
    if not isinstance(value, dict):
        raise VoiceprintWorkerError(
            "VOICEPRINT_REPOSITORY_RESPONSE_INVALID",
            "voiceprint match repository response is invalid",
        )
    try:
        meeting = _meeting_snapshot(value["meeting"])
        track = _track_snapshot(value["track"])
        chunks = tuple(_chunk_snapshot(item) for item in value["chunks"])
        segments = tuple(
            _segment_snapshot(item, meeting_id=meeting.id, track_id=track.id) for item in value["segments"]
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise VoiceprintWorkerError(
            "VOICEPRINT_REPOSITORY_RESPONSE_INVALID",
            "voiceprint match repository response is invalid",
        ) from exc
    if int(value.get("owner_user_id", meeting.owner_user_id)) != meeting.owner_user_id:
        raise VoiceprintWorkerError(
            "VOICEPRINT_REPOSITORY_RESPONSE_INVALID",
            "voiceprint match owner response is invalid",
        )
    return MatchContext(meeting=meeting, track=track, chunks=chunks, segments=segments)


def _match_job_context(value: Any) -> MatchJobContext:
    if not isinstance(value, dict):
        raise VoiceprintWorkerError(
            "VOICEPRINT_REPOSITORY_RESPONSE_INVALID",
            "voiceprint match job repository response is invalid",
        )
    try:
        context_value = value.get("context", value)
        return MatchJobContext(
            job=_job_snapshot(value["job"]),
            context=_match_context(context_value),
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise VoiceprintWorkerError(
            "VOICEPRINT_REPOSITORY_RESPONSE_INVALID",
            "voiceprint match job repository response is invalid",
        ) from exc


def _repository_error(exc: Exception, operation: str) -> VoiceprintWorkerError:
    if isinstance(exc, VoiceprintWorkerError):
        return exc
    code = getattr(exc, "code", None)
    if code == "MEETING_VERSION_CONFLICT":
        return VoiceprintWorkerError(
            (
                "VOICEPRINT_JOB_LEASE_INVALID"
                if operation in {"enrollment", "match_job", "fail"}
                else "VOICEPRINT_CONCURRENT_CHANGE"
            ),
            "voiceprint state changed concurrently",
            retryable=True,
        )
    if operation == "enrollment" and code in {
        "MEETING_INVALID_OPERATION",
        "MEETING_RESOURCE_NOT_FOUND",
    }:
        return VoiceprintWorkerError(
            "VOICEPRINT_CONSENT_INVALID",
            "voiceprint enrollment authorization is no longer valid",
        )
    if operation in {"match", "match_job", "delete"} and code in {
        "MEETING_INVALID_OPERATION",
        "MEETING_RESOURCE_NOT_FOUND",
    }:
        return VoiceprintWorkerError(
            (
                "VOICEPRINT_MATCH_NOT_AUTHORIZED"
                if operation in {"match", "match_job"}
                else "VOICEPRINT_DELETE_NOT_AUTHORIZED"
            ),
            "voiceprint operation is not authorized",
        )
    return VoiceprintWorkerError(
        "VOICEPRINT_REPOSITORY_UNAVAILABLE",
        "voiceprint repository is unavailable",
        retryable=True,
    )


def _enum_string(value: Any) -> str:
    return str(getattr(value, "value", value))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def select_non_overlapping_segments(
    segments: Sequence[TrackSegment],
    *,
    meeting_id: str,
    policy: VoiceprintQualityPolicy,
) -> tuple[TrackSegment, ...]:
    eligible = [
        segment
        for segment in segments
        if segment.meeting_id == meeting_id
        and not segment.overlap
        and policy.min_segment_duration_ms <= segment.duration_ms <= policy.max_segment_duration_ms
        and segment.noise_level is not None
        and segment.noise_level <= policy.max_noise_level
        and segment.asr_confidence is not None
        and segment.asr_confidence >= policy.min_asr_confidence
    ]
    eligible.sort(
        key=lambda item: (
            item.noise_level if item.noise_level is not None else policy.max_noise_level,
            -(item.asr_confidence if item.asr_confidence is not None else policy.min_asr_confidence),
            -item.duration_ms,
            item.start_ms,
            item.id,
        )
    )
    selected: list[TrackSegment] = []
    for candidate in eligible:
        if any(candidate.start_ms < existing.end_ms and existing.start_ms < candidate.end_ms for existing in selected):
            continue
        selected.append(candidate)
    return tuple(selected)


def inspect_pcm_sample(segment: TrackSegment, pcm: bytes) -> SelectedSample:
    if not pcm or len(pcm) % 2:
        raise VoiceprintWorkerError("VOICEPRINT_AUDIO_INVALID", "voiceprint PCM is empty or misaligned")
    values = array("h")
    values.frombytes(pcm)
    if sys.byteorder != "little":
        values.byteswap()
    if not values:
        raise VoiceprintWorkerError("VOICEPRINT_AUDIO_INVALID", "voiceprint PCM has no samples")
    square_sum = math.fsum(float(value) * float(value) for value in values)
    rms = math.sqrt(square_sum / len(values)) / 32768.0
    clipped = sum(1 for value in values if abs(value) >= 32700)
    duration_ms = len(values) * 1_000 // 16_000
    if abs(duration_ms - segment.duration_ms) > 100:
        raise VoiceprintWorkerError("VOICEPRINT_AUDIO_RANGE_INVALID", "voiceprint PCM does not match segment timing")
    return SelectedSample(
        segment=segment,
        pcm=pcm,
        duration_ms=duration_ms,
        rms=rms,
        clipping_ratio=clipped / len(values),
    )


def aggregate_embeddings(values: Sequence[Sequence[float]]) -> tuple[float, ...]:
    if len(values) < 2:
        raise VoiceprintWorkerError("VOICEPRINT_EMBEDDINGS_INSUFFICIENT", "multiple voiceprint embeddings are required")
    normalized = [l2_normalize(item) for item in values]
    dimension = len(normalized[0])
    if any(len(item) != dimension for item in normalized):
        raise VoiceprintWorkerError("VOICEPRINT_EMBEDDING_DIMENSION_MISMATCH", "embedding dimensions differ")
    mean = tuple(math.fsum(item[index] for item in normalized) / len(normalized) for index in range(dimension))
    try:
        return l2_normalize(mean)
    except ValueError as exc:
        raise VoiceprintWorkerError(
            "VOICEPRINT_EMBEDDING_AGGREGATION_FAILED", "embedding aggregate is invalid"
        ) from exc


def l2_normalize(values: Sequence[float]) -> tuple[float, ...]:
    vector = tuple(float(value) for value in values)
    if not vector or any(not math.isfinite(value) for value in vector):
        raise ValueError("embedding contains non-finite values")
    norm = math.sqrt(math.fsum(value * value for value in vector))
    if not math.isfinite(norm) or norm <= 1e-12:
        raise ValueError("embedding norm is zero")
    return tuple(value / norm for value in vector)


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    normalized_left = l2_normalize(left)
    normalized_right = l2_normalize(right)
    if len(normalized_left) != len(normalized_right):
        raise ValueError("embedding dimensions differ")
    return math.fsum(a * b for a, b in zip(normalized_left, normalized_right, strict=True))


def classify_voiceprint_match(
    scored: Sequence[tuple[str, float]],
    *,
    effective_duration_ms: int,
    quality_grade: str,
    thresholds: VoiceprintThresholdPolicy,
    auto_match_enabled: bool,
) -> MatchOutcome:
    if not scored:
        return _unknown_outcome(
            "VOICEPRINT_NO_ACTIVE_PROFILE",
            effective_duration_ms=effective_duration_ms,
            quality_grade=quality_grade,
        )
    ordered = sorted(scored, key=lambda item: (-item[1], item[0]))
    profile_id, top1 = ordered[0]
    top2 = ordered[1][1] if len(ordered) > 1 else 0.0
    margin = max(0.0, min(1.0, top1 - top2))
    if effective_duration_ms < thresholds.min_effective_duration_ms:
        return _unknown_outcome(
            "VOICEPRINT_DURATION_INSUFFICIENT",
            score=top1,
            margin=margin,
            effective_duration_ms=effective_duration_ms,
            quality_grade=quality_grade,
        )
    if quality_grade not in thresholds.allowed_quality_grades:
        return _unknown_outcome(
            "VOICEPRINT_QUALITY_INSUFFICIENT",
            score=top1,
            margin=margin,
            effective_duration_ms=effective_duration_ms,
            quality_grade=quality_grade,
        )
    if top1 < thresholds.suggestion_min_score or margin < thresholds.suggestion_min_margin:
        return _unknown_outcome(
            "VOICEPRINT_THRESHOLD_NOT_MET",
            score=top1,
            margin=margin,
            effective_duration_ms=effective_duration_ms,
            quality_grade=quality_grade,
        )
    if (
        auto_match_enabled
        and thresholds.auto_match_validated
        and top1 >= thresholds.auto_min_score
        and margin >= thresholds.auto_min_margin
    ):
        return MatchOutcome(
            level=VoiceprintMatchLevel.AUTO_MATCH,
            voice_profile_id=profile_id,
            top1_score=top1,
            top1_top2_margin=margin,
            effective_duration_ms=effective_duration_ms,
            quality_grade=quality_grade,
            reason_code="VOICEPRINT_AUTO_THRESHOLD_MET",
        )
    return MatchOutcome(
        level=VoiceprintMatchLevel.SUGGESTION,
        voice_profile_id=profile_id,
        top1_score=top1,
        top1_top2_margin=margin,
        effective_duration_ms=effective_duration_ms,
        quality_grade=quality_grade,
        reason_code="VOICEPRINT_SUGGESTION_THRESHOLD_MET",
    )


def voiceprint_aad(
    *,
    owner_user_id: int,
    profile_id: str,
    encoder_name: str,
    encoder_version: str,
) -> bytes:
    payload = {
        "schema_version": VOICEPRINT_AAD_SCHEMA,
        "owner_user_id": owner_user_id,
        "profile_id": profile_id,
        "scope": VOICEPRINT_SCOPE,
        "purpose": VOICEPRINT_PURPOSE,
        "encoder_name": encoder_name,
        "encoder_version": encoder_version,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _derive_template_key(master_key: bytes, aad: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"siq.meeting.voiceprint.template-key.v2",
        info=b"siq.meeting.voiceprint/" + aad,
    ).derive(master_key)


def _validate_enrollment_context(context: EnrollmentContext, *, worker_id: str) -> None:
    if (
        context.job.job_kind != VOICEPRINT_ENROLL_JOB_KIND
        or context.job.state not in {"leased", "running"}
        or context.job.lease_owner != worker_id
    ):
        raise VoiceprintWorkerError("VOICEPRINT_JOB_LEASE_INVALID", "voiceprint job lease is invalid", retryable=True)
    owner_id = context.meeting.owner_user_id
    if (
        context.job.meeting_id != context.meeting.id
        or not context.meeting.voiceprint_enabled
        or context.meeting.state == "deleted"
        or context.profile.owner_user_id != owner_id
        or context.consent.actor_user_id != owner_id
        or context.consent.voice_profile_id != context.profile.id
        or context.consent.source_meeting_id != context.meeting.id
        or context.track.meeting_id != context.meeting.id
        or context.consent.revoked_at is not None
        or not context.consent.policy_version.strip()
        or context.consent.granted_at is None
        or context.consent.purpose != VOICEPRINT_PURPOSE
        or context.consent.scope != VOICEPRINT_SCOPE
        or context.profile.status not in {"collecting", "active"}
        or (context.track.voice_profile_id is not None and context.track.voice_profile_id != context.profile.id)
    ):
        raise VoiceprintWorkerError("VOICEPRINT_CONSENT_INVALID", "voiceprint consent or ownership is invalid")
    if any(
        segment.meeting_id != context.meeting.id or segment.speaker_track_id != context.track.id
        for segment in context.segments
    ):
        raise VoiceprintWorkerError("VOICEPRINT_SOURCE_TRACK_INVALID", "voiceprint source track is invalid")
    if any(chunk.meeting_id != context.meeting.id or chunk.state == "deleted" for chunk in context.chunks):
        raise VoiceprintWorkerError("VOICEPRINT_AUDIO_INVALID", "voiceprint audio manifest is invalid")


def _validate_match_context(context: MatchContext, *, owner_user_id: int) -> None:
    if (
        context.meeting.owner_user_id != owner_user_id
        or context.track.meeting_id != context.meeting.id
        or not context.meeting.voiceprint_enabled
    ):
        raise VoiceprintWorkerError("VOICEPRINT_MATCH_NOT_AUTHORIZED", "voiceprint matching is not authorized")
    if any(
        segment.meeting_id != context.meeting.id or segment.speaker_track_id != context.track.id
        for segment in context.segments
    ):
        raise VoiceprintWorkerError("VOICEPRINT_SOURCE_TRACK_INVALID", "voiceprint source track is invalid")
    if any(chunk.meeting_id != context.meeting.id or chunk.state == "deleted" for chunk in context.chunks):
        raise VoiceprintWorkerError("VOICEPRINT_AUDIO_INVALID", "voiceprint audio manifest is invalid")


def _validate_match_job_context(context: MatchJobContext, *, worker_id: str) -> None:
    if (
        context.job.job_kind != VOICEPRINT_MATCH_JOB_KIND
        or context.job.state not in {"leased", "running"}
        or context.job.lease_owner != worker_id
        or context.job.meeting_id != context.context.meeting.id
    ):
        raise VoiceprintWorkerError(
            "VOICEPRINT_JOB_LEASE_INVALID",
            "voiceprint match job lease or source is invalid",
            retryable=True,
        )
    _validate_match_context(
        context.context,
        owner_user_id=context.context.meeting.owner_user_id,
    )


def _enrollment_identity(context: EnrollmentContext) -> tuple[Any, ...]:
    return (
        context.job.id,
        context.job.meeting_id,
        context.job.lease_owner,
        context.meeting.id,
        context.meeting.owner_user_id,
        context.profile.id,
        context.consent.id,
        context.track.id,
    )


def _match_job_identity(context: MatchJobContext) -> tuple[Any, ...]:
    return (
        context.job.id,
        context.job.meeting_id,
        context.job.lease_owner,
        context.context.meeting.id,
        context.context.meeting.owner_user_id,
        context.context.track.id,
    )


def _quality_grade(
    samples: Sequence[SelectedSample],
    policy: VoiceprintQualityPolicy,
) -> str:
    mean_noise = math.fsum(item.segment.noise_level or 0.0 for item in samples) / len(samples)
    min_confidence = min(item.segment.asr_confidence or 0.0 for item in samples)
    mean_rms = math.fsum(item.rms for item in samples) / len(samples)
    max_clipping = max(item.clipping_ratio for item in samples)
    if (
        mean_noise <= policy.max_mean_noise_level
        and min_confidence >= policy.min_aggregate_asr_confidence
        and mean_rms >= policy.min_mean_rms
        and max_clipping <= policy.max_aggregate_clipping_ratio
    ):
        return "good"
    return "insufficient"


def _quality_summary(
    samples: Sequence[SelectedSample],
    policy: VoiceprintQualityPolicy,
) -> dict[str, Any]:
    return {
        "schema_version": "siq.voiceprint.quality.v1",
        "policy_version": policy.version,
        "quality_grade": _quality_grade(samples, policy),
        "sample_count": len(samples),
        "effective_duration_ms": sum(item.duration_ms for item in samples),
        "mean_rms": round(math.fsum(item.rms for item in samples) / len(samples), 6),
        "max_clipping_ratio": round(max(item.clipping_ratio for item in samples), 6),
        "mean_noise_level": round(
            math.fsum(item.segment.noise_level if item.segment.noise_level is not None else 1.0 for item in samples)
            / len(samples),
            6,
        ),
        "min_asr_confidence": round(
            min(item.segment.asr_confidence if item.segment.asr_confidence is not None else 0.0 for item in samples),
            6,
        ),
        "overlap_filtered": True,
        "audio_format": "pcm_s16le/16000/mono",
        "capture_device_class": "unknown",
    }


def _unknown_outcome(
    reason: str,
    *,
    score: float = 0.0,
    margin: float = 0.0,
    effective_duration_ms: int = 0,
    quality_grade: str = "unknown",
) -> MatchOutcome:
    return MatchOutcome(
        level=VoiceprintMatchLevel.UNKNOWN,
        voice_profile_id=None,
        top1_score=score,
        top1_top2_margin=margin,
        effective_duration_ms=effective_duration_ms,
        quality_grade=quality_grade,
        reason_code=reason,
    )


def _encode_vector(values: Sequence[float]) -> bytes:
    vector = l2_normalize(values)
    if len(vector) > 16_384:
        raise ValueError("voiceprint embedding dimension is too large")
    return _PLAINTEXT_HEADER.pack(VOICEPRINT_PLAINTEXT_MAGIC, len(vector)) + struct.pack(f"!{len(vector)}f", *vector)


def _decode_vector(value: bytes) -> tuple[float, ...]:
    if len(value) < _PLAINTEXT_HEADER.size:
        raise ValueError("voiceprint plaintext is truncated")
    magic, dimension = _PLAINTEXT_HEADER.unpack_from(value)
    expected = _PLAINTEXT_HEADER.size + dimension * 4
    if magic != VOICEPRINT_PLAINTEXT_MAGIC or dimension < 2 or len(value) != expected:
        raise ValueError("voiceprint plaintext is invalid")
    unpacked = struct.unpack_from(f"!{dimension}f", value, _PLAINTEXT_HEADER.size)
    return l2_normalize(unpacked)


def _decode_key(value: str) -> bytes:
    try:
        padding = "=" * (-len(value) % 4)
        return base64.b64decode((value + padding).encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError, binascii.Error) as exc:
        raise ValueError("voiceprint key encoding is invalid") from exc


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: Any) -> bytes:
    if not isinstance(value, str) or len(value) > 1_000_000:
        raise ValueError("voiceprint ciphertext field is invalid")
    padding = "=" * (-len(value) % 4)
    try:
        return base64.b64decode((value + padding).encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError, binascii.Error) as exc:
        raise ValueError("voiceprint ciphertext field is invalid") from exc


async def _read_bounded_response(response: httpx.Response, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise VoiceprintWorkerError(
                "VOICEPRINT_ENCODER_RESPONSE_TOO_LARGE",
                "speaker embedding response exceeded its limit",
                retryable=True,
            )
        chunks.append(chunk)
    return b"".join(chunks)


__all__ = [
    "ActiveVoiceTemplate",
    "AudioChunkRef",
    "ConsentSnapshot",
    "ControlledAudioReader",
    "DeleteResult",
    "EmbeddingResult",
    "EnrollmentCompletion",
    "EnrollmentContext",
    "HttpSpeakerEmbeddingClient",
    "MatchContext",
    "MatchJobContext",
    "MatchOutcome",
    "MatchRecord",
    "MeetingAudioStoreReader",
    "MeetingSnapshot",
    "MeetingVoiceprintRepositoryAdapter",
    "MeetingVoiceprintWorker",
    "SpeakerEmbeddingClient",
    "SpeakerTrackSnapshot",
    "TrackSegment",
    "VoiceProfileSnapshot",
    "VoiceprintJob",
    "VoiceprintKeyring",
    "VoiceprintMatchLevel",
    "VoiceprintQualityPolicy",
    "VoiceprintRepository",
    "VoiceprintThresholdPolicy",
    "VoiceprintWorkerError",
    "VoiceprintWorkerSettings",
    "WorkerRunResult",
    "aggregate_embeddings",
    "classify_voiceprint_match",
    "cosine_similarity",
    "inspect_pcm_sample",
    "l2_normalize",
    "select_non_overlapping_segments",
    "voiceprint_aad",
]
