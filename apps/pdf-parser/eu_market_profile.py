"""Europe market profile helpers for PDF parser quality and financial views."""

from __future__ import annotations

from datetime import datetime, timezone
from html import unescape
import json
from pathlib import Path
import re
from typing import Any, Iterable

EU_PROFILE_RULE_VERSION = "eu-pdf-profile-v6"
EU_DEFAULT_ACCOUNTING_STANDARD = "IFRS / EU local GAAP"

_CURRENCY_PATTERNS = [
    ("EUR", ("\u20ac", "eur", "euro", "euros")),
    ("GBP", ("\u00a3", "gbp", "sterling", "pounds sterling")),
    ("CHF", ("chf", "swiss franc", "swiss francs")),
    ("SEK", ("sek", "swedish krona", "swedish kronor")),
    ("DKK", ("dkk", "danish krone", "danish kroner")),
    ("NOK", ("nok", "norwegian krone", "norwegian kroner")),
    ("PLN", ("pln", "zloty", "zlotys")),
    ("USD", ("usd", "us$", "u.s.$", "us dollar", "us dollars")),
]

_CURRENCY_SYMBOLS = {
    "EUR": "\u20ac",
    "GBP": "\u00a3",
    "CHF": "CHF",
    "SEK": "SEK",
    "DKK": "DKK",
    "NOK": "NOK",
    "PLN": "PLN",
    "USD": "$",
}

EU_KEY_SECTIONS = [
    "Strategic Report",
    "Business Overview",
    "Key Performance Indicators",
    "Risk Management",
    "Governance",
    "Sustainability",
    "Financial Statements",
    "Auditor's Report",
    "Notes to the Financial Statements",
]

EU_CORE_FINANCIAL_TABLE_NAMES = [
    "Financial Highlights",
    "Consolidated Income Statement",
    "Consolidated Statement of Comprehensive Income",
    "Consolidated Statement of Financial Position",
    "Consolidated Statement of Changes in Equity",
    "Consolidated Statement of Cash Flows",
]

EU_INDICATOR_TABLE_NAMES = [
    "Revenue",
    "Operating Profit",
    "Profit for the Year",
    "Basic EPS",
    "Segment Information",
    "Net Debt",
    "Alternative Performance Measures",
]

EU_KEY_TABLE_DISPLAY_ORDER = EU_CORE_FINANCIAL_TABLE_NAMES + EU_INDICATOR_TABLE_NAMES

_ANNUAL_REPORT_TERMS = (
    "annual report",
    "annual report and accounts",
    "strategic report",
    "directors' report",
    "financial statements",
    "issuer annual report",
)

_ESEF_TERMS = (
    "esef",
    "ixbrl",
    "xhtml",
    "inline xbrl",
    "european single electronic format",
)

_CANDIDATE_RULES: dict[str, tuple[tuple[str, ...], str]] = {
    "Financial Highlights": (
        (
            "financial highlights",
            "group highlights",
            "key figures",
            "key financial figures",
            "principal key figures",
            "group key figures",
            "key data",
            "group results",
            "consolidated results",
            "operating results",
            "results of operations",
            "review of results of operations",
            "financial results",
            "key performance indicators",
            "key financial information",
            "key financial and non-financial information",
            "financial summary",
            "financial performance",
            "our financial performance",
            "key elements of financial performance",
            "strategic financial performance indicators",
            "performance highlights",
            "at a glance",
            "group at a glance",
            "five-year record",
            "five year record",
            "five-year summary",
            "five year summary",
            "five-year overview",
            "five year overview",
            "five-year financial summary",
            "five year financial summary",
            "results, cash flow and other information",
            "selected financial data",
        ),
        "core",
    ),
    "Consolidated Income Statement": (
        (
            "consolidated income statement",
            "consolidated income statements",
            "consolidated statement of operations",
            "consolidated statements of operations",
            "consolidated statement of income",
            "consolidated statements of income",
            "consolidated statement of profit or loss",
            "consolidated statements of profit or loss",
            "consolidated profit and loss account",
            "consolidated profit and loss statement",
            "consolidated statement of earnings",
            "income statement",
            "statement of profit or loss",
            "statement of operations",
            "statements of operations",
            "revenue operating profit profit before tax",
            "profit before tax income tax expense profit for the year",
        ),
        "core",
    ),
    "Consolidated Statement of Comprehensive Income": (
        (
            "consolidated statement of comprehensive income",
            "consolidated statements of comprehensive income",
            "statement of comprehensive income",
            "statements of comprehensive income",
            "other comprehensive income",
            "total comprehensive income",
        ),
        "core",
    ),
    "Consolidated Statement of Financial Position": (
        (
            "consolidated statement of financial position",
            "consolidated statements of financial position",
            "consolidated balance sheet",
            "consolidated balance sheets",
            "statement of financial position",
            "statements of financial position",
            "balance sheet",
            "balance sheets",
            "total assets total liabilities total equity",
            "assets liabilities equity net assets",
        ),
        "core",
    ),
    "Consolidated Statement of Changes in Equity": (
        (
            "consolidated statement of changes in equity",
            "consolidated statements of changes in equity",
            "consolidated statement of shareholders' equity",
            "consolidated statements of shareholders' equity",
            "consolidated statement of shareholders’ equity",
            "consolidated statements of shareholders’ equity",
            "consolidated statement of stockholders' equity",
            "consolidated statements of stockholders' equity",
            "statement of changes in equity",
            "changes in equity",
            "shareholders' equity",
            "shareholders’ equity",
            "ordinary share capital share premium retained earnings",
            "total attributable to equity holders non-controlling interests total equity",
        ),
        "core",
    ),
    "Consolidated Statement of Cash Flows": (
        (
            "consolidated cash flow statement",
            "consolidated cash flow statements",
            "consolidated statement of cash flows",
            "consolidated statements of cash flows",
            "cash flow statement",
            "cash flow statements",
            "statement of cash flows",
            "statements of cash flows",
            "net cash flows from operating activities",
            "cash and cash equivalents at 31 december",
        ),
        "core",
    ),
    "Revenue": (("revenue", "total income"), "indicator"),
    "Operating Profit": (("operating profit", "operating income"), "indicator"),
    "Profit for the Year": (("profit for the year", "profit attributable to equity holders"), "indicator"),
    "Basic EPS": (("basic earnings per share", "basic eps"), "indicator"),
    "Segment Information": (("segment information", "operating segments", "divisional review"), "indicator"),
    "Net Debt": (("net debt", "net debt leverage", "borrowings and lease liabilities"), "indicator"),
    "Alternative Performance Measures": (("alternative performance measures", "adjusted operating profit", "adjusted ebitda"), "indicator"),
}

_CORE_STATEMENT_NAMES = {
    "Consolidated Income Statement",
    "Consolidated Statement of Comprehensive Income",
    "Consolidated Statement of Financial Position",
    "Consolidated Statement of Changes in Equity",
    "Consolidated Statement of Cash Flows",
}

_EU_FULL_REPORT_KINDS = {"eu_annual_report", "eu_esef_annual_report"}

_FINANCIAL_HIGHLIGHT_TITLE_TERMS = (
    "financial highlights",
    "group highlights",
    "key figures",
    "key financial figures",
    "principal key figures",
    "group key figures",
    "key data",
    "group results",
    "consolidated results",
    "operating results",
    "results of operations",
    "review of results of operations",
    "financial results",
    "key performance indicators",
    "financial summary",
    "financial performance",
    "our financial performance",
    "key elements of financial performance",
    "strategic financial performance indicators",
    "performance highlights",
    "at a glance",
    "group at a glance",
    "five-year record",
    "five year record",
    "five-year summary",
    "five year summary",
    "five-year overview",
    "five year overview",
    "five-year financial summary",
    "five year financial summary",
    "key financial and non-financial information",
    "results, cash flow and other information",
    "selected financial data",
)

_FINANCIAL_HIGHLIGHT_METRICS = (
    "revenue",
    "revenues",
    "net sales",
    "sales",
    "turnover",
    "total revenue",
    "total income",
    "net interest income",
    "ebit",
    "ebitda",
    "gross profit",
    "operating profit",
    "operating income",
    "operating result",
    "operating return on sales",
    "net income",
    "net profit",
    "net result",
    "net revenue",
    "net revenue less treasury result",
    "net fee and commission income",
    "commercial net interest income",
    "profit for the year",
    "profit or loss",
    "profit before tax",
    "profit attributable",
    "gross operating income",
    "earnings per share",
    "basic eps",
    "eps",
    "return on equity",
    "return on tangible equity",
    "rote",
    "cet1 ratio",
    "free cash flow",
    "cash flow",
    "net debt",
    "net liquidity",
    "cash conversion rate",
    "organic growth",
    "underlying trading operating profit",
    "recurring ebitda",
    "recurring ebit",
    "dividend",
    "orders",
    "order intake",
    "bookings",
    "insurance revenue",
    "combined ratio",
    "claims expenses",
    "technical result",
    "net financial result",
    "sales revenue",
    "vehicle sales",
    "deliveries to customers",
)

_FINANCIAL_HIGHLIGHT_STRONG_TITLE_TERMS = (
    "financial highlights",
    "key figures",
    "group key figures",
    "key financial figures",
    "consolidated results",
    "group results",
    "key elements of financial performance",
    "financial summary",
    "five-year record",
    "five year record",
    "five-year overview",
    "five year overview",
    "five-year financial summary",
    "five year financial summary",
)

_NON_FINANCIAL_HIGHLIGHT_CONTEXTS = (
    "remuneration",
    "compensation",
    "ceo achievements",
    "sti targets",
    "board of directors",
    "board",
    "governance",
    "sustainability",
    "esrs",
    "taxonomy",
    "climate",
    "emissions",
    "ghg",
    "waste",
    "water",
    "workforce",
    "diversity",
    "human rights",
    "security holdings",
    "shares and non-voting equity securities",
    "employee",
    "suppliers",
    "regional performance",
    "by business segment",
    "by operating segment",
    "by geographical",
    "geographic area",
    "by medicine",
    "by product",
    "key marketed products",
    "zone ",
    "acquisition of equity interests",
    "closing balance sheet summary",
    "share key figures",
    "share price",
    "market indices",
    "ordinary share price",
    "preferred share price",
)

_FINANCIAL_HIGHLIGHT_HARD_BAD_CONTEXTS = (
    "remuneration",
    "compensation",
    "annual bonus",
    "variable compensation",
    "lti ",
    "lti 20",
    "stock awards",
    "performance factor",
    "performance assessment process",
    "directors' remuneration",
    "directors’ remuneration",
    "board of management remuneration",
    "board of directors",
    "board",
    "governance",
    "sustainability",
    "esrs",
    "taxonomy",
    "climate",
    "emissions",
    "ghg",
    "joint ventures",
    "associates and joint ventures",
    "country of incorporation",
    "forecast",
    "outlook",
    "projection",
    "segment results",
    "segment information",
    "reportable segments",
    "by segment",
    "by brand group",
    "by market",
    "share key figures",
    "share price",
    "market indices",
)

_STATEMENT_CONTEXT_TERMS = (
    "consolidated income statement",
    "consolidated income statements",
    "consolidated statement of income",
    "consolidated statements of income",
    "consolidated statement of profit or loss",
    "consolidated statements of profit or loss",
    "consolidated profit and loss account",
    "consolidated profit and loss statement",
    "income statement",
    "statement of income",
    "statements of operations",
    "statement of operations",
    "statements of income",
    "statement of profit or loss",
    "statements of profit or loss",
    "consolidated statement of operations",
    "consolidated statements of operations",
    "consolidated statement of comprehensive income",
    "consolidated statements of comprehensive income",
    "statement of comprehensive income",
    "statements of comprehensive income",
    "consolidated statement of financial position",
    "consolidated statements of financial position",
    "consolidated balance sheet",
    "consolidated balance sheets",
    "statement of financial position",
    "statements of financial position",
    "balance sheet",
    "balance sheets",
    "consolidated statement of changes in equity",
    "consolidated statements of changes in equity",
    "statement of changes in equity",
    "statements of changes in equity",
    "shareholders' equity",
    "shareholders’ equity",
    "stockholders' equity",
    "consolidated cash flow statement",
    "consolidated cash flow statements",
    "consolidated statement of cash flows",
    "consolidated statements of cash flows",
    "cash flow statement",
    "statement of cash flows",
    "statements of cash flows",
    "financial highlights",
    "key figures",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return _clean_text(text)


def _clean_text(value: Any) -> str:
    text = unescape(str(value or "")).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _normalize_text(value: Any) -> str:
    text = _clean_text(value)
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s+", " ", text).strip().lower()


def _compact_text(value: Any) -> str:
    return re.sub(r"[^0-9a-z]+", "", _normalize_text(value))


def is_eu_market(task: dict[str, Any] | None, filename: str | None = None) -> bool:
    task = task or {}
    submit_config = task.get("submit_config") if isinstance(task.get("submit_config"), dict) else {}
    explicit = submit_config.get("market") or task.get("market")
    if explicit:
        return str(explicit).strip().upper() == "EU"
    name = str(filename or task.get("filename") or "")
    lowered = name.lower()
    return bool(re.search(r"(?:^|[_\-])eu(?:[_\-]|$)", lowered) or "issuer_annual_report" in lowered)


def detect_eu_report_kind(text: str, filename: str | None = None) -> str:
    filename_text = str(filename or "")
    body = str(text or "")
    source = _normalize_text("\n".join([filename_text, body[:160000]]))
    long_source = _normalize_text("\n".join([filename_text, body[:900000]]))
    compact = _compact_text(source)
    long_compact = _compact_text(long_source)
    early_source = source[:40000]
    early_compact = _compact_text(early_source)
    formal_statement_terms = (
        "consolidated income statement",
        "consolidated statement of income",
        "consolidated statement of operations",
        "consolidated statement of profit or loss",
        "consolidated balance sheet",
        "consolidated statement of financial position",
        "consolidated statement of cash flows",
        "consolidated cash flow statement",
    )
    formal_statement_hits = sum(
        1 for term in formal_statement_terms if term in long_source or _compact_text(term) in long_compact
    )
    if ("report of the board of directors" in early_source or "reportoftheboardofdirectors" in early_compact) and (
        "nottheoriginalreportincludedintheauditedfinancialreport" in early_compact
        or ("esefcompliantannualfinancialreport" in early_compact and "xhtmlformat" in early_compact)
    ):
        return "eu_board_report"
    if "annual review" in early_source and (
        "accompanying reports" in early_source
        or "finance report" in source
        or "financial statements 2025" in early_source
        or "for full results" in source
    ):
        return "eu_annual_review"
    if "annualreview" in early_compact and (
        "financereport" in long_compact
        or "actualannualreportandofthefinancereport" in long_compact
        or "financialstatements2025" in early_compact
        or "forfullresults" in long_compact
    ) and formal_statement_hits < 2:
        return "eu_annual_review"
    if (
        "actualannualreportandofthefinancereport" in long_compact
        or "ourreportingconsistsoftheactualannualreportandofthefinancereport" in long_compact
    ) and formal_statement_hits < 2:
        return "eu_annual_review"
    if any(term in source or _compact_text(term) in compact for term in _ESEF_TERMS):
        return "eu_esef_annual_report"
    if "integrated report" in source and "financial statements" not in source[:80000]:
        return "eu_integrated_report"
    if any(term in source or _compact_text(term) in compact for term in _ANNUAL_REPORT_TERMS):
        return "eu_annual_report"
    if "half year" in source or "interim report" in source:
        return "eu_interim_report"
    return "eu_pdf_report"


def detect_eu_report_year(text: str, filename: str | None = None) -> int | None:
    source = "\n".join([str(filename or ""), str(text or "")[:160000]])
    for pattern in (
        r"(20\d{2})[-_/](?:0?[1-9]|1[0-2])[-_/](?:0?[1-9]|[12]\d|3[01])",
        r"year ended\s+31\s+december\s+(20\d{2})",
        r"annual report(?: and accounts)?\s+(20\d{2})",
        r"for the year ended\s+31\s+december\s+(20\d{2})",
    ):
        match = re.search(pattern, source, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def eu_candidate_group(name: str) -> str:
    if name in EU_CORE_FINANCIAL_TABLE_NAMES:
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
    if name == "Financial Highlights":
        if _looks_like_financial_highlights(signal):
            return 92.0
    if name == "Consolidated Income Statement":
        if {"revenue", "profit"}.issubset(tokens) and ({"income", "tax"}.issubset(tokens) or {"profit", "before", "tax"}.issubset(tokens)):
            return 88.0
        if {"operating", "profit"}.issubset(tokens) and "revenue" in tokens:
            return 86.0
    if name == "Consolidated Statement of Comprehensive Income":
        if {"other", "comprehensive", "income"}.issubset(tokens) or {"total", "comprehensive", "income"}.issubset(tokens):
            return 88.0
    if name == "Consolidated Statement of Financial Position":
        if {"total", "assets"}.issubset(tokens) and {"total", "liabilities"}.issubset(tokens) and {"total", "equity"}.issubset(tokens):
            return 90.0
        if {"assets", "liabilities", "equity"}.issubset(tokens) and "netassets" in compact:
            return 86.0
    if name == "Consolidated Statement of Changes in Equity":
        if {"ordinary", "share", "capital", "retained", "earnings"}.issubset(tokens) and {"total", "equity"}.issubset(tokens):
            return 90.0
        if "noncontrollinginterests" in compact and "totalequity" in compact and "sharepremium" in compact:
            return 88.0
    if name == "Consolidated Statement of Cash Flows":
        if {"operating", "activities", "investing", "activities", "financing", "activities"}.issubset(tokens) and {"cash", "equivalents"}.issubset(tokens):
            return 90.0
        if "netcashflowsfromoperatingactivities" in compact and "cashandcashequivalentsat31december" in compact:
            return 90.0
    if name == "Segment Information" and ("segment" in tokens or "divisional" in tokens) and ("revenue" in tokens or "income" in tokens):
        return 84.0
    if name == "Net Debt" and {"net", "debt"}.issubset(tokens):
        return 84.0
    return 0.0


def _context_adjusted_score(name: str, item: dict[str, Any], signal: str, score: float) -> float:
    if score <= 0:
        return 0.0
    heading = _normalize_text(item.get("heading"))
    compact = _compact_text(signal)
    if name == "Financial Highlights":
        if not _looks_like_financial_highlights(signal):
            return 0.0
        score += min(_financial_highlight_metric_hits(signal), 5) * 2
    if name in _CORE_STATEMENT_NAMES:
        if heading.startswith("company ") or " company statement " in f" {heading} " or heading.startswith("parent company "):
            return 0.0
        if int(item.get("rows") or 0) <= 12 and any(
            marker in compact
            for marker in (
                "inthissection",
                "contents",
                "tableofcontents",
                "companyfinancialstatements",
                "independentauditorsreport",
            )
        ):
            score -= 70.0
        if any(marker in compact for marker in ("inthissection", "contents", "tableofcontents", "glossary")):
            score -= 35.0
        if "notes to the company financial statements" in heading:
            score -= 25.0
        if item.get("rows") and int(item.get("rows") or 0) >= 15:
            score += 8.0
        if item.get("numeric_ratio") and float(item.get("numeric_ratio") or 0) >= 0.35:
            score += 6.0
        if name == "Consolidated Statement of Changes in Equity" and all(
            marker in compact for marker in ("noncontrollinginterests", "totalattributabletoequityholders", "totalequity")
        ):
            score += 22.0
    if heading:
        rules = _CANDIDATE_RULES.get(name)
        if rules and any(_normalize_text(term) in heading for term in rules[0]):
            score += 18.0
    return max(score, 0.0)


def _looks_like_financial_highlights(signal: str) -> bool:
    if not signal:
        return False
    has_title = any(term in signal for term in _FINANCIAL_HIGHLIGHT_TITLE_TERMS)
    if not has_title:
        return False
    if any(term in signal for term in _FINANCIAL_HIGHLIGHT_HARD_BAD_CONTEXTS):
        return False
    metric_hits = _financial_highlight_metric_hits(signal)
    has_money = _has_money_or_financial_unit(signal)
    has_strong_title = any(term in signal for term in _FINANCIAL_HIGHLIGHT_STRONG_TITLE_TERMS)
    if metric_hits < 3 and not ((has_strong_title and has_money and metric_hits >= 1) or (has_money and metric_hits >= 2)):
        return False
    bad_context = any(term in signal for term in _NON_FINANCIAL_HIGHLIGHT_CONTEXTS)
    if bad_context and metric_hits < 5:
        return False
    return has_money or metric_hits >= 4


def _financial_highlight_metric_hits(signal: str) -> int:
    compact = _compact_text(signal)
    hits = 0
    for metric in _FINANCIAL_HIGHLIGHT_METRICS:
        normalized = _normalize_text(metric)
        if normalized in signal or _compact_text(metric) in compact:
            hits += 1
    return hits


def _has_money_or_financial_unit(signal: str) -> bool:
    return bool(
        re.search(r"(?:€|£|\$|eur|euro|gbp|chf|sek|dkk|nok|pln|usd)\s*(?:m|mn|million|bn|billion)?", signal, flags=re.I)
        or "in € million" in signal
        or "in eur million" in signal
    )


def group_eu_key_table_candidates(table_index: list[dict[str, Any]] | None) -> dict[str, list[dict[str, Any]]]:
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
                    "_source": "eu_market_profile",
                }
            )
            grouped.setdefault(name, []).append(row)

    ordered: dict[str, list[dict[str, Any]]] = {}
    for name in EU_KEY_TABLE_DISPLAY_ORDER:
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
            summary.append({"name": name, "status": "missing", "candidate_group": eu_candidate_group(name)})
            continue
        primary = dict(rows[0])
        primary["name"] = name
        primary["status"] = "found"
        primary["candidate_count"] = len(rows)
        summary.append(primary)
    return summary


def found_sections(markdown: str, table_index: list[dict[str, Any]] | None = None) -> list[str]:
    text = _normalize_text(markdown)
    table_text = _normalize_text("\n".join(_table_signal_text(item) for item in table_index or [] if isinstance(item, dict)))
    source = "\n".join([text, table_text])
    compact_source = _compact_text(source)
    section_terms = {
        "Strategic Report": ("strategic report",),
        "Business Overview": ("business overview", "business model", "our business"),
        "Key Performance Indicators": ("key performance indicators", "kpis", "group highlights"),
        "Risk Management": ("risk management", "principal risks", "risk committee"),
        "Governance": ("governance", "corporate governance", "board of directors"),
        "Sustainability": ("sustainability", "climate-related", "tcfd"),
        "Financial Statements": ("financial statements", "consolidated financial statements"),
        "Auditor's Report": ("auditor's report", "independent auditor"),
        "Notes to the Financial Statements": ("notes to the consolidated financial statements", "notes to the financial statements"),
    }
    found = []
    for name in EU_KEY_SECTIONS:
        terms = section_terms[name]
        if any(_normalize_text(term) in source or _compact_text(term) in compact_source for term in terms):
            found.append(name)
    return found


def eu_quality_report_messages(
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
            warnings.append("EU PDF 单行/空壳表格比例偏高，建议复核 MinerU 表格切分质量。")
    if image_ref_count:
        info_messages.append("Markdown 包含图片引用，images 目录可作为 EU 年报视觉证据来源。")
    if report_kind in {"eu_annual_report", "eu_esef_annual_report"} and found_core_table_count < 3:
        warnings.append("EU Annual Report 核心 IFRS 报表候选偏少，建议复核 Financial Statements / Auditor's Report 附近页面。")
    elif report_kind == "eu_integrated_report" and found_core_table_count < 2:
        warnings.append("当前文件更像 EU Integrated Report，可能只包含经营/ESG/摘要；完整勾稽请优先使用 Annual Report/ESEF。")
    if suspicious_table_count:
        warnings.append(f"发现 {suspicious_table_count} 张可疑表样本，建议逐项打开可视化溯源。")
    return warnings, info_messages


def _load_table_index_by_number(result_dir_path: str | None) -> dict[int, dict[str, Any]]:
    if not result_dir_path:
        return {}
    path = Path(result_dir_path) / "table_index.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, list):
        return {}
    return {
        int(item.get("table_index")): item
        for item in payload
        if isinstance(item, dict) and item.get("table_index") is not None
    }


def _flatten_table_index_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(_clean_text(item) for item in value if item is not None)
    return _clean_text(value)


def _looks_like_statement_context(value: Any) -> bool:
    normalized = _normalize_text(value)
    compact = _compact_text(value)
    return any(term in normalized or _compact_text(term) in compact for term in _STATEMENT_CONTEXT_TERMS)


def _table_index_context(item: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(item, dict):
        return {}
    heading_candidates = [
        _flatten_table_index_text(item.get("source_caption")),
        _flatten_table_index_text(item.get("caption")),
        _flatten_table_index_text(item.get("heading")),
    ]
    heading = ""
    for candidate in heading_candidates:
        if candidate and len(candidate) <= 120 and _looks_like_statement_context(candidate):
            heading = candidate
            break
    if not heading:
        for candidate in heading_candidates:
            if candidate and len(candidate) <= 120:
                heading = candidate
                break
    signal = " ".join(
        _flatten_table_index_text(item.get(key))
        for key in (
            "source_caption",
            "heading",
            "caption",
            "unit",
            "preview",
            "signal_preview",
            "source_footnote",
        )
    )
    return {
        "heading": heading,
        "near_text": _clean_text(signal)[:900],
        "near_before": "",
        "near_after": "",
    }


def _merge_table_context(markdown_context: dict[str, str], index_context: dict[str, str]) -> dict[str, str]:
    if not index_context:
        return markdown_context
    merged = dict(markdown_context)
    current_heading = merged.get("heading") or ""
    index_heading = index_context.get("heading") or ""
    if index_heading and (not current_heading or not _looks_like_statement_context(current_heading) or _looks_like_statement_context(index_heading)):
        merged["heading"] = index_heading
    for key in ("near_text", "near_before", "near_after"):
        parts = [merged.get(key) or "", index_context.get(key) or ""]
        merged[key] = _clean_text(" ".join(part for part in parts if part))[:1200]
    return merged


def _iter_markdown_tables(markdown: str, table_index_by_number: dict[int, dict[str, Any]] | None = None):
    for index, match in enumerate(re.finditer(r"<table\b.*?</table>", markdown or "", flags=re.IGNORECASE | re.DOTALL), start=1):
        context = _table_context(markdown or "", match.start(), match.end())
        if table_index_by_number:
            context = _merge_table_context(context, _table_index_context(table_index_by_number.get(index)))
        yield {
            "table_index": index,
            "line": (markdown or "").count("\n", 0, match.start()) + 1,
            "html": match.group(0),
            "context": context,
        }


def _table_context(markdown: str, start: int, end: int) -> dict[str, str]:
    before = markdown[max(0, start - 2200):start]
    after = markdown[end:min(len(markdown), end + 420)]
    lines = [line.strip() for line in before.splitlines() if line.strip()]
    heading = ""
    for line in reversed(lines[-28:]):
        cleaned = re.sub(r"^#+\s*", "", _strip_html(line)).strip()
        if not cleaned or "<table" in cleaned.lower() or len(cleaned) > 90:
            continue
        lowered = _normalize_text(cleaned)
        if _looks_like_statement_context(lowered):
            heading = cleaned
            break
    if not heading:
        for line in reversed(lines[-14:]):
            cleaned = re.sub(r"^#+\s*", "", _strip_html(line)).strip()
            if not cleaned or len(cleaned) > 80:
                continue
            if cleaned.lower() in {"notes", "continued", "attributable to equity holders"}:
                continue
            heading = cleaned
            break
    return {
        "heading": heading,
        "unit": "",
        "near_text": _strip_html(before[-360:] + " " + after[:160])[:420],
        "near_before": _strip_html(before[-900:])[:900],
        "near_after": _strip_html(after[:280])[:280],
    }


def _eu_statement_body_is_plausible(statement_type: str, compact: str, rows: int) -> bool:
    if statement_type == "income_statement":
        has_revenue = any(
            token in compact
            for token in (
                "revenue",
                "revenues",
                "sales",
                "salesrevenue",
                "netsales",
                "totalnetsales",
                "turnover",
                "totalincome",
                "netinterestincome",
                "interestandsimilarincome",
                "insurancerevenue",
            )
        )
        has_profit = any(
            token in compact
            for token in (
                "profitbeforetax",
                "profitbeforetaxation",
                "profitlossbeforetax",
                "profitbeforeincometaxes",
                "profitlossfromcontinuingoperationsbeforeincometaxes",
                "incomelossbeforeincometaxes",
                "incomelossbeforetax",
                "profitfortheyear",
                "profitfortheperiod",
                "profitlossfortheperiod",
                "incomelossfortheyear",
                "incomelossfortheperiod",
                "profitaftertax",
                "profitaftertaxes",
                "incomebeforetax",
                "incomebeforetaxation",
                "incomebeforetaxes",
                "incomefortheyear",
                "incomefortheperiod",
                "incomeaftertax",
                "incomeaftertaxes",
                "netincome",
                "incomebeforeincometaxes",
                "incomebeforeincometax",
            )
        )
        return rows >= 6 and has_revenue and has_profit
    if statement_type == "balance_sheet":
        return rows >= 4 and "totalassets" in compact and ("totalliabilities" in compact or "netassets" in compact) and "totalequity" in compact
    if statement_type == "cash_flow_statement":
        return rows >= 7 and "operatingactivities" in compact and "investingactivities" in compact and "financingactivities" in compact
    if statement_type == "equity_statement":
        return rows >= 8 and ("sharecapital" in compact or "ordinarysharecapital" in compact) and "retainedearnings" in compact and "totalequity" in compact
    if statement_type == "comprehensive_income_statement":
        return rows >= 6 and "othercomprehensive" in compact and "totalcomprehensive" in compact
    return False


def _eu_statement_fragment_is_plausible(statement_type: str, compact: str, rows: int) -> bool:
    if statement_type == "balance_sheet":
        has_assets = "totalassets" in compact or ("assets" in compact and "cashandcashequivalents" in compact)
        has_liabilities_equity = any(
            token in compact
            for token in (
                "totalliabilities",
                "liabilitiesandshareholdersequity",
                "liabilitiesandshareholdersequity",
                "shareholdersequity",
                "totalequity",
            )
        )
        return rows >= 3 and (has_assets or has_liabilities_equity)
    if statement_type == "cash_flow_statement":
        return rows >= 3 and any(
            token in compact
            for token in (
                "cashflowfromoperatingactivities",
                "cashflowsfromoperatingactivities",
                "netcashflowsfromoperatingactivities",
                "cashflowfrominvestingactivities",
                "cashflowsfrominvestingactivities",
                "cashflowfromfinancingactivities",
                "cashflowsfromfinancingactivities",
                "netcashflowsfromfinancingactivities",
                "cashandcashequivalentsatend",
                "cashandcashequivalentsat31december",
            )
        )
    return _eu_statement_body_is_plausible(statement_type, compact, rows)


def _classify_eu_statement(table: dict[str, Any], grid: list[list[str]]) -> str | None:
    context = table.get("context") or {}
    heading = _normalize_text(context.get("heading"))
    first_rows = _normalize_text(" ".join(" ".join(row[:8]) for row in grid[:3]))
    body = _normalize_text(" ".join(" ".join(row[:8]) for row in grid[:120]))
    formal_signal = " ".join([heading, first_rows])
    signal = " ".join([formal_signal, body])
    compact = _compact_text(signal)
    rows = len(grid or [])
    if rows < 3:
        return None
    if "inthissection" in compact and rows <= 12:
        return None
    if heading.startswith("company ") or " company statement " in f" {heading} " or heading.startswith("parent company "):
        return None
    if (
        ("consolidated statement of comprehensive income" in formal_signal or "consolidated statements of comprehensive income" in formal_signal)
        and _eu_statement_body_is_plausible("comprehensive_income_statement", compact, rows)
    ):
        return "comprehensive_income_statement"
    if (
        (
            "consolidated statement of changes in equity" in formal_signal
            or "consolidated statements of changes in equity" in formal_signal
            or "statement of changes in equity" in heading
            or "statements of changes in equity" in heading
            or "consolidated statement of shareholders' equity" in formal_signal
            or "consolidated statements of shareholders' equity" in formal_signal
            or "consolidated statement of shareholders’ equity" in formal_signal
            or "consolidated statements of shareholders’ equity" in formal_signal
            or "consolidated statements of stockholders' equity" in formal_signal
        )
        and _eu_statement_body_is_plausible("equity_statement", compact, rows)
    ):
        return "equity_statement"
    formal_cash_flow = (
        "consolidated cash flow statement" in formal_signal
        or "consolidated cash flow statements" in formal_signal
        or "consolidated statement of cash flows" in formal_signal
        or "consolidated statements of cash flows" in formal_signal
        or "cash flow statement" in formal_signal
        or "cash flow statements" in formal_signal
        or "statement of cash flows" in formal_signal
        or "statements of cash flows" in formal_signal
    )
    if formal_cash_flow and (_eu_statement_fragment_is_plausible("cash_flow_statement", compact, rows) or rows >= 8):
        return "cash_flow_statement"
    if (
        (
            "consolidated balance sheet" in formal_signal
            or "consolidated balance sheets" in formal_signal
            or "consolidated statement of financial position" in formal_signal
            or "consolidated statements of financial position" in formal_signal
            or "balance sheet" in formal_signal
            or "balance sheets" in formal_signal
            or "statement of financial position" in formal_signal
            or "statements of financial position" in formal_signal
        )
        and _eu_statement_fragment_is_plausible("balance_sheet", compact, rows)
    ):
        return "balance_sheet"
    if (
        (
            "consolidated income statement" in formal_signal
            or "consolidated income statements" in formal_signal
            or "consolidated statement of profit or loss" in formal_signal
            or "consolidated statements of profit or loss" in formal_signal
            or "consolidated statement of operations" in formal_signal
            or "consolidated statements of operations" in formal_signal
            or "consolidated statement of income" in formal_signal
            or "consolidated statements of income" in formal_signal
            or "condensed consolidated statement of income" in formal_signal
            or "condensed consolidated income statement" in formal_signal
            or "income statement" in formal_signal
            or "income statements" in formal_signal
            or "statement of income" in formal_signal
            or "statements of income" in formal_signal
            or "statement of profit or loss" in formal_signal
            or "statements of profit or loss" in formal_signal
            or "statement of operations" in formal_signal
            or "statements of operations" in formal_signal
        )
        and _eu_statement_body_is_plausible("income_statement", compact, rows)
    ):
        return "income_statement"
    if rows >= 20 and "totalassets" in compact and "totalliabilities" in compact and "totalequity" in compact:
        return "balance_sheet"
    if rows >= 15 and _eu_statement_body_is_plausible("income_statement", compact, rows):
        return "income_statement"
    if rows >= 20 and "operatingactivities" in compact and "investingactivities" in compact and "financingactivities" in compact and "cashandcashequivalents" in compact:
        return "cash_flow_statement"
    if rows >= 10 and "sharecapital" in compact and "retainedearnings" in compact and "totalequity" in compact:
        return "equity_statement"
    return None


def _infer_unit_and_currency(grid: list[list[str]], context: dict[str, Any]) -> tuple[str, str]:
    source = " ".join([context.get("heading") or "", context.get("near_text") or "", " ".join(" ".join(row[:6]) for row in grid[:2])])
    lowered = _normalize_text(source)
    compact = _compact_text(source)
    currency = ""
    for code, markers in _CURRENCY_PATTERNS:
        if any(marker in lowered for marker in markers) or code.lower() in compact:
            currency = code
            break
    if not currency and "$" in source:
        currency = "USD"
    symbol = _CURRENCY_SYMBOLS.get(currency, "")
    currency_terms = r"(?:\u00a3|\u20ac|gbp|eur|chf|sek|dkk|nok|pln|usd|us\$|\$)"
    if re.search(fr"{currency_terms}\s?(?:m|mn|millions?)\b|millions?", lowered):
        unit = f"{currency or symbol} million".strip()
    elif re.search(fr"{currency_terms}\s?(?:bn|billions?)\b|billions?", lowered):
        unit = f"{currency or symbol} billion".strip()
    elif symbol:
        unit = symbol
    else:
        unit = ""
    return unit, currency


def _period_from_header(header: str, statement_type: str, report_year: int | None) -> str:
    match = re.search(r"(20\d{2})", str(header or ""))
    if match:
        year = int(match.group(1))
        return f"{year:04d}-12-31" if statement_type == "balance_sheet" else str(year)
    if report_year:
        lowered = _normalize_text(header)
        if "current" in lowered or "this year" in lowered:
            return f"{report_year:04d}-12-31" if statement_type == "balance_sheet" else str(report_year)
        if "prior" in lowered or "previous" in lowered:
            return f"{report_year - 1:04d}-12-31" if statement_type == "balance_sheet" else str(report_year - 1)
    return ""


def _first_numeric_row(grid: list[list[str]]) -> int:
    for index, row in enumerate(grid[:10]):
        if sum(1 for cell in row[1:] if _parse_number(cell) is not None) >= 1:
            return max(1, index)
    return 1 if grid else 0


def _column_descriptors(grid: list[list[str]], statement_type: str, report_year: int | None) -> list[dict[str, Any]]:
    if not grid:
        return []
    first_numeric = _first_numeric_row(grid)
    max_cols = max(len(row) for row in grid)
    descriptors = []
    for col in range(1, max_cols):
        header = " ".join(row[col] for row in grid[:first_numeric] if col < len(row) and row[col])
        if _normalize_text(header) in {"notes", "note"}:
            continue
        period = _period_from_header(header, statement_type, report_year)
        if period:
            descriptors.append(
                {
                    "column_index": col,
                    "label": header,
                    "period": period,
                    "variant": "",
                    "value_key": period,
                    "scope": "consolidated",
                }
            )
    if descriptors:
        return descriptors
    if report_year:
        numeric_scores = []
        for col in range(1, max_cols):
            count = sum(1 for row in grid if col < len(row) and _parse_number(row[col]) is not None)
            numeric_scores.append((col, count))
        value_cols = [col for col, count in numeric_scores if count >= 3]
        if len(value_cols) >= 2:
            value_cols = value_cols[-2:]
            periods = (
                f"{report_year:04d}-12-31" if statement_type == "balance_sheet" else str(report_year),
                f"{report_year - 1:04d}-12-31" if statement_type == "balance_sheet" else str(report_year - 1),
            )
            return [
                {
                    "column_index": col,
                    "label": period,
                    "period": period,
                    "variant": "",
                    "value_key": period,
                    "scope": "consolidated",
                }
                for col, period in zip(value_cols, periods)
            ]
    return []


def _parse_number(value: Any) -> float | None:
    raw = _clean_text(value)
    if not raw or raw in {"-", "\u2013", "\u2014", "--", "n/a"}:
        return None
    text = raw.replace(",", "").replace(" ", "")
    text = re.sub(r"\$\{?|\}|\^[-0-9A-Za-z]+", "", text)
    text = text.replace("\u00a3", "").replace("\u20ac", "").replace("$", "")
    text = re.sub(r"(?:gbp|eur|usd|chf|sek|dkk|nok|pln|million|m|bn|p|%)$", "", text, flags=re.IGNORECASE)
    negative = False
    if re.fullmatch(r"[\(\[].+[\)\]]", text):
        negative = True
        text = text[1:-1]
    if text in {"-", "\u2013", "\u2014", ""}:
        return None
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
        return None
    parsed = float(text)
    return -abs(parsed) if negative else parsed


def _canonical_name(label: Any, statement_type: str) -> str | None:
    text = _normalize_text(label)
    compact = _compact_text(text)
    if not compact or compact in {"assets", "liabilities", "equity", "notes", "operatingactivities", "investingactivities", "financingactivities"}:
        return None
    if statement_type == "balance_sheet":
        if compact == "totalassets":
            return "total_assets"
        if compact == "totalliabilities":
            return "total_liabilities"
        if compact == "totalequity":
            return "total_equity"
        if compact == "netassets":
            return "net_assets"
        if "totalequityattributable" in compact or "equityattributableto" in compact:
            return "equity_attributable_parent"
        if "noncontrollinginterests" in compact:
            return "minority_interests"
        if compact == "cashandcashequivalents":
            return "cash_and_cash_equivalents"
    if statement_type == "income_statement":
        if compact in {"revenue", "revenues", "sales", "netsales", "totalnetsales", "salesrevenue", "turnover", "insurancerevenue"}:
            return "operating_revenue"
        if compact in {"totalincome", "operatingincome"}:
            return "total_income"
        if compact in {"grossprofit", "grossmargin"}:
            return "gross_profit"
        if compact in {"operatingprofit", "incomefromoperations"}:
            return "operating_profit"
        if compact in {
            "profitbeforetax",
            "profitbeforetaxation",
            "profitlossbeforetax",
            "profitbeforeincometaxes",
            "profitlossfromcontinuingoperationsbeforeincometaxes",
            "incomelossbeforeincometaxes",
            "incomelossbeforetax",
            "incomebeforetax",
            "incomebeforetaxation",
            "incomebeforetaxes",
            "incomebeforeincometaxes",
            "incomebeforeincometax",
        }:
            return "profit_before_tax"
        if compact in {"incometaxexpense", "taxation", "taxationcharge"}:
            return "income_tax_expense"
        if compact in {
            "profitfortheyear",
            "profitfortheperiod",
            "profitlossfortheperiod",
            "incomelossfortheyear",
            "incomelossfortheperiod",
            "incomefortheyear",
            "incomefortheperiod",
            "profitaftertax",
            "profitaftertaxes",
            "incomeaftertax",
            "incomeaftertaxes",
            "netincome",
            "netprofit",
        }:
            return "net_profit"
        if (
            compact == "equityholders"
            or ("incomeattributableto" in compact and "shareholders" in compact)
            or ("profitattributableto" in compact and "shareholders" in compact)
            or ("incomeattributableto" in compact and "owners" in compact)
            or ("profitattributableto" in compact and "owners" in compact)
        ):
            return "parent_net_profit"
        if (
            compact == "noncontrollinginterests"
            or "incomeattributabletononcontrollinginterest" in compact
            or "profitattributabletononcontrollinginterest" in compact
        ):
            return "minority_profit_loss"
        if compact == "basicearningspershare":
            return "basic_eps"
        if compact == "dilutedearningspershare":
            return "diluted_eps"
    if statement_type == "cash_flow_statement":
        if compact == "profitfortheyear":
            return "net_profit"
        if compact == "cashgeneratedfromoperations":
            return "cash_generated_from_operations"
        if compact in {"netcashflowsfromoperatingactivities", "cashflowfromoperatingactivities", "cashflowsfromoperatingactivities"}:
            return "operating_cash_flow_net"
        if compact in {
            "netcashflowsusedininvestingactivities",
            "netcashflowsfrominvestingactivities",
            "cashflowfrominvestingactivities",
            "cashflowsfrominvestingactivities",
        }:
            return "investing_cash_flow_net"
        if compact in {
            "netcashflowsusedinfinancingactivities",
            "netcashflowsfromfinancingactivities",
            "cashflowfromfinancingactivities",
            "cashflowsfromfinancingactivities",
        }:
            return "financing_cash_flow_net"
        if (
            compact == "increasedecreaseincashandcashequivalents"
            or compact == "netincreasedecreaseincashandcashequivalents"
            or "increaseincashandcashequivalents" in compact
            or "decreaseincashandcashequivalents" in compact
        ):
            return "cash_equivalents_net_increase"
        if (
            compact in {"foreignexchangetranslation", "effectofexchangeratechanges"}
            or "effectsofexchangeratechanges" in compact
            or "foreignexchangerates" in compact
        ):
            return "fx_effect_cash"
        if "casheffectivechangesincashandcashequivalents" in compact:
            return "cash_equivalents_net_increase"
        if (
            compact == "cashandcashequivalentsat1january"
            or compact == "cashandcashequivalentsatjanuary1"
            or "cashandcashequivalentsatbeginning" in compact
        ):
            return "cash_equivalents_beginning"
        if (
            "cashandcashequivalentsat31december" in compact
            or "cashandcashequivalentsatdecember31" in compact
            or "cashandcashequivalentsatend" in compact
        ):
            return "cash_equivalents_ending"
    return None


def _statement_title(statement_type: str) -> str:
    return {
        "balance_sheet": "Consolidated Statement of Financial Position",
        "income_statement": "Consolidated Income Statement",
        "cash_flow_statement": "Consolidated Statement of Cash Flows",
    }.get(statement_type, statement_type)


def _new_statement(task_id: str | None, filename: str | None, statement_type: str, title: str, unit: str, currency: str) -> dict[str, Any]:
    return {
        "statement_id": f"{statement_type}:consolidated",
        "statement_type": statement_type,
        "statement_name": _statement_title(statement_type),
        "scope": "consolidated",
        "scope_name": "Consolidated",
        "title": title,
        "unit": unit,
        "scale": 1.0,
        "currency": currency,
        "task_id": task_id,
        "filename": filename,
        "columns": [],
        "items": [],
        "table_indexes": [],
        "line_numbers": [],
        "_item_lookup": {},
    }


def _add_statement_item(statement: dict[str, Any], label: str, row: list[str], descriptors: list[dict[str, Any]], table: dict[str, Any]) -> None:
    canonical = _canonical_name(label, statement["statement_type"])
    if not canonical:
        return
    values: dict[str, float] = {}
    raw_values: dict[str, str] = {}
    sources: dict[str, dict[str, Any]] = {}
    for desc in descriptors:
        col = int(desc["column_index"])
        if col >= len(row):
            continue
        value = _parse_number(row[col])
        if value is None:
            continue
        key = desc["value_key"]
        values[key] = value
        raw_values[key] = row[col]
        sources[key] = {"table_index": table["table_index"], "line": table["line"]}
    if not values:
        return
    item = statement["_item_lookup"].get(canonical)
    if item is None:
        item = {
            "name": _clean_text(label),
            "canonical_name": canonical,
            "values": {},
            "raw_values": {},
            "sources": {},
        }
        statement["_item_lookup"][canonical] = item
        statement["items"].append(item)
    for key, value in values.items():
        if key not in item["values"]:
            item["values"][key] = value
            item["raw_values"][key] = raw_values[key]
            item["sources"][key] = sources[key]


def _extract_statement_table(
    data: dict[str, Any],
    statements: dict[str, dict[str, Any]],
    table: dict[str, Any],
    grid: list[list[str]],
    statement_type: str,
    report_year: int | None,
) -> None:
    context = table.get("context") or {}
    title = context.get("heading") or _statement_title(statement_type)
    unit, currency = _infer_unit_and_currency(grid, context)
    descriptors = _column_descriptors(grid, statement_type, report_year)
    if not descriptors:
        data["warnings"].append(f"EU 表 {table['table_index']} 未识别到可校验期间列: {title}")
        return
    statement = statements.get(statement_type)
    if statement is None:
        statement = _new_statement(data.get("task_id"), data.get("filename"), statement_type, title, unit, currency)
        statements[statement_type] = statement
    if not statement.get("unit") and unit:
        statement["unit"] = unit
    if not statement.get("currency") and currency:
        statement["currency"] = currency
    for desc in descriptors:
        column = {
            "key": desc["value_key"],
            "period": desc["period"],
            "variant": "",
            "label": desc["label"],
        }
        if not any(existing["key"] == column["key"] for existing in statement["columns"]):
            statement["columns"].append(column)
    if table["table_index"] not in statement["table_indexes"]:
        statement["table_indexes"].append(table["table_index"])
    if table["line"] not in statement["line_numbers"]:
        statement["line_numbers"].append(table["line"])
    for row in grid:
        if not row:
            continue
        _add_statement_item(statement, row[0], row, descriptors, table)


def _eu_table_fragment_matches_statement(statement_type: str, table: dict[str, Any], grid: list[list[str]]) -> bool:
    context = table.get("context") or {}
    heading = _normalize_text(context.get("heading"))
    if heading and any(term in heading for term in ("analysis", "reconciliation", "sensitivity", "remuneration", "compensation")):
        return False
    body = _normalize_text(" ".join(" ".join(row[:8]) for row in grid[:120]))
    compact = _compact_text(body)
    return _eu_statement_fragment_is_plausible(statement_type, compact, len(grid or []))


def _value_for_current_period(statement: dict[str, Any], canonical: str, report_year: int | None) -> tuple[Any, str | None, dict[str, Any] | None]:
    item = next((row for row in statement.get("items", []) if row.get("canonical_name") == canonical), None)
    if not item:
        return None, None, None
    keys = []
    if report_year:
        keys.extend([str(report_year), f"{report_year:04d}-12-31"])
    keys.extend(sorted((item.get("values") or {}).keys(), reverse=True))
    for key in keys:
        if key in (item.get("values") or {}):
            return item["values"][key], key, item
    return None, None, item


def _build_key_metrics(statements: dict[str, dict[str, Any]], report_year: int | None) -> list[dict[str, Any]]:
    metrics = []
    metric_specs = [
        ("income_statement", "operating_revenue", "Revenue"),
        ("income_statement", "operating_profit", "Operating profit"),
        ("income_statement", "net_profit", "Profit for the year"),
        ("income_statement", "basic_eps", "Basic earnings per share"),
        ("balance_sheet", "total_assets", "Total assets"),
        ("balance_sheet", "total_equity", "Total equity"),
        ("cash_flow_statement", "operating_cash_flow_net", "Net cash flows from operating activities"),
        ("cash_flow_statement", "cash_equivalents_ending", "Cash and cash equivalents at year end"),
    ]
    for statement_type, canonical, label in metric_specs:
        statement = statements.get(statement_type)
        if not statement:
            continue
        value, period, item = _value_for_current_period(statement, canonical, report_year)
        if value is None or period is None:
            continue
        sources = item.get("sources") or {}
        metrics.append(
            {
                "name": label,
                "canonical_name": canonical,
                "value": value,
                "period": period,
                "unit": statement.get("unit") or "",
                "currency": statement.get("currency") or "",
                "values": {period: value},
                "raw_values": {period: (item.get("raw_values") or {}).get(period)},
                "sources": {period: sources.get(period)},
            }
        )
    return metrics


def build_eu_financial_data(
    markdown: str,
    task_id: str | None = None,
    filename: str | None = None,
    llm_judge: Any = None,
    llm_cache_dir: str | None = None,
    market: str | None = "EU",
    result_dir_path: str | None = None,
) -> dict[str, Any]:
    from financial_extractor import FINANCIAL_DATA_SCHEMA_VERSION, FINANCIAL_RULE_VERSION, parse_html_table

    markdown = markdown or ""
    report_year = detect_eu_report_year(markdown, filename=filename)
    report_kind = detect_eu_report_kind(markdown, filename=filename)
    data: dict[str, Any] = {
        "schema_version": FINANCIAL_DATA_SCHEMA_VERSION,
        "rule_version": FINANCIAL_RULE_VERSION,
        "task_id": task_id,
        "filename": filename,
        "market": "EU",
        "market_profile": "EU",
        "profile_rule_version": EU_PROFILE_RULE_VERSION,
        "accounting_standard": EU_DEFAULT_ACCOUNTING_STANDARD,
        "report_kind": report_kind,
        "report_year": report_year,
        "industry_profile": "general",
        "statements": [],
        "key_metrics": [],
        "classification_evidence": [],
        "llm_table_judgments": [],
        "warnings": [],
        "generated_at": _now_iso(),
        "classification_summary": {
            "looks_like_financial_report": True,
            "report_kind_blocked": False,
            "market_profile": "EU",
        },
    }
    statements: dict[str, dict[str, Any]] = {}
    extra_statement_candidates = []
    previous_statement_type: str | None = None
    previous_statement_table_index: int | None = None
    table_index_by_number = _load_table_index_by_number(result_dir_path)
    for table in _iter_markdown_tables(markdown, table_index_by_number):
        grid = parse_html_table(table["html"])
        if not grid:
            continue
        statement_type = _classify_eu_statement(table, grid)
        if (
            not statement_type
            and previous_statement_type in {"balance_sheet", "cash_flow_statement"}
            and previous_statement_table_index is not None
            and table["table_index"] <= previous_statement_table_index + 2
            and _eu_table_fragment_matches_statement(previous_statement_type, table, grid)
        ):
            statement_type = previous_statement_type
        if not statement_type:
            continue
        data["classification_evidence"].append(
            {
                "table_index": table["table_index"],
                "line": table["line"],
                "table_type": statement_type,
                "evidence": ["eu_market_profile.statement_rule"],
            }
        )
        if statement_type in {"balance_sheet", "income_statement", "cash_flow_statement"}:
            _extract_statement_table(data, statements, table, grid, statement_type, report_year)
            previous_statement_type = statement_type
            previous_statement_table_index = table["table_index"]
        else:
            extra_statement_candidates.append(
                {
                    "statement_type": statement_type,
                    "table_index": table["table_index"],
                    "line": table["line"],
                    "title": (table.get("context") or {}).get("heading") or statement_type,
                }
            )
            previous_statement_type = statement_type
            previous_statement_table_index = table["table_index"]
    for statement in statements.values():
        statement.pop("_item_lookup", None)
        statement["columns"].sort(key=lambda item: item["key"])
        data["statements"].append(statement)
    data["statements"].sort(key=lambda item: (item["statement_type"], item["scope"]))
    data["extra_statement_candidates"] = extra_statement_candidates
    data["key_metrics"] = _build_key_metrics(statements, report_year)
    data["detected_currencies"] = sorted(
        {
            item.get("currency")
            for item in data["statements"]
            if item.get("currency")
        }
    )
    statement_units = sorted(
        {
            item.get("unit")
            for item in data["statements"]
            if item.get("unit")
        }
    )
    if len(data["detected_currencies"]) == 1:
        data["currency"] = data["detected_currencies"][0]
    if len(statement_units) == 1:
        data["unit"] = statement_units[0]
    data["summary"] = {
        "statement_count": len(data["statements"]),
        "key_metric_count": len(data["key_metrics"]),
        "scopes": sorted({item["scope"] for item in data["statements"]}),
        "detected_currencies": data["detected_currencies"],
        "statement_units": statement_units,
    }
    if not data["statements"]:
        data["warnings"].append("EU PDF 未确认完整结构化 IFRS 财务报表，已按候选识别模式处理。")
    return data


def _item_map(statement: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not statement:
        return {}
    return {
        item.get("canonical_name"): item
        for item in statement.get("items", [])
        if item.get("canonical_name")
    }


def _periods_for_statement(statement: dict[str, Any] | None) -> list[str]:
    periods = set()
    for item in (statement or {}).get("items", []):
        periods.update((item.get("values") or {}).keys())
    return sorted(periods)


def _statement_by(data: dict[str, Any], statement_type: str) -> dict[str, Any] | None:
    for statement in data.get("statements", []):
        if statement.get("statement_type") == statement_type and statement.get("scope") == "consolidated":
            return statement
    return None


def _value(statement: dict[str, Any] | None, canonical: str, period: str) -> float | None:
    item = _item_map(statement).get(canonical)
    if not item:
        return None
    value = (item.get("values") or {}).get(period)
    return value if isinstance(value, (int, float)) else None


def _sum_optional(*values: float | None) -> float | None:
    if any(value is None for value in values):
        return None
    return sum(float(value) for value in values if value is not None)


def _tolerance(values: Iterable[float | None]) -> float:
    numeric = [abs(float(value)) for value in values if value is not None]
    magnitude = max(numeric) if numeric else 0.0
    return max(1.0, magnitude * 0.00001)


def _numeric_check(
    rule_id: str,
    rule_name: str,
    statement_type: str,
    period: str,
    left_name: str,
    left_value: float | None,
    right_formula: str,
    right_value: float | None,
    inputs: list[str],
    failure_status: str = "fail",
) -> dict[str, Any]:
    if left_value is None or right_value is None:
        return {
            "rule_id": rule_id,
            "rule_name": rule_name,
            "statement_type": statement_type,
            "scope": "consolidated",
            "period": period,
            "status": "skipped",
            "reason": "missing_required_items",
            "inputs": inputs,
        }
    diff = float(left_value) - float(right_value)
    tolerance = _tolerance([left_value, right_value])
    return {
        "rule_id": rule_id,
        "rule_name": rule_name,
        "statement_type": statement_type,
        "scope": "consolidated",
        "period": period,
        "left": {"name": left_name, "value": left_value},
        "right": {"formula": right_formula, "value": right_value},
        "diff": diff,
        "tolerance": tolerance,
        "status": "pass" if abs(diff) <= tolerance else failure_status,
        "reason": None if abs(diff) <= tolerance or failure_status == "fail" else "eu_ifrs_bridge_requires_presentation_review",
        "inputs": inputs,
    }


def _closest_formula(
    target: float | None,
    candidates: list[tuple[str, float | None]],
) -> tuple[str, float | None]:
    available = [(formula, value) for formula, value in candidates if value is not None]
    if not available:
        return candidates[0] if candidates else ("", None)
    if target is None:
        return available[0]
    return min(available, key=lambda item: abs(float(target) - float(item[1])))


def _presence_check(
    statement_type: str,
    statement: dict[str, Any] | None,
    *,
    required: bool = False,
    no_structured_statements: bool = False,
) -> dict[str, Any]:
    names = {
        "balance_sheet": "Required EU/IFRS statement present: statement of financial position / balance sheet",
        "income_statement": "Required EU/IFRS statement present: income statement / profit or loss",
        "cash_flow_statement": "Required EU/IFRS statement present: cash flow statement",
    }
    if statement:
        status = "pass"
        reason = "statement_found"
    elif required:
        status = "fail"
        if no_structured_statements:
            reason = "required_statement_not_extracted_or_parser_coverage_incomplete_for_eu_annual_report"
        else:
            reason = "required_statement_not_extracted_for_eu_annual_report"
    elif no_structured_statements:
        status = "warning"
        reason = "statement_not_extracted_or_parser_coverage_incomplete_for_eu_report"
    else:
        status = "warning"
        reason = "statement_not_extracted_or_not_located_for_eu_report"
    return {
        "rule_id": f"eu.presence.{statement_type}",
        "rule_name": names.get(statement_type, f"Required EU/IFRS statement present: {statement_type}"),
        "statement_type": statement_type,
        "scope": "consolidated",
        "period": "",
        "status": status,
        "reason": reason,
        "inputs": [],
    }


def build_eu_financial_checks(data: dict[str, Any]) -> dict[str, Any]:
    from financial_extractor import FINANCIAL_CHECKS_SCHEMA_VERSION, FINANCIAL_RULE_VERSION

    if not isinstance(data, dict):
        data = {}
    warnings = [
        item
        for item in list(data.get("warnings") or [])
        if "合并三大表" not in str(item) and "资产负债表、现金流量表、利润表" not in str(item)
    ]
    checks: list[dict[str, Any]] = []
    balance = _statement_by(data, "balance_sheet")
    income = _statement_by(data, "income_statement")
    cash = _statement_by(data, "cash_flow_statement")
    no_structured_statements = not any(data.get("statements") or [])
    requires_core_statements = str(data.get("report_kind") or "") in _EU_FULL_REPORT_KINDS
    for statement_type, statement in (
        ("balance_sheet", balance),
        ("income_statement", income),
        ("cash_flow_statement", cash),
    ):
        checks.append(
            _presence_check(
                statement_type,
                statement,
                required=requires_core_statements,
                no_structured_statements=no_structured_statements,
            )
        )

    for period in _periods_for_statement(balance):
        total_assets = _value(balance, "total_assets", period)
        total_liabilities = _value(balance, "total_liabilities", period)
        total_equity = _value(balance, "total_equity", period)
        net_assets = _value(balance, "net_assets", period)
        checks.append(
            _numeric_check(
                "eu.bs.assets_eq_liabilities_plus_equity",
                "IFRS/EU balance sheet: total assets = total liabilities + total equity",
                "balance_sheet",
                period,
                "Total assets",
                total_assets,
                "Total liabilities + Total equity",
                _sum_optional(total_liabilities, total_equity),
                ["total_assets", "total_liabilities", "total_equity"],
                failure_status="warning",
            )
        )
        checks.append(
            _numeric_check(
                "eu.bs.net_assets_eq_total_equity",
                "IFRS/EU balance sheet: net assets = total equity",
                "balance_sheet",
                period,
                "Net assets",
                net_assets,
                "Total equity",
                total_equity,
                ["net_assets", "total_equity"],
                failure_status="warning",
            )
        )

    for period in _periods_for_statement(income):
        profit_before_tax = _value(income, "profit_before_tax", period)
        income_tax = _value(income, "income_tax_expense", period)
        income_tax_expense = -abs(income_tax) if income_tax is not None else None
        net_profit = _value(income, "net_profit", period)
        parent_net_profit = _value(income, "parent_net_profit", period)
        minority_profit = _value(income, "minority_profit_loss", period)
        checks.append(
            _numeric_check(
                "eu.is.profit_before_tax_less_tax_eq_profit_for_year",
                "IFRS income statement: profit before tax + tax expense = profit for the year",
                "income_statement",
                period,
                "Profit for the year",
                net_profit,
                "Profit before tax - Income tax expense",
                _sum_optional(profit_before_tax, income_tax_expense),
                ["net_profit", "profit_before_tax", "income_tax_expense"],
                failure_status="warning",
            )
        )
        checks.append(
            _numeric_check(
                "eu.is.parent_plus_nci_eq_profit_for_year",
                "IFRS income statement: equity holders + non-controlling interests = profit for the year",
                "income_statement",
                period,
                "Profit for the year",
                net_profit,
                "Profit attributable to equity holders + non-controlling interests",
                _sum_optional(parent_net_profit, minority_profit),
                ["net_profit", "parent_net_profit", "minority_profit_loss"],
                failure_status="warning",
            )
        )

    for period in _periods_for_statement(cash):
        operating = _value(cash, "operating_cash_flow_net", period)
        investing = _value(cash, "investing_cash_flow_net", period)
        financing = _value(cash, "financing_cash_flow_net", period)
        net_change = _value(cash, "cash_equivalents_net_increase", period)
        fx_effect = _value(cash, "fx_effect_cash", period)
        beginning = _value(cash, "cash_equivalents_beginning", period)
        ending = _value(cash, "cash_equivalents_ending", period)
        cash_change_formula, cash_change_value = _closest_formula(
            net_change,
            [
                ("Operating cash flow + Investing cash flow + Financing cash flow + FX effect", _sum_optional(operating, investing, financing, fx_effect)),
                ("Operating cash flow + Investing cash flow + Financing cash flow", _sum_optional(operating, investing, financing)),
            ],
        )
        checks.append(
            _numeric_check(
                "eu.cf.operating_investing_financing_eq_net_change",
                "IFRS cash flow: operating + investing + financing cash flows = net increase/decrease in cash",
                "cash_flow_statement",
                period,
                "Increase/(decrease) in cash and cash equivalents",
                net_change,
                cash_change_formula,
                cash_change_value,
                ["operating_cash_flow_net", "investing_cash_flow_net", "financing_cash_flow_net", "cash_equivalents_net_increase"],
                failure_status="warning",
            )
        )
        ending_formula, ending_value = _closest_formula(
            ending,
            [
                ("Cash at beginning + net change in cash and cash equivalents", _sum_optional(beginning, net_change)),
                ("Cash at beginning + net change + foreign exchange translation", _sum_optional(beginning, net_change, fx_effect)),
            ],
        )
        checks.append(
            _numeric_check(
                "eu.cf.beginning_plus_change_fx_eq_ending_cash",
                "IFRS cash flow: beginning cash + net change + FX = ending cash",
                "cash_flow_statement",
                period,
                "Cash and cash equivalents at year end",
                ending,
                ending_formula,
                ending_value,
                ["cash_equivalents_beginning", "cash_equivalents_net_increase", "fx_effect_cash", "cash_equivalents_ending"],
                failure_status="warning",
            )
        )

    if not data.get("statements"):
        warnings.append("EU PDF 未确认完整结构化 IFRS 财务报表，已按候选识别模式处理；完整数值勾稽建议结合 ESEF/iXBRL 或原文表格复核。")
        warnings.append("EU 三大报表是年度报告应有项目；当前结果表示解析产物未抽到可校验正式报表，而不是判定公司不存在报表。")
    missing = [
        item
        for item in checks
        if item.get("rule_id", "").startswith("eu.presence.") and item.get("status") in {"fail", "warning"}
    ]
    if missing:
        warnings.append("EU 年报结构化报表覆盖不足；当前表示解析产物未抽取/未定位到正式 IFRS 报表，请优先复核 Financial Statements 附近表格。")

    counts = {"pass": 0, "fail": 0, "warning": 0, "skipped": 0}
    for item in checks:
        counts[item.get("status", "skipped")] = counts.get(item.get("status", "skipped"), 0) + 1
    overall = "fail" if counts.get("fail") else ("warning" if counts.get("warning") else ("pass" if counts.get("pass") else "skipped"))
    return {
        "schema_version": FINANCIAL_CHECKS_SCHEMA_VERSION,
        "rule_version": FINANCIAL_RULE_VERSION,
        "task_id": data.get("task_id"),
        "filename": data.get("filename"),
        "market": "EU",
        "market_profile": "EU",
        "profile_rule_version": EU_PROFILE_RULE_VERSION,
        "accounting_standard": data.get("accounting_standard") or EU_DEFAULT_ACCOUNTING_STANDARD,
        "detected_currencies": data.get("detected_currencies") or [],
        "currency": data.get("currency") or "",
        "unit": data.get("unit") or "",
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
