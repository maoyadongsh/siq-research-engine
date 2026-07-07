from __future__ import annotations

import json
import os
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES_SRC = REPO_ROOT / "services" / "market-report-rules" / "src"
HK_SCRIPTS = REPO_ROOT / "scripts" / "hk"
for path in (RULES_SRC, HK_SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from hk_evidence_lib import parsed_tables_from_document_full
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
from market_report_rules_service.models import AccountingStandard, Market, ParsedArtifact, ParsedFact
from market_report_rules_service.normalization import parse_date
from market_report_rules_service.pipeline import build_package_aware_load_plan, process_artifact


PARSER_VERSION = os.environ.get("SIQ_JP_PARSER_VERSION", "jp_edinet_evidence_parser_v1")
RULES_VERSION = os.environ.get("SIQ_JP_RULES_VERSION", "jp_edinet_rules_v1")
_JP_BANK_TICKERS = {"8306", "8316", "8411", "8308", "7182"}
_JP_INSURANCE_TICKERS = {"8725", "8750", "8766", "8795"}
_JP_TELECOM_TICKERS = {"9432", "9433", "9434", "9613", "9984"}
_JP_SEMICONDUCTOR_TICKERS = {"8035", "6857", "6723", "6920", "6146", "7735"}


@dataclass(frozen=True)
class CompanyWikiReportPaths:
    company_id: str
    report_id: str
    company_dir: Path
    report_dir: Path
    company_wiki_path: str
    wiki_report_path: str


def read_json(path: Path, default: Any = None) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else ({} if default is None else default)


def safe_wiki_slug(value: Any, fallback: str = "unknown") -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "-", text)
    text = re.sub(r"[,&]+", "", text)
    text = re.sub(r"[()\[\]{}]+", "", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip(" ._-") or fallback


def _repo_or_wiki_relative(path: Path, output_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        pass
    try:
        rel = path.resolve().relative_to(output_root.resolve())
        if output_root.name == "wiki":
            return str(Path("data") / "wiki" / rel)
    except ValueError:
        pass
    return str(path)


def company_wiki_report_paths(output_root: Path, metadata: dict[str, Any]) -> CompanyWikiReportPaths:
    market_root = output_root / "jp" if output_root.name == "wiki" else output_root
    ticker = safe_wiki_slug(metadata.get("security_code") or metadata.get("ticker"), "UNKNOWN")
    company_name = safe_wiki_slug(metadata.get("company_name") or metadata.get("company_name_en") or metadata.get("company_name_ja"), "unknown")
    company_id = f"{ticker}-{company_name}"
    fiscal_year = metadata.get("fiscal_year") or "unknown"
    report_type = safe_wiki_slug(metadata.get("report_type") or _report_type(metadata.get("form")), "report").replace("_", "-")
    doc_id = safe_wiki_slug(metadata.get("doc_id") or metadata.get("filing_id") or metadata.get("document_id"), "unknown")
    report_id = f"{fiscal_year}-{report_type}-{doc_id}"
    company_dir = market_root / "companies" / company_id
    report_dir = company_dir / "reports" / report_id
    return CompanyWikiReportPaths(
        company_id=company_id,
        report_id=report_id,
        company_dir=company_dir,
        report_dir=report_dir,
        company_wiki_path=_repo_or_wiki_relative(company_dir, output_root),
        wiki_report_path=_repo_or_wiki_relative(report_dir, output_root),
    )


def infer_metadata(source_path: Path, metadata_path: Path | None = None) -> dict[str, Any]:
    metadata = read_json(metadata_path or source_path.with_suffix(source_path.suffix + ".metadata.json"), {})
    candidate = metadata.get("candidate") if isinstance(metadata, dict) else {}
    if not isinstance(candidate, dict):
        candidate = {}
    stem_parts = source_path.stem.split("_")
    ticker = candidate.get("ticker") or candidate.get("security_code") or candidate.get("company_id") or (stem_parts[2] if len(stem_parts) > 2 else "UNKNOWN")
    doc_id = candidate.get("doc_id") or candidate.get("accession_number") or candidate.get("document_id") or source_path.stem.rsplit("_", 1)[-1]
    edinet_code = candidate.get("edinet_code") or candidate.get("company_id") or ""
    period_end = candidate.get("report_end") or candidate.get("period_end") or _filename_date(source_path.name)
    fiscal_year = _int_or_none(str(period_end or "")[:4]) or _int_or_none(candidate.get("year"))
    report_type = _report_type(candidate.get("report_type") or candidate.get("form") or source_path.parent.name)
    company_name = candidate.get("company_name") or (stem_parts[0] if stem_parts else source_path.stem)
    return {
        "raw_metadata": metadata,
        "doc_id": doc_id,
        "edinet_code": edinet_code,
        "security_code": str(ticker),
        "company_id": f"JP:{edinet_code or ticker}",
        "ticker": str(ticker),
        "company_name": company_name,
        "company_name_en": candidate.get("company_name_en"),
        "company_name_ja": candidate.get("company_name_ja"),
        "source_id": candidate.get("source_id") or "edinet",
        "form": candidate.get("form") or candidate.get("title") or "有価証券報告書",
        "report_type": report_type,
        "fiscal_year": fiscal_year,
        "fiscal_period": _fiscal_period(report_type),
        "period_end": period_end,
        "published_at": candidate.get("published_at") or candidate.get("filing_date"),
        "source_url": candidate.get("document_url") or candidate.get("source_url") or candidate.get("landing_url"),
        "accounting_standard": _accounting_standard(metadata),
        "industry_profile": candidate.get("industry_profile") or _infer_industry_profile(str(ticker), company_name, candidate),
    }


def _infer_industry_profile(ticker: str, company_name: Any, candidate: dict[str, Any]) -> str:
    code = re.sub(r"\D", "", str(ticker or ""))[:4]
    raw_name = " ".join(str(value or "") for value in (company_name, candidate.get("company_name_en"), candidate.get("company_name_ja")))
    name = raw_name.upper()
    if code in _JP_BANK_TICKERS or "BANK" in name or "銀行" in raw_name:
        return "bank"
    if code in _JP_INSURANCE_TICKERS or "INSURANCE" in name or "保険" in raw_name:
        return "insurance"
    if code in _JP_TELECOM_TICKERS or any(token in name for token in ("NTT", "KDDI", "SOFTBANK", "TELECOM")):
        return "telecom"
    if code in _JP_SEMICONDUCTOR_TICKERS or any(token in name for token in ("SEMICONDUCTOR", "ELECTRON", "ADVANTEST", "RENESAS")):
        return "semiconductor"
    if any(token in name for token in ("TOYOTA", "HONDA", "NISSAN", "SUBARU", "MOTOR", "ELECTRIC", "MITSUBISHI", "HITACHI", "PANASONIC", "CANON", "SONY")):
        return "manufacturing"
    if any(token in name for token in ("PHARMA", "TAKEDA", "ASTELLAS", "DAIICHI", "CHUGAI")):
        return "pharma"
    if any(token in name for token in ("RETAIL", "FAST RETAILING", "SEVEN", "AEON")):
        return "retail"
    return "general"


def extract_edinet_facts(source_path: Path) -> tuple[list[ParsedFact], list[dict[str, Any]]]:
    xml_payloads = _xml_payloads(source_path)
    facts: list[ParsedFact] = []
    raw_rows: list[dict[str, Any]] = []
    for rel, data in xml_payloads:
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            continue
        contexts = _contexts(root)
        units = _units(root)
        for elem in root.iter():
            tag = _qname(elem.tag)
            text = (elem.text or "").strip()
            value = _decimal(text)
            if not tag or value is None:
                continue
            context_ref = elem.attrib.get("contextRef") or elem.attrib.get("contextref")
            unit_ref = elem.attrib.get("unitRef") or elem.attrib.get("unitref")
            context = contexts.get(context_ref or "", {})
            unit = units.get(unit_ref or "") or unit_ref
            raw = {
                "fact_id": stable_id(rel, tag, context_ref, unit_ref, text),
                "source_file": rel,
                "concept": tag,
                "value_text": text,
                "context_ref": context_ref,
                "unit_ref": unit_ref,
                "unit": unit,
                "period_start": context.get("period_start"),
                "period_end": context.get("period_end"),
                "instant": context.get("instant"),
                "duration_days": context.get("duration_days"),
                "dimensions": context.get("dimensions") or {},
                "source_type": "edinet_xbrl_fact",
            }
            raw_rows.append(raw)
            facts.append(
                ParsedFact(
                    concept=tag,
                    value=value,
                    unit=unit,
                    period_start=parse_date(context.get("period_start")),
                    period_end=parse_date(context.get("period_end") or context.get("instant")),
                    duration_days=context.get("duration_days"),
                    context_id=context_ref,
                    label=tag.rsplit(":", 1)[-1],
                    raw=raw,
                )
            )
    return facts, raw_rows


def build_jp_artifact(source_path: Path, metadata_path: Path | None = None, parser_result_dir: Path | None = None) -> tuple[ParsedArtifact, dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    metadata = infer_metadata(source_path, metadata_path)
    facts, raw_facts = extract_edinet_facts(source_path)
    document_full = read_json(parser_result_dir / "document_full.json", {}) if parser_result_dir else {}
    artifact = ParsedArtifact(
        artifact_id=f"JP:{metadata['doc_id']}",
        market=Market.JP,
        company_id=metadata["company_id"],
        ticker=metadata["ticker"],
        company_name=metadata["company_name"],
        report_id=f"JP:{metadata['doc_id']}",
        report_type=metadata["report_type"],
        report_form=metadata["form"],
        fiscal_year=metadata["fiscal_year"],
        fiscal_period=metadata["fiscal_period"],
        period_end=parse_date(metadata["period_end"]),
        accounting_standard=AccountingStandard(metadata["accounting_standard"]),
        industry_profile=metadata.get("industry_profile") or "general",
        currency="JPY",
        unit="JPY",
        source_url=metadata["source_url"],
        source_files={"source": str(source_path), "parser_result": str(parser_result_dir) if parser_result_dir else None},
        facts=facts,
        tables=parsed_tables_from_document_full(document_full) if document_full else [],
        document_full={"edinet_facts_raw": raw_facts, **document_full},
        metadata=metadata,
    )
    return artifact, metadata, document_full, raw_facts


def write_jp_evidence_package(
    source_path: Path,
    output_root: Path,
    metadata_path: Path | None = None,
    parser_result_dir: Path | None = None,
    *,
    force: bool = False,
) -> Path:
    artifact, metadata, document_full, raw_facts = build_jp_artifact(source_path, metadata_path, parser_result_dir)
    paths = company_wiki_report_paths(output_root, metadata)
    result = process_artifact(artifact, include_load_plan=True)
    financial_data = financial_data_contract(result.extraction)
    financial_checks = financial_checks_contract(result.validation)

    package_dir = paths.report_dir
    if package_dir.exists() and force:
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)
    for name in ("raw", "sections", "tables", "xbrl", "metrics", "evidence", "parser", "qa", "images"):
        (package_dir / name).mkdir(exist_ok=True)
    shutil.copy2(source_path, package_dir / "raw" / source_path.name)
    if metadata_path and metadata_path.exists():
        shutil.copy2(metadata_path, package_dir / "raw" / "report.metadata.json")
    else:
        write_json(package_dir / "raw" / "report.metadata.json", metadata.get("raw_metadata") or {})
    (package_dir / "sections" / "report.md").write_text(_markdown(document_full, metadata), encoding="utf-8")
    _copy_parser_artifacts(package_dir, parser_result_dir)
    table_index = _write_tables(package_dir, artifact.tables)
    write_json(package_dir / "xbrl" / "facts_raw.json", {"schema_version": "edinet_xbrl_facts_raw_v1", "facts": raw_facts})

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "market": "JP",
        "filing_id": artifact.report_id,
        "report_id": paths.report_id,
        "company_id": artifact.company_id,
        "company_wiki_id": paths.company_id,
        "company_wiki_path": paths.company_wiki_path,
        "wiki_report_path": paths.wiki_report_path,
        "ticker": artifact.ticker,
        "company_name": artifact.company_name,
        "company_name_en": metadata.get("company_name_en"),
        "company_name_ja": metadata.get("company_name_ja"),
        "source_id": metadata["source_id"],
        "form": metadata["form"],
        "report_type": metadata["report_type"],
        "fiscal_year": artifact.fiscal_year,
        "fiscal_period": artifact.fiscal_period,
        "period_end": metadata["period_end"],
        "published_at": metadata["published_at"],
        "source_url": metadata["source_url"],
        "local_source_path": f"raw/{source_path.name}",
        "accounting_standard": artifact.accounting_standard.value,
        "industry_profile": artifact.industry_profile or metadata.get("industry_profile") or "general",
        "parser_version": PARSER_VERSION,
        "rules_version": RULES_VERSION,
        "quality_status": financial_checks.get("overall_status") or "warning",
        "artifact_hashes": {},
        "doc_id": metadata["doc_id"],
        "edinet_code": metadata["edinet_code"],
        "security_code": metadata["security_code"],
    }
    manifest["parse_run_id"] = result.load_plan.parse_run_id if result.load_plan else stable_parse_run_id(manifest, {})
    source_map = source_map_from_financial_data(manifest=manifest, financial_data=financial_data, package_dir=package_dir)
    normalized_metrics = normalized_metrics_from_financial_data(manifest=manifest, financial_data=financial_data, source_map=source_map)
    quality = build_quality_report(
        manifest=manifest,
        financial_data=financial_data,
        financial_checks=financial_checks,
        section_count=1,
        table_count=len(table_index),
        raw_fact_count=len(raw_facts),
        source_map=source_map,
        parser_warnings=[] if raw_facts else ["No EDINET XBRL facts were extracted."],
        rule_warnings=list(result.extraction.warnings) + list(result.validation.warnings),
    )
    manifest["quality_status"] = quality["overall_status"]
    _write_metrics(package_dir, financial_data, financial_checks, result.load_plan.model_dump(mode="json") if result.load_plan else {}, normalized_metrics, quality, source_map)
    manifest["artifact_hashes"] = compute_artifact_hashes(package_dir)
    write_json(package_dir / "manifest.json", manifest)
    _validation_with_package_gates, load_plan = build_package_aware_load_plan(
        result.extraction,
        result.validation,
        package_dir=package_dir,
    )
    write_json(package_dir / "metrics" / "load_plan.json", load_plan.model_dump(mode="json"))
    (package_dir / "README.md").write_text(_readme(manifest, quality), encoding="utf-8")
    _write_company_index(paths, manifest)
    validation = validate_evidence_package(package_dir)
    if not validation.ok:
        write_json(package_dir / "qa" / "contract_validation.json", validation.as_dict())
    return package_dir


def _copy_parser_artifacts(package_dir: Path, parser_result_dir: Path | None) -> None:
    if not parser_result_dir or not parser_result_dir.exists():
        return
    for name in (
        "document_full.json",
        "content_list_enhanced.json",
        "quality_report.json",
        "financial_data.json",
        "financial_checks.json",
        "table_relations.json",
    ):
        source = parser_result_dir / name
        if source.exists():
            shutil.copy2(source, package_dir / "parser" / name)


def _write_company_index(paths: CompanyWikiReportPaths, manifest: dict[str, Any]) -> None:
    company_path = paths.company_dir / "company.json"
    existing = read_json(company_path, {})
    reports = existing.get("reports") if isinstance(existing.get("reports"), list) else []
    report_entry = {
        "report_id": paths.report_id,
        "filing_id": manifest.get("filing_id"),
        "report_type": manifest.get("report_type"),
        "fiscal_year": manifest.get("fiscal_year"),
        "period_end": manifest.get("period_end"),
        "published_at": manifest.get("published_at"),
        "wiki_report_path": paths.wiki_report_path,
        "quality_status": manifest.get("quality_status"),
    }
    reports = [row for row in reports if not isinstance(row, dict) or row.get("report_id") != paths.report_id]
    reports.append(report_entry)
    payload = {
        "schema_version": "jp_company_wiki_v1",
        "market": "JP",
        "company_id": manifest.get("company_id"),
        "company_wiki_id": paths.company_id,
        "ticker": manifest.get("ticker"),
        "security_code": manifest.get("security_code"),
        "edinet_code": manifest.get("edinet_code"),
        "company_name": manifest.get("company_name"),
        "company_name_en": manifest.get("company_name_en"),
        "company_name_ja": manifest.get("company_name_ja"),
        "company_wiki_path": paths.company_wiki_path,
        "currency": "JPY",
        "reports": sorted(reports, key=lambda row: str(row.get("report_id") if isinstance(row, dict) else "")),
    }
    write_json(company_path, payload)
    (paths.company_dir / "README.md").write_text(
        f"# {manifest.get('ticker')} {manifest.get('company_name')}\n\n"
        f"- Market: `JP`\n"
        f"- EDINET: `{manifest.get('edinet_code') or ''}`\n"
        f"- Wiki path: `{paths.company_wiki_path}`\n",
        encoding="utf-8",
    )


def _xml_payloads(path: Path) -> list[tuple[str, bytes]]:
    if path.suffix.lower() == ".zip":
        rows = []
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if name.lower().endswith((".xbrl", ".xml")):
                    rows.append((name, zf.read(name)))
        return rows
    if path.suffix.lower() in {".xbrl", ".xml"}:
        return [(path.name, path.read_bytes())]
    return []


def _qname(tag: str) -> str:
    if tag.startswith("{"):
        namespace, local = tag[1:].split("}", 1)
        prefix = namespace.rstrip("/").rsplit("/", 1)[-1]
        if "ifrs" in namespace.lower():
            prefix = "ifrs-full"
        return f"{prefix}:{local}"
    return tag


def _contexts(root: ET.Element) -> dict[str, dict[str, Any]]:
    contexts: dict[str, dict[str, Any]] = {}
    for elem in root.iter():
        if not _local(elem.tag).lower().endswith("context"):
            continue
        cid = elem.attrib.get("id")
        if not cid:
            continue
        start = _child_text(elem, "startDate")
        end = _child_text(elem, "endDate")
        instant = _child_text(elem, "instant")
        dimensions = {}
        for child in elem.iter():
            if _local(child.tag).lower().endswith("explicitmember") and child.attrib.get("dimension"):
                dimensions[child.attrib["dimension"]] = (child.text or "").strip()
        contexts[cid] = {
            "period_start": start,
            "period_end": end or instant,
            "instant": instant,
            "duration_days": _duration_days(start, end),
            "dimensions": dimensions,
        }
    return contexts


def _units(root: ET.Element) -> dict[str, str]:
    units: dict[str, str] = {}
    for elem in root.iter():
        if not _local(elem.tag).lower().endswith("unit"):
            continue
        uid = elem.attrib.get("id")
        if not uid:
            continue
        measures = [(child.text or "").strip() for child in elem.iter() if _local(child.tag).lower().endswith("measure")]
        units[uid] = "JPY" if any("jpy" in measure.lower() for measure in measures + [uid]) else (measures[0] if measures else uid)
    return units


def _child_text(elem: ET.Element, local_name: str) -> str | None:
    for child in elem.iter():
        if _local(child.tag).lower() == local_name.lower():
            return (child.text or "").strip()
    return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag.rsplit(":", 1)[-1]


def _decimal(value: Any) -> Decimal | None:
    text = str(value or "").strip().replace(",", "")
    if not text or not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _duration_days(start: str | None, end: str | None) -> int | None:
    a = parse_date(start)
    b = parse_date(end)
    if isinstance(a, date) and isinstance(b, date):
        return (b - a).days + 1
    return None


def _write_tables(package_dir: Path, tables: list[Any]) -> list[dict[str, Any]]:
    rows = []
    for table in tables:
        idx = table.table_index or len(rows) + 1
        item = {
            "table_id": table.table_id,
            "table_index": idx,
            "title": table.title,
            "page_number": table.page_number,
            "row_count": len(table.rows),
            "column_count": max((len(row) for row in table.rows), default=0),
            "table_json_path": f"tables/table_{int(idx):04d}.json",
            "raw": table.raw,
        }
        rows.append(item)
        write_json(package_dir / item["table_json_path"], {**item, "rows": table.rows})
    write_json(package_dir / "tables" / "table_index.json", {"schema_version": "jp_table_index_v1", "tables": rows})
    return rows


def _write_metrics(package_dir: Path, financial_data: dict[str, Any], financial_checks: dict[str, Any], load_plan: dict[str, Any], normalized_metrics: list[dict[str, Any]], quality: dict[str, Any], source_map: dict[str, Any]) -> None:
    write_json(package_dir / "metrics" / "financial_data.json", financial_data)
    write_json(package_dir / "metrics" / "financial_checks.json", financial_checks)
    write_json(package_dir / "metrics" / "load_plan.json", load_plan)
    write_json(package_dir / "metrics" / "normalized_metrics.json", {"schema_version": "market_normalized_metrics_v1", "metrics": normalized_metrics})
    write_json(package_dir / "metrics" / "operating_metrics.json", {"schema_version": "market_operating_metrics_v1", "metrics": [row for row in normalized_metrics if row.get("statement_type") == "operating_metrics"]})
    write_json(package_dir / "qa" / "quality_report.json", quality)
    write_json(package_dir / "qa" / "source_map.json", source_map)
    write_json(package_dir / "qa" / "extraction_warnings.json", {"warnings": quality.get("parser_warnings", []) + quality.get("rule_warnings", [])})


def _markdown(document_full: dict[str, Any], metadata: dict[str, Any]) -> str:
    markdown = document_full.get("markdown") if isinstance(document_full.get("markdown"), dict) else {}
    if markdown.get("content"):
        return str(markdown["content"])
    return f"# {metadata.get('company_name')} {metadata.get('fiscal_year')} {metadata.get('form')}\n"


def _report_type(value: Any) -> str:
    text = str(value or "").lower()
    normalized = text.replace("_", " ").replace("-", " ")
    if "annual securities" in normalized or "有価証券報告書" in text:
        return "annual_securities_report"
    if "integrated" in normalized and "report" in normalized:
        return "integrated_report"
    if any(token in text for token in ("semi", "half", "半期", "中間")):
        return "semiannual"
    if any(token in text for token in ("quarter", "四半期", "q1", "q2", "q3")):
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
    if "jgaap" in text or "j-gaap" in text or "日本基準" in text:
        return "JGAAP"
    if "ifrs" in text:
        return "IFRS"
    return "IFRS"


def _readme(manifest: dict[str, Any], quality: dict[str, Any]) -> str:
    return (
        f"# {manifest.get('ticker')} {manifest.get('fiscal_year')} {manifest.get('form')}\n\n"
        f"- Market: `{manifest.get('market')}`\n"
        f"- Filing ID: `{manifest.get('filing_id')}`\n"
        f"- Document ID: `{manifest.get('doc_id')}`\n"
        f"- Quality: `{quality.get('overall_status')}`\n"
    )
