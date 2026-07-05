from __future__ import annotations

import html
import hashlib
import json
import os
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
    stable_id,
    stable_parse_run_id,
    validate_evidence_package,
    write_json,
)
from market_report_rules_service.models import AccountingStandard, Market, ParsedArtifact, ParsedTable
from market_report_rules_service.normalization import infer_currency, parse_date
from market_report_rules_service.pipeline import process_artifact


PARSER_VERSION = os.environ.get("SIQ_EU_PARSER_VERSION", "eu_pdf_evidence_parser_v1")
RULES_VERSION = os.environ.get("SIQ_EU_RULES_VERSION", "eu_ifrs_rules_v1")


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


def safe_wiki_slug(value: Any, fallback: str = "unknown") -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "-", text)
    text = re.sub(r"[,&]+", "", text)
    text = re.sub(r"[()\[\]{}]+", "", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip(" ._-") or fallback


def company_wiki_dir_name(ticker: Any, company_name: Any) -> str:
    return f"{safe_wiki_slug(ticker, 'UNKNOWN')}-{safe_wiki_slug(company_name, 'unknown')}"


def eu_report_id(fiscal_year: Any, report_type: Any, filing_key: Any) -> str:
    year = safe_wiki_slug(fiscal_year, "unknown")
    report_type_slug = safe_wiki_slug(report_type, "report").replace("_", "-")
    filing_slug = safe_wiki_slug(filing_key, "unknown").replace("_", "-")
    return f"{year}-{report_type_slug}-{filing_slug}"


def repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sniff_document_format(path: Path) -> str:
    suffix = path.suffix.lower()
    header = path.read_bytes()[:512] if path.exists() else b""
    if suffix == ".pdf" or header.startswith(b"%PDF-"):
        return "pdf"
    if suffix == ".zip" or header.startswith(b"PK\x03\x04"):
        return "esef_zip"
    if suffix in {".xhtml", ".html", ".htm"}:
        sample = header.decode("utf-8", errors="ignore").lower()
        return "ixbrl_xhtml" if any(token in sample for token in ("ix:", "ixt:", "xbrli:")) else "html"
    if suffix == ".xml" or header.lstrip().startswith(b"<?xml"):
        return "xml"
    return "unknown"


def infer_metadata(source_path: Path, metadata_path: Path | None = None) -> dict[str, Any]:
    metadata = read_json(metadata_path or source_path.with_suffix(source_path.suffix + ".metadata.json"), {})
    candidate = metadata.get("candidate") if isinstance(metadata, dict) else {}
    if not isinstance(candidate, dict):
        candidate = {}
    nested = candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}
    stem_parts = source_path.stem.split("_")
    country = _country_from_path(source_path) or nested.get("country") or candidate.get("country") or "unknown"
    country = _normalize_country(country)
    ticker = candidate.get("ticker") or candidate.get("company_id") or (stem_parts[2] if len(stem_parts) > 2 else "UNKNOWN")
    if isinstance(ticker, str) and ":" in ticker:
        ticker = ticker.rsplit(":", 1)[-1]
    company_name = candidate.get("company_name") or (stem_parts[0] if stem_parts else source_path.stem)
    period_end = candidate.get("report_end") or candidate.get("period_end") or _filename_date(source_path.name)
    fiscal_year = _int_or_none(str(period_end or "")[:4]) or _int_or_none(candidate.get("year")) or _int_or_none(_year_from_path(source_path))
    published_at = candidate.get("published_at") or _published_at_from_name(source_path.name)
    report_type = _report_type(candidate.get("report_type") or candidate.get("report_family") or candidate.get("form") or source_path.parent.name)
    source_url = candidate.get("document_url") or candidate.get("source_url") or candidate.get("landing_url")
    source_id = candidate.get("source_id") or ("six_direct" if country == "CH" else "eu_direct")
    return {
        "raw_metadata": metadata,
        "country": country,
        "company_id": f"{country}:{ticker}",
        "ticker": str(ticker),
        "company_name": str(company_name),
        "source_id": source_id,
        "source_tier": nested.get("source_tier") or candidate.get("source_tier") or "official_direct",
        "form": candidate.get("form") or report_type,
        "report_type": report_type,
        "fiscal_year": fiscal_year,
        "fiscal_period": _fiscal_period(report_type),
        "period_end": period_end,
        "published_at": published_at,
        "source_url": source_url,
        "landing_url": candidate.get("landing_url"),
        "accession_number": candidate.get("accession_number") or source_path.stem.rsplit("_", 1)[-1],
        "title": candidate.get("title"),
        "document_format": sniff_document_format(source_path),
        "language": candidate.get("language") or "unknown",
        "exchange": _exchange_for_country(country),
        "currency": _currency_for_country(country, metadata),
        "industry_profile": infer_industry_profile(str(ticker), str(company_name), str(candidate.get("title") or "")),
        "accounting_standard": _accounting_standard(metadata),
        "inline_xbrl": candidate.get("inline_xbrl"),
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
                    table_id=f"eu_table_{table_counter:04d}",
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
                    table_id=f"eu_table_{index:04d}",
                    title=title,
                    rows=rows,
                    page_number=item.get("pdf_page_number"),
                    table_index=index,
                    unit=_infer_unit(title, rows),
                    currency=infer_currency(title, default=None),
                    raw=item,
                )
            )
    return parsed


def build_eu_pdf_artifact(source_path: Path, parser_result_dir: Path, metadata_path: Path | None = None) -> tuple[ParsedArtifact, dict[str, Any], dict[str, Any]]:
    document_full = read_json(parser_result_dir / "document_full.json", {})
    metadata = infer_metadata(source_path, metadata_path)
    accession = metadata["accession_number"] or stable_id(source_path.name)[:12]
    filing_id = f"EU:{metadata['country']}:{metadata['ticker']}:{metadata['fiscal_year']}:{metadata['report_type']}"
    artifact = ParsedArtifact(
        artifact_id=f"EU:{metadata['country']}:{metadata['ticker']}:{accession}",
        market=Market.EU,
        company_id=metadata["company_id"],
        ticker=metadata["ticker"],
        company_name=metadata["company_name"],
        report_id=filing_id,
        report_type=metadata["report_type"],
        report_form=metadata["form"],
        fiscal_year=metadata["fiscal_year"],
        fiscal_period=metadata["fiscal_period"],
        period_end=parse_date(metadata["period_end"]),
        accounting_standard=AccountingStandard.IFRS,
        industry_profile=metadata["industry_profile"],
        currency=metadata["currency"],
        unit=_default_unit(document_full, metadata),
        source_url=metadata["source_url"],
        source_files={"pdf": str(source_path), "parser_result": str(parser_result_dir)},
        tables=parsed_tables_from_document_full(document_full),
        document_full=document_full,
        metadata=metadata,
    )
    return artifact, metadata, document_full


def write_eu_pdf_evidence_package(
    source_path: Path,
    parser_result_dir: Path,
    output_root: Path,
    metadata_path: Path | None = None,
    *,
    force: bool = False,
) -> Path:
    artifact, metadata, document_full = build_eu_pdf_artifact(source_path, parser_result_dir, metadata_path)
    result = process_artifact(artifact, include_load_plan=True)
    financial_data = financial_data_contract(result.extraction)
    financial_checks = financial_checks_contract(result.validation)

    filing_key = _filing_key(metadata, source_path)
    report_id = eu_report_id(artifact.fiscal_year, artifact.report_type, filing_key)
    company_wiki_id = company_wiki_dir_name(artifact.ticker, artifact.company_name)
    company_dir = output_root / "companies" / company_wiki_id
    package_dir = company_dir / "reports" / report_id
    if package_dir.exists() and force:
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)
    for name in ("raw", "sections", "tables", "xbrl", "metrics", "qa"):
        (package_dir / name).mkdir(exist_ok=True)

    raw_name = "report.pdf" if metadata["document_format"] == "pdf" else f"report{source_path.suffix.lower()}"
    shutil.copy2(source_path, package_dir / "raw" / raw_name)
    if metadata_path and metadata_path.exists():
        shutil.copy2(metadata_path, package_dir / "raw" / "report.metadata.json")
    else:
        write_json(package_dir / "raw" / "report.metadata.json", metadata.get("raw_metadata") or {})

    markdown = _markdown_from_document_full(document_full, parser_result_dir, metadata)
    (package_dir / "sections" / "report.md").write_text(markdown, encoding="utf-8")
    _write_section_index(package_dir, markdown, document_full)
    table_index = _write_tables(package_dir, artifact.tables)
    write_json(package_dir / "xbrl" / "facts_raw.json", {"schema_version": "eu_xbrl_facts_raw_v1", "facts": []})
    write_json(package_dir / "xbrl" / "contexts.json", {"schema_version": "eu_xbrl_contexts_v1", "contexts": []})
    write_json(package_dir / "xbrl" / "units.json", {"schema_version": "eu_xbrl_units_v1", "units": []})
    parser_quality = read_json(parser_result_dir / "quality_report.json", {})

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "market": "EU",
        "country": metadata["country"],
        "filing_id": artifact.report_id,
        "report_id": report_id,
        "company_id": artifact.company_id,
        "company_wiki_id": company_wiki_id,
        "company_wiki_path": repo_relative(company_dir),
        "wiki_report_path": repo_relative(package_dir),
        "ticker": artifact.ticker,
        "company_name": artifact.company_name,
        "exchange": metadata["exchange"],
        "source_id": metadata["source_id"],
        "source_tier": metadata["source_tier"],
        "form": metadata["form"],
        "report_type": metadata["report_type"],
        "fiscal_year": artifact.fiscal_year,
        "fiscal_period": artifact.fiscal_period,
        "period_end": metadata["period_end"],
        "published_at": metadata["published_at"],
        "source_url": metadata["source_url"],
        "landing_url": metadata["landing_url"],
        "local_source_path": f"raw/{raw_name}",
        "document_format": metadata["document_format"],
        "accounting_standard": artifact.accounting_standard.value,
        "report_language": metadata["language"],
        "parser_version": PARSER_VERSION,
        "rules_version": RULES_VERSION,
        "quality_status": financial_checks.get("overall_status") or "warning",
        "artifact_hashes": {},
        "accession_number": metadata["accession_number"],
        "currency": artifact.currency,
        "industry_profile": artifact.industry_profile,
        "downloaded_file_path": str(source_path),
        "download_metadata_path": str(metadata_path) if metadata_path else None,
        "pdf_parser_result_dir": str(parser_result_dir),
        "pdf_parser_task_id": str(parser_result_dir.name),
        "pdf_parser_quality_status": parser_quality.get("overall_status") or parser_quality.get("status") or "unknown",
        "source_pdf_sha256": _sha256_file(source_path),
        "inline_xbrl": metadata.get("inline_xbrl"),
        "xbrl_taxonomy": None,
        "xbrl_namespaces": {},
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
            "document_format": metadata["document_format"],
            "country": metadata["country"],
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
    write_eu_company_wiki_indexes(output_root, company_dir, manifest, quality)

    validation = validate_evidence_package(package_dir)
    if not validation.ok:
        write_json(package_dir / "qa" / "contract_validation.json", validation.as_dict())
    return package_dir


def _read_existing_json(path: Path) -> dict[str, Any]:
    payload = read_json(path, {})
    return payload if isinstance(payload, dict) else {}


def write_eu_company_wiki_indexes(output_root: Path, company_dir: Path, manifest: dict[str, Any], quality: dict[str, Any]) -> None:
    for dirname in ("reports", "metrics", "evidence", "semantic", "graph", "analysis", "factcheck", "tracking"):
        (company_dir / dirname).mkdir(parents=True, exist_ok=True)

    existing = _read_existing_json(company_dir / "company.json")
    report_id = str(manifest.get("report_id") or manifest.get("filing_id") or "unknown")
    report_rel = f"reports/{report_id}"
    report_entry = {
        "report_id": report_id,
        "filing_id": manifest.get("filing_id"),
        "market": "EU",
        "country": manifest.get("country"),
        "form": manifest.get("form"),
        "report_type": manifest.get("report_type"),
        "fiscal_year": manifest.get("fiscal_year"),
        "fiscal_period": manifest.get("fiscal_period"),
        "period_end": manifest.get("period_end"),
        "published_at": manifest.get("published_at"),
        "source_url": manifest.get("source_url"),
        "document_format": manifest.get("document_format"),
        "package_path": report_rel,
        "manifest": f"{report_rel}/manifest.json",
        "financial_data": f"{report_rel}/metrics/financial_data.json",
        "financial_checks": f"{report_rel}/metrics/financial_checks.json",
        "quality_report": f"{report_rel}/qa/quality_report.json",
        "source_map": f"{report_rel}/qa/source_map.json",
        "quality_status": quality.get("overall_status") or manifest.get("quality_status"),
        "wiki_report_path": manifest.get("wiki_report_path"),
    }
    reports = [item for item in existing.get("reports") or [] if isinstance(item, dict) and item.get("report_id") != report_id]
    reports.append(report_entry)
    reports.sort(key=lambda item: str(item.get("period_end") or item.get("published_at") or ""), reverse=True)
    primary_report_id = str(reports[0].get("report_id") or report_id)
    latest_report = next((item for item in reports if item.get("report_id") == primary_report_id), report_entry)

    company_json = {
        **existing,
        "schema_version": "eu_company_wiki_v1",
        "market": "EU",
        "country": manifest.get("country"),
        "company_id": manifest.get("company_id"),
        "company_wiki_id": manifest.get("company_wiki_id"),
        "company_wiki_path": manifest.get("company_wiki_path"),
        "ticker": manifest.get("ticker"),
        "isin": manifest.get("isin"),
        "lei": manifest.get("lei"),
        "exchange": manifest.get("exchange"),
        "company_name": manifest.get("company_name"),
        "industry_profile": manifest.get("industry_profile"),
        "accounting_standard": manifest.get("accounting_standard") or "IFRS",
        "currency": manifest.get("currency"),
        "primary_report_id": primary_report_id,
        "latest_filing_id": latest_report.get("filing_id"),
        "latest_fiscal_year": latest_report.get("fiscal_year"),
        "latest_period_end": latest_report.get("period_end"),
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
        "updated_at": now_iso(),
    }
    write_json(company_dir / "company.json", company_json)
    write_json(
        company_dir / "_index.json",
        {
            "schema_version": "eu_company_index_v1",
            "market": "EU",
            "country": company_json.get("country"),
            "company_id": company_json["company_id"],
            "company_wiki_id": company_json.get("company_wiki_id"),
            "company_wiki_path": company_json.get("company_wiki_path"),
            "primary_report_id": primary_report_id,
            "reports": reports,
            "updated_at": company_json["updated_at"],
        },
    )
    (company_dir / "company.md").write_text(_company_markdown(company_json, latest_report), encoding="utf-8")
    write_eu_root_catalog(output_root)


def write_eu_root_catalog(output_root: Path) -> None:
    companies: list[dict[str, Any]] = []
    for company_json_path in sorted((output_root / "companies").glob("*/company.json")):
        company = _read_existing_json(company_json_path)
        if not company:
            continue
        companies.append(
            {
                "company_id": company.get("company_id"),
                "company_wiki_id": company.get("company_wiki_id") or company_json_path.parent.name,
                "company_wiki_path": company.get("company_wiki_path") or repo_relative(company_json_path.parent),
                "market": "EU",
                "country": company.get("country"),
                "ticker": company.get("ticker"),
                "exchange": company.get("exchange"),
                "company_name": company.get("company_name"),
                "primary_report_id": company.get("primary_report_id"),
                "report_count": company.get("report_count") or len(company.get("reports") or []),
                "status": "ready",
            }
        )
    companies.sort(key=lambda item: (str(item.get("country") or ""), str(item.get("ticker") or item.get("company_id") or "")))
    write_json(
        output_root / "_meta" / "company_catalog.json",
        {
            "schema_version": "eu_company_catalog_v1",
            "market": "EU",
            "company_count": len(companies),
            "companies": companies,
            "generated_at": now_iso(),
        },
    )
    guide = output_root / "_meta" / "AGENT_GUIDE.md"
    if not guide.exists():
        guide.write_text(
            "# EU Wiki Agent Guide\n\n"
            "EU company Wiki uses `companies/<ticker>-<company>/company.json` as the company entry, "
            "then `reports/<report_id>/` for each PDF/ESEF package. Prefer `company.json`, "
            "`reports/<report_id>/manifest.json`, `metrics/financial_data.json`, "
            "`metrics/financial_checks.json`, `qa/quality_report.json`, and `qa/source_map.json` "
            "before PostgreSQL `eu_ifrs` fallback.\n",
            encoding="utf-8",
        )


def _company_markdown(company: dict[str, Any], latest: dict[str, Any]) -> str:
    return (
        f"# {company.get('ticker')} {company.get('company_name') or ''}\n\n"
        f"- Market: EU\n"
        f"- Country: `{company.get('country') or ''}`\n"
        f"- Latest report: `{latest.get('report_type')}` `{latest.get('report_id')}`\n"
        f"- Latest period end: `{latest.get('period_end')}`\n"
        f"- Quality: `{latest.get('quality_status')}`\n"
    )


def infer_industry_profile(ticker: str, company_name: str, title: str = "") -> str:
    haystack = f"{ticker} {company_name} {title}".upper()
    if ticker.upper() in {"BARC", "BNP", "INGA", "HSBA"} or any(token in haystack for token in ("BANK", "BARCLAYS", "BNP PARIBAS", "ING GROEP", "HSBC")):
        return "bank"
    if ticker.upper() in {"CS", "ZURN", "SREN", "MUV2"} or any(
        token in haystack
        for token in (
            "INSURANCE",
            "ASSURANCE",
            "REINSURANCE",
            "REINSUR",
            "SWISS RE",
            "ZURICH INSURANCE",
            "MUENCHENER RUECK",
            "MUNICH RE",
            "AXA",
        )
    ):
        return "insurance"
    if any(token in haystack for token in ("TOTALENERGIES", " BP", "ENERGY", "OIL", "GAS")):
        return "energy"
    if any(token in haystack for token in ("ASTRAZENECA", "SANOFI", "NOVARTIS", "ROCHE", "PHARMA", "MEDICINE")):
        return "pharma"
    if any(token in haystack for token in ("ASML", "SEMICONDUCTOR", "CHIP")):
        return "semiconductor"
    if any(token in haystack for token in ("NESTLE", "HEINEKEN", "CONSUMER", "BEVERAGE", "FOOD")):
        return "consumer"
    if any(token in haystack for token in ("SIEMENS", "AIR LIQUIDE", "INDUSTRIAL")):
        return "industrial"
    if any(token in haystack for token in ("TELEKOM", "TELECOM", "DEUTSCHE TELEKOM")):
        return "telecom"
    return "general"


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
    write_json(package_dir / "tables" / "table_index.json", {"schema_version": "eu_table_index_v1", "tables": rows})
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
                "title": task.get("filename") or "EU report",
                "level": 1,
                "line_start": 1,
                "char_start": 0,
                "char_end": len(markdown),
            }
        )
    write_json(package_dir / "sections" / "section_index.json", {"schema_version": "eu_section_index_v1", "sections": sections})


def _markdown_from_document_full(document_full: dict[str, Any], parser_result_dir: Path, metadata: dict[str, Any]) -> str:
    markdown = document_full.get("markdown") if isinstance(document_full.get("markdown"), dict) else {}
    content = markdown.get("content") if isinstance(markdown, dict) else None
    if content:
        return str(content)
    for candidate in (parser_result_dir / "result.md", parser_result_dir / "document.md"):
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    return f"# {metadata.get('company_name') or 'EU report'} {metadata.get('fiscal_year') or ''}\n"


def _clean_cell(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _html_table_rows(table_body: str) -> list[list[str]]:
    parser = _TableHTMLParser()
    parser.feed(table_body or "")
    return parser.rows


def _table_title(item: dict[str, Any], meta: dict[str, Any]) -> str | None:
    captions = item.get("table_caption") or meta.get("source_caption") or []
    if isinstance(captions, list) and captions:
        return " ".join(_clean_cell(value) for value in captions if _clean_cell(value)) or None
    return _clean_cell(meta.get("heading") or meta.get("title") or "") or None


def _infer_unit(title: str | None, rows: list[list[str]]) -> str | None:
    haystack = " ".join([title or "", *[" ".join(row[:5]) for row in rows[:4]]])
    match = re.search(r"(EUR|€|GBP|£|CHF|USD|US\$|dollars?)[^\n,;)]{0,40}(million|billion|thousand|mn|bn|m|k)?", haystack, flags=re.I)
    if match:
        return match.group(0)
    if re.search(r"\bin millions\b|\bmillion\b", haystack, flags=re.I):
        return "million"
    return None


def _page_number(item: dict[str, Any]) -> int | None:
    page_idx = item.get("page_idx")
    try:
        return int(page_idx) + 1
    except (TypeError, ValueError):
        return None


def _default_unit(document_full: dict[str, Any], metadata: dict[str, Any]) -> str | None:
    text = json.dumps(document_full.get("task") or {}, ensure_ascii=False)
    if metadata.get("currency") and re.search(r"\bmillion\b|\bmillions\b", text, flags=re.I):
        return f"{metadata['currency']} million"
    return metadata.get("currency")


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


def _filing_key(metadata: dict[str, Any], source_path: Path) -> str:
    year = metadata.get("fiscal_year") or "unknown"
    base = f"{metadata.get('country')}-{metadata.get('ticker')}-{year}"
    accession = str(metadata.get("accession_number") or "")
    if accession and accession not in {"manual", "unknown"} and not re.fullmatch(r"[0-9a-f]{8,}", accession):
        return f"{base}-{accession}"
    return base


def _country_from_path(path: Path) -> str | None:
    parts = list(path.parts)
    if "EU" in parts:
        index = parts.index("EU")
        if index + 1 < len(parts):
            return parts[index + 1]
    return None


def _year_from_path(path: Path) -> str | None:
    for part in path.parts:
        if re.fullmatch(r"20\d{2}", part):
            return part
    return None


def _normalize_country(value: Any) -> str:
    text = str(value or "").upper()
    return {"GB": "UK", "GBR": "UK", "UK": "UK", "FR": "FR", "DE": "DE", "NL": "NL", "CH": "CH"}.get(text, text)


def _exchange_for_country(country: str) -> str:
    return {
        "UK": "LSE",
        "FR": "Euronext Paris",
        "DE": "Xetra",
        "NL": "Euronext Amsterdam",
        "CH": "SIX",
    }.get(country, "unknown")


def _currency_for_country(country: str, metadata: dict[str, Any]) -> str:
    text = json.dumps(metadata or {}, ensure_ascii=False).lower()
    if "usd" in text or "us$" in text:
        return "USD"
    if "gbp" in text or "£" in text or "sterling" in text:
        return "GBP"
    if "chf" in text or "swiss franc" in text:
        return "CHF"
    return {"UK": "GBP", "CH": "CHF", "FR": "EUR", "DE": "EUR", "NL": "EUR"}.get(country, "EUR")


def _accounting_standard(metadata: dict[str, Any]) -> str:
    text = json.dumps(metadata or {}, ensure_ascii=False).lower()
    if "us gaap" in text or "u.s. gaap" in text:
        return "IFRS"
    return "IFRS"


def _report_type(value: Any) -> str:
    text = str(value or "").lower()
    if any(token in text for token in ("interim", "semi", "half", "中期", "半年")):
        return "semiannual"
    if any(token in text for token in ("quarter", "q1", "q2", "q3", "季度")):
        return "quarterly"
    return "annual"


def _fiscal_period(report_type: str) -> str:
    return {"annual": "FY", "semiannual": "H1", "quarterly": "Q"}.get(report_type, "FY")


def _filename_date(filename: str) -> str | None:
    match = re.search(r"(20\d{2}-\d{2}-\d{2})", filename)
    return match.group(1) if match else None


def _published_at_from_name(filename: str) -> str | None:
    matches = re.findall(r"(20\d{2}-\d{2}-\d{2})", filename)
    return matches[-1] if matches else None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _readme(manifest: dict[str, Any], quality: dict[str, Any]) -> str:
    return (
        f"# {manifest.get('country')} {manifest.get('ticker')} {manifest.get('fiscal_year')} {manifest.get('form')}\n\n"
        f"- Market: `{manifest.get('market')}`\n"
        f"- Filing ID: `{manifest.get('filing_id')}`\n"
        f"- Document format: `{manifest.get('document_format')}`\n"
        f"- Period end: `{manifest.get('period_end')}`\n"
        f"- Quality: `{quality.get('overall_status')}`\n"
        f"- Evidence coverage: `{quality.get('evidence_coverage_ratio')}`\n"
        f"- Source: {manifest.get('source_url') or 'local file'}\n"
    )
