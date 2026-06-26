# SIQ Research Engine

SIQ Research Engine 是从旧原型迁移而来的本地金融研究工作台。它把公告搜索下载、PDF 解析、证据沉淀、分析报告、事实核查、持续跟踪、法务合规和多智能体聊天整合在一个本地多服务系统中。

当前仓库已经进入 SIQ 维护布局。旧项目目录只作为只读对照和恢复参照，新的开发、路径修正和文档更新都应落在本仓库：

```text
/home/maoyd/siq-research-engine
```

## 当前布局

| 路径 | 职责 |
| --- | --- |
| `apps/api` | FastAPI 聚合后端：Wiki/报告 API、工作流导入、PDF 溯源、鉴权、设置、系统状态、Agent 聊天代理 |
| `apps/web` | React/Vite 前端工作台 |
| `apps/pdf-parser` | Flask PDF 解析与人工复核服务 |
| `agents/hermes` | Hermes profile 边界说明和 manifest；运行态仍保存在外部资产区 |
| `db` | DDL、DML、迁移和导入工具 |
| `infra` | Docker、Supervisor、环境变量样例和模型服务启动脚本 |
| `docs/operations` | 本地开发、外部资产、数据恢复等操作说明 |
| `docs` | 当前仍需维护的操作文档和模型集成说明 |
| `data` | 本地运行态数据，默认不纳入 Git |

## 运行服务

| 服务 | 路径 | 默认端口 | 说明 |
| --- | --- | ---: | --- |
| Web 工作台 | `apps/web` | `15173` | React/Vite UI |
| API 聚合后端 | `apps/api` | `18081` | 主后端 API |
| PDF 解析服务 | `apps/pdf-parser` | `15000` | PDF 转 Markdown/JSON、质量报告和溯源 |
| 公告搜索下载 | `services/report-finder` | `18000` | 公告搜索/下载服务 |
| Hermes profiles | `data/hermes/home/profiles` | `18642`, `18649`, `18650`, `18651`, `18652` | profile 名称迁移期保持兼容 |

## 数据与资产边界

`_external_assets` 是迁移保全区，不是应用源码目录。它保存 Wiki、数据库脚本和备份、Hermes 运行态、公告搜索服务、法务文档、MinerU 运行环境、PostgreSQL/Milvus/MinIO 快照等。不要把整个目录搬进源码区；只在确认需要维护时，提升单个脚本、模板或 manifest。

`data` 是本地运行态目录，可能包含 SQLite、上传 PDF、PDF 解析结果、聊天附件、日志和缓存。除 README、`.gitkeep` 或小型恢复 manifest 外，不应提交其中内容。

## 快速启动

一键入口会启动公告搜索、API、PDF 解析和 Web：

```bash
cd /home/maoyd/siq-research-engine
export SIQ_AUTH_SECRET_KEY="$(openssl rand -hex 32)"
./start_all.sh
```

打开：

```text
http://localhost:15173
```

`start_all.sh` 会设置 SIQ 默认路径，包括：

- `SIQ_PROJECT_ROOT`
- `SIQ_WIKI_ROOT`
- `SIQ_REPORT_FINDER_ROOT`
- `SIQ_REPORT_DOWNLOADS_ROOT`
- `SIQ_PDF2MD_ROOT`
- `SIQ_PDF2MD_DATA_DIR`
- `SIQ_DB_ROOT`
- `SIQ_HERMES_HOME`
- `SIQ_HERMES_PROFILES_ROOT`
- `SIQ_MINERU_VENV`

它不会启动 MinerU API、VLM、vLLM 等模型服务；这些主机相关服务记录在 `infra/model-services`。

## 手动启动

API 聚合后端：

```bash
cd /home/maoyd/siq-research-engine/apps/api
export SIQ_AUTH_SECRET_KEY="$(openssl rand -hex 32)"
./start.sh
```

Web 前端：

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm install
npm run dev -- --host 0.0.0.0 --port 15173
```

PDF 解析服务：

```bash
cd /home/maoyd/siq-research-engine/apps/pdf-parser
./run.sh
```

公告搜索下载服务：

```bash
cd /home/maoyd/siq-research-engine/services/report-finder
uv run uvicorn report_finder_service.app:app --host 127.0.0.1 --port 18000
```

## 健康检查

```bash
curl -s http://localhost:15173
curl -s http://localhost:18081/health
curl -s http://localhost:15000/api/health
curl -s http://localhost:18000/health
```

Hermes profile gateway 按需单独启动后检查：

```bash
curl -s http://localhost:18642/health  # assistant
curl -s http://localhost:18651/health  # analysis
curl -s http://localhost:18649/health  # factchecker
curl -s http://localhost:18650/health  # tracking
curl -s http://localhost:18652/health  # legal
```

## 开发验证

API：

```bash
cd /home/maoyd/siq-research-engine/apps/api
uv run python -m pytest tests
```

PDF 解析服务：

```bash
cd /home/maoyd/siq-research-engine/apps/pdf-parser
python3 -m pytest tests
```

Web：

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm run build
```

Shell 入口：

```bash
cd /home/maoyd/siq-research-engine
bash -n start_all.sh
bash -n apps/api/start.sh
bash -n apps/pdf-parser/run.sh
```

## 文档入口

| 文档 | 用途 |
| --- | --- |
| `docs/operations/local-development.md` | 本地开发和启动流程 |
| `docs/operations/data-restore.md` | 从备份或旧只读来源恢复数据 |
| `docs/Gemma4_Deployment_and_Invocation.md` | Gemma4 部署与调用说明 |
| `docs/Gemma4_SIQ_Technical_Report.md` | Gemma4 SIQ 技术报告 |
| `docs/gemma4_deployment_and_integration.md` | Gemma4 集成记录 |
| `apps/api/README.md` | API 聚合后端说明 |
| `apps/web/README.md` | Web 前端说明 |
| `apps/pdf-parser/README.md` | PDF 解析服务说明 |

## 迁移完成标准

迁移完成必须同时满足：

- 根 README 和活跃服务 README 均描述 SIQ 路径和启动方式。
- `apps/*`、`db/*`、`infra/*`、`agents/*`、`docs/*` 是明确的维护面。
- `_external_assets` 和 `data` 被记录为外部资产或运行态数据，并默认忽略。
- 活跃启动命令不依赖旧源目录。
- 旧 SIQ 路径只出现在兼容回退、历史文档或测试夹具中。
- 后端、前端、PDF 解析服务的基础 smoke check 通过，或失败项有明确后续记录。
- Git 工作区在有意迁移提交后恢复干净。
