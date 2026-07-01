"""Financial runtime display formatting helpers."""

from __future__ import annotations

from typing import Any


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


__all__ = [
    "_calculator_formula_text",
    "_calculator_per_capita_display",
    "_fmt_number",
    "_statement_row_table",
]
