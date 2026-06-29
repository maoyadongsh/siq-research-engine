# SIQ API 聚合后端

`apps/api` 是 SIQ Research Engine 的 FastAPI 控制面。它把 Web 工作台、PDF 解析服务、通用文档解析服务、公告下载服务、多市场规则服务、Wiki 文件系统、PostgreSQL 入库脚本、Milvus 入库脚本、Hermes 智能体和本地用户系统连接在一起，向前端提供统一的 `/api/*` 鉴权入口。

## 设计定位

API 后端不只是 CRUD 服务，而是研究链路的编排层：

- 对前端提供稳定的鉴权 API、工作流 API、报告 API 和 SSE Agent API。
- 对下游解析/下载/规则服务封装健康检查、路径解析、任务归属和错误处理。
- 对报告文件、PDF 页码、文档 artifact、市场 evidence package 和下载文件做访问控制。
- 对 Hermes Runs API 提供会话、附件、流式输出、停止、恢复和成本记录。
- 对 PDF/文档/市场 evidence package 的 Wiki、PostgreSQL、Milvus 工作流做预检、执行和状态汇总。

## 核心能力

| 模块 | 能力 | 价值 |
| --- | --- | --- |
| 鉴权与用户 | 登录、注册、会话、权限、用户审批、审计日志 | 支撑多用户本地工作台 |
| 用量与资产 | 文档解析额度、用户 artifact 归属、个人工作区 | 防止任务越权访问，支撑用户资产列表 |
| Wiki/报告 API | 公司列表、报告搜索、HTML/JSON/Markdown 报告读取、报告删除 | 前端报告页的统一数据来源 |
| Agent 代理 | 通用助手、分析、核查、跟踪、法务五类 Agent | 将专业智能体接入同一会话体验 |
| SSE 流式输出 | run id、增量文本、工具状态、推理片段、错误和完成事件 | 长任务可视化和中断恢复 |
| PDF 溯源 | 表格、页面、PDF 页图和短期签名访问 token | 报告引用能跳回原始披露页 |
| 通用文档代理 | 上传/URL/MinerU 导入、artifact、source map、表格关系、Schema 抽取 | 通用解析服务纳入用户鉴权和额度 |
| 市场报告代理 | CN/HK/US/EU/JP/KR 下载、SEC 上传、evidence package、后台 job、评测 | 多市场披露链路统一进入 Web |
| 工作流导入 | Wiki 导入、语义 chunks、Milvus、PostgreSQL 入库、预检和续跑 | 把文件型产物沉淀为可复用证据层 |
| 设置与状态 | LLM 设置、系统健康、模型配置测试 | 提供可运维的本地系统面板 |

## 启动

```bash
cd /home/maoyd/siq-research-engine/apps/api
export SIQ_AUTH_SECRET_KEY="$(openssl rand -hex 32)"
./start.sh
```

手动启动：

```bash
cd /home/maoyd/siq-research-engine/apps/api
uv sync
SIQ_AUTH_SECRET_KEY="$(openssl rand -hex 32)" \
uv run uvicorn main:app --reload --host 0.0.0.0 --port 18081
```

健康检查：

```bash
curl -s http://localhost:18081/health
curl -s http://localhost:18081/api/system/status
curl -s http://localhost:18081/api/wiki/companies/list
```

## 路由分组

| 分组 | 前缀 | 说明 |
| --- | --- | --- |
| 健康检查 | `/health` | 服务存活状态 |
| 鉴权 | `/api/auth/*` | 登录、注册、当前用户、用户管理、审计 |
| Wiki | `/api/wiki/*` | 公司、报告、核查、跟踪、法务产物读取 |
| 通用聊天 | `/api/chat/*` | 通用助手、附件、历史会话、运行控制 |
| 专业 Agent | `/api/analysis/*`, `/api/factchecker/*`, `/api/tracking/*`, `/api/legal/*` | 专业智能体聊天代理 |
| PDF 溯源 | `/api/source/*`, `/api/pdf_page/*`, `/api/source_access/*` | 表格、页面、PDF 页图和签名访问 |
| PDF/工作区 | `/api/pdf/*`, `/api/workspace/*`, `/api/downloads/*` | 用户工作区、已下载报告和 PDF 代理 |
| 通用文档 | `/api/documents/*` | 文档解析服务鉴权代理、任务归属、额度、artifact、抽取 |
| 工作流 | `/api/workflow/*` | PDF/文档 Wiki、语义层、数据库导入任务 |
| 市场报告 | `/api/market-reports/*`, `/api/us-sec/*`, `/api/jobs/*`, `/api/markets*` | 多市场 evidence package、SEC 案例集、后台 job |
| 设置状态 | `/api/settings/*`, `/api/system/*` | 模型设置和系统状态 |
| 评测 | `/api/eval/*` | E2E 评测入口 |

## 下游服务

| 下游 | 默认地址 | 相关环境变量 |
| --- | --- | --- |
| PDF 解析服务 | `http://127.0.0.1:15000` | `SIQ_PDF2MD_API_BASE` |
| 通用文档解析服务 | `http://127.0.0.1:15010` | `SIQ_DOCUMENT_PARSER_API_BASE`, `SIQ_DOCUMENT_PARSER_ACCESS_TOKEN` |
| 统一公告下载服务 | `http://127.0.0.1:18000` | `SIQ_REPORT_FINDER_BASE` |
| 多市场规则服务 | `http://127.0.0.1:18020` | `SIQ_MARKET_REPORT_RULES_BASE` |
| Hermes profiles | `18642/18649/18650/18651/18652` | `SIQ_HERMES_*_PORT`, `SIQ_HERMES_HOME` |
| Wiki | `data/wiki` | `SIQ_WIKI_ROOT` |
| 下载目录 | `data/market-report-finder/downloads` | `SIQ_REPORT_DOWNLOADS_ROOT` |

## Hermes Profiles

| 前端功能 | Profile | 默认端口 | API 前缀 |
| --- | --- | ---: | --- |
| 问答助手 | `siq_assistant` | `18642` | `/api/chat/*` |
| 智能分析 | `siq_analysis` | `18651` | `/api/analysis/*` |
| 事实核查 | `siq_factchecker` | `18649` | `/api/factchecker/*` |
| 持续跟踪 | `siq_tracking` | `18650` | `/api/tracking/*` |
| 法务合规 | `siq_legal` | `18652` | `/api/legal/*` |

后端通过 `services/hermes_client.py` 和 `services/agent_chat_runtime.py` 代理 Hermes Runs API，并处理附件上传、会话映射、流式事件、停止和运行恢复。

## 市场报告控制面

`routers/market_reports.py` 是多市场财报链路的主要入口，负责：

- 透传官方报告搜索下载服务。
- 上传 SEC/市场文件并生成下载元数据。
- 构建 US/HK/EU/JP/KR evidence package。
- 浏览 package 列表、详情、质量报告、文件和 evidence。
- 调用市场专属入库脚本写入 PostgreSQL。
- 调用 Milvus 入库脚本写入市场 evidence chunks。
- 启动和查询后台 job，避免长任务阻塞 HTTP 请求。

package 根目录默认位于：

```text
data/wiki/us_sec
data/wiki/hk_reports
data/wiki/eu_reports
data/wiki/jp_reports
data/wiki/kr_reports
```

## 通用文档控制面

`routers/document_parser.py` 将 `apps/document-parser` 纳入统一鉴权和用户资产体系：

- `/api/documents/tasks` 支持多文件上传或 URL 提交。
- `/api/documents/import/mineru` 支持导入已有 MinerU 输出目录。
- `/api/documents/artifact/*`、`source/*`、`figures/*`、`table-relations/*` 代理标准产物。
- `/api/documents/extract/*` 支持模板或自定义 JSON Schema 抽取。
- 创建任务时记录 `UserArtifact`，非管理员只能访问自己的文档任务。
- `DOCUMENT_PARSE_EVENT` 接入每日额度控制。

## 目录结构

```text
apps/api/
  main.py                  FastAPI app、生命周期、CORS、router 注册
  database.py              SQLModel engine/session
  models.py                本地 SQLModel 表
  schemas.py               Pydantic/API schema
  routers/                 API 路由
  services/                鉴权、Hermes、设置、路径、状态、引用链接等服务
  agents/tracking/         跟踪业务模型与规则入口
  scripts/                 初始化和维护脚本
  tests/                   API 与服务测试
  start.sh                 本地启动脚本
  pyproject.toml           Python 依赖
```

## 关键环境变量

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `SIQ_PROJECT_ROOT` | 仓库根目录 | 项目根路径 |
| `SIQ_BACKEND_ROOT` | `apps/api` | API 源码目录 |
| `SIQ_DATA_ROOT` | `data` | 运行态数据根目录 |
| `SIQ_BACKEND_DATA_ROOT` | `data/backend` | API 本地数据库、附件、设置和日志 |
| `SIQ_WIKI_ROOT` | `data/wiki` | 公司 Wiki 与报告产物 |
| `SIQ_REPORT_DOWNLOADS_ROOT` | `data/market-report-finder/downloads` | 官方披露文件下载目录 |
| `SIQ_PDF2MD_API_BASE` | `http://127.0.0.1:15000` | PDF 解析服务地址 |
| `SIQ_DOCUMENT_PARSER_API_BASE` | `http://127.0.0.1:15010` | 通用文档解析服务地址 |
| `SIQ_REPORT_FINDER_BASE` | `http://127.0.0.1:18000` | 公告搜索下载服务地址 |
| `SIQ_MARKET_REPORT_RULES_BASE` | `http://127.0.0.1:18020` | 多市场规则服务地址 |
| `SIQ_HERMES_HOME` | `data/hermes/home` | Hermes 运行态根目录 |
| `SIQ_DB_ROOT` | `db` | 数据库脚本根目录 |
| `SIQ_CONFIG_DIR` | `data/backend/.siq` | LLM 设置存储目录 |
| `SIQ_AUTH_SECRET_KEY` | 无 | JWT/session 密钥，至少 32 字符 |

## 开发验证

```bash
cd /home/maoyd/siq-research-engine/apps/api
uv run python -m pytest tests
uv run python -c "import main; print(main.app.title)"
bash -n start.sh
```

## 维护原则

- 新增 API 时优先放入明确 router，并同步前端代理中的具体前缀。
- 文件路径统一通过 `services/path_config.py` 或环境变量解析。
- 不提交本地 `.db`、`.siq`、聊天附件、上传文件、缓存和虚拟环境。
- 涉及 PDF、文档 artifact 或市场 package 的接口必须保留任务归属校验和路径白名单。
- 涉及 Agent 的接口应保持可停止、可恢复、可审计的流式事件语义。
- 后台 job 返回命令时必须隐藏数据库口令、API key 等敏感参数。
