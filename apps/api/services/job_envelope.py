from __future__ import annotations

from typing import Any, Mapping

CANONICAL_SCHEMA_VERSION = "siq_job_envelope_v1"


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


def _is_canonical_job(job: Mapping[str, Any]) -> bool:
    return job.get("schema_version") == CANONICAL_SCHEMA_VERSION and bool(str(job.get("id") or "").strip())


def _canonical_step_to_workflow_public(step: Mapping[str, Any]) -> dict[str, Any]:
    legacy = step.get("legacy_payload") if isinstance(step.get("legacy_payload"), Mapping) else None
    if legacy is not None:
        return _public_copy(legacy)

    payload: dict[str, Any] = {
        "step": step.get("name"),
        "status": step.get("status"),
    }
    if step.get("started_at") is not None:
        payload["startedAt"] = step.get("started_at")
    if step.get("finished_at") is not None:
        payload["finishedAt"] = step.get("finished_at")
    if step.get("result") is not None:
        payload["result"] = _public_copy(step.get("result"))
    if step.get("message") is not None:
        payload["message"] = step.get("message")
    return payload


def market_job_to_canonical(job: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": CANONICAL_SCHEMA_VERSION,
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
        "owner": job.get("owner"),
        "heartbeat_at": job.get("heartbeat_at"),
        "interrupted_reason": job.get("interrupted_reason"),
        "durability_status": job.get("durability_status"),
        "persistence_error": job.get("persistence_error"),
        "steps": [],
        "logs": [],
        "attempts": int(job.get("attempt") or 1),
        "source_schema": "market_file_backed_job_v1",
        "legacy_payload": _market_legacy_payload(job),
    }


def workflow_job_to_canonical(job: Mapping[str, Any]) -> dict[str, Any]:
    steps = job.get("steps") if isinstance(job.get("steps"), list) else []
    return {
        "schema_version": CANONICAL_SCHEMA_VERSION,
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
    base_payload = {
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
    payload = (
        {key: value for key, value in base_payload.items() if key in legacy}
        if legacy
        else dict(base_payload)
    )
    if "updated_at" in legacy or job.get("updated_at") is not None:
        payload["updated_at"] = job.get("updated_at")
    if "subject" in legacy:
        payload["subject"] = _public_copy(job.get("subject"))
    extra_fields = (
        "owner",
        "heartbeat_at",
        "interrupted_reason",
        "durability_status",
        "persistence_error",
    )
    for field in extra_fields:
        if field in legacy or job.get(field) is not None:
            payload[field] = job.get(field)
    if "attempt" in legacy or int(job.get("attempts") or 1) != 1:
        payload["attempt"] = int(job.get("attempts") or 1)
    return payload


def canonical_to_workflow_public(job: Mapping[str, Any]) -> dict[str, Any]:
    legacy = job.get("legacy_payload") if isinstance(job.get("legacy_payload"), Mapping) else {}
    subject = job.get("subject") if isinstance(job.get("subject"), Mapping) else {}
    steps = [_canonical_step_to_workflow_public(step) for step in job.get("steps", []) if isinstance(step, Mapping)]
    return {
        "jobId": job.get("id"),
        "taskId": subject.get("task_id") or legacy.get("taskId"),
        "status": job.get("status"),
        "steps": steps,
        "createdAt": job.get("created_at"),
        "updatedAt": job.get("updated_at"),
        **({"result": _public_copy(job.get("result"))} if "result" in legacy or job.get("result") is not None else {}),
        **({"error": job.get("error")} if "error" in legacy or job.get("error") is not None else {}),
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
        if _is_canonical_job(item):
            canonical_jobs.append(_public_copy(dict(item)))
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
