# SIQ PDF 解析服务

`apps/pdf-parser` 是 SIQ Research Engine 的本地 PDF 解析、复核和结构化抽取服务。它接收年报 PDF，将任务提交给 MinerU/VLM，写出 Markdown 和 JSON 产物，生成质量报告，抽取财务报表，并提供 Web 工作台和 API 聚合后端使用的证据溯源接口。

该服务由 SIQ `pdf2md_web` 工具迁移而来。新的部署应使用 SIQ 路径和 `SIQ_*` 环境变量。

## 核心流程

```text
PDF 上传
  -> 本地 SQLite 任务队列
  -> MinerU / VLM 解析
  -> Markdown 和中间产物
  -> 页码标记、表格索引、质量报告
  -> 财务报表和指标抽取
  -> 勾稽校验
  -> 溯源与人工修正 API
```

## 当前产物版本

| 产物 | 版本 |
| --- | --- |
| `quality_report.json` | `10` |
| `content_list_enhanced.json` | `8` |
| `document_full.json` | `1` |
| `financial_data.json` | `13` |
| `financial_checks.json` | `12` |
| 财务规则 | `financial_rules_v14` |

运行后可通过 `/api/health` 查看实际加载版本。

## 启动

```bash
cd /home/maoyd/siq-research-engine/apps/pdf-parser
./run.sh
```

默认服务地址：

```text
http://127.0.0.1:15000
```

常用覆盖：

```bash
HOST=127.0.0.1 \
PORT=15000 \
MINERU_API_URL=http://127.0.0.1:8003 \
VLM_API_URL=http://127.0.0.1:8002 \
SIQ_PDF2MD_DATA_DIR=/home/maoyd/siq-research-engine/data/pdf-parser \
./run.sh
```

`run.sh` 只启动 Flask Web 服务。MinerU、VLM 和 vLLM 等上游模型服务需要提前启动。

## 运行环境

默认情况下，`run.sh` 使用：

```text
runtimes/mineru-native
```

可通过环境变量覆盖：

```bash
SIQ_MINERU_VENV=/path/to/mineru_native ./run.sh
```

`MINERU_VENV` 和 `SIQ_MINERU_VENV` 仅作为迁移期兼容回退。

## 数据目录

SIQ 部署应将运行态数据放在源码目录外：

```text
data/pdf-parser/
  uploads/
  results/
  output/
  db/tasks.db
  cache/financial_llm/
  logs/
  workflow_jobs.json
```

`run.sh` 默认设置：

```text
SIQ_PDF2MD_DATA_DIR=/home/maoyd/siq-research-engine/data/pdf-parser
```

单项路径可覆盖：

| 变量 | 用途 |
| --- | --- |
| `SIQ_PDF_UPLOADS_ROOT` 或 `UPLOAD_FOLDER` | 上传 PDF 目录 |
| `SIQ_PDF_RESULTS_ROOT` 或 `RESULTS_FOLDER` | 解析结果目录 |
| `SIQ_PDF_OUTPUT_ROOT` 或 `OUTPUT_FOLDER` | MinerU 输出目录 |
| `SIQ_PDF_TASK_DB_PATH` 或 `TASK_DB_PATH` | SQLite 任务数据库 |
| `SIQ_FINANCIAL_LLM_CACHE_ROOT` 或 `FINANCIAL_LLM_CACHE_FOLDER` | 可选 LLM 表格判断缓存 |
| `SIQ_PDF2MD_LOG_ROOT` 或 `PDF2MD_LOG_DIR` | 解析服务日志目录 |

运行态数据默认忽略。不要提交上传 PDF、结果目录、任务数据库、模型缓存或日志。

## 目录结构

```text
apps/pdf-parser/
  app.py
  artifact_manager.py
  financial_extractor.py
  mineru_client.py
  path_config.py
  pdf_source_viewer.py
  quality_engine.py
  quality_report.py
  task_store.py
  run.sh
  requirements.txt
  static/app.js
  templates/index.html
  scripts/
  tests/
  Dockerfile
```

## 主要 API

| API | 用途 |
| --- | --- |
| `GET /api/health` | 服务和上游健康状态 |
| `GET /api/tasks` | 列出解析任务 |
| `POST /api/upload` | 上传 PDF 并创建任务 |
| `GET /api/status/<task_id>` | 查询任务状态 |
| `GET /api/result/<task_id>` | Markdown/结果 payload |
| `GET /api/quality/<task_id>` | 质量报告 |
| `GET /api/financial/<task_id>` | 财务抽取和校验 |
| `GET /api/source/<task_id>/table/<table_index>` | 表格溯源 |
| `GET /api/source/<task_id>/page/<page_number>` | 页面溯源 |
| `POST /api/source/<task_id>/table/<table_index>/correction` | 保存人工表格修正 |

## 开发验证

```bash
cd /home/maoyd/siq-research-engine/apps/pdf-parser
python3 -m pytest tests
bash -n run.sh
```

## 迁移注意事项

- 不要新增指向旧 PDF 解析目录的源码默认值。
- 兼容环境变量只在迁移期保留。
- 运行态路径统一通过 `path_config.py` 解析。
- 源码保留在 `apps/pdf-parser`，运行产物保留在 `data/pdf-parser`。
