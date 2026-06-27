"""Helpers for source map evidence records."""

from __future__ import annotations

from typing import Any


def evidence_id(task_id: str, page_number: int, local_id: str) -> str:
    return f"doc:{task_id}:p{int(page_number or 1)}:{local_id}"


def source_map_coverage(blocks: list[dict[str, Any]]) -> float:
    if not blocks:
        return 0.0
    covered = sum(1 for block in blocks if (block.get("source_ref") or {}).get("evidence_id"))
    return round(covered / len(blocks), 4)
