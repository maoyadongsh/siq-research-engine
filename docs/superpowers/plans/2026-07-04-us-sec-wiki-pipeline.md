# US SEC Wiki Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a US SEC wiki batch pipeline that discovers downloaded filings, regenerates shared market evidence packages, writes company-level wiki indexes, and produces case-set metadata for PostgreSQL ingestion.

**Architecture:** Keep single-filing parsing in `sec_evidence_lib.py`; add pure importable scripts for downloaded-case discovery and wiki index aggregation; add a small batch CLI that composes the two. Downstream PostgreSQL ingestion reads evidence packages and case-set paths from `data/wiki/us_sec`; Milvus ingestion is deferred.

**Tech Stack:** Python 3.13 in `apps/api/.venv`, BeautifulSoup/lxml already used by SEC parser, shared `market_report_rules_service.evidence_package` contract helpers, pytest.

## Global Constraints

- US filings are HTML/iXBRL, not PDF; do not introduce page-based assumptions.
- Wiki package contract is `market_evidence_package_v1`.
- PostgreSQL target remains `siq_us.sec_us`; wiki scripts must not write database rows.
- Milvus ingestion is out of scope for this phase; wiki and PostgreSQL scripts must not require Milvus dependencies.
- Build output root defaults to `data/wiki/us_sec`.
- Download root defaults to `data/market-report-finder/downloads/US`.
- Keep changes scoped to US SEC wiki scripts, tests, and documentation.

---

## File Structure

- Create `scripts/us-sec/discover_sec_downloaded_cases.py`: scan local SEC downloads and write `_meta/downloads_index.json`.
- Create `scripts/us-sec/build_sec_wiki_index.py`: aggregate package manifests/metrics into company wiki indexes, root package index, quality summary, and case set.
- Create `scripts/us-sec/build_sec_wiki.py`: batch orchestration CLI.
- Modify `scripts/us-sec/sec_evidence_lib.py`: ensure manifest and source artifacts comply with `market_evidence_package_v1` and validation-friendly fields.
- Create `scripts/us-sec/tests/test_sec_wiki_pipeline.py`: unit tests for discovery, indexing, and package manifest behavior.
- Modify `docs/architecture/us-sec-archive-ingestion.md`: document the new wiki build entrypoint.

### Task 1: Download Discovery

**Files:**
- Create: `scripts/us-sec/discover_sec_downloaded_cases.py`
- Test: `scripts/us-sec/tests/test_sec_wiki_pipeline.py`

**Interfaces:**
- Produces: `scan_downloads(downloads_root: Path, forms: set[str] | None = None, tickers: set[str] | None = None, limit: int = 0) -> list[dict[str, Any]]`
- Produces: `write_downloads_index(rows: list[dict[str, Any]], output_root: Path) -> Path`

- [ ] **Step 1: Write failing tests**

```python
def test_scan_downloads_normalizes_sec_metadata(tmp_path):
    html = tmp_path / "ACME" / "2025" / "annual" / "ACME_US_ACME_2025-12-31_10-K_2026-02-01_sec_a.html"
    html.parent.mkdir(parents=True)
    html.write_text("<html></html>", encoding="utf-8")
    html.with_suffix(".html.metadata.json").write_text(json.dumps({"candidate": {"ticker": "ACME", "company_id": "123", "company_name": "ACME INC", "form": "10-K", "report_end": "2025-12-31", "published_at": "2026-02-01", "accession_number": "0000000123-26-000001", "document_url": "https://www.sec.gov/x.htm", "inline_xbrl": True}}), encoding="utf-8")
    rows = discovery.scan_downloads(tmp_path, forms={"10-K"})
    assert rows[0]["ticker"] == "ACME"
    assert rows[0]["fiscal_year"] == 2025
    assert rows[0]["source_sha256"]
```

- [ ] **Step 2: Run red test**

Run: `PYTHONPATH=scripts/us-sec apps/api/.venv/bin/python -m pytest scripts/us-sec/tests/test_sec_wiki_pipeline.py::test_scan_downloads_normalizes_sec_metadata -q`
Expected: FAIL because `discover_sec_downloaded_cases.py` does not exist.

- [ ] **Step 3: Implement discovery**

Create the module with JSON helpers, `sha256_file`, metadata inference from finder metadata and filename fallback, deterministic sorting, CLI args `--downloads-root`, `--output-root`, `--forms`, `--tickers`, `--limit`.

- [ ] **Step 4: Run green test**

Run: `PYTHONPATH=scripts/us-sec apps/api/.venv/bin/python -m pytest scripts/us-sec/tests/test_sec_wiki_pipeline.py::test_scan_downloads_normalizes_sec_metadata -q`
Expected: PASS.

### Task 2: Company Wiki Index

**Files:**
- Create: `scripts/us-sec/build_sec_wiki_index.py`
- Test: `scripts/us-sec/tests/test_sec_wiki_pipeline.py`

**Interfaces:**
- Consumes: package directories with `manifest.json`, `metrics/*.json`, and `qa/quality_report.json`
- Produces: `build_wiki_index(output_root: Path, forms: set[str] | None = None, tickers: set[str] | None = None, case_set_name: str = "case_set_50_us_10k.json") -> dict[str, Any]`

- [ ] **Step 1: Write failing tests**

```python
def test_build_wiki_index_writes_company_and_root_indexes(tmp_path):
    package = make_package(tmp_path / "AAPL" / "2025" / "10-K_0000320193-25-000079", ticker="AAPL", quality="pass")
    summary = indexer.build_wiki_index(tmp_path, forms={"10-K"})
    assert summary["package_count"] == 1
    assert (tmp_path / "AAPL" / "company.json").is_file()
    assert (tmp_path / "AAPL" / "metrics" / "latest" / "financial_data.json").is_file()
    assert (tmp_path / "_meta" / "package_index.json").is_file()
    assert (tmp_path / "case_set_50_us_10k.json").is_file()
```

- [ ] **Step 2: Run red test**

Run: `PYTHONPATH=scripts/us-sec apps/api/.venv/bin/python -m pytest scripts/us-sec/tests/test_sec_wiki_pipeline.py::test_build_wiki_index_writes_company_and_root_indexes -q`
Expected: FAIL because `build_sec_wiki_index.py` does not exist.

- [ ] **Step 3: Implement index builder**

Create package discovery, summary extraction, latest filing selection, company JSON/Markdown writers, metric copy helpers, root package index, quality summary, and case-set writer.

- [ ] **Step 4: Run green test**

Run: `PYTHONPATH=scripts/us-sec apps/api/.venv/bin/python -m pytest scripts/us-sec/tests/test_sec_wiki_pipeline.py::test_build_wiki_index_writes_company_and_root_indexes -q`
Expected: PASS.

### Task 3: Shared US Evidence Contract

**Files:**
- Modify: `scripts/us-sec/sec_evidence_lib.py`
- Test: `scripts/us-sec/tests/test_sec_wiki_pipeline.py`

**Interfaces:**
- Produces: `manifest.json` with `schema_version=market_evidence_package_v1`, `country`, `source_tier`, `document_format`, `parse_run_id`, `quality_status`, and non-empty `artifact_hashes`.

- [ ] **Step 1: Write failing test**

```python
def test_sec_manifest_uses_market_evidence_contract(monkeypatch, tmp_path):
    monkeypatch.setattr(sec_evidence_lib, "normalize_metrics", fake_normalize_metrics)
    package = sec_evidence_lib.write_evidence_package(make_ixbrl_fixture(tmp_path), tmp_path / "wiki", metadata_path=make_metadata(tmp_path), force=True)
    manifest = json.loads((package / "manifest.json").read_text())
    assert manifest["schema_version"] == "market_evidence_package_v1"
    assert manifest["country"] == "US"
    assert manifest["source_tier"] == "official"
    assert manifest["document_format"] == "ixbrl_html"
    assert manifest["parse_run_id"]
```

- [ ] **Step 2: Run red test**

Run: `PYTHONPATH=scripts/us-sec:services/market-report-rules/src apps/api/.venv/bin/python -m pytest scripts/us-sec/tests/test_sec_wiki_pipeline.py::test_sec_manifest_uses_market_evidence_contract -q`
Expected: FAIL because current manifests miss the new shared fields.

- [ ] **Step 3: Implement manifest contract fields**

Use `stable_parse_run_id` from the shared evidence package module after computing artifact hashes. Add `country`, `source_tier`, `document_format`, and complete artifact paths without changing existing downstream field names.

- [ ] **Step 4: Run green test**

Run: `PYTHONPATH=scripts/us-sec:services/market-report-rules/src apps/api/.venv/bin/python -m pytest scripts/us-sec/tests/test_sec_wiki_pipeline.py::test_sec_manifest_uses_market_evidence_contract -q`
Expected: PASS.

### Task 4: Batch Wiki CLI

**Files:**
- Create: `scripts/us-sec/build_sec_wiki.py`
- Test: `scripts/us-sec/tests/test_sec_wiki_pipeline.py`

**Interfaces:**
- Consumes: `discover_sec_downloaded_cases.scan_downloads`, `sec_evidence_lib.write_evidence_package`, `build_sec_wiki_index.build_wiki_index`
- Produces: CLI report JSON printed to stdout and optional report path.

- [ ] **Step 1: Write failing test**

```python
def test_batch_build_invokes_discovery_package_and_index(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(build_sec_wiki.discovery, "scan_downloads", lambda *a, **k: [{"source_path": str(tmp_path / "a.html"), "metadata_path": None, "ticker": "AAPL", "form": "10-K"}])
    monkeypatch.setattr(build_sec_wiki.sec_evidence_lib, "write_evidence_package", lambda *a, **k: calls.append("package") or (tmp_path / "wiki" / "AAPL" / "2025" / "10-K_x"))
    monkeypatch.setattr(build_sec_wiki.indexer, "build_wiki_index", lambda *a, **k: {"package_count": 1})
    report = build_sec_wiki.build_sec_wiki(tmp_path, tmp_path / "wiki", forms={"10-K"}, force=False)
    assert calls == ["package"]
    assert report["index"]["package_count"] == 1
```

- [ ] **Step 2: Run red test**

Run: `PYTHONPATH=scripts/us-sec apps/api/.venv/bin/python -m pytest scripts/us-sec/tests/test_sec_wiki_pipeline.py::test_batch_build_invokes_discovery_package_and_index -q`
Expected: FAIL because `build_sec_wiki.py` does not exist.

- [ ] **Step 3: Implement batch CLI**

Add `build_sec_wiki(...)`, CLI args `--downloads-root`, `--output-root`, `--forms`, `--tickers`, `--limit`, `--force`, `--incremental`, `--continue-on-error`, `--report`, and deterministic JSON report writing.

- [ ] **Step 4: Run green test and smoke command**

Run: `PYTHONPATH=scripts/us-sec apps/api/.venv/bin/python -m pytest scripts/us-sec/tests/test_sec_wiki_pipeline.py -q`
Expected: all tests PASS.

Smoke: `apps/api/.venv/bin/python scripts/us-sec/build_sec_wiki.py --downloads-root data/market-report-finder/downloads/US --output-root data/wiki/us_sec --forms 10-K --force --continue-on-error --report data/wiki/us_sec/_meta/build_report.json`
Expected: report JSON exists and package/index counts are non-zero.

### Task 5: Documentation And Final Verification

**Files:**
- Modify: `docs/architecture/us-sec-archive-ingestion.md`

- [ ] **Step 1: Document commands**

Add the new wiki build command and downstream handoff command for PostgreSQL.

- [ ] **Step 2: Run verification**

Run:

```bash
PYTHONPATH=scripts/us-sec:services/market-report-rules/src apps/api/.venv/bin/python -m pytest scripts/us-sec/tests/test_sec_wiki_pipeline.py db/imports/tests/test_import_sec_filing_to_postgres.py -q
apps/api/.venv/bin/python -m py_compile scripts/us-sec/discover_sec_downloaded_cases.py scripts/us-sec/build_sec_wiki_index.py scripts/us-sec/build_sec_wiki.py scripts/us-sec/sec_evidence_lib.py
```

Expected: pytest exits 0 and py_compile exits 0.
