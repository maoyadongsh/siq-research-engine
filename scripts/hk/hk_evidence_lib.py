from __future__ import annotations

import copy
import html
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
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
from market_report_rules_service.normalization import infer_currency, infer_scale, parse_date
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


def _metadata_path_for_pdf(pdf_path: Path) -> Path:
    candidates = [
        pdf_path.with_suffix(pdf_path.suffix + ".metadata.json"),
        pdf_path.with_suffix(".metadata.json"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _clean_cell(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _html_table_rows(table_body: str) -> list[list[str]]:
    parser = _TableHTMLParser()
    parser.feed(table_body or "")
    return parser.rows


def _enhanced_table_rows(item: dict[str, Any]) -> list[list[str]]:
    structure = item.get("structure") if isinstance(item.get("structure"), dict) else {}
    header_preview = structure.get("header_preview") if isinstance(structure, dict) else None
    rows: list[list[str]] = []
    if isinstance(header_preview, list):
        for line in header_preview:
            if not isinstance(line, str):
                continue
            cells = [_clean_cell(cell) for cell in line.split("|")]
            cells = [cell for cell in cells if cell]
            if len(cells) >= 2:
                rows.append(cells)
    if rows:
        return rows
    preview = str(item.get("preview") or "")
    cells = [_clean_cell(cell) for cell in re.split(r"\s{2,}|\s+\|\s+", preview) if _clean_cell(cell)]
    return [cells] if len(cells) >= 2 else []


def infer_metadata(pdf_path: Path, metadata_path: Path | None = None) -> dict[str, Any]:
    metadata = read_json(metadata_path or _metadata_path_for_pdf(pdf_path), {})
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
        "industry_profile": _industry_profile(candidate, company_name),
    }


def parsed_tables_from_document_full(
    document_full: dict[str, Any],
    content_list_enhanced: dict[str, Any] | None = None,
) -> list[ParsedTable]:
    content = document_full.get("content_list") or []
    enhanced = _content_list_enhanced(document_full, content_list_enhanced)
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
            rows = _enhanced_table_rows(item)
            if not rows:
                continue
            index = int(item.get("table_index") or len(parsed) + 1)
            title = _table_title({}, item)
            unit = _infer_unit(title, rows)
            parsed.append(
                ParsedTable(
                    table_id=f"hk_table_{index:04d}",
                    title=title,
                    rows=rows,
                    page_number=item.get("pdf_page_number"),
                    table_index=index,
                    unit=unit,
                    currency=infer_currency(unit, title, default=None),
                    raw=item,
                )
            )
    return parsed


def build_hk_artifact(pdf_path: Path, parser_result_dir: Path, metadata_path: Path | None = None) -> tuple[ParsedArtifact, dict[str, Any], dict[str, Any]]:
    document_full = read_json(parser_result_dir / "document_full.json", {})
    standalone_enhanced = _standalone_content_list_enhanced(parser_result_dir)
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
        industry_profile=metadata.get("industry_profile") or "general",
        currency=_default_currency(metadata),
        unit=_default_unit(document_full),
        source_url=metadata["source_url"],
        source_files={"pdf": str(pdf_path), "parser_result": str(parser_result_dir)},
        tables=parsed_tables_from_document_full(document_full, standalone_enhanced),
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
    standalone_enhanced = _standalone_content_list_enhanced(parser_result_dir)
    result = process_artifact(artifact, include_load_plan=True)
    financial_data = financial_data_contract(result.extraction)
    financial_checks = financial_checks_contract(result.validation)
    parser_financial_data = read_json(parser_result_dir / "financial_data.json", {})
    parser_financial_checks = read_json(parser_result_dir / "financial_checks.json", {})
    if _financial_metric_count(financial_data) == 0 and _financial_metric_count(parser_financial_data) > 0:
        financial_data = parser_financial_data
        if parser_financial_checks:
            financial_checks = parser_financial_checks
    financial_data = _normalize_financial_data_units(financial_data)

    filing_key = metadata["accession_number"] or stable_id(pdf_path.name)[:12]
    package_dir = output_root / artifact.ticker / str(artifact.fiscal_year or "unknown") / f"{artifact.report_type}_{filing_key}"
    source_pdf_path = pdf_path
    source_metadata_path = metadata_path
    source_parser_result_dir = parser_result_dir
    staged_inputs = None
    if package_dir.exists() and force:
        staged_inputs = tempfile.TemporaryDirectory(prefix="hk-package-inputs-")
        staged_root = Path(staged_inputs.name)
        if _path_is_within(pdf_path, package_dir):
            source_pdf_path = staged_root / "report.pdf"
            shutil.copy2(pdf_path, source_pdf_path)
        if metadata_path and metadata_path.exists() and _path_is_within(metadata_path, package_dir):
            source_metadata_path = staged_root / "report.metadata.json"
            shutil.copy2(metadata_path, source_metadata_path)
        if parser_result_dir.is_dir() and _path_is_within(parser_result_dir, package_dir):
            source_parser_result_dir = staged_root / "parser_result"
            shutil.copytree(parser_result_dir, source_parser_result_dir)
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)
    for name in ("raw", "sections", "tables", "xbrl", "metrics", "qa", "parser"):
        (package_dir / name).mkdir(exist_ok=True)

    try:
        source_pdf_sha256 = _sha256_file(source_pdf_path)
        shutil.copy2(source_pdf_path, package_dir / "raw" / "report.pdf")
        if source_metadata_path and source_metadata_path.exists():
            shutil.copy2(source_metadata_path, package_dir / "raw" / "report.metadata.json")
        else:
            write_json(package_dir / "raw" / "report.metadata.json", metadata.get("raw_metadata") or {})
    finally:
        if staged_inputs is not None and source_parser_result_dir == parser_result_dir:
            staged_inputs.cleanup()
            staged_inputs = None

    markdown = _markdown_from_document_full(document_full, source_parser_result_dir)
    (package_dir / "sections" / "report.md").write_text(markdown, encoding="utf-8")
    _write_section_index(package_dir, markdown, document_full)
    table_index = _write_tables(package_dir, artifact.tables)
    if not table_index:
        table_index = _write_parser_table_index(package_dir, source_parser_result_dir, document_full, standalone_enhanced)
    write_json(package_dir / "xbrl" / "facts_raw.json", {"schema_version": "hk_xbrl_facts_raw_v1", "facts": []})
    parser_quality = read_json(source_parser_result_dir / "quality_report.json", {})

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
        "source_pdf_sha256": source_pdf_sha256,
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
    _write_parser_artifacts(package_dir, source_parser_result_dir, document_full, standalone_enhanced, financial_data, financial_checks)
    _write_report_complete(package_dir, markdown, document_full, quality, standalone_enhanced)
    _write_enhancement_qa(package_dir, document_full, standalone_enhanced)
    if staged_inputs is not None:
        staged_inputs.cleanup()
        staged_inputs = None
    manifest["artifact_hashes"] = compute_artifact_hashes(package_dir)
    write_json(package_dir / "manifest.json", manifest)
    (package_dir / "README.md").write_text(_readme(manifest, quality), encoding="utf-8")

    validation = validate_evidence_package(package_dir)
    if not validation.ok:
        write_json(package_dir / "qa" / "contract_validation.json", validation.as_dict())
    return package_dir


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _standalone_content_list_enhanced(parser_result_dir: Path) -> dict[str, Any]:
    enhanced = read_json(parser_result_dir / "content_list_enhanced.json", {})
    return enhanced if isinstance(enhanced, dict) else {}


def _content_list_enhanced(
    document_full: dict[str, Any],
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    enhanced = document_full.get("content_list_enhanced")
    if not (isinstance(enhanced, dict) and enhanced):
        enhanced = fallback
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
    content_list_enhanced: dict[str, Any] | None,
    financial_data: dict[str, Any],
    financial_checks: dict[str, Any],
) -> None:
    enhanced = _content_list_enhanced(document_full, content_list_enhanced)
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


def _write_report_complete(
    package_dir: Path,
    markdown: str,
    document_full: dict[str, Any],
    quality: dict[str, Any],
    content_list_enhanced: dict[str, Any] | None = None,
) -> None:
    enhanced = _content_list_enhanced(document_full, content_list_enhanced)
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


def _write_enhancement_qa(
    package_dir: Path,
    document_full: dict[str, Any],
    content_list_enhanced: dict[str, Any] | None = None,
) -> None:
    enhanced = _content_list_enhanced(document_full, content_list_enhanced)
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


def _normalize_financial_data_units(financial_data: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(financial_data)
    for statement in normalized.get("statements") or []:
        if not isinstance(statement, dict):
            continue
        _normalize_unit_fields(statement)
        for item in statement.get("items") or []:
            if isinstance(item, dict):
                _normalize_unit_fields(item, fallback_unit=statement.get("unit"), fallback_currency=statement.get("currency"))
    for bucket in ("key_metrics", "operating_metrics"):
        for item in normalized.get(bucket) or []:
            if isinstance(item, dict):
                _normalize_unit_fields(item)
    return normalized


def _normalize_unit_fields(
    item: dict[str, Any],
    *,
    fallback_unit: str | None = None,
    fallback_currency: str | None = None,
) -> None:
    unit = item.get("unit") or fallback_unit
    inferred_currency = infer_currency(unit, default=None) or infer_currency(item.get("currency"), fallback_currency, default=item.get("currency") or fallback_currency)
    if inferred_currency:
        item["currency"] = inferred_currency
    if unit:
        scale = infer_scale(unit)
        if scale != 1:
            item["scale"] = str(scale)


def _financial_metric_count(financial_data: dict[str, Any]) -> int:
    count = 0
    for statement in financial_data.get("statements") or []:
        if not isinstance(statement, dict):
            continue
        for item in statement.get("items") or []:
            count += _metric_item_value_count(item)
    for bucket in ("key_metrics", "operating_metrics"):
        for item in financial_data.get(bucket) or []:
            count += _metric_item_value_count(item)
    return count


def _metric_item_value_count(item: Any) -> int:
    if not isinstance(item, dict):
        return 0
    values = item.get("values")
    if isinstance(values, dict):
        return len([value for value in values.values() if value not in (None, "")])
    if item.get("value") not in (None, "") and (item.get("period_key") or item.get("canonical_name") or item.get("label")):
        return 1
    return 0


def _write_parser_table_index(
    package_dir: Path,
    parser_result_dir: Path,
    document_full: dict[str, Any],
    content_list_enhanced: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    payload = read_json(parser_result_dir / "table_index.json", {})
    if isinstance(payload, list):
        tables = payload
    elif isinstance(payload, dict):
        tables = payload.get("tables") if isinstance(payload.get("tables"), list) else []
    else:
        tables = []
    if not tables:
        enhanced = _content_list_enhanced(document_full, content_list_enhanced)
        tables = enhanced.get("tables") if isinstance(enhanced.get("tables"), list) else []
    rows: list[dict[str, Any]] = []
    for offset, item in enumerate((table for table in tables if isinstance(table, dict)), start=1):
        raw = item.get("raw") if isinstance(item.get("raw"), dict) else item
        table_index = _int_or_none(item.get("table_index") or raw.get("table_index")) or offset
        structure = raw.get("structure") if isinstance(raw.get("structure"), dict) else {}
        rel_path = str(item.get("table_json_path") or f"tables/table_{int(table_index):04d}.json")
        if not rel_path.startswith("tables/"):
            rel_path = f"tables/{Path(rel_path).name}"
        normalized = {
            "table_id": item.get("table_id") or f"hk_table_{int(table_index):04d}",
            "table_index": table_index,
            "title": item.get("title") or raw.get("title") or raw.get("heading"),
            "page_number": item.get("page_number") or item.get("pdf_page_number") or raw.get("pdf_page_number") or raw.get("page_number"),
            "row_count": item.get("row_count") or raw.get("rows") or structure.get("expanded_rows"),
            "column_count": item.get("column_count") or structure.get("expanded_columns"),
            "table_json_path": rel_path,
            "unit": item.get("unit") or raw.get("unit"),
            "currency": item.get("currency") or raw.get("currency"),
            "raw": raw,
        }
        rows.append(normalized)
        write_json(package_dir / rel_path, {**normalized, "rows": _table_rows_from_index_item(item, raw)})
    write_json(package_dir / "tables" / "table_index.json", {"schema_version": "hk_table_index_v1", "tables": rows})
    return rows


def _table_rows_from_index_item(item: dict[str, Any], raw: dict[str, Any]) -> list[list[str]]:
    existing_rows = item.get("rows")
    if isinstance(existing_rows, list) and existing_rows and all(isinstance(row, list) for row in existing_rows):
        return existing_rows
    structure = raw.get("structure") if isinstance(raw.get("structure"), dict) else {}
    rows: list[list[str]] = []
    for value in structure.get("header_preview") or []:
        row = [cell.strip() for cell in str(value).split("|")]
        if any(row):
            rows.append(row)
    preview = str(raw.get("preview") or item.get("preview") or "").strip()
    if preview:
        rows.append([preview])
    return rows


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
        return None
    return None


def _default_currency(metadata: dict[str, Any]) -> str | None:
    text = json.dumps(metadata.get("raw_metadata") or {}, ensure_ascii=False).lower()
    if "rmb" in text or "renminbi" in text:
        return "CNY"
    if "usd" in text or "us$" in text:
        return "USD"
    return None


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


def _industry_profile(candidate: dict[str, Any], company_name: str | None) -> str:
    explicit = str(candidate.get("industry_profile") or candidate.get("industry") or "").strip().lower()
    if explicit in {
        "bank",
        "insurance",
        "internet_platform",
        "semiconductor",
        "telecom",
        "energy",
        "manufacturing",
        "real_estate",
        "retail",
        "saas",
    }:
        return explicit
    text = " ".join(
        str(value or "")
        for value in (
            candidate.get("ticker"),
            candidate.get("company_id"),
            company_name,
            candidate.get("stock_name"),
            (candidate.get("metadata") or {}).get("stock_name") if isinstance(candidate.get("metadata"), dict) else None,
        )
    ).lower()
    compact = re.sub(r"[^0-9a-z]+", " ", text)
    rules: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("bank", ("bank", "hsbc", "hang seng", "standard chartered", "icbc", "ccb", "abc", "boc", "bankcomm")),
        ("insurance", ("insurance", "insur", "aia", "ping an", "china life", "cpic", "picc", "prudential")),
        ("internet_platform", ("tencent", "alibaba", "baba", "jd", "meituan", "kuaishou", "netease", "baidu", "trip com", "bilibili")),
        ("semiconductor", ("semiconductor", "smic", "hua hong", "chip", "microelectronics")),
        ("telecom", ("telecom", "mobile", "unicom", "tower")),
        ("energy", ("sinopec", "petrochina", "cnooc", "shenhua", "coal", "oil", "gas", "energy", "power")),
        ("manufacturing", ("auto", "automobile", "geely", "byd", "great wall", "li auto", "motor", "machinery")),
        ("real_estate", ("property", "properties", "land", "real estate", "developer", "reit")),
        ("retail", ("retail", "consumer", "restaurant", "stores")),
    )
    for profile, aliases in rules:
        if any(alias in compact for alias in aliases):
            return profile
    return "general"


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
