# SIQ 深度优化开发计划书

日期：2026-07-07

状态：可执行开发计划

适用范围：`/home/maoyd/siq-research-engine`

上游依据：

- `docs/architecture/2026-07-06-siq-full-audit-optimization-execution-plan.md`
- `docs/architecture/2026-07-06-siq-risk-calibrated-optimization-plan.md`
- 2026-07-07 路径/服务、后端安全、前端体验、AI/财务质量四线只读审计结论

## 1. 目标

本计划用于把审计建议转化为可由 Codex 或智能体集群分批执行的开发任务。核心目标不是重写系统，而是在不降低项目质量、不降低财务事实精度、不大改前端视觉和主交互的前提下，完成以下优化：

1. 收敛路径、服务、数据源口径，减少运行方式差异导致的隐性错误。
2. 收紧内部服务鉴权、API 代理、上传和命令入口边界。
3. 保证解析产物、PostgreSQL 入库、Wiki 派生资产、向量检索之间的事实晋升链路可信。
4. 修正 `force=true`、load plan、evidence resolvability 等可污染可信链路的风险。
5. 大文件按业务边界拆解，降低长期维护成本，并以 characterization tests 防止行为回归。
6. 前端只做文案、可访问性、错误模型和状态可靠性收口，不改变主视觉风格。

一句话原则：

> 允许探索，允许草稿，允许 review；但不允许未验证内容进入 canonical facts、正式检索链路或生产发布物。

## 2. 硬约束

1. 不通过删除测试、降低断言、把 `fail` 改成 `warning`、跳过 quality gate 来制造绿灯。
2. 不自动迁移、删除、重命名 `data/`、`var/`、`artifacts/`、`runtimes/` 中真实运行态数据。
3. 不提交 `.env`、真实 token、数据库文件、runtime 数据、auth token、LLM key。
4. 不大改前端主视觉、导航结构和用户核心流程。
5. 大文件拆解第一阶段必须保持行为等价；优先移动纯函数、DTO、适配层，再改内部结构。
6. 所有跨服务契约变更必须同步测试、示例 env、文档和前端状态文案。
7. 每批任务必须可以单独验收、单独回滚；不得把路径治理、安全修复和大规模拆文件混在一个不可审查提交里。

## 3. 当前架构地图

### 3.1 服务边界

| 模块 | 路径 | 当前职责 | 主要风险 |
| --- | --- | --- | --- |
| Web | `apps/web` | React/Vite 前端、多市场解析、工作台、研究链路 | Wiki/解析产物文案漂移，部分交互语义不够原生 |
| API | `apps/api` | FastAPI 聚合后端、鉴权、workspace、market reports、agent runtime | 大文件、代理边界、命令入口、force gate |
| PDF Parser | `apps/pdf-parser` | PDF 上传、MinerU/解析编排、财务提取、结果产物 | 大文件、内部 token、任务归属、市场分流 |
| Document Parser | `apps/document-parser` | 通用文档解析、任务结果、导入 | 内部 token、身份头信任、URL 下载边界 |
| Finder | `services/market-report-finder` | 市场报告发现与下载 | 服务级鉴权、下载权限、容量限制 |
| Rules | `services/market-report-rules` | 多市场 evidence package 与 load plan | gate 决策未硬消费、路径硬编码 |
| Contracts | `packages/market-contracts` | evidence package 契约、质量门禁 | resolvability 能力未被全链路消费 |
| Hermes | `agents/hermes` | AI 研究包、报告生成、事实核查 | evidence refs、confidence、factcheck 闭环 |
| Infra | `infra` | Docker、env example、supervisor、CI | `var/runtime`、`data/artifacts` 路径漂移 |

### 3.2 状态路径目标契约

建议冻结为：

| 环境变量 | 目标用途 | 不应放入 |
| --- | --- | --- |
| `SIQ_DATA_ROOT` | 长期业务数据、解析输入、downloads、Wiki 派生资产、数据库持久化 | 临时 pid、短期 cache |
| `SIQ_RUNTIME_ROOT` | logs、run、cache、socket、pid、临时运行态 | 长期业务数据 |
| `SIQ_ARTIFACTS_ROOT` | 可再生成产物、评测、安全扫描、导出结果 | canonical 源数据 |
| `SIQ_REPORT_DOWNLOADS_ROOT` | 报告下载输入，建议 `$SIQ_DATA_ROOT/market-report-finder/downloads` | 纯临时目录 |

解析产物到可信链路的推荐口径：

```text
PDF/HTML/XBRL 原始输入
  -> parser results / structured artifacts
  -> quality gate / evidence resolvability
  -> PostgreSQL canonical/review/quarantine
  -> Wiki 作为解析产物派生的知识资产
  -> RAG/vector 只消费 quality_passed 或被审计允许的内容
```

## 4. 执行协议

每个开发批次必须执行：

1. `git status --short --branch`
2. 阅读本批次涉及文件和已有测试。
3. 如涉及大文件拆解，先补 characterization tests 或确认已有覆盖。
4. 小步提交：每个提交只覆盖一个安全边界、一个路径契约或一个拆解切片。
5. 运行目标测试；跨模块契约变更运行组合测试。
6. 最后运行 `git diff --check`。

禁止：

- 未读测试直接重构。
- 多个 worker 同时写同一大文件。
- 把 facade 删除后再补兼容。
- 用全局 search/replace 改路径但不跑路径测试。
- 将前端文案改成隐藏失败状态。

## 5. 分阶段开发计划

## P0：冻结可信边界和路径契约

P0 目标：先消除最容易污染可信链路和运行环境的风险。P0 不做大规模拆文件，只做边界收紧和必要兼容。

### P0-01 路径契约冻结与 doctor 输出

目标：让本地启动、Docker、脚本、API、parser 对同一类数据使用同一套根目录。

涉及文件：

- `start_all.sh`
- `infra/env/local.example`
- `infra/env/docker.example`
- `infra/env/production.example`
- `infra/docker/docker-compose.yml`
- `apps/api/services/path_config.py`
- `apps/pdf-parser/path_config.py`
- `apps/document-parser/path_config.py`
- `services/market-report-finder/src/market_report_finder_service/core/config.py`
- `docs/operations/local-development.md`

详细任务：

1. 确定 `var` 与 `runtime` 的唯一命名。推荐保留环境变量 `SIQ_RUNTIME_ROOT`，本地默认路径可指向 `var/`，但文档必须明确：变量名是 runtime，目录名可以是 var。
2. 统一 `SIQ_REPORT_DOWNLOADS_ROOT` 默认值为 `$SIQ_DATA_ROOT/market-report-finder/downloads`。
3. 将 env example、Compose、`start_all.sh`、三个 `path_config.py` 的默认值对齐。
4. 新增只读 doctor/print-paths 能力，输出以下路径：
   - project root
   - data root
   - runtime root
   - artifacts root
   - report downloads root
   - pdf parser results root
   - document parser results root
   - wiki/company root
5. doctor 输出不得打印 secret、DB URL password、token。
6. CI 增加轻量路径一致性测试，至少验证 env example 与代码默认值不互相矛盾。

验收命令：

```bash
cd /home/maoyd/siq-research-engine
python3 -m pytest apps/api/tests/test_path_config.py -q
docker compose -f infra/docker/docker-compose.yml --env-file infra/env/local.example config >/tmp/siq-compose-config.yml
git diff --check
```

通过标准：

- 不迁移真实 `data/`。
- doctor 可解释当前所有生效路径。
- `local.example`、`docker.example`、`production.example` 与 Compose 不再表达相反默认值。

### P0-02 Parser 内部服务鉴权

目标：避免直连 `pdf-parser` 或 `document-parser` 时伪造用户角色、绕过 API 鉴权和租户隔离。

涉及文件：

- `apps/pdf-parser/pdf_parser_request_utils.py`
- `apps/pdf-parser/pdf_parser_app_impl.py`
- `apps/pdf-parser/tests/`
- `apps/document-parser/app.py`
- `apps/document-parser/tests/`
- `infra/docker/docker-compose.yml`
- `infra/env/*.example`

详细任务：

1. 生产和 Docker profile 下强制要求内部服务 token。
2. token 未校验通过时，不信任 `X-SIQ-User-Id`、`X-SIQ-User-Role`、`X-SIQ-Workspace-Id`。
3. 无 token 的本地开发模式必须显式标记为 local/dev，并有启动日志提示。
4. 补负向测试：
   - 无 token 访问写接口返回 401/403。
   - 伪造 admin header 不得获得 admin 权限。
   - API 代理携带合法 token 时保留原行为。
5. Docker 默认不应以空 token 暴露可写接口。

验收命令：

```bash
cd apps/pdf-parser
python3 -m pytest tests -q

cd ../document-parser
python3 -m pytest tests -q
```

### P0-03 Finder/Rules 服务鉴权与 API proxy allowlist

目标：避免 `/api/v1/{upstream_path}` 或内部服务无鉴权暴露下载、处理、load-plan 等高风险能力。

涉及文件：

- `apps/api/routers/market_reports.py`
- `apps/api/services/market_report_proxy.py`
- `services/market-report-finder/src/market_report_finder_service/app.py`
- `services/market-report-finder/src/market_report_finder_service/api/routes/reports.py`
- `services/market-report-rules/src/market_report_rules_service/app.py`
- 相关测试

详细任务：

1. Finder 和 Rules 增加内部服务 token 验证。
2. API proxy 改为明确 allowlist，按 endpoint 映射转发，不允许任意上游路径透传。
3. 下载、批量下载、direct-download、load-plan 至少要求 `report.create` 或更高权限。
4. 保留已有 SSRF、私网 IP、redirect、content-length/content-type 防护，不得弱化。
5. 加容量限制和频率限制的接口占位，先实现配置项和测试，后续接完整 rate limiter。

验收命令：

```bash
cd apps/api
uv run python -m pytest tests/test_market_reports_proxy.py tests/test_market_report_proxy_service.py -q

cd ../../services/market-report-finder
uv run python -m pytest tests -q

cd ../market-report-rules
uv run --extra dev pytest tests -q
```

### P0-04 `force=true` 与 hard gate 决策修正

目标：禁止 `force=true` 绕过 hard gate，允许被审计的 soft gate 例外。

涉及文件：

- `apps/api/routers/market_reports.py`
- `packages/market-contracts/src/siq_market_contracts/evidence_package.py`
- `packages/market-contracts/tests/`
- `apps/api/tests/test_market_reports_proxy.py`

详细任务：

1. API 层必须读取 contracts 输出的 `force_allowed`、`import_blocked`、`vector_ingest_blocked`。
2. hard gate 命中时：
   - 不允许正式 import。
   - 不允许 vector ingest。
   - 可写入 quarantine/review artifact。
3. `force=true` 必须携带：
   - reason
   - operator/user id
   - ticket 或 change id
   - expires_at 或 one-shot marker
4. 所有 force 行为写审计日志，不打印 secret。
5. 前端状态文案区分“强制保存草稿/复核材料”和“正式入库”。

验收命令：

```bash
cd packages/market-contracts
uv run python -m pytest tests -q

cd ../../apps/api
uv run python -m pytest tests/test_market_reports_proxy.py -q
```

## P1：可信数据晋升链路

P1 目标：解析可以继续产出，候选内容可以展示，但只有证据可解析、质量通过的内容才能进入 canonical 和检索链路。

### P1-01 Rules load plan 增加 promotion decisions

目标：避免 validation fail 后仍生成可被 importer 误消费的正式 rows。

涉及文件：

- `services/market-report-rules/src/market_report_rules_service/models.py`
- `services/market-report-rules/src/market_report_rules_service/validation.py`
- `services/market-report-rules/src/market_report_rules_service/load_plan.py`
- `services/market-report-rules/tests/`

详细任务：

1. `DbLoadPlan` 增加：
   - `can_import`
   - `can_vector_ingest`
   - `promotion_decisions`
   - `blocked_reasons`
   - `quarantine_rows`
2. validation fail 时，正式 `fact_rows` 和 `evidence_rows` 不得被 importer 当成 canonical rows。
3. warning 可生成 review rows，但必须携带 warning reasons。
4. importer 或 API 调用方必须拒绝 `can_import=false` 的正式导入。

验收命令：

```bash
cd services/market-report-rules
uv run --extra dev pytest tests -q
```

### P1-02 Evidence resolvability 全链路硬消费

目标：contracts 已有 evidence resolvability 能力，rules/load-plan/API 必须消费它。

涉及文件：

- `packages/market-contracts/src/siq_market_contracts/evidence_package.py`
- `services/market-report-rules/src/market_report_rules_service/validation.py`
- `services/market-report-rules/src/market_report_rules_service/load_plan.py`
- `services/market-report-rules/sql/001_market_rules_staging.sql`
- 相关测试

详细任务：

1. 对 evidence path、source map、source url、artifact hash 做可解析检查。
2. dangling path、缺 source_map、伪 source_url、hash mismatch 必须 hard block canonical/import/vector。
3. SQL staging 中保存 resolvability summary，便于审计。
4. 前端质量标签避免只显示“证据 100%”，应能区分“证据字段存在”和“证据可回链”。

验收命令：

```bash
cd packages/market-contracts
uv run python -m pytest tests -q

cd ../../services/market-report-rules
uv run --extra dev pytest tests -q
```

### P1-03 Research pack 事实状态与 schema 校验

目标：避免低可信、无证据或仅外部背景内容被合并成正式判断。

涉及文件：

- `agents/hermes/profiles/siq_analysis/templates/research_pack.schema.json`
- `agents/hermes/profiles/siq_analysis/scripts/validate_research_packs.py`
- `agents/hermes/profiles/siq_analysis/scripts/merge_research_packs.py`
- `agents/hermes/profiles/siq_analysis/rules/report_workflow.md`
- 相关样本和测试

详细任务：

1. 增加事实状态：
   - `verified_fact`
   - `modeled_estimate`
   - `external_context`
   - `assumption`
   - `gap`
2. `key_findings` 必须要求 evidence refs，或明确标注为 assumption/gap/external_context。
3. `validate_research_packs.py` 真正加载 JSON Schema。
4. 增加 evidence resolver，检查 `evidence_id/source_file/pdf_page/table_index/md_line` 是否能回到原始产物。
5. `merge_research_packs.py` 合并时保留 confidence、review_required、fact_status。
6. final validation 输出：
   - `contract_pass`
   - `publish_ready`
   - `pass_with_review`
7. 外部来源 `unknown` 只能进入背景，不得支撑核心结论。

验收命令：

```bash
cd /home/maoyd/siq-research-engine
python3 -m pytest agents/hermes/profiles/siq_analysis/tests -q
python3 agents/hermes/profiles/siq_analysis/scripts/validate_research_packs.py --help
```

如果当前没有完整 tests 目录，应先建立最小 schema/resolver 单元测试，不直接改生产脚本。

### P1-04 财务 sanity gate 与同业异常降级

目标：防止异常单位、币种、期间、scale、极端 YoY 污染同业分位和财务判断。

涉及文件：

- `apps/pdf-parser/financial_extractor.py`
- `apps/pdf-parser/financial_extractor*`
- `agents/hermes/profiles/siq_analysis/scripts/generate_research_packs.py`
- `agents/hermes/profiles/siq_analysis/scripts/merge_research_packs.py`
- 相关样本测试

详细任务：

1. 对 peer metrics 增加轻量硬校验：
   - 单位
   - 币种
   - 会计准则
   - 期间
   - 合并口径
   - 审计状态
   - scale
2. 极端 YoY、ROE、利润率、扣非利润量级异常时，排除该 peer 的聚合值。
3. 不阻断整份报告，改为 `pass_with_review` 或 peer-level quarantine。
4. 报告正文不得引用被 quarantine 的 peer 指标作为确定性结论。

验收：

- 构造异常 peer metrics 样本，验证该 peer 被排除出聚合。
- 正常 peer metrics 不被误杀。
- final validation 能解释降级原因。

### P1-05 Factcheck 与 tracking 合规闭环

目标：factcheck 进入发布门，tracking 去交易化。

涉及文件：

- `agents/hermes/profiles/siq_analysis/rules/report_workflow.md`
- `agents/hermes/profiles/siq_factchecker/scripts/factcheck_cli.py`
- `data/wiki/tracking/scripts/module4_alert_trigger.py`
- 相关测试

详细任务：

1. factcheck verdict 写回 report quality：
   - `block` 阻断 publish_ready。
   - `request_changes` 标记 `pass_with_review`。
   - `pass` 才允许 publish_ready。
2. tracking 文案移除交易动作表达，例如买入、卖出、减仓、止损。
3. 替换为：
   - 复核假设
   - 调整风险暴露评估
   - 提交人工审阅
   - 跟踪下一期披露
4. 增加合规词负向测试。

## P2：前端体验与用户心智收口

P2 目标：让用户看到的系统状态与真实数据链路一致，不制造“Wiki 是主数据源”或“质量 fail 但看起来完成”的误解。

### P2-01 去 Wiki 主心智文案

涉及文件：

- `apps/web/src/components/pdf/PdfWorkflowPanel.tsx`
- `apps/web/src/pages/Help.tsx`
- `apps/web/src/pages/MarketParsingPage.tsx`
- `apps/web/src/pages/UsParsing.tsx`
- 相关 e2e

详细任务：

1. 用户主流程统一使用：
   - 解析产物
   - 入库
   - 研究资产
   - 派生知识资产
2. “Wiki” 只作为技术/历史兼容说明，放帮助页底部或 tooltip。
3. 多市场 PDF 页面不得再出现“Wiki 证据包”主模块。
4. PostgreSQL 入库说明明确：数据来自解析产物，Wiki 由解析产物派生。

验收：

```bash
cd apps/web
npm run test:unit
npm run check:frontend
```

### P2-02 PDF 最近任务列表语义修复

涉及文件：

- `apps/web/src/components/pdf/PdfTaskList.tsx`
- `apps/web/src/pages/pdf/usePdfTasks.ts`
- 相关 e2e

详细任务：

1. 保留“最近任务（点击查看结果）”标题提示。
2. 任务文件名/描述区域改为可聚焦按钮或链接，点击效果与“查看结果”一致。
3. 行容器不得使用嵌套真实按钮的 `role=button`。
4. 删除、重跑、补拉使用轻量确认态或 dialog，不用裸 `confirm` 作为最终方案。
5. 补测试：
   - 鼠标点击文件名打开结果。
   - Enter/Space 打开结果。
   - 删除取消不删除。
   - 结果拉取失败可重试。

### P2-03 错误模型和 API error adapter

涉及文件：

- `apps/web/src/shared/api/client.ts`
- `apps/web/src/pages/pdf/usePdfTasks.ts`
- `apps/web/src/pages/documents/useDocumentTasks.ts`
- PDF/Document 页面组件

详细任务：

1. 统一错误来源：
   - upload
   - polling
   - result
   - import
   - auth
   - network
2. HTML 误命中、stdout/stderr、HTTP preview 转成用户可行动文案。
3. 页面展示下一步操作：
   - 刷新任务
   - 重新拉取结果
   - 检查解析服务
   - 重新登录
4. 不在普通用户页面直接展示长 stack trace 或敏感路径。

### P2-04 解析入口命名收敛

涉及文件：

- `apps/web/src/app/routes.tsx`
- `apps/web/src/features/pdf-parsing/PdfParsingWorkbench.tsx`
- `apps/web/src/pages/MarketParsingPage.tsx`
- `apps/web/src/pages/UsParsing.tsx`

详细任务：

1. 明确 `MarketParsingPage` 是多市场通用工作台。
2. `PdfParsingWorkbench` 若只是 re-export，应改名或删除伪抽象。
3. 美股 SEC 独立页保留，但兼容 PDF 入口只保留一个清晰位置。
4. 路由和组件名不必一次性大迁移，先加注释和测试保护，再逐步改名。

## P3：大文件拆解专项

P3 目标：降低长期维护成本。所有拆解必须先保行为，再改结构；每个大文件拆解独立执行，不与安全和路径修复混合。

### P3 总体拆解规则

1. 第一提交只补 characterization tests 或确认已有测试覆盖。
2. 第二提交只移动纯函数、常量、类型、schema，不改行为。
3. 第三提交抽服务对象或 pipeline step。
4. 第四提交删除死代码和重复兼容层。
5. 每轮拆解后运行目标测试。
6. 原入口文件保留 facade 至少一个迭代周期，避免外部 import 崩溃。
7. 单文件目标：
   - 第一阶段降到 2500 行以内。
   - 第二阶段降到 1500 行以内。
   - 长期核心 facade 控制在 800-1200 行以内。

### P3-01 拆解 `apps/api/services/agent_chat_runtime_impl.py`

当前规模：约 6292 行。

当前职责：

- chat run 编排
- preflight context
- active run / duplicate run 控制
- attachment/context 注入
- memory/local context
- tool 调用
- streaming event
- citations/source trace
- usage/cost
- final response persistence
- fallback 和异常处理

目标结构：

| 目标模块 | 职责 |
| --- | --- |
| `agent_runtime_preflight.py` | preflight context、message/context contract |
| `agent_runtime_sessions.py` | run lifecycle、active run、duplicate run |
| `agent_runtime_attachments.py` | attachment 解析和上下文注入 |
| `agent_runtime_tools.py` | tool plan、tool call、tool result normalization |
| `agent_runtime_streaming.py` | streaming event、SSE payload、heartbeat |
| `agent_runtime_citations.py` | citation/source trace、evidence link |
| `agent_runtime_memory.py` | local memory、conversation memory |
| `agent_runtime_financial_guard.py` | 财务事实引用 guard |
| `agent_chat_runtime_impl.py` | facade 和主编排，不再放细节实现 |

详细任务：

1. 建立 characterization tests：
   - preflight message 可选/必填契约。
   - active run 防重复。
   - attachment 注入。
   - streaming 首包/尾包/错误包。
   - tool output normalization。
   - citation link 生成。
2. 抽出纯函数：
   - payload normalization
   - event builder
   - error envelope builder
   - run id/session id helper
3. 抽 run lifecycle：
   - create run
   - mark running
   - mark completed
   - mark failed
   - duplicate guard
4. 抽 preflight：
   - message/history/local memory/attachments/source trace 输入明确化。
   - preflight 返回 DTO，避免传散乱 dict。
5. 抽 streaming：
   - 主文件只调用 `stream_chat_run(...)`。
   - 所有 SSE event 统一格式。
6. 抽 tool orchestration：
   - tool call 权限检查。
   - tool result 截断与安全过滤。
7. 最后清理 facade：
   - 主文件只保留 public API、编排流和兼容 import。

验收命令：

```bash
cd apps/api
uv run python -m pytest \
  tests/test_agent_runtime_chat_preflight.py \
  tests/test_agent_runtime_active_runs.py \
  tests/test_agent_chat_runtime_attachments.py \
  tests/test_agent_runtime_streaming.py \
  tests/test_agent_runtime_tool_output.py \
  tests/test_agent_runtime_citations.py \
  -q
```

通过标准：

- 不改变 API response schema。
- 不改变现有 chat 行为。
- `agent_chat_runtime_impl.py` 第一阶段低于 2500 行，第二阶段低于 1500 行。
- 新模块均有对应测试或被现有测试覆盖。

### P3-02 拆解 `apps/pdf-parser/pdf_parser_app_impl.py`

当前规模：约 4431 行。

当前职责：

- Flask route
- 上传参数解析
- 任务创建/查询/删除/重试
- 文件存储
- parser pipeline
- market profile dispatch
- result artifact 读取
- PostgreSQL/Wiki/入库相关状态文案
- 错误处理

目标结构：

| 目标模块 | 职责 |
| --- | --- |
| `pdf_parser_routes.py` | Flask route 注册与 request/response 适配 |
| `pdf_parser_jobs.py` | task lifecycle、状态机、重试、删除 |
| `pdf_parser_storage.py` | 文件保存、路径安全、hash、大小限制 |
| `pdf_parser_pipeline.py` | parser pipeline 编排 |
| `pdf_parser_market_dispatch.py` | CN/HK/US/EU/KR/JP 市场分流 |
| `pdf_parser_results.py` | result artifact discovery 和读取 |
| `pdf_parser_errors.py` | 统一错误 envelope |
| `pdf_parser_app_impl.py` | create app/facade/兼容入口 |

详细任务：

1. 先补 route-level characterization tests：
   - upload success/fail
   - task list
   - result read
   - reparse/refetch/delete
   - owner scope
   - token required
2. 抽 storage：
   - 安全文件名。
   - 文件大小限制。
   - stream write。
   - sha256。
   - 临时文件 atomic rename。
3. 抽 jobs：
   - task model。
   - task status transition。
   - owner/workspace scope。
4. 抽 market dispatch：
   - market normalization。
   - profile lookup。
   - fallback policy。
5. 抽 results：
   - result_complete.md。
   - financial_data.json。
   - source map。
   - quality summary。
6. route 层只做参数解析、鉴权、调用 service、返回 JSON。

验收命令：

```bash
cd apps/pdf-parser
python3 -m pytest tests -q
```

通过标准：

- 所有现有 endpoint 路径保持兼容。
- `pdf_parser_app_impl.py` 第一阶段低于 2000 行，第二阶段低于 1200 行。
- 所有路径写入使用统一 storage helper。

### P3-03 拆解 `apps/pdf-parser/financial_extractor.py`

当前规模：约 3807 行。

当前职责：

- 表格识别
- statement 分类
- financial metric 抽取
- 单位/币种/期间推断
- 多市场 profile 兼容
- warnings/quality 状态
- markdown/table fallback

目标结构：

| 目标模块 | 职责 |
| --- | --- |
| `financial_extractor.py` | 兼容 facade |
| `financial_extractor_models.py` | DTO、statement、metric、warning model |
| `financial_extractor_tables.py` | 表格解析、header/row normalization |
| `financial_extractor_statements.py` | 三表识别与 statement mapping |
| `financial_extractor_metrics.py` | 指标抽取和 canonical metric name |
| `financial_extractor_units.py` | 单位、币种、scale、期间推断 |
| `financial_extractor_quality.py` | sanity gate、warnings/fail 分类 |
| `financial_extractor_markdown.py` | markdown fallback |

详细任务：

1. 先冻结现有测试：
   - CN/HK/EU/JP/KR/US 样本。
   - 三表识别。
   - 单位/scale。
   - warnings/fail 分类。
2. 抽 DTO 和常量，避免循环 import。
3. 抽表格 normalization，输入输出均为 typed structures。
4. 抽 statement mapping，独立测试三表识别。
5. 抽 units/scale，并加入异常 scale 负向测试。
6. 抽 quality gate，承接 P1 财务 sanity gate。
7. facade 保留旧函数名，逐步迁移调用方。

验收命令：

```bash
cd apps/pdf-parser
python3 -m pytest tests/test_pdf_parser_financial_service.py tests/test_page_markers.py -q
```

通过标准：

- 不降低 EU/HK/CN/JP/KR/US 现有解析测试通过率。
- warning/fail 语义不放宽。
- `financial_extractor.py` 第二阶段低于 1200 行。

### P3-04 拆解 `agents/hermes/profiles/siq_analysis/scripts/html_renderer_v2.py`

当前规模：约 4211 行。

当前职责：

- report HTML render
- 模板拼装
- 图表/表格
- CSS/asset
- escaping/sanitization
- section layout
- CLI 参数

目标结构：

| 目标模块 | 职责 |
| --- | --- |
| `html_renderer_v2.py` | CLI/facade |
| `renderer_models.py` | 渲染输入 DTO |
| `renderer_templates.py` | 页面模板和 section template |
| `renderer_sections.py` | 各章节渲染 |
| `renderer_charts.py` | chart/table rendering |
| `renderer_assets.py` | CSS、字体、静态资源 |
| `renderer_sanitize.py` | escaping、HTML 安全 |
| `renderer_toc.py` | 目录和 anchor |

详细任务：

1. 建立 golden HTML snapshot 或结构化断言：
   - report title。
   - section count。
   - citation anchor。
   - chart/table placeholder。
   - escaping。
2. 先抽 CSS/asset 常量。
3. 再抽 section renderer。
4. 再抽 chart/table renderer。
5. 最后抽 CLI 与 data loading。

验收：

```bash
cd /home/maoyd/siq-research-engine
python3 -m pytest agents/hermes/profiles/siq_analysis/tests -q
```

若测试目录不足，先新增最小 renderer snapshot 测试。

### P3-05 拆解 `packages/market-contracts/src/siq_market_contracts/evidence_package.py`

当前规模：约 2121 行。

当前职责：

- evidence package model
- validation rules
- gate decision
- artifact hash
- evidence resolvability
- import/vector block decision

目标结构：

| 目标模块 | 职责 |
| --- | --- |
| `evidence_package.py` | 兼容 facade |
| `evidence_models.py` | dataclass/pydantic-like model |
| `evidence_hashing.py` | hash、manifest、artifact integrity |
| `evidence_resolver.py` | source path/url/source map resolvability |
| `evidence_gates.py` | gate severity、promotion target、force_allowed |
| `evidence_validation.py` | validation orchestration |

详细任务：

1. 先补 gate decision tests：
   - hard fail cannot import。
   - soft fail can review。
   - force_allowed=false 不可绕过。
   - unresolvable evidence hard block。
2. 抽 model，不改变 public import。
3. 抽 hashing/resolver。
4. 抽 gates。
5. facade re-export 旧 API。

验收：

```bash
cd packages/market-contracts
uv run python -m pytest tests -q
```

### P3-06 拆解 API 超长 routers

目标文件：

- `apps/api/routers/workflow.py`，约 2566 行
- `apps/api/routers/primary_market_meeting.py`，约 2056 行
- `apps/api/routers/deals.py`，约 1726 行
- `apps/api/routers/workspace.py`，约 1693 行

拆解策略：

1. 不先改 URL。
2. 先把 business logic 下沉到 `apps/api/services/*`。
3. router 文件只保留 request/response、Depends、status code。
4. 对同一路由域按资源拆分：
   - workflow jobs
   - workflow artifacts
   - workflow references
   - meeting readiness
   - meeting notes
   - deal documents
   - deal decisions
   - deal reports
5. 使用 `APIRouter.include_router` 保持 OpenAPI path 不变。

验收命令：

```bash
cd apps/api
uv run python -m pytest \
  tests/test_workflow_job_service.py \
  tests/test_workflow_subprocess_contracts.py \
  tests/test_primary_market_meeting_router.py \
  tests/test_deals_router.py \
  tests/test_workspace_sync.py \
  -q
```

通过标准：

- OpenAPI path 不变。
- 权限依赖不丢失。
- 单 router 文件逐步低于 1200 行。

### P3-07 拆解 vector ingestion 脚本

目标文件：

- `scripts/vector-index/milvus-ingestion/ingest_final.py`，约 3643 行
- `scripts/vector-index/milvus-ingestion/ingest_cloud_bailian.py`，约 3184 行

目标结构：

| 目标模块 | 职责 |
| --- | --- |
| `ingest_final.py` | CLI/facade |
| `ingestion_config.py` | env/config loading |
| `ingestion_documents.py` | document discovery/chunk |
| `ingestion_embeddings.py` | embedding provider |
| `ingestion_milvus.py` | Milvus collection/upsert |
| `ingestion_filters.py` | quality gate / source filter |
| `ingestion_report.py` | summary and audit output |

详细任务：

1. 先补 dry-run 测试。
2. 抽 config loader。
3. 抽 document discovery。
4. 抽 embedding provider。
5. 抽 Milvus adapter。
6. 强制只消费 `quality_passed` 或审计允许的 review 内容。

验收：

```bash
cd /home/maoyd/siq-research-engine
python3 -m py_compile scripts/vector-index/milvus-ingestion/*.py
python3 -m pytest scripts/maintenance/tests/test_run_market_ingestion_eval.py -q
```

### P3-08 前端大组件轻拆

目标文件：

- `apps/web/src/pages/PrimaryMarketMeeting.tsx`
- `apps/web/src/pages/DealWorkflow.tsx`
- `apps/web/src/features/primary-market/primaryMarketViewModel.ts`

拆解策略：

1. 不改变主视觉。
2. 先抽纯 view model selector。
3. 再抽 toolbar、list、detail、status panel。
4. 再抽 hooks。
5. 保留 page-level composition。

验收：

```bash
cd apps/web
npm run test:unit
npm run check:frontend
```

## P4：CI、测试和质量门禁

### P4-01 增加路径一致性 CI

目标：防止 `var/runtime`、`data/artifacts` 再次漂移。

任务：

1. 新增路径契约测试。
2. 检查 env example 中关键路径。
3. 检查 Compose config 中关键 mount。
4. `find`/扫描脚本默认 prune `data/var/artifacts/runtimes/node_modules/.venv`。

### P4-02 增加安全负向测试

覆盖：

- parser 无 token。
- 伪造 admin header。
- finder/rules 无内部 token。
- API proxy 非 allowlist path。
- viewer 执行上传/删除/入库。
- US SEC 上传超限。
- system.config 命令路径越界。

### P4-03 增加可信链路负向测试

覆盖：

- hard gate + force=true 仍不可 import。
- unresolvable evidence 不可 vector ingest。
- fail load plan 不生成 canonical rows。
- unknown external source 不进入核心结论。
- factcheck block 不可 publish_ready。

### P4-04 大文件阈值观察门

先做 observe，不阻断：

1. CI 输出 top 20 最大源码文件。
2. 超过 2500 行 warning。
3. 超过 4000 行 report。
4. 连续两个迭代无下降再升级为 soft gate。

## 6. 智能体集群协作拆分

建议按独占写集并行：

| Worker | 任务 | 独占写集 |
| --- | --- | --- |
| Path Worker | P0-01 | `infra/env/*`、`start_all.sh`、`path_config.py`、路径测试 |
| Security Worker | P0-02/P0-03/P0-04 | parser auth、finder/rules auth、API proxy、market_reports gate |
| Data Quality Worker | P1-01/P1-02 | `market-contracts`、`market-report-rules` |
| Hermes Worker | P1-03/P1-05 | `agents/hermes/profiles/siq_analysis`、factchecker、tracking |
| Frontend Worker | P2 | `apps/web` |
| API Runtime Refactor Worker | P3-01 | `apps/api/services/agent_*` 相关文件 |
| PDF Parser Refactor Worker | P3-02/P3-03 | `apps/pdf-parser` |
| Renderer Refactor Worker | P3-04 | `agents/hermes/profiles/siq_analysis/scripts/*renderer*` |
| Contracts Refactor Worker | P3-05 | `packages/market-contracts` |
| Router Refactor Worker | P3-06 | `apps/api/routers` 与对应 services/tests |

并行限制：

1. `apps/api/routers/market_reports.py` 只能由 Security Worker 写。
2. `packages/market-contracts/src/siq_market_contracts/evidence_package.py` 只能由 Data Quality 或 Contracts Refactor Worker 写，不能同时写。
3. `apps/pdf-parser/pdf_parser_app_impl.py` 和 `financial_extractor.py` 不能同时被两个 worker 改。
4. `agents/hermes/profiles/siq_analysis/scripts/merge_research_packs.py` 只能由 Hermes Worker 写。
5. 前端 P2 与后端 P0/P1 可并行，但涉及 API response schema 的文案必须等后端契约冻结。

## 7. 推荐提交顺序

1. `chore(paths): align runtime path contract and add doctor output`
2. `fix(security): require internal parser service authentication`
3. `fix(security): restrict market service proxy and service tokens`
4. `fix(quality): enforce force_allowed and hard gate promotion decisions`
5. `feat(rules): add promotion decisions to market load plans`
6. `feat(contracts): enforce evidence resolvability across import gates`
7. `feat(analysis): add research fact status and schema validation`
8. `fix(web): align parsing copy with artifact-first ingestion`
9. `fix(web): make parser task result rows accessible`
10. `refactor(api): split chat runtime lifecycle and streaming modules`
11. `refactor(pdf): split parser routes storage jobs and results`
12. `refactor(pdf): split financial extractor table statement quality modules`
13. `refactor(analysis): split html renderer modules`
14. `refactor(contracts): split evidence package validation modules`
15. `chore(ci): add path security and large-file observe checks`

## 8. 总体验收计划

分批验收：

```bash
cd /home/maoyd/siq-research-engine
git diff --check
./scripts/check_all.sh
```

核心模块验收：

```bash
cd apps/api
uv run python -m pytest tests -q

cd ../pdf-parser
python3 -m pytest tests -q

cd ../document-parser
python3 -m pytest tests -q

cd ../../services/market-report-finder
uv run python -m pytest tests -q

cd ../market-report-rules
uv run --extra dev pytest tests -q

cd ../../packages/market-contracts
uv run python -m pytest tests -q

cd ../../apps/web
npm run test:unit
npm run check:frontend
```

最终通过标准：

1. 路径 doctor 输出一致，env example、Compose、代码默认值一致。
2. Parser、finder、rules 内部服务不能被无 token 直连写入。
3. viewer 不能执行上传、删除、重试、入库、强制导入等写操作。
4. `force=true` 不能绕过 hard gate。
5. fail package 不进入 canonical facts 或 vector index。
6. evidence resolvability 被 rules/load-plan/API 消费。
7. Research pack 保留 fact status、confidence、review_required。
8. 前端不再把 Wiki 表达为主数据源。
9. 大文件拆解后测试保持通过，public imports 保持兼容。
10. 所有质量提升通过更明确的契约、测试和审计实现，而不是降低标准。

## 9. 回滚策略

1. P0/P1 安全和质量任务：按提交回滚，不能保留半套契约。
2. 路径任务：只回滚代码和 env example，不移动真实数据。
3. 大文件拆解：facade 保留期间可以回滚到旧入口；新模块未稳定前不删除旧 public API。
4. 前端任务：如出现视觉或交互回归，优先回滚组件拆分，保留文案修正。
5. CI 任务：observe gate 可暂时降级为报告输出；hard security gate 不应降级。

## 10. 非目标

本计划不包含：

1. 重新设计产品首页或主视觉。
2. 自动迁移 `data/` 历史目录。
3. 替换 MinerU 或重写解析引擎。
4. 把 Wiki 删除；Wiki 仍可作为解析产物派生知识资产存在。
5. 将所有质量规则一次性设为 hard gate。
6. 引入新的大型框架或微服务拆分。

## 11. 第一批建议执行包

建议第一批只做以下 4 件事，风险最低、收益最大：

1. P0-01 路径契约和 doctor。
2. P0-02 parser 内部 token。
3. P0-04 force gate 修正。
4. P2-01 去 Wiki 主心智文案。

第一批完成后再启动大文件拆解。这样可以先把可信边界钉住，再让重构在清晰边界内发生。
