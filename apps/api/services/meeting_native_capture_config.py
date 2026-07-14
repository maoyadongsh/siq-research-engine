"""Fail-closed configuration for the optional iOS native capture backend."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from services.path_config import BACKEND_DATA_ROOT


def _bool(name: str, default: bool, errors: list[str]) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    errors.append(f"{name} must be a boolean")
    return default


def _int(name: str, default: int, minimum: int, maximum: int, errors: list[str]) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        errors.append(f"{name} must be an integer")
        return default
    if value < minimum or value > maximum:
        errors.append(f"{name} must be between {minimum} and {maximum}")
        return default
    return value


@dataclass(frozen=True, slots=True)
class MeetingNativeCaptureSettings:
    enabled: bool = False
    root: Path = BACKEND_DATA_ROOT / "meeting_native_captures"
    token_ttl_seconds: int = 86_400
    max_batch_bytes: int = 8 * 1024 * 1024
    max_total_bytes: int = 4 * 1024 * 1024 * 1024
    max_retained_bytes_per_owner: int = 16 * 1024 * 1024 * 1024
    max_duration_seconds: int = 14_400
    max_active_per_owner: int = 2
    max_batch_concurrency: int = 8
    batch_queue_timeout_seconds: int = 2
    min_storage_free_bytes: int = 512 * 1024 * 1024
    finalization_lease_seconds: int = 300
    finalization_retry_delay_seconds: int = 20
    finalization_poll_seconds: int = 1
    finalization_max_attempts: int = 5
    errors: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.max_retained_bytes_per_owner >= self.max_total_bytes:
            return
        message = (
            "SIQ_MEETING_NATIVE_CAPTURE_MAX_RETAINED_BYTES_PER_OWNER must be "
            "greater than or equal to SIQ_MEETING_NATIVE_CAPTURE_MAX_TOTAL_BYTES"
        )
        if message not in self.errors:
            object.__setattr__(self, "errors", (*self.errors, message))

    @property
    def operational(self) -> bool:
        return self.enabled and not self.errors

    @classmethod
    def from_env(cls) -> "MeetingNativeCaptureSettings":
        errors: list[str] = []
        root_raw = os.getenv("SIQ_MEETING_NATIVE_CAPTURE_ROOT", "").strip()
        return cls(
            enabled=_bool("SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED", False, errors),
            root=Path(root_raw) if root_raw else BACKEND_DATA_ROOT / "meeting_native_captures",
            token_ttl_seconds=_int(
                "SIQ_MEETING_NATIVE_CAPTURE_TOKEN_TTL_SECONDS",
                86_400,
                300,
                604_800,
                errors,
            ),
            max_batch_bytes=_int(
                "SIQ_MEETING_NATIVE_CAPTURE_MAX_BATCH_BYTES",
                8 * 1024 * 1024,
                3_200,
                64 * 1024 * 1024,
                errors,
            ),
            max_total_bytes=_int(
                "SIQ_MEETING_NATIVE_CAPTURE_MAX_TOTAL_BYTES",
                4 * 1024 * 1024 * 1024,
                3_200,
                64 * 1024 * 1024 * 1024,
                errors,
            ),
            max_retained_bytes_per_owner=_int(
                "SIQ_MEETING_NATIVE_CAPTURE_MAX_RETAINED_BYTES_PER_OWNER",
                16 * 1024 * 1024 * 1024,
                3_200,
                1024 * 1024 * 1024 * 1024,
                errors,
            ),
            max_duration_seconds=_int(
                "SIQ_MEETING_NATIVE_CAPTURE_MAX_DURATION_SECONDS",
                14_400,
                60,
                86_400,
                errors,
            ),
            max_active_per_owner=_int(
                "SIQ_MEETING_NATIVE_CAPTURE_MAX_ACTIVE_PER_OWNER",
                2,
                1,
                20,
                errors,
            ),
            max_batch_concurrency=_int(
                "SIQ_MEETING_NATIVE_CAPTURE_MAX_BATCH_CONCURRENCY",
                8,
                1,
                128,
                errors,
            ),
            batch_queue_timeout_seconds=_int(
                "SIQ_MEETING_NATIVE_CAPTURE_BATCH_QUEUE_TIMEOUT_SECONDS",
                2,
                1,
                60,
                errors,
            ),
            min_storage_free_bytes=_int(
                "SIQ_MEETING_NATIVE_CAPTURE_MIN_STORAGE_FREE_BYTES",
                512 * 1024 * 1024,
                0,
                16 * 1024 * 1024 * 1024 * 1024,
                errors,
            ),
            finalization_lease_seconds=_int(
                "SIQ_MEETING_NATIVE_FINALIZATION_LEASE_SECONDS",
                300,
                30,
                3_600,
                errors,
            ),
            finalization_retry_delay_seconds=_int(
                "SIQ_MEETING_NATIVE_FINALIZATION_RETRY_DELAY_SECONDS",
                20,
                0,
                3_600,
                errors,
            ),
            finalization_poll_seconds=_int(
                "SIQ_MEETING_NATIVE_FINALIZATION_POLL_SECONDS",
                1,
                1,
                60,
                errors,
            ),
            finalization_max_attempts=_int(
                "SIQ_MEETING_NATIVE_FINALIZATION_MAX_ATTEMPTS",
                5,
                1,
                20,
                errors,
            ),
            errors=tuple(errors),
        )


def native_capture_capability(settings: MeetingNativeCaptureSettings | None = None) -> dict[str, object]:
    value = settings or MeetingNativeCaptureSettings.from_env()
    return {
        "available": value.operational,
        "adapter": "ios_native",
        "requires_native_runtime": True,
        "web_background_recording_supported": False,
        "configuration_errors": list(value.errors),
        "audio": {"encoding": "pcm_s16le", "sample_rate": 16_000, "channels": 1},
        "limits": {
            "max_batch_bytes": value.max_batch_bytes,
            "max_total_bytes": value.max_total_bytes,
            "max_retained_bytes_per_owner": value.max_retained_bytes_per_owner,
            "max_duration_seconds": value.max_duration_seconds,
            "token_ttl_seconds": value.token_ttl_seconds,
            "max_batch_concurrency": value.max_batch_concurrency,
            "min_storage_free_bytes": value.min_storage_free_bytes,
            "finalization_max_attempts": value.finalization_max_attempts,
        },
    }
