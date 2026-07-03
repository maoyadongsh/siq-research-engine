"""Source workbench helpers for PDF parser artifacts."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Callable

from pdf_parser_artifact_service import result_dir, write_json


def _coerce_positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _coerce_nonnegative_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _coerce_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def corrections_path(task: dict[str, Any], *, results_folder: str) -> str:
    return os.path.join(result_dir(task, results_folder), "corrections.json")


def load_corrections(task: dict[str, Any], *, results_folder: str) -> dict[str, Any]:
    path = corrections_path(task, results_folder=results_folder)
    if not os.path.exists(path):
        return {
            "schema_version": 1,
            "task_id": task["task_id"],
            "filename": task.get("filename"),
            "tables": {},
            "updated_at": None,
        }
    with open(path, "r", encoding="utf-8") as infile:
        payload = json.load(infile)
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("schema_version", 1)
    payload.setdefault("task_id", task["task_id"])
    payload.setdefault("filename", task.get("filename"))
    payload.setdefault("tables", {})
    return payload


def save_table_correction(
    task: dict[str, Any],
    table_item: dict[str, Any],
    payload: dict[str, Any],
    *,
    results_folder: str,
    now_iso: Callable[[], str],
) -> dict[str, Any]:
    corrections = load_corrections(task, results_folder=results_folder)
    tables = corrections.setdefault("tables", {})
    table_key = str(table_item["table_index"])
    review_status = str(payload.get("review_status") or "needs_fix")
    if review_status not in {"unreviewed", "correct", "needs_fix", "fixed", "ignored"}:
        review_status = "needs_fix"

    record = {
        "table_index": table_item["table_index"],
        "markdown_line": table_item.get("line"),
        "pdf_page_number": table_item.get("pdf_page_number"),
        "bbox": table_item.get("bbox"),
        "suspect_reasons": table_item.get("suspect_reasons", []),
        "review_status": review_status,
        "table_markdown": str(payload.get("table_markdown") or "")[:1000000],
        "note": str(payload.get("note") or "")[:20000],
        "updated_at": now_iso(),
    }
    tables[table_key] = record
    corrections["updated_at"] = record["updated_at"]
    os.makedirs(result_dir(task, results_folder), exist_ok=True)
    write_json(corrections_path(task, results_folder=results_folder), corrections)
    return record


def page_bbox_extent(
    task: dict[str, Any],
    page_index: int | None,
    *,
    load_json_artifact: Callable[[dict[str, Any], str], Any],
    page_bbox_extent_from_content_list: Callable[[Any, int | None], Any],
) -> Any:
    content_list = load_json_artifact(task, "content_list.json")
    return page_bbox_extent_from_content_list(content_list, page_index)


def page_content_payload(
    task: dict[str, Any],
    page_number: int,
    *,
    report: dict[str, Any] | None = None,
    focus_table: int | None = None,
    load_json_artifact: Callable[[dict[str, Any], str], Any],
    page_content_payload_from_content_list: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    page_number = int(page_number)
    if page_number <= 0:
        raise ValueError("Invalid page number")
    content_list = load_json_artifact(task, "content_list.json")
    return page_content_payload_from_content_list(content_list, page_number, report=report, focus_table=focus_table)


def find_source_table(report: dict[str, Any], table_index: int) -> dict[str, Any] | None:
    target_table_index = _coerce_positive_int(table_index)
    if target_table_index is None:
        return None
    table_index_items = report.get("table_index", []) if isinstance(report, dict) else []
    if not isinstance(table_index_items, list):
        return None
    for item in table_index_items:
        if not isinstance(item, dict):
            continue
        if _coerce_positive_int(item.get("table_index")) == target_table_index:
            return item
    return None


def source_table_pdf_page_image_payload(
    *,
    task_id: str,
    task: dict[str, Any],
    table_item: dict[str, Any],
    bbox_extent: Any,
) -> dict[str, Any]:
    pdf_page_number = _coerce_positive_int(table_item.get("pdf_page_number"))
    return {
        "url": f"/api/pdf_page/{task_id}/{pdf_page_number}" if pdf_page_number else "",
        "page_number": pdf_page_number,
        "pdf_page_number": pdf_page_number,
        "printed_page_number": table_item.get("printed_page_number"),
        "page_count": task.get("pdf_page_count"),
        "bbox": _coerce_list(table_item.get("bbox")),
        "bbox_extent": bbox_extent,
    }


def _fallback_source_table_page_content(table_item: dict[str, Any]) -> dict[str, Any]:
    page_number = _coerce_positive_int(table_item.get("pdf_page_number")) or 1
    page_index = _coerce_nonnegative_int(table_item.get("pdf_page_index"))
    return {
        "page_number": page_number,
        "pdf_page_number": page_number,
        "printed_page_number": table_item.get("printed_page_number"),
        "page_index": page_index if page_index is not None else page_number - 1,
        "block_count": 0,
        "table_count": 0,
        "page_tables": [],
        "blocks": [],
    }


def source_table_payload(
    *,
    task_id: str,
    task: dict[str, Any],
    table_item: dict[str, Any],
    table_html: str,
    markdown_excerpt: str,
    artifacts: dict[str, Any],
    correction: Any,
    page_content: dict[str, Any],
    bbox_extent: Any,
) -> dict[str, Any]:
    if not isinstance(page_content, dict):
        page_content = _fallback_source_table_page_content(table_item)
    return {
        "task_id": task_id,
        "filename": task.get("filename"),
        "table": table_item,
        "table_html": table_html,
        "markdown_excerpt": markdown_excerpt,
        "artifacts": artifacts if isinstance(artifacts, dict) else {},
        "correction": correction,
        "page_content": page_content,
        "pdf_page_image": source_table_pdf_page_image_payload(
            task_id=task_id,
            task=task,
            table_item=table_item,
            bbox_extent=bbox_extent,
        ),
    }


def pdf_page_image_path(task: dict[str, Any], page_number: int, *, results_folder: str) -> str:
    page_number = int(page_number)
    page_dir = os.path.join(result_dir(task, results_folder), "pdf_pages")
    os.makedirs(page_dir, exist_ok=True)
    return os.path.join(page_dir, f"page_{page_number:04d}.png")


def ensure_pdf_page_image(task: dict[str, Any], page_number: int, *, results_folder: str) -> str:
    page_number = int(page_number)
    if page_number <= 0:
        raise ValueError("Invalid page number")
    image_path = pdf_page_image_path(task, page_number, results_folder=results_folder)
    if os.path.exists(image_path) and os.path.getsize(image_path) > 0:
        return image_path

    upload_path = task.get("upload_path")
    if not upload_path or not os.path.exists(upload_path):
        raise FileNotFoundError("Original PDF not found")

    prefix = os.path.join(os.path.dirname(image_path), f"page_{page_number:04d}")
    subprocess.run(
        [
            "pdftoppm",
            "-f",
            str(page_number),
            "-l",
            str(page_number),
            "-png",
            "-r",
            "144",
            upload_path,
            prefix,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    generated_path = f"{prefix}-{page_number}.png"
    if not os.path.exists(generated_path):
        generated_candidates = [
            os.path.join(os.path.dirname(image_path), name)
            for name in os.listdir(os.path.dirname(image_path))
            if name.startswith(os.path.basename(prefix) + "-") and name.endswith(".png")
        ]
        if generated_candidates:
            generated_path = generated_candidates[0]
    if generated_path != image_path and os.path.exists(generated_path):
        os.replace(generated_path, image_path)
    if not os.path.exists(image_path):
        raise FileNotFoundError("Rendered page image not found")
    return image_path
