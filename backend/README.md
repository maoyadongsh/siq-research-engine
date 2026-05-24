# FinSight 聚合后端 README

本目录是 `douge_ai_agent` 当前主后端，基于 FastAPI。它不是 PDF 下载或 PDF 解析服务本体，而是前端工作台的聚合入口：统一提供 Wiki 文件服务、Hermes 多智能体聊天代理、PDF 溯源代理、解析结果导入工作流、系统状态、模型连接设置、已下载 PDF 文件管理，以及本地宠物/成就/聊天历史 SQLite 数据。

## 1. 后端职责

| 职责 | 说明 |
| --- | --- |
| Wiki 文件服务 | 读取 `/home/maoyd/wiki/companies`，返回公司列表、报告列表、HTML/JSON/MD/图片文件 |
| Agent 聊天代理 | 对接 Hermes Runs API，支持 assistant、analysis、factchecker、tracking、legal 五个 profile |
| SSE 流式输出 | 前端聊天通过 `/chat/stream` 接收增量、工具状态、推理片段和完成事件 |
| 活跃 Run 恢复 | 页面刷新后可通过 `/chat/active` 和 `/chat/active/stream` 接回运行中的 Hermes run |
| 聊天历史 | 用 SQLite 保存各 profile 的多轮历史，下一轮调用会带 `conversation_history` |
| PDF 解析工作流 | 把 `pdf2md_web/results/<task_id>` 导入 Wiki、生成语义层、导入 PostgreSQL |
| PDF 来源代理 | 将 pdf2md 的表格/页面/PDF 页源信息包装成可读 HTML |
| 已下载 PDF 管理 | 列出、打开、删除 report-finder-service 下载目录里的 PDF |
| 设置与状态 | 保存/测试 OpenAI-compatible 模型配置，汇总下游服务健康状态 |
| 宠物/成就系统 | 保存宠物状态、动作日志和成就进度 |

## 2. 技术栈

| 类型 | 当前实现 |
| --- | --- |
| Web 框架 | FastAPI |
| ASGI 服务 | uvicorn |
| 包管理 | uv |
| 数据库 | SQLite + SQLModel，同步与异步 session 共用同一文件 |
| HTTP 客户端 | httpx |
| SSE | sse-starlette |
| 外部 Agent | Hermes Runs API |

依赖定义见 `pyproject.toml`：

```toml
fastapi
uvicorn[standard]
sqlmodel
httpx[socks]
aiosqlite
sse-starlette
greenlet
```

## 3. 启动

```bash
cd /home/maoyd/finsight/backend
uv sync
WIKI_ROOT=/home/maoyd/wiki uv run uvicorn main:app --reload --host 0.0.0.0 --port 10081
```

健康检查：

```bash
curl -s http://localhost:10081/health
curl -s http://localhost:10081/api/wiki/companies/list
curl -s http://localhost:10081/api/system/status
```

## 4. 目录结构

```text
backend/
  main.py                         FastAPI 应用入口、CORS、router 注册
  database.py                     SQLite/SQLModel engine 和 session
  models.py                       PetState、ChatMessage、Achievement、InteractionLog
  schemas.py                      API 响应/请求 Pydantic 模型
  seed.py                         初始化宠物和成就种子数据
  pyproject.toml                  Python 依赖
  uv.lock                         uv 锁文件
  hermes-api-multi-turn.md         Hermes 多轮对话记录
  PROJECT_DESIGN.md               历史设计说明
  routers/
    chat.py                       普通财报助手 /api/chat/*
    analysis.py                   分析助手 /api/analysis/chat/*
    factchecker.py                核查助手 /api/factchecker/chat/*
    tracking_agent.py             跟踪聊天助手 /api/tracking/chat/*
    legal.py                      法务助手 /api/legal/chat/*
    wiki.py                       Wiki 公司/报告/文件服务
    workflow.py                   PDF 解析产物导入 Wiki/语义层/DB
    source.py                     pdf2md 来源表格/页面/PDF 页代理
    downloads.py                  已下载 PDF 列表/打开/删除
    settings.py                   LLM 设置读取、保存、连接测试
    system.py                     下游服务健康状态汇总
    pet.py                        宠物状态与互动
    achievements.py               成就列表
    tracking.py                   早期跟踪业务 REST API
  services/
    agent_chat_runtime.py          Hermes run 创建、流式消费、历史、停止和恢复
    hermes_client.py               Hermes Runs API 客户端与 profile 端口映射
    hermes_model_control.py        通过聊天指令切换 Hermes profile 的模型配置
    llm_settings.py                设置页 OpenAI-compatible 配置保存和测试
    system_status.py               系统状态采集
    citation_links.py              回复中的 PDF 来源链接补齐
    achievement_checker.py         成就进度计算
  agents/tracking/                早期规则型持续跟踪模块
  data/pet.db                     运行时 SQLite，首次启动自动创建
```

## 5. 环境变量

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `WIKI_ROOT` | `/home/maoyd/wiki` | Wiki 根目录，`wiki.py` 读取其 `companies/` |
| `REPORT_DOWNLOADS_ROOT` | `/home/maoyd/report-finder-service/downloads` | 已下载 PDF 根目录 |
| `PDF2MD_API_BASE` | `http://127.0.0.1:5000` | `source.py` 代理的 pdf2md 服务 |
| `PDF2MD_PROXY_TIMEOUT` | `60` | pdf2md 来源代理超时秒数 |
| `PDF2MD_ROOT` | `/home/maoyd/finsight/pdf2md_web` | 工作流读取 pdf2md 任务库 |
| `PDF_RESULTS_ROOT` | `/home/maoyd/finsight/pdf2md_web/results` | 工作流读取解析产物 |
| `WIKISET_ROOT` | `/home/maoyd/wiki/wikiset` | Wiki 构建脚本目录 |
| `WIKI_REBUILD_SCRIPT` | `$WIKISET_ROOT/rebuild_wiki_v2.py` | task 导入 Wiki 的构建脚本 |
| `SEMANTIC_SCRIPT` | `$WIKISET_ROOT/extract_company_semantics.py` | 语义层抽取脚本 |
| `LLM_SEMANTIC_SCRIPT` | `$WIKISET_ROOT/llm_semantic_enrichment.py` | 本地模型语义增强脚本 |
| `LLM_SEMANTIC_ENABLED` | `true` | 是否默认生成 `semantic/llm/<report_id>/` |
| `LLM_SEMANTIC_REQUIRED` | `true` | LLM 增强失败时是否让语义步骤失败 |
| `LLM_SEMANTIC_TIMEOUT` | `900` | LLM 增强脚本超时秒数 |
| `DB_IMPORT_SCRIPT` | `/home/maoyd/DB/PROGRAM/import_document_full_to_postgres.py` | document_full 导入 PostgreSQL 的脚本 |
| `DB_CONFIG_PY` | `/home/maoyd/finance_evidence_poc/DB/DML/postgresql_connect.py` | PostgreSQL 连接配置脚本 |
| `FINSIGHT_CONFIG_DIR` | `backend/.finsight` | LLM 设置 JSON 保存目录 |
| `FINSIGHT_LOCAL_LLM_BASE_URL` | `http://127.0.0.1:8004/v1` | 设置页和语义增强的本地模型默认地址 |
| `FINSIGHT_LOCAL_LLM_MODEL` | `Qwen3.6-35B-A3B-FP8` | 设置页和语义增强的本地模型默认名称 |

## 6. Hermes Agent 对接

Hermes 配置集中在 `services/hermes_client.py`：

| 后端 profile key | Hermes model | 端口 | 前端入口 |
| --- | --- | --- | --- |
| `finsight_assistant` | `finsight_assistant` | `8642` | `/api/chat/*` |
| `analysis` | `finsight_analysis` | `8651` | `/api/analysis/chat/*` |
| `factchecker` | `finsight_factchecker` | `8649` | `/api/factchecker/chat/*` |
| `tracking` | `finsight_tracking` | `8650` | `/api/tracking/chat/*` |
| `legal` | `finsight_legal` | `8652` | `/api/legal/chat/*` |

默认认证：

```text
Authorization: Bearer change-me-local-dev
```

启动示例：

```bash
hermes profile use finsight_legal
hermes gateway start
curl -s http://localhost:8652/health
```

聊天路由使用 Hermes Runs API：

1. 从 SQLite 读取最近历史。
2. 保存当前 user message。
3. 调用 Hermes `POST /v1/runs`，携带 `conversation_history`。
4. 前端流式接口订阅 `GET /v1/runs/{run_id}/events`。
5. 后端转为自己的 SSE：`run`、`delta`、`tool`、`reasoning`、`done`、`error`。
6. 完成后保存 assistant message。

更多背景见 `hermes-api-multi-turn.md`。

## 7. API 详表

所有路径以下均省略主机 `http://localhost:10081`。

### 7.1 健康与根页面

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/health` | 返回 `{"status":"ok"}` |
| `GET` | `/` | 返回旧单页聊天 HTML：`../front/index.html` |

### 7.2 聊天 API

普通助手路径为 `/api/chat/*`。业务助手路径为：

- `/api/analysis/chat/*`
- `/api/factchecker/chat/*`
- `/api/tracking/chat/*`
- `/api/legal/chat/*`

每个助手都提供：

| 方法 | 子路径 | 说明 |
| --- | --- | --- |
| `POST` | `/chat` | 非流式返回完整 `reply` |
| `POST` | `/chat/stream` | SSE 流式输出 |
| `POST` | `/chat/stop` | 请求停止当前 Hermes run |
| `GET` | `/chat/active` | 查询当前 session 是否有运行中的 run |
| `GET` | `/chat/active/stream?offset=N` | 从指定事件 offset 接回运行中的 run |
| `GET` | `/chat/history` | 当前 session 最近历史 |
| `GET` | `/chat/sessions` | 当前 profile 的历史会话列表 |
| `POST` | `/chat/session` | 新建 session 并切换 |
| `POST` | `/chat/session/{session_id}` | 切换到已有 session |
| `DELETE` | `/chat/session` | 删除当前 session 消息，并创建新 session |

请求体：

```json
{"message": "请分析这家公司现金流风险"}
```

非流式响应：

```json
{"reply": "...", "new_achievements": []}
```

SSE 事件示例：

```text
event: run
data: {"run_id":"run_xxx"}

event: delta
data: {"content":"增量文本"}

event: done
data: {"new_achievements":[]}
```

### 7.3 Wiki API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/wiki/companies/list` | 公司清单，含分析/核查/跟踪/法务数量 |
| `GET` | `/api/wiki/companies/recent-results?limit=8` | 最近生成的 HTML 结果 |
| `GET` | `/api/wiki/reports/search?q=茅台&limit=10` | 搜索已生成报告 |
| `GET` | `/api/wiki/companies/{company_dir}/reports` | 分析报告 HTML 列表 |
| `GET` | `/api/wiki/companies/{company_dir}/factchecks` | 事实核查 HTML 列表 |
| `GET` | `/api/wiki/companies/{company_dir}/trackings` | 持续跟踪 HTML 列表 |
| `GET` | `/api/wiki/companies/{company_dir}/legals` | 法务意见书 HTML 列表 |
| `GET` | `/api/wiki/companies/{path}` | 白名单文件读取 |
| `DELETE` | `/api/wiki/companies/{company_dir}/{result_type}/{filename}` | 删除生成的 HTML 报告 |

允许读取的扩展名：

```text
.html .json .md .csv .txt .png .jpg .jpeg .svg
```

Wiki 公司目录预期结构：

```text
/home/maoyd/wiki/companies/<company_dir>/
  company.json
  company.md
  reports/<report_id>/
    report.md
    report.json
    document_full.json
    artifact_manifest.json
  analysis/*.html
  factcheck/*.html
  tracking/*.html
  legal/*.html
  metrics/*.json
  semantic/*.json
  evidence/*.json
```

### 7.4 工作流 API

Wiki 保持知识入口范围，不作为全量解析产物仓库：全量解析信息由 `/home/maoyd/finsight/pdf2md_web/results/<task_id>` 与 PostgreSQL `pdf2md` schema 承担。Wiki report 目录只保留报告正文、兼容语义层的 `document_full.json`、`report.json` 和轻量 `artifact_manifest.json`；manifest 记录核心产物路径、hash、schema/rule 版本，用于判断 Wiki/语义层/DB 是否过期。

语义步骤会先运行规则层，再默认调用本地 Qwen3.6 生成 LLM 增强层。LLM 输出在 `companies/<company_id>/semantic/llm/<report_id>/`，包含 `enrichment.json`、`business_profile.json`、`claims.json`、`risks.json`、`events.json`、`review_queue.json` 和 `extraction_log.json`。该层只读取规则层 `segments/evidence/facts/claims`，不直接扫描全量 `document_full.json`，也不覆盖规则层事实。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/workflow/task/{task_id}/status` | 检查核心产物包、Wiki、semantic、DB 状态与 stale 信息 |
| `GET` | `/api/workflow/task/{task_id}/preflight` | 检查核心文件、脚本、数据库连接等预检项 |
| `POST` | `/api/workflow/task/{task_id}/wiki-import` | 将报告入口和轻量 manifest 导入 Wiki |
| `POST` | `/api/workflow/task/{task_id}/semantic` | 运行规则语义层，并默认调用本地 Qwen3.6 生成 LLM 增强层 |
| `POST` | `/api/workflow/task/{task_id}/db-import` | 导入 PostgreSQL |
| `POST` | `/api/workflow/task/{task_id}/run-remaining` | 后台顺序运行未就绪/已过期的 Wiki、语义层和 DB 步骤 |
| `GET` | `/api/workflow/job/{job_id}` | 查询后台流水线任务状态 |

工作流默认读取：

```text
/home/maoyd/finsight/pdf2md_web/results/<task_id>/document_full.json
/home/maoyd/finsight/pdf2md_web/tasks.db
```

导入目标：

```text
/home/maoyd/wiki/companies/<company_id>/
```

### 7.5 PDF 来源代理 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/source/{task_id}/table/{table_index}` | 表格来源；HTML 或 JSON |
| `GET` | `/api/source/{task_id}/page/{page_number}` | 页级来源；HTML 或 JSON |
| `GET` | `/api/pdf_page/{task_id}/{page_number}` | PDF 单页 PNG |
| `POST` | `/api/source/{task_id}/table/{table_index}/correction` | 保存表格人工修正 |

`source.py` 会根据 `Accept` 或 `format=json/html` 决定返回可读 HTML 还是原始 JSON。

### 7.6 下载文件 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/downloads/reports?q=&limit=80` | 列出已下载 PDF |
| `GET` | `/api/downloads/report-file?path=<relativePath>` | 打开 PDF 文件 |
| `DELETE` | `/api/downloads/report-file?path=<relativePath>` | 删除 PDF 文件 |

安全限制：

- `path` 必须是相对路径。
- 禁止 `..` 和绝对路径。
- 只能访问 `REPORT_DOWNLOADS_ROOT` 下的 `.pdf`。

### 7.7 设置与系统状态

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/settings/llm` | 读取模型设置，不返回 API Key 明文 |
| `PUT` | `/api/settings/llm` | 保存本地/云端模型设置 |
| `POST` | `/api/settings/llm/test` | 以 OpenAI-compatible `/chat/completions` 测试连接 |
| `GET` | `/api/system/status` | 汇总 report-finder、pdf2md、Hermes、Wiki、模型设置状态 |

设置文件默认位置：

```text
backend/.finsight/llm_settings.json
```

注意：设置页的模型测试只验证 OpenAI-compatible 连接；业务 Agent 实际调用仍通过 Hermes profiles。

### 7.8 宠物与成就 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/pet/state` | 宠物状态 |
| `POST` | `/api/pet/feed` | 喂食 |
| `POST` | `/api/pet/play` | 玩耍 |
| `POST` | `/api/pet/rest` | 休息 |
| `GET` | `/api/achievements` | 成就列表 |

普通聊天会自动触发宠物 `chat` 动作和成就检查。

### 7.9 早期规则型 Tracking API

`routers/tracking.py` 和 `agents/tracking/` 是早期规则型持续跟踪模块，与 `/api/tracking/chat/*` 共享前缀但用途不同。若后续重新挂载这些 REST API，默认也会读写 `WIKI_ROOT/companies/<公司>/tracking/`，与主 Wiki 展示目录保持一致。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/tracking/process` | 输入报告文本，生成跟踪面板 |
| `GET` | `/api/tracking/dashboard/{stock_code}` | 读取跟踪面板 |
| `POST` | `/api/tracking/sentiment/refresh` | 生成舆情日报 |
| `POST` | `/api/tracking/metrics/refresh` | 刷新指标面板 |
| `GET` | `/api/tracking/alerts/{stock_code}` | 读取预警 |
| `POST` | `/api/tracking/alerts/{alert_id}/acknowledge` | 确认预警 |
| `GET` | `/api/tracking/items/{stock_code}` | 读取跟踪事项 |

当前主 UI 的报告展示主要走 Wiki HTML；这些 REST API 更多是保留的结构化能力。

## 8. 数据模型

SQLite 文件：

```text
backend/data/pet.db
```

表：

| 表 | 模型 | 说明 |
| --- | --- | --- |
| `petstate` | `PetState` | 宠物等级、经验、饥饿、心情、精力 |
| `chatmessage` | `ChatMessage` | 不同 session 的 user/assistant 消息 |
| `achievement` | `Achievement` | 成就定义和进度 |
| `interactionlog` | `InteractionLog` | feed/play/rest/chat 动作日志 |

首次启动时 `lifespan` 会调用：

```python
create_db_and_tables()
seed_data()
```

## 9. 前端联动关系

| 前端模块 | 后端 API |
| --- | --- |
| `ChatBot.tsx`、`ChatPage.tsx` | `/api/chat/*` |
| `AgentChatPanel.tsx` | `/api/analysis/chat/*`、`/api/factchecker/chat/*`、`/api/tracking/chat/*`、`/api/legal/chat/*` |
| `ReportViewer.tsx` | `/api/wiki/companies/*` |
| `Dashboard.tsx` | `/api/wiki/companies/list`、`/api/wiki/companies/recent-results` |
| `Topbar.tsx` | `/api/wiki/reports/search`、`/pdfapi/tasks`、`/api/downloads/reports` |
| `SearchDownload.tsx` | `/api/downloads/*`，另有 `/api/v1/*` 由 Vite 转发到 8000 |
| `PdfParsing.tsx` | `/api/workflow/*`、`/api/source/*`，另有 `/pdfapi/*` 由 Vite 转发到 5000 |
| `Settings.tsx` | `/api/settings/llm`、`/api/system/status` |

## 10. 常见排查

### 10.1 `/api/wiki/companies/list` 返回空

```bash
echo "$WIKI_ROOT"
ls -la /home/maoyd/wiki/companies
curl -s http://localhost:10081/api/wiki/companies/list
```

确认 `WIKI_ROOT` 指向真实 Wiki 根目录，且下面有 `companies/`。

### 10.2 聊天流式接口中断

检查对应 Hermes：

```bash
curl -s http://localhost:8651/health
curl -s http://localhost:8652/health
```

然后看后端日志中是否有 `httpx.TimeoutException` 或 Hermes 4xx/5xx。

### 10.3 前端刷新后仍显示旧回复

聊天历史存于 `backend/data/pet.db`。删除当前会话应走：

```bash
curl -X DELETE http://localhost:10081/api/chat/session
```

业务助手对应替换前缀，例如 `/api/legal/chat/session`。

### 10.4 工作流导入失败

优先确认 task 目录完整：

```bash
ls -la /home/maoyd/finsight/pdf2md_web/results/<task_id>
test -f /home/maoyd/finsight/pdf2md_web/results/<task_id>/document_full.json
curl -s http://localhost:10081/api/workflow/task/<task_id>/status
```

若无法识别股票代码或报告年份，`wiki-import` 会返回 422，避免错误导入。

### 10.5 已下载 PDF 看不到

确认聚合后端读取的根目录：

```bash
ls -la /home/maoyd/report-finder-service/downloads
curl -s 'http://localhost:10081/api/downloads/reports?limit=5'
```

如下载服务路径改变，需要设置 `REPORT_DOWNLOADS_ROOT`。
