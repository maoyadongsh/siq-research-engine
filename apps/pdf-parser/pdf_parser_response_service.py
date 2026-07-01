"""Pure response helpers for PDF parser task payloads."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from task_store import COMPLETED, COMPLETED_MISSING_ARTIFACT

DEFAULT_RECENT_TASK_LIMIT = 300
MIN_RECENT_TASK_LIMIT = 100
MAX_RECENT_TASK_LIMIT = 1000


def build_task_duplicate_payload(
    task: Mapping[str, Any] | None,
    *,
    has_markdown_artifact: Callable[[Mapping[str, Any]], bool],
) -> dict[str, Any] | None:
    if not task:
        return None
    return {
        "task_id": task.get("task_id"),
        "filename": task.get("filename"),
        "market": task.get("market"),
        "status": task.get("status"),
        "stage": task.get("stage"),
        "created_at": task.get("created_at"),
        "uploaded_at": task.get("uploaded_at"),
        "completed_at": task.get("completed_at"),
        "pdf_page_count": task.get("pdf_page_count"),
        "markdown_ready": bool(has_markdown_artifact(task)),
    }


def clamp_recent_task_limit(
    raw_value: Any,
    *,
    default: int = DEFAULT_RECENT_TASK_LIMIT,
    minimum: int = MIN_RECENT_TASK_LIMIT,
    maximum: int = MAX_RECENT_TASK_LIMIT,
) -> int:
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def normalize_recent_task(
    task: Mapping[str, Any],
    *,
    has_markdown_artifact: Callable[[Mapping[str, Any]], bool],
) -> dict[str, Any]:
    normalized = dict(task)
    markdown_ready = bool(has_markdown_artifact(normalized))
    if normalized.get("status") == COMPLETED and not markdown_ready:
        normalized["status"] = COMPLETED_MISSING_ARTIFACT
        normalized["stage"] = COMPLETED_MISSING_ARTIFACT
    normalized["markdown_ready"] = markdown_ready
    normalized.pop("markdown_path", None)
    return normalized


def normalize_recent_tasks(
    tasks: list[Mapping[str, Any]],
    *,
    has_markdown_artifact: Callable[[Mapping[str, Any]], bool],
) -> list[dict[str, Any]]:
    return [
        normalize_recent_task(task, has_markdown_artifact=has_markdown_artifact)
        for task in tasks
    ]
