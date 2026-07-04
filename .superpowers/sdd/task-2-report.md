# Task 2 Report: package reader exposes HK V2 paths/counts

## Scope
- Repo: `/home/maoyd/siq-research-engine`
- Branch: `master`
- Owned files:
  - `packages/market-contracts/src/siq_market_contracts/evidence_package.py`
  - `packages/market-contracts/tests/test_evidence_package.py`
  - `apps/api/tests/test_market_reports_proxy.py`

## Requirements handled
- `read_market_package_summary()` now exposes optional HK V2 file paths in `paths`.
- `read_market_package_detail()` now exposes grouped `parser_artifacts` and `qa_artifacts` payloads.
- Existing package compatibility is preserved because no HK V2 file was added to `REQUIRED_FILES`.
- API-level coverage confirms the shared reader output flows through the market package detail endpoints.

## TDD log
1. Extended the HK fixture in `packages/market-contracts/tests/test_evidence_package.py` with V2 artifacts:
   - `parser/document_full.json`
   - `parser/content_list_enhanced.json`
   - `parser/table_relations.json`
   - `sections/report_complete.md`
   - `qa/footnotes.json`
   - `qa/toc.json`
   - `qa/financial_note_links.json`
   - `qa/table_quality_signals.json`
2. Added assertions that `summary["paths"]` includes:
   - `document_full`
   - `content_list_enhanced`
   - `report_complete`
   - `footnotes`
   - `toc`
   - `financial_note_links`
   - `table_quality_signals`
3. Added assertions that `detail` exposes `parser_artifacts` and `qa_artifacts` JSON payloads.
4. Added API test `test_market_package_detail_returns_hk_v2_paths` in `apps/api/tests/test_market_reports_proxy.py` covering both by-path and by-filing-id detail readers.

## Red phase evidence
### 1) Package contracts test
Command:
```bash
cd /home/maoyd/siq-research-engine/packages/market-contracts
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_evidence_package.py
```
Result:
- Failed in `test_validate_and_read_market_package`
- Failure: `KeyError: 'document_full'` from `summary["paths"]["document_full"]`

### 2) API test command from brief
Command from brief:
```bash
cd /home/maoyd/siq-research-engine/apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_market_reports_proxy.py::test_market_package_quality_by_path_and_filing_id
```
Result:
- Stale on current branch
- Pytest reported `not found` for `test_market_package_quality_by_path_and_filing_id`

Decision:
- Used the nearest focused API targets in the same file after implementation:
  - `tests/test_market_reports_proxy.py::test_market_package_detail_returns_hk_v2_paths`
  - `tests/test_market_reports_proxy.py::test_market_package_quality_routes_keep_response_contract`

## Implementation summary
### `packages/market-contracts/src/siq_market_contracts/evidence_package.py`
- Extended `PACKAGE_FILE_PATHS` with optional HK V2 mappings:
  - `report_complete`
  - `document_full`
  - `content_list_enhanced`
  - `table_relations`
  - `footnotes`
  - `toc`
  - `financial_note_links`
  - `table_quality_signals`
- Added `_artifact_payloads()` helper to load only existing optional artifacts.
- Updated `read_market_package_detail()` to return:
  - `parser_artifacts`
  - `qa_artifacts`
- Left `REQUIRED_FILES` unchanged, so legacy US/JP/KR/EU packages remain valid without V2 files.

### `packages/market-contracts/tests/test_evidence_package.py`
- Expanded HK package fixture with representative parser/QA V2 files.
- Added summary path assertions and detail artifact assertions.

### `apps/api/tests/test_market_reports_proxy.py`
- Added `_write_hk_v2_package()` helper fixture builder.
- Added `test_market_package_detail_returns_hk_v2_paths` to verify endpoint/helper output includes HK V2 file paths.

## Verification
### Package contracts
Command:
```bash
cd /home/maoyd/siq-research-engine/packages/market-contracts
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_evidence_package.py
```
Result:
- `2 passed in 0.02s`

### API focused tests
Command:
```bash
cd /home/maoyd/siq-research-engine/apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_market_reports_proxy.py::test_market_package_detail_returns_hk_v2_paths \
  tests/test_market_reports_proxy.py::test_market_package_quality_routes_keep_response_contract
```
Result:
- `2 passed, 2 warnings in 0.43s`
- Warnings are existing Pydantic deprecation warnings from `apps/api/schemas.py`

## Compatibility / risk notes
- HK V2 file exposure is optional-by-presence only.
- No validator requirements were tightened.
- No package generation, importer, DDL, frontend, or API default behavior was changed.

## Commit
- Commit created after focused verification.
