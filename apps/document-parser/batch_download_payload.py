"""Batch download request and manifest helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


SCHEMA_DOCUMENT_PARSE_BATCH_DOWNLOAD = "document_parse_batch_download_v1"
MAX_BATCH_DOWNLOAD_TASKS = 50


def requested_batch_download_task_ids(payload: Mapping[str, Any]) -> list[str] | None:
    raw_task_ids = payload.get("task_ids") or payload.get("taskIds") or []
    if not isinstance(raw_task_ids, list):
        return None

    normalized_ids: list[str] = []
    for raw_id in raw_task_ids:
        task_id = str(raw_id or "").strip()
        if task_id and task_id not in normalized_ids:
            normalized_ids.append(task_id)
    return normalized_ids


def build_batch_download_manifest(
    *,
    batch_id: str,
    requested_task_ids: Sequence[str],
    included: Sequence[dict[str, Any]],
    missing: Sequence[str],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_DOCUMENT_PARSE_BATCH_DOWNLOAD,
        "batch_id": batch_id,
        "requested_task_ids": list(requested_task_ids),
        "included": list(included),
        "missing": list(missing),
        "task_count": len(included),
    }
