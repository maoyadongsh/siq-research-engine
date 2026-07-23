"""PostgreSQL-authoritative queue for recoverable workflow jobs.

The API stores a serializable workflow description and returns immediately.
Independent workers claim jobs with a lease.  Every heartbeat, snapshot update,
and terminal publish is fenced by both owner and attempt so an expired worker
cannot overwrite a newer attempt.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Optional

from sqlalchemy import and_, case, or_, text, update
from sqlmodel import Field, Session, SQLModel, select

ACTIVE_WORKFLOW_JOB_STATUSES = {"queued", "running"}
TERMINAL_WORKFLOW_JOB_STATUSES = {"succeeded", "failed", "interrupted", "cancelled"}
EXHAUSTED_REASON = "workflow_worker_lease_expired_max_attempts"


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _load(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


class WorkflowQueueJob(SQLModel, table=True):
    __tablename__ = "workflow_queue_jobs"

    job_id: str = Field(primary_key=True, max_length=64)
    task_id: str = Field(max_length=255, index=True)
    retry_scope: str = Field(max_length=80, index=True)
    idempotency_key: str = Field(max_length=64, index=True)
    status: str = Field(default="queued", max_length=24, index=True)
    snapshot_json: str
    result_json: Optional[str] = Field(default=None)
    error: Optional[str] = Field(default=None)
    attempt: int = Field(default=0)
    max_attempts: int = Field(default=3)
    owner: Optional[str] = Field(default=None, max_length=255, index=True)
    heartbeat_at: Optional[datetime] = Field(default=None)
    available_at: datetime = Field(default_factory=utcnow_naive, index=True)
    lease_until: Optional[datetime] = Field(default=None, index=True)
    interrupted_reason: Optional[str] = Field(default=None, max_length=120)
    created_at: datetime = Field(default_factory=utcnow_naive, index=True)
    started_at: Optional[datetime] = Field(default=None)
    finished_at: Optional[datetime] = Field(default=None)
    updated_at: datetime = Field(default_factory=utcnow_naive)


class WorkflowLeaseLostError(RuntimeError):
    """Raised when an obsolete worker attempt tries to publish state."""


class WorkflowQueueCoordinator:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        lease_seconds: int = 120,
        max_attempts: int = 3,
    ) -> None:
        self._session_factory = session_factory
        self.lease_seconds = max(5, int(lease_seconds))
        self.max_attempts = max(1, int(max_attempts))
        self._local_enqueue_lock = threading.Lock()

    @staticmethod
    def _overlay(row: WorkflowQueueJob) -> dict[str, Any]:
        snapshot = _load(row.snapshot_json, {})
        if not isinstance(snapshot, dict):
            snapshot = {}
        snapshot.update(
            {
                "jobId": row.job_id,
                "taskId": row.task_id,
                "retryScope": row.retry_scope,
                "idempotencyKey": row.idempotency_key,
                "status": row.status,
                "attempt": row.attempt,
                "maxAttempts": row.max_attempts,
                "ownerId": row.owner,
                "heartbeatAt": _iso(row.heartbeat_at),
                "leaseExpiresAt": _iso(row.lease_until),
                "availableAt": _iso(row.available_at),
                "createdAt": _iso(row.created_at),
                "startedAt": _iso(row.started_at),
                "finishedAt": _iso(row.finished_at),
                "updatedAt": _iso(row.updated_at),
                "interruptedReason": row.interrupted_reason,
                "durabilityStatus": "durable",
            }
        )
        if row.result_json is not None:
            snapshot["result"] = _load(row.result_json, None)
        if row.error is not None:
            snapshot["error"] = row.error
        return snapshot

    @staticmethod
    def _lock_idempotency_key(session: Session, key: str) -> None:
        if session.get_bind().dialect.name == "postgresql":
            session.exec(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))").bindparams(key=key)
            )

    def enqueue(
        self,
        *,
        snapshot: dict[str, Any],
        max_attempts: int | None = None,
    ) -> tuple[dict[str, Any], bool]:
        job_id = str(snapshot.get("jobId") or "").strip()
        task_id = str(snapshot.get("taskId") or "").strip()
        retry_scope = str(snapshot.get("retryScope") or "workflow").strip()
        idempotency_key = str(snapshot.get("idempotencyKey") or "").strip()
        if not all((job_id, task_id, retry_scope, idempotency_key)):
            raise ValueError("workflow queue jobs require jobId, taskId, retryScope, and idempotencyKey")
        self.recover_exhausted()
        now = utcnow_naive()
        attempts = max(1, int(max_attempts or self.max_attempts))

        # SQLite is used only by local/tests.  PostgreSQL uses an advisory
        # transaction lock so separate API processes converge on one active job.
        with self._local_enqueue_lock, self._session_factory() as session:
            self._lock_idempotency_key(session, idempotency_key)
            existing = session.exec(
                select(WorkflowQueueJob)
                .where(
                    WorkflowQueueJob.idempotency_key == idempotency_key,
                    WorkflowQueueJob.status.in_(ACTIVE_WORKFLOW_JOB_STATUSES),
                )
                .order_by(WorkflowQueueJob.created_at.desc())
            ).first()
            if existing is not None:
                return self._overlay(existing), True
            row = WorkflowQueueJob(
                job_id=job_id,
                task_id=task_id,
                retry_scope=retry_scope,
                idempotency_key=idempotency_key,
                status="queued",
                snapshot_json=_dump(snapshot),
                max_attempts=attempts,
                available_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._overlay(row), False

    def get(self, job_id: str) -> dict[str, Any] | None:
        self.recover_exhausted(job_id=job_id)
        with self._session_factory() as session:
            row = session.get(WorkflowQueueJob, job_id)
            return self._overlay(row) if row is not None else None

    def recover_exhausted(self, *, job_id: str | None = None) -> int:
        now = utcnow_naive()
        conditions = [
            WorkflowQueueJob.status == "running",
            WorkflowQueueJob.lease_until.is_not(None),
            WorkflowQueueJob.lease_until <= now,
            WorkflowQueueJob.attempt >= WorkflowQueueJob.max_attempts,
        ]
        if job_id is not None:
            conditions.append(WorkflowQueueJob.job_id == job_id)
        statement = (
            update(WorkflowQueueJob)
            .where(*conditions)
            .values(
                status="interrupted",
                error="Workflow worker lease expired and the retry budget was exhausted",
                interrupted_reason=EXHAUSTED_REASON,
                finished_at=now,
                lease_until=None,
                updated_at=now,
            )
        )
        with self._session_factory() as session:
            result = session.exec(statement)
            session.commit()
            return int(result.rowcount or 0)

    def claim_next(self, *, owner: str) -> dict[str, Any] | None:
        self.recover_exhausted()
        for _ in range(8):
            now = utcnow_naive()
            eligible = and_(
                WorkflowQueueJob.attempt < WorkflowQueueJob.max_attempts,
                or_(
                    and_(WorkflowQueueJob.status == "queued", WorkflowQueueJob.available_at <= now),
                    and_(
                        WorkflowQueueJob.status == "running",
                        WorkflowQueueJob.lease_until.is_not(None),
                        WorkflowQueueJob.lease_until <= now,
                    ),
                ),
            )
            with self._session_factory() as session:
                query = (
                    select(WorkflowQueueJob.job_id)
                    .where(eligible)
                    .order_by(WorkflowQueueJob.available_at, WorkflowQueueJob.created_at)
                    .limit(1)
                )
                if session.get_bind().dialect.name == "postgresql":
                    query = query.with_for_update(skip_locked=True)
                job_id = session.exec(query).first()
                if job_id is None:
                    return None
                statement = (
                    update(WorkflowQueueJob)
                    .where(WorkflowQueueJob.job_id == job_id, eligible)
                    .values(
                        status="running",
                        owner=owner,
                        attempt=WorkflowQueueJob.attempt + 1,
                        heartbeat_at=now,
                        lease_until=now + timedelta(seconds=self.lease_seconds),
                        started_at=case(
                            (WorkflowQueueJob.started_at.is_(None), now),
                            else_=WorkflowQueueJob.started_at,
                        ),
                        finished_at=None,
                        updated_at=now,
                        error=None,
                        interrupted_reason=case(
                            (WorkflowQueueJob.status == "running", "lease_expired_reclaimed"),
                            else_=None,
                        ),
                    )
                )
                result = session.exec(statement)
                session.commit()
                if result.rowcount != 1:
                    continue
                row = session.get(WorkflowQueueJob, job_id)
                return self._overlay(row) if row is not None else None
        return None

    def heartbeat(self, job_id: str, *, owner: str, attempt: int) -> bool:
        now = utcnow_naive()
        statement = (
            update(WorkflowQueueJob)
            .where(
                WorkflowQueueJob.job_id == job_id,
                WorkflowQueueJob.status == "running",
                WorkflowQueueJob.owner == owner,
                WorkflowQueueJob.attempt == attempt,
                WorkflowQueueJob.lease_until > now,
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

    def mutate_snapshot(
        self,
        job_id: str,
        *,
        owner: str,
        attempt: int,
        mutate: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        now = utcnow_naive()
        with self._session_factory() as session:
            query = select(WorkflowQueueJob).where(WorkflowQueueJob.job_id == job_id)
            if session.get_bind().dialect.name == "postgresql":
                query = query.with_for_update()
            row = session.exec(query).first()
            if (
                row is None
                or row.status != "running"
                or row.owner != owner
                or row.attempt != attempt
                or row.lease_until is None
                or row.lease_until <= now
            ):
                raise WorkflowLeaseLostError(f"workflow job lease lost: {job_id}")
            snapshot = _load(row.snapshot_json, {})
            if not isinstance(snapshot, dict):
                snapshot = {}
            mutate(snapshot)
            row.snapshot_json = _dump(snapshot)
            row.updated_at = now
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._overlay(row)

    def finish(
        self,
        job_id: str,
        *,
        owner: str,
        attempt: int,
        status: str,
        snapshot: dict[str, Any],
        result: Any | None = None,
        error: str | None = None,
    ) -> bool:
        if status not in TERMINAL_WORKFLOW_JOB_STATUSES:
            raise ValueError(f"unsupported workflow terminal status: {status}")
        now = utcnow_naive()
        statement = (
            update(WorkflowQueueJob)
            .where(
                WorkflowQueueJob.job_id == job_id,
                WorkflowQueueJob.status == "running",
                WorkflowQueueJob.owner == owner,
                WorkflowQueueJob.attempt == attempt,
                WorkflowQueueJob.lease_until > now,
            )
            .values(
                status=status,
                snapshot_json=_dump(snapshot),
                result_json=_dump(result) if result is not None else None,
                error=error,
                finished_at=now,
                lease_until=None,
                updated_at=now,
            )
        )
        with self._session_factory() as session:
            result_row = session.exec(statement)
            session.commit()
            return result_row.rowcount == 1


def workflow_worker_owner_id(prefix: str = "workflow-worker") -> str:
    return f"{prefix}:{uuid.uuid4().hex}"


__all__ = [
    "EXHAUSTED_REASON",
    "WorkflowLeaseLostError",
    "WorkflowQueueCoordinator",
    "WorkflowQueueJob",
    "workflow_worker_owner_id",
]
