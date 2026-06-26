# PDF2MD PostgreSQL 分层与脚本整理

本文整理 `/home/maoyd/DB` 和 `finance_evidence_poc/DB` 里的 PostgreSQL 建表、进数脚本与分层设计，便于直接对照当前实现。

数据库连接配置在 `finance_evidence_poc/DB/DML/postgresql_connect.py`：

- host: `192.168.2.121`
- port: `5432`
- dbname: `ai_platform`
- user: `dgx`
- schema: `pdf2md`

## 脚本入口

- `DB/DDL/001_create_pdf2md_schema.sql`
- `DB/DML/001_upsert_document_full.sql`
- `DB/PROGRAM/import_document_full_to_postgres.py`
- `finance_evidence_poc/DB/DML/postgresql_connect.py`

## 当前分层

| 层级 | 目标 | 已落地表 |
|---|---|---|
| 1 | 公司主数据 | `companies` |
| 2 | 公告/财报来源 | `company_filings` |
| 3 | 解析运行 | `documents`、`parse_runs` |
| 4 | 原始产物引用 | `document_artifacts`、`raw_payload_refs` |
| 5 | 版面结构 | `document_pages`、`content_blocks`、`document_tables`、`quality_warnings`、`footnotes`、`toc_entries`、`financial_note_links` |
| 6 | 财务事实 | `financial_statements`、`financial_statement_items`、`financial_key_metrics`、`financial_checks` |
| 7 | 证据与 RAG | `document_chunks`、`evidence_citations`、`analysis_claims`、`claim_evidence_links` |
| 8 | 报告/反馈/评测 | `generated_reports`、`report_sections`、`review_feedback`、`gold_financial_items`、`evaluation_runs`、`evaluation_results` |

## 实际数据流

`document_full.json`
-> `documents`
-> `document_artifacts` / `raw_payload_refs`
-> `document_pages`
-> `content_blocks`
-> `document_tables`
-> `quality_warnings`
-> `footnotes`
-> `toc_entries`
-> `financial_note_links`
-> `financial_statements`
-> `financial_statement_items`
-> `financial_key_metrics`
-> `financial_checks`
-> `document_chunks`
-> `evidence_citations`
-> `generated_reports` / `report_sections`
-> `analysis_claims` / `claim_evidence_links`
-> `review_feedback`
-> `gold_financial_items`
-> `evaluation_runs` / `evaluation_results`

## 代码已覆盖

- `DB/DDL/001_create_pdf2md_schema.sql`
- `DB/DML/001_upsert_document_full.sql`
- `DB/PROGRAM/import_document_full_to_postgres.py`
- `finance_evidence_poc/DB/DML/postgresql_connect.py`

## 数据库实查结果

已连接 `ai_platform` 并确认 `pdf2md` schema 当前有 27 张表：

`analysis_claims`、`claim_evidence_links`、`companies`、`company_filings`、`content_blocks`、`document_artifacts`、`document_chunks`、`document_pages`、`document_tables`、`documents`、`evaluation_results`、`evaluation_runs`、`evidence_citations`、`financial_checks`、`financial_key_metrics`、`financial_note_links`、`financial_statement_items`、`financial_statements`、`footnotes`、`generated_reports`、`gold_financial_items`、`parse_runs`、`quality_warnings`、`raw_payload_refs`、`report_sections`、`review_feedback`、`toc_entries`。

## 备注

`postgresql_connect.py` 里目前把数据库密码写死了，能跑，但后续建议改成环境变量注入。

本文总结 `pdf2md` PostgreSQL schema 的数据分层、各层表作用、存储数据、核心字段，并基于当前已导入的 10 份测评样本展示真实数据样例。

## 总览

当前库从“PDF 解析结果持久化”扩展为“上市公司财报知识库 + 证据库 + 报告评测库”，共 8 层：

| 层级 | 目标 | 主要表 |
|---|---|---|
| 1. 公司主数据层 | 统一公司实体，支撑公司检索和跨公司分析 | `companies` |
| 2. 公告/财报来源层 | 管理公司对应的年报、半年报、公告文件 | `company_filings` |
| 3. 解析运行层 | 管理一次 PDF 解析任务和解析版本 | `documents`、`parse_runs` |
| 4. 原始产物引用层 | 保存文件产物路径，不把大文件塞进数据库 | `document_artifacts`、`raw_payload_refs` |
| 5. 版面结构层 | 保存页、块、表格、脚注、质量告警 | `document_pages`、`content_blocks`、`document_tables`、`quality_warnings`、`footnotes`、`toc_entries`、`financial_note_links` |
| 6. 财务事实层 | 保存三大表、财务项目、关键指标、勾稽校验 | `financial_statements`、`financial_statement_items`、`financial_key_metrics`、`financial_checks` |
| 7. 证据与 RAG 层 | 保存可检索分片和可引用证据 | `document_chunks`、`evidence_citations`、`analysis_claims`、`claim_evidence_links` |
| 8. 报告、反馈与评测层 | 保存生成报告、人工采纳、标准答案和评测结果 | `generated_reports`、`report_sections`、`review_feedback`、`gold_financial_items`、`evaluation_runs`、`evaluation_results` |

## 1. 公司主数据层

### `pdf2md.companies`

作用：把财报文件中的公司名、证券代码沉淀为统一公司实体，避免后续分析只依赖文件名字符串。

存储数据：公司 ID、证券简称、证券代码、交易所、行业、上市状态、别名、原始来源。

核心字段：

| 字段 | 说明 |
|---|---|
| `company_id` | 公司主键，由股票代码或公司简称生成 |
| `stock_code` | 证券代码，当前文件名缺失时为空 |
| `stock_name` | 公司简称或证券简称 |
| `exchange` | 交易所代码，如 SSE、SZSE |
| `industry` | 所属行业 |
| `aliases` | 公司别名数组 |

示例数据：

| company_id | stock_name | stock_code | exchange |
|---|---|---|---|
| `co_31d564cf77ee7e57afe56160` | `_ST花王` | null | null |
| `co_8feb89d17ca2e816a2065d6e` | `东鹏饮料` | null | null |

说明：当前样本文件名中多数没有证券代码，所以 `stock_code` 为空；后续接入公告检索或证券主数据后可补齐。

## 2. 公告/财报来源层

### `pdf2md.company_filings`

作用：管理公司的一份披露文件，例如某公司某年度年报。它把“公司实体”和“具体 PDF 财报文件”关联起来。

存储数据：公司 ID、任务 ID、报告年份、报告期间、报告类型、标题、公告日期、PDF 路径、来源 URL、是否最新。

核心字段：

| 字段 | 说明 |
|---|---|
| `filing_id` | 披露文件主键 |
| `company_id` | 关联 `companies.company_id` |
| `task_id` | 关联 `documents.task_id` |
| `report_year` | 报告年份 |
| `report_period` | 报告期间，如 FY、H1、Q1 |
| `report_type` | 报告类型，如 annual_report |
| `title` | 披露文件标题或 PDF 文件名 |
| `is_latest` | 是否为当前目标公司最新财报 |

示例数据：

| filing_id | report_year | report_period | report_type | title |
|---|---:|---|---|---|
| `filing_2db61706d9501d05e986e5ae` | 2025 | FY | annual_report | `_ST花王：2025年年度报告.pdf` |
| `filing_50cdfe834a3c71bf49085547` | 2025 | FY | annual_report | `东鹏饮料：东鹏饮料（集团）股份有限公司2025年年度报告.pdf` |

## 3. 解析运行层

### `pdf2md.documents`

作用：兼容型文档/任务主表，保存 `document_full.json` 的任务信息、质量摘要、财务整体状态和产物路径。

存储数据：任务 ID、文件名、报告年份、Markdown 字符数、schema 版本、财务校验整体状态、公司/公告/解析运行关联。

核心字段：

| 字段 | 说明 |
|---|---|
| `task_id` | PDF 解析任务 ID |
| `company_id` | 关联公司 |
| `filing_id` | 关联披露文件 |
| `parse_run_id` | 关联解析运行 |
| `filename` | PDF 文件名 |
| `report_year` | 报告年份 |
| `markdown_chars` | Markdown 正文字符数 |
| `financial_overall_status` | 财务勾稽整体状态 |

示例数据：

| task_id | filename | report_year | markdown_chars | financial_overall_status |
|---|---|---:|---:|---|
| `003f1c09-1c25-409f-9c0c-3ca45808ea74` | `信达证券：信达证券股份有限公司2025年年度报告.pdf` | 2025 | 746778 | pass |
| `03a40b26-c064-4000-8108-5f86474fd8b3` | `_ST花王：2025年年度报告.pdf` | 2025 | 348074 | pass |

### `pdf2md.parse_runs`

作用：记录一次解析运行。将“同一份财报”与“某次解析结果”拆开，支持不同模型、规则版本、OCR 参数的对比。

存储数据：解析运行 ID、任务 ID、parser 名称、parser 版本、schema 版本、状态、质量分。

核心字段：

| 字段 | 说明 |
|---|---|
| `parse_run_id` | 解析运行主键 |
| `task_id` | 关联任务 |
| `filing_id` | 关联披露文件 |
| `parser_name` | 解析器名称 |
| `parser_version` | 解析器版本 |
| `schema_version` | document_full schema 版本 |
| `quality_score` | 解析质量分 |

示例数据：

| parse_run_id | parser_name | parser_version | schema_version | status | quality_score |
|---|---|---|---:|---|---:|
| `run_ba96b5cf3561570ef17b377e` | mineru | 3.1.2 | 1 | completed | 96 |
| `run_4beb6864cafc692c03436efb` | mineru | 3.1.2 | 1 | completed | 96 |

## 4. 原始产物引用层

### `pdf2md.document_artifacts`

作用：记录每个任务目录下的产物文件，不把 PDF、图片、大 JSON、大 Markdown 直接写入数据库。

存储数据：产物名称、路径、URL、是否存在、大小、修改时间、原始引用 JSON。

核心字段：

| 字段 | 说明 |
|---|---|
| `task_id` | 所属任务 |
| `artifact_name` | 产物名称 |
| `kind` | 产物类型 |
| `path` | 本地路径 |
| `url` | Web 可打开 URL |
| `exists` | 文件是否存在 |
| `size_bytes` | 文件大小 |

示例数据：

| artifact_name | exists | path |
|---|---|---|
| `content_list_enhanced.json` | true | `/home/maoyd/pdf2md_web_backup_20260505_101036测评样本/results/003f1c09-1c25-409f-9c0c-3ca45808ea74/content_list_enhanced.json` |
| `content_list.json` | true | `/home/maoyd/pdf2md_web_backup_20260505_101036/results/003f1c09-1c25-409f-9c0c-3ca45808ea74/content_list.json` |

### `pdf2md.raw_payload_refs`

作用：专门记录大体积原始产物的轻量引用。

存储数据：`document_full`、`content_list`、`middle_json`、`model_output`、`result.md`、`result_complete.md` 等产物路径和摘要。

核心字段：

| 字段 | 说明 |
|---|---|
| `payload_name` | 原始产物类型 |
| `path` | 本地路径 |
| `url` | 访问 URL |
| `summary` | 摘要 JSON |

示例数据：

| payload_name | path |
|---|---|
| complete_markdown | `/home/maoyd/pdf2md_web_backup_20260505_101036/results/003f1c09-1c25-409f-9c0c-3ca45808ea74/result_complete.md` |
| content_list | `/home/maoyd/pdf2md_web_backup_20260505_101036/results/003f1c09-1c25-409f-9c0c-3ca45808ea74/content_list.json` |

## 5. 版面结构层

### `pdf2md.document_pages`

作用：页级索引，支撑页级 RAG、页码跳转、页面覆盖率检查。

存储数据：页码、页索引、块数量、页文本预览、原始页 JSON。

核心字段：`task_id`、`page_number`、`page_index`、`block_count`、`preview`

示例数据：

| page_number | block_count | preview |
|---:|---:|---|
| 1 | 5 | `公司代码：601059 公司简称：信达证券 # 信达证券股份有限公司2025 年年度报告` |
| 2 | 19 | `# 重要提示 一、本公司董事会及董事、高级管理人员保证年度报告内容的真实...` |

### `pdf2md.content_blocks`

作用：保存 MinerU 原始内容块的轻量索引，用于 bbox 高亮、按页重建、解析质量追溯。

存储数据：块类型、页码、bbox、文本预览、图片路径、原始块 JSON。

核心字段：`block_index`、`block_type`、`page_number`、`bbox`、`text_preview`、`image_path`

示例数据：

| block_index | block_type | page_number | text_preview |
|---:|---|---:|---|
| 1 | text | 1 | `公司代码：601059` |
| 2 | text | 1 | `公司简称：信达证券` |

### `pdf2md.document_tables`

作用：保存表格溯源、表格结构和质量信号，是财报表格复核和证据追溯的核心表。

存储数据：表格编号、PDF 页码、bbox、行列数、置信度、是否可疑、预览文本、表格结构 JSON。

核心字段：`table_index`、`pdf_page_number`、`bbox`、`rows_count`、`cells_count`、`confidence`、`is_suspicious`、`structure`

示例数据：

| table_index | pdf_page_number | rows | cells | confidence | suspicious | preview |
|---:|---:|---:|---:|---|---|---|
| 1 | 4 | 3 | 4 | high | false | `备查文件目录 载有公司负责人、主管会计工作负责人...` |
| 2 | 5 | 36 | 106 | high | true | `常用词语释义 信达证券、公司、本公司 指...` |

### `pdf2md.quality_warnings`

作用：保存质量报告中的 warning，帮助筛选低质量解析结果。

核心字段：`task_id`、`warning_index`、`warning`

示例数据：

| warning_index | warning |
|---:|---|
| 1 | Markdown 包含图片引用，请确认 images 目录已保存并可被下游读取。 |
| 2 | 发现 18 张可疑表样本，建议在前端“优先复核表”中逐项打开可视化溯源。 |

### `pdf2md.footnotes`

作用：保存脚注和注释，用于附注追溯和未绑定脚注检查。

核心字段：`footnote_index`、`page_number`、`markdown_line`、`text`

示例数据：

| footnote_index | markdown_line | text |
|---:|---:|---|
| 1 | 117 | `2、信达期货` |
| 2 | 138 | `4、信达澳亚` |

### `pdf2md.toc_entries` 与 `pdf2md.financial_note_links`

作用：

- `toc_entries`：保存目录/标题候选。
- `financial_note_links`：保存财报主表项目与附注标题/编号的关联。

当前 10 份样本这两张表暂无数据，原因是对应 `content_list_enhanced.toc` 和 `financial_note_links` 没有产出。

## 6. 财务事实层

### `pdf2md.financial_statements`

作用：保存报表级信息，每张资产负债表、利润表、现金流量表等一行。

存储数据：报表 ID、报表类型、口径、标题、单位、币种、来源表格编号、期间列定义。

核心字段：`statement_id`、`statement_type`、`scope`、`title`、`currency`、`table_indexes`、`columns`

示例数据：

| statement_id | statement_type | scope | title | currency |
|---|---|---|---|---|
| balance_sheet:consolidated | balance_sheet | consolidated | 合并资产负债表 | CNY |
| balance_sheet:parent_company | balance_sheet | parent_company | 母公司资产负债表 | CNY |

### `pdf2md.financial_statement_items`

作用：财务报表项目事实表，每个“项目 + 期间”一行，是 SQL 财务分析的核心。

存储数据：项目名称、标准化名称、期间、数值、原始文本、来源表格、来源页码、来源 bbox。

核心字段：`statement_id`、`item_name`、`canonical_name`、`period_key`、`value`、`raw_value`、`source_table_index`

示例数据：

| statement_id | item_name | period_key | value | raw_value | source_table_index |
|---|---|---|---:|---|---:|
| balance_sheet:consolidated | 货币资金 | 2024-12-31 | 22137616445.64 | 22,137,616,445.64 | 87 |
| balance_sheet:consolidated | 货币资金 | 2025-12-31 | 26760323675.95 | 26,760,323,675.95 | 87 |

### `pdf2md.financial_key_metrics`

作用：保存主要会计数据和关键财务指标，例如营业收入、EPS、ROE。

存储数据：指标名称、标准化名称、期间、值、单位、来源表格。

核心字段：`metric_name`、`canonical_name`、`period_key`、`value`、`unit`、`source_table_index`

示例数据：

| metric_name | canonical_name | period_key | value | unit | source_table_index |
|---|---|---|---:|---|---:|
| 营业收入 | operating_revenue | 2023 | 3483493982.45 | 元 | 23 |
| 营业收入 | operating_revenue | 2024 | 3291547424.2 | 元 | 23 |

### `pdf2md.financial_checks`

作用：保存财务勾稽校验结果，用于判断抽取出的财务数据是否内部一致。

存储数据：规则名称、期间、状态、差异、容差、左右两侧公式和值。

核心字段：`rule_id`、`rule_name`、`statement_type`、`period`、`status`、`diff`、`tolerance`

示例数据：

| rule_name | period | status | diff | tolerance |
|---|---|---|---:|---:|
| 资产总计 = 负债合计 + 所有者权益合计 | 2024-12-31 | pass | 0.0 | 534512.0218877 |
| 资产总计 = 负债和所有者权益总计 | 2024-12-31 | pass | 0.0 | 534512.0218877 |

## 7. 证据与 RAG 层

### `pdf2md.document_chunks`

作用：保存可检索分片，服务 RAG、问答和报告生成。当前自动生成页级 chunk 和表格级 chunk。

存储数据：分片类型、页码、标题、文本内容、来源块/表格 ID、embedding 占位。

核心字段：`chunk_id`、`chunk_index`、`chunk_type`、`page_number`、`title`、`content`、`source_table_ids`

示例数据：

| chunk_index | chunk_type | page_number | title | content |
|---:|---|---:|---|---|
| 1 | page | 1 | PDF_PAGE:1 | `公司代码：601059 公司简称：信达证券 # 信达证券股份有限公司2025 年年度报告` |
| 2 | page | 2 | PDF_PAGE:2 | `# 重要提示 一、本公司董事会及董事、高级管理人员保证年度报告内容的真实...` |

### `pdf2md.evidence_citations`

作用：统一保存可引用证据。生成报告中的事实声明可以绑定到这里，实现“每个结论可追溯”。

存储数据：证据类型、来源 ID、页码、bbox、引用文本、路径、URL、原始 JSON。

核心字段：`citation_id`、`source_type`、`source_id`、`page_number`、`bbox`、`quote_text`

示例数据：

| source_type | source_id | page_number | quote_text |
|---|---|---:|---|
| table | 290 | 84 | `截至报告期末普通股股东总数(户) 74,730 年度报告披露日前上一月末...` |
| table | 339 | 157 | `账龄 期末余额 期初余额 金额 比例(%) 金额 比例(%)...` |

### `pdf2md.analysis_claims` 与 `pdf2md.claim_evidence_links`

作用：

- `analysis_claims`：保存报告中的事实声明。
- `claim_evidence_links`：保存事实声明与证据的关联。

当前基础导入不会写入这两张表，后续报告生成和事实核验模块写入。

## 8. 报告、反馈与评测层

### `pdf2md.generated_reports`

作用：保存智能体生成的研报、经营分析、问答报告等。

核心字段：`report_id`、`company_id`、`filing_id`、`task_id`、`report_type`、`prompt_version`、`model_name`、`content`

### `pdf2md.report_sections`

作用：按章节保存报告内容，方便章节级证据绑定和人工反馈。

核心字段：`section_id`、`report_id`、`section_index`、`section_name`、`content`、`confidence`、`evidence_count`

### `pdf2md.review_feedback`

作用：保存人工是否采纳、评分和意见，用于计算报告内容采纳率。

核心字段：`feedback_id`、`report_id`、`section_id`、`reviewer`、`decision`、`score`、`comment`

### `pdf2md.gold_financial_items`

作用：保存标准答案财务项目，用于评估抽取值是否准确。

核心字段：`company_id`、`filing_id`、`canonical_name`、`period_key`、`expected_value`、`unit`

### `pdf2md.evaluation_runs` 与 `pdf2md.evaluation_results`

作用：

- `evaluation_runs`：一次评测任务。
- `evaluation_results`：评测指标结果，例如准确率、采纳率、检索成功率。

核心字段：

- `evaluation_runs`：`eval_run_id`、`task_id`、`eval_type`、`started_at`、`completed_at`
- `evaluation_results`：`metric_name`、`total_count`、`correct_count`、`accuracy`、`tolerance`

当前数据状态：

| table | rows |
|---|---:|
| generated_reports | 0 |
| report_sections | 0 |
| analysis_claims | 0 |
| review_feedback | 0 |
| gold_financial_items | 0 |
| evaluation_runs | 0 |
| evaluation_results | 0 |

说明：这些表不由 `document_full.json` 基础导入产生，而是由后续报告生成、人工评审、标准答案评测模块写入。

## 数据入库的作用

1. 支撑批量检索：可以按公司、年份、报告类型、解析状态快速筛选目标财报。
2. 支撑精确分析：财务项目和关键指标已结构化成事实表，可直接 SQL 计算同比、结构占比、跨公司对比。
3. 支撑证据追溯：表格、页面、内容块、财务校验都能回到 PDF 页码、bbox 或原始产物路径。
4. 支撑 RAG：`document_chunks` 提供页级和表格级检索单元，后续可写入 embedding。
5. 支撑质量评估：`quality_warnings`、`financial_checks`、`evaluation_results` 分别覆盖解析质量、财务勾稽、标准答案评测。
6. 支撑报告闭环：`generated_reports`、`review_feedback` 可计算报告内容采纳率，反向优化提示词和模型流程。
