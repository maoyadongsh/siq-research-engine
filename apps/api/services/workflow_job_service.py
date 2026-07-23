from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from weakref import WeakValueDictionary

NowFactory = Callable[[], str]
TERMINAL_STEP_STATUSES = {"succeeded", "failed", "skipped"}
COMMAND_RESULT_KEYS = {"returnCode", "stdout", "stderr"}
OUTPUT_TAIL_LIMIT = 6000
ACTIVE_JOB_STATUSES = {"queued", "running"}
DEFAULT_JOB_LEASE_SECONDS = 120
DEFAULT_LEGACY_STALE_SECONDS = 900


class WorkflowJobStoreConflictError(RuntimeError):
    """Raised when a caller attempts to persist against a stale revision."""


_path_locks: WeakValueDictionary[str, threading.RLock] = WeakValueDictionary()
_path_locks_guard = threading.Lock()


def workflow_job_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def workflow_job_lease_updates(
    *,
    owner_id: str,
    now: str,
    lease_seconds: int = DEFAULT_JOB_LEASE_SECONDS,
) -> dict[str, Any]:
    timestamp = _parse_timestamp(now)
    if timestamp is None:
        raise ValueError(f"invalid workflow job heartbeat timestamp: {now!r}")
    expires = timestamp + timedelta(seconds=max(1, lease_seconds))
    return {
        "ownerId": owner_id,
        "heartbeatAt": now,
        "leaseExpiresAt": expires.isoformat().replace("+00:00", "Z"),
    }


def workflow_job_idempotency_key(
    *,
    task_id: str,
    retry_scope: str,
    metadata: Mapping[str, Any] | None = None,
) -> str:
    created_by = metadata.get("created_by") if isinstance(metadata, Mapping) else None
    actor_id = created_by.get("id") if isinstance(created_by, Mapping) else None
    identity = f"workflow-job-v1\0{task_id}\0{retry_scope}\0{actor_id or 'system'}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


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


def _read_store_payload(store_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(store_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"revision": 0, "jobs": []}
    if not isinstance(payload, dict):
        return {"revision": 0, "jobs": payload if isinstance(payload, list) else []}
    return payload


def workflow_job_store_revision(store_path: Path) -> int:
    revision_path = store_path.with_name(f".{store_path.name}.revision")
    try:
        revision_payload = json.loads(revision_path.read_text(encoding="utf-8"))
        revision = revision_payload.get("revision") if isinstance(revision_payload, dict) else None
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return 0
    return revision if isinstance(revision, int) and revision >= 0 else 0


def _atomic_write_revision(store_path: Path, revision: int) -> None:
    revision_path = store_path.with_name(f".{store_path.name}.revision")
    _atomic_write_store(revision_path, {"revision": revision})


def _atomic_write_store(store_path: Path, payload: dict[str, Any]) -> None:
    temp_path: Path | None = None
    try:
        target_mode = stat.S_IMODE(store_path.stat().st_mode)
    except FileNotFoundError:
        target_mode = 0o644
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
            json.dump(payload, handle, ensure_ascii=False, indent=2)
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


def _tail(value: object, *, limit: int = OUTPUT_TAIL_LIMIT) -> str:
    text = str(value or "")
    return text[-limit:]


def _first_text(values: list[str]) -> str:
    return next((value for value in values if value), "")


def _is_command_result(value: Any) -> bool:
    return isinstance(value, dict) and bool(COMMAND_RESULT_KEYS.intersection(value))


def _extract_command_results(value: Any, *, stage: str | None = None) -> list[dict[str, Any]]:
    if _is_command_result(value):
        payload = {
            "stage": stage or str(value.get("stage") or "command"),
            "returnCode": value.get("returnCode"),
            "stdoutTail": _tail(value.get("stdout")),
            "stderrTail": _tail(value.get("stderr")),
        }
        if value.get("timeoutSeconds") is not None:
            payload["timeoutSeconds"] = value.get("timeoutSeconds")
        if value.get("command") is not None:
            payload["command"] = value.get("command")
        return [payload]

    results: list[dict[str, Any]] = []
    if isinstance(value, dict):
        nested_stage = str(value.get("stage") or stage or "").strip() or None
        for key, item in value.items():
            if key in {"stdout", "stderr"}:
                continue
            child_stage = str(key) if isinstance(key, str) else nested_stage
            results.extend(_extract_command_results(item, stage=child_stage or nested_stage))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            results.extend(_extract_command_results(item, stage=f"{stage or 'item'}[{index}]"))
    return results


def _step_output_summary(updates: dict[str, Any]) -> dict[str, Any]:
    command_results = _extract_command_results(updates.get("result"))
    stdout_tail = _first_text([str(item.get("stdoutTail") or "") for item in command_results])
    stderr_tail = _first_text([str(item.get("stderrTail") or "") for item in command_results])
    timeout_seconds = next(
        (item.get("timeoutSeconds") for item in command_results if item.get("timeoutSeconds") is not None),
        None,
    )
    summary: dict[str, Any] = {}
    if command_results:
        summary["commandResults"] = command_results
    if stdout_tail:
        summary["stdoutTail"] = stdout_tail
    if stderr_tail:
        summary["stderrTail"] = stderr_tail
    if timeout_seconds is not None:
        summary["timeoutSeconds"] = timeout_seconds
    return summary


def _sort_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        jobs,
        key=lambda item: (str(item.get("createdAt") or ""), str(item.get("jobId") or "")),
    )


def load_workflow_jobs(store_path: Path) -> dict[str, dict[str, Any]]:
    try:
        payload = _read_store_payload(store_path)
    except Exception:
        return {}
    raw_jobs = payload.get("jobs") if isinstance(payload, dict) else payload
    if not isinstance(raw_jobs, list):
        return {}
    jobs: dict[str, dict[str, Any]] = {}
    for job in raw_jobs:
        if isinstance(job, dict) and isinstance(job.get("jobId"), str):
            job_id = job["jobId"].strip()
            if job_id:
                jobs[job_id] = job
    return dict((job["jobId"], job) for job in _sort_jobs(list(jobs.values())))


def recover_stale_workflow_jobs(
    jobs: dict[str, dict[str, Any]],
    *,
    now: str,
    legacy_stale_seconds: int = DEFAULT_LEGACY_STALE_SECONDS,
) -> list[str]:
    current_time = _parse_timestamp(now)
    if current_time is None:
        raise ValueError(f"invalid workflow recovery timestamp: {now!r}")
    recovered: list[str] = []
    for job_id, job in jobs.items():
        if str(job.get("status") or "") not in ACTIVE_JOB_STATUSES:
            continue
        expires_at = _parse_timestamp(job.get("leaseExpiresAt"))
        if expires_at is None:
            last_update = _parse_timestamp(job.get("updatedAt") or job.get("createdAt"))
            expires_at = (
                last_update + timedelta(seconds=max(1, legacy_stale_seconds))
                if last_update is not None
                else current_time
            )
        if expires_at > current_time:
            continue

        reason = "workflow worker lease expired; process-local target cannot be recovered"
        current_step = str(job.get("currentStep") or "").strip()
        steps = job.get("steps")
        if current_step and isinstance(steps, list):
            step = next(
                (item for item in steps if isinstance(item, dict) and item.get("step") == current_step),
                None,
            )
            if isinstance(step, dict) and str(step.get("status") or "") not in TERMINAL_STEP_STATUSES:
                step.update({"status": "failed", "error": reason, "finishedAt": now})
        job.update({
            "status": "interrupted",
            "recoverable": False,
            "recoveryReason": "process_restart_unrecoverable_target",
            "recoveredAt": now,
            "finishedAt": now,
            "updatedAt": now,
            "leaseExpiresAt": None,
            "error": reason,
        })
        if current_step:
            job["failedStep"] = current_step
        recovered.append(job_id)
    return recovered


def _merge_jobs(
    persisted: dict[str, dict[str, Any]],
    incoming: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    def merge_job(existing: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
        merged_job = {**existing, **job}
        existing_steps = existing.get("steps")
        incoming_steps = job.get("steps")
        if not isinstance(existing_steps, list) or not isinstance(incoming_steps, list):
            return merged_job
        steps_by_name = {
            str(step.get("step")): dict(step)
            for step in existing_steps
            if isinstance(step, dict) and str(step.get("step") or "").strip()
        }
        step_order = list(steps_by_name)
        for step in incoming_steps:
            if not isinstance(step, dict) or not str(step.get("step") or "").strip():
                continue
            step_name = str(step["step"])
            if step_name not in steps_by_name:
                step_order.append(step_name)
            steps_by_name[step_name] = {**steps_by_name.get(step_name, {}), **step}
        merged_job["steps"] = [steps_by_name[step_name] for step_name in step_order]
        return merged_job

    merged = dict(persisted)
    for job_id, job in incoming.items():
        existing = merged.get(job_id)
        if existing is None or str(job.get("updatedAt") or "") >= str(existing.get("updatedAt") or ""):
            merged[job_id] = merge_job(existing or {}, job)
    return merged


def persist_workflow_jobs(
    store_path: Path,
    jobs: dict[str, dict[str, Any]],
    *,
    max_jobs: int = 200,
    expected_revision: int | None = None,
) -> int:
    with _locked_store(store_path):
        payload = _read_store_payload(store_path)
        revision = workflow_job_store_revision(store_path)
        if expected_revision is not None and revision != expected_revision:
            raise WorkflowJobStoreConflictError(
                f"stale workflow job store update: expected revision {expected_revision}, found {revision}"
            )
        raw_jobs = payload.get("jobs") if isinstance(payload, dict) else []
        persisted = {
            str(item["jobId"]): item
            for item in raw_jobs
            if isinstance(item, dict) and str(item.get("jobId") or "").strip()
        } if isinstance(raw_jobs, list) else {}
        merged = _merge_jobs(persisted, jobs)
        job_list = _sort_jobs(list(merged.values()))[-max_jobs:]
        retained = {str(job["jobId"]): job for job in job_list}
        next_revision = revision + 1
        _atomic_write_revision(store_path, next_revision)
        _atomic_write_store(store_path, {"jobs": job_list})
        jobs.clear()
        jobs.update(retained)
        return next_revision


def recover_workflow_job_store(
    store_path: Path,
    *,
    now: str,
    max_jobs: int = 200,
    legacy_stale_seconds: int = DEFAULT_LEGACY_STALE_SECONDS,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    with _locked_store(store_path):
        jobs = load_workflow_jobs(store_path)
        recovered = recover_stale_workflow_jobs(
            jobs,
            now=now,
            legacy_stale_seconds=legacy_stale_seconds,
        )
        if recovered:
            revision = workflow_job_store_revision(store_path) + 1
            job_list = _sort_jobs(list(jobs.values()))[-max_jobs:]
            _atomic_write_revision(store_path, revision)
            _atomic_write_store(store_path, {"jobs": job_list})
            jobs = {str(job["jobId"]): job for job in job_list}
        return jobs, recovered


def claim_workflow_job(
    store_path: Path,
    jobs: dict[str, dict[str, Any]],
    candidate: dict[str, Any],
    *,
    now: str,
    max_jobs: int = 200,
    legacy_stale_seconds: int = DEFAULT_LEGACY_STALE_SECONDS,
) -> tuple[dict[str, Any], bool]:
    idempotency_key = str(candidate.get("idempotencyKey") or "").strip()
    if not idempotency_key:
        raise ValueError("workflow job candidate requires idempotencyKey")
    with _locked_store(store_path):
        persisted = load_workflow_jobs(store_path)
        recover_stale_workflow_jobs(
            persisted,
            now=now,
            legacy_stale_seconds=legacy_stale_seconds,
        )
        reusable = next(
            (
                job
                for job in persisted.values()
                if job.get("idempotencyKey") == idempotency_key
                and str(job.get("status") or "") in ACTIVE_JOB_STATUSES
            ),
            None,
        )
        reused = reusable is not None
        selected = reusable or candidate
        if not reused:
            persisted[str(candidate["jobId"])] = candidate
        job_list = _sort_jobs(list(persisted.values()))[-max_jobs:]
        revision = workflow_job_store_revision(store_path) + 1
        _atomic_write_revision(store_path, revision)
        _atomic_write_store(store_path, {"jobs": job_list})
        jobs.clear()
        jobs.update({str(job["jobId"]): job for job in job_list})
        return selected, reused


def create_workflow_job(
    jobs: dict[str, dict[str, Any]],
    *,
    job_id: str,
    task_id: str,
    now: NowFactory,
    retry_scope: str | None = None,
    idempotency_key: str | None = None,
    owner_id: str | None = None,
    lease_seconds: int = DEFAULT_JOB_LEASE_SECONDS,
) -> dict[str, Any]:
    timestamp = now()
    job = {
        "jobId": job_id,
        "taskId": task_id,
        "status": "queued",
        "steps": [],
        "currentStep": None,
        "retryScope": retry_scope or "workflow",
        "createdAt": timestamp,
        "updatedAt": timestamp,
    }
    if idempotency_key:
        job["idempotencyKey"] = idempotency_key
    if owner_id:
        job.update(workflow_job_lease_updates(owner_id=owner_id, now=timestamp, lease_seconds=lease_seconds))
    jobs[job_id] = job
    return job


def update_workflow_job(
    jobs: dict[str, dict[str, Any]],
    job_id: str,
    *,
    now: NowFactory,
    **updates: Any,
) -> bool:
    job = jobs.get(job_id)
    if not job:
        return False
    job.update(updates)
    job["updatedAt"] = now()
    return True


def record_workflow_job_step(
    jobs: dict[str, dict[str, Any]],
    job_id: str,
    step: str,
    status: str,
    *,
    now: NowFactory,
    **updates: Any,
) -> bool:
    job = jobs.get(job_id)
    if not job:
        return False
    steps = job.setdefault("steps", [])
    current = next((item for item in steps if item.get("step") == step), None)
    if not current:
        current = {"step": step, "startedAt": now()}
        steps.append(current)
    current.update({"status": status, **updates, **_step_output_summary(updates)})
    job["currentStep"] = step
    if status in TERMINAL_STEP_STATUSES:
        current.setdefault("finishedAt", now())
    if status == "failed":
        job["failedStep"] = step
    job["updatedAt"] = now()
    return True
