#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "data" / "wiki" / "us_sec"
METRIC_FILES = ("financial_data.json", "financial_checks.json", "normalized_metrics.json")


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


def build_wiki_index(
    output_root: Path,
    *,
    forms: set[str] | None = None,
    tickers: set[str] | None = None,
    case_set_name: str = "case_set_50_us_10k.json",
) -> dict[str, Any]:
    output_root = output_root.resolve()
    packages = discover_packages(output_root, forms=forms, tickers=tickers)
    by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in packages:
        by_ticker[str(item.get("ticker") or "UNKNOWN")].append(item)

    for ticker, items in sorted(by_ticker.items()):
        _write_company_index(output_root, ticker, _sort_filings(items))

    package_index_path = output_root / "_meta" / "package_index.json"
    quality_summary_path = output_root / "_meta" / "quality_summary.json"
    case_set_path = output_root / case_set_name
    write_json(package_index_path, {"schema_version": "sec_package_index_v1", "generated_at": now_iso(), "count": len(packages), "items": packages})
    quality_summary = _quality_summary(packages)
    write_json(quality_summary_path, quality_summary)
    write_json(case_set_path, _case_set(case_set_name, packages))
    return {
        "schema_version": "sec_wiki_index_build_summary_v1",
        "generated_at": now_iso(),
        "output_root": str(output_root),
        "package_count": len(packages),
        "company_count": len(by_ticker),
        "paths": {
            "package_index": str(package_index_path),
            "quality_summary": str(quality_summary_path),
            "case_set": str(case_set_path),
        },
        "quality_counts": quality_summary["quality_counts"],
    }


def discover_packages(output_root: Path, *, forms: set[str] | None = None, tickers: set[str] | None = None) -> list[dict[str, Any]]:
    form_filter = {item.upper() for item in forms} if forms else None
    ticker_filter = {item.upper() for item in tickers} if tickers else None
    items: list[dict[str, Any]] = []
    for manifest_path in sorted(output_root.glob("*/*/*/manifest.json")):
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
        items.append(_package_summary(output_root, package_dir, manifest))
    return _sort_filings(items)


def _package_summary(output_root: Path, package_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    quality = read_json(package_dir / "qa" / "quality_report.json", {})
    metrics = read_json(package_dir / "metrics" / "normalized_metrics.json", {})
    source_map = read_json(package_dir / "qa" / "source_map.json", {})
    counts = {
        "sections": _count(quality, "section_count"),
        "tables": _count(quality, "table_count"),
        "raw_facts": _count(quality, "raw_fact_count", "xbrl_fact_count"),
        "metrics": _count(quality, "normalized_metric_count") or len(metrics.get("metrics") or []),
        "evidence": len(source_map.get("entries") or []),
    }
    return {
        "schema_version": "sec_package_summary_v1",
        "market": "US",
        "package_path": repo_relative(package_dir),
        "manifest_path": repo_relative(package_dir / "manifest.json"),
        "filing_id": manifest.get("filing_id"),
        "parse_run_id": manifest.get("parse_run_id"),
        "company_id": manifest.get("company_id"),
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
        "document_format": manifest.get("document_format"),
        "accounting_standard": manifest.get("accounting_standard"),
        "counts": counts,
    }


def _write_company_index(output_root: Path, ticker: str, items: list[dict[str, Any]]) -> None:
    company_dir = output_root / ticker
    latest = _latest_filing(items)
    company = {
        "schema_version": "sec_company_wiki_v1",
        "market": "US",
        "ticker": ticker,
        "company_id": latest.get("company_id"),
        "cik": latest.get("cik"),
        "company_name": latest.get("company_name"),
        "latest_filing_id": latest.get("filing_id"),
        "latest_fiscal_year": latest.get("fiscal_year"),
        "latest_period_end": latest.get("period_end"),
        "package_count": len(items),
        "updated_at": now_iso(),
    }
    write_json(company_dir / "company.json", company)
    write_json(company_dir / "filings.json", {"schema_version": "sec_company_filings_v1", "ticker": ticker, "count": len(items), "items": items})
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
    totals = {"sections": 0, "tables": 0, "raw_facts": 0, "metrics": 0, "evidence": 0}
    for item in packages:
        status = str(item.get("quality_status") or "unknown").lower()
        quality_counts[status] = quality_counts.get(status, 0) + 1
        counts = item.get("counts") or {}
        for key in totals:
            totals[key] += int(counts.get(key) or 0)
    return {
        "schema_version": "sec_quality_summary_v1",
        "generated_at": now_iso(),
        "package_count": len(packages),
        "quality_counts": quality_counts,
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
