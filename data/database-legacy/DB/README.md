# PDF2MD PostgreSQL 入库与财务查询说明

本目录维护 PDF2MD 财报解析结果的 PostgreSQL 入库、数据表设计、前端查询 API 和相关辅助脚本。核心目标是把 `document_full.json` 中的上市公司财务数据落到 `pdf2md` schema，并通过浏览器或 `/query` 接口用自然语言查询三大表和指标。

## 目录结构

```text
/home/maoyd/DB
├── DDL
│   └── 001_create_pdf2md_schema.sql      # 建 schema、表、索引、中文注释
├── DML
│   └── 001_upsert_document_full.sql      # 导入脚本使用的命名 SQL 块
├── PROGRAM
│   ├── import_document_full_to_postgres.py # document_full.json 入库脚本
│   ├── financial_query_api.py              # FastAPI 查询服务
│   ├── financial_query_ui.html             # 前端查询页面
│   ├── stock_name_to_code.py               # 公司简称/股票代码映射辅助
│   ├── stock_name_to_code_data.json        # 本地公司映射数据
│   ├── test_financial_query_api_cases.py   # 查询接口测试样例
│   └── export_zte_wide_to_excel.py         # 中兴通讯宽表明细导出示例
└── pdf2md_data_layers_summary.md
```

## 数据流

```text
pdf2md results/*/document_full.json
  -> PROGRAM/import_document_full_to_postgres.py
  -> PostgreSQL pdf2md schema
  -> PROGRAM/financial_query_api.py
  -> GET / 或 GET /ui 前端页面
  -> POST /query 查询接口
  -> 浏览器表格 + 原始 JSON
```

## 数据库连接

导入脚本和查询 API 默认使用：

```text
/home/maoyd/finance_evidence_poc/DB/DML/postgresql_connect.py
```

当前默认配置入口：

```text
host=127.0.0.1
port=5432
dbname=<从配置或环境变量读取>
user=<从配置或环境变量读取>
password=<从配置或环境变量读取，不在 README 中展示>
schema=pdf2md
```

也可以用环境变量覆盖：

```text
PGHOST
PGPORT
PGDATABASE
PGUSER
PGPASSWORD
DATABASE_URL
```

## 当前检查结论

检查时间：2026-05-29。本目录是 SIQ 的 PostgreSQL 数据层工程，不是聚合后端本身。聚合后端 `workflow.py` 会调用本目录的导入脚本；Hermes 智能体在需要补证据、补页码或交叉校验时只读查询 `pdf2md` schema。

当前本机 PostgreSQL 进程存在；`financial_query_api.py` 的 `18888` 查询服务当前未运行，因此演示时应以主工作台和 Agent 查询为主，必要时再手动启动 `18888`。

### 数据层在 SIQ 中的位置

```text
pdf2md_web/results/<task_id>/document_full.json
  -> /home/maoyd/DB/PROGRAM/import_document_full_to_postgres.py
  -> PostgreSQL pdf2md schema
  -> Agent 只读查询 / 可选 18888 查询 API
  -> 报告证据增强、页码补全、三表指标交叉校验
```

### 决赛关注点

| 维度 | 本目录贡献 |
| --- | --- |
| 创新性 | 把 PDF 解析产物沉淀成可查询、可回溯的结构化证据库，而不是只保存一份 Markdown |
| 技术难度 | 需要将文档、页面、内容块、表格、财务项目、宽表和证据引用统一到 `pdf2md` schema |
| 完成度 | DDL、DML、导入脚本、查询 API 和测试样例均已存在，可支持从 `document_full.json` 到数据库的落库 |
| 商业价值 | 为投研、审计、合规和评测提供可复用的数据底座，支持批量查询和证据回放 |

## 评委技术说明

`/home/maoyd/DB` 是 SIQ 的 PostgreSQL 结构化证据层。Wiki 负责可读、可审计的文件化知识资产；PostgreSQL 负责把同一批解析结果转成可查询、可聚合、可批量校验的数据表，尤其适合三大表项目、关键指标、页面表格、内容块和证据引用的跨公司检索。

| 维度 | 实现说明 |
| --- | --- |
| 技术架构 | DDL 建模 + DML upsert SQL 块 + `import_document_full_to_postgres.py` 入库脚本 + FastAPI 查询服务 + 前端查询页 |
| 技术栈 | PostgreSQL、schema `pdf2md`、SQL/JSONB、Python、psycopg、FastAPI、Pydantic、本地股票简称映射 |
| 数据流 | `document_full.json` -> 文档/页面/内容块/表格/财务项目拆分 -> PostgreSQL `pdf2md` schema -> Agent 只读查询/18888 查询 API |
| 算法模型 | 财务项目规范化、公司代码映射、period/scope 标准化、JSONB 宽表聚合、upsert 去重、表格来源与 PDF 页码关联 |
| 证据契约 | 三大表项目通过 `task_id + source_table_index` 关联 `document_tables`，补齐 PDF 页码、Markdown 行号和表格来源 |
| 商业价值 | 支持批量财务查询、跨公司对比、指标校验、报告事实核查和评测 metadata 生成 |

### PostgreSQL 数据处理流程

```text
pdf2md_web/results/<task_id>/document_full.json
  -> import_document_full_to_postgres.py
  -> pdf2md.documents / document_pages / content_blocks / document_tables
  -> pdf2md.financial_*_items / financial_key_metrics / financial_all_metrics_wide
  -> pdf2md.evidence_citations / quality_warnings
  -> Agent 证据增强、页码补全、三表查询、评测链路 metadata
```

这层数据库的技术难度在于“同一事实多视角对齐”：一条营业收入记录不仅要有值，还要同时保留公司、报告期、报表类型、单位、来源表、PDF 页码、Markdown 行、bbox 和原始 JSON。这样分析报告中的数字可以被事实核查助手重新查询，也可以被前端或评测接口回放到原始 PDF 证据。

## 核心数据表

### 文档与溯源表

```text
pdf2md.documents
pdf2md.document_artifacts
pdf2md.document_pages
pdf2md.content_blocks
pdf2md.document_tables
pdf2md.quality_warnings
pdf2md.footnotes
pdf2md.toc_entries
pdf2md.raw_payload_refs
```

这些表保存任务、文件引用、页级索引、内容块、表格位置、质量告警和原始产物引用。PDF、图片、Markdown 等大文件不直接写入数据库，只保存路径、URL、摘要和必要 JSONB。

### 公司与披露文件表

```text
pdf2md.companies
pdf2md.company_filings
pdf2md.parse_runs
```

`companies` 保存公司实体；`company_filings` 保存一份年报/半年报/季报等披露文件；`parse_runs` 保存一次解析运行，便于同一份财报多次解析后做版本比较。

### 财务事实表

通用财务表：

```text
pdf2md.financial_statements
pdf2md.financial_statement_items
pdf2md.financial_key_metrics
pdf2md.financial_checks
pdf2md.financial_note_links
```

项目当前前端主要查询的四张表：

```text
pdf2md.financial_balance_sheet_items       # 资产负债表明细
pdf2md.financial_income_statement_items    # 利润表明细
pdf2md.financial_cash_flow_statement_items # 现金流量表明细
pdf2md.financial_all_metrics_wide          # 全指标宽表 JSONB
```

三大表明细的常用字段：

```text
task_id
statement_id
item_index
period_key
company_id
stock_code
stock_name
exchange
filing_id
parse_run_id
report_year
report_period
statement_name
scope
scope_name
item_name
canonical_name
value
raw_value
unit
currency
source_page_number
source_table_index
source_bbox
source
raw_item
imported_at
```

宽表 `financial_all_metrics_wide` 每个 `task_id + period_key` 一行，把资产负债表、利润表、现金流量表和关键指标聚合到 JSONB：

```text
balance_sheet
income_statement
cash_flow_statement
key_metrics
all_metrics
```

## 建表

首次使用或表结构更新时执行 DDL：

```bash
python3 /home/maoyd/DB/PROGRAM/import_document_full_to_postgres.py \
  --ddl \
  --limit 1 \
  --config-py /home/maoyd/finance_evidence_poc/DB/DML/postgresql_connect.py
```

也可以直接用 `psql` 执行 `DDL/001_create_pdf2md_schema.sql`。

## 数据入库

入库脚本：

```text
/home/maoyd/DB/PROGRAM/import_document_full_to_postgres.py
```

默认数据源目录：

```text
/home/maoyd/pdf2md_web_backup_20260511_000657测试样本/results
```

导入默认目录下全部 `document_full.json`：

```bash
python3 /home/maoyd/DB/PROGRAM/import_document_full_to_postgres.py \
  --config-py /home/maoyd/finance_evidence_poc/DB/DML/postgresql_connect.py
```

递归导入指定结果目录：

```bash
python3 /home/maoyd/DB/PROGRAM/import_document_full_to_postgres.py \
  /home/maoyd/pdf2md_web_backup_20260511_000657测试样本/results \
  --recursive \
  --config-py /home/maoyd/finance_evidence_poc/DB/DML/postgresql_connect.py
```

导入单个文件：

```bash
python3 /home/maoyd/DB/PROGRAM/import_document_full_to_postgres.py \
  /path/to/document_full.json \
  --config-py /home/maoyd/finance_evidence_poc/DB/DML/postgresql_connect.py
```

冒烟测试只导入一个文件：

```bash
python3 /home/maoyd/DB/PROGRAM/import_document_full_to_postgres.py \
  /path/to/results \
  --recursive \
  --limit 1 \
  --config-py /home/maoyd/finance_evidence_poc/DB/DML/postgresql_connect.py
```

## 入库逻辑

入库脚本主要步骤：

1. 扫描 `document_full.json`。
2. 读取 `task`、`quality_report`、`financial_data`、`financial_checks`、`content_list_enhanced`、`artifacts` 等字段。
3. 从文件名和本地映射识别公司简称、股票代码、交易所。
4. 写入 `documents`、`companies`、`company_filings`、`parse_runs`。
5. 写入页、块、表格、脚注、目录、质量告警和原始产物引用。
6. 遍历 `financial_data.statements[]`，按 `statement_type` 写入三大表明细。
7. 遍历 `financial_data.key_metrics[]`，写入关键指标表。
8. 聚合三大表和关键指标，写入 `financial_all_metrics_wide`。
9. 执行 merge 去重，保证同一粒度多次入库时只保留最新数据。

## 去重与 merge 规则

同一个 `task_id` 再次导入时，脚本会先删除该任务的子表数据，再重新插入。

跨 `task_id` 出现同一公司、同一报告期、同一指标的重复数据时，脚本会按最新导入时间保留一条。三大表的业务粒度包括：

```text
公司 + report_year + report_period + period_key
+ statement_id + scope
+ item_name + canonical_name
+ source_table_index + source_page_number
```

宽表的业务粒度包括：

```text
公司 + report_year + report_period + period_key
```

保留优先级：

```text
imported_at DESC, task_id DESC
```

注意：合并口径和母公司口径不是重复数据。例如：

```text
balance_sheet:consolidated
balance_sheet:parent_company
```

默认查询返回合并口径；问题中明确包含“母公司”时返回母公司口径。

## 单位处理

入库脚本会优先使用源 JSON 中每张报表或每个指标的 `unit`。如果源头明细项单位为空，脚本会从原始文本中推断财务报表默认单位，例如：

```text
人民币千元
百万元
万元
元
```

特殊指标如 `基本每股收益`、`稀释每股收益` 会补为：

```text
元/股
```

因此数据库中的 `unit` 是“源头单位 + 入库补齐”的结果。若要追溯源头原始值，可查看：

```text
raw_value
raw_item
source
source_table_index
source_page_number
document_tables.raw
```

## 启动查询服务

API 脚本：

```text
/home/maoyd/DB/PROGRAM/financial_query_api.py
```

前台启动：

```bash
cd /home/maoyd
python3 -m uvicorn DB.PROGRAM.financial_query_api:app --host 0.0.0.0 --port 18888
```

后台启动：

```bash
tmux new-session -d -s financial_query_api \
  'cd /home/maoyd && python3 -m uvicorn DB.PROGRAM.financial_query_api:app --host 0.0.0.0 --port 18888'
```

查看服务：

```bash
tmux ls
ss -ltnp | grep 18888
```

访问前端：

```text
http://127.0.0.1:18888/
http://127.0.0.1:18888/ui
```

健康检查：

```bash
curl -s http://127.0.0.1:18888/health
curl -s http://127.0.0.1:18888/healthz
```

## 查询 API

接口：

```text
POST /query
```

请求体：

```json
{
  "question": "查询华安证券2025年回购业务资金净增加额",
  "use_hermes": false,
  "limit": 10
}
```

字段说明：

```text
question   自然语言问题
use_hermes 是否调用 Hermes 解析；关闭时使用本地规则解析
limit      最多返回行数，范围 1 到 1000
```

示例：

```bash
curl -s http://127.0.0.1:18888/query \
  -H 'content-type: application/json' \
  -d '{"question":"查询华安证券2025年回购业务资金净增加额","use_hermes":false,"limit":10}'
```

响应结构：

```json
{
  "question": "查询华安证券2025年回购业务资金净增加额",
  "parsed": {},
  "source_tables": [],
  "rows": [],
  "row_count": 0
}
```

## 前端到输出的逻辑链条

```text
浏览器输入问题
  -> JS fetch POST /query
  -> FastAPI query_financial_data()
  -> merge_parse() 解析公司、年份、期间、报表类型、口径、指标
  -> resolve_company() 在 pdf2md.companies 和三大表中识别公司
  -> require_company_match() 避免公司没识别时误返回其他公司
  -> 如果是“只输入公司名”，query_company_all_metrics() 返回该公司全部指标
  -> 如果是“三大表查询”，query_statement_table() 返回整张表明细
  -> 如果是“指标查询”，infer_metric_from_database() 从数据库动态识别指标名
  -> query_metric_from_split_tables() 查询三大表明细
  -> 如果三大表无结果，query_metric_from_wide() 查询宽表 fallback
  -> dedupe_response_rows() 响应层去重
  -> 返回 JSON
  -> 前端 render() 渲染统计卡片、表格和原始 JSON
```

## 支持的查询类型

### 查整张表

```text
查询华安证券2025年现金流量表
查询中兴通讯2025年利润表
查询美的集团2025年资产负债表
```

### 查具体指标

```text
给我看中兴通讯2025年营业收入
查询华安证券2025年回购业务资金净增加额
给我看美的集团2025-12-31总资产
```

### 只输入公司名

```text
中兴通讯
美的集团
工商银行
```

只输入公司名时，后端按公司过滤，返回三大表中该公司的全部指标。默认口径为合并口径。

### 查母公司口径

```text
给我看比亚迪2025-12-31母公司总资产
查询中兴通讯2025年母公司利润表
```

## 自然语言解析规则

后端会识别：

```text
公司名/股票代码
年份，如 2025年
日期期间，如 2025-12-31
报表类型：资产负债表、利润表、现金流量表
报表口径：合并、母公司
指标名：内置别名 + 数据库动态指标匹配
```

指标识别优先级：

1. 本地内置别名，如“营收”映射到“营业总收入”。
2. Hermes 解析结果，如果 `use_hermes=true`。
3. 数据库动态匹配，即从已入库的 `item_name` / `canonical_name` 里找最长命中项。

## 常用 SQL

查看四张核心表行数：

```sql
SELECT 'balance' AS table_name, count(*) FROM pdf2md.financial_balance_sheet_items
UNION ALL
SELECT 'income', count(*) FROM pdf2md.financial_income_statement_items
UNION ALL
SELECT 'cash_flow', count(*) FROM pdf2md.financial_cash_flow_statement_items
UNION ALL
SELECT 'wide', count(*) FROM pdf2md.financial_all_metrics_wide;
```

查看公司：

```sql
SELECT stock_code, stock_name, exchange, company_id
FROM pdf2md.companies
ORDER BY stock_code NULLS LAST, stock_name;
```

查某公司某指标：

```sql
SELECT stock_code, stock_name, report_year, report_period, period_key,
       statement_id, item_name, value, raw_value, unit,
       source_table_index, source_page_number
FROM pdf2md.financial_cash_flow_statement_items
WHERE stock_name = '华安证券'
  AND report_year = 2025
  AND item_name ILIKE '%回购业务资金净增加额%';
```

查某条数据的 JSON 溯源：

```sql
SELECT source, raw_item
FROM pdf2md.financial_income_statement_items
WHERE stock_name = '中兴通讯'
  AND item_name ILIKE '%营业收入%'
LIMIT 5;
```

## 测试

查询接口测试样例：

```bash
cd /home/maoyd
python3 /home/maoyd/DB/PROGRAM/test_financial_query_api_cases.py
```

接口服务启动后也可以用 curl 做冒烟测试：

```bash
curl -s http://127.0.0.1:18888/query \
  -H 'content-type: application/json' \
  -d '{"question":"中兴通讯","use_hermes":false,"limit":10}'
```

## Excel 导出

中兴通讯宽表明细导出脚本：

```text
/home/maoyd/DB/PROGRAM/export_zte_wide_to_excel.py
```

当前导出文件示例：

```text
/home/maoyd/DB/PROGRAM/中兴通讯_financial_all_metrics_wide_明细.xlsx
```

## 常见问题

### 1. 前端报 PostgreSQL unavailable

说明 FastAPI 能访问，但数据库连接失败。检查：

```bash
docker ps
nc -vz 127.0.0.1 5432
python3 /home/maoyd/finance_evidence_poc/DB/DML/postgresql_connect.py
```

### 2. 查询返回其它公司

通常是公司识别失败导致 SQL 没有公司过滤。当前 API 已加 `require_company_match()`：如果用户显式输入公司名但库里找不到，会返回 404，不会继续返回其它公司。

### 3. 指标提示“请在问题中指定指标名”

说明解析器没有识别出指标，且数据库动态匹配也没有命中。可以先查整张表确认源头是否有该指标：

```text
查询华安证券2025年现金流量表
```

如果整张表能看到该指标，但指标查询失败，需要检查 `infer_metric_from_database()` 的匹配逻辑或该指标是否被错误分表。

### 4. 同一指标返回两条

先看 `statement_id` 和 `scope`。常见原因是合并口径和母公司口径同时存在：

```text
consolidated
parent_company
```

默认查询合并口径。要查母公司口径，在问题里写“母公司”。

### 5. source 指向的表格和指标文本不一致

以 `source_table_index`、`source`、`raw_item` 为准做初步定位，但仍要回到 `document_full.json` / `result_complete.md` 校验。部分源 JSON 中可能存在 `sources.line` 或 `table_index` 错指，表现为数据入到了某张表，但原始文本出现在相邻表格或其它报表段落。

排查路径：

```text
1. 查数据库 raw_item/source
2. 根据 task_id 找 documents.document_full_path 和 complete_markdown_path
3. 在 document_full.json 搜 item_name/raw_value
4. 在 result_complete.md 搜 item_name/raw_value
5. 对比 document_tables 的 table_index、markdown_line、preview
```

## 重要文件

```text
/home/maoyd/DB/DDL/001_create_pdf2md_schema.sql
/home/maoyd/DB/DML/001_upsert_document_full.sql
/home/maoyd/DB/PROGRAM/import_document_full_to_postgres.py
/home/maoyd/DB/PROGRAM/financial_query_api.py
/home/maoyd/DB/PROGRAM/financial_query_ui.html
/home/maoyd/DB/PROGRAM/README.md
```
