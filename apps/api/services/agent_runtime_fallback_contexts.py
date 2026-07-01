"""Pure fallback context formatting helpers for the Hermes agent runtime."""

from __future__ import annotations

import html
import re
from typing import Any


def _postgres_row_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("metric_payload")
    return payload if isinstance(payload, dict) else {}


def _postgres_row_source(row: dict[str, Any]) -> dict[str, Any]:
    payload = _postgres_row_payload(row)
    source = payload.get("source")
    return source if isinstance(source, dict) else {}


def _postgres_row_metric_name(row: dict[str, Any]) -> str:
    payload = _postgres_row_payload(row)
    for value in (
        row.get("item_name"),
        row.get("metric_name"),
        payload.get("item_name"),
        payload.get("metric_name"),
        row.get("metric_key"),
        row.get("canonical_name"),
        payload.get("canonical_name"),
    ):
        if value not in (None, ""):
            return str(value)
    return "未返回"


def _postgres_row_value(row: dict[str, Any]) -> Any:
    payload = _postgres_row_payload(row)
    return row.get("raw_value") or row.get("value") or payload.get("raw_value") or payload.get("value")


def _postgres_row_unit(row: dict[str, Any]) -> Any:
    payload = _postgres_row_payload(row)
    return row.get("unit") or payload.get("unit")


def _postgres_row_pdf_page(row: dict[str, Any]) -> Any:
    source = _postgres_row_source(row)
    for value in (
        row.get("source_page_number"),
        row.get("pdf_page"),
        row.get("pdf_page_number"),
        source.get("page_number"),
        source.get("pdf_page"),
        source.get("pdf_page_number"),
    ):
        if value not in (None, ""):
            return value
    return None


def _postgres_row_table_index(row: dict[str, Any]) -> Any:
    source = _postgres_row_source(row)
    for value in (
        row.get("source_table_index"),
        row.get("table_index"),
        source.get("table_index"),
        source.get("source_table_index"),
    ):
        if value not in (None, ""):
            return value
    return None


def _postgres_row_md_line(row: dict[str, Any]) -> Any:
    source = _postgres_row_source(row)
    for value in (
        row.get("source_markdown_line"),
        row.get("markdown_line"),
        row.get("md_line"),
        source.get("markdown_line"),
        source.get("line"),
        source.get("md_line"),
    ):
        if value not in (None, ""):
            return value
    return None


def _markdown_table_cell(value: Any) -> str:
    text = str(value if value not in (None, "") else "未返回")
    return text.replace("\n", " ").replace("|", "\\|").strip()


def _safe_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _nearest_report_pdf_page(lines: list[str], line_number: int | None) -> int | None:
    if not line_number:
        return None
    index = max(0, min(len(lines), line_number) - 1)
    for line in reversed(lines[: index + 1]):
        match = re.search(r"\[PDF_PAGE:\s*(\d+)\]", line)
        if match:
            return int(match.group(1))
    return None


def _html_to_text(value: str) -> str:
    text = re.sub(r"<\s*/\s*(?:td|th)\s*>", " | ", value or "", flags=re.IGNORECASE)
    text = re.sub(r"<\s*/\s*tr\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"[ \t]+", " ", text)).strip()


def _normalize_search_text(value: Any) -> str:
    return re.sub(r"[\s（）()_\-：:、,，;；/]+", "", str(value or "").lower())


def _specific_fulltext_terms(terms: list[str], generic_terms: set[str] | tuple[str, ...] = ()) -> list[str]:
    specific: list[str] = []
    normalized_generic = {_normalize_search_text(generic) for generic in generic_terms}
    for term in terms:
        normalized = _normalize_search_text(term)
        if not normalized:
            continue
        if normalized in normalized_generic:
            continue
        specific.append(term)
    return specific


def _line_match_score(line: str, terms: list[str]) -> int:
    normalized_line = _normalize_search_text(line)
    score = 0
    for term in terms:
        normalized_term = _normalize_search_text(term)
        if not normalized_term:
            continue
        if normalized_term in normalized_line:
            score += 20 + min(len(normalized_term), 20)
    if "<table" in line.lower():
        score += 8
    if re.search(r"\d", line):
        score += 3
    return score


def _line_matches_any_term(line: str, terms: list[str]) -> bool:
    normalized_line = _normalize_search_text(line)
    return any(_normalize_search_text(term) in normalized_line for term in terms if _normalize_search_text(term))


def _snippet_window(lines: list[str], line_number: int, *, radius: int = 2, snippet_chars: int = 900) -> str:
    start = max(1, line_number - radius)
    end = min(len(lines), line_number + radius)
    snippet = "\n".join(lines[start - 1:end])
    snippet = _html_to_text(snippet)
    if len(snippet) > snippet_chars:
        snippet = snippet[:snippet_chars].rstrip() + "..."
    return snippet


def _nearest_table_meta(tables: list[dict[str, Any]], line_number: int | None, *, max_distance: int = 3) -> dict[str, Any] | None:
    if not line_number:
        return None
    candidates: list[tuple[int, dict[str, Any]]] = []
    for table in tables:
        line = _safe_int(table.get("line") or table.get("md_line") or table.get("markdown_line"))
        if line is None:
            continue
        distance = abs(line - line_number)
        if distance <= max_distance:
            candidates.append((distance, table))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], _safe_int(item[1].get("table_index")) or 10**9))
    return candidates[0][1]


__all__ = [
    "_html_to_text",
    "_line_match_score",
    "_line_matches_any_term",
    "_markdown_table_cell",
    "_nearest_report_pdf_page",
    "_nearest_table_meta",
    "_normalize_search_text",
    "_postgres_row_md_line",
    "_postgres_row_metric_name",
    "_postgres_row_payload",
    "_postgres_row_pdf_page",
    "_postgres_row_source",
    "_postgres_row_table_index",
    "_postgres_row_unit",
    "_postgres_row_value",
    "_snippet_window",
    "_specific_fulltext_terms",
]
