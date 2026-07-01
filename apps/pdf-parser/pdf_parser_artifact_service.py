"""Artifact path, file, and display helpers for PDF parser results."""

from __future__ import annotations

import base64
import io
import json
import os
import re
import threading
from typing import Any, Callable, Mapping
import zipfile


ARTIFACT_OPEN_ALLOWLIST = {
    "result.md": ("text/markdown; charset=utf-8", False),
    "result_complete.md": ("text/markdown; charset=utf-8", False),
    "document_full.json": ("application/json; charset=utf-8", False),
    "quality_report.json": ("application/json; charset=utf-8", False),
    "table_relations.json": ("application/json; charset=utf-8", False),
    "table_index.json": ("application/json; charset=utf-8", False),
    "financial_data.json": ("application/json; charset=utf-8", False),
    "financial_checks.json": ("application/json; charset=utf-8", False),
    "middle.json": ("application/json; charset=utf-8", False),
    "content_list.json": ("application/json; charset=utf-8", False),
    "content_list_enhanced.json": ("application/json; charset=utf-8", False),
    "model_output.json": ("application/json; charset=utf-8", False),
}


def classify_open_artifact_name(
    task_id: str,
    raw_artifact_name: str,
    result_directory: str,
    *,
    sanitize_filename: Callable[[str], str],
    allowlist: Mapping[str, tuple[str, bool]] | None = None,
) -> dict[str, Any]:
    """Classify an open_artifact raw name without touching Flask or the filesystem."""
    artifact_name = str(raw_artifact_name or "")
    images_dir = os.path.join(result_directory, "images")
    if artifact_name == "images/download":
        return {
            "kind": "images_download",
            "artifact": "images",
            "images_dir": images_dir,
            "download_name": f"{task_id}_images.zip",
        }
    if artifact_name == "images":
        return {
            "kind": "images_index",
            "artifact": "images",
            "images_dir": images_dir,
        }
    if artifact_name.startswith("images/"):
        image_name = sanitize_filename(artifact_name.split("/", 1)[1])
        return {
            "kind": "image_file",
            "artifact": "images",
            "image_name": image_name,
            "path": os.path.join(images_dir, image_name),
            "mimetype": "image/png" if image_name.lower().endswith(".png") else "image/jpeg",
        }

    safe_artifact_name = sanitize_filename(artifact_name)
    artifact_allowlist = ARTIFACT_OPEN_ALLOWLIST if allowlist is None else allowlist
    if safe_artifact_name not in artifact_allowlist:
        return {
            "kind": "forbidden",
            "artifact_name": safe_artifact_name,
        }

    mimetype, binary = artifact_allowlist[safe_artifact_name]
    return {
        "kind": "artifact_file",
        "artifact_name": safe_artifact_name,
        "path": os.path.join(result_directory, safe_artifact_name),
        "mimetype": mimetype,
        "binary": binary,
    }


def legacy_markdown_path(task: dict[str, Any], results_folder: str) -> str:
    return os.path.join(results_folder, f"{task['task_id']}.md")


def canonical_markdown_path(task: dict[str, Any], results_folder: str) -> str:
    return os.path.join(results_folder, task["task_id"], "result.md")


def markdown_artifact_path(task: dict[str, Any], results_folder: str) -> str | None:
    candidates = []
    if task.get("markdown_path"):
        candidates.append(task["markdown_path"])
    candidates.append(canonical_markdown_path(task, results_folder))
    candidates.append(legacy_markdown_path(task, results_folder))

    seen = set()
    for path in candidates:
        if not path or path in seen:
            continue
        seen.add(path)
        if os.path.exists(path):
            return path
    return None


def has_markdown_artifact(task: dict[str, Any], results_folder: str) -> bool:
    return markdown_artifact_path(task, results_folder) is not None


def result_dir(task: dict[str, Any], results_folder: str) -> str:
    return os.path.join(results_folder, task["task_id"])


def write_json(path: str, payload: Any) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp_path = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as outfile:
            json.dump(payload, outfile, ensure_ascii=False, indent=2)
            outfile.write("\n")
            outfile.flush()
            os.fsync(outfile.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def load_json_artifact(
    task: dict[str, Any],
    filename: str,
    *,
    results_folder: str,
    read_json_cached: Callable[[str], Any],
    coerce_json_artifact: Callable[[Any], Any] | None = None,
) -> Any:
    path = os.path.join(result_dir(task, results_folder), filename)
    if not os.path.exists(path):
        return None
    payload = read_json_cached(path)
    return coerce_json_artifact(payload) if coerce_json_artifact else payload


def write_markdown(task: dict[str, Any], markdown: str | None, *, results_folder: str) -> str | None:
    if markdown is None:
        return None
    directory = result_dir(task, results_folder)
    os.makedirs(directory, exist_ok=True)
    markdown_path = os.path.join(directory, "result.md")
    with open(markdown_path, "w", encoding="utf-8") as outfile:
        outfile.write(markdown)
    task["markdown_path"] = markdown_path
    return markdown_path


def decode_image_payload(payload: Any) -> bytes | None:
    if isinstance(payload, dict):
        payload = payload.get("data") or payload.get("content") or payload.get("base64")
    if not isinstance(payload, str):
        return None
    if "," in payload and payload.lower().startswith("data:"):
        payload = payload.split(",", 1)[1]
    try:
        return base64.b64decode(payload, validate=False)
    except Exception:
        return None


def save_images(images: Any, images_dir: str) -> int:
    if not isinstance(images, dict):
        return 0
    os.makedirs(images_dir, exist_ok=True)
    saved = 0
    for name, payload in images.items():
        image_bytes = decode_image_payload(payload)
        if not image_bytes:
            continue
        safe_name = os.path.basename(str(name)) or f"image_{saved + 1}.jpg"
        if not os.path.splitext(safe_name)[1]:
            safe_name += ".jpg"
        with open(os.path.join(images_dir, safe_name), "wb") as outfile:
            outfile.write(image_bytes)
        saved += 1
    return saved


def artifact_status(task: dict[str, Any], *, results_folder: str) -> dict[str, dict[str, Any]]:
    directory = result_dir(task, results_folder)
    artifacts = {}
    for name in (
        "result.md",
        "result_complete.md",
        "document_full.json",
        "content_list_enhanced.json",
        "quality_report.json",
        "table_relations.json",
        "table_index.json",
        "financial_data.json",
        "financial_checks.json",
        "middle.json",
        "content_list.json",
        "model_output.json",
    ):
        path = os.path.join(directory, name)
        artifacts[name] = {
            "exists": os.path.exists(path),
            "path": path if os.path.exists(path) else "",
            "url": f"/api/artifact/{task['task_id']}/{name}" if os.path.exists(path) else "",
        }
    images_dir = os.path.join(directory, "images")
    artifacts["images"] = {
        "exists": os.path.isdir(images_dir),
        "path": images_dir if os.path.isdir(images_dir) else "",
        "url": f"/api/artifact/{task['task_id']}/images" if os.path.isdir(images_dir) else "",
    }
    return artifacts


def image_artifact_names(images_dir: str) -> list[str]:
    return [
        name
        for name in sorted(os.listdir(images_dir))
        if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        and os.path.isfile(os.path.join(images_dir, name))
    ]


def build_images_zip(images_dir: str, image_names: list[str]) -> io.BytesIO:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for name in image_names:
            zip_file.write(os.path.join(images_dir, name), arcname=name)
    archive.seek(0)
    return archive


def markdown_excerpt(markdown: str, line: int | None, radius: int = 12) -> list[dict[str, Any]]:
    lines = markdown.splitlines()
    if not lines:
        return []
    line = max(1, min(int(line or 1), len(lines)))
    start = max(1, line - radius)
    end = min(len(lines), line + radius)
    return [
        {
            "line": idx,
            "text": lines[idx - 1],
            "focus": idx == line,
        }
        for idx in range(start, end + 1)
    ]


def table_html_by_index(markdown: str, table_index: int) -> str:
    for idx, match in enumerate(
        re.finditer(r"<table\b.*?</table>", markdown, flags=re.IGNORECASE | re.DOTALL),
        start=1,
    ):
        if idx == table_index:
            return match.group(0)
    return ""


def apply_table_corrections(markdown: str, corrections: dict[str, Any] | None) -> tuple[str, int]:
    tables = corrections.get("tables", {}) if isinstance(corrections, dict) else {}
    replacements = {}
    for key, item in tables.items():
        if not isinstance(item, dict):
            continue
        if item.get("review_status") != "fixed":
            continue
        table_markdown = item.get("table_markdown")
        if not table_markdown:
            continue
        try:
            table_index = int(item.get("table_index") or key)
        except (TypeError, ValueError):
            continue
        replacements[table_index] = str(table_markdown)

    if not replacements:
        return markdown, 0

    replaced_count = 0

    def replace_match(match):
        nonlocal replaced_count
        replace_match.table_index += 1
        corrected = replacements.get(replace_match.table_index)
        if corrected is None:
            return match.group(0)
        replaced_count += 1
        return corrected

    replace_match.table_index = 0
    corrected_markdown = re.sub(
        r"<table\b.*?</table>",
        replace_match,
        markdown,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return corrected_markdown, replaced_count
