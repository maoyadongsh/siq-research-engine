"""Bounded configuration for long meeting-recording imports."""

from __future__ import annotations

import os
import shutil
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
class MeetingImportSettings:
    enabled: bool = False
    root: Path = BACKEND_DATA_ROOT / "meeting_imports"
    max_file_bytes: int = 4 * 1024 * 1024 * 1024
    owner_quota_bytes: int = 8 * 1024 * 1024 * 1024
    max_active_per_owner: int = 3
    min_chunk_bytes: int = 256 * 1024
    max_chunk_bytes: int = 16 * 1024 * 1024
    max_duration_seconds: int = 14_400
    upload_ttl_seconds: int = 86_400
    lease_seconds: int = 600
    retry_base_seconds: int = 15
    ffprobe_timeout_seconds: int = 60
    ffmpeg_timeout_seconds: int = 15_000
    ffprobe_bin: str = "ffprobe"
    ffmpeg_bin: str = "ffmpeg"
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def operational(self) -> bool:
        return self.enabled and not self.errors

    @classmethod
    def from_env(cls) -> "MeetingImportSettings":
        errors: list[str] = []
        enabled = _bool("SIQ_MEETING_IMPORT_ENABLED", False, errors)
        root_raw = os.getenv("SIQ_MEETING_IMPORT_ROOT", "").strip()
        root = Path(root_raw) if root_raw else BACKEND_DATA_ROOT / "meeting_imports"
        ffprobe_bin = os.getenv("SIQ_MEETING_IMPORT_FFPROBE_BIN", "ffprobe").strip() or "ffprobe"
        ffmpeg_bin = os.getenv("SIQ_MEETING_IMPORT_FFMPEG_BIN", "ffmpeg").strip() or "ffmpeg"
        if enabled:
            if not _resolve_executable(ffprobe_bin):
                errors.append("SIQ_MEETING_IMPORT_FFPROBE_BIN is not executable")
            if not _resolve_executable(ffmpeg_bin):
                errors.append("SIQ_MEETING_IMPORT_FFMPEG_BIN is not executable")
            try:
                from services.meeting_finalization import MeetingFinalizationSettings

                finalization = MeetingFinalizationSettings.from_env()
                if finalization.endpoint is None:
                    errors.append("SIQ_MEETING_FINAL_ASR_URL or SIQ_MEETING_ASR_WS_URL is required")
            except ValueError as exc:
                errors.append(f"meeting final ASR configuration is invalid: {exc}")
        min_chunk = _int(
            "SIQ_MEETING_IMPORT_MIN_CHUNK_BYTES", 256 * 1024, 64 * 1024, 16 * 1024 * 1024, errors
        )
        max_chunk = _int(
            "SIQ_MEETING_IMPORT_MAX_CHUNK_BYTES", 16 * 1024 * 1024, 256 * 1024, 64 * 1024 * 1024, errors
        )
        if min_chunk > max_chunk:
            errors.append("SIQ_MEETING_IMPORT_MIN_CHUNK_BYTES must not exceed max chunk bytes")
        return cls(
            enabled=enabled,
            root=root,
            max_file_bytes=_int(
                "SIQ_MEETING_IMPORT_MAX_FILE_BYTES",
                4 * 1024 * 1024 * 1024,
                1024,
                64 * 1024 * 1024 * 1024,
                errors,
            ),
            owner_quota_bytes=_int(
                "SIQ_MEETING_IMPORT_OWNER_QUOTA_BYTES",
                8 * 1024 * 1024 * 1024,
                1024,
                256 * 1024 * 1024 * 1024,
                errors,
            ),
            max_active_per_owner=_int("SIQ_MEETING_IMPORT_MAX_ACTIVE_PER_OWNER", 3, 1, 100, errors),
            min_chunk_bytes=min_chunk,
            max_chunk_bytes=max_chunk,
            max_duration_seconds=_int(
                "SIQ_MEETING_IMPORT_MAX_DURATION_SECONDS", 14_400, 60, 86_400, errors
            ),
            upload_ttl_seconds=_int(
                "SIQ_MEETING_IMPORT_UPLOAD_TTL_SECONDS", 86_400, 900, 604_800, errors
            ),
            lease_seconds=_int("SIQ_MEETING_IMPORT_LEASE_SECONDS", 600, 30, 3600, errors),
            retry_base_seconds=_int("SIQ_MEETING_IMPORT_RETRY_BASE_SECONDS", 15, 1, 600, errors),
            ffprobe_timeout_seconds=_int(
                "SIQ_MEETING_IMPORT_FFPROBE_TIMEOUT_SECONDS", 60, 5, 600, errors
            ),
            ffmpeg_timeout_seconds=_int(
                "SIQ_MEETING_IMPORT_FFMPEG_TIMEOUT_SECONDS", 15_000, 60, 86_400, errors
            ),
            ffprobe_bin=ffprobe_bin,
            ffmpeg_bin=ffmpeg_bin,
            errors=tuple(errors),
        )


def _resolve_executable(value: str) -> str | None:
    if "/" in value:
        path = Path(value)
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    return shutil.which(value)
