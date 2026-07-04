from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "market_evidence_package_v1"

REQUIRED_MANIFEST_FIELDS = (
    "schema_version",
    "market",
    "filing_id",
    "company_id",
    "ticker",
    "company_name",
    "source_id",
    "form",
    "report_type",
    "fiscal_year",
    "fiscal_period",
    "period_end",
    "published_at",
    "source_url",
    "local_source_path",
    "accounting_standard",
    "parser_version",
    "rules_version",
    "quality_status",
    "artifact_hashes",
)

REQUIRED_DIRECTORIES = ("raw", "sections", "tables", "xbrl", "metrics", "qa")
REQUIRED_FILES = (
    "manifest.json",
    "README.md",
    "metrics/financial_data.json",
    "metrics/financial_checks.json",
    "qa/quality_report.json",
    "qa/source_map.json",
)

PACKAGE_FILE_PATHS = {
    "manifest": "manifest.json",
    "quality_report": "qa/quality_report.json",
    "source_map": "qa/source_map.json",
    "financial_data": "metrics/financial_data.json",
    "financial_checks": "metrics/financial_checks.json",
    "normalized_metrics": "metrics/normalized_metrics.json",
    "table_index": "tables/table_index.json",
    "report_complete": "sections/report_complete.md",
    "document_full": "parser/document_full.json",
    "content_list_enhanced": "parser/content_list_enhanced.json",
    "table_relations": "parser/table_relations.json",
    "footnotes": "qa/footnotes.json",
    "toc": "qa/toc.json",
    "financial_note_links": "qa/financial_note_links.json",
    "table_quality_signals": "qa/table_quality_signals.json",
}


@dataclass
class EvidencePackageValidation:
    package_dir: Path
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    manifest: dict[str, Any] = field(default_factory=dict)
    artifact_hashes: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "package_dir": str(self.package_dir),
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "manifest": self.manifest,
            "artifact_hashes": self.artifact_hashes,
        }


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stable_id(*parts: Any) -> str:
    return hashlib.sha256("\x1f".join("" if item is None else str(item) for item in parts).encode("utf-8")).hexdigest()


def compute_artifact_hashes(package_dir: Path, *, include_manifest: bool = False) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(package_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(package_dir))
        if not include_manifest and rel == "manifest.json":
            continue
        hashes[rel] = sha256_file(path)
    return hashes


def stable_parse_run_id(manifest: dict[str, Any], artifact_hashes: dict[str, str] | None = None) -> str:
    hashes = artifact_hashes if artifact_hashes is not None else manifest.get("artifact_hashes") or {}
    return stable_id(
        manifest.get("filing_id"),
        manifest.get("parser_version"),
        manifest.get("rules_version"),
        json.dumps(hashes, sort_keys=True, ensure_ascii=False),
    )


def market_package_paths(package_dir: Path) -> dict[str, str]:
    return {
        key: rel
        for key, rel in PACKAGE_FILE_PATHS.items()
        if (package_dir / rel).exists()
    }


def _quality_count(quality: dict[str, Any], key: str, summary_key: str | None = None) -> Any:
    if not isinstance(quality, dict):
        return None
    if quality.get(key) is not None:
        return quality.get(key)
    summary = quality.get("summary") if isinstance(quality.get("summary"), dict) else {}
    return summary.get(summary_key or key)


def _source_map_entries(source_map: dict[str, Any]) -> list[Any]:
    entries = source_map.get("entries") if isinstance(source_map, dict) else []
    return entries if isinstance(entries, list) else []


def _normalized_metrics(payload: dict[str, Any]) -> list[Any]:
    metrics = payload.get("metrics") if isinstance(payload, dict) else []
    return metrics if isinstance(metrics, list) else []


def _tables(payload: dict[str, Any]) -> list[Any]:
    tables = payload.get("tables") if isinstance(payload, dict) else []
    return tables if isinstance(tables, list) else []


def _artifact_payloads(package_dir: Path, artifacts: dict[str, str]) -> dict[str, Any]:
    return {
        key: read_json(package_dir / rel, {})
        for key, rel in artifacts.items()
        if (package_dir / rel).exists()
    }


def read_market_package_summary(package_dir: Path, *, display_path: str | None = None) -> dict[str, Any]:
    package_dir = package_dir.resolve()
    manifest = read_json(package_dir / "manifest.json", {})
    quality = read_json(package_dir / "qa" / "quality_report.json", {})
    metrics = _normalized_metrics(read_json(package_dir / "metrics" / "normalized_metrics.json", {}))
    source_map = _source_map_entries(read_json(package_dir / "qa" / "source_map.json", {}))
    return {
        "package_path": display_path or str(package_dir),
        "paths": market_package_paths(package_dir),
        "market": manifest.get("market") if isinstance(manifest, dict) else None,
        "country": manifest.get("country") if isinstance(manifest, dict) else None,
        "document_format": manifest.get("document_format") if isinstance(manifest, dict) else None,
        "filing_id": manifest.get("filing_id") if isinstance(manifest, dict) else None,
        "parse_run_id": manifest.get("parse_run_id") if isinstance(manifest, dict) else None,
        "ticker": manifest.get("ticker") if isinstance(manifest, dict) else None,
        "company_name": manifest.get("company_name") if isinstance(manifest, dict) else None,
        "form": manifest.get("form") if isinstance(manifest, dict) else None,
        "report_type": manifest.get("report_type") if isinstance(manifest, dict) else None,
        "fiscal_year": manifest.get("fiscal_year") if isinstance(manifest, dict) else None,
        "fiscal_period": manifest.get("fiscal_period") if isinstance(manifest, dict) else None,
        "period_end": manifest.get("period_end") if isinstance(manifest, dict) else None,
        "published_at": (manifest.get("published_at") or manifest.get("filing_date")) if isinstance(manifest, dict) else None,
        "quality_status": (_quality_count(quality, "overall_status") or manifest.get("quality_status")) if isinstance(manifest, dict) else None,
        "counts": {
            "sections": _quality_count(quality, "section_count"),
            "tables": _quality_count(quality, "table_count"),
            "raw_facts": _quality_count(quality, "raw_fact_count", "xbrl_fact_count"),
            "metrics": _quality_count(quality, "normalized_metric_count") or len(metrics),
            "evidence": len(source_map),
        },
    }


def read_market_package_detail(package_dir: Path, *, display_path: str | None = None) -> dict[str, Any]:
    package_dir = package_dir.resolve()
    summary = read_market_package_summary(package_dir, display_path=display_path)
    source_map = read_json(package_dir / "qa" / "source_map.json", {})
    table_index = read_json(package_dir / "tables" / "table_index.json", {})
    normalized_metrics = read_json(package_dir / "metrics" / "normalized_metrics.json", {})
    parser_artifact_paths = {
        "document_full": PACKAGE_FILE_PATHS["document_full"],
        "content_list_enhanced": PACKAGE_FILE_PATHS["content_list_enhanced"],
        "table_relations": PACKAGE_FILE_PATHS["table_relations"],
    }
    qa_artifact_paths = {
        "footnotes": PACKAGE_FILE_PATHS["footnotes"],
        "toc": PACKAGE_FILE_PATHS["toc"],
        "financial_note_links": PACKAGE_FILE_PATHS["financial_note_links"],
        "table_quality_signals": PACKAGE_FILE_PATHS["table_quality_signals"],
    }
    return {
        **summary,
        "manifest": read_json(package_dir / "manifest.json", {}),
        "quality": read_json(package_dir / "qa" / "quality_report.json", {}),
        "financial_data": read_json(package_dir / "metrics" / "financial_data.json", {}),
        "financial_checks": read_json(package_dir / "metrics" / "financial_checks.json", {}),
        "metrics": _normalized_metrics(normalized_metrics),
        "source_map": _source_map_entries(source_map),
        "tables": _tables(table_index),
        "parser_artifacts": _artifact_payloads(package_dir, parser_artifact_paths),
        "qa_artifacts": _artifact_payloads(package_dir, qa_artifact_paths),
    }


def validate_evidence_package(package_dir: Path, *, strict_hashes: bool = True) -> EvidencePackageValidation:
    package_dir = package_dir.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    manifest_path = package_dir / "manifest.json"
    manifest = read_json(manifest_path, {})

    if not package_dir.is_dir():
        errors.append(f"Package directory does not exist: {package_dir}")
        return EvidencePackageValidation(package_dir=package_dir, ok=False, errors=errors)
    if not manifest:
        errors.append("manifest.json is missing or empty")

    for rel in REQUIRED_FILES:
        if not (package_dir / rel).is_file():
            errors.append(f"Required file missing: {rel}")
    for rel in REQUIRED_DIRECTORIES:
        if not (package_dir / rel).is_dir():
            errors.append(f"Required directory missing: {rel}")

    missing = [field for field in REQUIRED_MANIFEST_FIELDS if field not in manifest]
    if missing:
        errors.append(f"Manifest required fields missing: {', '.join(missing)}")
    blank = [
        field
        for field in REQUIRED_MANIFEST_FIELDS
        if field in manifest and field != "artifact_hashes" and manifest.get(field) in (None, "")
    ]
    if blank:
        errors.append(f"Manifest required fields are blank: {', '.join(blank)}")

    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"manifest.schema_version must be {SCHEMA_VERSION}")
    if manifest.get("market") not in {"US", "HK", "JP", "KR", "EU"}:
        errors.append("manifest.market must be one of US/HK/JP/KR/EU")

    local_source_path = manifest.get("local_source_path")
    if local_source_path and not (package_dir / str(local_source_path)).is_file():
        errors.append(f"local_source_path does not exist: {local_source_path}")

    listed_hashes = manifest.get("artifact_hashes")
    if not isinstance(listed_hashes, dict) or not listed_hashes:
        errors.append("manifest.artifact_hashes must be a non-empty object")
        listed_hashes = {}
    computed_hashes = compute_artifact_hashes(package_dir)
    for rel, expected_hash in listed_hashes.items():
        if rel == "manifest.json":
            continue
        actual_hash = computed_hashes.get(rel)
        if actual_hash is None:
            errors.append(f"artifact_hashes entry is missing on disk: {rel}")
        elif strict_hashes and actual_hash != expected_hash:
            errors.append(f"artifact hash mismatch: {rel}")

    financial_data = read_json(package_dir / "metrics" / "financial_data.json", {})
    source_map = read_json(package_dir / "qa" / "source_map.json", {})
    source_entries = source_map.get("entries") if isinstance(source_map, dict) else []
    if not isinstance(source_entries, list):
        errors.append("qa/source_map.json entries must be a list")
        source_entries = []
    evidence_ids = {entry.get("evidence_id") for entry in source_entries if isinstance(entry, dict)}
    missing_evidence = missing_financial_data_evidence(financial_data)
    if missing_evidence:
        errors.append(f"financial_data metrics missing evidence: {', '.join(missing_evidence[:20])}")
    for entry in source_entries:
        if not isinstance(entry, dict):
            continue
        if not entry.get("evidence_id"):
            errors.append("source_map entry missing evidence_id")
        if not (entry.get("target") or entry.get("local_path") or entry.get("source_url")):
            errors.append(f"source_map entry has no target/local/source URL: {entry.get('evidence_id')}")
    if source_entries and not evidence_ids:
        errors.append("source_map entries do not define evidence_id values")

    quality = read_json(package_dir / "qa" / "quality_report.json", {})
    for field_name in (
        "overall_status",
        "section_count",
        "table_count",
        "raw_fact_count",
        "normalized_metric_count",
        "evidence_coverage_ratio",
        "required_statement_status",
        "critical_warnings",
        "parser_warnings",
        "rule_warnings",
    ):
        if isinstance(quality, dict) and field_name not in quality:
            warnings.append(f"quality_report missing recommended field: {field_name}")

    return EvidencePackageValidation(
        package_dir=package_dir,
        ok=not errors,
        errors=errors,
        warnings=warnings,
        manifest=manifest,
        artifact_hashes=computed_hashes,
    )


def missing_financial_data_evidence(financial_data: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for row in iter_financial_data_items(financial_data):
        sources = row.get("sources") if isinstance(row, dict) else None
        if not isinstance(sources, dict) or not sources:
            missing.append(str(row.get("canonical_name") or row.get("name") or "unknown"))
            continue
        for period_key, evidence in sources.items():
            if not isinstance(evidence, dict) or not evidence:
                missing.append(f"{row.get('canonical_name') or row.get('name')}:{period_key}")
    return missing


def iter_financial_data_items(financial_data: dict[str, Any]):
    for statement in financial_data.get("statements") or []:
        for item in statement.get("items") or []:
            yield item
    for key in ("key_metrics", "operating_metrics"):
        for item in financial_data.get(key) or []:
            yield item


def source_map_from_financial_data(
    *,
    manifest: dict[str, Any],
    financial_data: dict[str, Any],
    package_dir: Path | None = None,
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in iter_financial_data_items(financial_data):
        canonical_name = row.get("canonical_name") or row.get("name")
        statement_type = row.get("statement_type")
        values = row.get("values") if isinstance(row.get("values"), dict) else {}
        sources = row.get("sources") if isinstance(row.get("sources"), dict) else {}
        for period_key, evidence in sources.items():
            if not isinstance(evidence, dict):
                continue
            evidence_id = evidence_id_for_fact(manifest, canonical_name, period_key, evidence)
            if evidence_id in seen:
                continue
            seen.add(evidence_id)
            target = evidence_target(evidence)
            local_path = evidence.get("path") or local_path_for_evidence(evidence)
            quote_text = evidence.get("quote_text") or evidence.get("html_snippet")
            entry = {
                "evidence_id": evidence_id,
                "market": manifest.get("market"),
                "country": manifest.get("country"),
                "filing_id": manifest.get("filing_id"),
                "parse_run_id": manifest.get("parse_run_id"),
                "ticker": manifest.get("ticker"),
                "company_name": manifest.get("company_name"),
                "canonical_name": canonical_name,
                "statement_type": statement_type,
                "period_key": period_key,
                "value": values.get(period_key),
                "source_type": evidence.get("source_type"),
                "source_id": evidence.get("source_id"),
                "page_number": evidence.get("page_number"),
                "table_index": evidence.get("table_index"),
                "row_index": evidence.get("row_index"),
                "column_index": evidence.get("column_index"),
                "xbrl_tag": evidence.get("xbrl_tag"),
                "context_ref": evidence.get("raw", {}).get("context_ref") if isinstance(evidence.get("raw"), dict) else None,
                "accession_number": evidence.get("accession_number"),
                "html_anchor": evidence.get("anchor"),
                "xpath": evidence.get("xpath"),
                "source_url": evidence.get("url") or manifest.get("source_url"),
                "local_path": local_path,
                "table_json_path": local_path if local_path and str(local_path).startswith("tables/") else None,
                "pdf_local_path": manifest.get("local_source_path"),
                "quote_text": quote_text,
                "text_hash": stable_id(quote_text) if quote_text else None,
                "target": target,
                "raw": evidence,
            }
            if package_dir is not None and entry["local_path"]:
                local = package_dir / str(entry["local_path"])
                if not local.exists():
                    entry["raw"] = {**entry["raw"], "local_path_missing": True}
            entries.append(entry)
    return {
        "schema_version": "market_source_map_v1",
        "market": manifest.get("market"),
        "filing_id": manifest.get("filing_id"),
        "entries": entries,
    }


def evidence_id_for_fact(
    manifest: dict[str, Any],
    canonical_name: Any,
    period_key: Any,
    evidence: dict[str, Any],
) -> str:
    if manifest.get("market") == "EU":
        country = str(manifest.get("country") or "unknown").lower()
        filing_id = str(manifest.get("filing_id") or "unknown").replace(":", "-")
        source_type = str(evidence.get("source_type") or "")
        page_number = evidence.get("page_number")
        table_index = evidence.get("table_index")
        row_index = evidence.get("row_index")
        column_index = evidence.get("column_index")
        if page_number is not None and table_index is not None and row_index is not None:
            parts = ["eu", country, filing_id, f"p{page_number}", f"t{table_index}", f"r{row_index}"]
            if column_index is not None:
                parts.append(f"c{column_index}")
            return ":".join(parts)
        if evidence.get("xbrl_tag"):
            fact_key = (
                evidence.get("raw", {}).get("fact_id")
                if isinstance(evidence.get("raw"), dict)
                else None
            ) or stable_id(
                evidence.get("xbrl_tag"),
                evidence.get("raw", {}).get("context_ref") if isinstance(evidence.get("raw"), dict) else None,
                evidence.get("raw", {}).get("unit_ref") if isinstance(evidence.get("raw"), dict) else None,
                canonical_name,
                period_key,
            )[:24]
            return f"eu:{country}:{filing_id}:xbrl:{fact_key}"
        if "html" in source_type and table_index is not None and row_index is not None:
            parts = ["eu", country, filing_id, "html", f"t{table_index}", f"r{row_index}"]
            if column_index is not None:
                parts.append(f"c{column_index}")
            return ":".join(parts)
    if manifest.get("market") == "HK":
        page_number = evidence.get("page_number")
        table_index = evidence.get("table_index")
        row_index = evidence.get("row_index")
        column_index = evidence.get("column_index")
        if page_number is not None and table_index is not None and row_index is not None:
            parts = [
                "hk",
                str(manifest.get("filing_id") or "unknown"),
                f"p{page_number}",
                f"t{table_index}",
                f"r{row_index}",
            ]
            if column_index is not None:
                parts.append(f"c{column_index}")
            return ":".join(parts)
    return stable_id(
        manifest.get("filing_id"),
        canonical_name,
        period_key,
        evidence.get("source_type"),
        evidence.get("source_id"),
        evidence.get("page_number"),
        evidence.get("table_index"),
        evidence.get("row_index"),
        evidence.get("column_index"),
        evidence.get("xbrl_tag"),
        evidence.get("raw", {}).get("context_ref") if isinstance(evidence.get("raw"), dict) else None,
    )


def local_path_for_evidence(evidence: dict[str, Any]) -> str | None:
    source_type = str(evidence.get("source_type") or "")
    table_index = evidence.get("table_index")
    if table_index is not None and ("table" in source_type or source_type.startswith("pdf_")):
        try:
            return f"tables/table_{int(table_index):04d}.json"
        except (TypeError, ValueError):
            return "tables/table_index.json"
    if evidence.get("xbrl_tag"):
        return "xbrl/facts_raw.json"
    return evidence.get("path")


def evidence_target(evidence: dict[str, Any]) -> str:
    url = evidence.get("url") or ""
    anchor = evidence.get("anchor") or evidence.get("html_anchor")
    page_number = evidence.get("page_number")
    table_index = evidence.get("table_index")
    row_index = evidence.get("row_index")
    column_index = evidence.get("column_index")
    if url and anchor:
        return f"{url}#{anchor}"
    if page_number is not None:
        return f"page={page_number};table={table_index};row={row_index};column={column_index}"
    if url:
        return str(url)
    if evidence.get("xbrl_tag"):
        return f"xbrl:{evidence.get('xbrl_tag')}:{evidence.get('source_id') or ''}"
    return ""


def normalized_metrics_from_financial_data(
    *,
    manifest: dict[str, Any],
    financial_data: dict[str, Any],
    source_map: dict[str, Any],
) -> list[dict[str, Any]]:
    entries_by_key: dict[tuple[Any, Any, Any, Any, Any, Any, Any, Any], dict[str, Any]] = {}
    for entry in source_map.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        key = (
            entry.get("canonical_name"),
            entry.get("period_key"),
            entry.get("source_type"),
            entry.get("source_id"),
            entry.get("page_number"),
            entry.get("table_index"),
            entry.get("row_index"),
            entry.get("column_index"),
        )
        entries_by_key[key] = entry

    rows: list[dict[str, Any]] = []
    parse_run_id = manifest.get("parse_run_id") or stable_parse_run_id(manifest)
    for item in iter_financial_data_items(financial_data):
        sources = item.get("sources") if isinstance(item.get("sources"), dict) else {}
        values = item.get("values") if isinstance(item.get("values"), dict) else {}
        raw_values = item.get("raw_values") if isinstance(item.get("raw_values"), dict) else {}
        periods = item.get("periods") if isinstance(item.get("periods"), dict) else {}
        for period_key, value in values.items():
            evidence = sources.get(period_key) if isinstance(sources, dict) else None
            if not isinstance(evidence, dict):
                evidence = {}
            lookup_key = (
                item.get("canonical_name"),
                period_key,
                evidence.get("source_type"),
                evidence.get("source_id"),
                evidence.get("page_number"),
                evidence.get("table_index"),
                evidence.get("row_index"),
                evidence.get("column_index"),
            )
            entry = entries_by_key.get(lookup_key, {})
            period = periods.get(period_key) if isinstance(periods.get(period_key), dict) else {}
            metric_id = stable_id(
                parse_run_id,
                item.get("canonical_name"),
                period_key,
                evidence.get("source_type"),
                evidence.get("source_id"),
                evidence.get("row_index"),
                evidence.get("column_index"),
            )
            rows.append(
                {
                    "metric_id": metric_id,
                    "filing_id": manifest.get("filing_id"),
                    "parse_run_id": parse_run_id,
                    "market": manifest.get("market"),
                    "ticker": manifest.get("ticker"),
                    "statement_type": item.get("statement_type"),
                    "canonical_name": item.get("canonical_name"),
                    "local_name": item.get("name"),
                    "label": item.get("name"),
                    "value": value,
                    "raw_value": raw_values.get(period_key),
                    "unit": item.get("unit"),
                    "currency": item.get("currency"),
                    "scale": item.get("scale"),
                    "period_key": period_key,
                    "period_start": period.get("period_start"),
                    "period_end": period.get("period_end") or period_key,
                    "duration_days": period.get("duration_days"),
                    "frame": period.get("frame"),
                    "qtd_ytd_type": period.get("qtd_ytd_type"),
                    "fiscal_year": period.get("fiscal_year") or manifest.get("fiscal_year"),
                    "fiscal_period": period.get("fiscal_period") or manifest.get("fiscal_period"),
                    "accounting_standard": manifest.get("accounting_standard"),
                    "taxonomy": item.get("taxonomy"),
                    "gaap_status": item.get("gaap_status"),
                    "confidence": item.get("confidence"),
                    "source_type": evidence.get("source_type"),
                    "evidence_id": entry.get("evidence_id"),
                    "raw_fact_id": evidence.get("raw", {}).get("fact_id") if isinstance(evidence.get("raw"), dict) else None,
                    "xbrl_tag": evidence.get("xbrl_tag"),
                    "context_ref": evidence.get("raw", {}).get("context_ref") if isinstance(evidence.get("raw"), dict) else None,
                    "page_number": evidence.get("page_number"),
                    "table_index": evidence.get("table_index"),
                    "row_index": evidence.get("row_index"),
                    "column_index": evidence.get("column_index"),
                    "raw": item.get("raw"),
                }
            )
    return rows


def build_quality_report(
    *,
    manifest: dict[str, Any],
    financial_data: dict[str, Any],
    financial_checks: dict[str, Any],
    section_count: int,
    table_count: int,
    raw_fact_count: int,
    source_map: dict[str, Any],
    parser_warnings: list[str] | None = None,
    rule_warnings: list[str] | None = None,
) -> dict[str, Any]:
    metric_count = 0
    evidence_count = 0
    statement_status: dict[str, str] = {
        "balance_sheet": "missing",
        "income_statement": "missing",
        "cash_flow_statement": "missing",
    }
    for statement in financial_data.get("statements") or []:
        statement_type = statement.get("statement_type")
        item_count = len(statement.get("items") or [])
        if statement_type in statement_status and item_count > 0:
            statement_status[statement_type] = "present"
        for item in statement.get("items") or []:
            values = item.get("values") if isinstance(item.get("values"), dict) else {}
            sources = item.get("sources") if isinstance(item.get("sources"), dict) else {}
            metric_count += len(values)
            evidence_count += sum(1 for key in values if sources.get(key))
    for bucket in ("key_metrics", "operating_metrics"):
        for item in financial_data.get(bucket) or []:
            values = item.get("values") if isinstance(item.get("values"), dict) else {}
            sources = item.get("sources") if isinstance(item.get("sources"), dict) else {}
            metric_count += len(values)
            evidence_count += sum(1 for key in values if sources.get(key))

    missing = missing_financial_data_evidence(financial_data)
    critical_warnings = []
    if missing:
        critical_warnings.append({"type": "missing_evidence", "metrics": missing})
    if any(status == "missing" for status in statement_status.values()):
        critical_warnings.append({"type": "missing_statement", "required_statement_status": statement_status})

    extraction_status = "ok"
    extraction_blockers: list[dict[str, Any]] = []
    if metric_count == 0 and table_count == 0:
        extraction_status = "parser_table_not_detected"
        extraction_blockers.append(
            {
                "type": "parser_table_not_detected",
                "message": "Parser output did not expose structured tables for rule extraction.",
            }
        )
    elif metric_count == 0 and table_count > 0 and all(status == "missing" for status in statement_status.values()):
        extraction_status = "financial_statement_table_not_recognized"
        extraction_blockers.append(
            {
                "type": "financial_statement_table_not_recognized",
                "message": "Parsed tables exist, but none were recognized as financial statement tables.",
            }
        )
    elif any(status == "missing" for status in statement_status.values()):
        extraction_status = "partial_statement_coverage"
        extraction_blockers.append(
            {
                "type": "partial_statement_coverage",
                "required_statement_status": statement_status,
            }
        )

    ratio = 1.0 if metric_count == 0 else round(evidence_count / metric_count, 6)
    return {
        "schema_version": "market_quality_report_v1",
        "market": manifest.get("market"),
        "filing_id": manifest.get("filing_id"),
        "parse_run_id": manifest.get("parse_run_id"),
        "overall_status": financial_checks.get("overall_status") or manifest.get("quality_status") or "warning",
        "section_count": section_count,
        "table_count": table_count,
        "raw_fact_count": raw_fact_count,
        "normalized_metric_count": metric_count,
        "evidence_coverage_ratio": ratio,
        "extraction_status": extraction_status,
        "extraction_blockers": extraction_blockers,
        "required_statement_status": statement_status,
        "critical_warnings": critical_warnings,
        "parser_warnings": parser_warnings or [],
        "rule_warnings": rule_warnings or financial_data.get("warnings") or financial_checks.get("warnings") or [],
        "source_map_entry_count": len(source_map.get("entries") or []),
    }
