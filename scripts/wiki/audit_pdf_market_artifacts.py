#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WIKI_ROOT = REPO_ROOT / "data" / "wiki"
PDF_MARKETS = ("HK", "EU", "JP", "KR")

ROOT_COMPAT_REQUIRED = (
    "report.md",
    "document_full.json",
    "artifact_manifest.json",
)

PARSER_ARCHIVE_REQUIRED = (
    "parser/result_complete.md",
    "parser/document_full.json",
    "parser/table_index.json",
    "parser/table_relations.json",
    "parser/financial_data.json",
    "parser/financial_checks.json",
    "parser/quality_report.json",
)

PACKAGE_REQUIRED = (
    "manifest.json",
    "README.md",
    "raw/report.pdf",
    "tables/table_index.json",
    "tables/table_relations.json",
    "metrics/financial_data.json",
    "metrics/financial_checks.json",
    "qa/quality_report.json",
    "qa/source_map.json",
)

STRONG_RECOMMENDED = (
    "parser/result.md",
    "parser/content_list.json",
    "parser/content_list_enhanced.json",
    "parser/table_relations.json",
    "metrics/normalized_metrics.json",
    "metrics/load_plan.json",
    "qa/extraction_warnings.json",
)

COMPANY_WORKSPACE_REQUIRED = (
    "company.json",
    "company.md",
    "_index.json",
    "metrics/latest/financial_data.json",
    "metrics/latest/financial_checks.json",
    "evidence/evidence_index.json",
    "semantic/retrieval_index.json",
    "graph/graph_index.json",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return {} if default is None else default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {} if default is None else default


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def exists_all(base: Path, paths: tuple[str, ...]) -> tuple[list[str], list[str]]:
    present: list[str] = []
    missing: list[str] = []
    for item in paths:
        if (base / item).exists():
            present.append(item)
        else:
            missing.append(item)
    return present, missing


def manifest_local_source_exists(package_dir: Path, manifest: dict[str, Any]) -> bool:
    raw = str(manifest.get("local_source_path") or "").strip()
    if not raw:
        return False
    candidate = package_dir / raw
    return candidate.exists() and candidate.is_file()


def package_dirs(wiki_root: Path, market: str) -> list[Path]:
    market_root = wiki_root / market.lower() / "companies"
    if not market_root.exists():
        return []
    return sorted(path.parent for path in market_root.glob("*/reports/*/manifest.json"))


def _status(
    *,
    missing_root: list[str],
    missing_parser: list[str],
    missing_package: list[str],
    missing_company: list[str],
) -> str:
    if not missing_root and not missing_parser and not missing_package and not missing_company:
        return "A_complete_pdf_wiki_archive"
    if not missing_parser and not missing_package and missing_root and not missing_company:
        return "B_missing_root_compat_only"
    if missing_package:
        return "D_missing_financial_evidence_or_table_layer"
    if missing_parser:
        return "C_missing_parser_archive"
    return "B_missing_company_workspace"


def audit_package(package_dir: Path, market: str) -> dict[str, Any]:
    company_dir = package_dir.parents[1]
    manifest = read_json(package_dir / "manifest.json", {})
    _, missing_root = exists_all(package_dir, ROOT_COMPAT_REQUIRED)
    _, missing_parser = exists_all(package_dir, PARSER_ARCHIVE_REQUIRED)
    _, missing_package = exists_all(package_dir, PACKAGE_REQUIRED)
    _, missing_recommended = exists_all(package_dir, STRONG_RECOMMENDED)
    _, missing_company = exists_all(company_dir, COMPANY_WORKSPACE_REQUIRED)

    if "raw/report.pdf" in missing_package and manifest_local_source_exists(package_dir, manifest):
        missing_package = [item for item in missing_package if item != "raw/report.pdf"]

    has_document_full = (package_dir / "document_full.json").exists() or (package_dir / "parser" / "document_full.json").exists()
    has_report_markdown = (package_dir / "report.md").exists() or (package_dir / "parser" / "result_complete.md").exists()
    has_table_relations = (package_dir / "tables" / "table_relations.json").exists() or (package_dir / "parser" / "table_relations.json").exists()
    has_financial_layer = (package_dir / "metrics" / "financial_data.json").exists() and (package_dir / "metrics" / "financial_checks.json").exists()
    has_evidence_layer = (package_dir / "qa" / "quality_report.json").exists() and (package_dir / "qa" / "source_map.json").exists()
    has_table_index = (package_dir / "tables" / "table_index.json").exists() or (package_dir / "parser" / "table_index.json").exists()
    can_postgres_import = has_financial_layer and has_evidence_layer
    can_basic_wiki = has_document_full and has_report_markdown and has_financial_layer and has_evidence_layer and has_table_index
    can_note_relation_extract = has_document_full and has_table_relations
    can_agent_deep_research = can_basic_wiki and can_note_relation_extract and not missing_root and not missing_parser

    status = _status(
        missing_root=missing_root,
        missing_parser=missing_parser,
        missing_package=missing_package,
        missing_company=missing_company,
    )
    return {
        "market": market.upper(),
        "status": status,
        "package_path": rel(package_dir),
        "company_path": rel(company_dir),
        "company_wiki_id": manifest.get("company_wiki_id") or company_dir.name,
        "report_id": manifest.get("report_id") or package_dir.name,
        "filing_id": manifest.get("filing_id"),
        "ticker": manifest.get("ticker") or manifest.get("stock_code"),
        "company_name": manifest.get("company_name") or manifest.get("company_full_name"),
        "quality_status": manifest.get("quality_status"),
        "parser_version": manifest.get("parser_version"),
        "rules_version": manifest.get("rules_version"),
        "missing": {
            "root_compat": missing_root,
            "parser_archive": missing_parser,
            "package_required": missing_package,
            "company_workspace": missing_company,
            "strong_recommended": missing_recommended,
        },
        "capabilities": {
            "can_basic_wiki": can_basic_wiki,
            "can_postgres_import": can_postgres_import,
            "can_note_relation_extract": can_note_relation_extract,
            "can_agent_deep_research": can_agent_deep_research,
        },
    }


def audit_markets(wiki_root: Path, markets: list[str], *, limit: int = 0) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for market in markets:
        dirs = package_dirs(wiki_root, market)
        if limit:
            dirs = dirs[:limit]
        for package_dir in dirs:
            items.append(audit_package(package_dir, market.upper()))

    status_counts = Counter(item["status"] for item in items)
    market_counts: dict[str, dict[str, int]] = {}
    for item in items:
        market_counts.setdefault(item["market"], {})
        market_counts[item["market"]][item["status"]] = market_counts[item["market"]].get(item["status"], 0) + 1
    capability_counts = {
        name: sum(1 for item in items if item["capabilities"].get(name))
        for name in ("can_basic_wiki", "can_postgres_import", "can_note_relation_extract", "can_agent_deep_research")
    }
    return {
        "schema_version": "pdf_market_artifact_audit_v1",
        "generated_at": now_iso(),
        "wiki_root": rel(wiki_root),
        "markets": markets,
        "package_count": len(items),
        "status_counts": dict(sorted(status_counts.items())),
        "market_status_counts": {market: dict(sorted(counts.items())) for market, counts in sorted(market_counts.items())},
        "capability_counts": capability_counts,
        "items": items,
    }


def markdown_report(payload: dict[str, Any]) -> str:
    lines = [
        "# PDF Market Artifact Audit",
        "",
        f"- Generated at: `{payload['generated_at']}`",
        f"- Wiki root: `{payload['wiki_root']}`",
        f"- Markets: `{', '.join(payload['markets'])}`",
        f"- Packages: `{payload['package_count']}`",
        "",
        "## Status Counts",
        "",
        "| Status | Count |",
        "| --- | ---: |",
    ]
    for status, count in payload["status_counts"].items():
        lines.append(f"| `{status}` | {count} |")
    lines.extend(["", "## Capability Counts", "", "| Capability | Count |", "| --- | ---: |"])
    for name, count in payload["capability_counts"].items():
        lines.append(f"| `{name}` | {count} |")
    lines.extend(["", "## Incomplete Packages", "", "| Market | Status | Ticker | Report | Missing highlights |", "| --- | --- | --- | --- | --- |"])
    for item in payload["items"]:
        if item["status"] == "A_complete_pdf_wiki_archive":
            continue
        missing = item["missing"]
        highlights = []
        for key in ("root_compat", "parser_archive", "package_required", "company_workspace"):
            values = missing.get(key) or []
            if values:
                highlights.append(f"{key}: {', '.join(values[:4])}")
        lines.append(
            "| {market} | `{status}` | `{ticker}` | `{report}` | {missing} |".format(
                market=item["market"],
                status=item["status"],
                ticker=item.get("ticker") or "",
                report=item.get("report_id") or "",
                missing="<br>".join(highlights) if highlights else "",
            )
        )
    lines.append("")
    return "\n".join(lines)


def parse_markets(value: str) -> list[str]:
    if not value.strip():
        return list(PDF_MARKETS)
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit PDF market Wiki packages against the A-share-aligned PDF archive contract.")
    parser.add_argument("--wiki-root", type=Path, default=DEFAULT_WIKI_ROOT)
    parser.add_argument("--markets", default=",".join(PDF_MARKETS), help="Comma-separated markets, default HK,EU,JP,KR")
    parser.add_argument("--limit", type=int, default=0, help="Limit packages per market for smoke checks")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--fail-on-incomplete", action="store_true")
    args = parser.parse_args()

    payload = audit_markets(args.wiki_root, parse_markets(args.markets), limit=args.limit)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(text + "\n", encoding="utf-8")
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(markdown_report(payload), encoding="utf-8")
    print(text)
    if args.fail_on_incomplete and payload["status_counts"].get("A_complete_pdf_wiki_archive", 0) != payload["package_count"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
