"""External, authenticated deletion ledger for recoverable voiceprint backups."""

from __future__ import annotations

import base64
import binascii
import fcntl
import hashlib
import hmac
import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Final
from uuid import UUID

from services.path_config import BACKEND_DATA_ROOT, RUNTIME_ROOT

TOMBSTONE_SCHEMA: Final = "siq.meeting.voiceprint_tombstone.v1"
EMPTY_TOMBSTONE_HEAD_HMAC: Final = "0" * 64
_ENTRY_FIELDS: Final = {
    "schema_version",
    "sequence",
    "owner_user_id",
    "profile_id",
    "deleted_at",
    "reason",
    "previous_hmac",
    "hmac",
}
_MAX_LEDGER_BYTES: Final = 64 * 1024 * 1024
_MAX_ENTRIES: Final = 200_000
_MAX_LINE_BYTES: Final = 2_048


class VoiceprintTombstoneError(RuntimeError):
    """Base error that callers must treat as fail-closed."""


class VoiceprintTombstoneConfigurationError(VoiceprintTombstoneError):
    pass


class VoiceprintTombstoneIntegrityError(VoiceprintTombstoneError):
    pass


@dataclass(frozen=True, slots=True)
class VoiceprintTombstone:
    sequence: int
    owner_user_id: int
    profile_id: str
    deleted_at: datetime
    reason: str
    previous_hmac: str
    entry_hmac: str


@dataclass(frozen=True, slots=True)
class VoiceprintTombstoneCheckpoint:
    entry_count: int
    head_hmac: str


class VoiceprintTombstoneLedger:
    """Append-only HMAC chain stored outside the application database backup."""

    def __init__(
        self,
        *,
        path: Path,
        hmac_key: bytes,
        backend_data_root: Path | None = None,
    ) -> None:
        resolved_path = path.expanduser().resolve(strict=False)
        backend_root = (backend_data_root or BACKEND_DATA_ROOT).expanduser().resolve(strict=False)
        if _is_within(resolved_path, backend_root):
            raise VoiceprintTombstoneConfigurationError(
                "voiceprint tombstone ledger must be outside the database backup root"
            )
        if len(hmac_key) != 32:
            raise VoiceprintTombstoneConfigurationError(
                "voiceprint tombstone HMAC key must be exactly 32 bytes"
            )
        self.path = resolved_path
        self._key = bytes(hmac_key)

    @classmethod
    def from_env(cls) -> "VoiceprintTombstoneLedger":
        raw_path = os.getenv("SIQ_MEETING_VOICEPRINT_TOMBSTONE_PATH", "").strip()
        path = (
            Path(raw_path)
            if raw_path
            else RUNTIME_ROOT / "security" / "meeting-voiceprint-tombstones.jsonl"
        )
        encoded_key = os.getenv(
            "SIQ_MEETING_VOICEPRINT_TOMBSTONE_HMAC_KEY",
            "",
        ).strip()
        if not encoded_key:
            raise VoiceprintTombstoneConfigurationError(
                "voiceprint tombstone HMAC key is required"
            )
        backend_root = Path(
            os.getenv("SIQ_BACKEND_DATA_ROOT", str(BACKEND_DATA_ROOT))
        )
        return cls(
            path=path,
            hmac_key=_decode_key(encoded_key),
            backend_data_root=backend_root,
        )

    def load(self) -> tuple[VoiceprintTombstone, ...]:
        if not self.path.exists():
            return ()
        self._validate_parent()
        descriptor = self._open(os.O_RDONLY)
        try:
            with os.fdopen(descriptor, "rb", closefd=True) as ledger:
                fcntl.flock(ledger.fileno(), fcntl.LOCK_SH)
                return self._read_locked(ledger)
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise

    def load_with_checkpoint(
        self,
    ) -> tuple[tuple[VoiceprintTombstone, ...], VoiceprintTombstoneCheckpoint]:
        """Authenticate the complete chain and return its external checkpoint."""
        entries = self.load()
        return entries, VoiceprintTombstoneCheckpoint(
            entry_count=len(entries),
            head_hmac=(
                entries[-1].entry_hmac
                if entries
                else EMPTY_TOMBSTONE_HEAD_HMAC
            ),
        )

    def initialize(self) -> int:
        """Create or authenticate the ledger before biometric work is accepted."""
        self._ensure_parent()
        descriptor = self._open(os.O_RDWR | os.O_APPEND | os.O_CREAT)
        try:
            with os.fdopen(descriptor, "r+b", closefd=True) as ledger:
                fcntl.flock(ledger.fileno(), fcntl.LOCK_EX)
                entries = self._read_locked(ledger)
                ledger.flush()
                os.fsync(ledger.fileno())
                return len(entries)
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise

    def append(
        self,
        *,
        owner_user_id: int,
        profile_id: str,
        deleted_at: datetime,
        reason: str,
    ) -> VoiceprintTombstone:
        owner, profile, timestamp, normalized_reason = _validated_values(
            owner_user_id,
            profile_id,
            deleted_at,
            reason,
        )
        self._ensure_parent()
        descriptor = self._open(os.O_RDWR | os.O_APPEND | os.O_CREAT)
        try:
            with os.fdopen(descriptor, "r+b", closefd=True) as ledger:
                fcntl.flock(ledger.fileno(), fcntl.LOCK_EX)
                entries = self._read_locked(ledger)
                matching = [item for item in entries if item.profile_id == profile]
                if matching:
                    latest = matching[-1]
                    if latest.owner_user_id != owner:
                        raise VoiceprintTombstoneIntegrityError(
                            "voiceprint tombstone profile ownership changed"
                        )
                    if latest.reason == "deleted" or latest.reason == normalized_reason:
                        return latest
                previous_hmac = (
                    entries[-1].entry_hmac
                    if entries
                    else EMPTY_TOMBSTONE_HEAD_HMAC
                )
                unsigned = {
                    "schema_version": TOMBSTONE_SCHEMA,
                    "sequence": len(entries) + 1,
                    "owner_user_id": owner,
                    "profile_id": profile,
                    "deleted_at": timestamp,
                    "reason": normalized_reason,
                    "previous_hmac": previous_hmac,
                }
                entry_hmac = self._sign(unsigned)
                payload = {**unsigned, "hmac": entry_hmac}
                encoded = _canonical_json(payload) + b"\n"
                if len(encoded) > _MAX_LINE_BYTES:
                    raise VoiceprintTombstoneIntegrityError(
                        "voiceprint tombstone entry exceeds its bound"
                    )
                if ledger.seek(0, os.SEEK_END) + len(encoded) > _MAX_LEDGER_BYTES:
                    raise VoiceprintTombstoneIntegrityError(
                        "voiceprint tombstone ledger is full"
                    )
                ledger.write(encoded)
                ledger.flush()
                os.fsync(ledger.fileno())
                return _entry_from_payload(payload)
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise

    def latest(self) -> dict[str, VoiceprintTombstone]:
        values: dict[str, VoiceprintTombstone] = {}
        for entry in self.load():
            values[entry.profile_id] = entry
        return values

    def is_tombstoned(self, *, owner_user_id: int, profile_id: str) -> bool:
        owner, profile, _, _ = _validated_values(
            owner_user_id,
            profile_id,
            datetime.now(timezone.utc),
            "revoked",
        )
        entry = self.latest().get(profile)
        if entry is None:
            return False
        if entry.owner_user_id != owner:
            raise VoiceprintTombstoneIntegrityError(
                "voiceprint tombstone profile ownership changed"
            )
        return True

    def _ensure_parent(self) -> None:
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if self.path.parent.is_symlink():
            raise VoiceprintTombstoneConfigurationError(
                "voiceprint tombstone directory cannot be a symlink"
            )
        mode = stat.S_IMODE(self.path.parent.stat().st_mode)
        if mode != 0o700:
            raise VoiceprintTombstoneConfigurationError(
                "voiceprint tombstone directory must have mode 0700"
            )

    def _validate_parent(self) -> None:
        if self.path.parent.is_symlink():
            raise VoiceprintTombstoneIntegrityError(
                "voiceprint tombstone directory cannot be a symlink"
            )
        mode = stat.S_IMODE(self.path.parent.stat().st_mode)
        if mode != 0o700:
            raise VoiceprintTombstoneIntegrityError(
                "voiceprint tombstone directory permissions are unsafe"
            )

    def _open(self, flags: int) -> int:
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        cloexec = getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(self.path, flags | nofollow | cloexec, 0o600)
        except OSError as exc:
            raise VoiceprintTombstoneIntegrityError(
                "voiceprint tombstone ledger cannot be opened safely"
            ) from exc
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or stat.S_IMODE(details.st_mode) != 0o600:
            os.close(descriptor)
            raise VoiceprintTombstoneIntegrityError(
                "voiceprint tombstone ledger must be a regular 0600 file"
            )
        return descriptor

    def _read_locked(self, ledger: BinaryIO) -> tuple[VoiceprintTombstone, ...]:
        details = os.fstat(ledger.fileno())
        if details.st_size > _MAX_LEDGER_BYTES:
            raise VoiceprintTombstoneIntegrityError(
                "voiceprint tombstone ledger exceeds its bound"
            )
        ledger.seek(0)
        entries: list[VoiceprintTombstone] = []
        previous_hmac = EMPTY_TOMBSTONE_HEAD_HMAC
        for line_number, line in enumerate(ledger, start=1):
            if line_number > _MAX_ENTRIES or len(line) > _MAX_LINE_BYTES:
                raise VoiceprintTombstoneIntegrityError(
                    "voiceprint tombstone ledger entry limit was exceeded"
                )
            try:
                payload = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise VoiceprintTombstoneIntegrityError(
                    "voiceprint tombstone ledger contains invalid JSON"
                ) from exc
            if not isinstance(payload, dict) or set(payload) != _ENTRY_FIELDS:
                raise VoiceprintTombstoneIntegrityError(
                    "voiceprint tombstone ledger contains unexpected fields"
                )
            entry = _entry_from_payload(payload)
            if entry.sequence != line_number or entry.previous_hmac != previous_hmac:
                raise VoiceprintTombstoneIntegrityError(
                    "voiceprint tombstone ledger chain is discontinuous"
                )
            unsigned = {key: payload[key] for key in payload if key != "hmac"}
            expected = self._sign(unsigned)
            if not hmac.compare_digest(entry.entry_hmac, expected):
                raise VoiceprintTombstoneIntegrityError(
                    "voiceprint tombstone ledger authentication failed"
                )
            entries.append(entry)
            previous_hmac = entry.entry_hmac
        return tuple(entries)

    def _sign(self, payload: dict[str, object]) -> str:
        return hmac.new(self._key, _canonical_json(payload), hashlib.sha256).hexdigest()


def _entry_from_payload(payload: dict[str, object]) -> VoiceprintTombstone:
    try:
        if (
            payload["schema_version"] != TOMBSTONE_SCHEMA
            or type(payload["sequence"]) is not int
            or type(payload["owner_user_id"]) is not int
            or not isinstance(payload["profile_id"], str)
            or not isinstance(payload["deleted_at"], str)
            or not isinstance(payload["reason"], str)
            or not isinstance(payload["previous_hmac"], str)
            or not isinstance(payload["hmac"], str)
        ):
            raise ValueError
        owner, profile, timestamp, reason = _validated_values(
            payload["owner_user_id"],
            payload["profile_id"],
            _parse_timestamp(payload["deleted_at"]),
            payload["reason"],
        )
        sequence = payload["sequence"]
        previous_hmac = payload["previous_hmac"]
        entry_hmac = payload["hmac"]
        if (
            sequence < 1
            or len(previous_hmac) != 64
            or len(entry_hmac) != 64
            or any(character not in "0123456789abcdef" for character in previous_hmac + entry_hmac)
            or payload["deleted_at"] != timestamp
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise VoiceprintTombstoneIntegrityError(
            "voiceprint tombstone ledger entry is invalid"
        ) from exc
    return VoiceprintTombstone(
        sequence=sequence,
        owner_user_id=owner,
        profile_id=profile,
        deleted_at=_parse_timestamp(timestamp),
        reason=reason,
        previous_hmac=previous_hmac,
        entry_hmac=entry_hmac,
    )


def _validated_values(
    owner_user_id: int,
    profile_id: str,
    deleted_at: datetime,
    reason: str,
) -> tuple[int, str, str, str]:
    if type(owner_user_id) is not int or owner_user_id <= 0:
        raise VoiceprintTombstoneIntegrityError("voiceprint tombstone owner is invalid")
    if not isinstance(profile_id, str):
        raise VoiceprintTombstoneIntegrityError(
            "voiceprint tombstone profile id is invalid"
        )
    try:
        profile = str(UUID(profile_id))
    except (TypeError, ValueError) as exc:
        raise VoiceprintTombstoneIntegrityError(
            "voiceprint tombstone profile id is invalid"
        ) from exc
    if not isinstance(deleted_at, datetime) or deleted_at.tzinfo is None:
        raise VoiceprintTombstoneIntegrityError(
            "voiceprint tombstone timestamp must be timezone-aware"
        )
    if not isinstance(reason, str):
        raise VoiceprintTombstoneIntegrityError("voiceprint tombstone reason is invalid")
    normalized_reason = reason.strip().lower()
    if normalized_reason not in {"revoked", "deleted"}:
        raise VoiceprintTombstoneIntegrityError("voiceprint tombstone reason is invalid")
    return (
        int(owner_user_id),
        profile,
        _canonical_timestamp(deleted_at),
        normalized_reason,
    )


def _canonical_timestamp(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise VoiceprintTombstoneIntegrityError(
            "voiceprint tombstone timestamp is invalid"
        ) from exc
    if parsed.tzinfo is None:
        raise VoiceprintTombstoneIntegrityError(
            "voiceprint tombstone timestamp must be timezone-aware"
        )
    return parsed.astimezone(timezone.utc)


def _decode_key(value: str) -> bytes:
    try:
        padding = "=" * (-len(value) % 4)
        return base64.b64decode(
            (value + padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except (UnicodeEncodeError, ValueError, binascii.Error) as exc:
        raise VoiceprintTombstoneConfigurationError(
            "voiceprint tombstone HMAC key encoding is invalid"
        ) from exc


def _canonical_json(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


__all__ = [
    "EMPTY_TOMBSTONE_HEAD_HMAC",
    "TOMBSTONE_SCHEMA",
    "VoiceprintTombstone",
    "VoiceprintTombstoneCheckpoint",
    "VoiceprintTombstoneConfigurationError",
    "VoiceprintTombstoneError",
    "VoiceprintTombstoneIntegrityError",
    "VoiceprintTombstoneLedger",
]
