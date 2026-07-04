# HK PostgreSQL Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the HK market PostgreSQL ingestion path so HK evidence packages remain the primary evidence source while PostgreSQL stores structured facts, evidence coordinates, and agent-friendly fallback indexes.

**Architecture:** HK parsing produces a company Wiki evidence package under `data/wiki/hk/companies/.../reports/...`; the importer reads only structured package artifacts and writes them into `siq_hk.pdf2md_hk`. The frontend HK page should use market package build/import APIs instead of the generic PDF workflow for the HK main path, while agents continue to prefer Wiki package files and use PostgreSQL views only for structured lookup and fallback evidence coordinates.

**Tech Stack:** Python 3, psycopg, PostgreSQL DDL SQL, FastAPI market report router, React/Vite frontend, Node unit tests, pytest.

## Global Constraints

- Wiki package is the primary evidence entrypoint; PostgreSQL is a synchronized structured index and fallback query layer.
- Do not extract financial facts from Wiki Markdown or other natural-language renderings.
- Import sources are structured artifacts: `manifest.json`, `metrics/financial_data.json`, `qa/source_map.json`, `tables/table_index.json`, `parser/document_full.json`, `metrics/financial_checks.json`, `qa/quality_report.json`, and enhancement JSON files.
- Every financial fact that can answer a question must preserve evidence linkage to page number, table index, row/column index, bbox, or `evidence_id` when available.
- HK company identity is anchored by market plus five-digit HKEX stock code, not by mutable company names.
- Default database is `siq_hk`; schema is fixed to `pdf2md_hk`.
- Keep Milvus out of this implementation except for preserving existing disabled/dry-run behavior.

---

## File Structure

- Modify `db/ddl/020_create_pdf2md_hk_schema.sql`: add missing columns, constraints, indexes, and agent views without breaking existing tables.
- Modify `db/imports/import_hk_evidence_package_to_postgres.py`: make the importer read structured HK package files directly, upsert richer company/filing metadata, and import evidence, tables, blocks, financial facts, checks, quality, and retrieval chunks.
- Modify `db/imports/tests/test_import_hk_evidence_package.py`: add unit tests for schema targeting, identity, SQL calls, evidence page coordinates, statement item rows, and retrieval chunks.
- Modify `apps/api/tests/test_market_reports_proxy.py`: lock the `/api/market-reports/packages/import` HK command plan and database env behavior.
- Modify `apps/web/src/features/market-parsing/packageActions.ts`, `apps/web/src/features/market-parsing/packageActions.test.ts`, `apps/web/src/pages/HkParsing.tsx`, and `apps/web/src/pages/MarketParsingPage.tsx`: connect HK page controls to market package build/import actions and avoid generic PDF workflow as the HK main path.
- Modify `agents/hermes/profiles/siq_analysis/rules/data_sources.md` and `agents/hermes/profiles/siq_assistant/rules/data_sources.md`: document Wiki-first, PostgreSQL-fallback lookup order for HK.

---

### Task 1: HK DDL Contract For Structured Ingestion

**Files:**
- Modify: `db/ddl/020_create_pdf2md_hk_schema.sql`
- Test: `db/imports/tests/test_import_hk_evidence_package.py`

**Interfaces:**
- Consumes: existing `pdf2md_hk` tables and current importer `run_ddl(conn)`.
- Produces: schema columns/views used by later importer tasks: `filings.report_id`, `pdf_tables.bbox`, `evidence_citations.bbox`, richer `retrieval_chunks`, `v_agent_financial_facts`, and `v_latest_company_reports`.

- [ ] **Step 1: Write the failing DDL test**

Add `test_hk_ddl_exposes_agent_recall_columns_and_views` to `db/imports/tests/test_import_hk_evidence_package.py`. It should read `importer.DDL_PATH` and assert these exact snippets exist:

```python
assert "alter table pdf2md_hk.filings add column if not exists report_id text" in ddl
assert "alter table pdf2md_hk.pdf_tables add column if not exists bbox jsonb" in ddl
assert "alter table pdf2md_hk.evidence_citations add column if not exists bbox jsonb" in ddl
assert "alter table pdf2md_hk.retrieval_chunks add column if not exists company_id text" in ddl
assert "alter table pdf2md_hk.retrieval_chunks add column if not exists text text" in ddl
assert "create or replace view pdf2md_hk.v_agent_financial_facts" in ddl
assert "create or replace view pdf2md_hk.v_latest_company_reports" in ddl
assert "unique" in ddl.lower() and "hkex_stock_code" in ddl.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd /home/maoyd/siq-research-engine
apps/api/.venv/bin/python -m pytest db/imports/tests/test_import_hk_evidence_package.py::test_hk_ddl_exposes_agent_recall_columns_and_views -q --tb=short
```

Expected: FAIL because one or more schema snippets are missing.

- [ ] **Step 3: Implement DDL additions**

Add idempotent `alter table`, indexes, and views to `db/ddl/020_create_pdf2md_hk_schema.sql`:

```sql
alter table pdf2md_hk.companies add column if not exists exchange text;
alter table pdf2md_hk.filings add column if not exists report_id text;
alter table pdf2md_hk.pdf_tables add column if not exists bbox jsonb;
alter table pdf2md_hk.evidence_citations add column if not exists bbox jsonb;
alter table pdf2md_hk.retrieval_chunks add column if not exists company_id text;
alter table pdf2md_hk.retrieval_chunks add column if not exists section_title text;
alter table pdf2md_hk.retrieval_chunks add column if not exists statement_type text;
alter table pdf2md_hk.retrieval_chunks add column if not exists page_number integer;
alter table pdf2md_hk.retrieval_chunks add column if not exists table_index integer;
alter table pdf2md_hk.retrieval_chunks add column if not exists text text;
create unique index if not exists uq_pdf2md_hk_companies_hkex_stock_code on pdf2md_hk.companies (hkex_stock_code) where hkex_stock_code is not null and hkex_stock_code <> '';
create index if not exists idx_pdf2md_hk_companies_aliases_gin on pdf2md_hk.companies using gin (aliases);
create index if not exists idx_pdf2md_hk_filings_company_year on pdf2md_hk.filings (company_id, fiscal_year desc, report_type);
create index if not exists idx_pdf2md_hk_retrieval_chunks_agent on pdf2md_hk.retrieval_chunks (company_id, doc_type, canonical_name, period_key);
```

Create `v_agent_financial_facts` joining `financial_statement_items`, `filings`, `companies`, `parse_runs`, and `evidence_citations`, exposing company, filing, metric, value, evidence page/table/row/column/bbox, `quote_text`, `wiki_package_path`, and `source_url`.

Create `v_latest_company_reports` selecting the latest parse run per `company_id` and `report_type`, ordered by `period_end`, `fiscal_year`, and `completed_at`.

- [ ] **Step 4: Run test to verify it passes**

Run the same pytest command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add db/ddl/020_create_pdf2md_hk_schema.sql db/imports/tests/test_import_hk_evidence_package.py
git commit -m "feat(hk): extend postgres schema for agent recall"
```

---

### Task 2: HK Importer Identity And Evidence Coordinates

**Files:**
- Modify: `db/imports/import_hk_evidence_package_to_postgres.py`
- Test: `db/imports/tests/test_import_hk_evidence_package.py`

**Interfaces:**
- Consumes: DDL columns from Task 1 and package files under `data/wiki/hk/companies/.../reports/...`.
- Produces:
  - `build_company_record(manifest: dict[str, Any]) -> dict[str, Any]`
  - `build_filing_record(manifest: dict[str, Any], package_dir: Path, quality: dict[str, Any]) -> dict[str, Any]`
  - `build_evidence_row(item: dict[str, Any], *, filing_id: str, parse_run_id: str) -> dict[str, Any]`

- [ ] **Step 1: Write failing tests for identity and evidence coordinates**

Add tests that assert:

```python
company = importer.build_company_record(manifest)
assert company["company_id"] == "HK:00700"
assert company["hkex_stock_code"] == "00700"
assert company["stock_code"] == "00700"
assert company["exchange"] == "HKEX"
assert "Tencent Holdings Limited" in company["aliases"]

filing = importer.build_filing_record(manifest, tmp_path, {"overall_status": "pass"})
assert filing["filing_id"] == "HK:00700:12100024"
assert filing["report_id"] == "2025-annual-12100024"

row = importer.build_evidence_row({"evidence_id": "e1", "page_number": 25, "table_index": 6, "row_index": 3, "column_index": 2, "bbox": [1, 2, 3, 4]}, filing_id="HK:00700:12100024", parse_run_id="run1")
assert row["bbox"] == [1, 2, 3, 4]
assert row["page_number"] == 25
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/maoyd/siq-research-engine
apps/api/.venv/bin/python -m pytest db/imports/tests/test_import_hk_evidence_package.py -q --tb=short
```

Expected: FAIL because helper functions do not exist.

- [ ] **Step 3: Implement record builders and wire SQL upserts**

Implement helpers in `import_hk_evidence_package_to_postgres.py`:

- `_unique_strings(values)` returns stable de-duplicated non-empty strings.
- `build_company_record` normalizes five-digit stock code, `company_id`, exchange, names, aliases, and raw manifest.
- `build_filing_record` normalizes `report_id`, dates, local path, quality status, and raw manifest.
- `build_evidence_row` preserves `bbox`, page/table/row/column, quote, paths, and raw payload.

Update `_upsert_company` and `_upsert_filing` to write all produced fields. Update `_insert_evidence` SQL to include `bbox`. Update `_insert_tables` SQL to include table `bbox`.

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd /home/maoyd/siq-research-engine
apps/api/.venv/bin/python -m pytest db/imports/tests/test_import_hk_evidence_package.py -q --tb=short
```

Expected: PASS for new helper tests and existing importer tests.

- [ ] **Step 5: Commit**

```bash
git add db/imports/import_hk_evidence_package_to_postgres.py db/imports/tests/test_import_hk_evidence_package.py
git commit -m "feat(hk): import company identity and evidence coordinates"
```

---

### Task 3: Financial Statement Items And Retrieval Chunks

**Files:**
- Modify: `db/imports/import_hk_evidence_package_to_postgres.py`
- Test: `db/imports/tests/test_import_hk_evidence_package.py`

**Interfaces:**
- Consumes: `metrics/financial_data.json`, `qa/source_map.json`, DDL tables from Task 1.
- Produces:
  - `build_statement_item_rows(manifest, financial_data, source_map, parse_run_id) -> list[dict[str, Any]]`
  - `build_retrieval_chunk_rows(manifest, financial_data, quality, source_map, parse_run_id, package_dir) -> list[dict[str, Any]]`

- [ ] **Step 1: Write failing tests for financial facts and retrieval chunks**

Add tests that assert `build_statement_item_rows` preserves:

```python
assert row["company_id"] == "HK:00700"
assert row["statement_type"] == "balance_sheet"
assert row["canonical_name"] == "total_assets"
assert row["source_page_number"] == 25
assert row["source_table_index"] == 6
assert row["source_bbox"] == [1, 2, 3, 4]
```

Add a retrieval chunk test that asserts:

```python
assert rows[0]["company_id"] == "HK:00700"
assert rows[0]["doc_type"] == "financial_fact"
assert rows[0]["canonical_name"] == "revenue"
assert rows[0]["page_number"] == 6
assert "Revenue" in rows[0]["text"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/maoyd/siq-research-engine
apps/api/.venv/bin/python -m pytest db/imports/tests/test_import_hk_evidence_package.py -q --tb=short
```

Expected: FAIL because row builders do not exist.

- [ ] **Step 3: Implement row builders and insert functions**

Implement:

- `_source_map_by_evidence_id(source_map)` mapping `evidence_id -> source`.
- `_source_from_item(item, source_map_by_id)` where explicit item source overrides source-map fallback.
- `build_statement_item_rows` producing one row per `financial_data.statements[].items[]`.
- `build_retrieval_chunk_rows` producing deterministic chunks for financial facts, quality warnings, and check failures.

Wire importer to:

- insert `financial_statement_items`;
- insert statement-specific tables for balance sheet, income statement, and cash flow;
- upsert `financial_all_metrics_wide` grouped by `period_key`;
- insert `retrieval_chunks` with deterministic `chunk_uid = stable_id(parse_run_id, "financial_fact", canonical_name, period_key, evidence_id)`.

- [ ] **Step 4: Run tests to verify they pass**

Run the same pytest command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add db/imports/import_hk_evidence_package_to_postgres.py db/imports/tests/test_import_hk_evidence_package.py
git commit -m "feat(hk): import financial facts for postgres recall"
```

---

### Task 4: Market Package API And HK Frontend Button Wiring

**Files:**
- Modify: `apps/api/tests/test_market_reports_proxy.py`
- Modify: `apps/web/src/features/market-parsing/packageActions.ts`
- Modify: `apps/web/src/features/market-parsing/packageActions.test.ts`
- Modify: `apps/web/src/pages/HkParsing.tsx`
- Modify: `apps/web/src/pages/MarketParsingPage.tsx`

**Interfaces:**
- Consumes: `/api/market-reports/packages/build` and `/api/market-reports/packages/import`.
- Produces: HK page uses market package build/import for the main HK data pipeline; generic PDF workflow is secondary and not the main PostgreSQL import path.

- [ ] **Step 1: Write API command-plan regression test**

Add `test_hk_market_package_import_uses_hk_database_env` to `apps/api/tests/test_market_reports_proxy.py`. It should monkeypatch `run_command`, call `_run_market_package_import({"market": "HK", "package_path": str(package_dir), "ddl": True})`, and assert:

```python
assert result["ok"] is True
assert "import_hk_evidence_package_to_postgres.py" in " ".join(captured["args"])
assert "--ddl" in captured["args"]
assert captured["kwargs"]["env"]["SIQ_HK_PGDATABASE"] == "siq_hk"
```

- [ ] **Step 2: Run API test**

```bash
cd /home/maoyd/siq-research-engine/apps/api
.venv/bin/python -m pytest tests/test_market_reports_proxy.py::test_hk_market_package_import_uses_hk_database_env -q --tb=short
```

Expected: PASS if existing command plan is correct; otherwise FAIL and fix the command/env path.

- [ ] **Step 3: Write frontend action test**

Add a test to `apps/web/src/features/market-parsing/packageActions.test.ts` asserting `runMarketPackageImportAction({ market: "HK", packagePath: "HK/pkg" })` calls `runImport("HK", "HK/pkg", true)` and returns stdout.

- [ ] **Step 4: Run frontend test**

```bash
cd /home/maoyd/siq-research-engine/apps/web
/home/maoyd/.hermes/node/bin/node scripts/run-node-unit-tests.mjs src/features/market-parsing/packageActions.test.ts
```

Expected: PASS if current default already works; otherwise FAIL and fix.

- [ ] **Step 5: Implement HK page wiring**

Change HK copy and controls so the primary PostgreSQL button calls market package import:

- label `构建 HK 证据包`;
- label `导入 HK PostgreSQL`;
- explanatory copy `Wiki package 为主证据入口；PostgreSQL 用于结构化查询和证据坐标兜底。`;
- call `runMarketPackageBuildAction` to produce package path;
- call `runMarketPackageImportAction({ market: "HK", packagePath, ddl: true })`.

Do not change A 股 standard workflow behavior.

- [ ] **Step 6: Run focused tests**

Run API and frontend commands from Steps 2 and 4. Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/api/tests/test_market_reports_proxy.py apps/web/src/features/market-parsing/packageActions.ts apps/web/src/features/market-parsing/packageActions.test.ts apps/web/src/pages/HkParsing.tsx apps/web/src/pages/MarketParsingPage.tsx
git commit -m "feat(hk): wire postgres import to market package pipeline"
```

---

### Task 5: Agent Documentation And End-To-End Regression

**Files:**
- Modify: `agents/hermes/profiles/siq_analysis/rules/data_sources.md`
- Modify: `agents/hermes/profiles/siq_assistant/rules/data_sources.md`

**Interfaces:**
- Consumes: HK Wiki package files and PostgreSQL views/tables.
- Produces: agent rules that state Wiki-first, PostgreSQL-fallback lookup order.

- [ ] **Step 1: Edit agent data-source rules**

Add this HK section to both files:

```markdown
### HK 财报证据读取顺序

HK 市场以 `data/wiki/hk/companies/<stock>-<name>/reports/<report_id>` evidence package 为主证据入口。优先读取 `manifest.json`、`metrics/financial_data.json`、`qa/source_map.json`、`tables/table_index.json`、`parser/document_full.json`、`metrics/financial_checks.json`。

PostgreSQL `siq_hk.pdf2md_hk` 是结构化索引与兜底查询层，不是二次抽取来源。只有在需要跨公司/跨年度聚合、批量筛选、质量统计，或 Wiki package 证据路径缺失时，才查询 `v_agent_financial_facts`、`v_latest_company_reports`、`financial_statement_items`、`evidence_citations`。

回答财务事实时必须保留 evidence 信息：优先使用 Wiki `qa/source_map.json` 中的页码、表格、行列、bbox；若使用 PostgreSQL 兜底，必须带回 `page_number`、`table_index`、`row_index`、`column_index`、`bbox`、`quote_text` 或说明缺失原因。
```

- [ ] **Step 2: Run documentation verification**

```bash
cd /home/maoyd/siq-research-engine
grep -R -n "HK 财报证据读取顺序\|v_agent_financial_facts\|PostgreSQL.*兜底" agents/hermes/profiles/siq_analysis/rules/data_sources.md agents/hermes/profiles/siq_assistant/rules/data_sources.md
```

Expected: grep prints matching HK sections in both files.

- [ ] **Step 3: Run focused regression**

```bash
cd /home/maoyd/siq-research-engine
apps/api/.venv/bin/python -m pytest db/imports/tests/test_import_hk_evidence_package.py -q --tb=short
apps/api/.venv/bin/python -m pytest apps/api/tests/test_market_reports_proxy.py::test_hk_market_package_import_uses_hk_database_env -q --tb=short
cd /home/maoyd/siq-research-engine/apps/web
/home/maoyd/.hermes/node/bin/node scripts/run-node-unit-tests.mjs src/features/market-parsing/packageActions.test.ts
```

Expected: all focused tests PASS.

- [ ] **Step 4: Optional live import smoke test**

If PostgreSQL is available on DGX:

```bash
cd /home/maoyd/siq-research-engine
PACKAGE=$(find data/wiki/hk/companies -path '*/reports/*/manifest.json' | head -1 | xargs dirname)
SIQ_HK_PGDATABASE=siq_hk apps/api/.venv/bin/python db/imports/import_hk_evidence_package_to_postgres.py "$PACKAGE" --ddl
```

Expected: command exits 0 and prints a `parse_run_id`. If PostgreSQL is unavailable, record the connection error and rely on unit/API regression.

- [ ] **Step 5: Commit**

```bash
git add agents/hermes/profiles/siq_analysis/rules/data_sources.md agents/hermes/profiles/siq_assistant/rules/data_sources.md
git commit -m "docs(hk): document wiki-first postgres fallback lookup"
```

---

## Final Verification

After all tasks are complete, run:

```bash
cd /home/maoyd/siq-research-engine
apps/api/.venv/bin/python -m pytest db/imports/tests/test_import_hk_evidence_package.py -q --tb=short
apps/api/.venv/bin/python -m pytest apps/api/tests/test_market_reports_proxy.py::test_hk_market_package_import_uses_hk_database_env -q --tb=short
cd /home/maoyd/siq-research-engine/apps/web
/home/maoyd/.hermes/node/bin/node scripts/run-node-unit-tests.mjs src/features/market-parsing/packageActions.test.ts
```

Then inspect:

```bash
cd /home/maoyd/siq-research-engine
git log --oneline -5
git status --short --branch
```

Do not claim completion unless the focused tests pass or any unavailable external dependency is explicitly reported.
