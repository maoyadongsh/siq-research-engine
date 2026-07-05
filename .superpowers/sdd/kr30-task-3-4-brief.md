### Task 3: Add Unit Tests For The KR Batch Helper

**Files:**
- Create: `scripts/ops/tests/test_download_kr_2025_annuals_to_parse_queue.py`

**Interfaces:**
- Consumes: planned helper functions `_normalize_kr_code()`, `_requested_codes()`, `_candidate_pool()`, `_existing_downloaded_pdf_for_ticker()`.
- Produces: failing tests for code parsing, candidate selection, skip behavior, and existing-download discovery.

- [ ] **Step 1: Write the failing script helper tests**

Create `scripts/ops/tests/test_download_kr_2025_annuals_to_parse_queue.py` with:

```python
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "download_kr_2025_annuals_to_parse_queue.py"
SPEC = importlib.util.spec_from_file_location("download_kr_2025_annuals_to_parse_queue", SCRIPT_PATH)
assert SPEC is not None
kr_download = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = kr_download
assert SPEC.loader is not None
SPEC.loader.exec_module(kr_download)


def test_normalize_kr_code_pads_to_six_digits():
    assert kr_download._normalize_kr_code("5930") == "005930"
    assert kr_download._normalize_kr_code("KR:270") == "000270"
    assert kr_download._normalize_kr_code("") == ""


def test_requested_codes_accepts_repeated_and_combined_values():
    args = SimpleNamespace(code=["5930", "000660"], codes="270, 12330 373220")

    assert kr_download._requested_codes(args) == ["005930", "000660", "000270", "012330", "373220"]


def test_candidate_pool_defaults_to_30_catalog_entries():
    pool = kr_download._candidate_pool([])

    assert len(pool) == 30
    assert pool[0]["ticker"] == "005930"
    assert pool[-1]["ticker"] == "097950"
    assert all(seed["market"] == "KR" for seed in pool)


def test_candidate_pool_keeps_manual_unknown_codes():
    pool = kr_download._candidate_pool(["005930", "123456"])

    assert pool[0]["ticker"] == "005930"
    assert pool[0]["name"] == "Samsung Electronics Co., Ltd."
    assert pool[1] == {"market": "KR", "ticker": "123456", "industry": "manual", "name": "123456"}


def test_existing_downloaded_pdf_for_ticker_finds_2025_annual_pdf(tmp_path: Path):
    pdf_path = (
        tmp_path
        / "KR"
        / "Samsung-Electronics-Co.,-Ltd"
        / "2025"
        / "年报"
        / "Samsung-Electronics-Co.,-Ltd_KR_005930_2025-12-31_年报_2026-03-10_dart_public_a4d8816f.pdf"
    )
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"%PDF-1.7\n")

    assert kr_download._existing_downloaded_pdf_for_ticker(tmp_path, "005930", 2025) == pdf_path
    assert kr_download._existing_downloaded_pdf_for_ticker(tmp_path, "000660", 2025) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
cd /home/maoyd/siq-research-engine
python3 -m pytest scripts/ops/tests/test_download_kr_2025_annuals_to_parse_queue.py -q
```

Expected: FAIL because `scripts/ops/download_kr_2025_annuals_to_parse_queue.py` does not exist yet.

---

### Task 4: Implement The KR Batch Download And Parser Enqueue Helper

**Files:**
- Create: `scripts/ops/download_kr_2025_annuals_to_parse_queue.py`
- Test: `scripts/ops/tests/test_download_kr_2025_annuals_to_parse_queue.py`

**Interfaces:**
- Consumes: `KrAnnualReportCatalog`, `DartPublicClient`, `ReportDownloader`, `ReportTarget`, pdf-parser `/api/upload`.
- Produces: CLI helper supporting `--target-count`, `--report-year`, `--download-only`, `--code`, `--codes`, `--skip-code`, `--manifest`, `--pdf-api-base`, `--task-db`.

- [ ] **Step 1: Create the script imports, constants, and path setup**

Create `scripts/ops/download_kr_2025_annuals_to_parse_queue.py` starting with:

```python
#!/usr/bin/env python3
"""Download KR annual reports and enqueue them in the PDF parser."""

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

os.environ.setdefault(
    "MARKET_REPORT_DOWNLOAD_DIR",
    str(PROJECT_ROOT / "data" / "market-report-finder" / "downloads"),
)

from market_report_finder_service.markets.kr.catalog import KR_ANNUAL_REPORT_CATALOG, KrAnnualReportCatalog  # noqa: E402
from market_report_finder_service.markets.kr.public_dart import DartPublicClient  # noqa: E402
from market_report_finder_service.models.schemas import ReportTarget  # noqa: E402
from market_report_finder_service.services.downloader import ReportDownloader  # noqa: E402


PDF_MAX_BYTES = 100 * 1024 * 1024
DEFAULT_TARGET_COUNT = 30
DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "market-report-finder" / "kr_2025_annual_download_queue_manifest.json"
```

- [ ] **Step 2: Add pure helper functions**

Add these functions below the constants:

```python
def _normalize_kr_code(raw: str) -> str:
    digits = re.sub(r"\D+", "", str(raw or ""))
    return digits.zfill(6) if digits else ""


def _requested_codes(args: argparse.Namespace) -> list[str]:
    raw_values = [*args.code]
    if args.codes:
        raw_values.append(args.codes)
    codes: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        for token in re.split(r"[,;\s]+", str(raw)):
            code = _normalize_kr_code(token)
            if not code or code in seen:
                continue
            codes.append(code)
            seen.add(code)
    return codes


def _candidate_pool(include_codes: list[str]) -> list[dict[str, str]]:
    known = {
        entry.ticker: {
            "market": "KR",
            "ticker": entry.ticker,
            "company_id": entry.company_id,
            "industry": entry.industry,
            "name": entry.company_name,
        }
        for entry in KR_ANNUAL_REPORT_CATALOG
    }
    if include_codes:
        return [known.get(code, {"market": "KR", "ticker": code, "industry": "manual", "name": code}) for code in include_codes]
    return list(known.values())


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


def _existing_downloaded_pdf_for_ticker(download_root: Path, ticker: str, report_year: int) -> Path | None:
    pattern = f"*_KR_{ticker}_{report_year}-*_年报_*_dart_public_*.pdf"
    matches = sorted((download_root / "KR").glob(f"*/{report_year}/年报/{pattern}"))
    return matches[-1] if matches else None
```

- [ ] **Step 3: Add parser token and upload helpers**

Add:

```python
def _resolve_pdf_token(pdf_api_base: str) -> str:
    token = (os.environ.get("PDF2MD_ACCESS_TOKEN") or os.environ.get("SIQ_PDF2MD_ACCESS_TOKEN") or "").strip()
    if token:
        return token
    for child in Path("/proc").iterdir():
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
```

- [ ] **Step 4: Add DART selection and enqueue helpers**

Add:

```python
def _company_for_seed(seed: dict[str, str]):
    return KrAnnualReportCatalog.resolve_company(
        ticker=seed.get("ticker"),
        company_name=seed.get("name"),
        company_id=seed.get("company_id") or None,
    )[0]


def _selected_annual(public: DartPublicClient, company, year: int):
    filings = public.list_filings(
        company,
        target=ReportTarget.annual_report,
        forms=["annual"],
        report_year=year,
    )
    matches = [item for item in filings if item.report_end.year == year and item.file_format == "pdf"]
    return matches[0] if matches else None


def _enqueue_or_mark(
    *,
    item: dict,
    pdf_path: Path,
    args: argparse.Namespace,
    pdf_token: str,
    existing_filenames: set[str],
) -> bool:
    if pdf_path.suffix.lower() != ".pdf":
        item["status"] = "skipped"
        item["reason"] = f"Downloaded file is not PDF: {pdf_path.name}"
        return False
    if pdf_path.stat().st_size > PDF_MAX_BYTES:
        item["status"] = "skipped"
        item["reason"] = f"PDF exceeds parser limit: {pdf_path.stat().st_size} bytes"
        return False
    if args.download_only:
        item["status"] = "already_downloaded" if item.get("existing_download") else "downloaded"
        item["reason"] = "download-only mode; parser enqueue deferred"
        return True
    if pdf_path.name in existing_filenames:
        item["status"] = "already_in_queue"
        item["reason"] = "filename already exists in pdf-parser tasks"
        return True
    upload = _upload_pdf(args.pdf_api_base, pdf_token, pdf_path)
    item["upload"] = upload
    if 200 <= upload["status_code"] < 300:
        item["status"] = "queued"
        item["task_id"] = upload["payload"].get("task_id")
        for task in upload["payload"].get("tasks") or []:
            if task.get("filename"):
                existing_filenames.add(str(task["filename"]))
        return True
    if upload["status_code"] == 409:
        item["status"] = "already_in_queue"
        item["reason"] = upload["payload"].get("message") or "duplicate filename"
        existing_filenames.add(pdf_path.name)
        return True
    item["status"] = "upload_failed"
    item["reason"] = str(upload["payload"])[:500]
    return False
```

- [ ] **Step 5: Add `main()` and argument parsing**

Add:

```python
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-count", type=int, default=0)
    parser.add_argument("--report-year", type=int, default=2025)
    parser.add_argument("--pdf-api-base", default=os.environ.get("SIQ_PDF2MD_API_BASE", "http://127.0.0.1:15000"))
    parser.add_argument("--task-db", default=str(PROJECT_ROOT / "data" / "pdf-parser" / "db" / "tasks.db"))
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--code", action="append", default=[])
    parser.add_argument("--codes", default="")
    parser.add_argument("--skip-code", action="append", default=[])
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    args = parser.parse_args()

    include_codes = _requested_codes(args)
    candidate_pool = _candidate_pool(include_codes)
    target_count = args.target_count or (len(candidate_pool) if include_codes else DEFAULT_TARGET_COUNT)
    if target_count <= 0:
        parser.error("--target-count must be positive when no --code/--codes values are provided")
    if include_codes and target_count > len(candidate_pool):
        parser.error("--target-count cannot exceed the number of selected --code/--codes values")

    public = DartPublicClient()
    downloader = ReportDownloader()
    pdf_token = "" if args.download_only else _resolve_pdf_token(args.pdf_api_base)
    existing_filenames = set() if args.download_only else _existing_task_filenames(Path(args.task_db))
    download_root = Path(os.environ.get("MARKET_REPORT_DOWNLOAD_DIR", PROJECT_ROOT / "data" / "market-report-finder" / "downloads"))
    skip_codes = {_normalize_kr_code(code) for code in args.skip_code if _normalize_kr_code(code)}

    manifest = {
        "started_at": _now(),
        "report_year": args.report_year,
        "target_count": target_count,
        "mode": "download_only" if args.download_only else "download_and_enqueue",
        "selection_note": "Mainstream Korean listed companies selected from the curated KR catalog with broad industry coverage.",
        "include_codes": include_codes,
        "items": [],
        "skipped": [],
    }

    succeeded = 0
    for seed in candidate_pool:
        if succeeded >= target_count:
            break
        ticker = seed["ticker"]
        if ticker in skip_codes:
            continue
        print(f"[{succeeded}/{target_count}] {ticker} {seed['name']}", flush=True)
        item = {"seed": seed, "status": "started", "events": []}
        try:
            existing_pdf = _existing_downloaded_pdf_for_ticker(download_root, ticker, args.report_year)
            if existing_pdf is not None:
                item["existing_download"] = True
                item["downloaded_file"] = {"saved_path": str(existing_pdf.resolve()), "file_name": existing_pdf.name}
                if _enqueue_or_mark(
                    item=item,
                    pdf_path=existing_pdf,
                    args=args,
                    pdf_token=pdf_token,
                    existing_filenames=existing_filenames,
                ):
                    manifest["items"].append(item)
                    succeeded += 1
                else:
                    manifest["skipped"].append(item)
                continue

            company = _company_for_seed(seed)
            item["company"] = company.model_dump(mode="json")
            annual = _selected_annual(public, company, args.report_year)
            if annual is None:
                item["status"] = "not_found"
                item["reason"] = f"No {args.report_year} DART public annual report PDF found"
                manifest["skipped"].append(item)
                continue
            item["filing"] = annual.model_dump(mode="json")
            downloaded = downloader.download(annual)
            item["downloaded_file"] = downloaded.model_dump(mode="json")
            pdf_path = Path(downloaded.saved_path)
            if _enqueue_or_mark(
                item=item,
                pdf_path=pdf_path,
                args=args,
                pdf_token=pdf_token,
                existing_filenames=existing_filenames,
            ):
                manifest["items"].append(item)
                succeeded += 1
            else:
                manifest["skipped"].append(item)
        except Exception as exc:
            item["status"] = "error"
            item["reason"] = repr(exc)
            manifest["skipped"].append(item)
        finally:
            Path(args.manifest).parent.mkdir(parents=True, exist_ok=True)
            manifest["updated_at"] = _now()
            manifest["downloaded_or_existing_count"] = succeeded
            Path(args.manifest).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            time.sleep(0.5)

    manifest["completed_at"] = _now()
    manifest["downloaded_or_existing_count"] = succeeded
    Path(args.manifest).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"downloaded_or_existing_count": succeeded, "manifest": args.manifest}, ensure_ascii=False, indent=2))
    return 0 if succeeded >= target_count else 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Run script helper tests**

Run:

```bash
cd /home/maoyd/siq-research-engine
python3 -m pytest scripts/ops/tests/test_download_kr_2025_annuals_to_parse_queue.py -q
```

Expected: PASS.

- [ ] **Step 7: Run market-report-finder tests touched by this work**

Run:

```bash
cd /home/maoyd/siq-research-engine/services/market-report-finder
uv run pytest tests/test_kr_catalog.py tests/test_dart_client.py tests/test_downloader.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit script implementation**

Run:

```bash
cd /home/maoyd/siq-research-engine
git add scripts/ops/download_kr_2025_annuals_to_parse_queue.py scripts/ops/tests/test_download_kr_2025_annuals_to_parse_queue.py
git commit -m "feat(kr): add annual report batch download helper"
```

---

