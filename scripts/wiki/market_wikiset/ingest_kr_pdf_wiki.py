#!/usr/bin/env python3
"""Build KR company Wiki workspaces from standardized PDF parser results.

The output follows the A-share company workspace contract while applying
Korean DART/KIFRS-specific identity, statement scope, and evidence rules.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import Counter, defaultdict
from html.parser import HTMLParser
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
    to_float,
    write_json,
    write_text,
)


DEFAULT_OUTPUT_ROOT = REPO_ROOT / "data" / "wiki" / "kr"

KR_FILENAME_RE = re.compile(
    r"^(?P<company>.+?)_"
    r"(?P<market>KR)_"
    r"(?P<ticker>\d{6})_"
    r"(?P<period_end>\d{4}-\d{2}-\d{2})_"
    r"(?P<report_type>[^_]+)_"
    r"(?P<published_at>\d{4}-\d{2}-\d{2})_"
    r"(?P<source_id>.+?)_"
    r"(?P<url_hash>[0-9a-fA-F]{8})"
    r"(?:\.pdf)?$",
    re.IGNORECASE,
)

REPORT_KIND_SLUG = {
    "kr_business_report": "annual",
    "annual_report": "annual",
    "annual": "annual",
    "年报": "annual",
    "business_report": "annual",
    "quarterly_report": "quarterly",
    "semiannual_report": "interim",
    "half_year_report": "interim",
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

CONTEXT_LIMIT = 800

NON_PRIMARY_TABLE_PATTERNS = (
    "보고기간후사건",
    "수정을 요하지 않는 보고기간후사건",
    "주요 종속기업",
    "종속기업의 요약",
    "종속기업명",
    "사업부문별",
    "영업부문",
    "부문별",
    "외부매출액",
    "지역간내부매출액",
    "조건부자본증권",
    "미상환 잔액",
    "공정가치",
    "가치평가기법",
    "수준 3 투입변수",
    "주식기준보상",
    "자기주식",
    "주당이익",
    "기본주당",
    "희석주당",
)

FORMAL_TABLE_RESCUE_PRIORITY = 950


def clean_company_name(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[-_]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class SimpleTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.current_row: list[str] | None = None
        self.current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "tr":
            self.current_row = []
        elif tag.lower() in {"td", "th"}:
            self.current_cell = []

    def handle_data(self, data: str) -> None:
        if self.current_cell is not None:
            self.current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self.current_cell is not None:
            if self.current_row is not None:
                self.current_row.append(re.sub(r"\s+", " ", "".join(self.current_cell)).strip())
            self.current_cell = None
        elif tag == "tr" and self.current_row is not None:
            self.rows.append(self.current_row)
            self.current_row = None


def parse_html_table(markup: str) -> list[list[str]]:
    parser = SimpleTableParser()
    parser.feed(markup)
    return parser.rows


def parse_kr_filename(filename: Any) -> dict[str, str]:
    stem = Path(str(filename or "")).name
    stem = re.sub(r"\.pdf$", "", stem, flags=re.IGNORECASE)
    match = KR_FILENAME_RE.match(stem)
    if not match:
        return {}
    data = {key: str(value or "").strip() for key, value in match.groupdict().items()}
    data["company_name"] = clean_company_name(data.get("company"))
    data["source_filename"] = Path(str(filename or "")).name
    data["filename_pattern"] = "<company>_KR_<ticker>_<period_end>_<report_type>_<published_at>_<source_id>_<url_hash>.pdf"
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
    values = (period_end, *fallback)
    for value in values:
        match = re.search(r"(20\d{2}|19\d{2})", str(value or ""))
        if match:
            return int(match.group(1))
    return None


def report_kind_slug(report_kind: Any, report_type: Any) -> str:
    key = str(report_kind or report_type or "kr_business_report").strip()
    return REPORT_KIND_SLUG.get(key, safe_slug(key.lower(), "report"))


def normalize_kr_label(label: Any) -> str:
    text = str(label or "")
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.sub(r"\[[^]]*\]", "", text)
    text = text.replace("ㆍ", "").replace("·", "").replace(" ", "")
    text = re.sub(r"^[IVX]+\.", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^[0-9]+[.)]?", "", text)
    return re.sub(r"\s+", "", text).strip()


def canonical_from_rescue_label(statement_type: str, label: Any) -> str:
    text = normalize_kr_label(label)
    if not text:
        return ""
    if statement_type == "balance_sheet":
        if text == "현금및현금성자산":
            return "cash_and_cash_equivalents"
        if text == "유동자산":
            return "current_assets"
        if text == "비유동자산":
            return "non_current_assets"
        if text == "자산총계":
            return "total_assets"
        if text == "유동부채":
            return "current_liabilities"
        if text == "비유동부채":
            return "non_current_liabilities"
        if text == "부채총계":
            return "total_liabilities"
        if text in {"지배기업의소유지분", "지배기업소유주지분", "지배기업소유주에게귀속되는자본"}:
            return "parent_equity"
        if text == "비지배지분":
            return "nci_equity"
        if text == "자본총계":
            return "total_equity"
        if text in {"부채및자본총계", "부채와자본총계", "부채와자본총액"}:
            return "total_liabilities_and_equity"
    if statement_type == "income_statement":
        if text in {"매출액", "매출", "영업수익", "수익"}:
            return "operating_revenue"
        if text in {"매출총이익", "매출총이익손실"}:
            return "gross_profit"
        if text in {"영업이익", "영업이익손실"}:
            return "operating_profit"
        if text in {"법인세비용차감전순이익", "법인세비용차감전순이익손실", "법인세비용차감전계속영업이익", "법인세비용차감전계속영업이익손실"}:
            return "total_profit"
        if text in {"법인세비용", "법인세비용수익", "법인세수익"}:
            return "income_tax_expense"
        if text in {"당기순이익", "당기순이익손실", "계속영업당기순이익", "계속영업당기순이익손실"}:
            return "net_profit"
        if text in {"지배기업의소유주지분", "지배기업소유주지분", "지배기업소유주지분순이익", "지배기업의소유주에게귀속되는당기순이익"}:
            return "parent_net_profit"
    if statement_type == "cash_flow_statement":
        if text in {"영업활동현금흐름", "영업활동으로인한현금흐름", "영업활동으로인한순현금흐름"}:
            return "operating_cash_flow_net"
        if text in {"투자활동현금흐름", "투자활동으로인한현금흐름", "투자활동으로인한순현금흐름"}:
            return "investing_cash_flow_net"
        if text in {"재무활동현금흐름", "재무활동으로인한현금흐름", "재무활동으로인한순현금흐름"}:
            return "financing_cash_flow_net"
        if text in {"현금및현금성자산의순증감", "현금및현금성자산의증가감소", "현금및현금성자산의증감"}:
            return "cash_equivalents_net_increase"
        if text in {"기초현금및현금성자산", "현금및현금성자산의기초"}:
            return "cash_equivalents_beginning"
        if text in {"기말현금및현금성자산", "현금및현금성자산의기말"}:
            return "cash_equivalents_ending"
    return ""


def prior_year_period(period: str) -> str:
    match = re.match(r"^(\d{4})(-\d{2}-\d{2})$", str(period or ""))
    if not match:
        return ""
    return f"{int(match.group(1)) - 1}{match.group(2)}"


def normalize_period_key(period: str, row: dict[str, Any]) -> str:
    text = str(period or "").strip()
    current = str(row.get("period_end") or "").strip()
    if not text or not current:
        return text
    match = re.match(r"^(\d{4})(-\d{2}-\d{2})$", text)
    current_match = re.match(r"^(\d{4})(-\d{2}-\d{2})$", current)
    if not match or not current_match:
        return text
    year = int(match.group(1))
    current_year = int(current_match.group(1))
    suffix = match.group(2)
    ticker_prefix = int(str(row.get("ticker") or "0")[:4] or 0)
    # Some KR parser outputs inherit a stock-code-derived pseudo year
    # (e.g. ticker 207940 -> 2079) while the table text and filename prove
    # the filing period. Preserve relative year offsets and map them back.
    if ticker_prefix and year in {ticker_prefix, ticker_prefix - 1, ticker_prefix - 2} and abs(ticker_prefix - current_year) > 5:
        return f"{current_year - (ticker_prefix - year):04d}{suffix}"
    return text


def preferred_periods(row: dict[str, Any]) -> set[str]:
    current = str(row.get("period_end") or "").strip()
    periods = {current} if current else set()
    prior = prior_year_period(current)
    if prior:
        periods.add(prior)
    return periods


def source_metadata(meta: dict[str, Any], filename: str) -> dict[str, Any]:
    parsed = parse_kr_filename(filename)
    return {
        key: value
        for key, value in {
            "source_filename": filename,
            "filename_pattern": parsed.get("filename_pattern"),
            "company_short_name": parsed.get("company_name") or meta.get("company_name"),
            "market": "KR",
            "stock_code": parsed.get("ticker") or meta.get("ticker") or meta.get("stock_code"),
            "raw_ticker": parsed.get("ticker") or meta.get("ticker"),
            "report_end": parsed.get("period_end") or meta.get("period_end"),
            "report_type": parsed.get("report_type") or meta.get("report_type"),
            "published_at": parsed.get("published_at") or meta.get("disclosure_date"),
            "source_id": parsed.get("source_id") or meta.get("source"),
            "url_hash": parsed.get("url_hash"),
            "source": "kr_dart_report_finder_filename" if parsed else meta.get("source"),
        }.items()
        if value
    }


def evidence_from_item(item: dict[str, Any], result_dir: Path, table_index_payload: Any) -> dict[str, Any]:
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    raw = evidence.get("raw") if isinstance(evidence.get("raw"), dict) else {}
    raw_table = raw.get("table") if isinstance(raw.get("table"), dict) else {}
    table_index = evidence.get("table_index") or raw_table.get("table_index")
    table = table_by_index(table_index, table_index_payload)
    md_line = raw_table.get("line") or evidence.get("md_line") or table.get("line")
    page = evidence.get("pdf_page_number") or evidence.get("page_number") or raw_table.get("pdf_page_number") or table.get("pdf_page_number")
    source_context = source_context_from_item(item)
    statement_title = statement_title_from_context(source_context)
    source_role = source_role_from_item(item)
    return {
        "source_type": evidence.get("source_type") or "dart_pdf_statement_table",
        "source_id": evidence.get("source_id"),
        "quote_text": evidence.get("quote_text"),
        "md_line": md_line,
        "pdf_page_number": page,
        "table_index": table_index,
        "row_index": evidence.get("row_index"),
        "column_index": evidence.get("column_index"),
        "heading": raw_table.get("heading") or table.get("heading"),
        "statement_title": statement_title,
        "source_context": source_context,
        "source_role": source_role,
        "source_kind": raw_table.get("source") or "financial_data_statement",
        "markdown_path": rel(result_dir / "result_complete.md"),
    }


def item_period(item: dict[str, Any], default_period: str = "") -> str:
    return str(item.get("period_key") or item.get("period_end") or default_period or "").strip()


def source_context_from_item(item: dict[str, Any]) -> str:
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    raw = evidence.get("raw") if isinstance(evidence.get("raw"), dict) else {}
    table = raw.get("table") if isinstance(raw.get("table"), dict) else {}
    near_tail = str(table.get("near_text") or "")[-CONTEXT_LIMIT:]
    preview = str(table.get("preview") or "")[:CONTEXT_LIMIT]
    parts = [
        table.get("inherited_statement_title"),
        near_tail,
        table.get("heading"),
        preview,
        item.get("local_name"),
        item.get("label"),
    ]
    return re.sub(r"\s+", " ", " ".join(str(part or "") for part in parts if part)).strip()


def table_context_from_item(item: dict[str, Any]) -> str:
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    raw = evidence.get("raw") if isinstance(evidence.get("raw"), dict) else {}
    table = raw.get("table") if isinstance(raw.get("table"), dict) else {}
    parts = [
        table.get("heading"),
        table.get("preview"),
        table.get("near_text"),
        table.get("following_text"),
        " ".join(str(x) for x in table.get("matched_financial_names") or []),
    ]
    return re.sub(r"\s+", " ", " ".join(str(part or "") for part in parts if part)).strip()


def statement_title_from_context(context: str) -> str:
    text = str(context or "")
    patterns = [
        r"(?:#\s*)?2[-.]?\d*\.\s*연결\s*재무상태표",
        r"(?:#\s*)?2[-.]?\d*\.\s*연결\s*손익계산서",
        r"(?:#\s*)?2[-.]?\d*\.\s*연결\s*포괄손익계산서",
        r"(?:#\s*)?2[-.]?\d*\.\s*연결\s*현금흐름표",
        r"연결\s*재무상태표",
        r"연결\s*손익계산서",
        r"연결\s*포괄손익계산서",
        r"연결\s*현금흐름표",
        r"요약\s*연결\s*재무정보",
        r"재무상태\s*및\s*영업실적",
        r"(?:#\s*)?4[-.]?\d*\.\s*재무상태표",
        r"(?:#\s*)?4[-.]?\d*\.\s*손익계산서",
        r"(?:#\s*)?4[-.]?\d*\.\s*포괄손익계산서",
        r"(?:#\s*)?4[-.]?\d*\.\s*현금흐름표",
    ]
    matches: list[tuple[int, str]] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            matches.append((match.start(), match.group(0).strip()))
    if not matches:
        return ""
    return sorted(matches, key=lambda item: item[0])[-1][1]


def source_role_from_item(item: dict[str, Any]) -> str:
    text = source_context_from_item(item)
    table_text = table_context_from_item(item)
    lower = text.lower()
    flat = compact(text)
    title = statement_title_from_context(text)
    title_flat = compact(title)
    table_flat = compact(table_text)
    if "요약연결재무정보" in title_flat:
        return "summary_consolidated"
    if "재무상태및영업실적" in title_flat:
        return "mda_financial_review"
    if any(token in title_flat for token in ("연결재무상태표", "연결손익계산서", "연결포괄손익계산서", "연결현금흐름표")):
        return "formal_consolidated_statement"
    if re.search(r"(?:#\s*)?4[-.]?\d*\.\s*(?:재무상태표|손익계산서|포괄손익계산서|현금흐름표)", title):
        return "separate_statement"
    if any(compact(pattern) in table_flat for pattern in NON_PRIMARY_TABLE_PATTERNS):
        if any(token in table_flat for token in ("주요종속기업", "종속기업의요약", "종속기업명")):
            return "subsidiary_or_investee_table"
        if any(token in table_flat for token in ("사업부문별", "영업부문", "부문별", "외부매출액", "지역간내부매출액")):
            return "segment_table"
        if any(token in table_flat for token in ("공정가치", "가치평가기법", "수준3투입변수")):
            return "fair_value_note"
        if any(token in table_flat for token in ("보고기간후사건", "수정을요하지않는보고기간후사건")):
            return "subsequent_events_note"
        return "non_primary_note_table"
    if any(token in text for token in ("요약별도재무정보", "별도재무정보", "별도 재무정보")):
        return "separate_summary"
    if re.search(r"(?:#\s*)?4\.\s*재무제표", text) or re.search(
        r"(?:#\s*)?4[-.]?\d*\.\s*(?:재무상태표|손익계산서|포괄손익계산서|현금흐름표)", text
    ):
        return "separate_statement"
    if any(token in text for token in ("주당이익", "기본주당", "희석주당")) or any(
        token in lower for token in ("earnings per share", "weighted average", "basic earnings", "diluted earnings", "eps")
    ):
        return "eps_or_per_share_note"
    if "요약연결재무정보" in text or "summaryconsolidatedfinancialinformation" in flat:
        return "summary_consolidated"
    if any(
        token in flat
        for token in (
            "연결재무제표",
            "연결재무상태표",
            "연결손익계산서",
            "연결포괄손익계산서",
            "연결현금흐름표",
            "consolidatedstatementoffinancialposition",
            "consolidatedstatementofprofitorloss",
            "consolidatedstatementofcomprehensiveincome",
            "consolidatedstatementofcashflows",
        )
    ):
        return "formal_consolidated_statement"
    if "재무상태및영업실적" in flat or "managementdiscussion" in lower:
        return "mda_financial_review"
    if "연결" in text or "consolidated" in lower:
        return "consolidated_context"
    return "unclassified"


def compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def source_priority(item: dict[str, Any], statement_type: str) -> int:
    text = source_context_from_item(item)
    lower = text.lower()
    flat = compact(text)
    score = 0
    role = source_role_from_item(item)
    role_scores = {
        "formal_consolidated_statement": 600,
        "summary_consolidated": 320,
        "mda_financial_review": 180,
        "consolidated_context": 120,
        "unclassified": 0,
        "separate_summary": -700,
        "separate_statement": -750,
        "eps_or_per_share_note": -900,
        "subsidiary_or_investee_table": -1000,
        "segment_table": -1000,
        "fair_value_note": -1000,
        "subsequent_events_note": -1000,
        "non_primary_note_table": -1000,
    }
    score += role_scores.get(role, 0)
    if statement_type == "balance_sheet" and any(token in flat for token in ("연결재무상태표", "consolidatedstatementoffinancialposition", "consolidatedbalancesheet")):
        score += 120
    if statement_type == "income_statement" and any(token in flat for token in ("연결손익계산서", "연결포괄손익계산서", "consolidatedstatementofprofitorloss", "consolidatedstatementofcomprehensiveincome")):
        score += 120
    if statement_type == "cash_flow_statement" and any(token in flat for token in ("연결현금흐름표", "연결현금흐를표", "consolidatedstatementofcashflows")):
        score += 120
    if any(token in text for token in ("요약별도재무정보", "별도재무정보", "별도 재무정보")):
        score -= 350
    if any(token in text for token in ("별도 재무상태표", "별도 손익계산서", "별도 포괄손익계산서", "별도 현금흐름표")):
        score -= 350
    if re.search(r"(?:#\s*)?4\.\s*재무제표", text):
        score -= 500
    if "재무제표" in text and "연결재무제표" not in text and "연결 재무제표" not in text and role != "formal_consolidated_statement":
        score -= 120
    if role != "formal_consolidated_statement" and any(token in text for token in ("주당이익", "기본주당", "희석주당", "earnings per share", "weighted average")):
        score -= 500
    if "주석" in text and not any(token in text for token in ("연결 재무상태표", "연결 포괄손익계산서", "연결 손익계산서", "연결 현금흐름표")):
        score -= 40
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    if evidence.get("source_type") == "dart_pdf_statement_table":
        score += 30
    raw = evidence.get("raw") if isinstance(evidence.get("raw"), dict) else {}
    table = raw.get("table") if isinstance(raw.get("table"), dict) else {}
    if table.get("source_confidence") == "high":
        score += 20
    elif table.get("source_confidence") == "medium":
        score += 10
    try:
        score += int(float(item.get("confidence") or 0) * 10)
    except Exception:
        pass
    return score


def allowed_primary_source_role(role: str) -> bool:
    return role not in {
        "separate_statement",
        "separate_summary",
        "eps_or_per_share_note",
        "subsidiary_or_investee_table",
        "segment_table",
        "fair_value_note",
        "subsequent_events_note",
        "non_primary_note_table",
    }


def formal_statement_type_for_table(table: dict[str, Any]) -> str:
    text = " ".join(str(table.get(key) or "") for key in ("near_text", "heading", "preview"))
    flat = compact(text)
    if "연결재무상태표" in flat:
        return "balance_sheet"
    if "연결포괄손익계산서" in flat or "연결손익계산서" in flat:
        return "income_statement"
    if "연결현금흐름표" in flat:
        return "cash_flow_statement"
    return ""


def period_columns_from_header(header: list[str], row: dict[str, Any]) -> dict[int, str]:
    current = str(row.get("period_end") or "").strip()
    current_year = report_year_from_period(current)
    if not current_year:
        return {}
    result: dict[int, str] = {}
    ordered_offsets = [0, 1, 2, 3, 4]
    for index, cell in enumerate(header[1:], start=1):
        text = str(cell or "")
        offset: int | None = None
        match = re.search(r"제\s*(\d+)\s*기", text)
        if match:
            # Korean DART tables usually order period columns as current,
            # prior, prior-2 even when the fiscal term number is company-specific.
            offset = index - 1
        date_match = re.search(r"(20\d{2}|19\d{2})", text)
        if date_match:
            year = int(date_match.group(1))
            offset = current_year - year
        if offset is None and index - 1 < len(ordered_offsets):
            offset = ordered_offsets[index - 1]
        if offset is not None and 0 <= offset <= 4:
            result[index] = f"{current_year - offset:04d}{current[4:]}"
    return result


def unit_scale_from_table(table: dict[str, Any]) -> tuple[str, str]:
    text = " ".join(str(table.get(key) or "") for key in ("heading", "near_text", "preview"))
    if "억원" in text:
        return "KRW 100 million", "100000000"
    if "십억원" in text:
        return "KRW billion", "1000000000"
    if "백만원" in text:
        return "KRW million", "1000000"
    if "천원" in text:
        return "KRW thousand", "1000"
    if "원" in text:
        return "KRW", "1"
    return "reported", "reported"


def table_markup_at_line(result_md: str, line_number: Any) -> str:
    try:
        start = int(line_number) - 1
    except Exception:
        return ""
    lines = result_md.splitlines()
    if start < 0 or start >= len(lines):
        return ""
    for index in range(start, min(len(lines), start + 8)):
        line = lines[index]
        if "<table" in line and "</table>" in line:
            return line[line.find("<table") : line.rfind("</table>") + len("</table>")]
    return ""


def rescue_metrics_from_formal_tables(row: dict[str, Any], existing_keys: set[tuple[str, str, str]]) -> list[dict[str, Any]]:
    result_md_path = row["result_dir"] / "result_complete.md"
    if not result_md_path.exists():
        return []
    result_md = result_md_path.read_text(encoding="utf-8", errors="ignore")
    allowed_periods = preferred_periods(row)
    rescued: list[dict[str, Any]] = []
    for table in row["table_index"] or []:
        if not isinstance(table, dict):
            continue
        statement_type = formal_statement_type_for_table(table)
        if statement_type not in PRIMARY_CANONICALS:
            continue
        markup = table_markup_at_line(result_md, table.get("line"))
        if not markup:
            continue
        rows = parse_html_table(markup)
        if len(rows) < 2:
            continue
        periods_by_column = period_columns_from_header(rows[0], row)
        if not periods_by_column:
            continue
        for row_index, table_row in enumerate(rows[1:], start=1):
            if not table_row:
                continue
            label = table_row[0]
            canonical = canonical_from_rescue_label(statement_type, label)
            if not canonical or canonical not in PRIMARY_CANONICALS[statement_type]:
                continue
            for column_index, period in periods_by_column.items():
                if allowed_periods and period not in allowed_periods:
                    continue
                if column_index >= len(table_row):
                    continue
                raw_value = table_row[column_index]
                value = to_float(raw_value)
                if value is None:
                    continue
                key = (statement_type, canonical, period)
                if key in existing_keys:
                    continue
                unit, scale = unit_scale_from_table(table)
                source = {
                    "source_type": "formal_consolidated_table_rescue",
                    "source_id": f"kr_formal_table_{table.get('table_index')}",
                    "quote_text": " | ".join(str(part) for part in [label, raw_value] if part not in (None, "")),
                    "md_line": table.get("line"),
                    "pdf_page_number": table.get("pdf_page_number"),
                    "table_index": table.get("table_index"),
                    "row_index": row_index,
                    "column_index": column_index,
                    "heading": table.get("heading"),
                    "statement_title": {
                        "balance_sheet": "연결 재무상태표",
                        "income_statement": "연결 포괄손익계산서",
                        "cash_flow_statement": "연결 현금흐름표",
                    }.get(statement_type, ""),
                    "source_context": re.sub(r"\s+", " ", " ".join(str(table.get(k) or "") for k in ("near_text", "heading", "preview"))).strip()[:CONTEXT_LIMIT],
                    "source_role": "formal_consolidated_statement_rescue",
                    "source_kind": "formal_consolidated_table_rescue",
                    "markdown_path": rel(result_md_path),
                    "task_id": row["task_id"],
                    "period": period,
                }
                source.update(evidence_urls(row["task_id"], source.get("pdf_page_number"), source.get("table_index")))
                rescued.append(
                    {
                        "metric_key": canonical,
                        "metric_name": label,
                        "canonical_name": canonical,
                        "local_name": label,
                        "raw_value": raw_value,
                        "value": value,
                        "unit": unit,
                        "currency": "KRW",
                        "scale": scale,
                        "confidence": "0.85",
                        "statement_type": statement_type,
                        "scope": "consolidated",
                        "period": period,
                        "fiscal_year": report_year_from_period(period),
                        "source": source,
                        "_source_priority": FORMAL_TABLE_RESCUE_PRIORITY,
                    }
                )
                existing_keys.add(key)
    return rescued


def build_three_statements(row: dict[str, Any]) -> dict[str, Any]:
    financial_data = row["financial_data"]
    result_dir = row["result_dir"]
    table_index_payload = row["table_index"]
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
            canonical = str(item.get("canonical_name") or "").strip()
            if not canonical or canonical not in PRIMARY_CANONICALS[statement_type]:
                continue
            period = normalize_period_key(item_period(item, row["period_end"]), row)
            if allowed_periods and period not in allowed_periods:
                continue
            evidence = evidence_from_item(item, result_dir, table_index_payload)
            if not allowed_primary_source_role(str(evidence.get("source_role") or "")):
                continue
            metric = {
                "metric_key": canonical,
                "metric_name": item.get("local_name") or item.get("label") or canonical,
                "canonical_name": canonical,
                "local_name": item.get("local_name") or item.get("label"),
                "raw_value": item.get("raw_value") or item.get("value"),
                "value": to_float(item.get("value")),
                "unit": item.get("unit") or statement.get("unit") or "",
                "currency": item.get("currency") or statement.get("currency") or "KRW",
                "scale": item.get("scale") or statement.get("scale") or "1",
                "confidence": item.get("confidence"),
                "statement_type": statement_type,
                "scope": scope,
                "period": period,
                "fiscal_year": item.get("fiscal_year") or report_year_from_period(period),
                "source": {
                    **evidence,
                    "task_id": row["task_id"],
                    "period": period,
                    "source_kind": evidence.get("source_kind") or "financial_data_statement",
                },
            }
            metric["source"].update(evidence_urls(row["task_id"], evidence.get("pdf_page_number"), evidence.get("table_index")))
            metric["_source_priority"] = source_priority(item, statement_type)
            key = (statement_type, canonical, period)
            previous = metrics_by_key.get(key)
            if not previous or metric["_source_priority"] > previous.get("_source_priority", -9999):
                metrics_by_key[key] = metric
    for metric in rescue_metrics_from_formal_tables(row, set()):
        key = (str(metric.get("statement_type") or ""), str(metric.get("canonical_name") or ""), str(metric.get("period") or ""))
        previous = metrics_by_key.get(key)
        if not previous or metric["_source_priority"] > previous.get("_source_priority", -9999):
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
        "market": "KR",
        "report_id": row["report_id"],
        "period_end": row["period_end"],
        "metrics": metrics,
        "extraction_method": "kr_pdf_financial_data_statement_bridge_v1",
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
            "market": "KR",
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
    weak_roles = {"mda_financial_review", "summary_consolidated", "consolidated_context", "unclassified"}
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
        "strict_policy": "prefer formal consolidated statement; use formal table rescue when parser evidence is misbound; block separate, segment, subsidiary, EPS, fair-value, and subsequent-event note tables",
    }


def build_retrieval_index(row: dict[str, Any], evidence_items: list[dict[str, Any]]) -> dict[str, Any]:
    chunks = []
    for item in evidence_items:
        chunks.append(
            {
                "chunk_id": f"{item['evidence_id']}-chunk",
                "evidence_id": item["evidence_id"],
                "market": "KR",
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
            "market": "KR",
            "company_id": row["company_wiki_id"],
            "company_name": row["company_name"],
            "ticker": row["ticker"],
            "report_id": row["report_id"],
            "topic": "full_report",
            "source_type": "wiki_report_markdown",
            "file": f"reports/{row['report_id']}/report.md",
            "text": f"{row['company_name']} {row['ticker']} {row['report_year']} DART business report full text fallback",
        }
    )
    return {
        "schema_version": "kr_retrieval_index_v1",
        "market": "KR",
        "company_id": row["company_wiki_id"],
        "report_id": row["report_id"],
        "chunk_count": len(chunks),
        "chunks": chunks,
        "generated_at": now_iso(),
    }


def inspect_kr_result(result_dir: Path) -> dict[str, Any] | None:
    metadata = read_json(result_dir / "metadata.json", {})
    financial_data = read_json(result_dir / "financial_data.json", {})
    if str(metadata.get("market") or financial_data.get("market") or "").upper() != "KR":
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
    parsed = parse_kr_filename(filename)
    ticker = str(metadata.get("ticker") or metadata.get("stock_code") or financial_data.get("ticker") or parsed.get("ticker") or "").zfill(6)
    company_name = clean_company_name(metadata.get("company_name") or financial_data.get("company_name") or parsed.get("company_name") or ticker)
    period_end = str(metadata.get("period_end") or financial_data.get("period_end") or parsed.get("period_end") or "")
    report_year = report_year_from_period(period_end) or valid_year(metadata.get("fiscal_year")) or report_year_from_period(filename)
    report_kind = metadata.get("report_kind") or financial_data.get("report_kind") or "kr_business_report"
    report_id = f"{int(report_year)}-{report_kind_slug(report_kind, metadata.get('report_type'))}" if report_year else f"unknown-{result_dir.name[:8]}"
    company_wiki_id = f"{ticker}-{safe_slug(company_name)}"
    warnings: list[str] = []
    if not ticker or ticker == "000000":
        warnings.append("missing_kr_ticker")
    if not company_name or company_name == ticker:
        warnings.append("missing_company_name")
    if not report_year:
        warnings.append("missing_report_year")
    if metadata.get("report_year") and valid_year(metadata.get("report_year")) != report_year:
        warnings.append("metadata_report_year_corrected")
    if len(financial_data.get("statements") or []) < 3:
        warnings.append("missing_three_financial_statements")
    if financial_checks.get("overall_status") == "fail":
        warnings.append("financial_checks_fail")
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
        "period_end": period_end,
        "published_at": parsed.get("published_at") or metadata.get("disclosure_date"),
        "source_id": parsed.get("source_id") or metadata.get("source") or "dart",
        "report_year": int(report_year) if report_year else None,
        "report_kind": report_kind,
        "report_type": metadata.get("report_type") or parsed.get("report_type") or report_kind,
        "report_id": report_id,
        "warnings": warnings,
        "score": (
            (1000 if ticker else 0)
            + (500 if report_year else 0)
            + (300 if financial_checks.get("overall_status") in {"pass", "warning"} else 0)
            + len(financial_data.get("statements") or []) * 100
            + int(quality.get("table_count") or 0)
        ),
    }


def select_active(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    skipped = []
    for row in rows:
        if row["warnings"] and any(w in row["warnings"] for w in ("missing_kr_ticker", "missing_company_name", "missing_report_year")):
            skipped.append({k: row.get(k) for k in ("task_id", "filename", "warnings")})
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
                    "score": item["score"],
                    "selected": index == 0,
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
        "schema_version": "kr_report_wiki_v1",
        "generated_at": now_iso(),
        "identity": {
            "market": "KR",
            "company_id": f"KR:{row['ticker']}",
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
        "- Market: KR",
        f"- Exchange: {company.get('exchange') or 'KRX'}",
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
            "schema_version": "kr_validation_v1",
            "market": "KR",
            "report_id": row["report_id"],
            "financial_checks": row["financial_checks"],
            "three_statement_source": "financial_data.json",
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
        write_json(metrics_dir / "normalized_metrics.json", {"schema_version": "kr_normalized_metrics_v1", "source": "three_statements.json", "metrics": three_statements.get("metrics") or [], "generated_at": now_iso()})
        report_json = build_report_json(row, three_statements, evidence_items, copied)
        write_json(report_dir / "report.json", report_json)
        package_manifest = write_report_package_facade(
            market="KR",
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
            "schema_version": "kr_validation_v1",
            "financial_checks": latest_financial_checks or {},
            "three_statement_metric_count": len((latest_payload or {}).get("metrics") or []),
            "three_statement_source_quality": source_quality_summary((latest_payload or {}).get("metrics") or []),
            "generated_at": now_iso(),
        },
    )
    write_json(company_dir / "metrics" / "latest" / "financial_data.json", latest_financial_data or {})
    write_json(company_dir / "metrics" / "latest" / "financial_checks.json", latest_financial_checks or {})
    write_json(company_dir / "metrics" / "latest" / "normalized_metrics.json", {"schema_version": "kr_normalized_metrics_v1", "source": "three_statements.json", "metrics": (latest_payload or {}).get("metrics") or [], "generated_at": now_iso()})
    write_json(company_dir / "evidence" / "evidence_index.json", {"schema_version": 1, "market": "KR", "company_id": primary["company_wiki_id"], "evidence_count": len(all_evidence), "evidence": all_evidence, "generated_at": now_iso()})
    write_json(company_dir / "evidence" / "pdf_refs.json", {"schema_version": 1, "market": "KR", "company_id": primary["company_wiki_id"], "refs": all_pdf_refs, "generated_at": now_iso()})
    write_json(company_dir / "evidence" / "image_manifest.json", {"schema_version": 1, "market": "KR", "company_id": primary["company_wiki_id"], "images": [], "generated_at": now_iso()})
    write_json(company_dir / "evidence" / "source_map_latest.json", {"schema_version": "kr_source_map_latest_v1", "source": "evidence_index.json", "latest_report_id": primary["report_id"], "generated_at": now_iso()})
    write_json(company_dir / "semantic" / "retrieval_index.json", build_retrieval_index(primary, all_evidence))
    for name, payload in {
        "subject_profile.json": {"schema_version": "kr_subject_profile_v1", "market": "KR", "company_id": primary["company_wiki_id"], "company_name": primary["company_name"], "ticker": primary["ticker"], "generated_at": now_iso()},
        "segments.json": {"schema_version": "kr_segments_v1", "segments": [], "note": "Rule-based segment extraction is deferred; use report.md full-text fallback.", "generated_at": now_iso()},
        "facts.json": {"schema_version": "kr_facts_v1", "facts": [], "generated_at": now_iso()},
        "relations.json": {"schema_version": "kr_relations_v1", "relations": [], "generated_at": now_iso()},
        "claims.json": {"schema_version": "kr_claims_v1", "claims": [], "generated_at": now_iso()},
        "document_links.json": {"schema_version": "kr_document_links_v1", "links": [], "note": "KR note relation extraction is deferred; use report.md/document_full fallback.", "generated_at": now_iso()},
        "note_links.json": {"schema_version": "kr_note_links_v1", "links": [], "generated_at": now_iso()},
        "evidence_semantic.json": {"schema_version": "kr_evidence_semantic_v1", "items": [], "generated_at": now_iso()},
        "extraction_log.json": {"schema_version": "kr_semantic_extraction_log_v1", "steps": [], "generated_at": now_iso()},
    }.items():
        write_json(company_dir / "semantic" / name, payload)
    write_json(company_dir / "graph" / "graph_index.json", {"schema_version": "kr_graph_index_v1", "market": "KR", "company_id": primary["company_wiki_id"], "nodes": [], "edges": [], "generated_at": now_iso()})
    write_text(company_dir / "graph" / "company.md", f"# {primary['company_name']}\n\nKR company graph workspace.\n")
    write_text(company_dir / "graph" / "report.md", f"# {primary['company_name']} Reports\n\nPrimary report: {primary['report_id']}.\n")
    write_text(company_dir / "analysis" / "README.md", f"# {primary['company_name']} Analysis Workspace\n\nAll important conclusions must cite metrics/evidence/report sources.\n")
    write_text(company_dir / "obsidian" / "README.md", f"# {primary['company_name']} Obsidian Workspace\n")
    write_text(company_dir / "obsidian" / "index.md", f"# {primary['company_name']}\n\n- [[../company.md|Company]]\n")

    company_json = {
        "schema_version": "kr_company_wiki_v1",
        "market": "KR",
        "company_id": f"KR:{primary['ticker']}",
        "company_wiki_id": primary["company_wiki_id"],
        "company_wiki_path": rel(company_dir),
        "stock_code": primary["ticker"],
        "ticker": primary["ticker"],
        "exchange": "KRX",
        "company_short_name": primary["company_name"],
        "company_full_name": primary["company_name"],
        "company_name": primary["company_name"],
        "aliases": sorted({primary["ticker"], primary["company_name"], safe_slug(primary["company_name"])}),
        "currency": "KRW",
        "accounting_standard": (latest_financial_data or {}).get("accounting_standard") or "KIFRS",
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
    write_json(company_dir / "_index.json", {"schema_version": "kr_company_index_v1", "market": "KR", "company_id": primary["company_wiki_id"], "primary_report_id": primary["report_id"], "reports": reports, "status": status, "updated_at": now_iso()})
    write_text(company_dir / "company.md", build_company_md(company_json))
    return {"company": company_json, "reports": reports, "evidence_count": len(all_evidence), "status": status}


def kr_agent_guide() -> str:
    return """# KR Wiki Agent Guide

This market wiki follows the A-share company workspace contract with KR/DART-specific evidence rules.

Default routing:

1. Read `_meta/company_catalog.json` to resolve the KRX ticker or company alias.
2. Read `companies/<company_wiki_id>/company.json` and choose `primary_report_id` unless the user specified a year/report.
3. For financial statement values, read `metrics/reports/<report_id>/three_statements.json`, then `key_metrics.json` and `validation.json`.
4. Use `evidence/evidence_index.json` for source `task_id`, `pdf_page_number`, `table_index`, and `md_line`.
5. Use `reports/<report_id>/report.md` and `document_full.json` only for full-text fallback, notes, subsidiaries, segments, and cross-checks.

KR-specific rules:

- Primary financial extraction uses consolidated (`연결`) statements only.
- Separate/standalone (`별도`) tables and EPS/주당이익 note tables are not primary metric sources.
- Subsidiary/segment queries use full-text fallback through `report.md`, `document_full.json`, and table evidence.
- Financial warnings are preserved in `validation.json`; they do not replace source evidence.
"""


def kr_readme(company_count: int, report_count: int) -> str:
    return f"""# KR Company Wiki

This directory is generated from standardized PDF parser results for Korean DART PDF business reports.

- Companies: `{company_count}`
- Reports: `{report_count}`
- Primary source: `data/pdf-parser/results`
- Main agent entrypoints: `_meta/company_catalog.json`, company `company.json`, `metrics/reports/<report_id>/three_statements.json`, `evidence/evidence_index.json`.

The KR Wiki keeps consolidated financial statements as the primary structured metric layer. Separate, subsidiary, and segment details are available through full-text/table fallback and are not promoted into primary metrics.
"""


def write_market_root(output_root: Path, company_results: list[dict[str, Any]], selection: dict[str, Any], source_results_dir: Path) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    for directory in ("_meta", "derived", "_quarantine", "_trash", "companies"):
        (output_root / directory).mkdir(exist_ok=True)
    updated_company_ids = {str(item["company"].get("company_wiki_id") or "") for item in company_results}
    existing_companies = read_json(output_root / "_meta" / "company_catalog.json", {}).get("companies") or []
    existing_reports = read_json(output_root / "_meta" / "report_catalog.json", {}).get("reports") or []
    companies = [item for item in existing_companies if str(item.get("company_wiki_id") or "") not in updated_company_ids]
    reports = [item for item in existing_reports if str(item.get("company_wiki_id") or "") not in updated_company_ids]
    issues = []
    latest = read_json(output_root / "derived" / "three_statements_latest.json", {}) or {}
    for result in company_results:
        company = result["company"]
        companies.append(
            {
                "market": "KR",
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
            reports.append({**report, "market": "KR", "company_wiki_id": company.get("company_wiki_id"), "company_path": company.get("company_wiki_path"), "ticker": company.get("ticker"), "company_name": company.get("company_name")})
            if report.get("status") != "ready":
                issues.append({"company_wiki_id": company.get("company_wiki_id"), "report_id": report.get("report_id"), "status": report.get("status")})
        latest_path = output_root / "companies" / str(company.get("company_wiki_id")) / "metrics" / "latest" / "three_statements.json"
        latest[company.get("ticker")] = read_json(latest_path, {})
    companies.sort(key=lambda item: str(item.get("ticker") or ""))
    reports.sort(key=lambda item: (str(item.get("ticker") or ""), str(item.get("report_id") or "")))
    write_json(output_root / "_meta" / "company_catalog.json", {"schema_version": "kr_company_catalog_v1", "market": "KR", "generated_at": now_iso(), "company_count": len(companies), "companies": companies})
    write_json(output_root / "_meta" / "report_catalog.json", {"schema_version": "kr_report_catalog_v1", "market": "KR", "generated_at": now_iso(), "report_count": len(reports), "reports": reports})
    write_json(output_root / "_meta" / "market_profile.json", {"schema_version": "kr_market_profile_v1", "market": "KR", "source": "DART PDF business reports", "company_id_rule": "<6-digit-krx-code>-<company-slug>", "report_id_rule": "<year>-<report-kind-slug>", "primary_statement_scope": "consolidated", "subsidiary_relation_policy": "not_structured_use_full_text_fallback", "accounting_standard": "KIFRS", "generated_at": now_iso()})
    write_json(output_root / "_meta" / "ingestion_manifest.json", {"schema_version": "kr_ingestion_manifest_v1", "market": "KR", "generated_at": now_iso(), "source_results_dir": rel(source_results_dir), "company_count": len(companies), "report_count": len(reports), "selection": selection})
    write_json(output_root / "_meta" / "quality_summary.json", {"schema_version": "kr_quality_summary_v1", "market": "KR", "generated_at": now_iso(), "company_count": len(companies), "report_count": len(reports), "status_counts": dict(Counter(item.get("status") for item in reports)), "issue_count": len(issues)})
    write_json(output_root / "_meta" / "extraction_issues.json", {"schema_version": "kr_extraction_issues_v1", "market": "KR", "generated_at": now_iso(), "issues": issues, "selection": selection})
    write_json(output_root / "derived" / "three_statements_latest.json", latest)
    guide = kr_agent_guide()
    write_text(output_root / "_meta" / "AGENT_GUIDE.md", guide)
    write_text(output_root / "AGENTS.md", guide)
    write_text(output_root / "README.md", kr_readme(len(companies), len(reports)))


def build_plan(results_dir: Path, *, task_id: str = "") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = []
    for result_dir in sorted(results_dir.iterdir()):
        if not result_dir.is_dir():
            continue
        if task_id and result_dir.name != task_id:
            continue
        row = inspect_kr_result(result_dir)
        if row:
            rows.append(row)
    return select_active(rows)


def run(args: argparse.Namespace) -> dict[str, Any]:
    results_dir = args.results_dir.resolve()
    output_root = args.output_root.resolve()
    active, selection = build_plan(results_dir, task_id=str(getattr(args, "task_id", "") or "").strip())
    if args.limit:
        active = active[: args.limit]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in active:
        grouped[row["company_wiki_id"]].append(row)
    plan = {
        "market": "KR",
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
    parser = argparse.ArgumentParser(description="Ingest KR PDF parser results into an A-share-aligned company Wiki workspace.")
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--task-id", default="", help="Only ingest the selected parser task directory.")
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
