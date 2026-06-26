# PDF2MD 财务查询程序说明

本目录包含两类程序：

- `import_document_full_to_postgres.py`：把 `document_full.json` 批量导入 PostgreSQL 的 `pdf2md` schema。
- `financial_query_api.py`：提供浏览器页面和 `/query` API，用自然语言查询公司三大表和财务指标。

## 数据流

```text
document_full.json
  -> import_document_full_to_postgres.py
  -> PostgreSQL pdf2md.* tables
  -> financial_query_api.py /query
  -> browser table + raw JSON
```

## 数据库连接

API 和导入脚本默认使用这个配置文件：

```text
/home/maoyd/finance_evidence_poc/DB/DML/postgresql_connect.py
```

当前默认连接到本机 PostgreSQL，具体库名、用户名和密码从配置文件或环境变量读取，不在 README 中展示：

```text
host=127.0.0.1
port=5432
```

也可以通过环境变量覆盖：`PGHOST`、`PGPORT`、`PGDATABASE`、`PGUSER`、`PGPASSWORD`。

## 当前检查结论

检查时间：2026-05-29。PostgreSQL 进程存在，但 `financial_query_api.py` 对应的 `http://127.0.0.1:18888/health` 当前未运行。主工作台不依赖 `18888` 才能启动；该 API 更适合作为数据排查、自然语言查三表和评委补充演示入口。

如果需要演示数据库查询功能，先启动：

```bash
cd /home/maoyd
python3 -m uvicorn DB.PROGRAM.financial_query_api:app --host 0.0.0.0 --port 18888
```

### 决赛关注点

| 维度 | 本程序目录贡献 |
| --- | --- |
| 创新性 | 支持把 PDF 解析总账 `document_full.json` 转成可查询的财务证据库 |
| 技术难度 | 处理公司身份匹配、三大表拆分、宽表聚合、重复导入 merge、页码和表格证据关联 |
| 完成度 | 已有入库脚本、查询 API、HTML 查询页和测试用例；可按需启动 |
| 商业价值 | 让财报智能体结果能够被数据库复核和批量查询，适合企业内控和投研知识库落地 |

## 导入数据

导入脚本的位置：

```bash
/home/maoyd/DB/PROGRAM/import_document_full_to_postgres.py
```

公司主数据现在优先来自 wiki：

```text
/home/maoyd/wiki/companies/<公司目录>/company.json
```

导入脚本会先用 `company.json.reports[].task_id` / `reports[].document_full` / `reports[].source_filename` 匹配公司；匹配不到时才回退到文件名推断。`documents`、`company_filings`、三大拆分表和宽表都会继承这套公司字段。

公司身份规则：

- `stock_code` 是上市公司业务唯一锚点，入库和抽取时必须保持独立字段。
- `stock_name` / wiki 的 `company_short_name` 是独立简称字段。
- `company_id` 只作为兼容 wiki 目录和 PostgreSQL 外键的技术 ID/slug，当前形态通常为 `股票代码-公司简称`，不要把它当成不可拆的业务主键。

当前默认 `document_full.json` 数据源目录：

```text
/home/maoyd/pdf2md_web_backup_20260511_000657测试样本/results
```

直接导入默认目录下所有 `document_full.json`：

```bash
python3 /home/maoyd/DB/PROGRAM/import_document_full_to_postgres.py   --config-py /home/maoyd/finance_evidence_poc/DB/DML/postgresql_connect.py   --wiki-companies-dir /home/maoyd/wiki/companies
```

导入单个文件：

```bash
python3 /home/maoyd/DB/PROGRAM/import_document_full_to_postgres.py   /path/to/document_full.json   --config-py /home/maoyd/finance_evidence_poc/DB/DML/postgresql_connect.py
```

限制导入数量，适合冒烟测试：

```bash
python3 /home/maoyd/DB/PROGRAM/import_document_full_to_postgres.py   --config-py /home/maoyd/finance_evidence_poc/DB/DML/postgresql_connect.py   --wiki-companies-dir /home/maoyd/wiki/companies   --limit 1
```

重跑当前 10 份 wiki 案例时，若遇到 `300017-网宿科技` 的 wiki 复制件 JSON 损坏，可使用 `/home/maoyd/pdf2md_web/results/a65972d0-aa69-4b76-82ef-a0fc2c030240/document_full.json` 作为该 task 的有效原始产物；公司主数据仍会通过 `task_id` 匹配 `/home/maoyd/wiki/companies/300017-网宿科技/company.json`。

## 导入去重规则

导入同一个 `task_id` 时，会先删除该任务的子表数据，再重新插入。

如果同一家公司、同一报告期、同一指标来自多个 `task_id`，导入脚本会执行 merge 清理，只保留最新一条。三大表的去重粒度包括：

```text
公司 + report_year + report_period + period_key + statement_id + scope + item_name + canonical_name + source_table_index + source_page_number
```

宽表 `financial_all_metrics_wide` 的去重粒度包括：

```text
公司 + report_year + report_period + period_key
```

保留规则：按 `imported_at DESC, task_id DESC` 选择最新行。

## 启动查询服务

```bash
cd /home/maoyd
python3 -m uvicorn DB.PROGRAM.financial_query_api:app --host 0.0.0.0 --port 18888
```

后台启动示例：

```bash
tmux new-session -d -s financial_query_api   'cd /home/maoyd && python3 -m uvicorn DB.PROGRAM.financial_query_api:app --host 0.0.0.0 --port 18888'
```

浏览器访问：

```text
http://127.0.0.1:18888/
```

健康检查：

```bash
curl -s http://127.0.0.1:18888/health
```

## API 调用

```bash
curl -s http://127.0.0.1:18888/query   -H 'content-type: application/json'   -d '{"question":"查询华泰证券2025年回购业务资金净增加额","use_hermes":false,"limit":10}'
```

请求字段：

- `question`：自然语言问题。
- `use_hermes`：是否调用 Hermes 解析。默认可关闭，后端有规则解析和数据库动态指标识别。
- `limit`：最多返回行数。

响应结构：

```json
{
  "question": "查询华泰证券2025年回购业务资金净增加额",
  "parsed": {},
  "source_tables": [],
  "rows": [],
  "row_count": 0
}
```

## 查询链路

一次查询从前端到结果的链路如下：

```text
浏览器输入问题
  -> POST /query
  -> merge_parse() 解析年份、公司、报表类型、口径、内置指标
  -> resolve_company() 识别公司
  -> infer_metric_from_database() 从已入库 item_name/canonical_name 动态识别指标
  -> query_statement_table() 或 query_metric_from_split_tables()
  -> 拆分表无结果时 fallback 到 query_metric_from_wide()
  -> dedupe_response_rows() 响应去重
  -> 返回 JSON
  -> 前端 render() 渲染表格和 raw JSON
```

## 自然语言解析规则

后端会尝试解析：

- 公司名：如 `比亚迪`、`华泰证券`。
- 年份：如 `2025年`。
- 日期期间：如 `2025-12-31`。
- 报表类型：资产负债表、利润表、现金流量表。
- 报表口径：默认 `consolidated` 合并口径。
- 指标名：优先内置别名，未命中时从数据库已有指标动态匹配。

口径规则：

- 默认返回合并口径：`statement_scope=consolidated`。
- 问题中包含 `母公司`、`母公司口径`、`parent` 时返回母公司口径：`statement_scope=parent_company`。

示例：

```text
给我看比亚迪2025-12-31总资产
查询华泰证券2025年现金流量表
查询华泰证券2025年回购业务资金净增加额
给我看比亚迪2025-12-31母公司总资产
```

## 主要查询表

三大拆分表：

```text
pdf2md.financial_balance_sheet_items
pdf2md.financial_income_statement_items
pdf2md.financial_cash_flow_statement_items
```

宽表 fallback：

```text
pdf2md.financial_all_metrics_wide
```

公司主数据：

```text
pdf2md.companies
```

## 常见问题

### 查询返回其它公司

通常是公司名没有被识别，导致 SQL 没加公司过滤。当前版本已经收紧显式公司名匹配：如果用户明确输入了公司名，但库里没有对应公司，会返回 404，不会误返回其它公司。

### 同一个指标返回多条

先看 `statement_id` 和 `scope`。例如：

```text
balance_sheet:consolidated
balance_sheet:parent_company
```

这不是重复数据，而是合并报表和母公司报表两个口径。默认查询只返回合并口径；要查母公司口径，需要在问题里写 `母公司`。

### 指标不在内置列表里

当前版本会从数据库已入库指标名中动态匹配。例如 `回购业务资金净增加额` 不在原始内置别名里，也可以查询。

### 公司未入库

如果返回：

```json
{"detail":"未找到公司 XXX 的入库财务数据，请先导入对应 document_full.json。"}
```

说明该公司没有出现在当前 PostgreSQL 的 `pdf2md` 数据中，需要先导入对应 `document_full.json`。

## 验证命令

查看入库规模：

```bash
docker exec a04e3d6eea99 psql -U dgx -d ai_platform -c "SELECT count(*) FROM pdf2md.documents; SELECT count(*) FROM pdf2md.companies;"
```

查看三大表行数：

```bash
docker exec a04e3d6eea99 psql -U dgx -d ai_platform -c "SELECT count(*) AS balance_items FROM pdf2md.financial_balance_sheet_items; SELECT count(*) AS income_items FROM pdf2md.financial_income_statement_items; SELECT count(*) AS cash_flow_items FROM pdf2md.financial_cash_flow_statement_items;"
```

测试查询：

```bash
curl -s http://127.0.0.1:18888/query   -H 'content-type: application/json'   -d '{"question":"给我看比亚迪2025-12-31总资产","use_hermes":false,"limit":10}'
```
