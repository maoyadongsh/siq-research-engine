from __future__ import annotations

import html
import hashlib
import json
import os
import re
import shutil
import sys
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
from market_report_rules_service.models import AccountingStandard, Market, ParsedArtifact, ParsedTable
from market_report_rules_service.normalization import infer_currency, parse_date
from market_report_rules_service.pipeline import process_artifact


PARSER_VERSION = os.environ.get("SIQ_HK_PARSER_VERSION", "hk_pdf_evidence_parser_v1")
RULES_VERSION = os.environ.get("SIQ_HK_RULES_VERSION", "hkex_rules_v1")


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


def read_json(path: Path, default: Any = None) -> Any:
    if not path or not path.exists():
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
    return parser.rows


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
    }


def parsed_tables_from_document_full(document_full: dict[str, Any]) -> list[ParsedTable]:
    content = document_full.get("content_list") or []
    enhanced = document_full.get("content_list_enhanced") or {}
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
            preview = str(item.get("preview") or "")
            rows = [[cell.strip() for cell in re.split(r"\s{2,}|\s+\|\s+", preview) if cell.strip()]]
            if not rows or len(rows[0]) < 2:
                continue
            index = int(item.get("table_index") or len(parsed) + 1)
            title = _table_title({}, item)
            parsed.append(
                ParsedTable(
                    table_id=f"hk_table_{index:04d}",
                    title=title,
                    rows=rows,
                    page_number=item.get("pdf_page_number"),
                    table_index=index,
                    raw=item,
                )
            )
    return parsed


def build_hk_artifact(pdf_path: Path, parser_result_dir: Path, metadata_path: Path | None = None) -> tuple[ParsedArtifact, dict[str, Any], dict[str, Any]]:
    document_full = read_json(parser_result_dir / "document_full.json", {})
    metadata = infer_metadata(pdf_path, metadata_path)
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
        currency=_default_currency(metadata),
        unit=_default_unit(document_full),
        source_url=metadata["source_url"],
        source_files={"pdf": str(pdf_path), "parser_result": str(parser_result_dir)},
        tables=parsed_tables_from_document_full(document_full),
        document_full=document_full,
        metadata=metadata,
    )
    return artifact, metadata, document_full


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
    financial_data = financial_data_contract(result.extraction)
    financial_checks = financial_checks_contract(result.validation)

    filing_key = metadata["accession_number"] or stable_id(pdf_path.name)[:12]
    package_dir = output_root / artifact.ticker / str(artifact.fiscal_year or "unknown") / f"{artifact.report_type}_{filing_key}"
    if package_dir.exists() and force:
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)
    for name in ("raw", "sections", "tables", "xbrl", "metrics", "qa"):
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
        "language": metadata.get("language"),
        "report_language": metadata.get("language") or "unknown",
        "parser_result_dir": str(parser_result_dir),
        "pdf_parser_task_id": str(parser_result_dir.name),
        "pdf_parser_quality_status": parser_quality.get("overall_status") or parser_quality.get("status") or "unknown",
        "source_pdf_sha256": _sha256_file(pdf_path),
        "industry_profile": artifact.industry_profile or metadata.get("industry_profile") or "general",
    }
    manifest["parse_run_id"] = result.load_plan.parse_run_id if result.load_plan else stable_parse_run_id(manifest, {})
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
        rule_warnings=list(result.extraction.warnings) + list(result.validation.warnings),
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
    write_json(package_dir / "metrics" / "load_plan.json", result.load_plan.model_dump(mode="json") if result.load_plan else {})
    write_json(package_dir / "metrics" / "normalized_metrics.json", {"schema_version": "market_normalized_metrics_v1", "metrics": normalized_metrics})
    write_json(package_dir / "metrics" / "operating_metrics.json", {"schema_version": "market_operating_metrics_v1", "metrics": [row for row in normalized_metrics if row.get("statement_type") == "operating_metrics"]})
    write_json(package_dir / "qa" / "quality_report.json", quality)
    write_json(package_dir / "qa" / "source_map.json", source_map)
    write_json(package_dir / "qa" / "extraction_warnings.json", {"warnings": quality["parser_warnings"] + quality["rule_warnings"]})
    manifest["artifact_hashes"] = compute_artifact_hashes(package_dir)
    write_json(package_dir / "manifest.json", manifest)
    (package_dir / "README.md").write_text(_readme(manifest, quality), encoding="utf-8")

    validation = validate_evidence_package(package_dir)
    if not validation.ok:
        write_json(package_dir / "qa" / "contract_validation.json", validation.as_dict())
    return package_dir


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
    if content:
        return str(content)
    for candidate in (parser_result_dir / "result.md", parser_result_dir / "document.md"):
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
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
