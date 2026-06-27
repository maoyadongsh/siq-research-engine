# SIQ API 聚合后端

`apps/api` 是 SIQ Research Engine 的 FastAPI 聚合后端。它把 Web 工作台、PDF 解析服务、公告下载服务、Wiki 文件系统、Hermes 智能体和本地用户系统连接在一起，提供统一的鉴权、路由、流式对话、溯源链接和工作流编排能力。

## 设计定位

API 后端不是单纯的 CRUD 服务，而是研究链路的“控制面”：

- 对前端提供稳定的 `/api/*` 入口。
- 对下游服务封装健康检查、路径解析和错误处理。
- 对 Hermes Runs API 提供会话、附件、SSE 流式输出、停止和恢复能力。
- 对报告文件、PDF 页码、表格证据和下载文件做统一访问控制。
- 对 `document_full.json` 入 Wiki、语义索引和 PostgreSQL 的流程做编排。

## 核心能力

| 模块 | 能力 | 价值 |
| --- | --- | --- |
| 鉴权与用户 | 登录、注册、会话、权限、用户审批、审计日志 | 支撑多用户本地工作台和管理后台 |
| Wiki/报告 API | 公司列表、报告搜索、HTML 报告读取、报告删除 | 前端报告页的统一数据来源 |
| Agent 代理 | 通用助手、分析、核查、跟踪、法务五类 Agent | 将专业智能体接入同一会话体验 |
| SSE 流式输出 | run id、增量文本、工具状态、推理片段、错误和完成事件 | 支持长任务可视化和中断恢复 |
| PDF 溯源 | 表格、页面、PDF 页图和短期签名访问 token | 让报告引用能跳回原始披露页 |
| 工作流导入 | Wiki 导入、语义索引、数据库入库、预检和续跑 | 把解析产物沉淀为可复用证据层 |
| 下载管理 | 已下载 PDF 列表、打开、删除和工作区链接 | 连接搜索下载与解析复核 |
| 设置与状态 | LLM 设置、系统健康、模型配置测试 | 提供可运维的本地系统面板 |
| 工作区 | 项目、资产、PDF 代理和个人工作区摘要 | 支撑用户按项目组织研究资产 |

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
curl -s http://localhost:18081/api/wiki/companies/list
curl -s http://localhost:18081/api/system/status
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
| 下载管理 | `/api/downloads/*` | 已下载公告 PDF 管理 |
| 工作流 | `/api/workflow/*` | Wiki、语义层、数据库导入任务 |
| 工作区 | `/api/workspace/*`, `/api/pdf/*` | 用户工作区和 PDF 代理 |
| 设置状态 | `/api/settings/*`, `/api/system/*` | 模型设置和系统状态 |
| 评测 | `/api/eval/*` | E2E 评测入口 |

## Hermes Profiles

| 前端功能 | Profile | 默认端口 | API 前缀 |
| --- | --- | ---: | --- |
| 问答助手 | `siq_assistant` | `18642` | `/api/chat/*` |
| 智能分析 | `siq_analysis` | `18651` | `/api/analysis/*` |
| 事实核查 | `siq_factchecker` | `18649` | `/api/factchecker/*` |
| 持续跟踪 | `siq_tracking` | `18650` | `/api/tracking/*` |
| 法务合规 | `siq_legal` | `18652` | `/api/legal/*` |

后端通过 `services/hermes_client.py` 和 `services/agent_chat_runtime.py` 代理 Hermes Runs API，并处理附件上传、会话映射、流式事件和运行恢复。

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
  uv.lock                  uv 锁定文件
```

## 关键环境变量

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `SIQ_PROJECT_ROOT` | 仓库根目录 | 项目根路径 |
| `SIQ_BACKEND_ROOT` | `apps/api` | API 源码目录 |
| `SIQ_DATA_ROOT` | `data` | 运行态数据根目录 |
| `SIQ_BACKEND_DATA_ROOT` | `data/backend` | API 本地数据库、附件、设置和日志 |
| `SIQ_WIKI_ROOT` | `data/wiki` | 公司 Wiki 与报告产物 |
| `SIQ_REPORT_DOWNLOADS_ROOT` | `data/market-report-finder/downloads` | 公告 PDF 下载目录 |
| `SIQ_PDF2MD_API_BASE` | `http://127.0.0.1:15000` | PDF 解析服务地址 |
| `SIQ_REPORT_FINDER_BASE` | `http://127.0.0.1:18000` | 公告搜索下载服务地址 |
| `SIQ_HERMES_HOME` | `data/hermes/home` | Hermes 运行态根目录 |
| `SIQ_HERMES_PROFILES_ROOT` | `$SIQ_HERMES_HOME/profiles` | Hermes profiles 根目录 |
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

- 新增 API 时优先放入明确的 router，并在前端 Vite 兜底代理之前配置具体前缀。
- 文件路径统一通过 `services/path_config.py` 或环境变量解析。
- 不提交本地 `.db`、`.siq`、聊天附件、上传文件、缓存和虚拟环境。
- 涉及 PDF 溯源的接口应保留任务归属校验和短期签名机制。
- 涉及 Agent 的接口应保持可停止、可恢复、可审计的流式事件语义。
