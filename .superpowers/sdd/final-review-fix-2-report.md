# Final Review Fix 2

## Status

Fixed the HK evidence package force rebuild issue where a package-local `parser_result_dir` such as `package/parser` could be deleted before late parser artifact reads.

## Fix Summary

- Added `source_parser_result_dir` to keep the original parser path for normal external inputs.
- When `force=True`, `package_dir` exists, and `parser_result_dir` is a directory inside `package_dir`, the parser result directory is copied to the existing temporary staging root before `package_dir` is removed.
- Post-delete parser reads now use the staged parser directory for markdown fallback, parser quality, and `_write_parser_artifacts()`.
- External `parser_result_dir` behavior is unchanged.

## Regression Coverage

Updated `test_force_rebuild_preserves_package_local_source_inputs` to:

- Build an initial package from external raw PDF, metadata, and parser outputs.
- Rebuild using the existing package's `raw/report.pdf`, `raw/report.metadata.json`, and `parser/` directory with `force=True`.
- Assert parser `quality_report.json`, `financial_data.json`, `financial_checks.json`, `content_list_enhanced.json`, and enhanced QA payloads survive the rebuild.

## Test Output

Command:

```bash
cd /home/maoyd/siq-research-engine/services/market-report-rules && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_hk_evidence_package.py
```

Output:

```text
....                                                                     [100%]
4 passed in 0.09s
```
