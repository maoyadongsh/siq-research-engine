"""Validated, failure-contained configuration for the meeting domain."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

from services.meeting_voiceprint_tombstone import (
    VoiceprintTombstoneError,
    VoiceprintTombstoneLedger,
)
from services.meeting_voiceprint_worker import VoiceprintThresholdPolicy


def _env_value(name: str, aliases: tuple[str, ...]) -> tuple[str | None, str]:
    for candidate in (name, *aliases):
        raw = os.getenv(candidate)
        if raw is not None:
            return raw, candidate
    return None, name


def _bool(name: str, default: bool, errors: list[str], *aliases: str) -> bool:
    raw, source_name = _env_value(name, aliases)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    errors.append(f"{source_name} must be a boolean")
    return default


def _int(
    name: str,
    default: int,
    minimum: int,
    maximum: int,
    errors: list[str],
    *aliases: str,
) -> int:
    raw, source_name = _env_value(name, aliases)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        errors.append(f"{source_name} must be an integer")
        return default
    if value < minimum or value > maximum:
        errors.append(f"{source_name} must be between {minimum} and {maximum}")
        return default
    return value


@dataclass(frozen=True)
class MeetingSettings:
    enabled: bool = False
    asr_enabled: bool = False
    correction_learning_enabled: bool = False
    voiceprint_enabled: bool = False
    voiceprint_auto_match_enabled: bool = False
    ai_enabled: bool = False
    max_duration_seconds: int = 14_400
    max_chunk_bytes: int = 262_144
    reconnect_window_seconds: int = 60
    stream_ticket_ttl_seconds: int = 45
    stream_lease_ttl_seconds: int = 20
    stream_handshake_timeout_seconds: int = 10
    playback_ticket_ttl_seconds: int = 900
    max_active_per_user: int = 1
    max_active_total: int = 4
    audio_max_frames_per_second: int = 20
    audio_max_bytes_per_second: int = 128_000
    audio_rate_burst_seconds: int = 2
    sample_rate: int = 16_000
    channels: int = 1
    codec: str = "pcm_s16le"
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def operational(self) -> bool:
        return self.enabled and not self.errors

    @classmethod
    def from_env(cls) -> "MeetingSettings":
        errors: list[str] = []
        enabled = _bool("SIQ_MEETINGS_ENABLED", False, errors)
        asr_enabled = _bool(
            "SIQ_MEETING_REALTIME_ASR_ENABLED",
            False,
            errors,
            "SIQ_MEETINGS_ASR_ENABLED",
        )
        voiceprint_enabled = _bool(
            "SIQ_MEETING_VOICEPRINT_ENABLED",
            False,
            errors,
            "SIQ_MEETINGS_VOICEPRINT_ENABLED",
        )
        auto_match_requested = _bool(
            "SIQ_MEETING_VOICEPRINT_AUTO_MATCH_ENABLED",
            False,
            errors,
        )
        auto_match_enabled = False
        if enabled and asr_enabled:
            asr_url = os.getenv("SIQ_MEETING_ASR_WS_URL", "").strip()
            parsed = urlparse(asr_url)
            if (
                "{meeting_id}" not in asr_url
                or parsed.scheme not in {"ws", "wss"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                errors.append("SIQ_MEETING_ASR_WS_URL must be a credential-free ws(s) URL with {meeting_id}")
            profile = os.getenv("SIQ_DEPLOYMENT_PROFILE", "development").strip().lower()
            protected = profile in {"docker", "prod", "production"}
            service_token = (
                os.getenv("SIQ_MEETING_SPEECH_INTERNAL_SERVICE_TOKEN")
                or os.getenv("SIQ_MEETING_ASR_SERVICE_TOKEN")
                or ""
            ).strip()
            if protected and not service_token:
                errors.append("SIQ_MEETING_SPEECH_INTERNAL_SERVICE_TOKEN is required in protected deployments")
        if enabled and voiceprint_enabled:
            try:
                VoiceprintTombstoneLedger.from_env()
            except VoiceprintTombstoneError as exc:
                errors.append(f"voiceprint tombstone configuration is invalid: {exc}")
            if auto_match_requested:
                threshold_json = os.getenv(
                    "SIQ_MEETING_VOICEPRINT_THRESHOLDS_JSON",
                    "",
                ).strip()
                try:
                    threshold_policy = VoiceprintThresholdPolicy.from_json(threshold_json)
                except (KeyError, TypeError, ValueError):
                    threshold_policy = None
                if threshold_policy is None or not threshold_policy.auto_match_validated:
                    errors.append(
                        "voiceprint auto-match requires versioned thresholds with auto_match_validated=true"
                    )
                elif (
                    not threshold_policy.allowed_quality_grades
                    or "good" not in threshold_policy.allowed_quality_grades
                    or not threshold_policy.allowed_quality_grades <= {"good", "insufficient"}
                ):
                    errors.append("voiceprint auto-match allowed quality grades are invalid")
                else:
                    auto_match_enabled = True
        max_active_per_user = _int(
            "SIQ_MEETING_MAX_ACTIVE_PER_USER",
            1,
            1,
            100,
            errors,
            "SIQ_MEETINGS_MAX_ACTIVE_PER_USER",
        )
        max_active_total = _int(
            "SIQ_MEETING_MAX_ACTIVE_TOTAL",
            4,
            1,
            1_000,
            errors,
            "SIQ_MEETINGS_MAX_ACTIVE_TOTAL",
        )
        audio_max_frames_per_second = _int(
            "SIQ_MEETING_AUDIO_MAX_FRAMES_PER_SECOND",
            20,
            1,
            1_000,
            errors,
        )
        audio_max_bytes_per_second = _int(
            "SIQ_MEETING_AUDIO_MAX_BYTES_PER_SECOND",
            128_000,
            32_000,
            16 * 1024 * 1024,
            errors,
        )
        audio_rate_burst_seconds = _int(
            "SIQ_MEETING_AUDIO_RATE_BURST_SECONDS",
            2,
            1,
            10,
            errors,
        )
        if max_active_per_user > max_active_total:
            errors.append("SIQ_MEETING_MAX_ACTIVE_PER_USER must not exceed SIQ_MEETING_MAX_ACTIVE_TOTAL")
        return cls(
            enabled=enabled,
            asr_enabled=asr_enabled,
            correction_learning_enabled=_bool(
                "SIQ_MEETING_CORRECTION_LEARNING_ENABLED",
                False,
                errors,
            ),
            voiceprint_enabled=voiceprint_enabled,
            voiceprint_auto_match_enabled=auto_match_enabled,
            ai_enabled=_bool(
                "SIQ_MEETING_AI_ENABLED",
                False,
                errors,
                "SIQ_MEETINGS_AI_ENABLED",
            ),
            max_duration_seconds=_int(
                "SIQ_MEETING_MAX_DURATION_SECONDS",
                14_400,
                60,
                86_400,
                errors,
                "SIQ_MEETINGS_MAX_DURATION_SECONDS",
            ),
            max_chunk_bytes=_int(
                "SIQ_MEETING_AUDIO_MAX_FRAME_BYTES",
                262_144,
                1_024,
                4_194_304,
                errors,
                "SIQ_MEETINGS_MAX_CHUNK_BYTES",
            ),
            reconnect_window_seconds=_int(
                "SIQ_MEETING_RECONNECT_BUFFER_SECONDS",
                60,
                5,
                3_600,
                errors,
                "SIQ_MEETINGS_RECONNECT_WINDOW_SECONDS",
            ),
            stream_ticket_ttl_seconds=_int(
                "SIQ_MEETING_STREAM_TICKET_TTL_SECONDS",
                45,
                10,
                300,
                errors,
                "SIQ_MEETINGS_STREAM_TICKET_TTL_SECONDS",
            ),
            stream_lease_ttl_seconds=_int(
                "SIQ_MEETING_STREAM_LEASE_TTL_SECONDS",
                20,
                5,
                120,
                errors,
                "SIQ_MEETINGS_STREAM_LEASE_TTL_SECONDS",
            ),
            stream_handshake_timeout_seconds=_int(
                "SIQ_MEETING_STREAM_HANDSHAKE_TIMEOUT_SECONDS",
                10,
                3,
                60,
                errors,
                "SIQ_MEETINGS_STREAM_HANDSHAKE_TIMEOUT_SECONDS",
            ),
            playback_ticket_ttl_seconds=_int(
                "SIQ_MEETING_PLAYBACK_TICKET_TTL_SECONDS",
                900,
                60,
                3_600,
                errors,
                "SIQ_MEETINGS_PLAYBACK_TICKET_TTL_SECONDS",
            ),
            max_active_per_user=max_active_per_user,
            max_active_total=max_active_total,
            audio_max_frames_per_second=audio_max_frames_per_second,
            audio_max_bytes_per_second=audio_max_bytes_per_second,
            audio_rate_burst_seconds=audio_rate_burst_seconds,
            errors=tuple(errors),
        )


def meeting_capabilities(settings: MeetingSettings | None = None) -> dict:
    value = settings or MeetingSettings.from_env()
    # Import is independently gated so enabling meetings never exposes a
    # long-recording upload surface by accident.
    from services.meeting_import_config import MeetingImportSettings
    from services.meeting_native_capture_config import (
        MeetingNativeCaptureSettings,
        native_capture_capability,
    )

    import_settings = MeetingImportSettings.from_env()
    import_available = value.operational and import_settings.operational
    native_capture = native_capture_capability(MeetingNativeCaptureSettings.from_env())
    native_capture["available"] = bool(value.operational and native_capture["available"])
    return {
        "schema_version": "meeting.v1",
        "enabled": value.operational,
        "configuration_errors": list(value.errors),
        "audio": {
            "codec": value.codec,
            "sample_rate": value.sample_rate,
            "channels": value.channels,
            "frame_transport": "binary",
            "capture_adapters": {
                "web_audio_worklet": {"available": value.operational, "background_recording": False},
                "ios_native": native_capture,
            },
        },
        "asr": {
            "available": value.operational and value.asr_enabled,
            "languages": ["zh-CN"],
            "timestamps": True,
            "speaker_tracks": True,
        },
        "correction_learning": {
            "available": value.operational and value.correction_learning_enabled,
            "scope": "user_private",
        },
        "voiceprint": {
            "available": value.operational and value.voiceprint_enabled,
            "scope": "user_private",
            "auto_match": (
                value.operational
                and value.voiceprint_enabled
                and value.voiceprint_auto_match_enabled
            ),
        },
        "ai": {
            "available": value.operational and value.ai_enabled,
            "model_catalog_runtime": True,
        },
        "recording_import": {
            "available": import_available,
            "configuration_errors": list(import_settings.errors),
            "formats": ["wav", "flac", "mp3", "m4a", "webm", "ogg"],
            "resumable": True,
            "max_file_bytes": import_settings.max_file_bytes,
            "max_duration_seconds": import_settings.max_duration_seconds,
            "min_chunk_bytes": import_settings.min_chunk_bytes,
            "max_chunk_bytes": import_settings.max_chunk_bytes,
        },
        "limits": {
            "max_duration_seconds": value.max_duration_seconds,
            "max_chunk_bytes": value.max_chunk_bytes,
            "reconnect_window_seconds": value.reconnect_window_seconds,
            "stream_ticket_ttl_seconds": value.stream_ticket_ttl_seconds,
            "max_active_per_user": value.max_active_per_user,
            "max_active_total": value.max_active_total,
            "audio_max_frames_per_second": value.audio_max_frames_per_second,
            "audio_max_bytes_per_second": value.audio_max_bytes_per_second,
            "audio_rate_burst_seconds": value.audio_rate_burst_seconds,
        },
        "supported_audio_sources": ["microphone", *(["import"] if import_available else [])],
    }
