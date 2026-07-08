"""Table merge helpers for physical and logical table artifacts."""

from __future__ import annotations

import html
import re
from typing import Any


TABLE_RELATION_RULESET_VERSION = "table_merge_ruleset_2026_07_08_01"


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
    return {
        "schema_version": "document_table_relations_v1",
        "ruleset_version": TABLE_RELATION_RULESET_VERSION,
        "task_id": task_id,
        "relations": [],
    }


def build_table_relations(
    task_id: str,
    tables: list[dict[str, Any]],
    blocks: list[dict[str, Any]] | None = None,
    markdown: str = "",
) -> dict[str, Any]:
    """Build conservative cross-page continuation candidates for physical tables."""
    blocks = blocks or []
    rendered_tables = _extract_rendered_table_blocks(markdown)
    rendered_table_by_id = _map_tables_to_rendered_tables(tables, rendered_tables)

    normalized = [table for table in tables if table.get("table_id") and _bbox(table)]
    by_page: dict[int, list[dict[str, Any]]] = {}
    for table in normalized:
        by_page.setdefault(int(table.get("page_number") or 1), []).append(table)
    for page_tables in by_page.values():
        page_tables.sort(key=lambda item: (_bbox(item)[1], _bbox(item)[0]))

    first_table_ids_on_page: dict[int, set[str]] = {}
    for page_number, page_tables in by_page.items():
        first_table_ids_on_page[page_number] = {
            str(table.get("table_id") or "")
            for table in page_tables
            if _is_first_table_on_page(table, page_tables)
        }

    relations: list[dict[str, Any]] = []
    relation_index = 1
    used_to_tables: set[str] = set()
    for page_number in sorted(by_page):
        next_tables = by_page.get(page_number + 1) or []
        if not next_tables:
            continue
        next_page_first_table_ids = first_table_ids_on_page.get(page_number + 1) or set()
        from_tables = sorted(by_page[page_number], key=lambda item: _bbox(item)[3], reverse=True)
        for from_table in from_tables:
            if not _is_last_table_on_page(from_table, by_page[page_number]):
                continue
            best: tuple[float, dict[str, Any], list[str]] | None = None
            for to_table in next_tables:
                to_id = str(to_table.get("table_id") or "")
                if to_id in used_to_tables:
                    continue
                if next_page_first_table_ids and to_id not in next_page_first_table_ids:
                    continue
                markdown_state = _markdown_relation_state(from_table, to_table, rendered_table_by_id)
                if markdown_state == "separate":
                    continue
                if markdown_state != "same" and _has_body_text_after_table_on_page(from_table, blocks):
                    continue
                blocking_reasons = _target_continuation_blockers(
                    to_table,
                    blocks,
                    rendered_tables,
                    rendered_table_by_id,
                    from_table=from_table,
                )
                if blocking_reasons:
                    continue
                if not _passes_cross_page_geometry(from_table, to_table, tables) and markdown_state != "same":
                    continue
                if markdown_state != "same" and not _compatible_continuation_columns(from_table, to_table):
                    continue
                score, reasons = _continuation_score(from_table, to_table, tables)
                if markdown_state == "same":
                    score += 0.18
                    reasons.append("rendered_markdown_same_table")
                    score = min(score, 0.98)
                if score < 0.58:
                    continue
                if best is None or score > best[0]:
                    best = (score, to_table, reasons)
            if best is None:
                continue
            score, to_table, reasons = best
            used_to_tables.add(str(to_table.get("table_id") or ""))
            from_id = str(from_table.get("table_id") or "")
            to_id = str(to_table.get("table_id") or "")
            relation_type = "continuation" if score >= 0.82 else "candidate_continuation"
            from_bbox = _bbox(from_table)
            to_bbox = _bbox(to_table)
            relations.append(
                {
                    "relation_id": f"rel-{relation_index:06d}",
                    "from_table_id": from_id,
                    "to_table_id": to_id,
                    "source_table_id": from_id,
                    "target_table_id": to_id,
                    "fragment_table_ids": [from_id, to_id],
                    "relation_type": relation_type,
                    "merge_status": "auto_merged" if relation_type == "continuation" else "candidate",
                    "confidence": round(score, 4),
                    "merge_confidence": round(score, 4),
                    "page_numbers": [int(from_table.get("page_number") or 1), int(to_table.get("page_number") or 1)],
                    "visual_connector": {
                        "from_page": int(from_table.get("page_number") or 1),
                        "to_page": int(to_table.get("page_number") or 1),
                        "from_anchor": [_mid_x(from_bbox), from_bbox[3]],
                        "to_anchor": [_mid_x(to_bbox), to_bbox[1]],
                    },
                    "reasons": reasons,
                    "merge_reasons": reasons,
                    "review_status": "unreviewed",
                }
            )
            relation_index += 1
    return {
        "schema_version": "document_table_relations_v1",
        "ruleset_version": TABLE_RELATION_RULESET_VERSION,
        "task_id": task_id,
        "relations": relations,
    }


def build_logical_tables(task_id: str, tables: list[dict[str, Any]], relations: list[dict[str, Any]]) -> dict[str, Any]:
    logical_tables: list[dict[str, Any]] = []
    table_by_id = {str(table.get("table_id") or ""): table for table in tables if table.get("table_id")}
    used: set[str] = set()

    for relation in relations:
        table_ids = [str(item) for item in relation.get("fragment_table_ids") or [] if str(item)]
        fragments = [table_by_id[table_id] for table_id in table_ids if table_id in table_by_id]
        if len(fragments) < 2:
            continue
        used.update(table_ids)
        first = fragments[0]
        logical_tables.append(
            {
                "logical_table_id": f"lt-{len(logical_tables) + 1:06d}",
                "title": first.get("title") or first.get("caption") or table_ids[0],
                "fragment_table_ids": table_ids,
                "merge_status": "auto_merged" if relation.get("relation_type") == "continuation" else "candidate",
                "merge_confidence": relation.get("confidence") or 0.0,
                "merge_reasons": relation.get("merge_reasons") or relation.get("reasons") or [],
                "header_rows": [],
                "rows": [],
                "html": "\n".join(str(table.get("html") or "") for table in fragments if table.get("html")),
                "markdown": "\n\n".join(str(table.get("markdown") or "") for table in fragments if table.get("markdown")),
                "source_fragments": [
                    {
                        "table_id": str(table.get("table_id") or ""),
                        "page_number": table.get("page_number") or 1,
                        "row_range": [0, max(0, int((table.get("quality") or {}).get("row_count") or 0))],
                    }
                    for table in fragments
                ],
                "evidence_ids": [
                    f"doc:{task_id}:p{table.get('page_number') or 1}:{table.get('table_id') or ''}"
                    for table in fragments
                ],
                "warnings": [],
            }
        )

    for table in tables:
        table_id = str(table.get("table_id") or "")
        if table_id in used:
            continue
        logical_tables.append(single_fragment_logical_table(task_id, table, len(logical_tables) + 1))
    return {"schema_version": "document_logical_tables_v1", "task_id": task_id, "logical_tables": logical_tables}


def _continuation_score(from_table: dict[str, Any], to_table: dict[str, Any], all_tables: list[dict[str, Any]]) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 0.0
    if int(to_table.get("page_number") or 1) - int(from_table.get("page_number") or 1) == 1:
        score += 0.22
        reasons.append("adjacent_pages")

    from_columns = _column_count(from_table)
    to_columns = _column_count(to_table)
    if from_columns and to_columns and from_columns == to_columns:
        score += 0.22
        reasons.append("same_column_count")
    elif from_columns and to_columns and abs(from_columns - to_columns) <= 1:
        score += 0.1
        reasons.append("similar_column_count")

    from_bbox = _bbox(from_table)
    to_bbox = _bbox(to_table)
    page_height = max(_page_height(all_tables, int(from_table.get("page_number") or 1)), _page_height(all_tables, int(to_table.get("page_number") or 1)))
    if page_height:
        if from_bbox[3] >= page_height * 0.68:
            score += 0.16
            reasons.append("first_fragment_near_page_bottom")
        if to_bbox[1] <= page_height * 0.38:
            score += 0.16
            reasons.append("second_fragment_near_page_top")

    from_width = max(1.0, from_bbox[2] - from_bbox[0])
    to_width = max(1.0, to_bbox[2] - to_bbox[0])
    width_ratio = min(from_width, to_width) / max(from_width, to_width)
    left_delta = abs(from_bbox[0] - to_bbox[0]) / max(from_width, to_width)
    if width_ratio >= 0.75:
        score += 0.12
        reasons.append("similar_table_width")
    if left_delta <= 0.2:
        score += 0.08
        reasons.append("similar_left_edge")

    from_title = _norm_text(from_table.get("title") or from_table.get("caption") or "")
    to_title = _norm_text(to_table.get("title") or to_table.get("caption") or "")
    if from_title and to_title and from_title == to_title:
        score += 0.1
        reasons.append("same_caption")
    elif from_title and not to_title:
        score += 0.05
        reasons.append("caption_inherited")

    from_page = int(from_table.get("page_number") or 1)
    to_page = int(to_table.get("page_number") or 1)
    from_page_tables = [table for table in all_tables if int(table.get("page_number") or 1) == from_page and _bbox(table)]
    to_page_tables = [table for table in all_tables if int(table.get("page_number") or 1) == to_page and _bbox(table)]
    if from_page_tables:
        from_page_tables.sort(key=lambda item: (_bbox(item)[1], _bbox(item)[0]))
        if str(from_table.get("table_id") or "") == str(from_page_tables[-1].get("table_id") or ""):
            score += 0.12
            reasons.append("last_table_on_source_page")
    if to_page_tables:
        to_page_tables.sort(key=lambda item: (_bbox(item)[1], _bbox(item)[0]))
        if str(to_table.get("table_id") or "") == str(to_page_tables[0].get("table_id") or ""):
            score += 0.18
            reasons.append("first_table_on_target_page")
        else:
            score -= 0.22
            reasons.append("not_first_table_on_target_page")

    return min(score, 0.98), reasons


def _bbox(table: dict[str, Any]) -> list[float]:
    bbox = table.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        return []
    try:
        return [float(value) for value in bbox]
    except (TypeError, ValueError):
        return []


def _target_continuation_blockers(
    to_table: dict[str, Any],
    blocks: list[dict[str, Any]],
    rendered_tables: list[dict[str, Any]],
    rendered_table_by_id: dict[str, int],
    *,
    from_table: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    if _has_title_before_table_on_page(to_table, blocks):
        blockers.append("target_page_title_before_table")
    markdown_state = _markdown_relation_state(from_table, to_table, rendered_table_by_id)
    if markdown_state == "separate":
        blockers.append("rendered_markdown_separate_tables")
    if rendered_tables and _table_signature(to_table) and str(to_table.get("table_id") or "") not in rendered_table_by_id:
        blockers.append("target_table_missing_from_rendered_markdown")
    return blockers


def _passes_cross_page_geometry(from_table: dict[str, Any], to_table: dict[str, Any], all_tables: list[dict[str, Any]]) -> bool:
    from_bbox = _bbox(from_table)
    to_bbox = _bbox(to_table)
    if not from_bbox or not to_bbox:
        return False
    page_height = max(
        _page_height(all_tables, int(from_table.get("page_number") or 1)),
        _page_height(all_tables, int(to_table.get("page_number") or 1)),
    )
    return from_bbox[3] >= page_height * 0.68 and to_bbox[1] <= page_height * 0.38


def _compatible_continuation_columns(from_table: dict[str, Any], to_table: dict[str, Any]) -> bool:
    if not _table_signature(to_table):
        return True
    from_columns = _column_count(from_table)
    to_columns = _column_count(to_table)
    return bool(from_columns and to_columns and from_columns == to_columns)


def _is_first_table_on_page(table: dict[str, Any], page_tables: list[dict[str, Any]]) -> bool:
    bbox = _bbox(table)
    if not bbox:
        return False
    same_physical_tables = [
        item
        for item in page_tables
        if _same_physical_table(table, item)
    ]
    first_y = min((_bbox(item)[1] for item in page_tables if _bbox(item)), default=bbox[1])
    return min((_bbox(item)[1] for item in same_physical_tables if _bbox(item)), default=bbox[1]) <= first_y + 2


def _is_last_table_on_page(table: dict[str, Any], page_tables: list[dict[str, Any]]) -> bool:
    bbox = _bbox(table)
    if not bbox:
        return False
    same_physical_tables = [
        item
        for item in page_tables
        if _same_physical_table(table, item)
    ]
    last_y = max((_bbox(item)[3] for item in page_tables if _bbox(item)), default=bbox[3])
    return max((_bbox(item)[3] for item in same_physical_tables if _bbox(item)), default=bbox[3]) >= last_y - 2


def _same_physical_table(first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_bbox = _bbox(first)
    second_bbox = _bbox(second)
    if not first_bbox or not second_bbox:
        return False
    if int(first.get("page_number") or 1) != int(second.get("page_number") or 1):
        return False
    return all(abs(first_bbox[index] - second_bbox[index]) <= 2 for index in range(4))


def _has_title_before_table_on_page(table: dict[str, Any], blocks: list[dict[str, Any]]) -> bool:
    table_bbox = _bbox(table)
    page_number = int(table.get("page_number") or 1)
    if not table_bbox:
        return False
    for block in sorted(_page_blocks_before_y(blocks, page_number, table_bbox[1]), key=lambda item: _block_y(item)):
        if _is_ignorable_page_chrome(block):
            continue
        if str(block.get("type") or "").lower() == "table":
            continue
        text = _block_text(block)
        if _is_heading_block(block, text):
            return True
    return False


def _has_body_text_after_table_on_page(table: dict[str, Any], blocks: list[dict[str, Any]]) -> bool:
    table_bbox = _bbox(table)
    page_number = int(table.get("page_number") or 1)
    if not table_bbox:
        return False
    for block in sorted(_page_blocks_after_y(blocks, page_number, table_bbox[3]), key=lambda item: _block_y(item)):
        if _is_ignorable_page_chrome(block):
            continue
        if str(block.get("type") or "").lower() == "table":
            continue
        text = _block_text(block)
        if text and not _is_unit_or_note_line(text):
            return True
    return False


def _page_blocks_before_y(blocks: list[dict[str, Any]], page_number: int, y: float) -> list[dict[str, Any]]:
    result = []
    for block in blocks:
        if int(block.get("page_number") or 1) != page_number:
            continue
        bbox = _bbox(block)
        if not bbox or bbox[3] > y + 2:
            continue
        result.append(block)
    return result


def _page_blocks_after_y(blocks: list[dict[str, Any]], page_number: int, y: float) -> list[dict[str, Any]]:
    result = []
    for block in blocks:
        if int(block.get("page_number") or 1) != page_number:
            continue
        bbox = _bbox(block)
        if not bbox or bbox[1] <= y + 2:
            continue
        result.append(block)
    return result


def _is_heading_block(block: dict[str, Any], text: str) -> bool:
    if not text:
        return False
    if _is_unit_or_note_line(text):
        return False
    if str(block.get("sub_type") or "") in {"1", "2", "3", "4", "5", "6"}:
        return True
    if str(block.get("type") or "").lower() in {"heading", "section", "caption"}:
        return True
    markdown = str(block.get("markdown") or "").lstrip()
    if markdown.startswith("#"):
        return True
    return _looks_like_title(text)


def _looks_like_title(text: str) -> bool:
    compact = _norm_text(text)
    if not compact or len(compact) > 80:
        return False
    if _is_unit_or_note_line(text):
        return False
    if re.match(r"^[一二三四五六七八九十]+[、.．]", text):
        return True
    if re.match(r"^[(（]?[一二三四五六七八九十0-9]+[)）]", text):
        return True
    if re.match(r"^第[一二三四五六七八九十0-9]+[章节]", text):
        return True
    if re.match(r"^[0-9]+[、.．]", text):
        return True
    return len(compact) <= 32 and not any(char in compact for char in "，。；;：:")


def _is_unit_or_note_line(text: str) -> bool:
    compact = _norm_text(text)
    if not compact:
        return True
    if re.fullmatch(r"[-—_·•.\s0-9第页/]+", compact):
        return True
    return bool(re.fullmatch(r"(单位[:：])?([人民币港币美元欧元万亿元千元百万元亿元股%％,.，/]+)", compact))


def _is_ignorable_page_chrome(block: dict[str, Any]) -> bool:
    text = _block_text(block)
    bbox = _bbox(block)
    if not text:
        return True
    compact = _norm_text(text)
    if re.fullmatch(r"\d+", compact):
        return True
    if _is_unit_or_note_line(text):
        return True
    block_type = str(block.get("type") or "").lower()
    if block_type in {"title", "header", "page_number"} and bbox and (bbox[3] <= 96 or bbox[1] >= 890):
        return True
    if block_type == "page_number":
        return True
    if bbox and bbox[1] >= 900:
        return True
    return False


def _block_y(block: dict[str, Any]) -> float:
    bbox = _bbox(block)
    return bbox[1] if bbox else float(block.get("reading_order") or 0)


def _block_text(block: dict[str, Any]) -> str:
    text = str(block.get("text") or "").strip()
    if text:
        return text
    markdown = str(block.get("markdown") or "").strip()
    return re.sub(r"^#+\s*", "", markdown).strip()


def _extract_rendered_table_blocks(markdown: str) -> list[dict[str, Any]]:
    if not markdown:
        return []
    table_blocks: list[dict[str, Any]] = []
    for match in re.finditer(r"(?is)<table\b.*?</table>", markdown):
        table_blocks.append({"start": match.start(), "end": match.end(), "text": _normalize_table_text(match.group(0))})

    lines = markdown.splitlines(keepends=True)
    offset = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("|") and "|" in stripped[1:]:
            start = offset
            parts = [line]
            index += 1
            offset += len(line)
            while index < len(lines):
                next_line = lines[index]
                if not next_line.strip().startswith("|"):
                    break
                parts.append(next_line)
                offset += len(next_line)
                index += 1
            end = offset
            table_blocks.append({"start": start, "end": end, "text": _normalize_table_text("".join(parts))})
            continue
        offset += len(line)
        index += 1

    table_blocks.sort(key=lambda item: int(item["start"]))
    return table_blocks


def _map_tables_to_rendered_tables(tables: list[dict[str, Any]], rendered_tables: list[dict[str, Any]]) -> dict[str, int]:
    if not rendered_tables:
        return {}
    mapping: dict[str, int] = {}
    for table in tables:
        table_id = str(table.get("table_id") or "")
        if not table_id:
            continue
        signature = _table_signature(table)
        if not signature:
            continue
        best_index: int | None = None
        best_score = 0
        for index, rendered in enumerate(rendered_tables):
            rendered_text = str(rendered.get("text") or "")
            score = _signature_match_score(signature, rendered_text)
            if score > best_score:
                best_index = index
                best_score = score
        if best_index is None or best_score < max(2, min(4, len(signature))):
            continue
        mapping[table_id] = best_index
    return mapping


def _markdown_relation_state(
    from_table: dict[str, Any],
    to_table: dict[str, Any],
    rendered_table_by_id: dict[str, int],
) -> str:
    from_id = str(from_table.get("table_id") or "")
    to_id = str(to_table.get("table_id") or "")
    if from_id not in rendered_table_by_id or to_id not in rendered_table_by_id:
        return "unknown"
    if rendered_table_by_id[from_id] == rendered_table_by_id[to_id]:
        return "same"
    return "separate"


def _table_signature(table: dict[str, Any]) -> list[str]:
    text = _normalize_table_text(table.get("html") or table.get("markdown") or table.get("text") or "")
    tokens = [token for token in re.split(r"\s+", text) if token]
    result: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if _is_signature_noise(token) or token in seen:
            continue
        seen.add(token)
        result.append(token)
        if len(result) >= 12:
            break
    return result


def _signature_match_score(signature: list[str], rendered_text: str) -> int:
    return sum(1 for token in signature if token in rendered_text)


def _is_signature_noise(token: str) -> bool:
    return token in {"---", ":---", "---:", ":---:", "table", "tbody", "tr", "td", "th"} or re.fullmatch(r"[-—|]+", token) is not None


def _normalize_table_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"(?is)<t[dh][^>]*>", " ", text)
    text = re.sub(r"(?is)</t[dh]>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("|", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _mid_x(bbox: list[float]) -> float:
    return round((bbox[0] + bbox[2]) / 2, 2) if len(bbox) == 4 else 0.0


def _column_count(table: dict[str, Any]) -> int:
    quality = table.get("quality") if isinstance(table.get("quality"), dict) else {}
    value = quality.get("column_count")
    try:
        if int(value or 0) > 0:
            return int(value)
    except (TypeError, ValueError):
        pass
    rows = table.get("cells")
    if isinstance(rows, list):
        columns = []
        for cell in rows:
            if isinstance(cell, dict):
                try:
                    columns.append(int(cell.get("column_index") or 0))
                except (TypeError, ValueError):
                    continue
        if columns:
            return max(columns) + 1
    return 0


def _page_height(tables: list[dict[str, Any]], page_number: int) -> float:
    heights = [_bbox(table)[3] for table in tables if int(table.get("page_number") or 1) == page_number and _bbox(table)]
    return max([1000.0, *heights])


def _norm_text(value: Any) -> str:
    return "".join(str(value or "").split()).lower()
