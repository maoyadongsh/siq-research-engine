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
    status = "pass" if str(report_json.get("status") or "") == "ready" else "warning"
    warnings = sorted(set((row.get("warnings") or []) + (quality.get("warnings") or [])))
    return {
        "schema_version": f"{market.lower()}_package_quality_report_v1",
        "market": market,
        "overall_status": status,
        "section_count": 1 if (Path(row["result_dir"]) / "result_complete.md").is_file() or (Path(row["result_dir"]) / "result.md").is_file() else 0,
        "table_count": quality.get("table_count") or len((_table_index_payload(market, row).get("tables") or [])),
        "normalized_metric_count": len(metrics),
        "evidence_count": len(evidence_items),
        "required_statement_status": _required_statement_status(metrics),
        "missing_required_statements": [
            statement for statement, value in _required_statement_status(metrics).items() if value != "present"
        ],
        "financial_check_status": financial_checks.get("overall_status"),
        "parser_warnings": [],
        "rule_warnings": warnings,
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
    source_url = (
        metadata.get("source_url")
        or metadata.get("document_url")
        or metadata.get("landing_url")
        or ((row.get("source_metadata") or {}).get("document_url") if isinstance(row.get("source_metadata"), dict) else None)
        or ""
    )
    return {
        "schema_version": "market_evidence_package_v1",
        "market": market,
        "country": row.get("country"),
        "filing_id": row.get("filing_id") or f"{market}:{row.get('ticker')}:{row.get('report_id')}:{row.get('task_id')}",
        "company_id": company_id,
        "company_wiki_id": row.get("company_wiki_id"),
        "company_wiki_path": rel(report_dir.parents[1]),
        "wiki_report_path": rel(report_dir),
        "report_id": row.get("report_id"),
        "ticker": row.get("ticker") or row.get("stock_code"),
        "stock_code": row.get("stock_code") or row.get("ticker"),
        "company_name": row.get("company_name"),
        "source_id": row.get("source_id") or metadata.get("source_id") or market.lower(),
        "source_tier": row.get("source_tier") or metadata.get("source_tier") or "local_uploaded",
        "form": row.get("form") or row.get("report_kind") or row.get("report_type"),
        "report_type": row.get("report_type") or row.get("report_kind"),
        "fiscal_year": row.get("report_year"),
        "fiscal_period": row.get("fiscal_period") or "FY",
        "period_end": row.get("period_end"),
        "published_at": row.get("published_at"),
        "source_url": source_url,
        "source_manifest": {
            "schema_version": "siq_source_manifest_v1",
            "source_tier": row.get("source_tier") or metadata.get("source_tier") or "local_uploaded",
            "initial_url": source_url,
            "final_url": source_url,
            "redirect_chain": [],
            "content_sha256": artifact_hashes.get("raw/source_reference.json") or artifact_hashes.get("parser/document_full.json"),
            "retrieved_at": row.get("published_at") or now_iso(),
            "source_note": "Generated from local standardized parser artifacts; original official URL may be absent in legacy parser metadata.",
        },
        "local_source_path": rel(Path(row["result_dir"])),
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
) -> dict[str, Any]:
    """Mirror an A-share-style report workspace into the standard package contract."""

    market = market.upper()
    result_dir = Path(row["result_dir"])
    for dirname in ("raw", "sections", "tables", "xbrl", "metrics", "qa", "parser"):
        (report_dir / dirname).mkdir(parents=True, exist_ok=True)

    _copy_if_exists(report_dir / "report.md", report_dir / "sections" / "report_complete.md")
    _copy_if_exists(report_dir / "document_full.json", report_dir / "parser" / "document_full.json")
    for source_name in ("content_list_enhanced.json", "table_relations.json"):
        _copy_if_exists(result_dir / source_name, report_dir / "parser" / source_name)
    _copy_if_exists(metrics_dir / "financial_data.json", report_dir / "metrics" / "financial_data.json")
    _copy_if_exists(metrics_dir / "financial_checks.json", report_dir / "metrics" / "financial_checks.json")
    _copy_if_exists(metrics_dir / "normalized_metrics.json", report_dir / "metrics" / "normalized_metrics.json")
    write_json(report_dir / "metrics" / "key_metrics.json", {"schema_version": f"{market.lower()}_key_metrics_v1", "data": key_metrics, "generated_at": now_iso()})
    write_json(report_dir / "metrics" / "validation.json", validation)
    write_json(report_dir / "tables" / "table_index.json", _table_index_payload(market, row))
    write_json(report_dir / "qa" / "source_map.json", _source_map_from_evidence(market, str(row.get("report_id") or ""), evidence_items))
    quality_report = _quality_report(market, row, report_json, three_statements, evidence_items)
    write_json(report_dir / "qa" / "quality_report.json", quality_report)
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
