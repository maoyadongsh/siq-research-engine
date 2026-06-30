# SIQ 本地运行态目录

`var/` 用于承载本机运行时状态。该目录默认被 Git 忽略，除本 README 和必要的 `.gitkeep` 外，不提交任何业务数据、缓存、数据库文件或用户上传文件。

建议把新增运行态默认落到这里，而不是继续扩大 `data/` 的职责。兼容期内，现有服务仍可通过 `SIQ_*` 环境变量继续使用旧 `data/` 路径。

## 推荐子目录

| 路径 | 内容 |
| --- | --- |
| `var/api` | API 本地数据库、设置、附件、审计和成本日志 |
| `var/pdf-parser` | PDF 上传、任务库、解析结果、缓存和日志 |
| `var/document-parser` | 通用文档上传、任务库、解析结果、缓存和日志 |
| `var/market-report-finder` | 官方披露下载文件、下载索引和临时 manifest |
| `var/hermes` | Hermes 会话、响应、网关运行态和日志 |
| `var/wiki` | 本机 Wiki / evidence package / 生成报告工作区 |
| `var/db` | 本地 SQLite、PostgreSQL 逻辑备份或临时数据库文件 |
| `var/logs` | 服务日志 |
| `var/cache` | HTTP、embedding、LLM、解析缓存 |
| `var/runtimes` | 本机虚拟环境、模型运行时或外部工具运行目录 |

## 约束

- 不提交 `*.db`、上传文件、下载披露文件、解析产物、日志、缓存和模型权重。
- 需要长期保留或分享的数据应进入外部存储、对象存储、备份系统，或整理为小型可复现 fixture 后放到 `datasets/`。
- 修改服务默认路径时，优先引入 `SIQ_RUNTIME_ROOT` 或领域专属 `SIQ_*_DATA_DIR`，避免硬编码绝对路径。
