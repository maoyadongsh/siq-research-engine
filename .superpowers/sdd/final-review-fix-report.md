# HK V2 Final Review Fix Report

## Fix Summary

1. `db/imports/import_hk_evidence_package_to_postgres.py`
   - `_upsert_filing()` now writes `manifest["stock_code"]` to `pdf2md_hk.filings.stock_code`.
   - When `stock_code` is missing or empty, it falls back to `manifest["ticker"]`.
   - Conflict updates now include `stock_code = excluded.stock_code`.

2. `scripts/hk/hk_evidence_lib.py`
   - HK evidence package generation now reads standalone `parser_result_dir/content_list_enhanced.json`.
   - Embedded `document_full["content_list_enhanced"]` remains compatible and is used when populated.
   - Standalone enhanced content is used as fallback for parser artifacts, QA artifacts, table relations, report completion, and parsed table metadata when `document_full.json` lacks embedded enhanced content.

## Regression Coverage

- Added importer regression coverage for `filings.stock_code`, including conflict update SQL and ticker fallback.
- Added HK builder regression coverage where `document_full.json` has no embedded enhanced payload but `parser_result_dir/content_list_enhanced.json` exists. The test verifies:
  - `parser/content_list_enhanced.json`
  - `parser/table_relations.json`
  - `qa/footnotes.json`
  - `qa/toc.json`
  - `qa/financial_note_links.json`
  - `qa/table_quality_signals.json`

## Test Output

```text
$ cd /home/maoyd/siq-research-engine/services/market-report-rules
$ PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_hk_evidence_package.py
....                                                                     [100%]
4 passed in 0.09s
```

```text
$ cd /home/maoyd/siq-research-engine
$ PYTHONDONTWRITEBYTECODE=1 apps/api/.venv/bin/python -m pytest -q -p no:cacheprovider db/imports/tests/test_import_hk_evidence_package.py
..........                                                               [100%]
10 passed in 0.07s
```

## Notes

- No API/frontend/DDL/package reader/smoke script changes.
- No fabricated enhanced data; the builder only reads the existing standalone parser artifact as fallback.
- `market_evidence_package_v1` compatibility is preserved by keeping existing empty/default QA outputs when neither embedded nor standalone enhanced content is present.
