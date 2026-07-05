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

