"""Source image response payload helpers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def find_figure_by_image_id(figures: Sequence[dict[str, Any]], image_id: str) -> dict[str, Any] | None:
    for figure in figures:
        if figure.get("image_id") == image_id:
            return figure
    return None


def build_source_image_payload(task_id: str, image_id: str, figure: dict[str, Any]) -> dict[str, Any]:
    image_path = str(figure.get("image_path") or "")
    crop_path = str(figure.get("crop_path") or image_path)
    thumbnail_path = str(figure.get("thumbnail_path") or "")
    return {
        "task_id": task_id,
        "image_id": image_id,
        "page_number": figure.get("page_number") or 1,
        "bbox": figure.get("bbox") or [],
        "bbox_unit": figure.get("bbox_unit") or "none",
        "caption": figure.get("caption") or figure.get("alt_text") or "",
        "ocr_text": figure.get("ocr_text") or "",
        "figure": figure,
        "image_url": f"/api/artifact/{task_id}/{image_path}" if image_path else "",
        "crop_url": f"/api/artifact/{task_id}/{crop_path}" if crop_path else "",
        "thumbnail_url": f"/api/artifact/{task_id}/{thumbnail_path}" if thumbnail_path else "",
        "open_artifact_url": f"/api/documents/artifact/{task_id}/{image_path}" if image_path else "",
    }
