from __future__ import annotations

from typing import Any, Mapping


def _public_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _public_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_public_copy(item) for item in value]
    return value


def _workflow_step_to_canonical(step: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": step.get("step"),
        "status": step.get("status"),
        "started_at": step.get("startedAt"),
        "finished_at": step.get("finishedAt"),
        "result": _public_copy(step.get("result")),
        "message": step.get("message"),
        "legacy_payload": _public_copy(dict(step)),
    }


def _market_legacy_payload(job: Mapping[str, Any]) -> dict[str, Any]:
    return _public_copy({key: value for key, value in dict(job).items() if key != "target"})


def market_job_to_canonical(job: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "siq_job_envelope_v1",
        "id": job.get("job_id"),
        "kind": job.get("kind"),
        "subject": job.get("subject"),
        "status": job.get("status"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "updated_at": job.get("updated_at"),
        "created_by": _public_copy(job.get("created_by")),
        "result": _public_copy(job.get("result")),
        "error": job.get("error"),
        "steps": [],
        "logs": [],
        "attempts": 1,
        "source_schema": "market_file_backed_job_v1",
        "legacy_payload": _market_legacy_payload(job),
    }


def workflow_job_to_canonical(job: Mapping[str, Any]) -> dict[str, Any]:
    steps = job.get("steps") if isinstance(job.get("steps"), list) else []
    return {
        "schema_version": "siq_job_envelope_v1",
        "id": job.get("jobId"),
        "kind": "workflow-run-remaining",
        "subject": {"task_id": job.get("taskId")},
        "status": job.get("status"),
        "created_at": job.get("createdAt"),
        "started_at": None,
        "finished_at": None,
        "updated_at": job.get("updatedAt"),
        "created_by": None,
        "result": _public_copy(job.get("result")),
        "error": job.get("error"),
        "steps": [_workflow_step_to_canonical(step) for step in steps if isinstance(step, Mapping)],
        "logs": [],
        "attempts": 1,
        "source_schema": "workflow_job_v1",
        "legacy_payload": _public_copy(dict(job)),
    }


def canonical_to_market_public(job: Mapping[str, Any]) -> dict[str, Any]:
    legacy = job.get("legacy_payload") if isinstance(job.get("legacy_payload"), Mapping) else {}
    payload = {
        "job_id": job.get("id"),
        "kind": job.get("kind"),
        "status": job.get("status"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "created_by": _public_copy(job.get("created_by")),
        "result": _public_copy(job.get("result")),
        "error": job.get("error"),
    }
    if "updated_at" in legacy or job.get("updated_at") is not None:
        payload["updated_at"] = job.get("updated_at")
    if "subject" in legacy:
        payload["subject"] = _public_copy(job.get("subject"))
    return payload


def canonical_to_workflow_public(job: Mapping[str, Any]) -> dict[str, Any]:
    legacy = job.get("legacy_payload") if isinstance(job.get("legacy_payload"), Mapping) else {}
    subject = job.get("subject") if isinstance(job.get("subject"), Mapping) else {}
    steps = [
        _public_copy(step.get("legacy_payload"))
        for step in job.get("steps", [])
        if isinstance(step, Mapping) and isinstance(step.get("legacy_payload"), Mapping)
    ]
    return {
        "jobId": job.get("id"),
        "taskId": subject.get("task_id") or legacy.get("taskId"),
        "status": job.get("status"),
        "steps": steps,
        "createdAt": job.get("created_at"),
        "updatedAt": job.get("updated_at"),
        **({"result": _public_copy(job.get("result"))} if "result" in legacy else {}),
        **({"error": job.get("error")} if "error" in legacy else {}),
    }


def load_canonical_compatible_jobs(payload: Any, *, source: str) -> list[dict[str, Any]]:
    if source not in {"market", "workflow"}:
        raise ValueError("source must be market or workflow")
    raw_jobs = payload.get("jobs") if isinstance(payload, Mapping) else payload
    if not isinstance(raw_jobs, list):
        return []
    canonical_jobs: list[dict[str, Any]] = []
    for item in raw_jobs:
        if not isinstance(item, Mapping):
            continue
        if source == "market":
            if not str(item.get("job_id") or "").strip():
                continue
            canonical_jobs.append(market_job_to_canonical(item))
        elif source == "workflow":
            if not str(item.get("jobId") or "").strip():
                continue
            canonical_jobs.append(workflow_job_to_canonical(item))
    return canonical_jobs
