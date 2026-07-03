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
- 进展补充：`F-004` 已完成 `PdfSourceWorkbench.tsx` 第二阶段拆分，新增 `pdfSourceWorkbenchHelpers.ts`，把页码/bbox、跨页表关系、overlay 构建和物理表合并等纯 helper 搬出，并补 `pdfSourceWorkbenchHelpers.test.ts` 直接覆盖 page number/bbox、page table sort、物理表合并、table relation artifact、overlay 和 fallback HTML 边界；`SearchDownload.tsx` 已完成 model/table/downloaded panel、search/download flows、URL state、日志派生、download refresh 判定和 toast 文案 helper 拆分；`index.css` 已将 search/download、dashboard、通用 surface/button/search、quick-question、chat rendered/table/code、agent dock/composer、chat page shell 以及 root/body/dark/focus/reduced-motion/app spacing 全局基线迁到 `styles/search-download.css`、`styles/dashboard.css`、`styles/system-surfaces.css`、`styles/quick-questions.css`、`styles/chat.css`、`styles/app-base.css`，`index.css` 退为 import + theme 外壳；Document/PDF/Market parsing 的 feature API 已成为实现 owner，`lib/documentApi.ts`、`lib/pdfApi.ts`、`lib/secApi.ts` 退为兼容 re-export；`DocumentResultWorkbench.tsx` 已完成纯 utils、source preview、artifact/table/figure/status/extract/markdown panes、source lookup、table lookup、focused relation、preview page model 和 JSON preview 派生拆分，父组件保留 overlay `data-*`、mobile tab、refs、selection/scroll 和 resource open owner；移动端工作平台/系统平台宽度不一致已用响应式 E2E 固化。
- 进展补充：`DocumentResultWorkbench.tsx` 已完成 `documentResultFocusController.ts`、`documentResultViewModel.ts` 和 `documentResourceOpener.ts` 的最小抽取，分别收口 active page/focus/tab 状态、base/focused 派生链和资源打开错误态；相关测试已补齐并通过，父组件现在主要保留 refs、selection、scroll、resource open 调用位和 JSX 组合。
- 进展补充：`P-002` 已完成 quality/financial/document_full/content_list_enhanced/MinerU result 第一轮边界拆分，新增 `pdf_parser_quality_service.py`、`pdf_parser_financial_service.py`、`pdf_parser_document_full_service.py`、`pdf_parser_content_list_enhanced_service.py`、`pdf_parser_mineru_result_service.py`、`pdf_parser_response_service.py` 与聚焦测试；`pdf_parser_artifact_service.py` 已新增 open artifact name 纯分类 helper，并已最小接入 `open_artifact` Flask route，覆盖 images/download、images、images/<name>、allowlist artifact、forbidden artifact、missing artifact 和空图片下载；`pdf_parser_response_service.py` 已继续收拢 status response payload 纯 helper，`pdf_parser_app_impl.py` 只保留 elapsed/progress/markdown/local queue 依赖注入；`pdf_parser_quality_service.py` 已继续收拢 quality report payload 与基础 warning/info message 纯 helper，`pdf_parser_app_impl.py` 只保留 report kind/year、table index、candidate grouping 和 generated_at 注入；`pdf_parser_document_full_service.py` 已继续收拢 table relations payload、content_list_enhanced 回写 document_full 的纯 payload helper，并补强 relation table merge、relation alias 回填、无效表过滤、缺 body enhanced table 由 content_list 回填、missing-body content table source id 不串用、未知/非 dict relation 负路径、file reference、缺失 source/resource 状态和 content_list_enhanced 回写初始化覆盖；`pdf_parser_quality_service.py` 已补强银行资产负债表附近表定位噪声过滤、季度报告核心表规则、`equity_statement` 回填“所有者权益变动表”、key_metrics 回填“主要会计数据”时继承 `table_index` 表源元数据覆盖、statement display source 遇到噪声 table index 时回落附近真实资产负债表、有效 table index 不被 nearby fallback 抢走、非数字 `line_numbers` 防御、`candidate_summary_list` 和 `priority_review_tables` 规则、quality fallback 噪声过滤和 statement/metric 既有完整候选不覆盖边界；`quality_engine.py` 已补 report year / candidate confidence / candidate group 纯函数边界；`pdf_parser_financial_service.py` 已补 financial schema/rule mismatch、单边 artifact 读取和 stale checks 触发重写覆盖；`pdf_parser_content_list_enhanced_service.py` 已继续收拢 `build_content_list_enhanced_payload` 顶层 payload 组装、table source 映射/匹配、打印页码映射、Markdown 页码推断、脚注/Markdown 行号、目录/标题 helper、enhanced quality signals 聚合、flowchart 结构化图像和按需 OCR/VLM 候选图像附录覆盖，并补 `_markdown_image_details`、`_markdown_table_to_records`、`_mermaid_to_nodes_edges` 与重复图片路径绑定边界；`pdf_source_viewer.py` 已补 source-view payload 纯 helper 直接覆盖，包含 page bbox extent coercion/非法 bbox/目标页过滤、printed page number 映射、page content JSON 输入、非法页码、非 list 空 payload、source_id/bbox 表匹配、content_table_source_id=0、非法 report table row 跳过、非数字 focus table 行为、image/list/unknown block 边界；`pdf_parser_app_impl.py` 状态 owner 已清单化，仍保留 Flask route response、task state、queue claim、路径存在性、文件写入、`_fetch_and_cache_result` 和 `_ensure_*` 重编排 owner。
- 进展补充：`A-002` 已完成 tool output、parse-only discovery、attachment display、citation/evidence 渲染 helper、PostgreSQL fallback row helpers、PostgreSQL fallback parse/predicate helper、three-statement record context helper、Wiki fulltext 文本/snippet/alias/search terms 匹配 helper、Wiki catalog 只读 helper、local-memory 纯 helper、runtime dedupe helper、context/company helper、financial guard/calculation trace warning helper、financial display format helper、analysis completion guard intent/reply/input helper、general assistant context input helper、multi-company session context helper、Hermes run input text/multimodal helper、statement/note detail intent helper、attachment classification helper、PDF2MD parse-only alias/match helper 和 citation record label helper 下沉，新增/扩展 `agent_runtime_tool_output.py`、`agent_runtime_parse_only.py`、`agent_runtime_display.py`、`agent_runtime_citations.py`、`agent_runtime_fallback_contexts.py`、`agent_runtime_catalog.py`、`agent_runtime_postgres_fallback.py`、`agent_runtime_statement_context.py`、`agent_runtime_financial_format.py`、`agent_runtime_memory.py`、`agent_runtime_dedupe.py`、`agent_runtime_context.py`、`agent_runtime_financial_guard.py` 与聚焦测试；已补 `pdf_page_number` / `markdown_line` 引用别名去重、supplement 引用合并、正文已有引用去重、LaTeX inline symbol normalization（含带空格写法）、evidence trace 展示归一化调用顺序、primary-data evidence trace 判定、reference line filtering、source locator 默认值与链接追加、primary data source ref 默认值、source ref 去重编号、citation 引用区插入位置与正文 metric guard、auto evidence section strip、requested metric evidence guard、human capital / three-statement / statement table / note detail / wiki fulltext / PostgreSQL supplement renderer、three-statement record 递归迭代/期间排序/source fallback/核心记录判定/latest 选择、PostgreSQL query text/company_all/metric terms/row predicate、financial display number/per-capita/formula/table ref formatting、wiki fulltext report_id 默认 file、wiki fulltext html/text normalization、company alias 提取/剔除、fallback search terms 清洗/去重/sort、specific term filtering、line scoring、snippet 截断、PDF 页回溯、nearest table meta、Wiki catalog intent/排序/格式化/负路径、note detail direct/context statement/direct/empty guard 细边界、analysis_completed_artifacts code 兜底/负路径、analysis completion guard/general context 负路径、progress payload clamp / 文本提取 / tool-label 判定细边界、financial tool availability correction 与 reconciliation trace guard、display 绝对 URL / 空值 URL 编码与 path filename fallback、parse-only alias/limit/context-hint 细边界、record preview/statement value helper、markdown link label 清洗、附件 path basename 与通用 attachment 标签兜底、空白 filename fallback、kind normalization、未知 kind 文档链接兜底、空/多附件默认提示、附件 URL Markdown target 编码、query/fragment URL 编码、空白 URL 与混合附件独立处理、交易所前缀文件名股票代码/公司名匹配、6 位股票代码与港股 5 位边界、短 alias 防误匹配、parse-only 大小写 fallback term、parse-only artifact 字段完整输出、parse-only 无匹配返回空、general/company-dir 短路，以及跳过已存在 Wiki 后再应用 `limit` 的覆盖；`agent_chat_runtime_impl.py` 仍保留 `ACTIVE_RUNS`、SSE append、run lifecycle、DB session memory 刷新和普通 chat/streaming 共享状态 owner。
- 本轮追加：`pdf_parser_content_list_enhanced_service.py` 已下沉财报附注金额解析、单位倍率、近邻单位和金额误差比较 helper，`pdf_parser_app_impl.py` 保留兼容 wrapper；`agent_runtime_financial_format.py` 已下沉人效/人均场景的数字解析、行数值提取和 table trace 格式化 helper，`agent_chat_runtime_impl.py` 保留 source link 注入 wrapper。
- 本轮追加：Agent runtime citations/reference 合并边界补测已完成，覆盖空 body 新增引用区、全部无效 refs 原样返回、三级引用来源 section 在 peer/parent heading 前收口；`citation_links.py` 修复 printed_page 空槽位对齐，并补充缺 task_id/pdf_page 原样返回、本地 API 链接 query/fragment 保留和重复链接不追加覆盖。
- 本轮追加：PDF2MD parse-only context 剩余边界已补，覆盖市场前缀伪 alias 防误匹配、非 dict task info 过滤、空 task/artifact 字段展示兜底；PostgreSQL fallback pure helper 已补空 hint、0 值页码/表号、空 terms callback 短路和缺字段负路径；前端 citation renderer 新增 `rendererUtils.test.ts` smoke，覆盖 source/table action 抽取、普通 Markdown link 保留和长引用行解析。
- 本轮追加：Agent runtime display/tool-output/context 剩余细边界已补，覆盖 display 的 None URL、无 basename path fallback、控制空白 URL 编码，tool-output 的 None/空白、list JSON、长文本换行、tool/label 字段隔离，以及 context 的非 dict model/nested field、format context、attachment model_dump 脏数据防御。
- 当时建议：红灯 owner 设计窗口启动，并已完成首个 PDF parser queue claim/recover 最小试点；后续 PDF artifact orchestrator、前端 Document hook、Agent runtime active SSE / stop owner 和 `_collect_stream_run` 接线矩阵均已分轮完成，当前建议见 0.5 收口记录。

### 0.2 2026-06-30 深度全量检查结论

本次全量复核覆盖 Git 索引、目录结构、关键大文件、后端/前端/服务测试和启动入口。结论：

- 仓库索引治理有效：`git ls-files data` 只剩 `data/README.md`、`data/backend/.gitkeep`、`data/pdf-parser/.gitkeep`。
- `R-003` 之前工作树非常脏：`git status --short | wc -l` 约 725 行，包含大量已从索引移出的 data 删除项、前端/后端重构改动、未跟踪新模块和生成目录；该风险已通过分组 review/提交收口。
- `.gitignore` 已覆盖 `data/**`、`var/**`、`artifacts/**`、`**/.venv/`、`**/.pytest_cache/`、`**/__pycache__/`、`apps/web/dist/`、`apps/web/test-results/`、`apps/web/playwright-report/` 等运行态和生成目录；本地仍存在大量 ignored cache/runtime 目录，不应纳入提交。
- 当前最大剩余大文件：`agent_chat_runtime_impl.py` 约 6061 行、`pdf_parser_app_impl.py` 已降至约 3948 行、`apps/web/src/index.css` 已降至约 85 行，新增 `apps/web/src/styles/app-base.css` 约 162 行，`apps/web/src/styles/chat.css` 约 1121 行，`SearchDownload.tsx` 约 961 行但 download refresh/toast 派生已拆到 feature helper，`DocumentResultWorkbench.tsx` 已降至约 452 行；`PdfSourceWorkbench.tsx` 已降至约 708 行，新增的 `pdfSourceWorkbenchHelpers.ts` 约 742 行，后续可继续按 UI/数据派生边界拆分。
- 前端 route registry 已单源化；API client 核心能力已收口到 `shared/api/client.ts`，业务组件/页面已迁到 `features/*/api.ts` 或 shared client；`lib/documentApi`、`lib/pdfApi`、`lib/secApi` 已降为 feature API 兼容 re-export，`lib/apiClient` 暂作为 shared client 兼容出口保留。
- PDF parser 已完成入口 façade、request/runtime/page-marker/task-repository/artifact/source 第一阶段拆分；quality/financial/document_full/content_list_enhanced/MinerU 原始产物落盘已完成第一轮 service 下沉，`pdf_source_viewer.py` 的 source-view/page content payload helper 已有直接边界测试，且 report table 非数字页码防御已固化；`pdf_parser_app_impl.py` 仍保留任务状态、路由响应、queue claim 和 `_ensure_*` 编排。
- Agent runtime 已完成入口 façade、loop guard、progress/tool label、tool output normalization、parse-only discovery、display normalization、citation/evidence 渲染 helper、PostgreSQL fallback row/parse/predicate helpers、local-memory 纯 helper，以及普通 chat / streaming 的请求 envelope 与 run preflight context 薄边界；`ACTIVE_RUNS`、active SSE replay/heartbeat 和 stop owner 已迁入 `agent_runtime_streaming.py`，sessions/history/memory/dedupe/build-run-input owner 仍必须留在 `agent_chat_runtime_impl.py`，后续继续拆分需单独设计窗口。

本次验证基线：

```bash
cd apps/pdf-parser && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q  # 245 passed
cd apps/pdf-parser && python3 -m flask --app app.py routes         # 23 lines / routes loaded
cd apps/document-parser && python3 -m pytest -q                    # 27 passed
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_display.py tests/test_agent_runtime_parse_only.py tests/test_agent_runtime_tool_output.py tests/test_agent_runtime_progress.py tests/test_agent_chat_runtime_loops.py tests/test_agent_chat_runtime_attachments.py -q  # 104 passed
cd services/market-report-finder && uv run pytest -q               # 46 passed
cd services/market-report-rules && uv run pytest -q                # 29 passed
cd packages/market-contracts && uv run pytest -q                   # 2 passed
cd apps/web && npm run lint                                        # passed
cd apps/web && node --test src/features/search-download/urlState.test.ts src/features/search-download/downloadStatus.test.ts src/components/pdf/pdfSourceWorkbenchHelpers.test.ts  # 12 passed
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
- 红灯 owner 当时要求单独设计：`ACTIVE_RUNS`、SSE lifecycle、PDF parser queue claim/worker/Flask response、Document workbench refs/selection/scroll 不混入加速批次；后续已完成 active SSE / stop owner 最小迁移，普通 chat/history/attachments/memory/dedupe 仍不混批。
- 提速不扩大爆炸半径：每轮优先选择可回滚、可聚焦验证、不会跨越运行时状态 owner 的改动；文档只记录关键决策和验证结果，不做过度整理。

- `F-004` 前端 feature 化与样式收口：剩余约 0-2 个小轮次，约 0.25-0.75 天。
  1. `SearchDownload.tsx` toast / download refresh / 下载状态派生收口已完成；状态 owner 留页面层，新增 `features/search-download/downloadStatus.ts` 和直接单测。
  2. `DocumentResultWorkbench.tsx` json preview / page overlay derivation 已完成；父组件继续保留 refs、selection、scroll 和 resource open owner。
  3. `index.css` 全局/响应式样式审计已完成：root/body/dark/base focus/reduced-motion/app spacing 已迁到 `styles/app-base.css`，`index.css` 降至约 85 行；`PDF_CSS` / `DOCUMENT_CSS` 运行时字符串继续单独窗口评估。
  4. feature API 显式导出清理已基本完成；`features/document-parser/api.ts`、`features/pdf-parsing/api.ts`、`features/market-parsing/api.ts` 已成为实现 owner，`lib/documentApi.ts`、`lib/pdfApi.ts`、`lib/secApi.ts` 仅兼容 re-export。
- `P-002` / `P-001` PDF parser 边界拆分：剩余约 1-2 个小轮次，约 0.25-0.75 天。
  1. `content_list_enhanced` 脚注、目录、Markdown 页码派生 helper 已继续下沉；财报附注金额解析、单位倍率、近邻单位和金额误差比较 helper 已下沉到 `pdf_parser_content_list_enhanced_service.py`；`pdf_parser_app_impl.py` 仅保留兼容 wrapper，并补 service 级单测。
  2. `document_full` resource / table relation payload 覆盖已补强；本轮新增缺 body enhanced table 由 content_list 回填、missing-body content table source id 不串用、未知 table id / 非 dict relation 负路径覆盖；open artifact resolver 已先抽纯“artifact name 分类/路径/mimetype 决策”helper + 直接单测，并已最小接入 `open_artifact` route；Flask `send_file/jsonify`、错误文案、status code、下载名和 `.webp` 当前 mimetype 行为仍留 app。
  3. quality / financial / response 纯规则测试补强已继续推进；已覆盖银行资产负债表附近噪声表过滤、季度报告核心表规则、`equity_statement` 回填所有者权益变动表、key_metrics 表源元数据继承、statement display source 噪声 table index 回落、有效 table index 不被 nearby fallback 抢走、非数字行号防御、candidate summary、priority review 去重/截断、quality report payload/warning/info message、quality fallback 噪声过滤、statement/metric 既有完整候选不覆盖、financial schema/rule mismatch、单边 artifact 读取、stale checks 触发重写、duplicate payload、recent task normalization 和 status response payload，不改变 `_ensure_quality_report` / `_ensure_financial_artifacts` 调用时机。
  4. `pdf_parser_app_impl.py` 状态 owner 已清单化；queue claim / worker / Flask response 不在低风险拆分中修改。
- `A-002` / `A-001` Agent runtime 纯函数拆分：剩余约 2-5 个小轮次，约 0.5-1.5 天。
  1. Hermes run input / session context / intent 周边 helper 已继续下沉；`agent_runtime_context.py` 新增 statement/note detail intent、analysis completion guard/reply/input 与 attachment classification helper，保持普通 chat 和 streaming 调用顺序不变。
  2. citations / display / parse-only 只读 helper 补齐：已补引用别名字段去重、supplement 引用合并、正文已有引用去重、LaTeX inline symbol normalization、evidence trace 展示归一化调用顺序、primary-data evidence trace 判定、reference line filtering、source locator 默认值与链接追加、primary data source ref 默认值、source ref 去重编号、citation 引用区插入位置与正文 metric guard、auto evidence section strip、requested metric evidence guard、human capital / three-statement / statement table / note detail / wiki fulltext / PostgreSQL supplement renderer、wiki fulltext report_id 默认 file、company alias 提取/剔除、fallback search terms 清洗/去重/sort、人效/人均数字解析、行数值提取和 table trace helper、note detail direct/context statement/direct/empty guard 细边界、analysis_completed_artifacts code 兜底/负路径、analysis completion guard/general context 负路径、display 绝对 URL / 空值 URL 编码与 path filename fallback、parse-only alias/limit/context-hint 细边界、record preview/statement value helper、markdown link label 清洗、附件 path basename、通用 attachment 标签兜底、空白 filename fallback、kind normalization、未知 kind 文档链接兜底、空/多附件默认提示、附件 URL Markdown target 编码、query/fragment URL 编码、空白 URL 与混合附件独立处理、交易所前缀文件名股票代码/公司名匹配、6 位股票代码与港股 5 位边界、短 alias 防误匹配、parse-only 大小写 fallback term、parse-only artifact 字段完整输出、parse-only 无匹配返回空、general/company-dir 短路、跳过已存在 Wiki 后再应用 `limit` 覆盖、parse-only 市场前缀防误匹配、非 dict task info 过滤、空 artifact 字段兜底、display None URL/path fallback/URL control whitespace、tool-output None/blank/list/long text/tool-label 隔离、context 非 dict nested field/model_dump 防御，以及 citations/reference 空 body、全无效 refs、三级 section 收口和 citation link printed_page 空槽位对齐覆盖；后续继续按只读 helper + 直接单测推进。
  3. 下一步低风险优先级：PDF parser quality/source-view 低风险补测，或 Agent runtime attachments/history/local-memory owner 拆分前置覆盖。
  4. attachments / history / local-memory owner 拆分前置覆盖：高风险，至少 2 个提交；未补足覆盖前不迁移真实 owner。
  5. `ACTIVE_RUNS`、SSE event append、stop lifecycle：已完成最小 owner 迁移；`_collect_stream_run` / `stream_chat_reply` 仍保留单独设计窗口。
- 验证与文档：每轮都要做，约占开发时间 20%-30%。最低门禁为聚焦测试、`git diff --check`；涉及前端页面时跑 `npm run check:frontend`，涉及 PDF parser 时跑对应 service tests，涉及 Agent runtime 时跑对应 `apps/api` 聚焦测试。

本轮并行执行结果：

1. 前端窗口：完成 `SearchDownload.tsx` download refresh 判定、toast 文案 helper、`DocumentResultWorkbench.tsx` json preview / page overlay derivation、`index.css` 全局基线抽离、Document/PDF/Market parsing feature API 实现 owner 上移和直接/E2E 覆盖；页面继续保留下载状态、refs、selection、scroll 和 resource open owner。
2. PDF parser 窗口：完成 `content_list_enhanced` 脚注/Markdown 行号/目录标题 helper 下沉、财报附注金额解析/单位倍率/近邻单位/金额误差比较 helper 下沉、flowchart 结构化图像与按需 OCR/VLM 候选图像附录覆盖、artifact name 纯分类 helper及 `open_artifact` route 最小接入、status response payload helper、quality report payload/warning/info message helper、quality fallback 噪声过滤、statement/metric 既有完整候选不覆盖、`document_full` relation payload、relation alias、无效表过滤、缺 body enhanced table 回填、missing-body content source id 隔离、未知/非 dict relation 负路径、file reference、缺失 source/resource 状态、content_list_enhanced 回写初始化覆盖、quality 银行噪声表过滤、季度报告核心表规则、权益变动表回填、key_metrics 表源元数据继承、statement display source 噪声 index 回落附近真实资产负债表、有效 table index 不被 nearby fallback 抢走、非数字行号防御、candidate summary、priority review 去重/截断，以及 financial schema/rule mismatch、单边 artifact 读取、stale checks 触发重写、duplicate response、recent task clamp/normalization、source viewer page content payload 直接测试和 report table 非数字页码防御；`pdf_parser_app_impl.py` 状态 owner 已清单化，并继续保留 `_ensure_*` 编排 owner。
3. Agent runtime 窗口：完成 statement/note detail intent、attachment classification、PDF2MD parse-only alias/match、citation record label helper、Wiki fulltext alias/search terms helper、人效/人均数字解析/行数值提取/table trace helper 下沉，以及引用别名字段去重 / supplement 引用合并 / 正文已有引用去重 / LaTeX inline symbol normalization / evidence trace 展示归一化调用顺序 / primary-data evidence trace 判定 / reference line filtering / source locator 默认值与链接追加 / primary data source ref 默认值 / source ref 去重编号 / citation 引用区插入位置与正文 metric guard / auto evidence section strip / requested metric evidence guard / human capital、three-statement、statement table、note detail、wiki fulltext、PostgreSQL supplement renderer / three-statement record 递归迭代、期间排序、source fallback、核心记录判定、latest 选择 / wiki fulltext report_id 默认 file / company alias 提取与剔除 / fallback search terms 清洗、去重和排序 / Wiki catalog intent/排序/格式化/负路径 / note detail direct/context statement/direct/empty guard 细边界 / analysis_completed_artifacts code 兜底/负路径 / analysis completion guard/general context 负路径 / progress payload clamp / 文本提取 / tool-label 判定细边界 / display 绝对 URL / 空值 URL 编码与 path filename fallback / parse-only alias/limit/context-hint 细边界 / link label 清洗 / 附件 path basename 与通用 label 兜底 / 空白 filename fallback / kind normalization / 未知 kind 文档链接兜底 / 空/多附件默认提示 / 附件 URL 编码 / query/fragment URL 编码 / 空白 URL 与混合附件独立处理 / 交易所前缀文件名股票代码和公司名匹配 / 6 位股票代码与港股 5 位边界 / 短 alias 防误匹配 / parse-only 大小写 fallback term / parse-only artifact 字段完整输出 / parse-only 空匹配 / general 与已有 company dir 短路 / 跳过已有 Wiki 后再应用 `limit` 覆盖；当前 `ACTIVE_RUNS` / active SSE / stop owner 已迁到 `agent_runtime_streaming.py`，DB session memory 刷新与普通/streaming 编排仍留在 impl。
4. Agent runtime 引用边界追加：完成 citations/reference 合并边界补测，覆盖空 body 新增引用区、全部无效 refs 原样返回、三级引用来源 section 在 peer/parent heading 前收口；`citation_links.py` 修复 printed_page 空槽位对齐，并覆盖缺 task_id/pdf_page 原样返回、本地 API 链接 query/fragment 保留和重复链接不追加。
5. Agent runtime / frontend 追加：完成 parse-only 市场前缀伪 alias 防误匹配、非 dict task info 过滤、空 task/artifact 字段展示兜底；完成 PostgreSQL fallback 空 hint、0 值页码/表号、空 terms callback 短路和缺字段负路径；完成前端 citation renderer smoke，覆盖 source/table action 抽取、普通 Markdown link 保留和长引用行解析。
6. Agent runtime 细边界追加：完成 display 的 None URL、无 basename path fallback、控制空白 URL 编码；完成 tool-output 的 None/空白、list JSON、长文本换行保留、tool/label 字段隔离；完成 context 非 dict model/nested field、format context 与 attachment model_dump 脏数据防御。
7. 本轮聚焦验证：`cd apps/web && npm run check:frontend` 通过；`cd apps/web && npx playwright test e2e/tests/document-result-preview.spec.ts` 通过；`cd apps/web && npx playwright test e2e/tests/workspace-responsive.spec.ts e2e/tests/search-download-responsive.spec.ts` 通过，9 passed；`cd apps/web && npx playwright test e2e/tests/pdf-parsing-market-filter.spec.ts e2e/tests/search-download-responsive.spec.ts` 通过，5 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_display.py tests/test_agent_runtime_parse_only.py tests/test_agent_runtime_citations.py tests/test_agent_runtime_context.py -q` 通过，72 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_statement_context.py tests/test_agent_runtime_citations.py -q` 通过，37 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_catalog.py tests/test_agent_runtime_context.py -q` 通过，20 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_*.py -q` 通过，171 passed、1 warning；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_financial_format.py tests/test_financial_calculator.py tests/test_agent_chat_runtime_loops.py::test_human_efficiency_query_appends_metric_level_sources_for_basf tests/test_agent_chat_runtime_loops.py::test_multi_company_human_efficiency_context_includes_each_company_scope_and_basf_sources -q` 通过，53 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_postgres_fallback.py tests/test_agent_chat_runtime_loops.py -q` 通过，64 passed、17 warnings；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_fallback_contexts.py tests/test_agent_chat_runtime_loops.py::test_wiki_fulltext_fallback_searches_report_md_before_document_full tests/test_agent_chat_runtime_loops.py::test_wiki_fulltext_fallback_requires_specific_terms_for_halo_goodwill -q` 通过，12 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_financial_guard.py tests/test_financial_calculator.py -q` 通过，26 passed；`cd apps/pdf-parser && python3 -m pytest tests/test_pdf_source_viewer.py tests/test_pdf_parser_source_service.py -q` 通过，22 passed；`cd apps/pdf-parser && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider tests/test_pdf_parser_content_list_enhanced_service.py tests/test_page_markers.py -q` 通过，79 passed；`cd apps/pdf-parser && python3 -m pytest tests/test_pdf_parser_quality_service.py -q` 通过，20 passed；`cd apps/pdf-parser && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider tests/test_pdf_parser_document_full_service.py tests/test_table_relations.py -q` 通过，15 passed；`cd apps/pdf-parser && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q` 通过，245 passed；`git diff --check` 通过。
8. 本轮追加验证：`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_citations.py tests/test_citation_links.py -q` 通过，52 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_parse_only.py tests/test_agent_runtime_postgres_fallback.py -q` 通过，33 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_context.py tests/test_agent_runtime_display.py tests/test_agent_runtime_tool_output.py -q` 通过，40 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_*.py -q` 通过，189 passed、1 warning；`cd apps/web && npm run check:frontend` 通过；`git diff --check` 通过。

下一轮并行执行队列：

1. 前端窗口：CSS 主入口和 feature API owner 已收口；如继续前端，单独评估 `PDF_CSS` / `DOCUMENT_CSS` 运行时字符串或做低风险响应式 smoke，不与业务状态 owner 混做。
2. PDF parser 窗口：仅补低风险边界覆盖或继续观察 complete markdown 回填；不动 queue、Flask response 行为、`_ensure_*` 编排。
3. Agent runtime 窗口：citations/reference、PDF2MD parse-only context、PostgreSQL fallback pure helper、前端 citation renderer smoke、display/tool-output/context 细边界已补；随后已完成 active SSE / stop owner 最小迁移，下一步如继续 Agent runtime，优先 `_collect_stream_run` 极小切片，attachments/history/local-memory owner 仍暂不迁移。
4. 主线收口：合并上述改动后更新本节状态，跑聚焦验证，并按主题提交。

本阶段当时明确暂缓：

- 当时不拆 `ACTIVE_RUNS` 和 SSE lifecycle owner；随后已完成 active SSE / stop owner 最小迁移。
- 不改 PDF parser 本地 queue worker / claim / Flask response owner。
- 不迁移 `PDF_CSS` / `DOCUMENT_CSS` 运行时注入字符串。
- 不把 DocumentResultWorkbench 的 refs / selection / scroll owner 提前分散。

### 0.4 2026-07-01 智能体集群剩余工作量复盘

本轮启动 3 个只读智能体分别盘点 Agent runtime、PDF parser、前端/文档，并由主线程核对最近提交、当前大文件规模和方案记录。结论：当前优化方案已经从“大模块拆分期”进入“低风险边界补齐 + 红灯 owner 单独设计期”，可以适当提速，但不应把状态 owner 迁移混入普通补测批次。

当前代码规模与状态：

- `agent_chat_runtime_impl.py` 约 6197 行；pure helper 拆分和 `tests/test_agent_runtime_*.py` 覆盖已较充分，最近一次 runtime wildcard 为 189 passed、1 warning。剩余难点不是 helper，而是 attachments/history/local-memory 与 SSE/DB owner 的真实迁移。
- `pdf_parser_app_impl.py` 约 4102 行；PDF parser 第一阶段拆分基本完成，全量 `apps/pdf-parser` 基线为 245 passed。剩余主要是财报附注链接、table index / 质量候选、markdown page index 和 source-view loader wrapper 的低风险覆盖/小下沉。
- 前端 F-004 基本收口：`SearchDownload.tsx` 约 961 行但主要保留状态和事件编排，`DocumentResultWorkbench.tsx` 约 548 行，`PdfSourceWorkbench.tsx` 约 708 行，`index.css` 约 85 行。剩余更像维护尾项，而不是架构主风险。

更新后的剩余工作量估计：

- PDF parser：1-2 个小轮次，约 0.25-0.75 天。优先做财报附注链接 service 级测试/小下沉，其次做 table index / markdown page index / source-view loader wrapper 边界覆盖。
- Agent runtime：2-5 个小轮次，约 0.5-1.5 天。优先补 attachments/history/local-memory owner 拆分前置覆盖，再搬少量纯格式化 helper；真实 owner 迁移另开设计窗口。
- 前端/文档：0-2 个小轮次，约 0.25-0.75 天。优先补 `documentResultWorkbenchDerivations` 和 `pdfSourceWorkbenchHelpers` 直接单测，必要时做 `PDF_CSS` / `DOCUMENT_CSS` selector 清单和响应式 smoke，不迁移注入机制。
- 风险可控提速后的总体低风险队列：约 1-2.5 天可完成一轮收口；`ACTIVE_RUNS`/SSE、PDF queue/Flask response、Document workbench refs/selection/scroll 等红灯 owner 另行排期。

下一轮建议按以下并行队列推进：

1. PDF parser 主线：补 `_canonical_financial_note_ref`、`_financial_note_title_line_hit`、`_financial_statement_note_ref_hits`、`_financial_note_title_tree`、`_build_financial_note_amount_check`、`_build_financial_note_links` 的 service 级覆盖，并评估是否下沉到 `pdf_parser_content_list_enhanced_service.py`。
2. PDF parser 次线：补 `_table_structure_signals`、`_matched_financial_table_names`、`_classify_table_semantics`、`_build_table_index`、`_group_key_table_candidates` 和 `_markdown_page_index` 的直接测试；source-view 只补真实 loader wrapper 的缺失/非 list/content 空值边界。
3. Agent runtime 主线：补 `_message_attachments`、`chat_message_has_visible_payload`、`normalize_history`、user 历史附件 reference 注入和 local-memory DB 行为测试；如搬迁，只搬 `_attachment_reference_context`、`_pdf_parse_is_terminal`、`_chat_message_payload` 这类纯格式化逻辑，并保留 impl wrapper。
4. 前端尾项：新增 `documentResultWorkbenchDerivations.test.ts` 覆盖 JSON preview、page model、relations/focus derivation；扩充 `pdfSourceWorkbenchHelpers.test.ts` 的 page content blocks、relation fallback、invalid metadata 边界；CSS 字符串只做评估/smoke。

本轮开发进度追加：

- Agent runtime：已补 `_message_attachments` 对坏 JSON、非 list、非 dict、空 path 的过滤；`chat_message_has_visible_payload` 已覆盖 attachment-only message 与空 path 负路径；`normalize_history` 已覆盖 attachment-only user 历史注入“历史附件上下文”、本地路径和前端链接。
- PDF parser：已补 source-view table block 在 `content_list` 缺 caption/footnote 时从 report `source_caption` / `source_footnote` fallback；`page_content_payload` loader wrapper 已覆盖字符串页码 coercion、report/focus_table 透传和 invalid page 预校验。
- 前端：`pdfSourceWorkbenchHelpers.test.ts` 已补 `cssAttrValue` / `deriveTaskId` URL 边界、`chooseFocusTableIndex` 非 source page fallback、`pageExtentForPage` fallback/扩展计算，以及 trace overlay 仅在当前未聚焦页出现。
- 本轮验证：`cd apps/api && .venv/bin/python -m pytest tests/test_agent_chat_runtime_attachments.py -q` 通过，11 passed、7 warnings；`cd apps/pdf-parser && PYTHONPATH=. python3 -m pytest tests/test_pdf_source_viewer.py tests/test_pdf_parser_source_service.py -q` 通过，26 passed；`cd apps/web && node --test src/components/pdf/pdfSourceWorkbenchHelpers.test.ts` 通过，10 passed；`cd apps/web && npm run build` 通过；`git diff --check` 通过。
- 下一轮队列收窄：PDF parser 继续优先财报附注链接和 table index / markdown page index 纯规则；Agent runtime 继续 local-memory DB 行为和少量附件纯格式化 helper 前置覆盖；前端优先 `documentResultWorkbenchDerivations.test.ts` 或 CSS selector smoke。

本轮开发进度继续追加：

- Agent runtime：已新增 local-memory DB 路径测试，覆盖 `refresh_session_memory` 只持久化当前 profile/session 的较早消息、不把最近窗口或其他 session 写入记忆，以及 profile/session prefix 不匹配时跳过写入并不加载 local-memory context。
- PDF parser：`_build_financial_note_links` 已改为 app 层兼容 wrapper，财报附注链接纯规则下沉到 `pdf_parser_content_list_enhanced_service.py`；新增 service 级测试覆盖附注号精确链接、statement table page 来源、markdown note page 来源、金额表校验，以及同数字附注号但标题不匹配时不误连。
- 前端：新增 `documentResultWorkbenchDerivations.test.ts`，覆盖 JSON preview 原样透传、跨页 relation 过滤和 table id 索引、block/table/figure focus 派生、visible relations fallback、preview pages/page models bridge、overlay/page number、markdown preview 和相邻页边界。
- 本轮验证：`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_memory.py tests/test_agent_chat_runtime_attachments.py tests/test_agent_chat_runtime_loops.py -q` 通过，72 passed、39 warnings；`cd apps/pdf-parser && python3 -m py_compile pdf_parser_content_list_enhanced_service.py pdf_parser_app_impl.py && PYTHONPATH=. python3 -m pytest tests/test_pdf_parser_content_list_enhanced_service.py tests/test_page_markers.py -q` 通过，81 passed；`cd apps/web && node --test src/components/document-parser/documentResultWorkbenchDerivations.test.ts` 通过，8 passed；`cd apps/web && npm run build` 通过；`git diff --check` 通过。
- 下一轮队列再次收窄：PDF parser 转向 table index / markdown page index 直接测试或小下沉；Agent runtime 转向 `_attachment_reference_context` / `_pdf_parse_is_terminal` / `_chat_message_payload` 等纯格式化 wrapper 前置覆盖；前端转向 `PDF_CSS` / `DOCUMENT_CSS` selector 清单和响应式 smoke。

本轮开发进度最终补齐：

- Agent runtime：已补 `_attachment_reference_context` 的安全上传目录、图片/文档格式化、越界/缺失/非法项跳过和空输入边界；已补 `_pdf_parse_is_terminal` 的成功、失败、取消、超时、队列终态以及 pending/running/submitted 非终态；已补 `_chat_message_payload` 的用户内容保留、附件 JSON 过滤、坏 JSON 容错和助手 evidence 展示归一化。
- PDF parser：新增 `test_pdf_parser_table_index.py`，直接覆盖 `_table_structure_signals`、`_matched_financial_table_names`、`_classify_table_semantics`、`_build_table_index`、`_group_key_table_candidates` 和 `_markdown_page_index`，本轮不改业务代码。
- 前端：新增 `styleSelectorSmoke.test.ts`，覆盖 `PDF_CSS` / `DOCUMENT_CSS` 非空、关键 selector、selector inventory 下限和已知重复 selector 清单；未迁移运行时 CSS 注入机制。
- 本轮验证：`cd apps/api && .venv/bin/python -m pytest tests/test_agent_chat_runtime_attachments.py tests/test_agent_chat_runtime_loops.py -q` 通过，72 passed、27 warnings；`cd apps/pdf-parser && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q` 通过，260 passed；`cd apps/web && node --test src/components/document-parser/styleSelectorSmoke.test.ts src/components/document-parser/documentResultWorkbenchDerivations.test.ts src/components/pdf/pdfSourceWorkbenchHelpers.test.ts` 通过，20 passed；`cd apps/web && npm run build` 通过；`git diff --check` 通过。
- 下一步建议：低风险前置覆盖队列已基本收口。若继续提速，应先写红灯 owner 设计小节和回滚/验证矩阵，再选择一个 owner 试点；否则只做 CI 文档清单、测试命令固化和少量不触碰状态 owner 的维护项。

当时继续明确的红灯边界：

- 当轮不拆 `ACTIVE_RUNS`、`ActiveRunState`、SSE event append、stream/stop run lifecycle；当前 active SSE 与 stop owner 已完成最小迁移。
- 不迁移普通 chat 与 streaming 共享的 history、attachments、memory、dedupe、run input 顺序。
- 不改 PDF parser queue claim/worker、MinerU 生命周期、Flask `send_file/jsonify`、`_ensure_*` artifact 编排和 task state/DB 写顺序。
- 不提前分散 `DocumentResultWorkbench.tsx` 的 refs、selection、scroll、resource open owner。

### 0.5 2026-07-01 红灯 owner 设计窗口

低风险前置覆盖基本收口后，下一阶段不应继续盲目堆小测试，而应进入红灯 owner 的设计窗口。本轮启动 3 个只读智能体盘点 Agent runtime、PDF parser 和前端 Document owner，结论是：先补红灯 owner 的行为矩阵，再选一个最小试点；不要一次性迁移多个状态 owner。

本轮红灯试点进度：

- PDF parser：queue claim/recover 最小试点已完成；新增 `pdf_parser_task_lifecycle_service.py`，`pdf_parser_task_repository.py` 接管 `claim_next_queued_task` / `recover_stale_submitting_tasks` SQLite 状态迁移，`pdf_parser_app_impl.py` 保留 `_claim_next_queued_task` / `_recover_stale_submitting_tasks` wrapper；未触碰 Flask response、MinerU submit/poll、`_fetch_and_cache_result`、`_ensure_*` 和 DB schema。
- Agent runtime：新增 `test_agent_runtime_active_runs.py` 覆盖 active key/profile alias、SSE offset replay、`done/error` terminal drain、disconnect return、stop replace、404 orphan cleanup 和 missing active stop；本轮仍未迁移 `ACTIVE_RUNS` / SSE owner。
- Frontend：扩展 `document-result-preview.spec.ts` 多页 fixture，覆盖 markdown focus 后 PDF/markdown focused 同步、prev/next/select page sync、tab scroll buttons、resource open failure `.doc-error` 和 mobile select tab state；未抽 hook、未迁 CSS。
- 本轮验证：API active-runs 聚焦测试 10 passed、PDF parser 全量 266 passed、Document result preview e2e 5 passed、Web build passed；提交前继续执行 `git diff --check`。
- 下一步建议：PDF parser 可进入 artifact orchestrator 前置测试/最小抽取，或切到前端 `useDocumentResultViewModel` / focus controller 试点；Agent runtime SSE owner 继续先补 heartbeat、existing active run join、session default context 等剩余矩阵。

本轮迁移前护栏追加：

- PDF parser：新增 `test_pdf_parser_mineru_lifecycle.py`，覆盖 submit 前 `submitting` 持久化、submit 成功写入 `mineru_task_id` / `pending` / `submitted_at`、本地 upload missing -> `failed`、upstream 404 -> `failed`、upstream completed 时先 fetch artifacts 再最终 persist、completed 缺 Markdown -> `completed_missing_artifact`；仍未迁移 MinerU lifecycle / `_fetch_and_cache_result` / `_ensure_*` owner。
- Agent runtime：扩展 `test_agent_runtime_active_runs.py`，补 SSE heartbeat 不写入 buffer、existing active run join 不创建第二个 Hermes run、session default context 按 profile/session 隔离、alias stop 后 canonical stream 可读、terminal snapshot drain 前后稳定；仍未迁移 `ACTIVE_RUNS`、SSE append 或 DB/session owner。
- Frontend：扩展 `documentResultWorkbenchDerivations.test.ts` 与 `document-result-preview.spec.ts`，补 block/table/figure/page focus 边界隔离、新 task active page reset、resource opener 失败后成功清错、mobile tab 与 preview page 状态隔离；仍未抽 hook、未迁 `DOCUMENT_CSS` / `PDF_CSS`。
- 本轮验证：API active-runs + loop 代表用例 15 passed、PDF parser 全量 271 passed、Document derivation 9 passed、Document result preview e2e 6 passed、Web build passed、`git diff --check` 通过。
- 下一步建议：迁移前护栏已经覆盖到第二层，下一轮可二选一推进：做 PDF artifact orchestrator 最小抽取，或做前端 `useDocumentResultViewModel` / focus controller 最小 hook 试点；Agent runtime streaming owner 仍建议再单独开一轮设计/迁移，不和其他 owner 同批。

本轮 PDF artifact orchestrator 最小抽取：

- PDF parser：新增 `pdf_parser_artifact_orchestrator_service.py`，下沉 MinerU result payload 选择、页码 marker 注入/稀疏页回填、Markdown 写入回调、MinerU artifact 保存回调、quality/markdown/restored page 日志顺序、completed / completed_missing_artifact 状态编排；`pdf_parser_app_impl.py` 的 `_fetch_and_cache_result` 保留 HTTP fetch、local markdown fast-path、force 语义和 app wrapper 依赖注入。
- 护栏补强：新增 `test_pdf_parser_artifact_orchestrator_service.py`，扩展 `test_pdf_parser_mineru_lifecycle.py` 覆盖非 404 upstream error detail、无 `results`、无 `md_content`、local markdown + force/no upstream、local markdown + force/upstream refresh 和 quality log 顺序。
- 明确未改：Flask route response、DB schema、queue claim/recover、MinerU submit/poll、artifact 文件名/schema version、`_ensure_*` 质量/财报/document_full 编排 owner。
- 本轮验证：PDF orchestrator/lifecycle 聚焦 14 passed、PDF parser 全量 280 passed、API active-runs 12 passed、Document preview e2e 6 passed、`git diff --check` 通过。
- 下一步建议：PDF 红灯试点已进入第二个最小抽取点；下一轮更适合转前端 Document hook 最小试点，或单独设计 Agent runtime streaming owner。若继续 PDF，应只推进 artifact orchestrator 后续极小边界，不触碰 MinerU submit/poll 和 `_ensure_*`。

本轮前端 Document focus controller 最小试点：

- Frontend：新增 `documentResultFocusController.ts`，用纯 reducer + `useDocumentResultFocusController` 接管 `activePage` / `focused` / `activeTab` 状态转换；`DocumentResultWorkbench.tsx` 只改 hook 接线，保留 JSX 结构、className、resource opener、tab scroll owner 和 `DOCUMENT_CSS` / `PDF_CSS` 注入机制。
- 护栏补强：新增 `documentResultFocusController.test.ts`，覆盖 block/table/figure/page focus 同步 active page、新 task reset、mobile tab 切换不污染 active page/focus。
- 本轮验证：focus controller 3 passed、document derivations 9 passed、Document preview e2e 6 passed、Web build passed、旁路 PDF parser 全量 280 passed、API active-runs 12 passed、`git diff --check` 通过。
- 下一步建议：前端 hook 试点已完成 focus controller、view model 和 resource opener；下一轮如继续前端，建议转向更细的响应式 smoke 或小的行为回归，不动 refs/scroll、CSS 注入或 JSX 结构。

本轮前端 Document view model 最小抽取：

- Frontend：新增 `documentResultViewModel.ts`，把 `DocumentResultWorkbench.tsx` 的纯派生链拆成 `buildDocumentResultBaseViewModel` 和 `buildDocumentResultViewModel` 两层；base 层收口 artifact、source lookup、table lookup、markdown block、page number 和 json preview，focused 层只负责 relation / overlay / preview pages / preview page model。
- 护栏补强：新增 `documentResultViewModel.test.ts`，直接覆盖 base 视图模型的 page / markdown 派生和 focused 视图模型的 relation / preview 页面边界；新增 `documentResourceOpener.test.ts`，覆盖成功清错、异常报错和空 URL 跳过。
- 安全修正：`sanitizeMarkdownHtml` 在 `DOMPurify.sanitize` 不可用时不再原样返回 HTML，而是降级为转义文本；`documentResultWorkbenchDerivations.test.ts` 已补恶意表格 HTML 属性回归，避免 Node/SSR 类环境绕过净化。
- 代码边界：`DocumentResultWorkbench.tsx` 继续保留 refs、selection、scroll、resource open 调用位和 JSX 结构；`documentResultWorkbenchUtils.ts` 只补了清洗器安全降级，不扩大状态 owner。
- 本轮验证：Document derivations / view model / resource opener 聚焦测试 15 passed，`npm run check:frontend` 通过，`git diff --check` 通过。
- 下一步建议：前端 Document hook 试点已完成，后续只做维护尾项；如继续前端，优先补响应式 smoke、selector 清单或小型行为回归，不再碰 refs/scroll owner。

本轮多智能体并行推进：

- Agent runtime：`agent_runtime_streaming.py` 从 façade 升级为 ACTIVE_RUNS/SSE 第一阶段 owner，接管 `ActiveRunState`、`ACTIVE_RUNS`、profile/session key、progress/event append、snapshot 基础逻辑和 active stream replay/heartbeat；`agent_chat_runtime_impl.py` 保留 `get_active_run_snapshot` / `stream_active_run_events` 薄 wrapper，用于注入 diagnostic 与 heartbeat 配置，`stop_active_run`、`stream_chat_reply`、`_collect_stream_run` 和普通 chat/history/attachments/memory/dedupe 顺序仍留在 impl。
- PDF parser：`select_markdown_result` 已补非 dict / 空 payload 防御，artifact orchestrator 测试新增 malformed payload、本地 Markdown 已存在、required markdown 空 payload和 quality/markdown/backfill 日志顺序边界；未碰 MinerU submit/poll、Flask response、DB schema、queue claim/recover 和 `_ensure_*`。
- Frontend：`styleSelectorSmoke.test.ts` 新增 CSS rule body 解析和 `DOCUMENT_CSS` mobile/overflow smoke，覆盖移动端 preview 单列、source pane 分隔线、segment/toggle/task toolbar 响应式、批量按钮 tap target、workbench/pane `min-width: 0` 和 Markdown/table/relation flow 横向 overflow guard；未改 JSX/CSS 注入和 refs/scroll owner。
- CI / 文档：Phase 8 新增红灯 owner 迁移准入门禁，明确 Agent runtime、PDF parser、Web Node unit / frontend check，以及通用 `git diff --check` 失败门禁和 `git status --short` 收尾 review 输出。
- 本轮验证：`apps/api` active-runs + loops 70 passed，`apps/api` `tests/test_agent_runtime_*.py` 204 passed，`apps/pdf-parser` artifact/lifecycle 18 passed，`apps/pdf-parser` 全量 284 passed，`apps/web` Document/CSS Node 聚焦 19 passed，`apps/web` `npm run check:frontend` 通过，`git diff --check` 通过。
- 下一步建议：Agent runtime streaming owner 已完成第一阶段迁移；随后 stop owner 已完成，若继续 Agent runtime，应只推进 `_collect_stream_run` 的极小切片，并保持普通 chat/history/attachments/memory/dedupe 顺序不动。PDF parser 和 Frontend 当前只建议做维护尾项。

本轮多智能体并行继续推进：

- Agent runtime：`stop_active_run` owner 已下沉到 `agent_runtime_streaming.py`，`agent_chat_runtime_impl.py` 只保留薄 wrapper 注入 `stop_run` 和消息常量，继续保持 `runtime.stop_run` monkeypatch 语义；新增 idempotent stop、alias stop 传 canonical profile、Hermes 404 orphan cleanup 后 active stream drain，以及 `services.agent_runtime_streaming.stop_active_run(profile, session_id)` 直接调用兼容护栏。仍未迁移 `_collect_stream_run`、`stream_chat_reply`、普通 chat/history/attachments/memory/dedupe/build-run-input 顺序。
- PDF parser：`pdf_source_viewer.py` 补 source-view 容错，非数字 `focus_table` 改为忽略、`report.table_index` 非 list 时安全跳过、`content_list` 缺失/非 list 时仍保留 report 中对应页的 `page_tables` fallback；测试覆盖 loader wrapper 的 missing / 非 list / invalid JSON content 与 report fallback。仍未碰 MinerU submit/poll、Flask response、DB schema、queue claim/recover、artifact orchestrator owner 和 `_ensure_*` 编排。
- Frontend：`styleSelectorSmoke.test.ts` 继续补 `PDF_CSS` mobile smoke，覆盖下载搜索、下载项、workbench、page stage、Markdown actions、dense table 横向滚动、任务 action tap target 和移动端不撑宽边界；未改 `DocumentResultWorkbench.tsx`、view model、resource opener、CSS 注入、refs/scroll 或 JSX 结构。
- 本轮验证：`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_active_runs.py tests/test_agent_chat_runtime_loops.py -q` 通过，73 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_*.py -q` 通过，207 passed；`cd apps/api && .venv/bin/python -m py_compile services/agent_runtime_streaming.py services/agent_chat_runtime_impl.py services/agent_runtime_sessions.py` 通过；`cd apps/pdf-parser && PYTHONPATH=. python3 -m pytest tests/test_pdf_source_viewer.py tests/test_pdf_parser_source_service.py -q` 通过，33 passed；`cd apps/pdf-parser && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q` 通过，291 passed；`cd apps/web && node --test src/components/document-parser/styleSelectorSmoke.test.ts src/components/document-parser/documentResultWorkbenchDerivations.test.ts src/components/document-parser/documentResultViewModel.test.ts src/components/document-parser/documentResourceOpener.test.ts` 通过，21 passed；`cd apps/web && npm run check:frontend` 通过；`git diff --check` 通过。
- 下一步建议：Agent runtime stop owner 已完成，默认下一轮只推进 `_collect_stream_run` 极小切片；若不碰 Agent runtime，则只做 PDF `_ensure_*` 前置测试或前端响应式 smoke 维护，不再扩大 owner 迁移面。

本轮多智能体并行再推进：

- Agent runtime：`_collect_stream_run` 主循环继续留在 `agent_chat_runtime_impl.py`，但 terminal state owner 再收口一层到 `agent_runtime_streaming.py`：新增 `_append_completed_active_run`、`_append_user_stopped_active_run`、`_clear_active_run`，由 streaming owner 统一负责 completed/stopped terminal event 和 ACTIVE_RUNS 清理；新增 helper 直接测试和 fake `stream_run` 接线测试，覆盖成功 `progress(completed) -> done`、用户 stop `replace -> error`、`ACTIVE_RUNS` 清理和后台保存语义。未迁移 Hermes `stream_run` 调用、`stop_run` 自动停止、tool/reasoning/delta 主循环、evidence normalization、history/dedupe/save 或 `done_payload_factory`。
- PDF parser：补 `ensure_pdf_page_image` 非法页码前置护栏测试，覆盖 `"not-a-number"`、`"0"`、`0`、`-1`，断言不调用 `pdftoppm`、不创建任务结果目录、不触碰缺失 PDF；仍未碰 `pdf_parser_app_impl.py`、MinerU submit/poll、Flask response、DB schema、queue claim/recover 和 artifact orchestrator owner。
- Frontend：`styleSelectorSmoke.test.ts` 新增 `selectorsForContext` 与 `DOCUMENT_CSS` 窄视口 selector inventory 测试，显式锁定 `@media (max-width: 720px)` 下的文档工作台 selector 清单；仍未改组件、view model、resource opener、CSS 注入、refs/scroll 或 JSX。
- 本轮验证：`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_active_runs.py tests/test_agent_chat_runtime_loops.py -q` 通过，77 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_*.py -q` 通过，211 passed；`cd apps/api && .venv/bin/python -m py_compile services/agent_runtime_streaming.py services/agent_chat_runtime_impl.py services/agent_runtime_sessions.py` 通过；`cd apps/pdf-parser && PYTHONPATH=. python3 -m pytest tests/test_pdf_source_viewer.py tests/test_pdf_parser_source_service.py -q` 通过，37 passed；`cd apps/pdf-parser && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q` 通过，295 passed；`cd apps/web && node --test src/components/document-parser/styleSelectorSmoke.test.ts src/components/document-parser/documentResultWorkbenchDerivations.test.ts src/components/document-parser/documentResultViewModel.test.ts src/components/document-parser/documentResourceOpener.test.ts` 通过，22 passed；`cd apps/web && npm run check:frontend` 通过；`git diff --check` 通过。
- 当时下一步建议：`_collect_stream_run` 只完成 terminal helper 收口，主循环和 `stream_chat_reply` 仍不迁；cancel/timeout/tool-loop 接线矩阵已在下一轮完成，当前建议见下方收口记录。

本轮多智能体并行收口推进：

- Agent runtime：已补 `_collect_stream_run` cancel / idle timeout / HTTP timeout / repeated tool-call / consecutive tool-error 接线矩阵，覆盖事件顺序、`runtime.stop_run` monkeypatch、history save、ACTIVE_RUNS 清理和当前 timeout 契约。生产主循环未迁移，Hermes `stream_run` 调用、tool/reasoning/delta 主分支、evidence normalization、history/dedupe/save、`done_payload_factory` 和 `stream_chat_reply` 仍留在 `agent_chat_runtime_impl.py`。
- 关键契约：timeout 当前会调用 `stop_run` 并写入 timeout delta/history，但仍进入 completed/done 终态；tool-loop/tool-error UI 显示具体停止原因，history 保存通过 `_failed_run_reply_for_history` 压缩为 `OUTPUT_LOOP_STOP_MESSAGE`。这两个行为已被测试锁住，后续若要改变语义必须单独设计。
- 本轮验证：`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_active_runs.py -q` 通过，25 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_active_runs.py tests/test_agent_chat_runtime_loops.py -q` 通过，82 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_*.py -q` 通过，216 passed；`cd apps/api && .venv/bin/python -m py_compile services/agent_chat_runtime_impl.py services/agent_runtime_sessions.py services/agent_runtime_streaming.py tests/test_agent_runtime_active_runs.py` 通过；`cd apps/pdf-parser && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q tests/test_pdf_parser_source_service.py tests/test_pdf_source_viewer.py tests/test_pdf_parser_artifact_orchestrator_service.py` 通过，45 passed；`cd apps/pdf-parser && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q` 通过，295 passed；`cd apps/web && node --test src/components/document-parser/styleSelectorSmoke.test.ts src/components/document-parser/documentResultWorkbenchDerivations.test.ts src/components/document-parser/documentResultViewModel.test.ts src/components/document-parser/documentResourceOpener.test.ts` 通过，22 passed；`cd apps/web && npm run check:frontend` 通过。
- 当时下一步建议：`_collect_stream_run` terminal helper 与 cancel/timeout/tool-loop 接线矩阵均已完成；reasoning 极小事件 helper 已在后续完成，当前建议见下方收口记录。

本轮 Agent runtime 极小事件 helper 抽取：

- Agent runtime：`_collect_stream_run` 的 `reasoning` 单分支已抽为 `agent_runtime_streaming._append_reasoning_active_run`，streaming owner 统一负责 reasoning event 与 reasoning progress；`agent_chat_runtime_impl.py` 只保留 `await _append_reasoning_active_run(state, ev.text)` 接线。该 helper 不修改 `full_reply`、`failed`、`loop_detected`、`idle_timed_out`，不调用 `stop_run`，不触碰 history/attachments/memory/dedupe/build-run-input。
- 覆盖：新增直接 helper 测试，锁定 `reasoning -> progress` 顺序、payload、`state.content` 不变和 `state.status == "running"`；新增 fake `stream_run` 接线测试，锁定 reasoning 事件在 delta 前、reasoning 不进入 assistant history。
- 本轮验证：`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_active_runs.py -q` 通过，27 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_active_runs.py tests/test_agent_chat_runtime_loops.py -q` 通过，84 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_*.py -q` 通过，218 passed；`cd apps/api && .venv/bin/python -m py_compile services/agent_chat_runtime_impl.py services/agent_runtime_sessions.py services/agent_runtime_streaming.py tests/test_agent_runtime_active_runs.py` 通过；`cd apps/pdf-parser && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q` 通过，295 passed；`cd apps/web && node --test src/components/document-parser/styleSelectorSmoke.test.ts src/components/document-parser/documentResultWorkbenchDerivations.test.ts src/components/document-parser/documentResultViewModel.test.ts src/components/document-parser/documentResourceOpener.test.ts` 通过，22 passed；`cd apps/web && npm run check:frontend` 通过。
- 下一步建议：Agent runtime 当前 owner 迁移线可以停止，默认进入提交清理与 CI 文档固化；若继续拆 runtime，必须另开独立设计窗口，不把 `stream_chat_reply`、ordinary chat、history、attachments、memory、dedupe 或 build-run-input 与维护尾项混批。

本轮 CI / 文档门禁固化：

- 新增 `scripts/check_owner_migration.sh`，聚合 Agent runtime streaming owner、PDF parser source/artifact、Web Node unit、frontend check、`git diff --check` 失败门禁和 `git status --short` 收尾 review 输出；该脚本用于当前红灯 owner 收口验证，不替代 `scripts/check_all.sh` 的基础全量检查。
- README 的“开发验证”已改为“合并前基础门禁”，前端基线统一为 `npm run check:frontend`；`scripts/README.md` 已登记红灯 owner 收口门禁入口。
- Phase 8 明确：基础合并门禁以 README 为准，红灯 owner 命令只用于对应模块变更的聚焦验证，可用 `scripts/check_owner_migration.sh` 聚合执行。
- 本轮验证：`bash -n scripts/check_owner_migration.sh` 通过；`scripts/check_owner_migration.sh` 通过，其中 API active run + loops 84 passed，API runtime focused 218 passed，PDF parser source/artifact 45 passed，PDF parser full 295 passed，Web Document node 22 passed，`npm run check:frontend` 通过，`git diff --check` 通过。

### 0.6 2026-07-02 Web 单测门禁与后续工作量更新

本轮在后台智能体并行复核后，继续选择低风险门禁治理，不再扩大红灯 owner 迁移面。结论：当前红灯 owner 主线已经进入“阶段完成 + 维护尾项”状态，下一阶段优先做门禁一致性、文档同步和独立设计窗口，而不是继续混批拆状态 owner。

本轮完成：

- Web Node 单测门禁从手写 4 个 Document parser 测试文件升级为自动发现 `apps/web/src/**/*.test.ts`；新增 Node ESM alias loader，支持测试中直接解析 Vite 风格 `@/` 别名。
- `apps/web` 当前 10 个 `.test.ts` 全部纳入 `npm run test:unit`，验证结果为 44 passed、0 failed。
- `scripts/check_owner_migration.sh` 的 Web 步骤已改为 `Web node unit gates`，聚合门禁现在覆盖 Web Node unit、`npm run check:frontend`、API runtime、PDF parser 和通用提交前检查。
- `README.md`、`apps/web/README.md` 和 `scripts/README.md` 已同步 Web unit gate 入口，避免后续只跑 lint/build 而漏掉 Node 单测。

本轮验证：

```bash
cd apps/web && npm run test:unit                                   # 44 passed
scripts/check_owner_migration.sh                                  # API 84/218, PDF 52/302, Web unit 44, frontend check passed
```

后续任务与工作量：

| 优先级 | 任务 | 范围 | 工作量 | 风险控制 / 门禁 |
| --- | --- | --- | --- | --- |
| P0 | 本轮提交清理 | Web test gate、README、脚本标签、方案文档 | S，约 0.25 天 | `scripts/check_owner_migration.sh`、`git diff --check`、`git status --short` |
| P0 | 已完成：对齐 `scripts/check_all.sh` 与 README 基础门禁 | 已增加 `apps/document-parser`、`services/market-report-rules`、`apps/web npm run test:unit` 和 `npm run check:frontend` | S，本轮完成 | 语法检查：`bash -n scripts/check_all.sh`；全量门禁：`scripts/check_all.sh` |
| P1 | PDF parser 维护尾项 | source-view payload 脏数据容错已补；后续仅补 `_ensure_*` 前置测试或回归触发的 source/artifact 负路径，不迁 MinerU lifecycle / Flask response / DB schema | S，约 0-0.25 天，按回归触发 | `apps/pdf-parser` 聚焦测试 + 全量 301 tests |
| P1 | Frontend 维护尾项 | 仅补响应式 smoke、selector inventory 或纯 helper 边界；不迁 refs、selection、scroll、CSS 注入 | S，约 0-0.5 天，按回归触发 | `npm run test:unit`、`npm run check:frontend`，必要时 Playwright 聚焦用例 |
| P2 | Agent runtime 新 owner 设计窗口 | `stream_chat_reply`、sessions/history/memory/dedupe/build-run-input | M-L，约 1-2 天 | 先写设计/回滚矩阵，再选 1 个 owner；必须跑 API runtime focused suite |
| P2 | PDF parser 新 owner 设计窗口 | MinerU lifecycle、`_ensure_*` 编排、Flask response 或任务状态写顺序 | L，约 1.5-2.5 天 | 单独窗口，不和 Agent/Web owner 同批；先补状态机矩阵 |
| P2 | 前端运行时 CSS 字符串迁移 | `DOCUMENT_CSS` / `PDF_CSS` 注入机制与样式模块化 | L，约 1-3 天 | 需要桌面/移动 Playwright + 截图或视觉 smoke；不与业务状态迁移同批 |

状态同步：

- `F-004`、`P-001`、`P-002`、`A-001`、`A-002` 均改为阶段完成，后续只保留维护尾项或单独设计窗口。
- 红灯 owner 收口脚本已成为当前主线的聚合验证入口，但仍不替代 README 的基础合并门禁。
- `scripts/check_all.sh` 已对齐 README 基础门禁；下一轮若继续治理，优先验证全量执行耗时和 CI 可用性。

本轮追加维护：

- Web Node unit runner 已从 shell `find` 参数拼接改为 `scripts/run-node-unit-tests.mjs`，由 Node 递归收集 `src/**/*.test.ts`，无测试文件时显式失败，避免文件名和 shell 展开差异造成门禁漂移。
- Node test alias loader 已统一负责 `@/` 运行时别名和 `apps/web/src` 内 extensionless 相对导入；4 个 Document parser 测试文件移除本地 `registerHooks` 样板。
- 新增 `nodeTestAliasLoaderSmoke.test.ts`，锁定 runtime alias import 与 extensionless relative import 行为；Web Node unit 当前覆盖 10 个测试文件、44 个子测试。

### 0.7 2026-07-02 PDF source-view payload 容错收口

本轮在后台智能体只读复核后，继续选择低风险维护尾项，不扩大 MinerU lifecycle、Flask response、DB schema、queue claim、前端 refs/selection/scroll 或 Agent runtime 新 owner。结论：PDF source-view 当前只需收紧坏 artifact 的 payload 形态，避免上游脏数据把前端 source-view 视图打断。

本轮完成：

- `pdf_source_viewer.py` 新增 bbox 与文本列表规范化 helper：标量 / dict / 非四元组 bbox 不再触发 `len()` 或索引异常，只作为不可匹配 bbox 处理；caption、footnote、list items、matched financial names 等字段统一输出为字符串数组。
- `page_content_payload_from_content_list` 保持既有 source_id / bbox / report fallback 匹配语义，额外覆盖标量 bbox 无法匹配 report table 时回退为普通 table payload。
- `pdf_parser_source_service.page_bbox_extent` wrapper 补 loader 负路径测试，确认只读取 `content_list.json`，坏 JSON / 缺失 artifact 返回 `None`。

本轮验证：

```bash
cd apps/pdf-parser && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q tests/test_pdf_source_viewer.py tests/test_pdf_parser_source_service.py  # 43 passed
python3 -m py_compile apps/pdf-parser/pdf_source_viewer.py apps/pdf-parser/pdf_parser_source_service.py
scripts/check_owner_migration.sh  # API 84/218, PDF source/artifact 52, PDF full 302, Web unit 44, frontend check passed
```

### 0.8 2026-07-02 Web Document quality tab smoke 收口

本轮继续按维护尾项推进，只补 Playwright smoke，不改 Document workbench 组件、refs / selection / scroll 状态，也不迁 `DOCUMENT_CSS` / `PDF_CSS` 运行时注入字符串。后台智能体复核结论：现有 `document-result-preview.spec.ts` 已 mock `quality_report.json`，质量页组件有稳定 `.doc-quality-list` / `.doc-data-row` 结构，适合做低风险断言。

本轮完成：

- `document-result-preview.spec.ts` 的桌面结果工作台 smoke 增加“质量”tab 断言，覆盖总体状态、页数、块数、表格数、图片数和无 warning 状态。
- 断言复用现有 fixture 与 mock API，不新增产品代码，不触碰 Document focus controller、view model、resource opener 或样式注入。

本轮验证：

```bash
cd apps/web && npm run e2e -- e2e/tests/document-result-preview.spec.ts  # 6 passed
cd apps/web && npm run test:unit                                         # 44 passed
cd apps/web && npm run check:frontend                                    # lint/build passed
```

### 0.9 2026-07-02 Web Document mobile quality select smoke 收口

本轮继续保持 test-only 策略，在 0.8 桌面质量 tab smoke 基础上补齐移动端 select 路径。改动仍只落在 Playwright 用例，不改 Document workbench 组件、不迁 refs / selection / scroll，也不触碰 CSS 注入机制。

本轮完成：

- `document-result-preview.spec.ts` 的移动端 select 用例新增 `quality` 标签切换断言，覆盖移动端专用 `select[aria-label="切换结果标签"]` 能进入质量页。
- 断言质量 pane 可见并显示页数，同时确认切回预览后页码仍保持在 p2，避免 tab 切换污染 active page。

本轮验证：

```bash
cd apps/web && npm run e2e -- e2e/tests/document-result-preview.spec.ts  # 6 passed
cd apps/web && npm run test:unit                                         # 44 passed
cd apps/web && npm run check:frontend                                    # lint/build passed
```

### 0.10 2026-07-02 PDF page image render path test 收口

本轮回到 PDF parser 维护尾项，只补 `_ensure_*` 前置测试，不迁 MinerU lifecycle、Flask response、DB schema、任务状态写顺序或 queue owner。改动仅覆盖 `pdf_parser_source_service.ensure_pdf_page_image` 的 pdftoppm 成功渲染与缓存落点行为。

本轮完成：

- `test_pdf_parser_source_service.py` 新增 monkeypatch 测试，模拟 `pdftoppm` 生成 `page_0003-3.png`，断言 service 会移动到标准缓存路径 `pdf_pages/page_0003.png`。
- 同时锁定 `pdftoppm` 的页码参数、输入 PDF、输出 prefix、`check/stdout/stderr/timeout` 参数，补齐此前仅覆盖缓存命中、非法页码和缺原 PDF 的空白。

本轮验证：

```bash
cd apps/pdf-parser && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q tests/test_pdf_parser_source_service.py  # 18 passed
cd apps/pdf-parser && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q tests/test_pdf_source_viewer.py tests/test_pdf_parser_source_service.py tests/test_pdf_parser_artifact_orchestrator_service.py  # 52 passed
```

当前剩余工作量重估：

- Frontend Document / F-004：主线拆分完成，桌面与移动端 quality tab smoke 已补，剩余 0 个计划内维护轮次，约 0 天。默认停止新增；若发现回归，只补响应式 smoke、selector 清单或少量纯 helper 边界；不迁 refs、selection、scroll、CSS 注入和 JSX 主结构。
- PDF parser：红灯试点已完成 queue claim/recover、artifact orchestrator、malformed payload 防御、source-view loader/payload 容错、坏 bbox 防御和 page image render path 前置测试；当前 source/artifact 聚焦门禁为 52 passed，全量基线为 302 passed。剩余 0 个计划内维护轮次，约 0 天；默认停止新增，只在回归触发时补 source/artifact 负路径。不碰 MinerU submit/poll、Flask response、DB schema、任务状态写顺序。
- Agent runtime：active SSE owner、stop owner、`_collect_stream_run` terminal helper、cancel/timeout/tool-loop 接线矩阵和 reasoning 单分支 helper 已完成，剩余 0 个计划内维护轮次，约 0 天；默认停止 owner 迁移。`stream_chat_reply`、sessions/history/attachments/memory/dedupe/build-run-input 必须另开设计窗口。
- Repo / CI / 文档：红灯 owner 收口脚本、README/Phase 8 门禁口径和当前基线数字已固化，剩余 0 个计划内维护轮次，约 0 天；后续只需按轮次记录验证结果并保证 ignored runtime/cache/build 不进索引。
- Repo / CI / 文档补充：`scripts/README.md` 已明确 `git diff --check` 是失败门禁，`git status --short` 仅作为收尾 review 输出；Web 步骤口径同步为 Web Node unit + `npm run check:frontend`。

下一轮推荐任务池：

1. 已完成：Agent runtime `_collect_stream_run` cancel/timeout/tool-loop 接线矩阵，已确认事件顺序、`stop_run` monkeypatch、history save 和 ACTIVE_RUNS 清理。
2. 已完成：Agent runtime `_collect_stream_run` reasoning 极小事件 helper；Hermes stream 调用、ordinary chat、history、attachments、memory、dedupe、build-run-input 仍未迁移。
3. PDF / Frontend 维护尾项：默认停止新增，除非发现回归；不再扩大 artifact orchestrator / MinerU lifecycle / Document workbench 状态 owner。已补 PDF source-view payload 容错、PDF page image render path 测试、Web Document 桌面 quality tab smoke 和移动端 quality select smoke。
4. 提交与发布清理：当前主线已完成；后续只在实际变更后按 API / PDF parser / Web / Docs 分主题提交，提交前跑 `scripts/check_owner_migration.sh` 或对应聚焦门禁，确认 `apps/web/dist/`、runtime cache、pytest cache 和本地数据不进入索引。

推荐试点顺序：

1. 已完成：PDF parser queue claim / recover 最小试点。
2. 已完成：PDF parser artifact orchestrator 最小抽取。
3. 已完成：前端 DocumentResultWorkbench focus controller / view model / resource opener 最小 hook 拆分。
4. 已完成第一阶段：Agent runtime `ACTIVE_RUNS` / active SSE owner 试点，`agent_runtime_streaming.py` 已成为 active run state owner；`agent_chat_runtime_impl.py` 继续保留 collect stream run、stream chat reply 和普通/streaming 编排。
5. 已完成：Agent runtime stop owner 最小迁移，使用 wrapper / 小型依赖注入保持 `runtime.stop_run` monkeypatch 语义，并保留 streaming 直接调用兼容。
6. 已完成：Agent runtime `_collect_stream_run` terminal helper 小切片，streaming owner 接管 completed/stopped terminal event 与 ACTIVE_RUNS 清理。
7. 已完成：`_collect_stream_run` cancel/timeout/tool-loop 接线矩阵；最大风险仍是 stream 事件顺序、terminal drain、`stop_run` monkeypatch 和 circular import，后续提取 helper 时继续用 wrapper / 惰性出口 / 小型依赖注入控制风险。
8. 已完成：`_collect_stream_run` reasoning 极小事件 helper；下一优先是停止 Agent runtime owner 迁移，进入提交清理与 CI 文档固化。

红灯 owner 行为矩阵：

- Agent runtime 必补：SSE offset replay、heartbeat、disconnect return、`done/error` terminal status、stop run 用户停止事件、Hermes 404 orphan cleanup、existing active run join、profile alias key normalization、session default context 隔离。验证建议：`tests/test_agent_chat_runtime_loops.py`、`tests/test_hermes_client.py`、`tests/test_agent_runtime_context.py`、`tests/test_agent_runtime_memory.py`。
- PDF parser 必补：oldest queued claim、cancelled / has `mineru_task_id` 不 claim、compare-and-swap 防 double claim、stale submitting recover、submit 前持久化 submitting、submit 成功写 mineru id / pending / submitted_at、upload missing failed、upstream 404 failed、completed fetch artifacts before final completed、missing markdown -> completed_missing_artifact。验证建议：新增 queue/lifecycle tests，并继续跑 `apps/pdf-parser` 全量。
- Frontend 必补：新 task active page reset、markdown block focus 后 PDF overlay 与 markdown block 同步 focused class、prev/next page 后 select 与 preview page 同步、tab scroll button 改变 `scrollLeft`、resource open 失败显示 `.doc-error`、移动端 select tab 状态保持。验证建议：扩 `documentResultWorkbenchDerivations.test.ts` 和 `e2e/tests/document-result-preview.spec.ts`。

回滚策略：

- 所有试点保留旧 façade / wrapper 名称和签名；路由、页面 import 和外部调用不在首批同步迁移。
- PDF parser 可用 env flag 或 wrapper switch 灰度新 lifecycle service，默认可回旧 app path；不改 DB schema，不改 artifact 文件名和 schema version。
- Frontend hook 拆分只移动逻辑，不改 JSX/className；失败时把 hook 内容内联回 `DocumentResultWorkbench.tsx`。CSS 迁移另开窗口，`documentStyles.ts` 保留兼容导出。
- Agent runtime 不改 `services.agent_chat_runtime` 的兼容策略；如 streaming 试点失败，`agent_runtime_streaming.py` / `agent_runtime_sessions.py` 可恢复为 re-export，router 无需回滚。

工作量估算：

- PDF parser：主试点与维护前置测试已完成，剩余 0 天；若强行继续 MinerU lifecycle owner 迁移，需另开 1.5-2.5 天设计与验证窗口。
- Frontend Document：主试点与桌面/移动端 quality smoke 已完成，剩余 0 天；`DOCUMENT_CSS` / `PDF_CSS` 迁移不再作为当前优化主线，若要做需另开 1-3 天 UI 验证窗口。
- Agent runtime streaming：active run state owner、stop owner、collect terminal helper、cancel/timeout/tool-loop 接线矩阵与 reasoning 极小事件 helper 已完成，剩余 0 天；若继续 `stream_chat_reply` 或 sessions/history/memory owner，需另开 1-2 天设计窗口。
- CI / 文档门禁：已完成当前红灯 owner 收口脚本、README 口径和基础门禁基线固化，剩余 0 天。

下一步建议：PDF parser queue claim/recover、artifact/MinerU lifecycle 迁移前矩阵、PDF artifact orchestrator、前端 Document focus controller / view model / resource opener、Agent runtime ACTIVE_RUNS / active SSE owner 第一阶段、stop owner、`_collect_stream_run` terminal helper、cancel/timeout/tool-loop 接线矩阵、reasoning 极小事件 helper 和红灯 owner 收口门禁脚本均已完成。当前主线应停止新增维护切片；若继续 Agent runtime，需另开 `stream_chat_reply`、sessions/history/memory owner 设计窗口。

### 0.11 2026-07-02 下一阶段独立设计窗口准入

本轮按“继续加速但严控风险”的要求，调用后台智能体并行只读复核 Agent runtime、PDF parser 与整体风险边界，并同步抽样检查 `apps/api/services/agent_chat_runtime_impl.py`、`agent_runtime_streaming.py` 和当前 API runtime 测试。结论：维护主线继续保持关闭；下一阶段若继续开发，首选开启 Agent runtime `stream_chat_reply` 独立设计窗口，但第一步只锁普通 chat 与 streaming 共享 preflight 编排合同，先产出设计矩阵、护栏测试矩阵和回滚矩阵，不在本轮直接迁运行时 owner。

选择依据：

- Agent runtime 已完成 `ACTIVE_RUNS` / active SSE / stop / `_collect_stream_run` terminal、cancel、timeout、tool-loop、reasoning 的维护切片；`agent_runtime_streaming.py` 已成为 active run state 与事件 append owner。
- `agent_chat_runtime_impl.py` 仍约 5985 行，`_stream_chat_reply_impl` 同时承载 existing active run join、dedupe、catalog reply、completion guard、history/memory、PDF 附件等待、图片预分析、`create_run`、ACTIVE_RUNS 注册、done payload 和 SSE join。下一步风险集中且边界清晰，适合先做 owner 设计。
- PDF parser 的 MinerU lifecycle、Flask response、DB schema / task state 写顺序、`_ensure_*` 编排仍是高耦合状态机，后台复核建议只作为备选设计窗口；当前不直接实现迁移。
- Frontend Document 当前只剩回归触发型维护；`DOCUMENT_CSS` / `PDF_CSS` 迁移需要视觉 smoke 和截图验证，不应与 Agent/PDF 状态 owner 同批。

第一窗口准入任务：

| 顺序 | 任务 | 范围 | 工作量 | 交付物 / 门禁 |
| --- | --- | --- | --- | --- |
| A0 | 本轮完成：下一窗口准入固化 | 更新优化方案，明确候选窗口、禁止混批、工作量和门禁 | S，约 0.25 天 | 本节文档；`git diff --check` |
| A1 | Agent runtime preflight 行为矩阵 | 只读梳理 blocking 与 streaming 共享顺序：`load_history`、`ensure_local_memory_context`、PDF 附件等待与 metadata refresh、save user、`build_hermes_run_input(... allow_initialize=not history ...)`、Hermes session id、`create_run` | S-M，约 0.5 天 | 1 页设计说明；不得迁 owner；API runtime focused baseline 先跑一次 |
| A2 | Agent runtime 回滚与测试矩阵 | 明确 wrapper / façade 保留点、monkeypatch 语义、circular import 防线、失败恢复路径；列出新增/复用测试 | S，约 0.25-0.5 天 | 回滚矩阵 + 测试矩阵；`services.agent_chat_runtime` 兼容入口不变 |
| A3 | Agent runtime preflight 护栏测试 | 仅补 blocking / streaming 编排哨兵测试，锁定当前 user 保存前加载 history/memory、PDF metadata 刷新后保存、catalog/dedupe short-circuit 不创建 run | M，约 0.5-1 天 | API focused suite 通过；仍不迁 sessions/history/memory/dedupe/build-run-input |
| A4 | Agent runtime 最小实现试点 | 仅在 A1-A3 通过后选择 1 个 owner，建议先抽 `stream_chat_reply` 编排边界，不混入 sessions/history/memory/dedupe/build-run-input | M，约 0.5-1 天 | API focused suite 通过；新增测试只覆盖迁移 owner，不顺手改行为 |
| B1 | PDF parser MinerU lifecycle 设计窗口 | 状态机、MinerU submit/poll/result fetch、local markdown fast path、completed_missing_artifact、日志与持久化时机 | L，约 1.5-2.5 天 | 单独窗口；先写状态机矩阵，不碰 Flask response / DB schema |
| C1 | Frontend CSS 注入迁移窗口 | `DOCUMENT_CSS` / `PDF_CSS` 模块化和视觉回归 | L，约 1-3 天 | 单独 UI 验证窗口；桌面/移动 Playwright + 截图或视觉 smoke |

禁止混批清单：

- Agent runtime 窗口不同时迁 `stream_chat_reply`、ordinary chat、sessions、history、attachments、memory、dedupe、build-run-input 多个 owner。
- Agent runtime 窗口不改变 `services.agent_chat_runtime` 兼容入口、router import、`runtime.stop_run` / `runtime.create_run` 等测试 monkeypatch 语义。
- PDF parser 窗口不与 Agent/Web 同批；未完成状态机矩阵前不动 MinerU submit/poll、Flask response、DB schema、任务状态写顺序和 `_ensure_*` 编排。
- Frontend 窗口不与 Agent/PDF 状态 owner 同批；CSS 注入迁移前不改 Document workbench refs、selection、scroll 和 JSX 主结构。
- 任一窗口都不把 `apps/web/dist/`、pytest cache、runtime cache、本地 DB、下载文件或解析产物加入索引。

Agent runtime 第一窗口建议门禁：

```bash
(cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_active_runs.py tests/test_agent_chat_runtime_loops.py tests/test_agent_chat_runtime_attachments.py tests/test_agent_runtime_memory.py tests/test_agent_runtime_dedupe.py tests/test_agent_runtime_context.py -q)
(cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_active_runs.py tests/test_agent_chat_runtime_loops.py -q)
(cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_*.py -q)
(cd apps/api && .venv/bin/python -m py_compile services/agent_runtime_streaming.py services/agent_chat_runtime_impl.py services/agent_runtime_sessions.py)
scripts/check_owner_migration.sh
git diff --check
```

PDF parser 备选窗口门禁：

```bash
(cd apps/pdf-parser && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q tests/test_pdf_parser_mineru_lifecycle.py tests/test_pdf_parser_artifact_orchestrator_service.py tests/test_pdf_parser_task_lifecycle_service.py)
(cd apps/pdf-parser && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q)
git diff --check
```

当前排期建议：下一轮先做 A1/A2 设计产物，预计 0.75-1 天；随后做 A3 preflight 护栏测试，预计 0.5-1 天；只有设计矩阵、回滚矩阵、护栏测试和 API focused baseline 同时通过后，再进入 A4 最小实现。PDF parser 和 Frontend CSS 均保持备选，不在 Agent runtime 第一窗口中并行修改。

本轮验证：

```bash
git diff --check
bash -n scripts/check_owner_migration.sh
bash -n scripts/check_all.sh
scripts/check_owner_migration.sh  # API 84/218, PDF source/artifact 52, PDF full 302, Web unit 44, frontend check passed
```

### 0.12 2026-07-02 Agent runtime preflight 护栏测试收口

本轮按 0.11 的 A3 范围推进，只补普通 chat 与 streaming 共享 preflight 的哨兵测试，不迁 `stream_chat_reply`、sessions/history/memory/dedupe/build-run-input owner，也不改变 `agent_chat_runtime_impl.py` 的运行时路径。后台智能体只读复核结论一致：先锁顺序与短路分支，再考虑 A4 最小实现。

本轮完成：

- 新增 `test_agent_runtime_chat_preflight.py`，覆盖 blocking `collect_chat_reply` 的 preflight 顺序：`load_history`、`ensure_local_memory_context`、PDF 附件等待、metadata refresh、save user、image analysis、`build_hermes_run_input`、`create_run`、`collect_run_result`、save assistant、refresh memory。
- 覆盖 streaming `stream_chat_reply` 的 preflight 顺序：history/memory 在当前 user 保存前加载，PDF metadata refresh 在 save user 前完成，`create_run` 收到的 `conversation_history` 不包含当前 user，Hermes session id 仍为 `siq:{profile}:{session_id}`。
- 锁定 `allow_initialize=not history` 合同：blocking 空历史为 `True`，streaming 有历史为 `False`。
- 补 streaming duplicate 与 blocking duplicate 早退哨兵，断言 duplicate path 不进入 history/memory/save/refresh/PDF wait/image/create run。
- 补 streaming existing active run join 哨兵，断言 join path 不进入 catalog/dedupe/history/memory/save/refresh/create run，只复用已有 active event stream。
- `scripts/check_owner_migration.sh` 的 API 首段门禁改为显式包含 preflight 测试，compile gate 同步编译新测试文件；`scripts/README.md` 同步说明 Agent runtime preflight 护栏已纳入红灯 owner 收口门禁。

本轮验证：

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_chat_preflight.py -q  # 5 passed
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_active_runs.py tests/test_agent_chat_runtime_loops.py tests/test_agent_runtime_chat_preflight.py -q  # 89 passed
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_*.py -q  # 223 passed
cd apps/api && .venv/bin/python -m py_compile services/agent_runtime_streaming.py services/agent_chat_runtime_impl.py services/agent_runtime_sessions.py tests/test_agent_runtime_chat_preflight.py
scripts/check_owner_migration.sh  # API 89/223, PDF source/artifact 52, PDF full 302, Web unit 44, frontend check passed
git diff --check
```

当前剩余工作量重估：

- A3 preflight 护栏测试：已完成，剩余 0 天。
- A4 Agent runtime 最小实现试点：仍需单独窗口，约 0.5-1 天；只允许选择 1 个 owner，优先考虑 `stream_chat_reply` preflight 边界的薄封装或纯函数化，不迁 sessions/history/memory/dedupe/build-run-input。
- PDF parser / Frontend CSS：继续保持备选窗口，不与 Agent runtime A4 同批。

下一步建议：A3 护栏已在 `scripts/check_owner_migration.sh` 完整聚合中通过；若继续进入 A4，应把改动限制在“复用 preflight 合同 + 保持兼容入口与 monkeypatch 语义”范围内。

### 0.13 2026-07-02 Agent runtime preflight 最小实现收口

本轮按 A4 最小实现试点推进，但继续遵守 0.12 的风险边界：只把普通 chat 与 streaming 共享的请求准备和运行前上下文加载收成薄 helper，不迁 `stream_chat_reply` owner，不迁 sessions/history/memory/dedupe/build-run-input owner，不改 `services.agent_chat_runtime` facade / monkeypatch 语义，不移动 Hermes run 创建、ACTIVE_RUNS 状态或 `_collect_stream_run`。

后台智能体并行复核后的关键取舍：

- 将“请求 envelope”和“运行 preflight context”分开，避免把 dedupe/hash/display 文本误归为真正 run preflight。
- `ChatRequestEnvelope` / `_prepare_chat_request_envelope` 只负责附件归一化、近期附件复用、dedupe hash 和用户展示文本。
- `ChatRunPreflightContext` / `_load_chat_run_preflight_context` 只负责加载当前 user 保存前的 history 与 local memory，并暴露 `allow_initialize`；附件 metadata 的等待/刷新仍留在调用点，以保持 streaming PDF progress 事件顺序。
- duplicate、catalog、general assistant 和 existing active run join 的短路语义不进入 `ChatRunPreflightContext`；active run join 仍沿用既有顺序，在请求 envelope 后直接复用 active event stream。

本轮完成：

- `agent_chat_runtime_impl.py` 新增 `ChatRequestEnvelope`、`ChatRunPreflightContext`、`_prepare_chat_request_envelope`、`_load_chat_run_preflight_context`，并让 blocking / streaming 共用同一请求准备与 history/memory preflight 合同。
- blocking 路径保持 `wait_for_pdf_attachment_parses` 与 `_attachments_with_fresh_metadata` 在 save user 前执行；streaming 路径保持先发 PDF progress，再等待/刷新 metadata，再 save user。
- `build_hermes_run_input(... allow_initialize=...)` 统一读取 `preflight_context.allow_initialize`，`create_run` 仍接收当前 user 保存前的 `preflight_context.history`。
- `test_agent_runtime_chat_preflight.py` 从 5 个哨兵扩展到 7 个：新增 envelope patch-point 测试、run preflight context patch-point 测试，并补 duplicate / active join 不进入 run preflight context 的红线。

本轮验证：

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_chat_preflight.py -q  # 7 passed
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_active_runs.py tests/test_agent_chat_runtime_loops.py tests/test_agent_runtime_chat_preflight.py -q  # 91 passed
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_*.py -q  # 225 passed
cd apps/api && .venv/bin/python -m py_compile services/agent_chat_runtime_impl.py services/agent_runtime_sessions.py services/agent_runtime_streaming.py tests/test_agent_runtime_chat_preflight.py
git diff --check
scripts/check_owner_migration.sh  # API 91/225, PDF source/artifact 52, PDF full 302, Web unit 44, frontend check passed
```

当前剩余工作量重估：

| 优先级 | 任务 | 范围 | 工作量 | 风险控制 / 门禁 |
| --- | --- | --- | --- | --- |
| P0 | 停止当前 Agent runtime owner 迁移线 | A4 已完成为薄 helper 边界；后续不继续拆普通 chat / streaming 主流程 | 0 天 | 保持 `scripts/check_owner_migration.sh` 绿；仅记录回归 |
| P1 | Agent runtime 下一设计窗口 | 如确需继续，先单独设计 `stream_chat_reply` 编排边界或 sessions/history/memory owner；不得与 PDF/Web 同批 | S-M，约 0.5-1 天设计，不含实现 | 先出行为矩阵、短路矩阵、回滚矩阵；新增护栏测试后再实现 |
| P2 | Agent runtime 后续实现试点 | 只有 P1 通过后选择 1 个 owner；候选为 streaming 编排再薄化或 history/memory owner 抽取 | M，约 0.5-1 天 / owner | API focused suite、runtime 通配测试、`py_compile`、聚合门禁全部通过 |
| P2 | PDF parser 维护尾项 | 只补 `_ensure_*` 前置测试或 source/artifact payload 负路径；不碰 MinerU lifecycle / response owner | S，约 0-0.25 天，按回归触发 | `apps/pdf-parser` 聚焦 + full suite |
| P2 | Frontend 维护尾项 | 只做响应式 smoke、selector 清单或小行为回归；不迁 refs/scroll/CSS runtime owner | S，约 0-0.25 天，按回归触发 | `npm run test:unit` + `npm run check:frontend` |

下一步建议：当前 A4 已满足“复用 preflight 合同 + 保持兼容入口与 monkeypatch 语义”的目标，默认应停止本条 owner 迁移线并提交清理。若继续开发，应先开 P1 设计窗口，不直接在 `agent_chat_runtime_impl.py` 上继续抽主流程。

### 0.14 2026-07-02 PDF `_ensure_*` 与前端移动 smoke 维护护栏

本轮按 0.13 的风险边界执行：不继续 Agent runtime owner 实现，不动 PDF MinerU lifecycle / Flask response / DB schema / task state / `_ensure_*` 编排 owner，只补 PDF source-view 与 table relations 的 `_ensure_*` 前置测试，并补一个前端 PDF 任务列表移动端真实 DOM smoke。后台智能体对 Agent runtime 的只读复核结论是：0.13 已到适合停手的位置，若继续 runtime 必须先写 P1 设计矩阵，本轮不应再抽主流程。

本轮完成：

- 在 `test_pdf_parser_source_service.py` 新增 `test_ensure_pdf_page_image_rerenders_empty_cache`。
- 锁定 `ensure_pdf_page_image` 对已存在但 0 字节的 page image cache 不可直接返回，必须重新调用 `pdftoppm` 并用生成图片覆盖空缓存。
- 在 `test_table_relations.py` 新增 `test_ensure_table_relations_artifact_rewrites_stale_artifact`。
- 锁定 stale `table_relations.json` 的 schema/ruleset 不可被复用，必须重写为当前 `document_table_relations_v1` 与 `TABLE_RELATION_RULESET_VERSION`，旧 payload 哨兵字段不应保留。
- 在 `pdf-parsing-market-filter.spec.ts` 新增移动端 `390x844` smoke，复用现有 mock，锁定 A 股解析页只展示 CN/未标记任务，同时 `.pdf-task-item`、`.task-actions`、`.pdf-task-action` 不造成页面横向溢出，任务按钮高度不低于 `44px`。
- 未修改 `pdf_parser_source_service.py`、PDF app 实现、前端组件或 CSS；当前实现已经满足这些合同。

本轮验证：

```bash
cd apps/pdf-parser && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider tests/test_pdf_parser_source_service.py -q  # 19 passed
cd apps/pdf-parser && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider tests/test_table_relations.py -q  # 3 passed
cd apps/pdf-parser && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider tests/test_table_relations.py tests/test_pdf_parser_document_full_service.py -q  # 16 passed
cd apps/pdf-parser && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q  # 304 passed
cd apps/web && npm run e2e -- e2e/tests/pdf-parsing-market-filter.spec.ts  # 3 passed
cd apps/web && npm run test:unit  # 44 passed
cd apps/web && npm run check:frontend
git diff --check
scripts/check_owner_migration.sh  # API 91/225, PDF source/artifact 53, PDF full 304, Web unit 44, frontend check passed
```

当前剩余工作量重估：

| 优先级 | 任务 | 范围 | 工作量 | 风险控制 / 门禁 |
| --- | --- | --- | --- | --- |
| P0 | 停止 Agent runtime 当前 owner 迁移线 | 0.13 已完成薄 helper 边界；本轮只接受 P1 设计文档，不继续实现 | 0 天 | 继续保持 API runtime gates 绿 |
| P1 | Agent runtime 下一设计窗口 | 行为矩阵、短路矩阵、回滚矩阵；不改 runtime 代码 | S，约 0.5 天 | `git diff --check`；如加测试再跑 API focused suite |
| P2 | PDF parser 维护尾项 | source-view / artifact payload 负路径仅按回归触发补测；不迁 MinerU / response / task state owner | 0-0.25 天 | 聚焦测试 + PDF parser full suite |
| P2 | Frontend 维护尾项 | quality tab 与 PDF task mobile smoke 已有；仅按回归触发补 selector 或响应式 smoke | 0-0.25 天 | Web unit + frontend check，必要时 Playwright 聚焦 |

下一步建议：当前主线继续以“维护尾项按回归触发”为主；如果用户要求继续加速，优先补 Agent runtime P1 设计矩阵，而不是直接实现新的 owner 迁移。

### 0.15 2026-07-02 Agent runtime P1 设计窗口矩阵

本轮按 0.14 的建议只补 Agent runtime P1 设计窗口，不修改 API 运行时代码，不新增测试，不继续抽 `stream_chat_reply`、sessions/history/memory/dedupe/build-run-input owner。后台智能体并行只读复核正常路径、短路/兼容入口和回滚/验证边界；结论一致：0.13 的 preflight 薄边界已经是当前实现线的停止点，下一次若继续实现，必须先按本节矩阵选 1 个 owner，并在实现前补对应哨兵测试。

准入与禁区：

- 准入：只允许在下一实现窗口选择 1 个 owner，且先补测试；候选仅限“streaming 编排再薄化”或“history/memory owner 抽取”之一。
- 禁区：不得同轮迁 `stream_chat_reply` 主编排、ordinary chat、sessions/history/memory/dedupe/build-run-input、Hermes `create_run`、`_collect_stream_run` 主循环、`ACTIVE_RUNS`、SSE append、stop lifecycle、PDF parser 或 Web owner。
- 兼容入口：`services.agent_chat_runtime` 继续通过 `sys.modules[__name__] = _impl` 暴露 impl 模块对象；所有 monkeypatch 入口必须继续命中 `runtime.*` 全局符号。
- 注意：`agent_runtime_attachments.py`、`agent_runtime_memory.py`、`agent_runtime_dedupe.py` 当前只能视为纯 helper / façade 边界；真实 owner 接线仍在 `agent_chat_runtime_impl.py`，不得在无新增哨兵测试时迁移。

正常行为矩阵：

| 路径 | 必须保持的顺序 | 关键合同 |
| --- | --- | --- |
| Blocking 正常 chat | request envelope -> catalog/general/duplicate 检查 -> analysis completion guard -> `load_history` -> `ensure_local_memory_context` -> PDF wait -> metadata refresh -> save user -> image analysis -> `build_hermes_run_input` -> `create_run` -> register `ACTIVE_RUNS` -> `collect_run_result` -> clear active -> normalize/evidence -> save assistant -> refresh memory -> remember completed | `conversation_history` 是保存当前 user 前的 history；`allow_initialize == not history`；Hermes session id 为 `siq:{profile}:{session_id}`；保存 user 使用 refreshed attachments |
| Streaming 无 PDF | request envelope -> active join 检查 -> catalog/general/duplicate 检查 -> analysis completion guard -> `load_history` -> `ensure_local_memory_context` -> save user -> image analysis -> `build_hermes_run_input` -> `create_run` -> register `ACTIVE_RUNS` -> append `run` -> spawn `_collect_stream_run` -> replay active stream | 不发“正在等待 PDF 解析” progress；`create_run` history 不包含当前 user；`_collect_stream_run` 仍负责 Hermes stream、assistant save、remember、done/clear |
| Streaming 有 PDF | request envelope -> active join 检查 -> catalog/general/duplicate 检查 -> analysis completion guard -> `load_history` -> `ensure_local_memory_context` -> yield PDF wait progress -> PDF wait -> metadata refresh -> save user -> image analysis -> `create_run` -> append `run` -> spawn `_collect_stream_run` -> replay active stream | PDF progress 必须早于 wait/refresh；save user 必须晚于 metadata refresh；事件序至少保持 preflight 测试锁定的 `progress, run, progress, done` |
| General assistant | request envelope -> `_is_general_assistant_request` 命中 -> `_forget_recent_completed_run` -> 继续正常 Hermes run | 这不是终止短路；它只禁止旧 duplicate/evidence/公司目录污染，run input 走 `GENERAL_ASSISTANT_CONTEXT` |

短路与兼容矩阵：

| 分支 | 当前合同 | 禁止事项 / 已有护栏 |
| --- | --- | --- |
| Existing active run join | streaming 在 envelope 后立即检查 `has_active_run`，命中后只 replay `stream_active_run_events` | 不得进入 catalog、duplicate、preflight、save、create run；`test_stream_chat_reply_existing_active_run_join_skips_preflight_side_effects` 已锁 |
| Catalog reply | preflight 前命中 `build_wiki_catalog_reply` 后保存 user/assistant、refresh memory、remember completed，并返回/stream catalog reply | 不得创建 Hermes run；不得进入 `ChatRunPreflightContext` |
| Duplicate | 非 catalog 且非 general 时查 `_recent_duplicate_reply`；blocking 直接返回，streaming 发 `delta` + `done {deduped: true}` | 不得进入 history/memory/save/refresh/PDF wait/image/create/collect；blocking 与 streaming duplicate 测试已锁 |
| Stop active run | `agent_runtime_streaming.py` 是 stop owner；无 state、重复 stop、404 orphan 均有固定返回与事件行为 | 不迁 stop lifecycle；`runtime.stop_run` monkeypatch 必须继续注入 streaming stop |
| Facade / state identity | `runtime.ACTIVE_RUNS`、`agent_runtime_sessions.ACTIVE_RUNS`、`agent_runtime_streaming.ACTIVE_RUNS` 必须是同一个 dict；`ActiveRunState` 与 append/clear helper identity 保持一致 | 不改 `agent_chat_runtime.py` facade；不复制 ACTIVE_RUNS；active run identity 测试已锁 |

回滚矩阵：

| 失败信号 | 立即回滚范围 | 保留项 |
| --- | --- | --- |
| preflight 顺序失败、history 包含当前 user、`allow_initialize` 变化 | 回滚本轮 owner 接线到 0.13 inline/薄 helper 调用点 | 保留现有 `test_agent_runtime_chat_preflight.py` 护栏 |
| streaming PDF progress 顺序变化或无 PDF 时误发等待 progress | 只回滚 streaming 接线，不动 blocking | 保留 PDF wait/refresh 调用点在 streaming callsite 的设计 |
| duplicate/catalog/active join 进入新 owner 或产生副作用 | 回滚新 owner 的短路前置接线 | 保留 forbidden monkeypatch 哨兵 |
| attachments owner 试点失败：近期附件复用、附件-only 消息、PDF 独立 parse dir、metadata refresh 或 safe path/context 测试失败 | 回滚 `_attachment_*`、`load_recent_session_attachments`、`wait_for_pdf_attachment_parses` 等真实接线到 `agent_chat_runtime_impl.py` | `agent_runtime_attachments.py` 继续只做 helper / façade |
| history/local-memory owner 试点失败：profile/session 串线、保存当前 user 前读取旧 history 顺序破坏、`refresh_session_memory` 包含当前轮 | 回滚 `load_history`、`save_message`、`refresh_session_memory`、`ensure_local_memory_context` 接线到 `agent_chat_runtime_impl.py` | `agent_runtime_memory.py` 继续只保留纯 helper |
| dedupe owner 试点失败：duplicate 未在 preflight 前短路，或 active run join 仍创建新 Hermes run | 回滚 `_recent_duplicate_reply`、`_remember_completed_run`、message hash 接线到 `agent_chat_runtime_impl.py` | `agent_runtime_dedupe.py` 继续只保留 hash/progress signature 等纯函数 |
| `_collect_stream_run` 再拆分失败：cancel/idle/global timeout/tool-loop/reasoning 顺序、`stop_run` monkeypatch、history save 或 `ACTIVE_RUNS` 清理失败 | 回滚 Hermes `stream_run` 调用、tool/delta 主循环、evidence normalization、save/done payload 接线到 `agent_chat_runtime_impl.py` | 不回滚已稳定的 streaming event primitive |
| monkeypatch/facade identity 失败 | 立即恢复 `agent_chat_runtime.py` 的 `sys.modules[__name__] = _impl` 与 impl 全局符号调用 | 不保留任何绕过 `runtime.*` 的静态绑定 |
| stop/active stream drain/404 orphan 回归 | 回滚对 `agent_runtime_streaming.py` 或 stop 注入路径的任何改动 | 保留 active run owner 在 streaming 模块 |
| API focused 通过但聚合门禁失败 | 不提交实现；只提交设计或测试补充 | 先修门禁，再重新评估 owner |

测试与验证矩阵：

| 阶段 | 必跑命令 |
| --- | --- |
| 设计文档窗口 | `git diff --check` |
| API 实现前 baseline | `cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_chat_preflight.py -q`；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_active_runs.py tests/test_agent_chat_runtime_loops.py tests/test_agent_runtime_chat_preflight.py tests/test_agent_chat_runtime_attachments.py tests/test_agent_runtime_memory.py tests/test_agent_runtime_dedupe.py -q` |
| API 实现后 focused | `cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_*.py -q`；`cd apps/api && .venv/bin/python -m py_compile services/agent_chat_runtime_impl.py services/agent_runtime_sessions.py services/agent_runtime_streaming.py services/agent_runtime_attachments.py services/agent_runtime_memory.py services/agent_runtime_dedupe.py tests/test_agent_runtime_chat_preflight.py` |
| 全仓收口 | `scripts/check_owner_migration.sh`；`git status --short` |

后续工作量重估：

| 优先级 | 任务 | 范围 | 工作量 | 门禁 |
| --- | --- | --- | --- | --- |
| P0 | Agent runtime P1 设计窗口 | 本节已完成；后续只需按矩阵执行 | 0 天 | `git diff --check` |
| P1 | Agent runtime 下一实现试点 | 只选 1 个 owner，优先“streaming 编排再薄化”或“history/memory owner 抽取”；先补测试再实现 | M，约 0.5-1 天 | API focused + 聚合门禁 |
| P2 | PDF / Frontend 维护尾项 | 仅按回归触发补测试；不与 Agent runtime 实现同批 | 0-0.25 天 | 对应聚焦门禁 |

下一步建议：若继续开发，先为选定的 1 个 Agent runtime owner 补一条失败态哨兵测试，再做最小实现；如果无法明确 owner，就停止实现线，只做回归触发的维护尾项。

### 0.16 2026-07-02 Agent runtime streaming 启动薄化收口

本轮按 0.15 矩阵选择唯一 owner：“streaming 编排再薄化”。后台智能体并行复核候选 owner、monkeypatch / 兼容入口和门禁覆盖后，结论是 history/memory owner 仍会同时触碰 DB 顺序、profile/session 隔离和当前 user 是否污染 history，暂不进入实现；本轮只在 `agent_chat_runtime_impl.py` 同文件内抽出 `_start_streaming_chat_run`，将原本内联的 `ActiveRunState` 创建、`ACTIVE_RUNS` 注册、`run` 事件 append、`_collect_stream_run` task 启动收进一个 helper。

完成项：

- 新增 `test_start_streaming_chat_run_uses_runtime_patch_points`：锁定 `_start_streaming_chat_run` 必须继续通过 `runtime._append_state_event` 与 `runtime._collect_stream_run` 全局符号工作，确保 `services.agent_chat_runtime` facade 的 monkeypatch 入口仍命中 impl。
- 在 streaming duplicate、catalog、existing active join 三条短路路径补 forbid 护栏，禁止进入 `_start_streaming_chat_run`、preflight、history/memory、Hermes `create_run` 或 `_collect_stream_run`。
- 保持 `ACTIVE_RUNS` owner 不变，仍由 `agent_runtime_streaming.py` 暴露共享 dict；本轮不迁 SSE append、stop lifecycle、`_collect_stream_run` 主循环、history/memory/dedupe/build-run-input、PDF parser 或 Web owner。

行为边界：

| 路径 | 本轮保持的合同 |
| --- | --- |
| Streaming 正常路径 | `create_run` 后调用 `_start_streaming_chat_run`；helper 注册同一个 `ActiveRunState`、先 append `run`、再启动 `_collect_stream_run`；随后仍由 `stream_active_run_events` replay |
| Streaming PDF 路径 | PDF wait progress、wait、metadata refresh、save user 顺序不变；事件序仍由测试锁定为 `progress, run, progress, done` |
| Duplicate / catalog / active join | 均不得进入 `_start_streaming_chat_run`；duplicate 只发 `delta/done deduped`，catalog 只保存 user/assistant 并返回 catalog reply，active join 只 replay 既有事件 |

回滚边界：

| 失败信号 | 回滚范围 |
| --- | --- |
| streaming PDF 事件顺序变化、无 PDF 时误发 wait progress、run event 丢失或重复 | 只回滚 `_start_streaming_chat_run` 接线到 0.15 前内联代码 |
| `runtime._append_state_event` / `runtime._collect_stream_run` monkeypatch 失效 | 回滚 helper 内静态绑定，恢复通过 impl 全局符号调用 |
| duplicate / catalog / active join 进入新 helper | 回滚 helper 调用位置，保留短路 forbid 测试 |
| active state identity 变化或 stop lifecycle 回归 | 不迁 `ACTIVE_RUNS` / `agent_runtime_streaming.py`；只撤回本轮 helper |

本轮验证：

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_chat_preflight.py -q  # 9 passed
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_active_runs.py tests/test_agent_chat_runtime_loops.py tests/test_agent_runtime_chat_preflight.py tests/test_agent_chat_runtime_attachments.py tests/test_agent_runtime_memory.py tests/test_agent_runtime_dedupe.py -q  # 116 passed
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_active_runs.py tests/test_agent_chat_runtime_loops.py tests/test_agent_runtime_chat_preflight.py -q  # 92 passed
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_*.py -q  # 226 passed
cd apps/api && .venv/bin/python -m py_compile services/agent_chat_runtime_impl.py services/agent_runtime_sessions.py services/agent_runtime_streaming.py services/agent_runtime_attachments.py services/agent_runtime_memory.py services/agent_runtime_dedupe.py tests/test_agent_runtime_chat_preflight.py tests/test_agent_runtime_active_runs.py
git diff --check
scripts/check_owner_migration.sh  # API 93/227, PDF 53/304, Web unit 44, frontend check passed
```

后续工作量重估：

| 优先级 | 任务 | 范围 | 工作量 | 门禁 |
| --- | --- | --- | --- | --- |
| P0 | Agent runtime streaming 启动薄化 | 本节已完成；后续只接受回归修复 | 0 天 | 保持 preflight / active-run / 聚合门禁绿 |
| P1 | Agent runtime 下一 owner 选择 | 二选一：继续 `_collect_stream_run` 内部纯函数级薄化，或重新评估 history/memory owner；必须先补失败态哨兵测试 | M，约 0.5-1 天 | `tests/test_agent_runtime_chat_preflight.py` + `tests/test_agent_runtime_active_runs.py` + `tests/test_agent_runtime_*.py` |
| P1 | history/memory owner 设计再确认 | 只做设计或测试哨兵，暂不迁 DB 写读 owner；重点锁 profile/session、保存当前 user 前 history、`refresh_session_memory` 轮次边界 | S-M，约 0.5 天 | 新增哨兵测试先红后绿；API focused |
| P2 | PDF / Frontend 维护尾项 | 仅按回归触发补测试；不与 Agent runtime owner 迁移同批 | 0-0.25 天 | 对应聚焦门禁 + `scripts/check_owner_migration.sh` |

下一步建议：若继续加速，优先做 history/memory owner 的只读设计和失败态哨兵测试；如果要继续实现，则仍只选 1 个极窄 owner，并在同轮禁止迁 `stream_chat_reply` 主编排、Hermes `create_run`、`_collect_stream_run` 主循环和 `ACTIVE_RUNS` owner。

### 0.17 2026-07-02 Local-memory source selector 纯边界

本轮按 0.16 的风险边界继续推进 history/memory，但没有迁 `load_history`、`save_message`、`refresh_session_memory` 的 DB 查询/提交 owner，也没有改 blocking/streaming preflight 顺序。后台智能体复核后建议 history/memory DB owner 暂不实现；本轮只把 `refresh_session_memory` 中“哪些旧消息可以进入本地记忆摘要”的纯选择逻辑抽到 `agent_runtime_memory.select_local_memory_source_messages`，并顺手修正一个轮次边界：当 recent window 把 user/assistant 切成半轮时，不把只有 user、assistant 已落入 recent window 的半轮写进长期记忆。

完成项：

- 新增 `select_local_memory_source_messages(messages, recent_limit=...)`：只选择 recent window 之前的旧消息，并在边界处剔除尾部 dangling user，避免 local-memory summary 写入半轮问题。
- `refresh_session_memory` 改为委托该纯 helper；DB 查询、profile/session prefix gate、`ChatSessionMemory` upsert、commit 与 `load_local_memory_context` 均保持在 `agent_chat_runtime_impl.py`。
- 新增 memory 哨兵：纯 selector 排除 recent window 与半轮；`refresh_session_memory` 必须通过 selector；真实 SQLite 场景锁定 recent 边界不拆轮次、`last_message_id` 落在完整 assistant 回复上。

行为与风险边界：

| 路径 | 本轮保持 / 改进 |
| --- | --- |
| Local-memory source selection | 从 `messages[:-recent_limit]` 变为纯 helper；仍不读取 DB；新增 dangling user 剔除，避免半轮摘要 |
| `refresh_session_memory` | 查询当前 `session_id`、profile/session prefix gate、record upsert 和 commit 不迁移；只替换 source message 选择调用 |
| Chat preflight | `load_history -> ensure_local_memory_context -> save current user` 顺序不变；当前 user 仍不得进入 Hermes `conversation_history` |
| Shortcuts | duplicate、catalog、active join 的 history/memory forbid 护栏继续有效；本轮不改短路路径 |

回滚边界：

| 失败信号 | 回滚范围 |
| --- | --- |
| local-memory summary 缺失过多旧上下文 | 只回滚 `select_local_memory_source_messages` 的 dangling user 剔除策略，保留 helper 边界与测试再评估 |
| profile/session 串线、record upsert 异常、DB commit 行为变化 | 回滚 `refresh_session_memory` 到内联 `messages[:-recent_limit]`，不动 memory 纯 helper 测试 |
| preflight 顺序或 duplicate/catalog/active join 回归 | 不扩大修复范围；回滚本轮 helper 接线并保留 0.16/0.17 哨兵 |

本轮验证：

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_memory.py -q  # 10 passed
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_chat_preflight.py -q  # 9 passed
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_active_runs.py tests/test_agent_chat_runtime_loops.py tests/test_agent_runtime_chat_preflight.py tests/test_agent_chat_runtime_attachments.py tests/test_agent_runtime_memory.py tests/test_agent_runtime_dedupe.py -q  # 120 passed
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_*.py -q  # 230 passed
cd apps/api && .venv/bin/python -m py_compile services/agent_chat_runtime_impl.py services/agent_runtime_sessions.py services/agent_runtime_streaming.py services/agent_runtime_attachments.py services/agent_runtime_memory.py services/agent_runtime_dedupe.py tests/test_agent_runtime_chat_preflight.py tests/test_agent_runtime_memory.py tests/test_agent_runtime_active_runs.py
git diff --check
scripts/check_owner_migration.sh  # API 93/230, PDF 53/304, Web unit 44, frontend check passed
```

后续工作量重估：

| 优先级 | 任务 | 范围 | 工作量 | 门禁 |
| --- | --- | --- | --- | --- |
| P0 | Local-memory source selector | 本节已完成；只接受回归修正 | 0 天 | memory + preflight + 聚合门禁 |
| P1 | history/memory 隔离哨兵补强 | 优先补同 profile 不同 session 隔离、同 session 不同 profile record 不覆盖、`ensure_local_memory_context` prefix gate；仍不迁 DB owner | S，约 0.25-0.5 天 | `tests/test_agent_runtime_memory.py` + API focused |
| P1 | history/memory DB owner 设计 | 若要迁 owner，先写设计矩阵和失败态测试；不得与 streaming/attachments/dedupe 同轮迁移 | M，约 0.5-1 天 | 新增哨兵先红后绿 + `scripts/check_owner_migration.sh` |
| P2 | PDF / Frontend 维护尾项 | 继续按回归触发 | 0-0.25 天 | 对应聚焦门禁 |

下一步建议：继续补 history/memory 隔离哨兵即可，不急于迁 DB owner；如果要实现，仍选择一个纯函数级或同文件薄 helper，避免同时动 preflight、DB upsert 和 streaming lifecycle。

### 0.18 2026-07-02 History/memory 隔离哨兵补强

本轮继续沿 0.17 的 history/memory 方向推进，但仍不迁 `load_history`、`save_message`、`refresh_session_memory` 的 DB owner。后台智能体复核后确认当前实现不必改逻辑；本轮重点是把隔离边界补成可回归的哨兵：同 profile 不同 session、same session foreign profile record、以及 `ensure_local_memory_context` 的 profile/session prefix gate。为避免 local-memory summary 被 recent window 切成半轮，`select_local_memory_source_messages` 继续保持“按 recent window 之前的旧消息、并对尾部 dangling user 向后对齐到完整 turn”的纯函数边界。

完成项：

- 新增 `test_refresh_session_memory_isolates_same_profile_sessions`：验证 `refresh_session_memory` 只影响目标 session，且 `load_local_memory_context` 不会把另一个 session 的内容串进来。
- 新增 `test_refresh_session_memory_keeps_foreign_profile_record_isolated_for_same_session_id`：同一 session_id 下的 foreign profile 记录保持原样，assistant 记录单独创建/更新。
- 新增 `test_ensure_local_memory_context_refreshes_and_respects_profile_prefix`：对匹配的 `siq_assistant / siq-assistant-*` 返回 `<local-memory>` 并写入 record；对不匹配 prefix 返回 `None`，不创建 record。
- 保留前一轮 selector 纯边界：recent window 之前的旧消息仍由 `select_local_memory_source_messages` 产出，不迁 DB 查询和 commit。

行为与风险边界：

| 路径 | 本轮保持 / 改进 |
| --- | --- |
| same profile 不同 session | 仅目标 session 进入 summary 与 memory record；其余 session 不被触碰 |
| same session 不同 profile | 复合主键 `profile + session_id` 继续隔离，foreign record 不覆盖 |
| ensure prefix gate | `ensure_local_memory_context()` 只在匹配 profile/session prefix 时刷新并读出本地记忆 |
| selector 边界 | 纯 helper 继续防止 recent window 把 dangling user 写入 summary |

回滚边界：

| 失败信号 | 回滚范围 |
| --- | --- |
| 隔离测试失败但 DB 逻辑未变 | 回滚本轮测试哨兵；不改 `refresh_session_memory` / `ensure_local_memory_context` |
| profile/session 串线或 foreign record 覆盖 | 回滚 `refresh_session_memory` 的本轮接线与 selector helper，恢复到 0.17 版本 |
| prefix gate 行为变化 | 回滚 `ensure_local_memory_context` 测试扩展，保留已有 preflight/memory 回归护栏 |

本轮验证：

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_memory.py -q  # 13 passed
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_chat_preflight.py -q  # 9 passed
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_active_runs.py tests/test_agent_chat_runtime_loops.py tests/test_agent_runtime_chat_preflight.py tests/test_agent_chat_runtime_attachments.py tests/test_agent_runtime_memory.py tests/test_agent_runtime_dedupe.py -q  # 123 passed
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_*.py -q  # 233 passed
cd apps/api && .venv/bin/python -m py_compile services/agent_chat_runtime_impl.py services/agent_runtime_sessions.py services/agent_runtime_streaming.py services/agent_runtime_attachments.py services/agent_runtime_memory.py services/agent_runtime_dedupe.py tests/test_agent_runtime_chat_preflight.py tests/test_agent_runtime_memory.py tests/test_agent_runtime_active_runs.py
git diff --check
scripts/check_owner_migration.sh  # API / PDF / Web gates passed
```

后续工作量重估：

| 优先级 | 任务 | 范围 | 工作量 | 门禁 |
| --- | --- | --- | --- | --- |
| P0 | History/memory 隔离哨兵补强 | 本节已完成；后续只接受回归修正 | 0 天 | memory + preflight + 聚合门禁 |
| P1 | history/memory DB owner 设计 | 如果继续推进，先写 owner 矩阵和失败态测试；不得与 streaming/attachments/dedupe 同轮迁移 | M，约 0.5-1 天 | 新增哨兵先红后绿 + `scripts/check_owner_migration.sh` |
| P1 | 继续补隔离回归面 | 若再推进，优先补 `test_collect_chat_reply_passes_old_history_before_saving_current_user` 这类真实 DB 顺序护栏 | S，约 0.25 天 | `tests/test_agent_runtime_memory.py` + `tests/test_agent_runtime_chat_preflight.py` |
| P2 | PDF / Frontend 维护尾项 | 继续按回归触发 | 0-0.25 天 | 对应聚焦门禁 |

下一步建议：如果继续加速，优先补真实 DB 顺序护栏，而不是直接迁 history/memory DB owner；这条线目前已经足够稳定，适合只做回归触发的小修小补。

### 0.19 2026-07-02 真实 DB 顺序护栏补强

本轮把上一节建议里的“真实 DB 顺序护栏”落成了：新增 `test_collect_chat_reply_passes_old_history_before_saving_current_user`，用独立 SQLite 临时库预置一段旧对话，再通过真实 `load_history` / `save_message` 包一层轻量计数，确认 `collect_chat_reply()` 在保存当前 user 之前拿到的仍是旧历史，且后续 `create_run()` 看到的 conversation_history 不包含当前轮问题。

完成项：

- 新增真实 DB 顺序哨兵：`load_history` 读到的历史只包含旧轮次，`save_user` 发生在预加载之后。
- 保留真实 `load_history` / `save_message` 行为，只对调用顺序做记录，避免测试变成纯 mock 流程图。
- 顺带验证保存后的 DB 结果：当前 user 与 assistant reply 都落库，旧消息顺序保持不变。

行为与风险边界：

| 路径 | 本轮保持 / 改进 |
| --- | --- |
| preflight 读写顺序 | `load_history` 先于 `save_message(user)`，`create_run()` 只看到旧 history |
| DB 持久化 | 新 user / assistant 回复都会真实落库，便于后续回归核对 |
| 测试真实性 | 只对调用顺序做包裹，不替换核心读写逻辑 |

回滚边界：

| 失败信号 | 回滚范围 |
| --- | --- |
| 顺序护栏失败 | 仅回滚 `test_collect_chat_reply_passes_old_history_before_saving_current_user` |
| DB 真实读写行为变化 | 回滚该测试中的 wrapper 方式，恢复到纯 mock 顺序测试 |
| 旧历史被当前轮污染 | 先修 `collect_chat_reply` 的 preflight / save 顺序，再决定是否扩展 memory 侧哨兵 |

本轮验证：

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_chat_preflight.py -q  # 10 passed
```

后续工作量重估：

| 优先级 | 任务 | 范围 | 工作量 | 门禁 |
| --- | --- | --- | --- | --- |
| P0 | 继续保持 preflight / memory 顺序哨兵 | 仅回归修正，不主动改 owner 结构 | 0 天 | `tests/test_agent_runtime_chat_preflight.py` + `tests/test_agent_runtime_memory.py` |
| P1 | history/memory DB owner 设计 | 若继续推进，先写 owner 矩阵和失败态测试；不得与 streaming/attachments/dedupe 同轮迁移 | M，约 0.5-1 天 | 新增哨兵先红后绿 + `scripts/check_owner_migration.sh` |
| P1 | 拓展更多真实 DB 顺序护栏 | 可优先覆盖 stream / attachment 分支，但保持单个场景一条测试 | S，约 0.25 天 | 对应聚焦测试 + `git diff --check` |
| P2 | PDF / Frontend 维护尾项 | 继续按回归触发 | 0-0.25 天 | 对应聚焦门禁 |

下一步建议：继续做“少量真实 DB 顺序护栏 + 不迁 owner”的路线；只有当这类回归足够稳定、且能明确拆出 owner 矩阵时，再进入 history/memory DB owner 设计。

### 0.20 2026-07-02 attachment 真实 DB 顺序护栏补强

本轮继续沿“少量真实 DB 顺序护栏 + 不迁 owner”的路线推进，但把分支换到了 attachment：新增 `test_collect_chat_reply_image_attachment_passes_old_history_before_saving_current_user`，用独立 SQLite 临时库预置旧对话，再通过真实 `load_history` / `save_message` 包一层记录，确认 `collect_chat_reply()` 在保存当前 user 之前拿到的仍是旧历史，且当前 image attachment 真实写入 `attachments_json`。

完成项：

- 新增 attachment 真实 DB 顺序哨兵：`load_history` 读到的历史只包含旧轮次，`save_user` 仍发生在 preflight 之后。
- 保留真实 `load_history` / `save_message` 行为，只对调用顺序做记录，确保测试验证的是真实 SQLite 写入。
- 顺带验证保存后的 DB 结果：当前 user 与 assistant reply 都落库，当前 user 的 attachment 元数据也真实写入。

行为与风险边界：

| 路径 | 本轮保持 / 改进 |
| --- | --- |
| preflight 读写顺序 | `load_history` 先于 `save_message(user)`，`create_run()` 只看到旧 history |
| attachment 持久化 | 当前 user 的 `attachments_json` 真实落库，可回归检查 |
| 测试真实性 | 只包一层调用记录，不替换核心读写逻辑 |

回滚边界：

| 失败信号 | 回滚范围 |
| --- | --- |
| 顺序护栏失败 | 仅回滚 `test_collect_chat_reply_image_attachment_passes_old_history_before_saving_current_user` |
| attachment 写入行为变化 | 回滚该测试中的 wrapper，恢复到纯 mock 顺序测试 |
| 旧历史被当前轮污染 | 先修 `collect_chat_reply` 的 preflight / save 顺序，再决定是否扩展 streaming 侧哨兵 |

本轮验证：

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_agent_chat_runtime_attachments.py -q  # 16 passed
```

后续工作量重估：

| 优先级 | 任务 | 范围 | 工作量 | 门禁 |
| --- | --- | --- | --- | --- |
| P0 | 继续保持 preflight / attachment 顺序哨兵 | 仅回归修正，不主动改 owner 结构 | 0 天 | `tests/test_agent_runtime_chat_preflight.py` + `tests/test_agent_chat_runtime_attachments.py` |
| P1 | 拓展 streaming 真实 DB 顺序护栏 | 继续把 `test_stream_chat_reply_preflight_refreshes_pdf_metadata_before_saving_user` 升级成真实 SQLite wrapper | S，约 0.25-0.5 天 | `tests/test_agent_runtime_chat_preflight.py` + `git diff --check` |
| P1 | history/memory DB owner 设计 | 若继续推进，先写 owner 矩阵和失败态测试；不得与 streaming/attachments/dedupe 同轮迁移 | M，约 0.5-1 天 | 新增哨兵先红后绿 + `scripts/check_owner_migration.sh` |
| P2 | PDF / Frontend 维护尾项 | 继续按回归触发 | 0-0.25 天 | 对应聚焦门禁 |

下一步建议：优先补 streaming 的真实 DB 顺序护栏，让 chat preflight 的两条主分支都落到真实 SQLite 验证上；等这两条稳定了，再考虑 history/memory DB owner 设计。

### 0.21 2026-07-02 streaming 真实 DB 顺序护栏补强

本轮按 0.20 的建议推进，把 `test_stream_chat_reply_preflight_refreshes_pdf_metadata_before_saving_user` 从纯 mock 读写升级为真实 SQLite wrapper：`load_history` / `save_message` 只包一层调用记录，底层仍走真实实现；PDF metadata、Hermes run、stream collect 和 local memory 继续 mock，避免把测试扩大成完整 streaming 集成链。

完成项：

- streaming 分支使用独立 SQLite 临时库预置旧对话，验证 `create_run()` 收到的 `conversation_history` 仍是 preflight 捕获的旧 history。
- `save_message(user)` 真实写入 DB，验证当前 streaming user message 与刷新后的 PDF attachment metadata 写入 `attachments_json`。
- 断言从完整调用列表改为关键偏序：`load_history < attachments_with_fresh_metadata < save_user < analyze_images < build_input < create_run`，避开 `save_message()` 内部二次刷新 metadata 带来的脆弱顺序。

行为与风险边界：

| 路径 | 本轮保持 / 改进 |
| --- | --- |
| streaming preflight 顺序 | `load_history` 先于 `save_message(user)`，`create_run()` 只看到旧 history |
| PDF attachment 持久化 | 刷新后的 `markdown_path` 写入当前 user 的 `attachments_json` |
| streaming 生命周期 | `_collect_stream_run` 仍 fake，只产生 terminal done；测试 finally 清理 `ACTIVE_RUNS` |
| local memory | `ensure_local_memory_context` 继续 fake，不牵连 memory refresh |

回滚边界：

| 失败信号 | 回滚范围 |
| --- | --- |
| streaming 顺序护栏失败 | 回滚 `test_stream_chat_reply_preflight_refreshes_pdf_metadata_before_saving_user` 的 SQLite wrapper 改造 |
| SSE 事件收尾不稳定 | 只回滚 streaming 测试改造，不影响 blocking / attachment 顺序护栏 |
| attachment metadata 写入变化 | 调整该测试的落库断言；不迁 `save_message` owner |

本轮验证：

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_chat_preflight.py -q  # 10 passed
```

后续工作量重估：

| 优先级 | 任务 | 范围 | 工作量 | 门禁 |
| --- | --- | --- | --- | --- |
| P0 | chat preflight 真实 DB 顺序护栏 | blocking / attachment / streaming 主路径已补齐；后续只接受回归修正 | 0 天 | preflight + attachment 聚焦测试 |
| P1 | history/memory DB owner 设计 | 先写 owner 矩阵、失败态测试和回滚边界；不得与 streaming/attachments/dedupe 同轮迁移 | M，约 0.5-1 天 | 新增哨兵先红后绿 + `scripts/check_owner_migration.sh` |
| P1 | DB owner 迁移候选落点评审 | 若设计通过，优先选择 `load_history/save_message` 或 memory record owner 的单一落点，不跨 owner 混改 | S-M，约 0.5 天 | owner 矩阵 + 聚合门禁 |
| P2 | PDF / Frontend 维护尾项 | 继续按回归触发 | 0-0.25 天 | 对应聚焦门禁 |

下一步建议：真实 DB 顺序护栏已经覆盖 blocking、attachment、streaming 三条主路径；下一轮应进入 history/memory DB owner 设计文档与失败态测试，不建议再继续堆同类顺序测试。

### 0.22 2026-07-02 history/memory DB owner 设计护栏

本轮没有直接迁 history/memory DB owner，而是先补 owner 矩阵和一个最小失败态护栏：`test_ensure_local_memory_context_clears_stale_record_when_recent_window_has_no_source`。该测试验证已有 `ChatSessionMemory` 记录时，如果当前消息窗口已经没有可进入 local memory 的 older turns，`ensure_local_memory_context()` 必须清空 stale summary 并返回 `None`，避免旧公司/旧口径继续被喂给 preflight。

Owner 矩阵：

| 类别 | 函数 / 区域 | 下一步处理 | 原因 |
| --- | --- | --- | --- |
| 编排 owner 保留 | `_collect_chat_reply_impl`、`_stream_chat_reply_impl`、`_load_chat_run_preflight_context` | 暂留 `agent_chat_runtime_impl.py` | 它们控制 `load_history -> memory -> attachment refresh -> save_user -> create_run` 顺序，不能与 DB owner 迁移同轮动 |
| streaming owner 保留 | `_collect_stream_run`、`_start_streaming_chat_run`、`ACTIVE_RUNS` | 不与 history/memory 同轮迁移 | 涉及 SSE 收尾、后台 task、terminal events |
| attachment owner 保留 | `load_recent_session_attachments`、`_message_attachments`、`chat_message_has_visible_payload`、`_attachments_with_fresh_metadata` | 暂留 | 与 attachment payload、metadata 刷新、`attachments_json` 写入耦合 |
| 候选 A | `normalize_history`、`load_history` | 第一迁移候选，迁出时保留 runtime 兼容入口 | 只读 DB，边界最小，已有 blocking/attachment/streaming 真实 DB 顺序护栏覆盖 |
| 候选 B | `_load_session_memory_record`、`refresh_session_memory`、`load_local_memory_context`、`ensure_local_memory_context` | 单独一轮迁移到 memory store | 语义集中在 `ChatSessionMemory`；本轮新增 stale clear 失败态护栏 |
| 候选 C | `save_message` | 后置单独迁移 | 真实写入 owner，同时包含 assistant evidence normalization、attachment metadata 二次刷新、schema column 兜底 |
| 候选 D | `chat_history_response`、`_chat_message_payload` | 后置 | 属于对外 history response/display 形状，不是第一 DB owner 核心 |

完成项：

- 新增 stale memory 失败态护栏：已有 memory record 但当前 recent window 无可总结 source 时，清空 stale summary、`last_message_id=None`、`ensure_local_memory_context()` 返回 `None`。
- 明确第一迁移候选为 `normalize_history + load_history`，而不是直接迁 `save_message` 或 streaming lifecycle。
- 明确 memory record owner 迁移必须单独成轮，不与 `save_message`、attachment metadata、dedupe/catalog 或 streaming active run 混改。

行为与风险边界：

| 路径 | 本轮保持 / 改进 |
| --- | --- |
| stale memory record | 已有记录不再因为当前无 source 而继续输出旧 context |
| memory owner 迁移准备 | 先有失败态护栏，再谈迁移 |
| runtime 行为 | 本轮只新增测试和文档，不改运行时代码 |

回滚边界：

| 失败信号 | 回滚范围 |
| --- | --- |
| stale clear 护栏不稳定 | 仅回滚 `test_ensure_local_memory_context_clears_stale_record_when_recent_window_has_no_source` |
| owner 矩阵与实际迁移冲突 | 更新 0.22 矩阵，不回退已通过的 preflight/attachment/streaming 顺序护栏 |
| 后续迁移影响 streaming / attachment | 停止迁移，回到本轮 owner 矩阵重新拆分 |

本轮验证：

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_memory.py -q  # 14 passed
```

后续工作量重估：

| 优先级 | 任务 | 范围 | 工作量 | 门禁 |
| --- | --- | --- | --- | --- |
| P0 | history/memory owner 失败态护栏 | 本轮已完成；后续只接受回归修正 | 0 天 | `tests/test_agent_runtime_memory.py` |
| P1 | 迁移 `normalize_history + load_history` owner | 新建 history store，runtime 保留薄 wrapper；不迁 save/memory/streaming | S-M，约 0.5 天 | preflight + runtime 聚焦 + owner 总门禁 |
| P1 | memory record owner 迁移设计 | 若继续迁 `_load_session_memory_record/refresh/load/ensure`，必须单独成轮 | M，约 0.5-1 天 | memory 全套 + stale clear 护栏 |
| P2 | `save_message` owner 迁移 | 后置，需单独处理 attachments/evidence/schema column 风险 | M，约 0.5-1 天 | attachment + preflight + chat history response |

下一步建议：优先迁 `normalize_history + load_history` 到独立 history store，并在 `agent_chat_runtime_impl.py` 保留兼容入口；不要同时迁 `save_message`、memory record owner 或 streaming lifecycle。

### 0.23 2026-07-02 history read owner 迁移

本轮按 0.22 的建议执行第一段 DB owner 迁移：新增 `services/agent_runtime_history.py`，把 `normalize_history` 的主体算法和 `load_history` 的只读 DB 查询迁到独立 history store；`agent_chat_runtime_impl.py` 继续保留同名薄 wrapper，并把 attachment、loop、evidence 相关 helper 以依赖注入传入新模块，确保现有 monkeypatch 点和 preflight 顺序护栏不变。

完成项：

- 新增 `agent_runtime_history.normalize_history()`：承接 role 过滤、可见 payload 过滤、assistant loop 污染过滤、连续同 role 折叠、leading assistant 裁剪、limit 截断。
- 新增 `agent_runtime_history.load_history()`：只负责读取当前 `session_id` 的 `ChatMessage`，按 id 还原顺序，再调用传入的 normalize 回调。
- 保留 `agent_chat_runtime_impl.normalize_history()` / `load_history()` 兼容入口：外部测试、monkeypatch、preflight 编排仍命中 runtime wrapper。
- 新增 `test_load_history_applies_normalize_history_contract_to_real_db_rows`：用真实 SQLite 验证 current session 过滤、顺序恢复、空/非法 role 过滤、leading assistant 裁剪、连续同 role 折叠、attachment-only user 注入历史附件上下文、loop-polluted assistant 过滤、以及 normalized history 上的 limit。

行为与风险边界：

| 路径 | 本轮保持 / 改进 |
| --- | --- |
| `load_history` patch point | 仍保留在 `agent_chat_runtime_impl.py`，preflight 测试可继续 monkeypatch |
| attachment owner | `_message_attachments`、`chat_message_has_visible_payload`、`_attachment_reference_context` 不迁出，只作为依赖注入 |
| loop/evidence owner | `_is_loop_polluted_assistant_message`、`_sanitize_assistant_history_reply`、`normalize_evidence_trace_for_display` 不迁出 |
| DB 写入 owner | `save_message` 未迁移，不触碰 attachment metadata 二次刷新和 schema column 兜底 |
| streaming/memory owner | `_collect_stream_run`、`ACTIVE_RUNS`、`refresh_session_memory` 本轮不动 |

回滚边界：

| 失败信号 | 回滚范围 |
| --- | --- |
| normalization 合同失败 | 回滚 `agent_runtime_history.py` 与 runtime wrapper 接线 |
| preflight monkeypatch 失效 | 保留 wrapper，回滚 `_load_chat_run_preflight_context` 任何直接调用新模块的改动 |
| attachment/loop/evidence 语义漂移 | 回滚依赖注入接线，不迁相关 helper |

本轮验证：

```bash
cd apps/api && .venv/bin/python -m py_compile services/agent_runtime_history.py services/agent_chat_runtime_impl.py services/agent_runtime_memory.py services/agent_runtime_attachments.py services/agent_runtime_streaming.py tests/test_agent_runtime_history.py tests/test_agent_runtime_chat_preflight.py tests/test_agent_chat_runtime_attachments.py tests/test_agent_chat_runtime_loops.py tests/test_agent_runtime_memory.py  # passed
git diff --check  # passed
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_history.py tests/test_agent_runtime_chat_preflight.py tests/test_agent_chat_runtime_attachments.py tests/test_agent_chat_runtime_loops.py tests/test_agent_runtime_memory.py -q  # 98 passed
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_*.py -q  # 236 passed
scripts/check_owner_migration.sh  # passed: API active/loop/preflight, runtime focused, compile, PDF parser, web node unit, frontend lint/build, whitespace/status review
```

后续工作量重估：

| 优先级 | 任务 | 范围 | 工作量 | 门禁 |
| --- | --- | --- | --- | --- |
| P0 | history read owner 迁移 | 本轮已完成；后续只接受回归修正 | 0 天 | history + preflight + attachment + loops |
| P1 | memory record owner 迁移设计 | 若继续迁 `_load_session_memory_record/refresh/load/ensure`，先拆 memory store，保持 stale clear 护栏 | M，约 0.5-1 天 | memory 全套 + owner 总门禁 |
| P1 | `chat_history_response` display owner 评审 | 可后置迁响应展示形状；不得混入 prompt history 或 save owner | S-M，约 0.5 天 | router/history response 相关测试 |
| P2 | `save_message` owner 迁移 | 继续后置，需单独处理 attachments/evidence/schema column 风险 | M，约 0.5-1 天 | attachment + preflight + chat history response |

下一步建议：先不要迁 `save_message`；如果继续推进后端 owner 拆分，优先做 memory record owner 的独立设计和失败态测试，或单独评审 `chat_history_response` display owner。

### 0.24 2026-07-02 memory record owner 迁移

本轮按 0.23 的 P1 建议继续推进 DB owner 拆分：`services/agent_runtime_memory.py` 从“纯摘要 helper”升级为 local-memory record owner，接管 `ChatSessionMemory` 的读取、刷新、上下文加载和 ensure 编排；`agent_chat_runtime_impl.py` 保留 `_load_session_memory_record()`、`refresh_session_memory()`、`load_local_memory_context()`、`ensure_local_memory_context()` 薄 wrapper，把 runtime 配置、profile/session gating、summary/context helper 以依赖注入传入 memory owner，确保 preflight、streaming、catalog、attachment 路径仍命中原 patch point。

完成项：

- `agent_runtime_memory.load_session_memory_record()` 接管 `ChatSessionMemory(profile, session_id)` 查询。
- `agent_runtime_memory.refresh_session_memory()` 接管当前 session 的 `ChatMessage` 顺序读取、recent window 外 source selection、summary 生成结果持久化、stale record 清空语义。
- `agent_runtime_memory.load_local_memory_context()` / `ensure_local_memory_context()` 接管 memory context 加载与 refresh-then-load 编排。
- runtime 保留兼容 wrapper：现有测试可继续 monkeypatch `_load_session_memory_record`、`refresh_session_memory`、`load_local_memory_context`、`ensure_local_memory_context`。
- 新增 wrapper 哨兵测试：验证 `refresh_session_memory()` 仍使用 runtime record/summary patch point；验证 `ensure_local_memory_context()` 仍按 runtime wrapper 顺序先 refresh 后 load。

行为与风险边界：

| 路径 | 本轮保持 / 改进 |
| --- | --- |
| memory DB owner | `ChatSessionMemory` 读写集中到 `agent_runtime_memory.py` |
| runtime patch point | `_load_session_memory_record`、`refresh_session_memory`、`load_local_memory_context`、`ensure_local_memory_context` 均保留 |
| profile/session gating | `LOCAL_MEMORY_ENABLED`、`LOCAL_MEMORY_ENABLED_PROFILES`、`_session_id_matches_profile` 仍由 runtime 注入，不在 memory owner 复制业务常量 |
| summary/context owner | loop 污染过滤、assistant sanitize、context 包装仍通过 runtime wrapper/helper 注入 |
| history/save/streaming owner | `load_history`、`save_message`、`_collect_stream_run`、`ACTIVE_RUNS` 本轮不动 |

回滚边界：

| 失败信号 | 回滚范围 |
| --- | --- |
| memory record DB 行为漂移 | 回滚 `agent_runtime_memory.py` 新增 DB owner 函数与 runtime wrapper 接线 |
| preflight/catalog monkeypatch 失效 | 保留 runtime wrapper，回滚 `ensure/refresh/load` 直接调用新模块的任何外溢改动 |
| stale clear 或 profile/session 隔离失败 | 仅回滚 memory owner 迁移，不触碰 history read owner 和 streaming owner |

本轮验证：

```bash
cd apps/api && .venv/bin/python -m py_compile services/agent_runtime_memory.py services/agent_chat_runtime_impl.py tests/test_agent_runtime_memory.py  # passed
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_memory.py -q  # 16 passed
git diff --check  # passed
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_memory.py tests/test_agent_runtime_chat_preflight.py tests/test_agent_chat_runtime_attachments.py tests/test_agent_chat_runtime_loops.py tests/test_agent_runtime_history.py -q  # 100 passed
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_*.py -q  # 238 passed
scripts/check_owner_migration.sh  # passed: API active/loop/preflight, runtime focused, compile, PDF parser, web node unit, frontend lint/build, whitespace/status review
```

后续工作量重估：

| 优先级 | 任务 | 范围 | 工作量 | 门禁 |
| --- | --- | --- | --- | --- |
| P0 | memory record owner 迁移 | 本轮已完成；后续只接受回归修正 | 0 天 | memory + preflight + runtime focused |
| P1 | `chat_history_response` display owner 评审 | 单独评审对外 history response/display 形状；不得混入 prompt history、memory、save owner | S-M，约 0.5 天 | router/history response 相关测试 + runtime focused |
| P2 | `save_message` owner 迁移设计 | 后置，先列 attachment metadata、evidence、schema column fallback、background refresh 风险矩阵 | M，约 0.5-1 天 | attachment + preflight + chat history response |
| P2 | datetime UTC warning 清理 | 横切模型和 runtime 时间戳；只在 owner 迁移暂停时做 | M，约 0.5 天 | API focused + model/migration smoke |

下一步建议：不要继续扩大 memory owner；如果继续按优化方案推进，优先单轮评审 `chat_history_response` display owner。`save_message` 仍应后置，因为它同时牵涉附件 metadata、evidence trace、schema column fallback 和 background memory refresh。

### 0.25 2026-07-02 外部深度检查建议采纳矩阵

本轮对 Kimi Code 的深度检查建议做本地事实校验后纳入优化方案。处理原则：只采纳可被当前代码事实支撑、能形成明确验收门禁的任务；对已过时或表述过重的判断降级，不把未验证的数字或泛化结论写成事实。

本地校验结论：

- `tracking` 并非“完全未认证”：当前 `apps/api/main.py` 已对 `tracking_agent.router` 挂载 `Depends(get_current_user)`；但 `apps/api/routers/tracking.py` 仍存在未被主路由纳入的旧实现、全局 `TrackingAgent()` 单例和缺少细粒度权限的问题，仍应纳入安全治理。
- `sentiment_monitor.py` 的 `ne_count` 未定义问题成立，且 `apps/api/agents/tracking` 与 `agents/hermes/profiles/siq_tracking` 两份镜像均存在。
- `apps/api/agents/tracking/agent.py` 的 `strftime("%Y-Q%q")` 问题成立，应改为显式季度计算。
- `source_token` 与 `SIQ_AUTH_SECRET_KEY` 耦合成立：`apps/api/routers/source.py` 当前用 `AuthService.secret_key()` 为 source token 签名，应拆出独立 source token secret。
- `services/market-report-rules` 未纳入 `check_all.sh` 的说法已过时：当前 `scripts/check_all.sh` 已包含 rules 测试。
- Playwright `baseURL` 与 `webServer` 在 `15174` 内部一致，并非简单“端口不一致导致必然失败”；但 README / 本地默认前端端口为 `15173`，应统一为可配置策略并更新文档。
- `agent_user_router.py` 的 `_sync_agent_workspace_after_reply()` 已通过 `run_in_executor` 包裹同步 `Session(engine)`，不是直接阻塞事件循环；但全仓仍有多个 async route 注入同步 `Session`，需要做专项审计，不应盲目替换。
- `eval_e2e.py` 中默认年份、汽车行业话术和公司定位硬编码仍明显存在；`services/market-report-rules` 已有 `IndustryProfile`，可作为配置化落点。

采纳任务：

| 优先级 | 任务 | 采纳范围 | 工作量 | 验收门禁 |
| --- | --- | --- | --- | --- |
| P0 | Tracking 运行时缺陷修复 | 修复两份 `sentiment_monitor.py` 的 `ne_count`、`agent.py` 的季度字符串、`get_dashboard()` 对 `tracking-items.md` 的回读/空面板问题 | S，约 0.25-0.5 天 | 新增 tracking module 单测；`py_compile` 两份 tracking 包 |
| P0 | Tracking 权限和旧路由收口 | 保留 `tracking_agent.router` 的全局认证；为 tracking route 加 `tracking:read/write` 或等价权限；确认 `apps/api/routers/tracking.py` 是否废弃，若废弃则移除或隔离；消除旧实现全局 `TrackingAgent()` 单例风险 | S-M，约 0.5-1 天 | router 权限测试；未授权/低权限访问失败；主路由只暴露一个 tracking 实现 |
| P0 | Source token 密钥解耦 | 新增 `SIQ_SOURCE_TOKEN_SECRET`，source token 不再复用 JWT auth secret；默认切到 source secret 签发与验签，旧 auth secret 只允许显式 env opt-in 兼容；补过期、签名、任务归属测试 | S-M，约 0.5-1 天 | `tests/test_source_access.py` 扩展；环境变量缺失/轮换场景测试 |
| P1 | Async DB 使用审计 | 不盲改 `run_in_executor` 场景；优先列出 async route 中直接依赖同步 `Session` 的路径，按 workspace/chat/market reports 分批迁移或隔离线程池 | M，约 1-2 天 | 新增审计清单；每批迁移跑对应 router 测试 + API focused |
| P1 | `eval_e2e.py` 配置化 | 将默认年份、汽车行业话术、公司定位映射、行业关注点抽到 `IndustryProfile` 或 rules service 配置；保留默认 profile 兼容现有 demo | M，约 1-2 天 | eval_e2e 单测覆盖非汽车行业、非 2025 年、非 A 股代码 |
| P1 | CI 基线落地 | 新增 CI workflow，先跑 `scripts/check_all.sh` 的稳定子集和前端检查；owner 迁移继续用 `scripts/check_owner_migration.sh` 做专项门禁 | M，约 1 天 | PR/Push CI 可复现；README 标明本地/CI 门禁关系 |
| P1 | Playwright 端口策略统一 | 不直接认定当前配置失败；改为通过 `SIQ_FRONTEND_PORT` / `PLAYWRIGHT_BASE_URL` 统一 dev、README、E2E 端口说明 | S，约 0.25-0.5 天 | Playwright smoke 可启动；README / `apps/web/e2e/README.md` 对齐 |
| P1 | 前端 token 存储安全设计 | 将 localStorage JWT 迁移到 httpOnly Cookie + CSRF 作为单独安全设计，不与当前 owner 拆分混批 | L，约 3-5 天 | threat model、后端 cookie auth、CSRF 测试、前端 auth 回归 |
| P2 | 代码质量工具渐进接入 | 不一次性全仓开启 ruff/black/mypy；先对新增/触碰 Python 文件启用 ruff check，后续再分模块扩大 | M，约 1-2 天起 | 不产生大规模格式 churn；CI 先以 advisory 或 touched-files 模式运行 |
| P2 | 债务标记治理 | 先生成分类报告，不直接承诺外部统计数字；按安全、运行时、架构、文档分桶 | S，约 0.5 天 | `rg` 统计脚本 + issue/taskbook 输出 |
| P2 | Hermes gateway 容器化与部署统一 | 当前 README 已明确 Hermes 仍依赖本机 editable venv；作为生产化任务纳入，但不阻塞近期安全/架构收口 | M-L，约 2-4 天 | Compose profile 可启动 Hermes gateway；health check 覆盖 |
| P2 | 可观测性基线 | Prometheus/Grafana/结构化日志作为生产化后续，不与当前 runtime owner 拆分同批 | M-L，约 2-4 天 | API 关键路径 metrics、JSON log、dashboard smoke |

不按原文采纳或降级处理：

| 原建议 | 处理 |
| --- | --- |
| “tracking 完全未认证” | 降级为“缺细粒度权限 + 旧路由/单例收口”，因为主路由已挂 `get_current_user` |
| “market-report-rules 未纳入 check_all.sh” | 不纳入新任务，当前已完成 |
| “Playwright baseURL 端口不一致导致失败” | 降级为端口配置/文档统一；当前 Playwright `baseURL` 与 `webServer.url` 均为 `15174` |
| “同步 Session 在 async 中多处直接阻塞” | 改为审计任务；已看到 `run_in_executor` 包裹场景，需逐条判断 |
| “全仓一次性 ruff/black/mypy/pre-commit” | 降级为渐进式工具接入，避免大规模无关 churn |
| “立刻统一所有部署方式” | 降级为生产化阶段任务，近期优先 P0 安全/运行时缺陷 |

更新后的近期路线图：

| 阶段 | 目标 | 任务 |
| --- | --- | --- |
| 第 1 阶段：止血 | 让 demo 和内测路径不暴露明显安全/运行时风险 | Tracking 运行时缺陷；Tracking 权限/旧路由收口；Source token 独立密钥 |
| 第 2 阶段：架构收口 | 延续当前 owner 拆分，保持小步可回滚 | `chat_history_response` display payload owner；`save_message` owner 设计矩阵；Async DB 审计 |
| 第 3 阶段：扩展性 | 减少多市场/多行业阻塞 | `eval_e2e.py` 配置化；CN rules 迁移设计；向量入库入口梳理 |
| 第 4 阶段：工程化 | 提升持续交付和可运维性 | CI 基线；Playwright 端口策略；ruff 渐进接入；Hermes 容器化；可观测性 |

### 0.26 2026-07-02 display payload owner 迁移

本轮按 0.24/0.25 的架构收口路线执行 `chat_history_response` display owner 的最小切片。根据后台复核，`chat_history_response` 的 DB 查询、`fetch_limit * 3`、session 过滤和 visible filter 暂留 `agent_chat_runtime_impl.py`，只将 `_chat_message_payload()` 的对外 response payload 组装迁入 `services/agent_runtime_display.py`；runtime 保留 `_chat_message_payload()` wrapper，用依赖注入传入 `_message_attachments`、`_assistant_reply_for_display` 和 `normalize_evidence_trace_for_display`。

完成项：

- 新增 `agent_runtime_display.chat_message_payload()`：接管对外历史消息 payload 字段组装，包括 `id/session_id/role/content/created_at/attachments`。
- runtime `_chat_message_payload()` 保留兼容入口，继续作为现有测试和调用点的 patch/read target。
- 新增真实 SQLite display response 合同测试：验证 foreign session 忽略、空内容无附件过滤、attachment-only user 仍展示为空 content + attachments、loop-polluted assistant 在 UI history 中显示 stop message、assistant evidence 做展示归一化、limit 后仍按时间正序。
- 新增 wrapper 哨兵测试：验证 `chat_history_response()` 仍使用 runtime 的 `chat_message_has_visible_payload` 与 `_chat_message_payload` patch point。

行为与风险边界：

| 路径 | 本轮保持 / 改进 |
| --- | --- |
| display payload owner | `_chat_message_payload` 主体迁入 `agent_runtime_display.py` |
| `chat_history_response` DB 查询 | 暂留 runtime，不迁入 display module，避免 query/visibility owner 扩大 |
| prompt history owner | `load_history` / `normalize_history` 不动，避免 UI history 与 Hermes prompt history 混用 |
| DB write owner | `save_message` / `save_message_in_background` 不动，避免保存顺序和附件 metadata 风险 |
| memory/streaming owner | `ensure_local_memory_context`、`refresh_session_memory`、`ACTIVE_RUNS`、`_collect_stream_run` 不动 |

回滚边界：

| 失败信号 | 回滚范围 |
| --- | --- |
| UI history payload 字段漂移 | 回滚 `agent_runtime_display.chat_message_payload()` 与 runtime wrapper 接线 |
| prompt history 或 attachment 语义漂移 | 确认未复用 `load_history` / `_attachment_reference_context`；仅回滚 display payload helper |
| router response 形状漂移 | 后续补 router history 哨兵，不扩大本轮 display owner |

本轮验证：

```bash
cd apps/api && .venv/bin/python -m py_compile services/agent_runtime_display.py services/agent_chat_runtime_impl.py tests/test_agent_runtime_display.py tests/test_agent_chat_runtime_loops.py  # passed
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_display.py tests/test_agent_chat_runtime_loops.py tests/test_agent_runtime_history.py -q  # 79 passed
cd apps/api && .venv/bin/python -m py_compile services/agent_chat_runtime.py services/agent_chat_runtime_impl.py services/agent_runtime_history.py services/agent_runtime_display.py tests/test_agent_runtime_display.py tests/test_agent_chat_runtime_loops.py tests/test_agent_chat_runtime_attachments.py tests/test_agent_runtime_chat_preflight.py tests/test_agent_router_attachments.py  # passed
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_history.py tests/test_agent_chat_runtime_loops.py tests/test_agent_chat_runtime_attachments.py tests/test_agent_runtime_chat_preflight.py tests/test_agent_runtime_display.py tests/test_agent_router_attachments.py -q  # 114 passed
scripts/check_owner_migration.sh  # passed: API active run + loops 94 passed; API runtime focused 241 passed; PDF parser source/artifact 53 passed; PDF parser full 304 passed; Web node unit 44 passed; npm run check:frontend passed; git diff --check passed
```

后续工作量重估：

| 优先级 | 任务 | 范围 | 工作量 | 门禁 |
| --- | --- | --- | --- | --- |
| P0 | display payload owner 迁移 | 本轮已完成；后续只接受回归修正 | 0 天 | display + loops + history |
| P1 | router history response 哨兵 | 固定 `/chat/history`、specialist user router、fixed agent router 的 response 形状和 runtime patch point | S，约 0.25-0.5 天 | 新增 router history tests |
| P1 | `save_message` owner 设计矩阵 | 仅设计，不立即迁移；列 attachment metadata、evidence、schema column fallback、background memory refresh 风险 | S-M，约 0.5 天 | 设计文档 + 现有 attachment/preflight 测试 |
| P0 | 外部深度检查 P0 止血项 | 本轮已完成；后续只接受回归修正 | 0 天 | tracking runtime + permissions + source tests |

### 0.27 2026-07-02 外部深度检查 P0 止血项收口

本轮按 0.25 的 P0 止血路线执行三条小切片：Tracking 运行时缺陷、Tracking 权限/旧路由收口、Source token 密钥解耦。后台分工中，source token 与 tracking 权限分别由 worker 并行实现，主线程完成 tracking runtime 修复、旧 router 单例进一步隔离、最终 review 和聚焦门禁。

完成项：

- Tracking runtime：修复两份 `sentiment_monitor.py` 中的 `ne_count` NameError；将 `apps/api/agents/tracking/agent.py` 的季度字符串改为显式 `_report_period_for_quarter()`；补 `tracking-items.md` 回读 parser，使 `get_dashboard()` 能展示 `ReportUpdater.create_tracking_items_file()` 生成的事项。
- Tracking schema：将 `TrackingDashboard.recent_alerts` 从 `AlertRecordResponse` 对齐为实际运行时返回的 `AlertReport`，避免 `process_report()` 构建 dashboard 时类型漂移。
- Tracking 权限：保留 `main.py` 对 `tracking_agent.router` 的全局认证，同时在 specialist tracking router 上按方法增加 `tracking.read` / `tracking.write` 细粒度权限；`analyst/admin/super_admin` 拥有读写，`viewer/reviewer` 不拥有。
- 旧 tracking route：`routers/__init__.py` 不再包级导入旧 `routers/tracking.py`；主应用 route table 不暴露 `/api/tracking/process`、`/api/tracking/dashboard/{stock_code}` 等旧 REST 路径；旧 router 内全局 `TrackingAgent()` 改为惰性初始化，降低误 import 风险。
- Source token：新增 `SIQ_SOURCE_TOKEN_SECRET`，配置后 source token 使用独立密钥签发/验签；未配置时 fallback 到 `SIQ_AUTH_SECRET_KEY` 保持本地/dev 兼容；兼容期支持当前 auth secret 签名的旧 source token 验签；短 source secret fail closed。

行为与风险边界：

| 路径 | 本轮保持 / 改进 |
| --- | --- |
| source token 签发 | 配置 `SIQ_SOURCE_TOKEN_SECRET` 后不再复用 JWT auth secret |
| source token 兼容 | 仅兼容当前 `SIQ_AUTH_SECRET_KEY` 签出的旧 token；如果 auth secret 已先轮换，更早 token 不再可验 |
| tracking specialist router | 继续复用 `create_specialist_agent_router()`，只在 route 层叠加权限依赖，不改 chat runtime |
| legacy tracking REST router | 不接入主应用；保留文件但移除 import-time 全局 agent 实例 |
| tracking markdown parser | 只解析 `ReportUpdater.create_tracking_items_file()` 生成的稳定格式，不扩展为通用 Markdown parser |

本轮验证：

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_tracking_runtime.py -q  # 4 passed
cd apps/api && .venv/bin/python -m pytest tests/test_source_access.py -q  # 13 passed
cd apps/api && .venv/bin/python -m pytest tests/test_tracking_agent_permissions.py -q  # 5 passed
cd apps/api && .venv/bin/python -m pytest tests/test_source_access.py tests/test_tracking_runtime.py tests/test_tracking_agent_permissions.py tests/test_agent_router_attachments.py -q  # 31 passed
cd apps/api && .venv/bin/python -m py_compile routers/source.py routers/tracking.py routers/tracking_agent.py agents/tracking/agent.py agents/tracking/modules/sentiment_monitor.py ../../agents/hermes/profiles/siq_tracking/modules/sentiment_monitor.py  # passed
git diff --check  # passed
scripts/check_owner_migration.sh  # passed: API active run + loops 94 passed; API runtime focused 241 passed; PDF parser source/artifact 53 passed; PDF parser full 304 passed; Web node unit 44 passed; npm run check:frontend passed; git diff --check passed
```

后续工作量重估：

| 优先级 | 任务 | 范围 | 工作量 | 门禁 |
| --- | --- | --- | --- | --- |
| P0 | 外部深度检查 P0 止血项 | 本轮已完成；后续只接受回归修正 | 0 天 | tracking runtime + permissions + source tests |
| P1 | Async DB 使用审计 | 列出 async route 中同步 `Session` 使用点，按 workspace/chat/market reports 分批治理 | M，约 1-2 天 | 审计清单 + 对应 router tests |
| P1 | `eval_e2e.py` 配置化 | 年份、行业话术、公司定位映射、行业关注点下沉到 profile/rules 配置 | M，约 1-2 天 | eval_e2e 单测覆盖非汽车行业、非 2025 年 |
| P0 | router history response 哨兵 | 本轮已完成；后续只接受回归修正 | 0 天 | `tests/test_router_history_response.py` |
| P1 | CI / Playwright | 不与 runtime owner 混批；后续分批做 CI 基线和端口策略 | S-M，约 0.5-1 天分批 | CI smoke；Playwright smoke |

### 0.28 2026-07-02 router history response 哨兵

本轮按 0.26 的 P1 后续项补 router 层 history response 哨兵。目标是固定路由薄包装合同和 runtime patch point，不改 `chat_history_response()` 的 DB 查询、visible filter、display payload owner，也不进入 Hermes / session manager / quota / workspace owner。

完成项：

- 新增 `apps/api/tests/test_router_history_response.py`，覆盖三类 history endpoint：
  - `routers.chat.chat_history()`：先通过 `resolve_or_create_session(..., "assistant", requested_session_id)` 解析 session，再调用 `routers.chat.chat_history_response()`，返回 `{"messages": <runtime list>, "session_id": <resolved_session_id>}`。
  - `create_specialist_agent_router()` 生成的 `/chat/history`：先通过 `resolve_or_create_session(..., config.tag, requested_session_id)` 解析 session，再调用 `routers.agent_user_router.chat_history_response()`，返回同样的 envelope。
  - `create_agent_chat_router()` 生成的 fixed-agent `/chat/history`：继续直接透传 `routers.agent_chat_router.chat_history_response()` 的 runtime list，不包 `messages/session_id`，避免行为漂移。
- 测试均直调 endpoint coroutine，使用 `SimpleNamespace` async session 和 monkeypatch 的 `chat_history_response()` / `resolve_or_create_session()`，不依赖真实 DB、Hermes、FastAPI auth 或 TestClient。
- 后台复核确认该测试范围与 0.26 的边界一致：router 哨兵只锁路由合同，runtime display/visibility 仍由 `test_agent_runtime_display.py` 覆盖。

行为与风险边界：

| 路径 | 本轮保持 / 改进 |
| --- | --- |
| assistant `/chat/history` | 固定 envelope response shape 和 resolved session id |
| specialist `/chat/history` | 固定 shared factory response shape，tracking 权限仍由独立测试覆盖 |
| fixed agent `/chat/history` | 保持 direct-list 既有行为，不做统一 envelope 变更 |
| runtime history owner | 不触碰 `chat_history_response()` 查询、过滤、payload wrapper |

本轮验证：

```bash
cd apps/api && .venv/bin/python -m py_compile tests/test_router_history_response.py routers/chat.py routers/agent_user_router.py routers/agent_chat_router.py  # passed
cd apps/api && .venv/bin/python -m pytest tests/test_router_history_response.py tests/test_agent_router_attachments.py tests/test_tracking_agent_permissions.py -q  # 17 passed
```

下一步候选：

- Async DB 使用审计：后台预研已列出 `services/auth_dependencies.py:get_current_user`、`routers/chat.py`、`routers/agent_user_router.py`、`routers/workspace.py`、`routers/document_parser.py`、`routers/market_reports.py` 等同步 `Session` 风险点；下一轮应先产出 allowlist / AST 检查设计，不直接大改 DB owner。
- CI / Playwright：继续按 0.27 重估拆成独立小批次，不和 Async DB 或 runtime owner 混批。

### 0.29 2026-07-02 Async DB 使用审计 baseline 护栏

本轮按 0.28 的下一步候选推进 Async DB 使用审计，但只做“发现与防扩散”护栏，不迁移 DB owner，不修改 async route 行为，也不把既有同步 `Session` 债务一次性变成基础门禁红灯。

完成项：

- 新增 `apps/api/tests/test_async_sync_session_audit.py`，用 AST 扫描 `apps/api/routers/*.py` 与 `apps/api/services/auth_dependencies.py` 中的 `ast.AsyncFunctionDef`。
- 当前最小规则覆盖两类风险：
  - async 函数参数默认值为 `Depends(get_session)`，例如 `session: Session = Depends(get_session)` / `sync_session: Session = Depends(get_session)`。
  - async 函数体内直接调用 `next(get_session())`。
- 扫描会递归进入 factory 内部的 nested async endpoint，例如 `create_specialist_agent_router.chat` / `chat_stream`，但检查某个 async 函数体时会跳过更内层函数，避免把 `done_payload` 重复计入外层 `chat_stream`。
- baseline 采用单条 finding allowlist，而不是文件级 allowlist。后续删除已有风险不会失败；新增未登记的同步 Session 使用会失败。

当前 baseline 概况：

| 类型 | 数量 | 主要集中区域 |
| --- | ---: | --- |
| `Depends(get_session)` in async def | 56 | `document_parser.py`、`workspace.py`、`source.py`、`chat.py`、`agent_user_router.py`、`auth.py`、`market_reports.py`、`auth_dependencies.py` |
| `next(get_session())` in async def | 2 | `routers/chat.py::chat`、`routers/chat.py::chat_stream.done_payload` |

边界与风险：

| 路径 | 本轮保持 / 改进 |
| --- | --- |
| DB owner | 不迁移同步 `Session` 到 `AsyncSession`，只加防扩散测试 |
| 门禁接入 | 暂不作为独立脚本 gate 加入 `scripts/check_all.sh` 或 `scripts/check_owner_migration.sh`；全量 API pytest 会间接覆盖该测试 |
| `run_in_executor` 场景 | 不盲判为必须立即迁移，先进入 allowlist / 分类审计 |
| 漏报范围 | 暂不覆盖 `Depends(database.get_session)`、`Annotated[..., Depends(get_session)]`、`Session(engine)`、跨函数同步 helper 调用等复杂模式 |

本轮验证：

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_async_sync_session_audit.py -q  # 1 passed
```

下一步候选：

- 将 `test_async_sync_session_audit.py` 的 baseline 按模块分桶输出到文档或独立报告，标注 P1/P2 迁移顺序。
- 优先设计 `auth_dependencies.py:get_current_user` 与 chat / specialist agent route 的迁移方案，因为它们覆盖面广且在主交互路径上。
- 后续若要进 CI，先作为 advisory 或单独 `check_async_db_audit.sh`，不要直接污染基础合并门禁。

### 0.30 2026-07-02 Async DB 审计工具化

本轮将 0.29 的内联 AST 审计逻辑抽成独立脚本，便于人工生成报告和后续 advisory CI 复用；测试仍只作为 allowlist 防扩散护栏，不作为独立脚本 gate 接入 `scripts/check_all.sh` / `scripts/check_owner_migration.sh`。

完成项：

- 新增 `apps/api/scripts/audit_async_sync_session.py`：
  - 默认扫描 `apps/api/routers/*.py` 与 `apps/api/services/auth_dependencies.py`。
  - 文本输出 summary、by_kind、by_path 和 finding key。
  - 支持 `--json` 输出，便于后续生成报告或接 advisory CI。
  - 默认退出码保持 0，当前定位为审计报告工具，不是阻断式门禁。
- `apps/api/tests/test_async_sync_session_audit.py` 改为复用 `scripts.audit_async_sync_session.sync_session_usage()`，测试文件仅保留 allowlist 和防扩散断言。
- 后台复核确认：工具化本身不应接入 owner migration 门禁或基础全量门禁；若后续接 CI，应单独脚本或 advisory 模式，避免把既有债务转成合并红灯。

当前工具输出概况：

```text
total: 58
depends_get_session: 56
next_get_session: 2
```

推荐迁移顺序预研：

| 顺序 | 候选 | 理由 | 测试要求 |
| --- | --- | --- | --- |
| 1 | `services/auth_dependencies.py:get_current_user` | 覆盖所有受保护 endpoint，局部代码清晰，收益最大 | token by id/by username、missing sub、user missing、pending/rejected/disabled |
| 2 | chat / specialist chat 的 quota + usage | 主交互路径仍注入同步 `Session`，其余 history/message runtime 已是 async | quota exceeded、usage source、control reply、normal reply、stream done |
| 3 | `upload_chat_attachments` PDF 分支 | 同步 DB 写入集中在 artifact/usage；文件写入和 parser submit 不应混迁 | non-PDF、PDF submit success、parser failure metadata |
| 4 | achievements / workspace artifact executor 场景 | 已用 executor 隔离，同步 helper 复用面较大，放后置 | 成就、workspace artifact、后台 done payload |

本轮验证：

```bash
cd apps/api && .venv/bin/python scripts/audit_async_sync_session.py  # total 58
cd apps/api && .venv/bin/python scripts/audit_async_sync_session.py --json  # valid JSON
cd apps/api && .venv/bin/python -m pytest tests/test_async_sync_session_audit.py -q  # 1 passed
cd apps/api && .venv/bin/python -m py_compile scripts/audit_async_sync_session.py tests/test_async_sync_session_audit.py  # passed
```

### 0.31 2026-07-02 API 安全线最终收口

本轮在 0.27-0.30 的基础上完成 API 安全线收口。原则是只修正当前安全/权限/source/auth 主题，不继续打开 Agent runtime、PDF parser、前端状态 owner 或新的 Async DB owner。

完成项：

- `services/auth_dependencies.py:get_current_user` 已从同步 `Session = Depends(get_session)` 迁到 `AsyncSession = Depends(get_async_session)`，用户查询改为 `await session.exec(...)`；`require_permission()` 调用形状保持不变。
- 认证分支覆盖补齐：token decode 失败、数字 subject、username subject、missing subject、missing user、pending、rejected with note、rejected without note、disabled、FastAPI dependency + temp async DB、main app protected route 正负例。
- Async DB 审计 baseline 更新：`services/auth_dependencies.py:get_current_user` 不再属于同步 Session allowlist；当前防扩散测试继续保护剩余同步 session 债务不新增。当前 `scripts/audit_async_sync_session.py` 输出已更新为 total 56（`depends_get_session` 54、`next_get_session` 2）；0.30 的 total 58 为迁移前 baseline，0.31 初稿中的 total 57 为中间态。
- Source token hardening 更新：配置 `SIQ_SOURCE_TOKEN_SECRET` 后 source token 使用独立密钥；新 token 用 source secret 签发，默认不再接受当前 `SIQ_AUTH_SECRET_KEY` 签过的旧 source token；只有显式 `SIQ_SOURCE_ACCEPT_LEGACY_AUTH_SECRET=1` 时才进入兼容验签；未配置 source secret 时保持旧 auth secret 行为；短 source secret fail closed；上游 PDF2MD 代理会大小写不敏感地剥离 `access_token` / `source_token` 查询参数，且不转发登录 `Authorization`。
- Tracking 权限收口补强：`tracking_agent.router` 在全局认证外继续按方法叠加 `tracking.read` / `tracking.write`；`viewer/reviewer` 对 read/write 均无 tracking 权限；真实 `/api/tracking/chat/history` 路由已覆盖权限、session envelope 和 FastAPI datetime JSON 序列化。
- Tracking runtime 与 schema 修复保持：`ne_count` NameError、季度字符串、`tracking-items.md` 回读、legacy tracking REST router 不暴露、旧 router agent 惰性初始化、`recent_alerts: list[AlertReport]`。

行为与风险边界：

| 路径 | 当前状态 |
| --- | --- |
| service auth dependency | 已迁 AsyncSession；router/auth.py 的本地同步 auth 依赖不在本轮范围 |
| source token legacy fallback | 配置 source secret 后默认关闭；只有显式 `SIQ_SOURCE_ACCEPT_LEGACY_AUTH_SECRET=1` 才接受旧 auth secret 签名，新 token 用 source secret 签发 |
| source upstream proxy | 不转发 login/source token 参数；保留业务非敏感 query 参数 |
| tracking permissions | analyst/admin/super_admin 读写；viewer/reviewer 403，且不会进入 runtime patch point |
| Async DB audit | 防扩散护栏；不把 workspace/document_parser/chat 等既有同步 Session 债务混入本轮迁移 |
| Agent/PDF/frontend owner | 本轮不触碰 |

本轮验证：

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_auth_dependencies.py tests/test_auth_dependencies_smoke.py tests/test_async_sync_session_audit.py tests/test_tracking_agent_permissions.py tests/test_tracking_runtime.py tests/test_router_history_response.py tests/test_source_access.py -q  # 43 passed
cd apps/api && .venv/bin/python scripts/audit_async_sync_session.py  # total 56
cd apps/api && .venv/bin/python scripts/audit_async_sync_session.py --json  # valid JSON
cd apps/api && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m py_compile services/auth_dependencies.py routers/source.py routers/tracking.py routers/tracking_agent.py services/auth_service.py agents/tracking/agent.py agents/tracking/modules/sentiment_monitor.py agents/tracking/schemas.py scripts/audit_async_sync_session.py tests/test_auth_dependencies.py tests/test_auth_dependencies_smoke.py tests/test_source_access.py tests/test_tracking_agent_permissions.py ../../agents/hermes/profiles/siq_tracking/modules/sentiment_monitor.py ../../agents/hermes/profiles/siq_tracking/schemas.py  # passed
git diff --check  # passed
```

下一步建议：

- 先做纯提交拆分和文档同步，确保新增脚本/测试纳入同一主题或按主题拆分提交。
- API 安全线提交后，再单独开启 PDF `_ensure_*` 前置测试切片；只补测试，不改 queue、Flask response、task state 或 `_ensure_*` 编排。
- Agent runtime `_collect_stream_run` / history / attachments / local-memory owner 继续后置到单独设计窗口。

### 0.32 2026-07-02 低风险维护切片与 Login 收口

本轮按后台智能体复核后的风险队列推进：先收束已存在的 Login UI 未提交切片，再补 downloads 安全边界和 PDF `_ensure_*` 脏数据前置测试。原则仍是只做维护尾项，不打开 Agent runtime、PDF queue / Flask response / MinerU lifecycle、Document workbench refs/selection/scroll 或新的 Async DB owner。

完成项：

- Login UI 收口：`Login.tsx` 接入双栏品牌文案和表单面板；`auth.css` 补齐 `min-width: 0`、窄屏 padding、长中英文换行和按钮/输入响应式保护。`/login` 与 `/register` 在 1440、768、360、320 宽度均无横向溢出；注册页仍使用普通 `.auth-card` 与 mobile poster。
- downloads 安全边界补测：`tests/test_downloads.py` 覆盖 open/delete 两条路由对 path traversal、绝对路径和非白名单后缀的拒绝；现有 `_safe_relative_path()` / `safe_path_join()` / suffix allowlist 行为保持不变。
- PDF `_ensure_*` 维护尾项：`test_pdf_parser_ensure_wrappers.py` 新增非 dict `content_list_enhanced.json`、缺 markdown / 非 dict enhanced 短路、非 dict cached quality report 三个脏数据边界；`_ensure_quality_report()` 只做一行防御修正，要求 cached report 必须是 dict 才读取 `.get()`。
- Async DB 审计数字同步：当前 `scripts/audit_async_sync_session.py` 实测为 total 56（`depends_get_session` 54、`next_get_session` 2），仅记录基线漂移，不迁移新的同步 Session owner。

本轮未触碰：

| 红线 owner | 状态 |
| --- | --- |
| Agent runtime ordinary chat/history/attachments/local-memory/dedupe/build-run-input | 不迁移 |
| `stream_chat_reply` / Hermes `stream_run` / `_collect_stream_run` 主循环 | 不迁移 |
| PDF parser queue、task state、MinerU submit/poll/fetch/cache、Flask response | 不修改 |
| Document workbench refs/selection/scroll/resource open/CSS 运行时注入 | 不修改 |
| `workflow.py`、`market_reports.py` 控制面实质抽取 | 不混入 |
| 新 Async DB owner 迁移 | 不混入 |

本轮验证：

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_downloads.py -q
# 9 passed, 2 warnings

cd apps/pdf-parser && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q tests/test_pdf_parser_ensure_wrappers.py
# 10 passed

cd apps/pdf-parser && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q
# 329 passed

cd apps/web && npm run check:frontend
# passed

cd apps/api && .venv/bin/python scripts/audit_async_sync_session.py
# total 56

git diff --check
# passed
```

下一步建议：

- 先提交本轮维护切片；ignored `apps/web/dist/`、`apps/web/playwright-report/`、`apps/web/test-results/` 不进入索引。
- 若继续推进，优先只做 Async DB 审计报告分桶或 CI advisory，不直接迁移 chat/workspace/document_parser DB owner。
- 其余大 owner 继续按单独设计窗口处理，避免与 UI/测试维护尾项混批。

### 0.33 2026-07-02 Async 审计 advisory 与 Playwright 端口策略

本轮继续按“工程化小切片 + 不迁 owner”推进。后台智能体并行复核后确认：Async DB 现阶段只适合做报告分桶和 advisory 能力，不进入 chat/workspace/document_parser 的同步 Session owner 迁移；Playwright 端口策略可独立收口；前端搜索下载响应式修补可作为维护尾项验证后单独提交。

完成项：

- Async DB audit advisory：`apps/api/scripts/audit_async_sync_session.py` 新增 `--summary`、`--markdown` 和 advisory migration priority 分桶；默认仍退出 0，不接入 `scripts/check_all.sh` 或 `scripts/check_owner_migration.sh` 阻断门禁。
- Async DB audit 测试加固：`tests/test_async_sync_session_audit.py` 继续锁定 allowlist 防扩散，并新增 total 56、by_kind、by_path、advisory bucket 顺序/计数、临时 nested async AST fixture 和 `--json --summary` 输出测试。
- Playwright 端口策略：`apps/web/playwright.config.ts` 支持 `PLAYWRIGHT_BASE_URL` 与 `SIQ_FRONTEND_PORT`，默认仍为 15174；`use.baseURL`、`webServer.url` 与 dev server 端口由同一 URL 派生，`PLAYWRIGHT_BASE_URL` 未显式端口时会补齐配置端口；`apps/web/e2e/README.md` 已补端口说明。
- 搜索下载响应式维护：`SearchDownload.tsx`、`DownloadedReportsPanel.tsx` 与 `search-download.css` 收紧市场选择栅格、hero 操作区、查询表单和已下载文件搜索栏的 `min-width: 0` / grid 约束，避免 1024-1439 与移动视口横向撑开。

本轮验证：

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_async_sync_session_audit.py -q
# 4 passed

cd apps/api && .venv/bin/python scripts/audit_async_sync_session.py --summary
# total 56, advisory buckets emitted

cd apps/api && .venv/bin/python scripts/audit_async_sync_session.py --json --summary
# valid JSON, findings omitted by request

cd apps/api && .venv/bin/python scripts/audit_async_sync_session.py --markdown --summary
# valid Markdown summary

cd apps/web && npx eslint playwright.config.ts
# passed

cd apps/web && npx playwright test --list
# 25 tests in 5 files

cd apps/web && npm run check:frontend
# passed

cd apps/web && npm run e2e -- e2e/tests/search-download-responsive.spec.ts --project=chromium
# 3 passed

git diff --check
# passed
```

下一步建议：

- 若继续工程化，Async DB audit 可新增非阻断脚本入口或生成报告产物，但仍不要接入硬 CI，也不要迁移 DB owner。
- Playwright 后续只在真实需要时跑单文件 smoke；不扩大聊天或搜索下载 E2E 矩阵。
- 大 owner 仍需单独设计窗口：Agent runtime history/save/message、PDF queue/MinerU/Flask response、Document workbench refs/scroll、workflow/market_reports 控制面瘦身均不与维护尾项混批。

### 0.34 2026-07-02 Async DB audit 非阻断入口

本轮继续按 0.33 的工程化小切片建议推进，只新增本地 advisory 入口，不接入硬门禁，不迁移任何同步 Session owner，也不修改 API router / DB 行为。后台复核确认债务标记分类报告可作为后续 P2 报告型任务，但本轮不混入。

完成项：

- 新增 `scripts/check_async_db_audit.sh`：默认使用 `apps/api/.venv/bin/python`，支持 `API_PY` 覆盖，执行 `apps/api/scripts/audit_async_sync_session.py --summary` 并输出 advisory 摘要。
- 保持非阻断语义：既有 56 个 finding 不导致失败；只有解释器缺失、审计脚本缺失或命令自身失败才失败。
- `scripts/README.md` 增加 Async DB audit advisory 入口说明，明确该入口不是合并门禁；需要 Markdown/JSON 报告时由调用方显式重定向输出。
- 未接入 `scripts/check_all.sh` / `scripts/check_owner_migration.sh`，未生成或提交运行态报告文件。
- 并行前端维护：搜索下载页根节点补 `search-download-page` class，并继续收紧已下载文件列表的长文件名、长路径和外层卡片 overflow 约束；不改查询/下载/删除业务逻辑。

本轮验证：

```bash
bash -n scripts/check_async_db_audit.sh
# passed

scripts/check_async_db_audit.sh
# total 56, depends_get_session 54, next_get_session 2

cd apps/web && npm run check:frontend
# passed

cd apps/web && npm run e2e -- e2e/tests/search-download-responsive.spec.ts --project=chromium
# 3 passed

git diff --check
# passed
```

下一步建议：

- 债务标记治理可单独做报告型切片，只分类不修代码，避免把历史债务修复混入工程化入口。
- Async DB 后续仍只做报告或设计矩阵；迁移 `chat.py` / `workspace.py` / `document_parser.py` 的同步 Session owner 必须另开单独窗口。

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
cd apps/document-parser && python3 -m pytest tests
cd apps/web && npm run test:unit && npm run check:frontend
```

红灯 owner 迁移准入门禁：

基础合并门禁以 `README.md` 的“合并前基础门禁”为准；以下命令用于对应模块变更的聚焦验证，不作为默认全量 CI。当前可用 `scripts/check_owner_migration.sh` 聚合执行本节 Agent runtime / PDF parser / Web Node unit / frontend check / 通用提交前检查。

```bash
# Agent runtime streaming owner / `_collect_stream_run` 接线矩阵迁移前后必须通过
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_active_runs.py -q
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_active_runs.py tests/test_agent_chat_runtime_loops.py -q
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_*.py -q
cd apps/api && .venv/bin/python -m py_compile services/agent_runtime_streaming.py services/agent_chat_runtime_impl.py services/agent_runtime_sessions.py

# PDF parser artifact / lifecycle 维护尾项必须通过
cd apps/pdf-parser && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider tests/test_pdf_parser_artifact_orchestrator_service.py tests/test_pdf_parser_mineru_lifecycle.py -q
cd apps/pdf-parser && PYTHONPATH=. python3 -m pytest tests/test_pdf_source_viewer.py tests/test_pdf_parser_source_service.py -q

# Frontend Document 维护尾项必须通过
cd apps/web && npm run test:unit
cd apps/web && npm run check:frontend

# 每轮通用
git diff --check
git status --short  # review 输出；有内容时人工确认是否为本轮预期改动或 ignored runtime/cache/build
```

准入要求：

- 红灯 owner 迁移前先跑对应聚焦门禁并记录基线，迁移后同命令必须通过。
- 涉及 `ACTIVE_RUNS` / SSE 时，不与 PDF parser、Document workbench refs/scroll 或 CSS 注入迁移同批。
- 聚焦门禁失败时先回滚本轮 owner 迁移或降级为测试护栏，不继续扩大改动面。

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
状态：阶段完成，后续仅维护尾项
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
- 已拆 `PdfSourceWorkbench.tsx` 的 PDF 页码/bbox、表格合并、跨页 continuation 判断、table_relations artifact 读取转换、page overlay 构建和 fallback page HTML 到 `pdfSourceWorkbenchHelpers.ts`；页面组件进一步降至约 708 行，状态 owner、URL/toast/download 流不迁移；新增 `pdfSourceWorkbenchHelpers.test.ts` 直接覆盖 page number/bbox、page table sort、物理表合并、table_relations artifact、overlay 和 fallback HTML。
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
- Web Node unit gate 已固化为 `npm run test:unit`，自动发现 `src/**/*.test.ts`，当前覆盖 10 个测试文件。

验收：

- 新代码不再直接从业务组件调用裸 `fetch`。
- 新增页面 API 只暴露在 `features/*/api.ts` 或 shared client。
- `npm run test:unit` 通过，44 passed；`npm run check:frontend` 通过。
- 关键 Playwright：`document-result-preview.spec.ts`、`pdf-parsing-market-filter.spec.ts`、`search-download-responsive.spec.ts`、`workspace-responsive.spec.ts` 相关覆盖已通过。

### P-001：拆 `apps/pdf-parser/app.py`

优先级：P1
范围：`apps/pdf-parser`
状态：阶段完成，后续仅维护尾项
动作：

- 入口层已收敛为兼容 façade，原实现下沉到 `pdf_parser_app_impl.py`。
- 请求/任务参数 helper 已抽到 `pdf_parser_request_utils.py`。
- 运行时通用 helper 与线程安全文件缓存已抽到 `pdf_parser_runtime_utils.py`，保留 app 层 `_utc_now` / `APP_ACCESS_TOKEN` 兼容入口。
- Markdown 页码 marker 注入、重建、稀疏页回填和 marker 行解析已抽到 `pdf_parser_page_markers.py`，`app.py` 旧导入路径继续可用。
- SQLite task repository 已抽到 `pdf_parser_task_repository.py`，包含 schema/init、row hydration、CRUD、重复文件查询、recent summary、refresh candidate、queue 只读查询和 referenced paths；`app.py` 旧私有入口继续由 wrapper 暴露。
- Artifact 文件与路径 helper 已抽到 `pdf_parser_artifact_service.py`，包含 Markdown 路径解析、JSON 原子写、JSON artifact 加载、Markdown 写入、artifact status、图片保存/列表/ZIP、表格 HTML 定位和 correction 应用纯函数；`pdf_parser_app_impl.py` 保留同名 wrapper 与 Flask response owner。
- Source workbench IO helper 已抽到 `pdf_parser_source_service.py`，包含 corrections 路径/读写、source page payload wrapper、page bbox extent wrapper 和 PDF page image 缓存/渲染；route、markdown fetch、quality report、complete markdown 写入仍由 `pdf_parser_app_impl.py` 编排。
- DB 队列 claim/recover 和 artifact orchestrator 已完成最小 owner 试点；worker、Flask response、MinerU lifecycle、`_ensure_*` 编排仍留在 `pdf_parser_app_impl.py`，避免 WSGI 多进程/本地队列 claim 语义在纯拆分中顺手改变。
- 后续只补 `_ensure_*` 前置测试或 source/artifact payload 负路径；新的 MinerU / response / task state owner 迁移必须另开设计窗口。
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
- 下一步优先：保持观察或补低风险 `_ensure_*` 前置测试。高风险项：MinerU lifecycle、Flask response、任务状态写顺序和多进程 WSGI 下本地 queue worker 必须单独设计，不能在维护尾项中顺手修改。

### P-002：拆 PDF quality / financial / document_full 边界

优先级：P1
范围：`apps/pdf-parser`
状态：阶段完成，后续仅维护尾项
背景：

- `pdf_parser_app_impl.py` 已从约 6700 行降到约 4154 行，但仍承担 task state、route response、queue claim、路径存在性、文件写入、`_fetch_and_cache_result` 和 `_ensure_*` 编排 owner。
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

- 新模块有直接单元测试；当前 `test_pdf_parser_quality_service.py` 已覆盖候选回填、摘要 warning、全文财报三大表充足时过滤核心表噪声、statement/metric 既有 found+table+line 候选不被 financial fallback 覆盖、质量报告文件读写、银行噪声表过滤、statement display source 噪声 index 回落附近真实资产负债表、有效 table index 不被 nearby fallback 抢走、非数字行号防御、candidate summary 与 priority review 去重/截断，`test_quality_engine.py` 已覆盖 report year、confidence 阈值和 candidate group 纯函数边界，`test_pdf_parser_financial_service.py` 已覆盖 financial 路径/读取/current/write/ensure、schema/rule mismatch、单边 artifact 读取和 stale checks 触发重写边界，`test_pdf_parser_document_full_service.py` 已覆盖 resource index、document_full payload、table relations payload、relation alias、无效表过滤、缺 body enhanced table 由 content_list 回填、missing-body content table source id 不串用、未知/非 dict relation 负路径、file reference、缺失 source/resource 状态、content_list_enhanced 回写 document_full 初始化与不突变边界，`test_pdf_parser_response_service.py` 已覆盖 duplicate payload、recent limit clamp 和 recent task normalization，`test_pdf_parser_content_list_enhanced_service.py` 已覆盖 content_list_enhanced payload、table source helper、page inference helper、quality signals、财报附注金额解析/单位倍率/近邻单位/金额误差比较、chart/flowchart image semantic blocks、按需 OCR/VLM 候选图像、complete markdown 附录、markdown image details、markdown table records、Mermaid nodes/edges 与重复图片路径绑定边界，`test_pdf_parser_mineru_result_service.py` 已覆盖 MinerU 原始产物落盘边界，`test_pdf_source_viewer.py` 已覆盖 `page_bbox_extent_from_content_list` JSON coercion、目标页过滤、非法 bbox/非正宽高返回空、`printed_page_numbers_by_pdf_page` 页码映射、`page_content_payload_from_content_list` JSON 输入、非法页码、非 list 空 payload、source_id/bbox 表匹配、content_table_source_id=0、非法 report table row 跳过、非数字 focus table 行为和 image/list/unknown block 边界；后续继续观察更复杂的 complete markdown 回填或仅补低风险 payload/helper 边界。
- 当前聚焦门禁：`cd apps/pdf-parser && python3 -m pytest tests/test_pdf_parser_response_service.py tests/test_pdf_parser_document_full_service.py tests/test_pdf_parser_quality_service.py tests/test_pdf_parser_financial_service.py tests/test_pdf_parser_content_list_enhanced_service.py -q` 通过，65 passed。
- `cd apps/pdf-parser && python3 -m pytest tests/test_pdf_source_viewer.py tests/test_pdf_parser_source_service.py -q` 通过，22 passed。
- `cd apps/pdf-parser && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider tests/test_pdf_parser_content_list_enhanced_service.py tests/test_page_markers.py -q` 通过，79 passed。
- `cd apps/pdf-parser && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q` 通过，245 passed。
- `cd apps/pdf-parser && python3 -m flask --app app.py routes` 通过。
- `pdf_parser_app_impl.py` 行数继续下降，且没有把新 service 变成状态 owner。

### A-001：拆 `agent_chat_runtime.py`

优先级：P1
范围：`apps/api/services`
状态：阶段完成，后续需单独设计窗口
动作：

- 入口层已收敛为兼容 façade，原实现下沉到 `agent_chat_runtime_impl.py`。
- 已新增 `agent_runtime_attachments.py`、`agent_runtime_streaming.py`、`agent_runtime_memory.py`、`agent_runtime_citations.py`、`agent_runtime_loop_guard.py`、`agent_runtime_tools.py`、`agent_runtime_sessions.py` 作为领域边界模块。
- `agent_runtime_loop_guard.py` 已升级为真实实现模块，loop 检测、history sanitizer、失败回复清洗和相关停止消息常量已从 `agent_chat_runtime_impl.py` 搬出；旧 `services.agent_chat_runtime` 入口仍可访问同名符号。
- `agent_runtime_progress.py` 已新增为真实实现模块，progress payload/signature、文本进度提取、tool preview/label 已从 `agent_chat_runtime_impl.py` 下沉；impl 保留同名 wrapper 并传入当前 hash/clock/wiki root，保持 monkeypatch 语义。
- `agent_runtime_streaming.py` 已升级为 ACTIVE_RUNS / active SSE / stop 第一阶段状态 owner，接管 `ActiveRunState`、`ACTIVE_RUNS`、key normalization、progress/event append、snapshot 基础逻辑、active stream replay/heartbeat 和 `stop_active_run`；`_collect_stream_run` completed/stopped terminal helper、reasoning 单分支 helper 已收口到 streaming owner，cancel/timeout/tool-loop 接线矩阵已补齐；`agent_chat_runtime_impl.py` 保留 stop 薄 wrapper 注入 `stop_run`/消息常量，并继续保留 Hermes `stream_run` 调用、tool/delta 主循环、stream chat reply 和普通/streaming 编排 wrapper。
- 其余 `agent_runtime_*` 仍作为边界和迁移索引，不作为可 monkeypatch 的真实状态 owner；router 仍从兼容入口导入，避免 history、attachments、memory 的绑定语义发生变化。
- 当前 owner 迁移线停止在 active run / stop / terminal / cancel-timeout-tool-loop / reasoning helper；如继续 `stream_chat_reply`、sessions/history/memory owner，需另开设计窗口。
- 先纯搬迁，不改变行为。

验收：

- `python3 -m py_compile apps/api/services/agent_chat_runtime.py apps/api/services/agent_chat_runtime_impl.py apps/api/services/agent_runtime_*.py` 通过。
- `cd apps/api && uv run pytest tests/test_agent_chat_runtime_loops.py -q` 通过，54 passed。
- `cd apps/api && uv run pytest tests/test_agent_runtime_progress.py tests/test_agent_chat_runtime_loops.py -q` 通过，57 passed。
- `cd apps/api && uv run pytest tests/test_agent_chat_runtime_attachments.py tests/test_agent_chat_runtime_loops.py tests/test_agent_router_attachments.py tests/test_chat_document_parser_attachment.py -q` 通过，72 passed。
- `cd apps/api && uv run pytest tests/test_agent_runtime_progress.py tests/test_agent_chat_runtime_attachments.py tests/test_agent_chat_runtime_loops.py tests/test_agent_router_attachments.py tests/test_chat_document_parser_attachment.py -q` 通过，75 passed。
- 本轮新增 streaming/stop owner 门禁：`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_active_runs.py tests/test_agent_chat_runtime_loops.py -q` 通过，73 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_*.py -q` 通过，207 passed；`cd apps/api && .venv/bin/python -m py_compile services/agent_runtime_streaming.py services/agent_chat_runtime_impl.py services/agent_runtime_sessions.py` 通过。
- 本轮新增 `_collect_stream_run` 接线矩阵门禁：`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_active_runs.py tests/test_agent_chat_runtime_loops.py -q` 通过，82 passed，覆盖 terminal helper、cancel、timeout、tool-loop 事件顺序、`stop_run` monkeypatch、history save 和 ACTIVE_RUNS 清理；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_*.py -q` 通过，216 passed；`cd apps/api && .venv/bin/python -m py_compile services/agent_chat_runtime_impl.py services/agent_runtime_sessions.py services/agent_runtime_streaming.py tests/test_agent_runtime_active_runs.py` 通过。
- 本轮新增 reasoning 极小事件 helper 门禁：`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_active_runs.py -q` 通过，27 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_active_runs.py tests/test_agent_chat_runtime_loops.py -q` 通过，84 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_*.py -q` 通过，218 passed；`cd apps/api && .venv/bin/python -m py_compile services/agent_chat_runtime_impl.py services/agent_runtime_sessions.py services/agent_runtime_streaming.py tests/test_agent_runtime_active_runs.py` 通过。
- SSE 事件语义不变，`services.agent_runtime_streaming` 仍可惰性导出 `hermes_timeout`、`stream_chat_reply`、`stream_idle_timeout`，且 `stop_active_run(profile, session_id)` 保留直接调用兼容。
- 高风险项：`ACTIVE_RUNS` 与 stop owner 已单一归属 `agent_runtime_streaming.py`，`_collect_stream_run` 已有 terminal/cancel/timeout/tool-loop/reasoning 接线矩阵保护；普通 chat 与 SSE 路径共享 attachments/history/local-memory/build-run-input 顺序仍留在 impl，真实拆分前需要先补覆盖或只搬纯函数/单分支 helper。

### A-002：拆 Agent runtime 纯函数边界

优先级：P1
范围：`apps/api/services`
状态：阶段完成，后续需单独设计窗口
背景：

- `agent_chat_runtime_impl.py` 已继续下降，仍是当前最大后端文件。
- `ACTIVE_RUNS`、active stream replay/heartbeat、event append 和 stop owner 已迁到 `agent_runtime_streaming.py`；`_collect_stream_run` 已完成 terminal helper、reasoning 单分支 helper 与 cancel/timeout/tool-loop 接线矩阵，但 Hermes `stream_run` 调用、tool/delta 主循环、`stream_chat_reply`、ordinary chat 与 streaming 共享的 history/attachments/memory/dedupe 顺序仍高度耦合，暂不继续混批迁移。
- 可继续安全拆分的是纯函数和只读 discovery 逻辑。
- 已新增 `agent_runtime_tool_output.py`，搬出 `_normalize_tool_output` 的纯函数实现；`agent_chat_runtime_impl.py` 通过 import alias 保持 `_normalize_tool_output` 兼容入口，未迁移 `ACTIVE_RUNS`、SSE、run lifecycle、attachments/history owner。
- 已新增 `agent_runtime_parse_only.py`，搬出 `_pdf2md_parse_only_matches`、`_should_consider_pdf2md_parse_only_context`、`build_pdf2md_parse_only_context` 的只读 discovery 逻辑；旧同名函数仍由 `agent_chat_runtime_impl.py` wrapper 转发，保持兼容入口。
- 本轮已补 `agent_runtime_parse_only.py` 剩余脏数据边界：市场前缀伪 alias 防误匹配、非 dict task info 过滤、空 task/result/artifact 字段展示兜底；仍不接入真实 Wiki/DB owner。
- 已新增 `agent_runtime_display.py`，搬出 `_display_message_with_attachments` 的纯展示格式化逻辑；旧同名函数仍由 `agent_chat_runtime_impl.py` wrapper 转发，消息保存顺序不变。
- 本轮已补 `agent_runtime_display.py` 细边界：`url=None` 不生成 Markdown target、无 basename path fallback 为 `attachment`、URL 内部控制空白编码。
- 本轮已补 `agent_runtime_tool_output.py` 细边界：`None` / 空白输出、list JSON 形状保持、长文本不截断且保留换行、`tool` / `label` 字段不被误当作 status/output。
- 已扩展 `agent_runtime_citations.py`，下沉 plain inline LaTeX normalization、evidence trace normalization、结构化 citation 检测、primary data source refs 合并、PostgreSQL fallback context 和多类 primary-data supplement renderer；旧同名函数仍由 `agent_chat_runtime_impl.py` wrapper 转发，保留 evidence fallback 编排和 monkeypatch 入口。
- 本轮已补 citations/reference 合并边界与 `citation_links.py` 后处理边界：空 body 新增引用区、全部无效 refs 原样返回、三级引用来源 section 在 peer/parent heading 前收口、缺 task_id/pdf_page 原样返回、本地 API 链接 query/fragment 保留、重复链接不追加和 printed_page 空槽位对齐。
- 已新增/实化 `agent_runtime_fallback_contexts.py`、`agent_runtime_catalog.py`、`agent_runtime_postgres_fallback.py`、`agent_runtime_statement_context.py`、`agent_runtime_financial_format.py`、`agent_runtime_memory.py`、`agent_runtime_dedupe.py`、`agent_runtime_context.py` 与 `agent_runtime_financial_guard.py`，下沉 PostgreSQL fallback row helper、PostgreSQL fallback query/parse/predicate helper、three-statement record iteration/ranking/latest helper、financial display number/per-capita/formula/table ref helper、Wiki fulltext html/text/snippet 匹配 helper、Wiki catalog intent/读取/排序/格式化/reply helper、local-memory summary/context 纯 helper、runtime dedupe helper、context/company helper、analysis completion guard intent helper、financial guard/calculation trace warning/tool availability correction helper、general assistant context input helper、multi-company session context helper 和 Hermes run input text/multimodal helper；`agent_chat_runtime_impl.py` 保留 DB session memory 刷新、普通 chat/streaming 共享状态 owner 和兼容 wrapper。
- 本轮已补 `agent_runtime_postgres_fallback.py` pure helper 边界：空 hint 保持原 query、0 值页码/表号保留且不改入参、空 requested terms 不调用 payload callback、缺字段 payload 不误匹配指标；前端新增 `rendererUtils.test.ts` smoke，覆盖 citation source/table action 抽取、普通 Markdown link 保留和长引用行解析。
- 本轮已补 `agent_runtime_context.py` 脏数据防御：非 dict `model_dump` 返回值、非 dict company/report/page 字段、attachment `model_dump` 非 dict 返回值均不会破坏 format context 或附件分类。

建议拆分顺序：

1. Agent runtime `_collect_stream_run` reasoning 极小事件 helper 已完成；下一步默认停止 owner 迁移并提交清理，attachments/history/local-memory owner 拆分仍需单独窗口。
2. 若切回 PDF parser，则只补 quality/source-view 这类低风险覆盖，不动 queue、Flask response 和 `_ensure_*` 编排。
3. 只在上述纯函数稳定后，再评估 attachments/history/local-memory/build-run-input 的真实 owner 拆分。

不搬迁：

- `_collect_stream_run` 的 Hermes `stream_run` 调用、`stop_run` 自动停止、tool/delta 主循环、evidence normalization、history/dedupe/save 和 `done_payload_factory`。
- `stream_chat_reply` 编排。
- ordinary chat 与 streaming 共享的 attachments/history/local-memory/build-run-input 顺序。
- 会改变 monkeypatch 绑定语义的全局配置。

验收：

- 本轮追加覆盖：引用区空 body 插入、无效 refs 全过滤、三级引用来源 section peer/parent heading 插入边界、citation links 缺 task_id/pdf_page 原样返回、本地 API link query/fragment 保留、重复链接不追加、printed_page 空槽位与多页定位对齐。
- 本轮追加覆盖：parse-only 市场前缀伪 alias 防误匹配、非 dict task info 过滤、空 task/artifact 字段展示兜底；PostgreSQL fallback 空 hint、0 值页码/表号、空 terms callback 短路和缺字段负路径；前端 citation renderer source/table action 抽取、普通 Markdown link 保留和长引用行解析。
- 本轮追加覆盖：display None URL、无 basename path fallback、控制空白 URL 编码；tool-output None/空白/list JSON/长文本换行/tool-label 隔离；context 非 dict model/nested field 与 attachment model_dump 脏数据防御。
- 新增 `tests/test_agent_runtime_display.py`、`tests/test_agent_runtime_parse_only.py`、`tests/test_agent_runtime_tool_output.py`、`tests/test_agent_runtime_context.py`、`tests/test_agent_runtime_fallback_contexts.py`、`tests/test_agent_runtime_catalog.py`、`tests/test_agent_runtime_postgres_fallback.py`、`tests/test_agent_runtime_statement_context.py`、`tests/test_agent_runtime_financial_format.py`、`tests/test_agent_runtime_financial_guard.py` 或等价覆盖；当前 citations/display/parse-only/context/fallback/catalog/postgres/statement/financial format/financial guard 已覆盖正文已有引用去重、缺文件名附件通用 label、空白 filename fallback、kind normalization、未知 kind 文档链接兜底、空/多附件默认提示、空白 URL 与混合附件独立处理、附件 URL Markdown target 编码、query/fragment URL 编码、LaTeX inline symbol normalization、evidence trace 展示归一化调用顺序、primary-data evidence trace 判定、reference line filtering、citation 引用区插入位置与正文 metric guard、human capital / three-statement / statement table / note detail / wiki fulltext / PostgreSQL supplement renderer、three-statement record 递归迭代/期间排序/source fallback/核心记录判定/latest 选择、PostgreSQL query text/company_all/metric terms/row predicate、financial display number/per-capita/formula/table ref formatting、human-efficiency 数字解析、行数值提取、table trace/source fallback、wiki fulltext report_id 默认 file、Wiki fulltext html/text normalization、company alias 提取/剔除、fallback search terms 清洗/去重/sort、specific term filtering、line scoring、snippet 截断、PDF 页回溯、nearest table meta、Wiki catalog intent/排序/格式化/负路径、note detail direct/context statement/direct/empty guard 细边界、analysis_completed_artifacts code 兜底/负路径、analysis completion guard/general context 负路径、display 绝对 URL / 空值 URL 编码与 path filename fallback、parse-only alias/limit/context-hint 细边界、source ref 默认值/去重编号、requested metric evidence guard、financial tool availability correction、calculation trace warning、reconciliation trace guard、runtime wrapper path 注入、6 位股票代码与港股 5 位边界、短 alias 防误匹配、parse-only 大小写 fallback term、parse-only artifact 字段完整输出和跳过已有 Wiki 后再应用 `limit`。
- 当前聚焦门禁：`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_context.py tests/test_agent_runtime_display.py tests/test_agent_runtime_parse_only.py tests/test_agent_runtime_citations.py -q` 通过，72 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_statement_context.py tests/test_agent_runtime_citations.py -q` 通过，37 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_catalog.py tests/test_agent_runtime_context.py -q` 通过，20 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_*.py -q` 通过，171 passed、1 warning；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_financial_format.py tests/test_financial_calculator.py tests/test_agent_chat_runtime_loops.py::test_human_efficiency_query_appends_metric_level_sources_for_basf tests/test_agent_chat_runtime_loops.py::test_multi_company_human_efficiency_context_includes_each_company_scope_and_basf_sources -q` 通过，53 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_postgres_fallback.py tests/test_agent_chat_runtime_loops.py -q` 通过，64 passed、17 warnings；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_fallback_contexts.py tests/test_agent_chat_runtime_loops.py::test_wiki_fulltext_fallback_searches_report_md_before_document_full tests/test_agent_chat_runtime_loops.py::test_wiki_fulltext_fallback_requires_specific_terms_for_halo_goodwill -q` 通过，12 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_financial_guard.py tests/test_financial_calculator.py -q` 通过，26 passed。
- 本轮新增门禁：`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_citations.py tests/test_citation_links.py -q` 通过，52 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_*.py -q` 通过，174 passed、1 warning。
- 本轮新增门禁：`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_parse_only.py tests/test_agent_runtime_postgres_fallback.py -q` 通过，33 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_*.py -q` 通过，181 passed、1 warning；`cd apps/web && npm run check:frontend` 通过。
- 本轮新增门禁：`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_context.py tests/test_agent_runtime_display.py tests/test_agent_runtime_tool_output.py -q` 通过，40 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_*.py -q` 通过，189 passed、1 warning。
- `cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_display.py tests/test_agent_runtime_parse_only.py tests/test_agent_runtime_tool_output.py tests/test_agent_runtime_progress.py tests/test_agent_chat_runtime_loops.py tests/test_agent_chat_runtime_attachments.py -q` 通过，104 passed。
- 本轮新增 `_collect_stream_run` 接线矩阵覆盖：cancel 分支、idle/global timeout 分支、tool-loop no-progress 分支均通过 fake `stream_run` 接线测试，覆盖事件顺序、`stop_run` monkeypatch、timeout delta/error、后台保存和 ACTIVE_RUNS 清理。
- 本轮新增门禁：`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_active_runs.py -q` 通过，25 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_active_runs.py tests/test_agent_chat_runtime_loops.py -q` 通过，82 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_*.py -q` 通过，216 passed；`cd apps/api && .venv/bin/python -m py_compile services/agent_chat_runtime_impl.py services/agent_runtime_sessions.py services/agent_runtime_streaming.py tests/test_agent_runtime_active_runs.py` 通过。
- 本轮新增 reasoning 极小事件 helper 覆盖：直接 helper 测试锁定 `reasoning -> progress` 顺序、payload、`state.content` 不变；fake `stream_run` 接线测试锁定 reasoning 在 delta 前且不进入 assistant history。
- 本轮新增门禁：`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_active_runs.py -q` 通过，27 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_active_runs.py tests/test_agent_chat_runtime_loops.py -q` 通过，84 passed；`cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_*.py -q` 通过，218 passed；`cd apps/api && .venv/bin/python -m py_compile services/agent_chat_runtime_impl.py services/agent_runtime_sessions.py services/agent_runtime_streaming.py tests/test_agent_runtime_active_runs.py` 通过。
- SSE 事件字段、停止按钮、orphaned run 恢复语义不变。

### 0.35 2026-07-02 全量检查与交互/权限收口记录

本轮按“先全量检查，再补低风险闭环，再收住前端 E2E 矩阵”的节奏推进。结论：主线门禁继续保持绿色，新增改动主要是聊天/侧边栏交互与 workspace/downloads/document parser/chat attachment 的边界测试，不涉及红灯 owner 迁移。

本轮全量验证结果：

```bash
cd /home/maoyd/siq-research-engine && scripts/check_all.sh
# apps/api: 486 passed
# apps/pdf-parser: 326 passed
# apps/document-parser: 27 passed
# services/market-report-finder: 46 passed
# services/market-report-rules: 29 passed
# apps/web unit: 44 passed
# apps/web check:frontend: passed

cd packages/market-contracts && uv run pytest -q
# 2 passed

cd apps/web && npm run e2e -- --project=chromium
# 25 passed

bash -n start_all.sh && find scripts infra apps services -type f -name '*.sh' -print0 | xargs -0 -r bash -n
# passed

git diff --check
# passed
```

本轮新增后端边界保护：

- `apps/api/tests/test_document_parser_proxy.py` 补齐 document parser 共享 task 删除、最后 owner 删除和 retry usage 记录边界；锁定共享 workspace link 删除不误删 upstream、最后 owner 才代理 upstream delete、retry 成功才记录 `document_retry`。
- `apps/api/tests/test_workspace_sync.py` 补齐旧 PDF parser workspace 删除共享语义、`duplicate_filename` 复用已有 parse task、download artifact 链接入 workspace，以及 workspace 搜索中 download `pageUrl` 编码派生。
- `apps/api/tests/test_chat_document_parser_attachment.py` 补齐 chat PDF 附件提交失败和成功但无 `task_id` 的负路径，确保不记录 usage、不创建 artifact、不加入后台轮询任务。
- 新增 `apps/api/tests/test_downloads.py`，覆盖 downloads 路由非 owner 打开 403、普通 owner 删除只 unlink workspace 不删真实文件、admin 删除真实文件。

本轮新增前端 E2E 与交互收口：

- 新增 `apps/web/e2e/tests/chat-responsive.spec.ts`，覆盖 `/chat` 390/768/1440 视口无横向溢出、顶部“查看历史/删除历史”直接可见、移动端历史选择、历史无效 session 过滤空态、发送后停止生成、全局 ChatBot 打开/最小化/展开/历史弹层。
- `apps/web/src/pages/ChatPage.tsx` 移动端头部调整为头像/标题靠左、操作区靠右；移动端仅保留图标，`sm` 以上显示“新建会话 / 查看历史 / 删除历史”文字，避免导航标签进入财报问答助手时头部挤压。
- `apps/web/src/components/layout/Topbar.tsx` 顶部导航开关收口为单个纯三横线按钮：移动端控制导航抽屉，桌面控制侧边栏收缩/展开；不添加底纹、分隔或额外修饰，保持原 UI 风格。
- `apps/web/src/components/layout/Sidebar.tsx` 桌面侧边栏底部新增收缩/展开箭头；收起态顶部保留 SIQ 图标，底部箭头用于展开，移动端抽屉不显示该桌面 toggle。

当前仓库状态与风险：

- Git 索引治理仍有效：`git ls-files data` 仅剩 `data/README.md`、`data/backend/.gitkeep`、`data/pdf-parser/.gitkeep`；`var/`、`artifacts/` 仅跟踪 README / `.gitkeep`。
- 当前工作区仍有未提交改动，应按主题拆分提交：后端边界测试、前端聊天 E2E、侧边栏/Topbar/ChatPage UI 交互、以及既有 `apps/web/src/styles/chat.css` 样式改动。
- 主要剩余大文件仍是 `apps/api/services/agent_chat_runtime_impl.py`（约 6045 行）、`apps/pdf-parser/pdf_parser_app_impl.py`（约 3948 行）、`apps/api/routers/workflow.py`（约 2719 行）、`apps/api/routers/market_reports.py`（约 1458 行）。下一阶段不建议继续盲目拆红灯 owner，应先按主题提交和文档同步。

下一轮建议任务：

1. 先收束当前工作区：按“后端测试 / 前端 E2E / 导航与聊天 UI / 文档”拆提交，确保新增测试文件进入索引，ignored runtime/cache/build 目录不进入索引。
2. 低风险后端尾项：在 `apps/api/tests/test_downloads.py` 补 path traversal、绝对路径、非白名单后缀拒绝等 downloads 安全边界。
3. 控制面瘦身候选：继续评估 `apps/api/routers/workflow.py` 与 `apps/api/routers/market_reports.py` 的 service/repository 抽取，但必须先补 route contract / response golden 边界。
4. 红灯 owner 暂缓混批：Agent runtime 普通 chat/history/attachments/memory/dedupe、PDF parser Flask response / MinerU submit-poll / `_ensure_*` 编排、Document workbench refs/scroll/CSS 注入仍需单独设计窗口。

### 0.36 2026-07-03 深度复核与后续任务更新

本节按用户要求重新深度核对本方案与当前工作区事实，并已按 0.37 的后续开发结果同步最新状态。若早期 0.3-0.34 的“下一步建议”与本节冲突，以本节为准。

本地复核范围：

- Git 索引与运行态目录：`git ls-files data var artifacts`。
- 关键文件规模：`agent_chat_runtime_impl.py`、`pdf_parser_app_impl.py`、`workflow.py`、`market_reports.py`、`eval_e2e.py`。
- 工程化入口：`.github/workflows/ci.yml`、`scripts/check_all.sh`、`scripts/check_owner_migration.sh`、`scripts/check_async_db_audit.sh`、`scripts/scan_todo_fixme.py`。
- 风险主题抽样：Async DB 审计、localStorage token、Hermes/Compose、eval_e2e 行业硬编码、债务标记报告。

复核结论：

- R/B/C/F/P/A 主任务仍维持“已完成或阶段完成”。`R-001/R-002` 的运行态索引治理有效，当前 Git 只跟踪 `data/README.md`、`data/backend/.gitkeep`、`data/pdf-parser/.gitkeep`、`var/README.md`、`var/logs/.gitkeep`、`var/run/.gitkeep` 和 `artifacts/README.md`。
- `B-003` 已有 `FileBackedJobService` 和 market report jobs，且记录 `created_by`；但这仍是文件持久化线程 job，不是可取消/可重试/多 worker 的中期 worker 平台。
- API 安全线 P0 已完成：auth dependency 已迁 `AsyncSession`，tracking 权限/旧 router 隔离和 source token 独立密钥均有测试覆盖。source token 当前策略是“配置 source secret 后新 token 用 source secret 签发，默认不再接受当前 auth secret 签过的旧 source token；短期迁移需要兼容时显式设置 `SIQ_SOURCE_ACCEPT_LEGACY_AUTH_SECRET=1`”。
- 工程化产物已存在但未完全收口：`.github/workflows/ci.yml`、`docs/architecture/2026-07-02-debt-marker-governance-report.md`、`scripts/scan_todo_fixme.py` 仍处于未跟踪状态；多个 README / env / compose 文件仍是 modified。
- CI 基线阶段完成为“稳定子集 CI”：workflow 已覆盖 shell syntax、API focused 和 Web checks，shell syntax 已包含 `scripts/check_async_db_audit.sh`；PDF parser、document-parser、market-report-finder、market-report-rules 和 `packages/market-contracts` 仍保留为本地重门禁或后续矩阵扩展。
- Async DB 治理仍未完成，但 `routers/chat.py` 已从同步 Session finding 中清零；后续 0.40/0.41 又推进了 `routers/document_parser.py` 多轮迁移。当前以 0.41 的 `total 29` advisory 为准：`workspace.py` 18 条为下一主 owner，`source.py` 5 条和 `document_parser.py` 3 条为 P2，`agent_user_router.py` 2 条、`market_reports.py` 1 条后置。该审计目前只作为 advisory，不是硬门禁。
- `eval_e2e.py` 配置化阶段完成：已支持 `industry_profile`，默认 `automotive` profile 保持现有汽车 demo 兼容，新增 `generic/general` profile 覆盖非汽车输出；更多行业 profile 和 profile 配置来源仍可后续扩展。
- 前端 token 存储安全仍未完成：`apps/web/src/lib/auth.tsx` 和 `shared/api/client.ts` 仍使用 localStorage `access_token` + Bearer header；httpOnly Cookie + CSRF 仍是独立安全设计窗口。
- 债务标记治理已从“生成报告”变为“分诊报告”：`scripts/scan_todo_fixme.py` 可生成 advisory，当前报告显示 8 条 finding；分诊结论已写入 `docs/architecture/2026-07-02-debt-marker-governance-report.md`，后续不应再把“生成报告/分诊”当作未完成，只跟踪 P1 报告审核元数据和 P2 DashScope 图像 embedding 两个真实后续动作。
- Hermes 容器化与可观测性仍未完成：Compose 没有 Hermes gateway service；README 也明确 Hermes 仍依赖本机 editable venv。`monitoring` profile 只有 Grafana，不等于 Prometheus/API metrics/结构化日志基线完成。
- Python 质量工具仍未完成：API / finder / rules / contracts 的 `pyproject.toml` 尚未接入 ruff/black/mypy/pre-commit；前端已有 eslint/tsc。
- 当前主要剩余大文件：`apps/api/services/agent_chat_runtime_impl.py` 6045 行、`apps/pdf-parser/pdf_parser_app_impl.py` 3948 行、`apps/api/routers/workflow.py` 2719 行、`apps/api/routers/market_reports.py` 1458 行、`apps/api/routers/eval_e2e.py` 1380 行、`apps/api/routers/workspace.py` 1131 行、`apps/web/src/pages/SearchDownload.tsx` 961 行、`apps/web/src/components/pdf/PdfSourceWorkbench.tsx` 708 行。后续不应因为行数继续盲拆；必须先补 route / 状态机 / UI 回归合同。

更新后的后续任务池：

| 优先级 | 任务 | 当前状态 | 下一步范围 | 验收门禁 |
| --- | --- | --- | --- | --- |
| P0 | 当前工作区按主题收口 | 未完成 | 将后端测试、前端交互、CI workflow、债务标记报告/脚本、文档更新分组 review；确认 `.github/workflows/ci.yml`、`scripts/scan_todo_fixme.py` 和债务报告是否纳入索引；继续排除 runtime/cache/build | `git status --short` 可解释；`git diff --check`；相关主题测试 |
| P0 | CI 基线定稿 | 稳定子集阶段完成 | 保持 GitHub Actions 为 P0 稳定子集；如需扩大，后续单独评估 PDF parser、document-parser、finder、rules、contracts 矩阵成本 | workflow 入索引；本地 `bash -n`；API/Web CI 子集可本地复现 |
| P1 | Async DB owner 治理 | chat owner 已完成，P1 大 owner 未完成 | 后续分别评估 `document_parser.py` 和 `workspace.py` owner，不能一次性替换所有同步 `Session`；`agent_user_router.py` 2 条后置 | `tests/test_async_sync_session_audit.py`；目标 router 聚焦测试；advisory finding 不新增 |
| P1 | 控制面瘦身第二轮 | 未完成 | `workflow.py` 与 `market_reports.py` 先补 route contract / golden response，再选一个 service/repository 抽取；`workflow.py` 自有 `_workflow_jobs` 不与 market job service 顺手合并 | 新增 route contract tests；目标 router 聚焦测试 |
| P1 | `eval_e2e.py` 配置化 | 阶段完成 | 保留默认 `automotive` 兼容与 `generic/general` profile；后续只在新增行业时扩展 profile/rules 配置，不继续在路由里散落行业话术 | eval_e2e 单测覆盖默认 profile 与非汽车 profile；新增行业需补 profile 测试 |
| P1 | 前端 token 存储安全设计 | 未完成 | 单独设计 httpOnly Cookie + CSRF；先写 threat model、兼容期和回滚策略，不与 feature/API client 重构混批 | auth router tests；前端登录/刷新/登出回归；CSRF 正负例 |
| P2 | 债务标记分诊 | 报告已生成，分诊未完成 | 基于 8 条 finding 决定 issue/backlog/误报；治理报告可保留为 advisory，不接硬门禁 | `python3 scripts/scan_todo_fixme.py --root .` 输出可解释 |
| P2 | Python 质量工具渐进接入 | 未完成 | 先对新增/触碰 Python 文件启用 ruff advisory 或 touched-files 模式；不做全仓格式化 churn | CI/advisory 不阻断历史债务；无大规模格式 diff |
| P2 | Hermes gateway 容器化 | 未完成 | 设计 Compose profile 或独立 service；明确 profile 同步、env、health check 与本机 venv fallback | Compose profile 可启动；health check smoke |
| P2 | 可观测性基线 | 未完成 | 在 API/PDF parser 关键路径增加 metrics / structured log 设计；Grafana 只是展示层，不替代采集层 | `/metrics` 或等价 smoke；JSON log 样例；dashboard smoke |
| P2 | 红灯 owner 新窗口 | 暂缓 | Agent runtime `save_message` / ordinary chat、PDF MinerU lifecycle / Flask response / task state、Document CSS 注入 / refs / scroll 只能单独设计 | 先写状态/回滚矩阵，再实现一个 owner |

0.36 初始复核没有重跑 `scripts/check_all.sh` 或 Playwright 全量；0.37 后续开发已补聚焦验证。下一轮如果要提交当前工作区，提交前应至少跑 `git diff --check`、对应主题聚焦测试，并在需要时跑 `scripts/check_all.sh`。

### 0.37 2026-07-03 后续开发推进记录

本轮在 0.36 复核基础上继续推进低风险切片，优先选择可用聚焦测试锁住的工程化和配置化任务，不打开红灯 owner。

完成项：

- CI 稳定子集收口：`.github/workflows/ci.yml` 的 shell syntax 检查纳入 `scripts/check_async_db_audit.sh`；README / scripts 文档明确 GitHub Actions 是 P0 稳定子集，本地 `scripts/check_all.sh` 仍是重门禁。
- `eval_e2e.py` profile 配置化：新增 profile helper，默认 `automotive` 保持原汽车/新能源/价格战 demo 兼容；新增 `generic/general` profile，非汽车输出不再带汽车行业术语。
- Chat Async DB P0 清零：`routers/chat.py` 中两处 `next(get_session())` 内联同步会话改为 `AsyncSession` helper；streaming 完成回调使用 fresh async session；审计从 total 56 降为 54，当前无 P0 bucket。
- 测试护栏补齐：新增 `tests/test_chat_async_achievements.py`，锁定 chat achievement 更新走 async session，并覆盖 streaming done payload 使用 fresh async session；`tests/test_async_sync_session_audit.py` 同步到 54 条 finding 和新的 advisory bucket 顺序。

当前未完成但顺序已更新：

| 优先级 | 任务 | 最新状态 | 下一步 |
| --- | --- | --- | --- |
| P0 | 当前工作区按主题收口 | 未完成 | 继续按后端测试、前端交互/E2E、CI/脚本、文档拆 review；确认未跟踪文件纳入策略 |
| P1 | Async DB dependency owner | chat owner 已完成 | 下一步进入 `document_parser.py` / `workspace.py` owner 设计，不能混批 |
| P1 | CI 扩展矩阵 | 稳定子集已定 | 暂不默认扩大到 check_all 等价矩阵；如扩展，单独评估耗时和 flaky 风险 |
| P1 | `eval_e2e.py` 多行业扩展 | generic profile 已完成 | 后续新增真实行业 profile 时复用当前 profile helper，并补对应测试 |
| P1 | 控制面瘦身第二轮 | 未开始 | `workflow.py` / `market_reports.py` 先补 route contract / golden response，再抽 service |
| P1 | 前端 token 存储安全 | 未开始 | 单独设计 httpOnly Cookie + CSRF，不与 API client 或 UI 改造混批 |
| P2 | 债务标记分诊 / Python 质量工具 / Hermes / 可观测性 | 未完成 | 保持 advisory 或设计窗口，不在当前 owner 未收口前升硬门禁 |

本轮验证：

```bash
cd apps/api && .venv/bin/python -m py_compile routers/chat.py routers/eval_e2e.py tests/test_async_sync_session_audit.py tests/test_chat_async_achievements.py tests/test_eval_e2e_config.py
cd apps/api && .venv/bin/python -m pytest tests/test_async_sync_session_audit.py tests/test_chat_async_achievements.py tests/test_eval_e2e_config.py -q  # 8 passed
scripts/check_async_db_audit.sh  # total 54, no P0 bucket
bash -n scripts/check_all.sh scripts/check_owner_migration.sh scripts/check_async_db_audit.sh
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_active_runs.py tests/test_agent_runtime_chat_preflight.py -q  # 37 passed
cd apps/api && .venv/bin/python -m pytest tests/test_agent_router_attachments.py tests/test_chat_document_parser_attachment.py -q  # 12 passed
```

### 0.38 2026-07-03 财报问答助手可用性修复

本轮响应“财报问答助手无法正常问答”的故障反馈，停止继续扩大迁移，先修复普通 `/api/chat` 路由的可用性，并补专业智能体回归。

根因：

- `record_usage_async()` 在同一个 `AsyncSession` 内 `commit()` 后，会让 session 中的 ORM 对象过期。
- `/api/chat` 在记 usage 后继续使用 `current_user.id/current_user.role` 做 session 恢复与消息保存，可能触发异步懒加载异常，导致财报问答助手无法正常返回。
- PDF 附件路径也有同类风险：document artifact 写入 commit 后再次读取 `current_user.id`，会导致 usage 未写入或提交分支被异常吞掉。

完成项：

- `routers/chat.py` 在写 usage/artifact 前先捕获 `current_user_id/current_user_role`，并将认证用户对象从当前写事务 session 中分离，避免 commit 后再次懒加载。
- `services/usage_service.py` 增加 async usage helper；`routers/chat.py` 剩余 3 条 `Session = Depends(get_session)` 已迁完，chat 路由从 Async DB allowlist 中移除。
- 新增 `tests/test_chat_route_usage.py`，覆盖 `/chat` 与 `/chat/stream` 在记 usage 后仍能继续使用当前用户并正常返回。
- `tests/test_chat_document_parser_attachment.py` 迁到 async session，继续锁定 PDF 附件成功才写 usage/artifact、失败或无 task_id 不写的语义。
- `tests/test_async_sync_session_audit.py` 更新为 total 51；当前 advisory 无 P0，且 `routers/chat.py` 已无同步 Session finding。

本轮验证：

```bash
cd apps/api && .venv/bin/python -m py_compile routers/chat.py services/usage_service.py tests/test_chat_route_usage.py tests/test_chat_document_parser_attachment.py tests/test_async_sync_session_audit.py
cd apps/api && .venv/bin/python -m pytest tests/test_chat_route_usage.py tests/test_chat_document_parser_attachment.py tests/test_async_sync_session_audit.py tests/test_chat_async_achievements.py -q  # 11 passed
cd apps/api && .venv/bin/python -m pytest tests/test_agent_router_attachments.py tests/test_agent_runtime_chat_preflight.py tests/test_agent_runtime_active_runs.py -q  # 46 passed
cd apps/api && .venv/bin/python -m pytest tests/test_tracking_agent_permissions.py tests/test_tracking_runtime.py tests/test_router_history_response.py -q  # 22 passed
scripts/check_async_db_audit.sh  # total 51, no P0 bucket, routers/chat.py absent
```

### 0.39 2026-07-03 后续护栏补强与安全默认收紧

本轮按 0.38 后的工作区收口要求继续推进，只做可由聚焦测试锁住的小切片，不进入 `document_parser.py` / `workspace.py` Async DB owner 大迁移，也不打开并发 quota 计数器、PDF 批量 partial response 或前端 Cookie/CSRF 设计窗口。

完成项：

- `usage_service.py` async helper 增加直接单测：覆盖 async usage 记录、按日聚合、admin unlimited、普通用户超额错误字符串，并加入 CI API focused 子集。
- `eval_e2e.py` profile 护栏补强：新增 `input.profile` / `input.industry_profile` alias、顶层 `industry_profile` 优先级和未知 profile fallback 的单测，继续保持默认 automotive 兼容与 generic/general 非汽车输出边界。
- Source token 安全默认收紧：配置独立 `SIQ_SOURCE_TOKEN_SECRET` 后，旧 `SIQ_AUTH_SECRET_KEY` 签名的 source token 默认不再被验证；短期迁移需要时必须显式设置 `SIQ_SOURCE_ACCEPT_LEGACY_AUTH_SECRET=1`。未配置独立 source secret 的环境仍 fallback 到 auth secret，保持旧部署可启动。
- Workspace API 时间出口修正：`WorkspaceProject` 与 `UserArtifact` payload 的 `created_at` / `updated_at` 从 naive UTC datetime 序列化为 `...Z` UTC 字符串，避免前端 `Date` 按本地时区误解析；DB 内部仍保持 naive UTC，避免模型/SQLite 兼容 churn。
- README、API README、local development 文档、Docker/本地/生产 env 样例和 compose 默认值同步为 `SIQ_SOURCE_ACCEPT_LEGACY_AUTH_SECRET=0`，迁移兼容改为显式 opt-in。

仍保留为后续设计窗口：

| 风险 | 本轮处理 | 后续要求 |
| --- | --- | --- |
| usage/quota 并发下 check-then-insert 非原子 | 记录为中期风险，不在当前收口切片硬改 | 设计 per-user/event/day counter 或事务串行化，并补并发回归 |
| PDF chat attachment 多 PDF 跨额度 partial side effect | 不混入本轮 | 设计前置额度检查或 partial response 语义，再补测试 |
| chat_stream 创建 SSE 前扣 usage | 保持现有“请求即计费”语义 | 若改为成功回答后计费，需同步产品语义和失败回滚测试 |
| unknown industry profile fallback automotive | 当前测试锁定现有 fallback | 若产品要求非汽车默认 generic 或 400，需单独改 API 合约 |

本轮验证：

```bash
cd apps/api && .venv/bin/python -m py_compile routers/source.py routers/workspace.py routers/eval_e2e.py services/usage_service.py tests/test_source_access.py tests/test_workspace_sync.py tests/test_eval_e2e_config.py tests/test_usage_service_async.py
cd apps/api && .venv/bin/python -m pytest tests/test_source_access.py tests/test_workspace_sync.py tests/test_eval_e2e_config.py tests/test_usage_service_async.py -q  # 39 passed
cd apps/api && .venv/bin/python -m pytest tests/test_async_sync_session_audit.py tests/test_agent_runtime_active_runs.py tests/test_agent_chat_runtime_loops.py tests/test_agent_runtime_chat_preflight.py tests/test_auth_dependencies_smoke.py tests/test_chat_async_achievements.py tests/test_chat_document_parser_attachment.py tests/test_chat_route_usage.py tests/test_document_parser_proxy.py tests/test_eval_e2e_config.py tests/test_source_access.py tests/test_tracking_agent_permissions.py tests/test_tracking_runtime.py tests/test_usage_service_async.py tests/test_workspace_sync.py -q  # 183 passed
```

### 0.40 2026-07-03 高风险 owner 前置窗口启动

本轮在用户要求“为什么不选择一个高风险的大项目开始做”后，正式启动高风险项目的受控前置窗口。原则是：先补迁移矩阵和副作用顺序 contract，不直接盲迁 `Depends(get_session)`，不碰刚收口的 `chat.py` / `usage_service.py` / `source.py` / `workspace.py` 热点。

完成项：

- 债务标记治理完成分诊：`docs/architecture/2026-07-02-debt-marker-governance-report.md` 新增分诊结论、后续任务清单和接受的规则哨兵说明；扫描仍保持 advisory，不接硬 CI。
- `routers/document_parser.py` Async DB owner 前置矩阵启动：在 `tests/test_document_parser_proxy.py` 扩展副作用顺序、owner scope 与失败路径 contract。
  - 额度已满的 URL 创建请求不创建上游 client、不写 usage、不写 artifact。
  - multipart 上传额度不足时先 429，不读取上传文件内容，也不创建上游 client。
  - 上游 `500` 返回时透传失败响应，不扣 `DOCUMENT_PARSE_EVENT` usage，也不写 `UserArtifact`。
  - retry 上游 `500` 返回时透传失败响应，不扣 `source="document_retry"` usage。
  - retry 缺少 owner access 时先返回 `403`，即使用户当天 quota 已满也不进入 quota/upstream；owner access 存在但 quota 已满时返回 `429`，不创建上游 client、不记录 `source="document_retry"` usage。
  - `list_document_tasks` 普通用户只返回当前用户 `document_parse` link 对应任务，且支持 `artifact_key` / `global_artifact_id` 两种匹配。
  - admin 默认返回 system scope 全量任务；显式 `SIQ_DOCUMENT_TASK_LIST_WORKSPACE_ONLY=true` 时改为 workspace 过滤，不泄露其他用户或未链接任务。
  - `download_document_batch` 普通用户在所选任务全部不可访问时直接返回 403，且不创建上游 client；已有正例继续锁定“部分可访问时静默过滤后代理”的 contract。
  - `download_document_batch` admin 支持 camelCase `taskIds`，会 trim / 去重 / 过滤空值后，以 `{"task_ids": [...]}` 代理到上游，不依赖 workspace link。
  - `delete_document_task` 明确当前 contract：先删除并 commit 当前用户 workspace link，再处理上游 DELETE；最后 owner 遇到上游 `500` 或 `RequestError` 时，本地 link 仍保持已删除，不回滚。
- `routers/document_parser.py` 前置矩阵用于锁定后续 async owner 迁移必须保持的“成功后才扣量/写 artifact、前置额度失败无副作用、删除先解绑本地 link”语义；真实迁移第一刀只切只读 `/documents/quota`，不触碰 create/retry/delete 写路径。
- `workspace.authenticated_pdf_upload` 增加上游失败无副作用 contract：新文件上传在 PDF parser 上游返回 `500` 时透传失败响应，不记录 `PARSE_EVENT` usage，也不写 `UserArtifact`。
- `workspace.authenticated_pdf_upload` 增加 duplicate 满额复用 contract：普通用户当天 `PARSE_EVENT` 已满时，命中 `_pdf_tasks_by_filename()` 的 duplicate 文件仍可返回 `409 duplicate_filename`，记录 `source="reused_parse"` artifact，且不新增 usage。
- `workspace.authenticated_pdf_upload` 增加 mixed reused/new 安全子集 contract：上传列表包含已存在文件和新文件时，quota 预检只按新文件数 `increment=1`；当上游 `2xx` 只返回新任务时，只记录一条 `pdf_upload` usage 和一个 `source="new_parse"` artifact。
- `workspace.authenticated_pdf_upload` 修复 mixed `2xx` payload 分类：上游同时返回 reused task + new task 时，只按 new tasks 记录 `PARSE_EVENT` usage 和 `source="new_parse"` artifact；reused tasks 不计费，缺少本地链接时补 `source="reused_parse"` artifact，已有 artifact 不被覆盖成 `new_parse`。
- `services/usage_service.py` 新增 `usage_response_payload_async()`，`document_parse_quota` 改为 `async def` + `Depends(get_async_session)`；该 route 原本是同步 `def`，不在 async sync-session audit finding 内，因此 advisory 计数保持 51 不变。
- `routers/document_parser.py` 第二刀真实迁移新增 async access helper，并将 `list_document_tasks` 与 `source_image` 切到 `AsyncSession = Depends(get_async_session)`；对应测试改用 async sqlite seed，async sync-session audit 从 total 51 / document_parser 25 降到 total 49 / document_parser 23。
- `routers/document_parser.py` 第三刀将 13 个 GET、无 body、无本地写入的同构 proxy routes 切到 async access helper，并用参数化 async session 测试覆盖每条 upstream path；async sync-session audit 从 total 49 / document_parser 23 降到 total 36 / document_parser 10，P1 排序变为 `workspace.py` 在前。
- `routers/document_parser.py` 第四刀将 4 个 POST body 转发但无本地 DB 写入的 routes 切到 async access helper：`review_document_table_relation`、`split_document_logical_table`、`merge_document_logical_tables`、`extract_document_schema`；补齐 review/extract async seed 测试和 split/merge 参数化 body 转发测试。async sync-session audit 从 total 36 / document_parser 10 降到 total 32 / document_parser 6，`document_parser.py` 从 P1 降为 P2。
- `routers/document_parser.py` 第五刀将 `cancel_document_task` 与 `download_document_batch` 切到 async access helper；补充 cancel route 测试并将 batch download 三条 owner/admin contract 测试改为 async sqlite seed。该轮 async sync-session audit 从 total 32 / document_parser 6 降到 total 30 / document_parser 4；后续又降到 0.41 记录的 total 29 / document_parser 3。
- `tests/test_agent_chat_runtime_loops.py` 收窄 wiki catalog 数量断言，只排除旧业务文案 `一共 **121 家**`，避免临时目录名中的数字触发假失败。

后续矩阵：当前 `document_parser` owner 前置矩阵和 `workspace.authenticated_pdf_upload` mixed `2xx` 风险已收口。0.41 复核后实际剩余同步 Session finding 只剩 `create_document_tasks`、`import_document_from_mineru` 和 `delete_document_task`；下一批真实 async owner 迁移应围绕 create/import 的 quota/usage/artifact 写入，以及 delete 的先删本地 link 顺序继续一刀一验。

本轮验证：

```bash
python3 scripts/scan_todo_fixme.py --root . --max-examples 50  # total 8, advisory unchanged
cd apps/api && .venv/bin/python -m pytest tests/test_document_parser_proxy.py tests/test_chat_document_parser_attachment.py tests/test_async_sync_session_audit.py -q  # 30 passed
cd apps/api && .venv/bin/python -m pytest tests/test_workspace_sync.py tests/test_document_parser_proxy.py tests/test_async_sync_session_audit.py -q  # 41 passed
cd apps/api && .venv/bin/python -m pytest tests/test_document_parser_proxy.py tests/test_async_sync_session_audit.py -q  # 33 passed
cd apps/api && .venv/bin/python -m pytest tests/test_workspace_sync.py tests/test_async_sync_session_audit.py -q  # 19 passed
cd apps/api && .venv/bin/python -m pytest tests/test_usage_service_async.py tests/test_document_parser_proxy.py tests/test_async_sync_session_audit.py -q  # 37 passed
cd apps/api && .venv/bin/python -m pytest tests/test_document_parser_proxy.py tests/test_async_sync_session_audit.py -q  # 34 passed
cd apps/api && .venv/bin/python -m pytest tests/test_document_parser_proxy.py tests/test_async_sync_session_audit.py -q  # 48 passed
cd apps/api && .venv/bin/python -m pytest tests/test_document_parser_proxy.py tests/test_async_sync_session_audit.py -q  # 50 passed
cd apps/api && .venv/bin/python -m pytest tests/test_document_parser_proxy.py tests/test_async_sync_session_audit.py -q  # 51 passed
cd apps/api && .venv/bin/python -m pytest tests/test_agent_chat_runtime_loops.py::test_wiki_catalog_reply_reads_current_catalog_for_count_and_list -q  # 1 passed
cd apps/api && .venv/bin/python -m pytest tests/test_async_sync_session_audit.py tests/test_agent_runtime_active_runs.py tests/test_agent_chat_runtime_loops.py tests/test_agent_runtime_chat_preflight.py tests/test_auth_dependencies_smoke.py tests/test_chat_async_achievements.py tests/test_chat_document_parser_attachment.py tests/test_chat_route_usage.py tests/test_document_parser_proxy.py tests/test_eval_e2e_config.py tests/test_source_access.py tests/test_tracking_agent_permissions.py tests/test_tracking_runtime.py tests/test_usage_service_async.py tests/test_workspace_sync.py -q  # 219 passed
```

### 0.41 2026-07-03 深度复核最终校准

本节是对整份方案的最新执行锚点。若 0.1-0.40 的历史流水、测试数量或下一步建议与本节冲突，以本节和第 12 节为准。

现状事实：

- 仓库运行态索引仍干净：`git ls-files data var artifacts` 只返回 `artifacts/README.md`、`data/README.md`、`data/backend/.gitkeep`、`data/pdf-parser/.gitkeep`、`var/README.md`、`var/logs/.gitkeep`、`var/run/.gitkeep`。
- Source token 策略已确认：配置 `SIQ_SOURCE_TOKEN_SECRET` 后新 token 用 source secret 签发，默认拒绝旧 `SIQ_AUTH_SECRET_KEY` source token；只有显式 `SIQ_SOURCE_ACCEPT_LEGACY_AUTH_SECRET=1` 才兼容旧 token。
- 前端鉴权仍是 localStorage `access_token` + Bearer header；httpOnly Cookie + CSRF 未做，必须单独开安全设计窗口。
- Async DB advisory 当前为 `total 29`，全部是 `Depends(get_session)`：`workspace.py` 18、`source.py` 5、`document_parser.py` 3、`agent_user_router.py` 2、`market_reports.py` 1。`routers/chat.py` 已清零；`document_parser.py` 已从 P1 降为 P2。
- 债务标记 advisory 当前已完成报告审核 `generated_by` 来源修复；后续只剩 DashScope 图像 embedding 真实动作，Hermes legal 规则里的占位符检测文本属于接受的规则哨兵。
- 当前工作区仍有多主题改动和未跟踪工程化文件，至少包括 `.github/workflows/ci.yml`、`scripts/scan_todo_fixme.py`、`docs/architecture/2026-07-02-debt-marker-governance-report.md`；提交前必须按主题 review 和分组提交。
- 当前主要剩余大文件：`agent_chat_runtime_impl.py` 6045 行、`pdf_parser_app_impl.py` 3948 行、`workflow.py` 2719 行、`market_reports.py` 1458 行、`eval_e2e.py` 1380 行、`workspace.py` 1131 行、`SearchDownload.tsx` 961 行、`PdfSourceWorkbench.tsx` 708 行。行数只是信号，不能作为直接拆分理由。

本轮全量验证：

```bash
scripts/check_all.sh
```

通过结果：

- `apps/api`: 552 passed，468 warnings。
- `apps/pdf-parser`: 329 passed。
- `apps/document-parser`: 27 passed。
- `services/market-report-finder`: 46 passed。
- `services/market-report-rules`: 29 passed，1 warning。
- `apps/web`: Node unit 46 passed。
- `apps/web`: `npm run check:frontend` 通过，包含 lint、TypeScript build 和 Vite build。

更新后的后续任务池：

| 优先级 | 任务 | 当前状态 | 下一步范围 | 验收门禁 |
| --- | --- | --- | --- | --- |
| P0 | 当前工作区收口 | 未完成 | 按后端 Async/usage/document-parser、前端交互/E2E、CI/脚本、文档四组 review；确认 `.github/workflows/ci.yml`、`scripts/scan_todo_fixme.py`、债务报告是否入索引；排除 `dist/cache/runtime/data` | `git status --short` 可解释；`git diff --check`；主题聚焦测试 |
| P1 | `workspace.py` Async DB owner | 未完成，18 条 finding | 先补 workspace route contract，再分读路径、PDF proxy 读路径、`authenticated_pdf_upload` 写路径迁移；保留 duplicate/reused/new quota 语义 | `tests/test_workspace_sync.py`；`tests/test_async_sync_session_audit.py` finding 不新增 |
| P1 | `document_parser.py` 剩余写路径 | 阶段完成，剩 3 条 finding | 只迁 `create_document_tasks`、`import_document_from_mineru`、`delete_document_task`；保持成功后才扣量/写 artifact、失败无副作用、delete 先解绑本地 link 的合同 | `tests/test_document_parser_proxy.py`；audit 降到 `document_parser.py: 0` |
| P1 | 控制面瘦身第二轮 | 未开始 | `workflow.py` 或 `market_reports.py` 二选一；先补 route contract / golden response，再抽 service/repository | 目标 router 聚焦测试；不改 endpoint 字段 |
| P1 | Source routes Async / 安全尾项 | 未完成，5 条 finding | 先评审 `/api/source*` access token、workspace 权限和 proxy 参数剥离合同，再迁 async session | `tests/test_source_access.py`；source token 正负例 |
| P1 | 前端 token 安全设计 | 未开始 | 设计 httpOnly Cookie + CSRF、兼容期、登出/刷新、跨域和回滚；不与 UI/API client 重构混批 | auth router tests；登录/刷新/登出回归；CSRF 正负例 |
| P2 | Python 质量工具渐进接入 | 未完成 | ruff/format 先做 touched-files 或 advisory，不全仓格式化 | 无大规模格式 churn；CI/advisory 可解释 |
| P2 | Hermes gateway 容器化 | 未完成 | 设计 Compose profile、profile 同步、env、health check、本机 venv fallback | Compose smoke；health check |
| P2 | 可观测性基线 | 未完成 | API/PDF parser 关键路径 metrics 或 structured log；Grafana 只算展示层 | `/metrics` 或等价 smoke；JSON log 样例 |
| P2 | 债务分诊动作 | 分诊完成，动作未做 | 报告审核 `generated_by` 来源、DashScope 图像 embedding 设计分别开小窗口 | 对应单测或 mocked smoke |
| P3 | 红灯 owner 新窗口 | 暂缓 | Agent runtime `save_message` / ordinary chat、PDF MinerU lifecycle / Flask response / task state、Document CSS 注入 / refs / scroll | 先写状态/回滚矩阵，再实现一个 owner |

### 0.42 2026-07-03 Async DB advisory 归零

本轮接续 0.41 的 Async DB owner 队列，按“一刀一验”完成剩余 async route 中 `Depends(get_session)` 的迁移；`apps/api/tests/test_async_sync_session_audit.py` 的 allowlist 已清空，审计基线更新为 `total 0`。

完成范围：

- `routers/workspace.py`：PDF proxy 读路径、`delete_my_pdf_task`、`authenticated_pdf_upload` 迁到 `AsyncSession`；补齐 async quota / artifact helper，保留 duplicate / reused / new parse quota 语义。
- `routers/document_parser.py`：剩余 create/import/delete 写路径已迁到 async quota / usage / artifact / access helper，保持“成功后才扣量/写 artifact、delete 先解绑本地 link”的合同。
- `routers/source.py`：source access、table/page/pdf page、table correction 迁到 async 授权 helper；保留 token 剥离和 source token 复用合同。
- `routers/agent_user_router.py`：specialist chat/chat_stream 的 quota 与 usage 改用 async helper；workspace artifact background sync write 暂不混入本轮。
- `routers/market_reports.py`：US SEC upload 的 best-effort workspace link 改用 `record_user_artifact_async()`，并新增异常吞掉合同测试。

本轮验证：

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_workspace_sync.py tests/test_document_parser_proxy.py tests/test_source_access.py tests/test_agent_router_attachments.py tests/test_tracking_agent_permissions.py tests/test_market_reports_proxy.py tests/test_async_sync_session_audit.py -q  # 131 passed, 31 warnings
cd apps/api && .venv/bin/python -m pytest -q  # 557 passed, 458 warnings
cd apps/web && npm run check:frontend  # passed
scripts/check_all.sh  # API 557 passed; PDF parser 329 passed; Document parser 27 passed; finder 46 passed; rules 29 passed; Web unit 46 passed; frontend check passed
```

说明：本轮没有重跑前端 Playwright 全量；0.41 的 Playwright 基线仍作为提交前浏览器重门禁参考。

### 0.43 2026-07-03 控制面瘦身合同前置

本轮接续 0.42，但不直接抽 `workflow.py` / `market_reports.py` service，也不改 endpoint 字段；只补控制面瘦身前置 contract tests，确保后续拆薄路由时关键响应 envelope 和 proxy 行为不漂移。

完成范围：

- `tests/test_market_reports_proxy.py` 增加 `_proxy_request` 合同：多值 query、POST body、`content-type` 和 upstream response media type 必须透传；`HEAD` 响应必须丢弃 upstream body。
- `tests/test_document_workflow_package.py` 增加 `_document_workflow_status_payload` envelope 合同：顶层固定 `taskId` / `targets` / `artifacts`，`targets` 固定包含 `wiki`、`postgres`、`milvus`、`full_text`、`object_storage`，并保留通用文档 Wiki status 的关键字段。

验证：

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_document_workflow_package.py tests/test_market_reports_proxy.py -q  # 23 passed, 2 warnings
```

### 0.44 2026-07-03 通用文档 Wiki package 合同加固

本轮继续 0.43 的 contract-only 策略，不抽 service、不改 endpoint 字段。重点加固通用文档 Wiki import 的轻量包合同，确保后续拆 `workflow.py` 时不会把完整解析产物误复制进 Wiki，也不会漂移控制面响应字段。

完成范围：

- `tests/test_document_workflow_package.py` 增加 `_import_document_task_to_wiki` 响应合同：固定 `ok`、`taskId`、`collection`、`documentKey`、`packageDir`、`manifestPath`、`copiedFiles`、`copiedDirectories`、`wiki` 顶层字段。
- 固定 package `manifest.json` 的核心策略字段：`generic_document_package_v1`、`document_id`、`package_version`、`full_parse_archive`、`import_targets`、`wiki_keeps`。
- 明确轻量/重型边界：`manifest.json`、`document.md`、`quality_report.json`、`source_map.json` 复制进 Wiki；`document_full.json`、blocks/tables/relations/figures/comparison 等完整解析产物只在 manifest 中以 source/sha/size 表示，不要求复制。

验证：

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_document_workflow_package.py tests/test_market_reports_proxy.py -q  # 24 passed, 2 warnings
```

### 0.45 2026-07-03 报告审核 metadata 债务修复

本轮处理债务分诊中的 `ReportReview.generated_by` 来源问题，保持 `ReportReviewCreate` 请求合同不变，不混入 auth/token 安全改造。

完成范围：

- `routers/auth.py` 新增报告生成元数据解析 helper：优先读取同名前缀 `.json` 中的 `report_meta.generator` / `report_meta.generated_by` / `quality_report.generated_by`；缺失或 JSON 损坏时回退到旧行为 `system`。
- 同步解析 `generated_at`，支持 ISO 字符串和 `Z` 时区；无效或缺失时继续使用当前时间。
- 保留报告正文 front matter / HTML meta / 简单 JSON metadata 的兼容兜底，避免旧报告或非标准渲染产物无法审核。
- 新增 `tests/test_auth_report_review.py`，覆盖 sibling metadata、缺失 metadata、损坏 metadata 三条合同。
- 重新生成 `docs/architecture/2026-07-02-debt-marker-governance-report.md`；当前扫描为 `total 4`，安全/运行时均为 0，真实剩余动作只剩 DashScope 图像 embedding，另外 3 条是 legal 质量规则的占位符检测文本。

验证：

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_auth_report_review.py tests/test_auth_dependencies.py tests/test_auth_router_current_user.py -q  # 15 passed, 37 warnings
python3 scripts/scan_todo_fixme.py --root . --max-examples 50  # total 4; 安全 0; 运行时 0
```

### 0.46 2026-07-03 Deal OS 后端与 IC Hermes profile / policy 切片

本轮按主题收口 primary-market / IC 后端能力，不混入前端 Deal 页面、infra/env 或 Hermes 启动脚本改动。目标是先让 `/api/deals` 具备可验证的文件系统 Deal package 能力，并让 Hermes IC profiles、公开 policy 和 deal preflight 合同被 API 控制面识别。

完成范围：

- 新增 `routers/deals.py`：提供 deal 列表、创建、详情、workflow、decision、audit、manifest、IC profiles、公开 IC policy、deal preflight、OpenClaw 导入和导入 job 查询接口；路由通过既有 `require_permission` 做 `report.view` / `report.create` / `audit.view` 权限控制。
- 新增 `services/deal_store.py`：集中处理 `data/wiki/deals` 下的 deal package 路径安全、manifest/project/workflow JSON 合同、审计事件追加和 API-facing payload 脱敏。
- 新增 `services/ic_openclaw_importer.py`：把 OpenClaw IC project 的核心 phase、discussion、decision 和 audit 文件映射到 SIQ deal package；覆盖 source root 限制、symlink 拒绝、overwrite 清理和 hash manifest。
- 新增 `services/ic_policy.py`：只读加载 IC workflow policy、profile matrix 和 manifest，输出不含本地绝对目录的公开 policy / profile readiness payload。
- 新增 `services/deal_contracts.py`：只读执行 deal preflight，检查核心文件、schema、deal_id 一致性、R1 专家报告、startup retrieval receipt、evidence gate 和 R4 decision 合同。
- `main.py` 挂载 `/api/deals`；`hermes_client.py`、`hermes_model_control.py`、`path_config.py` 增加 `siq_ic_*` profile 的 alias、默认端口、兼容端口、profile root 和模型控制矩阵。
- 新增 `tests/test_deal_store.py`、`tests/test_deals_router.py`、`tests/test_ic_policy.py`、`tests/test_hermes_ic_profiles.py`，覆盖 service 安全边界、FastAPI 路由合同、OpenClaw project_id 导入、异步 job envelope、preflight 合同、公开 policy 脱敏以及 Hermes IC profile 映射。

风险边界：

- `FileBackedJobService` 当前仍是本地单进程持久 job 方案，适合本地/单 worker；多 worker 并发写 `data/backend/deals/jobs.json` 需要后续单独设计。
- `OpenClawImportRequest.metadata` 已写入 `project_meta.import_metadata` 和 `manifest.openclaw_import.metadata`，并通过 `redact_public_payload` 过滤本地路径字段；后续如需结构化字段语义再单独扩展。
- `overwrite=True` 会删除既有 deal package；API 已做 deal_id/path 安全约束，产品入口仍应做显式确认。

验证：

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_deal_store.py tests/test_ic_policy.py tests/test_deals_router.py tests/test_hermes_ic_profiles.py tests/test_job_service.py -q  # 31 passed, 14 warnings
cd apps/api && .venv/bin/python -m py_compile routers/deals.py services/deal_contracts.py services/deal_store.py services/ic_openclaw_importer.py services/ic_policy.py services/hermes_client.py services/hermes_model_control.py services/path_config.py main.py
cd apps/api && .venv/bin/python -c "import main; print(main.app.title)"  # SIQ API
```

### 0.47 2026-07-03 Deal data-room document API

本轮继续沿 Deal OS 后端小切片推进，只处理 deal package 内 data room 文档的同步文件系统读写，不接入解析 pipeline、Milvus、前端页面或异步任务。

完成范围：

- 新增 `services/deal_documents.py`：提供 document id 校验、上传落盘、metadata 写入、manifest documents 同步、列表/详情读取和删除。
- `routers/deals.py` 新增 `/api/deals/{deal_id}/documents` 的 list/upload/detail/delete 路由；读路径用 `report.view`，写/删路径用 `report.create`。
- 上传文件名只保留 basename，兼容 POSIX/Windows 路径片段；存储文件名使用 `DOC-*` id + 安全扩展名，不复用用户原始文件名。
- 上传大小超过 `SIQ_DEAL_DOCUMENT_MAX_BYTES` 时删除半写文件；API-facing metadata 继续通过 `deal_store.redact_public_payload` 脱敏，不暴露绝对路径。
- 新增 `tests/test_deal_documents.py`，并扩展 `tests/test_deals_router.py` 覆盖文档上传、列表、详情、删除生命周期。

验证：

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_deal_documents.py tests/test_deals_router.py tests/test_deal_store.py tests/test_ic_policy.py -q  # 31 passed, 18 warnings
cd apps/api && .venv/bin/python -m py_compile routers/deals.py services/deal_documents.py services/deal_contracts.py services/deal_store.py services/ic_policy.py
cd apps/api && .venv/bin/python -c "import main; print(main.app.title)"  # SIQ API
```

### 0.48 2026-07-03 Deal document parser binding 脱敏护栏

本轮只修正 Deal document parser-task 绑定的公开 payload 脱敏，不改变解析任务生命周期，也不把 Deal data room 直接接入异步解析队列。

完成范围：

- `deal_store.redact_public_payload` 将 `deleted_by`、`bound_by`、`parse_bound_by` 纳入用户字段脱敏，只保留 `id` / `username`。
- 补充 `deal_documents.bind_parser_task` 合同测试：metadata、manifest、audit 同步；parser status / result / artifact URL；artifact 存在性；非法 task id / artifact path 拒绝；绑定人 email 不进入 API-facing payload。

验证：

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_deal_store.py tests/test_deal_documents.py tests/test_deals_router.py -q  # 27 passed, 22 warnings
```

### 0.49 2026-07-03 ReportViewer report skin smoke

本轮选择独立前端维护切片，只调整 `ReportViewer` 注入到报告 iframe 的兼容主题，不触碰 Deal 页面、登录页、全局布局或 E2E 配置。

完成范围：

- `reportViewerTheme.ts` 扩展 legal / audit / status 类报告的 card、verdict、status、badge、icon、header 等颜色兼容规则。
- 明确保留 `.report-header` 不被通用 `.header` override 误伤。
- 新增 `reportViewerTheme.test.ts`，用 Node smoke 锁住关键 selector，防止后续清理时删掉 legal verdict/status 表面规则。

验证：

```bash
cd apps/web && node --test src/components/report/reportViewerTheme.test.ts  # 2 passed
cd apps/web && npm run check:frontend  # passed
```

### 0.50 2026-07-03 Deal evidence offline package builder

本轮补齐前端 DealEvidence 需要的后端合同，但仍保持 P0 本地确定性边界：只读取已绑定 document-parser 的 `document.md`，写 deal package 内 evidence index / NDJSON / quality report；不调用 LLM、Hermes agent、PostgreSQL 或 Milvus。

完成范围：

- 新增 `services/deal_evidence.py`，从 data-room 文档 metadata 和 parser Markdown artifact 构建 `evidence/evidence_index.json`、`evidence/evidence_items.ndjson`、`evidence/evidence_quality_report.json`。
- `routers/deals.py` 新增 evidence build/read/quality/item 路由，分别用于构建证据包、读取证据包、读取质量报告和按 evidence_id 查看条目。
- evidence builder 支持 DOC_BLOCK marker 的 page/block/source evidence 解析；无 marker 时按 Markdown 段落生成 deterministic evidence chunk。
- quality report 明确标注 `llm_used=false`、`agent_used=false`、`milvus_written=false`，并输出 document binding、parser artifact、verified item、dimension coverage、NDJSON validity gates。
- manifest 写入 `evidence.last_build`，audit 写入 `deal_evidence_built`；公开 payload 对 `built_by` 脱敏。

验证：

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_deal_store.py tests/test_deals_router.py -q  # 27 passed, 29 warnings
cd apps/api && .venv/bin/python -m py_compile routers/deals.py services/deal_evidence.py services/deal_documents.py services/deal_contracts.py services/deal_store.py
cd apps/api && .venv/bin/python -c "import main; print(main.app.title)"  # SIQ API
```

### 0.51 2026-07-03 Deal 工作台前端纵切

本轮在 Deal 后端、document data-room 和 evidence offline builder 已提交后，补齐前端 Deal 工作台入口；仍不混入登录页重绘、Sidebar/Topbar 移动导航、Playwright 配置或 infra/env 改动。

完成范围：

- `routes.tsx` 新增 `/deals`、`/deals/:dealId`、data-room、evidence、workflow、decision、audit 页面路由，并在主导航暴露 `交易工作台`。
- 新增 `dealApi.ts` / `dealTypes.ts`，收口 Deal list/detail/import job、document lifecycle、parser task binding、evidence build/read、workflow、decision、audit 的前端 API 合同。
- 新增 `Deals`、`DealWorkspace`、`DealDataRoom`、`DealEvidence`、`DealWorkflow`、`DealDecision`、`DealAudit` 页面，覆盖 OpenClaw 导入、项目概览、preflight、data-room 上传/绑定解析任务、offline evidence build、流程报告、投决报告和审计日志查看。
- 将 OpenClaw 导入 Deal ID 示例改为后端可接受的大写格式，避免用户按小写 placeholder 填写后被后端拒绝。

验证：

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_deal_store.py tests/test_deals_router.py -q  # 27 passed, 29 warnings
cd apps/web && npm run check:frontend  # passed
```

### 0.52 2026-07-03 认证页响应式与演示默认账号开关

本轮只处理登录/注册页视觉与响应式验收，不混入 Sidebar/Topbar、Playwright 端口策略、infra/env 或 Deal 页面。

完成范围：

- 登录页重绘为 SIQ Research Engine 双栏视觉，默认不预填账号密码。
- 新增 `VITE_SIQ_DEMO_LOGIN_DEFAULTS=1` 和 `VITE_SIQ_LOGIN_DEFAULT_USERNAME` / `VITE_SIQ_LOGIN_DEFAULT_PASSWORD` 受控演示默认值；文档明确这些值会进入前端产物，仅用于受控演示环境。
- 注册页改为更紧凑的单卡片布局，必填星号保持 `aria-hidden`，表单控件在移动/桌面下不横向溢出。
- 新增 `auth-responsive.spec.ts` 覆盖登录页不预填弱默认、注册页控件存在、移动/桌面无横向溢出和截图留档；测试定位使用 input id，避免 password role / 隐藏星号造成 ARIA 差异。

验证：

```bash
cd apps/web && npx playwright test e2e/tests/auth-responsive.spec.ts  # 4 passed
cd apps/web && npm run check:frontend  # passed
```

### 0.53 2026-07-03 工作台导航响应式与 Playwright 端口策略

本轮只收口工作台导航响应式和 E2E dev server 端口策略，不混入 infra/env、Hermes 脚本或 Deal 页面后续小改。

完成范围：

- `Topbar` 根据 `1024px` 断点区分桌面侧边栏折叠与移动端抽屉开关，修正 `aria-label` / `aria-expanded`，避免移动端关闭状态仍被读成桌面展开。
- `Sidebar` 保留底部入口的权限过滤，确保 `/settings` 只对 `system.config` 用户可见；同时移除移动抽屉里占空间的说明卡片，让底部工具入口在窄屏更稳定。
- 新增 workspace 移动端抽屉 E2E smoke，覆盖打开、关闭和抽屉位置恢复。
- `playwright.config.ts` 将 `SIQ_FRONTEND_PORT` 注入 Vite dev server，和 `PLAYWRIGHT_BASE_URL` / 默认 `15174` 端口策略保持一致；E2E README 同步说明当前 smoke 覆盖面和端口覆盖方式。

验证：

```bash
cd apps/web && npx playwright test e2e/tests/workspace-responsive.spec.ts  # 7 passed
cd apps/web && npm run check:frontend  # passed
```

### 0.54 2026-07-03 Deal evidence 筛选与溯源链接合同

本轮只收口 Deal Evidence 浏览体验和后端读取合同，不混入 Hermes 启动脚本、CI workflow 或 source token infra 文档。

完成范围：

- `/api/deals/{deal_id}/evidence` 接入 `q`、`dimension`、`document_id`、`source_url`、`limit` 查询参数，返回 `applied_filters`、`available_filters`、`matched_count` 和 `total_item_count`，避免前端筛选只停留在 UI 层。
- Evidence package reader 读取完整 `evidence_items.ndjson` 后再筛选和截断；`available_filters` 始终基于完整条目集合生成，便于前端保留所有可选维度和文档。
- `DealEvidence` 页面新增搜索、维度、文档、limit 控件，显示 quote、locator，以及 `source_url` / `artifact_url` / `parser_page_url` 三类溯源入口。
- 前端 `dealApi` / `dealTypes` 同步 evidence filter 与 available filter 合同，同时保留旧的 `fetchDealEvidence(dealId, signal)` 调用兼容。

验证：

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_deals_router.py tests/test_deal_documents.py tests/test_deal_store.py -q  # 30 passed, 31 warnings
cd apps/web && npm run check:frontend  # passed
```

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
- 当前基础门禁：`scripts/check_all.sh` 已通过，覆盖 `apps/api` 552 tests、`apps/pdf-parser` 329 tests、`apps/document-parser` 27 tests、finder 46 tests、rules 29 tests、Web Node unit 46 tests 和 `npm run check:frontend`。

### 前端 DoD

- route/nav/permission/preload 单源配置。
- PDF 和多市场解析共享 workbench。
- API client 收口。
- legacy UI 不再被新代码默认使用。
- 核心页面 Playwright smoke 通过。
- 当前基线：`npm run test:unit` 46 passed，`npm run check:frontend` 通过；此前 Chromium Playwright 25 tests 通过，0.41 未重跑 Playwright 全量。聊天、工作平台、搜索下载、PDF/文档解析关键响应式 smoke 已覆盖。后续前端主要剩少量维护型交互回归，不建议继续扩大聊天 E2E 矩阵。

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

建议后续窗口按以下节奏接力。早期 Phase 1-8 与 R/B/C/F/P/A 卡片已经大多完成或阶段完成，当前不再从“仓库治理第一步”重新开始。

1. 窗口 A：当前工作区收口。按“后端 Async DB / usage / document-parser / 前端交互与 E2E / CI 与脚本 / 文档”拆分 review，确认 `.github/workflows/ci.yml`、`scripts/scan_todo_fixme.py` 和债务报告是否入索引，继续确保 ignored runtime/cache/build/data 不进入提交。
2. 窗口 B：Async DB advisory 归零后的守护。`scripts/audit_async_sync_session.py --summary` 当前为 `total 0`，`tests/test_async_sync_session_audit.py` allowlist 已清空；后续只允许通过聚焦测试保持不回流，不在本窗口继续扩展 DB owner 语义。
3. 窗口 C：控制面瘦身第二轮的合同前置。先为 `workflow.py` 或 `market_reports.py` 补 route contract / golden response，再选择一个 owner 抽 service/repository；不因行数同时拆多个控制面。
4. 窗口 D：债务分诊动作。报告审核 `generated_by` 来源已完成；DashScope 图像 embedding 单独开小窗口；债务标记文本不作为 blanket 清理目标。
5. 窗口 E：安全与生产化设计。前端 httpOnly Cookie + CSRF、Hermes gateway 容器化、Prometheus/结构化日志、Python ruff/touched-files advisory 分别开小窗口；`eval_e2e.py` 只有新增真实行业 profile 时才继续扩展。
6. 窗口 F：红灯 owner 新设计。只有在有明确回归或收益时，才单独推进 Agent runtime `save_message` / ordinary chat、PDF MinerU lifecycle / Flask response / task state、Document CSS 注入 / refs / scroll 等高耦合 owner。

每个窗口开工前应先执行：

```bash
cd /home/maoyd/siq-research-engine
git status --short
```

如果发现与自己任务无关的改动，不要回退；只在自己的文件范围内工作。

每个窗口收尾至少执行：

```bash
git diff --check
```

涉及代码时追加对应聚焦测试；涉及全仓提交前再跑 `scripts/check_all.sh`。Async DB 当前已经归零但仍作为 advisory/防回流护栏；债务标记当前也是 advisory，不应在未完成 owner 设计前升级为硬门禁。

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
