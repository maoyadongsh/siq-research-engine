# 港股年报解析后证据链与入库开发任务书

日期：2026-06-27

适用仓库：

```text
/home/maoyd/siq-research-engine
```

## 一、结论和目标

结论：港股市场应深度复刻 A 股 PDF 解析后的 Wiki 入库、PostgreSQL 入库、财务校验和解析质量报告规则；Milvus 入库应参考美股 SEC evidence package 的向量化规则。

这里的“深度复刻”不是复用或改动 A 股 legacy 代码路径，而是复刻 A 股链路已经验证过的工程语义：

- 每个财务事实必须有 PDF 页码、表格、行列坐标和原文证据。
- Wiki 证据包必须能独立重建 PostgreSQL 和 Milvus。
- PostgreSQL 必须市场隔离、幂等导入、可追溯到源文件。
- 财务校验必须解释 pass、warning、fail 的原因。
- Milvus 只做召回索引，不做事实源。
- Agent 命中向量 chunk 后，必须能回查 DB/Wiki/PDF 证据。

目标链路：

```text
HKEX PDF + finder metadata
  -> apps/pdf-parser 已解析结果
  -> HK ParsedArtifact 适配层
  -> HK rules 抽取 financial_data / financial_checks
  -> data/wiki/hk_reports evidence package
  -> pdf2md_hk PostgreSQL 幂等入库
  -> siq_hk_reports Milvus collection
  -> API/前端/Agent 可回溯引用
```

本任务书以“已完成 50 份港股案例下载和解析”为前提，第一阶段必须基于这 50 份解析结果跑规则、出质量报告、修正规则缺口。

## 二、硬性边界

1. 不修改 A 股既有行为。
   - 不修改 `apps/pdf-parser` 中 A 股正在使用的解析、抽取、校验逻辑。
   - 不修改 `db/ddl/001_create_pdf2md_schema.sql`。
   - 不修改 `db/imports/import_document_full_to_postgres.py`。
   - 不改变 A 股下载、解析、入库、查询 API 的默认行为。
   - 如需参考 A 股实现，只做只读分析；新增 HK 适配器和 HK schema。

2. 不把港股写入 A 股 schema。
   - A 股 legacy：`pdf2md`
   - 港股新增：`pdf2md_hk`
   - Milvus collection：`siq_hk_reports`
   - Wiki namespace：`data/wiki/hk_reports`

3. 不用大模型猜财务数字。
   - 财务数字只能来自 PDF 表格单元格、结构化解析结果、人工修正记录。
   - 大模型最多用于标题分类、章节辅助、候选经营指标分类，不允许生成无证据数字。

4. 每个核心事实必须有 evidence。
   - 最小定位：`filing_id + page_number + table_index + row_index + column_index`
   - 推荐定位：再加 `quote_text`、`bbox`、`table_json_path`、`pdf_local_path`、`source_url`、`text_hash`

5. 入库必须幂等。
   - 同一 evidence package 连续导入两次，事实数量不得翻倍。
   - `parse_run_id` 由 `filing_id + parser_version + rules_version + artifact_hashes` 稳定生成。

## 三、输入数据和样本清单

### 1. 输入来源

下载产物：

```text
data/market-report-finder/downloads/HK/.../*.pdf
data/market-report-finder/downloads/HK/.../*.pdf.metadata.json
```

解析产物：

```text
data/pdf-parser/results/<task_id>/
  document_full.json
  content_list_enhanced.json
  table_index.json
  quality_report.json
  result.md
  result_complete.md
```

### 2. 50 份案例 manifest

新增文件：

```text
eval_datasets/market_ingestion_cases/hk_50_cases.json
```

每个 case 必填：

```json
{
  "market": "HK",
  "ticker": "00700",
  "stock_code": "00700",
  "company_name": "TENCENT",
  "report_type": "annual",
  "fiscal_year": 2025,
  "period_end": "2025-12-31",
  "pdf_path": "data/market-report-finder/downloads/HK/TENCENT/2025/年报/...",
  "metadata_json": "data/market-report-finder/downloads/HK/TENCENT/2025/年报/....metadata.json",
  "parser_result_dir": "data/pdf-parser/results/<task_id>",
  "industry_profile": "internet_platform",
  "expected_metrics": [
    "operating_revenue",
    "net_profit",
    "total_assets",
    "total_liabilities",
    "total_equity",
    "operating_cash_flow"
  ],
  "expected_evidence": true
}
```

新增脚本：

```text
scripts/hk/discover_hk_parsed_cases.py
```

功能：

- 扫描 HK 下载目录和 `data/pdf-parser/results`。
- 按 PDF 文件名、metadata、hash 或人工映射文件匹配 parser result。
- 输出 `hk_50_cases.json`。
- 对缺失 `document_full.json`、`table_index.json`、metadata 的 case 输出 warning。

验收命令：

```bash
cd /home/maoyd/siq-research-engine
python3 scripts/hk/discover_hk_parsed_cases.py --limit 50 --output eval_datasets/market_ingestion_cases/hk_50_cases.json
```

验收标准：

- 至少 50 个 case 有 `pdf_path`、`metadata_json`、`parser_result_dir`。
- 每个 parser result 至少存在 `document_full.json` 和 `table_index.json`。
- 不能静默跳过缺失项，必须写入 `warnings`。

## 四、证据包合同

### 1. 目录结构

港股 evidence package 输出到：

```text
data/wiki/hk_reports/<ticker>/<fiscal_year>/<report_type>_<filing_key>/
  manifest.json
  README.md
  raw/
    report.pdf
    report.metadata.json
  sections/
    report.md
    section_index.json
  tables/
    table_index.json
    table_0001.json
    table_0002.json
  metrics/
    financial_data.json
    financial_checks.json
    load_plan.json
  qa/
    quality_report.json
    source_map.json
    extraction_warnings.json
```

必须符合：

```text
docs/architecture/market-evidence-package-contract.md
```

### 2. HK manifest 追加字段

`manifest.json` 除统一合同字段外，港股建议追加：

```json
{
  "exchange": "HKEX",
  "stock_code": "00700",
  "hkex_stock_code": "00700",
  "report_language": "zh-Hant|zh-Hans|en|bilingual|unknown",
  "industry_profile": "general|bank|insurance|property|energy|internet_platform|manufacturing|retail",
  "source_pdf_sha256": "...",
  "parser_result_dir": "data/pdf-parser/results/<task_id>",
  "pdf_parser_task_id": "<task_id>",
  "pdf_parser_quality_status": "pass|warning|fail"
}
```

### 3. evidence_id 规则

港股 evidence id 必须稳定：

```text
hk:<filing_id>:p<page_number>:t<table_index>:r<row_index>:c<column_index>
```

如果一个 fact 来自整行而非单元格，可用：

```text
hk:<filing_id>:p<page_number>:t<table_index>:r<row_index>
```

但 source map 中仍应尽量保留列坐标和期间列。

### 4. source_map 最小合同

`qa/source_map.json` 每个 evidence 至少包含：

```json
{
  "evidence_id": "hk:HK_00700_2025_annual:p128:t12:r8:c3",
  "market": "HK",
  "filing_id": "HK_00700_2025_annual",
  "ticker": "00700",
  "company_name": "TENCENT",
  "page_number": 128,
  "table_index": 12,
  "row_index": 8,
  "column_index": 3,
  "statement_type": "income_statement",
  "canonical_name": "operating_revenue",
  "period_key": "FY2025",
  "quote_text": "Revenue 660,257",
  "value_text": "660,257",
  "normalized_value": 660257000000,
  "unit": "HKD",
  "scale": 1000000,
  "table_json_path": "tables/table_0012.json",
  "pdf_local_path": "raw/report.pdf",
  "source_url": "https://...",
  "text_hash": "..."
}
```

## 五、HK ParsedArtifact 适配层

新增/修改文件：

```text
scripts/hk/hk_evidence_lib.py
scripts/hk/build_hk_evidence_package.py
services/market-report-rules/src/market_report_rules_service/markets/hk/extractor.py
services/market-report-rules/tests/test_hk_evidence_package.py
```

目标：

把 `apps/pdf-parser` 解析产物转换为 `market-report-rules` 可消费的 `ParsedArtifact(market=Market.HK)`。

实现要求：

1. 读取 `document_full.json`。
   - 提取页、段落、标题、表格。
   - 保留 PDF 页码。
   - 如果有 bbox，保留 bbox。

2. 读取 `content_list_enhanced.json`。
   - 用于补充章节顺序、表格前后文、图片/表格标题。
   - 不得用纯文本重新猜表格坐标。

3. 读取 `table_index.json`。
   - 生成 `tables/table_index.json`。
   - 每张表输出 `tables/table_000N.json`。
   - 保留 `page_number`、`table_index`、`title`、`row_count`、`column_count`、`cells`。

4. 生成 `ParsedTable`。
   - 每个 cell 至少有 `row_index`、`column_index`、`text`。
   - 推荐有 `bbox`、`page_number`、`is_header`、`col_span`、`row_span`。

5. 生成章节 Markdown。
   - `sections/report.md` 用于 Wiki 阅读和 Milvus section chunk。
   - `sections/section_index.json` 记录章节标题、页码范围、字符范围。

验收命令：

```bash
cd /home/maoyd/siq-research-engine
python3 scripts/hk/build_hk_evidence_package.py \
  <pdf_path> \
  --parser-result <parser_result_dir> \
  --metadata <metadata_json> \
  --force
```

验收标准：

- 生成完整 evidence package。
- 每个 package 通过统一 validator。
- 至少输出 1 个 table index 和 1 个 section index。
- 每个 financial fact 都能在 `qa/source_map.json` 找到 evidence。

## 六、HK 财务抽取规则

新增/修改文件：

```text
services/market-report-rules/src/market_report_rules_service/markets/hk/rules.py
services/market-report-rules/src/market_report_rules_service/markets/hk/aliases.py
services/market-report-rules/src/market_report_rules_service/markets/hk/industry_profiles.py
services/market-report-rules/tests/test_hk_rules.py
```

### 1. 表类型识别

必须识别：

- 利润表 / 综合收益表
- 资产负债表 / 财务状况表
- 现金流量表
- 权益变动表可作为 P1

标题 alias 要覆盖：

```text
Consolidated Statement of Profit or Loss
Consolidated Statement of Comprehensive Income
Consolidated Statement of Financial Position
Consolidated Statement of Cash Flows
綜合損益表
綜合全面收益表
綜合財務狀況表
綜合現金流量表
合併利潤表
合併資產負債表
合併現金流量表
```

选择规则：

- 优先 consolidated / 綜合 / 合併口径。
- 避免误选 company-only / parent-only 表。
- 避免把 notes 明细表误识别为主表。
- 同一 statement 多候选时，记录 rejected candidates 到 `qa/extraction_warnings.json`。

### 2. 核心 canonical metric

P0 必须覆盖：

利润表：

- `operating_revenue`
- `gross_profit`
- `operating_profit`
- `profit_before_tax`
- `income_tax_expense`
- `net_profit`
- `profit_attributable_to_owners`

资产负债表：

- `total_assets`
- `total_liabilities`
- `total_equity`
- `cash_and_cash_equivalents`
- `trade_receivables`
- `inventories`
- `borrowings`

现金流量表：

- `operating_cash_flow`
- `investing_cash_flow`
- `financing_cash_flow`
- `cash_and_cash_equivalents_end`

P1 扩展：

- 银行：`net_interest_income`、`net_fee_income`、`loans_and_advances`、`customer_deposits`
- 保险：`insurance_revenue`、`insurance_service_result`、`investment_return`
- 地产：`contracted_sales`、`investment_properties`
- 互联网平台：`gmv`、`monthly_active_users`、`paying_users`

### 3. 单位、币种和期间

实现要求：

- 从表头或章节上下文识别币种：HKD、RMB、USD。
- 识别单位：元、千元、百万元、million、RMB million、HK$ million。
- 识别期间列：本年、上年、2025、2024、Year ended 31 December 2025。
- 同一行多期间列必须分别生成 fact。
- 不允许把百分比、每股指标误当金额。

验收标准：

- 50 份样本均能输出 `metrics/financial_data.json`。
- 通用行业样本至少识别三大表中的 3 张。
- 银行/保险第一轮允许现金流量表 warning，但不能静默缺失。
- 核心 metric 缺失时必须写入 `missing_metrics`。

## 七、财务校验和质量报告

新增/修改文件：

```text
services/market-report-rules/src/market_report_rules_service/validation.py
services/market-report-rules/src/market_report_rules_service/markets/hk/quality.py
services/market-report-rules/tests/test_hk_quality_report.py
```

### 1. financial_checks.json

必须包含：

```json
{
  "overall_status": "pass|warning|fail",
  "checks": [
    {
      "check_id": "balance_sheet_equation",
      "status": "pass|warning|fail",
      "severity": "critical|warning|info",
      "message": "...",
      "inputs": [
        {"canonical_name": "total_assets", "period_key": "FY2025", "value": 123}
      ],
      "tolerance": 0.01,
      "evidence_ids": ["..."]
    }
  ],
  "missing_metrics": [],
  "warnings": []
}
```

P0 校验：

- 资产 = 负债 + 权益。
- 税前利润 - 所得税约等于净利润。
- 现金及现金等价物期末余额与现金流量表期末余额一致。
- 三大表期间列一致。
- 每个 normalized fact 至少有一个 evidence。

行业特化：

- 银行/保险缺普通收入、存货等指标不应直接 fail。
- 银行/保险的现金流量表缺失第一阶段可 warning。
- 地产和投资控股公司可对 fair value gain/loss 给 warning 解释。

### 2. qa/quality_report.json

必须包含：

```json
{
  "overall_status": "pass|warning|fail",
  "filing_id": "HK_00700_2025_annual",
  "parser_status": "pass|warning|fail",
  "rule_status": "pass|warning|fail",
  "section_count": 128,
  "table_count": 420,
  "statement_table_count": 3,
  "raw_cell_count": 100000,
  "normalized_metric_count": 96,
  "evidence_coverage_ratio": 1.0,
  "required_statement_status": {
    "income_statement": "pass",
    "balance_sheet": "pass",
    "cash_flow": "warning"
  },
  "critical_warnings": [],
  "parser_warnings": [],
  "rule_warnings": [],
  "rejected_candidates": []
}
```

质量分级：

- `pass`：核心表和核心指标满足行业 profile，evidence coverage 为 1。
- `warning`：可入库但存在行业差异、现金流缺失、单项勾稽不完全一致等。
- `fail`：无法生成三大表核心事实、缺 evidence、manifest 不完整、解析产物损坏。

验收标准：

- 50 份样本全部生成 `qa/quality_report.json`。
- `fail` case 必须有明确原因。
- 不允许通过删除 warning 来制造 pass。

## 八、PostgreSQL 港股 schema 和导入器

新增/修改文件：

```text
db/ddl/020_create_pdf2md_hk_schema.sql
db/imports/import_hk_evidence_package_to_postgres.py
db/imports/tests/test_import_hk_evidence_package.py
```

### 1. schema

schema 名称：

```sql
pdf2md_hk
```

核心表：

- `companies`
- `filings`
- `parse_runs`
- `artifacts`
- `filing_sections`
- `pdf_pages`
- `pdf_tables`
- `financial_facts`
- `operating_metric_facts`
- `financial_checks`
- `evidence_citations`
- `quality_reports`
- `retrieval_chunks`

### 2. 关键字段

`filings`：

- `filing_id`
- `ticker`
- `stock_code`
- `company_name`
- `report_type`
- `fiscal_year`
- `period_end`
- `published_at`
- `source_url`
- `pdf_sha256`

`parse_runs`：

- `parse_run_id`
- `filing_id`
- `parser_version`
- `rules_version`
- `artifact_hashes`
- `quality_status`
- `created_at`

`pdf_tables`：

- `table_id`
- `parse_run_id`
- `page_number`
- `table_index`
- `title`
- `row_count`
- `column_count`
- `table_json_path`

`financial_facts`：

- `fact_id`
- `parse_run_id`
- `filing_id`
- `ticker`
- `statement_type`
- `canonical_name`
- `label`
- `period_key`
- `period_end`
- `value`
- `unit`
- `currency`
- `scale`
- `confidence`
- `evidence_id`
- `source_table_id`

`evidence_citations`：

- `evidence_id`
- `parse_run_id`
- `filing_id`
- `page_number`
- `table_index`
- `row_index`
- `column_index`
- `quote_text`
- `table_json_path`
- `pdf_local_path`
- `source_url`
- `text_hash`

### 3. 幂等约束

必须设计唯一约束：

- `filings(filing_id)`
- `parse_runs(parse_run_id)`
- `pdf_tables(parse_run_id, page_number, table_index)`
- `evidence_citations(evidence_id)`
- `financial_facts(parse_run_id, canonical_name, period_key, evidence_id)`

导入策略：

- 先 upsert `companies`、`filings`、`parse_runs`。
- 再 upsert artifacts、sections、pages、tables。
- 再 upsert evidence。
- 最后 upsert facts、checks、quality report。
- 同一 package 重跑不增加重复 facts。

验收命令：

```bash
cd /home/maoyd/siq-research-engine
python3 db/imports/import_hk_evidence_package_to_postgres.py <package_dir> --run-ddl
python3 db/imports/import_hk_evidence_package_to_postgres.py <package_dir>
```

验收 SQL：

```sql
select ticker, canonical_name, period_key, value, evidence_id
from pdf2md_hk.financial_facts
where ticker = '00700'
order by canonical_name, period_key;
```

```sql
select f.canonical_name, e.page_number, e.table_index, e.row_index, e.column_index, e.quote_text
from pdf2md_hk.financial_facts f
join pdf2md_hk.evidence_citations e on e.evidence_id = f.evidence_id
where f.ticker = '00700'
limit 20;
```

验收标准：

- 同一 package 导入两次，`financial_facts` 行数不翻倍。
- SQL 能从 fact 追到 evidence，再追到 table JSON 和 PDF。
- 50 份样本至少 45 份能完成入库；失败样本必须有原因。

## 九、50 份样本批量跑规则

新增/修改文件：

```text
scripts/hk/ingest_hk_case_set.py
scripts/maintenance/run_market_ingestion_eval.py
eval_datasets/market_ingestion_cases/hk_50_cases.json
```

CLI：

```bash
cd /home/maoyd/siq-research-engine
python3 scripts/hk/ingest_hk_case_set.py \
  --cases eval_datasets/market_ingestion_cases/hk_50_cases.json \
  --build-package \
  --validate-package \
  --import-db \
  --report-output data/reports/hk_ingestion_eval_50.json \
  --markdown data/reports/hk_ingestion_eval_50.md
```

输出报告字段：

- case 数量
- package 成功数
- validator pass/warning/fail
- 入库成功数
- 三大表识别率
- 核心 metric 覆盖率
- evidence coverage ratio
- warning/fail 原因分布
- 行业 profile 分布
- 每个 case 的缺失指标和候选表错误

第一轮质量门槛：

- 50/50 case 生成 evidence package。
- 50/50 case 生成 `quality_report.json`。
- 通用行业样本三大表识别率不低于 90%。
- 所有 normalized financial facts 的 evidence coverage ratio 必须为 1。
- `fail` 不超过 10%，且必须全部有可解释原因。
- 银行/保险/地产样本允许 warning，但不能缺 source map。

最终 P0 质量门槛：

- 50/50 case 可重复生成 package。
- 50/50 case 通过 validator。
- 50/50 case 可幂等入库。
- 通用行业样本核心指标覆盖率不低于 90%。
- 银行/保险/地产样本必须进入行业 profile，不得误用通用规则硬 fail。

## 十、Milvus 入库和智能体召回

新增/修改文件：

```text
scripts/vector-index/milvus-ingestion/ingest_market_evidence_chunks.py
scripts/vector-index/milvus-ingestion/README.md
services/market-report-rules/src/market_report_rules_service/evidence_package.py
```

collection：

```text
siq_hk_reports
```

### 1. chunk 类型

Milvus chunk 从 evidence package 生成，不直接从原始 PDF 切。

P0 chunk：

- `section`：年报章节文本。
- `table`：主财务表 Markdown/JSON 摘要。
- `fact`：单个 canonical financial fact 卡片。
- `quality`：质量报告和 warning 摘要。

P1 chunk：

- `operating_metric`
- `management_discussion`
- `risk_factor`
- `audit_note`

### 2. fact chunk 模板

```text
Market: HK
Ticker: 00700
Company: TENCENT
Fiscal year: 2025
Statement: income_statement
Metric: operating_revenue
Period: FY2025
Value: HKD 660,257 million
Evidence: page 128, table 12, row 8, column 3
Source: annual report PDF
```

### 3. Milvus metadata

必填：

```json
{
  "market": "HK",
  "schema": "pdf2md_hk",
  "collection": "siq_hk_reports",
  "ticker": "00700",
  "stock_code": "00700",
  "company_name": "TENCENT",
  "filing_id": "HK_00700_2025_annual",
  "parse_run_id": "...",
  "doc_type": "section|table|fact|quality",
  "evidence_id": "hk:...",
  "canonical_name": "operating_revenue",
  "period_key": "FY2025",
  "page_number": 128,
  "table_index": 12,
  "wiki_path": "data/wiki/hk_reports/00700/2025/annual_...",
  "db_schema": "pdf2md_hk",
  "source_url": "https://..."
}
```

### 4. 入库命令

dry run：

```bash
cd /home/maoyd/siq-research-engine
python3 scripts/vector-index/milvus-ingestion/ingest_market_evidence_chunks.py \
  --package <package_dir> \
  --collection siq_hk_reports \
  --dry-run
```

正式入库：

```bash
python3 scripts/vector-index/milvus-ingestion/ingest_market_evidence_chunks.py \
  --package <package_dir> \
  --collection siq_hk_reports
```

验收标准：

- 删除 `siq_hk_reports` 后可从 Wiki package 重建。
- chunk metadata 能反查 `pdf2md_hk.evidence_citations`。
- Agent 检索结果必须返回 `evidence_id`，不能只返回文本。

### 5. 召回验收问题

每个样本至少测试：

- “腾讯 2025 年收入是多少？来源在哪一页？”
- “腾讯 2025 年总资产和总负债分别是多少？”
- “这份年报现金流量表有没有抽取 warning？”
- “给出 2025 年净利润数字，并返回 PDF 表格坐标。”

验收标准：

- 回答数字必须来自 PostgreSQL fact 或 Wiki fact chunk。
- 回答必须附 evidence。
- evidence 能跳回 PDF 页/表格/行列坐标。

## 十一、API、前端和 Agent 联通

新增/修改文件：

```text
apps/api/routers/market_reports.py
apps/web/src/pages/HkParsing.tsx
apps/web/src/pages/MarketParsingPage.tsx
apps/web/src/components/pdf/MarketParsingTabs.tsx
apps/web/src/lib/marketIngestionApi.ts
```

API：

- `GET /api/market-reports/packages?market=HK`
- `POST /api/market-reports/packages/build`
- `POST /api/market-reports/packages/import`
- `POST /api/market-reports/packages/vector-ingest`
- `GET /api/market-reports/packages/{filing_id}/quality`
- `GET /api/market-reports/evidence/{evidence_id}`

前端必须展示：

- 下载状态
- PDF 解析状态
- evidence package 状态
- PostgreSQL 入库状态
- Milvus 入库状态
- quality status
- warning/fail 原因
- fact evidence 预览入口

Agent 回查流程：

```text
Milvus hit
  -> metadata.evidence_id
  -> pdf2md_hk.evidence_citations
  -> pdf2md_hk.financial_facts / Wiki source_map
  -> PDF 页码/表格/行列坐标
```

验收标准：

- 前端能从 HK 列表进入质量报告。
- 点击指标 evidence 能看到页码、表格、行列和 quote。
- Agent 返回答案时包含 evidence id 和 source citation。

## 十二、行业 profile

新增文件：

```text
services/market-report-rules/src/market_report_rules_service/markets/hk/industry_profiles.py
```

P0 profile：

- `general`
- `bank`
- `insurance`
- `property`
- `internet_platform`
- `manufacturing`
- `energy`

profile 需要定义：

- 必需 statement。
- 必需 metric。
- 可选 metric。
- 不适用 metric。
- 校验规则启停。
- warning 降级策略。

示例：

```json
{
  "profile": "bank",
  "required_metrics": [
    "net_interest_income",
    "profit_attributable_to_owners",
    "total_assets",
    "customer_deposits"
  ],
  "not_applicable_metrics": [
    "inventories",
    "gross_profit"
  ],
  "cash_flow_required": false
}
```

## 十三、人工修正闭环

P1 新增：

```text
db/ddl/021_create_pdf2md_hk_corrections.sql
db/imports/apply_hk_corrections.py
apps/web/src/pages/HkParsing.tsx
```

修正记录必须保存：

- `correction_id`
- `filing_id`
- `parse_run_id`
- `fact_id`
- `evidence_id`
- `old_value`
- `new_value`
- `old_label`
- `new_label`
- `operator`
- `reason`
- `created_at`

要求：

- 修正后 fact 标记 `corrected=true`。
- 保留原始 evidence。
- 新增 correction evidence，不覆盖原始证据。
- Milvus fact chunk 重建时反映 corrected 状态。

## 十四、测试矩阵

规则服务：

```bash
cd /home/maoyd/siq-research-engine/services/market-report-rules
python3 -m pytest tests/test_hk_rules.py tests/test_hk_evidence_package.py
python3 -m pytest tests
```

DB 导入：

```bash
cd /home/maoyd/siq-research-engine
python3 -m pytest db/imports/tests/test_import_hk_evidence_package.py
```

50 样本：

```bash
python3 scripts/hk/ingest_hk_case_set.py \
  --cases eval_datasets/market_ingestion_cases/hk_50_cases.json \
  --build-package \
  --validate-package \
  --report-output data/reports/hk_ingestion_eval_50.json \
  --markdown data/reports/hk_ingestion_eval_50.md
```

Milvus dry run：

```bash
python3 scripts/vector-index/milvus-ingestion/ingest_market_evidence_chunks.py \
  --package <package_dir> \
  --collection siq_hk_reports \
  --dry-run
```

A 股回归：

```bash
cd /home/maoyd/siq-research-engine/apps/pdf-parser
python3 -m pytest tests
```

Finder 回归：

```bash
cd /home/maoyd/siq-research-engine/services/market-report-finder
python3 -m pytest tests
```

前端构建：

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm run build
```

## 十五、分阶段交付

### P0：50 样本证据链闭环

必须完成：

- `hk_50_cases.json`
- HK ParsedArtifact 适配
- HK evidence package 构建器
- HK rules 核心三表抽取
- `financial_checks.json`
- `qa/quality_report.json`
- `qa/source_map.json`
- `pdf2md_hk` schema
- 幂等导入器
- 50 样本批量报告

完成标准：

- 50 份样本都能生成 package。
- 所有 normalized fact 都有 evidence。
- 50 份样本都能输出质量报告。
- 至少 45 份样本可入库。
- fail case 有明确原因。

### P1：Milvus 和 Agent 可回溯召回

必须完成：

- `siq_hk_reports` collection 入库。
- fact/table/section/quality chunk。
- metadata 反查 DB evidence。
- API evidence 查询。
- 前端质量报告和 evidence 预览。
- Agent 回答附 evidence。

完成标准：

- 50 份样本可 dry-run 生成 chunk。
- 至少 20 份样本完成 Milvus 入库。
- 测试问题能返回准确数字和 PDF 坐标。

### P2：行业扩展和人工修正

必须完成：

- 银行/保险/地产/互联网/制造/能源 profile。
- 行业 metric 覆盖。
- 人工修正记录。
- 修正后重建 DB/Milvus。

完成标准：

- 行业样本 warning 原因可解释。
- 人工修正不破坏原始 evidence。
- corrected fact 可被 Agent 正确引用。

## 十六、不要做的事

- 不要把 HK 数据写入 `pdf2md`。
- 不要修改 A 股 legacy DDL 和导入脚本。
- 不要让 Milvus 成为唯一事实源。
- 不要用 LLM 补财务数字。
- 不要为了 pass 删除 warning。
- 不要只保存最终数字而丢失 PDF 坐标。
- 不要把银行/保险强行套普通制造业校验。

## 十七、最终 Definition of Done

港股链路可认为完成时，必须同时满足：

- 50 份已解析案例可批量生成 HK evidence package。
- 每个 package 通过统一 evidence package validator。
- `financial_data.json`、`financial_checks.json`、`quality_report.json`、`source_map.json` 完整。
- PostgreSQL `pdf2md_hk` 可幂等导入全部 pass/warning package。
- SQL 可从任一核心 fact 追到 PDF 页码、表格、行列和 quote。
- Milvus `siq_hk_reports` 可从 Wiki package 重建。
- Agent 命中召回后能返回 evidence id，并能回查到 DB/Wiki/PDF。
- A 股 PDF parser 回归测试保持通过。
