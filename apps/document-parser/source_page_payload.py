"""Source page response payload helpers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def build_source_page_payload(
    task_id: str,
    page_number: int,
    blocks: Sequence[dict[str, Any]],
    layout: dict[str, Any],
) -> dict[str, Any]:
    page_blocks = [block for block in blocks if int(block.get("page_number") or 1) == page_number]
    page_meta: dict[str, Any] = {}
    for page in layout.get("pages") or []:
        if isinstance(page, dict) and int(page.get("page_number") or 0) == page_number:
            page_meta = page
            break

    return {
        "task_id": task_id,
        "page_number": page_number,
        "page": {
            "page_number": page_number,
            "page_index": page_number - 1,
            "width": page_meta.get("width") or 0,
            "height": page_meta.get("height") or 0,
            "page_size": page_meta.get("page_size") or [],
            "bbox_unit": page_meta.get("bbox_unit") or "none",
        },
        "blocks": page_blocks,
        "block_count": len(page_blocks),
        "page_image_url": f"/api/source/{task_id}/page-image/{page_number}",
    }
