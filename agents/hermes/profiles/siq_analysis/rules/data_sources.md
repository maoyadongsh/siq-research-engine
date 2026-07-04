# Data Sources

## 公司定位入口

所有分析必须从 `_meta/company_catalog.json` 开始：

```text
/home/maoyd/siq-research-engine/data/wiki/_meta/company_catalog.json
```

港股 HK 使用独立但同构的市场 Wiki 根目录：

```text
/home/maoyd/siq-research-engine/data/wiki/hk/_meta/company_catalog.json
/home/maoyd/siq-research-engine/data/wiki/hk/companies/<ticker>-<company>/company.json
/home/maoyd/siq-research-engine/data/wiki/hk/companies/<ticker>-<company>/reports/<report_id>/
```

必须通过 `resolve_company.py` 唯一定位公司；严禁手写猜测 `/home/maoyd/siq-research-engine/data/wiki/companies/<公司名>`。

输出功能介绍、提问示例、示例命令或示例问题时，所有公司名必须来自 `company_catalog.json` 的实时内容；不得使用任何不在实时 catalog 中的公司。无法确认 catalog 时，不列具体公司名，改写为“某个已入库公司”。

## 数据读取优先级

1. 目标公司 wiki 目录全量盘点
   - 必须先通过 `resolve_company.py` 确认唯一公司目录。
   - 必须按 `/home/maoyd/siq-research-engine/data/wiki/_meta/AGENT_GUIDE.md` 读取：`company_catalog.json` -> `company.json` -> `company.md` -> `semantic/` -> `metrics/` -> `evidence/` -> `report.md`。
   - 完整报告可盘点单公司目录下 `company`、`reports`、`metrics`、`evidence`、`semantic`、`graph`、`tracking`、`factcheck`、既有 `analysis` 与 `_index.json`，但普通问答不需要全量读大文件。
   - 大文件可以索引化/摘要化读取，但必须记录读取状态、缺失文件、解析失败和采用口径。
2. `semantic/`
   - 先读 `semantic/retrieval_index.json`，再按问题读取 `facts.json`、`relations.json`、`claims.json`。
   - 财报项目、科目明细和附注解释优先读 `semantic/document_links.json`，再读 `semantic/note_links.json`。
   - 涉及“明细/构成/分布/组成/附注/减值准备/账龄/前五名/资产组/可收回金额/变动”等问题时，优先调用 `/home/maoyd/siq-research-engine/data/hermes/home/profiles/shared/scripts/note_detail_lookup.py --company <公司或代码> --metric <事项> --format markdown` 或等价逻辑，从 `document_links.json` 的 `note_table` 读取 `report.md` 表格行。
   - 若 `evidence/evidence_index.json` 未命中附注事项，只能说明“指标级证据索引无独立条目”，不得据此判断年报未披露；应继续检查 `semantic/document_links.json`、`semantic/note_links.json` 和 `report.md`。
3. `metrics/`
   - 指定年份或 `report_id`：优先 `metrics/reports/<report_id>/three_statements.json`、`key_metrics.json`、`validation.json`。
   - 未指定年份：优先 `metrics/latest/three_statements.json`、`key_metrics.json`、`validation.json`。
   - 旧路径 `metrics/three_statements.json`、`metrics/key_metrics.json`、`metrics/validation.json` 只作兼容入口。
   - HK 公司级 Wiki 中，核心财务底稿优先读取 `reports/<report_id>/metrics/financial_data.json`、`financial_checks.json`、`qa/source_map.json`；这是 HK 对齐 A 股三大表底稿的市场化 package 路径。
4. `evidence/evidence_index.json`
5. `reports/<report_id>/report.md`
6. `reports/<report_id>/document_full.json` 仅用于深度审计、重放或证据补全失败。

禁止从无证据的模型总结中直接生成事实。所有关键数字必须绑定 evidence。

附注明细匹配规则：查询要拆成“基础科目 + 意图”，例如 `商誉明细` 拆为基础科目 `商誉` 和意图 `明细`；基础科目必须出现在目标表标题或表格预览中，不能仅凭继承的 `note_title` 命中跨节表格。

## 行业对比数据

- 同行业公司从 `company_catalog.json` 筛选相同 `industry_sw1_code` 或 `industry_sw2_code`。
- 行业均值、中位数、最大值、最小值从 `metrics/*.json` 聚合计算。
- 同业样本少于 3 家时，必须标注“样本量不足，对比仅供参考”。
- 默认不联网，不使用 browser/web。若必须补充外部数据，需先说明来源、口径、日期并等待用户确认。

## PostgreSQL 备用/增强接口

默认优先使用 wiki 的 semantic、metrics、evidence 与 report.md。当 wiki 文件缺失、损坏、需要交叉校验，或用户明确要求使用数据库时，才查询 PostgreSQL。

PostgreSQL 的角色是“数据补充查询平台”，不是完整报告写作入口。完整报告必须先完成单公司 wiki 全量盘点；数据库查询只能在以下场景使用：
- wiki 指标缺失、口径冲突或证据页码缺失，需要补缺/核验。
- 需要从 `document_tables`、宽表或入库元数据补 PDF 页码、表格编号、入库时间。
- 需要验证同一指标在 wiki 与数据库中的数值、单位、期间是否一致。
- 用户明确要求使用数据库补充某项数据。

HK PostgreSQL fallback 只读查询目标为同一 PostgreSQL 实例内的 `siq_hk.pdf2md_hk`。不要把 HK 财报写入或查询为 A 股 `siq.pdf2md` 默认口径；HK 的公司主键为 `HK:<5位股票代码>`，例如 `HK:00700`。

推荐查询入口：

```bash
/home/maoyd/.hermes/hermes-agent/venv/bin/python /home/maoyd/siq-research-engine/data/hermes/home/profiles/shared/scripts/pg_query.py --profile-env /home/maoyd/siq-research-engine/data/hermes/home/profiles/siq_analysis/.env --sql "<只读 SQL>"
```

规则：
- 只读查询，禁止 INSERT/UPDATE/DELETE/DDL。
- 查询前先按股票代码或公司简称解析公司，避免跨公司误匹配。
- 单次查询限制结果数量，避免全表扫描；盘点时只查询 count、min/max year、distinct company 等元数据。
- 密码和连接串不得写入报告正文、session 记录或生成文件。
- 数据库结果与 wiki JSON 冲突时，以 wiki `metrics/*.json` 为主，并在“数据质量与溯源声明”中列出口径差异。
