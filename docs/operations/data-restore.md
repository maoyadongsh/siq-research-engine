# 数据恢复操作说明

本文记录 SIQ Research Engine 从本地备份或旧项目只读对照恢复数据的推荐路径。恢复目标默认放在本仓库的 `data/` 或由环境变量指向的外部挂载目录。

## 恢复原则

- 优先恢复到 `data/*`，不要把运行态数据写回源码目录。
- 优先使用逻辑导出、manifest 和可重复脚本，谨慎使用原始容器快照。
- 恢复后通过 `SIQ_*` 环境变量指向新位置。
- 大体量数据、数据库文件、上传 PDF、缓存和日志继续保持 Git 忽略。

## Wiki

默认位置：

```text
data/wiki
```

恢复后设置：

```bash
export SIQ_WIKI_ROOT=/home/maoyd/siq-research-engine/data/wiki
```

如果从旧只读来源恢复成本地副本：

```bash
cd /home/maoyd/siq-research-engine
mkdir -p data/wiki
rsync -a /path/to/old/wiki/ data/wiki/
export SIQ_WIKI_ROOT=/home/maoyd/siq-research-engine/data/wiki
```

## PDF 解析运行态

推荐恢复目标：

```text
data/pdf-parser/
```

常见子目录：

```text
uploads/
results/
output/
db/tasks.db
cache/financial_llm/
logs/
workflow_jobs.json
```

恢复后设置：

```bash
export SIQ_PDF2MD_DATA_DIR=/home/maoyd/siq-research-engine/data/pdf-parser
```

单项目录也可通过 `SIQ_PDF_UPLOADS_ROOT`、`SIQ_PDF_RESULTS_ROOT`、`SIQ_PDF_OUTPUT_ROOT`、`SIQ_PDF_TASK_DB_PATH` 覆盖。

## 公告下载文件

默认位置：

```text
data/market-report-finder/downloads
```

API 通过以下变量定位：

```bash
export SIQ_REPORT_FINDER_ROOT=/home/maoyd/siq-research-engine/services/market-report-finder
export SIQ_REPORT_DOWNLOADS_ROOT=/home/maoyd/siq-research-engine/data/market-report-finder/downloads
```

## PostgreSQL

旧备份中如有 PostgreSQL 逻辑导出，应优先使用：

```text
/path/to/postgres/exports
```

恢复前先确认目标数据库和用户。典型流程：

```bash
createdb siq
psql "$DATABASE_URL" -f /path/to/export.sql
```

恢复完成后设置：

```bash
export DATABASE_URL='postgresql://postgres:password@127.0.0.1:15432/siq'
```

原始 PostgreSQL 容器数据只作为最后恢复手段，不应直接提交或移动到源码目录。

## Milvus 和 MinIO

旧备份中如有 Milvus 和 MinIO 快照，优先视为离线恢复资产：

```text
/path/to/milvus
/path/to/minio
```

这两类数据优先视为快照资产。正式恢复前需要确认版本、容器参数、volume 挂载路径和数据一致性。没有经过验证的恢复流程前，不应把它们作为日常开发必需路径。

## Hermes 运行态

默认位置：

```text
data/hermes/home
```

恢复后设置：

```bash
export SIQ_HERMES_HOME=/home/maoyd/siq-research-engine/data/hermes/home
export SIQ_HERMES_PROFILES_ROOT=$SIQ_HERMES_HOME/profiles
```

profile 名称迁移期保持兼容，例如 `siq_assistant`、`siq_analysis`、`siq_factchecker`、`siq_tracking`、`siq_legal`。

## 验证清单

```bash
test -d "$SIQ_WIKI_ROOT/companies"
test -d "$SIQ_PDF2MD_DATA_DIR"
curl -s http://localhost:18081/health
curl -s http://localhost:15000/api/health
```

若恢复 PostgreSQL，再运行：

```bash
python3 db/imports/import_document_full_to_postgres.py --help
python3 db/imports/test_financial_query_api_cases.py
```

数据库测试需要有效的 `DATABASE_URL` 或 libpq 环境变量。
