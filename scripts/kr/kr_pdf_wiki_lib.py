from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import datetime, timezone
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
    stable_parse_run_id,
)
from market_report_rules_service.models import AccountingStandard, Market, ParsedArtifact, ParsedTable
from market_report_rules_service.normalization import infer_currency, parse_date
from market_report_rules_service.pipeline import process_artifact


CORE_STATEMENT_KEYWORDS = (
    "statement of financial position",
    "statement of profit or loss",
    "statement of comprehensive income",
    "statement of cash flows",
    "statement of changes in equity",
    "summary financial information",
    "연결 재무상태표",
    "연결 손익계산서",
    "연결 포괄손익계산서",
    "연결 현금흐름표",
    "연결 한금흐를표",
    "연결한금흐를표",
    "현금흐를표",
    "현금초를",
    "현금조율",
    "연결 자본변동표",
    "요약재무정보",
)
_KR_BANK_TICKERS = {"024110", "055550", "086790", "105560", "138930", "316140"}
_KR_INSURANCE_TICKERS = {"032830", "005830", "000810", "088350"}
_KR_SEMICONDUCTOR_TICKERS = {"000660", "005930", "042700"}
_KR_ENERGY_MATERIALS_TICKERS = {"005490", "010950", "096770", "051910"}


class _TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "tr":
            self._row = []
        elif tag.lower() in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(_clean_cell(" ".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(cell for cell in self._row):
                self.rows.append(self._row)
            self._row = None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _repo_relative(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def _clean_cell(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\\triangle", "-").replace("△", "-").replace("$", "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _html_table_rows(table_body: str) -> list[list[str]]:
    parser = _TableHTMLParser()
    parser.feed(table_body or "")
    return parser.rows


def normalize_kr_ticker(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if not digits:
        return "000000"
    return digits[-6:].zfill(6)


def _slug(value: str) -> str:
    parts = re.findall(r"[A-Za-z0-9가-힣]+", value or "Company")
    slug = "".join(part[:1].upper() + part[1:] for part in parts)
    return slug or "Company"


def kr_company_dir_name(ticker: str, company_name: str) -> str:
    return f"{normalize_kr_ticker(ticker)}-{_slug(company_name)}"


def _year_from_name(value: str) -> int | None:
    match = re.search(r"(20\d{2})", value)
    return int(match.group(1)) if match else None


def infer_kr_pdf_metadata(
    pdf_path: Path,
    parser_result_dir: Path,
    metadata_path: Path | None = None,
) -> dict[str, Any]:
    parser_manifest = _read_json(parser_result_dir / "manifest.json")
    metadata = dict(parser_manifest)
    if metadata_path:
        metadata.update(_read_json(metadata_path))

    task_id = str(metadata.get("task_id") or parser_result_dir.name)
    ticker = normalize_kr_ticker(metadata.get("ticker") or _ticker_from_kr_filename(pdf_path.name) or pdf_path.stem)
    company_name = str(metadata.get("company_name") or metadata.get("company") or _company_from_kr_filename(pdf_path.name) or pdf_path.stem)
    report_year = int(
        metadata.get("report_year")
        or metadata.get("fiscal_year")
        or _year_from_name(pdf_path.stem)
        or datetime.now(timezone.utc).year
    )
    report_type = str(metadata.get("report_type") or "annual").lower().replace(" ", "_")
    report_id = f"{report_year}-{report_type}_{task_id}"
    period_end = metadata.get("period_end") or metadata.get("report_end") or metadata.get("fiscal_year_end")
    if not period_end and report_type == "annual":
        period_end = f"{report_year}-12-31"
    industry_profile = metadata.get("industry_profile") or _infer_kr_industry_profile(ticker, company_name)
    return {
        "market": "KR",
        "ticker": ticker,
        "company_name": company_name,
        "report_year": report_year,
        "fiscal_year": report_year,
        "fiscal_period": "FY" if report_type == "annual" else report_type.upper(),
        "period_end": period_end,
        "report_type": report_type,
        "form": metadata.get("form") or "business_report",
        "report_id": report_id,
        "pdf_parser_task_id": task_id,
        "source_pdf": str(pdf_path),
        "source_url": metadata.get("source_url") or metadata.get("document_url") or metadata.get("landing_url"),
        "parser_result_dir": str(parser_result_dir),
        "industry_profile": industry_profile,
    }


def _ticker_from_kr_filename(filename: str) -> str | None:
    match = re.search(r"(?:^|[_-])KR[_-]([0-9]{5,6})(?:[_-]|$)", str(filename or ""), flags=re.I)
    if match:
        return match.group(1)
    parts = str(filename or "").split("_")
    for index, part in enumerate(parts[:-1]):
        if part.upper() == "KR" and re.fullmatch(r"\d{5,6}", parts[index + 1] or ""):
            return parts[index + 1]
    return None


def _company_from_kr_filename(filename: str) -> str | None:
    text = str(filename or "")
    match = re.search(r"(.+?)(?:[_-])KR[_-][0-9]{5,6}(?:[_-]|$)", text, flags=re.I)
    if not match:
        return None
    return match.group(1).replace("_", " ").strip(" -") or None


def _infer_kr_industry_profile(ticker: str, company_name: str) -> str:
    code = normalize_kr_ticker(ticker)
    raw_name = str(company_name or "")
    name = raw_name.upper()
    if code in _KR_BANK_TICKERS or "BANK" in name or "FINANCIAL" in name or "금융" in raw_name or "은행" in raw_name:
        return "bank"
    if code in _KR_INSURANCE_TICKERS or "INSURANCE" in name or "생명" in raw_name or "보험" in raw_name:
        return "insurance"
    if code in _KR_SEMICONDUCTOR_TICKERS or any(token in name for token in ("HYNIX", "SEMICONDUCTOR", "SAMSUNG ELECTRONICS")):
        return "semiconductor"
    if code in _KR_ENERGY_MATERIALS_TICKERS or any(token in name for token in ("POSCO", "CHEMICAL", "ENERGY", "SDI", "BATTERY", "CELLTRION")):
        return "energy"
    if any(token in name for token in ("HYUNDAI", "KIA", "MOBIS", "MOTOR", "ELECTRONICS", "LG", "SK", "KOREAN AIR")):
        return "manufacturing"
    return "general"


def _copy_if_exists(src: Path, dst: Path) -> None:
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _iter_tables(parser_result_dir: Path) -> list[dict[str, Any]]:
    document = _read_json(parser_result_dir / "document_full.json")
    tables: list[dict[str, Any]] = []
    for page in document.get("pages", []):
        if not isinstance(page, dict):
            continue
        page_no = page.get("page") or page.get("page_number")
        for table in page.get("tables", []):
            if not isinstance(table, dict):
                continue
            item = dict(table)
            item["pdf_page_number"] = int(page_no) if page_no else None
            item["caption"] = str(item.get("caption") or item.get("title") or "")
            tables.append(item)
    if tables:
        return tables

    content = _read_json(parser_result_dir / "content_list_enhanced.json")
    for item in content.get("items", []):
        if not isinstance(item, dict) or item.get("type") != "table":
            continue
        page_idx = item.get("page_idx")
        tables.append(
            {
                "table_index": item.get("table_index") or len(tables) + 1,
                "caption": str(item.get("caption") or item.get("text") or ""),
                "pdf_page_number": int(page_idx) + 1 if page_idx is not None else None,
            }
        )
    if tables:
        return tables

    for item in content.get("tables", []):
        if not isinstance(item, dict):
            continue
        caption = item.get("caption") or item.get("heading") or item.get("title") or ""
        if not caption and isinstance(item.get("source_caption"), list):
            caption = " ".join(str(part) for part in item["source_caption"] if part)
        tables.append(
            {
                "table_index": item.get("table_index") or len(tables) + 1,
                "caption": str(caption or item.get("preview") or ""),
                "pdf_page_number": item.get("pdf_page_number"),
                "md_line": item.get("line"),
            }
        )
    if tables:
        return tables

    table_index_path = parser_result_dir / "table_index.json"
    if table_index_path.exists():
        payload = json.loads(table_index_path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                caption = item.get("caption") or item.get("heading") or item.get("title") or ""
                if not caption and isinstance(item.get("source_caption"), list):
                    caption = " ".join(str(part) for part in item["source_caption"] if part)
                tables.append(
                    {
                        "table_index": item.get("table_index") or len(tables) + 1,
                        "caption": str(caption or item.get("preview") or ""),
                        "pdf_page_number": item.get("pdf_page_number"),
                        "md_line": item.get("line"),
                    }
                )
    return tables


def _document_full_with_sidecars(parser_result_dir: Path) -> dict[str, Any]:
    document_full = _read_json(parser_result_dir / "document_full.json")
    enhanced = _read_json(parser_result_dir / "content_list_enhanced.json")
    if enhanced and not isinstance(document_full.get("content_list_enhanced"), dict):
        document_full["content_list_enhanced"] = enhanced
    parser_quality = _read_json(parser_result_dir / "quality_report.json")
    if parser_quality and not isinstance(document_full.get("quality_report"), dict):
        document_full["quality_report"] = parser_quality
    return document_full


def parsed_tables_from_parser_result(parser_result_dir: Path, metadata: dict[str, Any]) -> list[ParsedTable]:
    document_full = _document_full_with_sidecars(parser_result_dir)
    content = document_full.get("content_list") if isinstance(document_full.get("content_list"), list) else []
    enhanced = document_full.get("content_list_enhanced") if isinstance(document_full.get("content_list_enhanced"), dict) else {}
    enhanced_by_source, enhanced_by_index = _table_meta_maps(enhanced.get("tables") if isinstance(enhanced, dict) else [])
    table_index_by_source, table_index_by_index = _table_meta_maps(_read_table_index(parser_result_dir))

    parsed: list[ParsedTable] = []
    content_table_counter = 0
    last_statement_title: str | None = None
    last_statement_page: int | None = None
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "table":
            continue
        rows = _html_table_rows(str(item.get("table_body") or ""))
        if not rows:
            continue
        content_table_counter += 1
        meta = {
            **table_index_by_source.get(content_table_counter, {}),
            **enhanced_by_source.get(content_table_counter, {}),
        }
        table_index = _int_or_none(meta.get("table_index") or item.get("table_index") or content_table_counter) or content_table_counter
        meta = {
            **table_index_by_index.get(table_index, {}),
            **enhanced_by_index.get(table_index, {}),
            **meta,
        }
        page_number = _page_number(item, meta)
        raw_title = _table_title(item, meta)
        statement_title = _statement_title(raw_title, meta)
        if statement_title:
            last_statement_title = statement_title
            last_statement_page = page_number
        title = statement_title or raw_title
        if not statement_title and last_statement_title and _nearby_page(page_number, last_statement_page):
            title = last_statement_title
            meta = {**meta, "inherited_statement_title": last_statement_title}

        raw = {
            **meta,
            "content_table_source_id": content_table_counter,
            "bbox": item.get("bbox") or meta.get("bbox"),
            "source_image_path": item.get("img_path") or meta.get("source_image_path"),
            "source_caption": item.get("table_caption") or meta.get("source_caption"),
            "source_footnote": item.get("table_footnote") or meta.get("source_footnote"),
            "preview": meta.get("preview") or " ".join(" | ".join(row[:5]) for row in rows[:4])[:500],
            "columns": _period_columns(rows, metadata),
        }
        statement_type = _statement_type_hint(title, raw, rows)
        if statement_type:
            raw["statement_type"] = statement_type
        unit = _unit_from_table(title, raw, rows)
        parsed.append(
            ParsedTable(
                table_id=f"kr_table_{table_index:04d}",
                title=title,
                rows=rows,
                page_number=page_number,
                table_index=table_index,
                unit=unit,
                currency=infer_currency(unit, title, default="KRW"),
                raw=raw,
            )
        )
    if parsed:
        return parsed

    tables: list[ParsedTable] = []
    for index, item in enumerate(_iter_tables(parser_result_dir), start=1):
        title = str(item.get("caption") or "") or None
        rows = item.get("rows") if isinstance(item.get("rows"), list) else []
        if not rows:
            preview = str(item.get("preview") or title or "")
            rows = [[cell for cell in re.split(r"\s{2,}|\s+\|\s+", preview) if cell]] if preview else []
        tables.append(
            ParsedTable(
                table_id=f"kr_table_{int(item.get('table_index') or index):04d}",
                title=title,
                rows=rows,
                page_number=item.get("pdf_page_number"),
                table_index=item.get("table_index") or index,
                unit=_unit_from_table(title, item, rows),
                currency="KRW",
                raw=item,
            )
        )
    return tables


def build_kr_pdf_artifact(pdf_path: Path, parser_result_dir: Path, metadata_path: Path | None = None) -> tuple[ParsedArtifact, dict[str, Any], dict[str, Any]]:
    metadata = infer_kr_pdf_metadata(pdf_path, parser_result_dir, metadata_path)
    document_full = _document_full_with_sidecars(parser_result_dir)
    tables = parsed_tables_from_parser_result(parser_result_dir, metadata)
    artifact = ParsedArtifact(
        artifact_id=f"KR:{metadata['ticker']}:{metadata['pdf_parser_task_id']}",
        market=Market.KR,
        company_id=f"KR:{metadata['ticker']}",
        ticker=metadata["ticker"],
        company_name=metadata["company_name"],
        report_id=metadata["report_id"],
        report_type=metadata["report_type"],
        report_form=metadata["form"],
        fiscal_year=metadata["fiscal_year"],
        fiscal_period=metadata["fiscal_period"],
        period_end=parse_date(metadata["period_end"]),
        accounting_standard=AccountingStandard.KIFRS,
        industry_profile=metadata.get("industry_profile") or "general",
        currency="KRW",
        unit="KRW",
        source_url=metadata.get("source_url"),
        source_files={"pdf": str(pdf_path), "parser_result": str(parser_result_dir)},
        tables=tables,
        document_full=document_full,
        metadata=metadata,
    )
    return artifact, metadata, document_full


def _table_meta_maps(items: Any) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    by_source: dict[int, dict[str, Any]] = {}
    by_index: dict[int, dict[str, Any]] = {}
    if not isinstance(items, list):
        return by_source, by_index
    for item in items:
        if not isinstance(item, dict):
            continue
        source_id = _int_or_none(item.get("content_table_source_id"))
        table_index = _int_or_none(item.get("table_index"))
        if source_id is not None:
            by_source[source_id] = item
        if table_index is not None:
            by_index[table_index] = item
    return by_source, by_index


def _read_table_index(parser_result_dir: Path) -> list[dict[str, Any]]:
    path = parser_result_dir / "table_index.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def _table_title(item: dict[str, Any], meta: dict[str, Any]) -> str | None:
    parts: list[str] = []
    for value in (item.get("table_caption"), meta.get("source_caption")):
        if isinstance(value, list):
            parts.extend(str(part) for part in value if part)
        elif value:
            parts.append(str(value))
    for key in ("heading", "title", "caption"):
        if meta.get(key):
            parts.append(str(meta[key]))
    cleaned = " ".join(_clean_cell(part) for part in parts if _clean_cell(part))
    if not cleaned or re.fullmatch(r"\[?PDF_PAGE:\s*\d+\]?", cleaned, flags=re.I):
        return None
    return cleaned


def _statement_title(title: str | None, meta: dict[str, Any]) -> str | None:
    parts = [str(title or ""), str(meta.get("heading") or ""), str(meta.get("preview") or "")[:200]]
    for value in meta.get("source_caption") or []:
        parts.append(str(value))
    text = " ".join(parts)
    if _looks_like_kr_statement_contents_table(text):
        return None
    if any(token in text for token in ("재무상태표", "Statement of Financial Position", "Balance Sheet")):
        return "연결 재무상태표"
    if any(token in text for token in ("현금흐름표", "현금흐를표", "현금초를", "현금조율", "한금흐를표", "Statement of Cash Flows", "Cash Flows")):
        return "연결 현금흐름표"
    if any(token in text for token in ("손익계산서", "포괄손익계산서", "Statement of Profit or Loss", "Comprehensive Income")):
        return "연결 포괄손익계산서"
    return None


def _statement_type_hint(title: str | None, raw: dict[str, Any], rows: list[list[str]]) -> str | None:
    text = " ".join([str(title or ""), str(raw.get("heading") or ""), str(raw.get("preview") or "")])
    compact = re.sub(r"\s+", "", text)
    if _looks_like_kr_statement_contents_table(text):
        return None
    if "재무상태표" in compact or "BalanceSheet" in compact or "FinancialPosition" in compact:
        return "balance_sheet"
    if any(token in compact for token in ("현금흐름표", "현금흐를표", "한금흐를표", "현금초를", "현금조율")) or "CashFlows" in compact:
        return "cash_flow_statement"
    if "손익계산서" in compact or "포괄손익계산서" in compact or "ProfitorLoss" in compact or "ComprehensiveIncome" in compact:
        return "income_statement"
    return None


def _looks_like_kr_statement_contents_table(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    core_hits = sum(
        1
        for marker in (
            "연결재무상태표",
            "연결손익계산서",
            "연결포괄손익계산서",
            "연결자본변동표",
            "연결현금흐름표",
            "연결한금흐를표",
            "현금흐를표",
            "재무제표에대한주석",
        )
        if marker in compact
    )
    if core_hits < 4:
        return False
    return not any(marker in compact for marker in ("유동자산", "비유동자산", "매출액", "영업이익", "영업활동", "투자활동", "재무활동", "자본금"))


def _unit_from_table(title: str | None, raw: dict[str, Any], rows: list[list[str]]) -> str | None:
    text = " ".join([str(title or ""), str(raw.get("unit") or ""), str(raw.get("heading") or "")])
    for value in raw.get("source_caption") or []:
        text += " " + str(value)
    text += " " + " ".join(" ".join(row[:4]) for row in rows[:3])
    if "백만원" in text:
        return "KRW million"
    if "억원" in text:
        return "KRW 100 million"
    if "KRW" in text.upper() or "원" in text:
        return "KRW"
    return None


def _period_columns(rows: list[list[str]], metadata: dict[str, Any]) -> list[dict[str, str]]:
    max_columns = max((len(row) for row in rows[:5]), default=0)
    if max_columns <= 1:
        return []
    best_row = []
    best_score = -1
    for row in rows[:5]:
        score = sum(1 for cell in row if _looks_like_period_cell(cell, metadata))
        if score > best_score:
            best_score = score
            best_row = row
    report_year = _int_or_none(metadata.get("report_year"))
    fiscal_period = metadata.get("fiscal_period") or "FY"
    columns: list[dict[str, str]] = []
    sequential_year = report_year
    for index in range(max_columns):
        cell = best_row[index] if index < len(best_row) else ""
        period = _period_for_cell(cell, metadata)
        if period is None and index > 0 and best_score > 0 and re.search(r"제\s*\d+\s*기", str(cell or "")) and sequential_year:
            period = f"{sequential_year - (index - 1)}-12-31"
        columns.append({"period_key": period, "period_end": period, "fiscal_period": str(fiscal_period)} if period else {})
    return columns


def _period_for_cell(cell: Any, metadata: dict[str, Any]) -> str | None:
    parsed = parse_date(cell)
    if parsed:
        return parsed.isoformat()
    text = str(cell or "")
    match = re.search(r"(20\d{2})\s*[./년-]\s*(\d{1,2})?\s*[./월-]?\s*(\d{1,2})?", text)
    if match:
        year = int(match.group(1))
        month = int(match.group(2) or 12)
        day = int(match.group(3) or 31)
        return f"{year:04d}-{month:02d}-{day:02d}"
    report_year = _int_or_none(metadata.get("report_year"))
    return None


def _looks_like_period_cell(cell: Any, metadata: dict[str, Any]) -> bool:
    text = str(cell or "")
    return bool(_period_for_cell(text, metadata) or re.search(r"제\s*\d+\s*기", text))


def _nearby_page(page: int | None, previous: int | None) -> bool:
    if page is None or previous is None:
        return True
    return 0 <= page - previous <= 2


def _page_number(item: dict[str, Any], meta: dict[str, Any]) -> int | None:
    for value in (meta.get("pdf_page_number"), meta.get("page_number")):
        parsed = _int_or_none(value)
        if parsed is not None:
            return parsed
    page_idx = _int_or_none(item.get("page_idx"))
    return page_idx + 1 if page_idx is not None else None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _line_for_caption(report_md: str, caption: str) -> int | None:
    caption_lower = caption.lower()
    for idx, line in enumerate(report_md.splitlines(), start=1):
        if caption_lower and caption_lower in line.lower():
            return idx
    return None


def _build_source_map(metadata: dict[str, Any], parser_result_dir: Path) -> dict[str, Any]:
    report_path = parser_result_dir / "report_complete.md"
    report_md = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    evidence = []
    for index, table in enumerate(_iter_tables(parser_result_dir), start=1):
        caption = str(table.get("caption") or "")
        evidence.append(
            {
                "evidence_id": f"KR-{metadata['ticker']}-{metadata['report_id']}-table-{index}",
                "market": "KR",
                "company_id": f"KR:{metadata['ticker']}",
                "ticker": metadata["ticker"],
                "report_id": metadata["report_id"],
                "filing_id": metadata["report_id"],
                "pdf_parser_task_id": metadata["pdf_parser_task_id"],
                "parser_result_dir": metadata["parser_result_dir"],
                "pdf_page_number": table.get("pdf_page_number"),
                "table_index": table.get("table_index") or index,
                "caption": caption,
                "md_path": "parser/report_complete.md",
                "wiki_path": "parser/report_complete.md",
                "md_line": table.get("md_line") or _line_for_caption(report_md, caption) or 1,
            }
        )
    return {"market": "KR", "report_id": metadata["report_id"], "evidence": evidence}


def _quality_report(metadata: dict[str, Any], source_map: dict[str, Any]) -> dict[str, Any]:
    captions = "\n".join(str(item.get("caption") or "") for item in source_map["evidence"]).lower()
    matched = [keyword for keyword in CORE_STATEMENT_KEYWORDS if keyword.lower() in captions]
    return {
        "market": "KR",
        "report_id": metadata["report_id"],
        "core_statement_matches": matched,
        "evidence_count": len(source_map["evidence"]),
        "financial_checks": {
            "status": "not_generated",
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "notes": [
                "KR PDF 未确认完整结构化连接财务报表，已按候选识别模式处理；完整数值勾稽建议结合 DART/XBRL 或原文表格复核。"
            ],
        },
    }


def _evidence_index(source_map: dict[str, Any]) -> dict[str, Any]:
    return {
        "market": "KR",
        "report_id": source_map["report_id"],
        "evidence": source_map["evidence"],
    }


def _retrieval_index(metadata: dict[str, Any], source_map: dict[str, Any]) -> dict[str, Any]:
    chunks = []
    for entry in source_map["evidence"]:
        chunks.append(
            {
                "chunk_id": f"{entry['evidence_id']}-retrieval",
                "market": "KR",
                "ticker": metadata["ticker"],
                "company_id": f"KR:{metadata['ticker']}",
                "report_id": metadata["report_id"],
                "evidence_id": entry["evidence_id"],
                "title": entry.get("caption") or "KR report evidence",
                "wiki_path": entry["md_path"],
                "page_number": entry.get("pdf_page_number"),
                "table_index": entry.get("table_index"),
                "source": {
                    "pdf_page_number": entry.get("pdf_page_number"),
                    "table_index": entry.get("table_index"),
                    "md_path": entry["md_path"],
                    "md_line": entry.get("md_line"),
                },
            }
        )
    return {"market": "KR", "report_id": metadata["report_id"], "chunks": chunks}


def _ensure_company_scaffold(company_dir: Path, metadata: dict[str, Any]) -> None:
    for dirname in ("reports", "metrics", "evidence", "semantic", "graph", "analysis", "factcheck", "tracking"):
        (company_dir / dirname).mkdir(parents=True, exist_ok=True)
    _write_json(
        company_dir / "company.json",
        {
            "market": "KR",
            "company_id": f"KR:{metadata['ticker']}",
            "ticker": metadata["ticker"],
            "company_name": metadata["company_name"],
            "wiki_root": "data/wiki/kr",
        },
    )
    company_md = company_dir / "company.md"
    if not company_md.exists():
        company_md.write_text(
            f"# {metadata['company_name']}\n\n市场：KR\n股票代码：{metadata['ticker']}\n",
            encoding="utf-8",
        )


def _update_catalogs(output_root: Path, package_dir: Path, metadata: dict[str, Any]) -> None:
    meta_dir = output_root / "_meta"
    company_catalog_path = meta_dir / "companies.json"
    report_catalog_path = meta_dir / "reports.json"
    company = {
        "market": "KR",
        "ticker": metadata["ticker"],
        "company_name": metadata["company_name"],
        "company_path": str(package_dir.parents[1].relative_to(output_root)),
    }
    report = {
        "market": "KR",
        "ticker": metadata["ticker"],
        "report_id": metadata["report_id"],
        "package_path": str(package_dir.relative_to(output_root)),
    }
    companies = _read_json(company_catalog_path).get("companies", [])
    reports = _read_json(report_catalog_path).get("reports", [])
    companies = [item for item in companies if item.get("ticker") != metadata["ticker"]] + [company]
    reports = [item for item in reports if item.get("package_path") != report["package_path"]] + [report]
    _write_json(company_catalog_path, {"market": "KR", "companies": sorted(companies, key=lambda item: item["ticker"])})
    _write_json(report_catalog_path, {"market": "KR", "reports": sorted(reports, key=lambda item: item["package_path"])})


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
            "raw": table.raw,
        }
        rows.append(item)
        _write_json(package_dir / item["table_json_path"], {**item, "rows": table.rows})
    _write_json(package_dir / "tables" / "table_index.json", {"schema_version": "kr_table_index_v1", "tables": rows})
    return rows


def _markdown_from_parser_result(parser_result_dir: Path, document_full: dict[str, Any], metadata: dict[str, Any]) -> str:
    for candidate in (parser_result_dir / "report_complete.md", parser_result_dir / "result.md", parser_result_dir / "document.md"):
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    markdown = document_full.get("markdown") if isinstance(document_full.get("markdown"), dict) else {}
    if markdown.get("content"):
        return str(markdown["content"])
    return f"# {metadata['company_name']} {metadata['report_year']} {metadata['report_type']}\n"


def _parser_warnings(document_full: dict[str, Any], tables: list[ParsedTable]) -> list[str]:
    warnings: list[str] = []
    if not document_full:
        warnings.append("KR PDF parser result document_full.json is empty.")
    if not tables:
        warnings.append("No KR parsed PDF tables were converted to ParsedTable.")
    elif not any(table.raw.get("statement_type") for table in tables if isinstance(table.raw, dict)):
        warnings.append("KR PDF tables were parsed, but no statement type hints were detected.")
    warnings.append("KR PDF quality uses KR/KIFRS PDF profile; XBRL/API profile may provide stronger fact-level validation.")
    return warnings


def _raw_cell_count(tables: list[ParsedTable]) -> int:
    return sum(len(row) for table in tables for row in table.rows)


def write_kr_pdf_wiki_package(
    pdf_path: Path,
    parser_result_dir: Path,
    output_root: Path,
    metadata_path: Path | None = None,
    *,
    force: bool = False,
) -> Path:
    pdf_path = Path(pdf_path)
    parser_result_dir = Path(parser_result_dir)
    output_root = Path(output_root)
    artifact, metadata, document_full = build_kr_pdf_artifact(pdf_path, parser_result_dir, metadata_path)
    result = process_artifact(artifact, include_load_plan=True)
    financial_data = financial_data_contract(result.extraction)
    financial_checks = financial_checks_contract(result.validation)
    financial_checks["rule_profile_id"] = financial_checks.get("profile_id")
    financial_checks["market_extensions"] = {
        "quality_profile_id": "kr_dart_pdf_quality_v1",
        "source_type": "pdf",
        "validation_strength": "pdf_table_level",
    }
    company_wiki_id = kr_company_dir_name(metadata["ticker"], metadata["company_name"])
    company_dir = output_root / "companies" / company_wiki_id
    _ensure_company_scaffold(company_dir, metadata)
    package_dir = company_dir / "reports" / metadata["report_id"]
    if package_dir.exists() and force:
        shutil.rmtree(package_dir)
    if package_dir.exists():
        raise FileExistsError(f"Package already exists: {package_dir}")

    for dirname in ("raw", "sections", "tables", "xbrl", "metrics", "evidence", "semantic", "qa", "parser"):
        (package_dir / dirname).mkdir(parents=True, exist_ok=True)

    _copy_if_exists(pdf_path, package_dir / "raw" / pdf_path.name)
    if metadata_path and metadata_path.exists():
        _copy_if_exists(metadata_path, package_dir / "raw" / "report.metadata.json")
    else:
        _write_json(package_dir / "raw" / "report.metadata.json", metadata)
    for name in ("document_full.json", "content_list_enhanced.json", "table_index.json", "table_relations.json", "report_complete.md", "manifest.json"):
        _copy_if_exists(parser_result_dir / name, package_dir / "parser" / name)

    markdown = _markdown_from_parser_result(parser_result_dir, document_full, metadata)
    (package_dir / "sections" / "report.md").write_text(markdown, encoding="utf-8")
    if not (package_dir / "parser" / "report_complete.md").exists():
        (package_dir / "parser" / "report_complete.md").write_text(markdown, encoding="utf-8")
    table_index = _write_tables(package_dir, artifact.tables)
    _write_json(package_dir / "xbrl" / "facts_raw.json", {"schema_version": "dart_pdf_facts_raw_v1", "facts": []})

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "package_schema": "market_evidence_package_v1",
        "market": "KR",
        "company_id": f"KR:{metadata['ticker']}",
        "ticker": metadata["ticker"],
        "company_name": metadata["company_name"],
        "exchange": "KRX",
        "currency": "KRW",
        "accounting_standard": "KIFRS",
        "industry_profile": artifact.industry_profile or metadata.get("industry_profile") or "general",
        "report_year": metadata["report_year"],
        "fiscal_year": metadata["fiscal_year"],
        "fiscal_period": metadata["fiscal_period"],
        "period_end": metadata["period_end"],
        "report_type": metadata["report_type"],
        "form": metadata["form"],
        "report_id": metadata["report_id"],
        "filing_id": metadata["report_id"],
        "company_wiki_id": company_wiki_id,
        "company_wiki_path": _repo_relative(company_dir),
        "wiki_report_path": _repo_relative(package_dir),
        "source_type": "pdf",
        "source_url": metadata.get("source_url"),
        "local_source_path": f"raw/{pdf_path.name}",
        "pdf_parser_task_id": metadata["pdf_parser_task_id"],
        "task_id": metadata["pdf_parser_task_id"],
        "parser_result_dir": metadata["parser_result_dir"],
        "parser_version": "kr_pdf_wiki_parser_v2",
        "rules_version": result.extraction.rule_version,
        "quality_profile_id": "kr_dart_pdf_quality_v1",
        "financial_check_profile_id": financial_checks.get("profile_id"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "artifact_hashes": {},
        "paths": {
            "source_pdf": f"raw/{pdf_path.name}",
            "report_complete": "parser/report_complete.md",
            "document_full": "parser/document_full.json",
            "content_list_enhanced": "parser/content_list_enhanced.json",
            "table_index": "parser/table_index.json",
            "quality_report": "qa/quality_report.json",
            "source_map": "qa/source_map.json",
            "evidence_index": "evidence/evidence_index.json",
            "retrieval_index": "semantic/retrieval_index.json",
            "financial_data": "metrics/financial_data.json",
            "financial_checks": "metrics/financial_checks.json",
            "load_plan": "metrics/load_plan.json",
        },
    }
    manifest["parse_run_id"] = result.load_plan.parse_run_id if result.load_plan else stable_parse_run_id(manifest, {})
    standard_source_map = source_map_from_financial_data(manifest=manifest, financial_data=financial_data, package_dir=package_dir)
    legacy_source_map = _build_source_map(metadata, parser_result_dir)
    source_map = {**standard_source_map, "evidence": legacy_source_map.get("evidence", [])}
    normalized_metrics = normalized_metrics_from_financial_data(manifest=manifest, financial_data=financial_data, source_map=standard_source_map)
    quality = build_quality_report(
        manifest=manifest,
        financial_data=financial_data,
        financial_checks=financial_checks,
        section_count=1 if markdown else 0,
        table_count=len(table_index),
        raw_fact_count=_raw_cell_count(artifact.tables),
        source_map=standard_source_map,
        parser_warnings=_parser_warnings(document_full, artifact.tables),
        rule_warnings=list(result.extraction.warnings) + list(result.validation.warnings),
    )
    quality.update(
        {
            "profile_id": "kr_dart_pdf_quality_v1",
            "rule_profile_id": financial_checks.get("profile_id"),
            "accounting_standard": "KIFRS",
            "summary": {
                "statement_count": len(financial_data.get("statements") or []),
                "table_count": len(table_index),
                "source_map_entry_count": len(standard_source_map.get("entries") or []),
            },
            "market_extensions": {
                "legacy_evidence_count": len(legacy_source_map.get("evidence") or []),
                "source_type": "pdf",
                "validation_strength": "pdf_table_level",
            },
        }
    )
    manifest["quality_status"] = quality.get("overall_status")

    _write_json(package_dir / "qa" / "source_map.json", source_map)
    _write_json(package_dir / "qa" / "quality_report.json", quality)
    _write_json(package_dir / "qa" / "extraction_warnings.json", {"warnings": quality.get("parser_warnings", []) + quality.get("rule_warnings", [])})
    _write_json(package_dir / "evidence" / "evidence_index.json", _evidence_index(legacy_source_map))
    _write_json(package_dir / "semantic" / "retrieval_index.json", _retrieval_index(metadata, legacy_source_map))
    _write_json(package_dir / "metrics" / "financial_data.json", financial_data)
    _write_json(package_dir / "metrics" / "financial_checks.json", financial_checks)
    _write_json(package_dir / "metrics" / "load_plan.json", result.load_plan.model_dump(mode="json") if result.load_plan else {"market": "KR", "report_id": metadata["report_id"]})
    _write_json(package_dir / "metrics" / "normalized_metrics.json", {"schema_version": "market_normalized_metrics_v1", "metrics": normalized_metrics})

    manifest["artifact_hashes"] = compute_artifact_hashes(package_dir)
    _write_json(package_dir / "manifest.json", manifest)
    (package_dir / "README.md").write_text(
        f"# {metadata['company_name']} {metadata['report_year']} {metadata['report_type']}\n\n"
        f"- Quality: `{quality.get('overall_status')}`\n"
        f"- Financial check profile: `{financial_checks.get('profile_id')}`\n\n"
        "本目录由韩国市场 PDF 解析产物生成，包含 Markdown、表格证据、质量报告以及可回溯到 PDF 页码的来源索引。\n",
        encoding="utf-8",
    )
    _update_catalogs(output_root, package_dir, metadata)
    return package_dir
