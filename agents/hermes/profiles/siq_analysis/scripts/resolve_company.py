#!/usr/bin/env python3
"""Resolve a SIQ company and report from the wiki catalog.

This script is intentionally deterministic. SIQ_analysis should use it
before reading company files, instead of constructing wiki paths from memory or
free-form company names.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any


DEFAULT_WIKI_DIR = Path(
    os.environ.get("SIQ_WIKI_ROOT")
    or os.environ.get("WIKI_DIR")
    or Path(__file__).resolve().parents[5] / "data" / "wiki"
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def norm(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[\s（）()\-_/]", "", text)


def catalog_path(wiki_dir: Path) -> Path:
    return wiki_dir / "_meta" / "company_catalog.json"


def load_catalog(wiki_dir: Path) -> dict[str, Any]:
    path = catalog_path(wiki_dir)
    if not path.exists():
        raise FileNotFoundError(f"company_catalog_not_found:{path}")
    return load_json(path)


def company_match_values(company: dict[str, Any]) -> set[str]:
    values = {
        company.get("company_id"),
        company.get("stock_code"),
        company.get("company_short_name"),
        company.get("company_full_name"),
    }
    aliases = company.get("aliases")
    if isinstance(aliases, list):
        values.update(aliases)
    return {norm(item) for item in values if item}


def find_company(query: str, catalog: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    needle = norm(query)
    exact: list[dict[str, Any]] = []
    fuzzy: list[dict[str, Any]] = []
    for company in catalog.get("companies", []) or []:
        if not isinstance(company, dict):
            continue
        values = company_match_values(company)
        if needle in values:
            exact.append(company)
            continue
        if needle and any(needle in value or value in needle for value in values if value):
            fuzzy.append(company)
    candidates = exact or fuzzy
    if len(candidates) == 1:
        return candidates[0], candidates
    return None, candidates


def report_for_year(company_json: dict[str, Any], year: int | None) -> dict[str, Any]:
    reports = company_json.get("reports")
    if isinstance(reports, list) and reports:
        if year is not None:
            for report in reports:
                if isinstance(report, dict) and int(report.get("report_year") or 0) == year:
                    return report
        for report in reports:
            if isinstance(report, dict) and report.get("report_id") == company_json.get("primary_report_id"):
                return report
        first = reports[0]
        if isinstance(first, dict):
            return first
    return {}


def rel_path(company_dir: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else company_dir / path


def path_payload(path: Path | None) -> dict[str, Any]:
    if not path:
        return {"path": None, "exists": False}
    return {"path": str(path), "exists": path.exists()}


def resolve_company(query: str, year: int | None = 2025, wiki_dir: Path = DEFAULT_WIKI_DIR) -> dict[str, Any]:
    catalog = load_catalog(wiki_dir)
    company, candidates = find_company(query, catalog)
    if not company:
        return {
            "ok": False,
            "error": "company_not_found_or_ambiguous",
            "query": query,
            "candidate_count": len(candidates),
            "candidates": [
                {
                    "company_id": item.get("company_id"),
                    "stock_code": item.get("stock_code"),
                    "company_short_name": item.get("company_short_name"),
                    "company_path": item.get("company_path"),
                }
                for item in candidates[:20]
            ],
            "wiki_dir": str(wiki_dir),
            "catalog": str(catalog_path(wiki_dir)),
        }

    company_dir = wiki_dir / str(company["company_path"])
    company_json_path = company_dir / "company.json"
    company_json = load_json(company_json_path) if company_json_path.exists() else {}
    report = report_for_year(company_json, year)
    report_id = report.get("report_id") or company.get("primary_report_id")
    report_dir = company_dir / "reports" / str(report_id) if report_id else None

    semantic_dir = company_dir / "semantic"
    llm_semantic_dir = semantic_dir / "llm" / f"{year}-annual" if year else None
    analysis_dir = company_dir / "analysis"

    paths = {
        "company_dir": path_payload(company_dir),
        "company_json": path_payload(company_json_path),
        "analysis_dir": path_payload(analysis_dir),
        "metrics_three_statements": path_payload(company_dir / "metrics" / "three_statements.json"),
        "metrics_key_metrics": path_payload(company_dir / "metrics" / "key_metrics.json"),
        "metrics_validation": path_payload(company_dir / "metrics" / "validation.json"),
        "evidence_index": path_payload(company_dir / "evidence" / "evidence_index.json"),
        "pdf_refs": path_payload(company_dir / "evidence" / "pdf_refs.json"),
        "semantic_facts": path_payload(semantic_dir / "facts.json"),
        "semantic_claims": path_payload(semantic_dir / "claims.json"),
        "semantic_relations": path_payload(semantic_dir / "relations.json"),
        "semantic_segments": path_payload(semantic_dir / "segments.json"),
        "semantic_evidence": path_payload(semantic_dir / "evidence_semantic.json"),
        "semantic_retrieval_index": path_payload(semantic_dir / "retrieval_index.json"),
        "llm_business_profile": path_payload(llm_semantic_dir / "business_profile.json" if llm_semantic_dir else None),
        "llm_risks": path_payload(llm_semantic_dir / "risks.json" if llm_semantic_dir else None),
        "llm_events": path_payload(llm_semantic_dir / "events.json" if llm_semantic_dir else None),
        "llm_review_queue": path_payload(llm_semantic_dir / "review_queue.json" if llm_semantic_dir else None),
        "report_dir": path_payload(report_dir),
        "report_md": path_payload(rel_path(company_dir, report.get("report_md"))),
        "report_json": path_payload(rel_path(company_dir, report.get("report_json"))),
        "document_full": path_payload(rel_path(company_dir, report.get("document_full"))),
        "artifact_manifest": path_payload(rel_path(company_dir, report.get("artifact_manifest"))),
    }

    return {
        "ok": True,
        "query": query,
        "wiki_dir": str(wiki_dir),
        "catalog": str(catalog_path(wiki_dir)),
        "company": {
            "company_id": company_json.get("company_id") or company.get("company_id"),
            "stock_code": company_json.get("stock_code") or company.get("stock_code"),
            "exchange": company_json.get("exchange") or company.get("exchange"),
            "company_short_name": company_json.get("company_short_name") or company.get("company_short_name"),
            "company_full_name": company_json.get("company_full_name") or company.get("company_full_name"),
            "industry_sw1": company.get("industry_sw1", ""),
            "industry_sw2": company.get("industry_sw2", ""),
            "industry_sw3": company.get("industry_sw3", ""),
            "company_path": company.get("company_path"),
        },
        "report": {
            "report_id": report_id,
            "report_year": report.get("report_year") or year,
            "report_kind": report.get("report_kind"),
            "status": report.get("status"),
            "task_id": report.get("task_id"),
            "source_filename": report.get("source_filename"),
        },
        "paths": paths,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", required=True, help="股票代码、company_id、公司简称或别名")
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--wiki-dir", type=Path, default=DEFAULT_WIKI_DIR)
    parser.add_argument("--write-json", type=Path)
    args = parser.parse_args()

    result = resolve_company(args.company, year=args.year, wiki_dir=args.wiki_dir)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.write_json:
        args.write_json.parent.mkdir(parents=True, exist_ok=True)
        args.write_json.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
