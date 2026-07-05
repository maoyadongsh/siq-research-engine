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

