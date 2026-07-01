# SIQ Research Engine 仓库与架构优化落地方案

日期：2026-06-29
适用仓库：`/home/maoyd/siq-research-engine`
目标读者：后续接手落盘的架构、后端、前端、数据工程、运维窗口
文档性质：优化方案、执行记录与后续任务拆解

## 0. 结论摘要

本项目已经从单体工作台演进为本地研究平台型 monorepo：

```text
Web 工作台
  -> API 控制面
  -> PDF / 通用文档解析服务
  -> 多市场公告下载服务
  -> 多市场规则服务
  -> evidence package / Wiki / PostgreSQL / Milvus
  -> Hermes 多智能体
```

当前主要问题不是技术栈错误，而是工程边界开始混合：

- 源码仓库和运行态数据混合：`data/` 已被 `.gitignore` 忽略，但仍有大量 PDF、SQLite、备份、下载 manifest 被 Git 跟踪。
- 控制面 API 过胖：`apps/api/routers/market_reports.py` 同时承担代理、上传、包浏览、脚本执行、入库、向量化和内存任务状态。
- 长任务执行方式偏本地脚本化：后台线程、进程内 dict、同步 `subprocess` 无法支撑重启恢复、多 worker、取消、重试、审计。
- 数据契约分散：API、finder、rules、脚本分别理解下载目录、evidence package、manifest、source map。
- 前端模块边界不足：PDF 解析页、多市场解析页、搜索下载页、文档结果工作台等大型页面/组件承担太多职责。
- 环境配置、启动脚本、Docker Compose 和本地运行形态尚未完全统一。

本方案建议按“先治理边界，再拆架构，再补测试”的顺序推进：

1. P0：仓库路径与运行态数据治理。
2. P0：API 控制面瘦身和任务执行收口。
3. P1：共享数据契约与 typed settings。
4. P1：前端 feature 化与 API client 收口。
5. P1：finder/rules/parser 服务边界收紧。
6. P2：测试、观测、文档和 CI 门禁补齐。

### 0.1 2026-06-30 现状复核

本轮重新检查后，方案应更新为以下状态：

- 已完成：`B-001` 的 market settings 收口、`B-002` 的 `MarketPackageRepository` 抽取、`C-001` 的 `packages/market-contracts` 初版；后端和前端的基础门禁也已通过。
- 已完成：`F-001` 的前端 route registry 已接入 `App.tsx`、`routePreload.ts`、`layoutData.ts`，并补上 `/forbidden` 页面。
- 已完成：`R-001` 的 tracked runtime data 已从 Git 索引移出，本地运行态文件保留不动。
- 已完成阶段性拆分：`P-001` 已将 PDF parser 入口 façade、请求 helper、运行时 helper、page marker、SQLite task repository、artifact service 和 source service 下沉；`A-001` 已将 Agent runtime 入口 façade、loop guard 和 progress/tool-label helper 下沉。
- 进展补充：`F-002` 已补齐共享 workbench 和市场隔离验证；`F-003` 已新增 `shared/api/client.ts`，并将 `pdfApi`、`documentApi`、`secApi`、`Settings`、`Dashboard`、`ReportViewer`、`NotificationMenu`、`VectorIngest`、`ChatAttachmentList`、`DocumentResultWorkbench`、`PdfSourceWorkbench` 迁入共享请求层和 feature 门面；业务组件/页面已不再直接导入 `lib/apiClient`、`lib/pdfApi`、`lib/secApi`、`lib/documentApi`，E2E/mock 规则也已修正；`features/document-parser/api.ts`、`features/pdf-parsing/api.ts`、`features/market-parsing/api.ts` 已接管对应 API 实现，`lib/documentApi.ts`、`lib/pdfApi.ts`、`lib/secApi.ts` 仅保留兼容 re-export。
- 已完成：`R-003` 已按主题拆成 8 个提交，运行态/构建产物仍保持 ignored，不进入索引。
- 进展补充：`F-004` 已完成 `PdfSourceWorkbench.tsx` 第二阶段拆分，新增 `pdfSourceWorkbenchHelpers.ts`，把页码/bbox、跨页表关系、overlay 构建和物理表合并等纯 helper 搬出；`SearchDownload.tsx` 已完成 model/table/downloaded panel、search/download flows、URL state、日志派生、download refresh 判定和 toast 文案 helper 拆分；`index.css` 已将 search/download、dashboard、通用 surface/button/search、quick-question、chat rendered/table/code、agent dock/composer、chat page shell 以及 root/body/dark/focus/reduced-motion/app spacing 全局基线迁到 `styles/search-download.css`、`styles/dashboard.css`、`styles/system-surfaces.css`、`styles/quick-questions.css`、`styles/chat.css`、`styles/app-base.css`，`index.css` 退为 import + theme 外壳；Document/PDF/Market parsing 的 feature API 已成为实现 owner，`lib/documentApi.ts`、`lib/pdfApi.ts`、`lib/secApi.ts` 退为兼容 re-export；`DocumentResultWorkbench.tsx` 已完成纯 utils、source preview、artifact/table/figure/status/extract/markdown panes、source lookup、table lookup、focused relation、preview page model 和 JSON preview 派生拆分，父组件保留 overlay `data-*`、mobile tab、refs、selection/scroll 和 resource open owner；移动端工作平台/系统平台宽度不一致已用响应式 E2E 固化。
- 进展补充：`P-002` 已完成 quality/financial/document_full/content_list_enhanced/MinerU result 第一轮边界拆分，新增 `pdf_parser_quality_service.py`、`pdf_parser_financial_service.py`、`pdf_parser_document_full_service.py`、`pdf_parser_content_list_enhanced_service.py`、`pdf_parser_mineru_result_service.py`、`pdf_parser_response_service.py` 与聚焦测试；`pdf_parser_artifact_service.py` 已新增 open artifact name 纯分类 helper，并已最小接入 `open_artifact` Flask route，覆盖 images/download、images、images/<name>、allowlist artifact、forbidden artifact、missing artifact 和空图片下载；`pdf_parser_document_full_service.py` 已继续收拢 table relations payload、content_list_enhanced 回写 document_full 的纯 payload helper，并补强 relation table merge、relation alias 回填、无效表过滤、file reference、缺失 source/resource 状态和 content_list_enhanced 回写初始化覆盖；`pdf_parser_quality_service.py` 已补强银行资产负债表附近表定位噪声过滤、季度报告核心表规则、`equity_statement` 回填“所有者权益变动表”、key_metrics 回填“主要会计数据”时继承 `table_index` 表源元数据覆盖、statement display source 遇到噪声 table index 时回落附近真实资产负债表、非数字 `line_numbers` 防御、`candidate_summary_list` 和 `priority_review_tables` 规则覆盖；`pdf_parser_financial_service.py` 已补 financial schema/rule mismatch、单边 artifact 读取和 stale checks 触发重写覆盖；`pdf_parser_content_list_enhanced_service.py` 已继续收拢 `build_content_list_enhanced_payload` 顶层 payload 组装、table source 映射/匹配、打印页码映射、Markdown 页码推断、脚注/Markdown 行号、目录/标题 helper 和 enhanced quality signals 聚合；`pdf_parser_app_impl.py` 状态 owner 已清单化，仍保留 Flask route response、task state、queue claim、路径存在性、文件写入、`_fetch_and_cache_result` 和 `_ensure_*` 重编排 owner。
- 进展补充：`A-002` 已完成 tool output、parse-only discovery、attachment display、citation/evidence 渲染 helper、PostgreSQL fallback row helpers、local-memory 纯 helper、runtime dedupe helper、context/company helper、analysis completion guard intent helper、general assistant context input helper、multi-company session context helper、Hermes run input text/multimodal helper、statement/note detail intent helper、attachment classification helper、PDF2MD parse-only alias/match helper 和 citation record label helper 下沉，新增/扩展 `agent_runtime_tool_output.py`、`agent_runtime_parse_only.py`、`agent_runtime_display.py`、`agent_runtime_citations.py`、`agent_runtime_fallback_contexts.py`、`agent_runtime_memory.py`、`agent_runtime_dedupe.py`、`agent_runtime_context.py` 与聚焦测试；已补 `pdf_page_number` / `markdown_line` 引用别名去重、supplement 引用合并、正文已有引用去重、LaTeX inline symbol normalization、source locator 默认值与链接追加、source ref 去重编号、auto evidence section strip、requested metric evidence guard、markdown link label 清洗、附件 path basename 与通用 attachment 标签兜底、附件 URL Markdown target 编码、交易所前缀文件名股票代码/公司名匹配、parse-only 无匹配返回空、general/company-dir 短路，以及跳过已存在 Wiki 后再应用 `limit` 的覆盖；`agent_chat_runtime_impl.py` 仍保留 `ACTIVE_RUNS`、SSE append、run lifecycle、DB session memory 刷新和普通 chat/streaming 共享状态 owner。
- 当前建议：按 0.3 的剩余工作量评估继续推进。下一轮优先继续 PDF parser 的 quality/status payload 纯 helper，或继续做 Agent runtime 只读 helper 覆盖；前端只保留 `PDF_CSS` / `DOCUMENT_CSS` 运行时字符串单独评估和必要响应式 smoke，继续避开 `ACTIVE_RUNS`、SSE lifecycle、本地 queue claim 和 Flask `send_file/jsonify` 行为变更。

### 0.2 2026-06-30 深度全量检查结论

本次全量复核覆盖 Git 索引、目录结构、关键大文件、后端/前端/服务测试和启动入口。结论：

- 仓库索引治理有效：`git ls-files data` 只剩 `data/README.md`、`data/backend/.gitkeep`、`data/pdf-parser/.gitkeep`。
- `R-003` 之前工作树非常脏：`git status --short | wc -l` 约 725 行，包含大量已从索引移出的 data 删除项、前端/后端重构改动、未跟踪新模块和生成目录；该风险已通过分组 review/提交收口。
- `.gitignore` 已覆盖 `data/**`、`var/**`、`artifacts/**`、`**/.venv/`、`**/.pytest_cache/`、`**/__pycache__/`、`apps/web/dist/`、`apps/web/test-results/`、`apps/web/playwright-report/` 等运行态和生成目录；本地仍存在大量 ignored cache/runtime 目录，不应纳入提交。
- 当前最大剩余大文件：`agent_chat_runtime_impl.py` 已降至约 6577 行、`pdf_parser_app_impl.py` 已降至约 4195 行、`apps/web/src/index.css` 已降至约 85 行，新增 `apps/web/src/styles/app-base.css` 约 162 行，`apps/web/src/styles/chat.css` 约 1121 行，`SearchDownload.tsx` 约 961 行但 download refresh/toast 派生已拆到 feature helper，`DocumentResultWorkbench.tsx` 已降至约 548 行；`PdfSourceWorkbench.tsx` 已降至约 708 行，新增的 `pdfSourceWorkbenchHelpers.ts` 约 742 行，后续可继续按 UI/数据派生边界拆分。
- 前端 route registry 已单源化；API client 核心能力已收口到 `shared/api/client.ts`，业务组件/页面已迁到 `features/*/api.ts` 或 shared client；`lib/documentApi`、`lib/pdfApi`、`lib/secApi` 已降为 feature API 兼容 re-export，`lib/apiClient` 暂作为 shared client 兼容出口保留。
- PDF parser 已完成入口 façade、request/runtime/page-marker/task-repository/artifact/source 第一阶段拆分；quality/financial/document_full/content_list_enhanced/MinerU 原始产物落盘已完成第一轮 service 下沉，`pdf_parser_app_impl.py` 仍保留任务状态、路由响应、queue claim 和 `_ensure_*` 编排。
- Agent runtime 已完成入口 façade、loop guard、progress/tool label、tool output normalization、parse-only discovery、display normalization、citation/evidence 渲染 helper、PostgreSQL fallback row helpers 与 local-memory 纯 helper 第一阶段拆分；`ACTIVE_RUNS`、SSE run owner、普通 chat 与 streaming 的共享状态仍必须留在 `agent_chat_runtime_impl.py`，下一阶段只搬同类纯函数。

本次验证基线：

```bash
cd apps/pdf-parser && python3 -m pytest tests -q                  # 150 passed
cd apps/pdf-parser && python3 -m flask --app app.py routes         # 23 lines / routes loaded
cd apps/document-parser && python3 -m pytest -q                    # 27 passed
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_display.py tests/test_agent_runtime_parse_only.py tests/test_agent_runtime_tool_output.py tests/test_agent_runtime_progress.py tests/test_agent_chat_runtime_loops.py tests/test_agent_chat_runtime_attachments.py -q  # 77 passed
cd services/market-report-finder && uv run pytest -q               # 46 passed
cd services/market-report-rules && uv run pytest -q                # 29 passed
cd packages/market-contracts && uv run pytest -q                   # 2 passed
cd apps/web && npm run lint                                        # passed
cd apps/web && npm run build                                       # passed
cd apps/web && npm run check:frontend                              # passed after API consumer / pane split
cd apps/web && npm run e2e -- e2e/tests/document-result-preview.spec.ts e2e/tests/pdf-parsing-market-filter.spec.ts e2e/tests/search-download-responsive.spec.ts  # 6 passed
bash -n start_all.sh && find scripts infra apps services -type f -name '*.sh' -print0 | xargs -0 -r bash -n
```

以上验证说明主要功能基线是绿的。合并前仍需确保本轮新增源码和文档按主题提交，且 ignored runtime/cache/build 目录不进入索引。

### 0.3 2026-07-01 剩余工作量评估与下一轮任务池

本轮以后剩余工作不再适合按“大模块一次性拆完”推进，应继续按小 PR / 小提交切片。粗估如下：

加速执行原则：

- 绿灯任务批量推进：纯 helper 下沉、只读测试覆盖、feature API 兼容出口收口、文档状态同步可以每轮合并 2-4 个低风险点，统一跑聚焦门禁后提交。
- 黄灯任务小步验证：涉及组件状态派生、PDF quality/financial 规则行为、Agent runtime 引用/展示输出的改动，每次只改一个行为面，并必须补直接测试。
- 红灯 owner 暂缓单独设计：`ACTIVE_RUNS`、SSE lifecycle、PDF parser queue claim/worker/Flask response、Document workbench refs/selection/scroll 不混入加速批次。
- 提速不扩大爆炸半径：每轮优先选择可回滚、可聚焦验证、不会跨越运行时状态 owner 的改动；文档只记录关键决策和验证结果，不做过度整理。

- `F-004` 前端 feature 化与样式收口：剩余约 0-2 个小轮次，约 0.25-0.75 天。
  1. `SearchDownload.tsx` toast / download refresh / 下载状态派生收口已完成；状态 owner 留页面层，新增 `features/search-download/downloadStatus.ts` 和直接单测。
  2. `DocumentResultWorkbench.tsx` json preview / page overlay derivation 已完成；父组件继续保留 refs、selection、scroll 和 resource open owner。
  3. `index.css` 全局/响应式样式审计已完成：root/body/dark/base focus/reduced-motion/app spacing 已迁到 `styles/app-base.css`，`index.css` 降至约 85 行；`PDF_CSS` / `DOCUMENT_CSS` 运行时字符串继续单独窗口评估。
  4. feature API 显式导出清理已基本完成；`features/document-parser/api.ts`、`features/pdf-parsing/api.ts`、`features/market-parsing/api.ts` 已成为实现 owner，`lib/documentApi.ts`、`lib/pdfApi.ts`、`lib/secApi.ts` 仅兼容 re-export。
- `P-002` / `P-001` PDF parser 边界拆分：剩余约 1-3 个小轮次，约 0.5-1 天。
  1. `content_list_enhanced` 脚注、目录、Markdown 页码派生 helper 已继续下沉；`pdf_parser_app_impl.py` 仅保留兼容 wrapper，并补 service 级单测。
  2. `document_full` resource / table relation payload 覆盖已补强；open artifact resolver 已先抽纯“artifact name 分类/路径/mimetype 决策”helper + 直接单测，并已最小接入 `open_artifact` route；Flask `send_file/jsonify`、错误文案、status code、下载名和 `.webp` 当前 mimetype 行为仍留 app。
  3. quality / financial / response 纯规则测试补强已继续推进；已覆盖银行资产负债表附近噪声表过滤、季度报告核心表规则、`equity_statement` 回填所有者权益变动表、key_metrics 表源元数据继承、statement display source 噪声 table index 回落、非数字行号防御、candidate summary、priority review 去重/截断、financial schema/rule mismatch、单边 artifact 读取、stale checks 触发重写、duplicate payload 和 recent task normalization，不改变 `_ensure_quality_report` / `_ensure_financial_artifacts` 调用时机。
  4. `pdf_parser_app_impl.py` 状态 owner 已清单化；queue claim / worker / Flask response 不在低风险拆分中修改。
- `A-002` / `A-001` Agent runtime 纯函数拆分：剩余约 3-6 个小轮次，约 0.75-2 天。
  1. Hermes run input / session context / intent 周边 helper 已继续下沉；`agent_runtime_context.py` 新增 statement/note detail intent 与 attachment classification helper，保持普通 chat 和 streaming 调用顺序不变。
  2. citations / display / parse-only 只读 helper 补齐：已补引用别名字段去重、supplement 引用合并、正文已有引用去重、LaTeX inline symbol normalization、source locator 默认值与链接追加、source ref 去重编号、auto evidence section strip、requested metric evidence guard、markdown link label 清洗、附件 path basename、通用 attachment 标签兜底、附件 URL Markdown target 编码、交易所前缀文件名股票代码/公司名匹配、parse-only 无匹配返回空、general/company-dir 短路、跳过已存在 Wiki 后再应用 `limit` 覆盖；后续继续按只读 helper + 直接单测推进。
  3. attachments / history / local-memory owner 拆分前置覆盖：高风险，至少 2 个提交；未补足覆盖前不迁移真实 owner。
  4. `ACTIVE_RUNS`、SSE event append、run lifecycle：高风险，暂缓到单独设计窗口。
- 验证与文档：每轮都要做，约占开发时间 20%-30%。最低门禁为聚焦测试、`git diff --check`；涉及前端页面时跑 `npm run check:frontend`，涉及 PDF parser 时跑对应 service tests，涉及 Agent runtime 时跑对应 `apps/api` 聚焦测试。

本轮并行执行结果：

1. 前端窗口：完成 `SearchDownload.tsx` download refresh 判定、toast 文案 helper、`DocumentResultWorkbench.tsx` json preview / page overlay derivation、`index.css` 全局基线抽离、Document/PDF/Market parsing feature API 实现 owner 上移和直接/E2E 覆盖；页面继续保留下载状态、refs、selection、scroll 和 resource open owner。
2. PDF parser 窗口：完成 `content_list_enhanced` 脚注/Markdown 行号/目录标题 helper 下沉、artifact name 纯分类 helper及 `open_artifact` route 最小接入、`document_full` relation payload、relation alias、无效表过滤、file reference、缺失 source/resource 状态、content_list_enhanced 回写初始化覆盖、quality 银行噪声表过滤、季度报告核心表规则、权益变动表回填、key_metrics 表源元数据继承、statement display source 噪声 index 回落附近真实资产负债表、非数字行号防御、candidate summary、priority review 去重/截断，以及 financial schema/rule mismatch、单边 artifact 读取、stale checks 触发重写、duplicate response、recent task clamp/normalization 测试；`pdf_parser_app_impl.py` 状态 owner 已清单化，并继续保留 `_ensure_*` 编排 owner。
3. Agent runtime 窗口：完成 statement/note detail intent、attachment classification、PDF2MD parse-only alias/match、citation record label helper 下沉，以及引用别名字段去重 / supplement 引用合并 / 正文已有引用去重 / LaTeX inline symbol normalization / source locator 默认值与链接追加 / source ref 去重编号 / auto evidence section strip / requested metric evidence guard / link label 清洗 / 附件 path basename 与通用 label 兜底 / 附件 URL 编码 / 交易所前缀文件名股票代码和公司名匹配 / parse-only 空匹配 / general 与已有 company dir 短路 / 跳过已有 Wiki 后再应用 `limit` 覆盖；`ACTIVE_RUNS`、SSE、DB session memory refresh 仍留在 impl。
4. 本轮聚焦验证：`cd apps/web && npm run check:frontend` 通过；`cd apps/web && npx playwright test e2e/tests/document-result-preview.spec.ts` 通过；`cd apps/web && npx playwright test e2e/tests/workspace-responsive.spec.ts e2e/tests/search-download-responsive.spec.ts` 通过，9 passed；`cd apps/web && npx playwright test e2e/tests/pdf-parsing-market-filter.spec.ts e2e/tests/search-download-responsive.spec.ts` 通过，5 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_parse_only.py tests/test_agent_runtime_context.py tests/test_agent_runtime_display.py tests/test_agent_runtime_citations.py -q` 通过，41 passed；`cd apps/pdf-parser && python3 -m pytest tests/test_pdf_parser_artifact_route.py tests/test_pdf_parser_artifact_service.py tests/test_pdf_parser_response_service.py tests/test_pdf_parser_document_full_service.py tests/test_pdf_parser_quality_service.py tests/test_pdf_parser_financial_service.py -q` 通过，49 passed；`cd apps/pdf-parser && python3 -m pytest -q` 通过，192 passed；`git diff --check` 通过。

下一轮并行执行队列：

1. 前端窗口：CSS 主入口和 feature API owner 已收口；如继续前端，单独评估 `PDF_CSS` / `DOCUMENT_CSS` 运行时字符串或做低风险响应式 smoke，不与业务状态 owner 混做。
2. PDF parser 窗口：如继续后端，可抽 quality report payload / status response 纯 helper；不动 queue、Flask response 行为、`_ensure_*` 编排。
3. Agent runtime 窗口：继续 citations / display / parse-only / context 只读 helper 补齐；真实 attachments/history/local-memory owner 拆分前先补覆盖。
4. 主线收口：合并上述改动后更新本节状态，跑聚焦验证，并按主题提交。

本阶段明确暂缓：

- 不拆 `ACTIVE_RUNS` 和 SSE lifecycle owner。
- 不改 PDF parser 本地 queue worker / claim / Flask response owner。
- 不迁移 `PDF_CSS` / `DOCUMENT_CSS` 运行时注入字符串。
- 不把 DocumentResultWorkbench 的 refs / selection / scroll owner 提前分散。

## 1. 当前架构事实

### 1.1 当前主要目录职责

```text
apps/web                  React / Vite 工作台
apps/api                  FastAPI 聚合后端、鉴权、工作流、Agent 代理
apps/pdf-parser           PDF / MinerU 解析服务，A 股财报主链路
apps/document-parser      通用文档解析服务
services/market-report-finder
                          CN/HK/US/EU/JP/KR 官方披露搜索与下载
services/market-report-rules
                          多市场解析后规则、校验、load plan
agents/hermes             Hermes profiles、规则、共享脚本
db/ddl                    数据库 DDL
db/dml                    数据库 DML
db/imports                PostgreSQL 入库脚本、测试、临时 UI、样本数据
scripts                   维护、市场 evidence 构建、评测、向量入库、运维脚本
infra                     Docker、env 模板、systemd、supervisor、模型服务
eval_datasets             评测集与部分评测输出
data                      本地运行态、下载文件、解析产物、Wiki、DB、日志
```

### 1.2 高风险信号

已观察到的典型风险路径：

```text
data/market-report-finder/downloads/**
data/document-parser/db/tasks.db
data/pdf-parser/db/backups/*.db
data/reports/*.json
data/reports/*.md
test-results/**
apps/web/dist/**
apps/web/playwright-report/**
apps/api/.venv/**
services/*/.venv/**
=3.0,
```

其中 `data/` 在 `.gitignore` 中已经标为运行态目录，但历史上已有大量文件被 Git 跟踪。后续窗口处理时要注意：

- 不要物理删除用户本地数据。
- 先用 `git rm --cached` 让文件脱离 Git 索引。
- 运行态文件保留到本地 `var/`、外部磁盘、对象存储或明确的备份位置。

### 1.3 规模信号

当前仍需优先关注的大文件/大模块：

```text
apps/api/services/agent_chat_runtime.py                 约 7800 行
apps/pdf-parser/app.py                                  约 6700 行
apps/pdf-parser/financial_extractor.py                  约 3600 行
apps/web/src/pages/SearchDownload.tsx                   约 953 行
apps/web/src/components/pdf/PdfSourceWorkbench.tsx      约 708 行
apps/web/src/components/document-parser/DocumentResultWorkbench.tsx
                                                         约 591 行
apps/web/src/styles/chat.css                            约 1121 行
apps/web/src/index.css                                  已降至约 85 行，仅保留 imports + theme
```

这些文件已经超过“局部维护”舒适区，应按业务领域、状态管理、展示组件、执行器和数据契约拆分。

## 2. 优化目标

### 2.1 仓库目标状态

源码仓库只承载可复现、可审查、可测试的内容：

- 应用源码
- 服务源码
- 共享契约
- 数据库 schema / migration / seed
- 稳定评测集和小型 fixtures
- 运维脚本和部署模板
- 架构文档

运行态和产物必须外置或进入 ignored 目录：

- 上传文件
- 下载 PDF/HTML/ZIP
- SQLite/PostgreSQL/Milvus/MinIO 数据
- 解析结果
- Hermes 会话
- 缓存
- 日志
- Playwright 报告
- 临时评测输出

### 2.2 后端目标状态

API 控制面只负责：

- 鉴权与权限
- 请求入参校验
- 下游服务编排
- 任务提交与状态查询
- 统一错误、审计、脱敏
- 为前端提供稳定 API

不应继续在 router 内直接承担：

- 市场 package 目录扫描细节
- 同步执行长脚本
- 直接拼所有 market 脚本路径
- 进程内任务状态
- 不可恢复的后台线程
- 多处重复读取环境变量

### 2.3 前端目标状态

前端按 feature 分层：

```text
src/app/                 路由、权限、应用壳配置
src/shared/              API client、基础 hooks、utils
src/components/ui/       通用低阶控件
src/components/page/     页面布局 primitives
src/features/*           业务功能模块
```

页面文件只做组合，不堆业务状态、轮询、DOM refs、API 解析和复杂渲染。

### 2.4 数据链路目标状态

多市场链路形成稳定 contract：

```text
DownloadedReport
  -> ParserArtifact
  -> EvidencePackage
  -> ValidationReport / LoadPlan
  -> DB Fact
  -> Vector Chunk
  -> Agent Citation
```

每一层有明确 schema version、artifact hash、来源路径和 evidence id。

## 3. 推荐目标目录

建议最终收敛到以下形态。迁移可以分阶段完成，不要求一次性移动所有目录。

```text
siq-research-engine/
  apps/
    web/
    api/
    pdf-parser/
    document-parser/
    market-report-finder/
    market-report-rules/

  packages/
    siq-core/
    market-contracts/
    agent-core/

  agents/
    hermes/
      profiles/

  db/
    ddl/
    dml/
    migrations/
    seeds/

  tools/
    data-import/
    data-migration/
    vector-index/
    market-packages/

  scripts/
    dev/
    ops/
    ci/

  infra/
    docker/
    env/
    systemd/
    supervisor/
    model-services/

  docs/
    architecture/
    operations/
    adr/
    reports/

  datasets/
    eval/
    fixtures/
    samples/

  var/              # git ignored，本地运行态
    api/
    pdf-parser/
    document-parser/
    market-report-finder/
    hermes/
    db/
    wiki/
    logs/
    cache/
    runtimes/

  artifacts/        # git ignored，生成产物
    test-results/
    playwright-report/
    eval-runs/
    generated-reports/
```

### 3.1 `data/` 与 `var/` 的迁移策略

短期可以保留 `data/` 作为兼容目录，但新增默认路径建议转向 `var/`：

```text
data/README.md          保留，说明 legacy / compatibility
var/README.md           新增，说明运行态目录
artifacts/README.md     新增，说明生成产物目录
datasets/README.md      新增，说明可版本化数据集
```

兼容期环境变量：

```text
SIQ_DATA_ROOT           默认可先指向 data，后续切到 var
SIQ_RUNTIME_ROOT        新增，默认 var
SIQ_ARTIFACTS_ROOT      新增，默认 artifacts
SIQ_DATASETS_ROOT       新增，默认 datasets
```

## 4. 服务边界设计

### 4.1 API 控制面

路径：`apps/api`

职责：

- 用户、鉴权、权限、审计
- 工作区、报告、下载、文档、PDF 代理
- Agent SSE / 会话 / 附件
- 提交后台任务、查询任务状态
- 聚合下游健康状态

不直接拥有：

- market 下载实现
- rules 抽取实现
- PDF / 文档解析实现
- evidence package 构建细节
- DB import 具体 SQL 写入逻辑
- Milvus chunk 入库具体实现

建议新增模块：

```text
apps/api/
  settings.py
  services/
    command_runner.py
    job_service.py
    market_report_gateway.py
    market_package_service.py
    market_package_repository.py
    market_assist_service.py
    evidence_package_reader.py
  routers/
    market_reports.py        # 只保留 HTTP 映射
    jobs.py                  # 可统一承接后台任务查询
```

### 4.2 Finder 服务

路径：当前 `services/market-report-finder`，目标可迁到 `apps/market-report-finder`

职责：

- 公司、证券、市场解析
- 官方披露检索
- 下载原始披露文件
- 生成下载 metadata
- 管理下载目录 index

建议优化：

- 引入 `MarketRegistry`
- 引入 `MarketResolver`
- 引入 `DownloaderPort`
- 下载 index 使用原子写和文件锁
- 批量下载转为任务，不阻塞 HTTP 请求
- route 不直接 new `ReportFinderOrchestrator()`，改为依赖注入

### 4.3 Rules 服务

路径：当前 `services/market-report-rules`，目标可迁到 `apps/market-report-rules`

职责：

- extraction
- validation
- load plan
- rules catalog
- evidence package contract validation

不建议承担：

- CN legacy gateway
- finder/pdf-parser 代理
- 直接暴露内部规则数组作为 API contract

建议新增：

```text
RuleCatalogService
EvidencePackageValidator
MarketPipelineRegistry
```

### 4.4 Parser 服务

路径：

```text
apps/pdf-parser
apps/document-parser
```

职责：

- 上传 / URL / MinerU bridge
- 解析任务状态
- artifact 生成
- source map / quality report / table relations

建议优化：

- `apps/pdf-parser/app.py` 拆为 router、task service、artifact service、source service、financial service。
- `apps/document-parser` 和 `apps/pdf-parser` 共享表格合并、source map、quality report 的 contract，而不是复制实现。
- 通用文档通过 PDF parser bridge 的临时任务要有明确生命周期和清理策略。

### 4.5 Package Builder / Import / Vector Worker

当前主要散在：

```text
scripts/us-sec/**
scripts/hk/**
scripts/eu/**
scripts/jp/**
scripts/kr/**
db/imports/*.py
scripts/vector-index/**
```

目标是把它们从“被 API 直接 subprocess 调用的脚本”升级为可审计任务：

```text
tools/market-packages/
tools/data-import/
tools/vector-index/
apps/api/services/command_runner.py
apps/api/services/job_service.py
```

长期可以独立为 worker 服务：

```text
apps/worker/
  jobs/
    build_market_package.py
    import_postgres.py
    ingest_milvus.py
```

## 5. 共享契约设计

### 5.1 `packages/market-contracts`

建议新增共享包：

```text
packages/market-contracts/
  pyproject.toml
  src/siq_market_contracts/
    __init__.py
    downloaded_report.py
    evidence_package.py
    source_map.py
    financial_metrics.py
    storage_layout.py
    validation.py
  tests/
```

核心模型：

```text
DownloadedReport
EvidencePackageManifest
EvidenceSource
SourceMapEntry
FinancialFact
FinancialChecks
NormalizedMetric
PackageSummary
StorageLayout
ArtifactHash
```

### 5.2 契约使用边界

```text
market-report-finder 写 DownloadedReport metadata
market-report-rules 读/写 EvidencePackage 和 validation
apps/api 读 PackageSummary，不直接散读私有目录
db import 读 EvidencePackageManifest 和 FinancialFact
vector ingest 读 SourceMapEntry 和 PackageSummary
Agent 引用 evidence_id 和 source location
```

### 5.3 兼容当前合同

现有 `docs/architecture/market-evidence-package-contract.md` 继续作为外部文档。新增 package 时不要立刻改所有生成器，先提供 reader/validator 兼容现有目录。

## 6. 后台任务设计

### 6.1 当前问题

当前后台任务存在典型风险：

- 进程内 `_jobs`，服务重启丢状态。
- daemon thread，无法可靠取消、重试、恢复。
- `wait=true` 会把长任务绑回 HTTP 请求生命周期。
- 多 worker 或多进程部署会出现任务不可见。
- `subprocess` 执行参数、cwd、env、stdout/stderr、脱敏逻辑分散。

### 6.2 最小可落地方案

先不引入复杂队列，也可以新增持久 job 表：

```text
Job
  id
  kind
  status: queued|running|succeeded|failed|cancelled|timeout
  created_by
  created_at
  started_at
  finished_at
  command_display
  command_hash
  cwd
  env_profile
  stdout_tail
  stderr_tail
  result_json
  error
  retry_count
```

新增 `CommandRunner`：

```text
CommandRunner.run(
  args,
  cwd,
  env,
  timeout,
  redact_keys,
  stdout_limit,
  stderr_limit,
)
```

### 6.3 中期方案

使用 Redis/RQ、Arq 或 Celery：

```text
API submit job
  -> Redis queue
  -> worker process
  -> job DB / Redis status
  -> API poll / SSE
```

本项目已有 Redis 依赖和 Docker Compose Redis 服务，中期建议优先评估 Arq 或 RQ，避免 Celery 初期复杂度过高。

## 7. 前端优化设计

### 7.1 目标目录

```text
apps/web/src/
  app/
    routes.tsx
    navigation.ts
    permissions.ts

  shared/
    api/
      client.ts
      errors.ts
    hooks/
    utils/

  components/
    ui/
    page/
    layout/
    legacy-ui/

  features/
    search-download/
      api.ts
      types.ts
      useSearchDownload.ts
      components/

    pdf-parsing/
      PdfParsingWorkbench.tsx
      api.ts
      types.ts
      state/
      components/
      source-trace/
      workflow/

    document-parser/
      api.ts
      types.ts
      state/
      workbench/
      panels/

    reports/
    agent-chat/
    settings/
    admin/
```

### 7.2 路由收口

当前路由、导航、权限、预加载散落在：

```text
apps/web/src/App.tsx
apps/web/src/components/layout/layoutData.ts
apps/web/src/lib/routePreload.ts
```

目标：

```typescript
type AppRoute = {
  path: string
  label?: string
  icon?: Icon
  component: LazyExoticComponent<ComponentType>
  permission?: string
  navGroup?: 'main' | 'admin' | 'system' | 'utility'
  hideGlobalChat?: boolean
  preload?: () => Promise<unknown>
}
```

`App.tsx` 只消费 route registry，`Sidebar` 也从同一份 registry 生成。

### 7.3 API client 收口

当前混用：

```text
apiJson
fetchWithAuth
installFetchAuth 全局 patch
组件内裸 fetch
```

目标：

```text
shared/api/client.ts       统一 token、错误解析、JSON/FormData、Blob
features/*/api.ts          每个业务域暴露函数
components                 不直接拼 URL，不直接解析 HTTP error
```

### 7.4 PDF / 多市场解析收口

当前：

```text
pages/PdfParsing.tsx
pages/MarketParsingPage.tsx
pages/{Hk,Us,Eu,Jp,Kr}Parsing.tsx
```

目标：

```text
features/pdf-parsing/PdfParsingWorkbench.tsx
pages/PdfParsing.tsx        CN wrapper
pages/HkParsing.tsx         HK wrapper
pages/UsParsing.tsx         US wrapper
...
```

页面 wrapper 只传：

```text
market
title
description
workflowMode
extraPanel
```

### 7.5 UI 系统收口

短期规则：

- `components/ui/legacy/*` 停止从 `components/ui/index.ts` 默认导出。
- 新页面只使用 `components/page` 和现代 `components/ui/*`。
- `index.css` 当前已拆为：

```text
styles/search-download.css
styles/dashboard.css
styles/system-surfaces.css
styles/quick-questions.css
styles/chat.css
styles/app-base.css
```

`index.css` 仅保留样式入口导入和 `@theme` token 定义；`PDF_CSS` / `DOCUMENT_CSS` 运行时注入字符串不在本轮混迁。

### 7.6 E2E 验收矩阵

最低应补：

```text
登录 / 权限跳转
路由烟雾矩阵
搜索下载主流程 mock
PDF 解析主流程 mock
文档解析主流程 mock
报告页空/加载/失败态
聊天发送/停止/恢复 mock
移动端 sidebar / bottom sheet / floating action 不重叠
```

## 8. 分阶段落地计划

### Phase 0：安全基线与冻结规则

目标：防止优化过程中误删运行态数据、误改 CN legacy 主链路。

任务：

- 记录当前 `git status --short`。
- 列出已跟踪运行态文件。
- 增加或更新路径治理文档。
- 明确禁止物理删除 `data/` 下业务文件。
- 明确 CN A 股主链路改动需单独评审。

建议命令：

```bash
cd /home/maoyd/siq-research-engine
git status --short
git ls-files data | wc -l
git ls-files 'data/**/*.pdf' | wc -l
git ls-files 'data/**/*.db' | wc -l
git ls-files -z | xargs -0 -r du -b | sort -nr | head -n 40
```

验收：

- 有一份明确的运行态迁移清单。
- 没有删除用户本地数据。
- 后续窗口知道哪些路径只能 `git rm --cached`。

### Phase 1：仓库路径治理

目标：让源码、运行态、生成产物、稳定数据集分层。

任务：

1. 新增目录说明：

```text
var/README.md
artifacts/README.md
datasets/README.md
```

2. 更新 `.gitignore`：

```text
var/
artifacts/
test-results/
**/test-results/
**/playwright-report/
**/.venv/
**/.pytest_cache/
```

3. 将已跟踪运行态文件移出 Git 索引。示例：

```bash
git rm --cached -r data/market-report-finder/downloads
git rm --cached data/document-parser/db/tasks.db
git rm --cached -r data/pdf-parser/db/backups
git rm --cached -r data/reports
```

注意：以上命令只移出 Git 索引，不删除本地文件。执行前必须再次确认路径。

4. 清理异常空文件：

```text
=3.0,
```

执行前需确认它不是用户刻意保留的文件。

验收：

- `git status` 中不再出现大量 PDF、DB、解析产物作为跟踪文件。
- 新增运行态文件不会被 Git 捕获。
- 本地服务仍能通过环境变量找到旧数据目录。

### Phase 2：环境配置与启动形态统一

目标：统一本地启动、Docker 启动、环境模板。

任务：

1. 统一 env 模板入口：

```text
infra/env/local.example
infra/env/docker.example
infra/env/production.example
```

2. 本地真实 env 建议使用：

```text
infra/env/local.env       # git ignored
```

3. `start_all.sh` 默认读取：

```text
SIQ_ENV_FILE=${SIQ_ENV_FILE:-infra/env/local.env}
```

兼容旧路径 `env/backend.env`、`env/frontend-dev.env` 一个周期，但文档标注 deprecated。

4. Docker Compose 补齐服务 profile：

```text
document-parser
market-report-rules
hermes gateways
vector ingest
worker
```

验收：

- README、`start_all.sh`、`infra/docker/docker-compose.yml` 对同一批环境变量命名一致。
- `start_all.sh` 与 Compose 的服务图差异有文档说明。

### Phase 3：共享契约包与 settings 收口

目标：减少 API、finder、rules、脚本对路径和 JSON 结构的重复理解。

任务：

1. 新增 `packages/market-contracts`。
2. 提供 Pydantic models 和 reader/validator。
3. 在 `services/market-report-rules` 中优先使用共享 validator。
4. 在 `apps/api` 中新增 `EvidencePackageReader`，替代 router 内散读 JSON。
5. 为 `apps/api` 新增统一 `Settings`。
6. 为 finder/rules 补齐 typed settings，停止在业务模块 import-time 直接读 env。

验收：

- contract package 有独立测试。
- API package summary 读取走 reader。
- `market_reports.py` 不再直接拼大量 `manifest/qa/metrics` 路径。
- 测试可通过构造 tmpdir fixture，不依赖真实 `data/wiki`。

### Phase 4：API 控制面瘦身

目标：把 `market_reports.py` 从胖 router 拆为薄路由 + service。

建议拆分顺序：

1. 抽 HTTP 代理：

```text
services/market_report_gateway.py
```

2. 抽 package reader：

```text
services/market_package_repository.py
services/evidence_package_reader.py
```

3. 抽 job runner：

```text
services/command_runner.py
services/job_service.py
```

4. 抽 LLM assist：

```text
services/market_assist_service.py
```

5. 保留 router：

```text
routers/market_reports.py
```

router 只负责：

- path/query/body 参数
- 权限 Depends
- 调用 service
- 转换 HTTP response

验收：

- `apps/api/routers/market_reports.py` 行数明显下降，目标少于 500 行。
- 私有工具函数转移到 service，并有单元测试。
- 外部 API URL 不变。
- 现有 `apps/api/tests/test_market_reports_proxy.py` 通过。

### Phase 5：持久任务与 worker

目标：长任务可恢复、可审计、可查询、可取消。

短期任务：

- 新增 job model / migration。
- `market package build`、`db import`、`vector ingest` 先接入持久 job。
- 保留旧 endpoint 响应格式，内部改为 job service。

中期任务：

- 引入 Redis 队列。
- 新增 worker 进程。
- API 只提交任务和查询状态。

验收：

- API 重启后仍可查询历史 job。
- 失败任务有 stderr tail 和脱敏 command display。
- 同一 package 重跑具备幂等语义。

### Phase 6：finder / rules / parser 服务边界收紧

目标：每个服务只做自己拥有的领域。

Finder：

- 下载 index 原子写。
- 批量下载任务化。
- 市场注册数据化。

Rules：

- `/rules` 改为 capabilities/catalog。
- CN legacy adapter 标记 deprecated 或迁出。
- 只处理解析后产物，不做下载/解析代理。

PDF parser：

- 拆 `app.py`。
- 财务抽取、任务状态、artifact、source viewer 分模块。

Document parser：

- 与 PDF parser 共享 table/source/quality contract。
- 明确 bridge 临时目录清理规则。

验收：

- 服务 README 明确职责边界。
- API-finder-rules 有 contract tests。
- CN legacy 行为不变。

### Phase 7：前端 feature 化

目标：降低页面级耦合，提高多市场页面一致性。

任务顺序：

1. 新增 route registry：

```text
apps/web/src/app/routes.tsx
```

2. 合并 PDF / Market parsing workbench：

```text
features/pdf-parsing/PdfParsingWorkbench.tsx
```

3. 收口 API client：

```text
shared/api/client.ts
features/*/api.ts
```

4. 拆大型页面：

```text
features/search-download/*
features/document-parser/*
features/pdf-parsing/*
```

5. UI legacy 隔离：

```text
components/legacy-ui/
```

验收：

- `PdfParsing.tsx` 和各市场 parsing page 只做 wrapper。
- `App.tsx` 和 `Sidebar` 使用同一 route registry。
- 组件内裸 `fetch` 显著减少。
- `npm run lint` 和 `npm run build` 通过。

### Phase 8：测试与观测补齐

后端测试：

```text
contract tests
job lifecycle tests
package reader golden fixtures
finder download index atomic write tests
rules capabilities tests
```

前端测试：

```text
route smoke
permission redirect
search-download mock flow
pdf parsing mock flow
document parser mock flow
report states
chat stream mock
mobile layout no overlap
```

运维检查：

```bash
bash -n start_all.sh
find scripts -type f -name '*.sh' -print0 | xargs -0 -r bash -n
cd apps/api && uv run python -m pytest tests
cd services/market-report-finder && uv run pytest
cd services/market-report-rules && uv run pytest
cd apps/web && npm run lint && npm run build
```

## 9. 具体任务卡片

### R-001：移出已跟踪运行态数据

优先级：P0
范围：Git 索引、`.gitignore`、路径说明
状态：已完成
路径：

```text
data/market-report-finder/downloads/**
data/document-parser/db/tasks.db
data/pdf-parser/db/backups/**
data/reports/**
test-results/**
```

动作：

- 列出文件。
- 确认本地保留。
- `git rm --cached` 移出索引。
- 更新 README / `.gitignore`。

验收：

- 本地文件未删除。
- Git 不再跟踪 PDF/DB/备份/运行报告。

### R-002：建立 `var/`、`artifacts/`、`datasets/`

优先级：P0
范围：目录与文档
状态：已完成
动作：

- 新增 README。
- 更新 `.gitignore`。
- 更新根 README 的目录说明。

验收：

- 新目录职责清晰。
- 后续运行产物有推荐落点。

### R-003：工作树分组收口与提交检查点

优先级：P0
范围：Git 工作树、已完成重构文件、运行态索引删除
状态：已完成
背景：

- 执行前 `git status --short` 约 725 行。
- 大量 `data/**` 删除项是运行态文件从 Git 索引移出的结果，不代表本地业务数据应物理删除。
- 多个已完成架构任务仍包含未跟踪源码文件，例如 `pdf_parser_app_impl.py`、`agent_chat_runtime_impl.py`、`packages/market-contracts`、`apps/web/src/app`、`apps/web/src/features`、`apps/web/src/shared`。

动作：

- 已按主题分组 review 和提交，避免把所有变更压成一个大提交。
- 第一组：仓库治理和运行态索引移除，包含 `.gitignore`、README、`data` 索引删除、`var/`、`artifacts/`、`datasets/` 说明。
- 第二组：API 控制面与 market contract，包含 settings、repository、command runner、job service、`packages/market-contracts`。
- 第三组：PDF parser 拆分，包含 app façade、impl、request/runtime/page-marker/task-repository/artifact/source service 和对应测试。
- 第四组：Agent runtime 拆分，包含 façade、impl、loop guard、progress 和对应测试。
- 第五组：前端 route/workbench/API client 迁移。
- 提交前确认 ignored cache/runtime 不进入索引：`git status --ignored --short` 只用于检查，不要 `git add -f` cache/runtime。

验收：

- 每个提交都有清晰主题和可回滚边界。
- `git diff --cached --name-only` 不包含 `.venv`、`__pycache__`、`.pytest_cache`、`dist`、`test-results`、大 PDF/DB/下载产物。
- 每个提交对应的 targeted tests 有记录。
- 最终 `git status --short` 不再被已确认的运行态删除项和未跟踪源码淹没。

### B-001：拆 `market_reports.py` 配置与路径常量

优先级：P0
范围：`apps/api`
状态：已完成
动作：

- 新增 `settings.py` 或扩展现有 `path_config.py`。
- 把 market roots、script registry、downstream URL 移入 typed settings。

验收：

- router import-time env 读取减少。
- 测试可注入 settings。

### B-002：抽 `MarketPackageRepository`

优先级：P0
范围：`apps/api`
状态：已完成
动作：

- 把 package glob、manifest/source_map/metrics 读取迁出 router。
- 支持 tmpdir fixture 测试。

验收：

- API 外部响应不变。
- reader 单元测试覆盖 US/HK/EU/JP/KR package。

### B-003：抽 `CommandRunner` 与 job service

优先级：P0
范围：`apps/api`
状态：已完成
动作：

- 统一 subprocess 调用。
- 统一 timeout、cwd、env、stdout/stderr tail、敏感参数脱敏。
- job 状态改为文件持久化存储，可重载恢复。
- 后台任务统一记录 `created_by` 审计信息。

验收：

- package build/import/vector ingest 都走同一 runner。
- 失败响应包含可审计但脱敏的信息。
- job 状态查询从持久化存储读取。

### C-001：新增 `packages/market-contracts`

优先级：P1
范围：共享 Python package
状态：已完成
动作：

- 定义 Pydantic models。
- 提供 reader/validator。
- 接入 rules validator 或 API reader。

验收：

- 独立测试通过。
- 至少一个 API reader 使用该包。

### F-001：新增前端 route registry

优先级：P1
范围：`apps/web`
状态：已完成
动作：

- 新增 `src/app/routes.tsx`。
- `App.tsx`、`layoutData.ts`、`routePreload.ts` 合并数据源。
- 补 `/forbidden` 页面或修正权限跳转。

验收：

- 新增路由只需改一处 registry。
- 导航、权限、懒加载一致。

### F-002：合并 PDF 解析和多市场解析 workbench

优先级：P1
范围：`apps/web`
状态：已完成
动作：

- 新增 `features/pdf-parsing/PdfParsingWorkbench.tsx`。
- `PdfParsing.tsx` 已变为薄 wrapper，`MarketParsingPage.tsx` 继续承载共享 workbench。
- 日志区域恢复为可折叠展示，移动端与多市场页面共用同一份 workbench 行为。

验收：

- CN/HK/US/EU/JP/KR 市场过滤行为不回归。
- Playwright 市场隔离测试通过。

### F-003：统一前端 API client

优先级：P1
范围：`apps/web`
状态：阶段完成，主要兼容出口已收口
动作：

- 新增 `shared/api/client.ts`。
- `lib/apiClient.ts` 已改为共享客户端的兼容出口。
- `features/pdf-parsing/api.ts`、`features/document-parser/api.ts`、`features/market-parsing/api.ts`、`features/settings/api.ts` 已补位为域级门面，并由对应 feature API 接管 PDF / Document / Market parsing 请求实现。
- `lib/documentApi.ts`、`lib/pdfApi.ts`、`lib/secApi.ts` 已降为兼容 re-export；`lib/apiClient.ts` 已改为共享客户端的兼容出口。
- 业务组件/页面已迁移到 `features/*/api.ts` 或 `shared/api/client.ts`。
- `shared/api/client.ts` 是唯一允许直接调用 `globalThis.fetch` 的基础客户端；业务组件不应新增裸 `fetch`。

验收：

- token、错误解析、JSON/FormData/Blob 处理统一。
- `npm run lint && npm run build` 通过。

### F-004：清理前端 API 兼容出口与大型页面

优先级：P1
范围：`apps/web`
状态：进行中
背景：

- `npm run lint`、`npm run build` 与 `npm run check:frontend` 已通过。
- route/nav/preload 已由 `app/routes.tsx` 单源管理。
- 业务页面和组件已基本停止直接导入 `lib/apiClient`、`lib/pdfApi`、`lib/secApi`、`lib/documentApi`；`lib/documentApi`、`lib/pdfApi`、`lib/secApi` 已降为兼容 re-export。
- 剩余大文件已不再包括 `index.css`；`DocumentResultWorkbench.tsx`、`PdfSourceWorkbench.tsx` 和 `SearchDownload.tsx` 均已完成多轮展示/派生边界拆分，状态 owner 继续留在页面或主组件层。

动作：

- 逐步把 `pages/*`、`components/*` 的业务 API 调用迁到对应 `features/*/api.ts`。
- 保留 `shared/api/client.ts` 作为底层唯一 fetch owner，`lib/apiClient.ts` 仅在兼容期 re-export。
- 已将 PDF parsing、document parser、market parsing、SEC panels、Dashboard、ReportViewer、NotificationMenu、GlobalSearch、Account/User 管理、workspace 和 chat attachment 等业务组件迁到 `features/*/api.ts` 或 `shared/api/client.ts`。
- 已拆 `SearchDownload.tsx` 的市场配置、类型、识别 helper、报告候选表格、已下载文件面板、search/download flows、URL state 和日志派生到 `features/search-download/model.ts`、`ReportTableSection.tsx`、`DownloadedReportsPanel.tsx`、`flows.ts`、`urlState.ts`、`logs.ts`，页面降至约 950 行。
- 已拆 `PdfSourceWorkbench.tsx` 的 compare pane、reading pane、PDF page pane、review correction pane、artifact pane 到 `PdfSourceWorkbenchPanels.tsx`，并将页图渲染拆到 `PdfSourcePagePreview.tsx`。
- 已拆 `PdfSourceWorkbench.tsx` 的 PDF 页码/bbox、表格合并、跨页 continuation 判断、table_relations artifact 读取转换、page overlay 构建和 fallback page HTML 到 `pdfSourceWorkbenchHelpers.ts`；页面组件进一步降至约 708 行，状态 owner、URL/toast/download 流不迁移。
- 已从 `index.css` 迁出 `search-download-*`、`smart-search-*` 及其平板/手机/dark 覆盖到 `styles/search-download.css`，并新增 `search-download-responsive.spec.ts` 覆盖 390、768、1440 三档无横向溢出。
- 已从 `index.css` 迁出 dashboard hero / illustration 容器样式到 `styles/dashboard.css`，保持顶部 `@import` 顺序，避免选择器级联变化。
- 已从 `index.css` 迁出通用 surface/button/search/scrollbar 样式到 `styles/system-surfaces.css`，包括 `premium-*`、`metric-tile`、`icon-button`、`global-search`、`sidebar-scrollbarless` 及 dark 覆盖；`index.css` 降至约 1265 行。
- 已从 `index.css` 迁出 quick-question/agent quick-question/analysis quick-question 样式到 `styles/quick-questions.css`，并保持在 `system-surfaces.css` 之后导入，确保 `premium-chip` 基础样式先于 quick-question 覆盖生效；`index.css` 降至约 1164 行。
- 已从 `index.css` 迁出 chat message bubble、rendered markdown、citation、table、message row/time/copy、code block、agent dock/composer、chat page shell 和相关 dark/mobile mode 样式到 `styles/chat.css`，保持原 `@import` 顺序和运行时注入样式不变；`index.css` 降至约 222 行。
- 已从 `index.css` 迁出 root/body/dark/base focus/reduced-motion/app spacing 全局基线到 `styles/app-base.css`，并去重 reduced-motion 规则；`index.css` 降至约 85 行。
- 已修复移动端工作平台和系统平台上下界面宽度不一致问题，并用 `workspace-responsive.spec.ts` 覆盖 390、768、1440 三档宽度。
- 已拆 `DocumentResultWorkbench.tsx` 的 status/relation/source-map/markdown/bbox 纯函数到 `documentResultWorkbenchUtils.ts`；组件内 refs、selection、scroll、resource open 和 JSX 结构暂不移动。
- 已拆 `DocumentResultWorkbench.tsx` 的 `AuthenticatedImage` 与 `PdfPagePreview` 到 `DocumentSourcePreview.tsx`；`imageSize`、objectURL cleanup、overlay click 和 protected figure image 加载已由 `document-result-preview.spec.ts` 覆盖。
- 已拆 `DocumentResultWorkbench.tsx` 的 source preview、artifact pane、table/source relation pane、figure pane、quality/workflow pane、extract/evidence pane、markdown pane 和 source lookup 派生；父组件继续保留 selection、scroll、resource open owner。
- 下一步如继续前端，可单独评估 `PDF_CSS` / `DOCUMENT_CSS` 字符串迁移，或继续做低风险响应式 smoke；状态 owner、refs 和 selection/scroll 仍不提前分散。

验收：

- 新代码不再直接从业务组件调用裸 `fetch`。
- 新增页面 API 只暴露在 `features/*/api.ts` 或 shared client。
- `npm run lint && npm run build` 通过；本轮额外执行 `npm run check:frontend` 通过。
- 关键 Playwright：`document-result-preview.spec.ts`、`pdf-parsing-market-filter.spec.ts`、`search-download-responsive.spec.ts`、`workspace-responsive.spec.ts` 相关覆盖已通过。

### P-001：拆 `apps/pdf-parser/app.py`

优先级：P1
范围：`apps/pdf-parser`
状态：进行中
动作：

- 入口层已收敛为兼容 façade，原实现下沉到 `pdf_parser_app_impl.py`。
- 请求/任务参数 helper 已抽到 `pdf_parser_request_utils.py`。
- 运行时通用 helper 与线程安全文件缓存已抽到 `pdf_parser_runtime_utils.py`，保留 app 层 `_utc_now` / `APP_ACCESS_TOKEN` 兼容入口。
- Markdown 页码 marker 注入、重建、稀疏页回填和 marker 行解析已抽到 `pdf_parser_page_markers.py`，`app.py` 旧导入路径继续可用。
- SQLite task repository 已抽到 `pdf_parser_task_repository.py`，包含 schema/init、row hydration、CRUD、重复文件查询、recent summary、refresh candidate、queue 只读查询和 referenced paths；`app.py` 旧私有入口继续由 wrapper 暴露。
- Artifact 文件与路径 helper 已抽到 `pdf_parser_artifact_service.py`，包含 Markdown 路径解析、JSON 原子写、JSON artifact 加载、Markdown 写入、artifact status、图片保存/列表/ZIP、表格 HTML 定位和 correction 应用纯函数；`pdf_parser_app_impl.py` 保留同名 wrapper 与 Flask response owner。
- Source workbench IO helper 已抽到 `pdf_parser_source_service.py`，包含 corrections 路径/读写、source page payload wrapper、page bbox extent wrapper 和 PDF page image 缓存/渲染；route、markdown fetch、quality report、complete markdown 写入仍由 `pdf_parser_app_impl.py` 编排。
- DB 队列 claim、worker、artifact 写入编排仍留在 `pdf_parser_app_impl.py`，避免 WSGI 多进程/本地队列 claim 语义在纯拆分中顺手改变。
- 继续按 router/task/artifact/source/financial/quality 拆模块。
- 保持外部 API 不变。

验收：

- `python3 -m py_compile apps/pdf-parser/app.py apps/pdf-parser/pdf_parser_app_impl.py apps/pdf-parser/pdf_parser_task_repository.py apps/pdf-parser/pdf_parser_artifact_service.py apps/pdf-parser/pdf_parser_source_service.py apps/pdf-parser/pdf_parser_page_markers.py apps/pdf-parser/pdf_parser_request_utils.py apps/pdf-parser/pdf_parser_runtime_utils.py` 通过。
- `cd apps/pdf-parser && python3 -m pytest tests/test_pdf_parser_artifact_service.py tests/test_pdf_parser_source_service.py -q` 通过，8 passed。
- `cd apps/pdf-parser && python3 -m pytest tests/test_runtime_paths_and_task_state.py -q` 通过，16 passed。
- `cd apps/pdf-parser && python3 -m pytest tests/test_runtime_paths_and_task_state.py tests/test_page_markers.py tests/test_table_relations.py -q` 通过，80 passed。
- `cd apps/pdf-parser && python3 -m pytest tests/test_runtime_paths_and_task_state.py tests/test_page_markers.py tests/test_table_relations.py tests/test_backfill_task_markets.py -q` 通过，82 passed。
- `cd apps/pdf-parser && python3 -m pytest tests -q` 通过，125 passed。
- `cd apps/pdf-parser && python3 -m flask --app app.py routes` 通过。
- `app.py` 行数明显下降。
- 下一步优先：quality/financial/document_full 边界梳理，或继续抽 source table payload builder / artifact open resolver 这类纯函数。高风险项：多进程 WSGI 下本地 queue worker / claim 需要单独设计，不能在纯拆分中顺手修改。

### P-002：拆 PDF quality / financial / document_full 边界

优先级：P1
范围：`apps/pdf-parser`
状态：进行中
背景：

- `pdf_parser_app_impl.py` 已从约 6700 行降到约 4417 行，但仍承担 content_list_enhanced / MinerU result 的少量编排 owner。
- 已拆出的 `pdf_parser_artifact_service.py` 和 `pdf_parser_source_service.py` 只处理文件/路径/source IO，不能继续吸收质量规则，避免形成新的巨石。
- 已新增 `pdf_parser_quality_service.py`，搬出 quality report 周边候选文本处理、statement/table source 选择、financial data 回填 quality candidates、warning 过滤、summary/priority review 规则，以及 `quality_report.json` / `table_index.json` 的轻量路径读写封装；`pdf_parser_app_impl.py` 保留兼容 wrapper 与 `_ensure_quality_report` 编排 owner。
- 已新增 `pdf_parser_financial_service.py`，收拢 financial artifact 路径、读取、current 判断、写入和 ensure；`pdf_parser_app_impl.py` 保留 `_financial_*` 兼容 wrapper 与调用编排。
- 已新增 `pdf_parser_document_full_service.py`，收拢 document_full resource index、payload 构建、table relations artifact payload 和 content_list_enhanced 回写 document_full 的纯 payload helper；`pdf_parser_app_impl.py` 保留同名 wrapper、路径存在性、写入/ensure/刷新 owner 和 table relation 编排 owner。
- 已新增 `pdf_parser_content_list_enhanced_service.py`，收拢 content_list_enhanced 的 page block 统计、image semantic block、complete markdown appendix/content/write helper、`build_content_list_enhanced_payload` 顶层 payload 组装、table source 映射/匹配、打印页码映射、Markdown 页码推断 helper 和 enhanced quality signals 聚合；`pdf_parser_app_impl.py` 保留 `_build_content_list_enhanced` 兼容 wrapper、`_ensure_content_list_enhanced_artifact` 编排 owner 和 table relation owner。
- 已新增 `pdf_parser_mineru_result_service.py`，收拢 MinerU upstream payload summary、middle/model_output/content_list 原始产物和 images 保存 helper；`pdf_parser_app_impl.py` 保留 `_save_mineru_artifacts` wrapper、quality/document_full 后续写入编排和 task state owner。
- 已新增 `pdf_parser_response_service.py`，收拢 duplicate payload、recent task limit clamp 和 recent task response normalization；`pdf_parser_app_impl.py` 保留 `_list_recent_tasks` 中 completed missing artifact 状态持久化、Flask response 与兼容 wrapper。

建议拆分顺序：

1. 继续扩展 `pdf_parser_quality_service.py` 的直接测试，只搬纯规则或轻量 IO wrapper；保留 `_ensure_quality_report` 在 app impl 编排。
2. 继续扩展 `pdf_parser_financial_service.py` 测试，只搬 financial 纯规则或轻量 IO wrapper；保留“何时重算”的 owner 在 app impl。
3. 继续扩展 `pdf_parser_document_full_service.py`：只搬 document_full 纯 payload/resource helper；app impl 保留路径解析、文件存在性、写入/ensure/刷新 owner。
4. 继续扩展 `pdf_parser_content_list_enhanced_service.py` / `pdf_parser_mineru_result_service.py`：只搬 content_list_enhanced 的纯展示/附录/payload helper 和 MinerU 原始产物落盘 helper；app impl 保留 `_fetch_and_cache_result`、`_ensure_*` 和 task state owner。

不搬迁：

- `_fetch_and_cache_result`：它拥有上游拉取、状态流转、日志和持久化。
- `_mark_completed_missing_artifact`、queue worker/claim、本地任务状态机。
- Flask route response/error owner。

`pdf_parser_app_impl.py` 当前状态 owner 清单：

- Flask app 生命周期、鉴权 hook、route 入参、`jsonify/send_file/render_template/make_response`。
- SQLite task 状态和本地任务变更顺序：status/stage/error/logs/timestamps、duplicate upload、recent task refresh。
- 本地 queue：`_queue_lock`、`_queue_wakeup`、queue claim、stale submitting recover、worker loop 和 wakeup。
- MinerU lifecycle：提交、轮询、cancel、result fetch/cache、失败/404 映射和 `completed_missing_artifact`。
- artifact ensure 顺序：`_fetch_and_cache_result`、`_save_mineru_artifacts`、quality/financial/document_full/content_list_enhanced/table_relations 的 `_ensure_*` 触发时机。
- download/open response：artifact allowlist 后的 file response、download filename、headers 和 HTTP error mapping。
- cleanup/retention：task record、uploads/results/output orphan cleanup。

验收：

- 新模块有直接单元测试；当前 `test_pdf_parser_quality_service.py` 已覆盖候选回填、摘要 warning、质量报告文件读写、银行噪声表过滤、statement display source 噪声 index 回落附近真实资产负债表、非数字行号防御、candidate summary 与 priority review 去重/截断，`test_pdf_parser_financial_service.py` 已覆盖 financial 路径/读取/current/write/ensure、schema/rule mismatch、单边 artifact 读取和 stale checks 触发重写边界，`test_pdf_parser_document_full_service.py` 已覆盖 resource index、document_full payload、table relations payload、relation alias、无效表过滤、file reference、缺失 source/resource 状态、content_list_enhanced 回写 document_full 初始化与不突变边界，`test_pdf_parser_response_service.py` 已覆盖 duplicate payload、recent limit clamp 和 recent task normalization，`test_pdf_parser_content_list_enhanced_service.py` 已覆盖 content_list_enhanced payload、table source helper、page inference helper、quality signals、image semantic blocks 与 complete markdown 写出，`test_pdf_parser_mineru_result_service.py` 已覆盖 MinerU 原始产物落盘边界；后续继续观察 quality report fallback 与更复杂的 complete markdown 回填。
- 当前聚焦门禁：`cd apps/pdf-parser && python3 -m pytest tests/test_pdf_parser_response_service.py tests/test_pdf_parser_document_full_service.py tests/test_pdf_parser_quality_service.py tests/test_pdf_parser_financial_service.py -q` 通过，38 passed。
- `cd apps/pdf-parser && python3 -m pytest tests -q` 通过，150 passed。
- `cd apps/pdf-parser && python3 -m flask --app app.py routes` 通过。
- `pdf_parser_app_impl.py` 行数继续下降，且没有把新 service 变成状态 owner。

### A-001：拆 `agent_chat_runtime.py`

优先级：P1
范围：`apps/api/services`
状态：进行中
动作：

- 入口层已收敛为兼容 façade，原实现下沉到 `agent_chat_runtime_impl.py`。
- 已新增 `agent_runtime_attachments.py`、`agent_runtime_streaming.py`、`agent_runtime_memory.py`、`agent_runtime_citations.py`、`agent_runtime_loop_guard.py`、`agent_runtime_tools.py`、`agent_runtime_sessions.py` 作为领域边界 façade。
- `agent_runtime_loop_guard.py` 已升级为真实实现模块，loop 检测、history sanitizer、失败回复清洗和相关停止消息常量已从 `agent_chat_runtime_impl.py` 搬出；旧 `services.agent_chat_runtime` 入口仍可访问同名符号。
- `agent_runtime_progress.py` 已新增为真实实现模块，progress payload/signature、文本进度提取、tool preview/label 已从 `agent_chat_runtime_impl.py` 下沉；impl 保留同名 wrapper 并传入当前 hash/clock/wiki root，保持 monkeypatch 语义。
- 其余 `agent_runtime_*` 仍作为边界和迁移索引，不作为可 monkeypatch 的真实状态 owner；router 仍从兼容入口导入，避免 SSE active-run、history、attachments 的绑定语义发生变化。
- 继续拆为 session、attachments、streaming、memory、citation、tools；下一步先拆 display normalization / parse-only discovery 等纯函数，再考虑 ACTIVE_RUNS 和 SSE 状态 owner。
- 先纯搬迁，不改变行为。

验收：

- `python3 -m py_compile apps/api/services/agent_chat_runtime.py apps/api/services/agent_chat_runtime_impl.py apps/api/services/agent_runtime_*.py` 通过。
- `cd apps/api && uv run pytest tests/test_agent_chat_runtime_loops.py -q` 通过，54 passed。
- `cd apps/api && uv run pytest tests/test_agent_runtime_progress.py tests/test_agent_chat_runtime_loops.py -q` 通过，57 passed。
- `cd apps/api && uv run pytest tests/test_agent_chat_runtime_attachments.py tests/test_agent_chat_runtime_loops.py tests/test_agent_router_attachments.py tests/test_chat_document_parser_attachment.py -q` 通过，72 passed。
- `cd apps/api && uv run pytest tests/test_agent_runtime_progress.py tests/test_agent_chat_runtime_attachments.py tests/test_agent_chat_runtime_loops.py tests/test_agent_router_attachments.py tests/test_chat_document_parser_attachment.py -q` 通过，75 passed。
- SSE 事件语义不变。
- 高风险项：`ACTIVE_RUNS` 必须保持单一 owner；普通 chat 与 SSE 路径共享 attachments/history/local-memory/build-run-input 顺序，真实拆分前需要先补覆盖或只搬纯函数。

### A-002：拆 Agent runtime 纯函数边界

优先级：P1
范围：`apps/api/services`
状态：进行中
背景：

- `agent_chat_runtime_impl.py` 仍约 6622 行，是当前最大后端文件。
- `ACTIVE_RUNS`、SSE event append、run lifecycle、ordinary chat 与 streaming 状态共享仍高度耦合，暂不迁移 owner。
- 可继续安全拆分的是纯函数和只读 discovery 逻辑。
- 已新增 `agent_runtime_tool_output.py`，搬出 `_normalize_tool_output` 的纯函数实现；`agent_chat_runtime_impl.py` 通过 import alias 保持 `_normalize_tool_output` 兼容入口，未迁移 `ACTIVE_RUNS`、SSE、run lifecycle、attachments/history owner。
- 已新增 `agent_runtime_parse_only.py`，搬出 `_pdf2md_parse_only_matches`、`_should_consider_pdf2md_parse_only_context`、`build_pdf2md_parse_only_context` 的只读 discovery 逻辑；旧同名函数仍由 `agent_chat_runtime_impl.py` wrapper 转发，保持兼容入口。
- 已新增 `agent_runtime_display.py`，搬出 `_display_message_with_attachments` 的纯展示格式化逻辑；旧同名函数仍由 `agent_chat_runtime_impl.py` wrapper 转发，消息保存顺序不变。
- 已扩展 `agent_runtime_citations.py`，下沉 plain inline LaTeX normalization、evidence trace normalization、结构化 citation 检测、primary data source refs 合并、PostgreSQL fallback context 和多类 primary-data supplement renderer；旧同名函数仍由 `agent_chat_runtime_impl.py` wrapper 转发，保留 evidence fallback 编排和 monkeypatch 入口。
- 已新增/实化 `agent_runtime_fallback_contexts.py`、`agent_runtime_memory.py`、`agent_runtime_dedupe.py` 与 `agent_runtime_context.py`，下沉 PostgreSQL fallback row helper、local-memory summary/context 纯 helper、runtime dedupe helper、context/company helper、analysis completion guard intent helper、general assistant context input helper、multi-company session context helper 和 Hermes run input text/multimodal helper；`agent_chat_runtime_impl.py` 保留 DB session memory 刷新、普通 chat/streaming 共享状态 owner 和兼容 wrapper。

建议拆分顺序：

1. 继续扩展 `agent_runtime_display.py`、`agent_runtime_parse_only.py`、`agent_runtime_tool_output.py`、`agent_runtime_context.py` 或 `agent_runtime_citations.py`：只搬同类纯函数，给 loop diagnosis 和 streaming 复用。
2. 只在上述纯函数稳定后，再评估 attachments/history/local-memory/build-run-input 的真实 owner 拆分。

不搬迁：

- `ACTIVE_RUNS` dict。
- `_append_state_event`、`_append_progress_event`。
- `stream_agent_chat` / `stop_agent_run` run lifecycle。
- 会改变 monkeypatch 绑定语义的全局配置。

验收：

- 新增 `tests/test_agent_runtime_display.py`、`tests/test_agent_runtime_parse_only.py`、`tests/test_agent_runtime_tool_output.py`、`tests/test_agent_runtime_context.py` 或等价覆盖；当前 citations/display/parse-only/context 已覆盖正文已有引用去重、缺文件名附件通用 label、附件 URL Markdown target 编码、LaTeX inline symbol normalization、source ref 去重编号和 requested metric evidence guard、跳过已有 Wiki 后再应用 `limit`。
- 当前聚焦门禁：`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_context.py tests/test_agent_runtime_display.py tests/test_agent_runtime_parse_only.py tests/test_agent_runtime_citations.py -q` 通过，38 passed。
- `cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_display.py tests/test_agent_runtime_parse_only.py tests/test_agent_runtime_tool_output.py tests/test_agent_runtime_progress.py tests/test_agent_chat_runtime_loops.py tests/test_agent_chat_runtime_attachments.py -q` 通过，77 passed。
- SSE 事件字段、停止按钮、orphaned run 恢复语义不变。

## 10. 验收标准总表

### 仓库治理 DoD

- `git ls-files data` 只剩 README、`.gitkeep` 或明确小型 fixtures。
- 大 PDF、DB、备份、运行日志不再被跟踪。
- `var/`、`artifacts/`、`datasets/` 职责清晰。
- 新运行态路径通过环境变量可配置。
- 合并前 `git diff --cached --name-only` 不包含 `.venv`、`__pycache__`、`.pytest_cache`、`dist`、`test-results`、大 PDF/DB/下载产物。
- 大型重构按主题拆提交，不能把运行态索引删除、前端迁移、API 拆分、PDF parser 拆分和 Agent 拆分混在一个提交里。

### 后端 DoD

- `market_reports.py` 变为薄路由。
- 长任务有持久状态。
- 脚本执行统一 runner。
- evidence package 有共享 reader/validator。
- settings 可注入、可测试。
- API-finder-rules contract tests 通过。
- 当前基线：`apps/api` Agent runtime 定向 77 tests、`apps/pdf-parser` 138 tests、`apps/document-parser` 27 tests、finder 46 tests、rules 29 tests、market-contracts 2 tests 已通过；本轮未重跑完整 `apps/api` 全量。

### 前端 DoD

- route/nav/permission/preload 单源配置。
- PDF 和多市场解析共享 workbench。
- API client 收口。
- legacy UI 不再被新代码默认使用。
- 核心页面 Playwright smoke 通过。
- 当前基线：`npm run check:frontend` 通过，关键 Playwright responsive 覆盖通过；后续前端主要剩 `PDF_CSS` / `DOCUMENT_CSS` 运行时字符串单独评估和必要的响应式 smoke。

### 运维 DoD

- `start_all.sh`、Docker Compose、README 对环境变量一致。
- Docker Compose profile 覆盖主要服务。
- 健康检查能展示有效配置摘要但不泄露 secret。

## 11. 风险与回滚

### 11.1 数据治理风险

风险：误删本地 PDF/DB/解析结果。
控制：

- 只执行 `git rm --cached`，不执行 `rm -rf data/...`。
- 执行前用 `du` 和 `git ls-files` 生成清单。
- 必要时先复制到外部目录。

### 11.2 API 拆分风险

风险：外部 URL 或响应字段变化导致前端回归。
控制：

- 先抽 service，不改 endpoint。
- 保留现有 tests。
- 增加 golden response fixture。

### 11.3 长任务迁移风险

风险：任务状态和旧前端轮询不兼容。
控制：

- 新 job service 兼容旧响应字段。
- 先迁移一个低风险任务，再迁移所有 package/import/vector 任务。

### 11.4 前端 feature 化风险

风险：大范围移动文件导致样式和状态回归。
控制：

- 每次只迁移一个页面族。
- 先 pure move，再行为重构。
- 每轮执行 `npm run lint && npm run build`，关键流程跑 Playwright。

### 11.5 CN legacy 风险

风险：多市场重构影响 A 股 legacy 链路。
控制：

- 默认不改 CN 解析、入库、财务抽取语义。
- 涉及公共工具抽取时必须跑 CN 回归测试。

## 12. 后续窗口建议执行顺序

建议后续窗口按以下节奏接力：

1. 窗口 A：只做 Phase 1，完成 Git 索引和目录治理，不碰业务代码。
2. 窗口 B：实现 `CommandRunner` 和持久 job，先接一个 package build 任务。
3. 窗口 C：合并 PDF / Market parsing workbench，并补市场隔离回归。
4. 窗口 D：统一前端 API client，先覆盖高频页面。
5. 窗口 E：拆 `pdf-parser/app.py` 和 `agent_chat_runtime.py`。
6. 窗口 F：补 contract tests、job tests、Playwright 主流程。

每个窗口开工前应先执行：

```bash
cd /home/maoyd/siq-research-engine
git status --short
```

如果发现与自己任务无关的改动，不要回退；只在自己的文件范围内工作。

## 13. 参考审查结论

本方案基于以下审查方向汇总：

- 仓库路径与文件治理。
- 后端控制面、长任务、配置和服务边界。
- 前端路由、组件、API client、状态和 E2E。
- 数据、脚本、评测集、Docker、本地启动和运行态目录。

核心判断：

```text
先让仓库边界干净，
再让 API 控制面变薄，
再让契约成为单一事实来源，
最后逐步做前端 feature 化和服务内部拆分。
```
