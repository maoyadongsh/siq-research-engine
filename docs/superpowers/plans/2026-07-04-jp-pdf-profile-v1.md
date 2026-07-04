# JP PDF Profile v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Japan-market PDF parsing profile so JP quality reports and financial checks no longer reuse A-share sections, table names, and missing-statement warnings.

**Architecture:** Keep A-share rules intact and add a market-aware profile layer for JP. The PDF parser will select JP sections/core table candidates based on `task.submit_config.market` or filename markers, while financial artifacts will mark JP as a candidate-identification profile with no A-share three-statement failure warnings unless JP-specific structured statements are later available.

**Tech Stack:** Python PDF parser services/tests, existing Node frontend unit tests, React market parsing panels.

---

### Task 1: JP Market Profile Rule Module

**Files:**
- Create: `apps/pdf-parser/jp_market_profile.py`
- Test: `apps/pdf-parser/tests/test_jp_market_profile.py`

- [ ] Write tests proving JP profile detects JP market from task/filename, classifies integrated report vs annual securities report, and finds `Financial Highlights`, balance sheet, income statement, cash flow, equity, and segment candidates from table index text.
- [ ] Run: `cd apps/pdf-parser && python3 -m pytest tests/test_jp_market_profile.py -q`; expect failures before implementation.
- [ ] Implement JP constants and helper functions in `jp_market_profile.py`.
- [ ] Re-run the test and confirm it passes.

### Task 2: Wire JP Quality Report Profile

**Files:**
- Modify: `apps/pdf-parser/pdf_parser_app_impl.py`
- Test: `apps/pdf-parser/tests/test_page_markers.py`

- [ ] Add tests calling `_build_quality_report` with a JP task and English/Japanese JP table content.
- [ ] Verify red: JP report currently contains A-share `missing_sections` and A-share `core_financial_table_candidates`.
- [ ] Modify `_build_quality_report` to select JP sections, JP core table names, JP indicator table names, and JP candidate grouping when task market is JP.
- [ ] Re-run targeted tests.

### Task 3: JP Financial Artifacts And Checks

**Files:**
- Modify: `apps/pdf-parser/pdf_parser_financial_service.py`
- Modify: `apps/pdf-parser/financial_extractor.py`
- Test: `apps/pdf-parser/tests/test_pdf_parser_financial_service.py`
- Test: `apps/pdf-parser/tests/test_financial_extractor.py`

- [ ] Add tests that JP market financial artifact generation adds `market: JP`, classifies JP report kind, and does not emit `未提取到合并资产负债表/利润表/现金流量表` warnings.
- [ ] Verify red.
- [ ] Pass `market` into `build_financial_data`, store it in `financial_data`, and let `build_financial_checks` skip A-share missing-three-table warnings for JP with a JP-specific informational warning.
- [ ] Re-run targeted tests.

### Task 4: Frontend JP Display Labels

**Files:**
- Modify: `apps/web/src/components/pdf/PdfQualityPanel.tsx`
- Modify: `apps/web/src/components/pdf/PdfFinancialPanel.tsx`
- Test: `apps/web/src/components/pdf/pdfMarketPanels.test.ts`

- [ ] Add Node unit tests for market-aware display model helpers that rename JP financial checks to `日本财务识别与一致性检查` and preserve artifact list.
- [ ] Verify red.
- [ ] Implement small pure helpers or prop-based label selection so `MarketParsingPage` passes `market` into quality/financial panels.
- [ ] Re-run targeted frontend tests.

### Task 5: Verification

**Files:**
- No new production files unless required by failing checks.

- [ ] Run: `cd apps/pdf-parser && python3 -m pytest tests/test_jp_market_profile.py tests/test_pdf_parser_financial_service.py tests/test_financial_extractor.py tests/test_page_markers.py -q`.
- [ ] Run: `cd apps/web && PATH=/home/maoyd/.local/bin:$PATH npm run test:unit`.
- [ ] Run: `cd apps/web && PATH=/home/maoyd/.local/bin:$PATH npm run check:frontend`.
- [ ] Use a completed JP sample task to refresh/read quality and financial artifacts enough to verify the user-visible A-share warnings are gone for JP.
