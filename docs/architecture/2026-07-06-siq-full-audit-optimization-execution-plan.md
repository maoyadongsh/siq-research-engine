# SIQ 全量检查优化执行方案

日期：2026-07-06

状态：可执行任务书

适用范围：`/home/maoyd/siq-research-engine`

## 目标

本方案基于 2026-07-06 对 SIQ Research Engine 的全量只读检查、测试验证和多视角智能体审阅结果生成。目标是在不降低项目质量、不牺牲事实精度、不大改前端视觉风格和交互功能的前提下，系统性提升项目的安全边界、解析可靠性、证据可追溯性、运行态路径治理、CI 可复现性和长期架构可维护性。

本方案不是重写项目，也不是重新设计产品 UI。它是一套可由 Codex 分阶段执行的优化任务书。

## 硬约束

1. 不降低事实精度：不得通过放宽质量门禁、弱化财务校验、删除 failing assertion、把 `fail` 改成 `warning` 等方式让测试通过。
2. 不大改前端风格：前端任务只做稳定性、性能、状态管理和小范围可用性修复，保留现有信息架构、视觉节奏、组件语义和主流程。
3. 不破坏已有本地数据：不得自动迁移、删除或重命名 `data/` 中的真实运行态文件；路径治理先做兼容层和文档，再做可控迁移。
4. 不回滚用户改动：当前工作区已有未提交修改，执行时必须先读相关文件并增量修改，不能使用 `git reset --hard`、`git checkout --` 等破坏性操作。
5. 不扩大安全暴露：不得新增无鉴权接口，不得把 parser token、数据库 URL、LLM key 或 source token 打到日志。
6. 不引入不可复现依赖：新增依赖必须锁定，Python 优先用已有 `uv.lock` 机制；parser requirements 需要专门补锁，不得临时放宽范围。
7. 不让 UI “看起来完成”：前端页面若展示入库、检索、质量门禁功能，必须有真实 API、状态和错误处理支撑。

## 当前基线

### 测试基线

检查时本地验证结果如下：

| 模块 | 命令 | 结果 |
| --- | --- | --- |
| API | `cd apps/api && uv run python -m pytest tests` | `1000 passed, 1 failed` |
| PDF parser | `cd apps/pdf-parser && python3 -m pytest tests` | `433 passed, 5 skipped, 1 failed` |
| Document parser | `cd apps/document-parser && python3 -m pytest tests` | `49 passed` |
| Market report finder | `cd services/market-report-finder && uv sync --extra dev && uv run python -m pytest tests` | `85 passed` |
| Market report rules | `cd services/market-report-rules && uv run --extra dev pytest` | `55 passed, 3 failed` |
| Market contracts | `cd packages/market-contracts && uv run python -m pytest tests` | `3 passed` |
| Web unit/build | `cd apps/web && npm run test:unit && npm run check:frontend` | `171 passed`，ESLint 和 Vite build 通过 |
| Script syntax | `bash -n ... && python3 -m py_compile ...` | 通过 |

### 当前必须修复的测试失败

1. API preflight context 契约漂移
   - `apps/api/tests/test_agent_runtime_chat_preflight.py:109`
   - `apps/api/services/agent_chat_runtime_impl.py:5481`
   - 表现：`_load_chat_run_preflight_context()` 新增必填 `message`，旧调用/测试未同步。

2. PDF parser EU financial check 状态漂移
   - `apps/pdf-parser/tests/test_pdf_parser_financial_service.py:508`
   - 表现：EU annual report 未抽到结构化报表时测试期望 `fail`，当前得到 `warning`。

3. Market rules EU/HK check 状态漂移
   - `services/market-report-rules/tests/test_eu_rules.py:184`
   - `services/market-report-rules/tests/test_hk_evidence_package.py:371`
   - `services/market-report-rules/tests/test_hk_evidence_package.py:447`
   - 表现：测试构造的完整报表桥接场景期望 `pass`，当前得到 `warning`。

## 执行协议

每个 Codex 执行任务都应遵守以下流程：

1. 先运行 `git status --short --branch`，确认工作区已有改动。
2. 只读取并修改当前任务明确列出的文件，除非测试证明必须扩展范围。
3. 先补或定位 failing test，再改生产代码。
4. 每个任务完成后至少运行该任务的目标测试；跨模块任务再运行相关组合测试。
5. 不把本方案所有任务混在一个提交里。建议按 P0/P1/P2 或更小任务拆分。
6. 修改路径、env、Docker、CI 时必须更新相关文档或示例。
7. 修复测试时必须解释业务语义，不能只改 assertion。

## P0：恢复质量基线与安全边界

P0 任务优先于功能扩展。完成 P0 后，项目应回到主要测试全绿，并补上最直接的权限、CSRF、敏感信息和运行暴露风险。

### P0-01 修复 API preflight context 契约

目标：恢复 `_load_chat_run_preflight_context()` 的调用兼容性，同时保留新增 `message` 对 preflight 的作用。

涉及文件：

- `apps/api/services/agent_chat_runtime_impl.py`
- `apps/api/tests/test_agent_runtime_chat_preflight.py`

建议实现：

1. 明确 `message` 是否是语义必需参数。
2. 若只用于新逻辑，优先让 `message: str = ""` 成为可选 keyword-only 参数，保留旧测试 patch point 的兼容性。
3. 若必须强制调用方传入，则更新所有调用点和测试，确保 `_load_chat_run_preflight_context()` 的公共测试契约不再悬空。
4. 补一条测试覆盖：传入 `message` 时 preflight 使用当前消息；未传入时仍只加载历史、local memory、attachments。

验收命令：

```bash
cd apps/api
uv run python -m pytest tests/test_agent_runtime_chat_preflight.py -q
```

通过标准：

- 该测试文件全绿。
- 不改变 active run、duplicate run、attachment preflight 的既有行为。

### P0-02 修复 EU/HK 财务校验状态回归

目标：恢复财务校验语义，不通过降低门禁让测试通过。

涉及文件：

- `apps/pdf-parser/pdf_parser_financial_service.py`
- `apps/pdf-parser/eu_market_profile.py`
- `services/market-report-rules/src/market_report_rules_service/validation.py`
- `services/market-report-rules/src/market_report_rules_service/markets/eu/extractor.py`
- `services/market-report-rules/src/market_report_rules_service/markets/hk/extractor.py`
- 可能涉及 `scripts/hk/hk_evidence_lib.py`
- 对应测试文件

修复原则：

1. EU annual report 缺失核心结构化报表时应保持高风险状态。不得把“报表未抽到”视为 pass。
2. 测试构造了完整三表和桥接关系时，应得到 pass。不得因为通用 warning 文案把完整场景降成 warning。
3. 对 `extraction.warnings` 和 `validation.warnings` 分级：信息性派生、fallback 提醒不应自动压低完整且桥接通过的结果；覆盖不足、必要报表缺失、hash mismatch、critical warning 才应阻断。
4. HK 从 parser `financial_data.json` 或 `result_complete.md` 恢复完整三表后，应保留来源证据并通过 required statements。

验收命令：

```bash
cd apps/pdf-parser
python3 -m pytest tests/test_pdf_parser_financial_service.py::test_write_financial_artifacts_dispatches_eu_market_to_eu_checks -q

cd ../../services/market-report-rules
uv run --extra dev pytest \
  tests/test_eu_rules.py::test_eu_split_balance_sheet_uses_equity_liabilities_context_and_shifted_header \
  tests/test_hk_evidence_package.py::test_build_hk_evidence_package_uses_parser_financial_data_when_table_rows_are_missing \
  tests/test_hk_evidence_package.py::test_build_hk_evidence_package_recovers_statement_tables_from_result_markdown \
  -q
```

通过标准：

- 上述 4 个失败用例全绿。
- 没有把 quality gate 整体放宽。
- financial checks 中 warning 与 fail 的含义可解释。

### P0-03 统一鉴权依赖并补写操作权限

目标：把“已登录”与“有权执行高成本/写操作”分开。

涉及文件：

- `apps/api/services/auth_dependencies.py`
- `apps/api/services/auth_service.py`
- `apps/api/routers/auth.py`
- `apps/api/routers/document_parser.py`
- `apps/api/routers/workspace.py`
- `apps/api/routers/settings.py`
- `apps/api/routers/system.py`
- 相关 API 测试

当前问题：

- `viewer` 只有 `report.view`、`company.view`，但可调用解析/上传/重试/修订接口。
- `services.auth_dependencies` 和 `routers.auth` 各有 `get_current_user/require_permission`，存在策略漂移。

建议实现：

1. 将 `get_current_user`、`require_permission` 的唯一实现收敛到 `services.auth_dependencies`。
2. `routers.auth` 保留 auth API，内部也复用 service dependency。
3. 新增或复用权限：
   - 最小改动：解析创建、重试、上传、修订使用 `require_permission("report.create")` 或 `report.edit`。
   - 更清晰改动：新增 `parse.create`、`parse.edit`、`parse.delete`，并更新角色权限矩阵。
4. 对以下接口增加写权限：
   - `POST /api/documents/tasks`
   - `POST /api/documents/import/mineru`
   - `POST /api/documents/retry/{task_id}`
   - `DELETE /api/documents/tasks/{task_id}`
   - `POST /api/documents/table-relations/.../review`
   - `POST /api/documents/logical-tables/.../split`
   - `POST /api/pdf/upload`
   - `POST /api/pdf/refetch/{task_id}`
   - `POST /api/pdf/reparse/{task_id}`
   - `POST /api/pdf/source/{task_id}/table/{table_index}/correction`
5. 管理类路由 `settings/system` 改为从 `services.auth_dependencies` 导入 `require_permission`。

必须新增测试：

- viewer 调用 `POST /api/documents/tasks` 返回 403。
- viewer 调用 `POST /api/pdf/upload` 返回 403。
- analyst 或 admin 调用上述接口保持可用。
- settings/system 的 dependency override 只需要 override 一处。

验收命令：

```bash
cd apps/api
uv run python -m pytest \
  tests/test_auth_dependencies.py \
  tests/test_auth_router_current_user.py \
  tests/test_document_parser_proxy.py \
  tests/test_downloads.py \
  tests/test_workspace_sync.py \
  -q
```

### P0-04 Cookie 模式增加 CSRF 防线

目标：在不破坏 bearer token 本地开发体验的前提下，保护 HttpOnly cookie 模式下的状态变更请求。

涉及文件：

- `apps/api/main.py`
- `apps/api/services/auth_service.py`
- `apps/api/routers/auth.py`
- `apps/web/src/shared/api/client.ts`
- `apps/web/src/lib/auth.tsx`
- 相关测试

建议实现：

1. Cookie mode 登录时设置额外非 HttpOnly CSRF cookie，或在 `/api/auth/me` 返回 CSRF token。
2. 前端 `apiFetch` 在 cookie mode 且请求为非 GET/HEAD/OPTIONS 时带 `X-CSRF-Token`。
3. 后端增加 dependency 或 middleware：
   - 仅在 `SIQ_AUTH_COOKIE_MODE=1` 且认证来源是 cookie 时强制校验。
   - Bearer token 请求不要求 CSRF，保留脚本/本地调试兼容。
   - 校验 `Origin/Referer` 必须是允许来源。
4. logout 清理 CSRF cookie。

必须新增测试：

- cookie mode 下 POST 无 CSRF header 返回 403。
- cookie mode 下 POST 带正确 CSRF header 通过。
- bearer token POST 不受 CSRF 影响。
- SameSite=None 时 Secure 自动启用的现有逻辑不被破坏。

验收命令：

```bash
cd apps/api
uv run python -m pytest tests/test_auth_dependencies.py tests/test_auth_router_current_user.py -q

cd ../web
npm run test:unit
```

### P0-05 收紧系统级列表默认可见性

目标：普通用户默认只看到自己的 workspace artifacts；系统级队列/下载清单只有管理员或显式 ops mode 可见。

涉及文件：

- `apps/api/routers/downloads.py`
- `apps/api/routers/workspace.py`
- `apps/web/src/features/pdf-parsing/api.ts`
- 相关测试

当前问题：

- `SIQ_DOWNLOAD_LIST_WORKSPACE_ONLY` 未设置时，普通用户可看到系统下载元数据。
- `SIQ_PDF_TASK_LIST_WORKSPACE_ONLY` 未设置时，普通用户可看到全局 PDF parser 队列。

建议实现：

1. 默认 workspace-only。
2. 新增显式 env：
   - `SIQ_DOWNLOAD_LIST_SYSTEM_VISIBLE=1`
   - `SIQ_PDF_TASK_LIST_SYSTEM_VISIBLE=1`
3. 管理员可通过 query 参数或 admin route 看系统视图。
4. 前端保持现有列表样式，不改变交互；只处理空态和权限提示。

验收命令：

```bash
cd apps/api
uv run python -m pytest tests/test_downloads.py tests/test_workspace_sync.py -q
```

## P1：解析链路、证据链和 AI 输出精度

P1 聚焦 SIQ 的核心价值：官方来源可信、解析可复跑、证据可定位、质量门禁可信。

### P1-01 Parser task 增加 owner/tenant/market scope

目标：让 PDF parser 和 document parser 的任务与 artifact 从底层开始具备 owner 边界。

涉及文件：

- `apps/pdf-parser/pdf_parser_task_repository.py`
- `apps/pdf-parser/pdf_parser_app_impl.py`
- `apps/pdf-parser/pdf_parser_request_utils.py`
- `apps/document-parser/task_store.py`
- `apps/document-parser/app.py`
- `apps/api/routers/workspace.py`
- `apps/api/routers/document_parser.py`

建议实现分两步：

第一步，API 代理层加 owner header：

- API 调 parser 服务时带 `X-SIQ-User-Id`、`X-SIQ-User-Role`、`X-SIQ-Market-Scope`。
- parser 若 header 缺失，兼容为 legacy/system owner，但在 response 中标记。

第二步，parser schema 加字段：

- PDF tasks 增加 `owner_id`, `tenant_id`, `market_scope`, `parse_config_hash`。
- Document tasks 增加同等字段。
- 查询、列表、下载、删除、artifact、source page 全部按 owner 过滤。
- admin/system token 可以显式绕过，但必须审计。

不得做：

- 不要直接删除旧 tasks.db。
- 不要一次性迁移真实 data 目录。
- 不要把 owner 仅存在 API 的 UserArtifact 表里，parser 内部也要能保护裸 task_id。

验收标准：

- 非 owner 无法读取、下载、删除 task。
- admin 可查看 system scope。
- legacy task 可读策略明确，默认仅 admin 或原 link owner。

### P1-02 PDF 去重和幂等键纳入 owner、market、解析配置

目标：避免跨用户、跨市场、跨配置误判重复任务。

涉及文件：

- `apps/pdf-parser/pdf_parser_app_impl.py`
- `apps/pdf-parser/pdf_parser_task_repository.py`
- `apps/pdf-parser/pdf_parser_request_utils.py`
- 相关测试

建议实现：

1. 计算 `parse_config_hash`，至少包含：
   - market
   - backend
   - parse_method
   - page range
   - formula/table enable
   - parser version
2. 去重 key 改为：
   - `owner_id + market + file_sha256 + parse_config_hash`
3. 文件名只用于展示，不作为强去重条件。
4. 对重复任务返回当前 owner 的已有 task，不泄漏其他 owner task id。
5. 保留显式 `force/reparse`。

### P1-03 官方来源 URL 归属和 redirect 校验

目标：防止用户提供 URL 被标记为官方来源，从而污染 evidence chain。

涉及文件：

- `services/market-report-finder/src/market_report_finder_service/api/routes/reports.py`
- `services/market-report-finder/src/market_report_finder_service/services/orchestrator.py`
- `services/market-report-finder/src/market_report_finder_service/services/downloader.py`
- `services/market-report-finder/src/market_report_finder_service/markets/*/service.py`

建议实现：

1. 每个 market source 提供 `owns_url(url)` 或 host allowlist。
2. direct/batch download 前校验 scheme 和 host。
3. downloader 跟随 redirect 后复核 effective URL。
4. 不属于官方 allowlist 的 URL 标记为 `manual_unverified`，不得写成 CNINFO/HKEX/SEC/EDINET/DART 官方 source。
5. metadata 中保留 original_url、effective_url、source_verification_status。

验收标准：

- 非官方 URL 不能进入 official source chain。
- redirect 到非官方域名失败或降级为 manual unverified。
- 原有官方 CN/HK/US/EU/JP/KR 下载测试通过。

### P1-04 下载器改为 streaming、大小限制、原子写

目标：降低大文件内存峰值，避免并发 index 损坏和半文件污染。

涉及文件：

- `services/market-report-finder/src/market_report_finder_service/services/downloader.py`
- 相关测试

建议实现：

1. 使用 streaming 下载，不直接依赖 `response.content`。
2. 设置 per-market 最大字节数和 content-type 检查。
3. 写入 `.tmp` 文件，完成 hash 校验后 atomic rename。
4. metadata/index 写入也使用 tmp + replace。
5. 对 index 更新加进程内锁；如未来多进程部署，再升级为 file lock。

### P1-05 LLM 表格判定缓存键使用完整上下文

目标：避免旧文档、旧年份、旧 missing statement 状态污染新任务。

涉及文件：

- `apps/pdf-parser/financial_extractor.py`
- 相关测试

建议实现：

1. cache key 从 `table_hash/prompt_version/model` 升级为完整标准化 request payload hash。
2. payload 至少包含：
   - filename
   - task_id 或 stable document id
   - report_year
   - market
   - missing_statement_types
   - rule evidence
   - table text/hash
   - prompt version
   - model id
3. cache value 保存 raw response、parsed decision、schema validation、created_at、expires_at。
4. LLM accepted evidence 与 rule extracted evidence 明确分层，不混成同一种 source。

### P1-06 证据契约增加 resolvability gate

目标：`evidence_coverage_ratio` 只统计真正可打开、可定位、可复核的证据。

涉及文件：

- `packages/market-contracts/src/siq_market_contracts/evidence_package.py`
- `services/market-report-rules/src/market_report_rules_service/evidence_package.py`
- `apps/pdf-parser/financial_extractor.py`
- 各 market evidence package 脚本
- 相关 contract tests

建议实现：

1. 定义 metric source 最小可定位结构：
   - PDF：`page_number + table_index + row/column 或 quote`
   - HTML/XBRL：`url + anchor/xpath/tag`
   - 本地 artifact：`artifact_path + line/table/cell`
2. `sources` 非空但不可定位时，不计入 coverage。
3. quality report 增加 `unresolvable_evidence_count`。
4. action gate 对不可定位比例设置 warning/fail threshold。

### P1-07 Market rules 禁止静默 legacy fallback

目标：避免 evidence package contract 漂移。

涉及文件：

- `services/market-report-rules/src/market_report_rules_service/evidence_package.py`
- `services/market-report-rules/pyproject.toml`
- `packages/market-contracts/src/siq_market_contracts/evidence_package.py`
- CI 配置

建议实现：

1. rules service 显式依赖 `siq-market-contracts`。
2. 生产导入失败时 fail fast，不再静默 fallback 到 `_legacy_evidence_package.py`。
3. legacy 文件只作为迁移参考，测试不应依赖它。
4. CI 增加测试：实际导入模块路径必须来自 `packages/market-contracts`。

### P1-08 评估 gate 覆盖所有市场并收敛数据集入口

目标：让多市场质量回归真正进入日常检查。

涉及文件：

- `scripts/maintenance/run_market_ingestion_eval.py`
- `scripts/maintenance/tests/test_run_market_ingestion_eval.py`
- `datasets/market_ingestion/`
- `eval_datasets/market_ingestion_cases/`
- `docs/architecture/2026-07-06-siq-evaluation-system.md`

建议实现：

1. 新增默认评估入口：`datasets/market_ingestion/secondary_market_mvp_cases.json`。
2. `eval_datasets/market_ingestion_cases` 作为 legacy fallback。
3. `quality_thresholds` 对 CN/HK/US/EU/JP/KR 通用化。
4. gate 至少覆盖：
   - package exists
   - manifest schema
   - quality_status
   - required statement coverage
   - evidence resolvability
   - bridge check status
   - artifact hash status
5. CI 中跑小样本 eval，不跑大文件下载。

## P2：前端稳定性和性能，不改主风格

P2 只做稳定性、性能和状态边界，不重做设计系统。

### P2-01 PDF 轮询避免并发重入

涉及文件：

- `apps/web/src/pages/pdf/usePdfTasks.ts`

建议实现：

1. 用 `setTimeout` 链式轮询替代 `setInterval`，或加 `pollInFlightRef`。
2. 每次请求带当前 task id，响应回来后确认 `taskIdRef.current === tid`。
3. 用 `AbortController` 取消旧任务请求。
4. 日志按 log id 或 log_count 去重，避免重复 append。

验收命令：

```bash
cd apps/web
npm run test:unit
npm run check:frontend
```

### P2-02 已下载 PDF 解析改为服务端引用入队

涉及文件：

- `apps/web/src/features/pdf-parsing/api.ts`
- `apps/web/src/pages/pdf/usePdfTasks.ts`
- `apps/api/routers/workspace.py`
- `apps/pdf-parser/pdf_parser_app_impl.py`

当前问题：

- 前端把已下载 PDF 完整下载成浏览器 `File`，再重新上传解析，造成双倍流量和内存峰值。

建议实现：

1. API 增加 `POST /api/pdf/tasks/from-download` 或扩展现有 upload route，接受 `download_relative_path`。
2. 后端校验 workspace link 和 path whitelist 后，把文件引用传给 parser。
3. 前端保留按钮文案和流程，只把数据传输方式从 blob upload 改成 path reference。
4. 仍保留手动上传本地文件的旧流程。

### P2-03 收敛 API request client，移除全局 fetch patch

涉及文件：

- `apps/web/src/main.tsx`
- `apps/web/src/lib/fetchWithAuth.ts`
- `apps/web/src/shared/api/client.ts`
- `apps/web/src/lib/agentChatStore.ts`
- `apps/web/src/features/primary-market/primaryMarketApi.ts`

建议实现：

1. `shared/api/client.ts` 成为唯一 request client。
2. SSE/stream 增加显式 `apiStreamFetch` 或 `apiFetch` 直接支持。
3. 移除 `installFetchAuth()` 全局 monkey patch。
4. 保留 `fetchWithAuth` 作为薄 wrapper 过渡，但内部调用 `apiFetch` 或标记 deprecated。
5. 测试覆盖 bearer、cookie mode、absolute URL、non-SIQ URL 不带 auth。

### P2-04 一级市场会议流式输出节流

涉及文件：

- `apps/web/src/pages/PrimaryMarketMeeting.tsx`
- `apps/web/src/lib/agentChatStream.ts`

建议实现：

1. token delta 先写入 ref buffer。
2. 用 `requestAnimationFrame` 或 50-100ms timer 批量 flush 到 React state。
3. 将 chat message state 下沉到局部组件或 store，减少整页重渲染。
4. 保留当前 UI 和交互，不改变会议布局。

### P2-05 大 Markdown 结果渲染优化

涉及文件：

- `apps/web/src/components/pdf/PdfMarkdownPreview.tsx`

建议实现：

1. 不引入新视觉风格。
2. 最小方案：按章节折叠或只渲染可视窗口附近行。
3. 复制、下载仍基于完整 markdown。
4. focused line 跳转必须可用。

### P2-06 MarketEvidencePackagesPanel 交互修复

涉及文件：

- `apps/web/src/components/pdf/MarketEvidencePackagesPanel.tsx`
- `apps/web/src/pages/HkParsing.tsx`
- `apps/web/src/pages/EuParsing.tsx`
- `apps/web/src/pages/JpParsing.tsx`
- `apps/web/src/pages/KrParsing.tsx`
- `apps/web/src/pages/UsParsing.tsx`

建议实现：

1. 查询状态拆成 `draftQuery` 和 `submittedQuery`，避免每次输入自动请求。
2. 加 AbortController，旧请求不能覆盖新请求。
3. `forceConfirmed` 按具体 action blocked key 判断：`import_blocked` 或 `vector_ingest_blocked`。
4. 如果产品需要该 panel，就挂到现有 market parsing page 的既有扩展区域，不改变主风格。
5. 如果暂不展示，则删除死入口或加明确 feature flag，避免死功能。

## P3：文件夹与路径治理

路径治理不是“整理目录好看”，而是降低密钥误提交、运行态污染、评测样本遗漏和部署迁移成本。

### 目标目录语义

建议保持如下语义：

| 目录 | 定位 | 是否可提交 |
| --- | --- | --- |
| `apps/` | 前后端和 parser 应用 | 是 |
| `services/` | 独立服务 | 是 |
| `packages/` | 共享库和契约 | 是 |
| `agents/` | Hermes profiles、规则、模板、脚本 | 是，需避免 runtime auth |
| `scripts/` | 运维、评估、迁移、批处理脚本 | 是 |
| `infra/` | Docker、env example、systemd、supervisor | 是 |
| `db/` | DDL、DML、迁移、导入脚本 | 是 |
| `docs/` | 架构、操作、任务书 | 是 |
| `datasets/` | 新增稳定、小型、可版本化样本 | 是 |
| `eval_datasets/` | 历史评测语料和兼容回归集 | 是，但不再作为新增首选 |
| `artifacts/` | 单次运行、评测、构建产物 | 默认不提交，README 可提交 |
| `var/` | 新运行态推荐根目录 | 默认不提交，README 可提交 |
| `data/` | 历史兼容运行态 | 默认不提交，逐步迁出 |
| `downloads/` | 旧下载或临时下载 | 不提交 |
| `runtimes/` | 本机模型/运行环境 | 不提交 |

### P3-01 将真实运行态移出 repo 树

现状：

- `data/` 约 48G。
- `data/backend/agent.db`、`data/backend/chat_uploads/*`、`data/hermes/home/auth.json` 等真实运行态位于 repo 树内。

建议：

1. 新增推荐环境变量：
   - `SIQ_LOCAL_STATE_ROOT=/var/lib/siq-research-engine` 或用户指定外部盘。
2. `SIQ_DATA_ROOT`、`SIQ_RUNTIME_ROOT`、`SIQ_ARTIFACTS_ROOT` 从该根派生。
3. `start_all.sh` 默认仍兼容当前 `data/`，但文档推荐新路径。
4. 提供只读迁移脚本 `scripts/migration/plan_runtime_paths.py`，只生成迁移计划，不移动文件。
5. 后续人工确认后再提供迁移执行脚本。

不得做：

- 不要自动移动 48G `data/`。
- 不要删除 `data/hermes/home/auth.json` 等用户本地状态。

### P3-02 env 路径收口

现状：

- 规范路径：`infra/env/local.example`、`infra/env/local.env`
- 兼容路径：`env/backend.env`、`env/frontend-dev.env`
- 服务内局部 `.env`：`services/market-report-finder/.env`

建议：

1. 所有文档和脚本首选 `infra/env/local.env`。
2. `env/backend.env`、`env/frontend-dev.env` 只保留兼容读取，启动日志提示迁移。
3. 服务目录下 `.env` 不再推荐，迁到 `infra/env/local.env` 或用户 shell profile。
4. 对真实-looking key 执行轮换，并增加 secret scan。

### P3-03 评测数据入口收口

现状：

- 新 MVP 样本在 `datasets/market_ingestion/`。
- 旧 harness 默认读 `eval_datasets/market_ingestion_cases`。

建议：

1. 新增样本默认进入 `datasets/`。
2. `eval_datasets/` 只作为 legacy。
3. `scripts/maintenance/run_market_ingestion_eval.py` 默认读 `datasets/market_ingestion`，保留 `--legacy-case-root`。
4. 文档中明确新增样本路径和 artifact 输出路径。

### P3-04 root wrapper 和 canonical infra 路径

现状：

- 根 `docker-compose.yml` 是 compatibility wrapper。
- canonical compose 位于 `infra/docker/docker-compose.yml`。

建议：

1. 保留 root wrapper，但 CI、文档、README 都使用 canonical path。
2. 新增 compose check：

```bash
docker compose -f infra/docker/docker-compose.yml --env-file infra/env/local.example config
```

## P4：DevOps、CI 和供应链

### P4-01 端口暴露和容器硬化

涉及文件：

- `infra/docker/docker-compose.yml`
- 各 Dockerfile

建议：

1. 本地 compose 默认端口绑定 `127.0.0.1`。
2. Postgres、Redis 默认不对外 publish，或只绑定 localhost。
3. Grafana 不使用 `latest`。
4. 容器使用非 root 用户。
5. parser 类服务增加 resource limit、pids limit、cap drop。
6. 生产部署通过反代和内网服务访问，不直接暴露 parser 和 DB。

### P4-02 依赖安装可复现

涉及文件：

- `start_all.sh`
- `scripts/check_all.sh`
- `apps/pdf-parser/requirements.txt`
- `apps/document-parser/requirements.txt`
- Dockerfile

建议：

1. `start_all.sh` 默认：
   - `uv sync --frozen`
   - `npm ci`
2. 增加 `SIQ_UPDATE_DEPS=1` 时才允许 `uv sync` 和 `npm install`。
3. parser requirements 生成锁或 constraints，Docker 和 CI 共用。
4. `scripts/check_all.sh` 对 uv 项目使用 frozen。

### P4-03 CI 增加安全和部署门禁

涉及文件：

- `.github/workflows/ci.yml`

建议增加：

1. secret scan：`gitleaks` 或 `trufflehog`。
2. shell/action lint：`shellcheck`、`actionlint`。
3. compose config check。
4. Dockerfile lint：`hadolint`。
5. 轻量安全扫描：`trivy fs`。
6. 小样本 market ingestion eval。
7. Playwright 增加 cookie mode、SSE、market package gate、PDF 大 markdown smoke。

## 推荐执行顺序

### 第一批：恢复测试和权限底线

- P0-01 API preflight context
- P0-02 EU/HK financial check 状态回归
- P0-03 鉴权依赖和写操作 RBAC

完成后运行：

```bash
cd /home/maoyd/siq-research-engine
./scripts/check_all.sh
```

### 第二批：安全边界

- P0-04 CSRF
- P0-05 系统级列表默认 workspace-only
- P4-01 compose localhost binding 和日志脱敏

完成后运行：

```bash
cd apps/api
uv run python -m pytest tests/test_auth_dependencies.py tests/test_auth_router_current_user.py tests/test_downloads.py tests/test_workspace_sync.py -q

cd ../web
npm run test:unit
npm run check:frontend
```

### 第三批：解析与证据链精度

- P1-01 parser owner scope
- P1-02 去重/幂等 key
- P1-03 官方 URL 归属
- P1-04 downloader streaming + atomic write
- P1-06 evidence resolvability gate

完成后运行：

```bash
cd apps/pdf-parser
python3 -m pytest tests

cd ../document-parser
python3 -m pytest tests

cd ../../services/market-report-finder
uv run python -m pytest tests

cd ../market-report-rules
uv run --extra dev pytest

cd ../../packages/market-contracts
uv run python -m pytest tests
```

### 第四批：前端性能和状态收口

- P2-01 PDF 轮询
- P2-02 下载 PDF 服务端引用解析
- P2-03 request client 收口
- P2-04 一级市场 stream 节流
- P2-05 Markdown 渲染优化

完成后运行：

```bash
cd apps/web
npm run test:unit
npm run check:frontend
npm run e2e
```

### 第五批：路径治理和 CI

- P3-01 到 P3-04
- P4-02 依赖可复现
- P4-03 CI 安全门禁

完成后运行：

```bash
cd /home/maoyd/siq-research-engine
bash -n scripts/check_all.sh start_all.sh apps/api/start.sh apps/pdf-parser/run.sh apps/document-parser/run.sh
docker compose -f infra/docker/docker-compose.yml --env-file infra/env/local.example config
```

## 最终验收

全量验收目标：

1. `scripts/check_all.sh` 通过。
2. `apps/web npm run test:unit && npm run check:frontend` 通过。
3. Playwright smoke 通过。
4. P0 权限/CSRF/系统列表测试通过。
5. Parser owner boundary 测试通过。
6. Market ingestion 小样本 eval 通过。
7. `data/` 不再新增新的默认运行态路径依赖；新路径使用 `SIQ_*` 显式配置。
8. Compose 默认不向所有网卡暴露数据库、Redis、parser。
9. Secret scan 不报真实密钥。

## 非目标

本方案不包含：

- 重做前端设计系统。
- 替换 Hermes 或多智能体框架。
- 把所有 parser 一次性改成分布式队列。
- 自动迁移 48G 运行态数据。
- 引入新的数据库产品替换现有 SQLite/PostgreSQL/Milvus。
- 为了赶进度降低 evidence package 的 quality gate。

## 给后续 Codex 的最小启动提示

建议每次执行时使用类似提示：

```text
请阅读 docs/architecture/2026-07-06-siq-full-audit-optimization-execution-plan.md。
执行其中的 P0-01，严格遵守硬约束和执行协议。
先确认当前 git status，只修改任务列出的文件。
补/修测试后运行指定验收命令，并在最终回复里说明改动文件和测试结果。
```

如果要并行执行，请按不重叠写集拆分：

- Worker A：P0-01 API preflight。
- Worker B：P0-02 PDF parser EU check。
- Worker C：P0-02 market-report-rules EU/HK check。
- Worker D：P0-03 auth dependency/RBAC。

不要让多个 worker 同时修改同一个大文件，例如 `apps/api/services/agent_chat_runtime_impl.py`、`apps/api/routers/workspace.py`、`apps/pdf-parser/pdf_parser_app_impl.py`。
