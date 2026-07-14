"""Durable, cross-process leases for IC task execution."""

from __future__ import annotations

import fcntl
import json
import os
import stat
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional
from weakref import WeakValueDictionary

from sqlalchemy import UniqueConstraint, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field, Session, SQLModel, select

ACTIVE_STATUSES = {"queued", "running"}
TERMINAL_STATUSES = {
    "succeeded",
    "failed",
    "cancelled",
    "interrupted",
    "timed_out",
    "stale_on_completion",
}
STORE_SCHEMA = "siq_ic_task_leases_v1"


class ICTaskAlreadyClaimedError(RuntimeError):
    """Raised when an unexpired lease already owns the requested IC task."""

    def __init__(self, claim: dict[str, Any]) -> None:
        self.claim = dict(claim)
        super().__init__(
            "IC task is already running "
            f"(attempt={claim.get('attempt')}, lease_expires_at={claim.get('lease_expires_at')})"
        )


class ICTaskOwnerReuseError(RuntimeError):
    """Raised when reclaim tries to reuse an expired execution capability."""


class ICTaskLeaseRecord(SQLModel, table=True):
    __tablename__ = "ic_task_leases"
    __table_args__ = (
        UniqueConstraint("scope_key", "task_key", name="uq_ic_task_lease_scope_task"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    scope_key: str = Field(max_length=500, index=True)
    task_key: str = Field(max_length=500, index=True)
    status: str = Field(default="running", max_length=24, index=True)
    owner: str = Field(max_length=255, index=True)
    attempt: int = Field(default=1)
    claimed_at: datetime
    heartbeat_at: datetime
    lease_until: Optional[datetime] = Field(default=None, index=True)
    finished_at: Optional[datetime] = Field(default=None)
    failure_reason: Optional[str] = Field(default=None, max_length=500)
    recovery_reason: Optional[str] = Field(default=None, max_length=120)
    history_json: str = Field(default="[]")
    updated_at: datetime


def _datetime_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _naive_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _history_load(value: str | None) -> list[dict[str, Any]]:
    try:
        payload = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [dict(item) for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


class PostgresICTaskLeaseStore:
    """Database authority for IC leases; production factory restricts it to PostgreSQL."""

    def __init__(self, *, session_factory: Callable[[], Session]):
        self._session_factory = session_factory

    @staticmethod
    def _snapshot(row: ICTaskLeaseRecord, *, include_history: bool = True) -> dict[str, Any]:
        snapshot = {
            "task_key": row.task_key,
            "status": row.status,
            "owner": row.owner,
            "attempt": row.attempt,
            "claimed_at": _datetime_iso(row.claimed_at),
            "heartbeat_at": _datetime_iso(row.heartbeat_at),
            "lease_expires_at": _datetime_iso(row.lease_until),
            "finished_at": _datetime_iso(row.finished_at),
            "failure_reason": row.failure_reason,
            "recovery_reason": row.recovery_reason,
        }
        if include_history:
            snapshot["history"] = _history_load(row.history_json)
        return snapshot

    def _get_row(self, session: Session, *, scope_key: str, task_key: str) -> ICTaskLeaseRecord | None:
        return session.exec(
            select(ICTaskLeaseRecord).where(
                ICTaskLeaseRecord.scope_key == scope_key,
                ICTaskLeaseRecord.task_key == task_key,
            )
        ).first()

    def _get(self, *, scope_key: str, task_key: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = self._get_row(session, scope_key=scope_key, task_key=task_key)
            return self._snapshot(row) if row else None

    def load(self, scope_key: str) -> dict[str, dict[str, Any]]:
        with self._session_factory() as session:
            rows = session.exec(
                select(ICTaskLeaseRecord).where(ICTaskLeaseRecord.scope_key == scope_key)
            ).all()
            return {row.task_key: self._snapshot(row) for row in rows}

    def claim(
        self,
        *,
        scope_key: str,
        task_key: str,
        owner: str,
        now: str,
        lease_seconds: int,
    ) -> dict[str, Any]:
        normalized_key = str(task_key or "").strip()
        normalized_owner = str(owner or "").strip()
        if not normalized_key or not normalized_owner:
            raise ValueError("IC task claim requires task_key and owner")
        parsed_now = _parse_timestamp(now)
        if parsed_now is None:
            raise ValueError(f"invalid IC task claim timestamp: {now!r}")
        timestamp = _naive_utc(parsed_now)
        expires = timestamp + timedelta(seconds=max(1, lease_seconds))

        for _attempt in range(4):
            with self._session_factory() as session:
                previous = self._get_row(session, scope_key=scope_key, task_key=normalized_key)
                if previous is None:
                    row = ICTaskLeaseRecord(
                        scope_key=scope_key,
                        task_key=normalized_key,
                        status="running",
                        owner=normalized_owner,
                        attempt=1,
                        claimed_at=timestamp,
                        heartbeat_at=timestamp,
                        lease_until=expires,
                        updated_at=timestamp,
                    )
                    session.add(row)
                    try:
                        session.commit()
                    except IntegrityError:
                        session.rollback()
                        continue
                    session.refresh(row)
                    return self._snapshot(row)

                is_active = previous.status in ACTIVE_STATUSES
                if is_active and previous.lease_until is not None and previous.lease_until > timestamp:
                    raise ICTaskAlreadyClaimedError(self._snapshot(previous))
                if is_active and previous.owner == normalized_owner:
                    raise ICTaskOwnerReuseError(
                        "IC task reclaim requires a fresh owner after lease expiry"
                    )

                history = _history_load(previous.history_json)
                history.append(self._snapshot(previous, include_history=False))
                recovery_reason = "lease_expired" if is_active else None
                statement = (
                    update(ICTaskLeaseRecord)
                    .where(
                        ICTaskLeaseRecord.id == previous.id,
                        ICTaskLeaseRecord.attempt == previous.attempt,
                        ICTaskLeaseRecord.status == previous.status,
                        ICTaskLeaseRecord.owner == previous.owner,
                        ICTaskLeaseRecord.lease_until == previous.lease_until,
                        ICTaskLeaseRecord.updated_at == previous.updated_at,
                    )
                    .values(
                        status="running",
                        owner=normalized_owner,
                        attempt=previous.attempt + 1,
                        claimed_at=timestamp,
                        heartbeat_at=timestamp,
                        lease_until=expires,
                        finished_at=None,
                        failure_reason=None,
                        recovery_reason=recovery_reason,
                        history_json=json.dumps(history, ensure_ascii=False, separators=(",", ":")),
                        updated_at=timestamp,
                    )
                )
                changed = session.exec(statement).rowcount == 1
                session.commit()
                if changed:
                    claimed = self._get(scope_key=scope_key, task_key=normalized_key)
                    if claimed is not None:
                        return claimed

        current = self._get(scope_key=scope_key, task_key=normalized_key)
        if current is not None:
            raise ICTaskAlreadyClaimedError(current)
        raise RuntimeError("IC task claim could not be persisted")

    def heartbeat(
        self,
        *,
        scope_key: str,
        task_key: str,
        owner: str,
        now: str,
        lease_seconds: int,
    ) -> dict[str, Any] | None:
        parsed_now = _parse_timestamp(now)
        if parsed_now is None:
            raise ValueError(f"invalid IC task heartbeat timestamp: {now!r}")
        timestamp = _naive_utc(parsed_now)
        statement = (
            update(ICTaskLeaseRecord)
            .where(
                ICTaskLeaseRecord.scope_key == scope_key,
                ICTaskLeaseRecord.task_key == task_key,
                ICTaskLeaseRecord.owner == owner,
                ICTaskLeaseRecord.status.in_(ACTIVE_STATUSES),
                ICTaskLeaseRecord.lease_until > timestamp,
            )
            .values(
                heartbeat_at=timestamp,
                lease_until=timestamp + timedelta(seconds=max(1, lease_seconds)),
                updated_at=timestamp,
            )
        )
        with self._session_factory() as session:
            changed = session.exec(statement).rowcount == 1
            session.commit()
        return self._get(scope_key=scope_key, task_key=task_key) if changed else None

    def finish(
        self,
        *,
        scope_key: str,
        task_key: str,
        owner: str,
        now: str,
        status: str,
        failure_reason: str | None = None,
    ) -> dict[str, Any] | None:
        normalized_status = str(status or "").strip()
        if normalized_status not in TERMINAL_STATUSES:
            raise ValueError(f"invalid IC task terminal status: {status!r}")
        parsed_now = _parse_timestamp(now)
        if parsed_now is None:
            raise ValueError(f"invalid IC task terminal timestamp: {now!r}")
        timestamp = _naive_utc(parsed_now)
        statement = (
            update(ICTaskLeaseRecord)
            .where(
                ICTaskLeaseRecord.scope_key == scope_key,
                ICTaskLeaseRecord.task_key == task_key,
                ICTaskLeaseRecord.owner == owner,
                ICTaskLeaseRecord.status.in_(ACTIVE_STATUSES),
                ICTaskLeaseRecord.lease_until > timestamp,
            )
            .values(
                status=normalized_status,
                heartbeat_at=timestamp,
                lease_until=None,
                finished_at=timestamp,
                failure_reason=str(failure_reason or "")[:500] or None,
                updated_at=timestamp,
            )
        )
        with self._session_factory() as session:
            changed = session.exec(statement).rowcount == 1
            session.commit()
        return self._get(scope_key=scope_key, task_key=task_key) if changed else None


_path_locks: WeakValueDictionary[str, threading.RLock] = WeakValueDictionary()
_path_locks_guard = threading.Lock()


def _thread_lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _path_locks_guard:
        return _path_locks.setdefault(key, threading.RLock())


@contextmanager
def _locked_store(store_path: Path) -> Iterator[None]:
    store_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = store_path.with_name(f".{store_path.name}.lock")
    with _thread_lock_for(store_path), lock_path.open("a+b") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _lease_expiry(now: str, lease_seconds: int) -> str:
    timestamp = _parse_timestamp(now)
    if timestamp is None:
        raise ValueError(f"invalid IC task lease timestamp: {now!r}")
    expires = timestamp + timedelta(seconds=max(1, lease_seconds))
    return expires.isoformat().replace("+00:00", "Z")


def _read_claims(store_path: Path) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(store_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(payload, dict):
        raise ValueError("IC task lease store must contain a JSON object")
    raw_claims = payload.get("claims")
    if not isinstance(raw_claims, list):
        return {}
    claims: dict[str, dict[str, Any]] = {}
    for claim in raw_claims:
        if not isinstance(claim, dict):
            continue
        task_key = str(claim.get("task_key") or "").strip()
        if task_key:
            claims[task_key] = claim
    return claims


def _atomic_write(store_path: Path, claims: dict[str, dict[str, Any]]) -> None:
    temp_path: Path | None = None
    try:
        target_mode = stat.S_IMODE(store_path.stat().st_mode)
    except FileNotFoundError:
        target_mode = 0o640
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=store_path.parent,
            prefix=f".{store_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(
                {"schema_version": STORE_SCHEMA, "claims": list(claims.values())},
                handle,
                ensure_ascii=False,
                indent=2,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, target_mode)
        os.replace(temp_path, store_path)
        temp_path = None
        directory_fd = os.open(store_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _load_file_ic_task_claims(store_path: Path) -> dict[str, dict[str, Any]]:
    """Load claim snapshots for status and audit APIs."""

    with _locked_store(store_path):
        return {task_key: dict(claim) for task_key, claim in _read_claims(store_path).items()}


def _claim_file_ic_task(
    store_path: Path,
    *,
    task_key: str,
    owner: str,
    now: str,
    lease_seconds: int,
) -> dict[str, Any]:
    """Atomically claim a task or reclaim it after its lease expires."""

    normalized_key = str(task_key or "").strip()
    normalized_owner = str(owner or "").strip()
    if not normalized_key or not normalized_owner:
        raise ValueError("IC task claim requires task_key and owner")
    current_time = _parse_timestamp(now)
    if current_time is None:
        raise ValueError(f"invalid IC task claim timestamp: {now!r}")

    with _locked_store(store_path):
        claims = _read_claims(store_path)
        previous = claims.get(normalized_key)
        if previous and str(previous.get("status") or "") in ACTIVE_STATUSES:
            expires_at = _parse_timestamp(previous.get("lease_expires_at"))
            if expires_at is not None and expires_at > current_time:
                raise ICTaskAlreadyClaimedError(previous)
            if previous.get("owner") == normalized_owner:
                raise ICTaskOwnerReuseError(
                    "IC task reclaim requires a fresh owner after lease expiry"
                )

        attempt = int(previous.get("attempt") or 0) + 1 if previous else 1
        history = list(previous.get("history") or []) if previous else []
        recovery_reason = None
        if previous:
            history.append({key: value for key, value in previous.items() if key != "history"})
        if previous and str(previous.get("status") or "") in ACTIVE_STATUSES:
            recovery_reason = "lease_expired"
        claim = {
            "task_key": normalized_key,
            "status": "running",
            "owner": normalized_owner,
            "attempt": attempt,
            "claimed_at": now,
            "heartbeat_at": now,
            "lease_expires_at": _lease_expiry(now, lease_seconds),
            "finished_at": None,
            "failure_reason": None,
            "recovery_reason": recovery_reason,
            "history": history,
        }
        claims[normalized_key] = claim
        _atomic_write(store_path, claims)
        return dict(claim)


def _heartbeat_file_ic_task(
    store_path: Path,
    *,
    task_key: str,
    owner: str,
    now: str,
    lease_seconds: int,
) -> dict[str, Any] | None:
    """Renew a lease only when task and owner still match."""

    current_time = _parse_timestamp(now)
    if current_time is None:
        raise ValueError(f"invalid IC task heartbeat timestamp: {now!r}")
    with _locked_store(store_path):
        claims = _read_claims(store_path)
        claim = claims.get(task_key)
        expires_at = _parse_timestamp(claim.get("lease_expires_at")) if claim else None
        if (
            not claim
            or claim.get("owner") != owner
            or str(claim.get("status") or "") not in ACTIVE_STATUSES
            or expires_at is None
            or expires_at <= current_time
        ):
            return None
        claim.update({"heartbeat_at": now, "lease_expires_at": _lease_expiry(now, lease_seconds)})
        _atomic_write(store_path, claims)
        return dict(claim)


def _finish_file_ic_task(
    store_path: Path,
    *,
    task_key: str,
    owner: str,
    now: str,
    status: str,
    failure_reason: str | None = None,
) -> dict[str, Any] | None:
    """Finish a claim only when its current owner still matches."""

    normalized_status = str(status or "").strip()
    if normalized_status not in TERMINAL_STATUSES:
        raise ValueError(f"invalid IC task terminal status: {status!r}")
    current_time = _parse_timestamp(now)
    if current_time is None:
        raise ValueError(f"invalid IC task terminal timestamp: {now!r}")
    with _locked_store(store_path):
        claims = _read_claims(store_path)
        claim = claims.get(task_key)
        expires_at = _parse_timestamp(claim.get("lease_expires_at")) if claim else None
        if (
            not claim
            or claim.get("owner") != owner
            or str(claim.get("status") or "") not in ACTIVE_STATUSES
            or expires_at is None
            or expires_at <= current_time
        ):
            return None
        claim.update(
            {
                "status": normalized_status,
                "heartbeat_at": now,
                "lease_expires_at": None,
                "finished_at": now,
                "failure_reason": str(failure_reason or "")[:500] or None,
            }
        )
        _atomic_write(store_path, claims)
        return dict(claim)


def _store_scope(store_path: Path) -> str:
    parts = Path(store_path).parts
    return "/".join(parts[-3:]) if len(parts) >= 3 else Path(store_path).name


def _app_database_url() -> str:
    return os.getenv("SIQ_APP_DATABASE_URL") or os.getenv("DATABASE_URL") or ""


def _selected_backend() -> str:
    configured = os.getenv("SIQ_IC_TASK_LEASE_BACKEND", "").strip().lower()
    if configured not in {"", "file", "postgres"}:
        raise RuntimeError("SIQ_IC_TASK_LEASE_BACKEND must be 'file' or 'postgres'.")
    production = os.getenv("SIQ_DEPLOYMENT_PROFILE", "local").strip().lower() in {"prod", "production"}
    backend = configured or ("postgres" if production else "file")
    if production and backend != "postgres":
        raise RuntimeError("Production IC task leases require the PostgreSQL backend.")
    if backend == "postgres" and not _app_database_url().startswith(
        ("postgresql://", "postgresql+psycopg://")
    ):
        raise RuntimeError("PostgreSQL SIQ_APP_DATABASE_URL is required for IC task leases.")
    return backend


_postgres_store_instance: PostgresICTaskLeaseStore | None = None
_postgres_store_lock = threading.Lock()


def _postgres_store() -> PostgresICTaskLeaseStore:
    global _postgres_store_instance
    if _postgres_store_instance is not None:
        return _postgres_store_instance
    with _postgres_store_lock:
        if _postgres_store_instance is None:
            from database import engine

            _postgres_store_instance = PostgresICTaskLeaseStore(
                session_factory=lambda: Session(engine)
            )
        return _postgres_store_instance


def load_ic_task_claims(store_path: Path) -> dict[str, dict[str, Any]]:
    """Load claim snapshots from the configured authority."""

    if _selected_backend() == "file":
        return _load_file_ic_task_claims(store_path)
    return _postgres_store().load(_store_scope(store_path))


def claim_ic_task(
    store_path: Path,
    *,
    task_key: str,
    owner: str,
    now: str,
    lease_seconds: int,
) -> dict[str, Any]:
    """Claim through PostgreSQL in production and the locked file store locally."""

    if _selected_backend() == "file":
        return _claim_file_ic_task(
            store_path,
            task_key=task_key,
            owner=owner,
            now=now,
            lease_seconds=lease_seconds,
        )
    return _postgres_store().claim(
        scope_key=_store_scope(store_path),
        task_key=task_key,
        owner=owner,
        now=now,
        lease_seconds=lease_seconds,
    )


def heartbeat_ic_task(
    store_path: Path,
    *,
    task_key: str,
    owner: str,
    now: str,
    lease_seconds: int,
) -> dict[str, Any] | None:
    """Renew through the configured authority without changing the public API."""

    if _selected_backend() == "file":
        return _heartbeat_file_ic_task(
            store_path,
            task_key=task_key,
            owner=owner,
            now=now,
            lease_seconds=lease_seconds,
        )
    return _postgres_store().heartbeat(
        scope_key=_store_scope(store_path),
        task_key=task_key,
        owner=owner,
        now=now,
        lease_seconds=lease_seconds,
    )


def finish_ic_task(
    store_path: Path,
    *,
    task_key: str,
    owner: str,
    now: str,
    status: str,
    failure_reason: str | None = None,
) -> dict[str, Any] | None:
    """Publish a terminal state only for the currently fenced owner."""

    if _selected_backend() == "file":
        return _finish_file_ic_task(
            store_path,
            task_key=task_key,
            owner=owner,
            now=now,
            status=status,
            failure_reason=failure_reason,
        )
    return _postgres_store().finish(
        scope_key=_store_scope(store_path),
        task_key=task_key,
        owner=owner,
        now=now,
        status=status,
        failure_reason=failure_reason,
    )
