# Task 6 Report: HK frontend/status package visibility

## Status
Complete on `master` in `/home/maoyd/siq-research-engine` via SSH alias `spark-1319`.

## Requirements Source
Read `/home/maoyd/siq-research-engine/.superpowers/sdd/task-6-brief.md` before implementation. Requirements were clear; no `NEEDS_CONTEXT` was needed.

## Existing Backend State
The required backend regression `test_market_package_detail_returns_hk_v2_paths` was already present in `apps/api/tests/test_market_reports_proxy.py`. The API router already routes market package detail through `_read_market_package_detail()`, which delegates to the shared package reader and returns `paths`, `parser_artifacts`, and `qa_artifacts`.

No backend production changes were needed.

## Red Phase
Added a lightweight frontend unit test for the new path grouping helper:

- `groupMarketPackagePaths groups HK V2 parser and QA files from dynamic paths`
- It asserts returned dynamic paths are grouped into `manifest/quality/source/financial/parser/qa/sections/tables`, including `parser/document_full.json`, `parser/content_list_enhanced.json`, `sections/report_complete.md`, and `qa/footnotes.json`.

After fixing an initial test syntax typo, the meaningful red failure was:

```text
Error [ERR_MODULE_NOT_FOUND]: Cannot find module '/home/maoyd/siq-research-engine/apps/web/src/features/market-parsing/packageFiles.ts'
```

## Implementation
Changed only the frontend package status surface:

- Added `apps/web/src/features/market-parsing/packageFiles.ts` with `groupMarketPackagePaths()`.
- Updated `MarketEvidencePackagesPanel` to render backend-returned `paths` dynamically in stable groups instead of one flat list.
- Kept existing counts and quality JSON rendering intact.
- Added `parser_artifacts` and `qa_artifacts` to the `MarketPackageDetail` TypeScript contract, matching the API payload already returned by the shared package reader.

No importer, DDL, API command defaults, package generation, or backend router behavior was changed.

## Verification
Ran the focused backend command from the brief:

```bash
cd /home/maoyd/siq-research-engine/apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_market_reports_proxy.py
```

Result:

```text
60 passed, 4 warnings in 0.56s
```

Ran the frontend unit command for the new helper. The repo runner currently executes all node unit tests rather than honoring the file argument:

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm run test:unit -- src/features/market-parsing/packageFiles.test.ts
```

Result:

```text
102 passed, 0 failed
```

Ran frontend build/typecheck:

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm run build
```

Result:

```text
tsc -b && vite build
✓ built
```

## Files Changed

- `apps/web/src/components/sec/MarketEvidencePackagesPanel.tsx`
- `apps/web/src/features/market-parsing/api.ts`
- `apps/web/src/features/market-parsing/packageFiles.ts`
- `apps/web/src/features/market-parsing/packageFiles.test.ts`
- `.superpowers/sdd/task-6-report.md`

## Concerns
The checked-in/live HK packages under `data/wiki/hk_reports` currently have manifests but no sampled V2 parser/QA files, so browser UI verification with real package artifacts is deferred to Task 8 or to a host state where HK V2 package files exist.
