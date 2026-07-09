# 多市场 `document_full.json` PostgreSQL 入库设计方案

## 目标

在不改动 A 股现有 PostgreSQL 入库链路的前提下，为 HK / JP / KR / EU / US 设计一套可落盘开发的 PostgreSQL 入库方案。

核心目标：

| 目标 | 说明 |
| --- | --- |
| 主输入统一 | 所有市场 PostgreSQL 抽取以 `document_full.json` 为主输入，深度参考 A 股 `document_full.json -> PostgreSQL` 链路。 |
| A 股不动 | 保留现有 `db/imports/import_document_full_to_postgres.py`、`pdf2md` schema、DDL/DML 与 API 调用行为。 |
| 市场规则独立 | 每个市场有自己的入库脚本和抽取规则，处理本市场公司身份、会计准则、语言、科目、币种、单位、期间、XBRL/tag 与证据坐标。 |
| 欧洲多国家多币种 | EU 市场必须按国家/交易所/币种建模，不能默认所有欧股都是 EUR，不能把不同原币事实混入同一数值口径。 |
| 指标分层归一 | 每个市场/国家都做指标和单位归一化，但归一化是派生增强层；先建设跨市场通用核心指标，再兼容市场/国家/行业/公司差异化指标。 |
| 颗粒度参考 A 股 | 主键、表结构、事实颗粒度、页码/表格/证据字段参考 A 股已入库表，不把指标只做成粗粒度 JSON dump。 |
| 支撑智能体准确查询 | 结构化指标精准入库，事实可按公司、报告、期间、科目、表格、页码查询，并能回溯原文证据。 |
| 前端按钮联动 | 各市场前端“导入 PostgreSQL”按钮切到新的 document_full 入库 API，同时保留旧 package importer 作为兼容入口。 |
| 回测闭环 | 每个市场完成入库后必须跑 PostgreSQL 入库回测，验证指标准确性、证据可回溯性和 Agent 查询可用性。 |

## 非目标

| 非目标 | 说明 |
| --- | --- |
| 不重写 A 股 importer | A 股现有 importer 是基准实现，不能为了统一而重构或改变其行为。 |
| 不把 Wiki package 作为事实主输入 | 各市场可以参考 Wiki 抽取方法和已有 package 结构，但 PostgreSQL 事实抽取主输入必须是 `document_full.json`。 |
| 不一次性强制统一所有市场 schema 名称 | 各市场仍写入自己的 database/schema；但表结构和事实颗粒度尽量向 A 股 `pdf2md` 靠齐。 |
| 不牺牲原始证据 | 不用“标准化字段”覆盖原始值、原始科目、原始单位、原始表格坐标。 |

## 当前基准：A 股链路

A 股现有链路：

```text
/home/maoyd/siq-research-engine/data/pdf-parser/results/<task_id>/document_full.json
  -> db/imports/import_document_full_to_postgres.py
  -> db/ddl/001_create_pdf2md_schema.sql
  -> db/dml/001_upsert_document_full.sql
  -> PostgreSQL database: siq
  -> schema: pdf2md
```

A 股 importer 的关键特征：

| 特征 | A 股实现 |
| --- | --- |
| 主输入 | 单个 `document_full.json`。 |
| 读取方式 | `data = load_json_artifact(json_path)` 后，所有结构化数据从 `data` 对象拆出。 |
| 财务事实来源 | `document_full.financial_data.statements`、`document_full.financial_data.key_metrics`。 |
| 表格/页码来源 | `document_full.content_list_enhanced.tables`、`document_full.markdown.pages`、`document_full.quality_report.table_index`。 |
| 质量来源 | `document_full.quality_report`、`document_full.financial_checks`。 |
| 证据来源 | `financial_data` item 的 source、`content_list_enhanced`、`table_index`、`document_full` 内 path/artifact 信息。 |
| 公司身份补齐 | 可旁路读取 `data/wiki/companies/<stock>-<company>/company.json`，用于 identity enrichment，不作为财务事实主输入。 |
| 幂等 | 先按 `task_id` 删除子表数据，再重写 facts/tables/chunks/citations。 |
| 归一化派生层 | `financial_items_enriched` 只追加 canonical_label、metric_family、unit_standardized、value_standardized、period、quality_flags 等弱归一字段，不覆盖原始三大表和 key_metrics。 |

本方案要求其他市场深度参考这个方法论，但不复用/改动 A 股脚本本身。

## 源解析产物

| 市场 | `document_full.json` 主路径 |
| --- | --- |
| CN | `/home/maoyd/siq-research-engine/data/pdf-parser/results/<task_id>/document_full.json` |
| HK | `/home/maoyd/siq-research-engine/data/pdf-parser/results/<task_id>/document_full.json` |
| JP | `/home/maoyd/siq-research-engine/data/pdf-parser/results/<task_id>/document_full.json` |
| KR | `/home/maoyd/siq-research-engine/data/pdf-parser/results/<task_id>/document_full.json` |
| EU | `/home/maoyd/siq-research-engine/data/pdf-parser/results/<task_id>/document_full.json` |
| US SEC | `/home/maoyd/siq-research-engine/data/parser-results/us-sec/<filing_id>/document_full.json` |

说明：

- `data/pdf-parser/results` 是 PDF parser 的 full json 源目录。
- `data/parser-results` 是 SEC / HTML parser 的 full json 源目录。
- `data/wiki/<market>/companies/.../reports/...` 仍可作为前端/Agent evidence package、公司目录、语义资产入口，但不作为 PostgreSQL 事实抽取主输入。

## 目标架构

```text
document_full.json
  -> market-specific importer script
  -> market-specific extraction rules
  -> A-share-like normalized row model
  -> market database/schema
  -> backtest SQL + Agent query validation
```

### 脚本设计

每个市场保留独立入口脚本，便于前端按钮、任务队列、日志、权限、回滚按市场隔离。

| 市场 | 新增入库脚本 | 规则模块 | 目标 database/schema |
| --- | --- | --- | --- |
| HK | `db/imports/import_hk_document_full_to_postgres.py` | `db/imports/market_document_full_rules/hk.py` | `siq_hk.pdf2md_hk` |
| JP | `db/imports/import_jp_document_full_to_postgres.py` | `db/imports/market_document_full_rules/jp.py` | `siq_jp.edinet_jp` |
| KR | `db/imports/import_kr_document_full_to_postgres.py` | `db/imports/market_document_full_rules/kr.py` | `siq_kr.dart_kr` |
| EU | `db/imports/import_eu_document_full_to_postgres.py` | `db/imports/market_document_full_rules/eu.py` | `siq_eu.eu_ifrs` |
| US SEC | `db/imports/import_us_sec_document_full_to_postgres.py` | `db/imports/market_document_full_rules/us_sec.py` | `siq_us.sec_us` |

共享基础设施：

| 路径 | 说明 |
| --- | --- |
| `db/imports/market_document_full_rules/base.py` | 定义规则接口、标准中间对象、字段校验、通用工具。 |
| `db/imports/market_document_full_rules/common.py` | 通用解析函数：数值解析、单位解析、期间归一、hash id、table source、bbox、artifact path 等。 |
| `db/imports/market_document_full_writer.py` | 可选共享 writer：封装 company/filing/parse_run/tables/facts/chunks/citations 写库逻辑。 |
| `db/imports/market_document_full_backtest.py` | 多市场 PostgreSQL 回测入口。 |

不新增统一 CLI 也可以落地；但建议共享规则接口和 writer，避免 5 个市场重复实现 A 股同款表结构写入逻辑。

## 参考 A 股的表结构和颗粒度

其他市场的目标 schema 可继续使用现有 `sec_us`、`pdf2md_hk`、`edinet_jp`、`dart_kr`、`eu_ifrs`，但表结构颗粒度应向 A 股 `pdf2md` 靠齐。

### 核心实体层

| A 股参考表 | 颗粒度 | 其他市场设计要求 |
| --- | --- | --- |
| `pdf2md.documents` | 一个解析任务 / 一个 `document_full.json` | 每个市场必须有等价 document/task 表或在 `parse_runs.raw` 中完整记录 task/document_full path；建议新增/补齐 `documents` 表。 |
| `pdf2md.companies` | 一个上市主体 | 各市场 company 主键必须稳定，不能只靠公司名。HK 用 HKEX code/ticker；JP 用 EDINET/security code；KR 用 corp code/stock code；EU 用 country+ticker/ISIN/LEI；US 用 CIK/ticker。 |
| `pdf2md.company_filings` | 一份披露文件 | 各市场 `filings` 表必须保留 report_type、fiscal_year、period_end、source_url/local_path、document_full_path。 |
| `pdf2md.parse_runs` | 一次解析运行 | 每次导入必须有 parse_run，记录 parser_version、schema_version、rule_version、quality、artifact_hashes。 |

### 文档结构层

| A 股参考表 | 颗粒度 | 其他市场设计要求 |
| --- | --- | --- |
| `pdf2md.document_artifacts` | 每个 artifact 一行 | 保留 `document_full.json`、Markdown、图片目录、原始 PDF/HTML、package path 的引用和 hash。 |
| `pdf2md.document_pages` | 每个 PDF 页一行 | PDF 市场必须写页级索引；US SEC HTML 可写 section/page-like synthetic page。 |
| `pdf2md.content_blocks` | 每个解析块一行 | 来自 `content_list`；用于 Agent 原文检索和证据补全。 |
| `pdf2md.document_tables` | 每个物理表格一行 | 来自 `content_list_enhanced.tables` 或 SEC `tables`；必须保留 table_index、page_number/html_anchor、bbox、preview、raw。 |
| `pdf2md.footnotes` | 每个脚注一行 | HK/JP/KR/EU PDF 市场都应支持；没有脚注时空表。 |
| `pdf2md.toc_entries` | 每个目录/标题候选一行 | 用于章节定位和 Agent 检索。 |

### 财务事实层

| A 股参考表 | 颗粒度 | 其他市场设计要求 |
| --- | --- | --- |
| `pdf2md.financial_statements` | 每张报表一行 | 每个 statement 保留 statement_type、statement_name、scope、title、unit、scale、currency、reporting_currency、table_indexes、columns、raw。 |
| `pdf2md.financial_statement_items` | 报表项目 x 期间一行 | 每个 item/value 一行；保留 raw_value、value、period_key、item_name、canonical_name、source、raw_unit、scale、fact_currency。 |
| `pdf2md.financial_balance_sheet_items` | 资产负债表项目 x 期间一行 | 按 statement_type 拆分，便于 SQL 和 Agent 查询。 |
| `pdf2md.financial_income_statement_items` | 利润表项目 x 期间一行 | 同上。 |
| `pdf2md.financial_cash_flow_statement_items` | 现金流量表项目 x 期间一行 | 同上。 |
| `pdf2md.financial_key_metrics` | 指标 x 期间一行 | EPS、ROE、margin、revenue growth 等指标。 |
| `pdf2md.financial_all_metrics_wide` | task/report x period 一行 | 将三大表和关键指标按 canonical_name 聚合为 JSONB，供 Agent 快速召回。 |
| `pdf2md.financial_checks` | 每条勾稽规则 x 期间一行 | 记录资产=负债+权益、现金流等规则结果。 |
| `pdf2md.financial_items_enriched` | 原始事实的派生层 | 保留原始事实，追加标准名、单位、期间、质量标签，不覆盖原事实。 |
| `pdf2md.financial_normalization_rules` | 每条归一化规则一行 | 记录 canonical、单位、期间、币种、行业指标等派生规则版本，所有派生字段必须能追溯规则。 |
| `operating_metric_facts` 等价层 | 差异化指标 x 期间一行 | 保留行业 KPI、公司自定义口径、运营指标，不强行并入通用核心指标。 |

### 证据和检索层

| A 股参考表 | 颗粒度 | 其他市场设计要求 |
| --- | --- | --- |
| `pdf2md.financial_note_links` | 财务项目与附注/表格关系 | 如果 `document_full.content_list_enhanced.financial_note_links` 有数据必须入库。 |
| `pdf2md.document_chunks` | 页/表/块级检索片段 | 每个市场必须生成可查询 chunk，支持 Agent fallback 检索。 |
| `pdf2md.evidence_citations` | 每条证据引用一行 | 核心财务事实必须尽量绑定 page/table/bbox/quote/local_path/source_url。 |
| `pdf2md.raw_payload_refs` | 大对象引用 | 不把完整 PDF/图片塞入 PostgreSQL；只存路径、hash、摘要。 |

## 指标和单位归一化设计

结论：要做归一化，但不能把归一化做成破坏性覆盖。多市场入库应深度参考 A 股 `financial_items_enriched` 的具体做法：原始事实表保持忠实；归一化结果进入可解释、可回放的派生层；每个派生字段都带规则版本、置信度和质量标签。

### 分层模型

| 层级 | 解决的问题 | 设计要求 |
| --- | --- | --- |
| Raw facts | 不丢失报告原貌。 | 保存原始科目、原始指标名、原始值、原始单位、原始币种、原始期间、原始证据和 raw JSON。 |
| Common core metrics | 让 Agent 和 SQL 稳定查询通用财务问题。 | 所有市场优先归一 revenue、gross_profit、operating_profit、profit_before_tax、net_profit、total_assets、total_liabilities、total_equity、operating_cash_flow、capex、cash_and_equivalents、basic_eps、diluted_eps、roe、gross_margin 等核心指标。 |
| Market/country canonical | 吸收本地准则、语言、taxonomy 差异。 | HK/JP/KR/EU/US 分别维护本市场 canonical mapping；EU 还要按 country/exchange/taxonomy tag 细分。 |
| Industry/company differentiated metrics | 兼容同一市场内不同公司的指标差异。 | 银行、保险、地产、互联网、制造、能源等行业 KPI，以及公司自定义 KPI，进入差异化指标层，不硬塞进通用核心指标。 |
| Wide/query layer | 支撑快速召回和自然语言查询。 | 通用核心指标进入 wide JSONB 的稳定键；差异化指标也可进入 `all_metrics`，但保留 `metric_scope`、`industry`、`company_defined` 等标签。 |

### 建议字段

| 字段 | 说明 |
| --- | --- |
| `item_name_raw` / `metric_name_raw` | 报告原文名称，回答和引用时优先展示。 |
| `canonical_name` / `canonical_label` | 通用或市场级标准名，作为查询键，不替代原始名称。 |
| `canonical_scope` | `common_core`、`market`、`country`、`industry`、`company`。 |
| `metric_family` | asset、liability、equity、revenue、profit、cash_flow、per_share、ratio、operating 等粗粒度族。 |
| `accounting_standard` / `taxonomy_tag` | IFRS、HKFRS、JGAAP、K-GAAP、US GAAP、ESEF/SEC concept 等来源标签。 |
| `raw_unit` / `unit_raw` | 原始单位文本。 |
| `unit_standardized` / `unit_scale` | 派生标准单位和缩放倍数。 |
| `value_extracted` / `value_standardized` | 原始抽取数值和派生标准化数值。 |
| `reporting_currency` / `fact_currency` | 报告币种和事实币种。EU 等多币种市场必须保留。 |
| `quality_flags` | `canonical_unmapped`、`unit_missing`、`unit_unmapped`、`period_unparsed`、`company_defined_metric`、`industry_specific_metric` 等。 |
| `normalization_rule_id` / `rule_version` | 每个派生结果可追溯到具体规则。 |

### 单位和币种规则

| 类别 | 规则 |
| --- | --- |
| 金额类 | 可按原币派生到最小货币单位或报告主单位，但必须保留 raw unit、scale 和 currency。 |
| 每股类 | EPS、DPS 等保持 per-share 单位，不和金额类指标比较。 |
| 比率/百分比 | ROE、margin、growth 等统一为 ratio 或 percent 的一种内部口径，并保留原始 `%` 文本。 |
| 数量/运营类 | MAU、stores、subscribers、production volume 等保留数量单位，不进入金额类宽表键。 |
| 多币种折算 | 只作为二级派生值，记录 `converted_currency`、`converted_value`、`fx_rate_date`、`fx_rate_source`；默认查询返回原币。 |

### 同一市场内公司差异的处理

| 场景 | 做法 |
| --- | --- |
| 指标可映射到核心指标 | 写入原始事实层，同时填 `canonical_scope=common_core` 和稳定 `canonical_name`。 |
| 指标只在某市场常见 | 写入市场 canonical，如 `market_canonical_name`，`canonical_scope=market`。 |
| 指标只在某国家或交易所常见 | 写入 country canonical，如 `country_canonical_name`，`canonical_scope=country`。 |
| 指标是行业 KPI | 写入差异化指标层，标 `canonical_scope=industry`、`industry`、`metric_family=operating`。 |
| 指标是公司自定义 KPI | 原样保存并标 `canonical_scope=company`、`company_defined_metric`；如定义文本可抽取，写 `raw_definition/source`。 |
| 无法可靠映射 | 保留 raw，`canonical_scope` 为空或 `unmapped`，打 `canonical_unmapped`，不得编造通用指标。 |

## 主键和唯一键设计

### 通用 ID 规则

| 对象 | 推荐稳定 ID |
| --- | --- |
| `company_id` | `market + stable_company_code`；例如 `HK:00700`、`JP:E02144`、`KR:00126380`、`EU:DE:SAP:DE0007164600`、`US:CIK0000320193`。 |
| `filing_id` | `market + company_id + report_type + period_end/source_id/accession/doc_id`。 |
| `parse_run_id` | `market + filing_id + document_full_sha256 + parser_version + rule_version`。如果需要重跑覆盖同一版本，可在 importer 中查询已有 parse_run 并复用。 |
| `statement_id` | `parse_run_id + statement_type + scope + table_index/title`。 |
| `financial item row` | 不一定需要全局 id；使用 `(task_id, statement_id, item_index, period_key)` 或市场 schema 等价唯一键。 |
| `table row` | `(parse_run_id or task_id, table_index)`；SEC HTML 可用 `(parse_run_id, table_id/html_anchor)`。 |
| `evidence_id` | `parse_run_id + source_type + page/table/row/column/quote_hash`。 |
| `chunk_uid` | `parse_run_id + chunk_type + page/table/block/statement/canonical_name + period_key`。 |

### 字段保留原则

| 字段类别 | 必须保留 |
| --- | --- |
| 原始值 | `raw_value`、`raw_item`、`raw_metric`、`raw` JSONB。 |
| 标准值 | `value` decimal、`canonical_name`、`normalized_unit`、`period_key`。 |
| 币种/单位 | `raw_unit`、`unit_label`、`scale`、`reporting_currency`、`fact_currency`、`presentation_currency`。如有折算派生值，另存 `converted_currency`、`converted_value`、`fx_rate_date`、`fx_rate_source`，不得覆盖原币事实。 |
| 证据 | `source_page_number`、`source_table_index`、`source_bbox`、`quote_text`、`local_path`、`source_url`。 |
| 身份 | `company_id`、`filing_id`、`parse_run_id`、`task_id`、`ticker/security_code/cik/lei`。 |
| 质量 | `confidence`、`quality_status`、`warning`、`rule_version`、`parser_version`。 |

## 抽取算法方法论

每个市场规则模块按同一阶段实现，但规则细节独立。

### 阶段 1：加载和校验

输入：

```text
document_full.json
```

校验：

| 校验项 | 说明 |
| --- | --- |
| JSON 可解析 | 文件必须可解析。 |
| market 可识别 | PDF 市场优先读 `metadata.market`、`document_full.task.filename`、`financial_data.market`；US SEC 读 `filing.market`。 |
| 必需块存在 | 至少有 `task/filing`、`financial_data` 或 SEC `facts`、`quality_report`、`tables/content_list_enhanced` 中的可用结构。 |
| hash 记录 | 计算 `document_full_sha256`，写入 parse_run/artifact。 |

### 阶段 2：公司和报告身份抽取

| 市场 | 主要字段 | 参考来源 |
| --- | --- | --- |
| HK | HKEX stock code、ticker、company_name、period_end、report_type | `financial_data`、`metadata`、filename、HK wiki company catalog。 |
| JP | EDINET code、security_code、ticker、company_name、period_end、doc_id | `financial_data`、filename、JP wiki/package manifest 仅作参考。 |
| KR | corp_code、stock_code、ticker、company_name、period_end、rcp_no | `financial_data`、filename、KR wiki/package manifest 仅作参考。 |
| EU | country、exchange、ticker、ISIN、LEI、company_name、period_end、reporting_currency | `financial_data`、filename、EU metadata/package manifest 仅作参考。 |
| US SEC | CIK、ticker、company_name、accession_number、form、period_end | `document_full.filing`、`source`、SEC parser manifest 仅作参考。 |

### 阶段 3：文档结构抽取

从 `document_full` 中抽：

| 输出 | 来源 |
| --- | --- |
| artifacts | `document_full.artifacts`、`source_files`、input path。 |
| pages | `markdown.pages`、`content_list_enhanced.pages`。 |
| blocks | `content_list`。 |
| tables | `content_list_enhanced.tables`；US SEC 使用 `document_full.tables`。 |
| footnotes | `content_list_enhanced.footnotes`。 |
| toc | `content_list_enhanced.toc`。 |
| table relations | `table_relations` 或 `content_list_enhanced.financial_note_links`。 |

### 阶段 4：财务事实抽取

PDF 市场优先使用：

```text
document_full.financial_data.statements[]
document_full.financial_data.key_metrics[]
document_full.financial_checks.checks[]
```

US SEC 优先使用：

```text
document_full.facts[]
document_full.tables[]
document_full.sections[]
document_full.relations[]
```

标准化流程：

| 步骤 | 说明 |
| --- | --- |
| statement 分类 | 识别 balance_sheet / income_statement / cash_flow_statement / key_metrics / operating_metrics。 |
| 期间归一 | 将列标题、period_start/period_end、fiscal_year 转为 `period_key`。 |
| 数值解析 | 支持负数括号、千分位、百分比、空值、破折号、单位缩放。 |
| 单位/币种 | 保留 raw unit，解析 currency、scale。不能丢失原始单位。EU 等多币种市场必须区分 reporting_currency、fact_currency 和 optional converted_currency。 |
| 科目标准化 | 输出 `item_name` 与 `canonical_name`；canonical 规则按 common_core、market、country、industry、company 分层维护。 |
| 差异化指标 | 对无法进入通用核心指标的行业 KPI、公司自定义 KPI，写入 differentiated/operating metric 层并保留定义和证据。 |
| 证据绑定 | 从 item source、table_index、page_number、bbox、quote_text 找回证据字段。 |
| 质量标签 | 给事实行添加 confidence/source_type/quality_status。 |

### 阶段 5：宽表和派生层

每个市场都要生成 A 股同款的宽表能力：

| 输出 | 用途 |
| --- | --- |
| `financial_all_metrics_wide` 等价层 | Agent 快速按 period 查询收入、利润、资产、现金流、EPS 等。 |
| `financial_items_enriched` 等价层 | 保留原始事实同时追加弱归一字段，便于跨市场比较。 |
| `financial_normalization_rules` 等价层 | 记录 canonical/unit/period/currency/industry KPI 的规则版本、置信度和说明。 |
| `operating_metric_facts` / `differentiated_metrics` 等价层 | 承载行业/公司差异化指标，不污染 common_core 指标。 |
| `retrieval_chunks/document_chunks` | 支撑 Agent 检索和回答引用。 |

## 各市场规则设计

### HK

| 规则点 | 设计 |
| --- | --- |
| 公司身份 | 使用 HKEX 5 位代码作为强锚点，如 `00700`；ticker 保留 `00700.HK` 或本地格式。 |
| 报表语言 | 支持英文/中文标题：`Consolidated Statement of Financial Position`、`綜合財務狀況表` 等。 |
| 会计准则 | HKFRS/IFRS；写入 `accounting_standard`。 |
| 单位 | 港币/人民币，支持 `HK$ million`、`RMB million`、`港币百万元`。 |
| 科目标准化 | 参考 HK wiki 抽取中的 financial profile，但规则落在 `hk.py`。 |
| 证据 | 优先 `content_list_enhanced.tables[].pdf_page_number/table_index/bbox`。 |

### JP

| 规则点 | 设计 |
| --- | --- |
| 公司身份 | EDINET code 优先，其次 security_code/ticker。 |
| 报告类型 | 识别 `annual securities report` / `有価証券報告書`。 |
| 语言 | 日文科目和英文 IFRS 科目都要支持。 |
| 单位 | 千日元、百万円、百万日元，必须写 scale。 |
| 科目标准化 | JP rule 维护日文科目到 canonical_name 的映射。 |
| 证据 | PDF 页码和 table_index 必须保留；XBRL/tag 信息如果在 full json 中存在则写入 raw/source。 |

### KR

| 规则点 | 设计 |
| --- | --- |
| 公司身份 | DART corp_code 优先，stock_code/ticker 辅助。 |
| 报告类型 | business report / annual report。 |
| 语言 | 韩文科目为主，兼容英文 IFRS。 |
| 单位 | KRW、百万韩元、千韩元。 |
| 科目标准化 | KR rule 维护韩文科目映射。 |
| 证据 | table_index/page_number/source bbox 必须写入。 |

### EU

| 规则点 | 设计 |
| --- | --- |
| 公司身份 | country + ticker + ISIN/LEI 组合，LEI/ISIN 优先级高。 |
| 文件形态 | PDF 与 ESEF XHTML/HTML 都可能存在；从 `document_full` 中抽取统一表格/facts。 |
| 会计准则 | IFRS/ESEF tag 优先保留。 |
| 单位/币种 | EUR/GBP/CHF 等多币种；必须保留 currency。 |
| 科目标准化 | IFRS taxonomy tag 可直接映射 canonical_name；无 tag 时用本地规则。 |
| 证据 | HTML anchor/xpath 与 PDF page/table 都要支持。 |

### US SEC

| 规则点 | 设计 |
| --- | --- |
| 公司身份 | CIK 为强锚点，ticker 辅助。 |
| 文件形态 | SEC HTML/iXBRL，不走 PDF parser。 |
| 事实来源 | `document_full.facts` 是核心；保留 concept、context_ref、unit_ref、dimensions、html_anchor、xpath。 |
| 报表表格 | `document_full.tables` 写入 html_tables 等价层。 |
| 期间 | 从 fact period_start/period_end/instant/context 推导 period_key。 |
| 证据 | html_anchor/xpath/source_url 是必须字段。 |

## 数据库设计策略

### 推荐策略

短期不强制把所有市场写进 `pdf2md` schema，而是在各市场 schema 内补齐 A 股同颗粒度表。

| 市场 | 现有 schema | 建议补齐方向 |
| --- | --- | --- |
| HK | `pdf2md_hk` | 已较接近 A 股，继续补 `documents`、`content_blocks`、`document_tables`、三大表拆分、wide、chunks、citations。 |
| JP | `edinet_jp` | 补齐 `documents`、`document_pages`、`content_blocks`、`financial_statements`、`financial_statement_items`、三大表拆分、wide、chunks。 |
| KR | `dart_kr` | 同 JP。 |
| EU | `eu_ifrs` | 补齐 PDF/HTML 双源表格、document_pages、content_blocks、statement_items、wide、chunks、多币种字段和 country-level canonical。 |
| US SEC | `sec_us` | 保留 XBRL raw facts，同时补 statement_items / wide / document_chunks 等 Agent 友好层。 |

### 必备表族

每个市场最终应具备：

| 表族 | 最小表 |
| --- | --- |
| 文档主表 | `documents`、`artifacts`、`parse_runs` |
| 主体/披露 | `companies`、`filings` |
| 结构索引 | `document_pages` 或 `filing_sections`、`content_blocks`、`document_tables` / `pdf_tables` / `html_tables` |
| 财务事实 | `financial_statements`、`financial_statement_items`、三大表拆分表、`financial_key_metrics` |
| 归一化派生 | `financial_items_enriched`、`financial_normalization_rules`、`metric_canonical_map` / 等价映射表 |
| 差异化指标 | `operating_metric_facts` 或 `differentiated_metrics` |
| 汇总宽表 | `financial_all_metrics_wide` |
| 质量检查 | `financial_checks`、`quality_reports` / `quality_warnings` |
| 证据检索 | `evidence_citations`、`document_chunks` / `retrieval_chunks` |
| 原始引用 | `raw_payload_refs` 或 `artifacts` |

## API 和前端按钮联动

### 后端 API

新增 document_full 入库 API，不替换旧 package API，先并行。

| API | 行为 |
| --- | --- |
| `POST /api/market-reports/document-full/import` | 新增。payload 接收 `market`、`document_full_path`、`task_id`、`ddl`、`force`。调用对应 `import_<market>_document_full_to_postgres.py`。 |
| `POST /api/market-reports/packages/import` | 保留旧入口，迁移期作为 fallback。 |
| `GET /api/market-reports/document-full/status` | 新增或扩展现有 status。按 market/task_id/filing_id 查询是否已入库、facts/tables/chunks 行数。 |

建议 payload：

```json
{
  "market": "HK",
  "task_id": "<pdf-parser-task-id>",
  "document_full_path": "/home/maoyd/siq-research-engine/data/pdf-parser/results/<task_id>/document_full.json",
  "ddl": true,
  "force": false
}
```

### 后端配置

| 配置 | 说明 |
| --- | --- |
| `MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS` | 新增市场到脚本路径映射。 |
| `MARKET_DOCUMENT_FULL_ROOTS` | CN/HK/JP/KR/EU 指向 `data/pdf-parser/results`，US 指向 `data/parser-results/us-sec`。 |
| `MARKET_DATABASES` | 复用现有 market database 配置。 |

### 前端按钮

现有前端已经有“导入 PostgreSQL”按钮和 package import API 调用。迁移后按钮行为如下：

| 页面/组件 | 当前行为 | 目标行为 |
| --- | --- | --- |
| `apps/web/src/pages/HkParsing.tsx` | 通过市场 package 入库 | 调用 document_full import API，传 task_id/document_full_path。 |
| `apps/web/src/pages/JpParsing.tsx` | 同上 | 同上。 |
| `apps/web/src/pages/KrParsing.tsx` | 同上 | 同上。 |
| `apps/web/src/pages/EuParsing.tsx` | 同上 | 同上。 |
| `apps/web/src/components/sec/UsSecIngestionPanel.tsx` | US SEC 走现有 SEC ingest/package import | 切到 US SEC document_full importer，保留 package rebuild 按钮。 |
| `apps/web/src/features/market-parsing/api.ts` | `runMarketPackageImport()` 调 `/packages/import` | 新增 `runMarketDocumentFullImport()` 调 `/document-full/import`。 |

按钮状态更新：

| 状态 | 判定 |
| --- | --- |
| `postgres_ready` | 目标 schema 中 parse_run 存在，且核心 facts/tables/chunks 行数大于 0。 |
| `warning` | 入库成功但 quality warning 或 core metric coverage 不足。 |
| `failed` | importer 返回非 0 或回测失败。 |

## 入库回测设计

### 每个市场必须跑的回测

| 回测项 | 目标 |
| --- | --- |
| 入库完整性 | documents/companies/filings/parse_runs/facts/tables/chunks 行数符合预期。 |
| 核心指标命中 | revenue、net profit、total assets、total liabilities、equity、operating cash flow 等指标可查询。 |
| 差异化指标保留 | 行业/公司 KPI 即使无法映射到 common_core，也必须能按原始名称或 differentiated metric 查询。 |
| 期间准确性 | FY/current/prior period 不错位。 |
| 单位准确性 | scale/currency 保留且数值可还原。 |
| 归一化可追溯 | canonical/unit/period/currency 派生字段能查到 rule_id/rule_version 和 quality_flags。 |
| 证据可回溯 | 核心指标能查到 page/table/bbox 或 SEC html_anchor/xpath。 |
| 幂等性 | 同一个 `document_full.json` 重复导入，事实行数不翻倍。 |
| Agent 查询 | 用固定问题集验证智能体能查到正确指标并给出证据。 |

### 回测脚本

新增：

```text
db/imports/backtests/run_market_document_full_postgres_backtest.py
db/imports/backtests/cases/hk_core_metrics.json
db/imports/backtests/cases/jp_core_metrics.json
db/imports/backtests/cases/kr_core_metrics.json
db/imports/backtests/cases/eu_core_metrics.json
db/imports/backtests/cases/us_sec_core_metrics.json
```

回测 case 格式：

```json
{
  "market": "HK",
  "company_id": "HK:00700",
  "report_year": 2025,
  "period_key": "FY2025",
  "assertions": [
    {
      "canonical_name": "revenue",
      "expected_value": "609015000000",
      "tolerance_ratio": 0.001,
      "required_evidence": true
    }
  ]
}
```

输出：

```text
eval_datasets/market_document_full_postgres/backtest_report.json
eval_datasets/market_document_full_postgres/backtest_report.md
```

### 成功门槛

| 指标 | 门槛 |
| --- | --- |
| 核心指标准确率 | 每个市场首批样本不低于 95%。 |
| 核心指标证据覆盖率 | 不低于 90%。 |
| 差异化指标保留率 | 样本中识别出的行业/公司 KPI 不低于 95% 保留 raw name/value/unit/evidence。 |
| 归一化可解释率 | 填了 canonical 或标准单位的事实 100% 带 rule_id/rule_version 或 source taxonomy。 |
| 入库幂等 | 100% 通过。 |
| Agent 查询命中率 | 固定问题集不低于 90%。 |
| 跨市场误写 | 0。 |

## 开发任务拆分

### Task 1：固化 A 股参考合同

输出：

| 文件 | 动作 |
| --- | --- |
| `db/imports/tests/test_a_share_document_full_contract.py` | 新增测试，只读验证 A 股 importer 的输入字段、核心行模型、主键策略，不改 A 股代码。 |
| `docs/architecture/a-share-postgres-reference-contract.md` | 可选，抽出 A 股表结构和行模型参考。 |

验收：

- 跑通 A 股现有 importer 测试。
- 明确哪些表/字段是其他市场必须参考的颗粒度。

### Task 2：抽象通用规则接口和 writer

输出：

| 文件 | 动作 |
| --- | --- |
| `db/imports/market_document_full_rules/base.py` | 新增规则接口和标准 row dataclass/TypedDict。 |
| `db/imports/market_document_full_rules/common.py` | 新增数值/单位/期间/证据工具。 |
| `db/imports/market_document_full_writer.py` | 新增按 market/schema 写标准行的 writer。 |
| `db/imports/market_document_full_rules/canonical_maps.py` | 新增 common_core canonical、市场/国家 canonical、单位/币种归一化和差异化指标分类规则。 |

验收：

- 无市场逻辑也能跑通空规则 fixture。
- writer 支持 delete-then-insert 幂等。
- 同一个 raw item 可以同时保留原始事实、生成 common_core 派生字段，或进入 differentiated metric 层。

### Task 3：HK 规则和 importer

输出：

| 文件 | 动作 |
| --- | --- |
| `db/imports/import_hk_document_full_to_postgres.py` | 新增。 |
| `db/imports/market_document_full_rules/hk.py` | 新增。 |
| `db/imports/tests/test_import_hk_document_full_to_postgres.py` | 新增。 |

验收：

- 使用至少 3 个 HK `document_full.json` 样本入库。
- 与现有 HK package importer 结果对比核心指标。
- 验证 HKD/RMB、per-share、ratio 指标单位归一化和差异化指标保留。
- 回测通过。

### Task 4：JP/KR/EU 规则和 importer

按市场复制 Task 3 模式。

验收：

- 每个市场至少 3 个样本。
- 核心指标准确率、证据覆盖率达标。
- JP/KR/EU 各自验证本地语言科目、市场/国家 canonical、单位/币种归一化和行业/公司差异化指标保留；EU 至少覆盖两个不同国家/币种。
- 前端按钮可触发对应市场入库。

### Task 5：US SEC 规则和 importer

输出：

| 文件 | 动作 |
| --- | --- |
| `db/imports/import_us_sec_document_full_to_postgres.py` | 新增。 |
| `db/imports/market_document_full_rules/us_sec.py` | 新增。 |
| `db/imports/tests/test_import_us_sec_document_full_to_postgres.py` | 新增。 |

验收：

- 从 `data/parser-results/us-sec/<filing_id>/document_full.json` 入库。
- 保留 XBRL facts、contexts、units、HTML tables、sections、evidence。
- SEC concept 映射到 common_core 时保留 concept/context/unit_ref；未映射 concept 仍可按原 tag 查询。
- US SEC 前端按钮可触发新入库链路。

### Task 6：后端 API 和前端按钮切换

输出：

| 文件 | 动作 |
| --- | --- |
| `apps/api/services/market_report_settings.py` | 新增 `MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS`。 |
| `apps/api/services/market_report_commands.py` | 新增 document_full import plan/args/env。 |
| `apps/api/routers/market_reports.py` | 新增 `/market-reports/document-full/import` 和 status。 |
| `apps/web/src/features/market-parsing/api.ts` | 新增 `runMarketDocumentFullImport()`。 |
| HK/JP/KR/EU/US 页面或组件 | “导入 PostgreSQL”按钮改调新 API。 |

验收：

- 前端点击按钮后，后端日志显示调用 `import_<market>_document_full_to_postgres.py`。
- 返回 job_id / stdout / stderr / parse_run_id。
- UI 状态能进入 `postgres_ready`。

### Task 7：回测和 Agent 查询验证

输出：

| 文件 | 动作 |
| --- | --- |
| `db/imports/backtests/run_market_document_full_postgres_backtest.py` | 新增回测 runner。 |
| `eval_datasets/market_document_full_postgres/*.json` | 新增市场样本和断言。 |
| `docs/reports/market-document-full-postgres-backtest.md` | 输出回测报告。 |

验收：

- 每个市场 PostgreSQL 入库后自动生成回测报告。
- Agent 固定问题集能读到正确结构化指标并带证据。
- 回测报告分别统计 common_core 指标准确率、差异化指标保留率、单位/币种归一化可解释率。

## 风险与约束

| 风险 | 应对 |
| --- | --- |
| 不同市场 `document_full.json` 字段不完全一致 | 规则模块必须容忍缺字段；缺失时写 warning，不静默编造。 |
| package importer 与新 importer 结果不一致 | 初期并行跑，对比核心指标，确认差异来源。 |
| US SEC HTML 与 PDF 结构差异大 | US SEC 单独规则，不强行套 PDF page 模型；使用 html_anchor/xpath 等价证据字段。 |
| 表结构不足 | 允许各市场 schema 增补 A 股同颗粒度表；不要把事实只塞进 raw JSON。 |
| 过度归一化导致指标失真 | 采用 A 股 enriched layer 模式：raw facts 不变，canonical/unit/period/currency 只做派生字段，低置信度映射必须打 quality_flags。 |
| 同一市场公司指标差异过大 | 先保证 common_core，其他行业/公司 KPI 进入 differentiated metric 层并保留原始定义和证据。 |
| 前端误触旧链路 | 按市场灰度切换，UI 文案明确“PostgreSQL 直接读取 document_full.json”。 |
| Agent 查询误用旧 package 事实 | 查询层优先读 PostgreSQL 新事实表；package/Wiki 用于证据展示和兜底。 |

## 最终验收清单

| 验收项 | 通过条件 |
| --- | --- |
| A 股未改动 | A 股现有 importer、DDL/DML、前端按钮行为不变。 |
| 五个非 A 股市场有独立脚本 | HK/JP/KR/EU/US 均有 `import_<market>_document_full_to_postgres.py`。 |
| 五个市场有独立规则 | HK/JP/KR/EU/US 均有 `market_document_full_rules/<market>.py`。 |
| 主输入唯一 | 新 importer 的测试证明核心事实来自 `document_full.json`。 |
| 表结构颗粒度达标 | 各市场具备 company/filing/parse_run/table/statement/item/wide/check/chunk/citation 等表族。 |
| 归一化分层达标 | 各市场具备 raw facts、common_core canonical、market/country canonical、differentiated metrics、normalization rules。 |
| 单位和币种不丢失 | 原始单位/币种、标准单位/scale、原币事实和可选折算派生字段可同时查询。 |
| 前端按钮联动 | 各市场页面“导入 PostgreSQL”触发新 API。 |
| 回测通过 | 每个市场至少 3 个样本，核心指标准确率、证据覆盖率、幂等性达标。 |
| Agent 查询准确 | 固定问题集查询结构化指标准确，并返回可回溯证据。 |
