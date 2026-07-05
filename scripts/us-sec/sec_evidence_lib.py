from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES_SRC = REPO_ROOT / "services" / "market-report-rules" / "src"
if str(RULES_SRC) not in sys.path:
    sys.path.insert(0, str(RULES_SRC))

from market_report_rules_service.contracts import financial_checks_contract, financial_data_contract
from market_report_rules_service.evidence_package import (
    SCHEMA_VERSION as MARKET_EVIDENCE_SCHEMA_VERSION,
    build_quality_report,
    compute_artifact_hashes,
    stable_parse_run_id,
)
from market_report_rules_service.models import AccountingStandard, Market, ParsedArtifact, ParsedFact, ParsedTable
from market_report_rules_service.pipeline import process_artifact

PARSER_VERSION = os.environ.get("SIQ_US_SEC_PARSER_VERSION", "sec_parser_v1")
RULES_VERSION = os.environ.get("SIQ_US_SEC_RULES_VERSION", "us_sec_rules_v1")

SECTION_DEFS_10K = (
    ("business", "item_1", r"item\s+1\.?\s+business"),
    ("risk_factors", "item_1a", r"item\s+1a\.?\s+risk\s+factors"),
    ("properties", "item_2", r"item\s+2\.?\s+properties"),
    ("legal_proceedings", "item_3", r"item\s+3\.?\s+legal\s+proceedings"),
    ("mda", "item_7", r"item\s+7\.?\s+management[’'`s\s]+discussion\s+and\s+analysis"),
    ("market_risk", "item_7a", r"item\s+7a\.?\s+quantitative\s+and\s+qualitative\s+disclosures"),
    ("financial_statements", "item_8", r"item\s+8\.?\s+financial\s+statements"),
    ("controls", "item_9a", r"item\s+9a\.?\s+controls\s+and\s+procedures"),
)

SECTION_DEFS_10Q = (
    ("financial_statements", "part_i_item_1", r"part\s+i[\s,.-]+item\s+1\.?\s+financial\s+statements"),
    ("mda", "part_i_item_2", r"part\s+i[\s,.-]+item\s+2\.?\s+management[’'`s\s]+discussion\s+and\s+analysis"),
    ("market_risk", "part_i_item_3", r"part\s+i[\s,.-]+item\s+3\.?\s+quantitative\s+and\s+qualitative"),
    ("controls", "part_i_item_4", r"part\s+i[\s,.-]+item\s+4\.?\s+controls\s+and\s+procedures"),
    ("risk_factors", "part_ii_item_1a", r"part\s+ii[\s,.-]+item\s+1a\.?\s+risk\s+factors"),
)

SECTION_DEFS_20F = (
    ("business", "item_4", r"item\s+4\.?\s+information\s+on\s+the\s+company"),
    ("risk_factors", "item_3d", r"item\s+3\.?d\.?\s+risk\s+factors"),
    ("mda", "item_5", r"item\s+5\.?\s+operating\s+and\s+financial\s+review"),
    ("financial_statements", "item_18", r"item\s+18\.?\s+financial\s+statements"),
    ("controls", "item_15", r"item\s+15\.?\s+controls\s+and\s+procedures"),
)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_id(*parts: Any) -> str:
    joined = "\x1f".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def clean_text(value: str | None) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def compact_accession(value: str | None, source_url: str | None = None) -> str:
    candidates = [value or "", source_url or ""]
    for text in candidates:
        match = re.search(r"(\d{10}-\d{2}-\d{6})", text)
        if match:
            return match.group(1)
        match = re.search(r"(\d{18})", text)
        if match:
            raw = match.group(1)
            return f"{raw[:10]}-{raw[10:12]}-{raw[12:]}"
    fallback = (value or "unknown").strip()
    return fallback if fallback and fallback.lower() != "manual" else "unknown"


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


def us_report_id(fiscal_year: Any, form: Any, accession: Any) -> str:
    year = safe_wiki_slug(fiscal_year, "unknown")
    form_slug = safe_wiki_slug(form, "filing")
    accession_slug = safe_wiki_slug(accession, "unknown")
    return f"{year}-{form_slug}-{accession_slug}"


def repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def infer_metadata(source_path: Path, metadata_path: Path | None = None) -> dict[str, Any]:
    metadata = read_json(metadata_path or source_path.with_suffix(source_path.suffix + ".metadata.json"))
    candidate = metadata.get("candidate") or {}
    downloaded = metadata.get("downloaded_file") or {}
    source_url = candidate.get("document_url") or candidate.get("source_url")
    accession = compact_accession(candidate.get("accession_number"), source_url)
    ticker = candidate.get("ticker") or candidate.get("company_id") or "UNKNOWN"
    period_end = candidate.get("report_end") or candidate.get("period_end")
    fiscal_year = int(str(period_end or candidate.get("year") or "0")[:4] or 0) or None
    return {
        "metadata": metadata,
        "ticker": str(ticker).upper(),
        "company_name": candidate.get("company_name") or ticker,
        "form": candidate.get("form") or candidate.get("report_type") or "10-K",
        "accession_number": accession,
        "fiscal_year": fiscal_year,
        "fiscal_period": "FY" if str(candidate.get("report_family") or "").lower() == "annual" else candidate.get("fiscal_period"),
        "period_end": period_end,
        "filing_date": candidate.get("published_at"),
        "accepted_at": candidate.get("accepted_at"),
        "source_url": source_url,
        "primary_document": candidate.get("primary_document"),
        "downloaded_file": downloaded,
    }


def soup_from_html(path: Path) -> BeautifulSoup:
    return BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "lxml-xml")


def extract_contexts(soup: BeautifulSoup) -> dict[str, dict[str, Any]]:
    contexts: dict[str, dict[str, Any]] = {}
    for context in soup.find_all(lambda tag: tag.name and tag.name.endswith("context")):
        context_id = context.get("id")
        if not context_id:
            continue
        start = _child_text(context, "startDate")
        end = _child_text(context, "endDate")
        instant = _child_text(context, "instant")
        dimensions = {}
        for member in context.find_all(lambda tag: tag.name and tag.name.endswith("explicitMember")):
            dimension = member.get("dimension")
            if dimension:
                dimensions[dimension] = clean_text(member.get_text(" ", strip=True))
        contexts[context_id] = {
            "context_ref": context_id,
            "entity_identifier": _child_text(context, "identifier"),
            "period_start": start,
            "period_end": end or instant,
            "instant": instant,
            "duration_days": _duration_days(start, end),
            "dimensions": dimensions,
            "raw": str(context)[:10000],
        }
    return contexts


def extract_units(soup: BeautifulSoup) -> dict[str, dict[str, Any]]:
    units: dict[str, dict[str, Any]] = {}
    for unit in soup.find_all(lambda tag: tag.name and tag.name.endswith("unit")):
        unit_id = unit.get("id")
        if not unit_id:
            continue
        measures = [clean_text(item.get_text(" ", strip=True)) for item in unit.find_all(lambda tag: tag.name and tag.name.endswith("measure"))]
        units[unit_id] = {
            "unit_ref": unit_id,
            "measures": measures,
            "unit": _normalize_unit(unit_id, measures),
            "raw": str(unit)[:10000],
        }
    return units


def extract_ixbrl_facts(soup: BeautifulSoup, contexts: dict[str, Any], units: dict[str, Any]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for tag in soup.find_all(lambda item: item.name and item.name.split(":")[-1] in {"nonFraction", "nonNumeric"}):
        concept = tag.get("name")
        if not concept:
            continue
        context_ref = tag.get("contextRef") or tag.get("contextref")
        unit_ref = tag.get("unitRef") or tag.get("unitref")
        value_text = clean_text(tag.get("value") or tag.get_text(" ", strip=True))
        numeric = parse_decimal(value_text)
        scale = tag.get("scale")
        if numeric is not None and scale not in (None, ""):
            try:
                numeric = numeric * (Decimal(10) ** int(scale))
            except (InvalidOperation, ValueError):
                pass
        if numeric is not None and tag.get("sign") == "-":
            numeric = -numeric
        context = contexts.get(context_ref or "", {})
        unit = units.get(unit_ref or "", {})
        fact_id = stable_id(concept, context_ref, unit_ref, value_text, tag.get("decimals"), tag.get("id"))
        facts.append(
            {
                "fact_id": fact_id,
                "concept": concept,
                "taxonomy": concept.split(":", 1)[0] if ":" in concept else None,
                "label": _label_from_concept(concept),
                "value_text": value_text,
                "value_numeric": str(numeric) if numeric is not None else None,
                "unit_ref": unit_ref,
                "unit": unit.get("unit"),
                "decimals": tag.get("decimals"),
                "scale": scale,
                "context_ref": context_ref,
                "period_start": context.get("period_start"),
                "period_end": context.get("period_end"),
                "duration_days": context.get("duration_days"),
                "instant": context.get("instant"),
                "fiscal_year": int(str(context.get("period_end") or "")[:4]) if str(context.get("period_end") or "")[:4].isdigit() else None,
                "dimensions": context.get("dimensions") or {},
                "is_extension": (concept.split(":", 1)[0].lower() if ":" in concept else "") not in {"us-gaap", "ifrs-full", "dei", "srt", "country"},
                "html_anchor": tag.get("id"),
                "xpath": None,
                "raw": {
                    "id": tag.get("id"),
                    "name": concept,
                    "contextRef": context_ref,
                    "unitRef": unit_ref,
                    "format": tag.get("format"),
                    "sign": tag.get("sign"),
                    "scale": scale,
                    "decimals": tag.get("decimals"),
                    "html_snippet": str(tag)[:2000],
                    "dimensions": context.get("dimensions") or {},
                },
            }
        )
    return facts


def build_sections(soup: BeautifulSoup, manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    body_text = clean_text(soup.get_text(" ", strip=True))
    form = str(manifest.get("form") or "").upper()
    section_defs = SECTION_DEFS_10Q if form == "10-Q" else SECTION_DEFS_20F if form == "20-F" else SECTION_DEFS_10K
    lowered = body_text.lower()
    candidates_by_section: dict[str, list[re.Match[str]]] = {
        section_id: [match for match in re.finditer(pattern, lowered, flags=re.IGNORECASE) if match.start() > 500]
        for _, section_id, pattern in section_defs
    }
    markers: list[tuple[int, str, str, str]] = []
    cursor = 500
    for index, (file_stem, section_id, _pattern) in enumerate(section_defs):
        next_section_id = section_defs[index + 1][1] if index + 1 < len(section_defs) else None
        chosen = _choose_section_match(
            body_text,
            candidates_by_section.get(section_id) or [],
            candidates_by_section.get(next_section_id or "") or [],
            cursor,
        )
        if chosen:
            markers.append((chosen.start(), file_stem, section_id, clean_text(body_text[chosen.start() : chosen.start() + 160])))
            cursor = chosen.start() + 1
    markers = sorted(markers, key=lambda item: item[0])
    sections: list[dict[str, Any]] = []
    markdown: dict[str, str] = {}
    for index, (start, file_stem, section_id, title) in enumerate(markers, start=1):
        end = markers[index][0] if index < len(markers) else len(body_text)
        text = body_text[start:end].strip()
        if len(text) < 200:
            continue
        section_title = _pretty_section_title(title, section_id)
        sections.append(
            {
                "section_id": section_id,
                "file": f"{file_stem}.md",
                "section_title": section_title,
                "section_order": len(sections) + 1,
                "html_anchor": section_id,
                "xpath": None,
                "char_start": start,
                "char_end": end,
                "text_hash": sha256_bytes(text.encode("utf-8")),
                "text_length": len(text),
            }
        )
        markdown[file_stem] = _section_markdown(manifest, section_id, section_title, text)
    if "notes" not in markdown:
        notes = _extract_notes(body_text)
        if notes:
            sections.append(
                {
                    "section_id": "notes",
                    "file": "notes.md",
                    "section_title": "Notes to Consolidated Financial Statements",
                    "section_order": len(sections) + 1,
                    "html_anchor": "notes",
                    "xpath": None,
                    "char_start": body_text.find(notes[:80]),
                    "char_end": body_text.find(notes[:80]) + len(notes),
                    "text_hash": sha256_bytes(notes.encode("utf-8")),
                    "text_length": len(notes),
                }
            )
            markdown["notes"] = _section_markdown(manifest, "notes", "Notes to Consolidated Financial Statements", notes)
    return sections, markdown


def extract_tables(soup: BeautifulSoup, sections: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[ParsedTable]]:
    table_index: list[dict[str, Any]] = []
    parsed: list[ParsedTable] = []
    for index, table in enumerate(soup.find_all("table"), start=1):
        rows: list[list[str]] = []
        for tr in table.find_all("tr"):
            cells = [clean_text(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"])]
            if any(cells):
                rows.append(cells)
        if not rows:
            continue
        title = _nearest_title(table)
        table_id = stable_id("table", index, title, rows[:3])
        section = _guess_table_section(title, sections)
        item = {
            "table_id": table_id,
            "table_index": len(table_index) + 1,
            "title": title,
            "section_id": section,
            "row_count": len(rows),
            "column_count": max(len(row) for row in rows),
            "html_anchor": table.get("id") or f"table_{index:04d}",
            "is_financial_statement_candidate": _is_financial_table(title, rows),
            "rows": rows[:200],
        }
        table_index.append(item)
        parsed.append(ParsedTable(table_id=table_id, title=title, rows=rows, table_index=len(table_index), raw=item))
    return table_index, parsed


def normalize_metrics(manifest: dict[str, Any], facts_raw: list[dict[str, Any]], tables: list[ParsedTable]) -> dict[str, Any]:
    parsed_facts: list[ParsedFact] = []
    for fact in facts_raw:
        value = parse_decimal(fact.get("value_numeric"))
        if value is None:
            continue
        parsed_facts.append(
            ParsedFact(
                concept=str(fact.get("concept") or ""),
                value=value,
                unit=fact.get("unit"),
                fiscal_year=fact.get("fiscal_year") or manifest.get("fiscal_year"),
                fiscal_period=manifest.get("fiscal_period"),
                period_start=parse_date(fact.get("period_start")),
                period_end=parse_date(fact.get("period_end")) or parse_date(manifest.get("period_end")),
                duration_days=fact.get("duration_days"),
                form=manifest.get("form"),
                context_id=fact.get("context_ref"),
                accession_number=manifest.get("accession_number"),
                decimals=_int_or_none(fact.get("decimals")),
                label=fact.get("label"),
                raw={**(fact.get("raw") or {}), "anchor": fact.get("html_anchor"), "context_ref": fact.get("context_ref"), "fact_id": fact.get("fact_id")},
            )
        )
    artifact = ParsedArtifact(
        artifact_id=manifest["filing_id"],
        market=Market.US,
        company_id=f"US:{manifest.get('cik') or manifest.get('ticker')}",
        ticker=manifest["ticker"],
        company_name=manifest.get("company_name"),
        report_id=manifest["filing_id"],
        report_type="annual" if str(manifest.get("form")).upper() in {"10-K", "20-F"} else "quarterly",
        report_form=manifest.get("form"),
        fiscal_year=manifest.get("fiscal_year"),
        fiscal_period=manifest.get("fiscal_period"),
        period_end=parse_date(manifest.get("period_end")),
        accounting_standard=AccountingStandard(manifest.get("accounting_standard") or "US_GAAP"),
        industry_profile=manifest.get("industry_profile") or "general",
        currency="USD",
        unit="USD",
        source_url=manifest.get("source_url"),
        source_files={"manifest": "manifest.json", "raw": manifest.get("local_source_path")},
        facts=parsed_facts,
        tables=tables,
        document_full={"sec_ixbrl_facts": facts_raw},
        metadata=manifest,
    )
    result = process_artifact(artifact, include_load_plan=True)
    financial_data = financial_data_contract(result.extraction)
    financial_checks = financial_checks_contract(result.validation)
    normalized = []
    raw_fact_by_key = {(item.get("concept"), item.get("context_ref"), str(parse_decimal(item.get("value_numeric")))): item for item in facts_raw}
    for statement in result.extraction.statements:
        for fact in statement.items:
            raw_fact = raw_fact_by_key.get((fact.local_name, _fact_context_ref(fact), str(fact.value)))
            normalized.append(_normalized_row(manifest, result.load_plan.parse_run_id if result.load_plan else None, fact, raw_fact))
    for fact in result.extraction.key_metrics:
        raw_fact = raw_fact_by_key.get((fact.local_name, _fact_context_ref(fact), str(fact.value)))
        normalized.append(_normalized_row(manifest, result.load_plan.parse_run_id if result.load_plan else None, fact, raw_fact))
    return {
        "normalized_metrics": normalized,
        "financial_data": financial_data,
        "financial_checks": financial_checks,
        "quality_status": financial_checks.get("overall_status", "warning"),
        "warnings": list(result.extraction.warnings) + list(result.validation.warnings),
    }


def build_source_map(manifest: dict[str, Any], sections: list[dict[str, Any]], facts_raw: list[dict[str, Any]], tables: list[dict[str, Any]]) -> dict[str, Any]:
    entries = []
    for section in sections:
        evidence_id = stable_id(manifest["filing_id"], "section", section["section_id"])
        entries.append({
            "evidence_id": evidence_id,
            "source_type": "sec_html_section",
            "section_id": section["section_id"],
            "html_anchor": section.get("html_anchor"),
            "local_path": f"sections/{section['file']}",
            "source_url": manifest.get("source_url"),
            "target": f"{manifest.get('source_url') or ''}#{section.get('html_anchor') or ''}",
            "raw": section,
        })
    for fact in facts_raw:
        evidence_id = stable_id(manifest["filing_id"], "fact", fact.get("fact_id"))
        entries.append({
            "evidence_id": evidence_id,
            "source_type": "sec_xbrl_fact",
            "section_id": "item_8",
            "xbrl_tag": fact.get("concept"),
            "context_ref": fact.get("context_ref"),
            "html_anchor": fact.get("html_anchor"),
            "local_path": "xbrl/facts_raw.json",
            "source_url": manifest.get("source_url"),
            "target": f"{manifest.get('source_url') or ''}#{fact.get('html_anchor') or ''}",
            "quote_text": fact.get("value_text"),
            "raw": fact,
        })
    for table in tables:
        evidence_id = stable_id(manifest["filing_id"], "table", table.get("table_id"))
        entries.append({
            "evidence_id": evidence_id,
            "source_type": "sec_html_table",
            "section_id": table.get("section_id"),
            "html_anchor": table.get("html_anchor"),
            "local_path": f"tables/table_{table['table_index']:04d}.json",
            "source_url": manifest.get("source_url"),
            "target": f"{manifest.get('source_url') or ''}#{table.get('html_anchor') or ''}",
            "raw": table,
        })
    return {"schema_version": "market_source_map_v1", "market": "US", "filing_id": manifest["filing_id"], "entries": entries}


def write_evidence_package(source_path: Path, output_root: Path, metadata_path: Path | None = None, force: bool = False) -> Path:
    meta = infer_metadata(source_path, metadata_path)
    soup = soup_from_html(source_path)
    contexts = extract_contexts(soup)
    units = extract_units(soup)
    facts_raw = extract_ixbrl_facts(soup, contexts, units)
    cik = _infer_cik(facts_raw, contexts, meta)
    accession = meta["accession_number"]
    if accession == "unknown" and facts_raw:
        accession = compact_accession(None, meta.get("source_url"))
    filing_id = f"US:{cik}:{accession}"
    manifest = {
        "schema_version": MARKET_EVIDENCE_SCHEMA_VERSION,
        "market": "US",
        "country": "US",
        "filing_id": filing_id,
        "company_id": f"US:{cik}",
        "ticker": meta["ticker"],
        "cik": cik,
        "company_name": meta["company_name"],
        "source_id": "sec",
        "source_tier": "official",
        "form": meta["form"],
        "report_type": "annual" if str(meta["form"]).upper() in {"10-K", "20-F"} else "quarterly",
        "document_format": "ixbrl_html" if facts_raw else "html",
        "accession_number": accession,
        "fiscal_year": meta["fiscal_year"],
        "fiscal_period": meta.get("fiscal_period") or "FY",
        "period_end": meta["period_end"],
        "filing_date": meta["filing_date"],
        "published_at": meta["filing_date"],
        "accepted_at": meta.get("accepted_at"),
        "source_url": meta["source_url"],
        "local_source_path": "raw/filing.htm",
        "accounting_standard": _accounting_standard(facts_raw),
        "industry_profile": _industry_profile(meta["ticker"], meta["company_name"]),
        "parser_version": PARSER_VERSION,
        "rules_version": RULES_VERSION,
        "artifacts": {
            "sections": "sections.json",
            "table_index": "tables/table_index.json",
            "xbrl_facts_raw": "xbrl/facts_raw.json",
            "xbrl_contexts": "xbrl/contexts.json",
            "xbrl_units": "xbrl/units.json",
            "xbrl_labels": "xbrl/labels.json",
            "xbrl_taxonomy_summary": "xbrl/taxonomy_summary.json",
            "financial_data": "metrics/financial_data.json",
            "financial_checks": "metrics/financial_checks.json",
            "normalized_metrics": "metrics/normalized_metrics.json",
            "operating_metrics": "metrics/operating_metrics.json",
            "quality_report": "qa/quality_report.json",
            "source_map": "qa/source_map.json",
            "extraction_warnings": "qa/extraction_warnings.json",
        },
    }
    report_id = us_report_id(manifest["fiscal_year"], manifest["form"], accession)
    company_wiki_id = company_wiki_dir_name(manifest["ticker"], manifest["company_name"])
    company_dir = output_root / "companies" / company_wiki_id
    package_dir = company_dir / "reports" / report_id
    manifest.update(
        {
            "report_id": report_id,
            "company_wiki_id": company_wiki_id,
            "company_wiki_path": repo_relative(company_dir),
            "wiki_report_path": repo_relative(package_dir),
        }
    )
    if package_dir.exists() and force:
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = package_dir / "raw"
    raw_dir.mkdir(exist_ok=True)
    shutil.copy2(source_path, raw_dir / "filing.htm")
    write_json(raw_dir / "filing.metadata.json", meta["metadata"])
    write_json(raw_dir / "sec_index.json", {"source_url": meta["source_url"], "primary_document": meta.get("primary_document")})

    sections, section_markdown = build_sections(soup, manifest)
    section_dir = package_dir / "sections"
    section_dir.mkdir(exist_ok=True)
    for stem, content in section_markdown.items():
        (section_dir / f"{stem}.md").write_text(content, encoding="utf-8")
    write_json(package_dir / "sections.json", {"schema_version": "sec_sections_v1", "sections": sections})

    table_index, parsed_tables = extract_tables(soup, sections)
    tables_dir = package_dir / "tables"
    tables_dir.mkdir(exist_ok=True)
    write_json(tables_dir / "table_index.json", {"schema_version": "sec_table_index_v1", "tables": [{k: v for k, v in table.items() if k != "rows"} for table in table_index]})
    for table in table_index:
        write_json(tables_dir / f"table_{table['table_index']:04d}.json", table)

    xbrl_dir = package_dir / "xbrl"
    write_json(xbrl_dir / "facts_raw.json", {"schema_version": "sec_xbrl_facts_raw_v1", "facts": facts_raw})
    write_json(xbrl_dir / "contexts.json", {"schema_version": "sec_xbrl_contexts_v1", "contexts": contexts})
    write_json(xbrl_dir / "units.json", {"schema_version": "sec_xbrl_units_v1", "units": units})
    write_json(xbrl_dir / "labels.json", {"schema_version": "sec_xbrl_labels_v1", "labels": _labels_from_facts(facts_raw)})
    write_json(xbrl_dir / "taxonomy_summary.json", _taxonomy_summary(facts_raw))

    metrics = normalize_metrics(manifest, facts_raw, parsed_tables)
    metrics_dir = package_dir / "metrics"
    write_json(metrics_dir / "normalized_metrics.json", {"schema_version": "sec_normalized_metrics_v1", "metrics": metrics["normalized_metrics"]})
    write_json(metrics_dir / "financial_data.json", metrics["financial_data"])
    write_json(metrics_dir / "financial_checks.json", metrics["financial_checks"])
    write_json(metrics_dir / "operating_metrics.json", {"schema_version": "sec_operating_metrics_v1", "metrics": []})

    source_map = build_source_map(manifest, sections, facts_raw, table_index)
    quality = build_quality_report(
        manifest=manifest,
        financial_data=metrics["financial_data"],
        financial_checks=metrics["financial_checks"],
        section_count=len(sections),
        table_count=len(table_index),
        raw_fact_count=len(facts_raw),
        source_map=source_map,
        parser_warnings=_quality_warnings(manifest, sections, facts_raw, metrics["normalized_metrics"]),
        rule_warnings=metrics["warnings"],
    )
    quality["summary"] = {
        "section_count": len(sections),
        "table_count": len(table_index),
        "xbrl_fact_count": len(facts_raw),
        "normalized_metric_count": len(metrics["normalized_metrics"]),
    }
    quality["warnings"] = quality["parser_warnings"] + quality["rule_warnings"]
    qa_dir = package_dir / "qa"
    write_json(qa_dir / "quality_report.json", quality)
    write_json(qa_dir / "extraction_warnings.json", {"warnings": quality["warnings"]})
    write_json(qa_dir / "source_map.json", source_map)
    manifest["quality_status"] = quality["overall_status"]
    manifest["artifact_hashes"] = compute_artifact_hashes(package_dir)
    manifest["parse_run_id"] = stable_parse_run_id(manifest, manifest["artifact_hashes"])
    write_json(package_dir / "manifest.json", manifest)
    (package_dir / "README.md").write_text(_readme(manifest, quality), encoding="utf-8")
    _write_company_wiki_indexes(output_root, company_dir, manifest, quality)
    return package_dir


def _read_existing_json(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    return payload if isinstance(payload, dict) else {}


def _now_iso() -> str:
    return date.today().isoformat()


def _write_company_wiki_indexes(output_root: Path, company_dir: Path, manifest: dict[str, Any], quality: dict[str, Any]) -> None:
    for dirname in ("reports", "metrics", "evidence", "semantic", "graph", "analysis", "factcheck", "tracking"):
        (company_dir / dirname).mkdir(parents=True, exist_ok=True)

    existing = _read_existing_json(company_dir / "company.json")
    report_id = str(manifest.get("report_id") or manifest.get("filing_id") or "unknown")
    report_rel = f"reports/{report_id}"
    report_entry = {
        "report_id": report_id,
        "filing_id": manifest.get("filing_id"),
        "market": "US",
        "form": manifest.get("form"),
        "report_type": manifest.get("report_type"),
        "fiscal_year": manifest.get("fiscal_year"),
        "fiscal_period": manifest.get("fiscal_period"),
        "period_end": manifest.get("period_end"),
        "published_at": manifest.get("published_at") or manifest.get("filing_date"),
        "accession_number": manifest.get("accession_number"),
        "source_url": manifest.get("source_url"),
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
        "schema_version": "us_company_wiki_v1",
        "market": "US",
        "company_id": manifest.get("company_id"),
        "company_wiki_id": manifest.get("company_wiki_id"),
        "company_wiki_path": manifest.get("company_wiki_path"),
        "ticker": manifest.get("ticker"),
        "cik": manifest.get("cik"),
        "exchange": manifest.get("exchange") or "SEC",
        "company_name": manifest.get("company_name"),
        "industry_profile": manifest.get("industry_profile"),
        "accounting_standard": manifest.get("accounting_standard"),
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
        "updated_at": _now_iso(),
    }
    write_json(company_dir / "company.json", company_json)
    write_json(
        company_dir / "_index.json",
        {
            "schema_version": "us_company_index_v1",
            "market": "US",
            "company_id": company_json["company_id"],
            "company_wiki_id": company_json.get("company_wiki_id"),
            "company_wiki_path": company_json.get("company_wiki_path"),
            "primary_report_id": primary_report_id,
            "reports": reports,
            "updated_at": company_json["updated_at"],
        },
    )
    (company_dir / "company.md").write_text(_company_markdown(company_json, latest_report), encoding="utf-8")
    _write_root_catalog(output_root)


def _write_root_catalog(output_root: Path) -> None:
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
                "market": "US",
                "ticker": company.get("ticker"),
                "cik": company.get("cik"),
                "company_name": company.get("company_name"),
                "primary_report_id": company.get("primary_report_id"),
                "report_count": company.get("report_count") or len(company.get("reports") or []),
                "status": "ready",
            }
        )
    companies.sort(key=lambda item: str(item.get("ticker") or item.get("company_id") or ""))
    write_json(
        output_root / "_meta" / "company_catalog.json",
        {
            "schema_version": "us_company_catalog_v1",
            "market": "US",
            "company_count": len(companies),
            "companies": companies,
            "generated_at": _now_iso(),
        },
    )
    guide = output_root / "_meta" / "AGENT_GUIDE.md"
    if not guide.exists():
        guide.write_text(
            "# US Wiki Agent Guide\n\n"
            "US company Wiki uses `companies/<ticker>-<company>/company.json` as the company entry, "
            "then `reports/<report_id>/` for each SEC filing package. Prefer `company.json`, "
            "`reports/<report_id>/manifest.json`, `metrics/financial_data.json`, "
            "`metrics/financial_checks.json`, and `qa/source_map.json` before PostgreSQL fallback.\n",
            encoding="utf-8",
        )


def _company_markdown(company: dict[str, Any], latest: dict[str, Any]) -> str:
    return (
        f"# {company.get('ticker')} {company.get('company_name') or ''}\n\n"
        f"- Market: US\n"
        f"- CIK: `{company.get('cik') or ''}`\n"
        f"- Latest filing: `{latest.get('form') or ''}` `{latest.get('accession_number') or ''}`\n"
        f"- Latest period end: `{latest.get('period_end') or ''}`\n"
        f"- Quality: `{latest.get('quality_status') or ''}`\n"
    )


def _child_text(tag: Any, local_name: str) -> str | None:
    child = tag.find(lambda item: item.name and item.name.endswith(local_name))
    return clean_text(child.get_text(" ", strip=True)) if child else None


def _duration_days(start: str | None, end: str | None) -> int | None:
    a = parse_date(start)
    b = parse_date(end)
    if a and b:
        return (b - a).days + 1
    return None


def _normalize_unit(unit_id: str, measures: list[str]) -> str:
    text = " ".join([unit_id, *measures]).lower()
    if "usd" in text and "share" in text:
        return "USD/shares"
    if "usd" in text:
        return "USD"
    if "share" in text:
        return "shares"
    if "pure" in text or "number" in text:
        return "number"
    return unit_id


def _label_from_concept(concept: str) -> str:
    label = concept.split(":", 1)[-1]
    return clean_text(re.sub(r"(?<!^)([A-Z])", r" \1", label))


def _pretty_section_title(title: str, section_id: str) -> str:
    text = clean_text(title)
    if not text:
        return section_id
    return text[:140]


def _section_markdown(manifest: dict[str, Any], section_id: str, section_title: str, text: str) -> str:
    frontmatter = {
        "schema_version": "sec_section_v1",
        "market": "US",
        "ticker": manifest.get("ticker"),
        "accession_number": manifest.get("accession_number"),
        "form": manifest.get("form"),
        "section_id": section_id,
        "section_title": section_title,
        "source_url": manifest.get("source_url"),
        "html_anchor": section_id,
    }
    return "---\n" + "\n".join(f"{k}: {v}" for k, v in frontmatter.items()) + f"\n---\n\n# {section_title}\n\n{text}\n"


def _extract_notes(text: str) -> str:
    match = re.search(r"notes\s+to\s+consolidated\s+financial\s+statements", text, flags=re.IGNORECASE)
    if not match:
        return ""
    return text[match.start() : min(len(text), match.start() + 120000)]


def _choose_section_match(
    body_text: str,
    candidates: list[re.Match[str]],
    next_candidates: list[re.Match[str]],
    cursor: int,
) -> re.Match[str] | None:
    for match in candidates:
        if match.start() < cursor:
            continue
        snippet = body_text[match.start() : match.start() + 260].lower()
        if any(token in snippet for token in ("for a discussion", "see item", "refer to item", "additional information about")):
            continue
        next_after = next((candidate.start() for candidate in next_candidates if candidate.start() > match.start()), None)
        if next_after is not None and next_after - match.start() < 300:
            continue
        return match
    return next((match for match in candidates if match.start() >= cursor), None)


def _nearest_title(table: Any) -> str | None:
    node = table
    for _ in range(5):
        node = node.find_previous(["div", "p", "span"])
        if not node:
            break
        text = clean_text(node.get_text(" ", strip=True))
        if 4 <= len(text) <= 180:
            return text
    return None


def _guess_table_section(title: str | None, sections: list[dict[str, Any]]) -> str | None:
    text = (title or "").lower()
    for keyword, section_id in (("risk", "item_1a"), ("cash flow", "item_8"), ("balance", "item_8"), ("operations", "item_7")):
        if keyword in text:
            return section_id
    return sections[-1]["section_id"] if sections else None


def _is_financial_table(title: str | None, rows: list[list[str]]) -> bool:
    haystack = " ".join([title or "", *[" ".join(row[:4]) for row in rows[:8]]]).lower()
    return any(token in haystack for token in ("balance sheet", "statements of operations", "cash flows", "net sales", "assets", "liabilities"))


def _infer_cik(facts_raw: list[dict[str, Any]], contexts: dict[str, Any], meta: dict[str, Any]) -> str:
    for fact in facts_raw:
        if fact.get("concept") == "dei:EntityCentralIndexKey" and fact.get("value_text"):
            return str(fact["value_text"]).zfill(10)
    for context in contexts.values():
        identifier = context.get("entity_identifier")
        if identifier and str(identifier).isdigit():
            return str(identifier).zfill(10)
    source_url = meta.get("source_url") or ""
    match = re.search(r"/data/(\d+)/", source_url)
    return match.group(1).zfill(10) if match else "UNKNOWN"


def _accounting_standard(facts_raw: list[dict[str, Any]]) -> str:
    has_ifrs = any(str(f.get("concept") or "").lower().startswith("ifrs-full:") for f in facts_raw)
    return "IFRS" if has_ifrs else "US_GAAP"


def _industry_profile(ticker: str, company_name: str) -> str:
    text = f"{ticker} {company_name}".lower()
    if "apple" in text or ticker.upper() in {"AAPL"}:
        return "consumer_hardware"
    return "general"


def _labels_from_facts(facts_raw: list[dict[str, Any]]) -> dict[str, str]:
    return {str(fact["concept"]): str(fact.get("label") or _label_from_concept(str(fact["concept"]))) for fact in facts_raw if fact.get("concept")}


def _taxonomy_summary(facts_raw: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    extension = 0
    for fact in facts_raw:
        taxonomy = fact.get("taxonomy") or "unknown"
        counts[taxonomy] = counts.get(taxonomy, 0) + 1
        extension += 1 if fact.get("is_extension") else 0
    return {"schema_version": "sec_taxonomy_summary_v1", "taxonomy_counts": counts, "extension_fact_count": extension, "fact_count": len(facts_raw)}


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalized_row(manifest: dict[str, Any], parse_run_id: str | None, fact: Any, raw_fact: dict[str, Any] | None) -> dict[str, Any]:
    metric_id = stable_id(parse_run_id or manifest["filing_id"], fact.canonical_name, fact.period_key, fact.local_name, raw_fact.get("fact_id") if raw_fact else None)
    return {
        "metric_id": metric_id,
        "filing_id": manifest["filing_id"],
        "parse_run_id": parse_run_id,
        "ticker": manifest["ticker"],
        "statement_type": fact.statement_type.value,
        "canonical_name": fact.canonical_name,
        "concept": fact.local_name,
        "label": fact.label,
        "value": str(fact.value),
        "unit": fact.unit,
        "currency": fact.currency,
        "period_key": fact.period_key,
        "period_start": fact.period_start.isoformat() if fact.period_start else None,
        "period_end": fact.period_end.isoformat() if fact.period_end else None,
        "duration_days": fact.duration_days,
        "qtd_ytd_type": fact.qtd_ytd_type,
        "fiscal_year": fact.fiscal_year,
        "fiscal_period": fact.fiscal_period,
        "segment_key": stable_id(raw_fact.get("dimensions")) if raw_fact and raw_fact.get("dimensions") else "consolidated",
        "dimensions": raw_fact.get("dimensions") if raw_fact else {},
        "confidence": str(fact.confidence),
        "evidence_id": stable_id(manifest["filing_id"], "fact", raw_fact.get("fact_id")) if raw_fact else None,
        "raw_fact_id": raw_fact.get("fact_id") if raw_fact else None,
        "raw": fact.raw,
    }


def _fact_context_ref(fact: Any) -> str | None:
    if isinstance(fact.raw, dict):
        return fact.raw.get("context_ref") or fact.raw.get("context_id") or (fact.raw.get("raw") or {}).get("context_ref")
    return None


def _quality_warnings(manifest: dict[str, Any], sections: list[dict[str, Any]], facts_raw: list[dict[str, Any]], metrics: list[dict[str, Any]]) -> list[str]:
    warnings = []
    if not sections:
        warnings.append("No SEC item sections were detected.")
    if not facts_raw:
        warnings.append("No inline XBRL facts were detected.")
    present = {item["canonical_name"] for item in metrics}
    for required in ("total_assets", "operating_revenue", "net_profit", "operating_cash_flow_net"):
        if required not in present and str(manifest.get("form")).upper() in {"10-K", "20-F"}:
            warnings.append(f"Required annual metric missing: {required}")
    return warnings


def _artifact_hashes(package_dir: Path) -> dict[str, str]:
    hashes = {}
    for path in sorted(package_dir.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            hashes[str(path.relative_to(package_dir))] = sha256_bytes(path.read_bytes())
    return hashes


def _readme(manifest: dict[str, Any], quality: dict[str, Any]) -> str:
    return (
        f"# {manifest.get('ticker')} {manifest.get('fiscal_year')} {manifest.get('form')}\n\n"
        f"- Filing ID: `{manifest.get('filing_id')}`\n"
        f"- Accession: `{manifest.get('accession_number')}`\n"
        f"- Period end: `{manifest.get('period_end')}`\n"
        f"- Quality: `{quality.get('overall_status')}`\n"
        f"- Source: {manifest.get('source_url') or 'local file'}\n"
    )
