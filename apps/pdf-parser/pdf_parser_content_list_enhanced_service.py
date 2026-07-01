"""Content list enhanced helpers split out from the Flask app boundary."""

from __future__ import annotations

from collections import Counter
import os
import re

from pdf_parser_page_markers import _collect_text_fragments, _compact_text_fragment
from pdf_source_viewer import coerce_json_artifact as _coerce_json_artifact, printed_page_numbers_by_pdf_page


def _strip_html(html):
    return re.sub(r"<[^>]+>", "", str(html or ""))


def _block_page_number(block):
    page_idx = block.get("page_idx") if isinstance(block, dict) else None
    return int(page_idx) + 1 if isinstance(page_idx, int) else None


def build_enhanced_page_blocks(content_list):
    content_list = _coerce_json_artifact(content_list)
    if not isinstance(content_list, list):
        return []
    printed_pages = printed_page_numbers_by_pdf_page(content_list)
    pages = {}
    for block in content_list:
        if not isinstance(block, dict):
            continue
        page_number = _block_page_number(block)
        if not page_number:
            continue
        payload = pages.setdefault(
            page_number,
            {
                "page_number": page_number,
                "pdf_page_number": page_number,
                "printed_page_number": printed_pages.get(page_number),
                "block_count": 0,
                "block_types": Counter(),
                "table_count": 0,
                "text_chars": 0,
                "footnote_texts": [],
            },
        )
        block_type = str(block.get("type") or "unknown")
        payload["block_count"] += 1
        payload["block_types"][block_type] += 1
        if block_type == "table":
            payload["table_count"] += 1
            for footnote in block.get("table_footnote") or []:
                if str(footnote or "").strip():
                    payload["footnote_texts"].append(str(footnote).strip())
        text = " ".join(_collect_text_fragments(block))
        payload["text_chars"] += len(text)
    return [
        {
            **{key: value for key, value in page.items() if key != "block_types"},
            "block_types": dict(page["block_types"]),
        }
        for _page_number, page in sorted(pages.items())
    ]


def printed_page_numbers_by_pdf_page_map(content_list):
    return printed_page_numbers_by_pdf_page(content_list)


def content_table_sources(content_list):
    content_list = _coerce_json_artifact(content_list)
    if not isinstance(content_list, list):
        return []
    printed_pages = printed_page_numbers_by_pdf_page_map(content_list)
    sources = []
    table_ordinal = 0
    for item in content_list:
        if not isinstance(item, dict) or item.get("type") != "table":
            continue
        table_body = item.get("table_body") or ""
        if not table_body:
            continue
        table_ordinal += 1
        page_idx = item.get("page_idx")
        page_number = int(page_idx) + 1 if isinstance(page_idx, int) else None
        sources.append(
            {
                "source_id": table_ordinal,
                "table_body": str(table_body).strip(),
                "pdf_page_index": page_idx,
                "pdf_page_number": page_number,
                "printed_page_number": printed_pages.get(page_number) if page_number else None,
                "bbox": item.get("bbox") or [],
                "image_path": item.get("img_path") or "",
                "caption": item.get("table_caption") or [],
                "footnote": item.get("table_footnote") or [],
            }
        )
    return sources


def normalized_table_html_for_match(table_html):
    return re.sub(r"\s+", "", str(table_html or "")).strip()


def content_table_source_maps(table_sources):
    exact = {}
    normalized = {}
    for source in table_sources:
        table_body = str(source.get("table_body") or "").strip()
        if not table_body:
            continue
        exact.setdefault(table_body, []).append(source)
        normalized_body = normalized_table_html_for_match(table_body)
        if normalized_body and normalized_body != table_body:
            normalized.setdefault(normalized_body, []).append(source)
    return exact, normalized


def pop_unused_content_table_source(table_html, exact_sources, normalized_sources, used_source_ids):
    table_html = str(table_html or "").strip()
    source = _pop_unused_source_from_bucket(exact_sources.get(table_html), used_source_ids)
    if source:
        source = dict(source)
        source["source_match"] = "content_list_body_exact"
        return source

    normalized_html = normalized_table_html_for_match(table_html)
    source = _pop_unused_source_from_bucket(normalized_sources.get(normalized_html), used_source_ids)
    if source:
        source = dict(source)
        source["source_match"] = "content_list_body_normalized"
        return source
    return {}


def _pop_unused_source_from_bucket(bucket, used_source_ids):
    if not bucket:
        return None
    while bucket:
        source = bucket.pop(0)
        source_id = source.get("source_id")
        if source_id in used_source_ids:
            continue
        used_source_ids.add(source_id)
        return source
    return None


def inferred_pdf_page_for_line(line, markers):
    if not line or not markers:
        return None, ""
    previous_marker = None
    next_marker = None
    for marker in markers:
        if marker["line"] <= line:
            previous_marker = marker
            continue
        next_marker = marker
        break
    if previous_marker and next_marker:
        previous_distance = line - previous_marker["line"]
        next_distance = next_marker["line"] - line
        if next_marker["page_number"] >= previous_marker["page_number"] and previous_distance <= 220:
            return previous_marker["page_number"], "between_ordered_markers"
        if previous_distance <= 80:
            return previous_marker["page_number"], "near_previous_marker"
        if next_distance <= 80:
            return next_marker["page_number"], "near_next_marker"
        return None, "ambiguous_marker_distance"
    if previous_marker and line - previous_marker["line"] <= 220:
        return previous_marker["page_number"], "tail_near_previous_marker"
    return None, "no_safe_marker"


def table_source_confidence(source_name):
    if source_name in {"content_list_body_exact", "content_list_body_normalized"}:
        return "high"
    if source_name == "markdown_marker_inferred":
        return "medium"
    return "low"


def build_content_list_enhanced_payload(
    markdown,
    *,
    schema_version,
    content_table_sources,
    content_table_source_maps,
    pop_unused_content_table_source,
    pdf_page_markers_by_line,
    printed_page_numbers_by_pdf_page,
    inferred_pdf_page_for_line,
    strip_html,
    table_structure_signals,
    table_source_confidence,
    count_table_rows,
    count_table_cells,
    build_enhanced_page_blocks,
    build_enhanced_footnotes,
    build_enhanced_toc,
    build_financial_note_links,
    build_image_semantic_blocks,
    build_enhanced_quality_signals,
    content_list=None,
    report_year=None,
):
    markdown = str(markdown or "")
    table_sources = content_table_sources(content_list)
    exact_table_sources, normalized_table_sources = content_table_source_maps(table_sources)
    used_source_ids = set()
    page_markers = pdf_page_markers_by_line(markdown)
    printed_pages = printed_page_numbers_by_pdf_page(content_list)
    tables = []

    for idx, match in enumerate(re.finditer(r"<table\b.*?</table>", markdown, flags=re.IGNORECASE | re.DOTALL), start=1):
        table_html = match.group(0)
        line = markdown.count("\n", 0, match.start()) + 1
        source = pop_unused_content_table_source(
            table_html,
            exact_table_sources,
            normalized_table_sources,
            used_source_ids,
        )
        pdf_page_number = source.get("pdf_page_number")
        pdf_page_index = source.get("pdf_page_index")
        printed_page_number = source.get("printed_page_number")
        source_name = source.get("source_match") if pdf_page_number else ""
        inferred_reason = ""
        if not pdf_page_number:
            inferred_page, inferred_reason = inferred_pdf_page_for_line(line, page_markers)
            if inferred_page:
                pdf_page_number = inferred_page
                pdf_page_index = inferred_page - 1
                printed_page_number = printed_pages.get(inferred_page)
                source_name = "markdown_marker_inferred"

        table_html_text = strip_html(table_html)
        structure = table_structure_signals(table_html)
        tables.append(
            {
                "table_index": idx,
                "line": line,
                "source": source_name or "unresolved",
                "confidence": table_source_confidence(source_name),
                "pdf_page_index": pdf_page_index,
                "pdf_page_number": pdf_page_number,
                "printed_page_number": printed_page_number,
                "pdf_page_inference_reason": inferred_reason if source_name == "markdown_marker_inferred" else "",
                "bbox": source.get("bbox") or [],
                "source_image_path": source.get("image_path") or "",
                "source_caption": source.get("caption") or [],
                "source_footnote": source.get("footnote") or [],
                "content_table_source_id": source.get("source_id"),
                "rows": count_table_rows(table_html),
                "cells": count_table_cells(table_html),
                "structure": structure,
                "preview": table_html_text[:220],
                "report_year": report_year,
            }
        )

    source_counts = Counter(item["source"] for item in tables)
    pages = build_enhanced_page_blocks(content_list)
    footnotes = build_enhanced_footnotes(markdown, content_list=content_list)
    toc = build_enhanced_toc(markdown, content_list=content_list)
    financial_note_links = build_financial_note_links(markdown, tables, page_markers)
    image_semantic_blocks = build_image_semantic_blocks(markdown, content_list=content_list)
    return {
        "schema_version": schema_version,
        "report_year": report_year,
        "table_count": len(tables),
        "content_table_body_count": len(table_sources),
        "source_counts": dict(source_counts),
        "tables": tables,
        "pages": pages,
        "footnotes": footnotes,
        "toc": toc,
        "financial_note_links": financial_note_links,
        "image_semantic_blocks": image_semantic_blocks,
        "quality_signals": build_enhanced_quality_signals(
            tables,
            footnotes,
            toc,
            pages,
            financial_note_links=financial_note_links,
            image_semantic_blocks=image_semantic_blocks,
        ),
    }


def _markdown_image_details(markdown):
    text = str(markdown or "")
    pattern = re.compile(
        r"!\[[^\]]*\]\((?P<path>[^)]+)\)"
        r"(?P<trailing>(?:[ \t]*\n|[ \t]{2,}\n|[ \t])*)"
        r"(?P<details><details>\s*<summary>(?P<summary>[^<]+)</summary>\s*(?P<body>.*?)</details>)?",
        flags=re.IGNORECASE | re.DOTALL,
    )
    details_by_path = {}
    order = 0
    for match in pattern.finditer(text):
        order += 1
        image_path = str(match.group("path") or "").strip()
        if not image_path:
            continue
        line = text.count("\n", 0, match.start()) + 1
        body = (match.group("body") or "").strip()
        summary = (match.group("summary") or "").strip()
        details_by_path.setdefault(image_path, []).append(
            {
                "markdown_image_order": order,
                "markdown_line": line,
                "summary_type": summary,
                "body": body,
                "body_preview": _compact_text_fragment(_strip_html(body), 320),
                "has_details": bool(match.group("details")),
            }
        )
    return details_by_path


def _image_semantic_kind(block_type, sub_type, detail_type):
    value = (detail_type or sub_type or block_type or "").lower()
    if "equation" in value or "formula" in value:
        return "formula"
    if value == "flowchart":
        return "flowchart"
    if value in {"bar", "pie", "line", "bar_line", "bar_stacked", "donut", "heatmap", "geo", "bubble"}:
        return "chart"
    if "chart" in value:
        return "chart"
    if value == "text_image":
        return "text_image"
    if value == "natural_image":
        return "natural_image"
    return "image"


def _image_semantic_confidence(block_type, sub_type, detail):
    if detail.get("has_details"):
        detail_type = str(detail.get("summary_type") or "").lower()
        if detail_type in {"bar", "pie", "line", "bar_line", "bar_stacked", "donut", "heatmap", "geo", "bubble", "flowchart"}:
            return "high"
        if detail_type in {"text_image", "natural_image"}:
            return "medium"
        return "medium"
    if block_type in {"chart", "equation"}:
        return "medium"
    if sub_type:
        return "low"
    return "low"


def _detect_text_language(text):
    plain = _strip_html(str(text or ""))
    zh = len(re.findall(r"[\u4e00-\u9fff]", plain))
    latin = len(re.findall(r"[A-Za-z]", plain))
    if zh and zh >= max(2, latin // 3):
        return "zh"
    if latin:
        return "en"
    return "unknown"


def _localize_markdown_table_headers_to_zh(text):
    lines = str(text or "").splitlines()
    replacements = {
        "Year": "年份",
        "Category": "类别",
        "Value": "数值",
        "Blue Bar Value": "蓝色柱数值",
        "Gold Bar Value": "金色柱数值",
    }
    localized = []
    for line in lines:
        if "|" not in line:
            localized.append(line)
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        new_cells = [replacements.get(cell, cell) for cell in cells]
        if len(new_cells) == len(cells):
            localized.append("| " + " | ".join(new_cells) + " |")
        else:
            localized.append(line)
    return "\n".join(localized)


def _localized_no_text_suffix(text):
    value = _compact_text_fragment(text, 600)
    if not value:
        return ""
    phrase_replacements = (
        ("no visible text or symbols visible", "未见可读文字或符号"),
        ("no visible text or symbols", "未见可读文字或符号"),
        ("no text or symbols visible", "未见可读文字或符号"),
        ("no text or symbols present", "未见文字或符号"),
        ("no text or symbols", "未见文字或符号"),
        ("no visible text", "未见可读文字"),
        ("no readable text in focus", "未见清晰可读文字"),
    )
    lowered = value.lower().rstrip(".")
    for source, target in phrase_replacements:
        lowered = lowered.replace(source, target)
    if lowered != value.lower().rstrip("."):
        match = re.search(r"(未见清晰可读文字|未见可读文字或符号|未见文字或符号|未见可读文字)", lowered)
        return match.group(1) if match else ""
    return ""


def _localize_plain_image_description_to_zh(text, semantic_kind="", detail_type=""):
    value = _compact_text_fragment(text, 600)
    if not value:
        return ""
    suffix = _localized_no_text_suffix(value)
    lowered = value.lower()
    if semantic_kind == "natural_image" or detail_type == "natural_image":
        if "portrait of" in lowered:
            return f"人物肖像图片，{suffix or '未见清晰可读文字'}。"
        if "line art illustration" in lowered or "illustration of" in lowered:
            return f"插图或线稿图片，{suffix or '未见清晰可读文字'}。"
        if "abstract" in lowered:
            return f"抽象装饰图片，{suffix or '未见清晰可读文字'}。"
        if "aerial view" in lowered:
            return f"航拍场景图片，{suffix or '未见清晰可读文字'}。"
        if "exterior view" in lowered:
            return f"建筑或场景外观图片，{suffix or '未见清晰可读文字'}。"
        if "interior" in lowered:
            return f"室内场景图片，{suffix or '未见清晰可读文字'}。"
        if "group photo" in lowered or "group of" in lowered:
            return f"人物合影或群体场景图片，{suffix or '未见清晰可读文字'}。"
        if suffix:
            return f"自然图片，{suffix}。"
        return "自然图片，原始英文描述已保留在 JSON 的 recognized_content 字段。"
    if semantic_kind == "text_image" or detail_type == "text_image":
        if _detect_text_language(value) == "en":
            return f"图片文字（英文原文）：{value}"
        return value
    if suffix:
        return f"图片描述：{suffix}。"
    return ""


def _normalized_image_content_zh(content, semantic_kind="", content_format="", detail_type=""):
    if not content:
        return ""
    if _detect_text_language(content) == "zh":
        return content
    if content_format == "markdown_table":
        return _localize_markdown_table_headers_to_zh(content)
    if content_format == "mermaid":
        return content
    if semantic_kind == "formula":
        return content
    if semantic_kind in {"natural_image", "text_image", "image"} or content_format == "plain_text":
        localized = _localize_plain_image_description_to_zh(
            content,
            semantic_kind=semantic_kind,
            detail_type=detail_type,
        )
        if localized:
            return localized
    return ""


def _markdown_table_to_records(markdown_table, max_rows=80):
    lines = []
    for raw_line in str(markdown_table or "").splitlines():
        line = raw_line.strip()
        if not line or "|" not in line or line.startswith("```"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells:
            continue
        is_separator = all(re.fullmatch(r":?-{2,}:?", cell or "") for cell in cells)
        if is_separator:
            continue
        lines.append(cells)
    if len(lines) < 2:
        return None
    headers = lines[0]
    normalized_headers = []
    seen = Counter()
    for index, header in enumerate(headers, start=1):
        name = header or f"列{index}"
        seen[name] += 1
        normalized_headers.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    rows = []
    for cells in lines[1 : max_rows + 1]:
        padded = cells + [""] * max(0, len(normalized_headers) - len(cells))
        rows.append({header: padded[idx] for idx, header in enumerate(normalized_headers)})
    return {
        "headers": normalized_headers,
        "rows": rows,
        "row_count": max(0, len(lines) - 1),
        "source": "markdown_table_in_image_details",
    }


def _strip_mermaid_fences(content):
    text = str(content or "").strip()
    text = re.sub(r"^```mermaid\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_mermaid_node_token(token):
    raw = str(token or "").strip().rstrip(";")
    raw = re.sub(r"\s+", " ", raw)
    if not raw:
        return "", ""
    node_match = re.match(r"([A-Za-z_][\w.-]*)", raw)
    node_id = node_match.group(1) if node_match else re.sub(r"\W+", "_", raw)[:32]
    label = ""
    quoted = re.search(r'["“](.+?)["”]', raw)
    if quoted:
        label = quoted.group(1).strip()
    else:
        bracket = re.search(r"[\[\(\{]([^\]\)\}]+)[\]\)\}]", raw)
        if bracket:
            label = bracket.group(1).strip().strip('"“”')
    return node_id, label or node_id


def _mermaid_to_nodes_edges(mermaid, max_edges=120):
    text = _strip_mermaid_fences(mermaid)
    if not text:
        return None
    nodes = {}
    edges = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("%") or line.startswith("%%"):
            continue
        if re.match(r"^(?:graph|flowchart|sequenceDiagram|classDiagram|stateDiagram|subgraph|end|style|classDef)\b", line):
            continue
        line = line.rstrip(";")
        edge_match = re.search(r"(.+?)\s*(-->|---|-.->|==>)\s*(.+)", line)
        if not edge_match:
            node_id, label = _parse_mermaid_node_token(line)
            if node_id:
                nodes.setdefault(node_id, {"id": node_id, "label": label})
            continue
        left = edge_match.group(1).strip()
        right = edge_match.group(3).strip()
        edge_label = ""
        if right.startswith("|"):
            label_match = re.match(r"\|([^|]+)\|\s*(.+)", right)
            if label_match:
                edge_label = label_match.group(1).strip()
                right = label_match.group(2).strip()
        source_id, source_label = _parse_mermaid_node_token(left)
        target_id, target_label = _parse_mermaid_node_token(right)
        if not source_id or not target_id:
            continue
        nodes.setdefault(source_id, {"id": source_id, "label": source_label})
        nodes.setdefault(target_id, {"id": target_id, "label": target_label})
        edges.append({"source": source_id, "target": target_id, "label": edge_label})
        if len(edges) >= max_edges:
            break
    if not nodes and not edges:
        return None
    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "source": "mermaid_in_image_details",
    }


def _image_bbox_area(bbox):
    if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
        return 0
    try:
        x0, y0, x1, y1 = [float(value) for value in bbox[:4]]
    except (TypeError, ValueError):
        return 0
    return max(0, x1 - x0) * max(0, y1 - y0)


def _image_ocr_vlm_candidate(block):
    confidence = block.get("confidence") or "low"
    has_content = bool(str(block.get("display_content") or block.get("recognized_content") or "").strip())
    area = _image_bbox_area(block.get("bbox"))
    kind = block.get("semantic_kind") or "image"
    needed = confidence == "low" and not has_content and area >= 50000
    if not needed:
        return {
            "needed": False,
            "priority": "none",
            "reason": "",
            "recommended_mode": "",
            "bbox_area": area,
        }
    priority = "high" if area >= 180000 or kind in {"chart", "flowchart", "text_image", "formula"} else "medium"
    return {
        "needed": True,
        "priority": priority,
        "reason": "低置信大图缺少可读文本或结构化内容，建议按需调用 OCR/VLM 二次识别。",
        "recommended_mode": "ocr_or_vlm_on_demand",
        "bbox_area": area,
    }


def _image_actionability(block, chart_data=None, flowchart_graph=None, ocr_vlm_candidate=None):
    kind = block.get("semantic_kind") or "image"
    display_content = str(block.get("display_content") or "").strip()
    if chart_data and chart_data.get("rows"):
        return "data_usable"
    if flowchart_graph and (flowchart_graph.get("nodes") or flowchart_graph.get("edges")):
        return "structure_usable"
    if kind == "formula" and display_content:
        return "formula_candidate"
    if kind == "text_image" and display_content:
        return "search_only"
    if kind == "chart" and display_content:
        return "search_only"
    if (ocr_vlm_candidate or {}).get("needed"):
        return "needs_ocr"
    return "visual_context_only"


def _should_show_image_block_in_complete(block):
    actionability = block.get("actionability") or ""
    if actionability in {"data_usable", "structure_usable", "formula_candidate", "search_only"}:
        return True
    return False


def build_image_semantic_blocks(markdown, content_list=None):
    content_list = _coerce_json_artifact(content_list)
    if not isinstance(content_list, list):
        content_list = []
    details_by_path = _markdown_image_details(markdown)
    path_offsets = Counter()
    blocks = []
    for source_id, block in enumerate(content_list, start=1):
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or block.get("category") or block.get("block_type") or "").lower()
        sub_type = str(block.get("sub_type") or block.get("subtype") or "").lower()
        image_path = (
            block.get("img_path")
            or block.get("image_path")
            or block.get("source_image_path")
            or block.get("image")
            or ""
        )
        is_semantic_image = block_type in {"image", "chart", "equation"} or bool(image_path and block_type != "table")
        if not is_semantic_image or not image_path:
            continue
        image_path = str(image_path)
        candidates = details_by_path.get(image_path) or []
        detail_index = path_offsets[image_path]
        detail = candidates[detail_index] if detail_index < len(candidates) else {}
        if candidates:
            path_offsets[image_path] += 1
        detail_type = detail.get("summary_type") or ""
        semantic_kind = _image_semantic_kind(block_type, sub_type, detail_type)
        body = detail.get("body") or ""
        content_format = ""
        if body:
            if "```mermaid" in body:
                content_format = "mermaid"
            elif re.search(r"^\s*\|.+\|\s*$", body, flags=re.MULTILINE):
                content_format = "markdown_table"
            elif "$$" in body or block_type == "equation":
                content_format = "latex_or_text"
            else:
                content_format = "plain_text"
        recognized_language = _detect_text_language(body)
        normalized_content_zh = _normalized_image_content_zh(
            body,
            semantic_kind=semantic_kind,
            content_format=content_format,
            detail_type=detail_type,
        )
        display_content = normalized_content_zh or body
        display_preview = _compact_text_fragment(_strip_html(display_content), 320)
        item = {
            "image_index": len(blocks) + 1,
            "content_source_id": source_id,
            "type": block_type or "image",
            "sub_type": sub_type,
            "semantic_kind": semantic_kind,
            "image_path": image_path,
            "pdf_page_index": block.get("page_idx"),
            "pdf_page_number": _block_page_number(block),
            "bbox": block.get("bbox") or [],
            "caption": block.get("image_caption") or block.get("caption") or [],
            "footnote": block.get("image_footnote") or block.get("footnote") or [],
            "markdown_line": detail.get("markdown_line"),
            "markdown_image_order": detail.get("markdown_image_order"),
            "detail_type": detail_type,
            "recognized_content": body,
            "recognized_language": recognized_language,
            "normalized_content_zh": normalized_content_zh,
            "display_content": display_content,
            "recognized_preview": detail.get("body_preview") or "",
            "display_preview": display_preview,
            "content_format": content_format,
            "confidence": _image_semantic_confidence(block_type, sub_type, detail),
            "source": "markdown_details_with_content_list" if detail else "content_list_image_block",
            "evidence": [
                value
                for value in (
                    "content_list_block",
                    "markdown_details" if detail else "",
                    "bbox" if block.get("bbox") else "",
                )
                if value
            ],
        }
        chart_data = _markdown_table_to_records(display_content) if content_format == "markdown_table" else None
        flowchart_graph = _mermaid_to_nodes_edges(body) if content_format == "mermaid" else None
        if chart_data:
            item["chart_data"] = chart_data
        if flowchart_graph:
            item["flowchart_graph"] = flowchart_graph
        ocr_vlm_candidate = _image_ocr_vlm_candidate(item)
        actionability = _image_actionability(
            item,
            chart_data=chart_data,
            flowchart_graph=flowchart_graph,
            ocr_vlm_candidate=ocr_vlm_candidate,
        )
        item["ocr_vlm_candidate"] = ocr_vlm_candidate
        item["actionability"] = actionability
        item["show_in_complete"] = _should_show_image_block_in_complete(item)
        blocks.append(item)
    return blocks


def complete_markdown_appendix(enhanced):
    signals = enhanced.get("quality_signals") or {}
    toc = enhanced.get("toc") or {}
    footnotes = enhanced.get("footnotes") or {}
    note_links = enhanced.get("financial_note_links") or {}
    image_blocks = enhanced.get("image_semantic_blocks") or []
    tables = enhanced.get("tables") or []
    lines = [
        "",
        "",
        "---",
        "",
        "# PDF 可恢复信息附录",
        "",
        "> 本附录由解析产物自动生成，用于补足 Markdown 难以表达的 PDF 结构信息；不改写原文和财务数字。",
        "",
        "## 解析溯源摘要",
        "",
        f"- 表格总数：{enhanced.get('table_count', 0)}",
        f"- content_list 精确表格：{(enhanced.get('source_counts') or {}).get('content_list_body_exact', 0)}",
        f"- Markdown 页码推断表格：{(enhanced.get('source_counts') or {}).get('markdown_marker_inferred', 0)}",
        f"- 缺页码表格：{signals.get('table_missing_page_count', 0)}",
        f"- 多级表头候选表：{signals.get('multi_level_header_table_count', 0)}",
        f"- 脚注引用：{signals.get('footnote_reference_count', 0)}",
        f"- 脚注定义：{signals.get('footnote_definition_count', 0)}",
        f"- 目录候选：{signals.get('toc_candidate_count', 0)}",
        f"- 财报项目附注关联：{(note_links.get('summary') or {}).get('linked_item_count', 0)}",
        f"- 图片/图表/公式语义块：{signals.get('image_semantic_block_count', 0)}",
        f"- 已带识别内容的图片语义块：{signals.get('image_semantic_recognized_count', 0)}",
        f"- 可展示图片增强块：{signals.get('image_semantic_show_count', 0)}",
        f"- 按需 OCR/VLM 候选图像：{signals.get('image_semantic_ocr_candidate_count', 0)}",
        "",
    ]
    toc_candidates = toc.get("toc_candidates") or []
    if toc_candidates:
        lines.extend(["## 目录候选索引", ""])
        for item in toc_candidates[:300]:
            page = item.get("target_page_number") or item.get("pdf_page_number") or "--"
            lines.append(f"- 第 {page} 页：{item.get('title')}")
        if len(toc_candidates) > 300:
            lines.append(f"- ... 其余 {len(toc_candidates) - 300} 条见 content_list_enhanced.json")
        lines.append("")
    definitions = footnotes.get("definitions") or []
    if definitions:
        lines.extend(["## 脚注与注释", "", "### 脚注定义"])
        for item in definitions[:200]:
            page = item.get("pdf_page_number") or "--"
            line = item.get("line") or "--"
            lines.append(f"- PDF {page} 页 / MD 行 {line}：{item.get('text')}")
        if len(definitions) > 200:
            lines.append(f"- ... 其余 {len(definitions) - 200} 条见 content_list_enhanced.json")
        lines.append("")
    unbound = [item for item in (footnotes.get("bindings") or []) if item.get("status") == "unbound"]
    if unbound:
        lines.extend(["### 未绑定脚注引用"])
        for item in unbound[:100]:
            lines.append(
                f"- 标记 {item.get('marker')} / PDF {item.get('reference_page') or '--'} 页 / MD 行 {item.get('reference_line') or '--'}"
            )
        if len(unbound) > 100:
            lines.append(f"- ... 其余 {len(unbound) - 100} 条见 content_list_enhanced.json")
        lines.append("")
    links = note_links.get("links") or []
    if links:
        lines.extend(["## 财报项目附注关联", ""])
        for item in links[:200]:
            amount_check = item.get("amount_check") or {}
            amount_status = amount_check.get("status") or "未校验"
            amount_confidence = amount_check.get("confidence") or ""
            note_ref = item.get("statement_note_ref") or item.get("note_ref") or "--"
            precision = item.get("precision_level") or item.get("confidence") or "--"
            lines.append(
                f"- {item.get('statement_item')} [{precision}] "
                f"附注 {note_ref} -> {item.get('note_title')} "
                f"(附注页 {item.get('note_page_number') or '--'} / 主表 {item.get('statement_table_index') or '--'} / "
                f"金额校验 {amount_status}{('/' + amount_confidence) if amount_confidence else ''})"
            )
        if len(links) > 200:
            lines.append(f"- ... 其余 {len(links) - 200} 条见 content_list_enhanced.json")
        lines.append("")
    recognized_image_blocks = [item for item in image_blocks if item.get("show_in_complete")]
    if recognized_image_blocks:
        lines.extend(["## 图片、图表与公式增强识别", ""])
        lines.append("仅展示有数据、结构、公式或可检索文字价值的增强块；自然图片等视觉上下文保留在 `content_list_enhanced.json`。")
        lines.append("")
        for item in recognized_image_blocks[:120]:
            page = item.get("pdf_page_number") or "--"
            line = item.get("markdown_line") or "--"
            kind = item.get("semantic_kind") or item.get("type") or "image"
            detail_type = item.get("detail_type") or item.get("sub_type") or "--"
            confidence = item.get("confidence") or "--"
            actionability = item.get("actionability") or "--"
            lines.append(
                f"- 图像 {item.get('image_index')} / {kind} / {detail_type} / "
                f"PDF {page} 页 / MD 行 {line} / 置信度 {confidence} / 可用性 {actionability} / {item.get('image_path')}"
            )
            preview = item.get("display_preview") or item.get("recognized_preview") or ""
            if preview:
                lines.append(f"  - 识别预览：{preview}")
            chart_data = item.get("chart_data") or {}
            if chart_data.get("rows"):
                lines.append(
                    f"  - 图表数据：{chart_data.get('row_count', len(chart_data.get('rows') or []))} 行，字段："
                    f"{'、'.join((chart_data.get('headers') or [])[:8])}"
                )
            flowchart_graph = item.get("flowchart_graph") or {}
            if flowchart_graph.get("nodes") or flowchart_graph.get("edges"):
                lines.append(
                    f"  - 流程结构：{flowchart_graph.get('node_count', len(flowchart_graph.get('nodes') or []))} 个节点，"
                    f"{flowchart_graph.get('edge_count', len(flowchart_graph.get('edges') or []))} 条关系"
                )
        if len(recognized_image_blocks) > 120:
            lines.append(f"- ... 其余 {len(recognized_image_blocks) - 120} 个图片语义块见 content_list_enhanced.json")
        lines.append("")
    ocr_candidates = [item for item in image_blocks if (item.get("ocr_vlm_candidate") or {}).get("needed")]
    if ocr_candidates:
        lines.extend(["## 按需 OCR/VLM 候选图像", ""])
        lines.append("这些图像面积较大但当前缺少可靠文字或结构化内容，建议在人工复核或智能体分析需要时再二次识别。")
        lines.append("")
        for item in ocr_candidates[:60]:
            candidate = item.get("ocr_vlm_candidate") or {}
            page = item.get("pdf_page_number") or "--"
            kind = item.get("semantic_kind") or item.get("type") or "image"
            lines.append(
                f"- 图像 {item.get('image_index')} / {kind} / PDF {page} 页 / "
                f"优先级 {candidate.get('priority') or '--'} / 面积 {round(candidate.get('bbox_area') or 0, 2)} / "
                f"{item.get('image_path')}"
            )
        if len(ocr_candidates) > 60:
            lines.append(f"- ... 其余 {len(ocr_candidates) - 60} 个候选图像见 content_list_enhanced.json")
        lines.append("")
    multi_header_tables = [
        table for table in tables if (table.get("structure") or {}).get("multi_level_header_candidate")
    ]
    if multi_header_tables:
        lines.extend(["## 多级表头候选表", ""])
        lines.append("完整表格结构请查看同目录 `content_list_enhanced.json` 的 `tables[].structure` 字段。")
        lines.append("")
        for table in multi_header_tables[:80]:
            structure = table.get("structure") or {}
            page = table.get("pdf_page_number") or "--"
            line = table.get("line") or "--"
            lines.append(
                f"- 表 {table.get('table_index')} / PDF {page} 页 / MD 行 {line} / "
                f"{structure.get('expanded_rows', 0)} 行 x {structure.get('expanded_columns', 0)} 列 / "
                f"表头候选 {structure.get('header_row_count', 0)} 行"
            )
            for preview in (structure.get("header_preview") or [])[:1]:
                lines.append(f"  - 表头预览：{preview}")
        if len(multi_header_tables) > 80:
            lines.append(f"- ... 其余 {len(multi_header_tables) - 80} 张表见 content_list_enhanced.json")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def complete_markdown_content(markdown, enhanced, corrections=None, apply_table_corrections=None):
    base_markdown = str(markdown or "")
    if corrections is not None and apply_table_corrections is not None:
        base_markdown, _replaced_count = apply_table_corrections(base_markdown, corrections)
    return base_markdown.rstrip() + complete_markdown_appendix(enhanced)


def write_complete_markdown_artifact(
    task,
    markdown,
    enhanced,
    corrections=None,
    *,
    result_dir,
    apply_table_corrections=None,
):
    if markdown is None or not isinstance(enhanced, dict):
        return None
    directory = result_dir(task)
    os.makedirs(directory, exist_ok=True)
    complete_path = os.path.join(directory, "result_complete.md")
    complete_markdown = complete_markdown_content(
        markdown,
        enhanced,
        corrections=corrections,
        apply_table_corrections=apply_table_corrections,
    )
    with open(complete_path, "w", encoding="utf-8") as outfile:
        outfile.write(complete_markdown)
    return complete_path
