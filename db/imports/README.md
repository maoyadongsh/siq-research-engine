# SIQ PostgreSQL 入库工具

`db/imports` 保存 PDF/市场报告解析产物入库 PostgreSQL 的工具，以及面向财务数据的查询辅助入口。它把 `apps/pdf-parser` 生成的 `document_full.json` 或市场 evidence package 转换为可 SQL 查询、可溯源、可供 Agent 使用的结构化证据层。

## 在系统中的位置

```text
PDF 解析结果 document_full.json / 市场 evidence package
  -> import_document_full_to_postgres.py
  -> import_*_evidence_package_to_postgres.py
  -> PostgreSQL pdf2md / sec_us / pdf2md_hk / edinet_jp / dart_kr / eu_ifrs schema
  -> API / Agent / 查询工具 / 评测流程
```

入库后的数据库用于：

- 补全 PDF 页码、表格编号和 Markdown 行号。
- 支撑财务指标查询、三大表校验和报告事实核查。
- 为分析、核查、跟踪 Agent 提供只读证据层。
- 将文件型解析产物转换为可批量统计和评测的数据资产。

## 文件说明

| 文件 | 用途 |
| --- | --- |
| `import_document_full_to_postgres.py` | 将一个或多个 `document_full.json` 导入 PostgreSQL |
| `financial_query_api.py` | 自然语言财务查询 API |
| `financial_query_ui.html` | 查询 API 的浏览器界面 |
| `stock_name_to_code.py` | 公司名称与股票代码映射工具 |
| `stock_name_to_code_data.json` | 离线公司名称与股票代码映射数据 |
| `export_zte_wide_to_excel.py` | 宽表数据导出示例 |
| `import_hk_evidence_package_to_postgres.py` | 导入港股 `data/wiki/hk_reports` evidence package 到 `pdf2md_hk` |
| `import_jp_evidence_package_to_postgres.py` | 导入日本 EDINET evidence package 到 `edinet_jp` |
| `import_kr_evidence_package_to_postgres.py` | 导入韩国 DART evidence package 到 `dart_kr` |
| `import_eu_evidence_package_to_postgres.py` | 导入欧股 PDF/HTML/ESEF evidence package 到 `eu_ifrs` |

## 数据库配置

优先使用 `DATABASE_URL`：

```bash
export DATABASE_URL='postgresql://postgres:password@127.0.0.1:15432/siq'
```

也可以使用标准 libpq 变量：

```bash
export PGHOST=127.0.0.1
export PGPORT=15432
export PGDATABASE=siq
export PGUSER=postgres
export PGPASSWORD='replace-me'
```

常用 SIQ 路径：

```bash
export SIQ_PROJECT_ROOT=/home/maoyd/siq-research-engine
export SIQ_WIKI_ROOT=$SIQ_PROJECT_ROOT/data/wiki
export SIQ_PDF_RESULTS_ROOT=$SIQ_PROJECT_ROOT/data/pdf-parser/results
export SIQ_DB_ROOT=$SIQ_PROJECT_ROOT/db
```

## 导入示例

导入默认结果目录中的 `document_full.json`：

```bash
cd /home/maoyd/siq-research-engine
python3 db/imports/import_document_full_to_postgres.py --ddl
```

递归导入：

```bash
python3 db/imports/import_document_full_to_postgres.py data/pdf-parser/results --recursive
```

导入单个文件：

```bash
python3 db/imports/import_document_full_to_postgres.py /path/to/document_full.json
```

限制导入数量，用于 smoke test：

```bash
python3 db/imports/import_document_full_to_postgres.py data/pdf-parser/results --recursive --limit 1
```

指定 Wiki 公司目录：

```bash
python3 db/imports/import_document_full_to_postgres.py \
  --wiki-companies-dir data/wiki/companies \
  --recursive
```

导入欧股 evidence package：

```bash
python3 db/imports/import_eu_evidence_package_to_postgres.py \
  --package data/wiki/eu_reports/NL/ASML/2025/annual_NL-ASML-2025 \
  --ddl
```

## 查询 API

本地启动：

```bash
cd /home/maoyd/siq-research-engine
uvicorn db.imports.financial_query_api:app --host 0.0.0.0 --port 18188
```

健康检查和查询：

```bash
curl -s http://127.0.0.1:18188/health
curl -s http://127.0.0.1:18188/query \
  -H 'content-type: application/json' \
  -d '{"question":"查询信达证券2025年利润表营业总收入"}'
```

## 数据表价值

导入工具会尽量保留以下信息：

| 信息 | 用途 |
| --- | --- |
| 文档元数据 | 公司、报告期、任务 ID、文件来源 |
| 页面与内容块 | PDF 页码、文本块、版面结构 |
| 表格结构 | 表格编号、行列内容、来源页 |
| 财务科目 | 三大表项目、规范字段、单位和期间 |
| 证据引用 | 指标到页面、表格和 Markdown 行的映射 |
| 质量告警 | 解析缺口、异常页、表格识别问题 |

## 导出示例

`export_zte_wide_to_excel.py` 读取 `DATABASE_URL` 或 libpq 变量：

```bash
python3 db/imports/export_zte_wide_to_excel.py
```

覆盖输出路径：

```bash
SIQ_ZTE_EXPORT_PATH=/tmp/zte.xlsx python3 db/imports/export_zte_wide_to_excel.py
```

## 维护原则

- 数据库口令只放环境变量或安全配置，不写入脚本和 README。
- 入库脚本应保持幂等、可限制数量、可按目录递归处理。
- 新增字段时同步更新 DDL、导入逻辑和查询工具。
- Agent 使用数据库时默认只读，不在报告生成中修改源数据。
