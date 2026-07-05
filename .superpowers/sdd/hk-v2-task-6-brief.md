## Task 6: HK 前端状态入口展示 V2 Package 内容

**Files:**
- `apps/api/routers/market_reports.py`
- `apps/api/tests/test_market_reports_proxy.py`
- 前端相关文件通过搜索确认后再改，优先检查包含 `HK Evidence Packages` 或 `Package Files` 的组件。

**Behavior:**
`https://arthurmao.synology.me:9391/parse-hk` 的 Evidence Packages 区域选择 package 后，不再只显示 0 和空文件；应展示 sections/tables/raw facts/metrics/evidence 数量、quality JSON、package files 中的 V2 路径，以及 parser/QA artifacts。

**TDD Steps:**

- [ ] 后端先加 `test_market_package_detail_returns_hk_v2_paths`：构造 HK V2 package，调用 package detail endpoint/函数，断言返回 `paths.document_full`、`paths.report_complete`、`paths.footnotes`。
- [ ] 如果前端已有测试框架，增加一个组件测试，mock package detail 响应后断言文件列表出现 `parser/document_full.json` 和 `sections/report_complete.md`。
- [ ] 若前端无现成测试，保留后端测试，并在 Task 8 用浏览器验收覆盖 UI。

**Implementation Steps:**

- [ ] 确认 `market_reports.py` 的 detail/quality/file endpoint 使用 `read_market_package_detail()`，若只返回部分字段，补充 `paths`、`parser_artifacts`、`qa_artifacts`。
- [ ] 搜索前端组件：

```bash
cd /home/maoyd/siq-research-engine
grep -RIn "HK Evidence Packages\|Package Files\|Milvus Dry Run\|Build Package" apps packages frontend 2>/dev/null | head -n 80
```

- [ ] 若组件只渲染固定 `PACKAGE_FILE_PATHS`，改为遍历后端返回的 `paths`，按 `manifest/quality/source/financial/parser/qa/sections/tables` 分组。
- [ ] 保持 `/parse-hk` 视觉和其他市场页一致，不引入新的说明性大段文本。

**Verification:**

```bash
cd /home/maoyd/siq-research-engine/apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_market_reports_proxy.py
```

Expected: HK package detail 后端返回 V2 文件，前端能够消费动态 paths。

---

