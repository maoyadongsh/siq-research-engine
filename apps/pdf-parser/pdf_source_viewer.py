"""Helpers for PDF source-view payloads used by the review workbench."""

from __future__ import annotations

import json


def coerce_json_artifact(payload):
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return payload
    return payload


def _bbox_items(value):
    if value is None or isinstance(value, (str, bytes)):
        return None
    try:
        if len(value) != 4:
            return None
        return [value[index] for index in range(4)]
    except TypeError:
        return None
    except (KeyError, IndexError):
        return None


def _bbox_payload(value):
    bbox = _bbox_items(value)
    return bbox if bbox is not None else []


def _text_value(value):
    if value is None:
        return ""
    return str(value).strip()


def _text_list(value):
    if isinstance(value, list):
        items = []
        for item in value:
            text = _text_value(item)
            if text:
                items.append(text)
        return items
    text = _text_value(value)
    return [text] if text else []


def page_bbox_extent_from_content_list(content_list, page_index):
    content_list = coerce_json_artifact(content_list)
    if not isinstance(content_list, list) or page_index is None:
        return None
    max_x = 0
    max_y = 0
    for item in content_list:
        if not isinstance(item, dict) or item.get("page_idx") != page_index:
            continue
        bbox = _bbox_items(item.get("bbox"))
        if bbox is None:
            continue
        try:
            max_x = max(max_x, float(bbox[0]), float(bbox[2]))
            max_y = max(max_y, float(bbox[1]), float(bbox[3]))
        except (TypeError, ValueError):
            continue
    if max_x <= 0 or max_y <= 0:
        return None
    return {"width": max_x, "height": max_y}


def printed_page_numbers_by_pdf_page(content_list):
    content_list = coerce_json_artifact(content_list)
    if not isinstance(content_list, list):
        return {}
    pages = {}
    for item in content_list:
        if not isinstance(item, dict) or item.get("type") != "page_number":
            continue
        page_idx = item.get("page_idx")
        if not isinstance(page_idx, int):
            continue
        value = str(item.get("text") or "").strip()
        if value:
            pages[page_idx + 1] = value
    return pages


def _bbox_key(value):
    bbox = _bbox_items(value)
    if bbox is None:
        return None
    try:
        return tuple(round(float(item), 2) for item in bbox)
    except (TypeError, ValueError):
        return None


def page_content_payload_from_content_list(content_list, page_number, report=None, focus_table=None):
    page_number = int(page_number)
    if page_number <= 0:
        raise ValueError("Invalid page number")
    try:
        focus_table_index = int(focus_table) if focus_table is not None else None
    except (TypeError, ValueError):
        focus_table_index = None
    page_index = page_number - 1
    content_list = coerce_json_artifact(content_list)
    printed_pages = printed_page_numbers_by_pdf_page(content_list)

    table_lookup = {}
    table_lookup_by_source_id = {}
    table_lookup_by_bbox = {}
    page_tables = []
    if isinstance(report, dict):
        report_table_index = report.get("table_index", [])
        if not isinstance(report_table_index, list):
            report_table_index = []
        for item in report_table_index:
            if not isinstance(item, dict):
                continue
            try:
                table_index = int(item.get("table_index") or 0)
            except (TypeError, ValueError):
                continue
            if table_index <= 0:
                continue
            try:
                item_pdf_page_number = int(item.get("pdf_page_number") or 0)
            except (TypeError, ValueError):
                continue
            table_lookup[table_index] = item
            source_id = item.get("content_table_source_id")
            if source_id is not None:
                try:
                    source_id_number = int(source_id)
                    table_lookup_by_source_id[source_id_number] = item
                    if source_id_number >= 0:
                        table_lookup_by_source_id.setdefault(source_id_number + 1, item)
                except (TypeError, ValueError):
                    pass
            bbox_key = _bbox_key(item.get("bbox"))
            if bbox_key is not None:
                table_lookup_by_bbox[(item_pdf_page_number, bbox_key)] = item
            if item_pdf_page_number == page_number:
                page_tables.append(
                    {
                        "table_index": table_index,
                        "source_table_index": item.get("content_table_source_id"),
                        "line": item.get("line"),
                        "heading": _text_value(item.get("heading")),
                        "printed_page_number": _text_value(item.get("printed_page_number"))
                        or printed_pages.get(page_number),
                        "matched_financial_names": _text_list(item.get("matched_financial_names")),
                    }
                )

    if not isinstance(content_list, list):
        page_tables.sort(key=lambda item: item.get("table_index") or 0)
        return {
            "page_number": page_number,
            "pdf_page_number": page_number,
            "printed_page_number": printed_pages.get(page_number),
            "page_index": page_index,
            "block_count": 0,
            "table_count": 0,
            "page_tables": page_tables,
            "blocks": [],
        }

    blocks = []
    table_seq = 0
    for source_index, item in enumerate(content_list, start=1):
        if not isinstance(item, dict):
            continue
        block_type = item.get("type") or "unknown"
        table_html = item.get("table_body") or ""
        has_table_body = block_type == "table" and bool(table_html)
        table_index = None
        if has_table_body:
            table_seq += 1
            table_index = table_seq
        if item.get("page_idx") != page_index:
            continue

        block = {
            "block_id": item.get("block_id") or f"b{source_index:06d}",
            "type": block_type,
            "bbox": _bbox_payload(item.get("bbox")),
            "page_number": page_number,
            "pdf_page_number": page_number,
            "reading_order": source_index,
        }
        if block_type in {"text", "header", "page_number"}:
            block["text"] = item.get("text") or ""
            block["text_level"] = item.get("text_level")
        elif block_type == "list":
            block["list_items"] = _text_list(item.get("list_items"))
            block["sub_type"] = _text_value(item.get("sub_type"))
        elif block_type == "table":
            source_table_index = table_index
            source = table_lookup_by_source_id.get(source_table_index or -1)
            if not source:
                source = table_lookup_by_bbox.get((page_number, _bbox_key(item.get("bbox"))))
            source = source or table_lookup.get(source_table_index or -1, {})
            endpoint_table_index = source.get("table_index") or source_table_index
            block["table_index"] = endpoint_table_index
            block["source_table_index"] = source_table_index
            block["table_html"] = table_html
            block["heading"] = _text_value(source.get("heading"))
            block["caption"] = _text_list(item.get("table_caption")) or _text_list(source.get("source_caption"))
            block["footnote"] = _text_list(item.get("table_footnote")) or _text_list(source.get("source_footnote"))
            block["line"] = source.get("line")
            block["printed_page_number"] = _text_value(source.get("printed_page_number")) or printed_pages.get(page_number)
            block["matched_financial_names"] = _text_list(source.get("matched_financial_names"))
            block["is_focus_table"] = bool(endpoint_table_index and focus_table_index == int(endpoint_table_index))
            block["missing_body"] = not bool(table_html)
        elif block_type == "image":
            block["image_path"] = item.get("img_path") or ""
            block["sub_type"] = _text_value(item.get("sub_type"))
            block["caption"] = _text_list(item.get("image_caption"))
            block["footnote"] = _text_list(item.get("image_footnote"))
        else:
            block["raw"] = item
        blocks.append(block)

    page_tables.sort(key=lambda item: item.get("table_index") or 0)
    return {
        "page_number": page_number,
        "pdf_page_number": page_number,
        "printed_page_number": printed_pages.get(page_number),
        "page_index": page_index,
        "block_count": len(blocks),
        "table_count": sum(1 for item in blocks if item.get("type") == "table" and item.get("table_html")),
        "page_tables": page_tables,
        "blocks": blocks,
    }
