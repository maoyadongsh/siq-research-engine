# SIQ API 聚合后端

`apps/api` 是 SIQ Research Engine 的 FastAPI 聚合后端，负责 Wiki/报告 API、PDF 解析溯源、工作流导入、公告下载代理、模型设置、系统状态、鉴权与工作区 API，以及基于 Hermes Runs API 的多智能体聊天。

该服务由 SIQ 后端迁移而来。新的配置应优先使用 `SIQ_*` 环境变量；部分 `SIQ_*` 和旧变量仅作为迁移期兼容回退保留。

## 职责边界

| 模块 | 职责 |
| --- | --- |
| Wiki/报告 API | 公司列表、报告列表、HTML 报告和报告文件读取 |
| Agent 聊天 | 将通用助手、分析、核查、跟踪、法务聊天代理到 Hermes Runs API |
| SSE 流式输出 | 推送 run id、增量文本、工具状态、推理片段、完成和错误事件 |
| 运行恢复 | 页面刷新后重连仍在运行的 Hermes run |
| 工作流导入 | 将 PDF 解析产物导入 Wiki、语义层和 PostgreSQL |
| PDF 溯源 | 代理 PDF 解析服务的表格、页面和 PDF 页面证据视图 |
| 下载管理 | 列出、打开和删除已下载公告 PDF |
| 设置与状态 | 保存模型设置并汇总下游服务健康状态 |
| 鉴权与工作区 | 本地用户、会话、工作区、审计和用量服务 |
| 宠物与成就 | 保留原型中的本地交互状态 |

## 启动

```bash
cd /home/maoyd/siq-research-engine/apps/api
export SIQ_AUTH_SECRET_KEY="$(openssl rand -hex 32)"
./start.sh
```

手动等价命令：

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

## 目录结构

```text
apps/api/
  main.py                  FastAPI app、生命周期、CORS、router 注册
  database.py              SQLModel engine/session
  models.py                本地 SQLModel 表
  schemas.py               Pydantic/API schema
  routers/                 API 路由
  services/                Hermes、设置、鉴权、路径、状态、引用链接等服务
  agents/tracking/         早期规则型跟踪模块
  scripts/                 初始化和维护脚本
  migrations/              SQL 迁移文件
  tests/                   API/服务测试
  start.sh                 SIQ 本地启动包装脚本
  pyproject.toml           Python 项目依赖
  uv.lock                  uv 锁定文件
```

运行态数据应落在 `data/backend`，不要放在源码目录旁。`apps/api/.siq`、`apps/api/data`、本地 `.db`、缓存和上传文件都应保持忽略。

## 关键环境变量

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `SIQ_PROJECT_ROOT` | 仓库根目录 | SIQ 主仓库根路径 |
| `SIQ_BACKEND_ROOT` | `apps/api` | API 源码根路径 |
| `SIQ_DATA_ROOT` | `data` | 运行态数据根目录 |
| `SIQ_BACKEND_DATA_ROOT` | `data/backend` | API 运行态数据 |
| `SIQ_WIKI_ROOT` | `data/wiki` | 包含 `companies/` 的 Wiki 根目录 |
| `SIQ_REPORT_FINDER_ROOT` | `services/report-finder` | 公告搜索下载服务根目录 |
| `SIQ_REPORT_DOWNLOADS_ROOT` | `$SIQ_REPORT_FINDER_ROOT/downloads` | 已下载 PDF 根目录 |
| `SIQ_PDF2MD_ROOT` | `apps/pdf-parser` | PDF 解析服务源码根目录 |
| `SIQ_PDF2MD_DATA_DIR` | `data/pdf-parser` | PDF 解析运行态数据根目录 |
| `SIQ_PDF_RESULTS_ROOT` | `data/pdf-parser/results` | PDF 解析结果产物 |
| `SIQ_HERMES_HOME` | `data/hermes/home` | Hermes 运行态根目录 |
| `SIQ_HERMES_PROFILES_ROOT` | `$SIQ_HERMES_HOME/profiles` | Hermes profiles 根目录 |
| `SIQ_DB_ROOT` | `db` | SIQ 数据库脚本根目录 |
| `SIQ_DB_IMPORT_SCRIPT` | `db/imports/import_document_full_to_postgres.py` | `document_full.json` PostgreSQL 导入脚本 |
| `SIQ_CONFIG_DIR` | `data/backend/.siq` 或兼容回退 | 模型设置存储目录 |
| `SIQ_AUTH_SECRET_KEY` | 必填 | JWT/session 密钥，至少 32 字符 |

路径默认值集中在 `services/path_config.py`。

## Hermes Profiles

Hermes profile 名称迁移期保持兼容：

| API 前缀 | Profile Key | 默认端口 |
| --- | --- | ---: |
| `/api/chat/*` | `siq_assistant` | `18642` |
| `/api/analysis/chat/*` | `analysis` / `siq_analysis` | `18651` |
| `/api/factchecker/chat/*` | `factchecker` / `siq_factchecker` | `18649` |
| `/api/tracking/chat/*` | `tracking` / `siq_tracking` | `18650` |
| `/api/legal/chat/*` | `legal` / `siq_legal` | `18652` |

Hermes 运行态默认保存在 `data/hermes/home`。源码侧只维护边界说明和 manifest，位置为 `agents/hermes`。

## API 分组

| 分组 | 前缀 |
| --- | --- |
| 健康检查 | `/health` |
| 通用助手聊天 | `/api/chat/*` |
| 专业助手聊天 | `/api/analysis/chat/*`, `/api/factchecker/chat/*`, `/api/tracking/chat/*`, `/api/legal/chat/*` |
| Wiki/报告文件 | `/api/wiki/*` |
| PDF 溯源 | `/api/source/*`, `/api/pdf_page/*` |
| PDF 工作流导入 | `/api/workflow/*` |
| 下载管理 | `/api/downloads/*` |
| 设置与系统状态 | `/api/settings/*`, `/api/system/*` |
| 鉴权、工作区、用户 | `/api/auth/*`, `/api/workspace/*`, 用户/管理员路由 |
| 评测 | `/api/eval/*` |

## 开发验证

```bash
cd /home/maoyd/siq-research-engine/apps/api
uv run python -m pytest tests
uv run python -c "import main; print(main.app.title)"
bash -n start.sh
```

## 迁移注意事项

- 不要新增指向旧源目录的硬编码路径。
- 文件系统路径优先使用 `SIQ_*` 变量和 `services/path_config.py`。
- `SIQ_*` 读取只作为迁移期兼容回退保留。
- 不要提交本地数据库、聊天上传、`.siq` 设置、缓存或虚拟环境。
