#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import build_sec_wiki_index as indexer
import discover_sec_downloaded_cases as discovery
import sec_evidence_lib

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DOWNLOADS_ROOT = REPO_ROOT / "data" / "market-report-finder" / "downloads" / "US"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "data" / "wiki" / "us"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def parse_csv_set(value: str | None, *, upper: bool = True) -> set[str] | None:
    if not value:
        return None
    items = {item.strip() for item in value.split(",") if item.strip()}
    if upper:
        items = {item.upper() for item in items}
    return items or None


def build_sec_wiki(
    downloads_root: Path,
    output_root: Path,
    *,
    forms: set[str] | None = None,
    tickers: set[str] | None = None,
    limit: int = 0,
    force: bool = False,
    incremental: bool = False,
    continue_on_error: bool = False,
) -> dict[str, Any]:
    rows = discovery.scan_downloads(downloads_root, forms=forms, tickers=tickers, limit=limit)
    downloads_index = discovery.write_downloads_index(rows, output_root)
    built: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for row in rows:
        expected = _expected_package_dir(output_root, row)
        if incremental and not force and expected and (expected / "manifest.json").exists():
            skipped.append({**row, "package_path": str(expected), "reason": "exists"})
            continue
        try:
            metadata_path = Path(row["metadata_path"]) if row.get("metadata_path") else None
            package_dir = sec_evidence_lib.write_evidence_package(Path(row["source_path"]), output_root, metadata_path, force=force)
            built.append({**row, "package_path": str(package_dir)})
        except Exception as exc:
            failure = {**row, "error": str(exc)}
            failed.append(failure)
            if not continue_on_error:
                raise
    index_summary = indexer.build_wiki_index(output_root, forms=forms, tickers=tickers)
    return {
        "schema_version": "sec_wiki_build_report_v1",
        "generated_at": now_iso(),
        "downloads_root": str(downloads_root),
        "output_root": str(output_root),
        "downloads_index": str(downloads_index),
        "discovered_count": len(rows),
        "built_count": len(built),
        "skipped_count": len(skipped),
        "failed_count": len(failed),
        "built": built,
        "skipped": skipped,
        "failed": failed,
        "index": index_summary,
    }


def _expected_package_dir(output_root: Path, row: dict[str, Any]) -> Path | None:
    ticker = row.get("ticker")
    fiscal_year = row.get("fiscal_year") or "unknown"
    form = row.get("form")
    accession = row.get("accession_number")
    if not ticker or not form or not accession:
        return None
    company_dir = sec_evidence_lib.company_wiki_dir_name(ticker, row.get("company_name") or ticker)
    report_id = sec_evidence_lib.us_report_id(fiscal_year, form, accession)
    return output_root / "companies" / company_dir / "reports" / report_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Build US SEC wiki packages and company indexes from downloaded filings.")
    parser.add_argument("--downloads-root", type=Path, default=DEFAULT_DOWNLOADS_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--forms", default="10-K")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args()
    report = build_sec_wiki(
        args.downloads_root,
        args.output_root,
        forms=parse_csv_set(args.forms),
        tickers=parse_csv_set(args.tickers),
        limit=args.limit,
        force=args.force,
        incremental=args.incremental,
        continue_on_error=args.continue_on_error,
    )
    if args.report:
        write_json(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
