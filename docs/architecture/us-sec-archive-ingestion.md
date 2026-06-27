# 美股 SEC 财报归档与入库设计

本文设计一条面向美股 SEC 年报、季报和 20-F/6-K 的确定性归档入库链路。目标是复用 A 股现有“文件证据包 + PostgreSQL 事实库 + Milvus 语义检索”的思想，但不把 SEC HTML/iXBRL 强行塞进 PDF/OCR 管线。

核心原则：

- HTML/iXBRL 原文是主证据，不伪造 PDF 页码。
- XBRL 数字事实必须进入 PostgreSQL，供稳定查询、计算、同比环比、勾稽和智能体问答使用。
- Wiki 保存完整证据包和可读 Markdown，作为可审计、可回放、可人工复核的权威文件层。
- Milvus 只保存可语义召回的文本 chunk 和轻量 metadata，不作为唯一事实源。
- Wiki、PostgreSQL、Milvus 必须按市场物理隔离，不能把 CN/HK/US 强行混入同一业务命名空间。
- 解析、抽取、校验、入库脚本应可重复运行、幂等更新，核心财务指标不依赖大模型。

## 零、多市场物理隔离约定

SIQ 的财报知识库应按市场隔离三类存储：Wiki 文件层、PostgreSQL 事实层、Milvus 语义检索层。统一工作台可以聚合展示，但底层存储不能混用。

### 本项目落地命名

本仓库当前以 `infra/docker/docker-compose.yml` 为服务边界。Wiki、PostgreSQL、Milvus 都必须进入本项目管理的目录、容器服务和 collection，不能写入 `/home/maoyd/wiki`、外部非本项目数据库或默认共享 collection。

| 市场 | 宿主机 Wiki 目录 | 容器内 Wiki 目录 | PostgreSQL 服务/数据库 | PostgreSQL Schema | Milvus 服务 | Milvus Collection | 证据定位主键 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A 股 CN | `$SIQ_PROJECT_ROOT/data/wiki/cn_reports` | `/data/wiki/cn_reports` | `postgres` / `siq` | `pdf2md`（现有 legacy），后续可迁移 `pdf2md_cn` | `infra/vector-index/milvus` / `standalone:19530` | `siq_cn_reports` | `task_id + pdf_page + table_index` |
| 港股 HK | `$SIQ_PROJECT_ROOT/data/wiki/hk_reports` | `/data/wiki/hk_reports` | `postgres` / `siq` | `pdf2md_hk` | `infra/vector-index/milvus` / `standalone:19530` | `siq_hk_reports` | `filing_id + pdf_page/table_index` |
| 美股 US | `$SIQ_PROJECT_ROOT/data/wiki/us_sec` | `/data/wiki/us_sec` | `postgres` / `siq` | `sec_us` | `infra/vector-index/milvus` / `standalone:19530` | `siq_us_sec_filings` | `filing_id + accession + section/html_anchor/context_ref` |

本项目本地开发的 PostgreSQL 连接来自 `env/backend.env` 或 compose 环境变量：宿主机连接 `127.0.0.1:15432/siq`，容器内连接 `postgres:5432/siq`。市场隔离优先使用不同 schema，不要求创建任何外部市场数据库。Agent 层必须禁止跨 schema 自动 fallback。

### 为什么必须隔离

- 证据定位不同：A 股/HK 多为 PDF 页码和表格编号，美股是 SEC section、HTML anchor、XBRL context。
- 会计口径不同：CASBE、HKFRS/IFRS、US GAAP/IFRS 不能共用同一套 canonical 规则而不带市场上下文。
- 报告形态不同：A 股/HK PDF 表格抽取为主，美股 XBRL facts 为主。
- Agent 策略不同：美股数字优先 SQL/XBRL，A 股数字可能来自 PDF 表格抽取，港股还要处理繁简体和中英双语标题。
- 重建成本不同：Milvus 可从 Wiki 重建；PostgreSQL facts 可从 Wiki JSON 重建；三者隔离后任一市场可独立清理和回放。

### 跨市场检索方式

跨市场比较不直接扫所有库，而走聚合层：

```text
Agent question
  -> market router
  -> per-market SQL/Wiki/Milvus retrieval
  -> normalized comparison view
  -> final answer with market-specific citations
```

推荐新增只读聚合视图或 API：

| 聚合对象 | 来源 |
| --- | --- |
| `analytics.company_universe` | 本项目 `siq` 数据库内 CN/HK/US schema 的 companies 只读合并 |
| `analytics.normalized_financial_facts` | 本项目 `siq` 数据库内各市场 normalized facts 合并，保留 `market`、`accounting_standard` |
| `analytics.filing_catalog` | 本项目 `siq` 数据库内各市场 filing/report 元数据合并 |

聚合层只用于查询和对比，不作为原始事实写入点。

## 一、总体流水线

```text
data/market-report-finder/downloads/US/<company>/<year>/<folder>/<filing>.htm
  -> scripts/us-sec/build_sec_evidence_package.py
  -> data/wiki/us_sec/<ticker>/<fiscal_year>/<form>_<accession>/
       raw/
       sections/
       tables/
       xbrl/
       metrics/
       qa/
       manifest.json
  -> services/market-report-rules US extractor
  -> db/imports/import_sec_filing_to_postgres.py
  -> scripts/vector-index/milvus-ingestion/ingest_sec_wiki_chunks.py
  -> Agent: PostgreSQL facts + Wiki evidence + Milvus recall
```

建议新增脚本：

| 脚本 | 作用 | 是否依赖大模型 |
| --- | --- | --- |
| `scripts/us-sec/build_sec_evidence_package.py` | 从 SEC HTML/iXBRL/metadata 生成 Wiki 证据包 | 否 |
| `scripts/us-sec/extract_sec_xbrl_facts.py` | 提取 inline XBRL facts、contexts、units、labels | 否 |
| `scripts/us-sec/normalize_sec_metrics.py` | 使用规则映射 canonical metrics | 否 |
| `db/imports/import_sec_filing_to_postgres.py` | 幂等写入 PostgreSQL | 否 |
| `scripts/vector-index/milvus-ingestion/ingest_sec_wiki_chunks.py` | 将 Wiki sections 切片入 Milvus | 否 |
| `scripts/us-sec/classify_sec_industry_profile.py` | 行业 profile 识别，可用 SIC/NAICS/关键词 | 默认否，可选 LLM 辅助 |

## 二、Wiki 如何存

Wiki 是“证据包”和“人可读归档层”。它保存原文、解析结果、章节 Markdown、表格 JSON、XBRL raw facts、规则抽取结果和质量报告。

### 目录布局

建议独立命名空间，不混入 A 股 `companies`：

```text
data/wiki/us_sec/
  AAPL/
    2025/
      10-K_0000320193-25-000079/
        manifest.json
        README.md
        raw/
          filing.htm
          filing.metadata.json
          sec_index.json
        sections/
          business.md
          risk_factors.md
          properties.md
          legal_proceedings.md
          mda.md
          market_risk.md
          financial_statements.md
          controls.md
          notes.md
        sections.json
        tables/
          table_index.json
          table_0001.json
          table_0002.json
        xbrl/
          facts_raw.json
          contexts.json
          units.json
          labels.json
          taxonomy_summary.json
        metrics/
          normalized_metrics.json
          financial_data.json
          financial_checks.json
          operating_metrics.json
        qa/
          quality_report.json
          extraction_warnings.json
          source_map.json
```

### `manifest.json`

`manifest.json` 是入口文件。所有脚本先读它，不扫描猜路径。

关键字段：

```json
{
  "schema_version": "sec_evidence_package_v1",
  "market": "US",
  "ticker": "AAPL",
  "cik": "0000320193",
  "company_name": "Apple Inc.",
  "form": "10-K",
  "accession_number": "0000320193-25-000079",
  "fiscal_year": 2025,
  "fiscal_period": "FY",
  "period_end": "2025-09-27",
  "filing_date": "2025-10-31",
  "source_url": "https://www.sec.gov/Archives/...",
  "local_source_path": "raw/filing.htm",
  "accounting_standard": "US_GAAP",
  "industry_profile": "consumer_hardware",
  "artifacts": {
    "sections": "sections.json",
    "xbrl_facts_raw": "xbrl/facts_raw.json",
    "financial_data": "metrics/financial_data.json",
    "financial_checks": "metrics/financial_checks.json",
    "quality_report": "qa/quality_report.json",
    "source_map": "qa/source_map.json"
  }
}
```

### 章节 Markdown

章节 Markdown 面向人读和向量化，必须保留结构化 frontmatter：

```markdown
---
schema_version: sec_section_v1
market: US
ticker: AAPL
accession_number: 0000320193-25-000079
form: 10-K
section_id: item_1a
section_title: Item 1A. Risk Factors
source_url: https://www.sec.gov/Archives/...
html_anchor: item_1a
---

# Item 1A. Risk Factors

...
```

建议固定章节：

| 文件 | 10-K/20-F 对应内容 |
| --- | --- |
| `business.md` | Item 1 / business overview |
| `risk_factors.md` | Item 1A |
| `properties.md` | Item 2 |
| `legal_proceedings.md` | Item 3 |
| `mda.md` | Item 7 / operating and financial review |
| `market_risk.md` | Item 7A |
| `financial_statements.md` | Item 8 / audited statements |
| `controls.md` | Item 9A |
| `notes.md` | Notes to consolidated financial statements |

10-Q 使用：

| 文件 | 10-Q 对应内容 |
| --- | --- |
| `financial_statements.md` | Part I, Item 1 |
| `mda.md` | Part I, Item 2 |
| `market_risk.md` | Part I, Item 3 |
| `controls.md` | Part I, Item 4 |
| `risk_factors.md` | Part II, Item 1A |

### Wiki 中保留哪些 JSON

| JSON | 用途 |
| --- | --- |
| `sections.json` | 章节边界、标题、DOM anchor、字符 offset |
| `tables/table_index.json` | HTML 表格索引、标题、section、行列数 |
| `xbrl/facts_raw.json` | 原始 iXBRL/companyfacts facts，不丢字段 |
| `xbrl/contexts.json` | contextRef、period、entity、dimensions |
| `xbrl/units.json` | unitRef、currency/share/unit 定义 |
| `metrics/normalized_metrics.json` | canonical metric 映射后的长表 JSON |
| `metrics/financial_data.json` | 对齐现有 `market-report-rules` contract |
| `metrics/financial_checks.json` | 勾稽、期间选择、缺失告警 |
| `qa/source_map.json` | fact/section/table 到 HTML anchor/xpath 的映射 |
| `qa/quality_report.json` | 解析质量门禁 |

## 三、PostgreSQL 如何存

PostgreSQL 是“可计算事实库”。美股必须写入本项目 compose 管理的 PostgreSQL 服务，不连接外部数据库。当前项目数据库为 `siq`，美股使用独立 schema `sec_us`，避免和 A 股、港股口径混淆：

```text
service: postgres
host: 127.0.0.1:15432（宿主机）/ postgres:5432（容器内）
database: siq
schema: sec_us
```

### 核心表

#### 1. 公司与 filing

`sec_us.companies`

| 字段 | 说明 |
| --- | --- |
| `company_id` | 内部主键，建议 `US:<CIK>` |
| `cik` | SEC CIK |
| `ticker` | 股票代码 |
| `company_name` | 公司名 |
| `sic` / `sic_description` | SEC SIC |
| `industry_profile` | 规则 profile |
| `exchange` | Nasdaq/NYSE 等，可为空 |
| `raw` | SEC company metadata |

`sec_us.filings`

| 字段 | 说明 |
| --- | --- |
| `filing_id` | `US:<CIK>:<accession>` |
| `company_id` | FK |
| `ticker` | 冗余便于查询 |
| `form` | 10-K/10-Q/20-F/6-K |
| `accession_number` | SEC accession |
| `fiscal_year` / `fiscal_period` | FY/Q1/Q2/Q3/Q4 |
| `period_end` | 报告期末 |
| `filing_date` / `accepted_at` | 披露日期 |
| `source_url` | SEC 原文 URL |
| `local_path` | Wiki raw 路径 |
| `accounting_standard` | US_GAAP/IFRS |
| `quality_status` | pass/warning/fail |
| `raw` | metadata |

#### 2. 解析运行与文件产物

`sec_us.parse_runs`

| 字段 | 说明 |
| --- | --- |
| `parse_run_id` | `sha256(filing_id + parser_version)` |
| `filing_id` | FK |
| `parser_version` | SEC parser 版本 |
| `rules_version` | market-report-rules 版本 |
| `wiki_package_path` | 证据包目录 |
| `status` | ready/warning/fail |
| `started_at` / `completed_at` | 运行时间 |
| `warnings` | JSONB |

`sec_us.artifacts`

记录 `manifest.json`、section md、facts raw、quality report、source map 等路径、hash、大小。

#### 3. 章节与表格

`sec_us.filing_sections`

| 字段 | 说明 |
| --- | --- |
| `section_id` | item_1 / item_1a / item_7 |
| `section_title` | 原始标题 |
| `section_order` | 顺序 |
| `markdown_path` | Wiki section 路径 |
| `html_anchor` / `xpath` | 原文定位 |
| `text_hash` | 去重 |
| `raw` | sections.json 对应项 |

`sec_us.html_tables`

| 字段 | 说明 |
| --- | --- |
| `table_id` | 稳定 hash |
| `section_id` | 所属章节 |
| `title` | 邻近标题 |
| `row_count` / `column_count` | 表格形状 |
| `table_json_path` | Wiki JSON |
| `html_anchor` / `xpath` | 原文定位 |
| `is_financial_statement_candidate` | 是否疑似三大表 |
| `raw` | 原始表格结构 |

#### 4. XBRL 原始事实

`sec_us.xbrl_facts_raw`

这是最重要的“原始事实层”，尽量不丢 SEC 字段。

| 字段 | 说明 |
| --- | --- |
| `fact_id` | `sha256(filing_id, concept, context_ref, unit_ref, value, decimals)` |
| `filing_id` | FK |
| `concept` | `us-gaap:NetIncomeLoss` |
| `taxonomy` | us-gaap/ifrs-full/dei/srt/extension |
| `label` | label |
| `value_text` | 原始值 |
| `value_numeric` | numeric |
| `unit_ref` / `unit` | USD/shares/USD-per-share |
| `decimals` | 原始 decimals |
| `scale` | iXBRL scale |
| `context_ref` | contextRef |
| `period_start` / `period_end` | 期间 |
| `duration_days` | 持续天数 |
| `instant` | 时点 |
| `fiscal_year` / `fiscal_period` | fy/fp |
| `frame` | SEC frame |
| `dimensions` | JSONB segment dimensions |
| `is_extension` | 是否公司扩展标签 |
| `html_anchor` / `xpath` | 原文定位 |
| `raw` | 原始 fact |

#### 5. 标准化指标

`sec_us.financial_facts`

面向问答、分析和计算的标准长表。

| 字段 | 说明 |
| --- | --- |
| `metric_id` | stable id |
| `filing_id` / `parse_run_id` | 关联 |
| `statement_type` | balance_sheet/income_statement/cash_flow/key_metrics |
| `canonical_name` | 标准指标名 |
| `concept` | 原 XBRL concept |
| `label` | 原 label |
| `value` | 标准数值 |
| `unit` / `currency` | USD/shares/USD_per_share |
| `period_key` | 2025 / 2025Q4YTD / 2025-09-27 |
| `period_start` / `period_end` | 期间 |
| `duration_days` | 区分 FY/YTD/QTD |
| `qtd_ytd_type` | instant/fy/ytd/qtd/h1 |
| `fiscal_year` / `fiscal_period` | 年度/季度 |
| `segment_key` | 分部或维度 hash，consolidated 为空 |
| `dimensions` | JSONB |
| `confidence` | 规则置信度 |
| `evidence_id` | FK |
| `raw_fact_id` | FK |
| `raw` | JSONB |

建议仍生成兼容视图：

| 视图/表 | 用途 |
| --- | --- |
| `sec_us.financial_balance_sheet_items` | 类似 A 股资产负债表查询 |
| `sec_us.financial_income_statement_items` | 利润表查询 |
| `sec_us.financial_cash_flow_statement_items` | 现金流查询 |
| `sec_us.financial_all_metrics_wide` | 单公司单期间宽表 |

这样 Agent 侧可以复用“按 canonical_name 查数”的策略。

#### 6. 经营指标与行业指标

`sec_us.operating_metric_facts`

| 字段 | 说明 |
| --- | --- |
| `metric_name` | 原始指标名 |
| `canonical_name` | 标准名 |
| `industry_profile` | saas/platform/bank/insurance/... |
| `value` / `unit` | 数值单位 |
| `period_key` | 期间 |
| `source_type` | xbrl/html_table/md_section |
| `evidence_id` | FK |
| `confidence` | 置信度 |
| `raw` | 原始证据 |

#### 7. 证据引用

`sec_us.evidence_citations`

| 字段 | 说明 |
| --- | --- |
| `evidence_id` | PK |
| `filing_id` / `parse_run_id` | 关联 |
| `source_type` | sec_xbrl_fact/sec_html_section/sec_html_table |
| `section_id` | Item 7 / Item 8 等 |
| `xbrl_tag` | concept |
| `html_anchor` / `xpath` | 原文定位 |
| `source_url` | SEC URL |
| `local_path` | Wiki 文件路径 |
| `quote_text` | 短摘录 |
| `target` | 前端可打开定位 |
| `raw` | JSONB |

## 四、Milvus 如何存

Milvus 是“语义召回层”，只存可读 chunk 和引用 metadata。

美股使用独立 collection：

```text
siq_us_sec_filings
```

### 入库对象

只入这些：

- `sections/*.md` 的章节文本。
- MD&A 中的经营分析段落。
- Risk Factors 风险段落。
- Notes 注释段落。
- 表格 caption + 轻量表格文本摘要。
- 可选：重大会计政策、收入确认、分部披露。

不建议把每个 XBRL fact 都单独入 Milvus。数字事实进 PostgreSQL，Milvus 只保存与解释相关的上下文。

### chunk metadata

在现有 `siq_chunk_v1` 基础上扩展 SEC 字段：

```json
{
  "schema_version": "siq_chunk_v1",
  "market": "US",
  "doc_type": "sec_filing_section",
  "evidence_level": "source_doc",
  "ticker": "AAPL",
  "cik": "0000320193",
  "company_name": "Apple Inc.",
  "form": "10-K",
  "accession_number": "0000320193-25-000079",
  "fiscal_year": 2025,
  "fiscal_period": "FY",
  "period_end": "2025-09-27",
  "section_id": "item_7",
  "section_title": "Item 7. Management's Discussion and Analysis",
  "wiki_path": "data/wiki/us_sec/AAPL/2025/10-K_0000320193-25-000079/sections/mda.md",
  "source_url": "https://www.sec.gov/Archives/...",
  "html_anchor": "item_7",
  "chunk_uid": "sha256...",
  "citation": "AAPL 2025 10-K, Item 7"
}
```

### chunking 规则

- 先按 SEC item 分段。
- 再按 Markdown heading / paragraph 分段。
- 单 chunk 建议 500-900 中文/英文 token 等价长度。
- 表格只入摘要，不把完整大表全部塞入向量库。
- 每个 chunk 必须有 `section_id`、`wiki_path`、`source_url`。

## 五、必要指标清单

### 通用三大表指标

这些必须覆盖所有行业：

资产负债表：

- `total_assets`
- `current_assets`
- `cash_and_cash_equivalents`
- `trade_receivables`
- `inventories`
- `property_plant_equipment`
- `goodwill`
- `total_liabilities`
- `current_liabilities`
- `borrowings`
- `lease_liabilities`
- `contract_liabilities`
- `total_equity`
- `parent_equity`
- `nci_equity`

利润表：

- `operating_revenue`
- `cost_of_sales`
- `gross_profit`
- `research_and_development_expense`
- `selling_general_admin_expense`
- `operating_profit`
- `interest_expense`
- `total_profit`
- `income_tax_expense`
- `net_profit`
- `parent_net_profit`
- `nci_profit`
- `basic_eps`
- `diluted_eps`
- `weighted_avg_shares_basic`
- `weighted_avg_shares_diluted`

现金流量表：

- `operating_cash_flow_net`
- `investing_cash_flow_net`
- `financing_cash_flow_net`
- `capital_expenditure`
- `depreciation_amortization`
- `share_based_compensation`
- `cash_equivalents_net_increase`
- `fx_effect_cash`

### 期间规则

美股问答很容易错在期间选择，必须规则化：

| filing | 利润表/现金流 | 资产负债表 |
| --- | --- | --- |
| 10-K | FY duration | period_end instant |
| 20-F | FY duration | period_end instant |
| 10-Q | 同时保留 QTD 和 YTD，默认分析用 YTD，季度经营讨论可用 QTD | quarter end instant |
| 6-K | 不假设完整三大表，按披露事实入库 | 如有则入库 |

### 行业 profile

行业 profile 用来补充经营指标，不改变三大表通用指标。

| profile | 经营指标示例 | 来源 |
| --- | --- | --- |
| `general` | employees, customers | HTML section/table |
| `saas` | ARR, RPO, subscription revenue, net retention, billings | MD&A, notes, tables, XBRL extension |
| `internet_platform` | MAU, DAU, GMV, take rate, active merchants | MD&A/tables |
| `consumer_hardware` | product revenue, services revenue, units if disclosed | segment note |
| `retail` | comparable sales, store count, square footage, inventory turnover | MD&A/tables |
| `manufacturing` | shipments, backlog, capacity, order book | MD&A/tables |
| `semiconductor` | wafer shipments, utilization, inventory days, capex | MD&A/tables |
| `energy` | production, reserves, realized price, lifting cost | standardized reserve tables |
| `bank` | net interest income, NIM, loans, deposits, NPL ratio, CET1 | bank-specific tables |
| `insurance` | premiums, combined ratio, loss ratio, reserves | insurance tables |
| `real_estate` | occupancy, NOI, FFO, same-store NOI | REIT tables |

行业指标抽取策略：

1. 优先 XBRL extension tags，但只作为候选，必须保留 tag 和 label。
2. 其次从 MD&A/notes 表格通过规则词典抽取。
3. 无规则命中时不入 PostgreSQL；可仅入 Wiki/Milvus 供问答检索。
4. 可选大模型只用于建议候选映射，不直接写事实库。

## 六、质量门禁

`qa/quality_report.json` 和 `sec_us.parse_runs.status` 至少包含：

| 检查 | 年报要求 |
| --- | --- |
| 原文件存在且 hash 稳定 | blocking |
| 能识别 form/accession/period_end | blocking |
| 能提取 sections | warning |
| 10-K/20-F 有 Assets、Revenue/NetIncome、OCF | blocking 或 severe warning |
| 资产 = 负债 + 权益 | warning/fail，考虑不同标签组合 |
| EPS 与 shares/net income 口径可解释 | warning |
| 10-Q QTD/YTD 区分 | blocking |
| XBRL facts 有 source anchor/context/unit | blocking |
| canonical metric 映射缺失率 | warning |
| extension tag 占比异常 | warning |

## 七、Agent 查询策略

智能体不直接“读一堆 HTML 猜答案”，而是按问题类型路由：

| 问题类型 | 主数据源 | 辅助数据源 |
| --- | --- | --- |
| 数字查询 | PostgreSQL `sec_us.financial_facts` | Wiki source_map |
| 同比/环比/比率 | PostgreSQL + 计算器 | Wiki 解释段落 |
| 管理层解释 | Milvus 召回 MD&A -> Wiki 原文 | PostgreSQL 指标 |
| 风险因素 | Milvus 召回 risk_factors -> Wiki 原文 | 无 |
| 指标出处 | PostgreSQL evidence_id -> Wiki/SEC anchor | 原始 HTML |
| 行业 KPI | PostgreSQL operating_metric_facts | Milvus/Wiki 验证 |

回答生成要求：

- 数字必须来自 PostgreSQL 或明确的 XBRL raw fact。
- 解释必须引用 Wiki section 或 SEC HTML anchor。
- 如果只有 Milvus 召回，没有 PostgreSQL/Wiki 证据，不得输出确定数字。
- 不使用 PDF 页码，除非后续单独生成 HTML rendered page map。

## 八、实施顺序

第一阶段：证据包与 HTML 打开

1. 已完成下载 `.htm` 保留原后缀。
2. 实现 `build_sec_evidence_package.py`。
3. 生成 Wiki `manifest.json`、`raw/filing.htm`、`sections/*.md`、`xbrl/facts_raw.json`。

第二阶段：规则抽取

1. 复用 `services/market-report-rules` 的 `US_CONCEPT_RULES`。
2. 补齐 companyfacts/iXBRL fact 输入适配。
3. 生成 `financial_data.json`、`financial_checks.json`、`normalized_metrics.json`。

第三阶段：PostgreSQL

1. 新增 `db/ddl/010_create_sec_us_schema.sql`。
2. 新增 `db/imports/import_sec_filing_to_postgres.py`。
3. 生成 `sec_us.financial_all_metrics_wide` 或视图。

第四阶段：Milvus

1. 新增 `ingest_sec_wiki_chunks.py`。
2. 只入 sections 和 table summaries。
3. chunk metadata 带 `filing_id/accession/section/wiki_path/source_url`。

第五阶段：Agent 工具

1. 新增 PostgreSQL 查询工具：`sec_metric_lookup.py`。
2. 新增 Wiki 原文定位工具：`sec_source_lookup.py`。
3. Agent 路由：数字先 SQL，解释走 Milvus/Wiki。

## 九、后端入库设计

后端设计分为“控制面”和“数据面”：

- 控制面由 `apps/api` 暴露任务、文件、质量报告、入库状态和前端所需查询 API。
- 数据面由 `scripts/us-sec/*`、`services/market-report-rules`、`db/imports/import_sec_filing_to_postgres.py` 和本项目 PostgreSQL `siq.sec_us` 共同完成。
- `services/market-report-finder` 仍负责下载与本地文件发现，不承担 SEC 结构化入库逻辑。
- `apps/pdf-parser` 不参与 SEC HTML/iXBRL 主链路；只有美股 PDF 附件才继续走通用 PDF 解析。

### 后端模块边界

建议新增或扩展以下模块：

| 路径 | 职责 |
| --- | --- |
| `scripts/us-sec/build_sec_evidence_package.py` | 从本地 SEC HTML/iXBRL 和 metadata 生成 Wiki 证据包 |
| `scripts/us-sec/extract_sec_xbrl_facts.py` | 抽取 `facts_raw.json`、`contexts.json`、`units.json`、`labels.json` |
| `scripts/us-sec/normalize_sec_metrics.py` | 调用/复用 US 规则，把 raw facts 映射为 canonical metrics |
| `services/market-report-rules/src/.../markets/us/` | 保存 US concept rules、期间选择、质量门禁和行业 KPI 规则 |
| `db/ddl/010_create_sec_us_schema.sql` | 创建 `sec_us` schema、核心表、索引、视图 |
| `db/imports/import_sec_filing_to_postgres.py` | 幂等导入 Wiki 证据包到 PostgreSQL |
| `scripts/vector-index/milvus-ingestion/ingest_sec_wiki_chunks.py` | 从 Wiki sections 重建 `siq_us_sec_filings` |
| `apps/api/routers/us_sec.py` | 前端 SEC 入库工作台 API |
| `apps/api/services/us_sec_ingestion.py` | 任务编排、脚本调用、日志聚合、状态机 |

后端不把所有逻辑塞进 API 进程。API 只做参数校验、任务编排、状态查询和结果读取；耗时解析、导入和向量化走脚本或 worker，保证可命令行回放。

### PostgreSQL DDL 设计

`db/ddl/010_create_sec_us_schema.sql` 至少包含：

```sql
create schema if not exists sec_us;

create table if not exists sec_us.companies (...);
create table if not exists sec_us.filings (...);
create table if not exists sec_us.parse_runs (...);
create table if not exists sec_us.artifacts (...);
create table if not exists sec_us.filing_sections (...);
create table if not exists sec_us.html_tables (...);
create table if not exists sec_us.xbrl_contexts (...);
create table if not exists sec_us.xbrl_units (...);
create table if not exists sec_us.xbrl_facts_raw (...);
create table if not exists sec_us.evidence_citations (...);
create table if not exists sec_us.financial_facts (...);
create table if not exists sec_us.operating_metric_facts (...);
```

关键约束：

| 表 | 主键/唯一键 | 必要索引 |
| --- | --- | --- |
| `sec_us.companies` | `company_id`，唯一 `cik` | `ticker`、`industry_profile` |
| `sec_us.filings` | `filing_id`，唯一 `accession_number` | `(ticker, fiscal_year, form)`、`period_end`、`filing_date` |
| `sec_us.parse_runs` | `parse_run_id` | `(filing_id, completed_at desc)`、`status` |
| `sec_us.artifacts` | `(parse_run_id, artifact_type)` | `sha256`、`local_path` |
| `sec_us.xbrl_facts_raw` | `fact_id` | `(filing_id, concept)`、`context_ref`、`period_end`、GIN `dimensions` |
| `sec_us.financial_facts` | `metric_id` | `(ticker, canonical_name, period_key)`、`(filing_id, statement_type)`、`raw_fact_id` |
| `sec_us.evidence_citations` | `evidence_id` | `(filing_id, source_type)`、`(section_id, xbrl_tag)` |

建议视图：

| 视图 | 用途 |
| --- | --- |
| `sec_us.v_latest_parse_runs` | 每个 filing 最新成功或 warning parse run |
| `sec_us.financial_balance_sheet_items` | 资产负债表长表查询 |
| `sec_us.financial_income_statement_items` | 利润表长表查询 |
| `sec_us.financial_cash_flow_statement_items` | 现金流量表长表查询 |
| `sec_us.financial_all_metrics_wide` | Agent 和报表页使用的宽表 |
| `analytics.filing_catalog` | 跨市场只读聚合，不写原始事实 |

### 导入脚本契约

`db/imports/import_sec_filing_to_postgres.py` 接收一个证据包目录，不直接猜下载目录：

```bash
python db/imports/import_sec_filing_to_postgres.py \
  --package data/wiki/us_sec/AAPL/2025/10-K_0000320193-25-000079 \
  --database-url "$DATABASE_URL" \
  --schema sec_us \
  --mode upsert
```

输入文件：

- `manifest.json`
- `sections.json`
- `tables/table_index.json`
- `xbrl/facts_raw.json`
- `xbrl/contexts.json`
- `xbrl/units.json`
- `metrics/normalized_metrics.json`
- `metrics/financial_data.json`
- `metrics/financial_checks.json`
- `qa/source_map.json`
- `qa/quality_report.json`

导入步骤：

1. 读取 `manifest.json`，生成 `filing_id = US:<CIK>:<accession>`。
2. 读取产物 hash，生成 `parse_run_id = sha256(filing_id + parser_version + rules_version + artifact_hashes)`。
3. 在事务中 upsert `companies`、`filings`、`parse_runs`。
4. 写入 `artifacts`、`filing_sections`、`html_tables`、`xbrl_contexts`、`xbrl_units`。
5. 批量 upsert `xbrl_facts_raw`。
6. 根据 `normalized_metrics.json` 写入 `financial_facts` 和 `operating_metric_facts`。
7. 根据 `source_map.json` 写入 `evidence_citations`，并回填 `financial_facts.evidence_id`。
8. 写入 `quality_status`，失败时保留 `parse_runs.status = fail` 和 warnings，便于前端展示。

幂等规则：

- 同一 `filing_id + parser_version + rules_version + artifact_hashes` 重跑不得产生重复行。
- 同一 filing 使用新规则版本重跑时，保留历史 `parse_run_id`，`v_latest_parse_runs` 指向最新通过的版本。
- `financial_facts` 按 `parse_run_id` 隔离，不覆盖旧版本事实；宽表视图只读最新版本。
- 导入事务失败时不得留下半写入的 facts；大批量事实可用 staging table，但最终切换必须在事务内完成。

### 入库任务状态机

控制面建议在 `apps/api` 使用轻量任务表或现有 workflow job 存储，记录 SEC 入库任务：

```text
queued
  -> evidence_packaging
  -> xbrl_extracting
  -> metric_normalizing
  -> quality_checking
  -> postgres_importing
  -> vector_ingesting
  -> completed
```

失败和跳过状态：

| 状态 | 含义 |
| --- | --- |
| `warning` | 已入库，但存在非阻断质量问题 |
| `failed` | 阻断失败，不能作为 facts 来源 |
| `cancelled` | 用户取消，保留已有日志 |
| `skipped` | 文件不是 SEC HTML/iXBRL 或已经是相同 hash 的最新版本 |

每个任务保存：

- `job_id`
- `market = US`
- `source_relative_path`
- `filing_id`
- `accession_number`
- `current_step`
- `status`
- `progress`
- `logs`
- `started_at`
- `completed_at`
- `package_path`
- `parse_run_id`
- `quality_summary`
- `error`

### API 设计

建议挂在 `apps/api`：

```text
/api/us-sec/*
```

核心接口：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/us-sec/downloads?query=&limit=` | 列出 `downloads/US` 中可入库的 SEC HTML/iXBRL 文件 |
| `POST` | `/api/us-sec/ingestion-jobs` | 创建入库任务 |
| `GET` | `/api/us-sec/ingestion-jobs` | 查询任务列表 |
| `GET` | `/api/us-sec/ingestion-jobs/{job_id}` | 查询任务详情、日志、进度 |
| `POST` | `/api/us-sec/ingestion-jobs/{job_id}/cancel` | 取消任务 |
| `POST` | `/api/us-sec/filings/{filing_id}/rebuild` | 从原始文件或 Wiki 证据包重建 |
| `GET` | `/api/us-sec/filings` | 查询已入库 filing catalog |
| `GET` | `/api/us-sec/filings/{filing_id}` | filing 元数据、质量、产物入口 |
| `GET` | `/api/us-sec/filings/{filing_id}/sections` | 章节列表和 Markdown 摘要 |
| `GET` | `/api/us-sec/filings/{filing_id}/metrics` | 标准化指标列表 |
| `GET` | `/api/us-sec/filings/{filing_id}/quality` | 质量报告 |
| `GET` | `/api/us-sec/evidence/{evidence_id}` | 打开证据定位 |
| `GET` | `/api/us-sec/artifacts/{parse_run_id}/{artifact_type}` | 下载或预览证据包产物 |

`POST /api/us-sec/ingestion-jobs` 请求：

```json
{
  "source_relative_path": "US/Apple-Inc/2025/年报/Apple-Inc_US_AAPL_2025-09-27_10-K_2025-10-31_sec_9a1590d0.htm",
  "steps": ["evidence", "normalize", "postgres", "vector"],
  "force_rebuild": false,
  "parser_version": "sec_parser_v1",
  "rules_version": "us_sec_xbrl_v1"
}
```

响应：

```json
{
  "job_id": "us-sec-job-...",
  "status": "queued",
  "filing_id": null,
  "source_relative_path": "US/Apple-Inc/2025/年报/...",
  "created_at": "2026-06-26T..."
}
```

`GET /api/us-sec/filings/{filing_id}/metrics` 响应按前端表格友好格式返回：

```json
{
  "filing_id": "US:0000320193:0000320193-25-000079",
  "parse_run_id": "...",
  "metrics": [
    {
      "canonical_name": "operating_revenue",
      "statement_type": "income_statement",
      "label": "Net sales",
      "value": 391035000000,
      "unit": "USD",
      "period_key": "2025",
      "qtd_ytd_type": "fy",
      "concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
      "confidence": 1.0,
      "evidence_id": "..."
    }
  ]
}
```

### 后端安全与路径约束

- API 只接受本项目 `data/market-report-finder/downloads/US` 下的相对路径，不接受任意绝对路径。
- artifact 读取必须通过 `parse_run_id + artifact_type` 或白名单相对路径解析。
- `source_url` 只作为外部跳转，不由后端代理抓取任意 URL。
- HTML 预览接口默认返回文本或经过 sanitization 的 HTML；原始 HTML 下载走附件响应。
- PostgreSQL 写入只允许连接本项目 `siq` 数据库，并且只写 `sec_us` schema；不得写入外部非本项目数据库或 CN/HK schema。
- Milvus 写入只允许连接本项目 `infra/vector-index/milvus` 管理的实例，目标 collection 必须显式为 `siq_us_sec_filings`，不得落入 `ic_collaboration_shared` 等通用默认 collection。

### 配置项

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | 宿主机 `postgresql+psycopg://...@127.0.0.1:15432/siq` / 容器内 `postgresql+psycopg://...@postgres:5432/siq` | 本项目 PostgreSQL 连接 |
| `SIQ_US_SEC_SCHEMA` | `sec_us` | 美股 PostgreSQL schema |
| `SIQ_WIKI_ROOT` | `$SIQ_PROJECT_ROOT/data/wiki` / 容器内 `/data/wiki` | 本项目 Wiki 根目录 |
| `SIQ_US_SEC_WIKI_ROOT` | `$SIQ_WIKI_ROOT/us_sec` | SEC 证据包根目录 |
| `SIQ_REPORT_DOWNLOADS_ROOT` | `$SIQ_PROJECT_ROOT/data/market-report-finder/downloads` / 容器内 `/data/downloads` | 本项目下载根目录 |
| `SIQ_US_SEC_DOWNLOAD_ROOT` | `$SIQ_REPORT_DOWNLOADS_ROOT/US` | 美股下载目录 |
| `SIQ_US_SEC_PARSER_VERSION` | `sec_parser_v1` | parser 版本 |
| `SIQ_US_SEC_RULES_VERSION` | `us_sec_xbrl_v1` | 规则版本 |
| `SIQ_MILVUS_HOST` | `127.0.0.1` / 容器网络 `standalone` | 本项目 Milvus host |
| `SIQ_MILVUS_PORT` | `19530` | 本项目 Milvus port |
| `SIQ_MILVUS_DB_NAME` | `default` | 本项目 Milvus database |
| `SIQ_US_SEC_MILVUS_COLLECTION` | `siq_us_sec_filings` | 美股向量 collection |
| `SIQ_US_SEC_ENABLE_VECTOR_INGEST` | `true` | 是否在工作流中执行向量入库 |

### 运行时边界校验

所有 SEC 入库脚本和 API 服务启动时必须做边界校验，配置不满足时直接失败：

| 校验项 | 规则 |
| --- | --- |
| 项目根目录 | `SIQ_PROJECT_ROOT` 默认 `/home/maoyd/siq-research-engine`，所有宿主机路径必须位于该目录下 |
| Wiki 根目录 | `SIQ_US_SEC_WIKI_ROOT` 必须解析到 `$SIQ_PROJECT_ROOT/data/wiki/us_sec` 或容器内 `/data/wiki/us_sec` |
| 下载根目录 | `SIQ_US_SEC_DOWNLOAD_ROOT` 必须解析到 `$SIQ_PROJECT_ROOT/data/market-report-finder/downloads/US` 或容器内 `/data/downloads/US` |
| PostgreSQL 数据库 | `DATABASE_URL` 的 database name 必须是 `siq` |
| PostgreSQL schema | 美股写入 schema 必须是 `sec_us` |
| Milvus host | 本地为 `127.0.0.1:19530`，容器网络为本项目 Milvus standalone 服务 |
| Milvus collection | 美股写入 collection 必须是 `siq_us_sec_filings` |

禁止行为：

- 不得使用 `/home/maoyd/wiki` 作为默认 Wiki 根目录。
- 不得连接或创建外部市场数据库；统一使用本项目 `siq` 数据库和市场专属 schema。
- 不得把 SEC chunks 写入 `ic_collaboration_shared`、`ic_finance_auditor` 等通用 collection。
- 不得通过 API 接受用户传入绝对路径绕过项目根目录。

### 后端验收标准

第一版后端完成后，至少满足：

1. 对现有 AAPL `.htm` 可生成 Wiki 证据包。
2. `manifest.json`、sections、raw facts、normalized metrics、quality report 文件齐全。
3. `import_sec_filing_to_postgres.py` 连续运行两次，表行数不重复膨胀。
4. `sec_us.financial_facts` 能查询收入、净利润、总资产、经营现金流。
5. `evidence_id` 能定位到 SEC section 或 XBRL fact 的 HTML anchor/context。
6. API 能从创建任务到查询 completed/warning 状态完整闭环。
7. 失败任务能在前端看到阻断原因和 warnings。

## 十、前端设计

前端目标是把美股页面从“通用 PDF 解析占位”升级为“SEC 披露入库工作台”。页面仍归属于解析/入库工作区，不做营销式入口。

### 页面入口

现有入口：

```text
apps/web/src/pages/UsParsing.tsx
apps/web/src/pages/MarketParsingPage.tsx
apps/web/src/components/pdf/MarketParsingTabs.tsx
```

建议将 `UsParsing.tsx` 改为 SEC 专用页面，保留市场 Tab 外壳：

```text
apps/web/src/pages/UsParsing.tsx
apps/web/src/components/sec/SecIngestionWorkbench.tsx
apps/web/src/components/sec/SecDownloadList.tsx
apps/web/src/components/sec/SecJobTimeline.tsx
apps/web/src/components/sec/SecFilingCatalog.tsx
apps/web/src/components/sec/SecQualityPanel.tsx
apps/web/src/components/sec/SecMetricsTable.tsx
apps/web/src/components/sec/SecEvidenceViewer.tsx
apps/web/src/lib/usSecApi.ts
apps/web/src/lib/usSecTypes.ts
```

`MarketParsingTabs` 中美股描述从 `SEC 披露` 保持不变；点击 `/parse-us` 进入 SEC 工作台，而不是 PDF 上传优先的页面。

### 首屏布局

首屏采用工作台布局：

```text
顶部：MarketParsingTabs + 美股 SEC 入库标题/状态摘要
左侧：本项目 `data/market-report-finder/downloads/US` 文件列表和搜索过滤
中间：所选 filing 的任务时间线、质量门禁、产物状态
右侧：已入库 filing catalog 和快捷筛选
下方：指标表、章节/证据预览、日志
```

首屏应优先展示真实可操作对象：

- 下载目录中可入库的 `.htm/.html/.xml` 文件。
- 当前选择文件的 form、ticker、fiscal year、filing date。
- 入库按钮：`生成证据包`、`入库 PostgreSQL`、`入库向量库`、`一键执行剩余步骤`。
- 最新任务状态和阻断错误。

### 前端状态模型

新增 `apps/web/src/lib/usSecTypes.ts`：

```ts
export type SecIngestionStep =
  | 'evidence_packaging'
  | 'xbrl_extracting'
  | 'metric_normalizing'
  | 'quality_checking'
  | 'postgres_importing'
  | 'vector_ingesting'

export type SecJobStatus = 'queued' | 'running' | 'warning' | 'completed' | 'failed' | 'cancelled' | 'skipped'

export interface SecDownloadedFiling {
  relativePath: string
  filename: string
  ticker?: string
  cik?: string
  companyName?: string
  form?: string
  fiscalYear?: number
  periodEnd?: string
  filingDate?: string
  size: number
  mtime: string
  alreadyIngested?: boolean
  latestParseRunId?: string
}

export interface SecIngestionJob {
  jobId: string
  status: SecJobStatus
  currentStep?: SecIngestionStep
  progress: number
  sourceRelativePath: string
  filingId?: string
  accessionNumber?: string
  packagePath?: string
  parseRunId?: string
  qualityStatus?: 'pass' | 'warning' | 'fail'
  qualitySummary?: Record<string, unknown>
  logs: string[]
  error?: string
}
```

### API Client

新增 `apps/web/src/lib/usSecApi.ts`：

| 函数 | 调用 |
| --- | --- |
| `loadSecDownloads(query)` | `GET /api/us-sec/downloads` |
| `createSecIngestionJob(body)` | `POST /api/us-sec/ingestion-jobs` |
| `loadSecIngestionJobs()` | `GET /api/us-sec/ingestion-jobs` |
| `loadSecIngestionJob(jobId)` | `GET /api/us-sec/ingestion-jobs/{job_id}` |
| `cancelSecIngestionJob(jobId)` | `POST /api/us-sec/ingestion-jobs/{job_id}/cancel` |
| `loadSecFilings(filters)` | `GET /api/us-sec/filings` |
| `loadSecFiling(filingId)` | `GET /api/us-sec/filings/{filing_id}` |
| `loadSecMetrics(filingId)` | `GET /api/us-sec/filings/{filing_id}/metrics` |
| `loadSecQuality(filingId)` | `GET /api/us-sec/filings/{filing_id}/quality` |
| `loadSecEvidence(evidenceId)` | `GET /api/us-sec/evidence/{evidence_id}` |

前端轮询策略：

- running job 每 1.5-2 秒轮询。
- completed/warning/failed 后停止轮询，并刷新 filing catalog。
- 页面重新进入时先恢复最近 20 个 job。

### 主要组件设计

#### `SecDownloadList`

功能：

- 搜索 ticker、公司名、form、文件名。
- 用 badge 标记 `10-K`、`10-Q`、`20-F`、`6-K`。
- 显示 `alreadyIngested` 和最新 `qualityStatus`。
- 支持单文件选择，不做多文件并行入库第一版。

操作：

- `生成证据包`
- `一键入库`
- `强制重建`
- `打开原始文件`

#### `SecJobTimeline`

展示后端状态机：

```text
证据包 -> XBRL -> 指标标准化 -> 质量门禁 -> PostgreSQL -> Milvus
```

每一步显示：

- pending/running/pass/warning/fail。
- 耗时。
- 关键产物数量，例如 facts 数、metrics 数、sections 数、chunks 数。
- 错误和 warning 摘要。

#### `SecQualityPanel`

以紧凑列表展示 `qa/quality_report.json`：

| 检查项 | 状态 | 摘要 |
| --- | --- | --- |
| 原文件 hash | pass | hash matched |
| form/accession/period | pass | 10-K / FY |
| XBRL facts anchor | pass | 12,345 facts |
| 10-Q QTD/YTD | warning/fail | 仅 10-Q 显示 |
| canonical coverage | warning | 缺失指标数量 |

阻断失败放在面板顶部，warning 放在次级列表。不要只显示 JSON 原文。

#### `SecMetricsTable`

面向分析员的可筛选指标表：

- statement type tabs：资产负债表、利润表、现金流、关键指标、经营指标。
- 筛选 canonical name / label / concept。
- 列：指标、值、单位、期间、QTD/YTD、concept、confidence、证据。
- 点击证据打开 `SecEvidenceViewer`。
- 默认只展示 consolidated facts；提供维度开关查看 segment/dimension facts。

#### `SecEvidenceViewer`

证据查看器优先支持三类来源：

| source_type | 展示 |
| --- | --- |
| `sec_xbrl_fact` | fact 值、concept、context、unit、dimensions、HTML anchor |
| `sec_html_section` | section Markdown 摘要，支持打开完整 section |
| `sec_html_table` | table JSON 或轻量表格预览 |

原始 HTML 预览需要做 sanitization。第一版可以提供：

- `在 Wiki 中打开`
- `打开 SEC 原文链接`
- `复制 citation`
- `下载 artifact`

### 与现有 PDF 页面关系

美股页面不再默认上传 PDF。保留兼容入口：

- 如果本项目 `data/market-report-finder/downloads/US` 中选择的是 `.pdf`，提示使用通用 PDF 解析能力。
- SEC `.htm/.html` 走 `SecIngestionWorkbench`。
- `MarketParsingPage` 继续服务 A 股和港股 PDF 解析，不被 SEC 状态污染。

### 前端验收标准

第一版前端完成后，至少满足：

1. `/parse-us` 能列出本项目 `data/market-report-finder/downloads/US` 中的 SEC HTML 文件。
2. 用户选择 AAPL `.htm` 后能创建入库任务并看到步骤进度。
3. 任务完成后能刷新 filing catalog。
4. filing 详情能展示质量门禁、三大表指标、证据入口。
5. 点击收入/净利润/总资产/经营现金流等指标的证据能打开 evidence viewer。
6. 失败任务显示明确错误，不停留在无限 loading。
7. 现有 `/parse` 和 `/parse-hk` 行为不回归。

## 十一、与 A 股链路的对应关系

| A 股 PDF 链路 | 美股 SEC 链路 |
| --- | --- |
| `document_full.json` | `manifest.json + sections.json + xbrl/facts_raw.json` |
| PDF 页码/table_index | SEC section/html_anchor/xpath/context_ref |
| `financial_data.json` | 同名 contract，来源 XBRL 规则 |
| `financial_checks.json` | 同名 contract，增加 QTD/YTD/XBRL 校验 |
| `pdf2md.financial_*` | `siq.sec_us.financial_*` |
| Wiki company reports | `data/wiki/us_sec/<ticker>/<year>/<filing>/` |
| Milvus report chunks | SEC sections chunks + metric evidence chunks |

这样做可以保证美股问答像 A 股一样“有脚本、有文件、有表、有向量、有证据”，但底层证据定位使用 SEC 原生结构，而不是硬套 PDF 页码。

## 十二、当前落地实现

当前实现以已跑通的 50 家美股 10-K 案例集作为金样本：

- 案例集清单：`data/wiki/us_sec/case_set_50_us_10k.json`
- 入库 dry-run 报告：`data/wiki/us_sec/case_set_50_us_10k_ingest_report.json`
- 编排脚本：`scripts/us-sec/ingest_sec_case_set.py`
- PostgreSQL schema：`db/ddl/010_create_sec_us_schema.sql`
- 单包导入：`db/imports/import_sec_filing_to_postgres.py`
- Milvus chunk 构建：`scripts/vector-index/milvus-ingestion/ingest_sec_wiki_chunks.py`
- 前端入口：`/parse-us`，组件 `UsSecIngestionPanel`
- API 入口：`GET /api/us-sec/case-set`、`POST /api/us-sec/case-set/ingest`

### 关系感知召回设计

为了避免机械切片，Milvus 不只写 SEC section 文本，也写标准指标证据 chunk：

| chunk 类型 | 主要用途 | 关键 metadata |
| --- | --- | --- |
| `sec_filing_section` | 管理层解释、风险因素、业务描述、附注原文召回 | `filing_id`、`section_id`、`section_role`、`related_table_ids`、`financial_statement_table_count`、`wiki_path` |
| `sec_metric_evidence` | 收入、净利润、资产、现金流等标准指标精确召回 | `canonical_name`、`concept`、`period_key`、`value`、`unit`、`evidence_id`、`raw_fact_id` |

主体与附注逻辑通过以下字段保留：

- `subject_scope`: `consolidated` 或 `dimension_specific`。
- `dimensions`: XBRL axis/member 原始映射。
- `dimension_axes` / `dimension_members`: 便于检索过滤。
- `relationship_kind`: `registrant_consolidated_metric` 或 `dimension_member_metric`。
- `section_role`: `financial_statement_notes`、`financial_statements`、`mda`、`risk_factors` 等。

这样智能体检索时可以先用 metadata 区分合并主体事实、分部事实、子公司/被投资方/股本类别等维度事实，再回到 PostgreSQL `financial_facts`、`xbrl_facts_raw`、`evidence_citations` 和 Wiki 原文。

### 财务勾稽校验

美股 SEC 解析后必须像 A 股解析一样展示财务勾稽校验，但规则口径不能机械套用所有 XBRL fact：

- 合并报表口径 facts：进入三大表硬勾稽。
- 带 XBRL dimensions 的 facts：默认视为分部、子公司、被投资方、股本类别或附注维度事实，不混入合并报表硬勾稽。
- dimension-specific facts 生成 warning，用于提醒智能体可召回但不能直接替代 consolidated facts。

当前核心勾稽包括：

| 类别 | 规则 |
| --- | --- |
| 资产负债表 | `Assets = liabilities + equity` |
| 资产负债表 | `Assets = liabilities and equity total` |
| 资产负债表 | `Assets = current assets + non-current assets` |
| 资产负债表 | `Liabilities = current liabilities + non-current liabilities` |
| 资产负债表 | `Equity = parent equity + non-controlling interests` |
| 利润表 | `Gross profit = revenue - cost of sales` |
| 利润表 | `Net profit = profit before tax - income tax` |
| 利润表 | `Net profit = parent net profit + non-controlling interests profit` |
| 现金流量表 | `Net cash change = operating + investing + financing + FX` |
| 现金流量表 | `Ending cash = beginning cash + net cash change` |
| 跨表 | `Balance sheet cash ~= cash flow ending cash` |

前端 `/parse-us` 的“财务勾稽校验”面板展示每条规则的状态、期间、差异、容差和失败原因；阻断性 fail 应优先进入规则修复或人工复核。

### 入库命令

只做计划和质量统计：

```bash
python scripts/us-sec/ingest_sec_case_set.py --dry-run --include-fail
```

写入 PostgreSQL，包括 `sec_us.retrieval_chunks` 审计表：

```bash
python scripts/us-sec/ingest_sec_case_set.py --postgres --ddl --include-fail
```

写入 PostgreSQL 并向量化入 Milvus：

```bash
python scripts/us-sec/ingest_sec_case_set.py --postgres --milvus --ddl --include-fail
```

截至当前 50 家 dry-run 统计：

| 项 | 数量 |
| --- | ---: |
| 公司包 | 50 |
| XBRL facts | 122,425 |
| normalized metrics | 4,679 |
| sections | 391 |
| tables | 6,968 |
| evidence/source map | 129,734 |
| retrieval chunks | 38,450 |
| section chunks | 33,771 |
| metric evidence chunks | 4,679 |

维度事实分离后，质量状态为 48 个 pass、2 个 fail。fail 包仍可保留为低 `quality_rank` 召回候选，但智能体输出确定数字时应优先使用 pass 包或明确提示校验风险。
