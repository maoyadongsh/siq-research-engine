## Task 3: 扩展 `pdf2md_hk` Schema 支持 V2 结构

**Files:**
- `db/ddl/020_create_pdf2md_hk_schema.sql`
- `db/imports/tests/test_import_hk_evidence_package.py`

**Behavior:**
HK schema 支持公司身份属性、parser artifacts、content blocks、footnotes、toc、financial note links、table relations、table quality signals。所有新增表按 `parse_run_id` 可幂等清理。

**TDD Steps:**

- [ ] 在 `test_import_hk_evidence_package.py` 新增 `test_hk_ddl_contains_v2_tables_and_identity_columns`，读取 DDL 文本并断言包含 `short_name`、`stock_code`、`hkex_stock_code`、`content_blocks`、`footnotes`、`toc_entries`、`financial_note_links`、`table_relations`、`parser_artifacts`、`table_quality_signals`。
- [ ] 运行测试，确认先失败。

**Implementation Steps:**

- [ ] 在 `companies` 表增加可空字段：`stock_code text`、`hkex_stock_code text`、`short_name text`、`company_name_en text`、`company_name_zh text`、`aliases jsonb not null default '[]'::jsonb`。
- [ ] 在 `filings` 表确认已有 `stock_code`，若没有则增加。
- [ ] 新增表 `parser_artifacts`、`content_blocks`、`footnotes`、`toc_entries`、`financial_note_links`、`table_relations`、`table_quality_signals`。
- [ ] 新增表均包含 `filing_id`、`parse_run_id`、稳定主键、page/table/target 字段和 `raw jsonb`。
- [ ] 为新增表增加 `parse_run_id`、`filing_id`、`page_number`、`table_index` 常用索引。
- [ ] DDL 使用 `create table if not exists` 和 `alter table ... add column if not exists`，保证可重复执行。

**SQL Sketch:**

```sql
create table if not exists pdf2md_hk.parser_artifacts (
  parse_run_id text not null references pdf2md_hk.parse_runs(parse_run_id) on delete cascade,
  artifact_key text not null,
  local_path text not null,
  schema_version text,
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  primary key (parse_run_id, artifact_key)
);
```

**Verification:**

```bash
cd /home/maoyd/siq-research-engine/db/imports
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/test_import_hk_evidence_package.py
```

Expected: DDL 文本测试通过。

---

