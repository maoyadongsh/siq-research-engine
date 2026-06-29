"""Page coordinate metadata helpers for document parser artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def decode_json_string(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("[", "{")):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return value
    return value


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def page_metadata_from_mineru_middle(middle: Any) -> list[dict[str, Any]]:
    middle = decode_json_string(middle)
    pdf_info = middle.get("pdf_info") if isinstance(middle, dict) else None
    if not isinstance(pdf_info, list):
        return []

    pages: list[dict[str, Any]] = []
    for index, page in enumerate(pdf_info):
        if not isinstance(page, dict):
            continue
        raw_size = page.get("page_size")
        if not isinstance(raw_size, list) or len(raw_size) < 2:
            continue
        try:
            width = float(raw_size[0])
            height = float(raw_size[1])
        except (TypeError, ValueError):
            continue
        if width <= 0 or height <= 0:
            continue
        pages.append(
            {
                "page_number": int(page.get("page_idx") or index) + 1,
                "page_index": int(page.get("page_idx") or index),
                "width": width,
                "height": height,
                "page_size": [width, height],
                "bbox_unit": "pdf_point",
                "source": "raw/mineru/middle.json",
            }
        )
    return pages


def load_mineru_page_metadata(source_dir: Path) -> list[dict[str, Any]]:
    return page_metadata_from_mineru_middle(read_json(source_dir / "middle.json", {}))


def merge_layout_page_metadata(payload: Any, page_metadata: list[dict[str, Any]]) -> Any:
    if not isinstance(payload, dict) or not page_metadata:
        return payload
    pages = payload.setdefault("pages", [])
    if not isinstance(pages, list):
        return payload

    by_page: dict[int, dict[str, Any]] = {}
    for page in pages:
        if isinstance(page, dict):
            try:
                by_page[int(page.get("page_number") or 0)] = page
            except (TypeError, ValueError):
                continue

    for meta in page_metadata:
        try:
            page_number = int(meta.get("page_number") or 0)
            width = float(meta.get("width") or 0)
            height = float(meta.get("height") or 0)
        except (TypeError, ValueError):
            continue
        if page_number <= 0 or width <= 0 or height <= 0:
            continue
        page = by_page.get(page_number)
        if page is None:
            page = {
                "page_number": page_number,
                "page_index": int(meta.get("page_index") or page_number - 1),
                "blocks": [],
            }
            pages.append(page)
            by_page[page_number] = page
        if not page.get("width"):
            page["width"] = width
        if not page.get("height"):
            page["height"] = height
        page.setdefault("page_size", [width, height])
        page.setdefault("bbox_unit", meta.get("bbox_unit") or "pdf_point")
        page.setdefault("metadata_source", meta.get("source") or "raw/mineru/middle.json")

    pages.sort(key=lambda item: int(item.get("page_number") or 0) if isinstance(item, dict) else 0)
    return payload
