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
    submit_config = task.get("submit_config") if isinstance(task.get("submit_config"), Mapping) else {}
    payload = {
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
    if submit_config.get("document_profile"):
        payload["document_profile"] = submit_config["document_profile"]
    if submit_config.get("parser_version"):
        payload["parser_version"] = submit_config["parser_version"]
    if submit_config.get("source_context"):
        payload["source_context"] = submit_config["source_context"]
    return payload


def build_recent_tasks_payload(
    tasks: list[Mapping[str, Any]],
    *,
    has_markdown_artifact: Callable[[Mapping[str, Any]], bool],
) -> dict[str, Any]:
    return {
        "tasks": normalize_recent_tasks(tasks, has_markdown_artifact=has_markdown_artifact),
    }


def build_status_response_payload(
    task: Mapping[str, Any],
    *,
    elapsed_seconds: int | None,
    page_progress: Mapping[str, Any] | None,
    progress_percent: float | None,
    markdown_ready: bool,
    local_queue_position: int | None,
    logs_slice: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if task.get("status") == COMPLETED and page_progress:
        page_progress = dict(page_progress)
        page_progress["processed"] = page_progress["total"]
        page_progress["remaining"] = 0
        progress_percent = 100.0

    submit_config = task.get("submit_config") if isinstance(task.get("submit_config"), Mapping) else {}
    payload = {
        "task_id": task["task_id"],
        "status": task["status"],
        "stage": task["stage"],
        "queue_position": task.get("queue_position"),
        "local_queue_position": local_queue_position,
        "filename": task["filename"],
        "file_size": task.get("file_size"),
        "pdf_page_count": task.get("pdf_page_count"),
        "error": task.get("error"),
        "elapsed_seconds": elapsed_seconds,
        "total_pages": task.get("pdf_page_count"),
        "processed_pages": page_progress["processed"] if page_progress else None,
        "progress_percent": progress_percent,
        "markdown_ready": bool(markdown_ready),
        "log_count": len(task.get("logs", [])),
        "logs": logs_slice if logs_slice is not None else [],
    }
    market = submit_config.get("market") or task.get("market")
    if market:
        payload["market"] = market
    if task.get("parse_config_hash"):
        payload["parse_config_hash"] = task["parse_config_hash"]
    if submit_config.get("document_profile"):
        payload["document_profile"] = submit_config["document_profile"]
    if submit_config.get("parser_version"):
        payload["parser_version"] = submit_config["parser_version"]
    if submit_config.get("source_context"):
        payload["source_context"] = submit_config["source_context"]
    return payload


def build_result_response_payload(markdown: Any, artifacts: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "markdown": markdown,
        "artifacts": dict(artifacts),
    }


def build_quality_response_payload(quality_report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "quality": dict(quality_report),
    }


def build_financial_response_payload(
    financial_data: Mapping[str, Any],
    financial_checks: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "financial_data": dict(financial_data),
        "financial_checks": dict(financial_checks),
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
