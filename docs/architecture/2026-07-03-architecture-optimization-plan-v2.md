# SIQ Research Engine 架构优化方案 v2

> - 日期：2026-07-03
> - 状态：新版执行锚点
> - 范围：现有二级市场研究、财报/文档解析、Market evidence package、Hermes 研究智能体、Web 工作台和本地运维
> - 明确排除：一级市场 / Deal OS / IC 投委会 / OpenClaw 兼容层。相关文件可继续存在于工作区，但不纳入本方案任务、验收和优先级。

## 1. 为什么重写本方案

旧版 `2026-06-29-repository-architecture-optimization-plan.md` 已经承担了设计、执行流水、阶段验证和并行事项记录，内容过长，不再适合作为后续开发的日常指挥文档。

本方案重新基于 2026-07-03 当前项目状态整理，目标是：

1. 给后续“拆大文件、优化架构、稳定门禁”提供清晰执行路线。
2. 把已经完成的治理成果固化为基线，而不是反复从头检查。
3. 排除一级市场并行改动，避免后续原架构优化混入 Deal / IC / OpenClaw 任务。
4. 每个优化窗口都能独立开发、独立测试、独立回滚。

本方案从现在起作为非一级市场架构优化主线的执行锚点。旧长文只保留为历史记录。

## 2. 当前基线

### 2.1 已完成基线

- 运行态目录治理已阶段完成：`data/`、`var/`、`artifacts/` 的职责已经清晰，运行态大文件不应进入提交。
- Async DB advisory 已归零：后续只做防回流，不再把旧 `Depends(get_session)` finding 当作当前待办。
- `index.css` 已拆为 theme/import 外壳，核心样式迁入 `styles/app-base.css`、`styles/chat.css`、`styles/search-download.css`、`styles/dashboard.css`、`styles/system-surfaces.css` 等。
- PDF / Document 前端 workbench 已有一批 pure helper、pane、API owner 和 Node 单测。
- `market_reports.py` 已完成 proxy、queueing、command/result payload、status payload、path safety、latest case selector 等多轮低风险下沉。
- `workflow.py` 已完成 workflow job store、通用文档 status / package builder / DB-Milvus status builder、command runner 等第一轮下沉。
- Agent runtime 已形成多个 service helper：streaming、sessions、history、memory、citations、display、context、fallback、financial guard/format、parse-only、tool output 等。
- `CommandRunner`、`FileBackedJobService`、`market_report_queueing` 已覆盖短期后台 job 和命令执行合同。

### 2.2 当前主要大文件

以下行数是风险信号，不是直接重构理由：

| 文件 | 当前规模 | 主要风险 |
| --- | ---: | --- |
| `apps/api/services/agent_chat_runtime_impl.py` | 约 6045 行 | 会话、DB、SSE、工具调用、引用、fallback、记忆逻辑仍高度耦合 |
| `apps/pdf-parser/pdf_parser_app_impl.py` | 约 3948 行 | Flask route、任务状态、artifact、quality、document_full、MinerU 编排仍混在一起 |
| `apps/pdf-parser/financial_extractor.py` | 约 3641 行 | 财务抽取规则、解析、容错和格式化耦合，回归风险高 |
| `apps/api/routers/workflow.py` | 约 2520 行 | 通用文档 workflow、旧 PDF workflow、job 状态、subprocess 编排仍在同一控制面 |
| `apps/api/routers/chat.py` | 约 1384 行 | 普通 chat / streaming route 与鉴权、usage、attachment、runtime 调用合同较密 |
| `apps/api/routers/eval_e2e.py` | 约 1380 行 | 评测配置、执行和产物读取混合，生产价值低于风险 |
| `apps/api/routers/workspace.py` | 约 1294 行 | 上传、artifact、quota、source link、PDF parser 调用仍有业务耦合 |
| `apps/api/routers/market_reports.py` | 约 1290 行 | 已明显瘦身，但 package build/import/vector/eval/SEC rebuild 真实执行 owner 仍在 router |
| `apps/web/src/pages/SearchDownload.tsx` | 约 961 行 | 页面状态 owner 仍重，虽然已有 feature helper 和 panels |
| `apps/web/src/pages/MarketParsingPage.tsx` | 约 636 行 | 多市场解析页面仍承担状态、tab、API 调用和展示编排 |
| `apps/document-parser/app.py` | 约 1280 行 | Flask app 仍含 route、task、provider、artifact 响应编排 |

### 2.3 当前工作区隔离要求

当前工作区存在大量一级市场 / Deal / IC 相关改动，例如：

- `apps/api/routers/deals.py`
- `apps/api/services/deal_*.py`
- `apps/api/services/ic_*.py`
- `agents/hermes/profiles/siq_ic_*`
- `apps/web/src/pages/Deal*.tsx`
- `apps/web/src/lib/dealApi.ts`

这些改动不属于本方案。后续执行本方案时：

- 不回退、不整理、不移动这些文件。
- 不把 Deal / IC 文件纳入原架构优化提交。
- 如果同一个文件同时有原架构与一级市场改动，先局部 review，再只改与本窗口相关的最小范围。

## 3. 目标架构

目标不是“把所有大文件切碎”，而是形成稳定 owner 边界。

```text
Web 页面
  -> feature API / view-model / panels
  -> apps/api thin routers
  -> services: command, job, repository, workflow, runtime helpers
  -> parser apps / finder / rules / DB / Milvus / Hermes
```

### 3.1 API 控制面原则

Router 应只负责：

- FastAPI 参数、鉴权、HTTPException 映射。
- 读取请求体、注入依赖、选择 service。
- 保留 endpoint URL、响应字段和旧 monkeypatch 测试入口。

Service / repository 应负责：

- payload builder。
- path safety。
- command args 和 command result payload。
- job envelope。
- 文件索引、manifest 读取和只读 lookup。
- pure helper 和可直接单测的业务派生。

真实副作用应集中在少数 owner：

- `run_command()` / subprocess。
- job runner。
- parser upstream 调用。
- DB / Milvus 写入。
- artifact 文件写入。

### 3.2 Parser apps 原则

Flask app 只保留：

- route adapter。
- request / response 映射。
- task lifecycle owner。
- 副作用编排。

Service 层负责：

- artifact path / source view / open artifact。
- quality payload。
- document_full / content_list_enhanced payload。
- financial checks / schema mismatch / stale checks。
- page metadata / table relations / source map 派生。

### 3.3 Agent runtime 原则

`agent_chat_runtime_impl.py` 不应继续吸收新 helper。后续新增逻辑必须优先落到已有 service 文件：

- `agent_runtime_context.py`
- `agent_runtime_citations.py`
- `agent_runtime_display.py`
- `agent_runtime_fallback_contexts.py`
- `agent_runtime_history.py`
- `agent_runtime_memory.py`
- `agent_runtime_parse_only.py`
- `agent_runtime_streaming.py`
- `agent_runtime_sessions.py`
- `agent_runtime_tool_output.py`
- `agent_runtime_financial_guard.py`
- `agent_runtime_financial_format.py`

## 4. 执行优先级

### P0：工作区和门禁保护

目标：让后续优化不被并行一级市场改动、运行态文件或测试漂移干扰。

任务：

1. 每轮开始执行 `git status --short`，标记本轮 touch set。
2. 不处理 Deal / IC / OpenClaw 文件。
3. 每轮收尾执行 `git diff --check`。
4. 代码变更必须配聚焦测试。
5. 涉及前端页面时执行 `npm run check:frontend`。

验收：

```bash
git diff --check
git status --short
```

### P1：Market reports 控制面继续瘦身

当前状态：`market_reports.py` 已完成多轮 helper 下沉，但真实执行 owner 仍较重。

下一步顺序：

1. **Build plan helper**
   - 从 `_run_market_package_build()` 抽 source selection、metadata sidecar、parser result 判定、script selection 的 plan builder。
   - Router 保留 HTTPException、`run_command()`、package detail 读取。
   - 必须先补 contract，确认：
     - `download_relative_path` 优先级。
     - `source_path` 相对/绝对路径。
     - metadata 显式/adjacent fallback。
     - HK / EU PDF parser result 必需。
     - EU ESEF 不接受 parser result。
     - parser result missing 404。

2. **Package import / vector ingest plan helper**
   - 抽 payload -> command args 前的计划对象。
   - 不执行真实 PostgreSQL / Milvus。
   - 保留 current stdout/stderr/result payload。

3. **US SEC rebuild plan helper**
   - 抽 raw source / metadata copy 前的只读 selector 和 plan builder。
   - 不迁临时目录写入，不迁 `run_command()`。

4. **Route file split**
   - 当 service helper 足够稳定后，再考虑把 router 拆成：
     - `market_reports_proxy_routes.py`
     - `market_reports_package_routes.py`
     - `market_reports_job_routes.py`
     - `us_sec_routes.py`
   - 只有在 route contract 全覆盖后再做文件移动。

验证：

```bash
cd apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_market_package_repository.py \
  tests/test_market_report_commands.py \
  tests/test_market_report_queueing_service.py \
  tests/test_market_report_status_service.py \
  tests/test_market_report_proxy_service.py \
  tests/test_market_reports_proxy.py \
  tests/test_job_service.py
```

### P1：Workflow 控制面二轮瘦身

当前状态：`workflow.py` 已有 `document_workflow_service.py` 和 `workflow_job_service.py`，但 DB/chunk subprocess、旧 PDF workflow 和 `_workflow_jobs` 仍在 router。

下一步顺序：

1. **先不做通用文档一键异步 workflow**
   - 当前通用文档的 step 顺序、失败恢复、前端轮询语义还未稳定。
   - 不应贸然把 document workflow 纳入 `_workflow_jobs`。

2. **Document command plan helper**
   - 抽 `import_document_task_to_database()` 和 `build_document_semantic_chunks()` 的 command args / env / timeout plan。
   - Router 保留 `run_command()`、HTTPException、真实文件读取。

3. **Workflow job schema 设计**
   - 明确 `workflow_job_service.py` 与 `FileBackedJobService` 的差异。
   - 暂不统一 schema，先写中期设计和 contract tests。

4. **旧 PDF run-remaining pipeline**
   - 只在 route contract 和 step envelope 完整后拆。
   - 不先改线程模型。

验证：

```bash
cd apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_document_workflow_service.py \
  tests/test_document_workflow_package.py \
  tests/test_workflow_job_service.py \
  tests/test_command_runner.py \
  tests/test_job_service.py
```

### P1：PDF parser app owner 收口

当前状态：`pdf_parser_app_impl.py` 仍是最大 Flask app owner，但已有多个 service 文件。

下一步顺序：

1. **Route response payload 收口**
   - 继续把 task status、quality、document_full、content_list_enhanced、open artifact response 的纯 payload 下沉。
   - 保留 Flask `jsonify`、`send_file`、status code 和文件存在检查。

2. **Task lifecycle service**
   - 当前已有 `pdf_parser_task_lifecycle_service.py` 雏形。
   - 后续只抽 terminal state、cancelled、progress clamp、elapsed 派生。
   - 不迁 queue claim 和 worker loop，除非先补 lifecycle contract。

3. **MinerU result / fetch cache owner**
   - 只抽 path and payload。
   - 不改真实网络调用、重试或文件写入顺序。

4. **financial_extractor 拆分设计**
   - 这是高风险大文件，先加只读 characterization tests。
   - 建议拆为：
     - statement detection。
     - numeric parsing。
     - table matching。
     - quality checks。
     - output normalization。
   - 每次只迁一个 pure helper。

验证：

```bash
cd apps/pdf-parser
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q
```

聚焦：

```bash
cd apps/pdf-parser
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q \
  tests/test_pdf_parser_source_service.py \
  tests/test_pdf_source_viewer.py \
  tests/test_pdf_parser_artifact_orchestrator_service.py \
  tests/test_pdf_parser_content_list_enhanced_service.py \
  tests/test_pdf_parser_document_full_service.py \
  tests/test_pdf_parser_quality_service.py
```

### P1：Agent runtime 大文件约束

当前状态：`agent_chat_runtime_impl.py` 已有大量 helper 下沉，但仍承担普通 chat、streaming、DB、session、SSE、tool orchestration。

下一步顺序：

1. **禁止新增杂项逻辑回流**
   - 新增 context / citation / display / fallback / financial / parse-only 逻辑必须放到对应 service。

2. **普通 chat / streaming 共享 preflight**
   - 抽 request envelope 和 preflight result 的 pure helper。
   - 不改 SSE 生命周期、不改 DB session 顺序。

3. **Session/history owner**
   - 继续把只读 history formatting、session title、dedupe 派生下沉。
   - 保存消息和 usage 仍保留高风险 owner，先加 contract tests。

4. **Tool orchestration**
   - 只抽 tool output normalization、tool label、progress payload。
   - 不迁真实 tool 调用顺序。

验证：

```bash
cd apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_agent_runtime_*.py \
  tests/test_agent_chat_runtime_loops.py \
  tests/test_agent_runtime_chat_preflight.py \
  tests/test_agent_runtime_active_runs.py
```

### P2：Document parser app

当前状态：`apps/document-parser/app.py` 仍集中 route 和 task 编排，但 service 层已较完整。

下一步顺序：

1. 抽 request parsing / route payload。
2. 抽 task status response。
3. 抽 MinerU import payload。
4. 保留 provider 调用、文件写入、task lifecycle 在 app，直到有 contract tests。

验证：

```bash
cd apps/document-parser
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
```

### P2：前端页面 owner 收口

当前状态：

- `SearchDownload.tsx` 仍约 961 行，但已有 `features/search-download/*`。
- `MarketParsingPage.tsx` 仍承担市场 tab、API、状态和展示。
- `DocumentResultWorkbench.tsx`、`PdfSourceWorkbench.tsx` 已有 helper/pane 拆分。

下一步顺序：

1. **SearchDownload**
   - 抽剩余 URL state、panel view model、download refresh plan。
   - 页面保留 state owner 和 event handler。

2. **MarketParsingPage**
   - 抽 market tab config、queue job status view model、package action handlers。
   - 不改路由和 API shape。

3. **Document/PDF workbench**
   - 只继续抽 pure derivation 和 pane。
   - 不改 scroll/ref/resource open owner。

验证：

```bash
cd apps/web
npm run test:unit
npm run check:frontend
```

关键 UI 变更才跑 Playwright：

```bash
cd apps/web
npm run e2e
```

### P2：Job / worker 中期设计

当前状态：

- `FileBackedJobService` 可满足短期 market jobs。
- `workflow.py` 仍有自有 `_workflow_jobs`。
- 当前不是生产级 worker 队列。

目标：

1. 明确是否迁 Redis/RQ/Arq/Celery 或本地 worker process。
2. 定义 job schema：
   - `job_id`
   - `kind`
   - `status`
   - `created_at`
   - `started_at`
   - `finished_at`
   - `created_by`
   - `result`
   - `error`
   - `steps`
3. 定义取消、重试、日志 tail、重启恢复语义。
4. 先写 contract tests，再迁一个低风险 job。

2026-07-04 更新：canonical schema 与 adapter rules 已在 `2026-07-04-job-worker-middle-design.md` 中明确；`apps/api/services/job_envelope.py` 和 `apps/api/tests/test_job_envelope.py` 已补齐首批 adapter 合同。该更新只关闭设计与合同测试前置项，不代表 Market / Workflow job store、route schema 或 worker loop 已迁移。

## 5. 不做事项

以下事项不纳入本方案：

- 一级市场 / Deal OS / IC 投委会 / OpenClaw。
- 新增模型能力、模型选型或大规模 prompt 改写。
- 真实 PostgreSQL / Milvus schema 大改。
- 一次性拆 `agent_chat_runtime_impl.py` 或 `pdf_parser_app_impl.py`。
- 把 advisory 扫描直接升级为 CI 硬门禁。
- 为追求行数下降而移动文件，不改变 owner 边界。

## 6. 窗口工作法

每个开发窗口必须遵守：

1. 选择一个 owner。
2. 只改 2-5 个相关文件。
3. 先补 contract，再抽实现。
4. 保留旧 wrapper，避免 route/test monkeypatch 入口失效。
5. 不改 endpoint URL、响应字段、错误文案，除非本窗口目标就是合同变更。
6. 收尾更新本方案的执行记录，或新建独立 task note。

推荐窗口模板：

```text
目标：
范围：
不做：
改动文件：
测试：
风险：
回滚：
```

## 7. 验证矩阵

### 7.1 最小通用门禁

```bash
git diff --check
```

### 7.2 API owner 门禁

Market reports：

```bash
cd apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_market_package_repository.py \
  tests/test_market_report_commands.py \
  tests/test_market_report_queueing_service.py \
  tests/test_market_report_status_service.py \
  tests/test_market_report_proxy_service.py \
  tests/test_market_reports_proxy.py \
  tests/test_job_service.py
```

Workflow：

```bash
cd apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_document_workflow_service.py \
  tests/test_document_workflow_package.py \
  tests/test_workflow_job_service.py \
  tests/test_command_runner.py \
  tests/test_job_service.py
```

Agent runtime：

```bash
cd apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_agent_runtime_*.py \
  tests/test_agent_chat_runtime_loops.py \
  tests/test_agent_runtime_chat_preflight.py \
  tests/test_agent_runtime_active_runs.py
```

Async DB guard：

```bash
cd /home/maoyd/siq-research-engine
scripts/check_async_db_audit.sh
```

### 7.3 Parser 门禁

PDF parser：

```bash
cd apps/pdf-parser
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q
```

Document parser：

```bash
cd apps/document-parser
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
```

### 7.4 Web 门禁

```bash
cd apps/web
npm run test:unit
npm run check:frontend
```

### 7.5 全仓合并前门禁

```bash
cd /home/maoyd/siq-research-engine
scripts/check_all.sh
```

`scripts/check_all.sh` 较重，不要求每个小窗口都跑，但合并前必须跑。

## 8. 风险与回滚

| 风险 | 控制 |
| --- | --- |
| 路由响应字段漂移 | 先补 route contract / golden response |
| 错误文案变化影响前端 | 测试锁定 HTTP status/detail |
| 真实执行链被误触发 | 用 fake `run_command` 并断言未调用 |
| path safety 改坏 | 保留 `Path.resolve()` + `relative_to()` 语义，补 symlink escape 测试 |
| DB / Milvus side effect | 默认 dry-run 或只测 args / payload |
| 大文件拆分过快 | 保留 wrapper，一次只迁 pure helper |
| 一级市场改动混入 | 本方案窗口不 touch Deal / IC 文件 |
| 测试慢导致跳过 | 每个 owner 定义聚焦门禁，合并前再全量 |

回滚策略：

1. 小窗口只改少量文件，必要时可按文件局部反向 patch。
2. 保留旧 wrapper，回滚 service helper 时 route 调用点易恢复。
3. 不做跨 owner 大重命名，避免回滚冲突。

## 9. 后续 5 个建议窗口

### 窗口 1：Market build plan helper

目标：抽 `_run_market_package_build()` 前半段 plan builder。

不做：

- 不迁 `run_command()`。
- 不迁 package detail 读取。
- 不改 parser result 合同。

测试：

- HK parser result 必需。
- EU PDF parser result 必需。
- EU ESEF 不接 parser result。
- metadata explicit / adjacent fallback。
- invalid download path 不执行命令。

### 窗口 2：Workflow document command plan helper

目标：抽通用文档 DB import 和 semantic chunk command plan。

不做：

- 不迁 subprocess。
- 不引入一键异步 workflow。
- 不统一 job schema。

### 窗口 3：PDF parser task status / lifecycle helper

目标：继续把 task status response、terminal/cancel/progress 派生下沉。

不做：

- 不改 worker loop。
- 不改 queue claim。
- 不改 result 文件布局。

### 窗口 4：Agent runtime ordinary chat preflight

目标：抽普通 chat 和 streaming 共享的 request/preflight pure helper。

不做：

- 不改 SSE lifecycle。
- 不改 DB save order。
- 不改 usage 语义。

### 窗口 5：SearchDownload 页面 view model

目标：抽剩余 view model / derived state，让页面只保留 state owner 和 handlers。

不做：

- 不改 API。
- 不改路由。
- 不做视觉重设。

## 10. 执行记录

后续不要再把大量流水追加到旧长文。建议在本节只追加简短记录：

```text
YYYY-MM-DD:
- Owner:
- 完成:
- 测试:
- 下一步:
```

```text
2026-07-03:
- Owner: Market reports / package build plan
- 完成: 确认 _run_market_package_build() 已通过 market_report_commands.build_market_package_build_plan() 下沉 source selection、metadata fallback、script selection 和 parser_result 判定；router 仍保留 run_command() 与 package detail 读取。
- 测试: apps/api tests/test_market_report_commands.py 34 passed；Market reports 聚焦门禁 116 passed，2 个既有 Pydantic deprecation warnings。
- 下一步: 进入 Workflow document command plan helper，抽 DB import / semantic chunk command args plan，不迁 subprocess。

2026-07-03:
- Owner: Workflow / document command plan
- 完成: 确认 document_workflow_service.document_db_import_plan() 与 document_semantic_plan() 已承接 DB import / semantic chunk command args、env、timeout plan；workflow router 仍保留 _run_command() 与 HTTPException。
- 测试: apps/api Workflow 聚焦门禁 45 passed。
- 下一步: 进入 PDF parser task status / lifecycle helper，继续验证 terminal/cancel/progress 派生下沉。

2026-07-03:
- Owner: PDF parser / task lifecycle
- 完成: 确认 pdf_parser_task_lifecycle_service 已承接 page progress、progress percent、cancel update、status failure update、stale submitting recovery 和 queue claim wrapper；Flask app 仍保留 worker loop 与 queue claim 调用点。
- 测试: apps/pdf-parser lifecycle/runtime 聚焦 56 passed；parser service 聚焦 105 passed。
- 下一步: 进入 Agent runtime ordinary chat preflight，确认 request/preflight helper 与 DB/SSE 顺序合同。

2026-07-03:
- Owner: Agent runtime / ordinary chat preflight
- 完成: 确认 agent_runtime_preflight 已承接 request envelope 与 run preflight context；agent_chat_runtime_impl 保留 wrapper/monkeypatch 入口，普通 chat 与 streaming 仍保留 catalog、duplicate、active-run join、DB save 和 SSE 生命周期顺序。
- 测试: apps/api agent runtime 聚焦门禁 114 passed，257 个既有 utcnow deprecation warnings。
- 下一步: 暂停继续开大窗口，先收尾 P0 门禁与工作区 touch set 审计；后续可进入 SearchDownload view model。

2026-07-03:
- Owner: SearchDownload / page view model
- 完成: 新增 features/search-download/viewModel.ts，收口年份列表、市场源提示、表格标题、visible/deferred 列表、候选解释 map、日志风险、候选/选中计数和 hasReports 派生；SearchDownload.tsx 继续保留 state owner、URL 同步、API/下载/toast handlers 与 JSX。
- 测试: apps/web npm run test:unit 56 passed；apps/web npm run build passed。
- 下一步: 后续如继续前端窗口，可再按 handler 分层评估搜索/下载编排，不迁 API 合同。

2026-07-03:
- Owner: Frontend / MarketParsing view model + SearchDownload selection
- 完成: 新增 features/market-parsing/viewModel.ts，收口移动端 section/tab 顺序、日志风险/展开文案、完成态 workflow/result gate、空态和 EU 结构化入口开关派生；新增 features/search-download/selection.ts，收口单项/分组选择 Set 纯变换；页面继续保留 task/workflow hooks、URL/API、上传/下载/结构化解析 handlers 和 JSX。
- 测试: apps/web npm run test:unit 63 passed；apps/web npm run build passed；git diff --check passed。
- 下一步: 前端继续建议按纯 helper 推进 SearchDownload officialSourceReadiness 或 assist payload；后端可按智能体评估进入 document-parser status payload helper。

2026-07-03:
- Owner: Document parser / status payload
- 完成: 新增 status_payload.build_task_status_payload()，收口 /api/status/<task_id> 的 task/log/log_count/artifacts_ready payload 组装；app.py 仍保留 Flask route、store、failed bridge recovery 和 request since 解析。
- 测试: apps/document-parser tests/test_status_payload.py + tests/test_document_parser_app.py 18 passed；apps/document-parser 全量 29 passed。
- 下一步: 后端后续可继续抽 MinerU import candidates payload 或 SearchDownload officialSourceReadiness，仍保持副作用 owner 不迁移。

2026-07-03:
- Owner: SearchDownload readiness / Document parser MinerU candidates
- 完成: 新增 features/search-download/officialSourceReadiness.ts，收口 JP/KR 官方源可用性决策，页面仅保留 health fetch、warning state、log 和 toast 副作用；新增 mineru_candidates_payload.build_mineru_import_candidates_payload()，收口 MinerU import candidates response shape，app.py 仍保留 limit parsing、allowed roots 和目录扫描。
- 测试: apps/web npm run test:unit 72 passed；apps/web npm run build passed；apps/document-parser candidates/status/app 聚焦 19 passed；apps/document-parser 全量 30 passed；目标 diff --check passed。
- 下一步: 避开 MarketEvidencePackagesPanel/packageActions 外部并行 slice；后续可继续 SearchDownload assist payload 纯 helper 或 document-parser candidates limit parser helper。

2026-07-03:
- Owner: Market reports / package import + vector ingest plan
- 完成: 新增 build_market_package_import_plan() 与 build_market_vector_ingest_plan()，把 package path、script selection 和 dry_run 派生从 router 下沉到 market_report_commands；router 仍保留 run_command()、timeout 与 result payload。
- 测试: Market reports owner 门禁 120 passed；本轮 API 组合门禁 159 passed；git diff --check passed。
- 下一步: Market reports 后续可继续 US SEC rebuild selector/plan helper，不迁 tempfile 写入和 run_command()。

2026-07-03:
- Owner: PDF parser / route response payload
- 完成: 新增 result/quality/financial response payload helper，/api/result、/api/quality、/api/financial 仍保留 Flask jsonify、upstream fetch/cache、quality/document_full/financial artifact 生成和错误分支。
- 测试: response/result/quality/financial/lifecycle 聚焦 30 passed；apps/pdf-parser 全量 334 passed。
- 下一步: PDF parser 后续可继续 images index 或 source-table payload helper，仍不迁 worker loop、queue claim 或 MinerU 网络调用。

2026-07-03:
- Owner: Workflow / document command plan contracts
- 完成: 补强 document_db_import_plan timeout override、document_semantic_plan collection override 与 document_semantic_command 兼容测试；生产代码不变。
- 测试: Workflow owner 门禁 47 passed；本轮 API 组合门禁 159 passed。
- 下一步: Workflow 若继续推进，优先补 router 层 command env/timeout/gating contract，再考虑中期 job schema 设计。

2026-07-03:
- Owner: Market reports / US SEC rebuild plan
- 完成: 新增 build_us_sec_rebuild_package_plan()，把 ticker latest-case selector、package path、manifest source fallback、raw metadata detection、build script/output root plan 下沉；router 仍保留 tempfile 复制、run_command()、stdout package path 解析与 package detail 读取。
- 测试: Market reports owner 门禁 123 passed；git diff --check passed。
- 下一步: 避免继续扩大 Market 窗口；后续可进入 PDF parser images index payload helper 或 Workflow router command gating tests。

2026-07-03:
- Owner: PDF parser / images index payload
- 完成: 新增 build_images_index_payload()，收口 /api/artifact/<task_id>/images 响应 shape；Flask route 仍保留 task lookup、目录存在检查、image scan、send_file/zip、错误映射和 nosniff header。
- 测试: artifact service/route 聚焦 13 passed；apps/pdf-parser 全量 336 passed；git diff --check passed。
- 下一步: PDF parser 若继续，source-table payload helper 需单独窗口并先锁 route contract；Workflow 可补 router command env/timeout/gating tests。

2026-07-03:
- Owner: Workflow / router command contracts
- 完成: 补强 document DB import 与 semantic router tests，锁住 _run_command env/timeout、chunks/milvus timeout、wiki not-ready gating 和 missing script 不执行命令；生产代码不变。
- 测试: Workflow owner 门禁 51 passed；本轮 API 组合门禁 166 passed；git diff --check passed。
- 下一步: 后续若继续 PDF parser source-table payload helper，应单独开窗，只下沉 find_source_table/source_table_payload 纯逻辑，不迁 markdown fetch、quality/artifact/page_content/bbox 副作用。

2026-07-03:
- Owner: PDF parser / source-table payload
- 完成: 新增 find_source_table()、source_table_pdf_page_image_payload() 与 source_table_payload()，收口 /api/source/<task_id>/table/<int> 响应查找和 payload shape；Flask route 仍保留 task lookup、markdown fetch、quality report、table HTML/excerpt、artifact status、corrections、page_content、bbox extent 与错误映射。
- 测试: source service/route 聚焦 27 passed；apps/pdf-parser 全量 340 passed；git diff --check passed。
- 下一步: PDF parser 可暂停继续扩大 app_impl touch set；后续优先考虑 document-parser route parsing helper 或 Web 纯 view-model 小窗口。

2026-07-03:
- Owner: Document parser / batch + source page payload
- 完成: 新增 batch_download_payload 与 source_page_payload，收口批量下载 task_id 解析、batch_manifest.json 组装和 /api/source/<task_id>/page/<int> 响应 shape；Flask app 仍保留 store、文件存在检查、zip 写入、layout 读取、send_file/jsonify 和错误映射。
- 测试: apps/document-parser 全量 42 passed；git diff --check passed。
- 下一步: document-parser 后续可继续抽 figures/source image payload 等纯响应 helper，但先避免迁 worker/provider/task lifecycle。

2026-07-03:
- Owner: SearchDownload / assist helper
- 完成: 新增 features/search-download/assist.ts，收口 smart assist 搜索计划、intent chips 和推荐 URL 派生；SearchDownload.tsx 仍保留 requestAssist、runSearch、URL/state 同步、toast 和日志副作用。
- 测试: apps/web npm run test:unit 78 passed；apps/web npm run check:frontend passed；git diff --check passed。
- 下一步: 前端后续可继续评估 download refresh plan 或 URL state 小 helper，不改 API/路由/视觉。

2026-07-03:
- Owner: Document parser / source image payload
- 完成: 新增 source_image_payload.find_figure_by_image_id() 与 build_source_image_payload()，收口 /api/figures/<task_id>/<image_id> 查找和 /api/source/<task_id>/image/<image_id> 响应 shape；Flask app 仍保留 figures.json 读取、404、jsonify 和文件路径存在检查。
- 测试: apps/document-parser 全量 46 passed；git diff --check passed。
- 下一步: document-parser 后续可继续 figures list 轻量合同测试，仍不迁 artifact path safety、provider 或 worker/task lifecycle。

2026-07-03:
- Owner: SearchDownload / URL initial state
- 完成: 新增 readSearchDownloadInitialState()，收口 URLSearchParams 初始读取、market fallback、filter 优先级和未 trim 行为；SearchDownload.tsx 仍保留 React state owner、URL patch/write side 和所有下载/assist 副作用。
- 测试: apps/web npm run test:unit 81 passed；apps/web npm run check:frontend passed；git diff --check passed。
- 下一步: 前端后续若继续，优先选择 download status 文案或 refreshed-list 纯派生，不改 API/路由/视觉。

2026-07-03:
- Owner: Document parser / figures route contract
- 完成: 补强 /api/figures/<task_id> route 合同测试，锁住 figures.json 原样 passthrough、schema/task_id/figure 字段和 missing task 404；未新增低收益 passthrough helper，避免为抽象而抽象。
- 测试: apps/document-parser 全量 46 passed；git diff --check passed。
- 下一步: document-parser 后续只在存在真实派生逻辑时再抽 helper，继续避免迁 artifact path safety、provider 或 worker/task lifecycle。

2026-07-03:
- Owner: Workflow / standard DB import command contracts
- 完成: 补强 import_task_to_database() 标准任务 DB import route command contract，锁住 DB_CONFIG_PY 分支 env=None、database-url 分支 PG/DATABASE_URL env、timeout=300、missing script 不执行命令；生产代码不变。
- 测试: Workflow owner 门禁 54 passed；git diff --check passed。
- 下一步: Workflow 后续可继续补 Obsidian command contract 或 naming repair/validate subprocess contract，仍不迁线程模型和 job schema。

2026-07-03:
- Owner: SearchDownload / download status logs
- 完成: 扩展 features/search-download/downloadStatus.ts，收口批量下载完成、fallback、逐个下载、全部完成和快捷下载成功/失败日志文案/类型派生；SearchDownload.tsx 仍保留 API 调用、状态、刷新和 toast 副作用。
- 测试: apps/web npm run test:unit 84 passed；apps/web npm run check:frontend passed；git diff --check passed。
- 下一步: 暂停继续扩大 SearchDownload；后续优先考虑 Agent runtime chat preflight short-circuit 纯 helper，或 Market ingestion eval plan helper。

2026-07-03:
- Owner: Agent runtime / chat preflight short-circuit
- 完成: 新增 agent_runtime_preflight.plan_chat_preflight_short_circuit()，收口 catalog/general/duplicate gate 决策；普通 chat 与 streaming 复用该 helper，DB save、active-run join、SSE 生命周期和 Hermes run 编排不迁移。
- 测试: apps/api .venv pytest test_agent_runtime_preflight.py + test_agent_runtime_chat_preflight.py 17 passed，31 个既有 utcnow deprecation warnings；py_compile passed。
- 下一步: 后续可进入 Market ingestion eval plan helper；继续避免触碰 Deal/IC/OpenClaw 与 Agent runtime DB/SSE 高风险 owner。

2026-07-03:
- Owner: Market reports / ingestion eval plan
- 完成: 新增并接入 build_market_ingestion_eval_plan()，把 eval script preflight 与 output/markdown 路径归一化下沉；router 仍保留 HTTPException 映射、run_command(cwd/timeout)、报告读取、queue/wait 分支和 result payload。
- 测试: Market reports owner 门禁 129 passed，2 个既有 Pydantic deprecation warnings；eval/queue/status 聚焦 111 passed；git diff --check passed。
- 下一步: 后续优先选择 Workflow router 命令合同测试或其它纯 helper 小窗口，继续避免 Deal/IC/OpenClaw 与真实执行链迁移。

2026-07-03:
- Owner: Workflow / semantic route command contracts
- 完成: 补强旧 Wiki semantic route 合同测试，锁住标准 semantic 的 rule/LLM 命令参数、LLM timeout/env、pre/post naming 与 obsidian 顺序、rule/required LLM 失败 stage，以及 generic semantic 的 identity gate 不执行命令；生产代码不变。
- 测试: test_workflow_subprocess_contracts.py 9 passed；Workflow/command runner 组合门禁 55 passed；git diff --check passed。
- 下一步: 后续若继续 Workflow，优先补 LLM optional/missing-script 合同或 generic semantic 成功路径；仍不迁 subprocess、线程模型、job schema 或一键 workflow。

2026-07-03:
- Owner: Workflow / semantic optional + generic contracts
- 完成: 继续补强旧 Wiki semantic route 合同测试，锁住 optional LLM missing-script/detail、optional LLM failure 非阻断、generic semantic 成功路径的 rule/LLM/obsidian 命令与不执行 naming；生产代码不变。
- 测试: test_workflow_subprocess_contracts.py 12 passed；Workflow/command runner 组合门禁 58 passed；git diff --check passed。
- 下一步: Workflow semantic route 暂停继续扩大；后续优先选择其它 owner 的纯 helper/合同测试，仍避开 Deal/IC/OpenClaw 与真实执行链迁移。

2026-07-04:
- Owner: Document parser / source route contracts
- 完成: 补强 MinerU import route 合同测试，锁住 /api/source/<task_id>/block/<block_id> 与 /api/source/<task_id>/table/<table_id> 的成功响应和 missing 404；生产代码不变。
- 测试: apps/document-parser test_document_parser_app.py 16 passed；payload/app 聚焦 30 passed；apps/document-parser 全量 46 passed；git diff --check passed。
- 下一步: Document parser 后续可考虑 table-relations response payload helper，仍不迁 table_merge ruleset、stale refresh、zip rebuild、worker/provider/task lifecycle。

2026-07-04:
- Owner: Document parser / table relations payload
- 完成: 新增 table_relations_payload.build_table_relations_response_payload()，收口 table relation corrections 合并、orphan manual_review relation 追加和 corrections 回显；route 仍保留文件存在检查、stale ruleset refresh、读 JSON、zip rebuild 和 jsonify。
- 测试: table_relations_payload + stale endpoint 聚焦 4 passed；document app + helper 19 passed；apps/document-parser 全量 49 passed；git diff --check passed。
- 下一步: 暂停继续扩大 Document parser；后续优先选择其它干净 owner 的纯 helper/合同测试，继续避开 Deal/IC/OpenClaw、Hermes smoke、Settings 并行改动。

2026-07-04:
- Owner: API source access / viewer contracts
- 完成: 补强 source access/viewer 纯 helper 合同测试，锁住显式 format 优先于 Accept、table HTML 清洗、source page total/printed page 推断、分页导航仅携带 source_token，以及 PDF2MD proxy 不转发 login/source token；生产代码不变。
- 测试: test_source_access.py 28 passed，仅有既有 Pydantic/utcnow warnings；git diff --check passed。
- 下一步: API source access 暂停继续扩大；后续优先选择其它纯 helper 或合同测试小窗口，继续避免迁移 PDF2MD proxy、鉴权策略和真实上游调用链。

2026-07-04:
- Owner: SearchDownload / curated annuals helper
- 完成: 新增 curatedAnnuals helper，收口 JP/KR 主流年报样本加载的 market gate、请求参数、去重、默认选中、companyInfo 与日志文案派生；SearchDownload.tsx 仍保留 API 调用、React state、toast/log 副作用和页面渲染。
- 测试: search-download 聚焦 node tests 34 passed；apps/web npm run test:unit 90 passed；apps/web npm run build passed；git diff --check passed。
- 下一步: 前端后续可继续按纯 helper 小窗口推进 MarketParsing 上传文件校验或其它未触碰 view-model，不改视觉、不改 API 合同。

2026-07-04:
- Owner: MarketParsing / upload validation helper
- 完成: 新增 uploadFiles.validateMarketParsingUploadFiles()，收口上传 PDF 数量、后缀和 100MB 文件大小校验；MarketParsingPage.tsx 仍保留 selectedFiles/error state、上传启动、拖拽和解析流程。顺手修复并行 DealWorkflow view-model 抽取遗留的类型/build 阻断：disputePositionCount 接收可选 positions，并补回页面导入。
- 测试: market-parsing 聚焦 node tests 13 passed；Deal/SearchDownload/MarketParsing 聚焦 13 passed；apps/web npm run test:unit 99 passed；apps/web npm run build passed；git diff --check passed。
- 下一步: 前端暂缓继续扩大；后续可接 Russell 建议的 agent_runtime_statement_context 纯合同测试，或先拆分/验收 Deal/OpenClaw 与 Hermes smoke 并行改动。

2026-07-04:
- Owner: Agent runtime / statement context contracts
- 完成: 补强 agent_runtime_statement_context 纯 helper 合同测试，锁住 latest_records_by_statement() 对 period="" / period=None 时回退 source.period 的行为，以及 latest period 选择和 core rank 输出顺序；生产代码不变。
- 测试: test_agent_runtime_statement_context.py 10 passed；git diff --check passed。
- 下一步: 先做工作树分组审计和提交切分建议；后续开发优先从未触碰的纯 helper/合同测试继续，避免继续扩大 Deal/OpenClaw、Hermes smoke、Document parser 和前端页面 owner。

2026-07-04:
- Owner: Market reports / status service ticker normalization
- 完成: 修复 latest_case_item_for_ticker() 候选 case ticker 未 strip 导致带空格 ticker 漏匹配的问题，并补合同测试锁住用户输入与 case item 双侧归一化；无 route、文件 IO 或 run_command 迁移。
- 测试: test_market_report_status_service.py 6 passed；git diff --check passed。
- 下一步: Market reports 暂停继续扩大；后续优先做工作树 owner 切分与验证，或选择未触碰的 Agent runtime 纯格式/helper 合同测试。

2026-07-04:
- Owner: Agent runtime / loop guard contracts
- 完成: 新增 agent_runtime_loop_guard service-level 合同测试，锁住重复状态行检测、正式答案标题不误杀 process trace、循环历史清洗不保留长重复原文，以及 display/history failed reply 对污染输出的替换策略；生产代码不变。
- 测试: test_agent_runtime_loop_guard.py + test_agent_chat_runtime_loops.py 61 passed，20 个既有 utcnow warnings；py_compile passed；git diff --check passed。
- 下一步: 停止继续扩大 Agent runtime；优先拆分当前工作树 owner 并跑对应门禁，避免 Deal/OpenClaw/Hermes 与 v2 纯 helper 主线混合提交。

2026-07-04:
- Owner: Market reports / proxy service contracts
- 完成: 补强 market_report_proxy service 合同测试，锁住 finder assist 成功 dict 透传、非 object JSON 空对象回退、assist upstream request error 的 502 映射、market rules GET 响应透传与 request error 502 映射，以及 health 单侧 request error 不阻断另一侧；生产代码不变，不触碰真实上游调用。
- 测试: test_market_report_proxy_service.py 13 passed，2 个既有 Pydantic deprecation warnings；Market reports owner 门禁 136 passed，2 个既有 Pydantic deprecation warnings。
- 下一步: 继续优先选择未触碰文件的纯 helper/合同测试小窗口；避免扩大 Deal/IC/OpenClaw、Hermes smoke、Document parser 和前端页面 owner。

2026-07-04:
- Owner: Shared command runner contracts
- 完成: 补强 command_runner 纯合同测试，锁住敏感参数大小写/下划线归一化脱敏、仅精确敏感 env assignment 脱敏，以及 run_command() 对 subprocess.run 的 list(args)、cwd、capture_output、text、timeout、env copy 和 check=False 调用契约；生产代码不变，不迁 subprocess owner。
- 测试: test_command_runner.py 8 passed；test_workflow_subprocess_contracts.py + test_command_runner.py 20 passed。
- 下一步: 继续以未触碰文件的 service-level 合同测试为主；若进入 Job service，只补 FileBackedJobService 纯行为合同，不迁 job lifecycle 或 route schema。

2026-07-04:
- Owner: Shared file-backed job service contracts
- 完成: 补强 FileBackedJobService 纯合同测试，锁住 malformed JSON / 非 list store payload 回退为空，以及持久化路径写入失败时不阻断运行时 job snapshot 与 terminal result；生产代码不变，不迁 job lifecycle、线程模型或 route schema。
- 测试: test_job_service.py 10 passed；document workflow/job/command 组合门禁 59 passed。
- 下一步: 优先停止扩大已脏 owner，必要时继续选择未触碰文件的只读合同测试；更大窗口需先做 owner 切分和提交规划。

2026-07-04:
- Owner: Workflow job service mutator contracts
- 完成: 补强 workflow_job_service 纯状态 mutator 合同测试，锁住重复 step 更新复用既有条目、不重复 append、终态 finishedAt 不被后续更新覆盖，以及 update_workflow_job 自定义字段更新刷新 updatedAt 且 missing job 不改已有 job；生产代码不变，不触碰 router/subprocess/job schema。
- 测试: test_workflow_job_service.py 9 passed；document workflow/job/command 组合门禁 61 passed。
- 下一步: 暂停扩大 Workflow job/service owner；继续按 v2 文档优先选择独立、未触碰文件的 service-level 合同测试，或进入工作树 owner 切分验收。

2026-07-04:
- Owner: Market reports / settings contracts
- 完成: 确认 market_report_queueing service 与 router 排队入口已有覆盖，未追加重复测试；补强 market_report_settings 配置合同测试，锁住空白主 env 回退 legacy env、URL trim/rstrip、非法 float 回默认值，以及市场级路径 override resolve 行为；生产代码不变。
- 测试: test_market_report_settings.py 5 passed；settings + queueing 小组合 15 passed，2 个既有 Pydantic deprecation warnings；git diff --check passed。
- 下一步: Market reports 配置/排队窗口收口；后续优先进入工作树 owner 切分验收，或继续只选未触碰文件的纯合同测试。

2026-07-04:
- Owner: Worktree owner split verification
- 完成: 复用并行智能体做只读 owner 风险审计，结论一致指向暂停扩大功能面、优先验收当前 dirty worktree；更新 `2026-07-04-worktree-owner-split-audit.md`，补齐 Market/API、Shared job/command、Deal/IC/OpenClaw、Hermes、Web、Document parser 等 owner 的完整 touch set 与实际验证结果。
- 测试: Market Reports API 组合 150 passed；Agent Runtime Contracts 71 passed；Source/Workflow/Shared 组合 57 passed；Document parser 聚焦 19 passed、全量 49 passed；Hermes smoke 3 passed 且脚本 py_compile passed；Deal API 81 passed；Web 聚焦 unit 101 passed；Web build passed；Deal workflow e2e 1 passed；git diff --check passed。warnings 均为既有 Pydantic/utcnow 或 Node FORCE_COLOR 提示。
- 下一步: 当前批次不再扩大代码面；按 owner audit 的提交顺序拆分提交或进入更高层合并门禁，继续保持 Deal/IC/OpenClaw 与 v2 主线分离。

2026-07-04:
- Owner: Higher-level merge gates
- 完成: 在不扩大代码面的前提下跑完更高层合并候选门禁，覆盖 API 全量、Web 全量 unit/build/Deal e2e、Document parser 全量、PDF parser 全量、Market report finder/rules 和 market contracts；并行只读智能体继续复核提交切分与 owner 风险，主线程未新增业务代码。
- 测试: apps/api 全量 867 passed，545 个既有 Pydantic/utcnow warnings；apps/web npm run test:unit 101 passed；apps/web npm run build passed；apps/web Deal workflow e2e 1 passed，既有 FORCE_COLOR/NO_COLOR 提示；apps/document-parser 全量 49 passed；apps/pdf-parser 全量 343 passed；services/market-report-finder 全量 46 passed；services/market-report-rules 全量 29 passed，1 个既有 StarletteDeprecationWarning；packages/market-contracts 2 passed；git diff --check passed。
- 下一步: 质量线已接近合并候选；后续优先按 `2026-07-04-worktree-owner-split-audit.md` 拆分提交并在每个 owner commit 后跑对应 focused gate，不再追加新的 helper/test 窗口，除非提交切分暴露真实失败。

2026-07-04:
- Owner: PDF parser / status lifecycle helper
- 完成: 新增 status_log_since_index() 与 should_refresh_task_from_upstream()，把 /api/status/<task_id> 的 since 参数归一化和取消任务刷新判定下沉到 pdf_parser_task_lifecycle_service；Flask route 仍保留 request/jsonify、上游刷新、异常映射、队列唤醒和 response payload。
- 测试: lifecycle/status 聚焦 14 passed；lifecycle + response + runtime state 组合 49 passed。
- 下一步: 继续避免扩大 PDF parser worker loop、queue claim 和 MinerU 网络调用；若继续 PDF parser，仅选择 route response 的纯派生或薄合同测试。

2026-07-04:
- Owner: Market reports / package build router contract
- 完成: 补强 _run_market_package_build() router 边界测试，锁住 HK/EU PDF 等需要 parser_result 的场景在 parser_result 路径不存在时返回 404 且不执行 run_command；生产代码不变。
- 测试: parser_result missing 聚焦 2 passed；Market reports owner 门禁 151 passed，2 个既有 Pydantic deprecation warnings；git diff --check passed。
- 下一步: Market build plan helper 继续视为完成态；后续不要扩大 market_reports.py，实现类任务转向其它未完成 owner 或提交切分验收。

2026-07-04:
- Owner: Job / worker middle design
- 完成: 新增 `2026-07-04-job-worker-middle-design.md`，明确 FileBackedJobService 与 workflow_job_service 的 schema / 执行模型差异，建议先以 canonical adapter + SQLite-backed local worker 作为中期路线，并把 Market ingestion eval 作为首个低风险迁移候选；未改生产执行代码。
- 测试: 文档设计窗口，无代码测试；git diff --check passed。
- 下一步: 迁移前先补 canonical adapter contract tests，继续禁止顺手合并 workflow `_workflow_jobs` 与 Market reports job schema。

2026-07-04:
- Owner: Job / worker canonical adapter contracts
- 完成: 补强 `job_envelope.py` canonical adapter，锁住 legacy Market / Workflow job 到 canonical envelope、canonical-native store reader、Market snake_case public projection、Workflow camelCase public projection，以及无 legacy step payload 时的 workflow step 回投；未迁移 job store、route schema、worker loop 或 subprocess owner。
- 测试: `test_job_envelope.py` 9 passed；Shared job/command adapter 组合 `test_job_envelope.py test_job_service.py test_workflow_job_service.py test_command_runner.py` 36 passed；job/queue focused gate `test_job_envelope.py test_job_service.py test_workflow_job_service.py test_market_report_queueing_service.py test_market_report_queueing.py test_market_reports_proxy.py` 93 passed，2 个既有 Pydantic deprecation warnings；`py_compile` passed。
- 下一步: 首个真实迁移候选仍限定为 Market ingestion eval queued job；迁移前需按 owner audit 跑 focused gate，并继续禁止顺手合并 workflow `_workflow_jobs` 与 Market reports job schema。

2026-07-04:
- Owner: Market ingestion eval / canonical queue adapter
- 完成: 在 `market_report_queueing.py` 为 `market-ingestion-eval` 接入 canonical adapter round-trip，queue start snapshot 与 job status read 先转 canonical 再投影回既有 Market snake_case public payload；`wait=true` inline 执行、`FileBackedJobService` 持久化 schema、线程生命周期、route schema 和 command execution 均未迁移。同步补强 sparse legacy payload 投影，避免测试 fake 或旧 job snapshot 被补出额外字段。
- 测试: `test_job_envelope.py test_market_report_queueing_service.py test_market_report_queueing.py test_market_reports_proxy.py` 78 passed，2 个既有 Pydantic deprecation warnings；first-migration focused gate `test_job_envelope.py test_job_service.py test_workflow_job_service.py test_market_report_queueing_service.py test_market_report_queueing.py test_market_reports_proxy.py` 97 passed，2 个既有 Pydantic deprecation warnings；`py_compile` passed。
- 下一步: 暂停继续扩大 job/worker 迁移；后续若继续，先做 owner split 验收或只补 Market job status/public payload 合同，仍禁止迁 Workflow `_workflow_jobs`、FileBackedJobService store schema 或 worker loop。

2026-07-04:
- Owner: Market reports / job status public payload contracts
- 完成: 补强 router-level `/jobs/{job_id}`（app 挂载后为 `/api/jobs/{job_id}`）route 合同测试，锁住底层返回 canonical `market-ingestion-eval` envelope 时仍投影为既有 Market snake_case public payload，且不泄漏 `schema_version`、`id`、`subject`、`steps`、`logs`、`attempts`、`source_schema`、`legacy_payload`、`target` 或 workflow camelCase 字段；生产代码不变。
- 测试: `test_market_report_queueing_service.py test_market_reports_proxy.py` 62 passed，2 个既有 Pydantic deprecation warnings；first-migration focused gate `test_job_envelope.py test_job_service.py test_workflow_job_service.py test_market_report_queueing_service.py test_market_report_queueing.py test_market_reports_proxy.py` 98 passed，2 个既有 Pydantic deprecation warnings；`py_compile tests/test_market_reports_proxy.py` passed。
- 下一步: job/worker 迁移窗口继续暂停；优先做 owner split 验收，或转向其它未触碰 owner 的纯合同测试，继续避开 Deal/IC/OpenClaw 与 Hermes smoke 并行改动。

2026-07-04:
- Owner: Market reports / job status HTTP route contracts
- 完成: 补强局部 FastAPI app + TestClient 合同，锁住 `/api/jobs/{job_id}` 的 200 JSON public payload 与 missing job 404 JSON `{"detail":"Job not found"}`；只覆盖 route HTTP envelope，不改生产代码、不引入 `main.app` 全量依赖。
- 测试: `test_market_report_queueing_service.py test_market_reports_proxy.py` 64 passed，2 个既有 Pydantic deprecation warnings 和 2 个既有 utcnow warnings；first-migration focused gate `test_job_envelope.py test_job_service.py test_workflow_job_service.py test_market_report_queueing_service.py test_market_report_queueing.py test_market_reports_proxy.py` 100 passed，2 个既有 Pydantic deprecation warnings 和 2 个既有 utcnow warnings；`py_compile tests/test_market_reports_proxy.py` passed。
- 下一步: Market job/worker 迁移继续暂停；优先进入 owner split 验收或选择未触碰 owner 的纯合同测试，不再扩大 Market queue/schema 面。

2026-07-04:
- Owner: Market/Shared owner split gate verification
- 完成: 停止新增功能面，按 `2026-07-04-worktree-owner-split-audit.md` 重跑当前受影响的 Market Reports API 与 Shared Job/Command focused gates；同步确认 owner audit touch set 已覆盖 Market/Shared 活跃文件，并补充 Deal/IC/OpenClaw 并行组的 `deal_evidence.py` 文档归属。
- 测试: Market Reports API owner gate 157 passed，2 个既有 Pydantic deprecation warnings 和 2 个既有 utcnow warnings；Shared Job/Command gate 37 passed；`py_compile services/job_envelope.py services/market_report_queueing.py tests/test_job_envelope.py tests/test_market_report_queueing_service.py tests/test_market_reports_proxy.py` passed；`git diff --check` passed。
- 下一步: 当前 v2 Market/Shared job adapter 批次进入提交切分/合并候选验收；后续不要继续扩大 Market queue/schema/job worker 面，优先处理 owner split 提交或选择完全独立的未触碰 owner。
```

详细技术记录可以新建独立 task note，例如：

```text
docs/architecture/YYYY-MM-DD-market-build-plan-helper.md
```
