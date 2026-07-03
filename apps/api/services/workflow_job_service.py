from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable


NowFactory = Callable[[], str]


def _sort_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        jobs,
        key=lambda item: (str(item.get("createdAt") or ""), str(item.get("jobId") or "")),
    )


def load_workflow_jobs(store_path: Path) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(store_path.read_text(encoding="utf-8"))
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


def persist_workflow_jobs(store_path: Path, jobs: dict[str, dict[str, Any]], *, max_jobs: int = 200) -> None:
    try:
        store_path.parent.mkdir(parents=True, exist_ok=True)
        job_list = _sort_jobs(list(jobs.values()))[-max_jobs:]
        tmp_path = store_path.with_suffix(f"{store_path.suffix}.tmp")
        tmp_path.write_text(json.dumps({"jobs": job_list}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(store_path)
    except Exception:
        return


def create_workflow_job(
    jobs: dict[str, dict[str, Any]],
    *,
    job_id: str,
    task_id: str,
    now: NowFactory,
) -> dict[str, Any]:
    timestamp = now()
    job = {
        "jobId": job_id,
        "taskId": task_id,
        "status": "queued",
        "steps": [],
        "createdAt": timestamp,
        "updatedAt": timestamp,
    }
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
    current.update({"status": status, **updates})
    if status in {"succeeded", "failed", "skipped"}:
        current.setdefault("finishedAt", now())
    job["updatedAt"] = now()
    return True
