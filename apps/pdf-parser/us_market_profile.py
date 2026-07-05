"""US SEC/PDF market profile helpers for quality and financial fallback views."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Iterable

US_PROFILE_RULE_VERSION = "us-pdf-profile-v1"
US_DEFAULT_ACCOUNTING_STANDARD = "US GAAP / SEC"

US_KEY_SECTIONS = [
    "Business",
    "Risk Factors",
    "Management's Discussion and Analysis",
    "Controls and Procedures",
    "Financial Statements",
    "Notes to Consolidated Financial Statements",
    "Report of Independent Registered Public Accounting Firm",
]

US_CORE_FINANCIAL_TABLE_NAMES = [
    "Selected Financial Data",
    "Consolidated Statements of Operations",
    "Consolidated Statements of Comprehensive Income",
    "Consolidated Balance Sheets",
    "Consolidated Statements of Cash Flows",
    "Consolidated Statements of Stockholders' Equity",
]

US_INDICATOR_TABLE_NAMES = [
    "Revenue",
    "Operating Income",
    "Net Income",
    "Diluted EPS",
    "Segment Information",
    "Liquidity and Capital Resources",
]

US_KEY_TABLE_DISPLAY_ORDER = US_CORE_FINANCIAL_TABLE_NAMES + US_INDICATOR_TABLE_NAMES

_CANDIDATE_RULES: dict[str, tuple[tuple[str, ...], str]] = {
    "Selected Financial Data": (
        (
            "selected financial data",
            "selected consolidated financial data",
            "financial highlights",
            "five year summary",
        ),
        "core",
    ),
    "Consolidated Statements of Operations": (
        (
            "consolidated statements of operations",
            "consolidated statement of operations",
            "consolidated statements of income",
            "consolidated income statements",
            "net sales cost of sales gross profit operating income",
            "revenue cost of revenue operating income net income",
        ),
        "core",
    ),
    "Consolidated Statements of Comprehensive Income": (
        (
            "consolidated statements of comprehensive income",
            "consolidated statement of comprehensive income",
            "other comprehensive income",
            "comprehensive income",
        ),
        "core",
    ),
    "Consolidated Balance Sheets": (
        (
            "consolidated balance sheets",
            "consolidated balance sheet",
            "total assets total liabilities stockholders' equity",
            "assets liabilities and stockholders' equity",
        ),
        "core",
    ),
    "Consolidated Statements of Cash Flows": (
        (
            "consolidated statements of cash flows",
            "consolidated statement of cash flows",
            "cash flows from operating activities",
            "net cash provided by operating activities",
            "cash and cash equivalents at end of period",
        ),
        "core",
    ),
    "Consolidated Statements of Stockholders' Equity": (
        (
            "consolidated statements of stockholders' equity",
            "consolidated statement of stockholders' equity",
            "consolidated statements of shareholders' equity",
            "additional paid-in capital accumulated other comprehensive income retained earnings",
        ),
        "core",
    ),
    "Revenue": (("revenue", "net sales", "total net sales"), "indicator"),
    "Operating Income": (("operating income", "income from operations"), "indicator"),
    "Net Income": (("net income", "net earnings"), "indicator"),
    "Diluted EPS": (("diluted earnings per share", "diluted eps"), "indicator"),
    "Segment Information": (("segment information", "reportable segments"), "indicator"),
    "Liquidity and Capital Resources": (("liquidity and capital resources", "cash requirements"), "indicator"),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_text(value: Any) -> str:
    text = str(value or "").replace("\xa0", " ")
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s+", " ", text).strip().lower()


def _compact_text(value: Any) -> str:
    return re.sub(r"[^0-9a-z]+", "", _normalize_text(value))


def detect_us_report_kind(text: str, filename: str | None = None) -> str:
    source = _normalize_text("\n".join([str(filename or ""), str(text or "")[:120000]]))
    if "form 10-k" in source or "10-k" in source or "10k" in source:
        return "us_10k"
    if "annual report" in source:
        return "us_annual_report"
    if "quarterly report" in source or "form 10-q" in source or "10-q" in source:
        return "us_10q"
    return "us_pdf_report"


def us_candidate_group(name: str) -> str:
    if name in US_CORE_FINANCIAL_TABLE_NAMES:
        return "core"
    return "indicator"


def _table_signal_text(item: dict[str, Any]) -> str:
    parts = [
        item.get("heading"),
        item.get("source_caption"),
        item.get("source_footnote"),
        item.get("caption"),
        item.get("footnote"),
        item.get("preview"),
        item.get("signal_preview"),
        item.get("text_preview"),
        item.get("near_text"),
        item.get("unit"),
    ]
    return _normalize_text(" ".join(str(part or "") for part in parts))


def _score_candidate(signal: str, terms: Iterable[str]) -> float:
    score = 0.0
    compact_signal = _compact_text(signal)
    for term in terms:
        normalized = _normalize_text(term)
        compact = _compact_text(term)
        if normalized and normalized in signal:
            score = max(score, 80.0 + min(len(normalized), 30))
        elif compact and compact in compact_signal:
            score = max(score, 75.0 + min(len(compact), 25))
    return score


def _fallback_rule_score(name: str, signal: str) -> float:
    tokens = set(re.findall(r"[a-z]+", signal))
    compact = _compact_text(signal)
    if name == "Consolidated Statements of Operations":
        if {"revenue", "operating", "income", "net"}.issubset(tokens):
            return 88.0
        if {"net", "sales", "gross", "profit"}.issubset(tokens):
            return 86.0
    if name == "Consolidated Balance Sheets":
        if {"total", "assets", "liabilities", "equity"}.issubset(tokens):
            return 90.0
        if "stockholdersequity" in compact and "totalassets" in compact:
            return 88.0
    if name == "Consolidated Statements of Cash Flows":
        if {"operating", "investing", "financing", "cash"}.issubset(tokens):
            return 90.0
        if "netcashprovidedbyoperatingactivities" in compact:
            return 88.0
    if name == "Consolidated Statements of Stockholders' Equity":
        if "additionalpaidincapital" in compact and "retainedearnings" in compact:
            return 88.0
        if "stockholdersequity" in compact and "accumulatedothercomprehensive" in compact:
            return 86.0
    if name == "Segment Information" and "segment" in tokens and ("revenue" in tokens or "sales" in tokens):
        return 84.0
    return 0.0


def _context_adjusted_score(name: str, item: dict[str, Any], signal: str, score: float) -> float:
    if score <= 0:
        return 0.0
    heading = _normalize_text(item.get("heading"))
    compact = _compact_text(signal)
    if name in US_CORE_FINANCIAL_TABLE_NAMES:
        if any(marker in compact for marker in ("tableofcontents", "item8financialstatements", "notesindex")):
            score -= 30.0
        if item.get("rows") and int(item.get("rows") or 0) >= 10:
            score += 8.0
        if item.get("numeric_ratio") and float(item.get("numeric_ratio") or 0) >= 0.3:
            score += 6.0
    rules = _CANDIDATE_RULES.get(name)
    if heading and rules and any(_normalize_text(term) in heading for term in rules[0]):
        score += 18.0
    return max(score, 0.0)


def group_us_key_table_candidates(table_index: list[dict[str, Any]] | None) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in table_index or []:
        if not isinstance(item, dict):
            continue
        signal = _table_signal_text(item)
        if not signal:
            continue
        for name, (terms, group) in _CANDIDATE_RULES.items():
            score = max(_score_candidate(signal, terms), _fallback_rule_score(name, signal))
            score = _context_adjusted_score(name, item, signal, score)
            if score <= 0:
                continue
            row = dict(item)
            row.update(
                {
                    "name": name,
                    "status": "found",
                    "candidate_group": group,
                    "candidate_score": round(score, 2),
                    "confidence": "high" if score >= 90 else "medium",
                    "_source": "us_market_profile",
                }
            )
            grouped.setdefault(name, []).append(row)

    ordered: dict[str, list[dict[str, Any]]] = {}
    for name in US_KEY_TABLE_DISPLAY_ORDER:
        rows = grouped.get(name) or []
        if not rows:
            continue
        rows = sorted(rows, key=lambda row: (-(row.get("candidate_score") or 0), row.get("table_index") or 0))[:5]
        for index, row in enumerate(rows):
            row["is_primary"] = index == 0
        ordered[name] = rows
    return ordered


def candidate_summary_list(key_table_candidates: dict[str, list[dict[str, Any]]], names: list[str]) -> list[dict[str, Any]]:
    summary = []
    for name in names:
        rows = key_table_candidates.get(name) or []
        if not rows:
            summary.append({"name": name, "status": "missing", "candidate_group": us_candidate_group(name)})
            continue
        primary = dict(rows[0])
        primary["name"] = name
        primary["status"] = "found"
        primary["candidate_count"] = len(rows)
        summary.append(primary)
    return summary


def found_sections(markdown: str, table_index: list[dict[str, Any]] | None = None) -> list[str]:
    source = _normalize_text(markdown)
    table_text = _normalize_text("\n".join(_table_signal_text(item) for item in table_index or [] if isinstance(item, dict)))
    source = "\n".join([source, table_text])
    compact = _compact_text(source)
    terms = {
        "Business": ("item 1. business", "business"),
        "Risk Factors": ("item 1a. risk factors", "risk factors"),
        "Management's Discussion and Analysis": ("management's discussion and analysis", "item 7."),
        "Controls and Procedures": ("controls and procedures", "item 9a."),
        "Financial Statements": ("financial statements", "item 8."),
        "Notes to Consolidated Financial Statements": ("notes to consolidated financial statements",),
        "Report of Independent Registered Public Accounting Firm": (
            "report of independent registered public accounting firm",
            "independent registered public accounting firm",
        ),
    }
    found = []
    for name in US_KEY_SECTIONS:
        if any(_normalize_text(term) in source or _compact_text(term) in compact for term in terms[name]):
            found.append(name)
    return found


def us_quality_report_messages(
    *,
    report_kind: str,
    table_count: int,
    single_row_table_count: int,
    image_ref_count: int,
    found_core_table_count: int,
    suspicious_table_count: int,
) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    info_messages: list[str] = []
    if table_count and single_row_table_count / table_count > 0.2:
        warnings.append("US PDF single-row/shell table ratio is high; review SEC Item 8 table segmentation.")
    if image_ref_count:
        info_messages.append("Markdown includes image references; use images as visual evidence for US PDF source review.")
    if report_kind in {"us_10k", "us_annual_report"} and found_core_table_count < 3:
        warnings.append("US annual report core statement candidates are sparse; review Item 8 / financial statements pages.")
    if suspicious_table_count:
        warnings.append(f"{suspicious_table_count} suspicious table samples require visual source review.")
    return warnings, info_messages


def _statement_by(data: dict[str, Any], statement_type: str) -> dict[str, Any] | None:
    for statement in data.get("statements") or []:
        if statement.get("statement_type") == statement_type:
            return statement
    return None


def _presence_check(statement_type: str, statement: dict[str, Any] | None) -> dict[str, Any]:
    names = {
        "balance_sheet": "Required US statement present: consolidated balance sheets",
        "income_statement": "Required US statement present: consolidated statements of operations",
        "cash_flow_statement": "Required US statement present: consolidated statements of cash flows",
    }
    return {
        "rule_id": f"us.presence.{statement_type}",
        "rule_name": names.get(statement_type, f"Required US statement present: {statement_type}"),
        "statement_type": statement_type,
        "scope": "consolidated",
        "period": "",
        "status": "pass" if statement else "fail",
        "reason": "statement_found" if statement else "statement_missing",
        "inputs": [],
    }


def build_us_financial_checks(data: dict[str, Any]) -> dict[str, Any]:
    from financial_extractor import FINANCIAL_CHECKS_SCHEMA_VERSION, FINANCIAL_RULE_VERSION

    if not isinstance(data, dict):
        data = {}
    warnings = [
        item
        for item in list(data.get("warnings") or [])
        if "合并资产负债表" not in str(item) and "合并利润表" not in str(item) and "合并现金流量表" not in str(item)
    ]
    checks = [
        _presence_check("balance_sheet", _statement_by(data, "balance_sheet")),
        _presence_check("income_statement", _statement_by(data, "income_statement")),
        _presence_check("cash_flow_statement", _statement_by(data, "cash_flow_statement")),
    ]
    if not data.get("statements"):
        warnings.append("US PDF fallback did not confirm structured financial statements; SEC HTML/iXBRL remains the preferred US ingestion path.")
    if any(item.get("status") == "fail" for item in checks):
        warnings.append("US statement coverage is incomplete; review Item 8 tables or use the SEC HTML/iXBRL workflow.")

    counts = {"pass": 0, "fail": 0, "warning": 0, "skipped": 0}
    for item in checks:
        counts[item.get("status", "skipped")] = counts.get(item.get("status", "skipped"), 0) + 1
    overall = "fail" if counts.get("fail") else ("pass" if counts.get("pass") else "skipped")
    return {
        "schema_version": FINANCIAL_CHECKS_SCHEMA_VERSION,
        "rule_version": FINANCIAL_RULE_VERSION,
        "task_id": data.get("task_id"),
        "filename": data.get("filename"),
        "market": "US",
        "market_profile": "US",
        "profile_rule_version": US_PROFILE_RULE_VERSION,
        "accounting_standard": data.get("accounting_standard") or US_DEFAULT_ACCOUNTING_STANDARD,
        "report_kind": data.get("report_kind"),
        "report_year": data.get("report_year"),
        "industry_profile": data.get("industry_profile"),
        "overall_status": overall,
        "summary": {
            "total": len(checks),
            "pass": counts.get("pass", 0),
            "fail": counts.get("fail", 0),
            "warning": counts.get("warning", 0),
            "skipped": counts.get("skipped", 0),
        },
        "checks": checks,
        "warnings": warnings,
        "generated_at": _now_iso(),
    }
