"""Status response payload helpers for the document parser app."""

from __future__ import annotations

from collections.abc import Sequence

from contracts import COMPLETED, COMPLETED_WITH_WARNINGS


def build_task_status_payload(task: dict, logs: Sequence[dict], log_count: int) -> dict:
    payload = dict(task)
    payload["logs"] = list(logs)
    payload["log_count"] = log_count
    payload["artifacts_ready"] = payload.get("status") in {COMPLETED, COMPLETED_WITH_WARNINGS}
    return payload
