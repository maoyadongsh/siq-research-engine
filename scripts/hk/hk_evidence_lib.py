from __future__ import annotations

import html
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES_SRC = REPO_ROOT / "services" / "market-report-rules" / "src"
if str(RULES_SRC) not in sys.path:
    sys.path.insert(0, str(RULES_SRC))

from market_report_rules_service.contracts import financial_checks_contract, financial_data_contract
from market_report_rules_service.evidence_package import (
    SCHEMA_VERSION,
    build_quality_report,
    compute_artifact_hashes,
    normalized_metrics_from_financial_data,
    source_map_from_financial_data,
    stable_id,
    stable_parse_run_id,
    validate_evidence_package,
    write_json,
)
from market_report_rules_service.load_plan import build_load_plan
from market_report_rules_service.models import (
    AccountingStandard,
    EvidenceRef,
    ExtractedFact,
    ExtractionResult,
    FinancialStatement,
    Market,
    ParsedArtifact,
    ParsedTable,
    StatementType,
)
from market_report_rules_service.normalization import compact_label, infer_currency, parse_date
from market_report_rules_service.pipeline import build_package_aware_load_plan, process_artifact
from market_report_rules_service.statement_detection import detect_statement_type_from_rows, detect_statement_type_from_title
from market_report_rules_service.validation import validate_extraction


PARSER_VERSION = os.environ.get("SIQ_HK_PARSER_VERSION", "hk_pdf_evidence_parser_v1")
RULES_VERSION = os.environ.get("SIQ_HK_RULES_VERSION", "hkex_rules_v1")
_BANK_CODES = {"00005", "00939", "01288", "01398", "02388", "03968", "03988"}
_INSURANCE_CODES = {"01299", "02318", "02328", "02628"}


class _TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[dict[str, Any]]] = []
        self._row: list[dict[str, Any]] | None = None
        self._cell: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            attr_map = {name.lower(): value for name, value in attrs}
            self._cell = {
                "parts": [],
                "rowspan": _safe_int(attr_map.get("rowspan"), 1),
                "colspan": _safe_int(attr_map.get("colspan"), 1),
            }

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell["parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(
                {
                    "text": _clean_cell(" ".join(self._cell["parts"])),
                    "rowspan": max(1, int(self._cell.get("rowspan") or 1)),
                    "colspan": max(1, int(self._cell.get("colspan") or 1)),
                }
            )
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(cell.get("text") for cell in self._row):
                self.rows.append(self._row)
            self._row = None


def read_json(path: Path, default: Any = None) -> Any:
    if not path or not path.exists() or not path.is_file():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def _clean_cell(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _html_table_rows(table_body: str) -> list[list[str]]:
    parser = _TableHTMLParser()
    parser.feed(table_body or "")
    return _expand_spans(parser.rows)


def _safe_int(value: Any, default: int) -> int:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _expand_spans(raw_rows: list[list[dict[str, Any]]]) -> list[list[str]]:
    rows: list[list[str]] = []
    pending: dict[int, tuple[str, int]] = {}
    max_width = 0

    for raw_row in raw_rows:
        row: list[str] = []
        col = 0
        next_pending: dict[int, tuple[str, int]] = {}

        def fill_pending_cells() -> None:
            nonlocal col
            while col in pending:
                text, remaining = pending[col]
                row.append(text)
                if remaining > 1:
                    next_pending[col] = (text, remaining - 1)
                col += 1

        for cell in raw_row:
            fill_pending_cells()
            text = str(cell.get("text") or "")
            colspan = max(1, int(cell.get("colspan") or 1))
            rowspan = max(1, int(cell.get("rowspan") or 1))
            for offset in range(colspan):
                row.append(text)
                if rowspan > 1:
                    next_pending[col + offset] = (text, rowspan - 1)
            col += colspan

        while True:
            later_cols = [idx for idx in pending if idx >= col]
            if not later_cols:
                break
            target = min(later_cols)
            while col < target:
                row.append("")
                col += 1
            fill_pending_cells()

        pending = next_pending
        max_width = max(max_width, len(row))
        rows.append(row)

    if max_width:
        rows = [row + [""] * (max_width - len(row)) for row in rows]
    return rows


def infer_metadata(pdf_path: Path, metadata_path: Path | None = None) -> dict[str, Any]:
    metadata = read_json(metadata_path or pdf_path.with_suffix(pdf_path.suffix + ".metadata.json"), {})
    candidate = metadata.get("candidate") if isinstance(metadata, dict) else {}
    if not isinstance(candidate, dict):
        candidate = {}
    stem_parts = pdf_path.stem.split("_")
    company_name = candidate.get("company_name") or (stem_parts[0] if stem_parts else pdf_path.stem)
    ticker = candidate.get("ticker") or candidate.get("company_id") or (stem_parts[2] if len(stem_parts) > 2 else "UNKNOWN")
    period_end = candidate.get("report_end") or candidate.get("period_end") or _filename_date(pdf_path.name)
    fiscal_year = _int_or_none(str(period_end or "")[:4]) or _int_or_none(candidate.get("year"))
    published_at = candidate.get("published_at") or (stem_parts[5] if len(stem_parts) > 5 and re.match(r"\d{4}-\d{2}-\d{2}", stem_parts[5]) else None)
    report_type = _report_type(candidate.get("report_type") or candidate.get("report_family") or candidate.get("form") or pdf_path.parent.name)
    return {
        "raw_metadata": metadata,
        "company_id": f"HK:{ticker}",
        "ticker": str(ticker).zfill(5) if str(ticker).isdigit() and len(str(ticker)) < 5 else str(ticker),
        "company_name": company_name,
        "source_id": candidate.get("source_id") or "hkex",
        "form": candidate.get("form") or report_type,
        "report_type": report_type,
        "fiscal_year": fiscal_year,
        "fiscal_period": _fiscal_period(report_type),
        "period_end": period_end,
        "published_at": published_at,
        "source_url": candidate.get("document_url") or candidate.get("source_url") or candidate.get("landing_url"),
        "accession_number": candidate.get("accession_number") or pdf_path.stem.rsplit("_", 1)[-1],
        "accounting_standard": _accounting_standard(metadata),
        "language": candidate.get("language"),
        "industry_profile": candidate.get("industry_profile") or _infer_industry_profile(str(ticker), company_name),
    }


def _infer_industry_profile(ticker: str, company_name: str) -> str:
    code = str(ticker or "").zfill(5)
    raw_name = str(company_name or "")
    name = raw_name.upper()
    if code in _BANK_CODES or "BANK" in name or "银行" in raw_name or "銀行" in raw_name:
        return "bank"
    if code in _INSURANCE_CODES or "INSURANCE" in name or "LIFE" in name or "AIA" in name or "保险" in raw_name or "保險" in raw_name:
        return "insurance"
    if any(token in name for token in ("TENCENT", "BABA", "MEITUAN", "JD-", "KUAISHOU", "NTES", "BIDU", "LI-AUTO")):
        return "internet_platform"
    if any(token in name for token in ("PETRO", "CNOOC", "SINOPEC", "SHENHUA", "COPPER", "ENERGY")):
        return "energy"
    if any(token in name for token in ("SEMICONDUCTOR", "SMIC", "HUA HONG")):
        return "semiconductor"
    if any(token in name for token in ("MOTOR", "AUTO", "ELECTRIC", "COPPER", "CRRC", "HAIER", "SUNNY", "BYD")):
        return "manufacturing"
    return "general"


def parsed_tables_from_document_full(document_full: dict[str, Any], enhanced_payload: dict[str, Any] | None = None) -> list[ParsedTable]:
    content = document_full.get("content_list") or []
    enhanced = enhanced_payload or document_full.get("content_list_enhanced") or {}
    enhanced_tables = enhanced.get("tables") if isinstance(enhanced, dict) else []
    enhanced_by_source: dict[int, dict[str, Any]] = {}
    enhanced_by_index: dict[int, dict[str, Any]] = {}
    if isinstance(enhanced_tables, list):
        for item in enhanced_tables:
            if not isinstance(item, dict):
                continue
            if item.get("content_table_source_id") is not None:
                enhanced_by_source[int(item["content_table_source_id"])] = item
            if item.get("table_index") is not None:
                enhanced_by_index[int(item["table_index"])] = item

    parsed: list[ParsedTable] = []
    table_counter = 0
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "table":
                continue
            rows = _html_table_rows(str(item.get("table_body") or ""))
            if not rows:
                continue
            table_counter += 1
            meta = enhanced_by_source.get(table_counter) or enhanced_by_index.get(table_counter) or {}
            title = _table_title(item, meta)
            unit = _infer_unit(title, rows)
            raw = {
                **meta,
                "content_table_source_id": table_counter,
                "bbox": item.get("bbox") or meta.get("bbox"),
                "source_image_path": item.get("img_path") or meta.get("source_image_path"),
                "source_caption": item.get("table_caption") or meta.get("source_caption"),
                "source_footnote": item.get("table_footnote") or meta.get("source_footnote"),
                "preview": meta.get("preview") or " ".join(" | ".join(row[:5]) for row in rows[:4])[:500],
            }
            parsed.append(
                ParsedTable(
                    table_id=f"hk_table_{table_counter:04d}",
                    title=title,
                    rows=rows,
                    page_number=meta.get("pdf_page_number") or _page_number(item),
                    table_index=table_counter,
                    unit=unit,
                    currency=infer_currency(unit, title, default=None),
                    raw=raw,
                )
            )
    if parsed:
        return parsed

    if isinstance(enhanced_tables, list):
        for item in enhanced_tables:
            if not isinstance(item, dict):
                continue
            raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
            rows = _rows_from_enhanced_table(item, raw)
            if not rows or max(len(row) for row in rows) < 2:
                continue
            index = int(item.get("table_index") or len(parsed) + 1)
            title = _table_title({}, item)
            parsed.append(
                ParsedTable(
                    table_id=f"hk_table_{index:04d}",
                    title=title,
                    rows=rows,
                    page_number=item.get("pdf_page_number") or raw.get("pdf_page_number"),
                    table_index=index,
                    unit=item.get("unit"),
                    currency=item.get("currency"),
                    raw=item,
                )
            )
    return parsed


def _rows_from_enhanced_table(item: dict[str, Any], raw: dict[str, Any]) -> list[list[str]]:
    structure = raw.get("structure") if isinstance(raw.get("structure"), dict) else {}
    preview_rows = structure.get("header_preview") if isinstance(structure.get("header_preview"), list) else []
    rows: list[list[str]] = []
    for row in preview_rows:
        cells = [_clean_cell(cell) for cell in str(row or "").split("|")]
        if any(cells):
            rows.append(cells)
    if rows:
        return rows
    preview = str(raw.get("preview") or item.get("preview") or "")
    cells = [_clean_cell(cell) for cell in re.split(r"\s{2,}|\s+\|\s+", preview) if _clean_cell(cell)]
    return [cells] if len(cells) >= 2 else []


def _document_full_with_sidecars(parser_result_dir: Path) -> dict[str, Any]:
    document_full = read_json(parser_result_dir / "document_full.json", {})
    if not isinstance(document_full, dict):
        document_full = {}
    content = document_full.get("content_list")
    if not isinstance(content, list) or not content:
        sidecar_content = read_json(parser_result_dir / "content_list.json", [])
        if isinstance(sidecar_content, dict):
            sidecar_content = sidecar_content.get("content_list") or sidecar_content.get("items") or []
        if isinstance(sidecar_content, list) and sidecar_content:
            document_full["content_list"] = sidecar_content
    enhanced = document_full.get("content_list_enhanced")
    if not isinstance(enhanced, dict) or not enhanced:
        sidecar_enhanced = read_json(parser_result_dir / "content_list_enhanced.json", {})
        if isinstance(sidecar_enhanced, dict) and sidecar_enhanced:
            document_full["content_list_enhanced"] = sidecar_enhanced
    return document_full


def _markdown_statement_tables(parser_result_dir: Path, *, start_index: int = 0) -> list[ParsedTable]:
    markdown_path = _best_markdown_path(parser_result_dir)
    if not markdown_path:
        return []
    text = markdown_path.read_text(encoding="utf-8", errors="ignore")
    tables: list[ParsedTable] = []
    seen: set[str] = set()
    seen_starts: set[int] = set()
    heading_pattern = re.compile(
        r"(?im)^#{1,6}\s+([^\n]*(?:statement of financial position|statement of profit or loss|statement of cash flows|cash flow statement|consolidated cash flow statement|consolidated balance sheet|consolidated income statement|balance sheet|income statement|cash flow|cash flows)[^\n]*)"
    )
    for match in heading_pattern.finditer(text):
        heading = _clean_cell(match.group(1))
        statement_type = _markdown_statement_type_from_heading(heading)
        if statement_type is None or not _is_primary_markdown_statement_heading(heading):
            continue
        table_start = text.find("<table", match.end())
        if table_start < 0 or table_start - match.end() > 2500:
            continue
        table_end = text.find("</table>", table_start)
        if table_end < 0:
            continue
        table_html = text[table_start : table_end + len("</table>")]
        rows = _html_table_rows(table_html)
        if not _usable_statement_rows(rows):
            continue
        signature = stable_id(statement_type.value, heading, rows[:6])
        if signature in seen:
            continue
        seen.add(signature)
        seen_starts.add(table_start)
        table_index = start_index + len(tables) + 1
        unit = _infer_unit(heading, rows)
        line_number = text.count("\n", 0, table_start) + 1
        tables.append(
            ParsedTable(
                table_id=f"hk_md_table_{table_index:04d}",
                title=heading,
                rows=rows,
                page_number=None,
                table_index=table_index,
                unit=unit,
                currency=infer_currency(unit, heading, default=None),
                raw={
                    "source": "result_markdown_statement_table",
                    "markdown_path": str(markdown_path),
                    "line": line_number,
                    "heading": heading,
                    "statement_type": statement_type.value,
                    "preview": " ".join(" | ".join(row[:5]) for row in rows[:6])[:800],
                },
            )
        )
    formal_window = _formal_statement_window(text)
    if formal_window:
        window_start, window_end = formal_window
        for match in re.finditer(r"<table\b.*?</table>", text, flags=re.I | re.S):
            table_start = match.start()
            if table_start in seen_starts or table_start < window_start or table_start > window_end:
                continue
            rows = _html_table_rows(match.group(0))
            if not _usable_statement_rows(rows):
                continue
            heading = _nearest_markdown_heading(text, table_start)
            statement_type = _markdown_statement_type_from_heading(heading) or detect_statement_type_from_rows(rows)
            if statement_type is None:
                continue
            if not _is_primary_markdown_statement_body(statement_type, rows):
                continue
            title = heading if _markdown_statement_type_from_heading(heading) else _statement_type_title(statement_type)
            signature = stable_id(statement_type.value, title, rows[:6])
            if signature in seen:
                continue
            seen.add(signature)
            seen_starts.add(table_start)
            table_index = start_index + len(tables) + 1
            unit = _infer_unit(title, rows)
            line_number = text.count("\n", 0, table_start) + 1
            tables.append(
                ParsedTable(
                    table_id=f"hk_md_table_{table_index:04d}",
                    title=title,
                    rows=rows,
                    page_number=None,
                    table_index=table_index,
                    unit=unit,
                    currency=infer_currency(unit, title, default=None),
                    raw={
                        "source": "result_markdown_formal_statement_window",
                        "markdown_path": str(markdown_path),
                        "line": line_number,
                        "heading": title,
                        "statement_type": statement_type.value,
                        "preview": " ".join(" | ".join(row[:5]) for row in rows[:6])[:800],
                    },
                )
            )
    return tables


def _formal_statement_window(text: str) -> tuple[int, int] | None:
    lowered = text.lower()
    start_candidates = []
    comprise_match = re.search(r"the consolidated financial statements.{0,300}?comprise", lowered, flags=re.S)
    if comprise_match:
        start_candidates.append(comprise_match.start())
    for marker in (
        "independent auditor",
        "independent auditor's report",
        "independent auditor’s report",
    ):
        pos = lowered.find(marker)
        if pos >= 0:
            start_candidates.append(pos)
    if not start_candidates:
        return None
    start = max(start_candidates)
    end_candidates = []
    for pattern in (
        r"(?im)^#{1,6}\s+notes to the consolidated financial statements",
        r"(?im)^#{1,6}\s+notes to consolidated financial statements",
        r"(?im)^#{1,6}\s+notes to the financial statements",
    ):
        match = re.search(pattern, text[start:])
        if match:
            end_candidates.append(start + match.start())
    end = min(end_candidates) if end_candidates else len(text)
    return start, end if end > start else len(text)


def _nearest_markdown_heading(text: str, table_start: int) -> str:
    prefix = text[max(0, table_start - 2500) : table_start]
    matches = list(re.finditer(r"(?im)^#{1,6}\s+([^\n]+)", prefix))
    return _clean_cell(matches[-1].group(1)) if matches else ""


def _statement_type_title(statement_type: StatementType) -> str:
    if statement_type == StatementType.BALANCE_SHEET:
        return "Consolidated Statement of Financial Position"
    if statement_type == StatementType.INCOME_STATEMENT:
        return "Consolidated Income Statement"
    if statement_type == StatementType.CASH_FLOW_STATEMENT:
        return "Consolidated Statement of Cash Flows"
    return statement_type.value.replace("_", " ").title()


def _is_primary_markdown_statement_body(statement_type: StatementType, rows: list[list[str]]) -> bool:
    compact = compact_label(" ".join(" ".join(row[:4]) for row in rows[:80]))
    if statement_type == StatementType.BALANCE_SHEET:
        return (
            "totalassets" in compact
            or ("noncurrentassets" in compact and "currentassets" in compact)
            or ("totalliabilities" in compact and "totalequity" in compact)
        )
    if statement_type == StatementType.INCOME_STATEMENT:
        return (
            any(term in compact for term in ("revenue", "revenues", "turnover", "收益", "收入"))
            and any(term in compact for term in ("profitbeforetax", "profitbeforeincometax", "除稅前", "除税前"))
            and any(term in compact for term in ("profitfortheyear", "profitfortheperiod", "年內溢利", "年内溢利", "netincome"))
        )
    if statement_type == StatementType.CASH_FLOW_STATEMENT:
        return (
            any(term in compact for term in ("cashflowsfromoperatingactivities", "netcashflowsgeneratedfromoperatingactivities", "cashgeneratedfromoperations", "經營活動", "经营活动"))
            and any(term in compact for term in ("cashflowsfrominvestingactivities", "investingactivities", "投資活動", "投资活动"))
            and any(term in compact for term in ("cashflowsfromfinancingactivities", "financingactivities", "融資活動", "融资活动"))
        )
    return False


def _best_markdown_path(parser_result_dir: Path) -> Path | None:
    for candidate in (
        parser_result_dir / "result_complete.md",
        parser_result_dir / "result.md",
        parser_result_dir / "document.md",
    ):
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    return None


def _is_primary_markdown_statement_heading(heading: str) -> bool:
    normalized = compact_label(heading)
    if re.match(r"^\d", normalized):
        normalized = re.sub(r"^\d+", "", normalized)
    normalized = normalized.lstrip(".-")
    if any(
        token in normalized
        for token in (
            "noteto",
            "notes",
            "analysis",
            "summary",
            "fiveyear",
            "amountsrecognised",
            "amountsrecognized",
            "fairvaluemeasurements",
            "ofthecompany",
            "companylevel",
        )
    ):
        return False
    if "consolidatedbalancesheet" in normalized or "consolidatedincomestatement" in normalized:
        return True
    if normalized in {"cashflow", "cashflows"} or re.fullmatch(r"\d*cashflows?", normalized):
        return True
    return normalized.startswith(
        (
            "consolidatedstatementof",
            "statementof",
            "consolidatedcashflowstatement",
            "cashflowstatement",
        )
    )


def _markdown_statement_type_from_heading(heading: str) -> StatementType | None:
    detected = detect_statement_type_from_title(heading)
    if detected is not None:
        return detected
    normalized = compact_label(heading)
    if normalized in {"cashflow", "cashflows"} or re.fullmatch(r"\d*cashflows?", normalized):
        return StatementType.CASH_FLOW_STATEMENT
    return None


def _usable_statement_rows(rows: list[list[str]]) -> bool:
    if len(rows) < 3:
        return False
    if max((len(row) for row in rows), default=0) < 2:
        return False
    numeric_rows = 0
    for row in rows:
        if any(re.search(r"\(?-?[\d,]+(?:\.\d+)?\)?", str(cell or "")) for cell in row[1:]):
            numeric_rows += 1
    return numeric_rows >= 2


def build_hk_artifact(pdf_path: Path, parser_result_dir: Path, metadata_path: Path | None = None) -> tuple[ParsedArtifact, dict[str, Any], dict[str, Any]]:
    document_full = _document_full_with_sidecars(parser_result_dir)
    metadata = infer_metadata(pdf_path, metadata_path)
    tables = parsed_tables_from_document_full(document_full)
    tables.extend(_markdown_statement_tables(parser_result_dir, start_index=len(tables)))
    artifact = ParsedArtifact(
        artifact_id=f"HK:{metadata['ticker']}:{metadata['accession_number']}",
        market=Market.HK,
        company_id=metadata["company_id"],
        ticker=metadata["ticker"],
        company_name=metadata["company_name"],
        report_id=f"HK:{metadata['ticker']}:{metadata['accession_number']}",
        report_type=metadata["report_type"],
        report_form=metadata["form"],
        fiscal_year=metadata["fiscal_year"],
        fiscal_period=metadata["fiscal_period"],
        period_end=parse_date(metadata["period_end"]),
        accounting_standard=AccountingStandard(metadata["accounting_standard"]),
        industry_profile=metadata.get("industry_profile") or "general",
        currency=_default_currency(metadata),
        unit=_default_unit(document_full),
        source_url=metadata["source_url"],
        source_files={"pdf": str(pdf_path), "parser_result": str(parser_result_dir)},
        tables=tables,
        document_full=document_full,
        metadata=metadata,
    )
    return artifact, metadata, document_full


_HK_LIABILITY_CANONICAL_NAMES = {
    "borrowings",
    "contract_liabilities",
    "current_liabilities",
    "lease_liabilities",
    "non_current_liabilities",
    "total_liabilities",
}


def _should_use_parser_financial_data(extraction: ExtractionResult, parser_financial_data: dict[str, Any]) -> bool:
    if not isinstance(parser_financial_data, dict):
        return False
    statements = parser_financial_data.get("statements") if isinstance(parser_financial_data.get("statements"), list) else []
    key_metrics = parser_financial_data.get("key_metrics") if isinstance(parser_financial_data.get("key_metrics"), list) else []
    operating_metrics = parser_financial_data.get("operating_metrics") if isinstance(parser_financial_data.get("operating_metrics"), list) else []
    if not any((statements, key_metrics, operating_metrics)):
        return False
    extracted_statement_types = {statement.statement_type for statement in extraction.statements if statement.items}
    parser_statement_types = {
        _statement_type(row.get("statement_type"))
        for row in statements
        if isinstance(row, dict) and isinstance(row.get("items"), list) and row.get("items")
    }
    required = {StatementType.BALANCE_SHEET, StatementType.INCOME_STATEMENT, StatementType.CASH_FLOW_STATEMENT}
    return len(parser_statement_types & required) > len(extracted_statement_types & required)


def _has_parser_financial_data(parser_financial_data: dict[str, Any]) -> bool:
    if not isinstance(parser_financial_data, dict):
        return False
    for key in ("statements", "key_metrics", "operating_metrics"):
        rows = parser_financial_data.get(key)
        if isinstance(rows, list) and rows:
            return True
    return False


def _merge_parser_and_markdown_extractions(parser_extraction: ExtractionResult, markdown_extraction: ExtractionResult) -> ExtractionResult:
    parser_statements = {
        statement.statement_type: statement
        for statement in parser_extraction.statements
        if statement.items
    }
    merged_statements: list[FinancialStatement] = []
    for statement_type in (
        StatementType.BALANCE_SHEET,
        StatementType.INCOME_STATEMENT,
        StatementType.CASH_FLOW_STATEMENT,
    ):
        parser_statement = parser_statements.get(statement_type)
        if parser_statement is not None:
            merged_statements.append(parser_statement)
            continue
        markdown_statement = next(
            (statement for statement in markdown_extraction.statements if statement.statement_type == statement_type and statement.items),
            None,
        )
        if markdown_statement is not None:
            merged_statements.append(markdown_statement)
    return parser_extraction.model_copy(
        update={
            "statements": merged_statements,
            "key_metrics": parser_extraction.key_metrics or markdown_extraction.key_metrics,
            "operating_metrics": parser_extraction.operating_metrics or markdown_extraction.operating_metrics,
            "warnings": _unique_values(
                [
                    *parser_extraction.warnings,
                    *markdown_extraction.warnings,
                    "HK wiki metrics merged parser financial_data with markdown statement-table fallback.",
                ]
            ),
        }
    )


def _extraction_from_financial_data_contract(financial_data: dict[str, Any], artifact: ParsedArtifact) -> ExtractionResult:
    warnings = list(financial_data.get("warnings") or [])
    warnings.append("HK wiki metrics were rebuilt from parser financial_data because parser table rows were unavailable.")
    statements: list[FinancialStatement] = []
    for row in financial_data.get("statements") or []:
        if not isinstance(row, dict):
            continue
        statement_type = _statement_type(row.get("statement_type"))
        items = _facts_from_contract_items(row.get("items") or [], statement_type, artifact, warnings)
        statements.append(
            FinancialStatement(
                statement_id=str(row.get("statement_id") or statement_type.value),
                statement_type=statement_type,
                statement_name=str(row.get("statement_name") or statement_type.value.replace("_", " ").title()),
                scope=str(row.get("scope") or "consolidated"),
                scope_name=row.get("scope_name"),
                title=row.get("title"),
                unit=row.get("unit"),
                scale=_decimal(row.get("scale"), Decimal("1")),
                currency=row.get("currency") or artifact.currency,
                table_indexes=[int(value) for value in row.get("table_indexes") or [] if _int_or_none(value) is not None],
                columns=row.get("columns") if isinstance(row.get("columns"), list) else [],
                items=items,
            )
        )
    key_metrics = _facts_from_contract_items(financial_data.get("key_metrics") or [], StatementType.KEY_METRICS, artifact, warnings)
    operating_metrics = _facts_from_contract_items(financial_data.get("operating_metrics") or [], StatementType.OPERATING_METRICS, artifact, warnings)
    return ExtractionResult(
        schema_version=1,
        rule_version=str(financial_data.get("rule_version") or RULES_VERSION),
        profile_id=str(financial_data.get("profile_id") or "hk_pdf_table_hybrid_v1"),
        artifact_id=artifact.artifact_id,
        market=Market.HK,
        accounting_standard=artifact.accounting_standard,
        industry_profile=artifact.industry_profile,
        company_overrides=artifact.company_overrides,
        company_id=artifact.company_id,
        ticker=artifact.ticker,
        company_name=artifact.company_name,
        report_id=artifact.report_id,
        report_type=artifact.report_type,
        report_form=artifact.report_form,
        fiscal_year=artifact.fiscal_year,
        fiscal_period=artifact.fiscal_period,
        period_end=artifact.period_end,
        statements=statements,
        key_metrics=key_metrics,
        operating_metrics=operating_metrics,
        warnings=warnings,
    )


def _facts_from_contract_items(
    items: list[dict[str, Any]],
    default_statement_type: StatementType,
    artifact: ParsedArtifact,
    warnings: list[str],
) -> list[ExtractedFact]:
    facts: list[ExtractedFact] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        statement_type = _statement_type(item.get("statement_type"), default_statement_type)
        if "values" not in item and "value" in item:
            fact = _fact_from_flat_contract_item(item, statement_type, artifact, warnings)
            if fact is not None:
                facts.append(fact)
            continue
        values = item.get("values") if isinstance(item.get("values"), dict) else {}
        raw_values = item.get("raw_values") if isinstance(item.get("raw_values"), dict) else {}
        sources = item.get("sources") if isinstance(item.get("sources"), dict) else {}
        periods = item.get("periods") if isinstance(item.get("periods"), dict) else {}
        canonical_name = str(item.get("canonical_name") or item.get("name") or "").strip()
        if not canonical_name:
            continue
        for period_key, value in values.items():
            value_decimal = _decimal(value)
            if value_decimal is None:
                warnings.append(f"Skipped HK parser financial fact with non-decimal value: {canonical_name} {period_key}")
                continue
            value_decimal = _normalize_hk_contract_value(canonical_name, value_decimal)
            period_meta = periods.get(period_key) if isinstance(periods.get(period_key), dict) else {}
            evidence_payload = sources.get(period_key) if isinstance(sources.get(period_key), dict) else {}
            evidence = _evidence_from_contract(evidence_payload, canonical_name, period_key)
            facts.append(
                ExtractedFact(
                    canonical_name=canonical_name,
                    local_name=str(item.get("name") or canonical_name),
                    label=item.get("name"),
                    statement_type=statement_type,
                    value=value_decimal,
                    raw_value=str(raw_values.get(period_key) if raw_values.get(period_key) is not None else value),
                    unit=item.get("unit") or artifact.unit,
                    currency=item.get("currency") or artifact.currency,
                    period_key=str(period_key),
                    period_start=parse_date(period_meta.get("period_start")),
                    period_end=parse_date(period_meta.get("period_end") or _period_end_from_key(period_key)),
                    duration_days=_int_or_none(period_meta.get("duration_days")),
                    frame=period_meta.get("frame"),
                    qtd_ytd_type=period_meta.get("qtd_ytd_type"),
                    fiscal_year=_int_or_none(period_meta.get("fiscal_year")) or artifact.fiscal_year,
                    fiscal_period=period_meta.get("fiscal_period") or artifact.fiscal_period,
                    scale=_decimal(item.get("scale"), Decimal("1")) or Decimal("1"),
                    market=Market.HK,
                    accounting_standard=artifact.accounting_standard,
                    taxonomy=item.get("taxonomy"),
                    is_extension=bool(item.get("is_extension")),
                    gaap_status=str(item.get("gaap_status") or "reported_gaap"),
                    source_accession=item.get("source_accession"),
                    confidence=_decimal(item.get("confidence"), Decimal("0.80")) or Decimal("0.80"),
                    evidence=evidence,
                    raw={"parser_financial_data_item": item.get("raw") or [], "fallback_source": "parser_financial_data"},
                )
            )
    return facts


def _fact_from_flat_contract_item(
    item: dict[str, Any],
    statement_type: StatementType,
    artifact: ParsedArtifact,
    warnings: list[str],
) -> ExtractedFact | None:
    canonical_name = str(item.get("canonical_name") or item.get("local_name") or item.get("label") or "").strip()
    if not canonical_name:
        return None
    value_decimal = _decimal(item.get("value"))
    if value_decimal is None:
        warnings.append(f"Skipped HK parser financial fact with non-decimal value: {canonical_name} {item.get('period_key')}")
        return None
    value_decimal = _normalize_hk_contract_value(canonical_name, value_decimal)
    evidence_payload = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    period_key = str(item.get("period_key") or _period_key_from_dates(item.get("period_end"), item.get("fiscal_year")) or "")
    if not period_key:
        period_key = str(artifact.period_end or artifact.fiscal_year or "unknown")
    return ExtractedFact(
        canonical_name=canonical_name,
        local_name=str(item.get("local_name") or item.get("label") or canonical_name),
        label=item.get("label") or item.get("local_name"),
        statement_type=statement_type,
        value=value_decimal,
        raw_value=str(item.get("raw_value") if item.get("raw_value") is not None else item.get("value")),
        unit=item.get("unit") or artifact.unit,
        currency=item.get("currency") or artifact.currency,
        period_key=period_key,
        period_start=parse_date(item.get("period_start")),
        period_end=parse_date(item.get("period_end") or _period_end_from_key(period_key)),
        duration_days=_int_or_none(item.get("duration_days")),
        frame=item.get("frame"),
        qtd_ytd_type=item.get("qtd_ytd_type"),
        fiscal_year=_int_or_none(item.get("fiscal_year")) or artifact.fiscal_year,
        fiscal_period=item.get("fiscal_period") or artifact.fiscal_period,
        scale=_decimal(item.get("scale"), Decimal("1")) or Decimal("1"),
        market=Market.HK,
        accounting_standard=artifact.accounting_standard,
        taxonomy=item.get("taxonomy"),
        is_extension=bool(item.get("is_extension")),
        gaap_status=str(item.get("gaap_status") or "reported_gaap"),
        source_accession=item.get("source_accession"),
        confidence=_decimal(item.get("confidence"), Decimal("0.80")) or Decimal("0.80"),
        evidence=_evidence_from_contract(evidence_payload, canonical_name, period_key),
        raw={"parser_financial_data_item": item.get("raw") or {}, "fallback_source": "parser_financial_data"},
    )


def _evidence_from_contract(payload: dict[str, Any], canonical_name: str, period_key: Any) -> EvidenceRef:
    data = dict(payload) if isinstance(payload, dict) else {}
    data.setdefault("source_type", "parser_financial_data")
    data.setdefault("source_id", f"{canonical_name}:{period_key}")
    return EvidenceRef(**data)


def _statement_type(value: Any, default: StatementType = StatementType.BALANCE_SHEET) -> StatementType:
    try:
        return StatementType(str(value or default.value))
    except ValueError:
        return default


def _normalize_hk_contract_value(canonical_name: str, value: Decimal) -> Decimal:
    if canonical_name in _HK_LIABILITY_CANONICAL_NAMES and value < 0:
        return -value
    return value


def _decimal(value: Any, default: Decimal | None = None) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _period_end_from_key(value: Any) -> str | None:
    text = str(value or "")
    return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None


def _period_key_from_dates(period_end: Any, fiscal_year: Any) -> str | None:
    parsed = parse_date(period_end)
    if parsed:
        return parsed.isoformat()
    year = _int_or_none(fiscal_year)
    return f"{year}-12-31" if year else None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def write_hk_evidence_package(
    pdf_path: Path,
    parser_result_dir: Path,
    output_root: Path,
    metadata_path: Path | None = None,
    *,
    force: bool = False,
) -> Path:
    artifact, metadata, document_full = build_hk_artifact(pdf_path, parser_result_dir, metadata_path)
    result = process_artifact(artifact, include_load_plan=True)
    parser_financial_data = read_json(parser_result_dir / "financial_data.json", {})
    extraction = result.extraction
    validation = result.validation
    load_plan = result.load_plan
    if _has_parser_financial_data(parser_financial_data):
        parser_extraction = _extraction_from_financial_data_contract(parser_financial_data, artifact)
        if _should_use_parser_financial_data(extraction, parser_financial_data):
            extraction = _merge_parser_and_markdown_extractions(parser_extraction, extraction)
            validation = validate_extraction(extraction)
            load_plan = build_load_plan(extraction, validation)
    financial_data = financial_data_contract(extraction)
    financial_checks = financial_checks_contract(validation)

    filing_key = metadata["accession_number"] or stable_id(pdf_path.name)[:12]
    report_id = _report_id(artifact.fiscal_year, artifact.report_type, filing_key)
    company_wiki_id = _company_dir_name(artifact.ticker, artifact.company_name)
    company_dir = output_root / "companies" / company_wiki_id
    package_dir = company_dir / "reports" / report_id
    if package_dir.exists() and force:
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)
    for name in ("raw", "sections", "tables", "xbrl", "metrics", "qa", "parser"):
        (package_dir / name).mkdir(exist_ok=True)

    shutil.copy2(pdf_path, package_dir / "raw" / "report.pdf")
    if metadata_path and metadata_path.exists():
        shutil.copy2(metadata_path, package_dir / "raw" / "report.metadata.json")
    else:
        write_json(package_dir / "raw" / "report.metadata.json", metadata.get("raw_metadata") or {})

    markdown = _markdown_from_document_full(document_full, parser_result_dir)
    (package_dir / "sections" / "report.md").write_text(markdown, encoding="utf-8")
    _write_section_index(package_dir, markdown, document_full)
    table_index = _write_tables(package_dir, artifact.tables)
    write_json(package_dir / "xbrl" / "facts_raw.json", {"schema_version": "hk_xbrl_facts_raw_v1", "facts": []})
    parser_quality = read_json(parser_result_dir / "quality_report.json", {})

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "market": "HK",
        "filing_id": artifact.report_id,
        "company_id": artifact.company_id,
        "ticker": artifact.ticker,
        "stock_code": artifact.ticker,
        "hkex_stock_code": artifact.ticker,
        "company_name": artifact.company_name,
        "exchange": "HKEX",
        "source_id": metadata["source_id"],
        "form": metadata["form"],
        "report_type": metadata["report_type"],
        "fiscal_year": artifact.fiscal_year,
        "fiscal_period": artifact.fiscal_period,
        "period_end": metadata["period_end"],
        "published_at": metadata["published_at"],
        "source_url": metadata["source_url"],
        "local_source_path": "raw/report.pdf",
        "accounting_standard": artifact.accounting_standard.value,
        "parser_version": PARSER_VERSION,
        "rules_version": RULES_VERSION,
        "quality_status": financial_checks.get("overall_status") or "warning",
        "artifact_hashes": {},
        "accession_number": metadata["accession_number"],
        "report_id": report_id,
        "company_wiki_id": company_wiki_id,
        "company_wiki_path": _rel_to(output_root, company_dir),
        "language": metadata.get("language"),
        "report_language": metadata.get("language") or "unknown",
        "parser_result_dir": str(parser_result_dir),
        "pdf_parser_task_id": str(parser_result_dir.name),
        "pdf_parser_quality_status": parser_quality.get("overall_status") or parser_quality.get("status") or "unknown",
        "source_pdf_sha256": _sha256_file(pdf_path),
        "industry_profile": artifact.industry_profile or metadata.get("industry_profile") or "general",
        "wiki_company_path": _rel_to(output_root, company_dir),
        "wiki_report_path": _rel_to(output_root, package_dir),
    }
    manifest["parse_run_id"] = load_plan.parse_run_id if load_plan else stable_parse_run_id(manifest, {})
    source_map = source_map_from_financial_data(manifest=manifest, financial_data=financial_data, package_dir=package_dir)
    normalized_metrics = normalized_metrics_from_financial_data(manifest=manifest, financial_data=financial_data, source_map=source_map)
    quality = build_quality_report(
        manifest=manifest,
        financial_data=financial_data,
        financial_checks=financial_checks,
        section_count=1 if markdown else 0,
        table_count=len(table_index),
        raw_fact_count=_raw_cell_count(artifact.tables),
        source_map=source_map,
        parser_warnings=_parser_warnings(document_full, artifact.tables),
        rule_warnings=list(extraction.warnings) + list(validation.warnings),
    )
    quality.update(
        {
            "parser_status": manifest["pdf_parser_quality_status"],
            "rule_status": financial_checks.get("overall_status") or "warning",
            "statement_table_count": _statement_table_count(financial_data),
            "raw_cell_count": _raw_cell_count(artifact.tables),
            "rejected_candidates": [],
        }
    )
    manifest["quality_status"] = quality["overall_status"]

    write_json(package_dir / "metrics" / "financial_data.json", financial_data)
    write_json(package_dir / "metrics" / "financial_checks.json", financial_checks)
    write_json(package_dir / "metrics" / "load_plan.json", load_plan.model_dump(mode="json") if load_plan else {})
    write_json(package_dir / "metrics" / "normalized_metrics.json", {"schema_version": "market_normalized_metrics_v1", "metrics": normalized_metrics})
    write_json(package_dir / "metrics" / "operating_metrics.json", {"schema_version": "market_operating_metrics_v1", "metrics": [row for row in normalized_metrics if row.get("statement_type") == "operating_metrics"]})
    write_json(package_dir / "qa" / "quality_report.json", quality)
    write_json(package_dir / "qa" / "source_map.json", source_map)
    write_json(package_dir / "qa" / "extraction_warnings.json", {"warnings": quality["parser_warnings"] + quality["rule_warnings"]})
    _write_parser_artifacts(package_dir, parser_result_dir, document_full, financial_data, financial_checks)
    _write_report_complete(package_dir, markdown, document_full, quality)
    _write_enhancement_qa(package_dir, document_full)
    manifest["artifact_hashes"] = compute_artifact_hashes(package_dir)
    write_json(package_dir / "manifest.json", manifest)
    _validation_with_package_gates, load_plan = build_package_aware_load_plan(extraction, validation, package_dir=package_dir)
    write_json(package_dir / "metrics" / "load_plan.json", load_plan.model_dump(mode="json"))
    (package_dir / "README.md").write_text(_readme(manifest, quality), encoding="utf-8")

    validation = validate_evidence_package(package_dir)
    if not validation.ok:
        write_json(package_dir / "qa" / "contract_validation.json", validation.as_dict())
    _write_company_wiki_indexes(output_root, company_dir, manifest, quality)
    return package_dir


def _content_list_enhanced(document_full: dict[str, Any]) -> dict[str, Any]:
    enhanced = document_full.get("content_list_enhanced")
    if not isinstance(enhanced, dict):
        return {}
    tables = enhanced.get("tables") if isinstance(enhanced.get("tables"), list) else []
    normalized_tables: list[dict[str, Any]] = []
    for table in tables:
        if not isinstance(table, dict):
            continue
        normalized_table = dict(table)
        if not isinstance(normalized_table.get("relations"), list):
            normalized_table["relations"] = []
        normalized_tables.append(normalized_table)
    return {
        **enhanced,
        "footnotes": enhanced.get("footnotes") if isinstance(enhanced.get("footnotes"), dict) else {},
        "toc": enhanced.get("toc") if isinstance(enhanced.get("toc"), dict) else {},
        "financial_note_links": enhanced.get("financial_note_links") if isinstance(enhanced.get("financial_note_links"), dict) else {},
        "quality_signals": enhanced.get("quality_signals") if isinstance(enhanced.get("quality_signals"), dict) else {},
        "tables": normalized_tables,
        "pages": enhanced.get("pages") if isinstance(enhanced.get("pages"), list) else [],
    }


def _report_id(fiscal_year: Any, report_type: str, filing_key: str) -> str:
    year = str(fiscal_year or "unknown")
    kind = re.sub(r"[^A-Za-z0-9_-]+", "-", str(report_type or "annual")).strip("-").lower() or "annual"
    key = re.sub(r"[^A-Za-z0-9_-]+", "-", str(filing_key or stable_id(year, kind))).strip("-") or "unknown"
    return f"{year}-{kind}-{key}"


def _company_dir_name(ticker: str, company_name: str | None) -> str:
    code = str(ticker).zfill(5) if str(ticker).isdigit() else str(ticker)
    return f"{code}-{_slug_part(company_name or code)}"


def _slug_part(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\\/:*?\"<>|]+", "-", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-.")
    return text[:80] or "UNKNOWN"


def _rel_to(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_existing_json(path: Path) -> dict[str, Any]:
    payload = read_json(path, {})
    return payload if isinstance(payload, dict) else {}


def _unique_values(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def _write_company_wiki_indexes(output_root: Path, company_dir: Path, manifest: dict[str, Any], quality: dict[str, Any]) -> None:
    report_id = str(manifest.get("report_id") or "")
    if not report_id:
        return
    report_rel = f"reports/{report_id}"
    report_entry = {
        "report_id": report_id,
        "filing_id": manifest.get("filing_id"),
        "task_id": manifest.get("pdf_parser_task_id"),
        "market": "HK",
        "report_kind": manifest.get("report_type"),
        "report_type": manifest.get("report_type"),
        "fiscal_year": manifest.get("fiscal_year"),
        "fiscal_period": manifest.get("fiscal_period"),
        "period_end": manifest.get("period_end"),
        "published_at": manifest.get("published_at"),
        "source_url": manifest.get("source_url"),
        "source_filename": "report.pdf",
        "package_path": report_rel,
        "manifest": f"{report_rel}/manifest.json",
        "report_md": f"{report_rel}/sections/report.md",
        "report_complete": f"{report_rel}/sections/report_complete.md",
        "document_full": f"{report_rel}/parser/document_full.json",
        "content_list_enhanced": f"{report_rel}/parser/content_list_enhanced.json",
        "financial_data": f"{report_rel}/metrics/financial_data.json",
        "financial_checks": f"{report_rel}/metrics/financial_checks.json",
        "quality_report": f"{report_rel}/qa/quality_report.json",
        "source_map": f"{report_rel}/qa/source_map.json",
        "quality_status": quality.get("overall_status") or manifest.get("quality_status"),
    }
    existing = _read_existing_json(company_dir / "company.json")
    reports = [item for item in existing.get("reports") or [] if isinstance(item, dict) and item.get("report_id") != report_id]
    reports.append(report_entry)
    reports.sort(key=lambda item: str(item.get("period_end") or item.get("published_at") or ""), reverse=True)
    primary_report_id = str(reports[0].get("report_id") or report_id)
    latest_report = next((item for item in reports if item.get("report_id") == primary_report_id), report_entry)
    company_path_rel = _rel_to(output_root, company_dir)
    aliases = _unique_values([
        manifest.get("ticker"),
        manifest.get("stock_code"),
        manifest.get("hkex_stock_code"),
        manifest.get("company_name"),
        *((manifest.get("aliases") or []) if isinstance(manifest.get("aliases"), list) else []),
    ])
    company_json = {
        **existing,
        "schema_version": "hk_company_wiki_v1",
        "market": "HK",
        "company_id": manifest.get("company_id"),
        "stock_code": manifest.get("stock_code") or manifest.get("ticker"),
        "hkex_stock_code": manifest.get("hkex_stock_code") or manifest.get("ticker"),
        "ticker": manifest.get("ticker"),
        "exchange": manifest.get("exchange") or "HKEX",
        "company_short_name": manifest.get("company_name"),
        "company_full_name": manifest.get("company_name"),
        "company_name": manifest.get("company_name"),
        "aliases": aliases,
        "company_path": company_path_rel,
        "primary_report_id": primary_report_id,
        "report_count": len(reports),
        "reports": reports,
        "metrics": {
            "latest": {
                "financial_data": latest_report.get("financial_data"),
                "financial_checks": latest_report.get("financial_checks"),
                "quality_report": latest_report.get("quality_report"),
            },
            "by_report": {
                str(item.get("report_id")): {
                    "financial_data": item.get("financial_data"),
                    "financial_checks": item.get("financial_checks"),
                    "quality_report": item.get("quality_report"),
                }
                for item in reports
                if item.get("report_id")
            },
        },
        "evidence": {
            "latest_source_map": latest_report.get("source_map"),
            "latest_manifest": latest_report.get("manifest"),
        },
        "updated_at": _now_iso(),
    }
    write_json(company_dir / "company.json", company_json)
    write_json(company_dir / "_index.json", {
        "schema_version": "hk_company_index_v1",
        "market": "HK",
        "company_id": company_json["company_id"],
        "company_path": company_path_rel,
        "primary_report_id": primary_report_id,
        "reports": reports,
        "updated_at": company_json["updated_at"],
    })
    _write_root_catalog(output_root)


def _write_root_catalog(output_root: Path) -> None:
    companies: list[dict[str, Any]] = []
    for company_json_path in sorted((output_root / "companies").glob("*/company.json")):
        company = _read_existing_json(company_json_path)
        if not company:
            continue
        companies.append({
            "company_id": company.get("company_id"),
            "market": "HK",
            "stock_code": company.get("stock_code"),
            "ticker": company.get("ticker"),
            "exchange": company.get("exchange") or "HKEX",
            "company_short_name": company.get("company_short_name"),
            "company_full_name": company.get("company_full_name"),
            "aliases": company.get("aliases") or [],
            "company_path": company.get("company_path") or _rel_to(output_root, company_json_path.parent),
            "primary_report_id": company.get("primary_report_id"),
            "report_count": company.get("report_count") or len(company.get("reports") or []),
            "status": "ready",
        })
    companies.sort(key=lambda item: str(item.get("stock_code") or item.get("company_id") or ""))
    write_json(output_root / "_meta" / "company_catalog.json", {
        "schema_version": "hk_company_catalog_v1",
        "market": "HK",
        "company_count": len(companies),
        "companies": companies,
        "generated_at": _now_iso(),
    })
    guide = output_root / "_meta" / "AGENT_GUIDE.md"
    if not guide.exists():
        guide.write_text(
            "# HK Wiki Agent Guide\n\n"
            "港股 Wiki 与 A 股保持同类路径语义：先读取 `_meta/company_catalog.json`，"
            "再进入 `companies/<ticker>-<company>/company.json`，最后按 "
            "`reports/<report_id>/` 读取单份报告包。\n\n"
            "数据优先级：`company.json` -> `reports/<report_id>/metrics/financial_data.json` -> "
            "`reports/<report_id>/metrics/financial_checks.json` -> "
            "`reports/<report_id>/qa/source_map.json` -> "
            "`reports/<report_id>/sections/report.md` -> "
            "`reports/<report_id>/parser/document_full.json` -> PostgreSQL `siq_hk.pdf2md_hk` fallback。\n",
            encoding="utf-8",
        )


def _empty_parser_financial_data() -> dict[str, Any]:
    return {
        "statements": [],
        "key_metrics": [],
        "operating_metrics": [],
        "warnings": [],
        "summary": {},
    }


def _empty_parser_financial_checks() -> dict[str, Any]:
    return {
        "overall_status": "unknown",
        "checks": [],
        "warnings": [],
        "summary": {},
    }


def _write_parser_artifacts(
    package_dir: Path,
    parser_result_dir: Path,
    document_full: dict[str, Any],
    financial_data: dict[str, Any],
    financial_checks: dict[str, Any],
) -> None:
    enhanced = _content_list_enhanced(document_full)
    tables = enhanced.get("tables") if isinstance(enhanced.get("tables"), list) else []
    relations: list[dict[str, Any]] = []
    for table in tables:
        if not isinstance(table, dict):
            continue
        base = {
            "table_index": table.get("table_index"),
            "content_table_source_id": table.get("content_table_source_id"),
            "pdf_page_number": table.get("pdf_page_number"),
        }
        table_relations = table.get("relations") if isinstance(table.get("relations"), list) else []
        for relation in table_relations:
            if isinstance(relation, dict):
                relations.append({**base, **relation})
    write_json(package_dir / "parser" / "document_full.json", document_full or {})
    write_json(package_dir / "parser" / "content_list_enhanced.json", enhanced)
    write_json(package_dir / "parser" / "table_relations.json", {"schema_version": "hk_table_relations_v1", "relations": relations})
    write_json(
        package_dir / "parser" / "quality_report.json",
        read_json(parser_result_dir / "quality_report.json", {"schema_version": "hk_parser_quality_report_v1", "overall_status": "unknown", "warnings": []}),
    )
    write_json(
        package_dir / "parser" / "financial_data.json",
        read_json(parser_result_dir / "financial_data.json", _empty_parser_financial_data()),
    )
    write_json(
        package_dir / "parser" / "financial_checks.json",
        read_json(parser_result_dir / "financial_checks.json", _empty_parser_financial_checks()),
    )


def _write_report_complete(package_dir: Path, markdown: str, document_full: dict[str, Any], quality: dict[str, Any]) -> None:
    enhanced = _content_list_enhanced(document_full)
    footnotes = enhanced.get("footnotes") if isinstance(enhanced.get("footnotes"), dict) else {}
    toc = enhanced.get("toc") if isinstance(enhanced.get("toc"), dict) else {}
    note_links = enhanced.get("financial_note_links") if isinstance(enhanced.get("financial_note_links"), dict) else {}
    pages = enhanced.get("pages") if isinstance(enhanced.get("pages"), list) else []
    tables = enhanced.get("tables") if isinstance(enhanced.get("tables"), list) else []
    sections = [
        markdown.rstrip(),
        "## 可恢复结构摘要",
        json.dumps({"parser_quality_status": quality.get("parser_status"), "table_count": len(tables), "page_count": len(pages)}, ensure_ascii=False, indent=2),
        "## 目录候选",
        json.dumps(toc or {"headings": [], "toc_candidates": [], "content_headings": [], "summary": {}}, ensure_ascii=False, indent=2),
        "## 脚注摘要",
        json.dumps(footnotes or {"references": [], "definitions": [], "bindings": [], "summary": {}}, ensure_ascii=False, indent=2),
        "## 附注关系摘要",
        json.dumps(note_links or {"links": [], "summary": {}}, ensure_ascii=False, indent=2),
        "## 图片/表格摘要",
        json.dumps({"pages": pages, "tables": tables}, ensure_ascii=False, indent=2),
    ]
    content = "\n\n".join(part for part in sections if part) + "\n"
    (package_dir / "sections" / "report_complete.md").write_text(content, encoding="utf-8")


def _write_enhancement_qa(package_dir: Path, document_full: dict[str, Any]) -> None:
    enhanced = _content_list_enhanced(document_full)
    write_json(package_dir / "qa" / "footnotes.json", {
        "schema_version": "hk_footnotes_v1",
        "payload": enhanced.get("footnotes") or {"references": [], "definitions": [], "bindings": [], "summary": {}},
    })
    write_json(package_dir / "qa" / "toc.json", {
        "schema_version": "hk_toc_v1",
        "payload": enhanced.get("toc") or {"headings": [], "toc_candidates": [], "content_headings": [], "summary": {}},
    })
    write_json(package_dir / "qa" / "financial_note_links.json", {
        "schema_version": "hk_financial_note_links_v1",
        "payload": enhanced.get("financial_note_links") or {"links": [], "summary": {}},
    })
    write_json(package_dir / "qa" / "table_quality_signals.json", {
        "schema_version": "hk_table_quality_signals_v1",
        "payload": enhanced.get("quality_signals") or {"signals": [], "summary": {}},
    })


def _write_tables(package_dir: Path, tables: list[ParsedTable]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table in tables:
        table_index = table.table_index or len(rows) + 1
        item = {
            "table_id": table.table_id,
            "table_index": table_index,
            "title": table.title,
            "page_number": table.page_number,
            "row_count": len(table.rows),
            "column_count": max((len(row) for row in table.rows), default=0),
            "table_json_path": f"tables/table_{int(table_index):04d}.json",
            "unit": table.unit,
            "currency": table.currency,
            "raw": table.raw,
        }
        rows.append(item)
        write_json(package_dir / item["table_json_path"], {**item, "rows": table.rows})
    write_json(package_dir / "tables" / "table_index.json", {"schema_version": "hk_table_index_v1", "tables": rows})
    return rows


def _write_section_index(package_dir: Path, markdown: str, document_full: dict[str, Any]) -> None:
    lines = markdown.splitlines()
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    char_offset = 0
    for index, line in enumerate(lines, start=1):
        if line.startswith("#"):
            if current is not None:
                current["char_end"] = char_offset
                sections.append(current)
            title = line.lstrip("#").strip() or f"Section {len(sections) + 1}"
            current = {
                "section_id": f"section_{len(sections) + 1:04d}",
                "title": title,
                "level": len(line) - len(line.lstrip("#")),
                "line_start": index,
                "char_start": char_offset,
            }
        char_offset += len(line) + 1
    if current is not None:
        current["char_end"] = char_offset
        sections.append(current)
    if not sections:
        task = document_full.get("task") if isinstance(document_full.get("task"), dict) else {}
        sections.append(
            {
                "section_id": "section_0001",
                "title": task.get("filename") or "Report",
                "level": 1,
                "line_start": 1,
                "char_start": 0,
                "char_end": len(markdown),
            }
        )
    write_json(package_dir / "sections" / "section_index.json", {"schema_version": "hk_section_index_v1", "sections": sections})


def _markdown_from_document_full(document_full: dict[str, Any], parser_result_dir: Path) -> str:
    markdown = document_full.get("markdown") if isinstance(document_full.get("markdown"), dict) else {}
    content = markdown.get("content") if isinstance(markdown, dict) else None
    markdown_path = _best_markdown_path(parser_result_dir)
    if markdown_path:
        return markdown_path.read_text(encoding="utf-8")
    if content:
        return str(content)
    return f"# {document_full.get('task', {}).get('filename') or 'HK report'}\n"


def _table_title(item: dict[str, Any], meta: dict[str, Any]) -> str | None:
    captions = item.get("table_caption") or meta.get("source_caption") or []
    if isinstance(captions, list) and captions:
        return " ".join(_clean_cell(value) for value in captions if _clean_cell(value)) or None
    return _clean_cell(meta.get("heading") or meta.get("title") or "") or None


def _infer_unit(title: str | None, rows: list[list[str]]) -> str | None:
    haystack = " ".join([title or "", *[" ".join(row[:4]) for row in rows[:3]]])
    match = re.search(r"(HK\$|RMB|US\$|USD|HKD|CNY)[^\n,;)]{0,30}(million|billion|thousand|mn|bn|百萬|百万|千)?", haystack, flags=re.I)
    if match:
        return match.group(0)
    return None


def _page_number(item: dict[str, Any]) -> int | None:
    page_idx = item.get("page_idx")
    try:
        return int(page_idx) + 1
    except (TypeError, ValueError):
        return None


def _default_unit(document_full: dict[str, Any]) -> str | None:
    filename = str(document_full.get("task", {}).get("filename") if isinstance(document_full.get("task"), dict) else "")
    if "HK" in filename:
        return "HKD"
    return None


def _default_currency(metadata: dict[str, Any]) -> str | None:
    text = json.dumps(metadata.get("raw_metadata") or {}, ensure_ascii=False).lower()
    if "rmb" in text or "renminbi" in text:
        return "CNY"
    if "usd" in text or "us$" in text:
        return "USD"
    return "HKD"


def _parser_warnings(document_full: dict[str, Any], tables: list[ParsedTable]) -> list[str]:
    warnings = []
    if not document_full:
        warnings.append("PDF parser result document_full.json is empty.")
    if not tables:
        warnings.append("No parsed PDF tables were converted to ParsedTable.")
    return warnings


def _statement_table_count(financial_data: dict[str, Any]) -> int:
    return sum(1 for statement in financial_data.get("statements") or [] if statement.get("items"))


def _raw_cell_count(tables: list[ParsedTable]) -> int:
    return sum(len(row) for table in tables for row in table.rows)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _report_type(value: Any) -> str:
    text = str(value or "").lower()
    if any(token in text for token in ("interim", "semi", "中期", "半年")):
        return "semiannual"
    if any(token in text for token in ("quarter", "q1", "q2", "q3", "季度")):
        return "quarterly"
    return "annual"


def _fiscal_period(report_type: str) -> str:
    return {"annual": "FY", "semiannual": "H1", "quarterly": "Q"}.get(report_type, "FY")


def _filename_date(filename: str) -> str | None:
    match = re.search(r"(20\d{2}-\d{2}-\d{2})", filename)
    return match.group(1) if match else None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _accounting_standard(metadata: dict[str, Any]) -> str:
    text = json.dumps(metadata or {}, ensure_ascii=False).lower()
    if "casbe" in text or "china accounting standards" in text or "中国企业会计准则" in text:
        return "CASBE"
    if "ifrs" in text:
        return "IFRS"
    return "HKFRS"


def _readme(manifest: dict[str, Any], quality: dict[str, Any]) -> str:
    return (
        f"# {manifest.get('ticker')} {manifest.get('fiscal_year')} {manifest.get('form')}\n\n"
        f"- Market: `{manifest.get('market')}`\n"
        f"- Filing ID: `{manifest.get('filing_id')}`\n"
        f"- Period end: `{manifest.get('period_end')}`\n"
        f"- Quality: `{quality.get('overall_status')}`\n"
        f"- Evidence coverage: `{quality.get('evidence_coverage_ratio')}`\n"
        f"- Source: {manifest.get('source_url') or 'local file'}\n"
    )
