# SIQ 通用文档解析服务

`apps/document-parser` 是 SIQ Research Engine 的通用文档解析运行时。它把 PDF、图片、Office、HTML、Markdown、纯文本、网页 URL 和已有 MinerU 结果目录归一为统一的文档 artifact 合同，供 Web、API、Wiki、数据库和向量层复用。

## 为什么要单独做

财报解析解决的是“上市公司披露文件怎么可信地变成证据包”；通用文档解析解决的是“任意文档怎么变成稳定的结构化底座”。两者看起来都在“解析文件”，但目标完全不同：

- 财报解析围绕市场、公司、期间、三大表、勾稽和财务质量门禁。
- 通用文档解析围绕文档类型识别、阅读顺序、块级证据、表格关系、Schema 抽取和归一化 artifact。

因此这里不复用财报语义，不默认出现“三大表”“目标价”“市场”等概念。

## 核心能力

| 能力 | 说明 |
| --- | --- |
| 文件/URL 导入 | 支持多文件上传，也支持网页 URL 抓取 |
| MinerU 产物导入 | 可导入已有 MinerU 输出目录，避免重复解析 |
| 类型识别 | 自动识别 PDF、HTML、文本、Office、图片等文档类型 |
| 标准化产物 | 生成 `manifest.json`、`document.md`、`document_full.json`、`blocks.json`、`tables.json`、`figures.json`、`source_map.json`、`quality_report.json` |
| 图像与表格 | 提取图片、表格、表格关系、逻辑表拆分/合并和人工复核记录 |
| 结构化抽取 | 支持模板抽取和自定义 JSON Schema 抽取 |
| 工作流导入 | 支持导入 Wiki、生成语义 chunks、写入 PostgreSQL 和 Milvus |
| 访问控制 | 通过 API 代理、用户额度和 `UserArtifact` 约束任务归属 |

## 解析流程

```text
上传文件 / 输入 URL / 导入 MinerU 目录
  -> 任务队列
  -> 文件类型识别
  -> 文档解析 provider
  -> 块级归一化 / 表格 / 图片 / source map
  -> quality report
  -> document_full.json
  -> Schema 抽取 / Wiki / PostgreSQL / Milvus
```

## 默认 provider

`app.py` 目前内置以下 provider/路径：

| provider | 说明 |
| --- | --- |
| `simple_text_parser` | 纯文本、Markdown 等文本型文档的轻量解析 |
| `html_reader` | HTML 正文提取 |
| `pdf_parser_bridge` | 通过 `apps/pdf-parser` 复用 MinerU/PDF 解析链路；上游只作临时解析引擎，最终产物归档到 `data/document-parser/results/<task_id>` |
| `mineru_import` | 导入已有 MinerU 结果目录 |

其中 Office、图片和电子表格会先转换为 PDF，再桥接到 `apps/pdf-parser` 的 MinerU 解析链路；最终仍归档到 `data/document-parser/results/<task_id>`，不会在本服务里伪造低质量占位产物。

前端 `/documents` 的预览页按“源 PDF 页图 + document.md HTML”对照展示：左侧通过 `/api/source/<task_id>/page-image/<page>` 渲染原页并叠加 bbox，高亮块/表格/图片；右侧将 `document.md` 渲染为可读 HTML。`table_relations.json` 中的跨页表 continuation 会在 PDF 页之间显示虚线连接和“合并”标签，产物列表的“打开”走认证 fetch/blob，登录态下可直接查阅 JSON、Markdown、ZIP 和页图。

## 启动

```bash
cd /home/maoyd/siq-research-engine/apps/document-parser
./run.sh
```

默认服务地址：

```text
http://127.0.0.1:15010
```

常用覆盖：

```bash
HOST=127.0.0.1 \
PORT=15010 \
SIQ_DOCUMENT_PARSE_DATA_DIR=/home/maoyd/siq-research-engine/data/document-parser \
SIQ_PDF2MD_API_BASE=http://127.0.0.1:15000 \
./run.sh
```

如果设置了访问令牌，请在调用时附带：

```bash
X-Document-Parser-Token: <token>
```

## 运行态目录

```text
data/document-parser/
  uploads/
  results/
  output/
  db/tasks.db
  cache/
  logs/
```

`apps/document-parser` 不把正式产物写入 `data/pdf-parser/results`。上传 PDF 或转换后的 PDF 会以 `doc-<task_id>` 临时提交给 `apps/pdf-parser`，完成后复制 MinerU 原始输出和图片到 `data/document-parser/results/<task_id>/raw/mineru` 与 `images/original`，随后默认删除临时 `pdf-parser` 任务，避免和 A 股、港股等财报解析存档混用。调试时可设置 `SIQ_DOCUMENT_PARSE_KEEP_PDF_BRIDGE_OUTPUT=1` 保留临时上游目录。

可覆盖路径：

| 变量 | 用途 |
| --- | --- |
| `SIQ_DOCUMENT_PARSE_DATA_DIR` | 文档解析运行态根目录 |
| `SIQ_DOCUMENT_UPLOADS_ROOT` | 上传目录 |
| `SIQ_DOCUMENT_RESULTS_ROOT` | 解析结果目录 |
| `SIQ_DOCUMENT_OUTPUT_ROOT` | 上游输出目录 |
| `SIQ_DOCUMENT_TASK_DB_PATH` | SQLite 任务数据库 |
| `SIQ_DOCUMENT_LOG_ROOT` | 日志目录 |
| `SIQ_DOCUMENT_CACHE_ROOT` | 缓存目录 |
| `SIQ_DOCUMENT_PARSER_ACCESS_TOKEN` | 解析服务访问令牌 |
| `SIQ_DOCUMENT_PARSE_MAX_FILE_MB` | 单文件大小上限 |
| `SIQ_DOCUMENT_PARSE_MAX_FILES_PER_UPLOAD` | 单次上传数量上限 |
| `SIQ_DOCUMENT_PARSE_CLOUD_ENABLED` | 是否启用云解析能力标志 |
| `SIQ_DOCUMENT_PARSE_IMPORT_ROOTS` | 允许导入 MinerU 结果目录的额外根目录 |

注：`apps/api` 中还保留了兼容变量 `SIQ_DOCUMENT_PARSE_RESULTS_ROOT` 供上层路径配置使用。

## 主要 API

| API | 用途 |
| --- | --- |
| `GET /api/health` | 服务状态和 provider 能力 |
| `GET /api/tasks` | 列出任务 |
| `POST /api/tasks` | 上传文件或提交 URL |
| `POST /api/import/mineru` | 导入已有 MinerU 输出目录 |
| `GET /api/import/mineru/candidates` | 检查可导入目录候选 |
| `GET /api/status/<task_id>` | 查询任务状态和日志 |
| `GET /api/result/<task_id>` | 查看解析结果 |
| `GET /api/artifact/<task_id>/<artifact>` | 读取标准化 artifact |
| `GET /api/download/<task_id>` | 下载完整 zip |
| `POST /api/download/batch` | 批量下载多个任务的结果包 |
| `POST /api/cancel/<task_id>` | 取消任务 |
| `POST /api/retry/<task_id>` | 重试任务 |
| `DELETE /api/tasks/<task_id>` | 删除任务及其本地产物 |
| `GET /api/source/<task_id>/page/<page_number>` | 页面溯源 |
| `GET /api/source/<task_id>/table/<table_id>` | 表格溯源 |
| `GET /api/source/<task_id>/image/<image_id>` | 图片溯源 |
| `GET /api/table-relations/<task_id>` | 表格关系 |
| `POST /api/table-relations/<task_id>/<relation_id>/review` | 表格关系复核 |
| `POST /api/logical-tables/<task_id>/<logical_table_id>/split` | 逻辑表拆分 |
| `POST /api/logical-tables/<task_id>/merge` | 逻辑表合并 |
| `GET /api/extraction/templates` | 结构化抽取模板 |
| `POST /api/extract/<task_id>` | 按模板或 schema 抽取 |

## 设计难点

- 文档类型很多，但最终必须落到同一套 artifact 合同里，不能让每种输入都长出一套不同结构。
- HTML、PDF、文本、Office、URL 的阅读顺序和 block 切分策略不同，必须统一 `source_map` 和 `document_full`。
- 导入已有 MinerU 目录时，不能假设其路径一定安全，所以要限制 root 并校验目录特征。
- 结构化抽取必须对齐 schema，而不是让模型自由发挥。
- 通用文档不能污染财报链路，尤其不能把“简单文本回退”伪装成高质量解析。
- 后台 worker 默认自动拉起，若需要严格控制任务生命周期，可通过环境变量关闭自动启动并由外部进程接管。

## 开发验证

```bash
cd /home/maoyd/siq-research-engine/apps/document-parser
python3 -m pytest tests
bash -n run.sh
```

## 维护原则

- 解析结果、上传文件、任务库、缓存和日志只放运行态目录。
- 新增 provider 时先补测试，再补 README 中的能力说明。
- 结构化抽取模板要保持“缺失字段返回 null，不要推断”。
- 导入外部 MinerU 目录时必须保留原始产物和审核痕迹。
