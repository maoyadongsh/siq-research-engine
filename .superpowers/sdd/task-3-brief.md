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

