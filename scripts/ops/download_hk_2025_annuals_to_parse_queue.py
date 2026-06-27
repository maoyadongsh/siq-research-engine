#!/usr/bin/env python3
"""Download 2025 HK annual reports and enqueue them in the PDF parser.

This is an operational helper, not an application service. It reuses the
project's HKEX finder/downloader modules and the pdf-parser upload API.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MARKET_FINDER_SRC = PROJECT_ROOT / "services" / "market-report-finder" / "src"
sys.path.insert(0, str(MARKET_FINDER_SRC))

# The finder settings object is created at import time, so defaults must be set
# before importing project modules.
os.environ.setdefault(
    "MARKET_REPORT_DOWNLOAD_DIR",
    str(PROJECT_ROOT / "data" / "market-report-finder" / "downloads"),
)

from market_report_finder_service.markets.hk.client import HkexClient  # noqa: E402
from market_report_finder_service.models.schemas import ReportTarget  # noqa: E402
from market_report_finder_service.services.downloader import ReportDownloader  # noqa: E402
from market_report_finder_service.services.orchestrator import ReportFinderOrchestrator  # noqa: E402


PDF_MAX_BYTES = 100 * 1024 * 1024


# All names below are intended to be comfortably above HKD 10bn market cap,
# selected from large HK-listed issuers/blue chips and spread across sectors.
CANDIDATES = [
    {"code": "00700", "sector": "technology", "name": "Tencent"},
    {"code": "09988", "sector": "technology", "name": "Alibaba"},
    {"code": "03690", "sector": "technology", "name": "Meituan"},
    {"code": "01810", "sector": "technology hardware", "name": "Xiaomi"},
    {"code": "01024", "sector": "technology", "name": "Kuaishou"},
    {"code": "09618", "sector": "e-commerce", "name": "JD.com"},
    {"code": "09888", "sector": "technology", "name": "Baidu"},
    {"code": "09999", "sector": "gaming", "name": "NetEase"},
    {"code": "00981", "sector": "semiconductors", "name": "SMIC"},
    {"code": "01347", "sector": "semiconductors", "name": "Hua Hong Semiconductor"},
    {"code": "00005", "sector": "banking", "name": "HSBC"},
    {"code": "01299", "sector": "insurance", "name": "AIA"},
    {"code": "00388", "sector": "financial infrastructure", "name": "HKEX"},
    {"code": "00939", "sector": "banking", "name": "China Construction Bank"},
    {"code": "01398", "sector": "banking", "name": "ICBC"},
    {"code": "03988", "sector": "banking", "name": "Bank of China"},
    {"code": "01288", "sector": "banking", "name": "Agricultural Bank of China"},
    {"code": "03968", "sector": "banking", "name": "China Merchants Bank"},
    {"code": "02318", "sector": "insurance", "name": "Ping An"},
    {"code": "02628", "sector": "insurance", "name": "China Life"},
    {"code": "02328", "sector": "insurance", "name": "PICC P&C"},
    {"code": "02388", "sector": "banking", "name": "BOC Hong Kong"},
    {"code": "00941", "sector": "telecom", "name": "China Mobile"},
    {"code": "00728", "sector": "telecom", "name": "China Telecom"},
    {"code": "00762", "sector": "telecom", "name": "China Unicom"},
    {"code": "00788", "sector": "telecom infrastructure", "name": "China Tower"},
    {"code": "00883", "sector": "energy", "name": "CNOOC"},
    {"code": "00857", "sector": "energy", "name": "PetroChina"},
    {"code": "00386", "sector": "energy", "name": "Sinopec"},
    {"code": "01088", "sector": "coal", "name": "China Shenhua"},
    {"code": "01171", "sector": "coal", "name": "Yankuang Energy"},
    {"code": "02899", "sector": "metals", "name": "Zijin Mining"},
    {"code": "00358", "sector": "metals", "name": "Jiangxi Copper"},
    {"code": "01211", "sector": "automobiles", "name": "BYD"},
    {"code": "00175", "sector": "automobiles", "name": "Geely Auto"},
    {"code": "02333", "sector": "automobiles", "name": "Great Wall Motor"},
    {"code": "02015", "sector": "automobiles", "name": "Li Auto"},
    {"code": "09868", "sector": "automobiles", "name": "XPeng"},
    {"code": "01766", "sector": "industrial", "name": "CRRC"},
    {"code": "02382", "sector": "optical components", "name": "Sunny Optical"},
    {"code": "00669", "sector": "consumer appliances", "name": "Techtronic Industries"},
    {"code": "06690", "sector": "consumer appliances", "name": "Haier Smart Home"},
    {"code": "02313", "sector": "apparel manufacturing", "name": "Shenzhou International"},
    {"code": "02020", "sector": "sportswear", "name": "ANTA Sports"},
    {"code": "02331", "sector": "sportswear", "name": "Li Ning"},
    {"code": "09633", "sector": "beverages", "name": "Nongfu Spring"},
    {"code": "00291", "sector": "beverages", "name": "China Resources Beer"},
    {"code": "00168", "sector": "beverages", "name": "Tsingtao Brewery"},
    {"code": "06862", "sector": "restaurants", "name": "Haidilao"},
    {"code": "01093", "sector": "healthcare", "name": "CSPC Pharmaceutical"},
    {"code": "01177", "sector": "healthcare", "name": "Sino Biopharm"},
    {"code": "06160", "sector": "biotech", "name": "BeiGene"},
    {"code": "01801", "sector": "biotech", "name": "Innovent"},
    {"code": "02269", "sector": "biotech services", "name": "WuXi Biologics"},
    {"code": "02359", "sector": "healthcare services", "name": "WuXi AppTec"},
    {"code": "00002", "sector": "utilities", "name": "CLP"},
    {"code": "00003", "sector": "utilities", "name": "HK and China Gas"},
    {"code": "00006", "sector": "utilities", "name": "Power Assets"},
    {"code": "01038", "sector": "infrastructure", "name": "CK Infrastructure"},
    {"code": "00016", "sector": "property", "name": "Sun Hung Kai Properties"},
    {"code": "00823", "sector": "reit", "name": "Link REIT"},
    {"code": "01109", "sector": "property", "name": "China Resources Land"},
    {"code": "00688", "sector": "property", "name": "China Overseas Land"},
    {"code": "00027", "sector": "gaming", "name": "Galaxy Entertainment"},
    {"code": "01928", "sector": "gaming", "name": "Sands China"},
    {"code": "00001", "sector": "conglomerate", "name": "CK Hutchison"},
    {"code": "00066", "sector": "transport", "name": "MTR"},
    {"code": "00267", "sector": "conglomerate", "name": "CITIC"},
    {"code": "00316", "sector": "travel", "name": "Orient Overseas"},
]


def _normalize_hk_code(raw: str) -> str:
    digits = re.sub(r"\D+", "", str(raw or ""))
    return digits.zfill(5) if digits else ""


def _requested_codes(args: argparse.Namespace) -> list[str]:
    raw_values = [*args.code]
    if args.codes:
        raw_values.append(args.codes)
    codes: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        for token in re.split(r"[,;\s]+", str(raw)):
            code = _normalize_hk_code(token)
            if not code or code in seen:
                continue
            codes.append(code)
            seen.add(code)
    return codes


def _candidate_pool(include_codes: list[str]) -> list[dict[str, str]]:
    if not include_codes:
        return CANDIDATES
    known = {seed["code"]: seed for seed in CANDIDATES}
    return [known.get(code, {"code": code, "sector": "manual", "name": code}) for code in include_codes]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _existing_task_filenames(db_path: Path) -> set[str]:
    if not db_path.exists():
        return set()
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT filename FROM tasks").fetchall()
        return {str(row[0]) for row in rows if row and row[0]}
    finally:
        conn.close()


def _resolve_pdf_token(pdf_api_base: str) -> str:
    token = (os.environ.get("PDF2MD_ACCESS_TOKEN") or os.environ.get("SIQ_PDF2MD_ACCESS_TOKEN") or "").strip()
    if token:
        return token
    # Best effort for local ops: the running Flask service has the token in its env.
    proc_root = Path("/proc")
    for child in proc_root.iterdir():
        if not child.name.isdigit():
            continue
        try:
            cmdline = (child / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "ignore")
            if "siq-research-engine/apps/pdf-parser/app.py" not in cmdline:
                continue
            env = (child / "environ").read_bytes().split(b"\0")
        except OSError:
            continue
        for item in env:
            if item.startswith(b"PDF2MD_ACCESS_TOKEN="):
                return item.split(b"=", 1)[1].decode("utf-8", "ignore").strip()
    raise RuntimeError(f"PDF2MD access token not found for {pdf_api_base}")


def _upload_pdf(pdf_api_base: str, token: str, pdf_path: Path) -> dict:
    headers = {"X-PDF2MD-Token": token} if token else {}
    data = {
        "backend": "hybrid-http-client",
        "parse_method": "auto",
        "formula_enable": "true",
        "table_enable": "true",
    }
    with httpx.Client(timeout=None, headers=headers) as client:
        with pdf_path.open("rb") as infile:
            response = client.post(
                f"{pdf_api_base.rstrip('/')}/api/upload",
                data=data,
                files=[("files", (pdf_path.name, infile, "application/pdf"))],
            )
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text[:500]}
    return {"status_code": response.status_code, "payload": payload}


def _company_for_code(client: HkexClient, rows: list[dict], code: str):
    candidates = client._company_candidates_from_rows(rows, ticker=code)
    if not candidates:
        raise RuntimeError(f"HKEX stock catalog did not match {code}")
    return candidates[0]


def _selected_annual(client: HkexClient, company, year: int):
    filings = client.list_filings(company, target=ReportTarget.annual_report, forms=["annual"])
    matches = [item for item in filings if item.report_end.year == year]
    ranked = ReportFinderOrchestrator._rank(matches)
    return ranked[0] if ranked else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--target-count",
        type=int,
        default=0,
        help="Number of annual reports to collect. Defaults to 50, or to the number of selected --code values.",
    )
    parser.add_argument("--report-year", type=int, default=2025)
    parser.add_argument("--pdf-api-base", default=os.environ.get("SIQ_PDF2MD_API_BASE", "http://127.0.0.1:15000"))
    parser.add_argument("--task-db", default=str(PROJECT_ROOT / "data" / "pdf-parser" / "db" / "tasks.db"))
    parser.add_argument("--download-only", action="store_true", help="Only download matching PDFs; do not enqueue parser tasks.")
    parser.add_argument("--code", action="append", default=[], help="HK stock code to include. Can be repeated.")
    parser.add_argument("--codes", default="", help="Comma/space separated HK stock codes to include.")
    parser.add_argument("--skip-code", action="append", default=[], help="HK stock code to skip. Can be repeated.")
    parser.add_argument(
        "--manifest",
        default=str(PROJECT_ROOT / "data" / "market-report-finder" / "hk_2025_annual_download_queue_manifest.json"),
    )
    args = parser.parse_args()

    include_codes = _requested_codes(args)
    candidate_pool = _candidate_pool(include_codes)
    target_count = args.target_count or (len(candidate_pool) if include_codes else 50)
    if target_count <= 0:
        parser.error("--target-count must be positive when no --code/--codes values are provided")
    if include_codes and target_count > len(candidate_pool):
        parser.error("--target-count cannot exceed the number of selected --code/--codes values")

    client = HkexClient()
    downloader = ReportDownloader()
    pdf_token = "" if args.download_only else _resolve_pdf_token(args.pdf_api_base)
    existing_filenames = set() if args.download_only else _existing_task_filenames(Path(args.task_db))

    active_rows = client._stock_rows(client.ACTIVE_STOCK_URL, status="active")
    inactive_rows = client._stock_rows(client.INACTIVE_STOCK_URL, status="inactive")
    rows = [*active_rows, *inactive_rows]

    manifest = {
        "started_at": _now(),
        "report_year": args.report_year,
        "target_count": target_count,
        "mode": "download_only" if args.download_only else "download_and_enqueue",
        "selection_note": (
            "HKEX annual report bodies selected via market-report-finder rules; notice letters, "
            "request forms, reply forms, and circular-only publication notices are excluded."
        ),
        "include_codes": include_codes,
        "items": [],
        "skipped": [],
    }

    succeeded = 0
    skip_codes = {_normalize_hk_code(code) for code in args.skip_code if _normalize_hk_code(code)}
    for candidate_seed in candidate_pool:
        if succeeded >= target_count:
            break
        code = candidate_seed["code"]
        if code in skip_codes:
            continue
        print(f"[{succeeded}/{target_count}] {code} {candidate_seed['name']}", flush=True)
        item = {"seed": candidate_seed, "status": "started", "events": []}
        try:
            company = _company_for_code(client, rows, code)
            item["company"] = company.model_dump(mode="json")
            annual = _selected_annual(client, company, args.report_year)
            if annual is None:
                item["status"] = "skipped"
                item["reason"] = f"No {args.report_year} annual report found"
                manifest["skipped"].append(item)
                continue
            item["filing"] = annual.model_dump(mode="json")
            downloaded = downloader.download(annual)
            item["downloaded_file"] = downloaded.model_dump(mode="json")
            pdf_path = Path(downloaded.saved_path)
            if pdf_path.suffix.lower() != ".pdf":
                item["status"] = "skipped"
                item["reason"] = f"Downloaded file is not PDF: {pdf_path.name}"
                manifest["skipped"].append(item)
                continue
            if downloaded.size_bytes > PDF_MAX_BYTES:
                item["status"] = "skipped"
                item["reason"] = f"PDF exceeds parser limit: {downloaded.size_bytes} bytes"
                manifest["skipped"].append(item)
                continue
            if args.download_only:
                item["status"] = "downloaded"
                item["reason"] = "download-only mode; parser enqueue deferred"
                manifest["items"].append(item)
                succeeded += 1
                continue
            if pdf_path.name in existing_filenames:
                item["status"] = "already_in_queue"
                item["reason"] = "filename already exists in pdf-parser tasks"
                manifest["items"].append(item)
                succeeded += 1
                continue
            upload = _upload_pdf(args.pdf_api_base, pdf_token, pdf_path)
            item["upload"] = upload
            if 200 <= upload["status_code"] < 300:
                item["status"] = "queued"
                tasks = upload["payload"].get("tasks") or []
                item["task_id"] = upload["payload"].get("task_id")
                for task in tasks:
                    if task.get("filename"):
                        existing_filenames.add(str(task["filename"]))
                manifest["items"].append(item)
                succeeded += 1
            elif upload["status_code"] == 409:
                item["status"] = "already_in_queue"
                item["reason"] = upload["payload"].get("message") or "duplicate filename"
                manifest["items"].append(item)
                succeeded += 1
            else:
                item["status"] = "upload_failed"
                item["reason"] = str(upload["payload"])[:500]
                manifest["skipped"].append(item)
        except Exception as exc:
            item["status"] = "error"
            item["reason"] = repr(exc)
            manifest["skipped"].append(item)
        finally:
            Path(args.manifest).parent.mkdir(parents=True, exist_ok=True)
            manifest["updated_at"] = _now()
            manifest["queued_or_existing_count"] = succeeded
            Path(args.manifest).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            time.sleep(0.5)

    manifest["completed_at"] = _now()
    manifest["queued_or_existing_count"] = succeeded
    Path(args.manifest).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"queued_or_existing_count": succeeded, "manifest": args.manifest}, ensure_ascii=False, indent=2))
    return 0 if succeeded >= target_count else 2


if __name__ == "__main__":
    raise SystemExit(main())
