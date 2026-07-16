# SIQ 通用文档解析服务

## 模块定位

`apps/document-parser` 是 SIQ 里负责“任意文档归一”的解析运行时。它面向 PDF、HTML、图片、Office、Markdown、纯文本、网页 URL 和既有 MinerU 结果目录，把异构输入转换为一套稳定的 document artifact 合同，供 Web、API、Wiki、PostgreSQL、Milvus 和结构化抽取流程复用。

它解决的问题不是“上市公司财报怎么抽三大表”，而是“任何文档怎么形成可消费、可回放、可抽取的结构化底座”。

## 在系统中的位置

```text
文件 / URL / MinerU 目录
  -> apps/document-parser
     -> document_full / source_map / blocks / tables / figures / quality
     -> API 代理 / Wiki / PostgreSQL / Milvus / schema extraction
```

与 `apps/pdf-parser` 的关系是分工而不是重复：

- `apps/pdf-parser` 面向财报语义、质量门禁和财务抽取。
- `apps/document-parser` 面向类型识别、块级结构、表格关系、图像与 schema extraction。
- 当文档需要高质量 PDF 版面能力时，`apps/document-parser` 可以通过 bridge 临时调用 `apps/pdf-parser`，但最终产物仍回到自身结果目录。

## 核心能力

| 能力 | 说明 |
| --- | --- |
| 文件 / URL 导入 | 支持多文件上传、URL 抓取和本地文件解析 |
| MinerU 目录导入 | 可导入既有 MinerU 输出，避免重复跑重解析 |
| 类型归一 | 自动识别 PDF、HTML、文本、Office、图片等输入类型 |
| 标准 artifact 生成 | 输出 `document.md`、`document_full.json`、`blocks.json`、`tables.json`、`figures.json`、`source_map.json`、`quality_report.json` |
| 表格关系与逻辑表 | 支持 continuation 识别、逻辑表拆分 / 合并与人工复核 |
| Schema 抽取 | 支持模板抽取与自定义 JSON Schema 抽取 |
| 工作流衔接 | 可接 Wiki、PostgreSQL、Milvus 与 API 侧资产归属体系 |

## 当前最新状态

| 方向 | 状态 | 说明 |
| --- | --- | --- |
| 通用证据合同 | `document_full`、`source_map`、`tables`、`figures`、`quality_report` 稳定输出 | 让非财报材料也能进入 Wiki、PostgreSQL、Milvus 和 Agent 工作流 |
| MinerU 导入 | 支持已有 MinerU 目录候选发现与导入 | 复用历史解析产物，减少重复计算和重跑成本 |
| 表格关系 | continuation / logical table / merge 等结构持续增强 | 为合同、尽调材料、研报附件和非标准 PDF 提供可复核表格层 |
| Schema extraction | 抽取模板与自定义 JSON Schema 并存 | 面向尽调、合同、会议材料和运营文档的结构化抽取入口 |
| API/Web 归属 | 通过 `apps/api` 做用户归属、source 访问和 artifact 控制 | 保留文档解析能力的安全边界 |

这个服务的商业价值是把“公司资料包里什么都有”的混乱现实变成统一证据合同。财报、网页、合同、图片和会议材料可以进入同一条治理链，而不是分别做一次性解析。

## 技术难点

通用文档解析的核心难点在于：输入类型很多，但输出合同不能失控。

- 输入异构：HTML、文本、Office、图片、PDF 和 URL 的阅读顺序、块边界和表格信息完全不同。
- 合同统一：不论来源是什么，最终都要落回统一的 `document_full`、`source_map`、`tables`、`figures` 和 `quality_report`。
- 上游桥接风险：Office 或图片常常需要先转成 PDF 再借用 `apps/pdf-parser`，但不能把桥接过程伪装成“原生财报解析”。
- 结构化抽取风险：schema extraction 必须对齐显式字段定义，不能让模型用自然语言猜测缺失值。
- 安全边界：外部目录导入、URL 抓取和 artifact 访问都需要做 root 限制、路径校验和 token 保护。

## 关键接口或标准产物

### 关键 API

| API | 用途 |
| --- | --- |
| `GET /api/health` | 服务状态、provider 能力和 worker 状态 |
| `POST /api/tasks` | 上传文件或提交 URL |
| `POST /api/import/mineru` | 导入既有 MinerU 目录 |
| `GET /api/import/mineru/candidates` | 查找可导入的目录候选 |
| `GET /api/tasks` | 列出任务 |
| `GET /api/status/<task_id>` | 查看任务状态和日志 |
| `GET /api/result/<task_id>` | 查看解析结果摘要 |
| `GET /api/artifact/<task_id>/<artifact>` | 读取标准 artifact |
| `GET /api/download/<task_id>` | 下载完整结果包 |
| `POST /api/download/batch` | 批量下载结果包 |
| `GET /api/source/<task_id>/page/<page_number>` | 页面级溯源 |
| `GET /api/source/<task_id>/page-image/<page_number>` | 页面预览图 |
| `GET /api/source/<task_id>/table/<table_id>` | 表格溯源 |
| `GET /api/source/<task_id>/image/<image_id>` | 图片溯源 |
| `GET /api/table-relations/<task_id>` | 表格关系 |
| `POST /api/logical-tables/<task_id>/merge` | 逻辑表合并 |
| `GET /api/extraction/templates` | 抽取模板列表 |
| `POST /api/extract/<task_id>` | 执行结构化抽取 |

### 标准 artifact

| 产物 | 作用 |
| --- | --- |
| `document.md` | 可读 Markdown 主体 |
| `document_full.json` | 文档统一事实合同 |
| `blocks.json` | 块级结构 |
| `tables.json` | 表格事实层 |
| `figures.json` | 图片事实层 |
| `source_map.json` | 坐标与来源映射 |
| `quality_report.json` | 质量告警与可信度说明 |
| `table_relations.json` | 跨页表与逻辑表关系 |

## 启动方式

### 标准启动

```bash
cd /home/maoyd/siq-research-engine/apps/document-parser
./run.sh
```

默认地址：

```text
http://127.0.0.1:15010
```

### 常用覆盖

```bash
cd /home/maoyd/siq-research-engine/apps/document-parser
HOST=127.0.0.1 \
PORT=15010 \
SIQ_DOCUMENT_PARSE_DATA_DIR=/home/maoyd/siq-research-engine/data/document-parser \
SIQ_PDF2MD_API_BASE=http://127.0.0.1:15000 \
./run.sh
```

如果启用了访问令牌，调用时需要携带：

```text
X-Document-Parser-Token: <token>
```

## 关键环境变量

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `SIQ_DOCUMENT_PARSE_DATA_DIR` | `$SIQ_DATA_ROOT/document-parser` | 文档解析运行态根目录 |
| `SIQ_DOCUMENT_UPLOADS_ROOT` | `$DATA_DIR/uploads` | 上传目录 |
| `SIQ_DOCUMENT_RESULTS_ROOT` | `$DATA_DIR/results` | 结果目录 |
| `SIQ_DOCUMENT_OUTPUT_ROOT` | `$DATA_DIR/output` | 中间输出目录 |
| `SIQ_DOCUMENT_TASK_DB_PATH` | `$DATA_DIR/db/tasks.db` | SQLite 任务库 |
| `SIQ_DOCUMENT_LOG_ROOT` | `$DATA_DIR/logs` | 日志目录 |
| `SIQ_DOCUMENT_CACHE_ROOT` | `$DATA_DIR/cache` | 缓存目录 |
| `SIQ_DOCUMENT_PARSER_ACCESS_TOKEN` | 空 | 访问令牌 |
| `SIQ_DOCUMENT_PARSE_MAX_FILE_MB` | `200` | 单文件大小上限 |
| `SIQ_DOCUMENT_PARSE_MAX_FILES_PER_UPLOAD` | `50` | 单次上传数量上限 |
| `SIQ_DOCUMENT_PARSE_WORKER_AUTOSTART` | `true` | 是否自动拉起后台 worker |
| `SIQ_PDF2MD_API_BASE` | `http://127.0.0.1:15000` | PDF bridge 上游地址 |
| `SIQ_DOCUMENT_PARSE_PDF_ARTIFACT_TRANSPORT` | `auto` | `api` 强制经认证 Artifact API 拉取，`shared_fs` 仅使用共享盘，`auto` 先校验共享盘再回退 API |
| `SIQ_DOCUMENT_PARSE_PDF_STAGE_MAX_FILE_BYTES` | `134217728` | API staging 单文件上限 |
| `SIQ_DOCUMENT_PARSE_PDF_STAGE_MAX_TOTAL_BYTES` | `1073741824` | 单任务 API staging 总量上限 |
| `SIQ_DOCUMENT_PARSE_PDF_STAGE_MAX_FILES` | `4096` | 单任务 API staging 文件数上限 |
| `SIQ_DOCUMENT_PARSE_PDF_STAGE_MAX_JSON_BYTES` | `16777216` | manifest、索引等 JSON 单文件上限 |

## 验证方式

```bash
cd /home/maoyd/siq-research-engine/apps/document-parser
python3 -m pytest tests
bash -n run.sh
```

若修改了 provider、artifact 或 source payload，至少补跑对应测试，并手动访问 `GET /api/health` 与一个任务结果页验证 payload 结构。

## 维护原则

- 不把通用文档解析与财报专用语义混写在一起。
- 新增 provider 时优先补测试、再补 README、最后接入 API / Web。
- `document_full`、`source_map`、`tables`、`figures` 等 artifact 结构要尽量稳定，避免下游频繁适配。
- 导入外部 MinerU 目录时必须保留原始产物和审计痕迹，不能做静默覆盖。
- 抽取结果必须诚实表达缺口，允许字段为 `null`，不允许为了“完整输出”而擅自推断。

## 创新性与商业价值

通用文档解析器把“文件转文字”提升为“文件编译成研究合同”。文本块、表格、图片、逻辑表、抽取字段和来源坐标使用稳定标识关联，使数据库、向量库和智能体能消费同一份事实结构。

| 设计 | 技术价值 | 业务价值 |
| --- | --- | --- |
| 多格式统一 artifact | PDF、Office、HTML、图片和文本进入同一结果模型 | 合同、招股书、访谈材料和内部底稿复用同一处理链 |
| 逻辑表关系 | 识别跨页表、合并/拆分与人工 review 状态 | 降低长表被错误切断造成的财务或条款误读 |
| Schema 驱动抽取 | 模板、结果、evidence map 和 validation report 分离 | 可针对行业、合同和尽调场景配置结构化抽取 |
| 可回溯资源接口 | block/table/image/page 级 source API | 智能体结论和人工审阅能返回原始位置 |

核心壁垒是版面结构、稳定身份、可编辑关系和证据坐标同时成立。只提高 OCR 文本准确率并不足以满足研究、法务和审计使用。
