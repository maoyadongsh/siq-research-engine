# SIQ 运行态数据目录

`data/` 保存 SIQ Research Engine 的本地运行态数据。该目录默认被 Git 忽略，除 README、`.gitkeep` 或小型恢复说明外，不应提交其中内容。

不要提交上传 PDF、解析结果、SQLite 数据库、聊天附件、模型缓存、日志、PostgreSQL 数据、Milvus 数据或 MinIO 数据。

## 当前子目录

| 路径 | 归属 | 内容 |
| --- | --- | --- |
| `data/backend` | `apps/api` | API 运行态，例如本地 SQLite、聊天附件、LLM 成本日志、运行设置 |
| `data/pdf-parser` | `apps/pdf-parser` | 上传 PDF、解析输出、任务库、缓存、日志、工作流任务记录 |
| `data/wiki` | API/Hermes/工作流 | 可选 SIQ 本地 Wiki 挂载或恢复副本 |
| `data/postgres` | 数据库运行/备份区 | 可选 PostgreSQL 本地数据或逻辑备份恢复区 |
| `data/milvus` | 向量库运行/备份区 | 可选 Milvus 数据或快照恢复区 |
| `data/sqlite` | 共享 SQLite 运行区 | 可选整合 SQLite 文件 |

## 恢复来源

多数运行态数据可从 `_external_assets` 恢复或重新生成：

| 数据 | 恢复来源 |
| --- | --- |
| Wiki | `_external_assets/wiki/wiki` |
| 公告下载文件 | `_external_assets/services/report-finder-service/downloads` |
| PDF 解析任务和结果历史 | `_external_assets` 中的保全副本或历史 `data/pdf-parser` 快照 |
| PostgreSQL 逻辑备份 | `_external_assets/postgres/exports` |
| Milvus 快照 | `_external_assets/milvus` |
| Hermes 运行态 | `_external_assets/hermes/hermes_home` |

## Git 策略

允许提交：

- `.gitkeep`
- 本 README
- 描述数据如何恢复的小型 manifest

禁止提交：

- `*.db`
- 上传 PDF
- 解析结果和输出目录
- 聊天附件
- 日志和缓存
- 原始数据库、向量库、对象存储快照
- 未明确提升为源码文档的生成报告
