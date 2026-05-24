# FinSight / douge_ai_agent 项目总览

`douge_ai_agent` 是 FinSight 本地研究工作台的前后端整合项目。它把财报下载、PDF 解析、Wiki 研究资产、智能分析、事实核查、持续跟踪、法务合规和多智能体聊天统一到一个 React 工作台中。

当前项目不是单体应用，而是一个本地多服务系统：

- 主前端：React + Vite，负责所有页面和交互。
- 聚合后端：FastAPI，负责 Wiki 文件服务、聊天代理、工作流导入、设置、状态和本地 SQLite 数据。
- 外部 PDF 下载服务：`/home/maoyd/report-finder-service`，负责查询/下载 A 股公告 PDF。
- PDF 解析服务：`/home/maoyd/finsight/pdf2md_web`，负责 PDF 转 Markdown、结构化抽取、表格溯源和财务校验。
- Hermes Agent 网关：多个 profile 分别服务财报问答、分析、核查、跟踪、法务。

## 1. 当前结论

### 1.1 实际使用的前后端

| 层级 | 当前实际路径 | 默认端口 | 说明 |
| --- | --- | --- | --- |
| 主前端 | `/home/maoyd/finsight/finall_all_front_0516/front` | `5173` | 当前 UI 工作台，包含法务合规页和新版 agent 头像 |
| 聚合后端 | `/home/maoyd/finsight/backend` | `10081` | 当前主后端，Vite 重点代理到这里 |
| 旧前端 | `/home/maoyd/finsight/front` | 同源或静态端口 | 单页聊天 HTML，保留作兼容/调试，不是主 UI |
| PDF 下载服务 | `/home/maoyd/report-finder-service` | `8000` | 外部依赖项目，主前端 `/api/v1/*` 代理到它 |
| PDF 解析服务 | `/home/maoyd/finsight/pdf2md_web` | `5000` | 当前主目录内服务，主前端 `/pdfapi/*` 代理到它 |

### 1.2 当前支持的业务页面

| 前端路由 | 页面 | 主要依赖 | 说明 |
| --- | --- | --- | --- |
| `/` | 工作平台 | `/api/wiki/*` | 汇总 Wiki 公司、报告数量、近期任务 |
| `/search` | 搜索下载 | `report-finder-service :8000` + 聚合后端下载文件代理 | 查询财报公告、批量下载、查看/删除已下载 PDF |
| `/parse` | 财报解析 | `pdf2md_web :5000` + `/api/workflow/*` | 上传或选择已下载 PDF，解析、溯源、导入 Wiki/DB |
| `/analysis` | 智能分析 | `/api/wiki/*` + `/api/analysis/chat/*` | 展示 HTML 分析报告，右侧分析助手 |
| `/verify` | 事实核查 | `/api/wiki/*` + `/api/factchecker/chat/*` | 展示 HTML 核查报告，右侧核查助手 |
| `/tracking` | 持续跟踪 | `/api/wiki/*` + `/api/tracking/chat/*` | 展示 HTML 跟踪报告，右侧跟踪助手 |
| `/legal` | 法务合规 | `/api/wiki/*` + `/api/legal/chat/*` | 展示 HTML 法律意见书，右侧法务助手 |
| `/chat` | 财报问答助手 | `/api/chat/*` | 独立全屏普通问答 |
| `/settings` | 设置 | `/api/settings/*`、`/api/system/status` | 服务连接、本地/云端模型测试、系统状态 |
| `/help` | 帮助 | 前端本地 | 操作说明 |

## 2. 整体架构

```text
Browser http://localhost:5173
  |
  |-- /api/chat
  |-- /api/wiki
  |-- /api/analysis
  |-- /api/factchecker
  |-- /api/tracking
  |-- /api/legal
  |-- /api/settings
  |-- /api/system
  |-- /api/downloads
  |-- /api/workflow
  |-- /api/source, /api/pdf_page
  |       -> douge_ai_agent/backend FastAPI :10081
  |
  |-- /api/v1/*
  |       -> report-finder-service FastAPI :8000
  |
  `-- /pdfapi/*
          -> pdf2md_web Flask :5000 (/api/*)

FastAPI :10081
  |
  |-- SQLite: backend/data/pet.db
  |-- Wiki: /home/maoyd/wiki/companies
  |-- Downloads: /home/maoyd/report-finder-service/downloads
  |-- PDF2MD source proxy: http://127.0.0.1:5000
  |
  |-- Hermes finsight_assistant :8642
  |-- Hermes finsight_factchecker :8649
  |-- Hermes finsight_tracking :8650
  |-- Hermes finsight_analysis :8651
  `-- Hermes finsight_legal :8652
```

## 3. 端口与健康检查

| 服务 | 端口 | 必需性 | 健康检查 |
| --- | --- | --- | --- |
| Vite 主前端 | `5173` | 必需 | `curl -s http://localhost:5173` |
| 聚合后端 FastAPI | `10081` | 必需 | `curl -s http://localhost:10081/health` |
| PDF 下载服务 | `8000` | 搜索下载页必需 | `curl -s http://localhost:8000/health` |
| PDF 解析服务 | `5000` | 财报解析页必需 | `curl -s http://localhost:5000/api/health` |
| Hermes assistant | `8642` | 普通聊天必需 | `curl -s http://localhost:8642/health` |
| Hermes factchecker | `8649` | 事实核查助手必需 | `curl -s http://localhost:8649/health` |
| Hermes tracking | `8650` | 跟踪助手必需 | `curl -s http://localhost:8650/health` |
| Hermes analysis | `8651` | 分析助手必需 | `curl -s http://localhost:8651/health` |
| Hermes legal | `8652` | 法务助手必需 | `curl -s http://localhost:8652/health` |
| MinerU API | `8003` | PDF 解析上游 | 由 pdf2md `/api/health` 汇总 |
| VLM API | `8002` | PDF 解析上游 | 由 pdf2md `/api/health` 汇总 |
| 本地 Qwen/vLLM | `8004` | 模型设置测试/本地 Hermes 配置 | OpenAI-compatible `/v1/chat/completions` |

## 4. 推荐启动方式

### 4.1 一键启动部分服务

项目根目录提供 `start_all.sh`，会启动：

- PDF 下载服务 `:8000`
- 聚合后端 `:10081`
- 主前端 `:5173`

```bash
cd /home/maoyd/finsight
./start_all.sh
```

注意：这个脚本不会启动 Hermes gateway、PDF 解析服务、MinerU、VLM 或本地大模型。

### 4.2 手动启动聚合后端

```bash
cd /home/maoyd/finsight/backend
uv sync
WIKI_ROOT=/home/maoyd/wiki uv run uvicorn main:app --reload --host 0.0.0.0 --port 10081
```

### 4.3 手动启动主前端

```bash
cd /home/maoyd/finsight/finall_all_front_0516/front
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

### 4.4 启动 PDF 下载服务

```bash
cd /home/maoyd/report-finder-service
.venv/bin/python -m uvicorn report_finder_service.app:app --host 127.0.0.1 --port 8000
```

### 4.5 启动 PDF 解析服务

```bash
cd /home/maoyd/finsight/pdf2md_web
HOST=127.0.0.1 PORT=5000 ./run.sh
```

### 4.6 启动 Hermes Agent

不同业务助手依赖不同 Hermes profile。按需分别启动：

```bash
hermes profile use finsight_assistant
hermes gateway start

hermes profile use finsight_analysis
hermes gateway start

hermes profile use finsight_factchecker
hermes gateway start

hermes profile use finsight_tracking
hermes gateway start

hermes profile use finsight_legal
hermes gateway start
```

聚合后端调用 Hermes 的默认鉴权为：

```text
Authorization: Bearer change-me-local-dev
```

## 5. 目录结构

```text
douge_ai_agent/
  README.md                         # 本总览
  start_all.sh                      # 启动 8000 + 10081 + 5173
  backend/                          # 聚合 FastAPI 后端
  finall_all_front_0516/
    README.md                       # 前端容器目录说明
    front/                          # 当前主 React/Vite 前端
    fron_template/                  # 模板/历史目录
  front/                            # 旧单页聊天 HTML
  tools/                            # 头像生成、抠图、动画、对比图脚本
  test/                             # 占位测试包
  wiki/                             # 项目内历史归档/样例数据，不是主 Wiki 根目录
  agent-avatar-archive-20260520/    # 当前确认版 agent 头像归档
  agent-avatar-archive-20260520.tar.gz
```

主 Wiki 数据不在本项目目录内，而在：

```text
/home/maoyd/wiki
```

主 PDF 下载服务与主 PDF 解析服务也在本项目目录外：

```text
/home/maoyd/report-finder-service
/home/maoyd/finsight/pdf2md_web
```

## 6. 数据目录与产物

| 数据 | 默认路径 | 产生者 | 消费者 |
| --- | --- | --- | --- |
| Wiki 公司库 | `/home/maoyd/wiki/companies` | PDF 解析导入、Agent 生成报告、脚本 | 聚合后端、前端报告页 |
| 聚合后端 SQLite | `/home/maoyd/finsight/backend/data/pet.db` | 聚合后端 | 聊天历史、宠物状态、成就 |
| 下载 PDF | `/home/maoyd/report-finder-service/downloads` | PDF 下载服务 | 搜索下载页、财报解析页、聚合后端下载文件代理 |
| PDF 解析结果 | `/home/maoyd/finsight/pdf2md_web/results` | PDF 解析服务 | 财报解析页、工作流导入 |
| PDF 解析任务库 | `/home/maoyd/finsight/pdf2md_web/tasks.db` | PDF 解析服务 | 财报解析页、工作流导入 |
| 头像前端资源 | `finall_all_front_0516/front/public/pet` | tools 脚本/人工确认 | 当前 UI |
| 头像归档 | `agent-avatar-archive-20260520` | 人工确认后归档 | 资产恢复/迁移 |

## 7. Vite 代理分发

代理配置位于：

```text
/home/maoyd/finsight/finall_all_front_0516/front/vite.config.ts
```

关键规则：

| 前端请求前缀 | 转发目标 | 说明 |
| --- | --- | --- |
| `/api/chat` | `http://127.0.0.1:10081` | 普通问答 |
| `/api/wiki` | `http://127.0.0.1:10081` | Wiki 公司/报告 |
| `/api/analysis` | `http://127.0.0.1:10081` | 分析助手 |
| `/api/factchecker` | `http://127.0.0.1:10081` | 核查助手 |
| `/api/tracking` | `http://127.0.0.1:10081` | 跟踪助手和跟踪业务 API |
| `/api/legal` | `http://127.0.0.1:10081` | 法务助手 |
| `/api/settings` | `http://127.0.0.1:10081` | 模型配置 |
| `/api/system` | `http://127.0.0.1:10081` | 系统状态 |
| `/api/downloads` | `http://127.0.0.1:10081` | 已下载 PDF 文件代理 |
| `/api/workflow` | `http://127.0.0.1:10081` | PDF 结果导入 Wiki/DB |
| `/api/source`、`/api/pdf_page` | `http://127.0.0.1:10081` | PDF 溯源可读代理 |
| `/api/*` | `http://127.0.0.1:8000/*` | PDF 下载服务，去掉 `/api` 前缀 |
| `/pdfapi/*` | `http://127.0.0.1:5000/api/*` | PDF 解析服务，改写为 `/api` |

因为 `/api` 兜底会转发到 `8000`，新增聚合后端路由时必须在兜底 `/api` 之前添加更具体的代理前缀。

## 8. 聚合后端 API 总览

聚合后端所有 router 都挂在 `/api` 下。

### 8.1 聊天类 API

普通问答：

```text
POST   /api/chat
POST   /api/chat/stream
POST   /api/chat/stop
GET    /api/chat/active
GET    /api/chat/active/stream
GET    /api/chat/history
GET    /api/chat/sessions
POST   /api/chat/session
POST   /api/chat/session/{session_id}
DELETE /api/chat/session
```

业务 Agent 具备相同结构，只是前缀不同：

```text
/api/analysis/chat/*
/api/factchecker/chat/*
/api/tracking/chat/*
/api/legal/chat/*
```

### 8.2 Wiki 与报告

```text
GET    /api/wiki/companies/list
GET    /api/wiki/companies/recent-results
GET    /api/wiki/reports/search
GET    /api/wiki/companies/{company_dir}/reports
GET    /api/wiki/companies/{company_dir}/factchecks
GET    /api/wiki/companies/{company_dir}/trackings
GET    /api/wiki/companies/{company_dir}/legals
GET    /api/wiki/companies/{path}
DELETE /api/wiki/companies/{company_dir}/{result_type}/{filename}
```

可删除的 `result_type` 包括 `analysis`、`factcheck`、`tracking`、`legal`，且仅限 HTML 报告。

### 8.3 PDF 解析工作流

```text
GET  /api/workflow/task/{task_id}/status
GET  /api/workflow/task/{task_id}/preflight
POST /api/workflow/task/{task_id}/wiki-import
POST /api/workflow/task/{task_id}/semantic
POST /api/workflow/task/{task_id}/db-import
POST /api/workflow/task/{task_id}/run-remaining
GET  /api/workflow/job/{job_id}
```

用途：检查解析产物包、把报告入口和轻量 manifest 导入 Wiki，生成语义层，或将全量解析信息导入 PostgreSQL。Wiki 不作为全量解析产物仓库；全量信息保留在 `/home/maoyd/finsight/pdf2md_web/results/<task_id>` 和 PostgreSQL `pdf2md` schema 中。

`POST /api/workflow/task/{task_id}/semantic` 会先运行规则层脚本，再默认调用本地 Qwen3.6 做 LLM 语义增强。规则层输出仍在 `semantic/*.json`；模型增强层单独写入：

```text
/home/maoyd/wiki/companies/<company_id>/semantic/llm/<report_id>/
  enrichment.json
  business_profile.json
  claims.json
  risks.json
  events.json
  review_queue.json
  extraction_log.json
```

模型结果不覆盖规则事实；每条正式结果必须绑定已有 `segment_id` 和 `evidence_id`。

### 8.4 PDF 来源可读代理

```text
GET  /api/source/{task_id}/table/{table_index}
GET  /api/source/{task_id}/page/{page_number}
GET  /api/pdf_page/{task_id}/{page_number}
POST /api/source/{task_id}/table/{table_index}/correction
```

这些接口代理 `pdf2md_web :5000`，并在浏览器接受 HTML 时包装成可读页面。

### 8.5 设置、状态、下载、宠物

```text
GET    /api/settings/llm
PUT    /api/settings/llm
POST   /api/settings/llm/test
GET    /api/system/status
GET    /api/downloads/reports
GET    /api/downloads/report-file
DELETE /api/downloads/report-file
GET    /api/pet/state
POST   /api/pet/feed
POST   /api/pet/play
POST   /api/pet/rest
GET    /api/achievements
```

## 9. Agent 与头像资产

当前前端专业 agent 头像由 `AgentAvatar.tsx` 映射：

| Agent | API 前缀 | Hermes profile | 前端头像 |
| --- | --- | --- | --- |
| 分析助手 | `/api/analysis` | `finsight_analysis` | `public/pet/agent-drafts/finsight-analysis-avatar-animated-transparent.webp` |
| 核查助手 | `/api/factchecker` | `finsight_factchecker` | `public/pet/agent-drafts/finsight-factchecker-avatar-animated-transparent.webp` |
| 跟踪助手 | `/api/tracking` | `finsight_tracking` | `public/pet/agent-drafts/finsight-tracking-avatar-animated-transparent.webp` |
| 法务助手 | `/api/legal` | `finsight_legal` | `public/pet/agent-drafts/finsight-legal-avatar-animated-transparent.webp` |
| 普通财报助手 | `/api` | `finsight_assistant` | `public/pet/finsight-avatar-animated.webp` |

已确认版头像归档：

```text
/home/maoyd/finsight/agent-avatar-archive-20260520
/home/maoyd/finsight/agent-avatar-archive-20260520.tar.gz
```

## 10. 配置环境变量

| 变量 | 默认值 | 使用者 | 说明 |
| --- | --- | --- | --- |
| `WIKI_ROOT` | `/home/maoyd/wiki` | 聚合后端 | Wiki 根目录 |
| `REPORT_DOWNLOADS_ROOT` | `/home/maoyd/report-finder-service/downloads` | 聚合后端 | 已下载 PDF 根目录 |
| `PDF2MD_API_BASE` | `http://127.0.0.1:5000` | 聚合后端 | PDF 来源代理目标 |
| `PDF2MD_PROXY_TIMEOUT` | `60` | 聚合后端 | PDF 来源代理超时 |
| `PDF2MD_ROOT` | `/home/maoyd/finsight/pdf2md_web` | 工作流 | PDF 解析服务根目录 |
| `PDF_RESULTS_ROOT` | `/home/maoyd/finsight/pdf2md_web/results` | 工作流 | PDF 解析结果目录 |
| `WIKISET_ROOT` | `/home/maoyd/wiki/wikiset` | 工作流 | Wiki 构建脚本目录 |
| `SEMANTIC_SCRIPT` | `/home/maoyd/wiki/wikiset/extract_company_semantics.py` | 工作流 | 语义抽取脚本 |
| `LLM_SEMANTIC_SCRIPT` | `/home/maoyd/wiki/wikiset/llm_semantic_enrichment.py` | 工作流 | 本地模型语义增强脚本 |
| `LLM_SEMANTIC_ENABLED` | `true` | 工作流 | 是否默认生成 LLM 语义增强层 |
| `LLM_SEMANTIC_REQUIRED` | `true` | 工作流 | LLM 增强失败时是否让语义步骤失败 |
| `LLM_SEMANTIC_TIMEOUT` | `900` | 工作流 | LLM 增强脚本超时秒数 |
| `DB_IMPORT_SCRIPT` | `/home/maoyd/DB/PROGRAM/import_document_full_to_postgres.py` | 工作流 | PostgreSQL 导入脚本 |
| `FINSIGHT_CONFIG_DIR` | `backend/.finsight` | 设置页 | LLM 设置文件位置 |
| `FINSIGHT_LOCAL_LLM_BASE_URL` | `http://127.0.0.1:8004/v1` | 设置页、LLM 语义增强 | 本地 OpenAI-compatible 模型地址 |
| `FINSIGHT_LOCAL_LLM_MODEL` | `Qwen3.6-35B-A3B-FP8` | 设置页、LLM 语义增强 | 本地模型名 |

## 11. 常见问题

### 11.1 工作台公司列表为空

检查：

```bash
curl -s http://localhost:10081/api/wiki/companies/list
ls -la /home/maoyd/wiki/companies
```

如果 `/home/maoyd/wiki/companies` 不存在或为空，前端不会有报告可展示。

### 11.2 搜索下载页接口 404

确认 `report-finder-service :8000` 已启动。前端 `/api/v1/*` 不是发给聚合后端，而是被 Vite 转给 `8000`。

### 11.3 财报解析页接口失败

确认 `pdf2md_web :5000` 已启动，并且 MinerU/VLM 健康：

```bash
curl -s http://localhost:5000/api/health
```

### 11.4 右侧业务助手一直转圈

确认对应 Hermes gateway 已启动：

```bash
curl -s http://localhost:8651/health   # analysis
curl -s http://localhost:8649/health   # factchecker
curl -s http://localhost:8650/health   # tracking
curl -s http://localhost:8652/health   # legal
```

### 11.5 法务页能打开但助手报错

法务报告列表依赖 `/api/wiki/companies/{company}/legals`，法务聊天依赖 `/api/legal/chat/stream` 和 Hermes `finsight_legal :8652`。报告展示正常不代表 Hermes 法务助手也已启动。

### 11.6 修改 Vite 代理后无效

Vite 代理配置只在 dev server 启动时读取。修改 `vite.config.ts` 后需要重启 `npm run dev`。

## 12. 文档入口

| 文档 | 内容 |
| --- | --- |
| `backend/README.md` | 聚合后端详细说明 |
| `finall_all_front_0516/README.md` | 当前前端容器目录说明 |
| `finall_all_front_0516/front/README.md` | 当前主前端详细说明 |
| `front/README.md` | 旧单页聊天 HTML |
| `tools/README.md` | 头像资产生成和归档工具 |
| `wiki/README.md` | 项目内 wiki 归档/样例目录说明 |
| `test/README.md` | 占位测试包说明 |
| `backend/hermes-api-multi-turn.md` | Hermes API 多轮对话记录 |
| `/home/maoyd/report-finder-service/README.md` | 外部 PDF 下载服务 |
| `/home/maoyd/finsight/pdf2md_web/README.md` | 当前主目录内 PDF 解析服务 |
