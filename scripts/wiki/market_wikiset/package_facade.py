#!/usr/bin/env python3
"""Write a standard market evidence package facade for market Wiki reports."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
SUSPICIOUS_TABLE_WARNING_TOKEN = "可疑表样本"
FINANCIAL_TABLE_TYPES = {
    "balance_sheet",
    "income_statement",
    "cash_flow_statement",
    "statement_of_changes_in_equity",
    "financial_statement",
    "fact",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def rel(path: Path, root: Path = REPO_ROOT) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_artifact_hashes(package_dir: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(package_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(package_dir).as_posix()
        if relative == "manifest.json":
            continue
        hashes[relative] = sha256_file(path)
    return hashes


def _copy_if_exists(source: Path, dest: Path) -> bool:
    if not source.is_file():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    return True


def _table_index_payload(market: str, row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("table_index")
    if isinstance(payload, dict):
        tables = payload.get("tables") if isinstance(payload.get("tables"), list) else []
        return {**payload, "schema_version": payload.get("schema_version") or f"{market.lower()}_table_index_v1", "tables": tables}
    if isinstance(payload, list):
        return {"schema_version": f"{market.lower()}_table_index_v1", "market": market, "tables": payload}
    return {"schema_version": f"{market.lower()}_table_index_v1", "market": market, "tables": []}


def _source_map_from_evidence(market: str, report_id: str, evidence_items: list[dict[str, Any]]) -> dict[str, Any]:
    entries = []
    for item in evidence_items:
        if not isinstance(item, dict):
            continue
        evidence_id = item.get("evidence_id")
        if not evidence_id:
            continue
        entries.append(
            {
                "evidence_id": evidence_id,
                "source_type": item.get("source_kind") or item.get("source_type") or "wiki_metric_evidence",
                "source_id": item.get("source_id") or evidence_id,
                "local_path": "metrics/normalized_metrics.json",
                "target": item.get("metric_key") or item.get("metric_name"),
                "report_id": report_id,
                "market": market,
                "page_number": item.get("pdf_page_number"),
                "pdf_page_number": item.get("pdf_page_number"),
                "table_index": item.get("table_index"),
                "row_index": item.get("row_index"),
                "column_index": item.get("column_index"),
                "quote_text": item.get("quote_text"),
                "raw": item,
            }
        )
    return {
        "schema_version": f"{market.lower()}_source_map_v1",
        "market": market,
        "report_id": report_id,
        "entry_count": len(entries),
        "entries": entries,
        "generated_at": now_iso(),
    }


def _financially_relevant_suspicious_tables(quality: dict[str, Any]) -> list[dict[str, Any]]:
    suspicious = quality.get("suspicious_tables")
    if not isinstance(suspicious, list):
        return []
    core_indexes = {
        candidate.get("table_index")
        for candidate in quality.get("core_financial_table_candidates") or []
        if isinstance(candidate, dict) and candidate.get("status") == "found"
    }
    relevant: list[dict[str, Any]] = []
    for table in suspicious:
        if not isinstance(table, dict):
            continue
        table_type = str(table.get("table_type") or "").strip().lower()
        if (
            table.get("table_index") in core_indexes
            or bool(table.get("matched_financial_names"))
            or bool(table.get("classification_reasons"))
            or table.get("year_binding_required") is True
            or table_type in FINANCIAL_TABLE_TYPES
        ):
            relevant.append(table)
    return relevant


def _quality_messages(row: dict[str, Any], quality: dict[str, Any]) -> tuple[list[str], list[str]]:
    warnings = [str(item) for item in row.get("warnings") or [] if str(item).strip()]
    advisories: list[str] = []
    suspicious = quality.get("suspicious_tables")
    can_classify_suspicious = isinstance(suspicious, list) and bool(suspicious)
    relevant_suspicious = _financially_relevant_suspicious_tables(quality)
    for item in quality.get("warnings") or []:
        message = str(item).strip()
        if not message:
            continue
        if (
            SUSPICIOUS_TABLE_WARNING_TOKEN in message
            and can_classify_suspicious
            and not relevant_suspicious
        ):
            advisories.append(message)
        else:
            warnings.append(message)
    return sorted(set(warnings)), sorted(set(advisories))


def _quality_report(
    market: str,
    row: dict[str, Any],
    report_json: dict[str, Any],
    three_statements: dict[str, Any],
    evidence_items: list[dict[str, Any]],
) -> dict[str, Any]:
    quality = row.get("quality") if isinstance(row.get("quality"), dict) else {}
    financial_checks = row.get("financial_checks") if isinstance(row.get("financial_checks"), dict) else {}
    metrics = three_statements.get("metrics") if isinstance(three_statements, dict) else []
    metrics = metrics if isinstance(metrics, list) else []
    evidence_coverage_ratio = round(min(len(evidence_items), len(metrics)) / len(metrics), 6) if metrics else None
    status = "pass" if str(report_json.get("status") or "") == "ready" else "warning"
    warnings, advisories = _quality_messages(row, quality)
    return {
        "schema_version": f"{market.lower()}_package_quality_report_v1",
        "market": market,
        "overall_status": status,
        "section_count": 1 if (Path(row["result_dir"]) / "result_complete.md").is_file() or (Path(row["result_dir"]) / "result.md").is_file() else 0,
        "table_count": quality.get("table_count") or len((_table_index_payload(market, row).get("tables") or [])),
        "raw_fact_count": len(metrics),
        "normalized_metric_count": len(metrics),
        "evidence_count": len(evidence_items),
        "evidence_coverage_ratio": evidence_coverage_ratio,
        "required_statement_status": _required_statement_status(metrics),
        "missing_required_statements": [
            statement for statement, value in _required_statement_status(metrics).items() if value != "present"
        ],
        "financial_check_status": financial_checks.get("overall_status"),
        "parser_warnings": [],
        "rule_warnings": warnings,
        "rule_advisories": advisories,
        "critical_warnings": [],
        "summary": {
            "markdown_chars": quality.get("markdown_chars"),
            "financial_summary": financial_checks.get("summary"),
        },
        "generated_at": now_iso(),
    }


def _required_statement_status(metrics: list[dict[str, Any]]) -> dict[str, str]:
    present = {str(item.get("statement_type") or "") for item in metrics if isinstance(item, dict)}
    return {
        statement: "present" if statement in present else "missing"
        for statement in ("income_statement", "balance_sheet", "cash_flow_statement")
    }


def _manifest_payload(
    *,
    market: str,
    row: dict[str, Any],
    report_json: dict[str, Any],
    report_dir: Path,
    quality_status: str,
    artifact_hashes: dict[str, str],
) -> dict[str, Any]:
    financial_data = row.get("financial_data") if isinstance(row.get("financial_data"), dict) else {}
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    company_id = row.get("company_id") or (report_json.get("identity") or {}).get("company_id")
    if not company_id:
        company_id = f"{market}:{row.get('ticker') or row.get('stock_code') or row.get('company_wiki_id')}"
    sidecar = row.get("sidecar") if isinstance(row.get("sidecar"), dict) else {}
    source_metadata = row.get("source_metadata") if isinstance(row.get("source_metadata"), dict) else {}
    source_tier = row.get("source_tier") or metadata.get("source_tier") or "local_uploaded"
    source_verification_status = row.get("source_verification_status")
    official_source_verified = bool(row.get("official_source_verified"))
    regulator_host_verified = bool(row.get("regulator_host_verified"))
    source_url = (
        row.get("source_url")
        or metadata.get("source_url")
        or metadata.get("document_url")
        or metadata.get("landing_url")
        or source_metadata.get("source_url")
        or source_metadata.get("document_url")
        or sidecar.get("source_url")
        or ""
    )
    accession_number = row.get("accession_number") or sidecar.get("accession_number")
    source_sha256 = row.get("source_sha256") or sidecar.get("content_sha256")
    raw_source_sha256 = artifact_hashes.get("raw/report.pdf")
    if source_sha256 and raw_source_sha256 and str(source_sha256).lower() != str(raw_source_sha256).lower():
        raise RuntimeError("verified source PDF hash does not match packaged raw/report.pdf")
    if official_source_verified and not raw_source_sha256:
        raise RuntimeError("verified official source requires packaged raw/report.pdf")
    local_source_path = "raw/report.pdf" if raw_source_sha256 else "parser/document_full.json"
    return {
        "schema_version": "market_evidence_package_v1",
        "market": market,
        "country": row.get("country"),
        "filing_id": row.get("filing_id") or f"{market}:{row.get('ticker')}:{row.get('report_id')}:{row.get('task_id')}",
        "accession_number": accession_number,
        "company_id": company_id,
        "company_wiki_id": row.get("company_wiki_id"),
        "company_wiki_path": rel(report_dir.parents[1]),
        "wiki_report_path": rel(report_dir),
        "report_id": row.get("report_id"),
        "ticker": row.get("ticker") or row.get("stock_code"),
        "stock_code": row.get("stock_code") or row.get("ticker"),
        "company_name": row.get("company_name"),
        "source_id": row.get("source_id") or metadata.get("source_id") or market.lower(),
        "source_tier": source_tier,
        "source_verification_status": source_verification_status,
        "official_source_verified": official_source_verified,
        "regulator_host_verified": regulator_host_verified,
        "form": row.get("form") or row.get("report_kind") or row.get("report_type"),
        "report_type": row.get("report_type") or row.get("report_kind"),
        "fiscal_year": row.get("report_year"),
        "fiscal_period": row.get("fiscal_period") or "FY",
        "period_end": row.get("period_end"),
        "published_at": row.get("published_at"),
        "source_url": source_url,
        "source_manifest": {
            "schema_version": "siq_source_manifest_v1",
            "source_tier": source_tier,
            "source_verification_status": source_verification_status,
            "official_source_verified": official_source_verified,
            "regulator_host_verified": regulator_host_verified,
            "initial_url": source_url,
            "final_url": source_url,
            "redirect_chain": [],
            "content_sha256": raw_source_sha256 or source_sha256 or artifact_hashes.get("parser/document_full.json"),
            "retrieved_at": row.get("published_at") or now_iso(),
            "source_note": "Generated from local standardized parser artifacts; original official URL may be absent in legacy parser metadata.",
        },
        "local_source_path": local_source_path,
        "document_format": row.get("document_format") or "pdf",
        "accounting_standard": financial_data.get("accounting_standard") or row.get("accounting_standard") or "",
        "parser_version": metadata.get("parser_version") or f"{market.lower()}_pdf_parser_result",
        "rules_version": metadata.get("rules_version") or f"{market.lower()}_wiki_rules_v1",
        "quality_status": quality_status,
        "artifact_hashes": artifact_hashes,
        "parse_run_id": row.get("parse_run_id") or f"{market}:{row.get('task_id')}",
        "task_id": row.get("task_id"),
        "source_filename": row.get("filename"),
        "generated_at": now_iso(),
    }


def write_report_package_facade(
    *,
    market: str,
    company_dir: Path,
    report_dir: Path,
    metrics_dir: Path,
    row: dict[str, Any],
    report_json: dict[str, Any],
    three_statements: dict[str, Any],
    key_metrics: Any,
    validation: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    package_financial_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mirror an A-share-style report workspace into the standard package contract."""

    market = market.upper()
    result_dir = Path(row["result_dir"])
    for dirname in ("raw", "sections", "tables", "xbrl", "metrics", "qa", "parser"):
        (report_dir / dirname).mkdir(parents=True, exist_ok=True)

    _copy_if_exists(report_dir / "report.md", report_dir / "sections" / "report_complete.md")
    source_pdf_path = Path(str(row.get("source_pdf_path") or "")).expanduser()
    if not source_pdf_path.is_absolute():
        source_pdf_path = REPO_ROOT / source_pdf_path
    _copy_if_exists(source_pdf_path, report_dir / "raw" / "report.pdf")
    for source_name in (
        "result.md",
        "result_complete.md",
        "document_full.json",
        "content_list.json",
        "content_list_enhanced.json",
        "table_index.json",
        "table_relations.json",
        "financial_data.json",
        "financial_checks.json",
        "quality_report.json",
    ):
        _copy_if_exists(result_dir / source_name, report_dir / "parser" / source_name)
    _copy_if_exists(result_dir / "table_relations.json", report_dir / "tables" / "table_relations.json")
    if package_financial_data is not None:
        write_json(report_dir / "metrics" / "financial_data.json", package_financial_data)
    else:
        _copy_if_exists(metrics_dir / "financial_data.json", report_dir / "metrics" / "financial_data.json")
    _copy_if_exists(metrics_dir / "financial_checks.json", report_dir / "metrics" / "financial_checks.json")
    _copy_if_exists(metrics_dir / "normalized_metrics.json", report_dir / "metrics" / "normalized_metrics.json")
    write_json(report_dir / "metrics" / "key_metrics.json", {"schema_version": f"{market.lower()}_key_metrics_v1", "data": key_metrics, "generated_at": now_iso()})
    write_json(report_dir / "metrics" / "validation.json", validation)
    write_json(report_dir / "tables" / "table_index.json", _table_index_payload(market, row))
    write_json(report_dir / "qa" / "source_map.json", _source_map_from_evidence(market, str(row.get("report_id") or ""), evidence_items))
    quality_report = _quality_report(market, row, report_json, three_statements, evidence_items)
    write_json(report_dir / "qa" / "quality_report.json", quality_report)
    write_json(
        report_dir / "qa" / "extraction_warnings.json",
        {
            "schema_version": f"{market.lower()}_extraction_warnings_v1",
            "warnings": quality_report["rule_warnings"],
            "advisories": quality_report["rule_advisories"],
        },
    )
    for name in ("facts_raw", "contexts", "units"):
        write_json(report_dir / "xbrl" / f"{name}.json", {"schema_version": f"{market.lower()}_xbrl_{name}_v1", name: []})
    write_json(
        report_dir / "raw" / "source_reference.json",
        {
            "schema_version": f"{market.lower()}_raw_source_reference_v1",
            "market": market,
            "task_id": row.get("task_id"),
            "result_dir": rel(result_dir),
            "source_filename": row.get("filename"),
            "generated_at": now_iso(),
        },
    )
    write_text(
        report_dir / "README.md",
        (
            f"# {row.get('company_name') or row.get('ticker')} {row.get('report_id')}\n\n"
            "This report directory is both an A-share-aligned company Wiki report "
            "and a standard market evidence package facade for API/import/vector tools.\n"
        ),
    )

    artifact_hashes = compute_artifact_hashes(report_dir)
    manifest = _manifest_payload(
        market=market,
        row=row,
        report_json=report_json,
        report_dir=report_dir,
        quality_status=quality_report["overall_status"],
        artifact_hashes=artifact_hashes,
    )
    write_json(report_dir / "manifest.json", manifest)
    return manifest
