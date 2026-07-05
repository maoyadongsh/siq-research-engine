# Task 3 Report: Financial Statement Items And Retrieval Chunks

Status: DONE

Modified files:
- db/imports/import_hk_evidence_package_to_postgres.py
- db/imports/tests/test_import_hk_evidence_package.py

## RED

Command:

```bash
cd /home/maoyd/siq-research-engine-hk-pg-impl
/home/maoyd/siq-research-engine/apps/api/.venv/bin/python -m pytest db/imports/tests/test_import_hk_evidence_package.py::test_hk_importer_statement_items_keep_source_page_and_bbox db/imports/tests/test_import_hk_evidence_package.py::test_hk_importer_retrieval_chunks_are_agent_friendly -q --tb=short
```

Result: FAILED as expected because `build_statement_item_rows` and `build_retrieval_chunk_rows` did not exist.

## GREEN

Command:

```bash
cd /home/maoyd/siq-research-engine-hk-pg-impl
/home/maoyd/siq-research-engine/apps/api/.venv/bin/python -m pytest db/imports/tests/test_import_hk_evidence_package.py -q --tb=short
```

Result: PASSED.

Output:

```text
.........                                                                [100%]
9 passed in 0.06s
```

Additional syntax check:

```bash
/home/maoyd/siq-research-engine/apps/api/.venv/bin/python -m py_compile db/imports/import_hk_evidence_package_to_postgres.py
```

Result: PASSED.

## Self-review

- Statement rows are built from `metrics/financial_data.json` and `qa/source_map.json` only.
- Evidence page/table/row/column/bbox is preserved in row builders.
- Importer now inserts statement rows, statement-specific tables, wide metrics, and retrieval chunks.
- `_delete_run_rows` clears newly imported tables for idempotent re-import by parse run.
- No Markdown/natural-language extraction was introduced.
