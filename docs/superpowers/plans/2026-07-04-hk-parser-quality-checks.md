# HK Parser Quality And Checks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make HK PDF parser task pages generate and display HK-specific key table candidates and financial validation checks using real HKEX annual report samples.

**Architecture:** Add a market-aware dispatcher in `apps/pdf-parser/pdf_parser_financial_service.py`. HK tasks are converted from parser result artifacts into `market-report-rules` `ParsedArtifact`, processed by the existing HK extractor/validator, then adapted back to the parser task JSON contracts (`financial_data.json`, `financial_checks.json`, `quality_report.json`). CN and unknown tasks keep the existing A-share extractor.

**Tech Stack:** Python services under `apps/pdf-parser` and `services/market-report-rules`, pytest, React/TypeScript front-end components in `apps/web`.

## Global Constraints

- Do not copy A-share labels into HK UI; use HKFRS/HKEX English/Chinese labels.
- Do not break CN parser behavior; market dispatch must default to the existing financial extractor.
- Regression must include all locally parsed HK samples discoverable under `data/pdf-parser/results`.
- Use TDD: write failing tests before implementation changes.
- Keep PostgreSQL and Milvus out of this task; this is parser-task artifacts and display only.

---

### Task 1: HK Financial Artifact Builder For PDF Parser

**Files:**
- Create: `apps/pdf-parser/hk_financial_artifacts.py`
- Modify: `apps/pdf-parser/tests/test_pdf_parser_financial_service.py`

**Interfaces:**
- Produces: `build_hk_financial_artifacts(task: dict, markdown: str, result_dir_path: str, filename: str | None = None) -> tuple[dict, dict]`
- Produces parser-compatible `financial_data` and `financial_checks` dictionaries.

- [ ] Write a failing unit test that passes a HK filename and a parser result directory with `document_full.json` / `content_list_enhanced.json`, then expects HK `financial_data["market"] == "HK"`, non-empty statements, and `financial_checks["overall_status"]` not `skipped`.
- [ ] Implement `hk_financial_artifacts.py` to infer metadata from filename/task, build `ParsedArtifact`, run `process_artifact`, and adapt extraction/validation model dumps.
- [ ] Run `PYTHONPATH=apps/pdf-parser:services/market-report-rules/src apps/pdf-parser/.venv/bin/python -m pytest apps/pdf-parser/tests/test_pdf_parser_financial_service.py -q`.

### Task 2: Market Dispatcher In Financial Service

**Files:**
- Modify: `apps/pdf-parser/pdf_parser_financial_service.py`
- Modify: `apps/pdf-parser/tests/test_pdf_parser_financial_service.py`

**Interfaces:**
- Consumes: `build_hk_financial_artifacts(...)` from Task 1.
- Keeps existing `write_financial_artifacts(...)` signature.

- [ ] Write a failing test proving HK task filenames call HK builder while CN/unknown tasks call the legacy builder.
- [ ] Add `_detect_market(task, filename)` helper using `task.submit_config.market`, `task.market`, and filename tokens like `_HK_` / `hkex`.
- [ ] Update `write_financial_artifacts` to dispatch HK to `build_hk_financial_artifacts` and legacy otherwise.
- [ ] Run the financial service tests.

### Task 3: HK Quality Candidate Adapter

**Files:**
- Create: `apps/pdf-parser/hk_quality_adapter.py`
- Modify: `apps/pdf-parser/pdf_parser_quality_service.py`
- Test: `apps/pdf-parser/tests/test_pdf_parser_quality_service.py`

**Interfaces:**
- Produces: `merge_hk_quality_candidates(report: dict, financial_data: dict, financial_checks: dict) -> dict`.

- [ ] Write a failing test with HK `financial_data` containing balance/income/cashflow facts and assert `quality.market == "HK"`, `core_financial_table_candidates` contains `Statement of Financial Position`, `Statement of Profit or Loss`, and `Statement of Cash Flows` with table indexes.
- [ ] Implement HK label mapping from statement types and industry profile to display names.
- [ ] Add `quality["market"]`, `quality["accounting_standard"]`, `quality["industry_profile"]`, `quality["hk_key_table_candidates"]`, and HK `core_financial_table_candidates`.
- [ ] Ensure CN quality merge still keeps existing A-share labels.
- [ ] Run quality service tests.

### Task 4: Frontend HK-Aware Display

**Files:**
- Modify: `apps/web/src/components/pdf/PdfQualityPanel.tsx`
- Modify: `apps/web/src/lib/pdfTypes.ts`
- Modify/Test: existing PDF parsing or component tests if available.

**Interfaces:**
- Consumes: `quality.market`, `quality.hk_key_table_candidates`, and market-aware `core_financial_table_candidates`.

- [ ] Add test/fixture or lightweight TypeScript assertion that HK quality report renders HK candidate names, not A-share labels.
- [ ] Extend `QualityReport` type with `market`, `accounting_standard`, `industry_profile`, `hk_key_table_candidates`.
- [ ] Update panel title/copy to use generic `关键表候选` but render HK names from quality payload.
- [ ] Run web targeted tests if local dependencies allow.

### Task 5: All HK Sample Regression

**Files:**
- Create: `apps/pdf-parser/tests/test_hk_parser_samples_regression.py`
- Optionally create helper under `apps/pdf-parser/tests/fixtures` only if needed.

**Interfaces:**
- Discovers local HK parser samples under `data/pdf-parser/results` by `document_full.task.filename` containing `_HK_` or `hkex`.

- [ ] Write a regression test that samples all discovered HK parser result directories and calls `build_hk_financial_artifacts`.
- [ ] Assert every sample returns `market == "HK"`, schema versions, and non-empty `quality` candidates when tables exist; for zero-table samples assert explicit warning rather than crash.
- [ ] For LINK REIT task `50090c9f-a424-4d73-b28c-96fa60dd99ff`, assert `financial_checks.overall_status != "skipped"` and at least one HK key candidate is produced.
- [ ] Run the HK regression test.

### Task 6: Rebuild LINK REIT Artifacts And Verify UI Payload

**Files:**
- No code file required unless tests reveal gaps.

**Interfaces:**
- Uses existing result dir `data/pdf-parser/results/50090c9f-a424-4d73-b28c-96fa60dd99ff`.

- [ ] Run a small rebuild script or service function to regenerate `financial_data.json`, `financial_checks.json`, and merge `quality_report.json` for LINK REIT.
- [ ] Inspect JSON and confirm HK candidate names and non-skipped financial checks.
- [ ] Run targeted backend/frontend tests and `git diff --check`.
- [ ] Commit and push after user-confirmed scope.
EOF'