from __future__ import annotations

import json
import logging
import os
import time
import uuid
import weakref
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import RLock, Thread
from typing import Any, Callable

from services.path_config import BACKEND_DATA_ROOT

try:
    from services import observability
except Exception:  # pragma: no cover - job execution must not depend on metrics importability.
    observability = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)
ACTIVE_JOB_STATUSES = {"queued", "running"}
INTERRUPTED_REASON = "process_restart_unrecoverable_target"
_LIVE_JOB_SERVICES: weakref.WeakSet[FileBackedJobService] = weakref.WeakSet()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return str(value)


def _snapshot_job(job: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in job.items() if key not in {"target"}}


def _sort_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        jobs,
        key=lambda item: (str(item.get("created_at") or ""), str(item.get("job_id") or "")),
    )


def _record_background_job_final_state(*, kind: str, status: str, started_monotonic: float) -> None:
    if observability is None:
        return
    try:
        observability.record_background_job_final_state(
            kind=kind,
            status=status,
            duration_seconds=time.perf_counter() - started_monotonic,
        )
    except Exception:
        return


class FileBackedJobService:
    def __init__(self, *, store_path: Path | None = None, max_jobs: int = 200):
        self._max_jobs = max_jobs
        self._job_lock = RLock()
        self._store_path = store_path or (BACKEND_DATA_ROOT / "market-reports" / "jobs.json")
        self._owner = f"job-worker-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._jobs: dict[str, dict[str, Any]] = self._load_jobs()
        self._recover_interrupted_jobs()
        _LIVE_JOB_SERVICES.add(self)

    def _load_jobs(self) -> dict[str, dict[str, Any]]:
        try:
            payload = json.loads(self._store_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        raw_jobs = payload.get("jobs") if isinstance(payload, dict) else payload
        if not isinstance(raw_jobs, list):
            return {}
        jobs: dict[str, dict[str, Any]] = {}
        for job in raw_jobs:
            if isinstance(job, dict):
                job_id = job.get("job_id")
                if isinstance(job_id, str) and job_id.strip():
                    jobs[job_id] = job
        return dict((job["job_id"], job) for job in _sort_jobs(list(jobs.values())))

    def _record_persistence_failure(self, *, operation: str, error: Exception) -> None:
        logger.error(
            "background_job_store_persist_failed",
            extra={
                "event": "background_job_store_persist_failed",
                "operation": operation,
                "store_name": self._store_path.name,
                "error_type": type(error).__name__,
            },
            exc_info=True,
        )
        if observability is None:
            return
        try:
            observability.record_background_job_persistence_failure(operation=operation)
        except Exception:
            return

    def _persist_locked(self, *, operation: str) -> bool:
        try:
            self._store_path.parent.mkdir(parents=True, exist_ok=True)
            jobs = _sort_jobs(list(self._jobs.values()))[-self._max_jobs :]
            payload = {"jobs": [_snapshot_job(job) for job in jobs]}
            tmp_path = self._store_path.with_suffix(f"{self._store_path.suffix}.tmp")
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            tmp_path.replace(self._store_path)
            return True
        except Exception as exc:
            self._record_persistence_failure(operation=operation, error=exc)
            return False

    def _mark_durability_locked(self, *, durable: bool) -> None:
        for job in self._jobs.values():
            job["durability_status"] = "durable" if durable else "degraded"
            job["persistence_error"] = None if durable else "job_store_write_failed"

    def _persist_with_status_locked(self, *, operation: str) -> bool:
        self._mark_durability_locked(durable=True)
        if self._persist_locked(operation=operation):
            return True
        self._mark_durability_locked(durable=False)
        return False

    def _recover_interrupted_jobs(self) -> None:
        now = _now_iso()
        recovered = False
        for job in self._jobs.values():
            job.setdefault("attempt", 1)
            job.setdefault("owner", None)
            job.setdefault("heartbeat_at", None)
            job.setdefault("interrupted_reason", None)
            job.setdefault("durability_status", "durable")
            job.setdefault("persistence_error", None)
            if str(job.get("status") or "") not in ACTIVE_JOB_STATUSES:
                continue
            owner = str(job.get("owner") or "")
            live_owners = {service._owner for service in _LIVE_JOB_SERVICES}
            if owner in live_owners:
                # A second service object in the same process can still execute the target.
                continue
            job.update(
                {
                    "status": "interrupted",
                    "finished_at": now,
                    "updated_at": now,
                    "interrupted_reason": INTERRUPTED_REASON,
                    "error": "Background job interrupted by process restart; target cannot be recovered",
                }
            )
            recovered = True
        if recovered:
            self._persist_with_status_locked(operation="restart_recovery")

    def _trim_locked(self) -> None:
        if len(self._jobs) <= self._max_jobs:
            return
        old_ids = [job["job_id"] for job in _sort_jobs(list(self._jobs.values()))[:-self._max_jobs]]
        for old_id in old_ids:
            self._jobs.pop(old_id, None)

    def _update_job_locked(self, job_id: str, **updates: Any) -> None:
        job = self._jobs.get(job_id)
        if not job:
            return
        job.update({key: _json_safe(value) for key, value in updates.items()})
        job["updated_at"] = _now_iso()
        self._trim_locked()
        self._persist_with_status_locked(operation="job_update")

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._job_lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            return _snapshot_job(job)

    def start(self, kind: str, target: Callable[[], Any], *, created_by: Any | None = None) -> dict[str, Any]:
        job_id = f"{kind}-{uuid.uuid4().hex[:12]}"
        job: dict[str, Any] = {
            "job_id": job_id,
            "kind": kind,
            "status": "queued",
            "created_at": _now_iso(),
            "started_at": None,
            "finished_at": None,
            "created_by": _json_safe(created_by) if created_by is not None else None,
            "attempt": 1,
            "owner": self._owner,
            "heartbeat_at": None,
            "interrupted_reason": None,
            "durability_status": "durable",
            "persistence_error": None,
            "result": None,
            "error": None,
            "target": target,
        }
        with self._job_lock:
            self._jobs[job_id] = job
            self._trim_locked()
            self._persist_with_status_locked(operation="job_create")

        def runner() -> None:
            started_monotonic = time.perf_counter()
            with self._job_lock:
                self._update_job_locked(
                    job_id,
                    status="running",
                    started_at=_now_iso(),
                    heartbeat_at=_now_iso(),
                )
            try:
                result = target()
                status = "succeeded"
                if isinstance(result, dict) and not result.get("ok", True):
                    status = "failed"
                with self._job_lock:
                    self._update_job_locked(
                        job_id,
                        status=status,
                        result=result,
                        finished_at=_now_iso(),
                    )
                _record_background_job_final_state(
                    kind=kind,
                    status=status,
                    started_monotonic=started_monotonic,
                )
            except Exception as exc:
                with self._job_lock:
                    self._update_job_locked(
                        job_id,
                        status="failed",
                        error=str(exc),
                        finished_at=_now_iso(),
                    )
                _record_background_job_final_state(
                    kind=kind,
                    status="failed",
                    started_monotonic=started_monotonic,
                )

        thread = Thread(target=runner, name=f"siq-{job_id}", daemon=True)
        thread.start()
        return _snapshot_job(job)


def _app_database_url() -> str:
    return os.getenv("SIQ_APP_DATABASE_URL") or os.getenv("DATABASE_URL") or ""


def _is_production_profile() -> bool:
    return os.getenv("SIQ_DEPLOYMENT_PROFILE", "local").strip().lower() in {"prod", "production"}


def create_job_service(*, store_path: Path | None = None, max_jobs: int = 200) -> Any:
    """Select file coordination locally and PostgreSQL authority in production."""
    configured = os.getenv("SIQ_BACKGROUND_JOB_BACKEND", "").strip().lower()
    if configured not in {"", "file", "postgres"}:
        raise RuntimeError("SIQ_BACKGROUND_JOB_BACKEND must be 'file' or 'postgres'.")

    production = _is_production_profile()
    backend = configured or ("postgres" if production else "file")
    if production and backend != "postgres":
        raise RuntimeError("Production background jobs require the PostgreSQL backend.")
    if backend == "file":
        return FileBackedJobService(store_path=store_path, max_jobs=max_jobs)

    database_url = _app_database_url()
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise RuntimeError("PostgreSQL SIQ_APP_DATABASE_URL is required for durable background jobs.")

    from database import engine
    from sqlmodel import Session

    from services.durable_job_service import DurableJobCoordinator, DurableJobService

    try:
        lease_seconds = max(30, int(os.getenv("SIQ_BACKGROUND_JOB_LEASE_SECONDS", "120")))
    except ValueError as exc:
        raise RuntimeError("SIQ_BACKGROUND_JOB_LEASE_SECONDS must be an integer.") from exc
    coordinator = DurableJobCoordinator(
        session_factory=lambda: Session(engine),
        lease_seconds=lease_seconds,
    )
    return DurableJobService(
        coordinator=coordinator,
        final_state_recorder=_record_background_job_final_state,
    )


market_report_job_service = create_job_service()

# Backward-compatible name kept for existing imports and tests.
InMemoryJobService = FileBackedJobService
