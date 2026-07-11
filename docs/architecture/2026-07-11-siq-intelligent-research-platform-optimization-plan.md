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

### 当前落盘状态（2026-07-11）

- `AgentFinancialFact` 契约已落到 `docs/architecture/agent-financial-query-contract.md` 和 `db/imports/financial_query_api.py`，返回保持 `rows` 兼容并新增 `agent_facts`，字段包含 `bbox`；`/query` REST 级响应已覆盖多市场 agent view 和旧 A 股 pdf2md fallback，同一契约返回，并对 legacy PostgreSQL 连接异常做泛化 503 脱敏；Hermes shared `financial_source_routing_contract.md` 已反向引用该机器契约，并通过测试锁住字段清单、`{schema}.v_agent_financial_facts` 和 `source_type=postgresql_agent_view`。
- `answer_audit_trace` 已在 Agent runtime 非流式/流式回答 guard 后写入 JSONL，并补齐 `trace_id`、`resolved_period`、fallback events、PostgreSQL fallback reason；事实字段保留原始引用行并补 `metric_name/source_page/quote` 标准别名，引用解析已覆盖千分位数值和含 `|` 的表格 quote。运行时不再向可见回答或历史消息追加“审计详情”摘要，避免审计元数据污染真实回答；主聊天和 analysis/factchecker/tracking/legal specialist chat 的非流式响应均返回 `ChatResponse.audit_trace_id`，流式 done payload 均返回 `audit_trace_id`，前端按结构化字段渲染折叠审计入口并懒加载完整 JSON。主聊天和 specialist chat 均已提供 `GET {apiPrefix}/chat/audit-traces/{trace_id}` 读取完整脱敏 trace。
- `scripts/maintenance/run_market_document_full_postgres_gate.py` 和 `scripts/ops/run_market_postgres_release_gate.sh` 已提供 contract/offline-postgres 分层门禁；PR CI 只跑 contract，contract 只要求真实样本 manifest 每市场至少 3 条清单结构和 v1 schema，不要求干净 checkout 存在未入库的本地 data 文件；manual/nightly workflow 跑 offline-postgres，并要求 self-hosted 数据环境，同时产出 `trace-offline` 与 `wiki-static` 金融 QA benchmark artifact。底层 backtest CLI 的默认 JSON/Markdown 也已改到 ignored `artifacts/eval-runs/local/`，只有显式 `--output/--markdown` 才刷新 tracked reports。
- 多市场 `{schema}.v_agent_financial_facts` 已固化为默认 latest-successful 语义：HK / JP / KR / EU / US 的最终 Agent fact view 均 join `{schema}.v_latest_parse_runs`，`v_latest_parse_runs` 只纳入 `pass/warning/completed/success` parse run；US raw XBRL facts union 也受同一 latest 约束，避免同一 filing 的历史重复入库污染 PostgreSQL fallback。
- Wiki/PostgreSQL parity 已输出机器可读分类码：`unit_display_diff`、`period_alias_diff`、`currency_label_diff`、`wiki_missing`、`postgres_missing`、`value_mismatch`，分类仅用于离线 gate 和报告，不进入实时回答路径。
- `apps/api/services/market_document_identity.py` 已承接非 A 市场 document_full identity / selector helper，import/status/Agent query scope 分别通过 `build_import_selector`、`build_status_selector`、`build_agent_query_scope` 锁住字段边界；status 查询和 document_full PostgreSQL import plan 已复用统一 identity 解析，避免 document_full 相对路径、绝对路径和 task alias 误命中，测试覆盖 `task_id`、绝对路径和 repo-relative path 三种输入都会落到同一 `document_full.json`。document_full PostgreSQL import API 返回会带上非敏感 `selector` / `identity` 摘要，便于前端、日志和 QA 直接审查本次入库实际绑定的 `market/document_full_path/task_id/path_keys`。生产 PostgreSQL fallback 会从上下文提取 `market/parse_run_id/filing_id` 并传给多市场 Agent view 查询；`financial_query_api.query_market_agent_view_result()` 会在 SQL where 层优先使用目标 `parse_run_id/filing_id`，且测试锁住 parsed scope 优先于 company hint，避免 standalone 查询误用旧 hint 污染目标 run；没有目标 scope 时才依赖 view 的 latest-successful 语义。backtest runner 的市场数据库 URL 也已复用 importer 的 hardened `market_ingestion_contract.database_url()`，避免导入和校验因泛用 `DATABASE_URL` 策略不同连到不同实例。
- 前端非 A 市场统一四阶段 `MarketIngestionPipelineState` 已落地；HK/JP/KR/EU generic PDF 和 US SEC 都按 `artifacts -> wiki -> semantic -> postgres` 派生按钮与卡片状态，action key 已统一为 `runAll/wiki/semantic/postgres`，generic PDF 层再映射到既有 `wiki-import-generic/semantic-generic/db-import` 执行函数。PostgreSQL 状态展示已统一到 `parse_runs/facts/tables/chunks/evidence/schema/parse_run_id`，ready 口径收紧为五项计数均大于 0；后端 `missing_counts` 也按同一五项口径返回，避免前端遗漏 `parse_runs` 或 `facts` 缺口。US SEC case-set status 已透传 `full_document_paths/parser_result_dir/parser_result_task_id`，避免最近任务误判缺 `document_full_path` 并错误禁用 PostgreSQL/一键入库；缺 `document_full_path` 时按钮前置禁用并显示可见原因，不再只依赖 hover title。按钮背后的 API payload 已用 fetch stub 和 Playwright 行为测试锁住：HK/JP/KR/EU generic PDF 继续发送 `{ market, task_id, ddl: true }`，US SEC 继续发送 `{ market: 'US', document_full_path, ddl: true, force: false }`。
- 金融 QA benchmark v1 已落到 `datasets/eval/financial_qa_benchmark/v1/` 和 `scripts/maintenance/run_financial_qa_benchmark.py`；PR CI 使用 `trace-offline` 预录 `answer_audit_trace` 做确定性门禁，不调用 LLM、不连接 PostgreSQL。当前 P0 覆盖 CN/HK/US/JP/KR/EU，含 9 条 trace case、9 个关键事实、1 个 calculator run 和 1 个证据缺失拒答；校验 case schema、resolved identity、key fact、精确 evidence、source policy、calculator trace 和 guardrail refusal；当 case 显式 `source_policy.allow_postgres_fallback=false` 时，trace 中出现 `postgres_facts` 会直接失败；单测已用当前 `agent_runtime_answer_audit.build_answer_audit_trace()` 生成 trace 反喂 benchmark，并新增 fake live runtime smoke 覆盖 `_collect_chat_reply_impl -> answer_audit_trace.jsonl -> trace-offline benchmark`，防止 evaluator 只适配静态黄金样本。`wiki-static` 当前覆盖 7 条 document_full fact case，用于本地检查 Wiki fact fixture 漂移。case `modes` 语义已在 dataset README 和 schema 单测中锁住：缺省代表全部已实现确定性模式，显式列表用于缩小运行模式，保留的 `postgres-fallback` 在 evaluator 落地前会被拒绝。
- P3 核心文件瘦身已开始采用“小刀切分”方式推进：`agent_runtime_context.py` 继续承接纯意图 helper，已下沉 goodwill 主表查询、statement-with-goodwill、direct-statement-with-goodwill 判断和 Hermes run input payload 选择；`agent_runtime_postgres_fallback.py` 已承接 financial_query_api loader、fallback 适用性 predicate、query parse、metric term predicate、financial query connection factory、multi-market agent view fallback、legacy metric row query、`pdf2md.document_tables` 页码补全、完整 `_postgres_fallback_result` 编排、`build_postgres_fallback_context` 薄编排和 fallback audit event/context helper；`agent_runtime_financial_sources.py` 已承接主要数据证据补充的来源调度、Wiki metrics 引用归一化和 Wiki/PostgreSQL fallback 编排；`agent_runtime_financial_guard.py` 已承接金融证据合约 fallback / invalid task_id / enforce 编排；`market_report_status_service.py` 已承接 market package load-plan summary、quality gate merge 规则、package quality response 组装、package list payload 过滤/排序/截断契约、US SEC package detail response 和 document_full status payload；`market_report_commands.py` 已承接 US SEC ingest `tickers/batch_tag` 过滤参数规范化与校验、US SEC upload 文件名清洗 / content-type 后缀推断 / build-compatible metadata payload 构造，以及 market package force audit 字段解析、脱敏、过期校验和 quality gate decision；`market_report_assist_service.py` 已承接市场报告 assist 的 JSON 抽取、候选压缩、prompt/user payload、Hermes mode 判断和规则/LLM merge 纯逻辑；`agent_chat_runtime_impl.py` 已删除 PostgreSQL fallback audit/context 的纯转发 wrapper，直接复用 `agent_runtime_postgres_fallback`；`agent_chat_runtime_impl.py` 和 `market_reports.py` 仍保留必要同名 wrapper 兼容既有调用点。
- P3 backtest runner 拆分已启动：`db/imports/backtests/document_fact_normalizer.py` 已承接 document_full 事实归一化、identity、证据判断、数值比较和稳定内容 hash；`db/imports/backtests/contract_cases.py` 已承接 fixture case 合约、断言检查和断言统计；`db/imports/backtests/agent_view_parity_helpers.py` 已承接 Agent view / Wiki-PostgreSQL parity 的纯比较、diff code 汇总和自动问题生成；`db/imports/backtests/agent_query_gate.py` 已承接 `v_agent_financial_facts` 固定问题查询和真实样本 parse_run 探针；`db/imports/backtests/wiki_postgres_parity_gate.py` 已承接 Wiki/document_full 与 PostgreSQL Agent view 离线对照，并保留显式断言 hard fail、自动生成对照 warning 降级语义；`db/imports/backtests/postgres_roundtrip_helpers.py` 已承接 PostgreSQL relation/table helper、作用域 selector、表族/表计数、content hash 和 required-evidence 检查；`db/imports/backtests/postgres_roundtrip_gate.py` 已承接 `check_db_case`、`check_db_case_sequence`、DB 导入、DDL 首次执行和幂等编排；`db/imports/backtests/production_sample_gate.py` 已承接真实样本 manifest v1 校验、路径解析、真实样本 case 生成和真实样本 parse_run 共存检查；`db/imports/backtests/report_writer.py` 已承接 JSON/Markdown 报告输出，runner 保留同名 wrapper，release gate 行为不变。
- P5 首版运行态可观测性已落地：API middleware 会记录 `siq_api_request_total` 与请求耗时 sum/count/max，`/health` 返回 uptime、请求数、错误数和 answer trace 数，`/metrics` 暴露 Prometheus text 格式；`answer_audit_trace` 写入时会同步累计 `siq_agent_fact_source_total`、`siq_postgres_fallback_reason_total`、`siq_answer_guardrail_block_total`、calculator run 和 citation 总数；document_full PostgreSQL import/status 入口已记录 `siq_ingestion_duration_seconds_*`、`siq_ingestion_fact_count` 和 `siq_frontend_pipeline_job_failure_total`，eval report 读取时会把离线 Wiki/PostgreSQL parity warnings 计入 `siq_wiki_postgres_parity_warning_total`；后台 `FileBackedJobService` 会在异步任务 succeeded/failed 终态记录 `siq_background_job_final_state_total` 和 `siq_background_job_duration_seconds_*`。该层目前为进程内轻量指标，不引入外部 Prometheus SDK。

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

当前进展：

- `agent_runtime_answer_audit.py` 已承接答案级 trace 生成和落盘。
- `agent_runtime_context.py` 已承接财务上下文/意图纯 helper；goodwill 主表查询和直答判断已从 `agent_chat_runtime_impl.py` 下沉，runtime 仅保留薄 wrapper。
- `agent_runtime_postgres_fallback.py` 已承接 financial_query_api loader、PostgreSQL fallback 适用性判断、query parse、metric term predicate、financial query connection factory、multi-market agent view fallback、legacy metric row query、`pdf2md.document_tables` 页码补全、完整 fallback result 编排、PostgreSQL fallback context builder 和 fallback audit event/context helper；runtime 仍保留薄 wrapper 与 legacy 调用点兼容。
- `agent_runtime_financial_sources.py` 已承接主要数据证据补充编排：Wiki metrics/note 引用缺口判断、Wiki metrics 文件引用归一化、human efficiency / human capital / statement / note / fulltext / PostgreSQL fallback 的补充来源优先级；runtime 仍保留薄 wrapper 与 guardrail 调用点兼容。

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

- `market_report_status_service.py` 已承接 package quality payload、package quality response 组装、load-plan summary 和 load-plan decision -> quality gates 合并逻辑；`market_reports.py` 仅保留 package 定位、读取 `load_plan.json` 与调用 service 的薄 wrapper。

建议 owner：

| 新 owner | 迁移内容 |
| --- | --- |
| `market_report_job_service.py` | job 创建、轮询、状态归档。 |
| `market_report_package_service.py` | package build / wiki import / semantic import。 |
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
- 仓库根目录暂未提供统一 `ruff.toml`、`mypy.ini` 或 `.pre-commit-config.yaml`；仅部分 Python 子项目有 `pyproject.toml`。

落地策略：

1. 第一阶段新增根级 `ruff.toml` 和轻量 `.pre-commit-config.yaml`，只检查 touched Python files、尾随空格、文件末尾换行、YAML/JSON 基础格式。当前已落地根级配置。
2. CI 先以 advisory 或 changed-files 模式运行 ruff，不做全仓 fail-fast。当前由 `scripts/maintenance/check_python_quality_touched.py` 发现 changed/untracked Python files；若本机或 CI 未安装 ruff，报告 advisory 并返回 0，后续再切 `--require-ruff`；JSON 输出会保留 ruff stdout/stderr，并在 CI 中随 `siq-quality-observe` artifact 上传，便于排查。
3. mypy 只对新拆出的低耦合模块逐步开启，如 backtest helpers、market identity、financial query contract；不直接套到 `agent_chat_runtime_impl.py`。
4. 禁止一次性黑盒格式化全仓，避免掩盖业务变更。

验收：

```bash
python3 -m pytest scripts/maintenance/tests/test_check_python_quality_touched.py -q
python3 scripts/maintenance/check_python_quality_touched.py --json
pre-commit run --files <touched-python-files>
ruff check <touched-python-files>
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

### M1：两周内，准确性门禁产品化

范围：

- Agent 查询契约文档。
- release gate 脚本。
- Wiki/PostgreSQL parity 分类，仅作为离线回测和发布门禁。
- 前端状态文案和 ready 定义对齐。

验收：

```bash
python3 db/imports/backtests/market_document_full_postgres_backtest.py \
  --db --import-before-db-check --idempotency --production-sample-db --production-agent-query

cd apps/web && npm run test:unit && npm run check:frontend
cd apps/api && .venv/bin/python -m pytest tests/test_agent_runtime_*.py -q
```

### M2：一个月内，Agent 审计闭环

范围：

- `answer_audit_trace`。
- AgentFinancialFact contract。
- 金融 QA benchmark v1。
- Agent runtime query/source/audit helper 下沉。

验收：

- P0 benchmark 关键事实准确率 100%。
- 每条财务答案都有 citations 和 trace。
- 缺证据答案能被 guardrail 阻断。

### M3：两个月内，平台工程质量收口

范围：

- market_reports router service 化。
- backtest runner 拆分。
- manual/nightly release gate CI。
- 一级市场 Deal OS 审计 trace 对齐。

验收：

- `agent_chat_runtime_impl.py`、`market_reports.py`、backtest runner 核心文件体积明显下降。
- CI artifact 稳定产出 release gate 报告。
- 一二级市场共用审计语言。

## 7. 建议优先执行清单

| 优先级 | 任务 | 理由 |
| --- | --- | --- |
| 1 | 新增 release gate 脚本和手动 CI | 立即把已证明有效的强回测变成发布保障；真实回答仍保持 Wiki-first / PostgreSQL fallback。 |
| 2 | 编写 Agent 金融查询契约 | 把“绝对准确”的要求从 prompt 变成数据合同。 |
| 3 | 建 `answer_audit_trace` | 让每个智能体输出都可复盘。 |
| 4 | 统一前端 pipeline state | 不改框架，但降低五市场状态和按钮分叉。 |
| 5 | identity / selector 单源化 | 解决入库、状态、Agent 查询之间的隐性错配风险。 |
| 6 | 拆分 Agent runtime 和 market_reports owner | 降低后续持续迭代的事故概率。 |
| 7 | 建金融 QA benchmark | 将准确性目标量化，形成长期可回归资产。 |
| 8 | 安全止血最小清单 | 吸收 Kimi S01/S02/S03，先处理密钥、数据权限和 release gate DB 暴露风险。 |
| 9 | Python touched-files 质量门禁 | 吸收 Kimi Q04/Q05，但只对新增/修改文件渐进执行，避免全仓 churn。 |
| 10 | 架构边界 advisory | 吸收 Kimi A01/A02/A07，先用 import-linter/deptry 观测非法 import，再分批 fail-fast。 |

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
