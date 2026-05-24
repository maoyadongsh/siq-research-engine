"""Task state helpers shared by the Web app and tests."""

from __future__ import annotations


COMPLETED = "completed"
COMPLETED_MISSING_ARTIFACT = "completed_missing_artifact"
FAILED = "failed"
CANCELLED = "cancelled"

TERMINAL_STATUSES = {
    COMPLETED,
    COMPLETED_MISSING_ARTIFACT,
    FAILED,
    "error",
    "failure",
    CANCELLED,
}

ACTIVE_UPSTREAM_STATUSES = {"submitted", "pending", "processing"}


def is_terminal_status(status):
    return str(status or "").lower() in TERMINAL_STATUSES


def is_success_status(status):
    return str(status or "").lower() in {COMPLETED, "success", "done", "finished"}


def is_failed_status(status):
    return str(status or "").lower() in {FAILED, "error", "failure", COMPLETED_MISSING_ARTIFACT}


def missing_artifact_message():
    return "任务已完成，但本地 Markdown 结果不存在；请尝试重新拉取结果或重新解析。"

