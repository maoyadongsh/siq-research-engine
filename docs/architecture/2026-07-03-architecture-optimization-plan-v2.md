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
```

详细技术记录可以新建独立 task note，例如：

```text
docs/architecture/YYYY-MM-DD-market-build-plan-helper.md
```
