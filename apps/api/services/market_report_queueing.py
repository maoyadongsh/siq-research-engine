from __future__ import annotations

from typing import Any, Callable


def job_created_by(user: Any | None) -> dict[str, Any] | None:
    if user is None:
        return None
    return {
        "id": getattr(user, "id", None),
        "username": getattr(user, "username", None),
        "email": getattr(user, "email", None),
        "full_name": getattr(user, "full_name", None),
        "role": getattr(user, "role", None),
    }


def queue_market_report_job(
    *,
    job_service: Any,
    kind: str,
    target: Callable[[], dict[str, Any]],
    created_by: Any | None = None,
) -> dict[str, Any]:
    job = job_service.start(
        kind,
        target,
        created_by=job_created_by(created_by),
    )
    return {"ok": True, "queued": True, **job}


def get_market_report_job(*, job_service: Any, job_id: str) -> dict[str, Any] | None:
    return job_service.get(job_id)
