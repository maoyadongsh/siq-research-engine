"""Financial runtime display formatting helpers."""

from __future__ import annotations

import re
from typing import Any


def _parse_number(value: Any) -> float | None:
    text = str(value or "").replace(",", "").replace("€", "").replace("%", "").strip()
    if text in {"", "-", "未返回", "N/A", "None", "null"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _row_numeric_values(row: list[str] | None) -> list[float]:
    values: list[float] = []
    for cell in (row or [])[1:]:
        text = str(cell or "").strip()
        if not text or re.fullmatch(r"\[\d+\]", text):
            continue
        value = _parse_number(text)
        if value is not None:
            values.append(value)
    return values


def _fmt_number(value: Any, digits: int = 1) -> str:
    if value is None:
        return "未返回"
    if isinstance(value, (int, float)):
        if float(value).is_integer():
            return f"{int(value):,}"
        return f"{value:,.{digits}f}"
    return str(value)


def _calculator_per_capita_display(payload: dict[str, Any] | None, *, preferred: str = "cny_10k") -> str:
    if not payload or payload.get("status") != "ok":
        return "未返回"
    result = payload.get("result") or {}
    if preferred == "native_10k":
        value = result.get("native_10k_per")
        unit = result.get("native_10k_per_unit")
    elif preferred == "native_per":
        value = result.get("native_per")
        unit = result.get("native_per_unit")
    else:
        value = result.get("cny_10k_per")
        unit = result.get("cny_10k_per_unit")
    if value in (None, "") or not unit:
        return "未返回"
    try:
        digits = 4 if preferred in {"native_10k", "cny_10k"} else 2
        formatted = float(value)
        return f"{formatted:,.{digits}f}{unit}"
    except (TypeError, ValueError):
        return f"{value}{unit}"


def _calculator_formula_text(payload: dict[str, Any] | None) -> str:
    formula = (payload or {}).get("formula") or []
    if not formula:
        return "未返回"
    return "；".join(str(item) for item in formula)


def _statement_row_table(row: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "pdf_page_number": (row or {}).get("pdf_page"),
        "table_index": (row or {}).get("table_index"),
        "line": (row or {}).get("md_line"),
    }


def _table_trace(
    index: int,
    *,
    source_type: str,
    file: str,
    metric: str,
    report_id: str,
    task_id: Any,
    table: dict[str, Any],
    links: str = "",
) -> str:
    pdf_page = table.get("pdf_page_number") or table.get("pdf_page") or "未返回"
    table_index = table.get("table_index") if table.get("table_index") not in (None, "") else "未返回"
    md_line = table.get("line") or table.get("md_line") or table.get("markdown_line") or "未返回"
    return (
        f"[H{index}] source_type={source_type}, file={file}, metric={metric}, period={report_id}, "
        f"task_id={task_id or '未返回'}, pdf_page={pdf_page}, table_index={table_index}, md_line={md_line}"
        + (f"，{links}" if links else "")
    )


__all__ = [
    "_calculator_formula_text",
    "_calculator_per_capita_display",
    "_fmt_number",
    "_parse_number",
    "_row_numeric_values",
    "_statement_row_table",
    "_table_trace",
]
