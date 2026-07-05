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

