# HK 市场 PostgreSQL 入库与智能体召回设计

## 背景与目标

HK 市场财报解析已经形成 `data/wiki/hk/companies/<stock>-<name>/reports/<report_id>` 的 evidence package 结构。该目录是前端和智能体的主入口，包含 `manifest.json`、`metrics/financial_data.json`、`qa/source_map.json`、`tables/table_index.json`、`parser/document_full.json` 等结构化产物。

PostgreSQL 不作为二次抽取来源，也不替代 Wiki package。它的定位是结构化索引、批量查询、跨公司/跨年度分析、质量统计，以及 Wiki package 缺失或需要 SQL 聚合时的证据坐标兜底。HK 入库必须直接读取解析产物和 evidence package 中的结构化 JSON，不从 Markdown 或自然语言内容重新抽取事实。

## 设计原则

1. Wiki package 是主证据入口，PostgreSQL 是同步索引与兜底查询层。
2. 入库源必须是结构化解析产物：`financial_data`、`source_map`、`table_index`、`document_full`、`financial_checks`、`quality_report`。
3. 所有可回答财务事实的记录必须能回到页码、表格、行列、bbox 或 source map 证据。
4. 公司主键不能依赖公司简称；HK 公司稳定锚点是市场代码加港交所股票代码。
5. 与 A 股链路保持同构：解析产物直接入库，Wiki 只提供组织、索引和智能体阅读入口。

## 数据源优先级

HK importer 读取一个 evidence package 目录，按以下文件入库：

| 文件 | 入库用途 |
| --- | --- |
| `manifest.json` | 公司、披露、报告、来源 URL、会计准则、package 路径 |
| `metrics/financial_data.json` | 财务事实、标准科目、期间、单位、币种、source/evidence |
| `qa/source_map.json` | 页码、表格、行列、bbox、quote，支撑证据回看 |
| `tables/table_index.json` 和 `tables/*.json` | 表格索引、表头、行列结构、原始单元格 |
| `parser/document_full.json` | 原始解析结构兜底，尤其是 content blocks、pages、enhanced tables |
| `metrics/financial_checks.json` | 勾稽校验结果 |
| `qa/quality_report.json` | 质量报告、关键表识别、warning、覆盖率 |
| `qa/footnotes.json`、`qa/toc.json`、`qa/financial_note_links.json`、`parser/table_relations.json`、`qa/table_quality_signals.json` | 增强结构和智能体召回辅助 |

## 主键与身份

公司层：

```text
company_id = HK:{hkex_stock_code}
例：HK:00700
```

`hkex_stock_code` 和 `stock_code` 均保存五位港股代码。`company_id` 是技术主键，`hkex_stock_code` 是业务唯一锚点。公司简称、英文名、中文名可变，不参与主键生成。

披露层：

```text
filing_id = HK:{hkex_stock_code}:{hkex_document_id/accession_number}
例：HK:00700:12100024
```

报告包层：

```text
report_id = {fiscal_year}-{report_type}-{accession_number}
例：2025-annual-12100024
```

解析运行层：

```text
parse_run_id = stable hash(filing_id + parser_version + rules_version + artifact_hashes)
```

同一份披露可因 parser/rules/产物 hash 不同产生多个解析运行，便于回归比较。

## PostgreSQL Schema

目标数据库默认 `siq_hk`，schema 固定为 `pdf2md_hk`。现有 `020_create_pdf2md_hk_schema.sql` 已有大部分基础表，后续需要补充字段写入、约束、索引和智能体视图。

### companies

公司主数据。建议字段：

```text
company_id PK
ticker
stock_code
hkex_stock_code
exchange
company_name
short_name
company_name_en
company_name_zh
aliases jsonb
raw jsonb
created_at
updated_at
```

约束与索引：

```text
unique(hkex_stock_code)
index(ticker)
gin(aliases)
```

`raw` 保存 manifest、company.json、目录名和身份推断来源。

### filings

HKEX 披露文件层。建议字段：

```text
filing_id PK
company_id FK
ticker
stock_code
report_id
form
report_type
fiscal_year
fiscal_period
period_end
published_at
source_id
source_url
local_path
accounting_standard
quality_status
raw jsonb
created_at
updated_at
```

索引：

```text
(company_id, fiscal_year desc, report_type)
(ticker, fiscal_year desc, report_type)
(period_end)
```

### parse_runs

解析运行层。建议字段：

```text
parse_run_id PK
filing_id FK
parser_version
rules_version
wiki_package_path
source_result_path
status
completed_at
warnings jsonb
artifact_hashes jsonb
raw jsonb
```

### parser_artifacts

产物文件索引。用于审计和增量判断。

```text
parse_run_id
filing_id
artifact_key
local_path
sha256
schema_version
page_number
table_index
target
raw jsonb
PK(parse_run_id, artifact_key)
```

### content_blocks、pdf_pages、pdf_tables

页、块、表格定位层。来源为 `document_full.json`、`content_list_enhanced.json`、`tables/table_index.json`。

`content_blocks` 必须保留 `page_number`、`bbox`、`block_type`、`target`、`markdown_path/raw`。

`pdf_tables` 必须保留 `page_number`、`table_index`、`title`、`row_count`、`column_count`、`table_json_path`、`raw`，后续可补 `bbox` 字段。

### evidence_citations

证据坐标同步表。它是 Wiki `qa/source_map.json` 的 SQL 副本，用于兜底和批量检索。

```text
evidence_id PK
filing_id
parse_run_id
source_type
source_id
page_number
table_index
row_index
column_index
bbox jsonb
quote_text
local_path
source_url
target
raw jsonb
```

智能体优先读 Wiki `source_map.json`，SQL 查询失败或需要聚合时读本表。

### financial_statement_items

主财务事实明细表。来源为 `metrics/financial_data.json`，证据来自 source/evidence 或 `qa/source_map.json`。

```text
item_uid PK
filing_id
parse_run_id
company_id
ticker
stock_code
company_name
exchange
statement_id
statement_type
statement_name
scope
scope_name
item_index
period_key
item_name
canonical_name
value
raw_value
unit
currency
scale
period_start
period_end
fiscal_year
fiscal_period
accounting_standard
industry_profile
confidence
source_page_number
source_table_index
source_row_index
source_column_index
source_bbox jsonb
evidence_id FK
raw jsonb
```

索引：

```text
(ticker, statement_type, canonical_name, period_key)
(filing_id, source_page_number, source_table_index)
(company_id, fiscal_year, canonical_name, period_key)
```

### financial_key_metrics、分表与宽表

`financial_key_metrics`、`financial_balance_sheet_items`、`financial_income_statement_items`、`financial_cash_flow_statement_items` 可沿用 `financial_statement_items` 的字段，用于加速常见查询。

`financial_all_metrics_wide` 保存每个 period 的 JSON 宽表：

```text
parse_run_id
filing_id
company_id
ticker
stock_code
company_name
exchange
period_key
fiscal_year
fiscal_period
balance_sheet jsonb
income_statement jsonb
cash_flow_statement jsonb
key_metrics jsonb
all_metrics jsonb
raw jsonb
PK(parse_run_id, period_key)
```

### financial_checks 与 quality_reports

`financial_checks` 保存规则、期间、状态、差异、容忍度和 raw payload。

`quality_reports` 保存解析质量、规则质量、关键表识别状态、覆盖率、warning、required statement status。

### 增强结构表

以下表用于智能体上下文增强和证据解释：

```text
footnotes
toc_entries
financial_note_links
table_relations
table_quality_signals
```

这些表不是主要事实来源，但能帮助回答“这个表为什么可信”、“附注在哪里”、“表格是否跨页/稀疏页补全”等问题。

## 智能体召回设计

PostgreSQL 侧增加轻量召回表和视图，不替代 Milvus。

### retrieval_chunks

用于 SQL 层快速召回和调试，不做向量嵌入主流程。

```text
chunk_uid PK
filing_id
parse_run_id
company_id
ticker
doc_type
section_title
canonical_name
statement_type
period_key
page_number
table_index
evidence_id
wiki_path
source_url
text
text_hash
metadata jsonb
embedded boolean
```

建议写入：

1. 财务事实摘要。
2. 关键表候选摘要。
3. 勾稽失败摘要。
4. 质量 warning 摘要。
5. 附注/脚注关系摘要。

### v_agent_financial_facts

面向智能体的事实视图，合并事实、披露、公司、证据。

字段：

```text
company_id
ticker
company_name
filing_id
report_type
fiscal_year
period_key
statement_type
canonical_name
item_name
value
unit
currency
page_number
table_index
row_index
column_index
bbox
quote_text
wiki_package_path
source_url
```

### v_latest_company_reports

每家公司最新报告视图，用于工作台和智能体初始化上下文。

## 入库脚本行为

脚本继续使用：

```bash
python db/imports/import_hk_evidence_package_to_postgres.py \
  data/wiki/hk/companies/00700-TENCENT/reports/2025-annual-12100024 \
  --ddl
```

但行为要补强：

1. `_upsert_company` 写入完整公司字段与 aliases。
2. `_upsert_filing` 写入 `report_id` 和完整来源字段。
3. `_insert_evidence` 写入 bbox，并兼容 `entries` 与 future source map payload。
4. `_insert_financial_facts` 从 `financial_data.json` 写入明细、分表和宽表。
5. 新增 `_insert_parser_artifacts`、`_insert_content_blocks`、`_insert_enhancement_tables`。
6. 新增 retrieval chunk 写入。
7. 保持幂等：同一 `parse_run_id` 重跑先删除 run 级子表，再重插。

## 前端链路

HK 页面主流程应从通用 PDF workflow 切到市场 package workflow：

1. `构建 HK 证据包`：调用 market package build，输出到 `data/wiki/hk/companies/.../reports/...`。
2. `导入 HK PostgreSQL`：调用 `/api/market-reports/packages/import`，传 `market=HK`、`package_path`、`ddl=true`。
3. `查看证据`：优先打开 Wiki package 文件；PostgreSQL 页码和证据坐标作为兜底。

Milvus HK 管道完成前不暴露正式向量入库按钮。

## 测试策略

1. DDL 测试：确认 `companies` 唯一约束、`filings.report_id`、evidence bbox、agent views 存在。
2. Importer 单元测试：用最小 HK package 验证 company/filing/fact/evidence/check/quality 的 SQL 参数。
3. 回归样本测试：抽取现有 50 个 HK package，dry-run 或临时库导入，统计失败数、事实数、证据覆盖率。
4. API 测试：`/api/market-reports/packages/import` 对 HK 选择 HK importer，并设置 `SIQ_HK_PGDATABASE=siq_hk`。
5. 前端测试：HK 页面按钮不再调用通用 workflow 的 `db-import` 作为主入库入口。

## 非目标

1. 不从 Wiki Markdown 二次抽取财务事实。
2. 不在本阶段实现 HK Milvus 正式入库。
3. 不把 HK 数据写入 A 股 `pdf2md` schema。
4. 不要求一次性补齐中英文公司全称；先保留字段和 aliases，后续从 HKEX metadata 或 PDF 封面增强。
