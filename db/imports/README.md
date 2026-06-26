# SIQ 数据库导入工具

本目录保存 PDF 解析产物 `document_full.json` 导入 PostgreSQL 的工具，以及基于 `pdf2md` schema 的查询辅助工具。

## 文件说明

| 文件 | 用途 |
| --- | --- |
| `import_document_full_to_postgres.py` | 将一个或多个 `document_full.json` 导入 PostgreSQL |
| `financial_query_api.py` | 可选的自然语言财务查询 API |
| `financial_query_ui.html` | 查询 API 的浏览器界面 |
| `stock_name_to_code.py` 和 `stock_name_to_code_data.json` | 离线公司名称/股票代码映射 |
| `export_zte_wide_to_excel.py` | 中兴通讯宽表数据导出示例 |

## 导入流程

```text
document_full.json
  -> import_document_full_to_postgres.py
  -> PostgreSQL pdf2md.* 表
  -> financial_query_api.py / 查询工具
```

## 推荐环境变量

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
export SIQ_WIKI_ROOT=$SIQ_PROJECT_ROOT/_external_assets/wiki/wiki
export SIQ_PDF_RESULTS_ROOT=$SIQ_PROJECT_ROOT/data/pdf-parser/results
export SIQ_DB_ROOT=$SIQ_PROJECT_ROOT/db
```

## 导入示例

导入默认 SIQ 结果目录下一层的所有 `document_full.json`：

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

覆盖 Wiki 公司主数据目录：

```bash
python3 db/imports/import_document_full_to_postgres.py \
  --wiki-companies-dir _external_assets/wiki/wiki/companies \
  --recursive
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

## 导出示例

`export_zte_wide_to_excel.py` 读取 `DATABASE_URL` 或 libpq 变量，不再包含数据库凭据。

```bash
python3 db/imports/export_zte_wide_to_excel.py
```

覆盖输出路径：

```bash
SIQ_ZTE_EXPORT_PATH=/tmp/zte.xlsx python3 db/imports/export_zte_wide_to_excel.py
```

## 迁移注意事项

- 不要新增旧源目录、旧 Wiki 目录或旧 DB 目录的硬编码默认值。
- 源码工具保留在 `db/imports`；数据库 dump 和运行态数据继续忽略。
- PostgreSQL 恢复优先使用 `_external_assets/postgres/exports` 中的逻辑导出，不优先使用原始容器数据。
