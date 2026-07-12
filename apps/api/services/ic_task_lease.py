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
from typing import Any, Iterator
from weakref import WeakValueDictionary

ACTIVE_STATUSES = {"queued", "running"}
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "interrupted"}
STORE_SCHEMA = "siq_ic_task_leases_v1"


class ICTaskAlreadyClaimedError(RuntimeError):
    """Raised when an unexpired lease already owns the requested IC task."""

    def __init__(self, claim: dict[str, Any]) -> None:
        self.claim = dict(claim)
        super().__init__(
            "IC task is already running "
            f"(attempt={claim.get('attempt')}, lease_expires_at={claim.get('lease_expires_at')})"
        )


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


def load_ic_task_claims(store_path: Path) -> dict[str, dict[str, Any]]:
    """Load claim snapshots for status and audit APIs."""

    with _locked_store(store_path):
        return {task_key: dict(claim) for task_key, claim in _read_claims(store_path).items()}


def claim_ic_task(
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


def heartbeat_ic_task(
    store_path: Path,
    *,
    task_key: str,
    owner: str,
    now: str,
    lease_seconds: int,
) -> dict[str, Any] | None:
    """Renew a lease only when task and owner still match."""

    with _locked_store(store_path):
        claims = _read_claims(store_path)
        claim = claims.get(task_key)
        if (
            not claim
            or claim.get("owner") != owner
            or str(claim.get("status") or "") not in ACTIVE_STATUSES
        ):
            return None
        claim.update({"heartbeat_at": now, "lease_expires_at": _lease_expiry(now, lease_seconds)})
        _atomic_write(store_path, claims)
        return dict(claim)


def finish_ic_task(
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
    with _locked_store(store_path):
        claims = _read_claims(store_path)
        claim = claims.get(task_key)
        if (
            not claim
            or claim.get("owner") != owner
            or str(claim.get("status") or "") not in ACTIVE_STATUSES
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
