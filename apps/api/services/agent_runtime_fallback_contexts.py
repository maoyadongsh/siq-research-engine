"""Pure fallback context formatting helpers for the Hermes agent runtime."""

from __future__ import annotations

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


__all__ = [
    "_markdown_table_cell",
    "_postgres_row_md_line",
    "_postgres_row_metric_name",
    "_postgres_row_payload",
    "_postgres_row_pdf_page",
    "_postgres_row_source",
    "_postgres_row_table_index",
    "_postgres_row_unit",
    "_postgres_row_value",
]
