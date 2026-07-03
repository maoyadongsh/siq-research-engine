from __future__ import annotations

from typing import Any


def _count_value(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def market_ingestion_eval_report_payload(
    *,
    report: Any,
    report_path: str,
    markdown_path: str,
    markdown: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": bool(report),
        "report_path": report_path,
        "markdown_path": markdown_path,
        "report": report,
    }
    if markdown is not None:
        result["markdown"] = markdown
    return result


def market_package_quality_payload(
    *,
    package_path: str,
    manifest: Any,
    quality: Any,
    financial_checks: Any,
    source_map: Any | None = None,
    include_source_map_summary: bool = False,
) -> dict[str, Any]:
    payload = {
        "ok": True,
        "package_path": package_path,
        "manifest": manifest,
        "quality": quality,
        "financial_checks": financial_checks,
    }
    if include_source_map_summary:
        source_entries = source_map.get("entries") if isinstance(source_map, dict) else []
        if not isinstance(source_entries, list):
            source_entries = []
        payload["source_map_summary"] = {"evidence": len(source_entries)}
    return payload


def latest_case_item_for_ticker(case_set: Any, ticker: str) -> dict[str, Any] | None:
    normalized_ticker = str(ticker or "").strip().upper()
    if not normalized_ticker:
        return None
    items = case_set.get("items") if isinstance(case_set, dict) else []
    if not isinstance(items, list):
        return None
    candidates = [
        item for item in items
        if isinstance(item, dict) and str(item.get("ticker") or "").upper() == normalized_ticker
    ]
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda item: (str(item.get("filing_date") or ""), str(item.get("period_end") or "")),
        reverse=True,
    )[0]


def us_sec_case_set_status_payload(
    *,
    case_set: Any,
    ingest_report: Any,
    case_set_path: str,
    ingest_report_path: str,
) -> dict[str, Any]:
    items = case_set.get("items") if isinstance(case_set, dict) else []
    if not isinstance(items, list):
        items = []

    quality: dict[str, int] = {}
    total_counts = {
        "xbrl_fact_count": 0,
        "normalized_metric_count": 0,
        "section_count": 0,
        "table_count": 0,
    }
    by_ticker = []
    for item in items:
        if not isinstance(item, dict):
            continue
        status = str(item.get("quality_status") or "unknown")
        quality[status] = quality.get(status, 0) + 1
        summary = item.get("quality_summary") if isinstance(item.get("quality_summary"), dict) else {}
        total_counts["xbrl_fact_count"] += _count_value(summary.get("xbrl_fact_count"))
        total_counts["normalized_metric_count"] += _count_value(summary.get("normalized_metric_count"))
        total_counts["section_count"] += _count_value(summary.get("section_count"))
        total_counts["table_count"] += _count_value(summary.get("table_count"))
        by_ticker.append({
            "ticker": item.get("ticker"),
            "company_name": item.get("company_name"),
            "fiscal_year": item.get("fiscal_year"),
            "period_end": item.get("period_end"),
            "filing_date": item.get("filing_date"),
            "quality_status": status,
            "quality_summary": summary,
            "package_path": item.get("package_path"),
        })

    relationship = {}
    if isinstance(ingest_report, dict):
        relationship = {
            "generated_at": ingest_report.get("generated_at"),
            "summary": ingest_report.get("summary") or {},
            "package_count": ingest_report.get("package_count"),
            "collection": ingest_report.get("collection"),
            "batch_tag": ingest_report.get("batch_tag"),
        }

    return {
        "case_set_path": case_set_path,
        "ingest_report_path": ingest_report_path,
        "company_count": len(by_ticker),
        "quality": quality,
        "counts": total_counts,
        "items": by_ticker,
        "ingest_report": relationship,
    }
