# Task 7 Report: HK V2 5-sample Smoke Report Script

## Status

Implemented `scripts/hk/run_hk_v2_smoke.py` on `master` in `/home/maoyd/siq-research-engine`.

Commit created: `23c8345 feat: add HK V2 smoke report script`.

## What Changed

- Added a standard-library CLI with defaults required by the brief:
  - `--root data/wiki/hk_reports`
  - `--output docs/superpowers/reports/hk_v2_smoke_report.md`
  - `--json-output docs/superpowers/reports/hk_v2_smoke_report.json`
- Fixed the five HK sample packages from the task brief.
- Reads and reports `manifest.json`, `qa/quality_report.json`, `tables/table_index.json`, `metrics/normalized_metrics.json`, and `qa/source_map.json`.
- Adds hard failure gates for:
  - missing required base files
  - missing required V2 files
  - validator failure or unavailable validator
  - empty normalized metrics
  - empty source-map evidence
  - missing V2 package-detail path keys
  - `quality_report.overall_status` values that indicate failure
- Writes Chinese Markdown and JSON reports with sample counts, quality, warnings, missing files, V2 path gaps, import dry-run validator status, and next steps.

## TDD / Verification

1. RED:
   - Command:
     `PYTHONDONTWRITEBYTECODE=1 python3 scripts/hk/run_hk_v2_smoke.py --root data/wiki/hk_reports --output /tmp/hk_v2_smoke_report.md --json-output /tmp/hk_v2_smoke_report.json`
   - Result before implementation: exit `2`, script file missing.

2. Positive fixture:
   - Built temporary full-V2 fixtures under `/tmp/hk-v2-smoke-fixture.*/hk_reports`.
   - Command returned exit `0`.
   - JSON summary: `status=pass`, `pass_count=5`, `fail_count=0`, no missing V2 files or detail paths.
   - Fixture was removed after inspection.

3. Live HK data:
   - Command:
     `PYTHONDONTWRITEBYTECODE=1 python3 scripts/hk/run_hk_v2_smoke.py --root data/wiki/hk_reports --output docs/superpowers/reports/hk_v2_smoke_report.md --json-output docs/superpowers/reports/hk_v2_smoke_report.json`
   - Result: exit `1`, expected for current data state.
   - Generated reports clearly show all five samples lack:
     `sections/report_complete.md`, `parser/document_full.json`, `parser/content_list_enhanced.json`, `parser/table_relations.json`, `qa/footnotes.json`, `qa/toc.json`, `qa/financial_note_links.json`, `qa/table_quality_signals.json`.
   - `03988/2025/annual_12132549` also has `quality_report.overall_status=fail`.
   - Base validator passed for the live samples; smoke fails because the stricter V2 gates fail.

4. Syntax check:
   - `python3 -m py_compile scripts/hk/run_hk_v2_smoke.py`
   - Result: exit `0`.

## Generated Outputs

- Live smoke Markdown: `docs/superpowers/reports/hk_v2_smoke_report.md`
- Live smoke JSON: `docs/superpowers/reports/hk_v2_smoke_report.json`
- These are current-data reports and were left untracked, not committed.

## Notes

- No package generation, importer, API, or frontend code was modified.
- Initial implementation did not add a focused script test because there was no existing HK script test area; verification used the required CLI checks and a temporary positive fixture.

## Review Fix: package-detail V2 path validation

Updated `scripts/hk/run_hk_v2_smoke.py` so `missing_detail_paths` is validated from `read_market_package_detail(... )["paths"]` instead of reconstructing paths from files on disk. If the package-detail reader cannot be imported/read, or if the returned detail lacks a `paths` object, the sample now fails clearly and reports all required V2 detail path keys as missing. Markdown sample status labels in the failure/detail section now render as Chinese labels.

Added focused script tests in `scripts/hk/tests/test_run_hk_v2_smoke.py` covering:

- V2 files present on disk while package detail lacks V2 path entries.
- package detail reader failure.
- missing required V2 file.
- validator failure.
- empty metrics and source-map evidence.

Verification outputs:

1. RED negative case before production change:
   - Command: `PYTHONDONTWRITEBYTECODE=1 packages/market-contracts/.venv/bin/python -m pytest scripts/hk/tests/test_run_hk_v2_smoke.py -q`
   - Result: exit `1`; expected failure was `TypeError: _sample_result() got an unexpected keyword argument 'detail_reader'`.

2. Focused smoke-script tests after fix:
   - Command: `PYTHONDONTWRITEBYTECODE=1 packages/market-contracts/.venv/bin/python -m pytest scripts/hk/tests/test_run_hk_v2_smoke.py -q`
   - Result: `5 passed in 0.02s`.

3. Contract reader regression:
   - Command: `PYTHONDONTWRITEBYTECODE=1 packages/market-contracts/.venv/bin/python -m pytest packages/market-contracts/tests/test_evidence_package.py -q`
   - Result: `2 passed in 0.01s`.

4. Syntax compile check without writing bytecode:
   - Command: `python3 - <<'PY' ... compile(source, "scripts/hk/run_hk_v2_smoke.py", "exec") ... PY`
   - Result: `compile ok`.

5. Live HK smoke command:
   - Command: `PYTHONDONTWRITEBYTECODE=1 python3 scripts/hk/run_hk_v2_smoke.py --root data/wiki/hk_reports --output docs/superpowers/reports/hk_v2_smoke_report.md --json-output docs/superpowers/reports/hk_v2_smoke_report.json`
   - Result: exit `1`, output:
     - `HK V2 smoke fail: /home/maoyd/siq-research-engine/docs/superpowers/reports/hk_v2_smoke_report.md`
     - `JSON: /home/maoyd/siq-research-engine/docs/superpowers/reports/hk_v2_smoke_report.json`
   - JSON summary: `status=fail`, `fail_count=5`.
   - All five samples report the eight required missing package-detail V2 path keys: `report_complete`, `document_full`, `content_list_enhanced`, `table_relations`, `footnotes`, `toc`, `financial_note_links`, `table_quality_signals`.

Generated `docs/superpowers/reports/hk_v2_smoke_report.md` and `.json` were refreshed for verification only and intentionally left untracked/uncommitted.
