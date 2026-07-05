#!/usr/bin/env python3
"""Download 2025 JP statutory annual securities reports from EDINET.

The JP issuer catalog intentionally contains many Integrated Reports for
discovery, but this operational helper downloads the statutory YUHO PDFs
from EDINET so the frontend JP download list contains complete annual reports.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MARKET_FINDER_SRC = PROJECT_ROOT / "services" / "market-report-finder" / "src"
sys.path.insert(0, str(MARKET_FINDER_SRC))

os.environ.setdefault(
    "MARKET_REPORT_DOWNLOAD_DIR",
    str(PROJECT_ROOT / "data" / "market-report-finder" / "downloads"),
)

from market_report_finder_service.markets.jp.catalog import (  # noqa: E402
    JP_ANNUAL_REPORT_CATALOG,
    JpAnnualReportCatalog,
    JpAnnualReportCatalogEntry,
)
from market_report_finder_service.markets.jp.client import EdinetClient  # noqa: E402
from market_report_finder_service.core.config import settings  # noqa: E402
from market_report_finder_service.models.schemas import FilingCandidate, ReportFamily, ReportType  # noqa: E402
from market_report_finder_service.services.downloader import ReportDownloader  # noqa: E402


DEFAULT_TARGET_COUNT = 30
DEFAULT_YEAR = 2025
DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "market-report-finder" / "jp_2025_yuho_download_manifest.json"
DEFAULT_CACHE_DIR = PROJECT_ROOT / "data" / "market-report-finder" / "edinet-documents-cache"


@dataclass
class Target:
    entry: JpAnnualReportCatalogEntry
    company: object
    windows: list[tuple[date, date]]
    candidate: FilingCandidate | None = None
    candidates_seen: list[str] = field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date_range(start: date, end: date) -> list[date]:
    if end < start:
        return []
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _cache_path(cache_dir: Path, target_date: date) -> Path:
    return cache_dir / f"{target_date.isoformat()}.json"


def _load_cached_rows(cache_dir: Path, target_date: date) -> list[dict] | None:
    path = _cache_path(cache_dir, target_date)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    rows = data.get("results") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return None
    return [row for row in rows if isinstance(row, dict)]


def _save_cached_rows(cache_dir: Path, target_date: date, rows: list[dict]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {"date": target_date.isoformat(), "cached_at": _now(), "results": rows}
    _cache_path(cache_dir, target_date).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _fetch_document_rows(
    http_client: httpx.Client,
    edinet: EdinetClient,
    target_date: date,
    *,
    cache_dir: Path,
) -> tuple[list[dict], bool]:
    cached = _load_cached_rows(cache_dir, target_date)
    if cached is not None:
        return cached, True
    if not settings.edinet_api_key:
        raise ValueError("EDINET_API_KEY is required for Japanese market report search")
    params = {
        "date": target_date.isoformat(),
        "type": "2",
        "Subscription-Key": settings.edinet_api_key,
    }
    for attempt in range(4):
        edinet._wait_for_slot()
        response = http_client.get(edinet.DOCUMENTS_URL, params=params)
        if response.status_code == 429:
            delay = edinet._retry_delay_seconds(response, attempt)
            print(f"[EDINET] 429 on {target_date.isoformat()}, sleeping {delay:.1f}s", file=sys.stderr, flush=True)
            time.sleep(delay)
            continue
        response.raise_for_status()
        payload = response.json()
        rows = [row for row in payload.get("results", []) if isinstance(row, dict)]
        _save_cached_rows(cache_dir, target_date, rows)
        return rows, False
    raise RuntimeError(f"EDINET rate limit persisted for {target_date.isoformat()}")


def _targets(client: EdinetClient, year: int, limit: int, tickers: set[str]) -> list[Target]:
    targets: list[Target] = []
    entries = [
        entry
        for entry in JP_ANNUAL_REPORT_CATALOG
        if not tickers or entry.ticker.upper() in tickers
    ][:limit]
    for entry in entries:
        company = JpAnnualReportCatalog.company_entity(entry)
        windows = client._company_filing_windows(company, allowed={ReportType.annual}, report_year=year)
        targets.append(Target(entry=entry, company=company, windows=windows))
    return targets


def _candidate_score(candidate: FilingCandidate) -> tuple[int, date, str]:
    title = candidate.title or ""
    is_correction = "訂正" in title or "修正" in title
    is_pdf = candidate.file_format.lower() == "pdf"
    return (0 if is_correction else 1, candidate.published_at, "1" if is_pdf else "0")


def _is_yuho_pdf_row(row: dict) -> bool:
    title = str(row.get("docDescription") or "")
    return (
        str(row.get("formCode") or "") in EdinetClient.ANNUAL_FORM_CODES
        and "有価証券報告書" in title
        and str(row.get("pdfFlag") or "1") == "1"
        and str(row.get("withdrawalStatus") or "0") == "0"
    )


def _find_candidates(
    client: EdinetClient,
    targets: list[Target],
    year: int,
    *,
    cache_dir: Path,
    progress_every: int,
    stop_when_found: bool,
) -> dict[str, int]:
    dates: set[date] = set()
    for target in targets:
        for start, end in target.windows:
            dates.update(_date_range(start, min(end, date.today())))

    stats = {"dates_scanned": 0, "dates_from_cache": 0, "rows_seen": 0, "yuho_rows_seen": 0}
    target_by_ticker = {
        client._normalize_ticker(target.entry.ticker): target
        for target in targets
    }
    sorted_dates = sorted(dates)
    print(f"[JP YUHO] scanning {len(sorted_dates)} EDINET dates for {len(targets)} companies", file=sys.stderr, flush=True)
    with EdinetClient._client() as http_client:
        for index, target_date in enumerate(sorted_dates, start=1):
            rows, from_cache = _fetch_document_rows(http_client, client, target_date, cache_dir=cache_dir)
            stats["dates_scanned"] += 1
            if from_cache:
                stats["dates_from_cache"] += 1
            stats["rows_seen"] += len(rows)
            for row in rows:
                if not _is_yuho_pdf_row(row):
                    continue
                stats["yuho_rows_seen"] += 1
                normalized_sec_code = client._normalize_ticker(str(row.get("secCode") or ""))
                target = target_by_ticker.get(normalized_sec_code)
                if not target:
                    continue
                report_type, family = client._infer_report_type(row)
                if report_type != ReportType.annual or family != ReportFamily.annual:
                    continue
                candidate = client._build_candidate(target.company, row, report_type, family)
                if not candidate or candidate.report_end.year != year:
                    continue
                target.candidates_seen.append(candidate.accession_number or candidate.document_url)
                if target.candidate is None or _candidate_score(candidate) > _candidate_score(target.candidate):
                    target.candidate = candidate
            if progress_every > 0 and (index % progress_every == 0 or index == len(sorted_dates)):
                found = sum(1 for target in targets if target.candidate)
                print(
                    f"[JP YUHO] {index}/{len(sorted_dates)} dates scanned, found {found}/{len(targets)}, cache {stats['dates_from_cache']}",
                    file=sys.stderr,
                    flush=True,
                )
            if stop_when_found and all(target.candidate for target in targets):
                print(
                    f"[JP YUHO] all {len(targets)} targets found after {index}/{len(sorted_dates)} dates",
                    file=sys.stderr,
                    flush=True,
                )
                break
    return stats


def _download_targets(targets: list[Target], *, dry_run: bool, delay_seconds: float) -> list[dict]:
    downloader = ReportDownloader()
    results: list[dict] = []
    for target in targets:
        candidate = target.candidate
        item = {
            "ticker": target.entry.ticker,
            "company_name": target.entry.company_name,
            "catalog_title": target.entry.title,
            "status": "missing",
            "candidate_count": len(target.candidates_seen),
        }
        if not candidate:
            results.append({**item, "reason": "No EDINET statutory YUHO PDF matched this company/year"})
            continue
        item.update(
            {
                "status": "found",
                "title": candidate.title,
                "doc_id": candidate.accession_number,
                "report_end": candidate.report_end.isoformat(),
                "published_at": candidate.published_at.isoformat(),
                "document_url": candidate.document_url,
                "form": candidate.form,
            }
        )
        if dry_run:
            results.append(item)
            continue
        try:
            downloaded = downloader.download(candidate)
            item.update(
                {
                    "status": "downloaded",
                    "file_name": downloaded.file_name,
                    "saved_path": downloaded.saved_path,
                    "size_bytes": downloaded.size_bytes,
                    "content_type": downloaded.content_type,
                    "cache_hit": downloaded.cache_hit,
                }
            )
        except Exception as exc:
            item.update({"status": "failed", "error": str(exc)})
        results.append(item)
        if delay_seconds > 0:
            time.sleep(delay_seconds)
    return results


def _write_manifest(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=DEFAULT_YEAR)
    parser.add_argument("--limit", type=int, default=DEFAULT_TARGET_COUNT)
    parser.add_argument("--ticker", action="append", default=[], help="Restrict to one or more JP listing tickers, e.g. 7974")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delay-seconds", type=float, default=0.5)
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--stop-when-found", action="store_true", help="Stop scanning once every requested target has a YUHO candidate.")
    args = parser.parse_args()

    client = EdinetClient()
    tickers = {str(value).strip().upper() for value in args.ticker if str(value).strip()}
    targets = _targets(client, args.year, args.limit, tickers)
    scan_stats = _find_candidates(
        client,
        targets,
        args.year,
        cache_dir=args.cache_dir,
        progress_every=args.progress_every,
        stop_when_found=args.stop_when_found,
    )
    results = _download_targets(targets, dry_run=args.dry_run, delay_seconds=args.delay_seconds)
    summary = {
        "year": args.year,
        "target_count": len(targets),
        "found": sum(1 for item in results if item["status"] in {"found", "downloaded", "failed"}),
        "downloaded": sum(1 for item in results if item["status"] == "downloaded"),
        "failed": sum(1 for item in results if item["status"] == "failed"),
        "missing": sum(1 for item in results if item["status"] == "missing"),
        "pdf_downloaded": sum(
            1 for item in results if item["status"] == "downloaded" and item.get("content_type") == "application/pdf"
        ),
        "dry_run": bool(args.dry_run),
        "checked_at": _now(),
        **scan_stats,
    }
    payload = {"summary": summary, "results": results}
    _write_manifest(args.manifest, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if summary["missing"] == 0 and summary["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
