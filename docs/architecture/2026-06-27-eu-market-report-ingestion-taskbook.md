# 欧股年报解析、证据包、入库与向量化开发任务书

日期：2026-06-27

适用仓库：

```text
/home/maoyd/siq-research-engine
```

## 0. 任务结论

欧股后续解析不应按英国、法国、德国、荷兰、瑞士分别复制五套解析链路，而应按原始文档形态分流：

- PDF 年报：深度复刻 A 股 / 港股 PDF 解析后的证据链深度。
- ESEF ZIP、XHTML、iXBRL、HTML 年报：深度复刻美股 SEC HTML/iXBRL 证据链深度。
- 最终产物：统一落到 `EU evidence package`、统一入 `eu_ifrs` PostgreSQL schema、统一进入 `siq_eu_reports` Milvus collection。

目标链路：

```text
欧股下载产物 PDF / HTML / ESEF ZIP / XHTML / iXBRL
  -> 文档形态识别
  -> PDF parser 或 ESEF/iXBRL parser
  -> EU ParsedArtifact 适配层
  -> EU IFRS rules 抽取 financial_data / financial_checks
  -> data/wiki/eu_reports evidence package
  -> eu_ifrs PostgreSQL 幂等入库
  -> siq_eu_reports Milvus collection
  -> 前端 / Agent 可回溯引用
```

## 1. 当前状态快照

### 1.1 已完成的下载侧能力

欧股下载功能已经接入 `services/market-report-finder`，当前覆盖五个主要欧洲市场：

- 英国：`UK`
- 法国：`FR`
- 德国：`DE`
- 荷兰：`NL`
- 瑞士：`CH`

已下载样本位于：

```text
data/market-report-finder/downloads/EU/
```

本轮端到端下载演练结果：

```text
data/market-report-finder/eu_download_smoke_2026-06-27.json
```

已验证 15 份样本下载成功，五国各 3 份：

| 国家 | 公司 | 年份 | 文件形态 |
| --- | --- | --- | --- |
| UK | AstraZeneca PLC | 2025 | PDF |
| UK | BP p.l.c. | 2025 | PDF |
| UK | Barclays PLC | 2025 | PDF |
| FR | TotalEnergies SE | 2025 | PDF |
| FR | Sanofi | 2025 | PDF |
| FR | Air Liquide S.A. | 2025 | PDF |
| DE | Siemens AG | 2025 | PDF |
| DE | SAP SE | 2025 | PDF |
| DE | Deutsche Telekom AG | 2025 | PDF |
| NL | ASML Holding N.V. | 2025 | PDF |
| NL | Koninklijke Philips N.V. | 2025 | PDF |
| NL | Heineken N.V. | 2025 | PDF |
| CH | Nestle S.A. | 2025 | PDF |
| CH | Novartis AG | 2025 | PDF |
| CH | Roche Holding AG | 2025 | PDF |

归档路径形态：

```text
data/market-report-finder/downloads/EU/<country>/<company>/<fiscal_year>/年报/<file>.pdf
data/market-report-finder/downloads/EU/<country>/<company>/<fiscal_year>/年报/<file>.pdf.metadata.json
```

其中 `<country>` 当前使用：

```text
UK | FR | DE | NL | CH
```

### 1.2 重要判断

欧股年报不能假设都是 PDF。

实际来源可能包括：

- 发行人官网 PDF 年报。
- 发行人官网 HTML 在线年报。
- ESEF ZIP 包，内部通常包含 XHTML/iXBRL、XML、taxonomy 等文件。
- XHTML/iXBRL 单文件。
- 主流年报平台或监管镜像提供的 PDF/HTML。

因此解析层必须支持多形态输入；前端已下载列表和下载器已经开始支持 `pdf/html/xml/zip`，解析层不能只按 PDF 设计。

## 2. 硬性工程边界

### 2.1 不改 A 股 legacy 行为

开发欧股解析时不得修改以下 A 股既有语义：

- 不修改 `apps/pdf-parser` 中 A 股默认解析、抽取、校验行为。
- 不修改 `db/ddl/001_create_pdf2md_schema.sql`。
- 不修改 `db/imports/import_document_full_to_postgres.py`。
- 不改变 A 股下载、解析、入库、查询 API 的默认行为。

如需参考 A 股实现，只能只读分析。新增欧股适配层必须放在 EU 专属脚本、EU 专属规则、EU 专属 schema 中。

### 2.2 不按国家拆 schema

欧股五国统一使用一个 schema：

```text
eu_ifrs
```

国家信息作为字段保存：

```text
country = UK | FR | DE | NL | CH
```

不得创建：

```text
eu_uk
eu_fr
eu_de
eu_nl
eu_ch
```

### 2.3 不用大模型猜财务数字

财务数字只允许来自：

- PDF 表格单元格。
- XBRL/iXBRL facts。
- XHTML/HTML 表格。
- 人工修正记录。

大模型最多用于：

- 章节标题辅助分类。
- 表格候选分类。
- 经营指标候选分类。
- 解释 quality warning。

不得用大模型生成无证据数字并入库。

### 2.4 每个事实必须有 evidence

PDF 事实最小证据：

```text
market + country + filing_id + page_number + table_index + row_index + column_index
```

ESEF/iXBRL 事实最小证据：

```text
market + country + filing_id + xbrl_tag + context_ref + unit_ref + fact_id/html_anchor/source_url
```

HTML table 事实最小证据：

```text
market + country + filing_id + html_anchor/xpath + table_index + row_index + column_index
```

### 2.5 入库必须幂等

同一个 evidence package 重复导入：

- 不得重复插入公司。
- 不得重复插入 filing。
- 不得重复插入 facts。
- 不得重复插入 evidence。

`parse_run_id` 必须稳定生成：

```text
stable_parse_run_id = hash(filing_id, parser_version, rules_version, artifact_hashes)
```

### 2.6 Milvus 只做召回

Milvus 不作为事实源。事实源必须是：

- `data/wiki/eu_reports/...`
- `eu_ifrs` PostgreSQL schema
- 原始下载文件与 metadata

Milvus metadata 至少包含：

```json
{
  "market": "EU",
  "country": "NL",
  "schema": "eu_ifrs",
  "filing_id": "...",
  "parse_run_id": "...",
  "evidence_id": "...",
  "source_type": "pdf_table|xbrl_fact|html_table|section",
  "ticker": "ASML",
  "company_name": "ASML Holding N.V.",
  "fiscal_year": 2025
}
```

## 3. 总体架构

## 3.1 文档形态识别

新增一个欧股解析入口，先识别文件形态：

```text
downloaded_file
  -> content sniff
  -> document_format
```

`document_format` 建议枚举：

```text
pdf
esef_zip
ixbrl_xhtml
html
xml
unknown
```

识别规则：

- `.pdf` 或文件头 `%PDF-`：`pdf`
- `.zip` 或文件头 `PK\x03\x04`，且内部包含 `.xhtml/.html/.xml/.xsd`：`esef_zip`
- `.xhtml/.html` 且包含 `ix:`、`ixt:`、`xbrli:` namespace：`ixbrl_xhtml`
- `.html/.htm` 普通网页：`html`
- `.xml` 或 XBRL instance：`xml`

## 3.2 分流策略

```text
pdf
  -> apps/pdf-parser
  -> EU PDF evidence builder
  -> PDF table facts + page/table evidence

esef_zip / ixbrl_xhtml
  -> EU ESEF/iXBRL parser
  -> XBRL facts + contexts + units + taxonomy
  -> SEC-like evidence builder

html
  -> HTML section/table parser
  -> HTML table facts
  -> 若含 iXBRL，则升级为 ixbrl_xhtml

xml
  -> XBRL instance parser
  -> 仅结构化 facts，不做 PDF 版面证据
```

## 4. EU Evidence Package 合同

### 4.1 输出根目录

```text
data/wiki/eu_reports/<country>/<ticker>/<fiscal_year>/<report_type>_<filing_key>/
```

示例：

```text
data/wiki/eu_reports/NL/ASML/2025/annual_NL-ASML-2025/
data/wiki/eu_reports/CH/NESN/2025/annual_CH-NESN-2025/
```

### 4.2 目录结构

```text
data/wiki/eu_reports/<country>/<ticker>/<fiscal_year>/<report_type>_<filing_key>/
  manifest.json
  README.md
  raw/
    report.pdf
    report.html
    report.xhtml
    esef.zip
    extracted/
    report.metadata.json
  sections/
    report.md
    section_index.json
    section_0001.md
  tables/
    table_index.json
    table_0001.json
    table_0002.json
  xbrl/
    facts_raw.json
    contexts.json
    units.json
    taxonomy.json
    calculation_linkbase.json
    presentation_linkbase.json
  metrics/
    financial_data.json
    financial_checks.json
    load_plan.json
  qa/
    source_map.json
    quality_report.json
    extraction_warnings.json
```

### 4.3 Manifest 必填字段

欧股仍使用统一合同：

```text
schema_version = market_evidence_package_v1
market = EU
```

需要先修改统一 validator，使其允许 `EU`：

```text
services/market-report-rules/src/market_report_rules_service/evidence_package.py
services/market-report-rules/src/market_report_rules_service/models.py
```

`manifest.json` 最小字段：

```json
{
  "schema_version": "market_evidence_package_v1",
  "market": "EU",
  "country": "NL",
  "filing_id": "EU:NL:ASML:2025:annual",
  "company_id": "NL:ASML",
  "ticker": "ASML",
  "company_name": "ASML Holding N.V.",
  "source_id": "issuer_annual_report|xbrl_filings_esef|six_direct|mainstream_repository",
  "source_tier": "official_direct|official_mirror|mainstream_repository",
  "form": "annual|ESEF|AFR|20-F|URD",
  "report_type": "annual",
  "fiscal_year": 2025,
  "fiscal_period": "FY",
  "period_end": "2025-12-31",
  "published_at": "2026-02-25",
  "source_url": "https://...",
  "landing_url": "https://...",
  "local_source_path": "raw/report.pdf",
  "document_format": "pdf",
  "accounting_standard": "IFRS",
  "report_language": "en",
  "parser_version": "eu_pdf_evidence_parser_v1",
  "rules_version": "eu_ifrs_rules_v1",
  "quality_status": "pass|warning|fail",
  "artifact_hashes": {}
}
```

建议追加字段：

```json
{
  "exchange": "LSE|Euronext Paris|Xetra|Euronext Amsterdam|SIX|unknown",
  "isin": "...",
  "lei": "...",
  "currency": "EUR|GBP|CHF|USD",
  "industry_profile": "general|bank|insurance|energy|pharma|semiconductor|consumer|industrial|telecom",
  "downloaded_file_path": "data/market-report-finder/downloads/EU/...",
  "download_metadata_path": "data/market-report-finder/downloads/EU/...metadata.json",
  "pdf_parser_task_id": "...",
  "pdf_parser_result_dir": "data/pdf-parser/results/...",
  "esef_entry_document": "raw/extracted/.../report.xhtml",
  "inline_xbrl": true,
  "xbrl_taxonomy": "ifrs-full",
  "xbrl_namespaces": {}
}
```

### 4.4 evidence_id 规则

PDF 表格证据：

```text
eu:<country>:<filing_id>:p<page_number>:t<table_index>:r<row_index>:c<column_index>
```

示例：

```text
eu:nl:EU-NL-ASML-2025-annual:p184:t12:r8:c3
```

XBRL fact 证据：

```text
eu:<country>:<filing_id>:xbrl:<fact_id_or_hash>
```

示例：

```text
eu:de:EU-DE-SAP-2025-annual:xbrl:ifrs-full_Revenue_context_2025
```

HTML table 证据：

```text
eu:<country>:<filing_id>:html:t<table_index>:r<row_index>:c<column_index>
```

### 4.5 source_map 最小字段

PDF evidence：

```json
{
  "evidence_id": "eu:nl:...:p184:t12:r8:c3",
  "filing_id": "EU:NL:ASML:2025:annual",
  "parse_run_id": "...",
  "source_type": "pdf_table",
  "country": "NL",
  "page_number": 184,
  "table_index": 12,
  "row_index": 8,
  "column_index": 3,
  "quote_text": "Total net sales",
  "local_path": "raw/report.pdf",
  "table_json_path": "tables/table_0012.json",
  "target": "metrics/financial_data.json#/statements/0/items/0"
}
```

XBRL evidence：

```json
{
  "evidence_id": "eu:de:...:xbrl:...",
  "filing_id": "EU:DE:SAP:2025:annual",
  "parse_run_id": "...",
  "source_type": "xbrl_fact",
  "country": "DE",
  "xbrl_tag": "ifrs-full:Revenue",
  "context_ref": "CurrentYearDuration",
  "unit_ref": "EUR",
  "fact_id": "fact-123",
  "html_anchor": "#fact-123",
  "local_path": "raw/extracted/report.xhtml",
  "source_url": "https://...",
  "target": "metrics/financial_data.json#/statements/0/items/0"
}
```

## 5. P0 开发包：先打通 PDF 闭环

P0 目标：基于已下载的 15 份欧股 PDF 年报，生成可验证 EU evidence package，并能入库和在前端/Agent 中回溯。

### P0-1：统一合同支持 EU

修改文件：

```text
services/market-report-rules/src/market_report_rules_service/models.py
services/market-report-rules/src/market_report_rules_service/evidence_package.py
services/market-report-rules/tests/test_evidence_package_contract.py
docs/architecture/market-evidence-package-contract.md
```

任务：

- `Market` enum 增加 `EU`。
- evidence package validator 允许 `manifest.market == "EU"`。
- manifest 推荐字段文档补充 `country`、`document_format`、`source_tier`。
- 增加测试：EU 最小证据包可通过 validator。

验收命令：

```bash
cd /home/maoyd/siq-research-engine/services/market-report-rules
uv run python -m pytest tests/test_evidence_package_contract.py
```

### P0-2：新增 EU PDF evidence builder

新增文件：

```text
scripts/eu/build_eu_pdf_evidence_package.py
scripts/eu/eu_pdf_evidence_lib.py
scripts/eu/discover_eu_downloaded_cases.py
services/market-report-rules/tests/test_eu_pdf_evidence_package.py
```

输入：

```text
data/market-report-finder/downloads/EU/<country>/<company>/<year>/年报/*.pdf
data/market-report-finder/downloads/EU/<country>/<company>/<year>/年报/*.pdf.metadata.json
data/pdf-parser/results/<task_id>/document_full.json
```

输出：

```text
data/wiki/eu_reports/<country>/<ticker>/<fiscal_year>/<report_type>_<filing_key>/
```

实现要求：

- 只消费 `apps/pdf-parser` 的解析结果，不修改 A 股解析逻辑。
- 从 `document_full.json` / `content_list_enhanced.json` 提取表格。
- 复用港股 `ParsedTable` 转换思路，保留页码、表格索引、行列坐标、bbox、quote_text。
- 构造 `ParsedArtifact(market=Market.EU, accounting_standard=IFRS)`。
- 调用 `process_artifact(..., include_load_plan=True)` 生成 `financial_data`、`financial_checks`。
- 输出 `sections/report.md`、`tables/table_index.json`、`qa/source_map.json`、`qa/quality_report.json`。

建议参考：

```text
scripts/hk/build_hk_evidence_package.py
scripts/hk/hk_evidence_lib.py
services/market-report-rules/src/market_report_rules_service/markets/hk/extractor.py
```

不得直接修改：

```text
apps/pdf-parser/*
db/imports/import_document_full_to_postgres.py
db/ddl/001_create_pdf2md_schema.sql
```

### P0-3：新增 EU IFRS PDF 规则

新增文件：

```text
services/market-report-rules/src/market_report_rules_service/markets/eu/__init__.py
services/market-report-rules/src/market_report_rules_service/markets/eu/definition.py
services/market-report-rules/src/market_report_rules_service/markets/eu/extractor.py
services/market-report-rules/src/market_report_rules_service/markets/eu/rules.py
services/market-report-rules/tests/test_eu_rules.py
```

任务：

- 基于 IFRS 常见英文科目 alias 建规则。
- 优先覆盖三大表核心指标：
  - revenue / sales
  - operating profit
  - profit before tax
  - net profit / profit attributable to shareholders
  - total assets
  - total liabilities
  - total equity
  - cash and cash equivalents
  - net cash from operating activities
  - investing cash flow
  - financing cash flow
- 支持 `EUR`、`GBP`、`CHF`、`USD`。
- 支持单位识别：million、bn、thousand、EURm、CHF million 等。
- 银行、保险暂允许 cash flow warning，但必须记录在 quality report。

推荐 profile：

```text
general
bank
insurance
energy
pharma
semiconductor
consumer
industrial
telecom
```

### P0-4：15 份 PDF 样本解析评估

新增 case manifest：

```text
eval_datasets/market_ingestion_cases/eu_15_pdf_cases.json
```

每个 case：

```json
{
  "market": "EU",
  "country": "NL",
  "ticker": "ASML",
  "company_name": "ASML Holding N.V.",
  "report_type": "annual",
  "fiscal_year": 2025,
  "period_end": "2025-12-31",
  "document_format": "pdf",
  "source_pdf": "data/market-report-finder/downloads/EU/NL/ASML-Holding-N.V/2025/年报/...",
  "metadata_json": "data/market-report-finder/downloads/EU/NL/ASML-Holding-N.V/2025/年报/....metadata.json",
  "parser_result_dir": "data/pdf-parser/results/<task_id>",
  "industry_profile": "semiconductor",
  "expected_metrics": [
    "revenue",
    "net_profit",
    "total_assets",
    "total_liabilities",
    "total_equity",
    "operating_cash_flow"
  ],
  "expected_evidence": true
}
```

验收标准：

- 至少 15 个 package 成功生成。
- `validate_evidence_package` 全部通过。
- 每个 package 有：
  - `manifest.json`
  - `raw/report.pdf`
  - `sections/report.md`
  - `tables/table_index.json`
  - `metrics/financial_data.json`
  - `metrics/financial_checks.json`
  - `qa/source_map.json`
  - `qa/quality_report.json`
- 每个核心 financial fact 至少有一个 evidence。
- 三大表识别：
  - general/industrial/tech/pharma/consumer/telecom：三大表尽量齐全。
  - bank/insurance：允许 cash flow 缺失或 warning，但 balance sheet / income statement 必须可抽核心项。

建议命令：

```bash
cd /home/maoyd/siq-research-engine
python3 scripts/eu/discover_eu_downloaded_cases.py \
  --download-root data/market-report-finder/downloads/EU \
  --output eval_datasets/market_ingestion_cases/eu_15_pdf_cases.json

python3 scripts/eu/build_eu_pdf_evidence_package.py \
  data/market-report-finder/downloads/EU/NL/ASML-Holding-N.V/2025/年报/<file>.pdf \
  --metadata data/market-report-finder/downloads/EU/NL/ASML-Holding-N.V/2025/年报/<file>.pdf.metadata.json \
  --parser-result data/pdf-parser/results/<task_id> \
  --force
```

## 6. P1 开发包：ESEF / iXBRL 结构化解析

P1 目标：对 ESEF ZIP、XHTML、iXBRL 走 SEC-like 结构化 facts 链路，不依赖 PDF 表格 OCR。

### P1-1：ESEF ZIP 解包与入口文档定位

新增文件：

```text
scripts/eu/build_eu_esef_evidence_package.py
scripts/eu/eu_esef_evidence_lib.py
services/market-report-rules/tests/test_eu_esef_evidence_package.py
```

任务：

- 解压 `raw/esef.zip` 到 `raw/extracted/`。
- 定位主 XHTML/iXBRL 文档。
- 识别 taxonomy、instance、schema、linkbase。
- 保存文件清单：

```text
xbrl/taxonomy.json
xbrl/entrypoints.json
raw/extracted_manifest.json
```

### P1-2：iXBRL facts 抽取

输出：

```text
xbrl/facts_raw.json
xbrl/contexts.json
xbrl/units.json
```

`facts_raw.json` 建议结构：

```json
{
  "schema_version": "eu_xbrl_facts_raw_v1",
  "facts": [
    {
      "fact_id": "...",
      "concept": "ifrs-full:Revenue",
      "taxonomy": "ifrs-full",
      "label": "Revenue",
      "value_text": "28000000000",
      "value_numeric": 28000000000,
      "unit_ref": "EUR",
      "unit": "EUR",
      "decimals": "-6",
      "scale": "1",
      "context_ref": "CurrentYearDuration",
      "period_start": "2025-01-01",
      "period_end": "2025-12-31",
      "duration_days": 365,
      "instant": null,
      "fiscal_year": 2025,
      "fiscal_period": "FY",
      "dimensions": {},
      "is_extension": false,
      "html_anchor": "#fact-...",
      "xpath": "..."
    }
  ]
}
```

建议库：

- 优先使用已有 Python 标准 XML/HTML parser 能力。
- 如引入专门库，必须固定依赖版本并补测试。
- 不允许只靠正则解析完整 XBRL。

### P1-3：EU XBRL rules

任务：

- 基于 IFRS taxonomy 概念映射到 canonical metrics。
- 支持 extension facts fallback：若 extension label 与 IFRS alias 高相似，但必须打 warning 或降低 confidence。
- period/context 选择规则参考美股 SEC：
  - 年报优先 FY duration。
  - balance sheet 优先 period_end instant。
  - income/cash flow 优先 current year duration。
  - 避免误取 prior year / segment / parent-only context。

建议参考：

```text
services/market-report-rules/src/market_report_rules_service/markets/us/extractor.py
scripts/us-sec/sec_evidence_lib.py
scripts/us-sec/extract_sec_xbrl_facts.py
db/imports/import_sec_filing_to_postgres.py
```

### P1-4：ESEF 样本

至少增加 5 个 ESEF/iXBRL 样本：

| 国家 | 公司建议 | 说明 |
| --- | --- | --- |
| NL | ASML | ESEF 包/官网均常见 |
| DE | Siemens 或 SAP | IFRS concepts 典型 |
| FR | TotalEnergies 或 Sanofi | 法国 URD + IFRS |
| UK | AstraZeneca 或 Barclays | 英国披露 + IFRS/20-F |
| CH | 可用发行人 iXBRL/HTML 时纳入；否则 CH 在 P1 可保持 PDF |

验收标准：

- 至少 5 个 ESEF/iXBRL package 通过 validator。
- `xbrl/facts_raw.json` 非空。
- `xbrl/contexts.json` 非空。
- `metrics/financial_data.json` 中核心指标来自 XBRL evidence。
- `qa/source_map.json` 可跳回 `raw/extracted/...xhtml` 的 anchor 或 xpath。

## 7. P2 开发包：PostgreSQL 入库

### P2-1：新增 EU DDL

新增文件：

```text
db/ddl/050_create_eu_ifrs_schema.sql
```

schema：

```text
eu_ifrs
```

建议表：

```text
eu_ifrs.companies
eu_ifrs.filings
eu_ifrs.parse_runs
eu_ifrs.artifacts
eu_ifrs.filing_sections
eu_ifrs.pdf_pages
eu_ifrs.pdf_tables
eu_ifrs.html_tables
eu_ifrs.xbrl_contexts
eu_ifrs.xbrl_units
eu_ifrs.xbrl_facts_raw
eu_ifrs.evidence_citations
eu_ifrs.financial_facts
eu_ifrs.operating_metric_facts
eu_ifrs.quality_checks
```

`companies` 必备字段：

```text
company_id primary key
country
ticker
isin
lei
company_name
exchange
industry_profile
raw jsonb
```

`filings` 必备字段：

```text
filing_id primary key
company_id
country
ticker
form
report_type
fiscal_year
fiscal_period
period_end
published_at
source_id
source_tier
source_url
landing_url
local_path
document_format
accounting_standard
quality_status
raw jsonb
```

PDF 表：

```text
pdf_pages
pdf_tables
```

HTML/XBRL 表：

```text
html_tables
xbrl_contexts
xbrl_units
xbrl_facts_raw
```

统一 evidence：

```text
evidence_citations
```

### P2-2：新增导入器

新增文件：

```text
db/imports/import_eu_evidence_package_to_postgres.py
db/imports/tests/test_import_eu_evidence_package.py
```

任务：

- 读取 `data/wiki/eu_reports/.../manifest.json`。
- 校验 `manifest.market == "EU"`。
- 调用统一 `validate_evidence_package`。
- 幂等 upsert company / filing / parse_run。
- 删除同一 parse_run 的旧 facts/evidence，再重插。
- 根据文件存在情况导入：
  - PDF package：`pdf_pages`、`pdf_tables`。
  - ESEF/iXBRL package：`xbrl_contexts`、`xbrl_units`、`xbrl_facts_raw`。
  - HTML package：`html_tables`。
- 导入 `financial_facts`、`operating_metric_facts`、`quality_checks`。

验收命令：

```bash
cd /home/maoyd/siq-research-engine
python3 db/imports/import_eu_evidence_package_to_postgres.py \
  --package data/wiki/eu_reports/NL/ASML/2025/annual_NL-ASML-2025 \
  --ddl
```

验收 SQL：

```sql
select country, ticker, fiscal_year, count(*)
from eu_ifrs.financial_facts
group by country, ticker, fiscal_year
order by country, ticker;

select source_type, count(*)
from eu_ifrs.evidence_citations
group by source_type;

select f.ticker, ff.canonical_name, ff.value, e.source_type, e.page_number, e.xbrl_tag
from eu_ifrs.financial_facts ff
join eu_ifrs.filings f on f.filing_id = ff.filing_id
left join eu_ifrs.evidence_citations e on e.evidence_id = ff.evidence_id
limit 50;
```

## 8. P3 开发包：前端与 API

### P3-1：欧股解析入口

前端已有搜索下载页 `欧股` 下载入口。后续需要新增/扩展解析入口：

```text
apps/web/src/pages/EuParsing.tsx
apps/web/src/pages/MarketParsingPage.tsx
apps/web/src/components/pdf/MarketParsingTabs.tsx
apps/web/src/lib/secApi.ts
apps/web/src/lib/pdfApi.ts
```

设计建议：

- 欧股解析页仍按国家筛选。
- 已下载列表可显示 PDF/HTML/ZIP，但操作按钮按文档形态分流：
  - PDF：`解析 PDF`
  - ZIP：`解析 ESEF`
  - HTML/XHTML：`解析 HTML/iXBRL`
- PDF 解析可以复用现有 PDF parser 提交任务。
- ESEF/HTML 解析调用新增 API。

### P3-2：API 路由

新增或扩展：

```text
apps/api/routers/market_reports.py
```

建议 API：

```text
POST /api/market-reports/eu/parse
POST /api/market-reports/eu/packages/build
GET  /api/market-reports/packages?market=EU
GET  /api/market-reports/package?market=EU&package_path=...
POST /api/market-reports/packages/import
POST /api/market-reports/packages/vector-ingest
```

必须支持：

- 从下载文件 relativePath 触发解析。
- 查询解析 job 状态。
- 展示 package quality。
- 打开 evidence 原文：
  - PDF 页。
  - table JSON。
  - XHTML anchor。
  - HTML section。

## 9. P4 开发包：Milvus 向量化

复用统一 market evidence chunk ingestion：

```text
scripts/vector-index/milvus-ingestion/ingest_market_evidence_chunks.py
```

新增或配置 collection：

```text
siq_eu_reports
```

chunk 来源：

- `sections/*.md`
- `tables/table_*.json`
- `metrics/financial_data.json`
- `qa/source_map.json`

metadata 必填：

```json
{
  "market": "EU",
  "country": "FR",
  "schema": "eu_ifrs",
  "collection": "siq_eu_reports",
  "ticker": "TTE",
  "company_name": "TotalEnergies SE",
  "filing_id": "...",
  "parse_run_id": "...",
  "fiscal_year": 2025,
  "document_format": "pdf",
  "evidence_id": "...",
  "source_type": "pdf_table"
}
```

验收：

- Milvus 命中 chunk 后能反查 `eu_ifrs.evidence_citations`。
- 能打开源 PDF 页或 XHTML anchor。
- 重建 collection 不依赖 Milvus 旧数据。

## 10. P5 开发包：质量评估

新增评估 case：

```text
eval_datasets/market_ingestion_cases/eu_15_pdf_cases.json
eval_datasets/market_ingestion_cases/eu_5_esef_cases.json
```

扩展：

```text
scripts/maintenance/run_market_ingestion_eval.py
```

EU 质量门禁：

### 10.1 PDF package

- `quality_status != fail`
- `table_count >= 5`
- `normalized_metric_count >= 10`
- `evidence_coverage_ratio >= 0.95`
- 核心指标至少命中：
  - revenue 或等价项
  - net_profit 或等价项
  - total_assets
  - total_liabilities
  - total_equity
  - operating_cash_flow，银行/保险可 warning

### 10.2 ESEF/iXBRL package

- `xbrl/facts_raw.json` fact 数量非空。
- contexts/units 非空。
- XBRL evidence coverage >= 0.95。
- 核心 IFRS concepts 命中数量达到门槛。
- extension fact 不得无 warning 直接当成高置信核心指标。

### 10.3 DB 入库

- 重复导入两次 facts 数不变。
- 每个 `financial_facts.evidence_id` 能 join 到 `evidence_citations`。
- 每个 `parse_run_id` 能追溯 package 路径。

## 11. 建议开发顺序

### 阶段 A：PDF 闭环

1. 合同支持 EU。
2. rules service 增加 EU market/profile。
3. 新增 EU PDF evidence builder。
4. 对 15 份已下载 PDF 跑 parser + builder。
5. 修 EU IFRS alias 规则。
6. 生成 `eu_15_pdf_cases.json`。
7. validator 全部通过。

### 阶段 B：DB 闭环

1. 新增 `050_create_eu_ifrs_schema.sql`。
2. 新增 `import_eu_evidence_package_to_postgres.py`。
3. 导入 15 个 PDF package。
4. SQL 验证 evidence join。

### 阶段 C：ESEF/iXBRL 闭环

1. 新增 ESEF parser。
2. 跑 5 个 ESEF/iXBRL 样本。
3. 复刻 SEC facts/context/unit 证据链。
4. 与 PDF package 共用 EU schema 入库。

### 阶段 D：前端/向量/Agent

1. 欧股解析页支持 PDF/ZIP/HTML 分流。
2. Package 状态页展示 EU quality report。
3. Milvus `siq_eu_reports` 重建。
4. Agent 引用能跳回 DB/Wiki/原文证据。

## 12. 参考文件清单

下载侧：

```text
services/market-report-finder/src/market_report_finder_service/markets/eu/
services/market-report-finder/src/market_report_finder_service/services/downloader.py
apps/api/routers/downloads.py
apps/web/src/pages/SearchDownload.tsx
```

PDF 解析参考：

```text
apps/pdf-parser/
scripts/hk/build_hk_evidence_package.py
scripts/hk/hk_evidence_lib.py
services/market-report-rules/src/market_report_rules_service/markets/hk/
db/ddl/020_create_pdf2md_hk_schema.sql
db/imports/import_hk_evidence_package_to_postgres.py
```

SEC/iXBRL 参考：

```text
scripts/us-sec/build_sec_evidence_package.py
scripts/us-sec/sec_evidence_lib.py
scripts/us-sec/extract_sec_xbrl_facts.py
services/market-report-rules/src/market_report_rules_service/markets/us/
db/ddl/010_create_sec_us_schema.sql
db/imports/import_sec_filing_to_postgres.py
```

统一合同：

```text
docs/architecture/market-evidence-package-contract.md
services/market-report-rules/src/market_report_rules_service/evidence_package.py
```

向量化：

```text
scripts/vector-index/milvus-ingestion/ingest_market_evidence_chunks.py
scripts/vector-index/milvus-ingestion/SIQ_INGEST_METADATA_SCHEMA.md
```

## 13. Definition of Done

欧股解析链路达到可交付状态，必须同时满足：

1. 下载侧：
   - 搜索下载页可搜索五国欧股。
   - 下载文件落入 `data/market-report-finder/downloads/EU/<country>/...`。
   - 已下载列表能显示 PDF/HTML/ZIP。

2. PDF 解析侧：
   - 15 份已下载 PDF 至少 12 份生成 pass/warning package。
   - 失败样本必须有明确 failure reason。
   - 每个 package 有 source_map 和 quality_report。

3. ESEF/iXBRL 解析侧：
   - 至少 5 份 ESEF/iXBRL 样本生成 package。
   - facts/context/units 可追溯。

4. DB：
   - `eu_ifrs` schema 可创建。
   - PDF package 和 ESEF package 都能导入。
   - 重复导入幂等。
   - facts 能 join evidence。

5. 前端：
   - 欧股解析页能按国家筛选。
   - package 列表能显示 EU package。
   - evidence 能打开原文位置。

6. Milvus / Agent：
   - `siq_eu_reports` collection 可重建。
   - Agent 引用能回查 `market=EU`、`country`、`filing_id`、`evidence_id`。

## 14. 风险和注意事项

1. 欧洲公司年报格式不统一。
   - 不要把 PDF 作为唯一格式。
   - ESEF ZIP 必须单独解析。

2. IFRS 概念和公司 extension 多。
   - extension facts 必须降低置信度或输出 warning。
   - 不要直接把 label 相似就当作核心指标。

3. PDF 年报版式差异大。
   - P0 先覆盖大公司英文年报。
   - 法语/德语本地语言年报放 P1/P2 规则扩展。

4. 银行/保险财报结构不同。
   - Barclays、Allianz、UBS 等金融类不应强制套一般工业三大表规则。
   - P0 可以标记 industry profile 并降低部分现金流门槛。

5. 瑞士不属于 EU ESEF 强约束范围。
   - CH 主要走发行人官网 PDF/HTML。
   - 不要假设 CH 一定有 ESEF ZIP。

6. 不要污染 A 股 legacy。
   - 所有 EU 适配都应新增旁路文件。
   - 只读参考 CN/HK/US，不能改 CN 默认行为。
