from __future__ import annotations

from typing import Any, Callable

from services import job_envelope

CANONICAL_MARKET_JOB_KINDS = {"market-ingestion-eval"}


class MarketReportJobError(Exception):
    def __init__(self, status_code: int, detail: Any):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


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


def _market_job_public_payload(job: dict[str, Any]) -> dict[str, Any]:
    canonical = (
        job
        if job.get("schema_version") == job_envelope.CANONICAL_SCHEMA_VERSION
        else job_envelope.market_job_to_canonical(job)
    )
    return job_envelope.canonical_to_market_public(canonical)


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
    if kind in CANONICAL_MARKET_JOB_KINDS and isinstance(job, dict):
        job = _market_job_public_payload(job)
    return {"ok": True, "queued": True, **job}


def run_or_queue_market_report_job(
    *,
    wait: bool,
    job_service: Any,
    kind: str,
    target: Callable[[], dict[str, Any]],
    created_by: Any | None = None,
) -> dict[str, Any]:
    if wait:
        return target()
    return queue_market_report_job(
        job_service=job_service,
        kind=kind,
        target=target,
        created_by=created_by,
    )


def get_market_report_job(*, job_service: Any, job_id: str) -> dict[str, Any] | None:
    job = job_service.get(job_id)
    if isinstance(job, dict) and job.get("kind") in CANONICAL_MARKET_JOB_KINDS:
        return _market_job_public_payload(job)
    return job


def market_report_job_status(*, job_service: Any, job_id: str) -> dict[str, Any]:
    job = get_market_report_job(job_service=job_service, job_id=job_id)
    if not job:
        raise MarketReportJobError(404, "Job not found")
    return job
