# SIQ 运行态数据目录

## 目录定位

`data/` 是 SIQ 的历史兼容运行态目录。它保留了大量服务当前默认依赖的本地数据路径，因此短期内仍然是系统的重要落点，但新的运行态设计应优先迁移到 `var/`。

## 主要内容

| 路径 | 内容 |
| --- | --- |
| `data/backend` | API 本地数据库、设置、附件、成本日志 |
| `data/pdf-parser` | PDF 上传、结果、任务库、缓存和日志 |
| `data/document-parser` | 文档上传、结果、任务库、缓存和日志 |
| `data/market-report-finder` | 官方披露下载文件与索引 |
| `data/wiki` | Wiki、报告、metrics、evidence、semantic |
| `data/hermes` | Hermes runtime home、会话与响应 |
| `data/postgres` | PostgreSQL 数据或备份放置区 |
| `data/milvus` | Milvus 数据或快照放置区 |
| `data/sqlite` | 小型 SQLite 数据库文件 |

## 当前最新状态

`data/` 仍是很多服务的默认兼容路径，尤其是 `data/wiki`、`data/market-report-finder/downloads`、`data/pdf-parser/results` 和 `data/hermes/home`。港股二级市场 MVP、US SEC package、JP/KR/EU package 和智能体产物都会在短期内继续依赖这里的历史目录。

治理要求是：事实资产可以从这里读取，但新增设计要逐步通过 `SIQ_RUNTIME_ROOT`、`SIQ_WIKI_ROOT`、`SIQ_REPORT_DOWNLOADS_ROOT` 等变量显式定位，避免让 `data/` 继续变成隐式全局状态。

## 与其他数据目录的边界

- `data/`：历史兼容运行态目录。
- `var/`：新增本地运行态推荐目录。
- `artifacts/`：构建、测试、评测和批处理生成产物。
- `datasets/`：可版本化稳定样本、fixtures 和小型示例。
- `eval_datasets/`：历史评测语料和回归集。

`data/` 不应该再被无限扩张成“所有东西都往里放”的总垃圾箱。

## 可提交与不可提交内容

可提交：

- 本 README
- 必要的 `.gitkeep`

不可提交：

- `*.db`
- 上传文件、下载披露文件、解析结果、日志、缓存
- 用户会话、聊天附件、成本日志
- PostgreSQL、Milvus、对象存储或模型缓存数据
- 含密钥、口令、个人信息或版权敏感内容的文件

## 运行或使用建议

- 若要逐步收口运行态，应优先引入 `SIQ_RUNTIME_ROOT` 和领域专属 `SIQ_*_DATA_DIR` 指向 `var/`。
- 大体量数据应考虑放外部磁盘、挂载目录或对象存储，而不是长期堆在仓库附近。
- 涉及原始披露、解析结果和 Agent 产物时，要区分事实资产、运行缓存和临时输出。

## 维护原则

- 兼容历史路径，但不把新设计全部继续堆到 `data/`。
- 当服务 README 提到运行态路径时，要明确说明这是“历史兼容默认值”还是“推荐新值”。
- 任何需要长期共享或评测的数据，都应整理后迁入 `datasets/` 或其他明确目录，而不是直接留在 `data/`。
