## Task 4: HK Importer 写入 V2 表并默认使用 `siq_hk`

**Files:**
- `db/imports/import_hk_evidence_package_to_postgres.py`
- `db/imports/tests/test_import_hk_evidence_package.py`

**Behavior:**
`import_hk_evidence_package_to_postgres.py` 默认连接 `siq_hk`，除非显式传入 `--database-url` 或环境变量覆盖。导入 V2 package 时写入新增表，并且同一 `parse_run_id` 重跑不会重复数据。

**TDD Steps:**

- [ ] 新增 `test_hk_database_url_defaults_to_siq_hk`，清空 `DATABASE_URL`、`SIQ_PGDATABASE`、`PGDATABASE` 后断言 URL 以 `/siq_hk` 结尾。
- [ ] 新增 fake connection，记录 `execute(sql, params)` 调用。
- [ ] 新增 `test_delete_run_rows_includes_v2_tables`，断言删除表包含新增 V2 表。
- [ ] 新增 `test_import_v2_artifacts_writes_parser_and_qa_tables`，构造 tmp package，调用 V2 insert 函数，断言 SQL 触达对应表。
- [ ] 运行测试，确认先失败。

**Implementation Steps:**

- [ ] 修改 `database_url()` 默认数据库优先级：`SIQ_HK_PGDATABASE` -> `SIQ_PGDATABASE` -> `PGDATABASE` -> `siq_hk`。
- [ ] `_upsert_company()` 写入 `stock_code`、`hkex_stock_code`、`short_name`、`company_name_en`、`company_name_zh`、`aliases`。
- [ ] `_delete_run_rows()` 增加 `table_quality_signals`、`table_relations`、`financial_note_links`、`toc_entries`、`footnotes`、`content_blocks`、`parser_artifacts`。
- [ ] 在 `import_package()` 中 `_insert_artifacts()` 后追加 `_insert_parser_artifacts()`、`_insert_content_blocks()`、`_insert_footnotes()`、`_insert_toc_entries()`、`_insert_financial_note_links()`、`_insert_table_relations()`、`_insert_table_quality_signals()`。
- [ ] 各插入函数从 package 文件读取 JSON，空文件或缺失文件时直接跳过。
- [ ] 所有主键使用 `stable_id(parse_run_id, artifact_key, page_number, table_index, target_id, row_index)`，避免不同 run 的同名脚注互相覆盖。

**Code Sketch:**

```python
def database_url(explicit: str | None) -> str:
    url = explicit or os.environ.get("DATABASE_URL")
    if url:
        return url.replace("postgresql+psycopg://", "postgresql://")
    db = (
        os.environ.get("SIQ_HK_PGDATABASE")
        or os.environ.get("SIQ_PGDATABASE")
        or os.environ.get("PGDATABASE")
        or "siq_hk"
    )
    host = os.environ.get("SIQ_PGHOST") or os.environ.get("PGHOST") or "127.0.0.1"
    port = os.environ.get("SIQ_PGPORT") or os.environ.get("PGPORT") or "15432"
    user = os.environ.get("SIQ_PGUSER") or os.environ.get("PGUSER") or "postgres"
    password = os.environ.get("SIQ_PGPASSWORD") or os.environ.get("PGPASSWORD") or ""
    auth = f"{user}:{password}" if password else user
    return f"postgresql://{auth}@{host}:{port}/{db}"
```

**Verification:**

```bash
cd /home/maoyd/siq-research-engine/db/imports
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/test_import_hk_evidence_package.py
```

Expected: importer 单元测试通过，默认 DB 指向 `siq_hk`。

---

