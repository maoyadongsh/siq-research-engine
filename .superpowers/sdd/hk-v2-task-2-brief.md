## Task 2: 让统一 Package Reader 展示 HK V2 文件和计数

**Files:**
- `packages/market-contracts/src/siq_market_contracts/evidence_package.py`
- `packages/market-contracts/tests/test_evidence_package.py`
- `apps/api/tests/test_market_reports_proxy.py`

**Behavior:**
`read_market_package_summary()` 和 `read_market_package_detail()` 读取 HK V2 package 时，`paths` 和 `detail` 返回新增 parser/QA 文件，前端 Evidence Packages 面板能显示非 0 的 sections/tables/raw facts/metrics/evidence，以及 V2 package files。

**TDD Steps:**

- [ ] 在 `packages/market-contracts/tests/test_evidence_package.py` 的 HK fixture 中写入 V2 文件。
- [ ] 断言 `summary["paths"]` 包含 `document_full`、`content_list_enhanced`、`report_complete`、`footnotes`、`toc`、`financial_note_links`、`table_quality_signals`。
- [ ] 断言 `detail` 包含 `parser_artifacts` 和 `qa_artifacts`，并能读取对应 JSON。
- [ ] 在 `apps/api/tests/test_market_reports_proxy.py` 增加一个 HK package detail 测试，确认 API 返回上述 `paths`。
- [ ] 运行测试，确认新增断言先失败。

**Implementation Steps:**

- [ ] 扩展 `PACKAGE_FILE_PATHS`，加入 V2 文件映射：`report_complete`、`document_full`、`content_list_enhanced`、`table_relations`、`footnotes`、`toc`、`financial_note_links`、`table_quality_signals`。
- [ ] 在 `read_market_package_detail()` 返回 `parser_artifacts` 和 `qa_artifacts`。
- [ ] 不把这些 V2 文件加入 `REQUIRED_FILES`，避免破坏 US/JP/KR/EU 旧包。

**Verification:**

```bash
cd /home/maoyd/siq-research-engine/packages/market-contracts
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_evidence_package.py

cd /home/maoyd/siq-research-engine/apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_market_reports_proxy.py::test_market_package_quality_by_path_and_filing_id
```

Expected: HK package detail includes V2 paths and existing markets remain compatible.

---

