"""Crash-recoverable meeting deletion and opt-in audio retention.

Deletion is intentionally handled outside request workers.  A user-authorized
DELETE request only moves the session into ``deleted`` and enqueues a durable
job.  This worker quiesces competing work, records an authenticated tombstone
outside database backups, removes controlled files, and finally scrubs meeting
content in one database transaction.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import fcntl
import hashlib
import hmac
import json
import os
import re
import shutil
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO, Final, Sequence
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, exists, func, or_, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from services.auth_service import User
from services.meeting_contracts import (
    LexiconScope,
    MeetingArtifact,
    MeetingASRCorrectionEvent,
    MeetingAudioChunk,
    MeetingEvent,
    MeetingIdempotencyRecord,
    MeetingJob,
    MeetingJobKind,
    MeetingJobState,
    MeetingLexiconEntry,
    MeetingLexiconVersion,
    MeetingModelSetting,
    MeetingModelSnapshot,
    MeetingSegmentRevision,
    MeetingSession,
    MeetingSpeakerTrack,
    MeetingState,
    MeetingStreamLease,
    MeetingStreamTicket,
    MeetingTermCandidate,
    MeetingTermCandidateSource,
    MeetingTranscriptSegment,
    MeetingVoiceprintMatch,
    ModelFallbackPolicy,
    ModelSelectionMode,
    utcnow,
)
from services.meeting_import_config import MeetingImportSettings
from services.meeting_import_contracts import MeetingImportChunk, MeetingImportUpload
from services.meeting_import_storage import MeetingImportStorage
from services.meeting_native_capture_contracts import (
    MeetingNativeCapture,
    MeetingNativeCaptureAudioLink,
    MeetingNativeCaptureBatch,
    MeetingNativeCaptureEpoch,
    MeetingNativeCaptureFinalization,
    MeetingNativeCaptureGap,
    MeetingNativeCaptureManifestEntry,
    MeetingNativeCaptureToken,
    NativeCaptureFinalizationState,
    NativeCaptureState,
)
from services.path_config import BACKEND_DATA_ROOT, RUNTIME_ROOT

DELETION_POLICY_VERSION: Final = "siq.meeting.deletion.v1"
DELETION_TOMBSTONE_SCHEMA: Final = "siq.meeting.deletion_tombstone.v1"
DELETION_AUDIT_SCHEMA: Final = "siq.meeting.deletion_audit.v1"
_ZERO_HMAC: Final = "0" * 64
_MAX_LEDGER_BYTES: Final = 64 * 1024 * 1024
_MAX_LEDGER_ENTRIES: Final = 200_000
_MAX_LEDGER_LINE_BYTES: Final = 2_048
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TOMBSTONE_FIELDS: Final = {
    "schema_version",
    "sequence",
    "owner_user_id",
    "meeting_id",
    "delete_job_id",
    "deleted_at",
    "reason",
    "previous_hmac",
    "hmac",
}
_ACTIVE_JOB_STATES: Final = {
    MeetingJobState.QUEUED.value,
    MeetingJobState.LEASED.value,
    MeetingJobState.RUNNING.value,
    MeetingJobState.RETRY_WAIT.value,
}


class MeetingRetentionError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class MeetingDeletionLedgerError(MeetingRetentionError):
    pass


@dataclass(frozen=True, slots=True)
class MeetingDeletionTombstone:
    sequence: int
    owner_user_id: int
    meeting_id: str
    delete_job_id: str
    deleted_at: datetime
    reason: str
    previous_hmac: str
    entry_hmac: str


@dataclass(frozen=True, slots=True)
class MeetingRetentionSettings:
    worker_enabled: bool = False
    retention_scan_enabled: bool = False
    audio_retention_days: int = 90
    scan_batch_size: int = 50
    lease_seconds: int = 300
    retry_delay_seconds: int = 30
    poll_interval_seconds: float = 1.0
    scan_interval_seconds: int = 3600

    @classmethod
    def from_env(cls) -> "MeetingRetentionSettings":
        return cls(
            worker_enabled=_env_bool("SIQ_MEETING_DELETE_WORKER_ENABLED", False),
            retention_scan_enabled=_env_bool("SIQ_MEETING_RETENTION_SCAN_ENABLED", False),
            audio_retention_days=_env_int("SIQ_MEETING_AUDIO_RETENTION_DAYS", 90, 1, 3650),
            scan_batch_size=_env_int("SIQ_MEETING_RETENTION_SCAN_BATCH_SIZE", 50, 1, 500),
            lease_seconds=_env_int("SIQ_MEETING_DELETE_LEASE_SECONDS", 300, 30, 3600),
            retry_delay_seconds=_env_int("SIQ_MEETING_DELETE_RETRY_DELAY_SECONDS", 30, 1, 3600),
            poll_interval_seconds=_env_float("SIQ_MEETING_DELETE_POLL_SECONDS", 1.0, 0.05, 60.0),
            scan_interval_seconds=_env_int("SIQ_MEETING_RETENTION_SCAN_INTERVAL_SECONDS", 3600, 60, 86_400),
        )


@dataclass(frozen=True, slots=True)
class PurgeResult:
    removed_roots: int = 0
    removed_entries: int = 0


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    ledger_entry_count: int
    matched_session_count: int
    scrubbed_session_count: int
    absent_session_count: int
    residual_session_count: int
    residual_storage_count: int
    ownership_mismatch_count: int

    @property
    def passed(self) -> bool:
        return (
            self.residual_session_count == 0 and self.residual_storage_count == 0 and self.ownership_mismatch_count == 0
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "siq.meeting.deletion_reconcile.v1",
            "status": "passed" if self.passed else "failed",
            "ledger_entry_count": self.ledger_entry_count,
            "matched_session_count": self.matched_session_count,
            "scrubbed_session_count": self.scrubbed_session_count,
            "absent_session_count": self.absent_session_count,
            "residual_session_count": self.residual_session_count,
            "residual_storage_count": self.residual_storage_count,
            "ownership_mismatch_count": self.ownership_mismatch_count,
        }


class MeetingDeletionLedger:
    """Append-only authenticated deletion intent stored outside DB backups."""

    def __init__(self, *, path: Path, hmac_key: bytes, backend_data_root: Path | None = None) -> None:
        self.path = path.expanduser().resolve(strict=False)
        backend_root = (backend_data_root or BACKEND_DATA_ROOT).expanduser().resolve(strict=False)
        if _is_within(self.path, backend_root):
            raise MeetingDeletionLedgerError(
                "DELETE_LEDGER_CONFIGURATION_INVALID",
                "meeting deletion ledger must be outside the database backup root",
                retryable=False,
            )
        if len(hmac_key) != 32:
            raise MeetingDeletionLedgerError(
                "DELETE_LEDGER_CONFIGURATION_INVALID",
                "meeting deletion ledger HMAC key must be exactly 32 bytes",
                retryable=False,
            )
        self._key = bytes(hmac_key)

    @classmethod
    def from_env(cls) -> "MeetingDeletionLedger":
        configured = os.getenv("SIQ_MEETING_DELETION_TOMBSTONE_PATH", "").strip()
        path = Path(configured) if configured else RUNTIME_ROOT / "security" / "meeting-deletion-tombstones.jsonl"
        encoded_key = os.getenv("SIQ_MEETING_DELETION_TOMBSTONE_HMAC_KEY", "").strip()
        if not encoded_key:
            raise MeetingDeletionLedgerError(
                "DELETE_LEDGER_CONFIGURATION_INVALID",
                "SIQ_MEETING_DELETION_TOMBSTONE_HMAC_KEY is required",
                retryable=False,
            )
        backend_root = Path(os.getenv("SIQ_BACKEND_DATA_ROOT", str(BACKEND_DATA_ROOT)))
        return cls(path=path, hmac_key=_decode_hmac_key(encoded_key), backend_data_root=backend_root)

    def initialize(self) -> int:
        self._ensure_parent()
        descriptor = self._open(os.O_RDWR | os.O_APPEND | os.O_CREAT)
        try:
            with os.fdopen(descriptor, "r+b", closefd=True) as ledger:
                fcntl.flock(ledger.fileno(), fcntl.LOCK_EX)
                values = self._read_locked(ledger)
                ledger.flush()
                os.fsync(ledger.fileno())
                return len(values)
        except Exception:
            _close_quietly(descriptor)
            raise

    def load(self) -> tuple[MeetingDeletionTombstone, ...]:
        if not self.path.exists():
            return ()
        self._validate_parent()
        descriptor = self._open(os.O_RDONLY)
        try:
            with os.fdopen(descriptor, "rb", closefd=True) as ledger:
                fcntl.flock(ledger.fileno(), fcntl.LOCK_SH)
                return self._read_locked(ledger)
        except Exception:
            _close_quietly(descriptor)
            raise

    def latest(self) -> dict[str, MeetingDeletionTombstone]:
        values: dict[str, MeetingDeletionTombstone] = {}
        for entry in self.load():
            previous = values.get(entry.meeting_id)
            if previous is not None and previous.owner_user_id != entry.owner_user_id:
                raise MeetingDeletionLedgerError(
                    "DELETE_LEDGER_INTEGRITY_FAILED",
                    "meeting deletion ownership changed in the ledger",
                    retryable=False,
                )
            values[entry.meeting_id] = entry
        return values

    def append(
        self,
        *,
        owner_user_id: int,
        meeting_id: str,
        delete_job_id: str,
        deleted_at: datetime,
        reason: str = "user_requested",
    ) -> MeetingDeletionTombstone:
        owner, meeting, job, timestamp, normalized_reason = _validate_tombstone_values(
            owner_user_id, meeting_id, delete_job_id, deleted_at, reason
        )
        self._ensure_parent()
        descriptor = self._open(os.O_RDWR | os.O_APPEND | os.O_CREAT)
        try:
            with os.fdopen(descriptor, "r+b", closefd=True) as ledger:
                fcntl.flock(ledger.fileno(), fcntl.LOCK_EX)
                entries = self._read_locked(ledger)
                matching = [entry for entry in entries if entry.meeting_id == meeting]
                if matching:
                    existing = matching[-1]
                    if existing.owner_user_id != owner:
                        raise MeetingDeletionLedgerError(
                            "DELETE_LEDGER_INTEGRITY_FAILED",
                            "meeting deletion ownership changed",
                            retryable=False,
                        )
                    return existing
                previous_hmac = entries[-1].entry_hmac if entries else _ZERO_HMAC
                unsigned: dict[str, object] = {
                    "schema_version": DELETION_TOMBSTONE_SCHEMA,
                    "sequence": len(entries) + 1,
                    "owner_user_id": owner,
                    "meeting_id": meeting,
                    "delete_job_id": job,
                    "deleted_at": timestamp,
                    "reason": normalized_reason,
                    "previous_hmac": previous_hmac,
                }
                entry_hmac = self._sign(unsigned)
                payload = {**unsigned, "hmac": entry_hmac}
                encoded = _canonical_json(payload) + b"\n"
                if len(encoded) > _MAX_LEDGER_LINE_BYTES:
                    raise MeetingDeletionLedgerError(
                        "DELETE_LEDGER_CAPACITY_EXCEEDED",
                        "meeting deletion ledger entry exceeds its bound",
                        retryable=False,
                    )
                if ledger.seek(0, os.SEEK_END) + len(encoded) > _MAX_LEDGER_BYTES:
                    raise MeetingDeletionLedgerError(
                        "DELETE_LEDGER_CAPACITY_EXCEEDED",
                        "meeting deletion ledger is full",
                        retryable=False,
                    )
                ledger.write(encoded)
                ledger.flush()
                os.fsync(ledger.fileno())
                return _tombstone_from_payload(payload)
        except Exception:
            _close_quietly(descriptor)
            raise

    def _ensure_parent(self) -> None:
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if self.path.parent.is_symlink():
            raise MeetingDeletionLedgerError(
                "DELETE_LEDGER_CONFIGURATION_INVALID",
                "meeting deletion ledger directory cannot be a symlink",
                retryable=False,
            )
        os.chmod(self.path.parent, 0o700)

    def _validate_parent(self) -> None:
        if self.path.parent.is_symlink() or stat.S_IMODE(self.path.parent.stat().st_mode) != 0o700:
            raise MeetingDeletionLedgerError(
                "DELETE_LEDGER_INTEGRITY_FAILED",
                "meeting deletion ledger directory permissions are unsafe",
                retryable=False,
            )

    def _open(self, flags: int) -> int:
        try:
            descriptor = os.open(
                self.path,
                flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
        except OSError as exc:
            raise MeetingDeletionLedgerError(
                "DELETE_LEDGER_UNAVAILABLE",
                "meeting deletion ledger cannot be opened safely",
            ) from exc
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or stat.S_IMODE(details.st_mode) != 0o600:
            os.close(descriptor)
            raise MeetingDeletionLedgerError(
                "DELETE_LEDGER_INTEGRITY_FAILED",
                "meeting deletion ledger must be a regular 0600 file",
                retryable=False,
            )
        return descriptor

    def _read_locked(self, ledger: BinaryIO) -> tuple[MeetingDeletionTombstone, ...]:
        if os.fstat(ledger.fileno()).st_size > _MAX_LEDGER_BYTES:
            raise MeetingDeletionLedgerError(
                "DELETE_LEDGER_INTEGRITY_FAILED", "meeting deletion ledger exceeds its bound", retryable=False
            )
        ledger.seek(0)
        values: list[MeetingDeletionTombstone] = []
        previous_hmac = _ZERO_HMAC
        for line_number, line in enumerate(ledger, start=1):
            if line_number > _MAX_LEDGER_ENTRIES or len(line) > _MAX_LEDGER_LINE_BYTES:
                raise MeetingDeletionLedgerError(
                    "DELETE_LEDGER_INTEGRITY_FAILED",
                    "meeting deletion ledger entry limit was exceeded",
                    retryable=False,
                )
            try:
                payload = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise MeetingDeletionLedgerError(
                    "DELETE_LEDGER_INTEGRITY_FAILED",
                    "meeting deletion ledger contains invalid JSON",
                    retryable=False,
                ) from exc
            if not isinstance(payload, dict) or set(payload) != _TOMBSTONE_FIELDS:
                raise MeetingDeletionLedgerError(
                    "DELETE_LEDGER_INTEGRITY_FAILED",
                    "meeting deletion ledger contains unexpected fields",
                    retryable=False,
                )
            entry = _tombstone_from_payload(payload)
            unsigned = {key: payload[key] for key in payload if key != "hmac"}
            if (
                entry.sequence != line_number
                or entry.previous_hmac != previous_hmac
                or not hmac.compare_digest(entry.entry_hmac, self._sign(unsigned))
            ):
                raise MeetingDeletionLedgerError(
                    "DELETE_LEDGER_INTEGRITY_FAILED",
                    "meeting deletion ledger authentication failed",
                    retryable=False,
                )
            values.append(entry)
            previous_hmac = entry.entry_hmac
        return tuple(values)

    def _sign(self, payload: dict[str, object]) -> str:
        return hmac.new(self._key, _canonical_json(payload), hashlib.sha256).hexdigest()


class MeetingStoragePurger:
    """Deletes only server-derived owner/meeting paths and never DB-supplied paths."""

    def __init__(
        self,
        *,
        audio_root: Path | None = None,
        export_root: Path | None = None,
        native_capture_root: Path | None = None,
    ) -> None:
        configured_audio = (os.getenv("SIQ_MEETING_AUDIO_ROOT") or os.getenv("SIQ_MEETINGS_AUDIO_ROOT") or "").strip()
        configured_export = os.getenv("SIQ_MEETING_EXPORT_ROOT", "").strip()
        self.audio_root = (
            (audio_root or (Path(configured_audio) if configured_audio else BACKEND_DATA_ROOT / "meeting_audio"))
            .expanduser()
            .resolve(strict=False)
        )
        self.export_root = (
            (export_root or (Path(configured_export) if configured_export else BACKEND_DATA_ROOT / "meeting_exports"))
            .expanduser()
            .resolve(strict=False)
        )
        configured_native = os.getenv("SIQ_MEETING_NATIVE_CAPTURE_ROOT", "").strip()
        self.native_capture_root = (
            (
                native_capture_root
                or (Path(configured_native) if configured_native else BACKEND_DATA_ROOT / "meeting_native_captures")
            )
            .expanduser()
            .resolve(strict=False)
        )
        for root in {self.audio_root, self.export_root, self.native_capture_root}:
            root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def purge_meeting(self, owner_user_id: int, meeting_id: str) -> PurgeResult:
        targets = {
            self._meeting_path(self.audio_root, owner_user_id, meeting_id),
            self._meeting_path(self.export_root, owner_user_id, meeting_id),
        }
        roots = entries = 0
        for target in targets:
            removed = _remove_controlled_tree(target)
            roots += int(removed > 0)
            entries += removed
        return PurgeResult(removed_roots=roots, removed_entries=entries)

    def purge_native_captures(self, owner_user_id: int, capture_ids: Sequence[str]) -> PurgeResult:
        roots = entries = 0
        for capture_id in sorted(set(capture_ids)):
            target = self._native_capture_path(owner_user_id, capture_id)
            removed = _remove_controlled_tree(target)
            roots += int(removed > 0)
            entries += removed
        return PurgeResult(removed_roots=roots, removed_entries=entries)

    def expire_audio(self, owner_user_id: int, meeting_id: str) -> PurgeResult:
        meeting_root = self._meeting_path(self.audio_root, owner_user_id, meeting_id)
        if meeting_root.is_symlink():
            return PurgeResult(int(_remove_controlled_tree(meeting_root) > 0), 1)
        removed = 0
        for name in ("chunks", "audio", "temp", "manifest.json"):
            removed += _remove_controlled_tree(meeting_root / name)
        try:
            meeting_root.rmdir()
            root_count = 1
        except (FileNotFoundError, OSError):
            root_count = 0
        return PurgeResult(removed_roots=root_count, removed_entries=removed)

    def has_meeting_storage(self, owner_user_id: int, meeting_id: str) -> bool:
        """Return whether controlled audio/export storage remains for a meeting."""

        for root in {self.audio_root, self.export_root}:
            owner_path = root / _safe_component(str(owner_user_id))
            try:
                owner_details = owner_path.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(owner_details.st_mode) or not stat.S_ISDIR(owner_details.st_mode):
                return True
            target = self._meeting_path(root, owner_user_id, meeting_id)
            if target.exists() or target.is_symlink():
                return True
        return False

    def has_native_capture_storage(self, owner_user_id: int, capture_ids: Sequence[str]) -> bool:
        owner_path = self.native_capture_root / _safe_component(str(owner_user_id))
        try:
            owner_details = owner_path.lstat()
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(owner_details.st_mode) or not stat.S_ISDIR(owner_details.st_mode):
            return True
        return any(
            (path := self._native_capture_path(owner_user_id, capture_id)).exists() or path.is_symlink()
            for capture_id in capture_ids
        )

    @staticmethod
    def _meeting_path(root: Path, owner_user_id: int, meeting_id: str) -> Path:
        owner = _safe_component(str(owner_user_id))
        meeting = _safe_component(meeting_id)
        owner_path = root / owner
        try:
            owner_details = owner_path.lstat()
        except FileNotFoundError:
            owner_details = None
        if owner_details is not None and (
            stat.S_ISLNK(owner_details.st_mode) or not stat.S_ISDIR(owner_details.st_mode)
        ):
            raise MeetingRetentionError(
                "DELETE_STORAGE_PATH_INVALID",
                "meeting storage owner directory is unsafe",
                retryable=False,
            )
        target = owner_path / meeting
        if target.parent.parent != root:
            raise MeetingRetentionError(
                "DELETE_STORAGE_PATH_INVALID", "meeting storage path escaped its root", retryable=False
            )
        return target

    def _native_capture_path(self, owner_user_id: int, capture_id: str) -> Path:
        owner = _safe_component(str(owner_user_id))
        capture = _safe_component(capture_id)
        owner_path = self.native_capture_root / owner
        try:
            owner_details = owner_path.lstat()
        except FileNotFoundError:
            owner_details = None
        if owner_details is not None and (
            stat.S_ISLNK(owner_details.st_mode) or not stat.S_ISDIR(owner_details.st_mode)
        ):
            raise MeetingRetentionError(
                "DELETE_STORAGE_PATH_INVALID",
                "native capture owner directory is unsafe",
                retryable=False,
            )
        return owner_path / capture


class MeetingRetentionWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        ledger: MeetingDeletionLedger,
        purger: MeetingStoragePurger,
        worker_id: str | None = None,
        settings: MeetingRetentionSettings | None = None,
        import_storage: MeetingImportStorage | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.ledger = ledger
        self.purger = purger
        self.worker_id = (worker_id or f"meeting-delete-{uuid4().hex[:12]}").strip()[:100]
        if not self.worker_id:
            raise ValueError("meeting retention worker_id is required")
        self.settings = settings or MeetingRetentionSettings.from_env()
        self.import_storage = import_storage

    def _recording_import_storage(self) -> MeetingImportStorage:
        if self.import_storage is None:
            self.import_storage = MeetingImportStorage(MeetingImportSettings.from_env().root)
        return self.import_storage

    async def _purge_import_staging(self, owner_user_id: int, meeting_id: str) -> None:
        async with self.session_factory() as session:
            upload_ids = list(
                (
                    await session.exec(
                        select(MeetingImportUpload.id).where(MeetingImportUpload.meeting_id == meeting_id)
                    )
                ).all()
            )
        if not upload_ids:
            return
        storage = self._recording_import_storage()
        for upload_id in upload_ids:
            await asyncio.to_thread(storage.purge_upload, owner_user_id, str(upload_id))

    async def _native_capture_ids(self, meeting_id: str) -> tuple[str, ...]:
        async with self.session_factory() as session:
            values = list(
                (
                    await session.exec(
                        select(MeetingNativeCapture.id)
                        .where(MeetingNativeCapture.meeting_id == meeting_id)
                        .order_by(MeetingNativeCapture.id)
                    )
                ).all()
            )
        return tuple(str(value) for value in values)

    async def initialize(self) -> int:
        return await asyncio.to_thread(self.ledger.initialize)

    async def claim_next(self) -> MeetingJob | None:
        now = utcnow()
        eligible = and_(
            MeetingJob.job_kind == MeetingJobKind.DELETE.value,
            or_(
                and_(
                    MeetingJob.state == MeetingJobState.QUEUED.value,
                    MeetingJob.attempt < MeetingJob.max_attempts,
                ),
                and_(
                    MeetingJob.state == MeetingJobState.RETRY_WAIT.value,
                    MeetingJob.attempt < MeetingJob.max_attempts,
                    or_(MeetingJob.lease_until.is_(None), MeetingJob.lease_until <= now),
                ),
                # A process can die after claiming its final configured attempt.
                # Expired active leases remain recoverable; explicit failures do
                # not.  Deletion steps are idempotent and the external tombstone
                # prevents a restored backup from resurrecting content.
                and_(
                    MeetingJob.state.in_([MeetingJobState.LEASED.value, MeetingJobState.RUNNING.value]),
                    MeetingJob.lease_until.is_not(None),
                    MeetingJob.lease_until <= now,
                ),
            ),
        )
        candidate = (
            select(MeetingJob.id)
            .join(MeetingSession, MeetingSession.id == MeetingJob.meeting_id)
            .where(eligible, MeetingSession.state == MeetingState.DELETED.value)
            .order_by(MeetingJob.created_at, MeetingJob.id)
            .limit(1)
            .scalar_subquery()
        )
        statement = (
            update(MeetingJob)
            .where(MeetingJob.id == candidate, eligible)
            .values(
                state=MeetingJobState.LEASED.value,
                attempt=MeetingJob.attempt + 1,
                lease_owner=self.worker_id,
                lease_until=now + timedelta(seconds=self.settings.lease_seconds),
                public_error_code=None,
                internal_diagnostic=None,
                updated_at=now,
            )
            .returning(MeetingJob.id)
        )
        async with self.session_factory() as session:
            result = await session.exec(statement)
            job_id = result.scalar_one_or_none()
            await session.commit()
            if job_id is None:
                return None
            return (await session.exec(select(MeetingJob).where(MeetingJob.id == job_id))).one()

    async def run_once(self) -> bool:
        job = await self.claim_next()
        if job is None:
            return False
        await self.process(job.id)
        return True

    async def process(self, job_id: str) -> None:
        heartbeat: asyncio.Task[bool] | None = None
        try:
            job, meeting = await self._mark_running_and_quiesce(job_id)
            heartbeat = asyncio.create_task(self._lease_heartbeat(job.id))
            tombstone = await asyncio.to_thread(
                self.ledger.append,
                owner_user_id=meeting.owner_user_id,
                meeting_id=meeting.id,
                delete_job_id=job.id,
                deleted_at=utcnow(),
                reason="user_requested",
            )
            native_capture_ids = await self._native_capture_ids(meeting.id)
            await asyncio.to_thread(self.purger.purge_meeting, meeting.owner_user_id, meeting.id)
            await asyncio.to_thread(
                self.purger.purge_native_captures,
                meeting.owner_user_id,
                native_capture_ids,
            )
            await self._purge_import_staging(meeting.owner_user_id, meeting.id)
            await self._scrub_database(job.id, tombstone, require_lease=True)
            lease_valid = await _finish_heartbeat(heartbeat)
            heartbeat = None
            if not lease_valid and not await self._delete_job_succeeded(job.id):
                raise MeetingRetentionError("DELETE_JOB_LEASE_LOST", "meeting delete lease was lost")
        except BaseException as exc:
            if heartbeat is not None:
                await _finish_heartbeat(heartbeat)
            if isinstance(exc, asyncio.CancelledError):
                raise
            await self._fail_job(job_id, exc)

    async def run_forever(self) -> None:
        await self.run_until_stopped(asyncio.Event())

    async def run_until_stopped(self, stop: asyncio.Event) -> None:
        """Run until requested to stop, allowing the current delete to finish."""

        await self.initialize()
        last_scan = datetime.min
        while not stop.is_set():
            worked = await self.run_once()
            now = utcnow()
            if (
                self.settings.retention_scan_enabled
                and (now - last_scan).total_seconds() >= self.settings.scan_interval_seconds
            ):
                await self.scan_expired_audio()
                last_scan = now
                worked = True
            if not worked:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.settings.poll_interval_seconds)
                except TimeoutError:
                    pass

    async def scan_expired_audio(self) -> int:
        """Expire completed-meeting audio only; never auto-delete transcripts."""
        if not self.settings.retention_scan_enabled:
            return 0
        cutoff = utcnow() - timedelta(days=self.settings.audio_retention_days)
        async with self.session_factory() as session:
            candidates = list(
                (
                    await session.exec(
                        select(MeetingSession.id, MeetingSession.owner_user_id)
                        .where(
                            MeetingSession.state.in_(
                                [
                                    MeetingState.STOPPED.value,
                                    MeetingState.ARCHIVED.value,
                                    MeetingState.INTERRUPTED.value,
                                ]
                            ),
                            MeetingSession.stopped_at.is_not(None),
                            MeetingSession.stopped_at <= cutoff,
                            or_(
                                exists().where(MeetingAudioChunk.meeting_id == MeetingSession.id),
                                exists().where(MeetingNativeCaptureBatch.meeting_id == MeetingSession.id),
                            ),
                        )
                        .order_by(MeetingSession.stopped_at, MeetingSession.id)
                        .limit(self.settings.scan_batch_size)
                    )
                ).all()
            )
        expired = 0
        for meeting_id, owner_user_id in candidates:
            if await self._expire_one_audio(str(meeting_id), int(owner_user_id), cutoff):
                expired += 1
        remaining = max(0, self.settings.scan_batch_size - expired)
        if remaining:
            expired += await self._scan_orphan_native_audio(cutoff, remaining)
        return expired

    async def _scan_orphan_native_audio(self, cutoff: datetime, limit: int) -> int:
        """Expire terminal native-only audio whose meeting never reached stopped."""

        if limit <= 0:
            return 0
        async with self.session_factory() as session:
            candidates = list(
                (
                    await session.exec(
                        select(
                            MeetingNativeCapture.id,
                            MeetingNativeCapture.meeting_id,
                            MeetingNativeCapture.owner_user_id,
                        )
                        .where(
                            or_(
                                and_(
                                    MeetingNativeCapture.state == NativeCaptureState.SEALED.value,
                                    MeetingNativeCapture.sealed_at.is_not(None),
                                    MeetingNativeCapture.sealed_at <= cutoff,
                                ),
                                and_(
                                    MeetingNativeCapture.state == NativeCaptureState.REVOKED.value,
                                    or_(
                                        and_(
                                            MeetingNativeCapture.revoked_at.is_not(None),
                                            MeetingNativeCapture.revoked_at <= cutoff,
                                        ),
                                        and_(
                                            MeetingNativeCapture.sealed_at.is_not(None),
                                            MeetingNativeCapture.sealed_at <= cutoff,
                                        ),
                                    ),
                                ),
                            ),
                            exists().where(MeetingNativeCaptureBatch.capture_id == MeetingNativeCapture.id),
                            ~exists().where(MeetingNativeCaptureAudioLink.capture_id == MeetingNativeCapture.id),
                        )
                        .order_by(
                            func.coalesce(
                                MeetingNativeCapture.revoked_at,
                                MeetingNativeCapture.sealed_at,
                            ),
                            MeetingNativeCapture.id,
                        )
                        .limit(limit)
                    )
                ).all()
            )
        expired = 0
        for capture_id, meeting_id, owner_user_id in candidates:
            if await self._expire_one_orphan_native_audio(
                str(capture_id),
                str(meeting_id),
                int(owner_user_id),
                cutoff,
            ):
                expired += 1
        return expired

    async def reconcile_tombstones(self, *, apply: bool) -> ReconcileReport:
        latest = sorted(
            self.ledger.latest().values(),
            key=lambda value: (value.owner_user_id, value.meeting_id),
        )
        matched = scrubbed = absent = ownership_mismatch = 0
        native_capture_ids: dict[str, tuple[str, ...]] = {}
        for entry in latest:
            native_capture_ids[entry.meeting_id] = await self._native_capture_ids(entry.meeting_id)
            async with self.session_factory() as session:
                meeting = await session.get(MeetingSession, entry.meeting_id)
            if meeting is None:
                absent += 1
                if apply:
                    await asyncio.to_thread(self.purger.purge_meeting, entry.owner_user_id, entry.meeting_id)
                    await asyncio.to_thread(
                        self.purger.purge_native_captures,
                        entry.owner_user_id,
                        native_capture_ids[entry.meeting_id],
                    )
                continue
            matched += 1
            if meeting.owner_user_id != entry.owner_user_id:
                ownership_mismatch += 1
                continue
            if apply:
                await asyncio.to_thread(self.purger.purge_meeting, entry.owner_user_id, entry.meeting_id)
                await asyncio.to_thread(
                    self.purger.purge_native_captures,
                    entry.owner_user_id,
                    native_capture_ids[entry.meeting_id],
                )
                await self._purge_import_staging(entry.owner_user_id, entry.meeting_id)
                await self._scrub_database(entry.delete_job_id, entry, require_lease=False)
                scrubbed += 1
        residual = await self._count_residuals(latest)
        storage_checks = await asyncio.gather(
            *(
                asyncio.to_thread(
                    self.purger.has_meeting_storage,
                    entry.owner_user_id,
                    entry.meeting_id,
                )
                for entry in latest
            )
        )
        native_storage_checks = await asyncio.gather(
            *(
                asyncio.to_thread(
                    self.purger.has_native_capture_storage,
                    entry.owner_user_id,
                    native_capture_ids[entry.meeting_id],
                )
                for entry in latest
            )
        )
        residual_storage = sum(map(int, storage_checks)) + sum(map(int, native_storage_checks))
        return ReconcileReport(
            ledger_entry_count=len(latest),
            matched_session_count=matched,
            scrubbed_session_count=scrubbed,
            absent_session_count=absent,
            residual_session_count=residual,
            residual_storage_count=residual_storage,
            ownership_mismatch_count=ownership_mismatch,
        )

    async def _mark_running_and_quiesce(self, job_id: str) -> tuple[MeetingJob, MeetingSession]:
        async with self.session_factory() as session:
            job = (
                await session.exec(
                    select(MeetingJob)
                    .where(
                        MeetingJob.id == job_id,
                        MeetingJob.job_kind == MeetingJobKind.DELETE.value,
                        MeetingJob.state == MeetingJobState.LEASED.value,
                        MeetingJob.lease_owner == self.worker_id,
                    )
                    .with_for_update()
                )
            ).first()
            if job is None:
                raise MeetingRetentionError("DELETE_JOB_LEASE_LOST", "meeting delete job is no longer owned")
            meeting = (
                await session.exec(select(MeetingSession).where(MeetingSession.id == job.meeting_id).with_for_update())
            ).first()
            if meeting is None or meeting.state != MeetingState.DELETED.value:
                raise MeetingRetentionError(
                    "DELETE_JOB_NOT_AUTHORIZED",
                    "meeting delete job is not backed by an authorized deleted session",
                    retryable=False,
                )
            job.state = MeetingJobState.RUNNING.value
            job.updated_at = utcnow()
            session.add(job)
            await session.exec(
                update(MeetingJob)
                .where(
                    MeetingJob.meeting_id == meeting.id,
                    MeetingJob.id != job.id,
                    MeetingJob.state.in_(list(_ACTIVE_JOB_STATES)),
                )
                .values(
                    state=MeetingJobState.CANCELLED.value,
                    lease_owner=None,
                    lease_until=None,
                    public_error_code="MEETING_DELETED",
                    internal_diagnostic=None,
                    updated_at=utcnow(),
                )
            )
            await session.exec(delete(MeetingStreamLease).where(MeetingStreamLease.meeting_id == meeting.id))
            await session.exec(delete(MeetingStreamTicket).where(MeetingStreamTicket.meeting_id == meeting.id))
            await session.commit()
            return job, meeting

    async def _scrub_database(
        self,
        delete_job_id: str,
        tombstone: MeetingDeletionTombstone,
        *,
        require_lease: bool,
    ) -> None:
        async with self.session_factory() as session:
            meeting = (
                await session.exec(
                    select(MeetingSession).where(MeetingSession.id == tombstone.meeting_id).with_for_update()
                )
            ).first()
            if meeting is None:
                return
            if meeting.owner_user_id != tombstone.owner_user_id:
                raise MeetingRetentionError(
                    "DELETE_TOMBSTONE_OWNER_MISMATCH",
                    "meeting deletion tombstone ownership does not match",
                    retryable=False,
                )
            delete_job = await session.get(MeetingJob, delete_job_id)
            if delete_job is not None and delete_job.meeting_id != meeting.id:
                raise MeetingRetentionError(
                    "DELETE_TOMBSTONE_JOB_MISMATCH",
                    "meeting deletion tombstone job does not match",
                    retryable=False,
                )
            if require_lease and (
                delete_job is None
                or delete_job.state != MeetingJobState.RUNNING.value
                or delete_job.lease_owner != self.worker_id
            ):
                raise MeetingRetentionError("DELETE_JOB_LEASE_LOST", "meeting delete lease was lost")
            if delete_job is None and not require_lease:
                delete_job = (
                    await session.exec(
                        select(MeetingJob).where(
                            MeetingJob.meeting_id == meeting.id,
                            MeetingJob.job_kind == MeetingJobKind.DELETE.value,
                        )
                    )
                ).first()
            if delete_job is None:
                delete_job = MeetingJob(
                    id=delete_job_id,
                    meeting_id=meeting.id,
                    job_kind=MeetingJobKind.DELETE.value,
                    idempotency_key=f"{meeting.id}:delete:v1",
                    state=MeetingJobState.RUNNING.value,
                    attempt=1,
                    max_attempts=3,
                    settings_version=max(1, meeting.settings_version),
                )
                session.add(delete_job)
                await session.flush()

            artifact_ids = list(
                (await session.exec(select(MeetingArtifact.id).where(MeetingArtifact.meeting_id == meeting.id))).all()
            )
            affected_candidates = list(
                (
                    await session.exec(
                        select(MeetingTermCandidateSource.candidate_id)
                        .where(MeetingTermCandidateSource.meeting_id == meeting.id)
                        .distinct()
                    )
                ).all()
            )
            await session.exec(
                delete(MeetingTermCandidateSource).where(MeetingTermCandidateSource.meeting_id == meeting.id)
            )
            await self._reconcile_candidates(session, affected_candidates)

            await session.exec(
                delete(MeetingLexiconEntry).where(
                    MeetingLexiconEntry.meeting_id == meeting.id,
                    MeetingLexiconEntry.scope == LexiconScope.CURRENT_MEETING.value,
                )
            )
            await session.exec(delete(MeetingLexiconVersion).where(MeetingLexiconVersion.meeting_id == meeting.id))
            await session.exec(
                update(MeetingLexiconEntry)
                .where(MeetingLexiconEntry.meeting_id == meeting.id)
                .values(meeting_id=None, updated_at=utcnow())
            )
            segment_ids = select(MeetingTranscriptSegment.id).where(MeetingTranscriptSegment.meeting_id == meeting.id)
            await session.exec(
                delete(MeetingSegmentRevision).where(col(MeetingSegmentRevision.segment_id).in_(segment_ids))
            )
            await session.exec(
                delete(MeetingASRCorrectionEvent).where(MeetingASRCorrectionEvent.meeting_id == meeting.id)
            )
            await session.exec(delete(MeetingVoiceprintMatch).where(MeetingVoiceprintMatch.meeting_id == meeting.id))
            await session.exec(
                delete(MeetingTranscriptSegment).where(MeetingTranscriptSegment.meeting_id == meeting.id)
            )
            await session.exec(delete(MeetingSpeakerTrack).where(MeetingSpeakerTrack.meeting_id == meeting.id))

            native_capture_ids = select(MeetingNativeCapture.id).where(MeetingNativeCapture.meeting_id == meeting.id)
            await session.exec(
                delete(MeetingNativeCaptureAudioLink).where(
                    col(MeetingNativeCaptureAudioLink.capture_id).in_(native_capture_ids)
                )
            )
            await session.exec(
                delete(MeetingNativeCaptureFinalization).where(
                    col(MeetingNativeCaptureFinalization.capture_id).in_(native_capture_ids)
                )
            )
            await session.exec(
                delete(MeetingNativeCaptureGap).where(col(MeetingNativeCaptureGap.capture_id).in_(native_capture_ids))
            )
            await session.exec(
                delete(MeetingNativeCaptureToken).where(
                    col(MeetingNativeCaptureToken.capture_id).in_(native_capture_ids)
                )
            )
            await session.exec(
                delete(MeetingNativeCaptureManifestEntry).where(
                    col(MeetingNativeCaptureManifestEntry.capture_id).in_(native_capture_ids)
                )
            )
            await session.exec(
                delete(MeetingNativeCaptureBatch).where(
                    col(MeetingNativeCaptureBatch.capture_id).in_(native_capture_ids)
                )
            )
            await session.exec(
                delete(MeetingNativeCaptureEpoch).where(
                    col(MeetingNativeCaptureEpoch.capture_id).in_(native_capture_ids)
                )
            )
            await session.exec(delete(MeetingNativeCapture).where(MeetingNativeCapture.meeting_id == meeting.id))
            await session.exec(delete(MeetingAudioChunk).where(MeetingAudioChunk.meeting_id == meeting.id))
            await session.exec(delete(MeetingStreamLease).where(MeetingStreamLease.meeting_id == meeting.id))
            await session.exec(delete(MeetingStreamTicket).where(MeetingStreamTicket.meeting_id == meeting.id))

            import_upload_ids = select(MeetingImportUpload.id).where(MeetingImportUpload.meeting_id == meeting.id)
            await session.exec(
                delete(MeetingImportChunk).where(col(MeetingImportChunk.upload_id).in_(import_upload_ids))
            )
            await session.exec(delete(MeetingImportUpload).where(MeetingImportUpload.meeting_id == meeting.id))

            await session.exec(
                update(MeetingArtifact)
                .where(MeetingArtifact.meeting_id == meeting.id)
                .values(supersedes_id=None, model_snapshot_id=None)
            )
            await session.exec(delete(MeetingArtifact).where(MeetingArtifact.meeting_id == meeting.id))
            await session.exec(
                delete(MeetingJob).where(MeetingJob.meeting_id == meeting.id, MeetingJob.id != delete_job.id)
            )
            delete_job.model_snapshot_id = None
            await session.exec(delete(MeetingModelSnapshot).where(MeetingModelSnapshot.meeting_id == meeting.id))
            await session.exec(delete(MeetingModelSetting).where(MeetingModelSetting.meeting_id == meeting.id))
            await session.exec(delete(MeetingEvent).where(MeetingEvent.meeting_id == meeting.id))

            idempotency_conditions = [
                MeetingIdempotencyRecord.resource_id == meeting.id,
                MeetingIdempotencyRecord.operation == f"exports.create:{meeting.id}",
            ]
            if artifact_ids:
                idempotency_conditions.append(col(MeetingIdempotencyRecord.resource_id).in_(artifact_ids))
            await session.exec(delete(MeetingIdempotencyRecord).where(or_(*idempotency_conditions)))

            deleted_at = tombstone.deleted_at
            meeting.title = "[deleted]"
            meeting.language = "und"
            meeting.state = MeetingState.DELETED.value
            meeting.postprocess_state = "not_started"
            meeting.audio_source = "microphone"
            meeting.voiceprint_enabled = False
            meeting.ai_enabled = False
            meeting.selection_mode = ModelSelectionMode.NONE.value
            meeting.requested_model_ref = None
            meeting.fallback_policy = ModelFallbackPolicy.DISABLED.value
            meeting.settings_version = 1
            meeting.version = 1
            meeting.stream_epoch = 0
            meeting.last_audio_sequence = -1
            meeting.last_segment_ordinal = 0
            meeting.active_lexicon_version = None
            meeting.started_at = None
            meeting.stopped_at = None
            meeting.created_at = deleted_at
            meeting.updated_at = deleted_at
            session.add(meeting)

            delete_job.job_kind = MeetingJobKind.DELETE.value
            delete_job.idempotency_key = f"{meeting.id}:delete:v1"
            delete_job.state = MeetingJobState.SUCCEEDED.value
            delete_job.lease_owner = None
            delete_job.lease_until = None
            delete_job.input_watermark = 0
            delete_job.settings_version = 1
            delete_job.input_json = "{}"
            delete_job.public_error_code = None
            delete_job.internal_diagnostic = None
            delete_job.updated_at = deleted_at
            session.add(delete_job)
            session.add(
                MeetingEvent(
                    meeting_id=meeting.id,
                    cursor=1,
                    event_type="session.deleted",
                    payload_json=json.dumps(
                        {
                            "schema_version": DELETION_AUDIT_SCHEMA,
                            "delete_job_id": delete_job.id,
                            "deleted_at": _format_time(deleted_at),
                            "policy_version": DELETION_POLICY_VERSION,
                            "tombstone_sequence": tombstone.sequence,
                            "tombstone_hmac": tombstone.entry_hmac,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    created_at=deleted_at,
                    published_at=deleted_at,
                )
            )
            await session.commit()

    async def _reconcile_candidates(self, session: AsyncSession, candidate_ids: Sequence[str]) -> None:
        for candidate_id in candidate_ids:
            remaining = list(
                (
                    await session.exec(
                        select(MeetingTermCandidateSource.meeting_id).where(
                            MeetingTermCandidateSource.candidate_id == candidate_id
                        )
                    )
                ).all()
            )
            if remaining:
                await session.exec(
                    update(MeetingTermCandidate)
                    .where(MeetingTermCandidate.id == candidate_id)
                    .values(
                        source_count=len(remaining),
                        distinct_meeting_count=len(set(remaining)),
                        updated_at=utcnow(),
                    )
                )
                continue
            await session.exec(
                update(MeetingLexiconEntry)
                .where(MeetingLexiconEntry.source_candidate_id == candidate_id)
                .values(source_candidate_id=None, updated_at=utcnow())
            )
            await session.exec(delete(MeetingTermCandidate).where(MeetingTermCandidate.id == candidate_id))

    async def _expire_one_orphan_native_audio(
        self,
        capture_id: str,
        meeting_id: str,
        owner_user_id: int,
        cutoff: datetime,
    ) -> bool:
        now = utcnow()
        async with self.session_factory() as session:
            await session.exec(select(User.id).where(User.id == owner_user_id).with_for_update())
            meeting = (
                await session.exec(
                    select(MeetingSession)
                    .where(
                        MeetingSession.id == meeting_id,
                        MeetingSession.owner_user_id == owner_user_id,
                        MeetingSession.state != MeetingState.DELETED.value,
                    )
                    .with_for_update()
                )
            ).first()
            if meeting is None:
                return False
            finalization = (
                await session.exec(
                    select(MeetingNativeCaptureFinalization)
                    .where(MeetingNativeCaptureFinalization.capture_id == capture_id)
                    .with_for_update()
                )
            ).first()
            capture = (
                await session.exec(
                    select(MeetingNativeCapture)
                    .where(
                        MeetingNativeCapture.id == capture_id,
                        MeetingNativeCapture.meeting_id == meeting.id,
                        MeetingNativeCapture.owner_user_id == owner_user_id,
                        MeetingNativeCapture.state.in_(
                            [NativeCaptureState.SEALED.value, NativeCaptureState.REVOKED.value]
                        ),
                    )
                    .with_for_update()
                )
            ).first()
            if capture is None or not _native_capture_terminal_before(capture, cutoff):
                return False
            has_batch = bool(
                (
                    await session.exec(
                        select(MeetingNativeCaptureBatch.id)
                        .where(MeetingNativeCaptureBatch.capture_id == capture.id)
                        .limit(1)
                    )
                ).first()
            )
            has_audio_link = bool(
                (
                    await session.exec(
                        select(MeetingNativeCaptureAudioLink.id)
                        .where(MeetingNativeCaptureAudioLink.capture_id == capture.id)
                        .limit(1)
                    )
                ).first()
            )
            active_upload = bool(
                (
                    await session.exec(
                        select(MeetingNativeCaptureToken.id)
                        .where(
                            MeetingNativeCaptureToken.capture_id == capture.id,
                            MeetingNativeCaptureToken.revoked_at.is_(None),
                            MeetingNativeCaptureToken.expires_at > now,
                        )
                        .limit(1)
                    )
                ).first()
            )
            active_meeting_work = bool(
                (
                    await session.exec(
                        select(MeetingJob.id)
                        .where(
                            MeetingJob.meeting_id == meeting.id,
                            MeetingJob.job_kind != MeetingJobKind.DELETE.value,
                            MeetingJob.state.in_(list(_ACTIVE_JOB_STATES)),
                        )
                        .limit(1)
                    )
                ).first()
            )
            if (
                not has_batch
                or has_audio_link
                or active_upload
                or active_meeting_work
                or _native_finalization_is_active(finalization, now)
            ):
                return False

            if finalization is not None:
                finalization.state = NativeCaptureFinalizationState.FAILED.value
                finalization.lease_owner = None
                finalization.lease_until = None
                finalization.retry_after = None
                finalization.public_error_code = "MEETING_AUDIO_RETENTION_EXPIRED"
                finalization.internal_diagnostic = None
                finalization.updated_at = now
                session.add(finalization)
            capture.state = NativeCaptureState.REVOKED.value
            capture.revoked_at = now
            capture.ingest_complete = False
            capture.server_playback_state = "not_ready"
            capture.updated_at = now
            session.add(capture)
            await session.exec(
                update(MeetingNativeCaptureToken)
                .where(MeetingNativeCaptureToken.capture_id == capture.id)
                .values(revoked_at=now)
            )
            await session.commit()

        if not await self._native_audio_is_quiesced(meeting_id, (capture_id,)):
            return False
        await asyncio.to_thread(
            self.purger.purge_native_captures,
            owner_user_id,
            (capture_id,),
        )

        async with self.session_factory() as session:
            await session.exec(select(User.id).where(User.id == owner_user_id).with_for_update())
            meeting = (
                await session.exec(
                    select(MeetingSession)
                    .where(
                        MeetingSession.id == meeting_id,
                        MeetingSession.owner_user_id == owner_user_id,
                        MeetingSession.state != MeetingState.DELETED.value,
                    )
                    .with_for_update()
                )
            ).first()
            finalization = (
                await session.exec(
                    select(MeetingNativeCaptureFinalization)
                    .where(MeetingNativeCaptureFinalization.capture_id == capture_id)
                    .with_for_update()
                )
            ).first()
            capture = (
                await session.exec(
                    select(MeetingNativeCapture)
                    .where(
                        MeetingNativeCapture.id == capture_id,
                        MeetingNativeCapture.meeting_id == meeting_id,
                        MeetingNativeCapture.owner_user_id == owner_user_id,
                        MeetingNativeCapture.state == NativeCaptureState.REVOKED.value,
                    )
                    .with_for_update()
                )
            ).first()
            if meeting is None or capture is None:
                return False
            await session.exec(
                delete(MeetingNativeCaptureAudioLink).where(MeetingNativeCaptureAudioLink.capture_id == capture.id)
            )
            removed = await session.exec(
                delete(MeetingNativeCaptureBatch).where(MeetingNativeCaptureBatch.capture_id == capture.id)
            )
            await session.exec(
                delete(MeetingNativeCaptureToken).where(MeetingNativeCaptureToken.capture_id == capture.id)
            )
            if finalization is not None:
                finalization.wav_sha256 = None
                finalization.wav_byte_size = None
                finalization.audio_chunk_count = 0
                finalization.ready_at = None
                finalization.updated_at = utcnow()
                session.add(finalization)
            if not removed.rowcount:
                await session.rollback()
                return False
            capture.total_bytes = 0
            capture.total_samples = 0
            capture.updated_at = utcnow()
            session.add(capture)
            cursor = int(
                (
                    await session.exec(
                        select(func.max(MeetingEvent.cursor)).where(MeetingEvent.meeting_id == meeting.id)
                    )
                ).one()
                or 0
            )
            session.add(
                MeetingEvent(
                    meeting_id=meeting.id,
                    cursor=cursor + 1,
                    event_type="native_capture.audio_retention.expired",
                    payload_json=json.dumps(
                        {
                            "schema_version": "siq.meeting.native_capture.audio_retention.v1",
                            "capture_id": capture.id,
                            "retention_days": self.settings.audio_retention_days,
                            "expired_at": _format_time(utcnow()),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
            )
            await session.commit()
            return True

    async def _expire_one_audio(self, meeting_id: str, owner_user_id: int, cutoff: datetime) -> bool:
        native_capture_ids: tuple[str, ...] = ()
        has_chunks = False
        has_native_batches = False
        async with self.session_factory() as session:
            await session.exec(select(User.id).where(User.id == owner_user_id).with_for_update())
            meeting = (
                await session.exec(select(MeetingSession).where(MeetingSession.id == meeting_id).with_for_update())
            ).first()
            if (
                meeting is None
                or meeting.owner_user_id != owner_user_id
                or meeting.state
                not in {
                    MeetingState.STOPPED.value,
                    MeetingState.ARCHIVED.value,
                    MeetingState.INTERRUPTED.value,
                }
                or meeting.stopped_at is None
                or _aware(meeting.stopped_at) > _aware(cutoff)
            ):
                return False
            has_chunks = bool(
                (
                    await session.exec(
                        select(MeetingAudioChunk.id).where(MeetingAudioChunk.meeting_id == meeting.id).limit(1)
                    )
                ).first()
            )
            has_native_batches = bool(
                (
                    await session.exec(
                        select(MeetingNativeCaptureBatch.id)
                        .where(MeetingNativeCaptureBatch.meeting_id == meeting.id)
                        .limit(1)
                    )
                ).first()
            )
            active_meeting_work = bool(
                (
                    await session.exec(
                        select(MeetingJob.id)
                        .where(
                            MeetingJob.meeting_id == meeting.id,
                            MeetingJob.job_kind != MeetingJobKind.DELETE.value,
                            MeetingJob.state.in_(list(_ACTIVE_JOB_STATES)),
                        )
                        .limit(1)
                    )
                ).first()
            )
            if (not has_chunks and not has_native_batches) or active_meeting_work:
                return False
            native_capture_ids = tuple(
                str(value)
                for value in (
                    await session.exec(
                        select(MeetingNativeCapture.id).where(MeetingNativeCapture.meeting_id == meeting.id)
                    )
                ).all()
            )
            if native_capture_ids:
                expired_at = utcnow()
                await session.exec(
                    update(MeetingNativeCaptureFinalization)
                    .where(col(MeetingNativeCaptureFinalization.capture_id).in_(native_capture_ids))
                    .values(
                        state=NativeCaptureFinalizationState.FAILED.value,
                        lease_owner=None,
                        lease_until=None,
                        retry_after=None,
                        public_error_code="MEETING_AUDIO_RETENTION_EXPIRED",
                        internal_diagnostic=None,
                        updated_at=expired_at,
                    )
                )
                await session.exec(
                    update(MeetingNativeCapture)
                    .where(col(MeetingNativeCapture.id).in_(native_capture_ids))
                    .values(
                        state="revoked",
                        revoked_at=expired_at,
                        ingest_complete=False,
                        server_playback_state="not_ready",
                        updated_at=expired_at,
                    )
                )
                await session.exec(
                    update(MeetingNativeCaptureToken)
                    .where(col(MeetingNativeCaptureToken.capture_id).in_(native_capture_ids))
                    .values(revoked_at=expired_at)
                )
                await session.commit()
        if native_capture_ids and not await self._native_audio_is_quiesced(
            meeting_id,
            native_capture_ids,
        ):
            return False
        await asyncio.to_thread(self.purger.expire_audio, owner_user_id, meeting_id)
        await asyncio.to_thread(
            self.purger.purge_native_captures,
            owner_user_id,
            native_capture_ids,
        )
        async with self.session_factory() as session:
            await session.exec(select(User.id).where(User.id == owner_user_id).with_for_update())
            meeting = (
                await session.exec(select(MeetingSession).where(MeetingSession.id == meeting_id).with_for_update())
            ).first()
            if meeting is None or meeting.state == MeetingState.DELETED.value:
                return False
            capture_ids = select(MeetingNativeCapture.id).where(MeetingNativeCapture.meeting_id == meeting_id)
            await session.exec(
                update(MeetingNativeCaptureFinalization)
                .where(col(MeetingNativeCaptureFinalization.capture_id).in_(capture_ids))
                .values(
                    wav_sha256=None,
                    wav_byte_size=None,
                    audio_chunk_count=0,
                    ready_at=None,
                    updated_at=utcnow(),
                )
            )
            expired_at = utcnow()
            await session.exec(
                update(MeetingNativeCapture)
                .where(MeetingNativeCapture.meeting_id == meeting_id)
                .values(
                    state="revoked",
                    revoked_at=expired_at,
                    total_bytes=0,
                    total_samples=0,
                    server_playback_state="not_ready",
                    updated_at=expired_at,
                )
            )
            await session.exec(
                delete(MeetingNativeCaptureAudioLink).where(
                    col(MeetingNativeCaptureAudioLink.capture_id).in_(capture_ids)
                )
            )
            await session.exec(
                delete(MeetingNativeCaptureBatch).where(col(MeetingNativeCaptureBatch.capture_id).in_(capture_ids))
            )
            await session.exec(
                delete(MeetingNativeCaptureToken).where(col(MeetingNativeCaptureToken.capture_id).in_(capture_ids))
            )
            removed = await session.exec(delete(MeetingAudioChunk).where(MeetingAudioChunk.meeting_id == meeting_id))
            if not removed.rowcount and not has_native_batches:
                await session.rollback()
                return False
            meeting.last_audio_sequence = -1
            meeting.updated_at = utcnow()
            session.add(meeting)
            cursor = int(
                (
                    await session.exec(
                        select(func.max(MeetingEvent.cursor)).where(MeetingEvent.meeting_id == meeting_id)
                    )
                ).one()
                or 0
            )
            session.add(
                MeetingEvent(
                    meeting_id=meeting_id,
                    cursor=cursor + 1,
                    event_type="audio.retention.expired",
                    payload_json=json.dumps(
                        {
                            "schema_version": "siq.meeting.audio_retention.v1",
                            "retention_days": self.settings.audio_retention_days,
                            "expired_at": _format_time(utcnow()),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
            )
            await session.commit()
            return True

    async def _native_audio_is_quiesced(
        self,
        meeting_id: str,
        capture_ids: Sequence[str],
    ) -> bool:
        """Confirm the durable fence before deleting native files.

        A worker that held a finalization row lock before retention started may
        finish its current registration first.  This check runs only after the
        revocation transaction commits, so no old attempt can publish or
        reconstruct files past the physical purge boundary.
        """

        if not capture_ids:
            return True
        async with self.session_factory() as session:
            captures = list(
                (
                    await session.exec(
                        select(MeetingNativeCapture).where(
                            MeetingNativeCapture.meeting_id == meeting_id,
                            col(MeetingNativeCapture.id).in_(capture_ids),
                        )
                    )
                ).all()
            )
            finalizations = list(
                (
                    await session.exec(
                        select(MeetingNativeCaptureFinalization).where(
                            MeetingNativeCaptureFinalization.meeting_id == meeting_id,
                            col(MeetingNativeCaptureFinalization.capture_id).in_(capture_ids),
                        )
                    )
                ).all()
            )
        return (
            len(captures) == len(capture_ids)
            and all(capture.state == "revoked" and capture.server_playback_state == "not_ready" for capture in captures)
            and all(
                value.state == NativeCaptureFinalizationState.FAILED.value
                and value.lease_owner is None
                and value.lease_until is None
                for value in finalizations
            )
        )

    async def _count_residuals(self, tombstones: Sequence[MeetingDeletionTombstone]) -> int:
        residual = 0
        async with self.session_factory() as session:
            for entry in tombstones:
                meeting = await session.get(MeetingSession, entry.meeting_id)
                if meeting is None:
                    continue
                if meeting.owner_user_id != entry.owner_user_id or not _is_scrubbed_session(meeting):
                    residual += 1
                    continue
                counts = await _sensitive_row_counts(session, meeting.id)
                audit_valid = await _minimal_audit_valid(session, meeting.id, entry)
                if any(counts.values()) or not audit_valid:
                    residual += 1
        return residual

    async def _lease_heartbeat(self, job_id: str) -> bool:
        interval = max(5.0, self.settings.lease_seconds / 3)
        try:
            while True:
                await asyncio.sleep(interval)
                now = utcnow()
                async with self.session_factory() as session:
                    result = await session.exec(
                        update(MeetingJob)
                        .where(
                            MeetingJob.id == job_id,
                            MeetingJob.state == MeetingJobState.RUNNING.value,
                            MeetingJob.lease_owner == self.worker_id,
                        )
                        .values(
                            lease_until=now + timedelta(seconds=self.settings.lease_seconds),
                            updated_at=now,
                        )
                    )
                    await session.commit()
                    if not result.rowcount:
                        return False
        except asyncio.CancelledError:
            return True

    async def _delete_job_succeeded(self, job_id: str) -> bool:
        async with self.session_factory() as session:
            job = await session.get(MeetingJob, job_id)
            return bool(job is not None and job.state == MeetingJobState.SUCCEEDED.value)

    async def _fail_job(self, job_id: str, exc: BaseException) -> None:
        code = exc.code if isinstance(exc, MeetingRetentionError) else "DELETE_WORKER_FAILED"
        retryable = not isinstance(exc, MeetingRetentionError) or exc.retryable
        async with self.session_factory() as session:
            job = (
                await session.exec(
                    select(MeetingJob).where(
                        MeetingJob.id == job_id,
                        MeetingJob.lease_owner == self.worker_id,
                        MeetingJob.state.in_([MeetingJobState.LEASED.value, MeetingJobState.RUNNING.value]),
                    )
                )
            ).first()
            if job is None:
                return
            will_retry = retryable and job.attempt < job.max_attempts
            job.state = MeetingJobState.RETRY_WAIT.value if will_retry else MeetingJobState.FAILED.value
            job.lease_owner = None
            job.lease_until = utcnow() + timedelta(seconds=self.settings.retry_delay_seconds) if will_retry else None
            job.public_error_code = code[:64]
            job.internal_diagnostic = type(exc).__name__[:200]
            job.updated_at = utcnow()
            session.add(job)
            await session.commit()


async def _sensitive_row_counts(session: AsyncSession, meeting_id: str) -> dict[str, int]:
    models = (
        MeetingStreamLease,
        MeetingAudioChunk,
        MeetingSpeakerTrack,
        MeetingTranscriptSegment,
        MeetingASRCorrectionEvent,
        MeetingVoiceprintMatch,
        MeetingModelSetting,
        MeetingModelSnapshot,
        MeetingArtifact,
        MeetingStreamTicket,
        MeetingNativeCapture,
        MeetingNativeCaptureBatch,
        MeetingNativeCaptureToken,
        MeetingNativeCaptureManifestEntry,
        MeetingNativeCaptureGap,
        MeetingNativeCaptureFinalization,
        MeetingNativeCaptureAudioLink,
    )
    values: dict[str, int] = {}
    for model in models:
        values[model.__tablename__] = int(
            (await session.exec(select(func.count()).select_from(model).where(model.meeting_id == meeting_id))).one()
        )
    capture_ids = select(MeetingNativeCapture.id).where(MeetingNativeCapture.meeting_id == meeting_id)
    values[MeetingNativeCaptureEpoch.__tablename__] = int(
        (
            await session.exec(
                select(func.count())
                .select_from(MeetingNativeCaptureEpoch)
                .where(col(MeetingNativeCaptureEpoch.capture_id).in_(capture_ids))
            )
        ).one()
    )
    values["meeting_jobs_non_delete"] = int(
        (
            await session.exec(
                select(func.count())
                .select_from(MeetingJob)
                .where(
                    MeetingJob.meeting_id == meeting_id,
                    MeetingJob.job_kind != MeetingJobKind.DELETE.value,
                )
            )
        ).one()
    )
    values["meeting_events_non_audit"] = int(
        (
            await session.exec(
                select(func.count())
                .select_from(MeetingEvent)
                .where(
                    MeetingEvent.meeting_id == meeting_id,
                    MeetingEvent.event_type != "session.deleted",
                )
            )
        ).one()
    )
    values["meeting_term_candidate_sources"] = int(
        (
            await session.exec(
                select(func.count())
                .select_from(MeetingTermCandidateSource)
                .where(MeetingTermCandidateSource.meeting_id == meeting_id)
            )
        ).one()
    )
    values["meeting_current_lexicon_entries"] = int(
        (
            await session.exec(
                select(func.count())
                .select_from(MeetingLexiconEntry)
                .where(
                    MeetingLexiconEntry.meeting_id == meeting_id,
                    MeetingLexiconEntry.scope == LexiconScope.CURRENT_MEETING.value,
                )
            )
        ).one()
    )
    values["meeting_lexicon_versions"] = int(
        (
            await session.exec(
                select(func.count())
                .select_from(MeetingLexiconVersion)
                .where(MeetingLexiconVersion.meeting_id == meeting_id)
            )
        ).one()
    )
    values["meeting_idempotency_records"] = int(
        (
            await session.exec(
                select(func.count())
                .select_from(MeetingIdempotencyRecord)
                .where(
                    or_(
                        MeetingIdempotencyRecord.resource_id == meeting_id,
                        MeetingIdempotencyRecord.operation == f"exports.create:{meeting_id}",
                    )
                )
            )
        ).one()
    )
    values["meeting_import_uploads"] = int(
        (
            await session.exec(
                select(func.count())
                .select_from(MeetingImportUpload)
                .where(MeetingImportUpload.meeting_id == meeting_id)
            )
        ).one()
    )
    import_upload_ids = select(MeetingImportUpload.id).where(MeetingImportUpload.meeting_id == meeting_id)
    values["meeting_import_chunks"] = int(
        (
            await session.exec(
                select(func.count())
                .select_from(MeetingImportChunk)
                .where(col(MeetingImportChunk.upload_id).in_(import_upload_ids))
            )
        ).one()
    )
    return values


async def _minimal_audit_valid(
    session: AsyncSession,
    meeting_id: str,
    tombstone: MeetingDeletionTombstone,
) -> bool:
    jobs = list((await session.exec(select(MeetingJob).where(MeetingJob.meeting_id == meeting_id))).all())
    events = list((await session.exec(select(MeetingEvent).where(MeetingEvent.meeting_id == meeting_id))).all())
    if len(jobs) != 1 or len(events) != 1:
        return False
    job = jobs[0]
    event = events[0]
    if (
        job.job_kind != MeetingJobKind.DELETE.value
        or job.state != MeetingJobState.SUCCEEDED.value
        or job.model_snapshot_id is not None
        or job.input_json != "{}"
        or job.public_error_code is not None
        or job.internal_diagnostic is not None
        or event.event_type != "session.deleted"
        or event.cursor != 1
    ):
        return False
    try:
        payload = json.loads(event.payload_json)
    except (TypeError, json.JSONDecodeError):
        return False
    return payload == {
        "schema_version": DELETION_AUDIT_SCHEMA,
        "delete_job_id": job.id,
        "deleted_at": _format_time(tombstone.deleted_at),
        "policy_version": DELETION_POLICY_VERSION,
        "tombstone_sequence": tombstone.sequence,
        "tombstone_hmac": tombstone.entry_hmac,
    }


def _is_scrubbed_session(meeting: MeetingSession) -> bool:
    return (
        meeting.state == MeetingState.DELETED.value
        and meeting.title == "[deleted]"
        and meeting.language == "und"
        and not meeting.ai_enabled
        and not meeting.voiceprint_enabled
        and meeting.requested_model_ref is None
        and meeting.last_audio_sequence == -1
        and meeting.last_segment_ordinal == 0
        and meeting.started_at is None
        and meeting.stopped_at is None
    )


def _native_capture_terminal_before(capture: MeetingNativeCapture, cutoff: datetime) -> bool:
    if capture.state == NativeCaptureState.SEALED.value:
        terminal_at = capture.sealed_at
    elif capture.state == NativeCaptureState.REVOKED.value:
        values = [value for value in (capture.sealed_at, capture.revoked_at) if value is not None]
        terminal_at = min(values, key=_aware) if values else None
    else:
        return False
    return terminal_at is not None and _aware(terminal_at) <= _aware(cutoff)


def _native_finalization_is_active(
    value: MeetingNativeCaptureFinalization | None,
    now: datetime,
) -> bool:
    if value is None:
        return False
    if value.state == NativeCaptureFinalizationState.PROCESSING.value:
        return value.lease_until is not None and _aware(value.lease_until) > _aware(now)
    if value.state in {
        NativeCaptureFinalizationState.QUEUED.value,
        NativeCaptureFinalizationState.RETRY_WAIT.value,
    }:
        return value.attempt < value.max_attempts
    return value.state == NativeCaptureFinalizationState.READY.value


async def _finish_heartbeat(task: asyncio.Task[bool]) -> bool:
    if not task.done():
        task.cancel()
    try:
        return await task
    except asyncio.CancelledError:
        return True


def _remove_controlled_tree(path: Path) -> int:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return 0
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        path.unlink(missing_ok=True)
        return 1
    count = 1
    try:
        for child in path.iterdir():
            count += _remove_controlled_tree(child)
        path.rmdir()
    except OSError:
        # shutil's fd-aware implementation handles permissions and directory
        # churn on supported platforms without following nested symlinks.
        try:
            shutil.rmtree(path)
        except OSError as nested:
            raise MeetingRetentionError(
                "DELETE_STORAGE_UNAVAILABLE", "meeting storage could not be removed"
            ) from nested
    return count


def _safe_component(value: str) -> str:
    if not _SAFE_COMPONENT.fullmatch(value):
        raise MeetingRetentionError(
            "DELETE_STORAGE_ID_INVALID", "meeting storage identifier is invalid", retryable=False
        )
    return value


def _canonical_json(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _format_time(value: datetime) -> str:
    aware = _aware(value).astimezone(timezone.utc)
    return aware.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    if not value.endswith("Z"):
        raise ValueError("timestamp must use UTC Z notation")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    return parsed.astimezone(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _validate_tombstone_values(
    owner_user_id: int,
    meeting_id: str,
    delete_job_id: str,
    deleted_at: datetime,
    reason: str,
) -> tuple[int, str, str, str, str]:
    if type(owner_user_id) is not int or owner_user_id <= 0:
        raise MeetingDeletionLedgerError(
            "DELETE_LEDGER_ENTRY_INVALID", "meeting deletion owner is invalid", retryable=False
        )
    try:
        meeting = str(UUID(meeting_id))
        job = str(UUID(delete_job_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise MeetingDeletionLedgerError(
            "DELETE_LEDGER_ENTRY_INVALID", "meeting deletion identity is invalid", retryable=False
        ) from exc
    normalized_reason = reason.strip()
    if normalized_reason not in {"user_requested", "restore_replay"}:
        raise MeetingDeletionLedgerError(
            "DELETE_LEDGER_ENTRY_INVALID", "meeting deletion reason is invalid", retryable=False
        )
    return owner_user_id, meeting, job, _format_time(deleted_at), normalized_reason


def _tombstone_from_payload(payload: dict[str, object]) -> MeetingDeletionTombstone:
    try:
        if (
            payload["schema_version"] != DELETION_TOMBSTONE_SCHEMA
            or type(payload["sequence"]) is not int
            or not isinstance(payload["owner_user_id"], int)
            or not isinstance(payload["meeting_id"], str)
            or not isinstance(payload["delete_job_id"], str)
            or not isinstance(payload["deleted_at"], str)
            or not isinstance(payload["reason"], str)
            or not isinstance(payload["previous_hmac"], str)
            or not isinstance(payload["hmac"], str)
        ):
            raise ValueError
        owner, meeting, job, timestamp, reason = _validate_tombstone_values(
            payload["owner_user_id"],
            payload["meeting_id"],
            payload["delete_job_id"],
            _parse_time(payload["deleted_at"]),
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
        raise MeetingDeletionLedgerError(
            "DELETE_LEDGER_INTEGRITY_FAILED",
            "meeting deletion ledger entry is invalid",
            retryable=False,
        ) from exc
    return MeetingDeletionTombstone(
        sequence=sequence,
        owner_user_id=owner,
        meeting_id=meeting,
        delete_job_id=job,
        deleted_at=_parse_time(timestamp),
        reason=reason,
        previous_hmac=previous_hmac,
        entry_hmac=entry_hmac,
    )


def _decode_hmac_key(value: str) -> bytes:
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(value + padding)
    except (ValueError, binascii.Error) as exc:
        raise MeetingDeletionLedgerError(
            "DELETE_LEDGER_CONFIGURATION_INVALID",
            "meeting deletion HMAC key is not valid base64url",
            retryable=False,
        ) from exc
    if len(decoded) != 32:
        raise MeetingDeletionLedgerError(
            "DELETE_LEDGER_CONFIGURATION_INVALID",
            "meeting deletion HMAC key must decode to 32 bytes",
            retryable=False,
        )
    return decoded


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _close_quietly(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise MeetingRetentionError("DELETE_CONFIGURATION_INVALID", f"{name} must be a boolean", retryable=False)


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise MeetingRetentionError(
            "DELETE_CONFIGURATION_INVALID", f"{name} must be an integer", retryable=False
        ) from exc
    if value < minimum or value > maximum:
        raise MeetingRetentionError(
            "DELETE_CONFIGURATION_INVALID",
            f"{name} must be between {minimum} and {maximum}",
            retryable=False,
        )
    return value


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise MeetingRetentionError("DELETE_CONFIGURATION_INVALID", f"{name} must be numeric", retryable=False) from exc
    if value < minimum or value > maximum:
        raise MeetingRetentionError(
            "DELETE_CONFIGURATION_INVALID",
            f"{name} must be between {minimum} and {maximum}",
            retryable=False,
        )
    return value
