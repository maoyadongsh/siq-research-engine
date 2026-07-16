"""Import existing MinerU output directories into generic document artifacts."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from contracts import ParseConfig, ParseOutput, SourceFile
from file_utils import guess_mime_type, safe_client_filename, sha256_file, validate_extension
from page_metadata import page_metadata_from_mineru_middle
from providers.simple import _blocks_to_markdown, _markdown_table


def parse_mineru_output_dir(task_id: str, source_dir: Path, config: ParseConfig | None = None) -> tuple[SourceFile, ParseOutput]:
    source_dir = source_dir.resolve()
    content_list = _read_json(source_dir / "content_list.json", [])
    middle = _read_json(source_dir / "middle.json", {})
    enhanced = _read_json(source_dir / "content_list_enhanced.json", {})
    markdown = _read_text(source_dir / "result_complete.md") or _read_text(source_dir / "result.md")
    metadata = _read_json(source_dir / "metadata.json", {}) or _read_json(source_dir / "document_full.json", {})
    source = _source_file_from_dir(task_id, source_dir, metadata)
    normalized_items = _normalize_content_list(content_list)
    page_metadata = page_metadata_from_mineru_middle(middle)
    page_count = _page_count(normalized_items, middle, enhanced)
    blocks = _content_items_to_blocks(task_id, normalized_items)
    if not markdown:
        markdown = _blocks_to_markdown(blocks)
    tables = _tables_from_content_list(task_id, normalized_items)
    tables.extend(_tables_from_enhanced(task_id, enhanced, len(tables)))
    figures = _figures_from_content_list(task_id, normalized_items, source_dir)
    figures.extend(_figures_from_enhanced(task_id, enhanced, source_dir, len(figures)))
    warnings = []
    if not normalized_items:
        warnings.append({"code": "empty_mineru_content_list", "severity": "warning", "message": "MinerU content_list 为空或不可解析。"})
    return source, ParseOutput(
        markdown=markdown,
        blocks=blocks,
        tables=_dedupe_tables(tables),
        figures=_dedupe_figures(figures),
        page_metadata=page_metadata,
        warnings=warnings,
        page_count=page_count,
        provider_name="mineru_import",
        upstream_parser_version=str((metadata.get("mineru_version") if isinstance(metadata, dict) else "") or ""),
        document_kind="pdf",
        raw_artifacts_dir=str(source_dir),
    )


def _source_file_from_dir(task_id: str, source_dir: Path, metadata: Any) -> SourceFile:
    filename = ""
    if isinstance(metadata, dict):
        task = metadata.get("task") if isinstance(metadata.get("task"), dict) else {}
        filename = str(task.get("filename") or metadata.get("filename") or metadata.get("result_file") or "").strip()
        source_files = metadata.get("source_files") if isinstance(metadata.get("source_files"), dict) else {}
        pdf_info = source_files.get("pdf") if isinstance(source_files.get("pdf"), dict) else {}
        raw_pdf_path = str(pdf_info.get("path") or "").strip().replace("\\", "/")
        if raw_pdf_path:
            relative_pdf = Path(raw_pdf_path)
            if not relative_pdf.is_absolute() and ".." not in relative_pdf.parts:
                pdf_path = (source_dir / relative_pdf).resolve()
                try:
                    pdf_path.relative_to(source_dir)
                except ValueError:
                    pdf_path = source_dir / "__invalid_source_path__"
                if not pdf_path.is_symlink() and pdf_path.is_file():
                    return _source_file_from_path(pdf_path, filename or pdf_path.name)
    for candidate in sorted(source_dir.iterdir()):
        if candidate.is_file() and candidate.suffix.lower() in {".pdf", ".md", ".txt"}:
            chosen_name = filename if filename and Path(filename).suffix.lower() == candidate.suffix.lower() else candidate.name
            return _source_file_from_path(candidate, chosen_name)
    fallback = source_dir / "result_complete.md"
    if not fallback.exists():
        fallback = source_dir / "result.md"
    if fallback.exists():
        return _source_file_from_path(fallback, fallback.name)
    placeholder = source_dir / f"{safe_client_filename(task_id)}.md"
    placeholder.write_text("", encoding="utf-8")
    return _source_file_from_path(placeholder, placeholder.name)


def _source_file_from_path(path: Path, filename: str) -> SourceFile:
    safe_name = safe_client_filename(filename or path.name)
    extension = Path(safe_name).suffix.lower() or path.suffix.lower() or ".md"
    if extension not in {".pdf", ".md", ".txt"}:
        safe_name = f"{safe_name}.pdf"
    try:
        extension = validate_extension(safe_name)
    except ValueError:
        safe_name = f"{Path(safe_name).stem}.pdf"
        extension = validate_extension(safe_name)
    return SourceFile(
        path=path,
        filename=safe_name,
        mime_type=guess_mime_type(safe_name),
        extension=extension,
        file_size=path.stat().st_size if path.exists() else 0,
        sha256=sha256_file(path) if path.exists() else "",
        source_type="mineru_import",
        source_url="",
    )


def _normalize_content_list(payload: Any) -> list[dict[str, Any]]:
    payload = _decode_json_string(payload)
    values: list[Any]
    if isinstance(payload, dict):
        for key in ("content_list", "items", "blocks"):
            value = _decode_json_string(payload.get(key))
            if isinstance(value, list):
                values = value
                break
        else:
            values = []
    elif isinstance(payload, list):
        values = payload
    else:
        values = []
    normalized: list[dict[str, Any]] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        sanitized = dict(item)
        for key in ("img_path", "image_path", "source_image_path"):
            if key in sanitized:
                sanitized[key] = _safe_image_path(sanitized.get(key))
        normalized.append(sanitized)
    return normalized


def _safe_image_path(value: Any) -> str:
    raw_path = str(value or "").strip().replace("\\", "/")
    if not raw_path:
        return ""
    path = Path(raw_path)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "images":
        return ""
    return path.as_posix()


def _existing_image_path(source_dir: Path, value: Any) -> str:
    relative = _safe_image_path(value)
    if not relative:
        return ""
    candidate = source_dir / relative
    current = source_dir
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            return ""
    try:
        candidate.resolve().relative_to(source_dir.resolve())
    except ValueError:
        return ""
    return relative if candidate.is_file() else ""


def _content_items_to_blocks(task_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocks = []
    for index, item in enumerate(items, start=1):
        item_type = str(item.get("type") or "text")
        page_number = int(item.get("page_number") or item.get("page_idx") or item.get("page_index") or 0) + (0 if item.get("page_number") else 1)
        block_type = _block_type(item_type)
        text = _item_text(item)
        markdown = _item_markdown(item, text)
        block_id = f"b{index:06d}"
        blocks.append(
            {
                "block_id": block_id,
                "type": block_type,
                "sub_type": str(item.get("sub_type") or item.get("text_level") or ""),
                "text": text,
                "markdown": markdown,
                "html": str(item.get("html") or item.get("table_body") or ""),
                "page_number": max(1, page_number),
                "page_index": max(0, page_number - 1),
                "sheet_name": "",
                "slide_number": None,
                "bbox": _bbox(item),
                "bbox_unit": "normalized_1000" if _bbox(item) else "none",
                "reading_order": index,
                "parent_block_id": "",
                "source_ref": {
                    "evidence_id": f"doc:{task_id}:p{max(1, page_number)}:{block_id}",
                    "source_type": f"mineru_{item_type}",
                    "path": "raw/mineru/content_list.json",
                },
                "confidence": 1.0,
                "warnings": [],
            }
        )
    return blocks


def _tables_from_content_list(task_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tables = []
    table_order = 1
    for index, item in enumerate(items, start=1):
        if str(item.get("type") or "") != "table":
            continue
        table_id = f"pt-{table_order:06d}"
        html = str(item.get("table_body") or item.get("html") or "")
        rows = _rows_from_html(html)
        markdown = _markdown_table(rows) if rows else str(item.get("markdown") or "")
        caption = _join_text(item.get("table_caption") or item.get("caption") or [])
        page_number = int(item.get("page_number") or item.get("page_idx") or item.get("page_index") or 0) + (0 if item.get("page_number") else 1)
        tables.append(
            {
                "table_id": table_id,
                "block_id": f"b{index:06d}",
                "title": caption or f"Table {table_order}",
                "caption": caption,
                "page_number": max(1, page_number),
                "bbox": _bbox(item),
                "bbox_unit": "normalized_1000" if _bbox(item) else "none",
                "html": html,
                "markdown": markdown,
                "cells": _table_cells(task_id, table_id, rows),
                "quality": {
                    "has_header": bool(rows),
                    "row_count": len(rows),
                    "column_count": max((len(row) for row in rows), default=0),
                    "empty_cell_ratio": _empty_cell_ratio(rows),
                },
                "source_image_path": str(item.get("img_path") or item.get("source_image_path") or ""),
            }
        )
        table_order += 1
    return tables


def _tables_from_enhanced(task_id: str, enhanced: Any, existing_count: int) -> list[dict[str, Any]]:
    enhanced = _decode_json_string(enhanced)
    if not isinstance(enhanced, dict) or not isinstance(enhanced.get("tables"), list):
        return []
    tables = []
    for offset, item in enumerate(enhanced["tables"], start=1):
        if not isinstance(item, dict):
            continue
        table_id = f"pt-enh-{existing_count + offset:06d}"
        row_count = int(item.get("rows") or 0)
        column_count = max(1, int(item.get("cells") or 0) // row_count) if row_count else 0
        preview = str(item.get("preview") or "")
        page_number = int(item.get("pdf_page_number") or item.get("page_number") or 1)
        tables.append(
            {
                "table_id": table_id,
                "block_id": "",
                "title": _join_text(item.get("source_caption") or []) or f"Table {existing_count + offset}",
                "caption": _join_text(item.get("source_caption") or []),
                "page_number": page_number,
                "bbox": _bbox(item),
                "bbox_unit": "normalized_1000" if _bbox(item) else "none",
                "html": "",
                "markdown": preview,
                "cells": [],
                "quality": {
                    "has_header": bool((item.get("structure") or {}).get("header_row_count")),
                    "row_count": row_count,
                    "column_count": column_count,
                    "empty_cell_ratio": 0.0,
                },
                "source_image_path": _safe_image_path(item.get("source_image_path")),
            }
        )
    return tables


def _figures_from_content_list(task_id: str, items: list[dict[str, Any]], source_dir: Path) -> list[dict[str, Any]]:
    figures = []
    image_order = 1
    for index, item in enumerate(items, start=1):
        if str(item.get("type") or "") != "image":
            continue
        image_path = _existing_image_path(
            source_dir,
            item.get("img_path") or item.get("image_path"),
        )
        image_id = f"img-{image_order:06d}"
        page_number = int(item.get("page_number") or item.get("page_idx") or item.get("page_index") or 0) + (0 if item.get("page_number") else 1)
        caption = _join_text(item.get("image_caption") or item.get("caption") or [])
        footnote = _join_text(item.get("image_footnote") or item.get("footnote") or [])
        figures.append(
            {
                "image_id": image_id,
                "block_id": f"b{index:06d}",
                "type": str(item.get("sub_type") or "image"),
                "page_number": max(1, page_number),
                "page_index": max(0, page_number - 1),
                "bbox": _bbox(item),
                "bbox_unit": "normalized_1000" if _bbox(item) else "none",
                "image_path": image_path,
                "crop_path": image_path,
                "thumbnail_path": image_path,
                "source_page_image_path": image_path,
                "caption": caption or Path(image_path).name,
                "footnote": footnote,
                "nearby_heading": "",
                "ocr_text": "",
                "alt_text": caption or Path(image_path).name,
                "markdown": f"![{caption or Path(image_path).name}]({image_path})" if image_path else "",
                "markdown_anchor": f"md-{image_id}",
                "evidence_id": f"doc:{task_id}:p{max(1, page_number)}:{image_id}",
                "quality": {
                    "crop_available": bool(image_path),
                    "caption_detected": bool(caption),
                    "ocr_available": False,
                    "is_low_resolution": False,
                },
            }
        )
        image_order += 1
    return figures


def _figures_from_enhanced(task_id: str, enhanced: Any, source_dir: Path, existing_count: int) -> list[dict[str, Any]]:
    enhanced = _decode_json_string(enhanced)
    if not isinstance(enhanced, dict) or not isinstance(enhanced.get("image_semantic_blocks"), list):
        return []
    figures = []
    for offset, item in enumerate(enhanced["image_semantic_blocks"], start=1):
        if not isinstance(item, dict):
            continue
        image_path = _existing_image_path(source_dir, item.get("image_path"))
        image_id = f"img-enh-{existing_count + offset:06d}"
        page_number = int(item.get("pdf_page_number") or item.get("page_number") or 1)
        caption = _join_text(item.get("caption") or [])
        display = str(item.get("display_content") or item.get("recognized_content") or "")
        figures.append(
            {
                "image_id": image_id,
                "block_id": "",
                "type": str(item.get("semantic_kind") or item.get("sub_type") or "image"),
                "page_number": page_number,
                "page_index": max(0, page_number - 1),
                "bbox": _bbox(item),
                "bbox_unit": "normalized_1000" if _bbox(item) else "none",
                "image_path": image_path,
                "crop_path": image_path,
                "thumbnail_path": image_path,
                "source_page_image_path": image_path,
                "caption": caption or display[:120] or Path(image_path).name,
                "footnote": _join_text(item.get("footnote") or []),
                "nearby_heading": "",
                "ocr_text": display,
                "alt_text": display[:160] or caption,
                "markdown": f"![{caption or Path(image_path).name}]({image_path})" if image_path else "",
                "markdown_anchor": f"md-{image_id}",
                "evidence_id": f"doc:{task_id}:p{page_number}:{image_id}",
                "quality": {
                    "crop_available": bool(image_path),
                    "caption_detected": bool(caption),
                    "ocr_available": bool(display),
                    "is_low_resolution": False,
                },
            }
        )
    return figures


def copy_mineru_images_to_result(source_dir: Path, result_dir: Path) -> None:
    images_dir = source_dir / "images"
    if not images_dir.exists():
        return
    target_dir = result_dir / "images" / "original"
    target_dir.mkdir(parents=True, exist_ok=True)
    for path in images_dir.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            path.resolve().relative_to(images_dir.resolve())
        except ValueError:
            continue
        shutil.copy2(path, target_dir / safe_client_filename(path.name))


def rewrite_image_paths_to_result(output: ParseOutput) -> None:
    output.markdown = _rewrite_markdown_image_paths(output.markdown)
    for block in output.blocks:
        if block.get("markdown"):
            block["markdown"] = _rewrite_markdown_image_paths(str(block.get("markdown") or ""))
    for figure in output.figures:
        image_path = str(figure.get("image_path") or "")
        if image_path.startswith("images/"):
            filename = safe_client_filename(Path(image_path).name)
            for key in ("image_path", "crop_path", "thumbnail_path", "source_page_image_path"):
                if figure.get(key):
                    figure[key] = f"images/original/{filename}"
        if figure.get("markdown"):
            figure["markdown"] = _rewrite_markdown_image_paths(str(figure.get("markdown") or ""))
    for table in output.tables:
        source_image_path = str(table.get("source_image_path") or "")
        if source_image_path.startswith("images/"):
            table["source_image_path"] = f"images/original/{safe_client_filename(Path(source_image_path).name)}"


def _rewrite_markdown_image_paths(markdown: str) -> str:
    if not markdown or "images/" not in markdown:
        return markdown

    def replace_markdown(match: re.Match[str]) -> str:
        prefix = match.group("prefix")
        image_path = match.group("path")
        suffix = match.group("suffix") or ""
        filename = safe_client_filename(Path(image_path).name)
        return f"{prefix}images/original/{filename}{suffix})"

    def replace_html(match: re.Match[str]) -> str:
        prefix = match.group("prefix")
        image_path = match.group("path")
        filename = safe_client_filename(Path(image_path).name)
        return f'{prefix}images/original/{filename}"'

    markdown = re.sub(
        r"(?P<prefix>!\[[^\]]*\]\()(?P<path>images/(?!original/|crops/|page_previews/)[^)\s]+)(?P<suffix>[#?][^)]*)?\)",
        replace_markdown,
        markdown,
    )
    return re.sub(
        r'(?P<prefix><img[^>]+src=")(?P<path>images/(?!original/|crops/|page_previews/)[^"]+)"',
        replace_html,
        markdown,
        flags=re.IGNORECASE,
    )


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _decode_json_string(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("[", "{")):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return value
    return value


def _page_count(items: list[dict[str, Any]], middle: Any, enhanced: Any) -> int:
    pages = [int(item.get("page_number") or item.get("page_idx") or item.get("page_index") or 0) + (0 if item.get("page_number") else 1) for item in items]
    enhanced = _decode_json_string(enhanced)
    if isinstance(enhanced, dict) and isinstance(enhanced.get("pages"), list):
        pages.extend(int(item.get("page_number") or 0) for item in enhanced["pages"] if isinstance(item, dict))
    middle = _decode_json_string(middle)
    if isinstance(middle, dict) and isinstance(middle.get("pdf_info"), list):
        pages.append(len(middle["pdf_info"]))
    return max(pages or [1])


def _block_type(item_type: str) -> str:
    if item_type in {"title", "header"}:
        return "title"
    if item_type in {"image"}:
        return "image"
    if item_type in {"table"}:
        return "table"
    if item_type in {"equation", "formula"}:
        return "equation"
    return "paragraph"


def _item_text(item: dict[str, Any]) -> str:
    if item.get("text") is not None:
        return str(item.get("text") or "").strip()
    if item.get("list_items"):
        return "\n".join(str(value) for value in item.get("list_items") or [])
    if item.get("table_body"):
        return re.sub(r"<[^>]+>", " ", str(item.get("table_body") or "")).strip()
    if item.get("img_path"):
        return str(item.get("img_path") or "")
    return ""


def _item_markdown(item: dict[str, Any], text: str) -> str:
    item_type = str(item.get("type") or "")
    if item_type == "image" and item.get("img_path"):
        caption = _join_text(item.get("image_caption") or [])
        return f"![{caption or Path(str(item.get('img_path'))).name}]({item.get('img_path')})"
    if item_type == "table" and item.get("table_body"):
        rows = _rows_from_html(str(item.get("table_body") or ""))
        return _markdown_table(rows) if rows else str(item.get("table_body") or "")
    if item.get("list_items"):
        return "\n".join(f"- {value}" for value in item.get("list_items") or [])
    if item.get("text_level") in {1, 2, 3, 4, 5, 6}:
        return f"{'#' * int(item.get('text_level'))} {text}".strip()
    return text


def _bbox(item: dict[str, Any]) -> list[float]:
    bbox = item.get("bbox")
    if isinstance(bbox, list) and len(bbox) == 4:
        result = []
        for value in bbox:
            try:
                result.append(float(value))
            except (TypeError, ValueError):
                return []
        return result
    return []


def _join_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def _rows_from_html(html: str) -> list[list[str]]:
    rows = []
    for row_match in re.finditer(r"(?is)<tr[^>]*>(.*?)</tr>", html or ""):
        cells = []
        for cell_match in re.finditer(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", row_match.group(1)):
            text = re.sub(r"(?is)<[^>]+>", " ", cell_match.group(1))
            cells.append(re.sub(r"\s+", " ", text).strip())
        if cells:
            rows.append(cells)
    return rows


def _table_cells(task_id: str, table_id: str, rows: list[list[str]]) -> list[dict[str, Any]]:
    cells = []
    for row_index, row in enumerate(rows):
        for column_index, text in enumerate(row):
            cells.append(
                {
                    "row_index": row_index,
                    "column_index": column_index,
                    "text": text,
                    "bbox": [],
                    "evidence_id": f"doc:{task_id}:table:{table_id}:r{row_index}:c{column_index}",
                }
            )
    return cells


def _empty_cell_ratio(rows: list[list[str]]) -> float:
    total = 0
    empty = 0
    for row in rows:
        for value in row:
            total += 1
            if not str(value or "").strip():
                empty += 1
    return round(empty / total, 4) if total else 0.0


def _dedupe_tables(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for table in tables:
        key = (table.get("page_number"), tuple(table.get("bbox") or []), table.get("markdown") or table.get("title"))
        if key in seen:
            continue
        seen.add(key)
        result.append(table)
    return result


def _dedupe_figures(figures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for figure in figures:
        key = (figure.get("image_path"), figure.get("page_number"), tuple(figure.get("bbox") or []))
        if key in seen:
            continue
        seen.add(key)
        result.append(figure)
    return result
