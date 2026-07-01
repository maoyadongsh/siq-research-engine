"""Content list enhanced helpers split out from the Flask app boundary."""

from __future__ import annotations

from collections import Counter
import os
import re

from financial_extractor import parse_html_table as financial_parse_html_table
from pdf_parser_page_markers import _collect_text_fragments, _compact_text_fragment, _pdf_page_markers_by_line as pdf_page_markers_by_line
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


SUPERSCRIPT_FOOTNOTE_REF_RE = re.compile(r"[\u00b9\u00b2\u00b3\u2070-\u2079]")
INLINE_FOOTNOTE_REF_RE = re.compile(r"(?<=[\u4e00-\u9fffA-Za-z])[1-9](?=[\u4e00-\u9fff])")
FOOTNOTE_DEF_RE = re.compile(r"^\s*(?:注|注释|说明)?\s*(?:[\u00b9\u00b2\u00b3\u2070-\u2079]|[1-9][\.、）)])\s*")
INLINE_FOOTNOTE_PREV_EXCLUDE = set("第表图附注")
INLINE_FOOTNOTE_NEXT_EXCLUDE = set("页章节条款项年月日号个亿万元股倍")


def markdown_line_offsets(markdown):
    offsets = []
    pos = 0
    for line in str(markdown or "").splitlines(True):
        offsets.append(pos)
        pos += len(line)
    if not offsets:
        offsets.append(0)
    return offsets


def line_number_for_offset(offsets, offset):
    best = 1
    for idx, start in enumerate(offsets, start=1):
        if start > offset:
            break
        best = idx
    return best


def _line_text_at(lines, line_number):
    return lines[line_number - 1] if 0 <= line_number - 1 < len(lines) else ""


def build_enhanced_footnotes(
    markdown,
    content_list=None,
    *,
    pdf_page_markers_by_line=None,
    infer_pdf_page_for_line=inferred_pdf_page_for_line,
):
    text = str(markdown or "")
    lines = text.splitlines()
    offsets = markdown_line_offsets(text)
    page_markers = pdf_page_markers_by_line(text) if callable(pdf_page_markers_by_line) else []
    references = []
    for match in SUPERSCRIPT_FOOTNOTE_REF_RE.finditer(text):
        line = line_number_for_offset(offsets, match.start())
        if FOOTNOTE_DEF_RE.search(_line_text_at(lines, line)):
            continue
        page_number, reason = infer_pdf_page_for_line(line, page_markers)
        references.append(
            {
                "marker": match.group(0),
                "line": line,
                "pdf_page_number": page_number,
                "pdf_page_source": "markdown_marker_inferred" if page_number else "",
                "pdf_page_inference_reason": reason if page_number else "",
                "context": _compact_text_fragment(text[max(0, match.start() - 40) : match.end() + 60], 120),
                "source": "markdown_superscript",
            }
        )
    inline_refs = []
    for match in INLINE_FOOTNOTE_REF_RE.finditer(text):
        line = line_number_for_offset(offsets, match.start())
        if FOOTNOTE_DEF_RE.search(_line_text_at(lines, line)):
            continue
        prev_char = text[match.start() - 1] if match.start() > 0 else ""
        next_char = text[match.end()] if match.end() < len(text) else ""
        if prev_char in INLINE_FOOTNOTE_PREV_EXCLUDE or next_char in INLINE_FOOTNOTE_NEXT_EXCLUDE:
            continue
        inline_refs.append(match)
    if len(inline_refs) <= 80:
        for match in inline_refs:
            line = line_number_for_offset(offsets, match.start())
            page_number, reason = infer_pdf_page_for_line(line, page_markers)
            references.append(
                {
                    "marker": match.group(0),
                    "line": line,
                    "pdf_page_number": page_number,
                    "pdf_page_source": "markdown_marker_inferred" if page_number else "",
                    "pdf_page_inference_reason": reason if page_number else "",
                    "context": _compact_text_fragment(text[max(0, match.start() - 40) : match.end() + 60], 120),
                    "source": "markdown_inline_digit",
                }
            )

    definitions = []
    for line_number, line in enumerate(lines, start=1):
        if not FOOTNOTE_DEF_RE.search(line):
            continue
        page_number, reason = infer_pdf_page_for_line(line_number, page_markers)
        definitions.append(
            {
                "line": line_number,
                "pdf_page_number": page_number,
                "pdf_page_source": "markdown_marker_inferred" if page_number else "",
                "pdf_page_inference_reason": reason if page_number else "",
                "text": _compact_text_fragment(line, 220),
                "source": "markdown_line",
            }
        )

    content_list = _coerce_json_artifact(content_list)
    if isinstance(content_list, list):
        for block in content_list:
            if not isinstance(block, dict):
                continue
            page_number = _block_page_number(block)
            footnotes = []
            if block.get("type") == "table":
                footnotes.extend(block.get("table_footnote") or [])
            if block.get("type") == "image":
                footnotes.extend(block.get("image_footnote") or [])
            for footnote in footnotes:
                footnote_text = str(footnote or "").strip()
                if not footnote_text:
                    continue
                definitions.append(
                    {
                        "line": None,
                        "pdf_page_number": page_number,
                        "pdf_page_source": "content_list",
                        "pdf_page_inference_reason": "",
                        "text": _compact_text_fragment(footnote_text, 220),
                        "source": "content_list_footnote",
                    }
                )

    definition_by_marker = {}
    for definition in definitions:
        marker_match = re.search(r"[\u00b9\u00b2\u00b3\u2070-\u2079]|[1-9]", definition.get("text") or "")
        if marker_match:
            definition_by_marker.setdefault(marker_match.group(0), definition)
    bindings = []
    for ref in references:
        definition = definition_by_marker.get(str(ref.get("marker") or ""))
        bindings.append(
            {
                "marker": ref.get("marker"),
                "reference_line": ref.get("line"),
                "definition_line": definition.get("line") if definition else None,
                "reference_page": ref.get("pdf_page_number"),
                "definition_page": definition.get("pdf_page_number") if definition else None,
                "status": "bound" if definition else "unbound",
            }
        )
    return {
        "references": references[:500],
        "definitions": definitions[:500],
        "bindings": bindings[:500],
        "summary": {
            "reference_count": len(references),
            "definition_count": len(definitions),
            "bound_count": sum(1 for item in bindings if item.get("status") == "bound"),
            "unbound_count": sum(1 for item in bindings if item.get("status") == "unbound"),
            "inline_digit_refs_suppressed": len(inline_refs) > 80,
        },
    }


TOC_LINE_RE = re.compile(r"^(?P<title>第[一二三四五六七八九十百]+[章节篇部][^.\n]{0,80}?|[一二三四五六七八九十]+、[^.\n]{1,80}|[0-9]+(?:\.[0-9]+)*[、. ]+[^.\n]{1,80}?)[\s.·…-]*(?P<page>\d{1,4})?$")


def heading_level_from_text(text):
    title = str(text or "").strip()
    if re.match(r"^第[一二三四五六七八九十百]+[章节篇部]", title):
        return 1
    if re.match(r"^[一二三四五六七八九十]+、", title):
        return 2
    if re.match(r"^[0-9]+(?:\.[0-9]+)+", title):
        return min(6, title.count(".") + 1)
    return 3


def build_enhanced_toc(
    markdown,
    content_list=None,
    *,
    pdf_page_markers_by_line=None,
    infer_pdf_page_for_line=inferred_pdf_page_for_line,
):
    text = str(markdown or "")
    page_markers = pdf_page_markers_by_line(text) if callable(pdf_page_markers_by_line) else []
    headings = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        title = _strip_html(match.group(2)).strip()
        if not title:
            continue
        page_number, reason = infer_pdf_page_for_line(line_number, page_markers)
        headings.append(
            {
                "title": title,
                "level": len(match.group(1)),
                "line": line_number,
                "pdf_page_number": page_number,
                "pdf_page_source": "markdown_marker_inferred" if page_number else "",
                "pdf_page_inference_reason": reason if page_number else "",
                "source": "markdown_heading",
            }
        )

    toc_candidates = []
    toc_zone_lines = set()
    lines = text.splitlines()
    for idx, line in enumerate(lines, start=1):
        cleaned = _strip_html(line).strip()
        if cleaned in {"目录", "目 录", "目次"} or cleaned.startswith("# 目录"):
            for target in range(idx, min(len(lines), idx + 180) + 1):
                toc_zone_lines.add(target)
    for line_number, line in enumerate(lines, start=1):
        if toc_zone_lines and line_number not in toc_zone_lines:
            continue
        cleaned = _strip_html(line).strip()
        if len(cleaned) < 4 or len(cleaned) > 120:
            continue
        match = TOC_LINE_RE.match(cleaned)
        if not match:
            continue
        title = (match.group("title") or "").strip(" .·…-")
        page_text = match.group("page")
        if not title:
            continue
        page_number, reason = infer_pdf_page_for_line(line_number, page_markers)
        toc_candidates.append(
            {
                "title": title,
                "level": heading_level_from_text(title),
                "line": line_number,
                "target_page_number": int(page_text) if page_text else None,
                "pdf_page_number": page_number,
                "pdf_page_source": "markdown_marker_inferred" if page_number else "",
                "pdf_page_inference_reason": reason if page_number else "",
                "source": "markdown_toc_candidate",
            }
        )

    content_headings = []
    content_list = _coerce_json_artifact(content_list)
    if isinstance(content_list, list):
        for block in content_list:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            title = str(block.get("text") or "").strip()
            level = block.get("text_level")
            if not isinstance(level, int) or level <= 0 or not title or len(title) > 120:
                continue
            content_headings.append(
                {
                    "title": title,
                    "level": min(level, 6),
                    "line": None,
                    "pdf_page_number": _block_page_number(block),
                    "pdf_page_source": "content_list",
                    "pdf_page_inference_reason": "",
                    "source": "content_list_text_level",
                }
            )

    return {
        "headings": headings[:500],
        "toc_candidates": toc_candidates[:500],
        "content_headings": content_headings[:500],
        "summary": {
            "heading_count": len(headings),
            "toc_candidate_count": len(toc_candidates),
            "content_heading_count": len(content_headings),
            "headings_with_page": sum(1 for item in headings if item.get("pdf_page_number")),
            "toc_candidates_with_target_page": sum(1 for item in toc_candidates if item.get("target_page_number")),
        },
    }


def table_source_confidence(source_name):
    if source_name in {"content_list_body_exact", "content_list_body_normalized"}:
        return "high"
    if source_name == "markdown_marker_inferred":
        return "medium"
    return "low"


def build_enhanced_quality_signals(tables, footnotes, toc, pages, financial_note_links=None, image_semantic_blocks=None):
    source_counts = Counter(item.get("source") or "unresolved" for item in tables)
    table_count = len(tables)
    exact = source_counts.get("content_list_body_exact", 0) + source_counts.get("content_list_body_normalized", 0)
    inferred = source_counts.get("markdown_marker_inferred", 0)
    missing_page = sum(1 for item in tables if not item.get("pdf_page_number"))
    multi_header = sum(1 for item in tables if (item.get("structure") or {}).get("multi_level_header_candidate"))
    foot_summary = footnotes.get("summary") or {}
    toc_summary = toc.get("summary") or {}
    note_link_summary = (financial_note_links or {}).get("summary") or {}
    image_blocks = image_semantic_blocks or []
    image_kind_counts = Counter(item.get("semantic_kind") or "image" for item in image_blocks)
    image_actionability_counts = Counter(item.get("actionability") or "unknown" for item in image_blocks)
    image_with_recognition = sum(1 for item in image_blocks if item.get("recognized_content"))
    image_with_display = sum(1 for item in image_blocks if item.get("display_content"))
    image_show_count = sum(1 for item in image_blocks if item.get("show_in_complete"))
    image_ocr_candidate_count = sum(1 for item in image_blocks if (item.get("ocr_vlm_candidate") or {}).get("needed"))
    return {
        "table_exact_rate": round(exact / table_count, 4) if table_count else 0,
        "table_inferred_rate": round(inferred / table_count, 4) if table_count else 0,
        "table_missing_page_count": missing_page,
        "multi_level_header_table_count": multi_header,
        "footnote_reference_count": foot_summary.get("reference_count", 0),
        "footnote_definition_count": foot_summary.get("definition_count", 0),
        "footnote_unbound_count": foot_summary.get("unbound_count", 0),
        "toc_heading_count": toc_summary.get("heading_count", 0),
        "toc_candidate_count": toc_summary.get("toc_candidate_count", 0),
        "content_heading_count": toc_summary.get("content_heading_count", 0),
        "page_count_with_content_blocks": len(pages),
        "financial_note_link_count": note_link_summary.get("linked_item_count", 0),
        "image_semantic_block_count": len(image_blocks),
        "image_semantic_kind_counts": dict(image_kind_counts),
        "image_semantic_actionability_counts": dict(image_actionability_counts),
        "image_semantic_recognized_count": image_with_recognition,
        "image_semantic_display_count": image_with_display,
        "image_semantic_show_count": image_show_count,
        "image_semantic_ocr_candidate_count": image_ocr_candidate_count,
    }


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


def parse_financial_amount_cell(value):
    raw = _strip_html(str(value or "")).strip()
    if not raw or raw in {"-", "—", "--", "－", "不适用", "无"}:
        return None
    normalized = raw.replace(",", "").replace("，", "").replace(" ", "")
    normalized = normalized.replace("人民币", "")
    negative = False
    if re.fullmatch(r"[（(].+[）)]", normalized):
        negative = True
        normalized = normalized[1:-1]
    normalized = normalized.replace("%", "")
    normalized = re.sub(r"(?:元|万元|千元|百万元|百万|亿元|万|千|亿)$", "", normalized)
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", normalized):
        return None
    number = float(normalized)
    return -abs(number) if negative else number


def financial_unit_scale_from_text(text):
    compact = str(text or "")
    unit_matches = re.findall(r"(?:单位|金额单位)\s*[：:]?\s*(?:人民币)?\s*(亿元|百万元|万元|千元|元)", compact)
    if unit_matches:
        return financial_unit_scale(unit_matches[-1])
    if "亿元" in compact:
        return 100000000.0
    if "百万元" in compact or "百万" in compact:
        return 1000000.0
    if "万元" in compact or "人民币万元" in compact:
        return 10000.0
    if "千元" in compact:
        return 1000.0
    return 1.0


def financial_unit_scale(unit):
    unit = str(unit or "")
    if unit == "亿元":
        return 100000000.0
    if unit == "百万元" or unit == "百万":
        return 1000000.0
    if unit == "万元":
        return 10000.0
    if unit == "千元":
        return 1000.0
    return 1.0


def financial_unit_scale_near(text, position):
    text = str(text or "")
    position = max(0, min(int(position or 0), len(text)))
    context = text[max(0, position - 1000) : min(len(text), position + 200)]
    unit_matches = list(
        re.finditer(r"(?:单位|金额单位)\s*[：:]?\s*(?:人民币)?\s*(亿元|百万元|万元|千元|元)", context)
    )
    if unit_matches:
        return financial_unit_scale(unit_matches[-1].group(1))
    return 1.0


def normalize_amount_for_compare(value, unit_scale=1.0):
    if value is None:
        return None
    try:
        return float(value) * float(unit_scale or 1.0)
    except (TypeError, ValueError):
        return None


def amount_close(left, right):
    if left is None or right is None:
        return False, None
    diff = abs(float(left) - float(right))
    tolerance = max(1.0, abs(float(left)) * 0.0001, abs(float(right)) * 0.0001)
    return diff <= tolerance, {"difference": diff, "tolerance": tolerance}


FINANCIAL_NOTE_ITEM_ALIASES = {
    "货币资金": ("货币资金", "现金及存放中央银行款项"),
    "交易性金融资产": ("交易性金融资产",),
    "应收票据": ("应收票据",),
    "应收账款": ("应收账款", "应收款项"),
    "预付款项": ("预付款项",),
    "其他应收款": ("其他应收款",),
    "存货": ("存货",),
    "长期股权投资": ("长期股权投资",),
    "固定资产": ("固定资产",),
    "在建工程": ("在建工程",),
    "无形资产": ("无形资产",),
    "商誉": ("商誉",),
    "短期借款": ("短期借款",),
    "应付账款": ("应付账款", "应付款项"),
    "合同负债": ("合同负债",),
    "长期借款": ("长期借款",),
    "吸收存款": ("吸收存款", "客户存款"),
    "发放贷款和垫款": ("发放贷款和垫款", "客户贷款及垫款", "贷款和垫款"),
    "拆出资金": ("拆出资金",),
    "拆入资金": ("拆入资金",),
    "买入返售金融资产": ("买入返售金融资产",),
    "卖出回购金融资产款": ("卖出回购金融资产款",),
    "融出资金": ("融出资金",),
    "代理买卖证券款": ("代理买卖证券款",),
    "应付债券": ("应付债券",),
    "保险合同负债": ("保险合同负债",),
    "投资资产": ("投资资产",),
    "营业收入": ("营业收入", "营业总收入"),
    "营业成本": ("营业成本", "营业总成本"),
    "利息净收入": ("利息净收入",),
    "手续费及佣金净收入": ("手续费及佣金净收入",),
    "保费收入": ("保险业务收入", "已赚保费", "保费收入"),
    "投资收益": ("投资收益",),
    "所得税费用": ("所得税费用",),
    "销售费用": ("销售费用",),
    "管理费用": ("管理费用",),
    "研发费用": ("研发费用",),
    "财务费用": ("财务费用",),
    "经营活动现金流量净额": ("经营活动产生的现金流量净额", "经营活动现金流量净额"),
}


CHINESE_NOTE_SECTION_NUMBERS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
    "十三": 13,
    "十四": 14,
    "十五": 15,
    "十六": 16,
    "十七": 17,
    "十八": 18,
    "十九": 19,
    "二十": 20,
}


def canonical_financial_note_ref(value, current_section=None):
    text = _strip_html(str(value or "")).strip()
    if not text or text in {"-", "—", "--", "无", "不适用"}:
        return None
    text = re.sub(r"\s+", "", text)
    text = text.replace("（", "(").replace("）", ")")
    text = re.sub(r"^(?:附注|注释|附注号|注释号|注|Note|note)", "", text)
    text = text.strip("：:、,.，。;；()[]【】")
    if not text:
        return None
    match = re.match(r"^([一二三四五六七八九十]{1,3})[、.．-]?(\d{1,3})(?:[、.．-](\d{1,3}))?$", text)
    if match:
        section = match.group(1)
        number = match.group(2)
        suffix = f".{match.group(3)}" if match.group(3) else ""
        return f"{section}、{int(number)}{suffix}"
    match = re.match(r"^([一二三四五六七八九十]{1,3})$", text)
    if match and current_section:
        return f"{current_section}、{CHINESE_NOTE_SECTION_NUMBERS.get(match.group(1), match.group(1))}"
    match = re.match(r"^(\d{1,3})(?:[、.．-](\d{1,3}))?$", text)
    if match:
        suffix = f".{int(match.group(2))}" if match.group(2) else ""
        return f"{current_section}、{int(match.group(1))}{suffix}" if current_section else f"{int(match.group(1))}{suffix}"
    return None


def note_ref_numeric_key(note_ref):
    match = re.search(r"(\d{1,3})(?:\.\d+)?$", str(note_ref or ""))
    return match.group(1) if match else ""


def canonical_item_name_from_alias(text):
    compact = _strip_html(str(text or "")).strip()
    for canonical, aliases in FINANCIAL_NOTE_ITEM_ALIASES.items():
        if any(alias and alias in compact for alias in aliases):
            return canonical
    return None


def clean_financial_note_title(text):
    title = _strip_html(str(text or "")).strip()
    title = re.sub(r"^#{1,6}\s*", "", title)
    title = re.sub(r"\s*[（(]\s*续\s*[）)]\s*$", "", title).strip()
    title = re.sub(r"\s+", " ", title)
    return title.strip(" ：:")


def financial_note_title_line_hit(raw_line):
    raw_line = _strip_html(str(raw_line or "")).strip()
    if not raw_line or len(raw_line) > 120:
        return None
    is_markdown_heading = bool(re.match(r"^#{1,6}\s+", raw_line))
    line = re.sub(r"^#{1,6}\s*", "", raw_line).strip()
    if re.search(r"\.{2,}\s*\d{1,4}\s*$", line) or re.search(r"…+\s*\d{1,4}\s*$", line):
        return None
    match = re.match(
        r"^(?:[（(]\s*(\d{1,3})\s*[）)]|(\d{1,3})|([一二三四五六七八九十]{1,3}))"
        r"(?:[、.．)]|\s+)\s*(.+?)\s*$",
        line,
    )
    note_key = None
    title = line
    if match:
        note_key = match.group(1) or match.group(2) or match.group(3)
        title = match.group(4)
    elif not is_markdown_heading:
        return None
    title = clean_financial_note_title(title)
    canonical = canonical_item_name_from_alias(title)
    if not canonical:
        return None
    if not note_key:
        starts_with_alias = any(
            alias and title.startswith(alias)
            for alias in FINANCIAL_NOTE_ITEM_ALIASES.get(canonical, ())
        )
        if not starts_with_alias:
            return None
    return {
        "note_key": note_key,
        "canonical_name": canonical,
        "title": title,
    }


def financial_statement_values_from_table_row(row, skip_columns=None, unit_scale=1.0):
    values = []
    skip_columns = set(skip_columns or [])
    for col_idx, cell in enumerate(row or []):
        if col_idx in skip_columns:
            continue
        amount = parse_financial_amount_cell(cell)
        if amount is None:
            continue
        values.append(
            {
                "column_index": col_idx,
                "raw": _strip_html(str(cell or "")).strip(),
                "value": amount,
                "normalized_value": normalize_amount_for_compare(amount, unit_scale),
                "unit_scale": unit_scale,
            }
        )
    return values[:8]


def statement_table_row_hit_for_canonical(table_html, canonical):
    try:
        grid = financial_parse_html_table(table_html)
    except Exception:
        grid = []
    if len(grid) < 2:
        return None
    unit_scale = financial_unit_scale_from_text(table_html)
    for row_idx, row in enumerate(grid[1:], start=1):
        first_nonempty = next((_strip_html(str(cell or "")).strip() for cell in row if _strip_html(str(cell or "")).strip()), "")
        if canonical_item_name_from_alias(first_nonempty) != canonical:
            continue
        return {
            "row_index": row_idx,
            "matched_alias": first_nonempty,
            "statement_values": financial_statement_values_from_table_row(row, unit_scale=unit_scale),
        }
    return None


def financial_note_zone_start(markdown):
    text = str(markdown or "")
    explicit_pattern = re.compile(
        r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:[一二三四五六七八九十]+(?:[、.．]|\s+))?"
        r"(?:合并|母公司|公司|本集团|集团)?财务报表(?:主要项目|项目)?(?:附注|注释)(?:\s*[（(]续[）)])?|"
        r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:[一二三四五六七八九十]+(?:[、.．]|\s+))?"
        r"(?:合并|母公司|公司|本集团|集团)?财务报表项目注释",
    )
    explicit_matches = list(explicit_pattern.finditer(text))
    min_explicit_offset = int(len(text) * 0.2)
    for match in explicit_matches:
        if match.start() >= min_explicit_offset:
            return match.start()
    if explicit_matches:
        later = explicit_matches[-1]
        if len(explicit_matches) > 1 and later.start() > explicit_matches[0].start():
            return later.start()

    offset = 0
    min_offset = int(len(text) * 0.35)
    for line in text.splitlines(True):
        raw_line = _strip_html(line).strip()
        if offset >= min_offset and financial_note_title_line_hit(raw_line):
            return offset
        offset += len(line)
    return max(0, int(len(text) * 0.45))


def financial_statement_note_ref_hits(markdown, note_start):
    statement_part = str(markdown or "")[:note_start]
    hits = {}
    table_iter = list(re.finditer(r"<table\b.*?</table>", statement_part, flags=re.IGNORECASE | re.DOTALL))
    for table_index, match in enumerate(table_iter, start=1):
        table_html = match.group(0)
        try:
            grid = financial_parse_html_table(table_html)
        except Exception:
            grid = []
        if len(grid) < 2:
            continue
        header_rows = min(4, len(grid))
        note_columns = []
        for row in grid[:header_rows]:
            for col_idx, cell in enumerate(row):
                cell_text = _strip_html(str(cell or "")).strip()
                if cell_text in {"附注", "注释", "附注号", "注释号", "注"} or re.fullmatch(r"附注[一二三四五六七八九十]+", cell_text):
                    note_columns.append((col_idx, canonical_financial_note_ref(cell_text)))
        if not note_columns:
            continue
        note_col_indexes = sorted({col for col, _section in note_columns})
        row_line_base = statement_part.count("\n", 0, match.start()) + 1
        for row_idx, row in enumerate(grid[1:], start=1):
            first_nonempty = next((_strip_html(str(cell or "")).strip() for cell in row if _strip_html(str(cell or "")).strip()), "")
            canonical = canonical_item_name_from_alias(first_nonempty)
            if not canonical:
                continue
            note_ref = None
            note_alias = ""
            for col_idx in note_col_indexes:
                if col_idx >= len(row):
                    continue
                candidate = canonical_financial_note_ref(row[col_idx])
                if candidate:
                    note_ref = candidate
                    note_alias = _strip_html(str(row[col_idx] or "")).strip()
                    break
            if not note_ref:
                continue
            table_unit_scale = financial_unit_scale_from_text(table_html)
            statement_values = financial_statement_values_from_table_row(
                row,
                skip_columns=note_col_indexes,
                unit_scale=table_unit_scale,
            )
            hits[canonical] = {
                "canonical_name": canonical,
                "matched_alias": first_nonempty,
                "line": row_line_base,
                "table_index": table_index,
                "note_ref": note_ref,
                "note_ref_raw": note_alias,
                "source": "statement_note_column",
                "row_index": row_idx,
                "statement_values": statement_values[:8],
            }
    return hits


def financial_statement_table_alias_hits(markdown, note_start):
    statement_part = str(markdown or "")[:note_start]
    hits = {}
    statement_heading_re = re.compile(
        r"(合并资产负债表|资产负债表|合并利润表|利润表|合并现金流量表|现金流量表|"
        r"CONSOLIDATED\s+STATEMENT|STATEMENT\s+OF\s+FINANCIAL\s+POSITION)",
        flags=re.IGNORECASE,
    )
    table_iter = list(re.finditer(r"<table\b.*?</table>", statement_part, flags=re.IGNORECASE | re.DOTALL))
    for table_index, match in enumerate(table_iter, start=1):
        before = _strip_html(statement_part[max(0, match.start() - 900) : match.start()])
        after = _strip_html(statement_part[match.end() : min(len(statement_part), match.end() + 160)])
        table_text = _strip_html(match.group(0))
        has_statement_context = bool(statement_heading_re.search(before))
        has_period = bool(re.search(r"20\d{2}年|20\d{2}|12月31日|本年|上年|本期|上期", table_text[:1200]))
        if not has_statement_context or not has_period:
            continue
        if re.search(r"(附注|注释|项目注释|项目附注|财务报表附注)", after[:120]):
            continue
        try:
            grid = financial_parse_html_table(match.group(0))
        except Exception:
            grid = []
        if len(grid) < 2:
            continue
        unit_scale = financial_unit_scale_from_text(match.group(0))
        row_line_base = statement_part.count("\n", 0, match.start()) + 1
        for row_idx, row in enumerate(grid[1:], start=1):
            first_nonempty = next((_strip_html(str(cell or "")).strip() for cell in row if _strip_html(str(cell or "")).strip()), "")
            canonical = canonical_item_name_from_alias(first_nonempty)
            if not canonical or canonical in hits:
                continue
            statement_values = financial_statement_values_from_table_row(row, unit_scale=unit_scale)
            hits[canonical] = {
                "canonical_name": canonical,
                "matched_alias": first_nonempty,
                "line": row_line_base,
                "table_index": table_index,
                "source": "statement_table_alias",
                "row_index": row_idx,
                "statement_values": statement_values[:8],
            }
    return hits


def financial_statement_item_hits(markdown):
    text = str(markdown or "")
    note_start = financial_note_zone_start(text)
    statement_part = text[:note_start]
    hits = {}
    for canonical, aliases in FINANCIAL_NOTE_ITEM_ALIASES.items():
        best_pos = None
        best_alias = None
        for alias in aliases:
            pos = statement_part.find(alias)
            if pos >= 0 and (best_pos is None or pos < best_pos):
                best_pos = pos
                best_alias = alias
        if best_pos is None:
            continue
        line = statement_part.count("\n", 0, best_pos) + 1
        table_index = None
        statement_values = []
        row_index = None
        for idx, match in enumerate(re.finditer(r"<table\b.*?</table>", statement_part, flags=re.IGNORECASE | re.DOTALL), start=1):
            if match.start() <= best_pos <= match.end():
                table_index = idx
                row_hit = statement_table_row_hit_for_canonical(match.group(0), canonical)
                if row_hit:
                    statement_values = row_hit.get("statement_values") or []
                    row_index = row_hit.get("row_index")
                break
        hits[canonical] = {
            "canonical_name": canonical,
            "matched_alias": best_alias,
            "line": line,
            "table_index": table_index,
            "source": "statement_text_alias",
            "row_index": row_index,
            "statement_values": statement_values,
        }
    for canonical, hit in financial_statement_table_alias_hits(markdown, note_start).items():
        hits.setdefault(canonical, hit)
    hits.update(financial_statement_note_ref_hits(markdown, note_start))
    return list(hits.values())


def financial_note_title_hits(markdown):
    text = str(markdown or "")
    note_start = financial_note_zone_start(text)
    note_part = text[note_start:]
    hits = {}
    offset = 0
    for raw_line in note_part.splitlines(True):
        hit = financial_note_title_line_hit(raw_line)
        if not hit:
            offset += len(raw_line)
            continue
        canonical = hit["canonical_name"]
        if canonical not in hits:
            absolute_pos = note_start + offset
            hits[canonical] = {
                "canonical_name": canonical,
                "matched_alias": hit["title"],
                "title": _compact_text_fragment(hit["title"], 160),
                "line": text.count("\n", 0, absolute_pos) + 1,
            }
        offset += len(raw_line)
    return hits


def financial_note_title_tree(markdown):
    text = str(markdown or "")
    page_markers = pdf_page_markers_by_line(text)
    note_start = financial_note_zone_start(text)
    lines = text.splitlines()
    tree = {}
    current_section = None
    current_scope = ""
    for line_number, line in enumerate(lines, start=1):
        if line_number < text.count("\n", 0, note_start) + 1:
            continue
        raw_line = _strip_html(line).strip()
        if not raw_line:
            continue
        section_match = re.match(
            r"^(?:#{1,6}\s*)?([一二三四五六七八九十]{1,3})(?:[、.．]|\s+)"
            r"\s*(.*(?:财务报表|报表).*(?:附注|注释).*)$",
            raw_line,
        )
        if section_match:
            current_section = section_match.group(1)
            section_title = section_match.group(2)
            if "母公司" in section_title or "公司财务报表" in section_title:
                current_scope = "parent_company"
            elif "合并" in section_title or "集团" in section_title:
                current_scope = "consolidated"
            else:
                current_scope = ""
            continue
        if len(raw_line) > 100:
            continue
        line_hit = financial_note_title_line_hit(raw_line)
        if not line_hit:
            continue
        title = line_hit["title"]
        canonical = line_hit["canonical_name"]
        note_ref = canonical_financial_note_ref(line_hit.get("note_key"), current_section=current_section)
        if not note_ref:
            continue
        page_number, reason = inferred_pdf_page_for_line(line_number, page_markers)
        tree[note_ref] = {
            "note_ref": note_ref,
            "numeric_key": note_ref_numeric_key(note_ref),
            "section": current_section,
            "scope": current_scope,
            "canonical_name": canonical,
            "title": title,
            "line": line_number,
            "pdf_page_number": page_number,
            "pdf_page_source": "markdown_marker_inferred" if page_number else "",
            "pdf_page_inference_reason": reason if page_number else "",
            "source": "markdown_note_title_tree",
        }
    return tree


def financial_note_slice(markdown, note):
    lines = str(markdown or "").splitlines()
    start_line = int((note or {}).get("line") or 0)
    if start_line <= 0 or start_line > len(lines):
        return ""
    end_line = len(lines) + 1
    for line_number in range(start_line + 1, len(lines) + 1):
        raw_line = _strip_html(lines[line_number - 1]).strip()
        if len(raw_line) > 100:
            continue
        if financial_note_title_line_hit(raw_line):
            end_line = line_number
            break
        if re.match(
            r"^(?:#{1,6}\s*)?[一二三四五六七八九十]{1,3}(?:[、.．]|\s+)\s*.*(?:财务报表|报表).*(?:附注|注释)",
            raw_line,
        ):
            end_line = line_number
            break
    return "\n".join(lines[start_line - 1 : min(end_line - 1, start_line + 420)])


def amount_candidates_from_note_slice(note_slice):
    text = str(note_slice or "")[:360000]
    unit_scale = financial_unit_scale_from_text(text)
    candidates = []
    table_iter = list(re.finditer(r"<table\b.*?</table>", text, flags=re.IGNORECASE | re.DOTALL))
    max_candidates = 240
    for table_pos, match in enumerate(table_iter[:24], start=1):
        try:
            grid = financial_parse_html_table(match.group(0))
        except Exception:
            grid = []
        table_unit_scale = financial_unit_scale_from_text(match.group(0))
        if table_unit_scale == 1.0:
            table_unit_scale = financial_unit_scale_near(text, match.start())
        if table_unit_scale == 1.0:
            table_unit_scale = unit_scale
        for row_idx, row in enumerate(grid):
            row_label = _compact_text_fragment(" ".join(_strip_html(str(cell or "")) for cell in row[:2]), 80)
            for col_idx, cell in enumerate(row):
                amount = parse_financial_amount_cell(cell)
                if amount is None:
                    continue
                candidates.append(
                    {
                        "source": "note_table",
                        "table_position": table_pos,
                        "row_index": row_idx,
                        "column_index": col_idx,
                        "row_label": row_label,
                        "raw": _strip_html(str(cell or "")).strip(),
                        "value": amount,
                        "normalized_value": normalize_amount_for_compare(amount, table_unit_scale),
                        "unit_scale": table_unit_scale,
                    }
                )
                if len(candidates) >= max_candidates:
                    break
            if len(candidates) >= max_candidates:
                break
        if len(candidates) >= max_candidates:
            break
    without_tables = re.sub(r"<table\b.*?</table>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    for match in re.finditer(r"[（(]?-?\d{1,3}(?:[,，]\d{3})*(?:\.\d+)?[）)]?|-?\d+(?:\.\d+)?", without_tables):
        amount = parse_financial_amount_cell(match.group(0))
        if amount is None:
            continue
        candidates.append(
            {
                "source": "note_text",
                "raw": match.group(0),
                "value": amount,
                "normalized_value": normalize_amount_for_compare(amount, unit_scale),
                "unit_scale": unit_scale,
            }
        )
        if len(candidates) >= max_candidates:
            break
    return candidates[:max_candidates]


def build_financial_note_amount_check(item, note, markdown):
    statement_values = item.get("statement_values") or []
    if not statement_values:
        return {
            "status": "no_statement_amount",
            "confidence": "none",
            "statement_values": [],
            "note_candidates": [],
            "matched": None,
        }
    note_slice = financial_note_slice(markdown, note)
    candidates = amount_candidates_from_note_slice(note_slice)
    sample_statement_values = statement_values[:4]
    sample_note_candidates = candidates[:12]
    for candidate_source, confidence in (("note_table", "high"), ("note_text", "medium")):
        scoped_candidates = [candidate for candidate in candidates if candidate.get("source") == candidate_source]
        for statement_value in statement_values:
            left = statement_value.get("normalized_value")
            for candidate in scoped_candidates:
                matched, detail = amount_close(left, candidate.get("normalized_value"))
                if not matched:
                    continue
                return {
                    "status": "verified",
                    "confidence": confidence,
                    "statement_values": sample_statement_values,
                    "note_candidates": sample_note_candidates,
                    "matched": {
                        "statement": statement_value,
                        "note": candidate,
                        **(detail or {}),
                    },
                }
    return {
        "status": "unverified",
        "confidence": "low",
        "statement_values": sample_statement_values,
        "note_candidates": sample_note_candidates,
        "matched": None,
    }


def financial_note_link_precision(link):
    if link.get("confidence") != "high":
        return link.get("confidence") or "medium"
    amount_check = link.get("amount_check") or {}
    if amount_check.get("status") == "verified" and amount_check.get("confidence") == "high":
        return "audit_ready_navigation"
    if amount_check.get("status") == "verified":
        return "high_with_amount_text_match"
    return "high_navigation_unverified_amount"


def financial_note_amount_summary(links):
    checks = [item.get("amount_check") or {} for item in links]
    return {
        "amount_check_count": len([item for item in checks if item.get("status")]),
        "amount_verified_count": sum(1 for item in checks if item.get("status") == "verified"),
        "amount_verified_table_count": sum(
            1
            for item in checks
            if item.get("status") == "verified" and item.get("confidence") == "high"
        ),
        "amount_unverified_count": sum(1 for item in checks if item.get("status") == "unverified"),
        "amount_no_statement_count": sum(1 for item in checks if item.get("status") == "no_statement_amount"),
    }


def build_financial_note_links(markdown, tables, page_markers):
    statement_items = financial_statement_item_hits(markdown)
    note_titles = financial_note_title_hits(markdown)
    note_tree = financial_note_title_tree(markdown)
    note_tree_by_numeric = {}
    for note in note_tree.values():
        note_tree_by_numeric.setdefault(note.get("numeric_key"), []).append(note)
    table_by_index = {item.get("table_index"): item for item in tables}
    links = []
    for item in statement_items:
        note = None
        method = "statement_item_to_note_title_alias"
        confidence = "medium"
        evidence = []
        note_ref = item.get("note_ref")
        if note_ref:
            direct = note_tree.get(note_ref)
            numeric_matches = note_tree_by_numeric.get(note_ref_numeric_key(note_ref)) or []
            if direct:
                note = direct
                evidence.append("note_ref_exact")
            elif len(numeric_matches) == 1:
                note = numeric_matches[0]
                evidence.append("note_ref_numeric_unique")
            elif numeric_matches:
                same_name = [candidate for candidate in numeric_matches if candidate.get("canonical_name") == item["canonical_name"]]
                if len(same_name) == 1:
                    note = same_name[0]
                    evidence.append("note_ref_numeric_and_title")
            if note:
                if note.get("canonical_name") == item["canonical_name"]:
                    method = "statement_note_ref_to_note_title"
                    evidence.append("title_match")
                    confidence = (
                        "high"
                        if "note_ref_exact" in evidence and "title_match" in evidence
                        else "medium"
                    )
                else:
                    note = None
                    evidence.append("note_ref_title_mismatch")
        if note is None:
            note = note_titles.get(item["canonical_name"])
            evidence.append("title_alias_match")
        if not note:
            continue
        amount_check = build_financial_note_amount_check(item, note, markdown)
        statement_page, statement_reason = inferred_pdf_page_for_line(item["line"], page_markers)
        note_page, note_reason = inferred_pdf_page_for_line(note["line"], page_markers)
        table = table_by_index.get(item.get("table_index")) or {}
        link = {
            "statement_item": item["canonical_name"],
            "statement_alias": item.get("matched_alias"),
            "statement_line": item.get("line"),
            "statement_table_index": item.get("table_index"),
            "statement_note_ref": note_ref,
            "statement_note_ref_raw": item.get("note_ref_raw"),
            "statement_page_number": table.get("pdf_page_number") or statement_page,
            "statement_page_source": table.get("source") if table.get("pdf_page_number") else ("markdown_marker_inferred" if statement_page else ""),
            "statement_page_inference_reason": table.get("pdf_page_inference_reason") or statement_reason,
            "note_title": note.get("title"),
            "note_alias": note.get("matched_alias"),
            "note_ref": note.get("note_ref"),
            "note_scope": note.get("scope"),
            "note_line": note.get("line"),
            "note_page_number": note.get("pdf_page_number") or note_page,
            "note_page_source": note.get("pdf_page_source") or ("markdown_marker_inferred" if note_page else ""),
            "note_page_inference_reason": note.get("pdf_page_inference_reason") or (note_reason if note_page else ""),
            "confidence": confidence,
            "method": method,
            "evidence": evidence,
            "amount_check": amount_check,
        }
        link["precision_level"] = financial_note_link_precision(link)
        links.append(link)
    amount_summary = financial_note_amount_summary(links)
    return {
        "links": links[:500],
        "note_title_tree": list(note_tree.values())[:500],
        "summary": {
            "statement_item_count": len(statement_items),
            "note_title_count": len(note_titles),
            "note_title_tree_count": len(note_tree),
            "linked_item_count": len(links),
            "high_confidence_link_count": sum(1 for item in links if item.get("confidence") == "high"),
            "audit_ready_navigation_count": sum(
                1 for item in links if item.get("precision_level") == "audit_ready_navigation"
            ),
            **amount_summary,
        },
    }


_canonical_financial_note_ref = canonical_financial_note_ref
_note_ref_numeric_key = note_ref_numeric_key
_canonical_item_name_from_alias = canonical_item_name_from_alias
_clean_financial_note_title = clean_financial_note_title
_financial_note_title_line_hit = financial_note_title_line_hit
_financial_statement_values_from_table_row = financial_statement_values_from_table_row
_statement_table_row_hit_for_canonical = statement_table_row_hit_for_canonical
_financial_note_zone_start = financial_note_zone_start
_financial_statement_note_ref_hits = financial_statement_note_ref_hits
_financial_statement_table_alias_hits = financial_statement_table_alias_hits
_financial_statement_item_hits = financial_statement_item_hits
_financial_note_title_hits = financial_note_title_hits
_financial_note_title_tree = financial_note_title_tree
_financial_note_slice = financial_note_slice
_amount_candidates_from_note_slice = amount_candidates_from_note_slice
_build_financial_note_amount_check = build_financial_note_amount_check
_financial_note_link_precision = financial_note_link_precision
_financial_note_amount_summary = financial_note_amount_summary
_build_financial_note_links = build_financial_note_links


_parse_financial_amount_cell = parse_financial_amount_cell
_financial_unit_scale_from_text = financial_unit_scale_from_text
_financial_unit_scale = financial_unit_scale
_financial_unit_scale_near = financial_unit_scale_near
_normalize_amount_for_compare = normalize_amount_for_compare
_amount_close = amount_close


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
