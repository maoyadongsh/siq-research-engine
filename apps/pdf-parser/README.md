# SIQ PDF 解析服务

`apps/pdf-parser` 是 SIQ Research Engine 的本地 PDF 解析、复核和结构化抽取服务。它接收上市公司年报 PDF，调用 MinerU / VLM 解析，生成 Markdown、表格、页码、质量报告、财务抽取和证据溯源接口。

## 核心价值

财报智能分析的可信度取决于 PDF 到结构化证据的质量。本服务负责把“不可直接计算的 PDF”变成可检索、可引用、可校验、可人工修正的研究底座。

| 能力 | 说明 |
| --- | --- |
| 任务化解析 | 上传 PDF 后进入 SQLite 任务队列，支持状态查询和结果读取 |
| Markdown 产物 | 输出可阅读、可切分、可导入 Wiki 的正文 |
| 表格与页码索引 | 保留 `table_index`、页码和来源片段，支撑后续报告引用 |
| 质量报告 | 检测空页、乱码、表格缺失、页码异常、内容结构问题 |
| 财务抽取 | 抽取三大表、关键指标、单位、期间和勾稽检查结果 |
| 人工修正 | 支持表格修正保存，让高价值样本可人工闭环 |
| 溯源 API | 提供表格、页面和 PDF 页图访问，用于前端证据复核 |

## 解析流程

```text
PDF 上传
  -> 本地任务队列
  -> MinerU / VLM 解析
  -> Markdown、content_list、middle_json
  -> 页码标记和表格索引增强
  -> quality_report.json
  -> financial_data.json / financial_checks.json
  -> document_full.json
  -> Wiki / PostgreSQL / 前端溯源
```

## 产物版本

| 产物 | 版本 |
| --- | --- |
| `quality_report.json` | `10` |
| `content_list_enhanced.json` | `8` |
| `document_full.json` | `1` |
| `financial_data.json` | `13` |
| `financial_checks.json` | `12` |
| 财务规则 | `financial_rules_v14` |

实际加载版本可通过 `/api/health` 查看。

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

`run.sh` 只启动 Flask Web 服务。MinerU、VLM、vLLM 等模型服务需要按本机模型环境提前启动。

## 运行环境

默认 MinerU Python 环境：

```text
runtimes/mineru-native
```

可通过环境变量覆盖：

```bash
SIQ_MINERU_VENV=/path/to/mineru_native ./run.sh
```

## 数据目录

运行态数据默认放在源码目录外：

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

可覆盖路径：

| 变量 | 用途 |
| --- | --- |
| `SIQ_PDF2MD_DATA_DIR` | PDF 解析运行态根目录 |
| `SIQ_PDF_UPLOADS_ROOT` | 上传 PDF 目录 |
| `SIQ_PDF_RESULTS_ROOT` | 解析结果目录 |
| `SIQ_PDF_OUTPUT_ROOT` | MinerU 输出目录 |
| `SIQ_PDF_TASK_DB_PATH` | SQLite 任务数据库 |
| `SIQ_FINANCIAL_LLM_CACHE_ROOT` | 财务表格判断缓存 |
| `SIQ_PDF2MD_LOG_ROOT` | 解析服务日志目录 |

## 主要 API

| API | 用途 |
| --- | --- |
| `GET /api/health` | 服务、模型上游和产物版本状态 |
| `GET /api/tasks` | 列出解析任务 |
| `POST /api/upload` | 上传 PDF 并创建任务 |
| `GET /api/status/<task_id>` | 查询任务状态 |
| `GET /api/result/<task_id>` | Markdown 和结果 payload |
| `GET /api/quality/<task_id>` | 质量报告 |
| `GET /api/financial/<task_id>` | 财务抽取和校验 |
| `GET /api/source/<task_id>/table/<table_index>` | 表格溯源 |
| `GET /api/source/<task_id>/page/<page_number>` | 页面溯源 |
| `POST /api/source/<task_id>/table/<table_index>/correction` | 保存人工表格修正 |

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

## 开发验证

```bash
cd /home/maoyd/siq-research-engine/apps/pdf-parser
python3 -m pytest tests
bash -n run.sh
```

## 维护原则

- 解析结果、上传 PDF、任务库、缓存和日志只放运行态目录。
- 新增财务抽取规则时要同步更新版本号、测试和质量报告说明。
- 涉及 PDF 页码或表格索引的改动必须验证前端溯源链接可打开。
- 字段不足时输出明确的数据缺口，不用模型猜测财务数字。
