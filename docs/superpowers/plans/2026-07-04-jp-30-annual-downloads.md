# JP 30 Annual Downloads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand Japan curated annual-report downloads from 10 to 30 mainstream companies and download annual-report PDFs plus metadata only.

**Architecture:** The existing JP finder already exposes a curated issuer annual-report catalog and download path. Extend the static JP catalog with 20 additional official issuer IR PDF entries, verify `curated_annual_reports(limit=30)` returns 30 unique companies with broad industry coverage, then run a one-off operational download using the existing `ReportDownloader` into the active data directory.

**Tech Stack:** Python 3.13, Pydantic schemas in `market_report_finder_service`, pytest, existing `ReportDownloader`.

---

### Task 1: Add Coverage Test

**Files:**
- Modify: `services/market-report-finder/tests/test_jp_service.py`

- [ ] **Step 1: Write the failing test**

Append a test that calls `JpReportFinder().curated_annual_reports(report_year=2025, limit=30)`, asserts 30 unique JP companies, and asserts at least 12 industry labels from candidate metadata.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/market-report-finder && uv run pytest tests/test_jp_service.py::test_jp_curated_annual_reports_cover_30_mainstream_companies -q`
Expected before implementation: failure because only 10 catalog entries exist.

### Task 2: Extend JP Catalog

**Files:**
- Modify: `services/market-report-finder/src/market_report_finder_service/markets/jp/catalog.py`

- [ ] **Step 1: Add 20 official issuer annual-report entries**

Add mainstream companies across electronics, semiconductors, finance, telecom, industrials, consumer, healthcare, energy/materials, rail, real estate, and entertainment. Each entry must include ticker, company name, official PDF URL, official IR landing URL, report end, published date, title, and aliases.

- [ ] **Step 2: Run focused tests**

Run: `cd services/market-report-finder && uv run pytest tests/test_jp_service.py -q`
Expected after implementation: all JP service tests pass.

### Task 3: Download 30 PDFs And Write Manifest

**Files:**
- Create runtime output: `data/market-report-finder/jp_2025_annual_download_30_manifest_20260704.json`
- Runtime downloads: `data/market-report-finder/downloads/JP/...`

- [ ] **Step 1: Use existing downloader**

Instantiate `JpReportFinder`, call `curated_annual_reports(report_year=2025, limit=30)`, then call `ReportDownloader().download(candidate)` for each candidate. Record success, saved path, metadata path, cache-hit state, size, industry, ticker, and URL.

- [ ] **Step 2: Verify totals**

Run a JSON/path count that checks the manifest has 30 items, 30 successes, and `downloads/JP` has at least 30 company directories containing PDFs and metadata.

### Task 4: Final Verification

**Files:**
- Inspect: git diff/status and manifest counts.

- [ ] **Step 1: Run focused tests**

Run: `cd services/market-report-finder && uv run pytest tests/test_jp_service.py -q`

- [ ] **Step 2: Report exact outcome**

Report changed source/test files, manifest path, JP company count, and any failed downloads with reasons.
