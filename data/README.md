# SIQ 运行态数据目录

`data/` 保存 SIQ Research Engine 的本地运行态数据。该目录默认被 Git 忽略，除 README、`.gitkeep` 或小型 manifest 外，不提交业务数据、缓存、数据库文件和模型产物。

## 子目录

| 路径 | 归属 | 内容 |
| --- | --- | --- |
| `data/backend` | `apps/api` | API 本地数据库、聊天附件、LLM 成本日志、运行设置 |
| `data/pdf-parser` | `apps/pdf-parser` | 财报 PDF 上传、解析输出、任务库、财务缓存、日志、工作流任务记录 |
| `data/document-parser` | `apps/document-parser` | 通用文档上传、解析结果、任务库、缓存、日志、工作流任务记录 |
| `data/market-report-finder` | `services/market-report-finder` | CN/HK/US/EU/JP/KR 原始披露文件、HTML/iXBRL/PDF 和下载索引 |
| `data/wiki` | API / Hermes / 工作流 | 公司 Wiki、报告产物、metrics、evidence、semantic 等 |
| `data/hermes` | Hermes profiles | 网关运行态、会话、日志、响应存储 |
| `data/postgres` | PostgreSQL | 本地数据库数据或逻辑备份放置区 |
| `data/milvus` | Milvus | 向量库数据或快照放置区 |
| `data/sqlite` | 共享 SQLite | 小型本地数据库文件 |

## 不提交内容

- `*.db`
- 上传 PDF、通用文档和下载披露文件
- PDF / 文档解析结果、输出目录和任务库
- 聊天附件、用户会话和审计明细
- 模型缓存、embedding 缓存和 LLM 请求日志
- PostgreSQL、Milvus、MinIO 等数据库或对象存储数据
- 包含 API key、数据库口令或个人信息的配置文件

## 运行建议

- 大体量数据使用独立磁盘或挂载目录，并通过 `SIQ_DATA_ROOT` 指向。
- 统一公告下载目录使用 `SIQ_REPORT_DOWNLOADS_ROOT` 或 `SIQ_MARKET_REPORT_DOWNLOADS_ROOT` 控制。
- PDF 解析目录使用 `SIQ_PDF2MD_DATA_DIR` 控制。
- 通用文档解析目录使用 `SIQ_DOCUMENT_PARSE_DATA_DIR` 控制。
- Wiki 根目录使用 `SIQ_WIKI_ROOT` 控制。
- Hermes 根目录使用 `SIQ_HERMES_HOME` 控制。

## 数据治理

财报研究数据通常包含原始披露文件、结构化指标、人工修正和生成报告。维护时应区分：

| 类型 | 建议 |
| --- | --- |
| 原始 PDF / HTML / ZIP | 保留来源、下载时间、文件 hash 和公司目录 |
| 解析产物 | 保留任务 ID、解析版本、质量报告和财务校验 |
| Wiki 产物 | 保留公司、年度、报告类型和生成时间 |
| Agent 报告 | 保留 JSON/HTML/Markdown、证据引用和人工复核状态 |
| 日志缓存 | 按需轮转，避免长期保存敏感 token 或用户输入 |
