"""Task lifecycle state transitions for the PDF parser queue."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Mapping

import pdf_parser_task_repository as task_repository


def calc_page_progress(
    task: Mapping[str, Any],
    elapsed: float | int | None,
    *,
    page_estimate_seconds: float,
) -> dict[str, Any] | None:
    if elapsed is None or elapsed <= 0:
        return None
    total = task.get("pdf_page_count")
    if not total or total <= 0:
        return None
    processed = min(total, max(0, int(elapsed / page_estimate_seconds)))
    remaining = max(0, total - processed)
    return {"total": total, "processed": processed, "remaining": remaining}


def calc_progress_percent(
    task: Mapping[str, Any],
    elapsed: float | int | None,
    *,
    page_estimate_seconds: float,
) -> float | None:
    total = task.get("pdf_page_count")
    if not total or total <= 0 or elapsed is None or elapsed <= 0:
        return None
    estimated_pages = min(float(total), max(0.0, elapsed / page_estimate_seconds))
    if total <= 0:
        return None
    return round((estimated_pages / float(total)) * 100, 1)


def build_cancel_task_update(
    task: Mapping[str, Any],
    *,
    upstream_cancelled: bool,
    now_iso: str,
) -> dict[str, Any]:
    mineru_task_id = task.get("mineru_task_id")
    if upstream_cancelled:
        message = "任务已取消，已通知 MinerU 停止处理。"
    elif mineru_task_id:
        message = "已停止本地查看；MinerU 后端可能仍在处理。"
    else:
        message = "任务已从本地排队队列中移除。"
    return {
        "patch": {
            "cancelled": True,
            "status": "cancelled",
            "stage": "cancelled",
            "completed_at": task.get("completed_at") or now_iso,
        },
        "log": {"message": message, "level": "warn"},
    }


def build_status_failure_update(
    task: Mapping[str, Any],
    *,
    error_detail: str,
    tolerance: int,
    now_iso: str,
) -> dict[str, Any]:
    failures = int(task.get("consecutive_status_failures") or 0) + 1
    error = f"任务状态查询失败: {error_detail}"
    patch: dict[str, Any] = {
        "consecutive_status_failures": failures,
        "error": error,
    }
    if failures >= tolerance:
        patch.update({
            "status": "failed",
            "stage": "failed",
            "completed_at": task.get("completed_at") or now_iso,
        })
        log = {"message": error, "level": "error"}
    else:
        log = {
            "message": f"状态查询超时，第 {failures}/{tolerance} 次，继续等待...",
            "level": "warn",
        }
    return {"patch": patch, "log": log}


def stale_submitting_cutoff_iso(now, stale_seconds):
    return (now - timedelta(seconds=stale_seconds)).replace(microsecond=0).isoformat() + "Z"


def claim_next_queued_task(db_path, *, normalize_task=None, lock=None):
    return task_repository.claim_next_queued_task(
        db_path,
        normalize_task=normalize_task,
        lock=lock,
    )


def recover_stale_submitting_tasks(db_path, *, stale_seconds, now_factory, lock=None):
    cutoff = stale_submitting_cutoff_iso(now_factory(), stale_seconds)
    return task_repository.recover_stale_submitting_tasks(
        db_path,
        cutoff,
        lock=lock,
    )
