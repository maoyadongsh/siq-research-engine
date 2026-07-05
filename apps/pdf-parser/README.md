# SIQ PDF 解析服务

## 模块定位

`apps/pdf-parser` 是 SIQ 面向财报 PDF 的专业解析运行时。它服务于 A 股为主的财报解析链路，也为部分港股、日股、韩股和 bridge 场景提供 PDF 版面事实能力。这个模块的目标不是“把 PDF 变成一段文本”，而是把财报 PDF 变成可校验、可引用、可人工复核的研究底座。

## 在系统中的位置

```text
PDF 披露文件
  -> apps/pdf-parser
     -> Markdown / content list / quality / financial data / source APIs
     -> Wiki / PostgreSQL / 前端溯源 / Agent 消费
```

它在系统里承担的是“财报专业解析面”的角色：

- 上游接收上传 PDF 或下载后的披露文件。
- 中游通过 MinerU / VLM 和本地增强逻辑生成标准产物。
- 下游把结果交给 API、Web、db/imports、市场 package 构建和 Hermes 消费。

## 核心能力

| 能力 | 说明 |
| --- | --- |
| 任务化解析 | 上传 PDF 后进入 SQLite 任务队列，支持状态查询、取消、重试与删除 |
| Markdown 与结构产物 | 输出 Markdown、`document_full.json`、`content_list_enhanced.json` 等标准产物 |
| 表格与页码增强 | 建立页码、表格索引、页面锚点和来源坐标 |
| 质量报告 | 输出 `quality_report.json`，区分可用、warning、失败等状态 |
| 财务抽取 | 生成 `financial_data.json` 和 `financial_checks.json` |
| 人工修正 | 支持表格关系修正、逻辑表拆分 / 合并和纠错回放 |
| Source API | 提供表格、页面、页图和 artifact 下载接口 |

## 技术难点

`apps/pdf-parser` 的难度不在“把 PDF OCR 出来”，而在“把财报里的结构性事实提出来且可追溯”：

- 版面复杂：财报中的目录、注释、图表、跨页表、附注表与主表经常交叉出现。
- 表格语义复杂：同一张表里可能同时包含单位、期间、子项目、合并范围和脚注，需要避免把结构误当数值。
- 页码与内容不同步：PDF 页图、Markdown、content list 和 table index 必须保持足够一致，前端才能可靠跳回证据。
- 财务抽取风险高：QTD / YTD、单位缩放、币种、审计状态和期间口径都可能让结果失真。
- 质量门禁必须诚实：当上游解析失败时，系统宁可失败，也不能静默退化成低质量文本输出。

## 关键接口或标准产物

### 关键 API

| API | 用途 |
| --- | --- |
| `GET /api/health` | 查看服务状态、上游地址与 artifact 版本 |
| `GET /api/tasks` | 列出任务 |
| `POST /api/upload` | 上传 PDF 并创建解析任务 |
| `POST /api/cancel/<task_id>` | 取消任务 |
| `POST /api/refetch/<task_id>` | 重新抓取结果视图 |
| `POST /api/reparse/<task_id>` | 触发重新解析 |
| `GET /api/status/<task_id>` | 查看任务状态和日志 |
| `GET /api/result/<task_id>` | 查看主结果 |
| `GET /api/quality/<task_id>` | 查看质量报告 |
| `GET /api/financial/<task_id>` | 查看财务抽取结果 |
| `GET /api/artifact/<task_id>/<artifact_name>` | 读取标准 artifact |
| `GET /api/source/<task_id>/table/<table_index>` | 表格溯源 |
| `GET /api/source/<task_id>/page/<page_number>` | 页面溯源 |
| `GET /api/pdf_page/<task_id>/<page_number>` | PDF 页图 |
| `POST /api/source/<task_id>/table/<table_index>/correction` | 表格人工修正 |
| `GET /api/download/<task_id>` | 下载结果包 |
| `GET /api/download_complete/<task_id>` | 下载完整归档包 |
| `GET /api/download_corrected/<task_id>` | 下载修正后结果 |
| `DELETE /api/tasks/<task_id>` | 删除任务与运行态产物 |

### 核心 artifact

| 产物 | 作用 |
| --- | --- |
| `document_full.json` | 文档统一事实合同 |
| `content_list_enhanced.json` | 增强后的段落与表格结构层 |
| `quality_report.json` | 解析质量门禁 |
| `table_relations.json` | 表格关系与逻辑表信息 |
| `financial_data.json` | 财务事实抽取结果 |
| `financial_checks.json` | 勾稽与一致性校验结果 |

## 启动方式

### 标准启动

```bash
cd /home/maoyd/siq-research-engine/apps/pdf-parser
./run.sh
```

默认地址：

```text
http://127.0.0.1:15000
```

### 常用覆盖

```bash
cd /home/maoyd/siq-research-engine/apps/pdf-parser
HOST=127.0.0.1 \
PORT=15000 \
MINERU_API_URL=http://127.0.0.1:8003 \
VLM_API_URL=http://127.0.0.1:8002 \
SIQ_PDF2MD_DATA_DIR=/home/maoyd/siq-research-engine/data/pdf-parser \
./run.sh
```

`run.sh` 默认激活 `runtimes/mineru-native` 环境，并连接本机 MinerU / VLM 上游。该脚本只启动 Flask 服务，不会自动拉起上游模型服务。

## 关键环境变量

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `SIQ_MINERU_VENV` | `$PROJECT_ROOT/runtimes/mineru-native` | MinerU Python 环境 |
| `SIQ_PDF2MD_DATA_DIR` | `$SIQ_DATA_ROOT/pdf-parser` | 运行态根目录 |
| `SIQ_PDF_UPLOADS_ROOT` | `$DATA_DIR/uploads` | 上传目录 |
| `SIQ_PDF_RESULTS_ROOT` | `$DATA_DIR/results` | 结果目录 |
| `SIQ_PDF_OUTPUT_ROOT` | `$DATA_DIR/output` | 中间输出目录 |
| `SIQ_PDF_TASK_DB_PATH` | `$DATA_DIR/db/tasks.db` | SQLite 任务库 |
| `SIQ_FINANCIAL_LLM_CACHE_ROOT` | `$DATA_DIR/cache/financial_llm` | 财务判断缓存 |
| `SIQ_PDF2MD_LOG_ROOT` | `$DATA_DIR/logs` | 日志目录 |
| `MINERU_API_URL` | `http://127.0.0.1:8003` | 上游 MinerU API |
| `VLM_API_URL` | `http://127.0.0.1:8002` | 上游视觉模型服务 |
| `TASK_RETENTION_HOURS` | `0` | 任务保留策略 |

## 验证方式

```bash
cd /home/maoyd/siq-research-engine/apps/pdf-parser
python3 -m pytest tests
bash -n run.sh
```

若改动了 source、artifact、quality 或 financial 路由，至少补跑对应测试模块，并手动调用 `/api/health` 与一个任务的 `/api/result/<task_id>` 或 `/api/source/...` 验证链路。

## 维护原则

- 财报质量门禁优先于“尽量返回结果”；低质量结果不能伪装成高可信事实层。
- 与页码、表格索引、source 坐标相关的变更必须验证前端溯源是否还能回跳。
- 任何财务规则更新都应同步反映到版本、测试和 README 描述里。
- 运行态目录、缓存、上传 PDF 和日志不写回源码目录。
- 当上游 MinerU / VLM 失败时，应显式暴露失败而不是偷偷降级为简单文本输出。
