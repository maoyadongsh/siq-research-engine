## Task 7: 建立 HK 5 样本 Smoke 验收脚本

**Files:**
- `scripts/hk/run_hk_v2_smoke.py`
- `docs/superpowers/reports/`

**Behavior:**
对现有 `data/wiki/hk_reports` 中 5 个代表性 package 做结构、质量、导入 dry run 检查，并输出中文报告。首批样本固定为：

- `data/wiki/hk_reports/00700/2025/annual_12100024`
- `data/wiki/hk_reports/01299/2025/annual_12106543`
- `data/wiki/hk_reports/00981/2025/annual_12097338`
- `data/wiki/hk_reports/03988/2025/annual_12132549`
- `data/wiki/hk_reports/09988/2025/annual_11727038`

**TDD Steps:**

- [ ] 新增脚本级测试可选；若仓库没有脚本测试模式，则用 CLI dry run 验收。
- [ ] 脚本必须返回非 0 当任一样本缺失必需 V2 文件、validator 失败、metrics/evidence 全空、或 package detail 无 V2 paths。

**Implementation Steps:**

- [ ] 脚本参数：`--root data/wiki/hk_reports`、`--output docs/superpowers/reports/hk_v2_smoke_report.md`、`--json-output docs/superpowers/reports/hk_v2_smoke_report.json`。
- [ ] 对每个样本读取 `manifest.json`、`qa/quality_report.json`、`tables/table_index.json`、`metrics/normalized_metrics.json`、`qa/source_map.json`。
- [ ] 输出每个样本：公司、ticker、filing_id、quality、sections、tables、metrics、evidence、缺失文件、主要 warnings。
- [ ] 输出聚合结论：通过/警告/失败、下一步需要补的 HK alias 或表格规则。

**Verification:**

```bash
cd /home/maoyd/siq-research-engine
PYTHONDONTWRITEBYTECODE=1 python3 scripts/hk/run_hk_v2_smoke.py \
  --root data/wiki/hk_reports \
  --output docs/superpowers/reports/hk_v2_smoke_report.md \
  --json-output docs/superpowers/reports/hk_v2_smoke_report.json
```

Expected: 生成中文 smoke 报告；若当前 package 尚未重建为 V2，报告明确列出缺失 V2 文件，而不是静默通过。

---

