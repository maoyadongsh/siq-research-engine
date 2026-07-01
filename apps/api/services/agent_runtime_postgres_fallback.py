"""Pure PostgreSQL fallback parse and predicate helpers for the agent runtime."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any


def postgres_query_text(
    message: str,
    context: Any | None = None,
    *,
    context_company_hint: Callable[[Any | None], str],
) -> str:
    hint = context_company_hint(context)
    if not hint:
        return message
    return f"{message}\n\n当前页面公司提示：{hint}"


def postgres_prepare_parsed(parsed: dict[str, Any], message: str) -> dict[str, Any]:
    output = dict(parsed)
    if output.get("metric_name") or output.get("canonical_name") or output.get("statement_type"):
        return output
    text = re.sub(r"\s+", "", message or "")
    if any(term in text for term in ("财务", "业绩", "表现", "基本面", "经营情况", "主要数据", "核心数据", "数据", "情况")):
        output["query_type"] = "company_all"
    return output


def postgres_requested_metric_terms(
    message: str,
    *,
    financial_note_metric_terms: tuple[str, ...],
    core_key_metric_terms: tuple[str, ...],
    core_key_metric_aliases: dict[str, tuple[str, ...]],
) -> list[str]:
    text = re.sub(r"\s+", "", message or "").lower()
    if not text:
        return []
    terms: list[str] = []
    for term in (*financial_note_metric_terms, *core_key_metric_terms):
        if str(term).lower() in text:
            terms.append(str(term))
    for aliases in core_key_metric_aliases.values():
        if any(str(alias).lower() in text for alias in aliases):
            terms.extend(str(alias) for alias in aliases)
    return sorted(dict.fromkeys(term for term in terms if term), key=len, reverse=True)


def postgres_row_matches_requested_terms(
    row: dict[str, Any],
    requested_terms: list[str],
    *,
    normalize_financial_text: Callable[[Any], str],
    postgres_row_payload: Callable[[dict[str, Any]], dict[str, Any]],
) -> bool:
    if not requested_terms:
        return True
    payload = postgres_row_payload(row)
    row_text = normalize_financial_text(
        " ".join(
            str(value)
            for value in (
                row.get("item_name"),
                row.get("metric_name"),
                row.get("metric_key"),
                row.get("canonical_name"),
                payload.get("item_name"),
                payload.get("metric_name"),
                payload.get("metric_key"),
                payload.get("canonical_name"),
            )
            if value not in (None, "")
        )
    )
    return any(normalize_financial_text(term) in row_text for term in requested_terms)


__all__ = [
    "postgres_prepare_parsed",
    "postgres_query_text",
    "postgres_requested_metric_terms",
    "postgres_row_matches_requested_terms",
]
