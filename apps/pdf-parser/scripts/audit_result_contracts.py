#!/usr/bin/env python3
"""Audit PDF parser result contracts by market."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BASE_DIR.parents[1]
sys.path.insert(0, str(BASE_DIR))

import pdf_parser_result_manifest_service as manifests  # noqa: E402


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def iter_result_dirs(results_dir: Path) -> list[Path]:
    return sorted(path for path in results_dir.iterdir() if path.is_dir())


def artifact_exists(artifact_manifest: dict[str, Any], name: str) -> bool:
    artifact = (artifact_manifest.get("artifacts") or {}).get(name) or {}
    return bool(artifact.get("exists"))


def market_financial_match(market: str, financial_data: dict[str, Any], financial_checks: dict[str, Any]) -> bool:
    if market == "CN":
        return True
    return (
        str(financial_data.get("market") or "").upper() == market
        and str(financial_checks.get("market") or "").upper() == market
    )


def expected_profile_present(market: str, financial_data: dict[str, Any], financial_checks: dict[str, Any]) -> bool:
    if market in {"HK", "JP", "KR", "EU"}:
        return bool(financial_data.get("profile_rule_version") and financial_checks.get("profile_rule_version"))
    return True


def audit_one(result_dir: Path) -> dict[str, Any]:
    metadata = read_json(result_dir / "metadata.json", {})
    artifact_manifest = read_json(result_dir / "artifact_manifest.json", {})
    hash_manifest = read_json(result_dir / "hash_manifest.json", {})
    financial_data = read_json(result_dir / "financial_data.json", {})
    financial_checks = read_json(result_dir / "financial_checks.json", {})
    quality_report = read_json(result_dir / "quality_report.json", {})
    table_relations = read_json(result_dir / "table_relations.json", {})
    document_full = read_json(result_dir / "document_full.json", {})
    enhanced = read_json(result_dir / "content_list_enhanced.json", {})
    table_index = read_json(result_dir / "table_index.json", [])

    market = str(metadata.get("market") or "").upper()
    missing = list((artifact_manifest.get("core") or {}).get("missing") or [])
    invalid_json = list((artifact_manifest.get("core") or {}).get("invalid_json") or [])
    required_missing = [name for name in manifests.REQUIRED_ARTIFACTS if not artifact_exists(artifact_manifest, name)]
    relation_count = len(table_relations.get("relations") or []) if isinstance(table_relations, dict) else 0
    relation_candidate_count = int(table_relations.get("candidate_table_count") or table_relations.get("physical_table_count") or 0) if isinstance(table_relations, dict) else 0
    enhanced_table_count = int(enhanced.get("table_count") or len(enhanced.get("tables") or []) or 0) if isinstance(enhanced, dict) else 0
    enhanced_page_count = len(enhanced.get("pages") or []) if isinstance(enhanced, dict) else 0
    table_index_count = len(table_index) if isinstance(table_index, list) else 0
    statement_count = len(financial_data.get("statements") or []) if isinstance(financial_data, dict) else 0
    key_metric_count = len(financial_data.get("key_metrics") or []) if isinstance(financial_data, dict) else 0
    checks_summary = financial_checks.get("summary") if isinstance(financial_checks, dict) else {}
    checks_fail = int((checks_summary or {}).get("fail") or 0)
    markdown_payload = document_full.get("markdown") if isinstance(document_full.get("markdown"), dict) else {}
    document_full_markdown_chars = len(str(markdown_payload.get("content") or ""))
    result_complete_chars = 0
    try:
        result_complete_chars = len((result_dir / "result_complete.md").read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        result_complete_chars = 0

    checks = {
        "metadata_present": bool(metadata),
        "artifact_manifest_present": bool(artifact_manifest),
        "hash_manifest_present": bool(hash_manifest),
        "artifact_manifest_ready": (artifact_manifest.get("core") or {}).get("ready") is True,
        "metadata_market_present": market in manifests.MARKETS,
        "required_artifacts_present": not required_missing and not missing,
        "json_artifacts_valid": not invalid_json,
        "bundle_hash_present": bool((artifact_manifest.get("core") or {}).get("bundle_sha256")),
        "market_financial_match": market_financial_match(market, financial_data, financial_checks),
        "market_profile_version_present": expected_profile_present(market, financial_data, financial_checks),
        "table_relations_schema_present": table_relations.get("schema_version") == "document_table_relations_v1",
        "financial_schema_present": bool(financial_data.get("schema_version") and financial_checks.get("schema_version")),
        "quality_schema_present": bool(quality_report.get("schema_version")),
        "result_complete_content_present": result_complete_chars > 1000,
        "document_full_markdown_present": document_full_markdown_chars > 1000,
        "content_list_enhanced_tables_present": enhanced_table_count > 0,
        "content_list_enhanced_pages_present": enhanced_page_count > 0,
        "table_index_present": table_index_count > 0,
        "table_relation_candidates_present": relation_candidate_count > 0,
        "financial_statements_present": statement_count > 0,
    }
    aligned = all(checks.values())
    return {
        "task_id": result_dir.name,
        "market": market or "UNKNOWN",
        "aligned": aligned,
        "checks": checks,
        "missing": sorted(set(missing + required_missing)),
        "invalid_json": invalid_json,
        "stats": {
            "relation_count": relation_count,
            "relation_candidate_count": relation_candidate_count,
            "enhanced_table_count": enhanced_table_count,
            "enhanced_page_count": enhanced_page_count,
            "table_index_count": table_index_count,
            "result_complete_chars": result_complete_chars,
            "document_full_markdown_chars": document_full_markdown_chars,
            "statement_count": statement_count,
            "key_metric_count": key_metric_count,
            "financial_check_fail_count": checks_fail,
            "financial_overall_status": financial_checks.get("overall_status") if isinstance(financial_checks, dict) else None,
            "quality_financial_overall_status": quality_report.get("financial_overall_status") if isinstance(quality_report, dict) else None,
        },
    }


def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_market: dict[str, Counter] = defaultdict(Counter)
    stats_by_market: dict[str, Counter] = defaultdict(Counter)
    for item in items:
        market = item["market"]
        by_market[market]["total"] += 1
        by_market[market]["aligned" if item["aligned"] else "not_aligned"] += 1
        for check, ok in item["checks"].items():
            if ok:
                by_market[market][f"check:{check}:pass"] += 1
            else:
                by_market[market][f"check:{check}:fail"] += 1
        stats = item.get("stats") or {}
        stats_by_market[market]["relation_count"] += int(stats.get("relation_count") or 0)
        stats_by_market[market]["relation_candidate_count"] += int(stats.get("relation_candidate_count") or 0)
        stats_by_market[market]["enhanced_table_count"] += int(stats.get("enhanced_table_count") or 0)
        stats_by_market[market]["enhanced_page_count"] += int(stats.get("enhanced_page_count") or 0)
        stats_by_market[market]["statement_count"] += int(stats.get("statement_count") or 0)
        stats_by_market[market]["key_metric_count"] += int(stats.get("key_metric_count") or 0)
        stats_by_market[market]["financial_check_fail_count"] += int(stats.get("financial_check_fail_count") or 0)
    return {
        "total": len(items),
        "aligned": sum(1 for item in items if item["aligned"]),
        "not_aligned": sum(1 for item in items if not item["aligned"]),
        "by_market": {market: dict(counter) for market, counter in sorted(by_market.items())},
        "stats_by_market": {market: dict(counter) for market, counter in sorted(stats_by_market.items())},
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# PDF Parser Result Contract Audit",
        "",
        f"- Total: `{report['summary']['total']}`",
        f"- Aligned: `{report['summary']['aligned']}`",
        f"- Not aligned: `{report['summary']['not_aligned']}`",
        "",
        "## By Market",
        "",
        "| Market | Total | Aligned | Not aligned | Pages | Tables | Relation candidates | Relations | Statements | Key metrics | Check fails |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    stats_by_market = report["summary"].get("stats_by_market") or {}
    for market, row in (report["summary"].get("by_market") or {}).items():
        stats = stats_by_market.get(market) or {}
        lines.append(
            "| {market} | {total} | {aligned} | {not_aligned} | {pages} | {tables} | {relation_candidates} | {relations} | {statements} | {metrics} | {fails} |".format(
                market=market,
                total=row.get("total", 0),
                aligned=row.get("aligned", 0),
                not_aligned=row.get("not_aligned", 0),
                pages=stats.get("enhanced_page_count", 0),
                tables=stats.get("enhanced_table_count", 0),
                relation_candidates=stats.get("relation_candidate_count", 0),
                relations=stats.get("relation_count", 0),
                statements=stats.get("statement_count", 0),
                metrics=stats.get("key_metric_count", 0),
                fails=stats.get("financial_check_fail_count", 0),
            )
        )
    failures = [item for item in report["items"] if not item["aligned"]]
    if failures:
        lines.extend(["", "## Failures", ""])
        for item in failures[:100]:
            failed_checks = [name for name, ok in item["checks"].items() if not ok]
            lines.append(f"- `{item['task_id']}` `{item['market']}`: {', '.join(failed_checks)}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", default=str(REPO_ROOT / "data/pdf-parser/results"))
    parser.add_argument("--markets", default="", help="Comma-separated market filter, e.g. HK,EU,JP,KR.")
    parser.add_argument("--json-output", default="")
    parser.add_argument("--markdown-output", default="")
    args = parser.parse_args(argv)

    market_filter = {item.strip().upper() for item in args.markets.split(",") if item.strip()}
    items = []
    for result_dir in iter_result_dirs(Path(args.results_dir)):
        item = audit_one(result_dir)
        if market_filter and item["market"] not in market_filter:
            continue
        items.append(item)
    report = {"summary": summarize(items), "items": items}

    if args.json_output:
        output = Path(args.json_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.markdown_output:
        output = Path(args.markdown_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["not_aligned"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
