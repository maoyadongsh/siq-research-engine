# Japan market profile helpers for PDF parser quality and financial views.

from __future__ import annotations

import re
from typing import Any, Iterable

JP_KEY_SECTIONS = [
    "Annual Securities Report",
    "Integrated Report",
    "Financial Highlights",
    "Management Strategy",
    "Business Overview",
    "Financial Section",
    "Corporate Governance",
    "Sustainability",
]

JP_CORE_FINANCIAL_TABLE_NAMES = [
    "Financial Highlights",
    "Consolidated Statement of Financial Position",
    "Consolidated Statement of Profit or Loss",
    "Consolidated Statement of Comprehensive Income",
    "Consolidated Statement of Cash Flows",
    "Consolidated Statement of Changes in Equity",
]

JP_INDICATOR_TABLE_NAMES = [
    "Segment Information",
    "Revenue",
    "Operating Profit",
    "Profit Attributable to Owners of Parent",
    "Total Assets",
    "Basic EPS",
]

JP_KEY_TABLE_DISPLAY_ORDER = JP_CORE_FINANCIAL_TABLE_NAMES + JP_INDICATOR_TABLE_NAMES

_ANNUAL_SECURITIES_TERMS = (
    "annual securities report",
    "financial instruments and exchange act",
    "有価証券報告書",
    "第一部【企業情報】",
    "経理の状況",
)

_INTEGRATED_REPORT_TERMS = (
    "integrated report",
    "value creation",
    "materiality",
    "sustainability",
)

_FINANCIAL_HIGHLIGHTS_TERMS = (
    "financial highlights",
    "selected financial data",
    "five-year summary",
    "主要な経営指標等の推移",
)

_CANDIDATE_RULES: dict[str, tuple[tuple[str, ...], str]] = {
    "Financial Highlights": (
        (
            "financial highlights",
            "selected financial data",
            "five-year summary",
            "主要な経営指標等の推移",
        ),
        "core",
    ),
    "Consolidated Statement of Financial Position": (
        (
            "consolidated statement of financial position",
            "consolidated balance sheet",
            "statement of financial position",
            "balance sheet",
            "assets current assets total assets liabilities and net assets",
            "連結財政状態計算書",
            "連結貸借対照表",
        ),
        "core",
    ),
    "Consolidated Statement of Profit or Loss": (
        (
            "consolidated statement of profit or loss",
            "consolidated statement of income",
            "statement of income",
            "profit or loss",
            "net sales costs and expenses operating income",
            "revenue operating profit profit before tax",
            "連結損益計算書",
        ),
        "core",
    ),
    "Consolidated Statement of Comprehensive Income": (
        (
            "statement of comprehensive income",
            "comprehensive income",
            "連結包括利益計算書",
        ),
        "core",
    ),
    "Consolidated Statement of Cash Flows": (
        (
            "consolidated statement of cash flows",
            "cash flows from operating activities",
            "net cash provided by operating activities",
            "連結キャッシュ・フロー計算書",
        ),
        "core",
    ),
    "Consolidated Statement of Changes in Equity": (
        (
            "consolidated statement of changes in equity",
            "changes in equity",
            "changes in net assets",
            "share capital retained earnings",
            "連結持分変動計算書",
            "連結株主資本等変動計算書",
        ),
        "core",
    ),
    "Segment Information": (
        (
            "segment information",
            "business segment",
            "reportable segments",
            "セグメント情報",
        ),
        "indicator",
    ),
    "Revenue": (("revenue", "net sales", "sales revenue", "売上高", "売上収益", "営業収益"), "indicator"),
    "Operating Profit": (("operating profit", "operating income", "営業利益"), "indicator"),
    "Profit Attributable to Owners of Parent": (
        ("profit attributable to owners of parent", "profit attributable to owner of parent", "親会社の所有者に帰属する"),
        "indicator",
    ),
    "Total Assets": (("total assets", "資産合計", "総資産"), "indicator"),
    "Basic EPS": (("basic earnings per share", "basic net income", "basic eps", "1株当たり"), "indicator"),
}


def _normalize_text(value: Any) -> str:
    text = str(value or "").replace("\xa0", " ").replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def _compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").lower())


def is_jp_market(task: dict[str, Any] | None, filename: str | None = None) -> bool:
    task = task or {}
    submit_config = task.get("submit_config") if isinstance(task.get("submit_config"), dict) else {}
    explicit = submit_config.get("market") or task.get("market")
    if explicit:
        return str(explicit).strip().upper() == "JP"
    name = str(filename or task.get("filename") or "")
    lowered = name.lower()
    return bool(re.search(r"(?:^|[_\-])jp(?:[_\-]|$)", lowered) or "edinet" in lowered)


def detect_jp_report_kind(text: str, filename: str | None = None) -> str:
    source = _normalize_text(chr(10).join([str(filename or ""), str(text or "")[:120000]]))
    compact = _compact_text(source)
    if any(term in source or _compact_text(term) in compact for term in _ANNUAL_SECURITIES_TERMS):
        return "jp_annual_securities_report"
    if any(term in source or _compact_text(term) in compact for term in _INTEGRATED_REPORT_TERMS):
        return "jp_integrated_report"
    if any(term in source or _compact_text(term) in compact for term in _FINANCIAL_HIGHLIGHTS_TERMS):
        return "jp_financial_highlights_only"
    return "jp_pdf_report"


def jp_candidate_group(name: str) -> str:
    if name in JP_CORE_FINANCIAL_TABLE_NAMES:
        return "core"
    return "indicator"


def _table_signal_text(item: dict[str, Any]) -> str:
    parts = [
        item.get("heading"),
        item.get("caption"),
        item.get("preview"),
        item.get("text_preview"),
        item.get("near_text"),
        item.get("unit"),
    ]
    return _normalize_text(" ".join(str(part or "") for part in parts))


def _score_candidate(signal: str, terms: Iterable[str]) -> float:
    score = 0.0
    for term in terms:
        normalized = _normalize_text(term)
        compact = _compact_text(term)
        if normalized and normalized in signal:
            score = max(score, 80.0 + min(len(normalized), 30))
        elif compact and compact in _compact_text(signal):
            score = max(score, 75.0 + min(len(compact), 25))
    return score


def _fallback_rule_score(name: str, signal: str) -> float:
    tokens = set(re.findall(r"[a-z]+", signal))
    if name == "Consolidated Statement of Financial Position":
        if {"assets", "liabilities"}.issubset(tokens) and ("equity" in tokens or "net" in tokens):
            return 86.0
    if name == "Consolidated Statement of Profit or Loss":
        if ("sales" in tokens or "revenue" in tokens) and ("income" in tokens or "profit" in tokens):
            return 86.0
    if name == "Consolidated Statement of Cash Flows":
        if "cash" in tokens and ("flows" in tokens or "flow" in tokens) and "operating" in tokens:
            return 86.0
    if name == "Consolidated Statement of Changes in Equity":
        if "equity" in tokens and ({"share", "capital"}.issubset(tokens) or "retained" in tokens):
            return 84.0
    return 0.0


def group_jp_key_table_candidates(table_index: list[dict[str, Any]] | None) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in table_index or []:
        if not isinstance(item, dict):
            continue
        signal = _table_signal_text(item)
        if not signal:
            continue
        for name, (terms, group) in _CANDIDATE_RULES.items():
            score = max(_score_candidate(signal, terms), _fallback_rule_score(name, signal))
            if score <= 0:
                continue
            row = dict(item)
            row.update(
                {
                    "name": name,
                    "status": "found",
                    "candidate_group": group,
                    "candidate_score": score,
                    "confidence": "high" if score >= 90 else "medium",
                    "_source": "jp_market_profile",
                }
            )
            grouped.setdefault(name, []).append(row)

    ordered: dict[str, list[dict[str, Any]]] = {}
    for name in JP_KEY_TABLE_DISPLAY_ORDER:
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
            summary.append({"name": name, "status": "missing", "candidate_group": jp_candidate_group(name)})
            continue
        primary = dict(rows[0])
        primary["name"] = name
        primary["status"] = "found"
        primary["candidate_count"] = len(rows)
        summary.append(primary)
    return summary


def found_sections(markdown: str, table_index: list[dict[str, Any]] | None = None) -> list[str]:
    text = _normalize_text(markdown)
    table_text = _normalize_text(chr(10).join(_table_signal_text(item) for item in table_index or [] if isinstance(item, dict)))
    source = chr(10).join([text, table_text])
    compact_source = _compact_text(source)
    found = []
    section_terms = {
        "Annual Securities Report": ("annual securities report", "有価証券報告書"),
        "Integrated Report": ("integrated report",),
        "Financial Highlights": ("financial highlights", "selected financial data", "主要な経営指標等の推移"),
        "Management Strategy": ("management strategy", "medium-term management", "経営方針"),
        "Business Overview": ("business overview", "事業の状況", "business strategy"),
        "Financial Section": ("financial section", "financial statements", "経理の状況"),
        "Corporate Governance": ("corporate governance", "コーポレート・ガバナンス"),
        "Sustainability": ("sustainability", "materiality", "サステナビリティ"),
    }
    for name in JP_KEY_SECTIONS:
        terms = section_terms[name]
        if any(_normalize_text(term) in source or _compact_text(term) in compact_source for term in terms):
            found.append(name)
    return found


def jp_quality_report_messages(
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
    if table_count:
        single_row_ratio = single_row_table_count / table_count
        if single_row_ratio > 0.2:
            warnings.append("单行/空壳表格比例偏高，建议复核表格识别质量。")
    if image_ref_count:
        info_messages.append("Markdown 包含图片引用，images 目录将作为 PDF 视觉元素与截图证据来源。")
    if report_kind == "jp_integrated_report" and found_core_table_count < 2:
        warnings.append("当前文件更像 JP Integrated Report，可能只包含经营/ESG/财务摘要；完整勾稽请优先使用 Annual Securities Report 或 EDINET/XBRL。")
    elif report_kind == "jp_financial_highlights_only" and found_core_table_count < 1:
        warnings.append("当前 JP 文件仅定位到财务摘要线索，未确认完整财务报表。")
    elif report_kind == "jp_annual_securities_report" and found_core_table_count < 3:
        warnings.append("JP Annual Securities Report 核心财务表候选偏少，建议复核 Financial Information/経理の状況 附近页面。")
    if suspicious_table_count:
        warnings.append(f"发现 {suspicious_table_count} 张可疑表样本，建议逐项打开可视化溯源。")
    return warnings, info_messages


def build_jp_financial_checks(data: dict[str, Any]) -> dict[str, Any]:
    from financial_extractor import FINANCIAL_CHECKS_SCHEMA_VERSION, FINANCIAL_RULE_VERSION, _now_iso

    warnings = list(data.get("warnings") or [])
    if not data.get("statements"):
        report_kind = str(data.get("report_kind") or "jp_pdf_report")
        if report_kind == "jp_integrated_report":
            warnings.append("JP Integrated Report 未确认完整财务三表，已按候选识别模式处理；请使用 Annual Securities Report 或 EDINET/XBRL 做完整勾稽。")
        elif report_kind == "jp_financial_highlights_only":
            warnings.append("JP 财务摘要文件未确认完整财务三表，已跳过完整勾稽。")
        else:
            warnings.append("JP PDF 未确认完整财务三表，已跳过 A 股三大表缺失校验。")
    return {
        "schema_version": FINANCIAL_CHECKS_SCHEMA_VERSION,
        "rule_version": FINANCIAL_RULE_VERSION,
        "task_id": data.get("task_id"),
        "filename": data.get("filename"),
        "market": "JP",
        "report_kind": data.get("report_kind"),
        "report_year": data.get("report_year"),
        "industry_profile": data.get("industry_profile"),
        "overall_status": "skipped",
        "summary": {"total": 0, "pass": 0, "fail": 0, "warning": 0, "skipped": 0},
        "checks": [],
        "warnings": warnings,
        "generated_at": _now_iso(),
    }
