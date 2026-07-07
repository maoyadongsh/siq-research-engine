from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import sys
import zipfile
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES_SRC = REPO_ROOT / "services" / "market-report-rules" / "src"
if str(RULES_SRC) not in sys.path:
    sys.path.insert(0, str(RULES_SRC))

from eu_pdf_evidence_lib import (
    company_wiki_dir_name,
    eu_report_id,
    infer_industry_profile,
    infer_metadata,
    repo_relative,
    sniff_document_format,
    write_eu_company_wiki_indexes,
)
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
from market_report_rules_service.models import AccountingStandard, Market, ParsedArtifact, ParsedFact, ParsedTable
from market_report_rules_service.normalization import infer_currency, parse_date, parse_decimal
from market_report_rules_service.pipeline import build_package_aware_load_plan, process_artifact


PARSER_VERSION = os.environ.get("SIQ_EU_ESEF_PARSER_VERSION", "eu_esef_evidence_parser_v1")
RULES_VERSION = os.environ.get("SIQ_EU_RULES_VERSION", "eu_ifrs_rules_v1")


def read_json(path: Path, default: Any = None) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else ({} if default is None else default)


def write_eu_esef_evidence_package(
    source_path: Path,
    output_root: Path,
    metadata_path: Path | None = None,
    *,
    force: bool = False,
) -> Path:
    source_path = source_path.resolve()
    metadata = _complete_metadata(source_path, metadata_path)
    payload = _load_source_payload(source_path)
    root: ET.Element | None = None
    loose_html: LooseHTMLDocument | None = None
    ns_prefixes = _namespace_prefixes(payload.entry_bytes)
    if metadata["document_format"] == "html":
        loose_html = parse_loose_html_document(payload.entry_bytes, metadata, source_file=payload.entry_rel)
        contexts: dict[str, dict[str, Any]] = {}
        units: dict[str, dict[str, Any]] = {}
        facts_raw: list[dict[str, Any]] = []
        tables_index = loose_html.tables_index
        parsed_tables = loose_html.parsed_tables
    else:
        root = _xml_root(payload.entry_bytes)
        contexts = extract_contexts(root, ns_prefixes)
        units = extract_units(root, ns_prefixes)
        facts_raw = extract_facts(root, contexts, units, ns_prefixes, source_file=payload.entry_rel)
        tables_index, parsed_tables = extract_html_tables(root)
    parsed_facts = parsed_facts_from_raw(facts_raw, metadata)
    filing_id = f"EU:{metadata['country']}:{metadata['ticker']}:{metadata['fiscal_year']}:{metadata['report_type']}"

    artifact = ParsedArtifact(
        artifact_id=f"EU:{metadata['country']}:{metadata['ticker']}:{metadata['accession_number']}",
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
        unit=metadata["currency"],
        source_url=metadata["source_url"],
        source_files={"source": str(source_path), "entrypoint": payload.entry_rel},
        facts=parsed_facts,
        tables=parsed_tables,
        document_full={"eu_xbrl_facts_raw": facts_raw, "eu_html_tables": tables_index},
        metadata=metadata,
    )
    result = process_artifact(artifact, include_load_plan=True)
    financial_data = financial_data_contract(result.extraction)
    financial_checks = financial_checks_contract(result.validation)
    normalized_metrics: list[dict[str, Any]]

    report_id = eu_report_id(artifact.fiscal_year, artifact.report_type, _package_leaf(metadata))
    company_wiki_id = company_wiki_dir_name(artifact.ticker, artifact.company_name)
    company_dir = output_root / "companies" / company_wiki_id
    package_dir = company_dir / "reports" / report_id
    if package_dir.exists() and force:
        shutil.rmtree(package_dir)
    for name in ("raw", "sections", "tables", "xbrl", "metrics", "qa"):
        (package_dir / name).mkdir(parents=True, exist_ok=True)

    _write_raw_artifacts(package_dir, source_path, payload)
    markdown = loose_html.markdown if loose_html is not None else _report_markdown(root, metadata)
    (package_dir / "sections" / "report.md").write_text(markdown, encoding="utf-8")
    write_json(package_dir / "sections" / "section_index.json", _section_index(markdown, metadata))
    _write_tables(package_dir, tables_index)
    _write_xbrl_artifacts(package_dir, payload, facts_raw, contexts, units)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "market": "EU",
        "country": metadata["country"],
        "filing_id": filing_id,
        "report_id": report_id,
        "company_id": artifact.company_id,
        "company_wiki_id": company_wiki_id,
        "company_wiki_path": repo_relative(company_dir),
        "wiki_report_path": repo_relative(package_dir),
        "ticker": artifact.ticker,
        "company_name": artifact.company_name,
        "exchange": metadata.get("exchange"),
        "source_id": metadata["source_id"],
        "source_tier": metadata["source_tier"],
        "form": metadata["form"],
        "report_type": metadata["report_type"],
        "fiscal_year": artifact.fiscal_year,
        "fiscal_period": artifact.fiscal_period,
        "period_end": metadata["period_end"],
        "published_at": metadata["published_at"],
        "source_url": metadata["source_url"],
        "landing_url": metadata.get("landing_url"),
        "local_source_path": payload.local_source_path,
        "document_format": metadata["document_format"],
        "accounting_standard": artifact.accounting_standard.value,
        "report_language": metadata.get("language"),
        "parser_version": PARSER_VERSION,
        "rules_version": RULES_VERSION,
        "quality_status": financial_checks.get("overall_status") or "warning",
        "artifact_hashes": {},
        "accession_number": metadata["accession_number"],
        "currency": artifact.currency,
        "industry_profile": artifact.industry_profile,
        "downloaded_file_path": str(source_path),
        "download_metadata_path": str(metadata_path) if metadata_path else None,
        "inline_xbrl": metadata["document_format"] in {"esef_zip", "ixbrl_xhtml"} or any(fact.get("source_type") == "eu_ixbrl_fact" for fact in facts_raw),
        "xbrl_taxonomy": _taxonomy_summary(facts_raw),
        "xbrl_namespaces": payload.namespaces,
        "xbrl_entrypoint": payload.entry_rel,
    }
    manifest["parse_run_id"] = result.load_plan.parse_run_id if result.load_plan else stable_parse_run_id(manifest, {})
    source_map = source_map_from_financial_data(manifest=manifest, financial_data=financial_data, package_dir=package_dir)
    normalized_metrics = normalized_metrics_from_financial_data(manifest=manifest, financial_data=financial_data, source_map=source_map)
    quality = build_quality_report(
        manifest=manifest,
        financial_data=financial_data,
        financial_checks=financial_checks,
        section_count=1 if markdown else 0,
        table_count=len(tables_index),
        raw_fact_count=len(facts_raw),
        source_map=source_map,
        parser_warnings=_parser_warnings_for_payload(metadata, facts_raw, parsed_tables),
        rule_warnings=list(result.extraction.warnings) + list(result.validation.warnings),
    )
    quality.update(
        {
            "parser_status": "ok" if facts_raw or parsed_tables else "no_structured_content",
            "rule_status": financial_checks.get("overall_status") or "warning",
            "document_format": metadata["document_format"],
            "country": metadata["country"],
            "xbrl_fact_count": len(facts_raw),
            "xbrl_context_count": len(contexts),
            "xbrl_unit_count": len(units),
            "entrypoint": payload.entry_rel,
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
    _validation_with_package_gates, load_plan = build_package_aware_load_plan(
        result.extraction,
        result.validation,
        package_dir=package_dir,
    )
    write_json(package_dir / "metrics" / "load_plan.json", load_plan.model_dump(mode="json"))
    (package_dir / "README.md").write_text(_readme(manifest, quality), encoding="utf-8")
    write_eu_company_wiki_indexes(output_root, company_dir, manifest, quality)
    validation = validate_evidence_package(package_dir)
    if not validation.ok:
        write_json(package_dir / "qa" / "contract_validation.json", validation.as_dict())
    return package_dir


class SourcePayload:
    def __init__(
        self,
        *,
        entry_rel: str,
        entry_bytes: bytes,
        document_format: str,
        local_source_path: str,
        extracted_files: list[dict[str, Any]],
        namespaces: dict[str, str],
    ) -> None:
        self.entry_rel = entry_rel
        self.entry_bytes = entry_bytes
        self.document_format = document_format
        self.local_source_path = local_source_path
        self.extracted_files = extracted_files
        self.namespaces = namespaces


class LooseHTMLDocument:
    def __init__(self, *, markdown: str, tables_index: list[dict[str, Any]], parsed_tables: list[ParsedTable]) -> None:
        self.markdown = markdown
        self.tables_index = tables_index
        self.parsed_tables = parsed_tables


class _LooseHTMLReportParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self.tables: list[dict[str, Any]] = []
        self._skip_depth = 0
        self._block_tag: str | None = None
        self._block_parts: list[str] = []
        self._heading_level: int | None = None
        self._heading_parts: list[str] = []
        self._last_heading: str | None = None
        self._table_depth = 0
        self._table_attrs: dict[str, str] = {}
        self._table_rows: list[list[str]] = []
        self._table_caption: list[str] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._caption_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_map = {key.lower(): value or "" for key, value in attrs}
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "table":
            if self._table_depth == 0:
                self._table_attrs = attrs_map
                self._table_rows = []
                self._table_caption = []
            self._table_depth += 1
            return
        if self._table_depth:
            if tag == "caption":
                self._caption_depth += 1
            elif tag == "tr" and self._table_depth == 1:
                self._row = []
            elif tag in {"td", "th"} and self._row is not None and self._table_depth == 1:
                self._cell = []
            return
        if re.fullmatch(r"h[1-6]", tag):
            self._heading_level = int(tag[1])
            self._heading_parts = []
        elif tag in {"p", "li"}:
            self._block_tag = tag
            self._block_parts = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._table_depth:
            if self._cell is not None:
                self._cell.append(data)
            elif self._caption_depth:
                self._table_caption.append(data)
            return
        if self._heading_level is not None:
            self._heading_parts.append(data)
        elif self._block_tag is not None:
            self._block_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if self._table_depth:
            if tag == "caption" and self._caption_depth:
                self._caption_depth -= 1
            elif tag in {"td", "th"} and self._cell is not None and self._row is not None and self._table_depth == 1:
                self._row.append(clean_text(" ".join(self._cell)))
                self._cell = None
            elif tag == "tr" and self._row is not None and self._table_depth == 1:
                if any(self._row):
                    self._table_rows.append(self._row)
                self._row = None
            elif tag == "table":
                self._table_depth -= 1
                if self._table_depth == 0 and self._table_rows:
                    self.tables.append(
                        {
                            "attrs": self._table_attrs,
                            "caption": clean_text(" ".join(self._table_caption)),
                            "heading": self._last_heading,
                            "rows": self._table_rows,
                        }
                    )
                    self._table_attrs = {}
                    self._table_rows = []
                    self._table_caption = []
            return
        if re.fullmatch(r"h[1-6]", tag) and self._heading_level is not None:
            text = clean_text(" ".join(self._heading_parts))
            if text:
                level = max(1, min(self._heading_level, 4))
                self.blocks.append(f"{'#' * level} {text}")
                self._last_heading = text
            self._heading_level = None
            self._heading_parts = []
        elif tag == self._block_tag:
            text = clean_text(" ".join(self._block_parts))
            if text:
                prefix = "- " if self._block_tag == "li" else ""
                self.blocks.append(f"{prefix}{text}")
            self._block_tag = None
            self._block_parts = []


def parse_loose_html_document(data: bytes, metadata: dict[str, Any], *, source_file: str) -> LooseHTMLDocument:
    parser = _LooseHTMLReportParser()
    parser.feed(data.decode("utf-8", errors="ignore"))
    title = f"# {metadata.get('company_name')} {metadata.get('fiscal_year')} {metadata.get('form')}"
    markdown_blocks = [title, *parser.blocks]
    tables_index: list[dict[str, Any]] = []
    parsed_tables: list[ParsedTable] = []
    for table_no, table in enumerate(parser.tables, start=1):
        rows = table.get("rows") or []
        if not rows:
            continue
        attrs = table.get("attrs") if isinstance(table.get("attrs"), dict) else {}
        anchor = attrs.get("id") or attrs.get("name") or f"table_{table_no:04d}"
        title_text = table.get("caption") or table.get("heading") or f"HTML table {table_no}"
        table_index = len(tables_index) + 1
        table_id = stable_id("eu_html_table", source_file, table_index, title_text, rows[:3])
        item = {
            "table_id": table_id,
            "source_type": "html_table",
            "table_index": table_index,
            "title": title_text,
            "html_anchor": anchor,
            "xpath": f"//table[{table_no}]",
            "row_count": len(rows),
            "column_count": max((len(row) for row in rows), default=0),
            "table_json_path": f"tables/table_{table_index:04d}.json",
            "rows": rows[:500],
            "raw": {"source_type": "html_table", "html_anchor": anchor, "xpath": f"//table[{table_no}]", "source_file": source_file},
        }
        unit = _infer_table_unit(title_text, rows, metadata)
        currency = infer_currency(unit, title_text, metadata.get("currency"), default=metadata.get("currency"))
        tables_index.append(item)
        parsed_tables.append(
            ParsedTable(
                table_id=table_id,
                title=title_text,
                rows=rows,
                table_index=table_index,
                unit=unit,
                currency=currency,
                raw=item,
            )
        )
    markdown = "\n\n".join(block for block in markdown_blocks if block)
    return LooseHTMLDocument(markdown=markdown + "\n", tables_index=tables_index, parsed_tables=parsed_tables)


def _complete_metadata(source_path: Path, metadata_path: Path | None) -> dict[str, Any]:
    metadata = infer_metadata(source_path, metadata_path)
    document_format = sniff_document_format(source_path)
    if document_format == "html" and source_path.suffix.lower() in {".xhtml", ".htm", ".html"}:
        sample = source_path.read_text(encoding="utf-8", errors="ignore")[:4096].lower()
        if "ix:" in sample or "inline_xbrl" in sample or "nonfraction" in sample:
            document_format = "ixbrl_xhtml"
    metadata["document_format"] = document_format
    metadata["source_url"] = metadata.get("source_url") or source_path.as_uri()
    metadata["published_at"] = metadata.get("published_at") or metadata.get("period_end")
    metadata["period_end"] = metadata.get("period_end") or f"{metadata.get('fiscal_year') or 'unknown'}-12-31"
    metadata["fiscal_year"] = metadata.get("fiscal_year") or _int_or_none(str(metadata.get("period_end"))[:4])
    metadata["accession_number"] = metadata.get("accession_number") or stable_id(source_path.name)[:12]
    metadata["industry_profile"] = metadata.get("industry_profile") or infer_industry_profile(metadata["ticker"], metadata["company_name"], metadata.get("title") or "")
    return metadata


def _load_source_payload(source_path: Path) -> SourcePayload:
    suffix = source_path.suffix.lower()
    if suffix == ".zip":
        with zipfile.ZipFile(source_path) as zf:
            names = [name for name in zf.namelist() if not name.endswith("/")]
            entry = _choose_entrypoint(names)
            entry_bytes = zf.read(entry)
            extracted = [
                {"path": name, "size_bytes": zf.getinfo(name).file_size, "sha256": hashlib.sha256(zf.read(name)).hexdigest()}
                for name in names
            ]
        return SourcePayload(
            entry_rel=f"raw/extracted/{entry}",
            entry_bytes=entry_bytes,
            document_format="esef_zip",
            local_source_path="raw/esef.zip",
            extracted_files=extracted,
            namespaces=_namespace_prefixes(entry_bytes),
        )
    data = source_path.read_bytes()
    document_format = sniff_document_format(source_path)
    if document_format == "html" and suffix in {".xhtml", ".html", ".htm"}:
        sample = data[:4096].decode("utf-8", errors="ignore").lower()
        if "ix:" in sample or "inline_xbrl" in sample or "nonfraction" in sample:
            document_format = "ixbrl_xhtml"
    raw_name = "report.html" if document_format == "html" else "report.xhtml" if document_format == "ixbrl_xhtml" else f"report{suffix or '.xml'}"
    return SourcePayload(
        entry_rel=f"raw/{raw_name}",
        entry_bytes=data,
        document_format=document_format,
        local_source_path=f"raw/{raw_name}",
        extracted_files=[],
        namespaces=_namespace_prefixes(data),
    )


def _choose_entrypoint(names: list[str]) -> str:
    def rank(name: str) -> tuple[int, int, str]:
        lower = name.lower()
        if lower.endswith((".xhtml", ".html", ".htm")) and not any(token in lower for token in ("taxonomy", "label", "pre", "cal", "def")):
            return (0, len(name), name)
        if lower.endswith((".xbrl", ".xml")) and not lower.endswith((".xsd",)):
            return (1, len(name), name)
        return (9, len(name), name)

    candidates = [name for name in names if rank(name)[0] < 9]
    if not candidates:
        raise SystemExit("No XHTML/XML entrypoint found in ESEF ZIP")
    return sorted(candidates, key=rank)[0]


def _xml_root(data: bytes) -> ET.Element:
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise SystemExit(f"Failed to parse ESEF/iXBRL XML/XHTML entrypoint: {exc}") from exc


def _namespace_prefixes(data: bytes) -> dict[str, str]:
    text = data.decode("utf-8", errors="ignore")
    prefixes: dict[str, str] = {}
    for prefix, uri in re.findall(r"xmlns(?::([A-Za-z0-9_.-]+))?=[\"']([^\"']+)[\"']", text):
        prefixes.setdefault(uri, prefix)
    return prefixes


def extract_contexts(root: ET.Element, ns_prefixes: dict[str, str]) -> dict[str, dict[str, Any]]:
    contexts: dict[str, dict[str, Any]] = {}
    for context in _iter_local(root, "context"):
        context_id = context.attrib.get("id")
        if not context_id:
            continue
        start = _child_text(context, "startDate")
        end = _child_text(context, "endDate")
        instant = _child_text(context, "instant")
        dimensions: dict[str, str] = {}
        for member in context.iter():
            local = _local_name(member.tag)
            if local == "explicitMember":
                dimension = member.attrib.get("dimension")
                if dimension:
                    dimensions[dimension] = clean_text(" ".join(member.itertext()))
            elif local == "typedMember":
                dimension = member.attrib.get("dimension")
                if dimension:
                    dimensions[dimension] = clean_text(" ".join(member.itertext()))
        contexts[context_id] = {
            "context_ref": context_id,
            "entity_identifier": _child_text(context, "identifier"),
            "period_start": start,
            "period_end": end or instant,
            "instant": instant,
            "duration_days": _duration_days(start, end),
            "dimensions": dimensions,
            "raw": ET.tostring(context, encoding="unicode")[:10000],
        }
    return contexts


def extract_units(root: ET.Element, ns_prefixes: dict[str, str]) -> dict[str, dict[str, Any]]:
    units: dict[str, dict[str, Any]] = {}
    for unit in _iter_local(root, "unit"):
        unit_ref = unit.attrib.get("id")
        if not unit_ref:
            continue
        measures = [clean_text(" ".join(item.itertext())) for item in unit.iter() if _local_name(item.tag) == "measure"]
        numerator = _divide_measures(unit, "unitNumerator")
        denominator = _divide_measures(unit, "unitDenominator")
        units[unit_ref] = {
            "unit_ref": unit_ref,
            "measure": measures[0] if measures else None,
            "measures": measures,
            "unit": _normalize_unit(unit_ref, measures),
            "numerator": numerator,
            "denominator": denominator,
            "raw": ET.tostring(unit, encoding="unicode")[:10000],
        }
    return units


def extract_facts(
    root: ET.Element,
    contexts: dict[str, dict[str, Any]],
    units: dict[str, dict[str, Any]],
    ns_prefixes: dict[str, str],
    *,
    source_file: str,
) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for elem in root.iter():
        local = _local_name(elem.tag)
        if local in {"nonFraction", "nonNumeric"} and elem.attrib.get("name"):
            fact = _inline_fact(elem, contexts, units, source_file)
        elif elem.attrib.get("contextRef") or elem.attrib.get("contextref"):
            concept = _qname(elem.tag, ns_prefixes)
            if _local_name(elem.tag) in {"context", "unit"} or concept.startswith(("xbrli:", "link:", "ix:")):
                continue
            fact = _xml_fact(elem, contexts, units, ns_prefixes, source_file)
        else:
            continue
        if fact:
            facts.append(fact)
    return facts


def _inline_fact(elem: ET.Element, contexts: dict[str, dict[str, Any]], units: dict[str, dict[str, Any]], source_file: str) -> dict[str, Any] | None:
    concept = elem.attrib.get("name")
    if not concept:
        return None
    context_ref = elem.attrib.get("contextRef") or elem.attrib.get("contextref")
    unit_ref = elem.attrib.get("unitRef") or elem.attrib.get("unitref")
    value_text = clean_text(elem.attrib.get("value") or " ".join(elem.itertext()))
    value_numeric = _scaled_decimal(value_text, elem.attrib.get("scale"), elem.attrib.get("sign"))
    context = contexts.get(context_ref or "", {})
    unit = units.get(unit_ref or "", {})
    fact_id = elem.attrib.get("id") or stable_id(source_file, concept, context_ref, unit_ref, value_text)
    return _fact_row(
        fact_id=fact_id,
        concept=concept,
        value_text=value_text,
        value_numeric=value_numeric,
        unit_ref=unit_ref,
        unit=unit.get("unit"),
        context_ref=context_ref,
        context=context,
        decimals=elem.attrib.get("decimals"),
        scale=elem.attrib.get("scale"),
        html_anchor=fact_id if elem.attrib.get("id") else None,
        xpath=None,
        source_file=source_file,
        source_type="eu_ixbrl_fact",
        raw={
            "id": elem.attrib.get("id"),
            "name": concept,
            "contextRef": context_ref,
            "unitRef": unit_ref,
            "format": elem.attrib.get("format"),
            "sign": elem.attrib.get("sign"),
            "scale": elem.attrib.get("scale"),
            "decimals": elem.attrib.get("decimals"),
            "html_snippet": ET.tostring(elem, encoding="unicode")[:2000],
        },
    )


def _xml_fact(elem: ET.Element, contexts: dict[str, dict[str, Any]], units: dict[str, dict[str, Any]], ns_prefixes: dict[str, str], source_file: str) -> dict[str, Any] | None:
    concept = _qname(elem.tag, ns_prefixes)
    context_ref = elem.attrib.get("contextRef") or elem.attrib.get("contextref")
    unit_ref = elem.attrib.get("unitRef") or elem.attrib.get("unitref")
    value_text = clean_text(" ".join(elem.itertext()))
    if not value_text:
        return None
    context = contexts.get(context_ref or "", {})
    unit = units.get(unit_ref or "", {})
    fact_id = elem.attrib.get("id") or stable_id(source_file, concept, context_ref, unit_ref, value_text)
    return _fact_row(
        fact_id=fact_id,
        concept=concept,
        value_text=value_text,
        value_numeric=parse_decimal(value_text),
        unit_ref=unit_ref,
        unit=unit.get("unit") or unit_ref,
        context_ref=context_ref,
        context=context,
        decimals=elem.attrib.get("decimals"),
        scale=elem.attrib.get("scale"),
        html_anchor=elem.attrib.get("id"),
        xpath=None,
        source_file=source_file,
        source_type="eu_xbrl_fact",
        raw={"id": elem.attrib.get("id"), "name": concept, "contextRef": context_ref, "unitRef": unit_ref},
    )


def _fact_row(
    *,
    fact_id: str,
    concept: str,
    value_text: str,
    value_numeric: Decimal | None,
    unit_ref: str | None,
    unit: str | None,
    context_ref: str | None,
    context: dict[str, Any],
    decimals: str | None,
    scale: str | None,
    html_anchor: str | None,
    xpath: str | None,
    source_file: str,
    source_type: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    return {
        "fact_id": fact_id,
        "concept": concept,
        "taxonomy": concept.split(":", 1)[0] if ":" in concept else None,
        "label": _label_from_concept(concept),
        "value_text": value_text,
        "value_numeric": str(value_numeric) if value_numeric is not None else None,
        "unit_ref": unit_ref,
        "unit": unit,
        "decimals": decimals,
        "scale": scale,
        "context_ref": context_ref,
        "period_start": context.get("period_start"),
        "period_end": context.get("period_end"),
        "duration_days": context.get("duration_days"),
        "instant": context.get("instant"),
        "fiscal_year": _int_or_none(str(context.get("period_end") or "")[:4]),
        "fiscal_period": "FY",
        "dimensions": context.get("dimensions") or {},
        "is_extension": _is_extension_concept(concept),
        "html_anchor": html_anchor,
        "xpath": xpath,
        "source_type": source_type,
        "source_file": source_file,
        "raw": {**raw, "fact_id": fact_id, "context_ref": context_ref, "unit_ref": unit_ref, "value_text": value_text},
    }


def parsed_facts_from_raw(facts_raw: list[dict[str, Any]], metadata: dict[str, Any]) -> list[ParsedFact]:
    facts: list[ParsedFact] = []
    for fact in facts_raw:
        value = parse_decimal(fact.get("value_numeric"))
        if value is None:
            continue
        facts.append(
            ParsedFact(
                concept=str(fact.get("concept") or ""),
                value=value,
                unit=fact.get("unit") or metadata.get("currency"),
                fiscal_year=fact.get("fiscal_year") or metadata.get("fiscal_year"),
                fiscal_period=fact.get("fiscal_period") or metadata.get("fiscal_period"),
                period_start=parse_date(fact.get("period_start")),
                period_end=parse_date(fact.get("period_end") or fact.get("instant") or metadata.get("period_end")),
                duration_days=fact.get("duration_days"),
                form=metadata.get("form"),
                context_id=fact.get("context_ref"),
                accession_number=metadata.get("accession_number"),
                decimals=_int_or_none(fact.get("decimals")),
                label=fact.get("label"),
                raw={
                    **(fact.get("raw") or {}),
                    "source_type": fact.get("source_type") or "eu_xbrl_fact",
                    "source_file": fact.get("source_file"),
                    "html_anchor": fact.get("html_anchor"),
                    "xpath": fact.get("xpath"),
                    "context_ref": fact.get("context_ref"),
                    "unit_ref": fact.get("unit_ref"),
                    "fact_id": fact.get("fact_id"),
                    "dimensions": fact.get("dimensions") or {},
                    "is_extension": fact.get("is_extension"),
                    "value_text": fact.get("value_text"),
                },
            )
        )
    return facts


def extract_html_tables(root: ET.Element) -> tuple[list[dict[str, Any]], list[ParsedTable]]:
    index: list[dict[str, Any]] = []
    parsed: list[ParsedTable] = []
    for table_no, table in enumerate(_iter_local(root, "table"), start=1):
        rows: list[list[str]] = []
        for tr in table.iter():
            if _local_name(tr.tag) != "tr":
                continue
            cells = [clean_text(" ".join(cell.itertext())) for cell in tr.iter() if _local_name(cell.tag) in {"td", "th"}]
            if any(cells):
                rows.append(cells)
        if not rows:
            continue
        title = _table_caption(table) or f"HTML table {table_no}"
        table_id = stable_id("eu_html_table", table_no, title, rows[:3])
        item = {
            "table_id": table_id,
            "source_type": "html_table",
            "table_index": len(index) + 1,
            "title": title,
            "html_anchor": table.attrib.get("id") or f"table_{table_no:04d}",
            "xpath": None,
            "row_count": len(rows),
            "column_count": max(len(row) for row in rows),
            "table_json_path": f"tables/table_{len(index) + 1:04d}.json",
            "rows": rows[:200],
            "raw": {"source_type": "html_table", "html_anchor": table.attrib.get("id")},
        }
        index.append(item)
        parsed.append(ParsedTable(table_id=table_id, title=title, rows=rows, table_index=item["table_index"], raw=item))
    return index, parsed


def _write_raw_artifacts(package_dir: Path, source_path: Path, payload: SourcePayload) -> None:
    raw_dir = package_dir / "raw"
    if payload.document_format == "esef_zip":
        shutil.copy2(source_path, raw_dir / "esef.zip")
        with zipfile.ZipFile(source_path) as zf:
            _safe_extract_zip(zf, raw_dir / "extracted")
    else:
        shutil.copy2(source_path, package_dir / payload.local_source_path)
    write_json(raw_dir / "extracted_manifest.json", {"schema_version": "eu_esef_extracted_manifest_v1", "entrypoint": payload.entry_rel, "files": payload.extracted_files})


def _write_xbrl_artifacts(
    package_dir: Path,
    payload: SourcePayload,
    facts_raw: list[dict[str, Any]],
    contexts: dict[str, dict[str, Any]],
    units: dict[str, dict[str, Any]],
) -> None:
    write_json(package_dir / "xbrl" / "facts_raw.json", {"schema_version": "eu_xbrl_facts_raw_v1", "facts": facts_raw})
    write_json(package_dir / "xbrl" / "contexts.json", {"schema_version": "eu_xbrl_contexts_v1", "contexts": contexts})
    write_json(package_dir / "xbrl" / "units.json", {"schema_version": "eu_xbrl_units_v1", "units": units})
    write_json(package_dir / "xbrl" / "entrypoints.json", {"schema_version": "eu_xbrl_entrypoints_v1", "primary": payload.entry_rel, "document_format": payload.document_format})
    write_json(package_dir / "xbrl" / "taxonomy.json", _taxonomy_summary(facts_raw))


def _write_tables(package_dir: Path, tables: list[dict[str, Any]]) -> None:
    write_json(package_dir / "tables" / "table_index.json", {"schema_version": "eu_table_index_v1", "tables": [{k: v for k, v in table.items() if k != "rows"} for table in tables]})
    for table in tables:
        write_json(package_dir / str(table["table_json_path"]), {**table, "rows": table.get("rows") or []})


def _report_markdown(root: ET.Element, metadata: dict[str, Any]) -> str:
    text = clean_text(" ".join(root.itertext()))
    if len(text) > 120000:
        text = text[:120000]
    return f"# {metadata.get('company_name')} {metadata.get('fiscal_year')} {metadata.get('form')}\n\n{text}\n"


def _parser_warnings_for_payload(metadata: dict[str, Any], facts_raw: list[dict[str, Any]], tables: list[ParsedTable]) -> list[str]:
    document_format = metadata.get("document_format")
    if document_format == "html":
        if tables:
            return ["Plain HTML report: no XBRL facts were extracted; metrics are table-derived."]
        return ["Plain HTML report: no tables or XBRL facts were extracted."]
    if facts_raw:
        return []
    return ["No EU ESEF/iXBRL facts were extracted."]


def _infer_table_unit(title: Any, rows: list[list[Any]], metadata: dict[str, Any]) -> str | None:
    haystack = " ".join([str(title or ""), *[" ".join(str(cell or "") for cell in row[:5]) for row in rows[:5]]])
    currency = metadata.get("currency")
    currency_match = re.search(r"\b(EUR|GBP|CHF|USD)\b|€|£|\bUS\$", haystack, flags=re.I)
    scale_match = re.search(r"\b(million|millions|bn|billion|thousand|thousands)\b", haystack, flags=re.I)
    if currency_match and scale_match:
        return f"{_currency_token(currency_match.group(0), currency)} {scale_match.group(1).lower()}"
    if currency_match:
        return _currency_token(currency_match.group(0), currency)
    if scale_match:
        return f"{currency or ''} {scale_match.group(1).lower()}".strip()
    return currency


def _currency_token(value: str, default: Any = None) -> str:
    text = str(value or "").upper()
    if "€" in text or "EUR" in text:
        return "EUR"
    if "£" in text or "GBP" in text:
        return "GBP"
    if "CHF" in text:
        return "CHF"
    if "US" in text or "USD" in text:
        return "USD"
    return str(default or text or "")


def _section_index(markdown: str, metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "eu_section_index_v1",
        "sections": [
            {
                "section_id": "report",
                "title": f"{metadata.get('company_name')} report",
                "level": 1,
                "line_start": 1,
                "char_start": 0,
                "char_end": len(markdown),
            }
        ],
    }


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _safe_extract_zip(zf: zipfile.ZipFile, target: Path) -> None:
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)
    for info in zf.infolist():
        if info.is_dir():
            continue
        out = (target / info.filename).resolve()
        try:
            out.relative_to(target)
        except ValueError as exc:
            raise SystemExit(f"Unsafe path in ESEF ZIP: {info.filename}") from exc
        out.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, out.open("wb") as dst:
            shutil.copyfileobj(src, dst)


def _iter_local(root: ET.Element, local_name: str):
    for elem in root.iter():
        if _local_name(elem.tag) == local_name:
            yield elem


def _local_name(tag: Any) -> str:
    text = str(tag)
    if text.startswith("{"):
        return text.rsplit("}", 1)[-1]
    return text.split(":", 1)[-1]


def _qname(tag: Any, ns_prefixes: dict[str, str]) -> str:
    text = str(tag)
    if not text.startswith("{"):
        return text
    namespace, local = text[1:].split("}", 1)
    prefix = ns_prefixes.get(namespace) or _infer_prefix(namespace)
    return f"{prefix}:{local}" if prefix else local


def _infer_prefix(namespace: str) -> str:
    lower = namespace.lower()
    if "ifrs" in lower:
        return "ifrs-full"
    if "iso4217" in lower:
        return "iso4217"
    if "inline" in lower or "ixbrl" in lower:
        return "ix"
    if "xbrl" in lower:
        return "xbrli"
    return namespace.rstrip("/").rsplit("/", 1)[-1].replace("-", "_")


def _child_text(elem: ET.Element, local_name: str) -> str | None:
    for child in elem.iter():
        if _local_name(child.tag) == local_name:
            return clean_text(" ".join(child.itertext()))
    return None


def _duration_days(start: str | None, end: str | None) -> int | None:
    start_date = parse_date(start)
    end_date = parse_date(end)
    if start_date and end_date:
        return (end_date - start_date).days + 1
    return None


def _divide_measures(unit: ET.Element, container_name: str) -> list[str]:
    rows: list[str] = []
    for container in unit.iter():
        if _local_name(container.tag) != container_name:
            continue
        for measure in container.iter():
            if _local_name(measure.tag) == "measure":
                rows.append(clean_text(" ".join(measure.itertext())))
    return rows


def _normalize_unit(unit_ref: str, measures: list[str]) -> str:
    text = " ".join([unit_ref, *measures]).lower()
    for code in ("eur", "gbp", "chf", "usd"):
        if code in text:
            return code.upper()
    if "share" in text:
        return "shares"
    if "pure" in text or "number" in text:
        return "number"
    return unit_ref


def _scaled_decimal(value_text: str, scale: str | None, sign: str | None) -> Decimal | None:
    value = parse_decimal(value_text)
    if value is None:
        return None
    if scale not in (None, ""):
        try:
            value = value * (Decimal(10) ** int(str(scale)))
        except (ValueError, ArithmeticError):
            pass
    if sign == "-":
        value = -abs(value)
    return value


def _label_from_concept(concept: str) -> str:
    label = concept.split(":", 1)[-1]
    return clean_text(re.sub(r"(?<!^)([A-Z])", r" \1", label))


def _is_extension_concept(concept: str) -> bool:
    taxonomy = concept.split(":", 1)[0].lower() if ":" in concept else ""
    return bool(taxonomy) and taxonomy not in {"ifrs-full", "ifrs", "esef_cor", "esef-cor", "dei", "country"}


def _table_caption(table: ET.Element) -> str | None:
    for child in table.iter():
        if _local_name(child.tag) == "caption":
            return clean_text(" ".join(child.itertext()))
    return None


def _taxonomy_summary(facts_raw: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    extension_count = 0
    for fact in facts_raw:
        taxonomy = str(fact.get("taxonomy") or "unknown")
        counts[taxonomy] = counts.get(taxonomy, 0) + 1
        extension_count += 1 if fact.get("is_extension") else 0
    return {"schema_version": "eu_xbrl_taxonomy_v1", "taxonomy_counts": counts, "extension_fact_count": extension_count, "fact_count": len(facts_raw)}


def _package_leaf(metadata: dict[str, Any]) -> str:
    year = metadata.get("fiscal_year") or "unknown"
    document_format = str(metadata.get("document_format") or "esef").replace("_", "-")
    return f"{metadata.get('report_type')}_{metadata.get('country')}-{metadata.get('ticker')}-{year}-{document_format}"


def _readme(manifest: dict[str, Any], quality: dict[str, Any]) -> str:
    return (
        f"# {manifest.get('country')} {manifest.get('ticker')} {manifest.get('fiscal_year')} {manifest.get('form')}\n\n"
        f"- Market: `{manifest.get('market')}`\n"
        f"- Filing ID: `{manifest.get('filing_id')}`\n"
        f"- Document format: `{manifest.get('document_format')}`\n"
        f"- XBRL facts: `{quality.get('xbrl_fact_count')}`\n"
        f"- Quality: `{quality.get('overall_status')}`\n"
        f"- Source: {manifest.get('source_url') or 'local file'}\n"
    )


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
