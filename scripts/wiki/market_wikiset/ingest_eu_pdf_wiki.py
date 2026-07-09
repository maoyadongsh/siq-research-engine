#!/usr/bin/env python3
"""Build EU company Wiki workspaces from standardized PDF parser results.

The output follows the A-share company workspace contract while applying
EU annual-report specific de-duplication, period, currency, and evidence rules.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import Counter, defaultdict
from html import unescape
from pathlib import Path
from typing import Any

from package_facade import write_report_package_facade

from ingest_hk_pdf_wiki import (
    REPO_ROOT,
    DEFAULT_RESULTS_DIR,
    build_pdf_refs,
    evidence_urls,
    now_iso,
    read_json,
    rel,
    safe_slug,
    sha256_file,
    table_by_index,
    write_json,
    write_text,
)


DEFAULT_OUTPUT_ROOT = REPO_ROOT / "data" / "wiki" / "eu"

EU_FILENAME_RE = re.compile(
    r"^(?P<company>.+?)_"
    r"(?P<market>EU)_"
    r"(?P<ticker>[^_]+)_"
    r"(?P<period_end>\d{4}-\d{2}-\d{2})_"
    r"(?P<report_type>[^_]+)_"
    r"(?P<published_at>\d{4}-\d{2}-\d{2})_"
    r"(?P<source_id>.+?)_"
    r"(?P<url_hash>[0-9a-fA-F]{8})"
    r"(?:\.pdf)?$",
    re.IGNORECASE,
)

REPORT_KIND_SLUG = {
    "eu_annual_report": "annual",
    "eu_esef_annual_report": "annual",
    "annual_report": "annual",
    "annual": "annual",
    "年报": "annual",
    "interim_report": "interim",
    "half_year_report": "interim",
    "quarterly_report": "quarterly",
}

PRIMARY_CANONICALS = {
    "balance_sheet": {
        "cash_and_cash_equivalents",
        "current_assets",
        "non_current_assets",
        "total_assets",
        "current_liabilities",
        "non_current_liabilities",
        "total_liabilities",
        "net_assets",
        "parent_equity",
        "nci_equity",
        "total_equity",
        "total_liabilities_and_equity",
    },
    "income_statement": {
        "operating_revenue",
        "total_income",
        "gross_profit",
        "operating_profit",
        "profit_before_tax",
        "income_tax_expense",
        "net_profit",
        "parent_net_profit",
        "minority_profit_loss",
        "total_profit",
        "finance_costs",
    },
    "cash_flow_statement": {
        "operating_cash_flow_net",
        "cash_generated_from_operations",
        "investing_cash_flow_net",
        "financing_cash_flow_net",
        "cash_equivalents_beginning",
        "cash_equivalents_ending",
        "cash_equivalents_net_increase",
    },
}

CANONICAL_ALIASES = {
    "equity_attributable_parent": "parent_equity",
    "minority_interests": "nci_equity",
}

SOURCE_RANK = {
    "issuer_annual_report": 500,
    "exchange_regulatory_news": 450,
    "six_direct": 420,
    "eu_direct": 400,
}

TICKER_COUNTRY = {
    "AD": "NL",
    "AI": "FR",
    "ASML": "NL",
    "AZN": "GB",
    "BARC": "GB",
    "BAS": "DE",
    "BMW": "DE",
    "BN": "FR",
    "BNP": "FR",
    "BP": "GB",
    "CFR": "CH",
    "CS": "FR",
    "DB1": "DE",
    "DG": "FR",
    "DGE": "GB",
    "DSFIR": "CH",
    "DTE": "DE",
    "GEBN": "CH",
    "GIVN": "CH",
    "GLEN": "GB",
    "HEIA": "NL",
    "HOLN": "CH",
    "HSBA": "GB",
    "IFX": "DE",
    "INGA": "NL",
    "LSEG": "GB",
    "MBG": "DE",
    "MC": "FR",
    "MUV2": "DE",
    "NOVN": "CH",
    "OR": "FR",
    "PHIA": "NL",
    "PRX": "NL",
    "REL": "GB",
    "REN": "NL",
    "RIO": "GB",
    "SAN": "FR",
    "SAP": "DE",
    "SHEL": "GB",
    "SHELL": "GB",
    "SIE": "DE",
    "SIKA": "CH",
    "SREN": "CH",
    "TTE": "FR",
    "VOW3": "DE",
    "ZURN": "CH",
}

COUNTRY_EXCHANGE = {
    "DE": "XETRA",
    "FR": "Euronext Paris",
    "GB": "LSE",
    "NL": "Euronext Amsterdam",
    "CH": "SIX",
}

COUNTRY_CURRENCY = {
    "DE": "EUR",
    "FR": "EUR",
    "GB": "GBP",
    "NL": "EUR",
    "CH": "CHF",
}

CONTEXT_LIMIT = 900
PRIMARY_STATEMENT_TYPES = {"balance_sheet", "income_statement", "cash_flow_statement"}

FORMAL_STATEMENT_MARKERS = {
    "balance_sheet": (
        "consolidated statement of financial position",
        "consolidated statements of financial position",
        "condensed consolidated statement of financial position",
        "consolidated balance sheet",
        "consolidated balance sheets",
        "consolidated statement of financial condition",
        "group balance sheet",
        "balance sheet for group",
    ),
    "income_statement": (
        "consolidated income statement",
        "consolidated income statements",
        "consolidated statement of income",
        "consolidated statements of income",
        "consolidated statement of profit or loss",
        "consolidated statements of profit or loss",
        "consolidated statement of operations",
        "consolidated statements of operations",
        "group income statement",
        "income statement for group",
    ),
    "cash_flow_statement": (
        "consolidated cash flow statement",
        "consolidated cash flow statements",
        "consolidated statement of cash flows",
        "consolidated statements of cash flows",
        "group cash flow statement",
        "cash flow statement for group",
    ),
}

SCOPED_FORMAL_STATEMENT_MARKERS = {
    "balance_sheet": (
        "statement of financial position",
        "statements of financial position",
        "balance sheet",
    ),
    "income_statement": (
        "income statement",
        "income statements",
        "statement of income",
        "statements of income",
        "statement of profit or loss",
        "statements of profit or loss",
        "statement of operations",
        "statements of operations",
    ),
    "cash_flow_statement": (
        "statement of cash flows",
        "statements of cash flows",
        "cash flow statement",
        "cash flow statements",
    ),
}

PARENT_OR_SEPARATE_PATTERNS = (
    r"\bparent company financial statements\b",
    r"\bseparate financial statements\b",
    r"\bcompany financial statements\b",
    r"\bcompany balance sheet\b",
    r"\bcompany income statement\b",
    r"\bcompany cash flow statement\b",
    r"\bbalance sheet structure of [a-z0-9 .,&'’\-]+ company\b",
    r"\bcondensed income statement for [a-z0-9 .,&'’\-]+ company\b",
    r"\bincome statement for [a-z0-9 .,&'’\-]+ company\b",
    r"\bcash flow statement for [a-z0-9 .,&'’\-]+ company\b",
    r"\bstatement of income of [a-z0-9 .,&'’\-]+ ag\b",
    r"\bstatement of cash flows of [a-z0-9 .,&'’\-]+ ag\b",
    r"\bbalance sheet of [a-z0-9 .,&'’\-]+ ag\b",
    r"\bunder german gaap\b",
    r"\bgerman commercial code\b",
    r"\bhgb\b",
)

WEAK_PARENT_OR_SEPARATE_PATTERNS = (
    r"\bunder german gaap\b",
    r"\bgerman commercial code\b",
    r"\bhgb\b",
)

GROUP_AND_SEGMENT_MARKERS = (
    "group and segments",
    "for group and segments",
    "group and segment",
    "by division",
)

NON_IFRS_MARKERS = (
    "alternative performance measure",
    "alternative performance measures",
    "non-ifrs",
    "non ifrs",
    "non gaap",
    "non-gaap",
    "adjusted ebitda",
    "adjusted ebit",
    "adjusted earnings",
    "adjusted income",
    "adjusted operating profit",
    "adjusted profit",
    "core net income",
    "underlying profit",
    "underlying income",
)

NON_FINANCIAL_MARKERS = (
    "sustainability",
    "emissions",
    "scope 1",
    "scope 2",
    "remuneration",
    "glossary",
    "legal proceedings",
)

SELECTED_SUMMARY_MARKERS = (
    "five-year summary",
    "five year summary",
    "selected financial",
    "key figures",
    "highlights",
)

FALLBACK_METRIC_PATTERNS = {
    "balance_sheet": (
        ("total_liabilities_and_equity", (r"\btotal (?:liabilities|equity) and (?:equity|liabilities)\b",)),
        ("cash_and_cash_equivalents", (r"\bcash and cash equivalents\b", r"\bcash equivalents\b")),
        ("non_current_assets", (r"\bnon[- ]current assets\b", r"\bnon current assets\b")),
        ("current_assets", (r"\bcurrent assets\b",)),
        ("total_assets", (r"\btotal assets\b", r"\btotal actif\b", r"\bsumme aktiva\b")),
        ("non_current_liabilities", (r"\bnon[- ]current liabilities\b", r"\bnon current liabilities\b")),
        ("current_liabilities", (r"\bcurrent liabilities\b",)),
        ("total_liabilities", (r"\btotal liabilities\b",)),
        ("parent_equity", (r"\bequity attributable to (?:shareholders|owners|equity holders) of (?:the )?(?:parent|company)\b", r"\bshareholders'? equity\b", r"\bequity - group share\b")),
        ("nci_equity", (r"\bnon[- ]controlling interests\b", r"\bminority interests\b")),
        ("total_equity", (r"\btotal equity\b", r"\btotal shareholders'? equity\b")),
        ("net_assets", (r"\bnet assets\b",)),
    ),
    "income_statement": (
        ("minority_profit_loss", (r"\bnon[- ]controlling interests\b", r"\bminority interests\b")),
        ("parent_net_profit", (r"\bnet (?:income|profit|loss) attributable to (?:shareholders|owners|equity holders)\b", r"\bnet (?:income|profit|loss) - group share\b")),
        ("profit_before_tax", (r"\bprofit before tax(?:es)?\b", r"\bincome before tax(?:es)?\b", r"\bearnings before tax(?:es)?\b")),
        ("income_tax_expense", (r"\bincome tax(?:es)?(?: expense| benefit)?\b", r"\btax expense\b")),
        ("operating_profit", (r"\boperating (?:income|profit|loss)\b", r"\bprofit from operations\b")),
        ("gross_profit", (r"\bgross profit\b",)),
        ("finance_costs", (r"\bfinance costs?\b", r"\bfinancial expenses?\b", r"\binterest expense\b")),
        ("operating_revenue", (r"\brevenue\b", r"\bsales\b", r"\bnet sales\b", r"\bturnover\b")),
        ("total_income", (r"\btotal income\b",)),
        ("net_profit", (r"\bnet (?:income|profit|loss)\b", r"\bincome for the period\b", r"\bprofit for the year\b")),
    ),
    "cash_flow_statement": (
        ("operating_cash_flow_net", (r"\bnet cash (?:provided by|used in|from) operating activities\b", r"\bnet cash provided/used by operating activities\b", r"\bnet cash flows? from operating activities\b", r"\bcash flows? provided by operating activities\b", r"\bcash flows? from operating activities\b", r"\bcash flow from operating activities\b", r"\bcash flows? provided by used in operating activities\b", r"\bnet increase (?:decrease )?in cash and cash equivalents generated by operating activities\b")),
        ("cash_generated_from_operations", (r"\bcash generated from operations\b", r"\bcash generated by operations\b", r"\bbefore changes in (?:net )?working capital\b")),
        ("investing_cash_flow_net", (r"\bnet cash (?:provided by|used in|from) investing activities\b", r"\bnet cash provided/used by investing activities\b", r"\bnet cash flows? from investing activities\b", r"\bcash flows? from investing activities\b", r"\bcash flows? provided by investment activities\b", r"\bcash flows? used in investment activities\b", r"\bcash flows? provided by used in investment activities\b", r"\bcash flows? provided by used in investing activities\b", r"\bnet decrease in cash and cash equivalents related to investing activities\b")),
        ("financing_cash_flow_net", (r"\bnet cash (?:provided by|used in|from) financing activities\b", r"\bnet cash provided/used by financing activities\b", r"\bnet cash flows? from financing activities\b", r"\bcash flows? from financing activities\b", r"\bcash inflow outflow from financing activities\b", r"\bcash flows? provided by financing activities\b", r"\bcash flows? used in financing activities\b", r"\bcash flows? provided by used in financing activities\b", r"\bnet decrease in cash and cash equivalents related to financing activities\b")),
        ("cash_equivalents_net_increase", (r"\bnet (?:increase|decrease).{0,80}cash (?:and cash equivalents)?\b", r"\bincrease decrease in cash\b", r"\bchange in cash and cash equivalents\b", r"\btotal net cash provided used\b")),
        ("cash_equivalents_beginning", (r"\bcash and cash equivalents (?:at|as of)? (?:the )?beginning\b", r"\bcash and cash equivalents as of 1 january\b", r"\bcash and cash equivalents, beginning of year\b", r"\bbalance of cash and cash equivalent accounts at the start\b", r"\bbeginning cash and cash equivalents\b", r"\bnet cash as of january 1\b")),
        ("cash_equivalents_ending", (r"\bcash and cash equivalents (?:at|as of)? (?:the )?end\b", r"\bcash and cash equivalents as of 31 december\b", r"\bcash and cash equivalents, end of year\b", r"\bbalance of cash and cash equivalent accounts at the end\b", r"\bending cash and cash equivalents\b", r"\bcash and cash equivalents at december 31\b", r"\bnet cash as of december 31\b")),
    ),
}


def clean_company_name(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[-_]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_eu_filename(filename: Any) -> dict[str, str]:
    stem = Path(str(filename or "")).name
    stem = re.sub(r"\.pdf$", "", stem, flags=re.IGNORECASE)
    match = EU_FILENAME_RE.match(stem)
    if not match:
        return {}
    data = {key: str(value or "").strip() for key, value in match.groupdict().items()}
    data["company_name"] = clean_company_name(data.get("company"))
    data["source_filename"] = Path(str(filename or "")).name
    data["filename_pattern"] = "<company>_EU_<ticker>_<period_end>_<report_type>_<published_at>_<source_id>_<url_hash>.pdf"
    return data


def valid_year(value: Any) -> int | None:
    try:
        year = int(value)
    except Exception:
        return None
    if 1990 <= year <= 2100:
        return year
    return None


def report_year_from_period(period_end: Any, *fallback: Any) -> int | None:
    for value in (period_end, *fallback):
        match = re.search(r"(20\d{2}|19\d{2})", str(value or ""))
        if match:
            return int(match.group(1))
    return None


def prior_year_period(period: str) -> str:
    match = re.match(r"^(\d{4})(-\d{2}-\d{2})$", str(period or ""))
    if not match:
        return ""
    return f"{int(match.group(1)) - 1}{match.group(2)}"


def preferred_periods(row: dict[str, Any]) -> set[str]:
    current = str(row.get("period_end") or "").strip()
    periods = {current} if current else set()
    prior = prior_year_period(current)
    if prior:
        periods.add(prior)
    return periods


def normalize_period_key(period: Any, row: dict[str, Any]) -> str:
    text = str(period or "").strip()
    current = str(row.get("period_end") or "").strip()
    if not text or not current:
        return text
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        current_year = report_year_from_period(current)
        raw_year = report_year_from_period(text)
        if (
            current_year
            and raw_year
            and abs(current_year - raw_year) <= 5
            and current[4:] != "-12-31"
            and text[4:] != current[4:]
        ):
            return f"{raw_year:04d}{current[4:]}"
        return text
    year_match = re.match(r"^(20\d{2}|19\d{2})$", text)
    current_year = report_year_from_period(current)
    if year_match and current_year:
        year = int(year_match.group(1))
        if abs(current_year - year) <= 5:
            return f"{year:04d}{current[4:]}"
    return text


def report_kind_slug(report_kind: Any, report_type: Any) -> str:
    key = str(report_kind or report_type or "eu_annual_report").strip()
    return REPORT_KIND_SLUG.get(key, safe_slug(key.lower(), "report"))


def canonical_name(value: Any) -> str:
    text = str(value or "").strip()
    text = CANONICAL_ALIASES.get(text, text)
    return text


def canonical_name_for_item(statement_type: str, item: dict[str, Any]) -> str:
    label = item.get("name") or item.get("local_name") or item.get("label") or ""
    inferred = fallback_metric_key(statement_type, label)
    canonical = canonical_name(item.get("canonical_name"))
    if inferred and statement_type in PRIMARY_CANONICALS and inferred in PRIMARY_CANONICALS[statement_type]:
        return inferred
    return canonical


def eu_to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("\u2212", "-").replace("\u2013", "-").replace("\u2014", "-")
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
    if re.search(r"^\s*-\s*", text):
        negative = True
    text = text.strip("()[] ")
    text = text.replace("$", "").replace("€", "").replace("£", "").replace("CHF", "")
    text = re.sub(r"\s+", "", text)
    text = text.replace(",", "")
    text = re.sub(r"[^0-9.\-]", "", text)
    if text in {"", "-", ".", "-."}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if negative and number > 0:
        return -number
    return number


def infer_country(ticker: str, company_name: str, metadata: dict[str, Any], parsed: dict[str, str]) -> str:
    for value in (metadata.get("country"), metadata.get("jurisdiction")):
        if value:
            return str(value).upper()
    ticker = str(ticker or "").upper()
    if ticker in TICKER_COUNTRY:
        return TICKER_COUNTRY[ticker]
    name = str(company_name or parsed.get("company_name") or "")
    if re.search(r"\bPLC\b|p\.l\.c", name, flags=re.IGNORECASE):
        return "GB"
    if re.search(r"\bN\.V\b|\bN\.V\.", name, flags=re.IGNORECASE):
        return "NL"
    if re.search(r"\bSE\b|S\.A|S\.A\.", name):
        return "FR"
    return "EU"


def source_metadata(meta: dict[str, Any], filename: str) -> dict[str, Any]:
    parsed = parse_eu_filename(filename)
    ticker = parsed.get("ticker") or meta.get("ticker") or meta.get("stock_code")
    company_name = parsed.get("company_name") or meta.get("company_name")
    country = infer_country(str(ticker or ""), str(company_name or ""), meta, parsed)
    return {
        key: value
        for key, value in {
            "source_filename": filename,
            "filename_pattern": parsed.get("filename_pattern"),
            "company_short_name": company_name,
            "market": "EU",
            "country": country,
            "stock_code": ticker,
            "raw_ticker": ticker,
            "report_end": parsed.get("period_end") or meta.get("period_end"),
            "report_type": parsed.get("report_type") or meta.get("report_type"),
            "published_at": parsed.get("published_at") or meta.get("disclosure_date"),
            "source_id": parsed.get("source_id") or meta.get("source"),
            "url_hash": parsed.get("url_hash"),
            "source": "eu_report_finder_filename" if parsed else meta.get("source"),
        }.items()
        if value
    }


def compact(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower().replace("-", "")


def table_context(table: dict[str, Any], statement: dict[str, Any] | None = None, item: dict[str, Any] | None = None) -> str:
    parts = [
        (statement or {}).get("title"),
        (statement or {}).get("statement_name"),
        table.get("near_text"),
        table.get("heading"),
        table.get("preview"),
        (item or {}).get("name"),
        (item or {}).get("label"),
    ]
    return re.sub(r"\s+", " ", " ".join(str(part or "") for part in parts if part)).strip()


def has_formal_statement_marker(statement_type: str, text: str) -> bool:
    flat = compact(text)
    return any(compact(token) in flat for token in FORMAL_STATEMENT_MARKERS.get(statement_type, ()))


def has_scoped_statement_marker(statement_type: str, text: str) -> bool:
    flat = compact(text)
    return any(compact(token) in flat for token in SCOPED_FORMAL_STATEMENT_MARKERS.get(statement_type, ()))


def is_parent_or_separate_context(text: str) -> bool:
    lower = str(text or "").lower()
    return any(re.search(pattern, lower) for pattern in PARENT_OR_SEPARATE_PATTERNS)


def is_strong_parent_or_separate_context(text: str) -> bool:
    lower = str(text or "").lower()
    return any(
        re.search(pattern, lower)
        for pattern in PARENT_OR_SEPARATE_PATTERNS
        if pattern not in WEAK_PARENT_OR_SEPARATE_PATTERNS
    )


def is_group_and_segment_statement(text: str) -> bool:
    lower = str(text or "").lower()
    return any(token in lower for token in GROUP_AND_SEGMENT_MARKERS)


def is_non_ifrs_context(text: str) -> bool:
    lower = str(text or "").lower()
    return any(token in lower for token in NON_IFRS_MARKERS)


def fallback_metric_key(statement_type: str, label: Any) -> str:
    lower = re.sub(r"\s+", " ", unescape(str(label or "")).lower()).strip()
    lower = re.sub(r"\([^)]*\)", " ", lower)
    lower = re.sub(r"\[[^]]*\]", " ", lower)
    lower = re.sub(r"\bnote[s]?\b\.?", " ", lower)
    lower = lower.replace("/", " ")
    lower = re.sub(r"\s+", " ", lower).strip()
    if not lower:
        return ""
    if lower.startswith(("of which", "therein")):
        return ""
    if statement_type == "balance_sheet" and any(token in lower for token in ("held for sale", "related to non-current assets")):
        return ""
    if any(token in lower for token in ("cost of sales", "other operating income", "other operating expenses", "income tax effects", "net of income tax", "net of income taxes", "before minority interests", "before non-controlling interests")):
        return ""
    if statement_type == "income_statement" and any(token in lower for token in ("per share", "eps", "weighted average")):
        return ""
    if statement_type == "income_statement" and any(token in lower for token in ("share of net profit", "share of profit", "share of result", "associates and joint ventures")):
        return ""
    if (
        statement_type == "income_statement"
        and any(token in lower for token in ("continuing operations", "discontinued operations"))
        and re.search(r"\bnet (?:profit|income|loss)\b|\bprofit\b|\bincome\b", lower)
    ):
        return ""
    if statement_type == "income_statement" and re.search(r"\b(?:profit|income|earnings).{0,80}before.{0,40}tax", lower):
        return "profit_before_tax"
    if statement_type == "cash_flow_statement" and any(token in lower for token in ("free cash flow", "capital expenditure", "dividend", "lease payment")):
        return ""
    for canonical, patterns in FALLBACK_METRIC_PATTERNS.get(statement_type, ()):
        if any(re.search(pattern, lower) for pattern in patterns):
            return canonical
    return ""


def fallback_metric_label_priority(statement_type: str, canonical: str, label: Any) -> int:
    lower = re.sub(r"\s+", " ", unescape(str(label or "")).lower()).strip()
    lower = re.sub(r"\([^)]*\)", " ", lower)
    lower = lower.replace("/", " ")
    lower = re.sub(r"\s+", " ", lower).strip()
    if statement_type == "cash_flow_statement":
        if canonical == "operating_cash_flow_net":
            if "before changes in" in lower or "before changes in net working capital" in lower:
                return 20
            if re.search(r"\bcash flows? (?:provided by |from )?operating activities\b", lower) or "generated by operating activities" in lower:
                return 100
        if canonical in {"investing_cash_flow_net", "financing_cash_flow_net", "cash_equivalents_net_increase"}:
            return 80
    if statement_type == "income_statement" and canonical == "net_profit":
        if "for the period" in lower or "for the year" in lower:
            return 100
        if "continuing operations" in lower or "discontinued operations" in lower:
            return 20
    return 50


def markdown_lines(row: dict[str, Any]) -> list[str]:
    cached = row.get("_markdown_lines")
    if isinstance(cached, list):
        return cached
    path = row["result_dir"] / "result_complete.md"
    lines = path.read_text(errors="ignore").splitlines() if path.exists() else []
    row["_markdown_lines"] = lines
    return lines


def markdown_table_body_near_line(row: dict[str, Any], line_number: Any, window: int = 4) -> str:
    try:
        center = int(line_number) - 1
    except Exception:
        return ""
    lines = markdown_lines(row)
    if not lines:
        return ""
    indexes = []
    for offset in range(window + 1):
        if offset == 0:
            indexes.append(center)
        else:
            indexes.extend([center - offset, center + offset])
    for index in indexes:
        if index < 0 or index >= len(lines):
            continue
        line = lines[index]
        if "<table" not in line.lower():
            continue
        tail = "\n".join(lines[index : min(len(lines), index + 8)])
        start = tail.lower().find("<table")
        end = tail.lower().find("</table>", start)
        if start >= 0 and end >= 0:
            return tail[start : end + len("</table>")]
        return line[line.lower().find("<table") :]
    return ""


def markdown_prelude_near_line(row: dict[str, Any], line_number: Any, back: int = 16) -> str:
    try:
        center = int(line_number) - 1
    except Exception:
        return ""
    lines = markdown_lines(row)
    if not lines:
        return ""
    parts = []
    for index in range(max(0, center - back), min(len(lines), center)):
        line = re.sub(r"\s+", " ", lines[index]).strip()
        if not line or "<table" in line.lower() or line.startswith("![]("):
            continue
        if len(line) > 260:
            continue
        parts.append(line.strip("# "))
    return re.sub(r"\s+", " ", " ".join(parts[-4:])).strip()


def html_table_rows(html: Any) -> list[list[str]]:
    text = str(html or "")
    if "<tr" not in text.lower():
        return []
    rows: list[list[str]] = []
    rowspan_slots: dict[int, int] = {}
    for row_html in re.findall(r"<tr\b[^>]*>(.*?)</tr>", text, flags=re.IGNORECASE | re.DOTALL):
        cells = []
        column_index = 0
        def consume_rowspan_slots() -> None:
            nonlocal column_index
            while rowspan_slots.get(column_index, 0) > 0:
                cells.append("")
                rowspan_slots[column_index] -= 1
                if rowspan_slots[column_index] <= 0:
                    rowspan_slots.pop(column_index, None)
                column_index += 1

        for attrs, cell_html in re.findall(r"<t[dh]\b([^>]*)>(.*?)</t[dh]>", row_html, flags=re.IGNORECASE | re.DOTALL):
            consume_rowspan_slots()
            cell_text = re.sub(r"<br\s*/?>", " ", cell_html, flags=re.IGNORECASE)
            cell_text = re.sub(r"<[^>]+>", " ", cell_text)
            cell_text = unescape(cell_text).replace("\xa0", " ")
            cell_text = re.sub(r"\s+", " ", cell_text).strip()
            colspan_match = re.search(r"\bcolspan=[\"']?(\d+)", attrs or "", flags=re.IGNORECASE)
            rowspan_match = re.search(r"\browspan=[\"']?(\d+)", attrs or "", flags=re.IGNORECASE)
            colspan = max(1, int(colspan_match.group(1))) if colspan_match else 1
            rowspan = max(1, int(rowspan_match.group(1))) if rowspan_match else 1
            if rowspan > 1:
                for offset in range(colspan):
                    rowspan_slots[column_index + offset] = max(rowspan_slots.get(column_index + offset, 0), rowspan - 1)
            cells.append(cell_text)
            cells.extend("" for _ in range(colspan - 1))
            column_index += colspan
        consume_rowspan_slots()
        if cells:
            rows.append(cells)
    return rows


def compact_header_cell(cell: Any) -> str:
    return re.sub(r"\s+", " ", str(cell or "").strip()).lower()


def table_body_by_content_source_id(row: dict[str, Any], content_source_id: Any) -> str:
    try:
        source_id = int(content_source_id)
    except Exception:
        return ""
    if source_id <= 0:
        return ""
    content_list = row.get("document_full", {}).get("content_list") if isinstance(row.get("document_full"), dict) else []
    table_items = [item for item in (content_list or []) if isinstance(item, dict) and item.get("type") == "table"]
    if source_id <= len(table_items):
        return str(table_items[source_id - 1].get("table_body") or "")
    return ""


def fallback_table_body(row: dict[str, Any], table: dict[str, Any]) -> str:
    markdown_body = markdown_table_body_near_line(row, table.get("line"))
    if markdown_body:
        return markdown_body
    return table_body_by_content_source_id(row, table.get("content_table_source_id") or table.get("table_index"))


def fallback_period_columns(rows: list[list[str]], row: dict[str, Any]) -> dict[int, str]:
    period_columns: dict[int, str] = {}
    current_year = report_year_from_period(row.get("period_end"))
    allowed = preferred_periods(row)
    header_rows = rows[:3]
    for header in header_rows:
        for index, cell in enumerate(header):
            year_match = re.search(r"(20\d{2}|19\d{2})", cell)
            if not year_match:
                continue
            period = normalize_period_key(year_match.group(1), row)
            if not allowed or period in allowed:
                period_columns[index] = period
            if current_year and index + 1 < len(header) and not str(header[index + 1] or "").strip():
                period_columns.setdefault(index + 1, period)
    if period_columns:
        prior = prior_year_period(str(row.get("period_end") or ""))
        if prior:
            for header in header_rows:
                for index, cell in enumerate(header):
                    if compact_header_cell(cell) in {"prev. year", "previous year", "prior year", "comparative year"}:
                        period_columns[index] = prior
        return period_columns
    if current_year:
        prior = prior_year_period(str(row.get("period_end") or ""))
        for header in header_rows:
            for index, cell in enumerate(header):
                label = compact_header_cell(cell)
                if re.match(r"^(?:20\d{2}|19\d{2})$", label):
                    period_columns[index] = normalize_period_key(label, row)
                elif label in {"prev. year", "previous year", "prior year", "comparative year"} and prior:
                    period_columns[index] = prior
        if period_columns:
            return period_columns
        for header in header_rows:
            numeric_indexes = [index for index, cell in enumerate(header) if re.search(r"\b(?:20\d{2}|19\d{2})\b", cell)]
            if len(numeric_indexes) >= 2:
                period_columns[numeric_indexes[-1]] = str(row.get("period_end") or f"{current_year}-12-31")
                if prior:
                    period_columns[numeric_indexes[-2]] = prior
                return period_columns
    return period_columns


def fallback_table_context(table: dict[str, Any], row: dict[str, Any] | None = None) -> str:
    parts = [
        table.get("heading"),
        " ".join(str(item) for item in (table.get("source_caption") or [])),
        markdown_prelude_near_line(row, table.get("line")) if row else "",
        table.get("near_text"),
        table.get("preview"),
    ]
    return re.sub(r"\s+", " ", " ".join(str(part or "") for part in parts if part)).strip()


def fallback_table_title_context(table: dict[str, Any], row: dict[str, Any] | None = None) -> str:
    parts = [
        table.get("heading"),
        " ".join(str(item) for item in (table.get("source_caption") or [])),
        markdown_prelude_near_line(row, table.get("line")) if row else "",
    ]
    return re.sub(r"\s+", " ", " ".join(str(part or "") for part in parts if part)).strip()


def fallback_table_heading_context(table: dict[str, Any]) -> str:
    parts = [
        table.get("heading"),
        " ".join(str(item) for item in (table.get("source_caption") or [])),
    ]
    return re.sub(r"\s+", " ", " ".join(str(part or "") for part in parts if part)).strip()


def table_is_fallback_candidate(statement_type: str, table: dict[str, Any], row: dict[str, Any] | None = None) -> bool:
    context = fallback_table_context(table, row)
    title_context = fallback_table_title_context(table, row)
    heading_context = fallback_table_heading_context(table)
    lower = context.lower()
    title_lower = title_context.lower()
    if "changes in equity" in title_lower or "comprehensive income" in title_lower:
        return False
    heading_has_statement = has_formal_statement_marker(statement_type, heading_context) or has_scoped_statement_marker(statement_type, heading_context)
    title_has_statement = has_formal_statement_marker(statement_type, title_context) or has_scoped_statement_marker(statement_type, title_context)
    if heading_has_statement:
        return True
    for other_statement_type in PRIMARY_STATEMENT_TYPES - {statement_type}:
        if has_formal_statement_marker(other_statement_type, heading_context) or has_scoped_statement_marker(other_statement_type, heading_context):
            return False
    if is_strong_parent_or_separate_context(title_context) or (is_parent_or_separate_context(context) and not title_has_statement):
        return False
    if any(token in lower for token in ("reconciliation of segment", "segment revenue", "operating segments", "notes on the consolidated income statement", "notes to the consolidated income statement")):
        return False
    if re.search(r"\btable\s+\d", str(table.get("heading") or "").lower()):
        return False
    if is_non_ifrs_context(context) and not has_formal_statement_marker(statement_type, context):
        return False
    if statement_type == "cash_flow_statement" and "free cash flow" in lower and not has_formal_statement_marker(statement_type, context):
        return False
    if any(token in lower for token in ("contents", "table of contents")):
        if not has_formal_statement_marker(statement_type, title_context):
            return False
    if title_has_statement:
        return True
    if statement_type == "cash_flow_statement" and any(token in lower for token in ("cash flows from operating activities", "net cash provided/used by operating activities")):
        return True
    return False


def fallback_candidate_score(row: dict[str, Any], statement_type: str, table: dict[str, Any]) -> int:
    context = fallback_table_context(table, row)
    title_context = fallback_table_title_context(table, row)
    heading_context = fallback_table_heading_context(table)
    score = 0
    if has_formal_statement_marker(statement_type, heading_context):
        score += 1200
    elif has_scoped_statement_marker(statement_type, heading_context):
        score += 940
    elif has_formal_statement_marker(statement_type, title_context):
        score += 1000
    elif has_scoped_statement_marker(statement_type, title_context):
        score += 820
    elif statement_type == "cash_flow_statement" and "cash flows from operating activities" in context.lower():
        score += 650
    if is_group_and_segment_statement(title_context):
        score -= 120
    if any(token in title_context.lower() for token in SELECTED_SUMMARY_MARKERS):
        score -= 180
    score += min(int(table.get("numeric_cells") or 0), 200)
    line = int(table.get("line") or 0)
    if line:
        score -= min(line // 10000, 5)
    return score


def source_role(statement_type: str, context: str) -> str:
    lower = str(context or "").lower()
    is_formal = has_formal_statement_marker(statement_type, context)
    if is_parent_or_separate_context(context):
        return "parent_or_separate_statement"
    if is_formal and is_non_ifrs_context(context):
        return "non_ifrs_or_adjusted_table"
    if is_formal and is_group_and_segment_statement(context):
        return "group_and_segment_statement"
    if is_formal:
        return "formal_consolidated_statement"
    if any(token in lower for token in ("segment", "geographical information", "by business area", "by operating segment")):
        return "segment_table"
    if is_non_ifrs_context(context):
        return "non_ifrs_or_adjusted_table"
    if any(token in lower for token in NON_FINANCIAL_MARKERS):
        return "non_financial_table"
    if any(token in lower for token in ("earnings per share", "basic eps", "diluted eps")):
        return "eps_or_per_share_note"
    if any(token in lower for token in SELECTED_SUMMARY_MARKERS):
        return "selected_financial_summary"
    if "note " in lower or lower.startswith("notes ") or "notes to the" in lower:
        return "financial_note_table"
    if "consolidated" in lower:
        return "consolidated_context"
    return "financial_data_consolidated"


def allowed_primary_source_role(role: str) -> bool:
    return role not in {
        "parent_or_separate_statement",
        "segment_table",
        "non_ifrs_or_adjusted_table",
        "non_financial_table",
        "eps_or_per_share_note",
        "financial_note_table",
    }


def source_priority(role: str, statement_type: str, context: str) -> int:
    scores = {
        "formal_consolidated_statement": 900,
        "group_and_segment_statement": 760,
        "consolidated_context": 650,
        "financial_data_consolidated": 520,
        "selected_financial_summary": 300,
    }
    score = scores.get(role, -1000)
    lower = str(context or "").lower()
    if statement_type == "balance_sheet" and any(token in lower for token in ("financial position", "balance sheet")):
        score += 50
    if statement_type == "income_statement" and any(token in lower for token in ("income statement", "profit or loss", "operations")):
        score += 50
    if statement_type == "cash_flow_statement" and "cash flow" in lower:
        score += 50
    return score


def source_from_item(
    row: dict[str, Any],
    statement: dict[str, Any],
    item: dict[str, Any],
    period_key: str,
    source_info: dict[str, Any],
) -> dict[str, Any]:
    table_index = source_info.get("table_index")
    table = table_by_index(table_index, row["table_index"])
    md_line = source_info.get("line") or table.get("line")
    page = source_info.get("pdf_page_number") or table.get("pdf_page_number")
    context = table_context(table, statement, item)
    role = source_role(str(statement.get("statement_type") or ""), context)
    source = {
        "source_type": "eu_financial_data_values",
        "source_id": f"eu_table_{table_index}" if table_index else None,
        "quote_text": None,
        "md_line": md_line,
        "pdf_page_number": page,
        "table_index": table_index,
        "row_index": source_info.get("row_index"),
        "column_index": source_info.get("column_index"),
        "heading": table.get("heading"),
        "statement_title": statement.get("title") or statement.get("statement_name"),
        "source_context": context[:CONTEXT_LIMIT],
        "source_role": role,
        "source_kind": "financial_data_values",
        "markdown_path": rel(row["result_dir"] / "result_complete.md"),
        "task_id": row["task_id"],
        "period": period_key,
    }
    source.update(evidence_urls(row["task_id"], source.get("pdf_page_number"), source.get("table_index")))
    return source


def fallback_source_from_table(row: dict[str, Any], table: dict[str, Any], statement_type: str, period_key: str) -> dict[str, Any]:
    context = fallback_table_context(table, row)
    role = source_role(statement_type, context)
    if table_is_fallback_candidate(statement_type, table, row) or table.get("_formal_fragment_statement_type") == statement_type:
        role = "formal_consolidated_statement" if not is_group_and_segment_statement(context) else "group_and_segment_statement"
    source = {
        "source_type": "eu_table_body_fallback",
        "source_id": f"eu_table_{table.get('table_index')}" if table.get("table_index") else None,
        "quote_text": None,
        "md_line": table.get("line"),
        "pdf_page_number": table.get("pdf_page_number"),
        "table_index": table.get("table_index"),
        "row_index": None,
        "column_index": None,
        "heading": table.get("heading"),
        "statement_title": table.get("heading") or " ".join(str(item) for item in (table.get("source_caption") or [])),
        "source_context": context[:CONTEXT_LIMIT],
        "source_role": role,
        "source_kind": "table_body_fallback",
        "markdown_path": rel(row["result_dir"] / "result_complete.md"),
        "task_id": row["task_id"],
        "period": period_key,
    }
    source.update(evidence_urls(row["task_id"], source.get("pdf_page_number"), source.get("table_index")))
    return source


def fallback_metrics_from_table(
    row: dict[str, Any],
    statement_type: str,
    table: dict[str, Any],
    *,
    require_candidate: bool = True,
) -> list[dict[str, Any]]:
    if require_candidate and not table_is_fallback_candidate(statement_type, table, row):
        return []
    body = fallback_table_body(row, table)
    rows = html_table_rows(body)
    if len(rows) < 2:
        return []
    period_columns = fallback_period_columns(rows, row)
    if not period_columns:
        return []
    metrics: list[dict[str, Any]] = []
    metrics_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    priorities_by_key: dict[tuple[str, str], int] = {}
    for row_index, cells in enumerate(rows):
        if len(cells) < 2:
            continue
        label = cells[0]
        canonical = fallback_metric_key(statement_type, label)
        if not canonical:
            continue
        for column_index, period in period_columns.items():
            if column_index >= len(cells):
                continue
            raw_value = cells[column_index]
            value = eu_to_float(raw_value)
            if value is None:
                continue
            key = (canonical, period)
            source = fallback_source_from_table(row, table, statement_type, period)
            if not allowed_primary_source_role(str(source.get("source_role") or "")):
                continue
            source["row_index"] = row_index
            source["column_index"] = column_index
            metric = {
                "metric_key": canonical,
                "metric_name": label,
                "canonical_name": canonical,
                "local_name": label,
                "raw_value": raw_value,
                "value": value,
                "unit": table.get("unit") or "",
                "currency": row.get("currency"),
                "scale": "1",
                "confidence": None,
                "statement_type": statement_type,
                "scope": "consolidated",
                "period": period,
                "fiscal_year": report_year_from_period(period),
                "source": source,
                "_source_priority": source_priority(str(source.get("source_role") or ""), statement_type, str(source.get("source_context") or "")) - 40,
            }
            priority = fallback_metric_label_priority(statement_type, canonical, label)
            previous_priority = priorities_by_key.get(key, -1)
            if key not in metrics_by_key or priority > previous_priority:
                metrics_by_key[key] = metric
                priorities_by_key[key] = priority
    return list(metrics_by_key.values())


def int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def table_has_conflicting_statement_marker(statement_type: str, table: dict[str, Any]) -> bool:
    heading_context = fallback_table_heading_context(table)
    title_lower = heading_context.lower()
    if "changes in equity" in title_lower or "comprehensive income" in title_lower:
        return True
    return any(
        has_formal_statement_marker(other_statement_type, heading_context)
        or has_scoped_statement_marker(other_statement_type, heading_context)
        for other_statement_type in PRIMARY_STATEMENT_TYPES - {statement_type}
    )


def is_adjacent_table_fragment(
    row: dict[str, Any],
    statement_type: str,
    base_table: dict[str, Any],
    table: dict[str, Any],
) -> bool:
    base_index = int_or_none(base_table.get("table_index"))
    table_index = int_or_none(table.get("table_index"))
    base_line = int_or_none(base_table.get("line"))
    table_line = int_or_none(table.get("line"))
    adjacent_index = base_index is not None and table_index is not None and 0 < abs(table_index - base_index) <= 1
    adjacent_line = base_line is not None and table_line is not None and 0 < abs(table_line - base_line) <= 10
    if not adjacent_index and not adjacent_line:
        return False
    context = fallback_table_context(table, row)
    lower = context.lower()
    if is_strong_parent_or_separate_context(context) or table_has_conflicting_statement_marker(statement_type, table):
        return False
    if is_non_ifrs_context(context):
        return False
    if any(token in lower for token in ("notes on the", "notes to the", "earnings per share", "free cash flow")):
        return False
    return True


def adjacent_table_fragments(
    row: dict[str, Any],
    statement_type: str,
    base_table: dict[str, Any],
) -> list[dict[str, Any]]:
    fragments = sorted(
        (
            table
            for table in row.get("table_index") or []
            if table is not base_table and is_adjacent_table_fragment(row, statement_type, base_table, table)
        ),
        key=lambda table: int_or_none(table.get("table_index")) or 0,
    )
    for table in fragments:
        table["_formal_fragment_statement_type"] = statement_type
    return fragments


def merge_metric_fragments(metric_groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for group in metric_groups:
        for metric in group:
            key = (str(metric.get("metric_key") or ""), str(metric.get("period") or ""))
            if key not in merged:
                merged[key] = metric
                continue
            previous_priority = metric.get("_source_priority", -9999)
            current_priority = merged[key].get("_source_priority", -9999)
            if previous_priority > current_priority:
                merged[key] = metric
    return list(merged.values())


def thin_statement_types(metrics_by_key: dict[tuple[str, str, str], dict[str, Any]], row: dict[str, Any]) -> set[str]:
    thin: set[str] = set()
    current_period = str(row.get("period_end") or "")
    prior_period = prior_year_period(current_period)
    minimum_distinct = {"balance_sheet": 5, "income_statement": 4, "cash_flow_statement": 3}
    for statement_type in PRIMARY_STATEMENT_TYPES:
        statement_metrics = [metric for key, metric in metrics_by_key.items() if key[0] == statement_type]
        canonical_names = {str(metric.get("metric_key") or "") for metric in statement_metrics}
        periods = {str(metric.get("period") or "") for metric in statement_metrics}
        if not statement_metrics:
            thin.add(statement_type)
            continue
        if len(canonical_names) < minimum_distinct.get(statement_type, 3):
            thin.add(statement_type)
            continue
        if current_period and prior_period and not ({current_period, prior_period} <= periods):
            thin.add(statement_type)
    return thin


def fallback_three_statement_metrics(row: dict[str, Any], target_statement_types: set[str]) -> list[dict[str, Any]]:
    if not target_statement_types:
        return []
    metrics: list[dict[str, Any]] = []
    for statement_type in sorted(target_statement_types):
        candidates = [table for table in row.get("table_index") or [] if table_is_fallback_candidate(statement_type, table, row)]
        candidates.sort(
            key=lambda table: fallback_candidate_score(row, statement_type, table),
            reverse=True,
        )
        best_metrics: list[dict[str, Any]] = []
        best_table: dict[str, Any] | None = None
        best_score = -10**9
        for table in candidates[:80]:
            table_metrics = fallback_metrics_from_table(row, statement_type, table)
            if not table_metrics:
                continue
            metric_groups = [table_metrics]
            for adjacent_table in adjacent_table_fragments(row, statement_type, table):
                adjacent_metrics = fallback_metrics_from_table(row, statement_type, adjacent_table, require_candidate=False)
                if adjacent_metrics:
                    metric_groups.append(adjacent_metrics)
            merged_metrics = merge_metric_fragments(metric_groups)
            distinct_metrics = {str(item.get("metric_key") or "") for item in merged_metrics}
            minimum_distinct = {"balance_sheet": 3, "income_statement": 3, "cash_flow_statement": 1}.get(statement_type, 1)
            if len(distinct_metrics) < minimum_distinct:
                continue
            score = fallback_candidate_score(row, statement_type, table) + len(distinct_metrics) * 160 + len(merged_metrics) * 4
            if score > best_score:
                best_score = score
                best_metrics = merged_metrics
                best_table = table
        if best_metrics:
            metrics.extend(best_metrics)
    return metrics


def build_three_statements(row: dict[str, Any]) -> dict[str, Any]:
    financial_data = row["financial_data"]
    allowed_periods = preferred_periods(row)
    metrics_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for statement in financial_data.get("statements") or []:
        statement_type = statement.get("statement_type")
        if statement_type not in PRIMARY_CANONICALS:
            continue
        scope = statement.get("scope") or "consolidated"
        if scope != "consolidated":
            continue
        for item in statement.get("items") or []:
            canonical = canonical_name_for_item(statement_type, item)
            if not canonical or canonical not in PRIMARY_CANONICALS[statement_type]:
                continue
            values = item.get("values") if isinstance(item.get("values"), dict) else {}
            raw_values = item.get("raw_values") if isinstance(item.get("raw_values"), dict) else {}
            sources = item.get("sources") if isinstance(item.get("sources"), dict) else {}
            if not values and item.get("value") is not None:
                values = {item.get("period_key") or item.get("period_end") or row["period_end"]: item.get("value")}
                raw_values = {next(iter(values)): item.get("raw_value") or item.get("value")}
            for raw_period, value_raw in values.items():
                period = normalize_period_key(raw_period, row)
                if allowed_periods and period not in allowed_periods:
                    continue
                raw_value = raw_values.get(raw_period, value_raw)
                value = eu_to_float(value_raw)
                if value is None:
                    value = eu_to_float(raw_value)
                if value is None:
                    continue
                source_info = sources.get(raw_period) if isinstance(sources.get(raw_period), dict) else {}
                source = source_from_item(row, statement, item, period, source_info)
                if not allowed_primary_source_role(str(source.get("source_role") or "")):
                    continue
                metric = {
                    "metric_key": canonical,
                    "metric_name": item.get("name") or item.get("local_name") or canonical,
                    "canonical_name": canonical,
                    "local_name": item.get("name") or item.get("local_name"),
                    "raw_value": raw_value,
                    "value": value,
                    "unit": item.get("unit") or statement.get("unit") or financial_data.get("unit") or "",
                    "currency": item.get("currency") or statement.get("currency") or financial_data.get("currency") or row.get("currency"),
                    "scale": item.get("scale") or statement.get("scale") or "1",
                    "confidence": item.get("confidence"),
                    "statement_type": statement_type,
                    "scope": scope,
                    "period": period,
                    "fiscal_year": report_year_from_period(period),
                    "source": source,
                    "_source_priority": source_priority(str(source.get("source_role") or ""), statement_type, str(source.get("source_context") or "")),
                }
                key = (statement_type, canonical, period)
                previous = metrics_by_key.get(key)
                if not previous or metric["_source_priority"] > previous.get("_source_priority", -9999):
                    metrics_by_key[key] = metric
    target_statement_types = thin_statement_types(metrics_by_key, row)
    for metric in fallback_three_statement_metrics(row, target_statement_types):
        key = (str(metric.get("statement_type") or ""), str(metric.get("metric_key") or ""), str(metric.get("period") or ""))
        previous = metrics_by_key.get(key)
        if not previous or metric.get("_source_priority", -9999) > previous.get("_source_priority", -9999):
            metrics_by_key[key] = metric
    metrics = []
    for metric in metrics_by_key.values():
        metric.pop("_source_priority", None)
        metrics.append(metric)
    metrics.sort(key=lambda item: (str(item.get("statement_type") or ""), str(item.get("metric_key") or ""), str(item.get("period") or "")))
    return {
        "company": row["company_name"],
        "stock_code": row["ticker"],
        "ticker": row["ticker"],
        "market": "EU",
        "country": row["country"],
        "report_id": row["report_id"],
        "period_end": row["period_end"],
        "metrics": metrics,
        "extraction_method": "eu_pdf_financial_data_values_bridge_v1",
    }


def build_evidence_index(row: dict[str, Any], three_statement_payload: dict[str, Any]) -> list[dict[str, Any]]:
    evidence_items: list[dict[str, Any]] = []
    for index, metric in enumerate(three_statement_payload.get("metrics") or [], start=1):
        source = metric.get("source") or {}
        item = {
            "evidence_id": f"{row['ticker']}-{row['report_id']}-metric-{index:05d}",
            "company_id": row["company_wiki_id"],
            "company_wiki_id": row["company_wiki_id"],
            "report_id": row["report_id"],
            "market": "EU",
            "country": row["country"],
            "stock_code": row["ticker"],
            "ticker": row["ticker"],
            "metric_key": metric.get("metric_key"),
            "metric_name": metric.get("metric_name"),
            "statement_type": metric.get("statement_type"),
            "scope": metric.get("scope"),
            "period": metric.get("period"),
            "raw_value": metric.get("raw_value"),
            "value": metric.get("value"),
            "unit": metric.get("unit"),
            "currency": metric.get("currency"),
            "scale": metric.get("scale"),
            "task_id": row["task_id"],
            "md_line": source.get("md_line"),
            "pdf_page_number": source.get("pdf_page_number"),
            "table_index": source.get("table_index"),
            "row_index": source.get("row_index"),
            "column_index": source.get("column_index"),
            "quote_text": source.get("quote_text"),
            "heading": source.get("heading"),
            "statement_title": source.get("statement_title"),
            "source_role": source.get("source_role"),
            "source_type": source.get("source_type"),
            "source_kind": source.get("source_kind"),
            "file": f"metrics/reports/{row['report_id']}/three_statements.json",
        }
        item.update(evidence_urls(row["task_id"], item.get("pdf_page_number"), item.get("table_index")))
        evidence_items.append(item)
    return evidence_items


def build_key_metrics(financial_data: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in financial_data.get("key_metrics") or [] if isinstance(item, dict)]


def source_quality_summary(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    role_counts = Counter()
    source_type_counts = Counter()
    statement_type_counts = Counter()
    weak_roles = {"selected_financial_summary", "financial_data_consolidated", "consolidated_context"}
    weak_metric_count = 0
    for metric in metrics:
        source = metric.get("source") or {}
        role = source.get("source_role") or "missing"
        source_type = source.get("source_type") or "missing"
        role_counts[role] += 1
        source_type_counts[source_type] += 1
        statement_type_counts[metric.get("statement_type") or "missing"] += 1
        if role in weak_roles:
            weak_metric_count += 1
    return {
        "source_role_counts": dict(sorted(role_counts.items())),
        "source_type_counts": dict(sorted(source_type_counts.items())),
        "statement_type_counts": dict(sorted(statement_type_counts.items())),
        "weak_metric_count": weak_metric_count,
        "strict_policy": "prefer consolidated annual-report statement tables; skip duplicate filings and block parent-company, segment, EPS, non-IFRS, sustainability, and note tables from primary metrics",
    }


def build_retrieval_index(row: dict[str, Any], evidence_items: list[dict[str, Any]]) -> dict[str, Any]:
    chunks = []
    for item in evidence_items:
        chunks.append(
            {
                "chunk_id": f"{item['evidence_id']}-chunk",
                "evidence_id": item["evidence_id"],
                "market": "EU",
                "country": row["country"],
                "company_id": row["company_wiki_id"],
                "company_name": row["company_name"],
                "ticker": row["ticker"],
                "report_id": row["report_id"],
                "topic": item.get("metric_key"),
                "source_type": "wiki_metrics",
                "file": item.get("file"),
                "pdf_page_number": item.get("pdf_page_number"),
                "table_index": item.get("table_index"),
                "md_line": item.get("md_line"),
                "text": " | ".join(
                    str(part)
                    for part in [
                        item.get("statement_type"),
                        item.get("metric_name"),
                        item.get("period"),
                        item.get("raw_value"),
                        item.get("currency"),
                        item.get("statement_title"),
                        item.get("source_role"),
                    ]
                    if part not in (None, "")
                ),
            }
        )
    chunks.append(
        {
            "chunk_id": f"{row['ticker']}-{row['report_id']}-full-report",
            "market": "EU",
            "country": row["country"],
            "company_id": row["company_wiki_id"],
            "company_name": row["company_name"],
            "ticker": row["ticker"],
            "report_id": row["report_id"],
            "topic": "full_report",
            "source_type": "wiki_report_markdown",
            "file": f"reports/{row['report_id']}/report.md",
            "text": f"{row['company_name']} {row['ticker']} {row['report_year']} EU annual report full text fallback",
        }
    )
    return {
        "schema_version": "eu_retrieval_index_v1",
        "market": "EU",
        "country": row["country"],
        "company_id": row["company_wiki_id"],
        "report_id": row["report_id"],
        "chunk_count": len(chunks),
        "chunks": chunks,
        "generated_at": now_iso(),
    }


def inspect_eu_result(result_dir: Path) -> dict[str, Any] | None:
    metadata = read_json(result_dir / "metadata.json", {})
    financial_data = read_json(result_dir / "financial_data.json", {})
    if str(metadata.get("market") or financial_data.get("market") or "").upper() != "EU":
        return None
    document_full = read_json(result_dir / "document_full.json", {})
    quality = read_json(result_dir / "quality_report.json", {})
    financial_checks = read_json(result_dir / "financial_checks.json", {})
    table_index = read_json(result_dir / "table_index.json", [])
    filename = (
        metadata.get("filename")
        or financial_data.get("filename")
        or ((document_full.get("task") or {}).get("filename") if isinstance(document_full, dict) else "")
        or ""
    )
    parsed = parse_eu_filename(filename)
    ticker = str(metadata.get("ticker") or metadata.get("stock_code") or financial_data.get("ticker") or parsed.get("ticker") or "").upper()
    company_name = clean_company_name(metadata.get("company_name") or financial_data.get("company_name") or parsed.get("company_name") or ticker)
    period_end = str(metadata.get("period_end") or financial_data.get("period_end") or parsed.get("period_end") or "")
    report_year = report_year_from_period(period_end) or valid_year(metadata.get("fiscal_year")) or report_year_from_period(filename)
    report_kind = metadata.get("report_kind") or financial_data.get("report_kind") or "eu_annual_report"
    report_id = f"{int(report_year)}-{report_kind_slug(report_kind, metadata.get('report_type'))}" if report_year else f"unknown-{result_dir.name[:8]}"
    country = infer_country(ticker, company_name, metadata, parsed)
    company_wiki_id = f"{ticker}-{safe_slug(company_name)}"
    metrics_value_count = 0
    for statement in financial_data.get("statements") or []:
        for item in statement.get("items") or []:
            values = item.get("values") if isinstance(item.get("values"), dict) else {}
            metrics_value_count += sum(1 for value in values.values() if eu_to_float(value) is not None)
    source_id = parsed.get("source_id") or metadata.get("source") or ""
    url_hash = parsed.get("url_hash") or ""
    warnings: list[str] = []
    if not ticker:
        warnings.append("missing_eu_ticker")
    if not company_name or company_name == ticker:
        warnings.append("missing_company_name")
    if not report_year:
        warnings.append("missing_report_year")
    if len(financial_data.get("statements") or []) < 3:
        warnings.append("missing_three_financial_statements")
    if financial_checks.get("overall_status") == "fail":
        warnings.append("financial_checks_fail")
    if metrics_value_count == 0:
        warnings.append("empty_financial_values")
    return {
        "task_id": result_dir.name,
        "result_dir": result_dir,
        "metadata": metadata,
        "document_full": document_full,
        "quality": quality,
        "financial_data": financial_data,
        "financial_checks": financial_checks,
        "table_index": table_index,
        "filename": filename,
        "source_metadata": source_metadata(metadata, filename),
        "ticker": ticker,
        "stock_code": ticker,
        "company_name": company_name,
        "company_wiki_id": company_wiki_id,
        "country": country,
        "exchange": COUNTRY_EXCHANGE.get(country, "EU"),
        "period_end": period_end,
        "published_at": parsed.get("published_at") or metadata.get("disclosure_date"),
        "source_id": source_id,
        "url_hash": url_hash,
        "report_year": int(report_year) if report_year else None,
        "report_kind": report_kind,
        "report_type": metadata.get("report_type") or parsed.get("report_type") or report_kind,
        "report_id": report_id,
        "currency": financial_data.get("currency") or COUNTRY_CURRENCY.get(country),
        "metrics_value_count": metrics_value_count,
        "warnings": warnings,
        "score": (
            (SOURCE_RANK.get(str(source_id), 0))
            + (1000 if ticker else 0)
            + (500 if report_year else 0)
            + (300 if financial_checks.get("overall_status") == "pass" else 180 if financial_checks.get("overall_status") == "warning" else 0)
            + len(financial_data.get("statements") or []) * 100
            + metrics_value_count
            + int(quality.get("table_count") or 0)
        ),
    }


def select_active(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    skipped = []
    for row in rows:
        if row["warnings"] and any(
            warning in row["warnings"]
            for warning in ("missing_eu_ticker", "missing_company_name", "missing_report_year", "empty_financial_values")
        ):
            skipped.append({key: row.get(key) for key in ("task_id", "filename", "warnings")})
            continue
        grouped[(row["ticker"], row["report_id"])].append(row)
    active = []
    duplicates = {}
    for key, candidates in sorted(grouped.items()):
        candidates.sort(key=lambda item: item["score"], reverse=True)
        active.append(candidates[0])
        if len(candidates) > 1:
            duplicates[f"{key[0]}-{key[1]}"] = [
                {
                    "task_id": item["task_id"],
                    "filename": item["filename"],
                    "source_id": item["source_id"],
                    "url_hash": item["url_hash"],
                    "score": item["score"],
                    "selected": index == 0,
                    "dedupe_action": "kept" if index == 0 else "skipped_duplicate_not_ingested",
                    "warnings": item["warnings"],
                }
                for index, item in enumerate(candidates)
            ]
    return active, {"duplicates": duplicates, "skipped": skipped}


def copy_report_files(row: dict[str, Any], report_dir: Path) -> dict[str, str]:
    result_dir = row["result_dir"]
    copied: dict[str, str] = {}
    for source_name, dest_name in {
        "result_complete.md": "report.md",
        "document_full.json": "document_full.json",
        "artifact_manifest.json": "artifact_manifest.json",
    }.items():
        source = result_dir / source_name
        if source.exists():
            shutil.copy2(source, report_dir / dest_name)
            copied[dest_name] = rel(report_dir / dest_name)
    return copied


def build_report_json(row: dict[str, Any], three_statements: dict[str, Any], evidence_items: list[dict[str, Any]], copied: dict[str, str]) -> dict[str, Any]:
    status = "ready"
    warnings = list(row["warnings"])
    if not (three_statements.get("metrics") or []):
        status = "needs_review"
        warnings.append("empty_three_statement_metrics")
    elif row["financial_checks"].get("overall_status") == "fail":
        status = "needs_review"
    source_files = {}
    for name in (
        "result_complete.md",
        "document_full.json",
        "financial_data.json",
        "financial_checks.json",
        "quality_report.json",
        "table_index.json",
        "table_relations.json",
        "content_list_enhanced.json",
        "artifact_manifest.json",
        "hash_manifest.json",
    ):
        path = row["result_dir"] / name
        if path.exists():
            source_files[name] = {"path": rel(path), "sha256": sha256_file(path)}
    return {
        "schema_version": "eu_report_wiki_v1",
        "generated_at": now_iso(),
        "identity": {
            "market": "EU",
            "country": row["country"],
            "company_id": f"EU:{row['country']}:{row['ticker']}",
            "company_wiki_id": row["company_wiki_id"],
            "ticker": row["ticker"],
            "company_name": row["company_name"],
        },
        "report": {
            "report_id": row["report_id"],
            "report_year": row["report_year"],
            "report_kind": row["report_kind"],
            "report_type": row["report_type"],
            "period_end": row["period_end"],
            "published_at": row["published_at"],
            "source_filename": row["filename"],
            "source_filename_metadata": row["source_metadata"],
        },
        "source": {
            "task_id": row["task_id"],
            "result_dir": rel(row["result_dir"]),
            "copied": copied,
            "source_files": source_files,
            "pdf_page_url_template": "/api/pdf_page/{task_id}/{page_number}",
            "source_page_url_template": "/api/source/{task_id}/page/{page_number}",
            "source_table_url_template": "/api/source/{task_id}/table/{table_index}",
        },
        "quality_summary": {
            "financial_overall_status": row["financial_checks"].get("overall_status"),
            "financial_summary": row["financial_checks"].get("summary"),
            "table_count": row["quality"].get("table_count"),
            "markdown_chars": row["quality"].get("markdown_chars"),
            "warnings": row["quality"].get("warnings") or [],
        },
        "financial_data_summary": {
            "statement_count": len(row["financial_data"].get("statements") or []),
            "three_statement_metric_count": len(three_statements.get("metrics") or []),
            "source_quality": source_quality_summary(three_statements.get("metrics") or []),
            "key_metric_count": len(row["financial_data"].get("key_metrics") or []),
            "warnings": row["financial_data"].get("warnings") or [],
        },
        "evidence": {"count": len(evidence_items), "sample": evidence_items[:20]},
        "status": status,
        "warnings": sorted(set(warnings)),
    }


def build_company_md(company: dict[str, Any]) -> str:
    lines = [
        f"# {company['company_name']} ({company['ticker']})",
        "",
        "- Market: EU",
        f"- Country: {company.get('country')}",
        f"- Exchange: {company.get('exchange')}",
        f"- Primary report: {company.get('primary_report_id')}",
        f"- Status: {company.get('status')}",
        "",
        "## Reports",
        "",
    ]
    for report in company.get("reports") or []:
        lines.append(f"- {report.get('report_year')} {report.get('report_kind')}: [{report.get('report_id')}]({report.get('report_md')})")
    lines.extend(
        [
            "",
            "## Data Entrypoints",
            "",
            "- [Latest three statements](metrics/latest/three_statements.json)",
            "- [Latest key metrics](metrics/latest/key_metrics.json)",
            "- [Validation](metrics/latest/validation.json)",
            "- [Evidence index](evidence/evidence_index.json)",
            "- [Retrieval index](semantic/retrieval_index.json)",
            "",
        ]
    )
    return "\n".join(lines)


def write_company(row_group: list[dict[str, Any]], output_root: Path) -> dict[str, Any]:
    row_group.sort(key=lambda row: (row["report_year"] or 0, row["period_end"], row["task_id"]), reverse=True)
    primary = row_group[0]
    company_dir = output_root / "companies" / primary["company_wiki_id"]
    for directory in (
        "reports",
        "metrics/reports",
        "metrics/latest",
        "evidence",
        "semantic/llm",
        "graph/facts",
        "graph/claims",
        "graph/notes",
        "graph/segments",
        "analysis",
        "factcheck",
        "tracking",
        "legal",
        "obsidian",
    ):
        (company_dir / directory).mkdir(parents=True, exist_ok=True)

    reports = []
    all_evidence = []
    all_pdf_refs = []
    latest_payload = None
    latest_financial_data = None
    latest_financial_checks = None
    latest_key_metrics = None
    status = "ready"

    for row in row_group:
        report_dir = company_dir / "reports" / row["report_id"]
        metrics_dir = company_dir / "metrics" / "reports" / row["report_id"]
        report_dir.mkdir(parents=True, exist_ok=True)
        metrics_dir.mkdir(parents=True, exist_ok=True)
        copied = copy_report_files(row, report_dir)
        three_statements = build_three_statements(row)
        evidence_items = build_evidence_index(row, three_statements)
        pdf_refs = build_pdf_refs(evidence_items)
        key_metrics = build_key_metrics(row["financial_data"])
        validation = {
            "schema_version": "eu_validation_v1",
            "market": "EU",
            "country": row["country"],
            "report_id": row["report_id"],
            "financial_checks": row["financial_checks"],
            "three_statement_source": "financial_data.json values/sources",
            "three_statement_metric_count": len(three_statements.get("metrics") or []),
            "three_statement_source_quality": source_quality_summary(three_statements.get("metrics") or []),
            "financial_check_status": row["financial_checks"].get("overall_status"),
            "warnings": row["warnings"],
            "generated_at": now_iso(),
        }
        write_json(metrics_dir / "three_statements.json", {"schema_version": 1, "source": "financial_data.json", "unit": "reported", "data": three_statements, "generated_at": now_iso()})
        write_json(metrics_dir / "key_metrics.json", {"schema_version": 1, "source": "financial_data.json", "data": key_metrics, "generated_at": now_iso()})
        write_json(metrics_dir / "validation.json", validation)
        write_json(metrics_dir / "financial_data.json", row["financial_data"])
        write_json(metrics_dir / "financial_checks.json", row["financial_checks"])
        write_json(metrics_dir / "normalized_metrics.json", {"schema_version": "eu_normalized_metrics_v1", "source": "three_statements.json", "metrics": three_statements.get("metrics") or [], "generated_at": now_iso()})
        report_json = build_report_json(row, three_statements, evidence_items, copied)
        write_json(report_dir / "report.json", report_json)
        package_manifest = write_report_package_facade(
            market="EU",
            company_dir=company_dir,
            report_dir=report_dir,
            metrics_dir=metrics_dir,
            row=row,
            report_json=report_json,
            three_statements=three_statements,
            key_metrics=key_metrics,
            validation=validation,
            evidence_items=evidence_items,
        )
        reports.append(
            {
                "report_id": row["report_id"],
                "report_year": row["report_year"],
                "report_kind": row["report_kind"],
                "report_type": row["report_type"],
                "period_end": row["period_end"],
                "published_at": row["published_at"],
                "status": report_json["status"],
                "task_id": row["task_id"],
                "source_filename": row["filename"],
                "source_filename_metadata": row["source_metadata"],
                "report_md": f"reports/{row['report_id']}/report.md",
                "report_json": f"reports/{row['report_id']}/report.json",
                "document_full": f"reports/{row['report_id']}/document_full.json",
                "manifest": f"reports/{row['report_id']}/manifest.json",
                "package_path": package_manifest.get("wiki_report_path"),
                "retrieval_status": "ready" if report_json["status"] == "ready" else "needs_review",
                "wiki_ready": report_json["status"] == "ready",
                "retrieval_issues": [] if report_json["status"] == "ready" else report_json.get("warnings") or [],
                "metrics": {
                    "three_statements": f"metrics/reports/{row['report_id']}/three_statements.json",
                    "key_metrics": f"metrics/reports/{row['report_id']}/key_metrics.json",
                    "validation": f"metrics/reports/{row['report_id']}/validation.json",
                    "financial_data": f"metrics/reports/{row['report_id']}/financial_data.json",
                    "financial_checks": f"metrics/reports/{row['report_id']}/financial_checks.json",
                },
            }
        )
        all_evidence.extend(evidence_items)
        all_pdf_refs.extend(pdf_refs)
        if row is primary:
            latest_payload = three_statements
            latest_financial_data = row["financial_data"]
            latest_financial_checks = row["financial_checks"]
            latest_key_metrics = key_metrics
        if report_json["status"] != "ready":
            status = "needs_review"

    write_json(company_dir / "metrics" / "latest" / "three_statements.json", {"schema_version": 1, "source": "financial_data.json", "unit": "reported", "data": latest_payload or {}, "generated_at": now_iso()})
    write_json(company_dir / "metrics" / "latest" / "key_metrics.json", {"schema_version": 1, "source": "financial_data.json", "data": latest_key_metrics or [], "generated_at": now_iso()})
    write_json(
        company_dir / "metrics" / "latest" / "validation.json",
        {
            "schema_version": "eu_validation_v1",
            "financial_checks": latest_financial_checks or {},
            "three_statement_metric_count": len((latest_payload or {}).get("metrics") or []),
            "three_statement_source_quality": source_quality_summary((latest_payload or {}).get("metrics") or []),
            "generated_at": now_iso(),
        },
    )
    write_json(company_dir / "metrics" / "latest" / "financial_data.json", latest_financial_data or {})
    write_json(company_dir / "metrics" / "latest" / "financial_checks.json", latest_financial_checks or {})
    write_json(company_dir / "metrics" / "latest" / "normalized_metrics.json", {"schema_version": "eu_normalized_metrics_v1", "source": "three_statements.json", "metrics": (latest_payload or {}).get("metrics") or [], "generated_at": now_iso()})
    write_json(company_dir / "evidence" / "evidence_index.json", {"schema_version": 1, "market": "EU", "country": primary["country"], "company_id": primary["company_wiki_id"], "evidence_count": len(all_evidence), "evidence": all_evidence, "generated_at": now_iso()})
    write_json(company_dir / "evidence" / "pdf_refs.json", {"schema_version": 1, "market": "EU", "country": primary["country"], "company_id": primary["company_wiki_id"], "refs": all_pdf_refs, "generated_at": now_iso()})
    write_json(company_dir / "evidence" / "image_manifest.json", {"schema_version": 1, "market": "EU", "company_id": primary["company_wiki_id"], "images": [], "generated_at": now_iso()})
    write_json(company_dir / "evidence" / "source_map_latest.json", {"schema_version": "eu_source_map_latest_v1", "source": "evidence_index.json", "latest_report_id": primary["report_id"], "generated_at": now_iso()})
    write_json(company_dir / "semantic" / "retrieval_index.json", build_retrieval_index(primary, all_evidence))
    for name, payload in {
        "subject_profile.json": {"schema_version": "eu_subject_profile_v1", "market": "EU", "country": primary["country"], "company_id": primary["company_wiki_id"], "company_name": primary["company_name"], "ticker": primary["ticker"], "generated_at": now_iso()},
        "segments.json": {"schema_version": "eu_segments_v1", "segments": [], "note": "Rule-based segment extraction is deferred; use report.md full-text fallback.", "generated_at": now_iso()},
        "facts.json": {"schema_version": "eu_facts_v1", "facts": [], "generated_at": now_iso()},
        "relations.json": {"schema_version": "eu_relations_v1", "relations": [], "generated_at": now_iso()},
        "claims.json": {"schema_version": "eu_claims_v1", "claims": [], "generated_at": now_iso()},
        "document_links.json": {"schema_version": "eu_document_links_v1", "links": [], "note": "EU note relation extraction is deferred; use report.md/document_full fallback.", "generated_at": now_iso()},
        "note_links.json": {"schema_version": "eu_note_links_v1", "links": [], "generated_at": now_iso()},
        "evidence_semantic.json": {"schema_version": "eu_evidence_semantic_v1", "items": [], "generated_at": now_iso()},
        "extraction_log.json": {"schema_version": "eu_semantic_extraction_log_v1", "steps": [], "generated_at": now_iso()},
    }.items():
        write_json(company_dir / "semantic" / name, payload)
    write_json(company_dir / "graph" / "graph_index.json", {"schema_version": "eu_graph_index_v1", "market": "EU", "country": primary["country"], "company_id": primary["company_wiki_id"], "nodes": [], "edges": [], "generated_at": now_iso()})
    write_text(company_dir / "graph" / "company.md", f"# {primary['company_name']}\n\nEU company graph workspace.\n")
    write_text(company_dir / "graph" / "report.md", f"# {primary['company_name']} Reports\n\nPrimary report: {primary['report_id']}.\n")
    write_text(company_dir / "analysis" / "README.md", f"# {primary['company_name']} Analysis Workspace\n\nAll important conclusions must cite metrics/evidence/report sources.\n")
    write_text(company_dir / "obsidian" / "README.md", f"# {primary['company_name']} Obsidian Workspace\n")
    write_text(company_dir / "obsidian" / "index.md", f"# {primary['company_name']}\n\n- [[../company.md|Company]]\n")

    company_json = {
        "schema_version": "eu_company_wiki_v1",
        "market": "EU",
        "country": primary["country"],
        "company_id": f"EU:{primary['country']}:{primary['ticker']}",
        "company_wiki_id": primary["company_wiki_id"],
        "company_wiki_path": rel(company_dir),
        "stock_code": primary["ticker"],
        "ticker": primary["ticker"],
        "exchange": primary["exchange"],
        "company_short_name": primary["company_name"],
        "company_full_name": primary["company_name"],
        "company_name": primary["company_name"],
        "aliases": sorted({primary["ticker"], primary["company_name"], safe_slug(primary["company_name"])}),
        "currency": primary.get("currency"),
        "accounting_standard": (latest_financial_data or {}).get("accounting_standard") or "IFRS",
        "industry_profile": (latest_financial_data or {}).get("industry_profile"),
        "primary_report_id": primary["report_id"],
        "report_count": len(reports),
        "reports": reports,
        "metrics": {
            "latest": {
                "three_statements": "metrics/latest/three_statements.json",
                "key_metrics": "metrics/latest/key_metrics.json",
                "validation": "metrics/latest/validation.json",
                "financial_data": "metrics/latest/financial_data.json",
                "financial_checks": "metrics/latest/financial_checks.json",
                "normalized_metrics": "metrics/latest/normalized_metrics.json",
            },
            "by_report": {item["report_id"]: item["metrics"] for item in reports},
        },
        "evidence": {
            "evidence_index": "evidence/evidence_index.json",
            "pdf_refs": "evidence/pdf_refs.json",
            "image_manifest": "evidence/image_manifest.json",
            "source_map_latest": "evidence/source_map_latest.json",
        },
        "semantic": {
            "retrieval_index": "semantic/retrieval_index.json",
            "document_links": "semantic/document_links.json",
            "note_links": "semantic/note_links.json",
        },
        "status": status,
        "updated_at": now_iso(),
    }
    write_json(company_dir / "company.json", company_json)
    write_json(company_dir / "_index.json", {"schema_version": "eu_company_index_v1", "market": "EU", "country": primary["country"], "company_id": primary["company_wiki_id"], "primary_report_id": primary["report_id"], "reports": reports, "status": status, "updated_at": now_iso()})
    write_text(company_dir / "company.md", build_company_md(company_json))
    return {"company": company_json, "reports": reports, "evidence_count": len(all_evidence), "status": status}


def eu_agent_guide() -> str:
    return """# EU Wiki Agent Guide

This market wiki follows the A-share company workspace contract with EU annual-report specific evidence rules.

Default routing:

1. Read `_meta/company_catalog.json` to resolve the ticker, country, or company alias.
2. Read `companies/<company_wiki_id>/company.json` and choose `primary_report_id` unless the user specified a year/report.
3. For financial statement values, read `metrics/reports/<report_id>/three_statements.json`, then `key_metrics.json` and `validation.json`.
4. Use `evidence/evidence_index.json` for source `task_id`, `pdf_page_number`, `table_index`, and `md_line`.
5. Use `reports/<report_id>/report.md` and `document_full.json` for full-text fallback, notes, subsidiaries, segments, and cross-checks.

EU-specific rules:

- The ingestion layer de-duplicates equivalent ticker/period reports; skipped duplicates are recorded in `_meta/ingestion_manifest.json`.
- Primary metrics use consolidated statements only.
- Parent-company, separate, segment, EPS, Non-IFRS/adjusted, sustainability, and note tables are not primary metric sources.
- When a formal consolidated table and a selected summary both provide a metric, the formal consolidated table wins.
- Subsidiary and segment queries use full-text fallback through `report.md`, `document_full.json`, and table evidence.
"""


def eu_readme(company_count: int, report_count: int) -> str:
    return f"""# EU Company Wiki

This directory is generated from standardized PDF parser results for EU/UK/Swiss annual reports.

- Companies: `{company_count}`
- Reports: `{report_count}`
- Primary source: `data/pdf-parser/results`
- Main agent entrypoints: `_meta/company_catalog.json`, company `company.json`, `metrics/reports/<report_id>/three_statements.json`, `evidence/evidence_index.json`.

The EU Wiki keeps consolidated financial statements as the primary structured metric layer. Duplicate filings, parent-company statements, Non-IFRS tables, separate/segment details, EPS notes, and sustainability tables are not promoted into primary metrics.
"""


def write_market_root(output_root: Path, company_results: list[dict[str, Any]], selection: dict[str, Any], source_results_dir: Path) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    for directory in ("_meta", "derived", "_quarantine", "_trash", "companies"):
        (output_root / directory).mkdir(exist_ok=True)
    companies = []
    reports = []
    issues = []
    latest = {}
    country_counts = Counter()
    for result in company_results:
        company = result["company"]
        country_counts[company.get("country") or "unknown"] += 1
        companies.append(
            {
                "market": "EU",
                "country": company.get("country"),
                "company_id": company.get("company_id"),
                "company_wiki_id": company.get("company_wiki_id"),
                "stock_code": company.get("stock_code"),
                "ticker": company.get("ticker"),
                "exchange": company.get("exchange"),
                "company_short_name": company.get("company_short_name"),
                "company_full_name": company.get("company_full_name"),
                "aliases": company.get("aliases") or [],
                "company_path": company.get("company_wiki_path"),
                "primary_report_id": company.get("primary_report_id"),
                "report_count": company.get("report_count"),
                "status": company.get("status"),
            }
        )
        for report in result.get("reports") or []:
            reports.append({**report, "market": "EU", "country": company.get("country"), "company_wiki_id": company.get("company_wiki_id"), "company_path": company.get("company_wiki_path"), "ticker": company.get("ticker"), "company_name": company.get("company_name")})
            if report.get("status") != "ready":
                issues.append({"company_wiki_id": company.get("company_wiki_id"), "report_id": report.get("report_id"), "status": report.get("status")})
        latest_path = output_root / "companies" / str(company.get("company_wiki_id")) / "metrics" / "latest" / "three_statements.json"
        latest[company.get("ticker")] = read_json(latest_path, {})
    companies.sort(key=lambda item: str(item.get("ticker") or ""))
    reports.sort(key=lambda item: (str(item.get("ticker") or ""), str(item.get("report_id") or "")))
    write_json(output_root / "_meta" / "company_catalog.json", {"schema_version": "eu_company_catalog_v1", "market": "EU", "generated_at": now_iso(), "company_count": len(companies), "country_counts": dict(sorted(country_counts.items())), "companies": companies})
    write_json(output_root / "_meta" / "report_catalog.json", {"schema_version": "eu_report_catalog_v1", "market": "EU", "generated_at": now_iso(), "report_count": len(reports), "reports": reports})
    write_json(output_root / "_meta" / "market_profile.json", {"schema_version": "eu_market_profile_v1", "market": "EU", "source": "EU/UK/Swiss annual reports", "countries": dict(sorted(country_counts.items())), "company_id_rule": "<ticker>-<company-slug>", "report_id_rule": "<year>-<report-kind-slug>", "primary_statement_scope": "consolidated", "dedupe_rule": "ticker + report_id; keep highest-ranked source, skip duplicates", "subsidiary_relation_policy": "not_structured_use_full_text_fallback", "accounting_standard": "IFRS / EU local GAAP", "generated_at": now_iso()})
    write_json(output_root / "_meta" / "ingestion_manifest.json", {"schema_version": "eu_ingestion_manifest_v1", "market": "EU", "generated_at": now_iso(), "source_results_dir": rel(source_results_dir), "company_count": len(companies), "report_count": len(reports), "selection": selection})
    write_json(output_root / "_meta" / "quality_summary.json", {"schema_version": "eu_quality_summary_v1", "market": "EU", "generated_at": now_iso(), "company_count": len(companies), "report_count": len(reports), "status_counts": dict(Counter(item.get("status") for item in reports)), "issue_count": len(issues), "country_counts": dict(sorted(country_counts.items()))})
    write_json(output_root / "_meta" / "extraction_issues.json", {"schema_version": "eu_extraction_issues_v1", "market": "EU", "generated_at": now_iso(), "issues": issues, "selection": selection})
    write_json(output_root / "derived" / "three_statements_latest.json", latest)
    guide = eu_agent_guide()
    write_text(output_root / "_meta" / "AGENT_GUIDE.md", guide)
    write_text(output_root / "AGENTS.md", guide)
    write_text(output_root / "README.md", eu_readme(len(companies), len(reports)))


def build_plan(results_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = []
    for result_dir in sorted(results_dir.iterdir()):
        if not result_dir.is_dir():
            continue
        row = inspect_eu_result(result_dir)
        if row:
            rows.append(row)
    return select_active(rows)


def run(args: argparse.Namespace) -> dict[str, Any]:
    results_dir = args.results_dir.resolve()
    output_root = args.output_root.resolve()
    active, selection = build_plan(results_dir)
    if args.limit:
        active = active[: args.limit]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in active:
        grouped[row["company_wiki_id"]].append(row)
    plan = {
        "market": "EU",
        "apply": bool(args.apply),
        "source_results_dir": rel(results_dir),
        "output_root": rel(output_root),
        "candidate_report_count": len(active),
        "company_count": len(grouped),
        "selection": selection,
        "companies": [
            {
                "company_wiki_id": key,
                "ticker": rows[0]["ticker"],
                "company_name": rows[0]["company_name"],
                "country": rows[0]["country"],
                "reports": [row["report_id"] for row in rows],
                "warnings": sorted({warning for row in rows for warning in row.get("warnings", [])}),
            }
            for key, rows in sorted(grouped.items())
        ],
    }
    if not args.apply:
        return plan
    company_results = [write_company(rows, output_root) for _, rows in sorted(grouped.items())]
    write_market_root(output_root, company_results, selection, results_dir)
    plan["written_company_count"] = len(company_results)
    plan["written_report_count"] = sum(len(item.get("reports") or []) for item in company_results)
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest EU PDF parser results into an A-share-aligned company Wiki workspace.")
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--apply", action="store_true", help="Write files. Omit for dry-run.")
    parser.add_argument("--json-output", type=Path, default=None)
    args = parser.parse_args()
    payload = run(args)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)
    if args.json_output:
        write_json(args.json_output, payload)


if __name__ == "__main__":
    main()
