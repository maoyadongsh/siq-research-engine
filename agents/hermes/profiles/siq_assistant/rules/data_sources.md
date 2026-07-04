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

必须通过 `resolve_company.py` 唯一定位公司，或直接读取 catalog 并使用其中的 `company_path`；严禁手写猜测 `/home/maoyd/siq-research-engine/data/wiki/companies/<公司名>`，也严禁把公司简称翻译成英文目录或拼音目录。

多市场公司 wiki 使用同一套公司级入口语义：A 股主路径为 `data/wiki/companies/<stock_code>-<company_name>/`；海外市场主路径为 `data/wiki/<market>/companies/<ticker>-<company_name>/`。日本市场必须从 `data/wiki/jp/companies/<ticker>-<company_name>/` 进入，并按 `company.json -> reports/<report_id>/manifest.json -> parser/quality_report.json -> metrics/ -> evidence/ -> sections/report.md` 读取。`data/wiki/jp_reports/` 只作历史兼容或迁移来源，禁止作为智能体查询主入口。

输出功能介绍、提问示例、示例命令或示例问题时，所有公司名也必须来自该 catalog 的实时内容；不得使用任何不在实时 catalog 中的公司。无法确认 catalog 时，不列具体公司名，改写为“某个已入库公司”。

推荐命令：

```bash
/home/maoyd/.hermes/hermes-agent/venv/bin/python \
  /home/maoyd/siq-research-engine/data/hermes/home/profiles/siq_analysis/scripts/resolve_company.py \
  --company "<公司简称或股票代码>" \
  --year 2025
```

如果解析出的路径不存在，必须回到 catalog 重新检查，不得直接判断公司不在工作集。

## 数据读取优先级

1. `company_catalog.json` / `resolve_company.py`
2. `company.json` / `company.md`
3. 主表、核心指标和所有财务数字，先读 `metrics/reports/<report_id>/three_statements.json`、`key_metrics.json`、`validation.json`；未指定时读 `metrics/latest/`，旧 `metrics/*.json` 只作兼容入口
   - HK 公司级 Wiki 中，核心财务数字优先读 `reports/<report_id>/metrics/financial_data.json`、`financial_checks.json`、`qa/source_map.json`；路径和 A 股同样挂在 `companies/<company>/reports/<report_id>/` 下。
4. `semantic/retrieval_index.json`、`document_links.json`、`note_links.json`、`facts.json`、`claims.json` 只用于管理层讨论、风险因素、业务结构和主表项目的附注展开
5. `evidence/evidence_index.json`
6. `reports/<report_id>/report.md`
7. `reports/<report_id>/document_full.json` 仅用于深度审计、重放或证据补全失败

禁止从无证据的模型总结中直接生成事实。所有关键数字必须绑定 evidence。

### HK 财报证据读取顺序

HK 市场以 `data/wiki/hk/companies/<stock>-<name>/reports/<report_id>` evidence package 为主证据入口。优先读取 `manifest.json`、`metrics/financial_data.json`、`qa/source_map.json`、`tables/table_index.json`、`parser/document_full.json`、`metrics/financial_checks.json`。

PostgreSQL `siq_hk.pdf2md_hk` 是结构化索引与兜底查询层，不是二次抽取来源。只有在需要跨公司/跨年度聚合、批量筛选、质量统计，或 Wiki package 证据路径缺失时，才查询 `v_agent_financial_facts`、`v_latest_company_reports`、`financial_statement_items`、`evidence_citations`。

回答财务事实时必须保留 evidence 信息：优先使用 Wiki `qa/source_map.json` 中的页码、表格、行列、bbox；若使用 PostgreSQL 兜底，必须带回 `page_number`、`table_index`、`row_index`、`column_index`、`bbox`、`quote_text` 或说明缺失原因。

## 文本定位规则

- 主表类问题（资产负债表、利润表、现金流量表、资产负债结构、现金流质量、总资产/总负债/经营现金流等）必须先回到 `three_statements.json` 指向的正文主表 PDF 页和 `table_index`。
- 查找“商誉、资产减值、管理层讨论、审计事项、附注”等文本时，先关键词定位，再读取命中段落。
- 深度多维分析可以全文检索，但必须先用 `metrics/*.json` 和 `evidence/*.json` 建立结构化底稿，再按分析维度定向检索 `report.md`、`semantic/*.json`；全文检索只补解释和交叉验证，不替代主表数值来源。
- 涉及明细、构成、分布、附注、减值准备、账龄、前五名、资产组、可收回金额或变动时，优先调用 `/home/maoyd/siq-research-engine/data/hermes/home/profiles/shared/scripts/note_detail_lookup.py --company <公司或代码> --metric <事项> --format markdown`，命中后直接展示表格行和可打开表格链接。
- 禁止逐页递增扫描年报；如果 3 次定位仍无命中，停止检索并说明证据链缺口。

## 行业对比数据

- 同行业公司从 `company_catalog.json` 筛选相同 `industry_sw1_code` 或 `industry_sw2_code`。
- 行业均值、中位数、最大值、最小值从 `metrics/*.json` 聚合计算。
- 同业样本少于 3 家时，必须标注"样本量不足，对比仅供参考"。
- 默认不联网，不使用 browser/web。若必须补充外部数据，需先说明来源、口径、日期并等待用户确认。

## PostgreSQL 备用/增强接口

默认优先使用 wiki 的 `metrics/*.json` 与 `evidence/*.json`。当 wiki 文件缺失、损坏、需要交叉校验，或用户明确要求使用数据库时，才查询 PostgreSQL。

HK PostgreSQL fallback 只读查询目标为同一 PostgreSQL 实例内的 `siq_hk.pdf2md_hk`。不要把 HK 财报当成 A 股 `siq.pdf2md` 查询；HK 公司主键为 `HK:<5位股票代码>`，例如 `HK:00700`。

推荐查询入口：

```bash
/home/maoyd/.hermes/hermes-agent/venv/bin/python /home/maoyd/siq-research-engine/data/hermes/home/profiles/shared/scripts/pg_query.py --profile-env /home/maoyd/siq-research-engine/data/hermes/home/profiles/siq_assistant/.env --sql "<只读 SQL>"
```

规则：
- 只读查询，禁止 INSERT/UPDATE/DELETE/DDL。
- 查询前先按股票代码或公司简称解析公司，避免跨公司误匹配。
- 单次查询限制结果数量，避免全表扫描；盘点时只查询 count、min/max year、distinct company 等元数据。
- 密码和连接串不得写入报告正文、session 记录或生成文件。
- 数据库结果与 wiki JSON 冲突时，以 wiki `metrics/*.json` 为主，并在"数据质量与溯源声明"中列出口径差异。
