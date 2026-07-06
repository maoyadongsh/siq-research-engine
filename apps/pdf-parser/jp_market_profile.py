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

JP_FORMAL_CORE_FINANCIAL_TABLE_NAMES = [
    "Financial Highlights",
    "Consolidated Statement of Financial Position",
    "Consolidated Statement of Profit or Loss",
    "Consolidated Statement of Comprehensive Income",
    "Consolidated Statement of Cash Flows",
    "Consolidated Statement of Changes in Equity",
]

JP_SUMMARY_CORE_FINANCIAL_TABLE_NAMES = [
    "Financial Highlights",
    "Consolidated Statement of Financial Position",
    "Consolidated Statement of Profit or Loss",
    "Consolidated Statement of Cash Flows",
]

JP_CORE_FINANCIAL_TABLE_NAMES = JP_FORMAL_CORE_FINANCIAL_TABLE_NAMES

JP_INDICATOR_TABLE_NAMES = [
    "Segment Information",
    "Revenue",
    "Operating Profit",
    "Profit Attributable to Owners of Parent",
    "Total Assets",
    "Basic EPS",
]

JP_KEY_TABLE_DISPLAY_ORDER = JP_CORE_FINANCIAL_TABLE_NAMES + JP_INDICATOR_TABLE_NAMES
JP_PROFILE_RULE_VERSION = "jp-pdf-profile-v6"

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
    "主要な連結経営指標等の推移",
    "主要な経営指標等の推移",
    "連結経営指標等",
    "提出会社の経営指標等",
    "経営指標等の推移",
)

_CANDIDATE_RULES: dict[str, tuple[tuple[str, ...], str]] = {
    "Financial Highlights": (
        (
            "financial highlights",
            "selected financial data",
            "five-year summary",
            "consolidated operating results",
            "operating results",
            "主要な連結経営指標等の推移",
            "主要な経営指標等の推移",
            "連結経営指標等",
            "提出会社の経営指標等",
            "経営指標等の推移",
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
            "連結資本変動計算書",
            "連結株主資本等変動計算書",
            "株主資本等変動計算書",
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
    head_source = _normalize_text(chr(10).join([str(filename or ""), str(text or "")[:30000]]))
    head_compact = _compact_text(head_source)
    has_integrated_signal = any(term in source or _compact_text(term) in compact for term in _INTEGRATED_REPORT_TERMS)
    has_integrated_title = "integrated report" in head_source or "integratedreport" in head_compact
    strong_annual_terms = _ANNUAL_SECURITIES_TERMS[1:]
    if any(term in source or _compact_text(term) in compact for term in strong_annual_terms):
        return "jp_annual_securities_report"
    if has_integrated_signal and has_integrated_title:
        return "jp_integrated_report"
    if "annual securities report" in source or "annualsecuritiesreport" in compact:
        return "jp_annual_securities_report"
    if has_integrated_signal:
        return "jp_integrated_report"
    if any(term in source or _compact_text(term) in compact for term in _FINANCIAL_HIGHLIGHTS_TERMS):
        return "jp_financial_highlights_only"
    return "jp_pdf_report"


def core_financial_table_names_for_report(report_kind: str | None) -> list[str]:
    if str(report_kind or "") in {"jp_integrated_report", "jp_financial_highlights_only"}:
        return list(JP_SUMMARY_CORE_FINANCIAL_TABLE_NAMES)
    return list(JP_FORMAL_CORE_FINANCIAL_TABLE_NAMES)


def jp_candidate_group(name: str) -> str:
    if name in JP_CORE_FINANCIAL_TABLE_NAMES:
        return "core"
    return "indicator"


def _table_signal_text(item: dict[str, Any]) -> str:
    parts = [
        item.get("heading"),
        item.get("caption"),
        item.get("source_caption"),
        item.get("preview"),
        item.get("signal_preview"),
        item.get("text_preview"),
        item.get("near_text"),
        item.get("unit"),
        item.get("source_footnote"),
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
    year_hits = len(set(re.findall(r"(?:20\d{2}|fy\d{2,4}|\d{4}\.3|\d{4}/3)", signal)))
    has_revenue_signal = (
        "revenue" in tokens
        or "revenues" in tokens
        or {"net", "sales"}.issubset(tokens)
        or {"operating", "revenue"}.issubset(tokens)
    )
    has_operating_profit_signal = {"operating", "profit"}.issubset(tokens) or {"operating", "income"}.issubset(tokens)
    has_profit_attributable_signal = (
        "profit attributable" in signal
        or "profits attributable" in signal
        or "income attributable" in signal
    )
    has_total_assets_signal = {"total", "assets"}.issubset(tokens)
    has_total_equity_signal = (
        {"total", "equity"}.issubset(tokens)
        or {"net", "assets"}.issubset(tokens)
        or "equity attributable" in signal
        or {"owners", "equity"}.issubset(tokens)
        or {"shareholders", "equity"}.issubset(tokens)
        or {"shareholder", "equity"}.issubset(tokens)
    )
    bad_summary_terms = (
        "non-financial data",
        "financial instruments",
        "cash flow allocation",
        "cash flow-related indicators",
        "financial kpis",
        "impact-weighted accounting",
    )
    if name == "Financial Highlights":
        has_bad_summary = any(term in signal for term in bad_summary_terms)
        compact_signal = _compact_text(signal)
        has_jp_highlights_title = any(
            term in compact_signal
            for term in (
                "主要な連結経営指標等の推移",
                "主要な経営指標等の推移",
                "連結経営指標等",
                "提出会社の経営指標等",
                "経営指標等の推移",
            )
        )
        has_jp_period_axis = any(term in compact_signal for term in ("回次", "決算年月", "決算期", "事業年度"))
        jp_metric_hits = sum(
            1
            for term in (
                "営業収益",
                "売上高",
                "売上収益",
                "経常利益",
                "営業利益",
                "税引前利益",
                "当期純利益",
                "親会社株主に帰属する当期純利益",
                "親会社の所有者に帰属する当期利益",
                "総資産額",
                "純資産額",
                "1株当たり",
            )
            if term in compact_signal
        )
        has_summary_title = any(
            term in signal
            for term in (
                "financial data",
                "financial summary",
                "selected financial data",
                "key consolidated financial data",
                "consolidated eleven-year summary",
                "eleven-year key consolidated financial data",
                "11-year summary",
                "5-year financial data",
                "financial results",
                "consolidated operating results",
                "operating results (for the year)",
            )
        )
        has_metric_cluster = (
            has_revenue_signal
            and has_operating_profit_signal
            and (
                {"net", "income"}.issubset(tokens)
                or {"net", "profit"}.issubset(tokens)
                or has_profit_attributable_signal
                or "eps" in tokens
                or {"per", "share"}.issubset(tokens)
                or has_total_assets_signal
                or {"cash", "flow"}.issubset(tokens)
                or {"cash", "flows"}.issubset(tokens)
            )
        )
        has_bank_result_cluster = (
            ({"gross", "profits"}.issubset(tokens) or {"gross", "profit"}.issubset(tokens))
            and ({"operating", "profits"}.issubset(tokens) or {"operating", "profit"}.issubset(tokens))
            and ({"ordinary", "profits"}.issubset(tokens) or {"ordinary", "profit"}.issubset(tokens))
            and has_profit_attributable_signal
        )
        has_period_summary_label = "for the year" in signal or "at year-end" in signal
        has_income_data_label = "statement of income data" in signal or "income data" in signal
        if not has_bad_summary and has_jp_highlights_title and jp_metric_hits >= 2:
            return 94.0
        if not has_bad_summary and has_jp_period_axis and jp_metric_hits >= 4:
            return 88.0
        if not has_bad_summary and has_income_data_label and has_bank_result_cluster:
            return 90.0
        if not has_bad_summary and has_bank_result_cluster and ("results change" in signal or year_hits >= 2):
            return 86.0
        if not has_bad_summary and has_summary_title and has_metric_cluster:
            return 90.0
        if not has_bad_summary and has_metric_cluster and year_hits >= 4:
            return 86.0
        if not has_bad_summary and has_metric_cluster and has_period_summary_label:
            return 84.0
        if not has_bad_summary and has_metric_cluster and ("per common share" in signal or {"amounts", "share"}.issubset(tokens)):
            return 84.0
    if name == "Consolidated Statement of Financial Position":
        if {"assets", "liabilities"}.issubset(tokens) and ("equity" in tokens or "net" in tokens):
            return 86.0
        if {"assets", "current"}.issubset(tokens) and {"total", "assets"}.issubset(tokens) and "liabilities" in tokens:
            return 84.0
        has_financial_position_label = (
            "financial position" in signal
            or "balance sheet data" in signal
            or "balance sheet" in signal
            or "at year-end" in signal
            or "financial indicators" in signal
            or "financial and non-financial data" in signal
            or "selected financial data" in signal
            or "financial data" in signal
        )
        if has_financial_position_label and has_total_assets_signal and has_total_equity_signal:
            return 86.0 if "financial position" in signal or "balance sheet" in signal else 84.0
    if name == "Consolidated Statement of Profit or Loss":
        if has_revenue_signal and ("income" in tokens or "profit" in tokens):
            return 86.0
        if has_revenue_signal and {"cost", "gross"}.intersection(tokens) and {"operating", "income"}.issubset(tokens):
            return 84.0
    if name == "Consolidated Statement of Comprehensive Income":
        if {"other", "comprehensive", "income"}.issubset(tokens) or {"total", "comprehensive", "income"}.issubset(tokens):
            return 86.0
    if name == "Consolidated Statement of Cash Flows":
        if "cash" in tokens and ("flows" in tokens or "flow" in tokens) and "operating" in tokens:
            return 86.0
        if {"operating", "activities"}.issubset(tokens) and (
            "depreciation" in tokens
            or {"income", "taxes"}.issubset(tokens)
            or {"investing", "activities"}.issubset(tokens)
            or {"financing", "activities"}.issubset(tokens)
        ):
            return 84.0
    if name == "Consolidated Statement of Changes in Equity":
        compact_signal = _compact_text(signal)
        jp_equity_components = sum(
            1
            for term in (
                "資本金",
                "資本剰余金",
                "資本剩余金",
                "利益剰余金",
                "利益剩余金",
                "自己株式",
                "その他の資本の構成要素",
                "その他の包括利益累計額",
                "非支配持分",
                "資本合計",
                "純資産合計",
            )
            if term in compact_signal
        )
        has_jp_equity_scope = any(term in compact_signal for term in ("親会社の所有者に帰属する持分", "株主資本", "資本合計"))
        has_jp_movement_axis = any(term in compact_signal for term in ("当期首残高", "当期変動額", "当期包括利益", "包括利益合計"))
        if has_jp_equity_scope and has_jp_movement_axis and jp_equity_components >= 4:
            return 88.0
        if "equity" in tokens and ({"share", "capital"}.issubset(tokens) or "retained" in tokens):
            return 84.0
        if {"common", "stock", "retained", "earnings", "treasury", "stock"}.issubset(tokens):
            return 84.0
        if {"capital", "surplus", "retained", "earnings"}.issubset(tokens) and {"comprehensive", "income"}.issubset(tokens):
            return 82.0
    return 0.0


def _year_hit_count(signal: str) -> int:
    return len(set(re.findall(r"(?:20\d{2}|fy\d{2,4}|\d{4}\.3|\d{4}/3)", signal)))


def _looks_like_metric_summary_table(signal: str) -> bool:
    tokens = set(re.findall(r"[a-z]+", signal))
    year_hits = _year_hit_count(signal)
    has_revenue = (
        "revenue" in tokens
        or "revenues" in tokens
        or {"net", "sales"}.issubset(tokens)
        or {"operating", "revenue"}.issubset(tokens)
    )
    has_profit = (
        {"operating", "profit"}.issubset(tokens)
        or {"operating", "income"}.issubset(tokens)
        or "profit attributable" in signal
        or "income attributable" in signal
    )
    has_position = (
        {"total", "assets"}.issubset(tokens)
        or {"total", "equity"}.issubset(tokens)
        or {"net", "assets"}.issubset(tokens)
    )
    has_cash_flow_metric = (
        "cash flows from operating activities" in signal
        or "net cash provided by operating activities" in signal
        or "free cash flow" in signal
    )
    summary_label = any(
        term in signal
        for term in (
            "financial highlights",
            "selected financial data",
            "financial data",
            "financial summary",
            "financial results",
            "for the fiscal year",
            "for the year:",
            "at year-end",
            "ten-year",
            "eleven-year",
            "10-year",
            "11-year",
            "past 10 years",
            "target actual",
            "financial targets",
        )
    )
    period_summary_label = "for the fiscal year" in signal or "for the year" in signal
    metric_cluster = has_revenue and has_profit and (has_position or has_cash_flow_metric or year_hits >= 4 or period_summary_label)
    return metric_cluster and (summary_label or year_hits >= 4)


def _looks_like_strategy_or_capital_table(signal: str) -> bool:
    strategy_terms = (
        "six capitals",
        "financial capital",
        "human capital",
        "intellectual capital",
        "social capital",
        "natural capital",
        "key monitoring indicators",
        "goals and indicators",
        "value creation",
        "materiality",
        "management strategy",
        "financial strategy",
        "capital input",
        "financial targets",
        "target actual",
    )
    return any(term in signal for term in strategy_terms)


def _has_explicit_statement_title(name: str, signal: str) -> bool:
    title_terms: dict[str, tuple[str, ...]] = {
        "Consolidated Statement of Financial Position": (
            "consolidated statement of financial position",
            "consolidated balance sheet",
            "statement of financial position",
            "連結財政状態計算書",
            "連結貸借対照表",
        ),
        "Consolidated Statement of Profit or Loss": (
            "consolidated statement of profit or loss",
            "consolidated statement of income",
            "consolidated statements of income",
            "statement of profit or loss",
            "statement of income",
            "income statement",
            "連結損益計算書",
        ),
        "Consolidated Statement of Comprehensive Income": (
            "consolidated statement of comprehensive income",
            "consolidated statements of comprehensive income",
            "statement of comprehensive income",
            "連結包括利益計算書",
        ),
        "Consolidated Statement of Cash Flows": (
            "consolidated statement of cash flows",
            "consolidated statements of cash flows",
            "statement of cash flows",
            "連結キャッシュ・フロー計算書",
        ),
        "Consolidated Statement of Changes in Equity": (
            "consolidated statement of changes in equity",
            "consolidated statements of changes in equity",
            "statement of changes in equity",
            "changes in net assets",
            "連結持分変動計算書",
            "連結株主資本等変動計算書",
        ),
    }
    return any(term in signal for term in title_terms.get(name, ()))


def _suppress_core_statement_candidate(name: str, signal: str, *, allow_summary_core: bool = False) -> bool:
    if name == "Financial Highlights" or name not in JP_CORE_FINANCIAL_TABLE_NAMES:
        return False
    if _has_explicit_statement_title(name, signal):
        return False
    if allow_summary_core and name in {
        "Consolidated Statement of Financial Position",
        "Consolidated Statement of Profit or Loss",
        "Consolidated Statement of Cash Flows",
    }:
        return False
    if name == "Consolidated Statement of Financial Position" and any(
        term in signal for term in ("at year-end", "balance sheet data", "financial indicators")
    ):
        return True
    if name == "Consolidated Statement of Profit or Loss" and _year_hit_count(signal) >= 4 and (
        "revenue" in signal or "revenues" in signal or "net sales" in signal
    ):
        return True
    if name == "Consolidated Statement of Comprehensive Income":
        if "shareholder return" in signal or "dividend on equity" in signal or "treasury stock acquisition" in signal:
            return True
        if "accumulated other comprehensive income" in signal and "total comprehensive income" not in signal:
            return True
    if name == "Consolidated Statement of Cash Flows" and _year_hit_count(signal) >= 4:
        return True
    if _looks_like_metric_summary_table(signal):
        return True
    if _looks_like_strategy_or_capital_table(signal):
        return True
    return False


def group_jp_key_table_candidates(table_index: list[dict[str, Any]] | None, *, report_kind: str | None = None) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    allow_summary_core = str(report_kind or "") in {"jp_integrated_report", "jp_financial_highlights_only"}
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
            if _suppress_core_statement_candidate(name, signal, allow_summary_core=allow_summary_core):
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
        "Financial Highlights": _FINANCIAL_HIGHLIGHTS_TERMS,
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
