#!/usr/bin/env python3
"""Shared company identity and path naming rules for wiki and PostgreSQL.

The canonical example is `000333-美的集团`:

- `stock_code` is the business identity anchor.
- `company_short_name` / `stock_name` is a separate attribute.
- `company_id` is a technical slug derived from those two fields.
- Source PDF filenames stay as provenance and are never treated as names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

A_SHARE_CODE_RE = re.compile(r"^[03689]\d{5}$")
REPORT_FINDER_FILENAME_RE = re.compile(
    r"^(?P<company>.+?)_"
    r"(?P<market>CN|HK|US)_"
    r"(?P<ticker>[^_]+)_"
    r"(?P<report_end>\d{4}-\d{2}-\d{2})_"
    r"(?P<report_type>[^_]+)_"
    r"(?P<published_at>\d{4}-\d{2}-\d{2})_"
    r"(?P<source_id>.+)_"
    r"(?P<url_hash>[0-9a-fA-F]{8})$",
)
LEGACY_CNINFO_FILENAME_RE = re.compile(
    r"^(?P<stock_code>[03689]\d{5})_20\d{2}_(?P<stock_name>[^_]+)_"
)
REPORT_INSTANCE_FIELD_RE = re.compile(
    r"_(?:CN|HK|US)_[^_]+_\d{4}-\d{2}-\d{2}_[^_]+_\d{4}-\d{2}-\d{2}_.+_[0-9a-fA-F]{8}$"
)
DOWNLOAD_PDF_FILENAME_PATTERN = (
    "<company_short_name>_<market>_<stock_code>_<report_end>_"
    "<report_type>_<published_at>_<source_id>_<url_hash>.pdf"
)


MANUAL_COMPANY_BY_CODE: dict[str, tuple[str, str]] = {
    "002594": ("比亚迪", "比亚迪股份有限公司"),
    "300017": ("网宿科技", "网宿科技股份有限公司"),
    "600104": ("上汽集团", "上海汽车集团股份有限公司"),
    "601238": ("广汽集团", "广州汽车集团股份有限公司"),
}


@dataclass(frozen=True)
class CompanyIdentity:
    company_id: str
    stock_code: str
    company_short_name: str
    company_full_name: str
    exchange: str


def clean_filename(filename: Any) -> str:
    name = Path(str(filename or "")).name
    return re.sub(r"\.pdf$", "", name, flags=re.IGNORECASE).strip()


def safe_slug_part(value: Any, fallback: str = "unknown") -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", text)
    text = re.sub(r"\s+", "", text)
    text = text.strip("._- ")
    return text or fallback


def strip_report_suffix(value: Any) -> str:
    text = clean_filename(value)
    text = re.sub(r"[\(\[（【]\s*(?:SH|SZ|BJ)?\s*[03689]\d{5}\s*(?:\.[A-Z]{2})?\s*[\)\]）】]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<!\d)(?:SH|SZ|BJ)?[03689]\d{5}(?:\.[A-Z]{2})?(?!\d)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:SSE|SZSE|BSE|SH|SZ|BJ|CN|HK|US)\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*20\d{2}\s*年\s*年?\s*", "", text)
    text = re.split(
        r"20\d{2}\s*年?\s*(?:年度报告全文|年度报告|年报|半年度报告|季度报告|第一季度报告|第三季度报告|报告摘要)|"
        r"年度报告全文|年度报告|年报|半年度报告|季度报告|第一季度报告|第三季度报告|报告摘要",
        text,
        maxsplit=1,
    )[0]
    text = re.sub(r"20\d{2}[-年].*$", "", text)
    text = re.sub(r"[_\-—–]+", " ", text)
    text = re.sub(r"\s+", "", text)
    return text.strip(" _-—–：:，,；;（）()[]【】")


def parse_report_finder_filename(filename: Any) -> dict[str, str]:
    stem = clean_filename(filename)
    match = REPORT_FINDER_FILENAME_RE.match(stem)
    if not match:
        return {}
    ticker = match.group("ticker").strip()
    return {
        "stock_code": ticker if A_SHARE_CODE_RE.match(ticker) else "",
        "company_short_name": strip_report_suffix(match.group("company")),
        "market": match.group("market"),
        "raw_ticker": ticker,
        "report_end": match.group("report_end"),
        "report_type": match.group("report_type"),
        "published_at": match.group("published_at"),
        "source_id": match.group("source_id"),
        "url_hash": match.group("url_hash"),
        "source": "report_finder_filename",
    }


def parse_legacy_cninfo_filename(filename: Any) -> dict[str, str]:
    stem = clean_filename(filename)
    match = LEGACY_CNINFO_FILENAME_RE.match(stem)
    if not match:
        return {}
    return {
        "stock_code": match.group("stock_code"),
        "company_short_name": strip_report_suffix(match.group("stock_name")),
        "source": "legacy_cninfo_filename",
    }


def parse_download_filename_identity(filename: Any) -> dict[str, str]:
    return parse_report_finder_filename(filename) or parse_legacy_cninfo_filename(filename)


def report_source_metadata(filename: Any) -> dict[str, str]:
    parsed = parse_download_filename_identity(filename)
    if not parsed:
        return {}
    return {
        key: value
        for key, value in {
            "source_filename": Path(str(filename or "")).name,
            "filename_pattern": DOWNLOAD_PDF_FILENAME_PATTERN if parsed.get("source") == "report_finder_filename" else "",
            "company_short_name": parsed.get("company_short_name"),
            "market": parsed.get("market"),
            "stock_code": parsed.get("stock_code"),
            "raw_ticker": parsed.get("raw_ticker"),
            "report_end": parsed.get("report_end"),
            "report_type": parsed.get("report_type"),
            "published_at": parsed.get("published_at"),
            "source_id": parsed.get("source_id"),
            "url_hash": parsed.get("url_hash"),
            "source": parsed.get("source"),
        }.items()
        if value
    }


def looks_like_report_instance_name(value: Any) -> bool:
    text = clean_filename(value)
    return bool(parse_report_finder_filename(text) or REPORT_INSTANCE_FIELD_RE.search(text))


def exchange_from_stock_code(stock_code: Any) -> str:
    code = str(stock_code or "").strip()
    if code.startswith(("600", "601", "603", "605", "688", "689")):
        return "SSE"
    if code.startswith(("000", "001", "002", "003", "300", "301")):
        return "SZSE"
    if code.startswith(("8", "4")):
        return "BSE"
    return "UNKNOWN"


def canonical_company_id(stock_code: Any, company_short_name: Any) -> str:
    code = str(stock_code or "").strip()
    short = safe_slug_part(company_short_name, "未知公司")
    return f"{code}-{short}" if code else f"UNKNOWN-{short}"


def canonicalize_identity(
    *,
    stock_code: Any,
    company_short_name: Any,
    company_full_name: Any = "",
    exchange: Any = "",
) -> CompanyIdentity:
    code = str(stock_code or "").strip()
    short = safe_slug_part(company_short_name, "未知公司")
    full = re.sub(r"\s+", "", str(company_full_name or short)).strip() or short
    if code in MANUAL_COMPANY_BY_CODE:
        manual_short, manual_full = MANUAL_COMPANY_BY_CODE[code]
        short = manual_short
        full = manual_full
    return CompanyIdentity(
        company_id=canonical_company_id(code, short),
        stock_code=code,
        company_short_name=short,
        company_full_name=full,
        exchange=str(exchange or exchange_from_stock_code(code) or "UNKNOWN"),
    )


def canonicalize_company_json(company: dict[str, Any]) -> tuple[dict[str, Any], str]:
    code = str(company.get("stock_code") or "").strip()
    short = company.get("company_short_name") or company.get("stock_name")
    full = company.get("company_full_name")
    parsed_source = ""
    reports = company.get("reports") or []
    source_filename = reports[0].get("source_filename") if reports and isinstance(reports[0], dict) else ""
    parsed = parse_download_filename_identity(source_filename)
    if parsed:
        parsed_source = parsed.get("source", "")
    if parsed and (not code or str(parsed.get("stock_code") or "") == code):
        if not code:
            code = parsed.get("stock_code", "")
        if not short or looks_like_report_instance_name(short) or looks_like_report_instance_name(company.get("company_id")):
            short = parsed.get("company_short_name")
    if not short:
        if source_filename:
            parsed = parse_download_filename_identity(source_filename)
            short = parsed.get("company_short_name")
            code = code or parsed.get("stock_code", "")
            parsed_source = parsed.get("source", "")
    if looks_like_report_instance_name(full):
        full = ""
    identity = canonicalize_identity(
        stock_code=code,
        company_short_name=short or company.get("company_id"),
        company_full_name=full,
        exchange=company.get("exchange"),
    )
    updated = dict(company)
    old_id = str(updated.get("company_id") or "")
    updated["company_id"] = identity.company_id
    updated["stock_code"] = identity.stock_code
    updated["exchange"] = identity.exchange
    updated["company_short_name"] = identity.company_short_name
    updated["company_full_name"] = identity.company_full_name
    aliases = updated.get("aliases") if isinstance(updated.get("aliases"), list) else []
    clean_aliases = []
    for value in [identity.company_short_name, identity.company_full_name, *aliases]:
        text = str(value or "").strip()
        if not text or text == old_id:
            continue
        parsed = parse_download_filename_identity(text)
        if parsed.get("company_short_name") and parsed.get("stock_code") == identity.stock_code:
            text = parsed["company_short_name"]
        if text not in clean_aliases:
            clean_aliases.append(text)
    updated["aliases"] = clean_aliases
    return updated, parsed_source


def company_dir_name(stock_code: Any, company_short_name: Any) -> str:
    return canonical_company_id(stock_code, company_short_name)


def company_dir_path(wiki_root: Any, stock_code: Any, company_short_name: Any) -> Path:
    return Path(wiki_root) / "companies" / company_dir_name(stock_code, company_short_name)


def generated_report_filename(
    stock_code: Any,
    company_short_name: Any,
    report_type: str,
    date: str,
    suffix: str = ".md",
) -> str:
    code = str(stock_code or "").strip()
    short = safe_slug_part(company_short_name, "未知公司")
    kind = safe_slug_part(report_type, "report")
    return f"{code}-{short}-{kind}-{date}{suffix}"


def generated_report_archive_path(
    wiki_root: Any,
    stock_code: Any,
    company_short_name: Any,
    report_type: str,
    date: str,
    suffix: str = ".md",
) -> Path:
    return (
        company_dir_path(wiki_root, stock_code, company_short_name)
        / "analysis"
        / "generated_reports"
        / report_type
        / generated_report_filename(stock_code, company_short_name, report_type, date, suffix)
    )
