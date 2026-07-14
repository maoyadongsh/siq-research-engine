"""Database-authoritative coordination for process-local background jobs.

The callable still runs in the API process and large artifacts remain on disk.
Only ownership, lease state, result metadata, and artifact references are stored
in the application database so multiple API workers cannot publish conflicting
state for the same job.
"""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from datetime import UTC, datetime, timedelta
from enum import Enum
from threading import Event, Lock, Thread
from typing import Any, Callable, Optional

from sqlalchemy import and_, case, or_, update
from sqlmodel import Field, Session, SQLModel, select

ACTIVE_JOB_STATUSES = {"queued", "running"}
TERMINAL_JOB_STATUSES = {"succeeded", "failed", "interrupted", "cancelled"}
RESTART_INTERRUPTED_REASON = "process_restart_lease_expired"
RUNTIME_INTERRUPTED_REASON = "lease_expired_without_terminal_update"
RECLAIMED_REASON = "lease_expired_reclaimed"


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _now_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return str(value)


def _json_dump(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(_json_safe(value), ensure_ascii=False, separators=(",", ":"))


def _json_load(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


def _artifact_refs(result: Any) -> list[str]:
    if not isinstance(result, dict):
        return []
    refs: list[str] = []
    for key in ("artifact", "artifact_path", "output_path", "path", "report_path"):
        value = result.get(key)
        if isinstance(value, (str, os.PathLike)) and str(value).strip():
            refs.append(str(value))
    return list(dict.fromkeys(refs))


class DurableBackgroundJob(SQLModel, table=True):
    __tablename__ = "durable_background_jobs"

    job_id: str = Field(primary_key=True, max_length=255)
    kind: str = Field(max_length=120, index=True)
    status: str = Field(default="queued", max_length=24, index=True)
    created_by_json: Optional[str] = Field(default=None)
    result_json: Optional[str] = Field(default=None)
    artifact_refs_json: Optional[str] = Field(default=None)
    error: Optional[str] = Field(default=None)
    attempt: int = Field(default=0)
    owner: Optional[str] = Field(default=None, max_length=255, index=True)
    heartbeat_at: Optional[datetime] = Field(default=None)
    lease_until: datetime = Field(index=True)
    interrupted_reason: Optional[str] = Field(default=None, max_length=120)
    created_at: datetime = Field(default_factory=utcnow_naive, index=True)
    started_at: Optional[datetime] = Field(default=None)
    finished_at: Optional[datetime] = Field(default=None)
    updated_at: datetime = Field(default_factory=utcnow_naive)


def runtime_job_owner_id() -> str:
    configured = os.getenv("SIQ_BACKGROUND_JOB_OWNER_ID", "").strip()
    if configured:
        # Treat the configured value as an operator-readable prefix.  A fresh
        # process incarnation must never reuse an old lease capability.
        return f"{configured}:{uuid.uuid4().hex}"
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"


class DurableJobCoordinator:
    def __init__(self, *, session_factory: Callable[[], Session], lease_seconds: int = 120):
        self._session_factory = session_factory
        self.lease_seconds = max(5, int(lease_seconds))

    def _snapshot(self, row: DurableBackgroundJob) -> dict[str, Any]:
        return {
            "job_id": row.job_id,
            "kind": row.kind,
            "status": row.status,
            "created_at": _now_iso(row.created_at),
            "started_at": _now_iso(row.started_at),
            "finished_at": _now_iso(row.finished_at),
            "updated_at": _now_iso(row.updated_at),
            "created_by": _json_load(row.created_by_json),
            "attempt": row.attempt,
            "owner": row.owner,
            "heartbeat_at": _now_iso(row.heartbeat_at),
            "lease_until": _now_iso(row.lease_until),
            "interrupted_reason": row.interrupted_reason,
            "durability_status": "durable",
            "persistence_error": None,
            "artifact_refs": _json_load(row.artifact_refs_json) or [],
            "result": _json_load(row.result_json),
            "error": row.error,
        }

    def create_job(
        self,
        *,
        job_id: str,
        kind: str,
        created_by: Any | None = None,
    ) -> dict[str, Any]:
        now = utcnow_naive()
        row = DurableBackgroundJob(
            job_id=job_id,
            kind=kind,
            status="queued",
            created_by_json=_json_dump(created_by),
            lease_until=now + timedelta(seconds=self.lease_seconds),
            created_at=now,
            updated_at=now,
        )
        with self._session_factory() as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._snapshot(row)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.exec(
                select(DurableBackgroundJob).where(DurableBackgroundJob.job_id == job_id)
            ).first()
            return self._snapshot(row) if row else None

    def claim(self, job_id: str, *, owner: str) -> dict[str, Any] | None:
        now = utcnow_naive()
        expires = now + timedelta(seconds=self.lease_seconds)
        statement = (
            update(DurableBackgroundJob)
            .where(
                DurableBackgroundJob.job_id == job_id,
                or_(
                    DurableBackgroundJob.status == "queued",
                    and_(
                        DurableBackgroundJob.status == "running",
                        DurableBackgroundJob.lease_until <= now,
                    ),
                ),
            )
            .values(
                status="running",
                owner=owner,
                heartbeat_at=now,
                lease_until=expires,
                started_at=case(
                    (DurableBackgroundJob.started_at.is_(None), now),
                    else_=DurableBackgroundJob.started_at,
                ),
                finished_at=None,
                attempt=DurableBackgroundJob.attempt + 1,
                interrupted_reason=case(
                    (DurableBackgroundJob.status == "running", RECLAIMED_REASON),
                    else_=DurableBackgroundJob.interrupted_reason,
                ),
                updated_at=now,
            )
        )
        with self._session_factory() as session:
            result = session.exec(statement)
            session.commit()
            if result.rowcount != 1:
                return None
        return self.get(job_id)

    def heartbeat(self, job_id: str, *, owner: str, attempt: int) -> bool:
        now = utcnow_naive()
        statement = (
            update(DurableBackgroundJob)
            .where(
                DurableBackgroundJob.job_id == job_id,
                DurableBackgroundJob.status == "running",
                DurableBackgroundJob.owner == owner,
                DurableBackgroundJob.attempt == attempt,
                DurableBackgroundJob.lease_until > now,
            )
            .values(
                heartbeat_at=now,
                lease_until=now + timedelta(seconds=self.lease_seconds),
                updated_at=now,
            )
        )
        with self._session_factory() as session:
            result = session.exec(statement)
            session.commit()
            return result.rowcount == 1

    def finish(
        self,
        job_id: str,
        *,
        owner: str,
        attempt: int,
        status: str,
        result: Any | None = None,
        error: str | None = None,
    ) -> bool:
        if status not in TERMINAL_JOB_STATUSES:
            raise ValueError(f"unsupported terminal job status: {status}")
        now = utcnow_naive()
        statement = (
            update(DurableBackgroundJob)
            .where(
                DurableBackgroundJob.job_id == job_id,
                DurableBackgroundJob.status == "running",
                DurableBackgroundJob.owner == owner,
                DurableBackgroundJob.attempt == attempt,
                DurableBackgroundJob.lease_until > now,
            )
            .values(
                status=status,
                result_json=_json_dump(result),
                artifact_refs_json=_json_dump(_artifact_refs(result)),
                error=error,
                finished_at=now,
                lease_until=now,
                updated_at=now,
            )
        )
        with self._session_factory() as session:
            changed = session.exec(statement).rowcount == 1
            session.commit()
            return changed

    def recover_expired_active_jobs(
        self,
        *,
        job_id: str | None = None,
        reason: str = RESTART_INTERRUPTED_REASON,
    ) -> int:
        now = utcnow_naive()
        conditions = [
            DurableBackgroundJob.status.in_(ACTIVE_JOB_STATUSES),
            DurableBackgroundJob.lease_until <= now,
        ]
        if job_id is not None:
            conditions.append(DurableBackgroundJob.job_id == job_id)
        statement = (
            update(DurableBackgroundJob)
            .where(*conditions)
            .values(
                status="interrupted",
                interrupted_reason=reason,
                error="Background job lease expired before a terminal update",
                finished_at=now,
                updated_at=now,
            )
        )
        with self._session_factory() as session:
            result = session.exec(statement)
            session.commit()
            return int(result.rowcount or 0)


class DurableJobService:
    """Existing start/get facade backed by database lease authority."""

    def __init__(
        self,
        *,
        coordinator: DurableJobCoordinator,
        owner: str | None = None,
        heartbeat_seconds: float | None = None,
        final_state_recorder: Callable[..., None] | None = None,
    ):
        self._coordinator = coordinator
        self._owner = owner or runtime_job_owner_id()
        default_heartbeat = max(1.0, coordinator.lease_seconds / 3)
        self._heartbeat_seconds = min(
            float(heartbeat_seconds or default_heartbeat),
            max(1.0, coordinator.lease_seconds / 2),
        )
        self._final_state_recorder = final_state_recorder
        self._recovery_lock = Lock()
        self._recovered = False

    def _ensure_recovered(self) -> None:
        if self._recovered:
            return
        with self._recovery_lock:
            if not self._recovered:
                self._coordinator.recover_expired_active_jobs()
                self._recovered = True

    def get(self, job_id: str) -> dict[str, Any] | None:
        self._ensure_recovered()
        self._coordinator.recover_expired_active_jobs(
            job_id=job_id,
            reason=RUNTIME_INTERRUPTED_REASON,
        )
        return self._coordinator.get(job_id)

    def start(self, kind: str, target: Callable[[], Any], *, created_by: Any | None = None) -> dict[str, Any]:
        self._ensure_recovered()
        job_id = f"{kind}-{uuid.uuid4().hex[:12]}"
        created = self._coordinator.create_job(
            job_id=job_id,
            kind=kind,
            created_by=created_by,
        )

        def runner() -> None:
            started_monotonic = time.perf_counter()
            claimed = self._coordinator.claim(job_id, owner=self._owner)
            if claimed is None:
                return
            claim_attempt = int(claimed["attempt"])
            heartbeat_stop = Event()

            def heartbeat_loop() -> None:
                while not heartbeat_stop.wait(self._heartbeat_seconds):
                    if not self._coordinator.heartbeat(
                        job_id,
                        owner=self._owner,
                        attempt=claim_attempt,
                    ):
                        return

            heartbeat = Thread(
                target=heartbeat_loop,
                name=f"siq-{job_id}-heartbeat",
                daemon=True,
            )
            heartbeat.start()
            status = "failed"
            result: Any = None
            error: str | None = None
            try:
                result = target()
                status = "succeeded"
                if isinstance(result, dict) and not result.get("ok", True):
                    status = "failed"
            except Exception as exc:
                error = str(exc)
            finally:
                heartbeat_stop.set()
                heartbeat.join(timeout=self._heartbeat_seconds + 0.5)

            published = self._coordinator.finish(
                job_id,
                owner=self._owner,
                attempt=claim_attempt,
                status=status,
                result=result,
                error=error,
            )
            if not published:
                self._coordinator.recover_expired_active_jobs(
                    job_id=job_id,
                    reason=RUNTIME_INTERRUPTED_REASON,
                )
            if published and self._final_state_recorder is not None:
                self._final_state_recorder(
                    kind=kind,
                    status=status,
                    started_monotonic=started_monotonic,
                )

        Thread(target=runner, name=f"siq-{job_id}", daemon=True).start()
        return created


__all__ = [
    "DurableBackgroundJob",
    "DurableJobCoordinator",
    "DurableJobService",
    "RECLAIMED_REASON",
    "RESTART_INTERRUPTED_REASON",
    "RUNTIME_INTERRUPTED_REASON",
    "runtime_job_owner_id",
    "utcnow_naive",
]
