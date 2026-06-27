"""Table merge helpers for physical and logical table artifacts."""

from __future__ import annotations

from typing import Any


def single_fragment_logical_table(task_id: str, table: dict[str, Any], index: int) -> dict[str, Any]:
    table_id = table.get("table_id") or f"pt-{index:06d}"
    row_count = int((table.get("quality") or {}).get("row_count") or 0)
    return {
        "logical_table_id": f"lt-{index:06d}",
        "title": table.get("title") or table.get("caption") or table_id,
        "fragment_table_ids": [table_id],
        "merge_status": "single",
        "merge_confidence": 1.0,
        "merge_reasons": ["single_fragment"],
        "header_rows": [],
        "rows": [],
        "html": table.get("html") or "",
        "markdown": table.get("markdown") or "",
        "source_fragments": [
            {"table_id": table_id, "page_number": table.get("page_number") or 1, "row_range": [0, max(0, row_count)]}
        ],
        "evidence_ids": [f"doc:{task_id}:p{table.get('page_number') or 1}:{table_id}"],
        "warnings": [],
    }


def empty_table_relations(task_id: str) -> dict[str, Any]:
    return {"schema_version": "document_table_relations_v1", "task_id": task_id, "relations": []}
