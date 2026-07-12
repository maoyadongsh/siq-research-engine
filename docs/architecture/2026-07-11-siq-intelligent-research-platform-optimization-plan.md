# SIQ 智能投研决策平台进一步优化方案

## 1. 背景和目标

本方案面向 SIQ 从“可售卖样板闭环”升级为“国内一二级市场智能投研决策平台”的下一阶段建设。目标不是重做现有系统，而是在不改变当前前端整体框架的前提下，围绕金融分析问答准确性、智能体输出全链路可审计可回溯、生产入库稳定性、工程质量和交付门禁，继续加固现有成果。

生产回答链路的默认原则是 Wiki-first、PostgreSQL fallback：真实智能体产出优先读取 Wiki evidence package、metrics、evidence 和 report.md；只有 Wiki 查不到、文件损坏、证据坐标缺失、需要结构化聚合，或用户明确要求数据库时，才查询 PostgreSQL。Wiki/PostgreSQL 对照暂时只作为离线回测和质量诊断能力，不进入真实回答的实时生成路径。

当前项目已经具备清晰的事实生产线：

```text
官方披露 / 本地文件 / URL
  -> 多市场下载与解析
  -> document_full.json / evidence package / quality report / source map
  -> Wiki / PostgreSQL / Milvus
  -> API / Web 工作台
  -> Hermes 智能体分析、核查、跟踪、法务、投委会协作
```

截至本次检查，最关键的多市场 PostgreSQL 闭环已经形成：

- `docs/reports/market-document-full-postgres-backtest.md` 显示 `Contract status: PASS` 和 `Acceptance status: PASS`。
- HK / JP / KR / EU / US 每市场 3 个真实样本已通过 PostgreSQL 入库、重复导入幂等、同市场多样本共存、Agent view 查询和 Wiki/PostgreSQL 对照。
- 生产 Agent 查询已通过各市场 `{schema}.v_agent_financial_facts` 做确定性查询门禁。
- 前端非 A 市场的 `LLM-Wiki入库`、`Wiki语义增强入库`、`PostgreSQL入库`、`一键入库` 已完成真实点击验证。

因此下一阶段的工程主线应从“把链路补通”转为“把链路做成可审计、可回归、可发布、可扩展的投研平台能力”。

## 2. 设计原则

| 原则 | 含义 |
| --- | --- |
| 事实先于回答 | 金融数字、结论、风险提示和引用必须先绑定事实源，再允许模型组织语言。 |
| PostgreSQL 是结构化事实层 | PostgreSQL 用于确定性查询、聚合、回测和 Agent fallback；真实回答默认只在 Wiki 查不到或需补缺时使用 PostgreSQL，不做实时双源对照。 |
| Wiki 是证据资产层 | Wiki package 保存公司、报告、质量、source map、report.md、metrics、semantic、evidence 等可审计文件资产。 |
| Agent 只能消费受控事实 | Agent 的财务问答必须走路由、计算、证据、fallback 和引用规则，不能凭模型记忆回答。 |
| 前端框架不重做 | 保留当前 React/Vite、现有页面和路由形态，只抽象状态模型和按钮逻辑，不做大规模 UI 重构。 |
| A 股不破坏 | A 股 `pdf2md` 和现有入库链路继续作为基准，不为了统一而改坏旗舰样板。 |
| 多市场保留差异 | HK / US / EU / JP / KR 保留市场身份、币种、准则、taxonomy、语言和证据差异，上层消费接口统一。 |
| 重验收轻口号 | 每个优化项必须有测试、回测、报告或可观察指标，避免只写文档不形成门禁。 |

## 3. 当前系统体检结论

### 3.1 已具备的优势

| 领域 | 当前能力 | 价值 |
| --- | --- | --- |
| 产品定位 | README 已明确 SIQ 是可审计研究生产线，而不是普通聊天 RAG。 | 项目方向清晰，适合做高可信金融研究平台。 |
| 多市场事实闭环 | `document_full.json -> PostgreSQL -> v_agent_financial_facts -> backtest` 已跑通。 | 支撑 HK / JP / KR / EU / US 的结构化事实查询和 Agent fallback。 |
| A 股样板 | 000333 等 A 股 Wiki、metrics、analysis、factcheck、tracking、legal 产物完整。 | 可作为国内二级市场商业 MVP 样板。 |
| 质量门禁 | package warning/fail 阻断、force override 审计、source map、quality report 已形成框架。 | 防止低质量解析静默污染知识库和数据库。 |
| 前端触发链路 | 非 A 市场四类入库按钮已按 A 股思路统一并真实点击验证。 | 用户能从工作台完成解析、Wiki、语义增强、PostgreSQL 入库。 |
| Agent 规则 | `financial_source_routing_contract.md` 和 `financial_calculation_contract.md` 已约束来源与计算。 | 为“绝对准确”的财务问答提供了规则基础。 |
| 工程门禁 | CI 覆盖安全、配置、API、前端、parser、market eval、Playwright smoke。 | 已有可持续质量治理基础。 |

### 3.2 主要风险

| 风险 | 现象 | 影响 |
| --- | --- | --- |
| 核心文件过大 | `agent_chat_runtime_impl.py` 约 6300 行，`workflow.py` 约 3000 行，`market_reports.py` 约 2200 行；backtest runner 已从约 2800 行降至约 680 行，主要保留 CLI、汇总和兼容 wrapper。 | 回归面大、owner 不清晰、局部修复容易影响问答、入库、状态或流式输出。 |
| 生产门禁未完全 CI 化 | 多市场真实 PostgreSQL 回测很强，但主要仍靠本地强测和报告。 | 发布质量依赖人工记忆，后续多人协作容易漏跑。 |
| Agent 查询契约仍偏分散 | Hermes 规则、runtime fallback、PostgreSQL view、离线 Wiki parity 分散在不同文件中。 | 问答准确性依赖多处隐性约定，难以证明每个答案都遵循同一事实路线。 |
| 前端状态模型仍有市场差异 | `PdfWorkflowPanel` 和 `UsSecIngestionPanel` 仍分别管理入库状态和按钮文案。 | 多市场继续扩展时容易出现状态不一致、按钮可点但后端未准备好等问题。 |
| identity / selector 仍需单源化 | `task_id`、`filing_id`、`parse_run_id`、`company_id`、`document_full_path` 在不同链路各自解析。 | 可能出现“已入库但状态查询未命中”的假阴性，或跨市场误查。 |
| 生成物评审噪音 | 大型 JSON 报告和代码改动混在同一工作树。 | 审查难度上升，关键业务变更容易被生成物淹没。 |

## 4. 目标架构

### 4.1 统一事实分层

```text
Raw Disclosure Layer
  官方披露文件、PDF、HTML、iXBRL、ESEF、EDINET、DART、CNINFO

Parser Artifact Layer
  document_full.json、source_map.json、quality_report.json、table_index、content blocks

Wiki Evidence Layer
  data/wiki/<market>/companies/<company>/reports/<report_id>/
  manifest、metrics、evidence、semantic、sections、parser artifacts

Structured Fact Layer
  PostgreSQL market schemas
  pdf2md / sec_us / pdf2md_hk / edinet_jp / dart_kr / eu_ifrs
  v_agent_financial_facts、v_latest_company_reports、financial_items_enriched

Semantic Retrieval Layer
  Milvus、semantic/llm、retrieval_index、reranker

Agent Decision Layer
  Hermes profiles
  source routing、calculator、reconciliation、citation、factcheck、tracking、legal、IC workflow

Audit and Evaluation Layer
  backtest、Wiki/PostgreSQL parity、Agent query benchmark、golden answer set、release gate
```

### 4.2 Agent 财务问答执行模型

金融问答必须从“模型直接回答”升级为“受控查询计划执行”：

```text
用户问题
  -> 公司 / 市场 / 报告期 / 指标 / 口径解析
  -> 查询计划生成
  -> Wiki 指标事实查询
  -> 若 Wiki 查不到 / 证据缺失 / 用户要求数据库，再查 PostgreSQL v_agent_financial_facts 兜底
  -> 必要时 semantic/report.md 补解释
  -> 计算器 / 勾稽器执行派生计算
  -> 引用和证据打包
  -> 答案生成
  -> 事实校验和审计日志落盘
```

说明：Wiki/PostgreSQL 数值对照不在真实产出路径实时执行。对照检查保留在离线 backtest、nightly gate、release gate 中，用于发现 Wiki package 和 PostgreSQL 事实层之间的入库、单位、期间或证据差异。

每次回答应可落出一个 `answer_audit_trace`：

| 字段 | 说明 |
| --- | --- |
| `question_id` | 用户问题或会话内消息 ID。 |
| `resolved_company` | market、company_id、ticker、company_name、confidence。 |
| `resolved_period` | fiscal_year、period_end、report_id、filing_id。 |
| `query_plan` | 指标、口径、数据源顺序、是否需要计算、是否允许 PostgreSQL fallback。 |
| `wiki_facts` | Wiki metrics / evidence 命中的事实。 |
| `postgres_facts` | 仅在 fallback 发生时记录 PostgreSQL view 命中的事实。 |
| `fallback_reason` | PostgreSQL fallback 的触发原因；未触发时为空。 |
| `calculator_runs` | 单位换算、同比、占比、勾稽校验的输入输出。 |
| `citations` | page、table、row、column、bbox、quote、source_url、local_path。 |
| `guardrail_result` | 是否允许确定性回答，或是否必须声明证据缺口。 |

## 5. 优化工作流和任务清单

### 当前落盘状态（截至 2026-07-12 复审）

- `AgentFinancialFact` 契约已落到 `docs/architecture/agent-financial-query-contract.md` 和 `db/imports/financial_query_api.py`，返回保持 `rows` 兼容并新增 `agent_facts`，字段包含 `bbox`；`/query` REST 级响应已覆盖多市场 agent view 和旧 A 股 pdf2md fallback，同一契约返回，并对 legacy PostgreSQL 连接异常做泛化 503 脱敏；Hermes shared `financial_source_routing_contract.md` 已反向引用该机器契约，并通过测试锁住字段清单、`{schema}.v_agent_financial_facts` 和 `source_type=postgresql_agent_view`。
- `answer_audit_trace` 已在 Agent runtime 非流式/流式回答 guard 后写入 JSONL，并补齐 `trace_id`、`resolved_period`、fallback events、PostgreSQL fallback reason；事实字段保留原始引用行并补 `metric_name/source_page/quote` 标准别名，引用解析已覆盖千分位数值和含 `|` 的表格 quote。运行时不再向可见回答或历史消息追加“审计详情”摘要，避免审计元数据污染真实回答；主聊天和 analysis/factchecker/tracking/legal specialist chat 的非流式响应均返回 `ChatResponse.audit_trace_id`，流式 done payload 均返回 `audit_trace_id`，前端按结构化字段渲染折叠审计入口并懒加载完整 JSON。主聊天和 specialist chat 均已提供 `GET {apiPrefix}/chat/audit-traces/{trace_id}` 读取完整脱敏 trace。
- `scripts/maintenance/run_market_document_full_postgres_gate.py` 和 `scripts/ops/run_market_postgres_release_gate.sh` 已提供 contract/offline-postgres 分层门禁；PR CI 只跑 contract，contract 只要求真实样本 manifest 每市场至少 3 条清单结构和 v1 schema，不要求干净 checkout 存在未入库的本地 data 文件；manual/nightly workflow 跑 offline-postgres，并要求 self-hosted 数据环境，同时产出 `trace-offline` 与 `wiki-static` 金融 QA benchmark artifact。底层 backtest CLI 的默认 JSON/Markdown 也已改到 ignored `artifacts/eval-runs/local/`，只有显式 `--output/--markdown` 才刷新 tracked reports。
- 多市场 `{schema}.v_agent_financial_facts` 已固化为默认 latest-successful 语义：HK / JP / KR / EU / US 的最终 Agent fact view 均 join `{schema}.v_latest_parse_runs`，`v_latest_parse_runs` 只纳入 `pass/warning/completed/success` parse run；US raw XBRL facts union 也受同一 latest 约束，避免同一 filing 的历史重复入库污染 PostgreSQL fallback。
- Wiki/PostgreSQL parity 已输出机器可读分类码：`unit_display_diff`、`period_alias_diff`、`currency_label_diff`、`wiki_missing`、`postgres_missing`、`value_mismatch`，分类仅用于离线 gate 和报告，不进入实时回答路径。
- `apps/api/services/market_document_identity.py` 已承接非 A 市场 document_full identity / selector helper，import/status/Agent query scope 分别通过 `build_import_selector`、`build_status_selector`、`build_agent_query_scope` 锁住字段边界；status 查询和 document_full PostgreSQL import plan 已复用统一 identity 解析，避免 document_full 相对路径、绝对路径和 task alias 误命中，测试覆盖 `task_id`、绝对路径和 repo-relative path 三种输入都会落到同一 `document_full.json`。document_full PostgreSQL import API 返回会带上非敏感 `selector` / `identity` 摘要，便于前端、日志和 QA 直接审查本次入库实际绑定的 `market/document_full_path/task_id/path_keys`。生产 PostgreSQL fallback 会从上下文提取 `market/parse_run_id/filing_id` 并传给多市场 Agent view 查询；`financial_query_api.query_market_agent_view_result()` 会在 SQL where 层优先使用目标 `parse_run_id/filing_id`，且测试锁住 parsed scope 优先于 company hint，避免 standalone 查询误用旧 hint 污染目标 run；没有目标 scope 时才依赖 view 的 latest-successful 语义。backtest runner 的市场数据库 URL 也已复用 importer 的 hardened `market_ingestion_contract.database_url()`，避免导入和校验因泛用 `DATABASE_URL` 策略不同连到不同实例。
- 前端非 A 市场统一四阶段 `MarketIngestionPipelineState` 已落地；HK/JP/KR/EU generic PDF 和 US SEC 都按 `artifacts -> wiki -> semantic -> postgres` 派生按钮与卡片状态，action key 已统一为 `runAll/wiki/semantic/postgres`，generic PDF 层再映射到既有 `wiki-import-generic/semantic-generic/db-import` 执行函数。PostgreSQL 状态展示已统一到 `parse_runs/facts/tables/chunks/evidence/schema/parse_run_id`，ready 口径收紧为五项计数均大于 0；后端 `missing_counts` 也按同一五项口径返回，避免前端遗漏 `parse_runs` 或 `facts` 缺口。US SEC case-set status 已透传 `full_document_paths/parser_result_dir/parser_result_task_id`，避免最近任务误判缺 `document_full_path` 并错误禁用 PostgreSQL/一键入库；缺 `document_full_path` 时按钮前置禁用并显示可见原因，不再只依赖 hover title。按钮背后的 API payload 已用 fetch stub 和 Playwright 行为测试锁住：HK/JP/KR/EU generic PDF 继续发送 `{ market, task_id, ddl: true }`，US SEC 继续发送 `{ market: 'US', document_full_path, ddl: true, force: false }`。
- 金融 QA benchmark v1 已落到 `datasets/eval/financial_qa_benchmark/v1/` 和 `scripts/maintenance/run_financial_qa_benchmark.py`；PR CI 使用 `trace-offline` 预录 `answer_audit_trace` 做确定性门禁，不调用 LLM、不连接 PostgreSQL。当前 P0 覆盖 CN/HK/US/JP/KR/EU，含 12 条 trace case、9 个关键事实、1 个 calculator run、1 个证据缺失拒答，以及工商银行营业收入数值错配、跨公司同值身份错配和伪造 calculator marker 三类攻击；除 case schema、resolved identity、key fact、精确 evidence、source policy、calculator trace 和 guardrail refusal 外，攻击 case 还要求精确 guardrail reason 与 claim/identity/trace validation 字段。当前 trace-offline 12/12 PASS，key fact 与 evidence coverage 均为 1.0。当 case 显式 `source_policy.allow_postgres_fallback=false` 时，trace 中出现 `postgres_facts` 会直接失败；单测已用当前 `agent_runtime_answer_audit.build_answer_audit_trace()` 生成 trace 反喂 benchmark，并新增 fake live runtime smoke 覆盖 `_collect_chat_reply_impl -> answer_audit_trace.jsonl -> trace-offline benchmark`，防止 evaluator 只适配静态黄金样本。`wiki-static` 当前覆盖 7 条 document_full fact case，用于本地检查 Wiki fact fixture 漂移。case `modes` 语义已在 dataset README 和 schema 单测中锁住：缺省代表全部已实现确定性模式，显式列表用于缩小运行模式，保留的 `postgres-fallback` 在 evaluator 落地前会被拒绝。
- P3 核心文件瘦身已开始采用“小刀切分”方式推进：`agent_runtime_context.py` 继续承接纯意图 helper，已下沉 goodwill 主表查询、statement-with-goodwill、direct-statement-with-goodwill 判断和 Hermes run input payload 选择；`agent_runtime_attachments.py` 已从 facade-only 变成真实 owner，承接附件安全路径、图片多模态预处理、PDF 附件等待/上下文、历史附件抽取和 ChatMessage 附件可见性契约，runtime 仅保留兼容 wrapper；`agent_runtime_wiki_context.py` 已承接 Wiki report 选择、company artifact paths、company scope prompt、document_full 全文检索、wiki fulltext fallback result/render 编排，runtime 保留同名 wrapper 注入 profile-aware `WIKI_ROOT`、resolver、JSON reader 和 evidence URL；`agent_runtime_postgres_fallback.py` 已承接 financial_query_api loader、fallback 适用性 predicate、query parse、metric term predicate、financial query connection factory、multi-market agent view fallback、legacy metric row query、`pdf2md.document_tables` 页码补全、完整 `_postgres_fallback_result` 编排、`build_postgres_fallback_context` 薄编排和 fallback audit event/context helper；`agent_runtime_financial_sources.py` 已承接主要数据证据补充的来源调度、Wiki metrics 引用归一化和 Wiki/PostgreSQL fallback 编排；`agent_runtime_financial_guard.py` 已承接金融证据合约 fallback / invalid task_id / enforce 编排；`market_report_status_service.py` 已承接 market package load-plan summary、quality gate merge 规则、package quality response 组装、package list payload 过滤/排序/截断契约、US SEC package detail response 和 document_full status payload；`market_report_commands.py` 已承接 US SEC ingest `tickers/batch_tag` 过滤参数规范化与校验、US SEC upload 文件名清洗 / content-type 后缀推断 / build-compatible metadata payload 构造，以及 market package force audit 字段解析、脱敏、过期校验和 quality gate decision；`market_report_package_service.py` 已承接 market package build/import/vector ingest、list/detail/quality/file/evidence route payload、US SEC latest case selector、case-set status payload、package detail by ticker、semantic company-dir 解析、semantic pre-step、case-set ingest 和 rebuild package 命令编排，router 仅保留依赖注入、FileResponse 和 HTTP error 映射；`market_report_assist_service.py` 已承接市场报告 assist 的 JSON 抽取、候选压缩、prompt/user payload、Hermes mode 判断和规则/LLM merge 纯逻辑；`market_report_eval_service.py` 已承接 market ingestion eval 的 plan/args/命令执行/报告读取调度，router 仅保留 HTTP error 映射和 queue/wait wrapper；`market_report_postgres_service.py` 已承接 document_full PostgreSQL import 命令/env/identity/metrics 编排和 import/status payload 组装，router 仅保留 HTTP error 映射、配置注入与 queue/wait wrapper；`market_report_queueing.py` 已承接 wait/queue 分流和 job status 对外投影，router 仅保留 job service 注入与 HTTP error 映射；`agent_chat_runtime_impl.py` 已删除 PostgreSQL fallback audit/context 的纯转发 wrapper，直接复用 `agent_runtime_postgres_fallback`；`agent_chat_runtime_impl.py` 和 `market_reports.py` 仍保留必要同名 wrapper 兼容既有调用点。
- 本轮继续把 Agent runtime 的 recent completed-run 幂等记忆下沉到 `agent_runtime_dedupe.py`：owner 现在持有 `RecentRunRecord`、`RECENT_COMPLETED_RUNS`、recent duplicate fallback、forget 和 remember 逻辑，runtime 只保留兼容 wrapper 并共享同一个全局字典，避免 CI/单测 monkeypatch 契约漂移。
- 本轮继续把 Agent runtime 的 task_id 证据链路径判断下沉到 `agent_runtime_task_ids.py`：owner 承接 task_id 格式识别、PDF2MD result/output 目录存在性、Wiki company/report manifest 命中、reply 中无效 task_id 抽取；runtime 保留同名 wrapper 并注入 PDF2MD roots、profile-aware `WIKI_ROOT` 和 company resolver，避免改变金融证据 guard 的外部契约。
- 本轮继续把 Agent streaming 的工具事件状态投影下沉到 `agent_runtime_streaming.py`：owner 承接 `tool.started/tool.completed` 对 active-run counters、progress payload、state-event payload、重复工具调用和连续工具错误阈值的纯状态计算；runtime 只负责流事件消费、stop_run、delta/error 写入和历史持久化，未触碰 PDF/MinerU/document-parser 解析主链路。
- 本轮继续把 Agent runtime 的展示投影下沉到 `agent_runtime_display.py`：owner 承接 ChatMessage 历史 payload 的可见过滤、时间顺序恢复、limit 截断和 `audit_trace_id` 保留；runtime 仍负责数据库查询和会话作用域，只把已查询出的消息交给 display owner 投影，避免把存储访问或解析产物发现塞进展示层。
- 本轮继续把 Agent runtime 的通用运行态 progress 文案下沉到 `agent_runtime_progress.py` / `agent_runtime_streaming.py`：任务启动、重复输出、工具循环、工具连续失败、terminal failed/cancelled、timeout、runtime exception、用户停止、完成、reasoning、orphan heartbeat 均由 owner 生成稳定 payload；runtime 保留的直接 `_progress_payload` 仅限聊天 PDF 附件等待 MinerU 独立解析这一解析链路提示，后续随解析链路单独拆分。
- P3 backtest runner 拆分已启动：`db/imports/backtests/document_fact_normalizer.py` 已承接 document_full 事实归一化、identity、证据判断、数值比较和稳定内容 hash；`db/imports/backtests/contract_cases.py` 已承接 fixture case 合约、断言检查和断言统计；`db/imports/backtests/agent_view_parity_helpers.py` 已承接 Agent view / Wiki-PostgreSQL parity 的纯比较、diff code 汇总和自动问题生成；`db/imports/backtests/agent_query_gate.py` 已承接 `v_agent_financial_facts` 固定问题查询和真实样本 parse_run 探针；`db/imports/backtests/wiki_postgres_parity_gate.py` 已承接 Wiki/document_full 与 PostgreSQL Agent view 离线对照，并保留显式断言 hard fail、自动生成对照 warning 降级语义；`db/imports/backtests/postgres_roundtrip_helpers.py` 已承接 PostgreSQL relation/table helper、作用域 selector、表族/表计数、content hash 和 required-evidence 检查；`db/imports/backtests/postgres_roundtrip_gate.py` 已承接 `check_db_case`、`check_db_case_sequence`、DB 导入、DDL 首次执行和幂等编排；`db/imports/backtests/production_sample_gate.py` 已承接真实样本 manifest v1 校验、路径解析、真实样本 case 生成和真实样本 parse_run 共存检查；`db/imports/backtests/report_writer.py` 已承接 JSON/Markdown 报告输出，runner 保留同名 wrapper，release gate 行为不变。
- P5 首版运行态可观测性已落地：API middleware 会记录 `siq_api_request_total` 与请求耗时 sum/count/max，`/health` 返回 uptime、请求数、错误数和 answer trace 数，`/metrics` 暴露 Prometheus text 格式；`answer_audit_trace` 写入时会同步累计 `siq_agent_fact_source_total`、`siq_postgres_fallback_reason_total`、`siq_answer_guardrail_block_total`、calculator run 和 citation 总数；document_full PostgreSQL import/status 入口已记录 `siq_ingestion_duration_seconds_*`、`siq_ingestion_fact_count` 和 `siq_frontend_pipeline_job_failure_total`，eval report 读取时会把离线 Wiki/PostgreSQL parity warnings 计入 `siq_wiki_postgres_parity_warning_total`；后台 `FileBackedJobService` 会在异步任务 succeeded/failed 终态记录 `siq_background_job_final_state_total` 和 `siq_background_job_duration_seconds_*`。该层目前为进程内轻量指标，不引入外部 Prometheus SDK。

### 2026-07-12 M1-S 实施收口结果

本轮按复审列出的 7 个 P0 发布阻断项完成代码、CI 和行为验收，结果如下：

| 阻断项 | 收口结果 | 权威证据 |
| --- | --- | --- |
| 金融事实无证据硬阻断 | 已完成。无 Wiki / PostgreSQL / calculator 确定性证据时返回结构化阻断回答，trace 写入 `guardrail_result.blocked=true`。 | `test_agent_runtime_financial_guard.py`、`test_agent_runtime_answer_audit.py`、`test_agent_runtime_postgres_fallback.py`。 |
| ChatContext / fallback event 归一化 | 已完成。非流式和流式 runtime 均将 Pydantic / mapping context 复制为可变 dict，fallback event 与 resolved identity 不再因类型边界丢失。 | `agent_runtime_context.mutable_context_dict()` 及 runtime 定向回归。 |
| API CI 全量执行面 | 已完成。PR CI 运行 `apps/api/tests` 下全部非 `slow/network` 测试；机器审计会列出发现、执行、排除和未执行文件，普通测试文件未进入 CI 时硬失败。真实数据引用测试使用显式 `slow` marker，不再混入 PR quick gate。 | `check_api_ci_test_coverage.py --fail-on-uncovered` 当前覆盖 92/92 个 API test files；全量 API quick gate 通过。 |
| Market ingestion eval 硬门禁 | 已完成。`--strict` 对 0 case、fail、missing package 和 block decision 返回非零；PR CI 使用 `eval_datasets/market_ingestion_contract/` 的可提交合成 HK package 和显式 `--wiki-root`，不依赖本机 `data/wiki`。真实 16 个 MVP case 保留给本地/nightly 数据环境。 | portable contract fixture 1/1 PASS；失败 fixture 单测证明 missing/block 必须失败。 |
| PostgreSQL gate 边界与 DDL authority | 已完成。runtime authority 固定为 HK/JP/KR/EU/US 五份 checked-in additive DDL，CN/A 股明确排除；结构化 contract 锁住 `parse_runs`、`v_latest_parse_runs`、`v_agent_financial_facts` 和 latest-successful join，runtime DDL 禁止 `DROP SCHEMA`。offline gate 通过 `SIQ_MARKET_POSTGRES_SAMPLE_ROOT` 读取 checkout 外只读真实样本，并在连接 DB 前一次列出全部缺失文件。 | `test_market_ingestion_contract.py`、production sample preflight tests、contract gate PASS。 |
| 前端 task/package scope | 已完成当前 PDF/US 工作台范围。generic PDF result/quality/workflow/source 请求按 task + generation token 隔离；US detail/rebuild/PostgreSQL payload 按 package/accession/document_full identity 隔离，同 ticker 多 filing 的迟到响应不能覆盖新选择。 | Web unit 251/251、US Playwright 4/4、generic task-switch Playwright 8/8、frontend build PASS。 |
| 安全止血 | 已完成当前 P0。Milvus/MinIO 凭证改为必填环境变量且端口默认 loopback；API 启动日志只输出 DB URL `configured/not configured`，不再输出 URI 任一片段；force-tracked runtime artifact 和数据库 dump/backup 后缀进入 changed-file hard gate。 | security hygiene、startup guard、container config、large-file gate tests 全部通过。 |

同时补齐了历史审计入口：`ChatMessage.audit_trace_id` 采用 nullable additive migration，非流式/流式最终回答持久化同一个稳定 trace ID，history API 和前端 store 在刷新后仍保留结构化审计入口；旧消息不回填且保持 `null`。API CI 执行审计在新增 specialist/pg_query 回归后覆盖 94/94 个 test files，全量 quick gate 为 1253 passed、3 deselected。

Touched-files Ruff 已进入“仅新增 fingerprint 失败”的 fail-fast：CI 和 pre-commit 固定使用 Ruff 0.14.10，以 Git base ref 中 touched 文件的原始内容现场生成差分基线；约 296 个历史告警不会阻断，但新增的 `path + code + message + normalized source range` fingerprint 或同 fingerprint 新增出现次数会失败。无效 base ref、Ruff 缺失或 Ruff 工具错误均硬失败，JSON artifact 保留基线/当前/新增计数及 SHA-256 fingerprint，避免浅克隆或工具降级造成空跑。当前共享工作树相对 `origin/master` 的实跑结果为 baseline 319、current 291、new 0。

### 2026-07-12 M1.5 / M2 / M3 增量实施结果

| 里程碑能力 | 当前结果 | 验收证据 |
| --- | --- | --- |
| Specialist artifact contract | factcheck / tracking / legal 已统一 `siq_specialist_artifact_v1`，包含 `artifact_type/company_id/source_report_path/output_path/html_url/citations/validation_result/audit_trace_id`；claim verdict、tracking module/PostgreSQL query facts、legal facts 进入同一 answer audit。validator 失败只保留 `_drafts` 诊断产物且不暴露公开 URL；同步响应、流式 done 和历史消息持久化同一 trace ID。 | specialist 定向矩阵 55/55；API quick gate 1253/1253。 |
| Hermes PostgreSQL 只读助手 | `pg_query.py` 增加市场事实 schema allowlist、单语句只读检查、500 行上限、30 秒 timeout 上限和稳定 `error_code`；assistant/factchecker/tracking 指引显式使用受控 helper，且不再包含历史实例地址。 | `test_hermes_pg_query.py` 16/16，含 CLI 退出码/JSON 错误契约。 |
| EU generic 非 PDF package build | EU 下载列表中的 ESEF ZIP / iXBRL / XHTML / HTML / XML / XBRL 可通过受控 `download_relative_path` 生成 package；状态按 `EU::download:<relativePath>` 和 request id 隔离。HK / JP / KR 继续 PDF-only，非 PDF 显示明确原因且不出现 build 操作。 | Web unit 255/255、frontend build PASS、Playwright 14/14。 |
| Ruff 差分 fail-fast | CI/pre-commit 使用 Git base snapshot 和 SHA-256 fingerprint 比较；历史告警、行号漂移、纯 rename 不阻断，新规则/新增重复/新文件告警、无效 base 或工具错误硬失败。 | 脚本测试 11/11；真实工作树 `new_finding_count=0`；actionlint PASS。 |
| 数据库性能 quick wins | `UsageEvent` 日额度统计由拉取 ORM rows 改为 SQL `SUM()`；新增 `(user_id,event_type,event_date)` 和 `ChatMessage(session_id,created_at)` 复合索引，启动迁移对旧库幂等补索引。 | usage/migration 7/7，相关 API 回归 73/73。 |
| Offline parity 分级 | generated parity 仅允许 `unit_display_diff/currency_label_diff/period_alias_diff` 在达到最低命中数后降级；`value_mismatch/wiki_missing/postgres_missing/evidence_missing` 和未知错误保持 hard fail，显式 assertion 始终 hard fail。 | parity/normalizer 定向矩阵 29/29。 |
| 长任务 workflow 可见契约 | workflow job 统一记录 `currentStep/failedStep/retryScope`；每个 step 从命令结果中抽取 `commandResults/stdoutTail/stderrTail/timeoutSeconds`，命令超时返回稳定 `returnCode=124/timedOut=true`；前端工作台展示当前 step、失败 step、retry scope、timeout 和 stdout/stderr 摘要。失败的 remaining pipeline 会把当前 step 标成 failed，不再停留在 running。PDF generic 与 standard workflow 的 action/run-all disabled reason 已统一进入状态模型和面板可见提示，不再只藏在按钮 title。 | workflow job/subprocess 定向 28/28；相关 API 矩阵 141/141；Web unit 256/256；`npm run check:frontend` PASS。 |
| mypy 白名单门禁 | 根级 `mypy.ini` 与 `scripts/maintenance/mypy_whitelist.toml` 已落地，首批覆盖低耦合 maintenance quality/performance gates；CI/pre-commit 使用 `check_mypy_whitelist.py --require-mypy` fail-fast，本地无 mypy 时可 advisory 输出，避免假绿。 | `uv run --with mypy==1.14.1 python scripts/maintenance/check_mypy_whitelist.py --require-mypy --json` PASS，当前 6 个 source files；maintenance gate tests 21/21；Ruff touched-files gate `new_finding_count=0`。 |
| Contract / nightly performance baseline | `run_performance_baseline.py` 分层为 PR-safe `contract` 与 self-hosted `nightly`。contract 跑 market ingestion strict fixture、document_full PostgreSQL contract fixture、market evidence chunk builder；nightly 接入 release gate，带 `--require-nightly-inputs`，在真实样本根目录和已导入 market DB 上产出 production sample file stats、`document_full.json` load/RSS、PostgreSQL `v_agent_financial_facts` query latency P50/P95/P99。nightly 还新增 `agent_memory_embedding_throughput` 与 `agent_memory_milvus_retrieval_latency` 两个向量探针：默认缺 embedding endpoint / `pymilvus` / Milvus collection 时只记录 skipped reason，不阻断 release；release wrapper 已支持 `SIQ_AGENT_MEMORY_VECTOR_PROBES_REQUIRED=1` 透传 `--require-agent-memory-vector-probes` 升级为 hard gate，也支持 `SIQ_AGENT_MEMORY_VECTOR_PROBES_SKIP=1` 显式关闭，并通过 self-hosted workflow env 读取 embedding/Milvus/cases/top-k 配置。`agent_memory_vector_preflight.{json,md}` 会在 offline-postgres release gate 中输出脱敏健康报告，包含 endpoint 是否配置、`pymilvus` 是否可用、Milvus connectivity、collection 是否存在和 required fields 是否完整；当 seed 或 required probes 打开时，Milvus 不可达会 hard fail。`eval_datasets/agent_memory_retrieval_contract/cases.json` 固定 3 个与 seed profiles 对齐的可提交 retrieval cases；未显式配置时 wrapper 默认用该 case 文件，并默认 seed `siq_assistant,siq_ic_legal_scanner,siq_ic_chairman` 三个 profile。`SIQ_AGENT_MEMORY_VECTOR_SEED=1` 会在 nightly performance baseline 前运行 Hermes profile → embedding → Milvus seed，写入 `agent_memory_milvus_seed.{json,md}`；非 dry-run seed 后还会写 `agent_memory_vector_post_seed_health.{json,md}` 并强制 `--require-milvus --require-collection`，确保 collection/schema 已就绪后才跑 retrieval probe。release seed 固定要求显式 embedding endpoint，缺失时生成 `error_type=embedding_endpoint_not_configured` 的失败 artifact，不回退 localhost；seed/health 摘要只记录 collection/chunk/inserted/error_type/布尔健康状态，不写 endpoint/token/host。PR 不依赖真实 `data/wiki`、Milvus、embedding、Parser 或 PostgreSQL；向量探针报告不写 endpoint、query、hit titles/source paths 或 token，wrapper 也不把 embedding endpoint 放入 CLI。 | contract/nightly local smoke PASS；`test_run_performance_baseline.py` 12/12；`test_run_market_document_full_postgres_gate.py` 16/16；`test_ingest_agent_memory_to_milvus.py` 3/3；`test_check_agent_memory_vector_health.py` 3/3；agent-memory retrieval fixture JSON valid；CI market-eval 与 self-hosted release gate artifact 已接入；mypy 6/6；Ruff touched-files gate `new_finding_count=0`。 |

本轮完成了 M1.5 的核心契约、M2 的 generic 非 PDF 关键缺口和长任务可见契约，并把 M3 的 Ruff/SQL quick wins、mypy 白名单 fail-fast、PR-safe contract baseline、self-hosted nightly performance baseline、可选 Milvus/embedding 性能探针、显式 seed artifact、agent-memory retrieval contract fixture、脱敏 vector preflight artifact 与 post-seed collection/schema hard gate 接入 CI/release artifacts；release gate 现在已有显式 required/skip/seed/env 透传，并有默认可提交 cases/profiles。总体优化目标仍保持 active，下一阶段继续在实际 self-hosted runner 上固化 Milvus 服务可达性，再处理核心大文件 owner 化拆分。

### 2026-07-12 生产发布阻断复审补充

其他窗口的只读审查结论显示：当前工作树不应进入生产发布。前文的“M1-S 已完成”和“M1.5 / M2 / M3 增量实施结果”只代表内层函数、contract fixture、局部 E2E 或开发环境门禁已形成，不能等同于生产容器、真实 HTTP 边界、双用户负向攻击用例和金融事实逐 claim 校验已通过。

因此发布状态临时调整为 **RED / blocked for production release**。后续所有“已完成”定义必须同时满足：

1. 内层单元测试或 contract fixture 通过。
2. 真实 HTTP API 使用当前鉴权中间件、当前用户、当前权限模型通过。
3. 至少一个负向攻击用例失败在预期位置，例如 BOLA、低权限写入、跨市场 fallback、SSRF、错误金融 claim。
4. 生产容器路径通过 smoke，而不是只通过 Vite dev server 或本地函数调用。
5. 对金融数值类功能，验收对象必须是 `company + filing/report + metric + period + value + unit + quote/evidence_id` 的完整 claim，不再只验证 task id、page/table 或引用格式。

#### 重新打开的 P0 发布阻断项

| 优先级 | 问题 | 当前评估 | 必须补齐的工程动作 | 发布验收 |
| --- | --- | --- | --- | --- |
| P0 | Deal OS 对象级越权 / BOLA | `private` Deal 仍可能只靠全局 `report.view/report.create` 权限访问；列表、详情、报告和文档写入需要按 Deal owner / member / role 再授权。 | 建立统一 `require_deal_access(deal_id, action, user)`；list 只返回用户可见 Deal；detail/report/document write 均调用同一 guard；审计记录 actor、deal_id、action、decision。 | 双用户测试：viewer 不能枚举他人 private deal；analyst 不能修改他人 deal；owner/member 可按角色访问；API HTTP 层返回 403/404 且不泄露对象存在性。 |
| P0 | `/workflow` 任务越权和低权限写操作 | 当前 workflow 路由主要依赖登录态，Wiki、语义增强、DB import、run-all 等可能对任意 task id 触发。 | 读操作绑定 `UserArtifact` / task ownership；写入要求 `report.create`；DB import、配置、运维动作要求 `system.config` 或更细粒度权限；所有 job enqueue 记录 user/task scope。 | 双用户 HTTP 测试：低权限用户无法读/写他人 task；viewer 不能触发 semantic/wiki/db-import/run-all；越权请求不会创建后台 job。 |
| P0 | 银行营业收入指标错配 | 已完成解析修复与真实长链路验收：`operating_revenue` 不再吞掉“利息净收入”；旧 offline golden 保持 PASS。修复 sparse-page backfill 后重新执行408页不中断 `live-http`，932秒完成，raw三路408页、最终结构284/141/973/33/30、四项值/来源/checks/quality全通过；raw与PDF API final Markdown分层记录。 | 保持解析链路单独修复，不通过 Agent runtime 包装绕过；继续扩展银行 canonical；self-hosted `live-http` 任何 timeout/缺产物/指标或质量错配继续 hard fail。长测必须写 checkpoint，未取得 terminal/result evidence时不得删除本地task；候选审批必须绑定用户实际取得的final artifact，不能用raw Markdown SHA冒充。 | Offline golden、不中断 `live-http` 运行稳定性、final baseline 显式审批参数复验和更新 manifest 后的 recovered-result 均 PASS：营业收入8,382.70亿元、利息净收入6,351.26亿元、`financial_checks.overall_status=pass`、无quality flags；该 final artifact 完成显式参数复验，但没有宣称获得独立 final artifact 审批；version `icbc-2025-mineru-3.1.2-v3`、final SHA `0bf82c...a432`、source line 433/table index 3。 |
| P0 | 多市场 ResearchIdentity 未贯通 HTTP 边界 | 本地产品链路已贯通：`ResearchIdentity` 进入 ChatContext/runtime/Hermes input/PostgreSQL/audit/history；完整 `company_id` 是 catalog 权威选择器，session 切换完整 identity 会刷新旧 scope；Wiki exact selector 复用 manifest 嵌套产物路径并输出 canonical company id。Wiki/report API 只在显式 company/report/manifest 四字段完整且无冲突时返回 identity，前端不猜测；`ChatMessage.research_identity_json` 与 `agent_memory.messages.research_identity_json` 均保存 user/assistant 逐消息不可变快照，旧行、部分身份或坏 JSON 返回 `null`，不回退到可变 session metadata。 | 保持单一 identity 契约；继续回填旧 Wiki 产物缺失的 `filing_id/parse_run_id`，并贯穿外部生产 Milvus/pgvector metadata/filter；不得从 ticker、展示名、task id或目录猜 identity。 | 五市场 Bearer TestClient 矩阵证明请求序列化、auth、Hermes input、真实 Wiki `identity_exact` selector、guard、audit API 与 SQLite history；同 session A→B 历史为 A/A/B/B，旧行 `null` 兼容。Agent Memory schema/write 回归证明完整四字段写 JSONB、缺失/部分身份写 SQL NULL。Hermes 为受控 stub，不是生产模型 E2E。剩余是旧产物权威字段回填、外部生产 collection 迁移、真实全市场 Wiki/Milvus E2E 和 pgvector 数据规模验证。 |
| P0 | 非 A 市场 miss 后误入 A 股 legacy PostgreSQL fallback | 已完成 fail-closed：明确 HK/JP/KR/EU/US 的 market view miss 不进入 legacy；缺完整 identity 时连 market-only latest view 和 Wiki primary/latest report 都不查询。 | 保持 CN/unknown legacy 兼容与 non-CN 边界；后续把同一 identity 过滤扩到 Milvus/pgvector，并保持 audit reason 稳定。 | 单测和真实 `main.app` HTTP trace 覆盖 HK/JP/KR/EU/US：market view=0、legacy=0、unsafe Wiki builders=None；trace 记录 `market_agent_view_skipped_for_incomplete_identity` 与 `legacy_fallback_skipped_for_non_cn_market`。 |
| P0 | 金融证据门禁只验引用格式，不验 claim 内容 | 已完成确定性 claim 内容、HTTP/auth、identity 和计算 trace 语义门禁：错误数值/期间/币种被阻断；每条 Wiki/PostgreSQL 来源必须显式匹配完整 `ResearchIdentity`；calculator/reconciliation 只接受版本化 JSON envelope，并重算输入/输出、单位/币种、指标角色和 evidence_id，旧工具名/标题文本不能放行。Controlled Hermes gateway compose 已打通真实密码用户→Web chat→create/SSE→guard→audit/history/PostgreSQL；这是 stub 编排契约，不是 live model。 | 保持 deterministic verifier、证据身份和结构化计算 trace hard gate；继续扩展跨句、省略主语、表格多期间和复杂中英自然语言。生产模型攻击集必须复用同一 benchmark/audit 断言，并在配置真实 Hermes token/model 后执行 live 攻击。 | ICBC `value_mismatch`、跨 identity 同值和伪 calculator marker 攻击进入 12-case benchmark，trace-offline 12/12 PASS，key fact/evidence coverage 均为 1.0；五市场 exact identity HTTP 矩阵 PASS，audit 记录具体 mismatch/trace reason。剩余是真实 Hermes/live model 推理攻击和外部生产代理链路。 |
| P0 | 默认 Web 生产容器没有 API 代理 | 本机验收边界已完成：Nginx production proxy、主 Compose Web→Nginx→FastAPI→PostgreSQL 真实密码登录、Cookie/CSRF、API restart 持久性，以及 Chromium UI 登录/刷新/logout 均 PASS。第41刀进一步关闭公开`/pdfapi`直连：浏览器只能走带auth/quota/UserArtifact ACL的`/api/pdf`，Web镜像和容器不再持有parser URL/token。 | 保持 runtime Nginx、project-scoped 隔离与 fail-closed CSRF；parser宿主机端口默认只能loopback，外部部署入口仍需正式 CA/ingress/LB 验证域名、证书链、HSTS和代理头。 | Web container smoke以可区分backend/finder验证`/api/pdf/health`命中FastAPI upstream，`/pdfapi/tasks`固定404且不触达上游；production compose password/browser与TLS artifact仍PASS。外部 CA、LB/ingress、HSTS preload和真实部署域名仍是发布剩余项。 |
| P0 | Python 依赖安全审计未通过 | 已完成第六刀止血：生产锁文件当前无已知漏洞，CI 不再只阻断 CRITICAL；仍需随漏洞库变化持续在 release/nightly 复跑。 | 保持兼容 FastAPI/Starlette 组合、`idna>=3.15`、`pydantic-settings>=2.14.2` 安全下限；CI 对生产锁文件运行 pip-audit，并阻断 HIGH/CRITICAL 或任何 production finding。 | API/finder/rules 锁文件级 `pip-audit` 无 HIGH/CRITICAL；相关服务单测和启动 smoke 通过。 |

2026-07-12 第一刀止血进展：Deal OS 主路由已接入文件型对象级 guard，`/api/deals` list 会按当前用户过滤 private deal，detail/report/document/evidence/workflow/decision/audit/manifest 等 `deal_id` 路由进入业务逻辑前先执行 `require_deal_access(deal_id, action, user)`；deny 统一返回 404，避免泄露对象存在性；write allow 与 deny 会写入 `audit/access_decisions.ndjson` sidecar，不污染业务 `audit_log.json` 事件计数。`primary-market/projects` 与 primary-market meeting facade 已复用同一对象级判断：project list/detail/status/transcript、meeting attachment、chat history/session/active、agent readiness/prepare/run、workflow advance、decision confirm、chat/chat stream 等入口都会在读取或写入 Deal 相关上下文前校验当前用户是否拥有该 private deal。已补双用户 HTTP BOLA 回归：owner 可访问，另一个具备 `report.create` 的 analyst 不能通过 `/api/deals` 枚举/读取 reports/写 documents，也不能通过 `/api/primary-market/projects` 枚举/读取 detail/status 或写 meeting transcript。该项尚未宣布生产完成，后续仍需把数据库型 ACL/成员管理、跨 worker 审计汇聚、primary-market 更深层的内部 helper 直接调用约束纳入同一契约。

2026-07-12 第二刀止血进展：`/api/workflow` 已补路由层 task scope guard。PDF task 的 status / preflight 读操作要求 `report.view + UserArtifact(parse)`；Wiki import、generic Wiki import、semantic、semantic-generic 要求 `report.create + UserArtifact(parse)`；DB import 与 run-remaining 要求 `system.config + UserArtifact(parse)`，其中 run-remaining 会把 actor metadata 写入 job。通用 document workflow 同步区分 `UserArtifact(document_parse)`：status 读操作要求 `report.view`，wiki-import / semantic 要求 `report.create`，document DB import 要求 `system.config`。`/api/workflow/job/{job_id}` 会按 job 的 `taskId` 反查当前用户是否拥有 parse/document_parse 链接，避免凭 job id 跨用户读取流水线状态。已补 HTTP 负向回归：owner 可读写自己 task，另一个 analyst 不能跨 task 读/写，viewer 不能触发 wiki/semantic 写操作，低权限用户不能 enqueue run-remaining，job status 不能跨用户读取。该项仍保留生产剩余风险：当前 guard 是路由层止血，不改变解析/导入/语义构建函数主职责；历史 job 缺少持久 owner 字段时依赖 `taskId -> UserArtifact` 反查；未来还需将 durable job store、审计汇聚、任务租约和数据库型 workspace ACL 纳入同一授权契约。

2026-07-12 第三刀止血进展：Agent PostgreSQL fallback 已在 market view miss 后增加市场边界 fail-closed。runtime 会从 `context.market`、`context.postgres/report/filing/resolved_period.market`、`company.market`、以及 `company_id/filing_id` 的 `HK:`、`US_SEC:` 等前缀归一化出 market；当 market 为 HK/JP/KR/EU/US 时，multi-market agent view 无结果后直接返回空，并向 audit fallback events 写入 `reason=market_boundary_closed`、`stage=legacy_fallback_skipped_for_non_cn_market`、`source=postgres_market_view`，不再继续调用 legacy A 股 `pdf2md` 连接工厂。显式 CN/A 股或完全未知 market 保持旧 legacy 兼容。已补单元回归：HK context 和 US_SEC alias 在 market view miss 后不会进入 legacy；CN/unknown 仍可命中 legacy。该项仍保留生产剩余风险：真实 HTTP chat 边界还需要与统一 `ResearchIdentity` 一起验收，确保 HK/JP/KR/EU/US 请求必带并持久化 `market/company_id/filing_id/parse_run_id`，而不是依赖目录或文本猜测。

2026-07-12 第四刀止血进展（经第21、27、28、41刀更新）：Web 生产镜像已从静态 `serve -s dist` 改为 `nginxinc/nginx-unprivileged` 运行时代理，通过 `SIQ_BACKEND_URL`、`SIQ_REPORT_FINDER_URL` 渲染 `/tmp/nginx.conf`，并以非 root `USER 101` 启动。业务 `/api/*` 前缀优先转发 FastAPI，通用 fallback 转发 report-finder，`/api/health` 与 compose healthcheck 均命中后端健康接口；主 Compose 已完成真实密码、Cookie/CSRF、API restart 持久性和 Chromium 登录/刷新/logout 本机验收。早期曾由 Web 暴露 `/pdfapi/*` 并持有 parser token 的设计已在第41刀撤销：当前生产 Nginx 和 Vite 均对 `/pdfapi` fail-closed，浏览器只能通过带 auth/quota/UserArtifact ACL 的 `/api/pdf` 访问解析能力，Web 镜像/容器不再接收 parser URL 或 token。Web unit、production build、配置安全检查和 hardened container smoke 均通过；这些证据仍不替代外部正式 CA、真实域名、LB/ingress、HSTS 与代理头验收。

2026-07-12 第五刀止血进展：多市场 `ResearchIdentity` 已接入 agent 对话 HTTP/runtime 薄契约，但不侵入解析主链路。`ChatContext` 新增一等 `research_identity`，并兼容 top-level、`company`、`report` 中的 `market/company_id/filing_id/parse_run_id`；runtime 会在 `_collect_chat_reply_impl` 和 streaming 路径把 context 规范化为同一 identity，注入 prompt 的 `ResearchIdentity:` 行、同步到 `company/report/resolved_period/postgres` 可读位置，并进入 answer audit trace 的 `resolved_company/resolved_period` 和 PostgreSQL fallback scope。`agent_runtime_postgres_fallback` 现在优先读取 `context.research_identity` 生成 market view scope 和非 A 市场 fail-closed 判断，避免只靠目录或自然语言猜测市场。已补契约回归：ChatRequest schema 保留 identity 字段；context helper 会从 `US_SEC:` / `HK:` 前缀归一化 market 并 fan-out；multi-market agent view 能收到 `market/filing_id/parse_run_id`；answer audit trace 能保留 `company_id/filing_id/parse_run_id`；JP/HK/US 等非 A market miss 后仍保持 legacy fallback 关闭。该项仍保留生产剩余风险：真实 HTTP chat、Wiki resolver、Milvus filter、历史消息持久化和前端传参仍需端到端验收；缺 identity 的非 A 市场请求后续要明确降级或拒答策略，不能依赖 `company.dir` 作为主契约。

2026-07-12 第六刀止血进展：Python 生产依赖安全审计已从一次性人工检查升级为锁文件级 CI 门禁。API / finder / rules 的 `pyproject.toml` 都写入了安全下限：`fastapi>=0.136.1`、`starlette>=1.3.1`、`idna>=3.15`；finder 额外锁住 `pydantic-settings>=2.14.2`。对应 `uv.lock` 已刷新到当前兼容版本，修复此前审查指出的 `idna`、Starlette、`pydantic-settings` 回退风险。新增 `scripts/maintenance/check_python_dependency_audit.py` 会对 API、finder、rules 三个服务分别执行 `uv export --locked --no-dev --no-emit-local --no-hashes`，再用 `pip-audit==2.10.1` 审计 production requirements；CI 的 `security-and-config` job 会安装 `uv + pip-audit`，输出 `artifacts/security/python-dependency-audit/summary.json`，并以 `--require-pip-audit --block-all-vulnerabilities` 阻断任何 production dependency finding。Trivy filesystem scan 也从只挡 `CRITICAL` 提升为 `HIGH,CRITICAL`。本地未安装 `pip-audit` 时脚本会通过 `uvx --from pip-audit==2.10.1 pip-audit` fallback，避免出现“CI 可跑、本地假失败”的门禁漂移。已验证三个服务导出的 production requirements 均为 `No known vulnerabilities found`；本轮实跑通过 finder 105/105、rules 79/79、API auth/workflow smoke 50/50、依赖审计脚本和配置测试 12/12、相关文件 py_compile 与 Ruff 精确检查。该项仍保留生产剩余风险：漏洞数据库会随时间变化，后续仍需在 release branch / nightly 上复跑同一门禁，并补完整服务启动 smoke；当前动作不涉及 parser 解析逻辑，也不解决银行指标错配。

2026-07-12 第七刀止血进展：上传代理的内存与连接耗尽风险已先在 API 路由层收口，未把解析主功能拆入 agent。新增 `apps/api/services/upload_proxy_limits.py` 作为通用上传代理限流 helper：上传文件按 1MB 分块读入 `SpooledTemporaryFile`，默认单文件 100MB、批次 200MB，超限在调用上游 parser 前返回 413，并在异常路径关闭临时文件；同时通过 `upload_proxy_timeout()` 统一生成 `httpx.Timeout(connect/write/read/pool)`，避免 `timeout=None` 无限占用连接。`workspace.authenticated_pdf_upload()` 现在用 `SIQ_PDF_UPLOAD_MAX_FILE_BYTES` / `SIQ_PDF_UPLOAD_MAX_BATCH_BYTES` / `SIQ_PDF_UPLOAD_*_TIMEOUT` 控制 PDF 上传代理，仍保留现有 hash 去重、quota、UserArtifact 记录与 parser `/api/upload` 合约；`document_parser.create_document_tasks()` 的 multipart 与 JSON 提交也改用 `SIQ_DOCUMENT_UPLOAD_MAX_*` 和 `SIQ_DOCUMENT_TASK_*_TIMEOUT`，并继续只做文档任务提交代理，不承接 MinerU/PDF 解析主职责。已补负向与契约回归：PDF 上传超单文件限制不会触发上游调用、PDF 上传使用显式 timeout、document JSON submit 使用显式 timeout、document multipart 超限不上游、multipart 上传传递打开的临时文件句柄。实跑通过 `test_workspace_sync.py` 28/28、`test_document_parser_proxy.py` 54/54、相关文件 py_compile、`upload_proxy_limits.py` Ruff 全量检查和两个路由 import-order 检查。该项仍保留生产剩余风险：parser 服务内部的真正流式接收、并发 worker 限制、反压/取消、慢响应端到端 timeout、RSS 峰值基线与完整容器上传 smoke 仍需后续验收；本轮只是 API upload proxy 止血。

2026-07-12 第八刀止血进展：report-finder 下载器的 DNS rebinding / SSRF 高危入口已先 fail-closed，并补上官方下载的连接层 IP pinning，未触碰解析链路。`Settings` 新增 `MARKET_REPORT_ALLOW_MANUAL_UNVERIFIED_DOWNLOADS`，默认 `false`；`ReportDownloader._validate_original_url()`、`_validate_effective_url()`、`_validate_fetch_url()` 在解析公网 IP 前先拒绝 `source_id=manual_unverified`、`source_verification_status=manual_unverified` 或 `source_tier=unverified_web` 的下载，只有在显式设置该开关且处于网络隔离环境时才允许继续走公网 DNS / redirect 校验。这样攻击者可控的 `sec.gov.evil.example` 这类 URL 默认不进入 `socket.getaddrinfo()` 或 `httpx.Client`。官方监管/发行人 allowlist 下载仍按原有 market owner 规则执行，并在每次 fetch/redirect 前保留私网、loopback、link-local、metadata IP 阻断；同时 `httpx.Client` 改用 `_PinnedReportHTTPTransport`，实际请求阶段由 transport 自行解析、校验并连接到刚校验过的公网 IP，Host header 保持原域名，HTTPS 仍使用原 hostname 做 SNI/证书校验，避免“校验时公网、httpx 实连时重新解析到内网/metadata”的 TOCTOU。已补负向与契约回归：manual_unverified 默认关闭时不会触发 DNS/HTTP；即使显式打开，DNS 解析到 `169.254.169.254` 仍失败；transport 会连接校验过的 IP 而不是 hostname；模拟第一次校验公网、请求前 rebinding 到 `127.0.0.1` 时会在 connect 前失败。实跑通过 `services/market-report-finder/tests` 109/109、相关文件 py_compile 与 Ruff 全量检查。该项仍保留生产剩余风险：应用层 pinning 不等于基础设施 egress 防火墙；生产仍需 egress proxy / network policy 阻断 RFC1918、loopback、link-local、metadata IP，并补真实容器/网络命名空间 smoke。

2026-07-12 第九刀止血进展：向量 collection 映射和 Agent memory Milvus schema 策略已完成生产安全止血，未触碰解析主链路。US market vector collection 不再由 API 独立硬编码为 `siq_us_sec_reports`；`apps/api/services/market_report_settings.py` 现在从 `db/imports/market_ingestion_contract.py` 的 `target_for_market(...).default_collection` 读取默认值，US / US_SEC 均落到同一 contract 定义的 `siq_us_sec_filings`，HK/JP/KR/EU 也复用同一 contract 默认值，同时保留 `SIQ_*_VECTOR_COLLECTION` 环境变量作为显式覆盖。`scripts/us-sec/ingest_sec_case_set.py` 的 `DEFAULT_COLLECTION` 也改为读取同一 contract，并继续支持 `SIQ_US_SEC_MILVUS_COLLECTION` 覆盖，避免 API、US SEC ingest 脚本和 PostgreSQL DDL/adapter 对 US collection 各自漂移。Agent memory 侧，`apps/api/services/agent_memory_milvus.py` 的 schema mismatch 默认行为从自动 `drop_collection()` 改为 fail-closed：现有 collection 缺 required fields 时会抛出带迁移指引的 `RuntimeError`，不会删除生产 collection；只有同时显式设置 `SIQ_AGENT_MEMORY_MILVUS_RECREATE_ON_SCHEMA_MISMATCH=true` 和 `SIQ_AGENT_MEMORY_MILVUS_ALLOW_DESTRUCTIVE_SCHEMA_RECREATE=true` 时，才允许在一次性/可丢弃环境执行破坏性重建。已补回归：market settings 锁住 US/US_SEC 默认 collection 与 env 覆盖；`market_vector_ingest_args()` 使用 settings 映射时给 US 注入 `--collection siq_us_sec_filings`；Agent memory schema mismatch 默认不 drop、只开旧 recreate 开关不 drop、双开关才 drop/create；US SEC pipeline 脚本默认 collection 仍为 `siq_us_sec_filings`。实跑通过 API settings / Agent memory Milvus 单测 9/9、market vector ingest 路由相关 5/5、US SEC pipeline 脚本测试 11/11、相关文件 py_compile、Ruff 精确检查和 diff-check。该项仍保留生产剩余风险：完整的版本化 collection 迁移、alias 切换、对账报告和回滚流程尚未实现；本轮只是消除默认名称漂移和生产自动 drop 的高危行为。

2026-07-12 第十刀止血进展（经第12、15、31、38、42刀更新）：金融答案确定性 claim verifier 已进入 runtime guard、answer audit 和真实 FastAPI TestClient/auth 边界。正文中的数值、显式期间、币种会与结构化 Wiki/PostgreSQL evidence 比对；缺必要证据字段、数值/期间/币种错配会以稳定 reason 阻断，派生指标或原值/准备/净额勾稽缺 calculator/reconciliation trace 时以 `financial_calculation_trace_missing` 阻断。完整 `ResearchIdentity` 下，每条来源还必须匹配 `market/company_id/filing_id/parse_run_id`，跨公司或跨报告即使数值相同也返回 `financial_evidence_identity_mismatch`；audit 基于 `raw_reply` 保留原始违例和期望/实际身份。calculator/reconciliation trace 已升级为版本化 `siq_financial_calculation_trace_v1` / `siq_financial_reconciliation_trace_v1` JSON envelope，逐项重算输入、输出、单位/币种、指标角色、evidence_id 和身份；旧标题、工具名、`operation=...` 自由文本及伪造 `99%` 均 fail-closed，审计不把未验证 marker 计入 runs。ICBC 错值、跨 identity 同值和伪 trace 攻击已进入 12-case trace-offline benchmark 并 12/12 PASS；五市场 Bearer HTTP 正向矩阵覆盖请求序列化、auth、受控 Hermes input、真实 Wiki `identity_exact` selector、guard、audit API 和 SQLite history。共享计算规则与 runtime 输出契约已同步要求生产端输出新版 envelope。这里证明的是 deterministic guard 与受控 stub 编排，不是完整自然语言解析、真实 Hermes/live model 推理、外部生产向量迁移或完整生产代理链路。

2026-07-12 第十一刀止血进展：工商银行银行营业收入错配已在 PDF parser 源头修复，未通过 Agent runtime 包装绕过，也未拆动 MinerU/解析主链路边界。根因是 `_canonical_name()` 允许 `operating_revenue` 的 alias 做后缀命中，而 `operating_revenue` 曾包含裸别名“收入”，导致“利息净收入”先被归入营业收入并在 `_merge_key_metrics()` 首值优先合并时覆盖真实“营业收入”。本轮在 `apps/pdf-parser/financial_extractor.py` 删除 `operating_revenue` 的裸“收入”alias，新增 `bank_net_interest_income` canonical 并纳入 key metrics；“利息净收入”只精确命中自身 canonical，不再落到 `operating_revenue`。同时 `_merge_key_metrics()` 对同一 canonical + period 的不同数值生成 `quality_flags[].code=key_metric_value_conflict`，`financial_checks.json` 会同步带出 `quality_flags`、中文 warning 和 `checks[]` 中的 `quality.key_metric_value_conflict.<canonical>` fail 项；因此有冲突时 `financial_checks.overall_status=fail`，避免未来静默首值覆盖或 release gate 只看 summary 时漏放。已补回归：工商银行 fixture 中 `operating_revenue[2025]=838,270,000,000`、`bank_net_interest_income[2025]=635,126,000,000`；重复营业收入 100/102 的冲突样本生成机器可读 fail flag、checks fail 和 overall fail。实跑通过 `apps/pdf-parser/tests/test_financial_extractor.py` 38/38，以及 `test_financial_extractor.py + test_page_markers.py + test_pdf_parser_quality_service.py` 共 120/120；随后 PDF parser 全量 463/463 passed、9 skipped。该项仍保留生产剩余风险：当前修复覆盖 contract fixture 与 parser 单元层，尚需用真实工商银行长 Markdown/PDF artifact 复跑；更多银行专属指标如营业收入/营业总收入口径、手续费及佣金净收入、非利息收入等 canonical 仍待系统化梳理。

2026-07-12 第十二刀止血进展：金融 claim guard 已从 route function 边界推进到真实 FastAPI HTTP/auth 边界。新增 TestClient 回归使用 `main.app`、临时 SQLite、真实 `get_current_user`、Bearer JWT 和 `POST /api/chat` JSON 请求/响应序列化；除 Hermes 外部 run 外保留真实 `collect_chat_reply -> financial guard -> answer audit -> save_message` 链路。错误回答“工商银行 2025 年营业收入为 6,351.26 亿元”在 evidence 为 8,382.70 亿元时返回 `## 财务数值证据不一致`，错误值不进入 HTTP 响应或 assistant 历史，`audit_trace_id` 与保存消息一致，trace 记录 `claim_verifier_result.reason=value_mismatch`。随后同一用例扩展为 Bearer 和 Cookie+CSRF 双路径，Cookie 缺失匹配 CSRF header 的负向请求稳定返回 403 且不进入 runtime；金融 guard、audit、chat route 定向矩阵实跑 63/63。剩余风险是真实 Hermes/model 调用、生产反向代理 + FastAPI/DB/TLS 完整 compose 和更复杂自然语言 claim 样本。

2026-07-12 第十三刀止血进展：新增 `apps/web/container-smoke/` 和 `scripts/maintenance/run_web_container_smoke.py`，用真实 production Web 镜像、非 root/read-only/cap-drop 容器与确定性 mock API 组成独立 Compose smoke。脚本自动分配 loopback host port、冷构建镜像、等待健康、验证 SPA、`/api/health -> /health`、Bearer、Cookie、`X-CSRF-Token`、请求体和上游 `Set-Cookie`，失败时输出容器状态/日志，最终始终清理网络和容器；无显式 runner port 的 compose 直接失败。该 smoke 已连续通过两次，并接入 `web-e2e-smoke` CI job。边界保持清晰：它证明 Nginx production image/proxy 合约，不替代真实 FastAPI/auth/DB/TLS 或完整生产 compose 验收。

2026-07-12 第十四刀止血进展：通用 document-parser URL ingestion 修复了重定向 SSRF 缺口。首跳 URL 和每次 30x redirect 都通过统一 `_validate_public_url()` 重新校验，仅允许 HTTP(S) 公网目标；private、loopback、link-local、multicast、reserved 和 metadata IP 在发起下一跳前阻断。`POST /api/tasks` 对非法 URL 返回稳定 `400 {error: invalid_url}`，不会创建 opener 或后台任务。回归覆盖 `file://`、metadata IP、redirect-to-metadata 和真实 Flask HTTP 边界，`test_document_parser_app.py` 实跑 24/24。剩余风险仍是基础设施 egress policy 和真实容器网络命名空间验证。

2026-07-12 第十五刀止血进展：多市场 `ResearchIdentity` 的真实 HTTP/audit 链路进一步收口。金融错误 claim 的 `main.app` TestClient 用例现在通过 Bearer 与 Cookie+CSRF 请求 JSON 携带 HK `market/company_id/filing_id/parse_run_id`，并在 fake Hermes run 接口处断言 runtime input 使用同一 identity；最终 `answer_audit_trace.resolved_company` 保留 HK 公司身份，`resolved_period` 保留 `filing_id` 和 `parse_run_id`。测试首次暴露 `_extract_resolved_period()` 未输出 `parse_run_id`，已修复为同时从 resolved period、research identity/context 和结构化引用提取，未通过放宽断言掩盖。相关 HTTP + answer audit 定向矩阵已通过，Ruff 精确检查通过。剩余边界是 Wiki resolver、Milvus filter、历史消息/前端身份持久化的全市场 E2E，以及非 A 市场缺 identity 的明确拒答或降级策略。

2026-07-12 第十六刀止血进展：工商银行错误修复已从小型 contract fixture 推进到真实长 Markdown 黄金门禁。新增 `eval_datasets/parser_financial_golden/v1/cases.json`，记录 2025 年报真实 Markdown 的相对文件名、SHA-256 `bcc84f012983310799e03752be77f55e1df9e322ec27beb03d5a324a83b6b658`、最小 900000 字节/8000 行约束、四个关键指标和来源行。`run_parser_financial_golden_gate.py` 提供 `contract` 与 `offline-samples` 两层：PR 只校验 manifest schema/path/hash/expected metric contract；self-hosted release 从 checkout 外只读样本根目录加载真实文件，校验 hash、规模、提取值、来源坐标、quality flags 和 `financial_checks`。本地对 910089 字节、8291 行真实样本实跑 PASS：`operating_revenue[2025]=838270000000`、`bank_net_interest_income[2025]=635126000000`、`net_profit[2025]=370766000000`、`parent_net_profit[2025]=368562000000`，四项均来自 Markdown 第 432 行/table 3，`quality_flags=[]`、`financial_checks.overall_status=pass`。该 gate 已接入 PR `market-eval` contract 和 `run_market_postgres_release_gate.sh`；offline release 要求外部 `SIQ_FINANCIAL_GOLDEN_SAMPLE_ROOT`。

2026-07-12 第十七刀止血进展：首次以 `SIQ_DEPLOYMENT_PROFILE=production` 对主 `infra/docker/docker-compose.yml` 启动真实 API 依赖栈并完成 FastAPI auth + PostgreSQL HTTP smoke，过程中修复了三个此前被配置/单测掩盖的发布阻断。第一，API production runtime 强制要求 `SIQ_CORS_ALLOW_ORIGINS`，compose 现在显式透传并 hard-require 该值，local/docker env template 固定 loopback origins。第二，PDF parser 原镜像使用子目录 build context，把 monorepo helper 错放到 `/app` 且漏掉 market-rules、HK/JP/KR scripts、shared contracts 和 Pydantic；镜像现在保留 `/app/apps/pdf-parser` 布局，只复制三个市场所需脚本/规则/contracts，并安装锁定的 Pydantic 与 `siq-market-contracts`，容器内 `import app + HK/JP/KR financial profiles` 通过。第三，compose 原先把 default network 硬命名为全局 `siq_network`，并行 stack 的两个 `postgres` DNS alias 会轮询并随机串库；已移除固定网络名，恢复 Compose project-scoped network，并用渲染级测试锁定 `<project>_default`。全新隔离 project 实跑结果：API、PostgreSQL、Redis、PDF parser、document-parser 五服务全部 healthy；`GET /health=200`；未带 token 的 `GET /api/workspace/summary=401`；在真实 `siq_app` 创建 approved analyst、由 API 容器生产密钥签发 Bearer JWT 后同一路由 `200`，响应用户来自 PostgreSQL；`siq_app` 有 12 张 public 表，`idx_chatmessage_session_created_at` 与 `idx_usage_events_user_type_date` 均存在。该手工验收已固化为 `run_production_compose_smoke.py`：自动分配 project/loopback ports/随机凭据并强制 project-scoped `postgres_data`，启动真实主 compose API 子集、创建 analyst/JWT、验证 401/200 与 PostgreSQL 表/索引，重启 API 后复用 JWT 再验用户和数据库持久性，失败输出状态/日志且 `finally` 清理 containers/volumes/network，JSON artifact 不含 token/密码；PR 只跑脚本与 compose config contract，真实容器执行接入 self-hosted/manual release workflow。脚本本身完整实跑 PASS：restart 前后 auth 均为 200、12 张表、2 个必需索引、1 个持久 smoke user，约 41 秒完成且清理后无残留 project container/volume/network。该 smoke 不虚报外部能力：没有覆盖 Web/TLS/reverse proxy、Cookie+CSRF 登录流程、Hermes/model/chat、Milvus，以及 MinerU/VLM 推理；PDF health 明确显示外部 MinerU/VLM 未就绪，但 parser HTTP 服务和 import graph 已健康。

2026-07-12 第十八刀止血进展：多市场 `ResearchIdentity` 已从“完整 HK identity 能传入 runtime”推进到缺 identity 的前置 fail-closed。document-full status 的 `parse_run_id/filing_id/document_full_path/task_id` 改为互斥 selector，多个 selector、显式 market 与 filing market 冲突、跨 market root path 都在真实 HTTP 边界返回 400，避免隐式优先级静默忽略。金融问答只要明确 market 为 HK/JP/KR/EU/US 且缺 `company_id/filing_id/parse_run_id` 任一项，就返回 `guardrail_reason=financial_research_identity_incomplete`；在模型输入构造和 fallback 阶段，Wiki company scope/fulltext/three-statement/note/human/direct/parse-only builders、multi-market latest view 与 A 股 legacy 均前置短路，不再按 annual/primary/latest 或目录猜测取数。真实 `main.app` + Bearer HTTP 五市场矩阵断言 market view=0、legacy=0、原始模型数值不进入响应/历史，audit 同时记录 `research_identity_incomplete`、`market_agent_view_skipped_for_incomplete_identity` 和 `legacy_fallback_skipped_for_non_cn_market`；普通非金融聊天不受影响。完整 identity 的 chat history 通过 `audit_trace_id` 回读保留 resolved company/filing/parse run 关联。剩余缺口不再是“缺 identity 怎么处理”，而是 complete identity 如何进入 Wiki 精确 report selector 与向量检索：现有 Milvus/pgvector memory schema、写入 metadata 和 retrieval API 都没有 market/company/filing/parse 字段，需要版本化 collection、数据迁移和双后端 filter，不能用局部 query patch 冒充闭环。

2026-07-12 第十九刀止血进展：真实工商银行原始 PDF 已纳入可执行 release gate。`cases.json` 版本化记录原始 408 页、9,289,165 字节 PDF 的相对路径与 SHA-256 `e2edab73032f143aad881f612382bc613c8b96b424bb197b125d64a0ef23c78b`；`run_parser_financial_pdf_release_gate.py` 提供 PR-safe `contract`、真实文件/服务 `preflight` 和 self-hosted `live-http` 三层。live 模式只有在 PDF identity、parser health/MinerU submit readiness、唯一任务上传、terminal status=completed、fresh Markdown 非空、四项 financial metric/source、quality flags 与 financial checks 全部通过时才 PASS；timeout、404、非 completed、缺 Markdown 和任何 financial assertion mismatch 都 hard fail。长任务现在原子写 checkpoint；未取得 terminal/result evidence 时保留本地 task 供恢复，只有取得结果证据后才清理，取消也必须由 upstream 明确确认。报告只保留 parser origin，剥离 userinfo/path/query/token。PR CI 已接 contract，release wrapper 仅在显式 `SIQ_PARSER_FINANCIAL_PDF_GATE_MODE=preflight|live-http` 时运行，`REQUIRED=1` 会传播失败；self-hosted workflow 校验 PDF root 位于 checkout 外并保留 JSON/Markdown artifact。

2026-07-12 第二十刀止血进展：金融 claim verifier 和 benchmark 继续扩展复杂自然语言与多市场单位。verifier 新增营业总收入、利息净收入、手续费及佣金净收入、非利息收入、归母净利润、毛利/净利率、资产负债率、ROE/ROA/NIM、不良率和 EPS 等 canonical alias，保留 `营收/毛利/商誉` 精选短别名；金额归一化新增 JPY/KRW/GBP/CHF、`iso4217:*`、million/billion/thousand 与每股单位。抽取从“同行每个 alias 绑定所有数字”收紧为 clause/最近指标绑定，并支持“营业收入和利息净收入分别为 X/Y”的顺序映射，避免同句多指标互相制造假 mismatch；工商银行同句正确值放行、互换值分别命中两个 `value_mismatch`。ROE/ROA/NIM 等派生指标继续强制 calculator trace。guard 还修复了证据 metadata 中 `source_type=... metric=...` 的等号被误判为商誉勾稽等式的问题：trace 缺失判定现在先看原始 claim，再追加后端证据，结构化来源行不参与派生/勾稽意图判断；真正的商誉原值/准备/净额与人均指标测试补入对应 reconciliation/calculator trace，没有放松门禁。金融 QA benchmark 增至 10 case，工商银行攻击 case 必须同时证明 `financial_claim_mismatch`、`value_mismatch`、claimed 6,351.26 与 evidence 8,382.70；trace-offline 10/10 PASS，复杂 verifier 11/11、此前扩展 runtime 集合 141/141。

2026-07-12 第二十一刀止血进展：第十七刀的真实 API/PostgreSQL smoke 已扩展到主 production Compose 的 Web -> Nginx -> FastAPI -> PostgreSQL 边界，未调用 Hermes/model、MinerU/VLM 或 Milvus。`run_production_compose_smoke.py` 现在为 Web、API、report-finder、parser、PostgreSQL、Redis 分配独立 loopback 端口和随机凭据，以 project-scoped volume/network 启动 Web 服务及依赖；通过 Nginx `/api` 代理使用手工 Cookie JWT 读取 workspace，得到 `200` 和真实 PostgreSQL 用户；同一 Cookie 对受保护 `POST /api/auth/logout` 缺少 CSRF header 时返回 `403`，匹配 `siq_csrf_token`、`X-CSRF-Token` 和同源 Origin 后返回 `200`。实跑先暴露并修复两个生产问题：Web 配置引用 `report-finder` upstream 却未声明依赖，Nginx 因 DNS 无法解析重启；Nginx 使用 `$host` 转发会丢宿主端口，导致合法 CSRF Origin 与后端同源 Host 不匹配，现统一使用 `$http_host`。脚本保留直连 API Bearer 401/200、API restart 后 JWT/用户持久性、12 张 public 表和 2 个必需索引断言，失败输出日志，`finally` 清理容器/卷/网络，artifact schema 升级为 `siq_production_compose_smoke_v2`；logout 还精确断言 access/CSRF 两个重复 `Set-Cookie` 均包含 `Max-Age=0; Path=/`。完整实跑 PASS：Cookie GET=200、缺 CSRF POST=403、合法 CSRF POST=200、两个 Cookie 清除均为 true、Bearer restart 前后=200，40.021 秒完成且无残留；真实执行继续由 self-hosted/manual release workflow 承担，PR contract 锁住 Web dependency、Host 端口透传和 smoke 断言。仍未覆盖 Web TLS termination、真实密码登录/浏览器 Cookie UX、Hermes/model/chat、Milvus 与 MinerU/VLM 推理，不据此宣称这些外部能力已完成。

2026-07-12 第二十二刀止血进展：真实 408 页工商银行 PDF 的长任务验收补上了结果保全和恢复路径。`run_parser_financial_pdf_release_gate.py` 的 `live-http` 现在原子写入脱敏 checkpoint；上传/轮询超时、进程中断或缺少结果证据时保留本地 task，不再在 `finally` 无条件删除，只有取得 terminal Markdown 与金融验收证据后才清理。新增 `recovered-result` 模式，可从原始 MinerU completed-result JSON 提取 Markdown，记录 result/Markdown SHA-256、backend/version/upstream task id 而不把原文写入报告；PDF parser 取消接口只有收到明确 `cancelled/canceled/deleted` 状态才报告 upstream cancelled，`success=true` 或 405 不再伪装成取消成功。真实 recovery artifact 对 PDF `e2edab...c78b` / 408 页和 upstream task `386c1e66-2ba0-49a6-9d1c-00965896252d` 验证通过，四项工商银行指标正确、`source_line=418` / `table_index=3`、`quality_flags=[]`、`financial_checks=pass`、`financial_semantics_passed=true`；但 fresh Markdown 相对固定 golden 的 `899,563` 字节、`7,757` 行和来源行漂移被单独标为 `fresh_layout_drift`，整体 release 仍为 `BLOCKED`，不能把语义通过冒充正式 `live-http` PASS。离线 golden 的固定 SHA/行数/来源坐标契约保持不变。

2026-07-12 第二十三刀止血进展：ResearchIdentity 的双后端隔离已从单测推进到本机真实容器契约证明，未修改外部生产业务数据。PostgreSQL 使用 `pg_temp` 临时记录验证 complete identity A/B 只命中各自记录、无 identity 只命中 unscoped、partial identity 在 backend 前 `ValueError`，会话结束后临时表与持久化测试记录均为零；本机 Milvus `siq_agent_memory` 保持 1,630 行且 v1 schema 缺四个 research scalar 字段，应用 preflight 返回 `compatible=false`、`migration_required=true`、`create_versioned_collection_and_reindex` 且 destructive recreate 关闭；独立 v2 contract collection 使用真实 `AgentMemoryVectorRecord`、`acl_expr()`、`search_records()` 验证同 identity/跨 identity/unscoped/partial 隔离后已删除。该刀证明了代码和本机查询契约，但外部生产 collection 仍必须独立完成 inventory、回填、reindex、校验、alias 切换和回滚报告，不能把本机结果冒充生产完成。

Milvus 角色边界补充：`siq_agent_memory`（包括 v2 schema）只承载 Agent memory 语义索引，不承载 MinerU/PDF 解析产物；`ic_legal_scanner` 等 legacy `ic_*` collection 承载法务/智能体背景知识。解析/Wiki package 的市场向量入库是独立的、显式触发的管线，使用 `siq_hk_reports`、`siq_jp_reports`、`siq_kr_reports`、`siq_eu_reports`、`siq_us_sec_filings` 或通用文档 `siq_documents` 等目标，由 vector-ingest/workflow 调用 `ingest_market_evidence_chunks.py`、`ingest_sec_wiki_chunks.py` 或 `ingest_document_chunks.py`；PDF/MinerU 完成本身不会自动写入这些 collection。结构化数字事实仍以 Wiki/PostgreSQL 为权威来源，Milvus 只做可回查的语义召回；当前本机 runtime 只证明 Agent memory profile seed 的 v1→v2 迁移/回滚和独立隔离契约，不能据此声称外部生产或市场解析产物已完成 Milvus 入库。Memory scope 方面，`user_private` 已按 tenant/user/profile 隔离；`project_shared` 现在要求同一 tenant、deal/project 和 `agent_group`，同族群内可共享而 primary/secondary 跨族群拒绝；`system_shared` 仍按设计跨族群共享。该 ACL hardening 已补 PostgreSQL/Milvus 跨组负向、同组放行和 system-shared 放行测试；旧的 `project_shared` 记录若缺 `agent_group` 会 fail-closed，外部生产迁移时必须回填并校验。

2026-07-12 第二十四刀止血进展：现场登录 502 暴露了 `start_all.sh` 在前端子进程退出后仍因 Hermes 进程存活而保持“整栈运行”假象；默认前端 `15173` 无监听，而 `18081` 后端本身健康。已恢复 `15173`，实测 `/api/health=200`、`/api/auth/login` 返回后端 JSON 而非 HTML/502；`start_all.sh` 末尾改为 `wait -n "${pids[@]}"`，任一服务子进程退出即清理整栈并给出明确失败。该改动与精确 Cookie 清除解析一起通过启动/production smoke 定向测试；仍不把 `15173` 的开发服务器启动视为 TLS、浏览器登录 UX 或 Hermes/model 生产验收。

2026-07-12 第二十五刀止血进展：Agent memory Milvus v1→v2 迁移补齐了不可变的离线 dry-run 计划器和严格只读本机 inventory。`scripts/hermes/plan_agent_memory_milvus_migration.py` 根据 schema、向量维度/metric/index、实体计数、id/content_hash manifest 和 ResearchIdentity 观察状态，生成 create-only target、四字段身份回填、alias bootstrap/switch、count/hash/identity/retrieval 验收清单和保留 v1 source 的回滚 manifest；禁止 drop source、破坏性重建、未验先切 alias和从 metadata/正文/标题/路径猜 identity。首次只读 artifact 如实用 `observation_status=unavailable`/null 表达 v1 缺标量字段时的未知，不用 0 冒充；随后新增的 profile contract inventory 对全量 1,630 条结构化字段逐条证明 `id prefix/source_kind/memory_type=profile_file`、`visibility=system_shared`、metadata schema=`siq_agent_profile_chunk_v1`，因此权威归类为 `research_scoped=0`、`complete=0`、`partial=0`、`unscoped=1630`。这消除了本机 profile seed 的 identity backfill 阻断，但不适用于任何含用户记忆或 research-scoped 记录的外部生产 collection。

2026-07-12 第二十六刀止血进展：本机真实 Milvus 已完成非破坏 v1→v2 staged migration、alias 切换和回滚往返验收，外部生产仍未执行。`migrate_agent_memory_milvus_v2.py` 默认 dry-run；`--apply` 会在 live source inventory 与批准 snapshot 的 count/fields/dimension/index/metric/manifest/profile contract 全部一致后，先把 `siq_agent_memory_active` bootstrap 到 v1，再 create-only 创建 22 字段 `siq_agent_memory__v2`、复制原向量并将四个 ResearchIdentity 字段置空；只有 schema、1,630 count、id/content_hash manifest `1a7c7ae...707ff54`、vector/index/metric 和 identity 空字段全部验证通过，显式 `--switch-alias` 才能把 alias 切到 v2。stage/switch artifact 均 PASS，v1/v2 各保留 1,630 条；真实 `--rollback` 已把 alias 从 v2 恢复 v1并验证，随后重新对账切回 v2，最终 alias 指向 v2，源/目标数量和 manifest 均未变化。通过 alias 的真实检索同租户 unscoped 命中、跨租户 0、带完整 research identity 0，profile retrieval contract 3/3、hit rate/MRR 均为 1.0。production compose 现在要求显式 `SIQ_AGENT_MEMORY_MILVUS_COLLECTION`，production/docker template 使用 `siq_agent_memory_active`，不再静默落回物理 v1。仍未证明：外部生产迁移、含 research-scoped 正样本的 same/cross identity 检索、真实 pgvector/Milvus 双后端对账与运行中 API 重启后使用 alias；这些项继续保持发布阻断。

2026-07-12 第二十七刀止血进展：主 production Compose auth smoke 已从手工 token 升级到真实密码与 Chromium 浏览器。隔离 project 在 PostgreSQL 中用应用 `AuthService.hash_password()` 创建临时 analyst，经 Web/Nginx `/api/auth/login` 获取 HttpOnly access cookie 与可读 CSRF cookie；cookie workspace 在 API restart 前后均 200，缺 CSRF logout=403，合法 CSRF logout=200 且双 cookie 清除。Playwright 真实 UI 填表登录，确认 localStorage 无 access token、刷新/重启后工作平台仍可用、账户菜单退出后 cookies 消失且 protected API=401。浏览器段 6.328 秒、完整 compose 53.571 秒 PASS，密码/JWT/CSRF/storage state 未写 artifact，容器和 volumes 零残留。

2026-07-12 第二十八刀止血进展：新增独立 test-only TLS reverse-proxy smoke，不向 production compose 注入自签证书。临时自签 HTTPS sidecar 以 read-only/cap-drop 运行并转发真实 Web；实跑证明 HTTPS health/login/cookie auth/合法 CSRF=200，缺 CSRF与错误 Origin=403，access/CSRF 均带 Secure 且 access HttpOnly，CookieJar 直连 HTTP 不发送 Secure cookie并得到401，logout 清除通过。测试暴露 Web Nginx 会覆盖前置 `X-Forwarded-Proto=https`，现改为保留可信前置协议；artifact 38.376 秒 PASS且所有临时资源清理。该证据只证明本机 TLS 代理契约，外部 production CA、LB/ingress、HSTS和真实域名仍未覆盖。

2026-07-12 第二十九刀止血进展：工商银行 fresh PDF layout drift 已通过“候选生成→独立审批→versioned fresh baseline”闭环收口，没有覆盖旧 offline golden。候选必须同时满足同一 PDF hash、raw middle/model/content 三路408页、结构284/141/973/33/30、四项金融指标、checks=pass、空 quality flags、source_line/table_index完整，且所有差异严格属于 presentation-only；批准精确绑定 version `icbc-2025-mineru-3.1.2-v2` 与 Markdown SHA-256 `a6c9f6...6928`。`approved_fresh_baseline` 独立保留899,563 bytes、7,758 splitlines、source line 418/table index 3，旧 offline SHA/min lines/source line 432不变。已有 completed raw result 的 approved recovered gate 1/1 PASS，原 layout drift仍展示并标记 `resolved_by_approved_fresh_baseline=true`；错误version/hash/结构/语义均hard fail。仍需一次不中断的当前 `live-http` upload→poll→result运行证明服务稳定性。

2026-07-12 第三十刀止血进展：Deal/workflow 文件状态并发与后台 job 生命周期完成本机加固。`deal_store` 增加 per-path RLock+`fcntl`、唯一临时文件、`fsync`+原子 replace、权限继承和 `update_json` 原子 RMW/CAS；audit/access、IC R1 reports/submitted agents、phase/project/decision 等不再裸 read-modify-write。Workflow job store 在进程锁内重读并合并磁盘状态，用隐藏 revision sidecar保持原 JSON 契约，stale revision明确冲突；job新增 owner/heartbeat/lease/idempotencyKey，worker持续延租，启动/查询/提交会把过期 queued/running安全转为 `failed+recoverable+stale_lease`，不自动重放闭包副作用。同 task/scope/actor active重复提交跨进程收敛，terminal/recovered允许显式重试。并发/路由回归覆盖8进程写store、6进程claim、40线程更新、restart/stale lease/heartbeat/重复提交，Deal扩展140与workflow相关131均PASS。

2026-07-12 第三十一刀止血进展：Production Compose 的 chat 编排已推进到 controlled Hermes gateway stub，而非真实模型推理。隔离运行使用真实密码/Cookie用户，经Web `/api/chat`=200，API向受Bearer保护的gateway create run与SSE各调用1次；受控错误金融回答被fail-closed为 `financial_evidence_missing`，claim audit仍精确记录`value_mismatch`，history有user/assistant 2条，PostgreSQL有2条消息且assistant关联audit。原始artifact因旧harness错误要求可见guard reason必须为mismatch而标failed，保留不篡改；修正harness后对原artifact离线复验证明编排契约通过。该证据明确是controlled stub；本机真实Hermes health可达但缺token，未执行live model inference。该轮观察到的 Agent Memory mirror greenlet问题已在第33刀修复并复验，不再列为剩余阻断。

2026-07-12 第三十二刀止血进展：上传代理在既有分块与 spool 基础上补齐进程级反压和取消安全。共享 `UploadProxyConcurrencyLimiter` 默认8并发、5秒可取消排队，容量超时返回503 `upload_proxy_busy` 与 `Retry-After`，不会创建上游连接；PDF multipart从二次缓冲开始占槽，document multipart在quota预检后占槽，JSON/import/from-download仅在上游HTTP期间占槽，成功后的usage/artifact处理不挤占容量。`buffer_upload_files` 修复分块读取被取消时“当前尚未加入列表的 spool”泄漏；chat PDF attachment移除 `Path.read_bytes()`，改用磁盘句柄流式发送并纳入相同limiter。compose暴露并发/排队参数。回归覆盖磁盘rollover、1KB chunk、慢upstream、413/timeout、排队/传输/读取三阶段取消和quota/去重；仍需真实容器并发大文件RSS与parser内部慢请求验收。

2026-07-12 第三十三刀止血进展：Agent Memory PostgreSQL mirror 的 compose greenlet fail-soft 已找到并修复。`save_message()` 原来在 `AsyncSession.commit()` 后读取默认 `expire_on_commit=True` 的 `msg.created_at`，隐式lazy-load发生在 async greenlet外并触发 `MissingGreenlet`；现改为commit前捕获时间戳。另修复 `SESSION_ID_RE` 只接受32/36位UUID、而实际SessionManager使用8位后缀导致 `user_id=None`、private mirror跳过的问题，现兼容8/32/canonical36且profile非贪婪。最终重建 compose smoke中chat=200、Agent Memory DB messages=2/sessions=1、browser PASS、金融guard blocked且audit=`value_mismatch`，日志无greenlet warning；mirror异常仍保留fail-soft，不阻断主chat。

2026-07-12 第三十四刀止血进展：SSRF从应用单测推进到一次性真实容器DNS/connect smoke，未修改production compose。fresh report-finder镜像在4张disposable internal bridge中验证：官方`www.sec.gov`策略通过后真实TCP仅连接pinned受控公网地址且Host保持原域名；DNS返回10.77/169.254.240/169.254.169.254/127.0.0.1均在connection factory前阻断且trap=0；redirect-to-metadata第二次connect=0；rebind序列公网→127.0.0.1在第二次connect前阻断。runner为read-only/cap-drop ALL/no-new-privileges，主compose hash未变，所有容器/网络finally清理。该artifact证明应用在容器namespace中的DNS/connect边界，不证明外部production firewall、Kubernetes NetworkPolicy或云egress policy。

2026-07-12 第三十五刀止血进展：ResearchIdentity 从临时SQL/Milvus分别验证推进到同fixture真实双后端对账。隔离 harness 在一次性 pgvector/pg16 schema 与本机临时 Milvus v2 collection 写入同一8条脱敏记录，执行identity A、identity B、unscoped和partial fail-closed四场景，覆盖跨user/tenant/agent_group；pgvector_dense与milvus_dense命中集合8/8一致，partial均在backend前拒绝，PostgreSQL schema/container与Milvus临时collection均finally清理。实跑发现 `_memory_acl_sql()` 的 nullable `:deal_id/:project_id IS NOT NULL` 在asyncpg触发`AmbiguousParameterError`，现改为显式TEXT cast并补回归。该证据完成本机research-scoped正负样本双后端契约，但不代替外部production数据迁移和规模/性能验收。

2026-07-12 第三十六刀止血进展：工商银行不中断 `live-http` upload→poll→result 首次完成后暴露的结构爆炸已定位到 sparse-page backfill，而非金融抽取或 MinerU 原始表格重复。旧判定会把短标题/短正文误当缺页；进一步复核发现，page marker 插值还会制造大量空 marker span，不能据此认定原 Markdown 缺失。现仅对真正空白 span 进入候选，并要求 `content_list` 重建内容在整份 Markdown 中缺少足够覆盖才允许回填；短标题、释义引言和已在相邻 span 出现的页面内容均保留。对首个66MB原始result重放后，回填页由182降为0、HTML表格由475恢复284，相关测试84 passed。修复代码加载后又执行一次新的不中断408页 `live-http`：932秒完成，raw middle/model/content均408页，最终结构284/141/973/33/30、四项金融值、table index=3、financial checks=pass、quality flags=[]全部通过，仅final artifact行号统一为433。审批流程随后纠正raw `md_content`与PDF API final Markdown的证据层级，候选生成器现在复用生产页标记/回填逻辑并同时记录raw/final SHA；final候选经显式审批参数复验后更新为`icbc-2025-mineru-3.1.2-v3`、SHA `0bf82c...a432`、913,194 bytes、8,377 splitlines和source line 433，更新manifest后的recovered-result 1/1 PASS。此前独立复核批准的是raw候选，不把该结论外推为final artifact的独立批准；第一次BLOCKED和候选未批准artifact均保留，不覆盖历史证据。

2026-07-12 第三十七刀止血进展：上传代理从 mock 慢上游回归推进到 disposable container 的并发/RSS证据。两个8MiB上传在真实API镜像中同时占满2个准入槽并滚落磁盘，第三个请求在0.25秒排队上限后返回503 `upload_proxy_busy` 与 `Retry-After: 1`；两个受控慢上游请求在read timeout后返回502并释放全部槽，随后256KiB恢复请求200。最终保留 artifact 的 cgroup `memory.current` 从51,290,112峰值到98,398,208 bytes，增量47,108,096 bytes，低于显式64MiB预算；3/3 buffered handles关闭，临时容器和网络清零，read-only/cap-drop/no-new-privileges均成立。该artifact证明应用镜像内的spool、反压、timeout和恢复边界，不证明外部production ingress/LB是否预缓冲、宿主机整体内存压力或真实parser延迟分布。

2026-07-12 第三十八刀止血进展（经第42刀补完产品供给与历史快照）：多市场 `ResearchIdentity` 在不降低门禁强度的前提下并行收口。catalog 以规范化 `market + company_id` 做唯一权威选择，runtime 真实传入 company id；不存在、重复、market 冲突或目录缺失均 fail-closed，不回退文本猜测。session 默认上下文遇到完整且不同的当前 identity 会刷新，覆盖跨公司及同公司 filing/parse run 切换；格式化失败时删除旧缓存。Wiki exact selector 复用 manifest 嵌套 `report_md/document_full` 路径并限制在 company 目录内，scope 输出 canonical company id。金融 claim verifier 逐行核对 Wiki/PostgreSQL 来源四字段，跨公司/filing/parse run 同值攻击或字段缺失返回 `financial_evidence_identity_mismatch`，audit 保留期望与实际身份。受保护 `main.app` Bearer HTTP 正向矩阵覆盖 HK/JP/KR/EU/US 的真实请求序列化、auth、SQLite history/audit API、Wiki `identity_exact` selector 和 guard/verifier；Hermes 与自动补证据为受控 stub。Wiki/report API、前端显式 payload 和 `ChatMessage` 逐消息快照已在第42刀补齐。本地产品链路完成后，剩余仍是旧 Wiki 权威字段回填、真实 Hermes 攻击、外部生产向量迁移和全市场 Wiki/Milvus/pgvector 规模 E2E。

2026-07-12 第三十九刀止血进展：PDF parser内部增加原子队列容量准入，不再只依赖API上传代理。SQLite repository在`BEGIN IMMEDIATE`事务内按全局及`owner_id/tenant_id`统计queued/submitting/submitted/pending/processing任务数和字节数，整批请求超出任一上限时全部拒绝；upload与from-download返回503、稳定`parser_queue_capacity_exceeded`、scope和`Retry-After`，并清理已准备文件。health暴露当前active/queued tasks、active bytes与配置上限；production/docker env提供全局32任务/2GiB、owner 8任务/512MiB及retry-after默认值。parser定向26 passed、扩展组合100 passed且compose/env配置测试21 passed。该门禁证明单实例SQLite准入原子性，不等于多副本共享durable queue；外部多实例仍需PostgreSQL/Redis worker backend、租约、crash takeover和真实部署压力验收。

2026-07-12 第四十刀止血进展：parser 准入从“容量计数原子”继续收紧到“任务身份不可覆盖”。repository 的 admission 改为 insert-only 原子事务，task id 已存在时返回 `409 parser_task_id_conflict`，不会通过 upsert 覆盖原任务；upload、from-download、reparse 统一经过同一冲突与容量门禁，跨 owner/tenant 的同 task id 也不能改写既有记录。上传落盘不再使用客户端 task id 作为文件路径，使用独占创建；from-download 使用隔离复制而非 hardlink，并在副本上计算大小、页数和 SHA；reparse 也对隔离副本重算 SHA 后再准入，不能绕过容量或复用受污染文件。PDF parser 全量回归 480 passed、9 skipped、2 subtests passed，py_compile、touched Ruff fingerprint 和 `git diff --check` 通过。该证据覆盖当前单实例 SQLite repository 与 HTTP 入口，不等于多副本共享队列、分布式唯一键、worker lease/crash takeover 或外部生产压力验收。

2026-07-12 第四十一刀止血进展：公开 `/pdfapi` 浏览器旁路已关闭。production Nginx 对精确 `/pdfapi` 和 `/pdfapi/*` 固定返回 404，Vite proxy 不再配置该前缀，也不再注入 `X-PDF2MD-Token`；Web Dockerfile、entrypoint、主 compose 和环境模板不再向浏览器层传递 parser URL/token，设置页移除 parser 直连配置并清理 legacy `pdf_api_base`。所有浏览器解析请求必须走 FastAPI `/api/pdf`，继续受 auth、quota 与 `UserArtifact` ACL 约束。拆分 mock backend/finder 的 hardened container smoke 证明 `/api/pdf/health` 命中 FastAPI upstream、generic `/api/*` fallback 命中 finder，而 `/pdfapi/tasks` 为 404 且两个上游均未收到请求；Web unit 265 passed、production build、container security config 和 Web container smoke 均 PASS。该刀只关闭应用发布物中的旁路；外部 ingress/LB 若另行暴露 parser、parser 宿主机端口绑定和网络策略仍需在真实部署环境独立审计。

2026-07-12 第四十二刀止血进展：第38刀的 `ResearchIdentity` 从 runtime 消费能力补齐到产品供给与不可变历史。Wiki company API 只从显式 metadata/exchange mapping 返回 `market/company_id`；report API 仅在 analysis artifact 或 primary report 与 manifest 能唯一映射、四字段完整且跨层无冲突时返回完整 identity，旧产物缺权威字段时 fail-closed，不从 ticker、目录或显示名拼造。前端 `mergeResearchIdentity()` 只合并显式字段，冲突即拒绝；ReportViewer 只有在 API 实际供给时才发送 identity。`ChatMessage` 与 `agent_memory.messages` 均新增 nullable `research_identity_json` additive migration，user/assistant 写入同一请求的规范化四字段快照，history/记忆行按消息保存；旧行、空值、部分身份和坏 JSON 均为 `null`，不会读取后来变更的 session metadata。五市场 Bearer TestClient 矩阵与 Wiki/report/history/audit 交叉回归证明本地 HTTP 边界；金融 benchmark 加入跨 identity 同值与伪 trace 攻击后 trace-offline 为 12/12 PASS，key fact/evidence coverage 均为 1.0。Hermes 仍是受控 stub，旧 Wiki 字段回填、外部生产向量数据和真实模型攻击不由此宣称完成。

2026-07-12 第四十三刀止血进展：财务计算 trace 从“要求模型在可见正文重写完整 JSON”改为“可信工具回执与展示摘要分层”，没有降低 deterministic guard。`financial_calculator.py` 的 ratio/yoy/cagr 结果新增输入快照；runtime 只从当前精确 Hermes session 的同一 user turn 读取 `terminal tool_call_id -> tool result`，且仅接受白名单 Python 与财务脚本路径、显式 `--format json`、单命令、完整单 JSON、成功退出的回执，管道、重定向、命令拼接、多 JSON、截断输出、错误 call id 或非白名单同名脚本均拒绝。后端使用请求的完整 `ResearchIdentity` 和最终来源行重新绑定 metric/period/value/unit/evidence_id，再执行原有确定性重算；可见回答与历史继续保留简洁 `## 计算器校验` / `## 勾稽校验` 摘要，完整 envelope、receipt hash 和 tool_call_id 只进入 answer audit JSON，并由 `audit_trace_id` 查询。缺失显式 evidence_id 的外部事实仍按原规则失败；自动稳定 ID 仅用于内部 trace 关联，不能绕过 claim verifier。当前 `SIQ_FINANCIAL_GUARDRAIL_MODE=warn` 调试模式保持原回答并追加诊断，不切换为阻断；生产 block 语义不变。另修复混合回答只识别单一 calculator operation 的 early-return，现会同时要求并覆盖 yoy/ratio/cagr/per_capita。金融 trace/guard/audit、calculator、claim verifier、chat route/runtime 定向回归均通过，本变更精确 Ruff 与 `git diff --check` 通过；全工作树 touched fingerprint gate 当前被 3 个并行变更中的 import-order 新诊断阻断（`apps/api/main.py`、`routers/primary_market_meeting.py`、`scripts/ops/tests/test_backup_restore_scripts.py`），本刀未覆盖或回退这些文件。

总体优化目标继续保持 active。上述刀次关闭的是本地代码、测试和受控容器边界；外部生产 CA/LB/egress、真实 Hermes/live model、旧数据权威 identity 回填、外部 Milvus/pgvector 迁移与规模验收，以及 parser 多副本 durable queue 仍是剩余工作，不能以本机 PASS 替代发布验收。

#### 高优先级生产硬化项

| 优先级 | 问题 | 当前评估 | 必须补齐的工程动作 | 验收 |
| --- | --- | --- | --- | --- |
| High | DNS rebinding SSRF | 应用/容器边界已完成第八刀与第34刀：manual默认fail-closed，官方allowlist/redirect逐跳公网解析，pinned transport避免重解析；真实disposable容器DNS/connect smoke证明私网/metadata/loopback/redirect/rebind均connect前阻断。剩余风险集中在外部基础设施egress。 | 保持manual默认拒绝、IP pinning、Host/SNI原域名和逐跳检查；production必须用egress proxy/NetworkPolicy/firewall再阻断私网、metadata、loopback。 | 应用单测与容器namespace trap smoke PASS；待外部production firewall、Kubernetes NetworkPolicy/云egress policy及部署环境smoke。 |
| High | 上传代理内存和连接耗尽 | 路由层已完成分块/spool/显式 timeout、第32刀并发反压与第37刀disposable container资源验收；第39刀又在parser内部增加全局/owner任务数与字节数的原子队列准入、503+Retry-After和health容量快照。 | 保持413、`SpooledTemporaryFile`、connect/write/read/pool timeout和两层准入；下一步在真实部署入口补LB buffering、宿主机压力和真实parser延迟分布，多副本迁移到共享durable queue并补worker crash takeover。 | helper/document/workspace/chat/parser/compose回归与disposable API container并发/RSS smoke均PASS；待外部production ingress/LB、宿主机整体压力、真实parser网络分布和多实例共享队列验收。 |
| High | 向量 collection 映射和 Milvus schema 策略 | 已完成第九刀、第25/26刀本机 v2 migration与第35刀双后端：US/US_SEC collection单源化；schema mismatch fail-closed；本机1,630条profile seed完成reindex/manifest/alias回滚往返；research-scoped A/B/unscoped/partial在真实临时pgvector+Milvus以同fixture 8/8对账。证据仍是本机隔离数据，不能外推外部生产。 | 保持 production 禁止 destructive recreate；在外部环境重复只读inventory/权威backfill、staged migration、alias reload/rollback，并执行真实数据规模与性能验收。 | planner、manifest、staged executor、alias往返、profile retrieval与research-scoped双后端均本机PASS；待外部production inventory/migration、运行API alias reload与规模/性能E2E。 |
| P1 | 后台任务和 Deal 状态并发语义不足 | 本机文件型状态已完成第30刀加固：原子写/锁/CAS、跨进程job合并、heartbeat/lease、stale recoverable和幂等claim均已实现；不会再静默吞持久化失败或自动重放不可重建闭包。外部多实例共享存储和durable worker仍未证明。 | 保持现有文件模式的锁/租约/幂等契约；生产多副本下一阶段迁移到PostgreSQL/Redis backed queue/job state，以数据库事务、唯一键、worker ownership和可观测重试替代本机`fcntl`。 | 本机多线程/多进程、restart/stale lease/heartbeat/重复提交已PASS；待多容器共享后端、worker crash takeover、数据库唯一键和队列级端到端验收。 |

#### 修订后的止血顺序

1. 立即止血：Deal/workflow 授权、银行指标错配、跨市场 legacy fallback、生产 Web API 代理、Python 依赖漏洞。
2. 一周内：统一 `ResearchIdentity`、claim 级证据校验、上传限流、US collection 单源化、Milvus 禁止生产自动 drop。
3. 两到四周：持久化任务队列、Deal 事务化、生产镜像 E2E、前端聊天性能和无障碍治理。

#### 修订后的发布验证矩阵

| 验证域 | 必跑证据 |
| --- | --- |
| Auth/BOLA | Deal 和 workflow 双用户 HTTP 负向测试，覆盖 list/detail/write/job enqueue。 |
| 金融事实准确性 | PDF parser 银行样本、金融 QA claim verifier 负向样本、多市场 fallback fail-closed trace。 |
| 多市场 HTTP identity | HK/JP/KR/EU/US chat 请求携带 `ResearchIdentity` 后，Wiki/PostgreSQL/Milvus/audit trace 均绑定同一 identity。 |
| 生产容器 | Web 生产镜像启动后 `/api/health`、登录、cookie/CSRF、主要页面 API 调用 smoke 通过。 |
| Supply chain | API、finder、rules 的生产锁文件 `pip-audit` 无 HIGH/CRITICAL；前端 `npm audit --omit=dev` 保持 0。 |
| SSRF / 上传资源 | SSRF rebinding 负向测试、metadata IP 阻断、pinned transport 回归、真实 egress policy smoke、大文件上传 413/429、parser timeout smoke。 |

### 2026-07-12 多智能体复审刷新（实施前问题快照）

本轮复审由四条并行视角完成：Agent / 后端可信问答、PostgreSQL / 数据门禁、前端多市场工作台、工程质量 / CI / 安全 / 性能。结论是：M1 的核心闭环已经从“规划”进入“可运行门禁 + 可审计回答 + 多市场前端入口”的阶段，后续优化不应再泛泛追加功能，而应围绕以下硬缺口收敛。

> 状态说明：下列 P0 条目保留为复审发现、目标行为和验收定义；其当前实施状态以上方“M1-S 实施收口结果”为准。

#### 已确认的真实能力边界

| 能力 | 当前状态 | 边界说明 |
| --- | --- | --- |
| 金融问答来源链 | 已形成 `Wiki-first / PostgreSQL fallback / audit trace` 主链路。 | 真实回答不做 Wiki/PostgreSQL 实时对照；对照只进入离线 gate。 |
| PostgreSQL 多市场入库 gate | HK / JP / KR / EU / US 已有 `document_full -> schema -> idempotency -> evidence -> agent view -> parity` 闭环。 | CN / A 股仍走现有 legacy 链路，不纳入当前非 A 多市场 PostgreSQL gate。 |
| 前端多市场入库工作台 | HK / JP / KR / EU generic PDF 和 US SEC 工作台已有统一四阶段状态雏形。 | generic 非 PDF 结构化解析入口仍未真正接线；US 多 filing 选择仍需从 ticker 级改为 package / accession 级。 |
| specialist 工作流 | 工作树正在形成 factcheck / tracking / legal 的确定性 artifact workflow、引用校验和审计扩展。 | 当前应作为 M1.5 能力面继续收敛，尚需统一 `audit_trace_id`、artifact contract、质量门禁和回归测试。 |
| 工程治理 | release gate、安全启动、容器非 root、轻量 metrics、touched Python quality、大文件检查等已开始落地。 | CI API 测试仍是白名单，market ingestion eval 仍未硬失败，ruff 仍偏 advisory，Milvus/MinIO hardening 未完成。 |

#### P0：必须优先补齐的发布阻断项

1. **金融事实无证据时必须硬阻断。**
   - 当前风险：financial evidence guard 在无 deterministic fallback 时仍可能返回原始回答。
   - 目标行为：凡涉及财务数值、期间、币种、单位、同比/占比计算，若没有 Wiki evidence、PostgreSQL agent fact 或 calculator trace，必须返回“证据不足，不能确定回答”，并写入 `guardrail_result.blocked=true`。
   - 验收：新增端到端测试覆盖 Wiki 命中、Wiki miss + PostgreSQL 命中、PostgreSQL unavailable、invalid task_id、无 fallback 五种路径。

2. **统一 ChatContext / audit event 传递。**
   - 当前风险：Pydantic `ChatContext` 与 dict context 混用时，`_audit_fallback_events`、resolved company、period、trace metadata 可能丢失。
   - 目标行为：router/runtime 边界统一归一化为 dict payload；所有 fallback reason 均进入 `answer_audit_trace`。
   - 验收：非流式和流式 specialist chat 均能回放 `wiki_structured_miss`、`wiki_fulltext_miss`、`postgres_unavailable`、`market_view_hit` 等原因。

3. **CI 不再只跑 API 白名单。**
   - 当前风险：`apps/api/tests/test_*.py` 已超过 90 个，CI 仅白名单跑约 24 个，新增边缘路由可绕过回归。
   - 目标行为：短期增加“白名单覆盖率检查”，中期按模块矩阵全量跑 API tests；慢测用 marker 分层而不是静默跳过。
   - 验收：CI 输出实际发现的 test files、运行的 test files、未运行清单；未运行非 slow/network 测试时失败。

4. **Market ingestion eval 变成硬门禁。**
   - 当前风险：`run_market_ingestion_eval.py` 主要写报告，CI 只检查 case 数，未按 fail / missing / blocked 阻断。
   - 目标行为：脚本产生明确 `passed` 和非零退出码，或 CI 检查 `summary.fail == 0`、`missing_package == 0`、`eval_gate_status.block == 0`。
   - 验收：构造失败 fixture 时 CI job 必须失败。

5. **PostgreSQL gate 边界和 DDL 权威必须写死。**
   - 当前风险：`market_ingestion_contract.py` 可生成 reset-style DDL，runtime import 使用 checked-in additive DDL，存在 schema authority 漂移。
   - 目标行为：明确 `db/ddl/*.sql` 是 runtime DDL 权威，生成式 DDL 只允许 test/contract dry-run；或反向把生成结果纳入 DDL diff gate。
   - 验收：新增 schema contract 测试，DDL 漂移时失败；文档明确 CN/A 股不在非 A 多市场 gate 内。

6. **前端 task/package scoped state 不得串档。**
   - 当前风险：切换完成任务时，workflow/source/PostgreSQL 状态可能沿用上一任务缓存；US SEC 同 ticker 多 filing 可能展示或入库错 package。
   - 目标行为：workflow status、source workbench、selected package、Markdown、quality、PostgreSQL status 全部以 `taskId` 或 `packagePath/accession/document_full_path` 为 key。
   - 验收：新增 task switching、US same-ticker multi-filing、PostgreSQL action payload 测试。

7. **安全止血补完。**
   - 当前风险：Milvus/MinIO 仍存在默认凭证/公开端口；`apps/api/start.sh` 可能原样打印含密码 DB URL。
   - 目标行为：默认凭证改环境变量，端口默认 loopback/internal network；所有日志打印连接串必须脱敏。
   - 验收：security hygiene 检查覆盖 Milvus compose、DB URL redaction、生产 profile。

#### P1：下一批高收益落地项

1. **specialist artifact workflow 统一契约。**
   - 将 factcheck、tracking、legal 纳入统一 artifact contract：`artifact_type`、`company_id`、`source_report_path`、`output_path`、`html_url`、`citations`、`validation_result`、`audit_trace_id`。
   - `answer_audit_trace` 已开始扩展 `legal_facts`，下一步要把 factcheck/tracking/legal 都纳入 `source_type` 和引用字段白名单。
   - legal opinion 必须保持专业条件性表达，禁止绝对承诺；tracking 必须记录 PostgreSQL 只读查询和跟踪模块状态；factcheck 必须输出 claim-level verdict。

2. **Hermes PostgreSQL 只读查询助手产品化。**
   - `agents/hermes/profiles/shared/scripts/pg_query.py` 已向项目 PostgreSQL 配置靠拢，后续应统一环境变量读取、SQL 只读 parser、超时、row limit、输出脱敏。
   - 所有 specialist profile 不再引用旧 `127.0.0.1:5432 / ai_platform / dgx`。
   - 高风险 SQL keyword、跨 schema 查询、无 limit 大查询需要有明确错误码。

3. **多市场 importer 写入前质量门禁。**
   - 当前 importer 只做 facts/chunks/citations 非空检查；更强质量判断主要在 gate。
   - 后续应把 `quality_gate_guard` 中与事实完整性、document_full identity、证据可追溯有关的硬条件前移到 import plan，至少给出 `import_blocked/action_blocked/force_needed_reason`。
   - 允许 warning 入库，但必须在前端状态卡和 release report 中展示 warning code。

4. **parity warning 策略明确化。**
   - 保持“实时回答不做对照”，但离线 parity 要区分 release blocking 和 observability warning。
   - `value_mismatch`、`postgres_missing`、`wiki_missing` 默认阻断；`unit_display_diff`、`period_alias_diff`、`currency_label_diff` 可按市场设 warning 阈值。
   - 每个 warning 必须有 diff code、market、case_id、metric、period、wiki_value、postgres_value。

5. **前端审计入口一等化。**
   - UI 不应只从回答正文里的“审计详情”文本解析 trace id。
   - `audit_trace_id` 应作为 message model / renderer prop / action button 的一等字段；历史消息加载也能显示审计入口。
   - 长任务按钮要显示 step、stdout/stderr 摘要、timeout、retry scope、disabled reason。

6. **Python 质量门禁从 advisory 走向 fail-fast。**
   - CI 安装 ruff，对 touched Python files 使用 `--require-ruff`。
   - pre-commit 纳入 ruff、shellcheck、actionlint、eslint、large-file gate。
   - mypy 仍只对白名单模块逐步开启，不直接压到历史大文件。

7. **性能 quick wins 先做可测小改。**
   - `UsageEvent` 查询改 SQL `SUM()` 并补 `(user_id, event_type, event_date)` 复合索引。
   - `ChatMessage` 补 `(session_id, created_at)` 复合索引。
   - 建慢查询 / explain 基线后再进入分区、异步化、队列化大改。

#### P2：工程结构和体验持续收口

1. **继续小刀拆大文件。**
   - 当前 `agent_chat_runtime_impl.py` 约 5433 行，`market_reports.py` 约 1503 行，`pdf_parser_app_impl.py` 约 4496 行。
   - 优先把 facade-only 模块变成真实 owner：sessions、tools、source/provenance、market report orchestration；attachments 已完成真实 owner 化，streaming active-run state 已有 owner 但仍可继续瘦 runtime 调用点。
   - 每次拆分只做一个 owner 边界，带回归测试，不做全仓格式化。

2. **流式运行状态治理。**
   - 当前 active run/event state 仍主要是进程内，短期文档化 sticky single-worker 限制。
   - 中期迁移到 Redis / Postgres backed state，支持重启、断线重连和多 worker。

3. **generic 非 PDF 结构化解析接线。**
   - 前端已展示能力暗示，但 generic `MarketParsingPage` 未传 `buildDownloadedPackage`，`canBuildDownloadedPackage` 仍为 false。
   - EU ESEF / iXBRL / XHTML / ZIP 应通过 package build 接入，HK/JP/KR 继续保持 PDF-only 行为。

4. **报告和 artifact 卫生。**
   - 大型 JSON、绝对路径、真实样本细节默认进入 ignored `artifacts/eval-runs/`。
   - tracked `eval_datasets` 只保留小型 fixture、schema、脱敏摘要；`docs/reports` 保留人工可读 Markdown。

5. **Milvus / embedding / 大对象性能优化进入度量阶段。**
   - 已先把 embedding throughput 与 Milvus retrieval latency 接进 nightly optional probes；下一步在 self-hosted 明确 endpoint、collection seed 和依赖安装后启用 hard gate，再决定 HNSW、缓存、流式 JSON、任务队列重构。

#### 更新后的验证矩阵

| 验证域 | 必跑命令 / 期望 |
| --- | --- |
| 金融问答契约 | `cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_answer_audit.py tests/test_agent_runtime_postgres_fallback.py tests/test_agent_chat_runtime_loops.py -q` |
| 多市场 PostgreSQL contract | `python3 scripts/maintenance/run_market_document_full_postgres_gate.py --mode contract`，不连 DB，不刷新 tracked report。 |
| 多市场 PostgreSQL strict | `python3 scripts/maintenance/run_market_document_full_postgres_gate.py --mode offline-postgres`，仅 self-hosted / 本地数据环境执行，HK/JP/KR/EU/US 要求 acceptance pass。 |
| Market ingestion eval | `python3 scripts/maintenance/run_market_ingestion_eval.py --strict`，失败 case 必须非零退出。 |
| 前端多市场工作台 | `cd apps/web && npm run test:unit && npm run check:frontend`，补 task scoped state、same ticker filing、audit trace prop 测试。 |
| 安全和质量 | `python3 scripts/maintenance/check_local_security_hygiene.py --scope workflow`、`python3 scripts/maintenance/check_python_quality_touched.py --json --require-ruff`。 |

### P0：金融准确性和可审计问答底座

#### P0-1. 建立 Agent 查询指引和机器门禁的统一契约

目标：把现有 Hermes 文档规则和 PostgreSQL view 门禁统一成一个可执行契约。

落地动作：

1. 新增 `docs/architecture/agent-financial-query-contract.md`。
2. 定义财务问答的来源优先级：Wiki metrics 为主，PostgreSQL view 只做 fallback 补缺，semantic 只补解释，LLM 语义不得作为数值来源。
3. 定义 `AgentFinancialFact` 字段；字段清单以 `docs/architecture/agent-financial-query-contract.md` 的 `AgentFinancialFact` 表和 `db/imports/financial_query_api.py::AGENT_FINANCIAL_FACT_FIELDS` 为单一事实源，测试会直接比较二者，防止文档和代码漂移。
4. 将 `agents/hermes/profiles/shared/rules/financial_source_routing_contract.md` 与该契约互相引用。
5. 在 `db/imports/financial_query_api.py` 的返回结构中对齐契约字段，避免前端/Agent 看到市场特有表结构。

验收：

```bash
python3 -m pytest db/imports/tests/test_financial_query_api_market_agent.py -q
cd apps/api && .venv/bin/python -m pytest \
  tests/test_agent_runtime_postgres_fallback.py \
  tests/test_agent_chat_runtime_loops.py::test_financial_source_routing_contract_is_exposed_to_agent_profiles -q
```

#### P0-2. 固化 Wiki/PostgreSQL 对照检查为常规回测

目标：确保同一指标在 Wiki 和 PostgreSQL 中差异可发现、可解释、可阻断；该能力仅用于离线回测、nightly gate 和 release gate，不进入真实回答实时产出路径。

落地动作：

1. 在现有 `market_document_full_postgres_backtest.py` 基础上拆出 parity runner。
2. 每个市场维护 10-20 个核心问题样本：
   - 营收、净利润、总资产、总负债、权益、经营现金流、EPS、毛利率、ROE。
   - US/EU 增加 XBRL concept / IFRS taxonomy 样本。
   - HK/JP/KR 增加本地语言科目样本。
3. 对照规则：
   - value、period、unit、currency 一致为 PASS。
   - 仅展示单位不同但标准化值一致为 WARN。
   - 值不同、期间错配、币种错配、证据缺失为 FAIL。
4. 将 parity warnings 分类：`unit_display_diff`、`period_alias_diff`、`currency_label_diff`、`wiki_missing`、`postgres_missing`、`value_mismatch`。

验收：

```bash
python3 db/imports/backtests/market_document_full_postgres_backtest.py \
  --db \
  --import-before-db-check \
  --idempotency \
  --production-sample-db \
  --production-agent-query
```

#### P0-3. 答案级审计 trace

目标：任何金融分析问答都能回放“为什么这么答”。

落地动作：

1. 在 Agent runtime 生成结构化 `answer_audit_trace`。
2. trace 默认写入本地 runtime 目录或会话 artifact，不直接暴露数据库口令。
3. 前端保持现有框架，优先使用响应结构化 `audit_trace_id` 渲染可按 `trace_id` 展开的“审计详情”；生产回答默认不把审计摘要写入可见正文。
4. trace 中必须包含查询计划、事实命中、计算器输出、引用、guardrail 结果；若触发 PostgreSQL fallback，记录 fallback 原因和命中的数据库事实。
5. 为每条 trace 生成稳定 `trace_id`，非流式 `ChatResponse.audit_trace_id` 和流式 done payload `audit_trace_id` 都应透出该 ID，并通过主聊天只读 API 按当前用户会话权限读取完整脱敏 JSON。

验收：

```bash
cd apps/api
.venv/bin/python -m pytest tests/test_agent_runtime_answer_audit.py tests/test_agent_runtime_postgres_fallback.py -q

cd ../web
node --import ./scripts/register-node-test-alias-loader.mjs --test \
  src/lib/agentChatStream.test.ts \
  src/components/chat/renderers/rendererUtils.test.ts \
  src/components/chat/renderers/auditTraceRenderer.test.ts
```

### P1：多市场入库和发布门禁产品化

#### P1-1. release gate 命令化

目标：把强回测从“知道要跑”变成“一条命令必跑”。

已新增：

```text
scripts/ops/run_market_postgres_release_gate.sh
```

命令内部执行：

```bash
bash scripts/ops/run_market_postgres_release_gate.sh \
  --mode offline-postgres \
  --output-dir artifacts/eval-runs/release
```

该 wrapper 聚合三类 artifact：`market_document_full_postgres_*_gate.json/md`、
`financial_qa_benchmark_trace_offline.json/md` 和 `financial_qa_benchmark_wiki_static.json/md`。
大型回测报告默认写入 `artifacts/`，不在普通开发中回写 `docs/reports` 或 `eval_datasets`。
PR contract 模式不连接 PostgreSQL，也不要求 manifest 所列真实样本文件存在；offline-postgres 模式仍要求真实样本文件存在并完成 PostgreSQL 导入、幂等、Agent view 和离线 parity 检查。

验收：

- 本地可一键运行。
- 输出 Markdown 摘要和 JSON 报告 artifact。
- 失败时明确列出市场、样本、指标、证据缺失或 parity 差异。

#### P1-2. CI/nightly 分层

目标：避免每次 PR 都跑重型真实样本，但发布前一定跑。

建议 CI 分三层：

| 层级 | 触发 | 内容 |
| --- | --- | --- |
| PR quick gate | pull_request | 单测、lint、build、轻量 fixture backtest。 |
| Manual release gate | workflow_dispatch | 在 self-hosted 数据环境启动 Postgres，跑真实样本 DB + Agent query + parity，并跑金融 QA `trace-offline` / `wiki-static`。 |
| Nightly gate | schedule | 在 self-hosted 数据环境跑全市场真实样本、金融 QA、报告上传 artifact、失败通知。 |

新增 workflow：

```text
.github/workflows/market-postgres-release-gate.yml
```

验收：

- PR 不明显变慢。
- 手动触发能跑完整门禁。
- 报告作为 GitHub artifact 上传，不强制把大型 JSON 每次提交进仓库。

#### P1-3. identity / selector 单源化

目标：消除“已入库但状态查询未命中”的假阴性。

建议新增模块：

```text
apps/api/services/market_document_identity.py
```

职责：

| 函数 | 说明 |
| --- | --- |
| `resolve_document_full_identity()` | 从 market、task_id、filing_id、document_full_path、package_path 解析统一身份。 |
| `build_import_selector()` | 生成 importer 用 selector。 |
| `build_status_selector()` | 生成 document-full/status 用 selector。 |
| `build_agent_query_scope()` | 生成 Agent view 查询 scope。 |

验收：

```bash
cd apps/api
.venv/bin/python -m pytest tests/test_market_report_commands.py tests/test_market_reports_proxy.py -q
```

### P2：前端入库工作流统一，不改整体框架

#### P2-1. 统一 MarketIngestionPipelineState

目标：保留现有页面和按钮布局，但统一非 A 市场状态模型。

已落地：

```text
apps/web/src/features/market-parsing/marketIngestionPipelineState.ts
```

状态：

```ts
type MarketPipelineStage =
  | 'raw_ready'
  | 'wiki_ready'
  | 'semantic_ready'
  | 'postgres_ready'
  | 'agent_query_ready'
  | 'warning'
  | 'failed'
  | 'unknown'
```

按钮状态从 pipeline state 派生：

| 按钮 | 可点击条件 | 成功后目标状态 |
| --- | --- | --- |
| LLM-Wiki入库 | `raw_ready` 或更高 | `wiki_ready` |
| Wiki语义增强入库 | `wiki_ready` | `semantic_ready` |
| PostgreSQL入库 | `raw_ready` 或 `wiki_ready` | `postgres_ready` |
| 一键入库 | `raw_ready` | 顺序执行到 `postgres_ready`，有条件再到 `agent_query_ready` |

验收：

```bash
cd apps/web
npm run test:unit -- MarketParsingPage packageActions usSecWorkbench PdfWorkflowPanel
npm run check:frontend
```

#### P2-2. 前端状态展示一致化

目标：HK / JP / KR / EU / US 状态展示按 A 股的清晰度对齐。

落地动作：

1. 统一状态文案：未解析、Wiki 已生成、语义增强已生成、PostgreSQL 已入库、证据不足、入库失败。
2. 每个状态展示同一组核心计数：parse_runs、facts、tables、chunks、evidence、schema、parse_run_id。
3. PostgreSQL ready 的定义引用 `market-postgres-schema-equivalence.md`，并在 `/api/workflow/task/{task_id}/status` 与 `/api/market-reports/document-full/status` 保持一致：

```text
postgres_ready = parse_runs > 0 and facts > 0 and tables > 0 and chunks > 0 and evidence > 0
```

验收：

- `marketIngestionPipelineState.test.ts`、`usSecWorkbench.test.ts`、`pdf-parsing/api.test.ts` 和 `MarketParsingPages.test.ts` 已覆盖四阶段状态、五项计数 ready、US `full_document_paths` 推导和非 A 市场按钮 wiring。
- `npm run check:frontend` 已通过。

### P3：核心 owner 文件渐进瘦身

#### P3-1. Agent runtime 拆分

目标：降低金融问答改动风险。

边界红线：

- Agent runtime owner 只承接问答运行态、证据选择、引用校验、上下文/prompt 组装、展示 formatter 和审计 trace；不得继续吸收 PDF parser / Wiki builder / PostgreSQL importer / semantic enrichment / vector ingest 的脚本执行或产物生成职责。
- 若 Agent 对话需要解析侧能力，可在 Agent 层做只读编排和依赖注入，例如调用已存在的 artifact descriptor、source map、证据行、状态摘要或查询接口；但解析主功能本身，包括 parser 任务提交、产物生成、目录扫描、manifest/schema 解释、package build/import/vector ingest，不得拆进 Agent runtime owner。
- Parser / ingestion artifact discovery 应沉到中立 repository / locator / ingestion service，例如解析任务目录扫描、manifest/schema 解释、package build/import/vector 命令编排；Agent 只能消费稳定 artifact descriptor、证据行或只读查询结果。
- 对已存在的灰区保持“先适配、后迁移”：`agent_runtime_task_ids.py` 的 task_id 证据可用性检查、`agent_runtime_parse_only.py` 的 parse-only context formatter 和 `agent_runtime_wiki_context.py` 的 Wiki/document_full 只读兜底允许短期保留，但后续不得再扩大 parser 目录结构知识；若继续整理，应优先抽中立 artifact locator，再由 Agent 注入调用。

当前进展：

- `agent_runtime_answer_audit.py` 已承接答案级 trace 生成和落盘。
- `agent_runtime_context.py` 已承接财务上下文/意图纯 helper、goodwill 主表查询/直答判断、公司 scope/context item 编排、session contextual input prompt 组装和 Hermes multimodal run input payload 选择；runtime 仅保留注入依赖的薄 wrapper。
- `agent_runtime_attachments.py` 已承接附件安全路径、图片多模态预处理、PDF 附件等待/上下文、历史附件抽取、`ChatMessage.attachments_json` 解析和附件-only 消息可见性判断；runtime 保留同名 wrapper，并同步 `CHAT_UPLOAD_ROOT`、image model 和附件 migration ready state，兼容既有 monkeypatch 测试。
- `agent_runtime_preflight.py` 已承接 chat request envelope、catalog/general short-circuit plan、历史/附件/本地记忆预加载，以及 local memory 与 agent memory 的 preflight 合并；runtime 继续保留 thin wrapper 注入 history/attachment/memory loader。
- `agent_runtime_memory.py` 已承接 local memory 摘要、持久化、加载、刷新和 agent memory 检索上下文编排；runtime 只保留 env 配置读取与 owner 调用 wrapper，避免在主 runtime 内直接散落 vector memory timeout/rollback 逻辑。
- `agent_runtime_streaming.py` 已承接 `ActiveRunState`、`ACTIVE_RUNS`、active run snapshot/SSE replay、stop/terminal event 写入，以及 tool.started/tool.completed 的进度投影和循环计数状态更新；runtime 仍负责调用 Hermes stream 与最终回答审计/落库。
- `agent_runtime_dedupe.py` 已承接 recent completed-run 幂等记忆：`RecentRunRecord`、`RECENT_COMPLETED_RUNS`、duplicate fallback、forget 和 remember 逻辑均由 owner 持有；runtime 继续保留 `_recent_duplicate_reply` / `_forget_recent_completed_run` / `_remember_completed_run` 薄 wrapper，并把全局状态别名指向 owner，兼容既有调用点与测试。
- `agent_runtime_task_ids.py` 已承接 task_id 证据链路径判断：task_id 正则抽取、PDF2MD result/output 目录存在性、Wiki company/report manifest 扫描、reply 中无效 task_id 判定均由 owner 持有；runtime 继续保留 `_task_id_exists` / `_invalid_task_ids_in_reply` 等薄 wrapper，并注入 roots、profile-aware `WIKI_ROOT` 和 company resolver。
- `agent_runtime_wiki_context.py` 已承接 Wiki report 选择、company artifact paths、company scope prompt、document_full 全文检索、wiki fulltext fallback result/render 编排；runtime 继续保留 `_primary_report_for_company`、`_company_artifact_paths`、`_wiki_fulltext_fallback_result`、`build_wiki_fulltext_fallback_context` 和 `build_company_wiki_scope_context` 等 wrapper，注入 profile-aware `WIKI_ROOT`、resolver、JSON reader 与 evidence URL，兼容既有 monkeypatch / tool facade。
- `agent_runtime_postgres_fallback.py` 已承接 financial_query_api loader、PostgreSQL fallback 适用性判断、query parse、metric term predicate、financial query connection factory、multi-market agent view fallback、legacy metric row query、`pdf2md.document_tables` 页码补全、完整 fallback result 编排、PostgreSQL fallback context builder 和 fallback audit event/context helper；runtime 仍保留薄 wrapper 与 legacy 调用点兼容。
- `agent_runtime_financial_sources.py` 已承接主要数据证据补充编排：Wiki metrics/note 引用缺口判断、Wiki metrics 文件引用归一化、human efficiency / human capital / statement / note / fulltext / PostgreSQL fallback 的补充来源优先级；runtime 仍保留薄 wrapper 与 guardrail 调用点兼容。
- `agent_runtime_financial_format.py` 已承接 human efficiency / generic human efficiency 证据 Markdown 格式化；runtime 通过注入 calculator 与 source-link helper 保持现有引用链接契约。

建议从 `apps/api/services/agent_chat_runtime_impl.py` 继续下沉：

| 新 owner | 迁移内容 |
| --- | --- |
| `agent_runtime_query_plan.py` | 财务问题解析、公司/期间/指标意图。 |
| `agent_runtime_financial_sources.py` | Wiki / PostgreSQL / semantic 来源调度。 |
| `agent_runtime_answer_audit.py` | answer_audit_trace 生成和落盘。 |
| `agent_runtime_guardrails.py` | 证据缺失、冲突、不可确定回答的阻断。 |

策略：

- 每次只迁一个纯 helper 或薄编排函数。
- 保留旧 wrapper，先测后删。
- 不在同一 PR 中同时改前端状态和 Agent runtime。

验收：

```bash
cd apps/api
.venv/bin/python -m pytest tests/test_agent_runtime_*.py tests/test_agent_chat_runtime_loops.py -q
```

#### P3-2. market_reports router 拆分

目标：把 API 路由从命令编排中解耦。

当前进展：

- `market_report_status_service.py` 已承接 package quality payload、package quality response 组装、load-plan summary 和 load-plan decision -> quality gates 合并逻辑；`market_report_eval_service.py` 已承接 market ingestion eval 的 plan/args/命令执行/报告读取调度；`market_report_postgres_service.py` 已承接 document_full PostgreSQL import 命令/env/identity/metrics 编排和 import/status payload 组装；`market_report_queueing.py` 已承接 wait/queue 分流与 job status 对外投影；`market_report_package_service.py` 已承接 market package build/import/vector ingest、list/detail/quality/file/evidence route payload、US SEC latest case selector、case-set status payload、package detail by ticker、semantic company-dir 解析、semantic pre-step、case-set ingest、semantic-only 响应和 rebuild package 编排；这些 service 通过 router wrapper 注入命令 runner、JSON reader、路径展示函数、数据库状态函数、metrics recorder 或 job service，以保持既有 monkeypatch 契约；`market_reports.py` 继续保留少量兼容 wrapper、FileResponse 和 HTTP error 映射。

建议 owner：

| 新 owner | 迁移内容 |
| --- | --- |
| `market_report_queueing.py` / `job_service.py` | wait/queue 分流、job 创建、轮询、状态对外投影。 |
| `market_report_package_service.py` | package build/import/vector ingest、list/detail/quality/file/evidence route payload、US SEC latest case selector / case-set status / package detail by ticker / semantic pre-step / semantic-only / case-set ingest / rebuild package。 |
| `market_report_postgres_service.py` | document_full import、status、cleanup。 |
| `market_report_eval_service.py` | ingestion eval 和 release gate 调度。 |

验收：

```bash
cd apps/api
.venv/bin/python -m pytest tests/test_market_reports_proxy.py tests/test_market_report_commands.py -q
```

#### P3-3. backtest runner 拆分

目标：保持强验收能力，同时降低单文件复杂度。

建议模块：

| 模块 | 职责 |
| --- | --- |
| `document_fact_normalizer.py` | document_full 事实归一化、identity、证据判断、数值比较、稳定 hash；已落盘并由 runner 直接 re-export。 |
| `contract_cases.py` | fixture case 合约、断言检查和断言统计；已落盘并由 runner 同名 wrapper 委托调用。 |
| `postgres_roundtrip_helpers.py` | PostgreSQL relation/table helper、作用域 selector、表族/表计数、content hash、required-evidence 检查；已落盘并由 runner 直接 re-export 兼容名。 |
| `postgres_roundtrip_gate.py` | DB 导入、DDL 首次执行、幂等编排、`check_db_case` / `check_db_case_sequence`；已落盘并由 runner 同名 wrapper 委托调用。 |
| `production_sample_gate.py` | manifest、真实样本、共存检查；manifest v1 校验、真实样本 case 生成和 parse_run 共存检查已落盘。 |
| `agent_view_parity_helpers.py` | Agent view / Wiki-PostgreSQL parity 纯比较、diff code 汇总、自动问题生成；已落盘并由 runner 直接 re-export 私有兼容别名。 |
| `agent_query_gate.py` | `v_agent_financial_facts` 固定问题查询和真实样本 parse_run 探针；已落盘并由 runner 同名 wrapper 委托调用。 |
| `wiki_postgres_parity_gate.py` | Wiki/document_full 与 PostgreSQL Agent view 离线对照；已落盘并由 runner 同名 wrapper 委托调用，保留 explicit parity hard fail 和 generated parity warning 降级语义。 |
| `report_writer.py` | Markdown / JSON 输出；已落盘并由 runner 同名 wrapper 委托调用。 |

验收：

```bash
python3 -m pytest db/imports/tests/test_market_document_full_postgres_backtest.py -q
python3 db/imports/backtests/market_document_full_postgres_backtest.py
```

### P4：一二级市场平台化能力

#### P4-1. 二级市场：高可信问答 benchmark

目标：让“金融分析问答绝对准确”有可量化门槛。

建议新增数据集：

```text
datasets/eval/financial_qa_benchmark/v1/
  cases.jsonl
  traces/
    p0_golden_traces.jsonl
```

v1 默认模式是 `trace-offline`：只读取黄金 case 和预录 `answer_audit_trace`，校验关键事实、期间、单位/币种、resolved identity、精确 evidence、来源策略、calculator runs、guardrail，不调用实时 LLM，也不连接 PostgreSQL。`wiki-static` 模式直接读取 case 引用的 `document_full.json`，用于检查 Wiki fact fixture 是否漂移；`postgres-fallback` 后续只放 manual/nightly gate。

每条 case：

```json
{
  "question": "2025 年营收是多少？同比变化如何？",
  "market": "HK",
  "company_id": "HK:00700",
  "report_id": "2025-annual",
  "expected_facts": [
    {
      "canonical_name": "revenue",
      "period": "2025",
      "value": "...",
      "currency": "HKD",
      "unit": "..."
    }
  ],
  "required_evidence": ["page_number", "table_index", "quote_text"],
  "requires_calculator": true
}
```

评分：

| 指标 | 门槛 |
| --- | --- |
| 关键数值准确率 | P0 样本 100%。 |
| 期间/币种/单位准确率 | P0 样本 100%。 |
| evidence 覆盖率 | P0 样本 100%，P1 样本 >= 98%。 |
| 计算一致率 | 使用 calculator 的派生指标 100%。 |
| 不确定性处理 | 缺证据时必须拒绝确定回答。 |

#### P4-2. 一级市场：Deal OS 审计对齐

目标：把一级市场 IC / Deal OS 与二级市场共用“证据、计算、引用、审计”语言。

落地动作：

1. 对 Deal documents 建立 `deal_evidence_package` contract。
2. 一级市场问答同样生成 `answer_audit_trace`。
3. IC 结论区分事实、假设、模型推演和投资判断。
4. 每个投资建议必须引用：
   - 原始尽调材料或会议纪要。
   - 财务模型输入。
   - 关键假设来源。
   - 风险和反证材料。

验收：

```bash
cd apps/api
.venv/bin/python -m pytest tests/test_deal_store.py tests/test_deals_router.py tests/test_ic_policy.py tests/test_hermes_ic_profiles.py -q
```

### P5：运维、数据治理和发布质量

#### P5-1. 生成物治理

目标：减少评审噪音。

规则：

- 大型 JSON 报告默认进入 `artifacts/` 或 CI artifact。
- 仓库保留 Markdown 摘要和小型 baseline。
- `eval_datasets/.../backtest_report.json` 若继续入仓，应只在 release gate 更新，不和功能改动混批。

验收：

```bash
git diff --stat
git diff --check
```

#### P5-2. 数据清理和非 A 市场重复入库治理

目标：支持用户允许的“非 A 市场历史重复入库可删除”，但 A 股不动。

落地动作：

1. 保留 `db/imports/cleanup_market_document_full_parse_runs.py` 作为显式工具；当前已支持 `--parse-run-id` / `--parse-run-id-file`、`--company-id`、`--filing-id`、`--older-than` 多 selector dry-run，并要求至少一个 selector，避免误删整市场。`--older-than` 单独使用会被拒绝，除非显式传 `--allow-market-wide-older-than`。
2. 默认拒绝 CN / A 股清理。
3. 支持 dry-run、按 market、company_id、filing_id、parse_run_id、older-than 清理；当前新增 `db/imports/analyze_market_document_full_duplicates.py`，只读分析同一 filing 下多 parse_run 的历史重复候选，输出 latest、candidate obsolete 和可复制 cleanup dry-run 命令。
4. 清理前输出将删除的表族计数。
5. `--apply` 清理后自动输出剩余 parse_run 子表计数和 `v_agent_financial_facts` Agent view 行数探针。

当前状态：多市场 `v_agent_financial_facts` 已在 DDL 层 join `v_latest_parse_runs`，形成默认 latest-successful 语义；需要精确回测某次导入时，release gate 仍可通过 `parse_run_id` 显式探针验证该 run 是否进入 Agent view。历史重复 run 由 analyzer 识别、cleanup 工具显式清理，A 股继续不纳入该清理链路。

验收：

```bash
python3 -m pytest db/imports/tests/test_cleanup_market_document_full_parse_runs.py -q
python3 -m pytest db/imports/tests/test_analyze_market_document_full_duplicates.py -q
python3 -m pytest db/imports/tests/test_market_document_full_writer.py::test_market_agent_fact_views_are_scoped_to_latest_successful_parse_runs -q
python3 db/imports/analyze_market_document_full_duplicates.py --market HK --json
python3 db/imports/cleanup_market_document_full_parse_runs.py --market HK --filing-id <filing_id>
```

#### P5-3. 可观测性

目标：上线演示或内测时能定位“慢、错、缺证据、状态不一致”。

建议指标：

| 指标 | 说明 |
| --- | --- |
| `siq_api_request_total` | 已落地；按 method、path、status_code 统计 API 请求数。 |
| `siq_api_request_duration_ms_sum/count/max` | 已落地；按 method、path 统计 API 请求耗时。 |
| `siq_agent_fact_source_total` | 已落地；从 answer audit trace 统计 Wiki、PostgreSQL、semantic、calculator 等事实/引用来源。 |
| `siq_postgres_fallback_reason_total` | 已落地；统计 PostgreSQL fallback reason，如 `market_view_hit`、`postgres_unavailable`。 |
| `siq_answer_guardrail_block_total` | 已落地；统计 answer audit trace 的 guardrail blocked 状态。 |
| `siq_answer_calculator_run_total` | 已落地；统计回答审计中的 calculator runs。 |
| `siq_answer_citation_total` | 已落地；统计回答审计中的 citations。 |
| `siq_ingestion_duration_seconds_sum/count/max` | 已落地；document_full PostgreSQL import 按 market、stage、status 统计耗时。 |
| `siq_ingestion_fact_count` | 已落地；document_full status 按 market、kind 记录 parse_runs/facts/tables/chunks/evidence 最新计数。 |
| `siq_wiki_postgres_parity_warning_total` | 已落地；读取 market ingestion eval report 时按 market、diff_code 统计离线对照 warning。 |
| `siq_frontend_pipeline_job_failure_total` | 已落地；前端触发的 document_full PostgreSQL import 失败按 market、action、reason 计数。 |
| `siq_background_job_final_state_total` | 已落地；后台 job worker 按 kind、terminal status 统计 succeeded/failed 终态。 |
| `siq_background_job_duration_seconds_sum/count/max` | 已落地；后台 job worker 按 kind、terminal status 统计异步任务耗时。 |

验收：

```bash
cd apps/api
.venv/bin/python -m pytest tests/test_observability.py tests/test_agent_runtime_answer_audit.py -q
.venv/bin/python -m pytest tests/test_job_service.py -q
.venv/bin/python -m pytest tests/test_market_reports_proxy.py::test_market_document_full_import_command_uses_market_script_path_and_env tests/test_market_reports_proxy.py::test_market_document_full_import_failure_records_pipeline_metric tests/test_market_reports_proxy.py::test_market_document_full_import_status_queries_postgres_counts -q
curl -s http://127.0.0.1:18081/metrics | head
```

#### P5-4. Python 质量工具和提交前门禁

目标：把当前 ad-hoc 的 ruff / mypy 使用收束成项目级约束，但避免一次性全仓格式化造成大规模无业务 diff。

本地核验：

- 当前存在 `.ruff_cache/`、`.mypy_cache/`，说明工具曾被局部运行。
- 仓库根目录已提供渐进式 `.pre-commit-config.yaml`、`mypy.ini` 和 touched-files Ruff gate；Ruff 不做全仓历史债务 fail-fast，mypy 只跑显式白名单。

落地策略：

1. 第一阶段新增根级 `ruff.toml` 和轻量 `.pre-commit-config.yaml`，只检查 touched Python files、尾随空格、文件末尾换行、YAML/JSON 基础格式。当前已落地根级配置。
2. CI 和 pre-commit 已统一调用 `scripts/maintenance/check_python_quality_touched.py`。脚本对当前 touched 文件及其 Git base ref 快照分别执行同一版本 Ruff，并按 `path + rule code + message + normalized source range` 生成 SHA-256 fingerprint；基线已有 fingerprint 不阻断，新增 fingerprint 或同 fingerprint 新增出现次数 fail-fast。行号移动不会制造新告警，纯 rename 会映射到旧路径内容；新文件基线为空。
3. CI 固定安装 Ruff 0.14.10 并传入 `--require-ruff`；PR 使用完整 fetch 的 `origin/<base branch>`，push 使用事件 before SHA（不可解析时显式回退 `HEAD^`）。脚本本身对无效 base ref、Ruff 缺失、非 lint 类 Ruff 退出码和无效 JSON 均硬失败，禁止静默回退到空检查。JSON artifact 记录 `baseline_ref`、`ruff_version`、基线/当前/新增数量和新增 fingerprint 明细，可直接审计。
4. mypy 已通过根级 `mypy.ini` 和 `scripts/maintenance/mypy_whitelist.toml` 启动白名单 fail-fast。首批目标为 maintenance quality/performance gate 脚本；后续继续扩到 backtest helpers、market identity、financial query contract，不直接套到 `agent_chat_runtime_impl.py`。
5. 禁止一次性黑盒格式化全仓，避免掩盖业务变更。历史 Ruff 告警只允许随业务触达逐步减少，不提交大规模无业务格式化 diff。

验收：

```bash
python3 -m pytest scripts/maintenance/tests/test_check_python_quality_touched.py -q
python3 scripts/maintenance/check_python_quality_touched.py --base-ref HEAD --require-ruff --json
uv run --with mypy==1.14.1 python scripts/maintenance/check_mypy_whitelist.py --require-mypy --json
python3 scripts/maintenance/run_performance_baseline.py --mode contract --repeat 5 --json
python3 scripts/maintenance/run_performance_baseline.py --mode nightly --repeat 1 --skip-postgres-nightly --json
pre-commit run --files <touched-python-files>
```

#### P5-5. Git 体积和大文件治理

目标：降低 clone、CI cache 和 review 成本，防止二进制资产和临时 diff 继续膨胀 Git 历史。

本地核验：

- `.git` 当前约 2.0GB。
- 已追踪的大文件包含 `apps/web/public/videos/*.mp4`、多份 animated webp，以及 `.superpowers/sdd/*.diff`。
- `data/` 当前约 61GB，但 `.gitignore` 已覆盖 `data/**`，主要风险是未来误追踪或通过 force add 绕过。

落地策略：

1. 不在普通功能开发中执行 history rewrite；Git 历史瘦身单独开维护窗口。
2. 为视频、webp、模型/数据库 dump、运行时 artifact 建立 Git LFS 或外部 artifact policy。
3. 将 `.superpowers/**/*.diff`、本地审查产物、回测大 JSON 明确归为非源码 artifact，后续只保留必要 Markdown 摘要。
4. 增加大文件检查脚本或 CI job，对新增单文件超过阈值的 tracked files 报警；当前已新增 `scripts/maintenance/check_large_file_changes.py`，只检查新增/变更文件，PR 中比较 `origin/<base>...HEAD`，阻止媒体、压缩包、数据库 dump、超阈值图片和 `.superpowers` 本地审查产物继续进入源码历史。
5. 对既有大文件先形成清单和迁移方案，再执行 `git rm --cached` / LFS 迁移；不得删除用户本地原始数据。

验收：

```bash
git ls-files -z | xargs -0 du -b | sort -nr | head -30
git diff --stat
python3 -m pytest scripts/maintenance/tests/test_check_large_file_changes.py -q
python3 scripts/maintenance/check_large_file_changes.py --json
```

#### P5-6. 环境变量路径和本地数据防污染

目标：减少新成员配置困惑，避免运行时数据、数据库文件和本地 Wiki package 被误提交。

落地策略：

1. 明确 canonical 本地环境文件为 `infra/env/local.env`；`env/*.env` 仅作为兼容期入口保留。
2. README、docs 和脚本示例统一优先使用 `infra/env/local.env`。
3. 保持 `data/**`、`data/wiki/`、`data/postgres/`、`data/pdf-parser/results/` 等运行目录默认 ignore。
4. 新增发布/提交前 artifact hygiene 检查：若 tracked diff 中出现 `data/`、数据库 dump、大型 parser result，直接提示拆出 artifact；当前 `check_large_file_changes.py` 覆盖 changed-file 阻断，`check_local_security_hygiene.py --scope workflow` 已锁住 `data/**`、`data/wiki/`、`data/postgres/`、`data/pdf-parser/results/`、`var/**`、`artifacts/**` 和 env 文件 ignore 规则。
5. 对需要入仓的小型 fixture，统一放在 `eval_datasets/` 或 `tests/fixtures/`，并控制大小和 schema 版本。

验收：

```bash
git status --short
git check-ignore data/wiki data/postgres data/pdf-parser/results
python3 scripts/maintenance/check_local_security_hygiene.py --scope workflow
```

#### P5-7. Kimi 优化路线图纳入评估

来源：`docs/optimization-roadmap.md` 从安全、架构、代码质量、测试、性能、DevEx 六个维度提出 5 个阶段、28 个任务。该路线图整体可采纳，但它是全仓工程治理计划，优先级必须服从本方案的主目标：金融问答准确、证据可审计、PostgreSQL / Wiki / Agent release gate 可回归。

立即纳入本方案：

| Kimi 项 | 纳入位置 | 取舍说明 |
| --- | --- | --- |
| S01 密钥安全 | P5-6 / P5-8 | 必须纳入。真实密钥轮换、`infra/env/local.env` 规范化、secret scan 属于上线前安全底线；Git 历史清理单独维护窗口执行。 |
| S02 数据目录权限 | P5-8 | 必须纳入。`data/`、`artifacts/`、`runtime/` 应默认私有，防止本地财报、数据库和审计 trace 泄露。 |
| S03 release gate PostgreSQL 安全 | P1-2 / P5-8 | 已开始落地。offline-postgres workflow 禁用 trust auth，使用密码认证，并将 PostgreSQL 仅绑定到 `127.0.0.1:15432`。 |
| S08 环境治理 | P5-6 | 已纳入。canonical 环境入口统一为 `infra/env/local.env`，再补 minimal example。 |
| Q04 / Q05 ruff、mypy、pre-commit | P5-4 | 已纳入但降级为渐进 touched-files/advisory，避免全仓格式化 churn。 |
| T01 CI 全量回归分层 | P1-2 / M3 | 纳入。PR quick gate、nightly/manual full gate 分层推进，不把真实 DB 大回测塞进普通 PR。 |
| T03 DB 导入测试 | P1 / P3 | 纳入。多市场 document_full importer 已有 backtest gate，后续继续补直接契约测试。 |
| P09 可观测性 | P5-3 | 纳入。健康检查、metrics 和 trace 指标是 production readiness。 |
| P10 DevEx / troubleshooting | P5-6 | 纳入。环境和故障排查文档能降低多人协作成本。 |

纳入但排在 M2/M3 之后：

| Kimi 项 | 排期 | 原因 |
| --- | --- | --- |
| A01 market-contracts 单一事实来源 | M3/M4 | 方向正确，但涉及 services / scripts / db/imports 多层迁移，需先有契约测试和 import-linter dry-run。 |
| A02 禁止跨服务 import | M3/M4 | 纳入架构治理，但先 advisory，再 fail-fast，避免一次性打断工具链。 |
| A03 market_reports router 分层 | M3 | 已在 P3-2，继续以 service/repository 小刀拆分。 |
| A04 Agent runtime 拆分 | M2/M3 | 已在 P3-1，优先拆 query plan、guardrails、evidence/source、sessions/streaming。 |
| A06 前端依赖规则 | M3 | 可纳入 ESLint 约束，但不改变当前前端框架。 |
| Q02 大文件拆分 | M3/M4 | 已在 P3，继续拆 `agent_chat_runtime_impl.py`、`market_reports.py`、`pdf_parser_app_impl.py`，每刀带测试。 |
| Q03 重复代码治理 | M4 | parser/common、wiki accessor、Hermes profile 去重应在边界稳定后做。 |
| T06/T07/T08 测试分层、fixture 工厂、coverage | M3/M4 | 应纳入，但 coverage 阈值逐步提升，不直接阻塞当前 M1。 |
| P01/P02 DB 索引和分区 | M4 | 需要真实慢查询和表规模数据支撑，先度量再改 DDL。 |
| P06/P07 向量和缓存优化 | M4 | 需要检索 P95/P99 和命中率指标，不能凭感觉优化。 |
| P11 CI cache / 镜像扫描 | M3/M4 | 可作为 DevOps 小窗口推进。 |
| P12 文档生命周期 | M4 | 可纳入 docs governance，但优先级低于安全和准确性。 |

暂不纳入当前 M1，需独立维护窗口：

| Kimi 项 | 暂缓原因 |
| --- | --- |
| Git 历史 BFG / filter-repo 清理 | 高风险操作，会影响所有协作者 clone 和分支；先做大文件清单、LFS policy、`git rm --cached` 方案评审。 |
| 全仓 ruff format / mypy fail-fast / TS strict 一次性开启 | 当前 dirty worktree 和历史债务较多，容易产生巨量非业务 diff；改为 touched-files 和模块白名单。 |
| P03 API 全异步化 | 涉及 FastAPI、DB session、LLM、Milvus、流式输出，必须在 Agent runtime 拆分和测试增强之后。 |
| P04/P08 parser 多进程和 Celery/RQ 队列化 | 会改变任务语义、状态模型和失败重试，需要单独架构设计和迁移计划。 |
| 大规模删除重复目录或 profile | 先明确运行时唯一 owner 和契约，再删除，避免破坏 Hermes / legacy entrypoint。 |

#### P5-8. 安全止血最小落地清单

目标：吸收 Kimi 路线图中的高优先级安全项，但用最小、可回归的方式推进，不混入业务重构。

优先动作：

1. 密钥和环境：轮换真实密钥；`SIQ_AUTH_SECRET_KEY`、`SIQ_SOURCE_TOKEN_SECRET` 必须使用 32 bytes 以上随机值；README 和脚本只推荐 `infra/env/local.env`。
2. 本地数据权限：为 `data/`、`artifacts/`、`runtime/`、`runtimes/`、`var/` 提供权限检查脚本，提示而不删除用户业务数据。当前 `check_local_security_hygiene.py --scope local-dirs` 已从顶层目录扩展到有限深度文件扫描（默认 max depth 4 / 最多 50 条 finding），会跳过 venv symlink；`--scope workflow` 同时校验运行态目录和 env 文件的 `.gitignore` 规则不会漂移；本机运行态目录与可写文件已收敛为非 world-readable / non world-executable。
3. CI PostgreSQL 安全：release gate workflow 禁用 `POSTGRES_HOST_AUTH_METHOD=trust`，端口只绑定 localhost 或 self-hosted 内网。当前 release gate 已改为 `POSTGRES_PASSWORD` / `SIQ_PGPASSWORD` / `PGPASSWORD`，并绑定 `127.0.0.1:15432:5432`。
4. 容器和基础设施：服务容器逐步切 non-root；当前 `services/market-report-finder` 与 `services/market-report-rules` 已新增 `USER siq`，`report-finder` / `market-report-finder` / `market-report-rules` compose 服务已显式 `user: "10001:10001"`，CI hadolint 已覆盖 6 个服务 Dockerfile，并由 `check_local_security_hygiene.py --scope workflow` 校验 Dockerfile/compose/CI 三处不漂移。`observe_security_artifacts.sh` 的 Docker Trivy fallback 已改为宿主 UID/GID 写入 artifact，避免生成 root-owned `artifacts/security` 报告。Milvus / MinIO 默认凭证移入环境变量仍保留为后续小窗口。
5. 生产启动：`SIQ_DEPLOYMENT_PROFILE=production` 下禁止 `--reload`、`FLASK_DEBUG=1` 和宽松 CORS。当前 `start_all.sh`、`apps/api/start.sh` 已在 production profile 下默认关闭 uvicorn reload、默认绑定 `127.0.0.1`，并拒绝显式 `SIQ_UVICORN_RELOAD=1` / `FLASK_DEBUG=1`；API 运行时通过 `services/runtime_security.py` 要求 production 明确设置 `SIQ_CORS_ALLOW_ORIGINS` 且禁止 `*`。
6. 日志轮转：supervisor/systemd 日志设置 maxbytes/backups，避免长跑环境磁盘打满。当前 `infra/supervisor/supervisord.conf` 已为 supervisord 主日志和所有 stdout/stderr 服务日志配置 `*_maxbytes=20MB`、`*_backups=5`，并由 `check_local_security_hygiene.py --scope workflow` 校验新增 logfile 不遗漏轮转配置。

验收：

```bash
python3 -m pytest scripts/maintenance/tests/test_run_market_document_full_postgres_gate.py -q
python3 -m pytest scripts/maintenance/tests/test_check_local_security_hygiene.py scripts/maintenance/tests/test_container_security_config.py scripts/maintenance/tests/test_security_observe_artifacts.py -q
cd apps/api && .venv/bin/python -m pytest tests/test_runtime_security.py -q
python3 -m pytest scripts/maintenance/tests/test_production_startup_guards.py -q
python3 - <<'PY'
import configparser
cfg = configparser.ConfigParser(interpolation=None)
assert cfg.read("infra/supervisor/supervisord.conf")
PY
python3 scripts/maintenance/check_local_security_hygiene.py --scope workflow
python3 scripts/maintenance/check_local_security_hygiene.py --scope local-dirs
```

## 6. 里程碑

### M1-S：一周内，可信问答与门禁收口

范围：

- 金融事实无证据硬阻断。
- ChatContext / fallback event / audit trace 传递一致化。
- API CI 覆盖从白名单走向全量或机器可审计分层。
- Market ingestion eval 失败时硬失败。
- PostgreSQL gate 明确 HK/JP/KR/EU/US 覆盖边界、CN/A 股独立 legacy 边界。
- DDL authority、report artifact hygiene、tracked JSON 红线写入门禁。

验收：

```bash
cd apps/api && .venv/bin/python -m pytest \
  tests/test_agent_runtime_answer_audit.py \
  tests/test_agent_runtime_postgres_fallback.py \
  tests/test_agent_chat_runtime_loops.py -q

python3 scripts/maintenance/run_market_document_full_postgres_gate.py --mode contract
python3 scripts/maintenance/run_market_ingestion_eval.py --strict
python3 scripts/maintenance/check_local_security_hygiene.py --scope workflow
```

### M1.5：两周内，specialist artifact 审计一体化

范围：

- factcheck / tracking / legal workflow 统一 artifact contract。
- specialist chat 非流式与流式均返回 `audit_trace_id`，并能按权限读取完整脱敏 trace。
- legal facts、tracking PostgreSQL facts、factcheck claim verdict 进入统一审计 schema。
- Hermes shared `pg_query.py` 只读查询助手产品化，替换旧 PostgreSQL 指引。
- specialist artifact HTML / Markdown 输出必须通过引用和质量 validator。

验收：

- factcheck、tracking、legal 三类请求均能生成 artifact、保存消息、返回可点开的 artifact URL 和 `audit_trace_id`。
- 每个 artifact 至少包含 source path / chunk / evidence / validation result。
- legal opinion 无绝对法律承诺和模板残留；tracking 报告记录模块状态和 PostgreSQL 查询来源；factcheck 输出 claim-level verdict。

### M2：一个月内，多市场前端入库工作台和状态模型硬化

范围：

- generic 非 PDF 结构化解析接线，EU ESEF / XHTML / ZIP 可从市场工作台进入 package build。
- 所有 workflow/source/quality/PostgreSQL 状态按 `taskId`、`packagePath` 或 `document_full_path` key 化。
- US SEC 从 ticker 选择改为 package / accession / document_full 选择，避免同 ticker 多 filing 串档。
- 审计 trace 成为前端 message model 一等字段，不依赖回答正文解析。
- 长任务进度、stdout/stderr 摘要、timeout、retry scope、disabled reason 可见；当前 workflow job 已统一 `currentStep/failedStep/retryScope` 与 step 输出摘要，generic PDF / standard PDF action 与 run-all disabled reason 已在前端状态模型和面板中可见。

验收：

```bash
cd apps/web
npm run test:unit
npm run check:frontend
```

新增测试至少覆盖 generic non-PDF package build、task switching stale state、US same-ticker multi-filing、history-loaded audit trace、long-running job progress/error。

### M3：两个月内，平台工程质量和性能基线收口

范围：

- `agent_chat_runtime_impl.py`、`market_reports.py`、`pdf_parser_app_impl.py` 继续小刀拆分，facade-only 模块变成真实 owner。
- ruff touched-files 在 CI fail-fast；mypy 白名单已接入 CI/pre-commit，后续按低耦合模块扩展。
- `UsageEvent` SQL 聚合和复合索引、`ChatMessage(session_id, created_at)` 复合索引落地。
- Milvus/MinIO 默认凭证和端口 hardening 完成。
- PR-safe contract performance baseline 进入 CI artifact；真实样本文件/`document_full.json` load、PostgreSQL query latency 进入 nightly/self-hosted 报告；embedding throughput、Milvus retrieval latency 已作为 optional probes 进入 nightly 报告，release wrapper 支持 `SIQ_AGENT_MEMORY_VECTOR_PROBES_REQUIRED=1` / `SIQ_AGENT_MEMORY_VECTOR_PROBES_SKIP=1` 和 collection/cases/top-k/model env 透传；默认 retrieval cases 已固定为 `eval_datasets/agent_memory_retrieval_contract/cases.json`，默认 seed profiles 与该 case 文件对齐；`agent_memory_vector_preflight.{json,md}` 会区分 endpoint/pymilvus/Milvus connectivity/collection schema 问题；`SIQ_AGENT_MEMORY_VECTOR_SEED=1` 可在同一 release artifact 下生成脱敏 seed 报告，且要求显式 embedding endpoint、缺失即 fast-fail；非 dry-run seed 后还会生成 post-seed health 报告并强制 collection/schema 完整；self-hosted 配齐 endpoint/Milvus/seed 后可用 required flag 升级为硬门禁。

验收：

- 大文件行数持续下降，且每次拆分有对应测试。
- CI artifact 稳定产出 release gate、financial QA、market ingestion eval、security/quality observe 报告。
- 性能优化先有 baseline 再改 DDL / queue / async，不凭感觉改架构。

## 6.1 全市场问答链路契约（2026-07-12 落地）

全市场问答复用 A 股的上层方法论，但不强制底层产物同构：

```text
问题
  -> 市场/公司/ticker 解析（六市场实时 catalog）
  -> company.json + report manifest 锁定 report_id/filing_id/parse_run_id
  -> 市场事实适配器
       CN/HK/JP/KR/EU: three_statements.json 扁平 metrics
       US: financial_data.json statements/items/values/sources
  -> validation / financial_checks 质量门禁
  -> 核心事实与证据
       PDF 市场: task_id + pdf_page + table_index + md_line
       US SEC: source_url + source_anchor + xbrl_tag + html_snippet
  -> calculator / reconciliation
  -> Wiki 缺失时按完整 ResearchIdentity 查询 PostgreSQL Agent view
  -> citation guard + answer_audit_trace
```

| 市场 | Wiki 核心事实 | 权威证据坐标 | PostgreSQL fallback | 质量规则 |
| --- | --- | --- | --- | --- |
| CN | `metrics/reports/<report_id>/three_statements.json` | PDF 页、表格、Markdown 行 | `pdf2md` | CN financial rules / validation |
| HK | 同上；底层可由 `financial_data.json` 派生 | HKEX PDF 页表 | `pdf2md_hk.v_agent_financial_facts` | HK profile + financial checks |
| US | `reports/<report_id>/metrics/financial_data.json`，运行时展开 items/values | SEC 原文 URL、iXBRL anchor、XBRL tag | `sec_us.v_agent_financial_facts` | US GAAP / SEC financial checks |
| JP | `three_statements.json` | EDINET PDF 页表 | `edinet_jp.v_agent_financial_facts` | JP/EDINET profile + financial checks |
| KR | `three_statements.json` | DART PDF 页表 | `dart_kr.v_agent_financial_facts` | KR/DART profile + financial checks |
| EU | `three_statements.json` | ESEF/年报 PDF 页表 | `eu_ifrs.v_agent_financial_facts` | IFRS/EU profile + financial checks |

运行时必须遵守以下边界：

- catalog 全市场统计只聚合六个生产 catalog；根 catalog 中迁移期非 CN 条目不得重复计数。未分类 legacy 主体可继续被具体问题召回，但不冒充 A 股公司。
- 短 ticker 使用完整 token 边界匹配；英文公司名仅允许完整名称或至少两个有意义的连续名称 token，禁止任意子串模糊命中。
- validation 为 `fail` 时不得把 Wiki 候选数字注入回答；`warning` 可以回答，但必须把状态与检查摘要放入底稿。
- PDF 页表证据与 SEC URL + anchor 是等价的结构化证据路线；SEC 链路不能因没有 PDF task_id 被误判为无证据。
- PostgreSQL fallback 必须携带完整 `market/company_id/filing_id/parse_run_id`；缺任一字段都 fail closed，不得回退到 A 股 schema 猜测。
- parser 污染的展示 unit 可规范化，但必须保留 raw unit；金额、期间、币种和原始披露值不得由模型改写。

2026-07-12 真实 Wiki live smoke 覆盖 CN/HK/US/JP/KR/EU，每个市场均命中三张表、完整报告身份、validation 和结构化证据。离线 `trace-offline` 现为 12/12（含 ICBC 错值、跨 identity 同值和伪 trace 攻击）、`wiki-static` 7/7；这两项仍是发布门禁，live smoke 用于发现 catalog、runtime adapter 和当前生产 Wiki 漂移。

```bash
cd apps/api
uv run python ../../scripts/maintenance/run_live_market_qa_smoke.py \
  --output ../../artifacts/eval-runs/financial-qa/live-market-qa-smoke.json \
  --json
```

## 7. 建议优先执行清单（2026-07-12 刷新）

| 优先级 | 任务 | 理由 |
| --- | --- | --- |
| 1 | 金融事实无证据硬阻断 | 这是“绝对准确”的最低底线，不能让无引用数值进入历史消息。 |
| 2 | ChatContext / audit event 归一化 | 保证 PostgreSQL fallback reason、resolved company、period 和 trace metadata 不丢。 |
| 3 | API CI 全量或机器可审计分层 | 当前白名单会漏掉新路由和 specialist workflow 回归。 |
| 4 | Market ingestion eval 硬失败 | 入库评测不能只生成报告，必须能阻断失败发布。 |
| 5 | DDL authority 与 PostgreSQL gate 边界 | 明确 HK/JP/KR/EU/US 与 CN/A 股边界，避免 schema 漂移和误删风险。 |
| 6 | 前端 task/package scoped state | 防止多市场工作台切换任务、同 ticker 多 filing、PostgreSQL 入库按钮串档。 |
| 7 | specialist artifact contract | 将 factcheck/tracking/legal 纳入统一可审计产出链。 |
| 8 | Hermes 只读 PostgreSQL 查询助手 | 给智能体明确、安全、可审计的本地结构化查询入口。 |
| 9 | 安全止血补完 | Milvus/MinIO 凭证、端口、DB URL 日志脱敏是上线前必须处理的风险。 |
| 10 | Python touched-files fail-fast 与 pre-commit | 让本地提交和 CI 质量口径一致，继续避免全仓格式化 churn。 |
| 11 | 性能 quick wins | 先做 SQL 聚合、复合索引和慢查询基线，再做异步化/队列化/分区大改。 |
| 12 | 大文件 owner 化拆分 | 在准确性和门禁稳定后，继续降低核心文件变更风险。 |

## 8. 不建议做的事

| 不建议 | 原因 |
| --- | --- |
| 重写前端框架 | 当前 React/Vite 和页面结构已经能支撑工作台，重写会打断已验证链路。 |
| 为了统一而改 A 股 importer | A 股是旗舰样板和基准合同，应保持兼容。 |
| 让 Agent 直接读向量结果回答财务数字 | 数值问答必须优先走 Wiki metrics/evidence，必要时 PostgreSQL fallback，并用 calculator 校验；向量只补解释。 |
| 把所有市场强行统一成一个 schema | 市场身份、准则、币种、taxonomy 差异真实存在，应统一事实契约而非抹平底层差异。 |
| 把大型 backtest JSON 每次都混入功能 PR | 会降低审查质量，应作为 release artifact 或单独报告更新。 |
| 在当前 M1 做 Git 历史改写 | filter-repo / BFG 会影响所有协作者和分支，应单独维护窗口执行。 |
| 一次性开启全仓 strict typing / formatting | 历史债务较大，容易产生巨量非业务 diff；应改为 touched-files 和模块白名单。 |
| 在拆分完成前做 Agent runtime 全异步化 | 会同时改变流式输出、DB session、LLM、Milvus 和审计 trace 行为，风险高于当前收益。 |

## 9. 结论

SIQ 当前最宝贵的资产不是某一个页面或某一个模型调用，而是已经形成的“可信披露解析 + Wiki 证据资产 + PostgreSQL 结构化事实 + Agent 查询门禁 + 回测报告”闭环。下一阶段要做的是把这条闭环产品化、门禁化、契约化，并把它扩展到国内一二级市场的统一投研决策场景。

只要按本方案推进，SIQ 可以从“能展示高质量样板”进一步升级为：

- 二级市场：可跨 A/H/US/EU/JP/KR 做高可信财报问答、对比、核查和跟踪。
- 一级市场：可对尽调材料、会议纪要、财务模型和 IC 结论做证据化决策支持。
- 工程平台：可通过 CI/nightly/release gate 证明每次发布没有破坏事实链路。
- 审计系统：每个智能体输出都能回到数据源、查询计划、计算过程和引用证据。
