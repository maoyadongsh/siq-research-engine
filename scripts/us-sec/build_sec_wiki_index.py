#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sec_wiki_ingestion_rules import (
    FULL_DOCUMENT_WIKI_FILES,
    WIKI_INGESTION_PLAN_PATH,
    summarize_wiki_ingestion_plan,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "data" / "wiki" / "us"
METRIC_FILES = ("financial_data.json", "financial_checks.json", "normalized_metrics.json")
REQUIRED_STATEMENTS = ("balance_sheet", "income_statement", "cash_flow_statement")
REQUIRED_RETRIEVAL_METRICS = (
    "total_assets",
    "total_liabilities",
    "total_equity",
    "operating_revenue",
    "net_profit",
    "operating_cash_flow_net",
)
FULL_DOCUMENT_FILES = FULL_DOCUMENT_WIKI_FILES


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_csv_set(value: str | None, *, upper: bool = True) -> set[str] | None:
    if not value:
        return None
    items = {item.strip() for item in value.split(",") if item.strip()}
    if upper:
        items = {item.upper() for item in items}
    return items or None


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


def build_wiki_index(
    output_root: Path,
    *,
    forms: set[str] | None = None,
    tickers: set[str] | None = None,
    case_set_name: str = "case_set_50_us_10k.json",
) -> dict[str, Any]:
    output_root = output_root.resolve()
    packages = discover_packages(output_root, forms=forms, tickers=tickers)
    by_company: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in packages:
        company_key = str(item.get("company_wiki_id") or company_wiki_dir_name(item.get("ticker"), item.get("company_name")) or "UNKNOWN")
        by_company[company_key].append(item)

    for company_key, items in sorted(by_company.items()):
        _write_company_index(output_root, company_key, _sort_filings(items))
    _write_company_catalog(output_root)

    package_index_path = output_root / "_meta" / "package_index.json"
    quality_summary_path = output_root / "_meta" / "quality_summary.json"
    case_set_path = output_root / "_meta" / case_set_name
    market_profile_path = output_root / "_meta" / "market_profile.json"
    ingestion_manifest_path = output_root / "_meta" / "ingestion_manifest.json"
    write_json(package_index_path, {"schema_version": "sec_package_index_v1", "generated_at": now_iso(), "count": len(packages), "items": packages})
    quality_summary = _quality_summary(packages)
    write_json(quality_summary_path, quality_summary)
    write_json(case_set_path, _case_set(case_set_name, packages))
    write_json(market_profile_path, _market_profile(packages))
    write_json(ingestion_manifest_path, _ingestion_manifest(packages, case_set_name=case_set_name))
    return {
        "schema_version": "sec_wiki_index_build_summary_v1",
        "generated_at": now_iso(),
        "output_root": str(output_root),
        "package_count": len(packages),
        "company_count": len(by_company),
        "paths": {
            "package_index": str(package_index_path),
            "quality_summary": str(quality_summary_path),
            "case_set": str(case_set_path),
            "market_profile": str(market_profile_path),
            "ingestion_manifest": str(ingestion_manifest_path),
        },
        "quality_counts": quality_summary["quality_counts"],
        "retrieval_status_counts": quality_summary["retrieval_status_counts"],
        "full_document_status_counts": quality_summary["full_document_status_counts"],
        "wiki_ingestion_status_counts": quality_summary["wiki_ingestion_status_counts"],
    }


def discover_packages(output_root: Path, *, forms: set[str] | None = None, tickers: set[str] | None = None) -> list[dict[str, Any]]:
    form_filter = {item.upper() for item in forms} if forms else None
    ticker_filter = {item.upper() for item in tickers} if tickers else None
    items: list[dict[str, Any]] = []
    for pattern in ("companies/*/reports/*/manifest.json", "*/*/*/manifest.json"):
        for manifest_path in sorted(output_root.glob(pattern)):
            if any(part.startswith("_") for part in manifest_path.parts):
                continue
            package_dir = manifest_path.parent
            manifest = read_json(manifest_path, {})
            if manifest.get("market") != "US":
                continue
            form = str(manifest.get("form") or "").upper()
            ticker = str(manifest.get("ticker") or "UNKNOWN").upper()
            if form_filter and form not in form_filter:
                continue
            if ticker_filter and ticker not in ticker_filter:
                continue
            summary = _package_summary(output_root, package_dir, manifest)
            if not any(item.get("package_path") == summary.get("package_path") for item in items):
                items.append(summary)
    return _sort_filings(items)


def _package_summary(output_root: Path, package_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    quality = read_json(package_dir / "qa" / "quality_report.json", {})
    metrics = read_json(package_dir / "metrics" / "normalized_metrics.json", {})
    financial_data = read_json(package_dir / "metrics" / "financial_data.json", {})
    source_map = read_json(package_dir / "qa" / "source_map.json", {})
    ingestion_plan = read_json(package_dir / WIKI_INGESTION_PLAN_PATH, {})
    company_wiki_id = manifest.get("company_wiki_id") or company_wiki_dir_name(manifest.get("ticker"), manifest.get("company_name"))
    report_id = manifest.get("report_id") or filing_slug(str(manifest.get("filing_id") or manifest.get("accession_number") or package_dir.name))
    counts = {
        "sections": _count(quality, "section_count"),
        "tables": _count(quality, "table_count"),
        "raw_facts": _count(quality, "raw_fact_count", "xbrl_fact_count"),
        "metrics": _count(quality, "normalized_metric_count") or len(metrics.get("metrics") or []),
        "evidence": len(source_map.get("entries") or []),
    }
    retrieval = _retrieval_readiness(financial_data, metrics, quality, source_map)
    full_document = _full_document_summary(package_dir, quality, ingestion_plan)
    return {
        "schema_version": "sec_package_summary_v1",
        "market": "US",
        "package_path": repo_relative(package_dir),
        "manifest_path": repo_relative(package_dir / "manifest.json"),
        "filing_id": manifest.get("filing_id"),
        "report_id": report_id,
        "parse_run_id": manifest.get("parse_run_id"),
        "parser_result_dir": manifest.get("parser_result_dir"),
        "parser_result_task_id": manifest.get("parser_result_task_id"),
        "company_id": manifest.get("company_id"),
        "company_wiki_id": company_wiki_id,
        "company_wiki_path": manifest.get("company_wiki_path") or repo_relative(output_root / "companies" / company_wiki_id),
        "wiki_report_path": manifest.get("wiki_report_path") or repo_relative(package_dir),
        "cik": manifest.get("cik"),
        "ticker": manifest.get("ticker"),
        "company_name": manifest.get("company_name"),
        "form": manifest.get("form"),
        "report_type": manifest.get("report_type"),
        "accession_number": manifest.get("accession_number"),
        "fiscal_year": manifest.get("fiscal_year"),
        "fiscal_period": manifest.get("fiscal_period"),
        "period_end": manifest.get("period_end"),
        "filing_date": manifest.get("filing_date") or manifest.get("published_at"),
        "published_at": manifest.get("published_at") or manifest.get("filing_date"),
        "source_url": manifest.get("source_url"),
        "quality_status": quality.get("overall_status") or manifest.get("quality_status") or "warning",
        "retrieval_status": retrieval["retrieval_status"],
        "wiki_ready": retrieval["wiki_ready"],
        "retrieval_issues": retrieval["issues"],
        "full_document_status": full_document["status"],
        "full_document_ready": full_document["ready"],
        "full_document_paths": full_document["paths"],
        "full_document_quality": full_document["quality"],
        "wiki_ingestion_plan": repo_relative(package_dir / WIKI_INGESTION_PLAN_PATH),
        "wiki_ingestion_status": full_document["wiki_ingestion"]["status"],
        "wiki_ingestion_ready": full_document["wiki_ingestion"]["ready"],
        "wiki_ingestion_summary": full_document["wiki_ingestion"],
        "required_statement_status": retrieval["required_statement_status"],
        "core_metric_status": retrieval["core_metric_status"],
        "quality_summary": {
            "section_count": counts["sections"],
            "table_count": counts["tables"],
            "xbrl_fact_count": counts["raw_facts"],
            "normalized_metric_count": counts["metrics"],
            "evidence_count": counts["evidence"],
            "evidence_resolvability_ratio": retrieval["evidence_resolvability_ratio"],
            "unresolvable_evidence_count": retrieval["unresolvable_evidence_count"],
            "missing_metric_source_count": retrieval["missing_metric_source_count"],
            "derived_metric_count": retrieval["derived_metric_count"],
        },
        "document_format": manifest.get("document_format"),
        "accounting_standard": manifest.get("accounting_standard"),
        "counts": counts,
    }


def _write_company_index(output_root: Path, company_key: str, items: list[dict[str, Any]]) -> None:
    company_dir = output_root / "companies" / company_key
    latest = _latest_filing(items)
    company = {
        "schema_version": "sec_company_wiki_v1",
        "market": "US",
        "ticker": latest.get("ticker"),
        "company_id": latest.get("company_id"),
        "company_wiki_id": company_key,
        "company_wiki_path": repo_relative(company_dir),
        "cik": latest.get("cik"),
        "company_name": latest.get("company_name"),
        "latest_filing_id": latest.get("filing_id"),
        "primary_report_id": latest.get("report_id"),
        "latest_fiscal_year": latest.get("fiscal_year"),
        "latest_period_end": latest.get("period_end"),
        "package_count": len(items),
        "report_count": len(items),
        "reports": [
            {
                "report_id": item.get("report_id"),
                "filing_id": item.get("filing_id"),
                "form": item.get("form"),
                "report_type": item.get("report_type"),
                "fiscal_year": item.get("fiscal_year"),
                "period_end": item.get("period_end"),
                "published_at": item.get("published_at"),
                "package_path": item.get("package_path"),
                "parser_result_dir": item.get("parser_result_dir"),
                "parser_result_task_id": item.get("parser_result_task_id"),
                "wiki_report_path": item.get("wiki_report_path"),
                "quality_status": item.get("quality_status"),
                "retrieval_status": item.get("retrieval_status"),
                "wiki_ready": item.get("wiki_ready"),
                "retrieval_issues": item.get("retrieval_issues") or [],
                "full_document_status": item.get("full_document_status"),
                "full_document_ready": item.get("full_document_ready"),
                "full_document_paths": item.get("full_document_paths") or {},
                "wiki_ingestion_status": item.get("wiki_ingestion_status"),
                "wiki_ingestion_ready": item.get("wiki_ingestion_ready"),
            }
            for item in items
        ],
        "updated_at": now_iso(),
    }
    write_json(company_dir / "company.json", company)
    write_json(company_dir / "filings.json", {"schema_version": "sec_company_filings_v1", "ticker": latest.get("ticker"), "count": len(items), "items": items})
    write_json(
        company_dir / "_index.json",
        {
            "schema_version": "sec_company_index_v1",
            "company": "company.json",
            "filings": "filings.json",
            "latest": latest,
            "metrics_latest": {name.removesuffix(".json"): f"metrics/latest/{name}" for name in METRIC_FILES},
            "package_paths": [item["package_path"] for item in items],
        },
    )
    (company_dir / "company.md").write_text(_company_markdown(company, latest), encoding="utf-8")
    for item in items:
        _copy_report_metrics(output_root, company_dir, item)
    _copy_latest_metrics(output_root, company_dir, latest)


def _copy_report_metrics(output_root: Path, company_dir: Path, item: dict[str, Any]) -> None:
    source_dir = _resolve_package_path(output_root, item) / "metrics"
    target_dir = company_dir / "metrics" / "reports" / filing_slug(str(item.get("filing_id") or item.get("accession_number") or "unknown"))
    for name in METRIC_FILES:
        source = source_dir / name
        if source.exists():
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target_dir / name)


def _copy_latest_metrics(output_root: Path, company_dir: Path, latest: dict[str, Any]) -> None:
    source_dir = _resolve_package_path(output_root, latest) / "metrics"
    target_dir = company_dir / "metrics" / "latest"
    for name in METRIC_FILES:
        source = source_dir / name
        if source.exists():
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target_dir / name)


def _write_company_catalog(output_root: Path) -> None:
    companies: list[dict[str, Any]] = []
    for company_json_path in sorted((output_root / "companies").glob("*/company.json")):
        company = read_json(company_json_path, {})
        if not isinstance(company, dict) or not company:
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
                "report_count": company.get("report_count") or company.get("package_count") or len(company.get("reports") or []),
                "status": "ready" if any(report.get("wiki_ready") for report in company.get("reports") or []) else "needs_review",
                "retrieval_status": "ready" if any(report.get("wiki_ready") for report in company.get("reports") or []) else "needs_review",
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
            "generated_at": now_iso(),
        },
    )


def _resolve_package_path(output_root: Path, item: dict[str, Any]) -> Path:
    raw = Path(str(item.get("package_path") or ""))
    if raw.is_absolute():
        return raw
    candidate = REPO_ROOT / raw
    if candidate.exists():
        return candidate
    return output_root / raw


def _latest_filing(items: list[dict[str, Any]]) -> dict[str, Any]:
    non_fail = [item for item in items if str(item.get("quality_status") or "").lower() != "fail"]
    return _sort_filings(non_fail or items)[0]


def _sort_filings(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            str(item.get("period_end") or ""),
            int(item.get("fiscal_year") or 0),
            str(item.get("filing_date") or item.get("published_at") or ""),
            str(item.get("package_path") or ""),
        ),
        reverse=True,
    )


def _quality_summary(packages: list[dict[str, Any]]) -> dict[str, Any]:
    quality_counts: dict[str, int] = {}
    retrieval_status_counts: dict[str, int] = {}
    full_document_status_counts: dict[str, int] = {}
    wiki_ingestion_status_counts: dict[str, int] = {}
    totals = {"sections": 0, "tables": 0, "raw_facts": 0, "metrics": 0, "evidence": 0}
    issue_count = 0
    for item in packages:
        status = str(item.get("quality_status") or "unknown").lower()
        quality_counts[status] = quality_counts.get(status, 0) + 1
        retrieval_status = str(item.get("retrieval_status") or "unknown").lower()
        retrieval_status_counts[retrieval_status] = retrieval_status_counts.get(retrieval_status, 0) + 1
        full_document_status = str(item.get("full_document_status") or "unknown").lower()
        full_document_status_counts[full_document_status] = full_document_status_counts.get(full_document_status, 0) + 1
        wiki_ingestion_status = str(item.get("wiki_ingestion_status") or "unknown").lower()
        wiki_ingestion_status_counts[wiki_ingestion_status] = wiki_ingestion_status_counts.get(wiki_ingestion_status, 0) + 1
        issue_count += len(item.get("retrieval_issues") or [])
        counts = item.get("counts") or {}
        for key in totals:
            totals[key] += int(counts.get(key) or 0)
    return {
        "schema_version": "sec_quality_summary_v1",
        "market": "US",
        "generated_at": now_iso(),
        "company_count": len({item.get("company_wiki_id") for item in packages}),
        "report_count": len(packages),
        "package_count": len(packages),
        "quality_counts": quality_counts,
        "retrieval_status_counts": retrieval_status_counts,
        "full_document_status_counts": full_document_status_counts,
        "wiki_ingestion_status_counts": wiki_ingestion_status_counts,
        "full_document_ready_count": full_document_status_counts.get("ready", 0),
        "full_document_missing_count": full_document_status_counts.get("missing", 0),
        "full_document_partial_count": full_document_status_counts.get("partial", 0),
        "wiki_ingestion_ready_count": wiki_ingestion_status_counts.get("ready", 0),
        "wiki_ready_count": retrieval_status_counts.get("ready", 0),
        "issue_count": issue_count,
        "totals": totals,
    }


def _case_set(case_set_name: str, packages: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "sec_case_set_v1",
        "name": case_set_name,
        "generated_at": now_iso(),
        "count": len(packages),
        "items": packages,
    }


def _market_profile(packages: list[dict[str, Any]]) -> dict[str, Any]:
    forms = sorted({str(item.get("form") or "") for item in packages if item.get("form")})
    return {
        "schema_version": "us_market_profile_v1",
        "market": "US",
        "source": "SEC EDGAR HTML/iXBRL filings",
        "forms": forms,
        "company_id_rule": "<ticker>-<company-slug>",
        "report_id_rule": "<fiscal_year>-<form>-<accession>",
        "primary_statement_scope": "consolidated",
        "dedupe_rule": "ticker + SEC accession; fallback source_sha256",
        "source_artifact_model": "SEC HTML/iXBRL facts + sections + HTML tables",
        "subsidiary_relation_policy": "not_structured_use_full_text_and_table_fallback",
        "accounting_standard": "US GAAP / IFRS for 20-F when present",
        "retrieval_ready_rule": "required SEC sections or facts present, three statements present, six core metrics present, metric evidence resolvable",
        "generated_at": now_iso(),
    }


def _ingestion_manifest(packages: list[dict[str, Any]], *, case_set_name: str) -> dict[str, Any]:
    return {
        "schema_version": "us_ingestion_manifest_v1",
        "market": "US",
        "generated_at": now_iso(),
        "company_count": len({item.get("company_wiki_id") for item in packages}),
        "report_count": len(packages),
        "case_set": f"_meta/{case_set_name}",
        "selection": {
            "forms": sorted({str(item.get("form") or "") for item in packages if item.get("form")}),
            "package_count": len(packages),
            "dedupe_rule": "ticker + SEC accession; fallback source_sha256",
        },
        "rules": {
            "canonical_parser_artifacts": "data/parser-results/us-sec/<task_id>",
            "wiki_ingestion_plan": WIKI_INGESTION_PLAN_PATH,
            "ready_rule": "parser result exists, Wiki mirror exists, mirror hashes match, raw HTML hashes match",
        },
        "status_counts": {
            "wiki_ingestion": _count_by(packages, "wiki_ingestion_status"),
            "full_document": _count_by(packages, "full_document_status"),
            "retrieval": _count_by(packages, "retrieval_status"),
        },
    }


def _full_document_summary(package_dir: Path, quality: dict[str, Any], ingestion_plan: dict[str, Any]) -> dict[str, Any]:
    paths = {
        name: {
            "path": rel,
            "exists": (package_dir / rel).exists(),
        }
        for name, rel in FULL_DOCUMENT_FILES.items()
    }
    existing_count = sum(1 for item in paths.values() if item["exists"])
    if existing_count == len(FULL_DOCUMENT_FILES):
        status = "ready"
    elif existing_count == 0:
        status = "missing"
    else:
        status = "partial"
    wiki_ingestion = summarize_wiki_ingestion_plan(ingestion_plan)
    if wiki_ingestion["status"] in {"ready", "partial", "missing"}:
        status = str(wiki_ingestion["status"])
    full_quality = quality.get("full_document") if isinstance(quality.get("full_document"), dict) else {}
    return {
        "status": status,
        "ready": status == "ready" and bool(wiki_ingestion["ready"]),
        "paths": paths,
        "wiki_ingestion": wiki_ingestion,
        "quality": {
            "dom_node_count": full_quality.get("dom_node_count"),
            "block_count": full_quality.get("block_count"),
            "markdown_chars": full_quality.get("markdown_chars"),
            "table_relation_count": full_quality.get("table_relation_count"),
            "block_source_map_count": full_quality.get("block_source_map_count"),
            "fact_linkage_ratio": full_quality.get("fact_linkage_ratio"),
            "table_linkage_ratio": full_quality.get("table_linkage_ratio"),
        },
    }


def _retrieval_readiness(
    financial_data: dict[str, Any],
    normalized_metrics: dict[str, Any],
    quality: dict[str, Any],
    source_map: dict[str, Any],
) -> dict[str, Any]:
    statements = financial_data.get("statements") if isinstance(financial_data, dict) else []
    statement_types = {
        str(statement.get("statement_type") or "")
        for statement in statements or []
        if isinstance(statement, dict) and statement.get("items")
    }
    required_statement_status = {
        statement: "present" if statement in statement_types else "missing"
        for statement in REQUIRED_STATEMENTS
    }
    metrics = normalized_metrics.get("metrics") if isinstance(normalized_metrics, dict) else []
    metric_names = {
        str(item.get("canonical_name") or "")
        for item in metrics or []
        if isinstance(item, dict)
    }
    core_metric_status = {
        metric: "present" if metric in metric_names else "missing"
        for metric in REQUIRED_RETRIEVAL_METRICS
    }
    metric_source_summary = _metric_source_summary(financial_data)
    source_map_summary = _source_map_summary(source_map)
    issues: list[dict[str, Any]] = []
    missing_statements = [key for key, value in required_statement_status.items() if value != "present"]
    if missing_statements:
        issues.append({"type": "missing_required_statements", "items": missing_statements})
    missing_metrics = [key for key, value in core_metric_status.items() if value != "present"]
    if missing_metrics:
        issues.append({"type": "missing_core_metrics", "items": missing_metrics})
    if metric_source_summary["missing_metric_source_count"]:
        issues.append({"type": "missing_metric_sources", "count": metric_source_summary["missing_metric_source_count"]})
    if metric_source_summary["unresolvable_metric_source_count"]:
        issues.append({"type": "unresolvable_metric_sources", "count": metric_source_summary["unresolvable_metric_source_count"]})
    parser_warnings = quality.get("parser_warnings") if isinstance(quality.get("parser_warnings"), list) else []
    blocking_parser_warnings = [warning for warning in parser_warnings if _is_blocking_parser_warning(warning)]
    if blocking_parser_warnings:
        issues.append({"type": "parser_warnings", "count": len(blocking_parser_warnings)})
    wiki_ready = not issues
    return {
        "retrieval_status": "ready" if wiki_ready else "needs_review",
        "wiki_ready": wiki_ready,
        "issues": issues,
        "required_statement_status": required_statement_status,
        "core_metric_status": core_metric_status,
        "missing_metric_source_count": metric_source_summary["missing_metric_source_count"],
        "unresolvable_metric_source_count": metric_source_summary["unresolvable_metric_source_count"],
        "unresolvable_evidence_count": source_map_summary["unresolvable_source_map_entry_count"],
        "evidence_resolvability_ratio": source_map_summary["evidence_resolvability_ratio"],
        "derived_metric_count": sum(
            1
            for item in metrics or []
            if isinstance(item, dict) and isinstance(item.get("raw"), dict) and item["raw"].get("derived")
        ),
    }


def _is_blocking_parser_warning(warning: Any) -> bool:
    text = str(warning or "")
    if text.startswith("full_document: HTML table count ") and "differs from tables/table_index.json" in text:
        return False
    return True


def _metric_source_summary(financial_data: dict[str, Any]) -> dict[str, int]:
    missing = 0
    unresolvable = 0
    for item in _iter_financial_items(financial_data):
        values = item.get("values") if isinstance(item.get("values"), dict) else {}
        sources = item.get("sources") if isinstance(item.get("sources"), dict) else {}
        for period_key in values:
            evidence = sources.get(period_key)
            if not isinstance(evidence, dict) or not evidence:
                missing += 1
            elif not _evidence_resolvable(evidence):
                unresolvable += 1
    return {"missing_metric_source_count": missing, "unresolvable_metric_source_count": unresolvable}


def _source_map_summary(source_map: dict[str, Any]) -> dict[str, Any]:
    entries = source_map.get("entries") if isinstance(source_map, dict) else []
    entries = entries if isinstance(entries, list) else []
    unresolvable = sum(1 for entry in entries if not (isinstance(entry, dict) and _evidence_resolvable(entry)))
    denominator = len(entries)
    return {
        "source_map_entry_count": denominator,
        "unresolvable_source_map_entry_count": unresolvable,
        "evidence_resolvability_ratio": round((denominator - unresolvable) / denominator, 6) if denominator else None,
    }


def _iter_financial_items(financial_data: dict[str, Any]):
    for statement in financial_data.get("statements") or []:
        if not isinstance(statement, dict):
            continue
        for item in statement.get("items") or []:
            if isinstance(item, dict):
                yield item
    for key in ("key_metrics", "operating_metrics"):
        for item in financial_data.get(key) or []:
            if isinstance(item, dict):
                yield item


def _evidence_resolvable(evidence: dict[str, Any]) -> bool:
    if not isinstance(evidence, dict) or not evidence:
        return False
    if evidence.get("source_type") == "derived_reported_metric" and evidence.get("quote_text"):
        return True
    if evidence.get("url") and (evidence.get("anchor") or evidence.get("html_anchor") or evidence.get("xbrl_tag") or evidence.get("xpath")):
        return True
    if evidence.get("local_path") and (evidence.get("quote_text") or evidence.get("xbrl_tag") or evidence.get("html_anchor")):
        return True
    if evidence.get("target"):
        return True
    return False


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown").lower()
        counts[value] = counts.get(value, 0) + 1
    return counts


def _count(quality: dict[str, Any], key: str, summary_key: str | None = None) -> int:
    if quality.get(key) is not None:
        return int(quality.get(key) or 0)
    summary = quality.get("summary") if isinstance(quality.get("summary"), dict) else {}
    return int(summary.get(summary_key or key) or 0)


def repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path.resolve())


def filing_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "unknown"


def _company_markdown(company: dict[str, Any], latest: dict[str, Any]) -> str:
    return (
        f"# {company.get('ticker')} {company.get('company_name') or ''}\n\n"
        f"- Market: US\n"
        f"- CIK: `{company.get('cik')}`\n"
        f"- Latest filing: `{latest.get('form')}` `{latest.get('accession_number')}`\n"
        f"- Latest period end: `{latest.get('period_end')}`\n"
        f"- Quality: `{latest.get('quality_status')}`\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build US SEC company wiki indexes from evidence packages.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--forms", default="10-K")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--case-set-name", default="case_set_50_us_10k.json")
    args = parser.parse_args()
    summary = build_wiki_index(
        args.output_root,
        forms=parse_csv_set(args.forms),
        tickers=parse_csv_set(args.tickers),
        case_set_name=args.case_set_name,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
