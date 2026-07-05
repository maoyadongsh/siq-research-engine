# Task 3 Report: extend `pdf2md_hk` schema for V2 structure

## Scope
Implemented only the two owned files:
- `/home/maoyd/siq-research-engine/db/ddl/020_create_pdf2md_hk_schema.sql`
- `/home/maoyd/siq-research-engine/db/imports/tests/test_import_hk_evidence_package.py`

Did not implement importer write logic; Task 4 owns that.

## Requirements source
Read and followed:
- `/home/maoyd/siq-research-engine/.superpowers/sdd/task-3-brief.md`

## TDD flow
1. Added `test_hk_ddl_contains_v2_tables_and_identity_columns` to assert the DDL text contains the required V2 markers:
   - `short_name`
   - `stock_code`
   - `hkex_stock_code`
   - `content_blocks`
   - `footnotes`
   - `toc_entries`
   - `financial_note_links`
   - `table_relations`
   - `parser_artifacts`
   - `table_quality_signals`
2. Ran the focused test file before touching the DDL.
3. Observed the expected failing assertion on missing `short_name` in the schema text.
4. Implemented the minimal repeatable DDL changes.
5. Re-ran the focused test file and verified it passed.

## Changes made
### Test file
Added a DDL text regression test in:
- `/home/maoyd/siq-research-engine/db/imports/tests/test_import_hk_evidence_package.py`

### DDL file
Extended `/home/maoyd/siq-research-engine/db/ddl/020_create_pdf2md_hk_schema.sql` with repeatable schema changes.

#### Companies columns
Used `alter table ... add column if not exists` to add:
- `stock_code text`
- `hkex_stock_code text`
- `short_name text`
- `company_name_en text`
- `company_name_zh text`
- `aliases jsonb not null default '[]'::jsonb`

#### Filings repeatability guard
Added:
- `alter table pdf2md_hk.filings add column if not exists stock_code text;`

#### New V2 tables
Added `create table if not exists` definitions for:
- `pdf2md_hk.parser_artifacts`
- `pdf2md_hk.content_blocks`
- `pdf2md_hk.footnotes`
- `pdf2md_hk.toc_entries`
- `pdf2md_hk.financial_note_links`
- `pdf2md_hk.table_relations`
- `pdf2md_hk.table_quality_signals`

Each new table includes:
- `filing_id`
- `parse_run_id`
- a stable primary key
- page/table/target fields
- `raw jsonb`

#### Indexes
Added common indexes covering `parse_run_id`, `filing_id`, `page_number`, and `table_index` for the new tables.

## Verification
Focused test command executed from repo root on `spark-1319`:

```bash
if [ -x apps/api/.venv/bin/python ]; then PY=apps/api/.venv/bin/python; else PY=python3; fi
PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest -q -p no:cacheprovider db/imports/tests/test_import_hk_evidence_package.py
```

### Red phase result
- `1 failed, 2 passed`
- Failure was the new DDL text test, asserting `short_name` was missing from the schema text.

### Green phase result
- `3 passed in 0.04s`

## Commit
Created commit:
- `f34ef5bf54729b9c2257ce262536c5ea70533d18` `Extend pdf2md_hk schema for V2 structure`

## Concerns
- The new V2 tables are schema-only placeholders for Task 4 importer write logic; no insert/upsert code was added here by design.
- The DDL text test checks presence of required markers, not full column-by-column table contracts.
