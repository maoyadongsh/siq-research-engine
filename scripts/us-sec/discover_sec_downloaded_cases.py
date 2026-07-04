#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DOWNLOADS_ROOT = REPO_ROOT / "data" / "market-report-finder" / "downloads" / "US"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "data" / "wiki" / "us_sec"


def read_json(path: Path, default: Any = None) -> Any:
    if not path or not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_csv_set(value: str | None, *, upper: bool = True) -> set[str] | None:
    if not value:
        return None
    items = {item.strip() for item in value.split(",") if item.strip()}
    if upper:
        items = {item.upper() for item in items}
    return items or None


def scan_downloads(
    downloads_root: Path,
    *,
    forms: set[str] | None = None,
    tickers: set[str] | None = None,
    limit: int = 0,
) -> list[dict[str, Any]]:
    downloads_root = downloads_root.resolve()
    form_filter = {item.upper() for item in forms} if forms else None
    ticker_filter = {item.upper() for item in tickers} if tickers else None
    rows: list[dict[str, Any]] = []
    for source_path in sorted(downloads_root.rglob("*")):
        if source_path.suffix.lower() not in {".htm", ".html"}:
            continue
        metadata_path = source_path.with_suffix(source_path.suffix + ".metadata.json")
        metadata = read_json(metadata_path, {})
        candidate = metadata.get("candidate") if isinstance(metadata, dict) else {}
        if not isinstance(candidate, dict):
            candidate = {}
        ticker = _ticker(candidate, source_path)
        form = _form(candidate, source_path)
        if ticker_filter and ticker not in ticker_filter:
            continue
        if form_filter and form not in form_filter:
            continue
        period_end = candidate.get("report_end") or candidate.get("period_end") or _period_end_from_filename(source_path.name)
        filing_date = candidate.get("published_at") or candidate.get("filing_date") or _filing_date_from_filename(source_path.name)
        fiscal_year = _int_or_none(str(period_end or candidate.get("year") or "")[:4])
        source_url = candidate.get("document_url") or candidate.get("source_url")
        accession = _normalize_accession(candidate.get("accession_number"), source_url)
        rows.append(
            {
                "schema_version": "sec_download_case_v1",
                "market": "US",
                "source_id": candidate.get("source_id") or "sec",
                "company_id": str(candidate.get("company_id") or candidate.get("cik") or ""),
                "ticker": ticker,
                "company_name": candidate.get("company_name") or candidate.get("source_name") or ticker,
                "form": form,
                "report_type": candidate.get("report_type") or form,
                "report_family": candidate.get("report_family") or ("annual" if form in {"10-K", "20-F"} else None),
                "fiscal_year": fiscal_year,
                "period_end": period_end,
                "filing_date": filing_date,
                "accepted_at": candidate.get("accepted_at"),
                "accession_number": accession,
                "primary_document": candidate.get("primary_document"),
                "source_url": source_url,
                "landing_url": candidate.get("landing_url"),
                "inline_xbrl": candidate.get("inline_xbrl"),
                "file_format": candidate.get("file_format") or source_path.suffix.lower().lstrip("."),
                "source_path": str(source_path),
                "metadata_path": str(metadata_path) if metadata_path.exists() else None,
                "source_sha256": sha256_file(source_path),
            }
        )
        if limit and len(rows) >= limit:
            break
    return rows


def write_downloads_index(rows: list[dict[str, Any]], output_root: Path) -> Path:
    path = output_root / "_meta" / "downloads_index.json"
    write_json(
        path,
        {
            "schema_version": "sec_downloads_index_v1",
            "generated_at": now_iso(),
            "downloads_root": None,
            "count": len(rows),
            "items": rows,
        },
    )
    return path


def _ticker(candidate: dict[str, Any], source_path: Path) -> str:
    value = candidate.get("ticker") or candidate.get("company_id") or ""
    if value:
        return str(value).upper()
    match = re.search(r"_US_([A-Za-z0-9.-]+)_", source_path.name)
    return match.group(1).upper() if match else "UNKNOWN"


def _form(candidate: dict[str, Any], source_path: Path) -> str:
    value = candidate.get("form") or candidate.get("report_type") or ""
    if value:
        return str(value).upper()
    for token in ("10-K", "10-Q", "20-F", "6-K"):
        if token in source_path.name.upper():
            return token
    return "UNKNOWN"


def _period_end_from_filename(filename: str) -> str | None:
    match = re.search(r"_US_[^_]+_(\d{4}-\d{2}-\d{2})_", filename)
    return match.group(1) if match else None


def _filing_date_from_filename(filename: str) -> str | None:
    match = re.search(r"_(10-K|10-Q|20-F|6-K)_(\d{4}-\d{2}-\d{2})_", filename, flags=re.IGNORECASE)
    return match.group(2) if match else None


def _normalize_accession(value: Any, source_url: str | None = None) -> str | None:
    value_text = str(value or "").strip()
    candidates = [] if value_text.lower() in {"", "manual", "unknown"} else [value_text]
    if source_url:
        candidates.append(source_url)
    for text in candidates:
        accession = _compact_accession(text)
        if accession:
            return accession
    return value_text or None


def _compact_accession(text: str) -> str | None:
    match = re.search(r"(\d{10}-\d{2}-\d{6})", text)
    if match:
        return match.group(1)
    match = re.search(r"(\d{18})", text)
    if not match:
        return None
    raw = match.group(1)
    return f"{raw[:10]}-{raw[10:12]}-{raw[12:]}"


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover downloaded US SEC HTML/iXBRL filings and write a wiki downloads index.")
    parser.add_argument("--downloads-root", type=Path, default=DEFAULT_DOWNLOADS_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--forms", default="10-K", help="Comma-separated SEC forms, e.g. 10-K,10-Q,20-F. Empty means all.")
    parser.add_argument("--tickers", default="", help="Comma-separated ticker filter.")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    rows = scan_downloads(
        args.downloads_root,
        forms=parse_csv_set(args.forms),
        tickers=parse_csv_set(args.tickers),
        limit=args.limit,
    )
    path = write_downloads_index(rows, args.output_root)
    print(path)


if __name__ == "__main__":
    main()
