# SIQ 风险校准型项目优化方案

日期：2026-07-06

状态：可执行任务书

适用范围：`/home/maoyd/siq-research-engine`

上游依据：

- `docs/architecture/2026-07-06-siq-full-audit-optimization-execution-plan.md`
- 2026-07-06 第二轮全方位只读深度审计结论
- 关于“门禁过严可能导致智能体频繁阻断、反向降低质量”的讨论结论

## 1. 核心目标

本方案的目标不是把项目改得更“严厉”，而是把项目改得更“可信”。

优化后应满足：

1. 不降低财务事实精度。
2. 不通过放宽断言、删除测试、降级 quality gate 来制造绿灯。
3. 不大改现有前端视觉风格和主交互流程。
4. 不阻断智能体探索、解析和草稿生成。
5. 严格阻断“不可信结果”晋升为正式事实、正式检索内容或生产发布物。
6. 所有例外都必须可审计、可追溯、可回滚。

一句话原则：

> 智能体可以探索，结果可以落草稿，证据不足进入 review；只有通过硬门禁的内容才能进入 canonical facts、RAG 检索链路和生产发布。

## 2. 风险校准原则

### 2.1 不做一刀切门禁

所有质量规则分为三类：

| 类型 | 用途 | 是否阻断 | 典型处理 |
| --- | --- | --- | --- |
| Hard Gate | 不可逆、高风险、会污染可信链路的问题 | 是 | 直接阻断晋升或发布 |
| Soft Gate | 有风险但可人工复核、可延后修复的问题 | 否 | warning、review queue、降级展示 |
| Observe Gate | 新规则或误杀率未知的规则 | 否 | 只记录指标，不影响流程 |

### 2.2 阻断晋升，不阻断探索

以下行为允许继续进行：

- PDF 解析生成原始产物。
- LLM 生成摘要或候选解释。
- market finder 下载候选文件。
- 前端展示草稿、候选证据和 review 状态。
- 智能体在 sandbox 或本地任务空间中尝试修复。

以下行为必须受门禁控制：

- 写入 canonical financial facts。
- 进入正式 RAG/vector index。
- 被标记为 official/verified source。
- 作为财务结论被 agent runtime 引用。
- 合入生产部署配置。
- 对外暴露无鉴权内部服务。

### 2.3 质量不能靠“降低标准”获得

禁止以下行为：

1. 删除 failing test。
2. 放宽核心断言来通过测试。
3. 把应为 `fail` 的财务事实污染改成 `warning`。
4. 把无证据事实写入正式事实表。
5. 在文案上隐藏失败状态。
6. 用 `force=true` 绕过 hard gate。
7. 把真实 secret、token、数据库文件、runtime 数据提交到 git。

## 3. 门禁分级定义

### 3.1 Hard Gate

Hard Gate 只用于不可逆或高危路径。命中后不得进入可信链路。

#### 安全类

- 真实密钥、token、`.env`、auth.json、数据库文件进入 git staged diff。
- Auth、RBAC、workspace/tenant isolation 被破坏。
- viewer 等低权限角色可以执行上传、解析、重试、删除、入库等写操作。
- 内部 parser、market finder、model endpoint、Milvus、MinIO 对外暴露且无鉴权。
- Cookie 模式下状态变更请求缺少 CSRF 防护。
- source HTML 在同源上下文中直接渲染，且无法证明已安全隔离。

#### 财务事实类

- canonical financial facts 无 `evidence_id`。
- quality gate 为 `fail` 的 package 进入正式 DB、vector index 或 agent trusted context。
- 核心三表必要项目缺失却被标记为 pass。
- XBRL/PDF evidence 无法解析到原始事实位置，却作为正式事实入库。
- 官方来源无法验证，但被标记为 official source。
- 测试通过依赖降低财务规则严格性。

#### 工程交付类

- CI 关键测试被删除或静默跳过。
- 生产 compose/systemd 引入 `--reload`、默认密码、`latest` 关键镜像、无鉴权公网端口。
- 迁移脚本可能删除或覆盖 `data/` 中真实运行态文件。
- agent worker 修改非授权写集并覆盖他人改动。

### 3.2 Soft Gate

Soft Gate 不阻断开发，但必须进入 review queue 或降级展示。

- 财务证据覆盖率不足，但能定位到候选 PDF 页或 XBRL fact。
- accounting standard 为 `UNKNOWN`，但结果仅作为候选展示。
- source tier 不明确，但未进入 official/verified 链路。
- 市场规则不完整但不影响核心事实污染。
- PDF parser 回退策略命中，例如 OCR/table fallback。
- 前端可访问性、长报告性能、状态恢复问题。
- 非核心路径缺少测试。
- DevOps hardening 不完整但没有生产暴露。

### 3.3 Observe Gate

Observe Gate 用于新规则冷启动。建议运行 1-2 个迭代周期，统计：

- 命中率。
- 误杀率。
- 修复成本。
- 对智能体开发效率的影响。
- 对财务结果 precision/recall 的实际影响。

满足以下条件后再升级为 Soft 或 Hard：

- 误杀率低。
- 修复路径明确。
- 对项目可信链路有直接价值。
- 有稳定测试或 golden dataset 支撑。

## 4. 可信链路晋升模型

### 4.1 财务数据状态机

建议所有财务事实遵循以下状态：

| 状态 | 含义 | 可展示 | 可入库 | 可检索 | 可被 agent 当作事实 |
| --- | --- | --- | --- | --- | --- |
| `raw_artifact` | 原始 PDF/HTML/XBRL/Markdown 产物 | 是 | 原始区 | 否 | 否 |
| `candidate_fact` | parser/LLM/rule 提取的候选事实 | 是 | 候选区 | 否 | 否 |
| `evidence_located` | 有页码、bbox、table、xbrl tag 或 quote | 是 | 候选区 | 限 review | 否 |
| `evidence_verified` | 原始值、单位、期间、币种、scale 校验通过 | 是 | review 区 | 可灰度 | 限说明 |
| `quality_passed` | 市场规则和质量门禁通过 | 是 | canonical | 是 | 是 |
| `review_exception` | 人工例外允许保留 | 是 | review 区 | 否，除非二次批准 | 否 |

核心规则：

- `candidate_fact` 不得直接进入 canonical。
- `review_exception` 不得自动进入 vector index。
- `quality_passed` 必须可追溯到原始 source hash 和 evidence hash。
- LLM 输出永远不能单独提升事实等级，必须引用已验证 evidence。

### 4.2 Source Trust Level

建议统一 source tier：

| Tier | 含义 | 示例 |
| --- | --- | --- |
| `official_regulator` | 监管机构官方源 | SEC EDGAR、HKEXnews、ESEF regulator、EDINET、DART |
| `official_issuer` | 公司官方 IR 或公告源 | 公司投资者关系网站 |
| `recognized_vendor` | 可信数据供应商 | 经 allowlist 的数据服务 |
| `unverified_web` | 普通网页或搜索结果 | 搜索结果、新闻、未知 CDN |
| `local_uploaded` | 用户上传 | 用户本地文件 |

规则：

- 只有 `official_regulator` 和经过验证的 `official_issuer` 能直接支持 official evidence。
- `unverified_web` 只能作为候选线索。
- redirect 后最终 URL、DNS 解析 IP、content hash 都应进入 source manifest。

## 5. 分阶段执行计划

## P0：可信链路硬边界

目标：不阻断智能体探索，但阻断污染可信事实、检索链路和生产安全边界。

### P0-01 引入分级门禁契约

目标：把 gate 从简单 pass/warning/fail 升级为可执行决策。

建议新增或扩展结构：

- `GateSeverity`: `hard`, `soft`, `observe`
- `GateMode`: `observe`, `warn`, `enforce`
- `GateDecision`: `allow`, `review`, `block`
- `PromotionTarget`: `draft`, `review`, `canonical`, `retrieval`, `production`

设计要求：

1. 同一个问题在不同 promotion target 下可以有不同决策。
2. `fail` 不必阻断原始产物生成，但必须阻断 canonical/retrieval。
3. 所有 gate 输出必须包含 `rule_id`、`severity`、`reason`、`target`、`evidence_refs`。
4. 前端展示时区分“解析完成”和“可信通过”，避免用户误解。

优先写集：

- `packages/market-contracts/src/siq_market_contracts/evidence_package.py`
- `packages/market-contracts/tests/`
- `services/market-report-rules/src/market_report_rules_service/validation.py`
- `services/market-report-rules/tests/`

验收：

```bash
cd packages/market-contracts
uv run python -m pytest tests -q

cd ../../services/market-report-rules
uv run --extra dev pytest tests -q
```

### P0-02 强制阻断 fail package 入正式链路

目标：让 `quality_gate=fail` 的 evidence package 可以保存为 review artifact，但不能写入 canonical facts 或 vector index。

执行要求：

1. 所有 import 脚本入库前调用统一 `enforce_quality_gates(package_dir, target="canonical")`。
2. `block` 时退出非零，并输出阻断规则。
3. `review` 时允许写入 review/staging 区，但不得写入正式事实表或向量索引。
4. `force` 只允许处理 Soft Gate，且必须记录操作者、原因、时间、package hash。
5. Hard Gate 不允许 `force`。

重点文件：

- `db/imports/import_hk_evidence_package_to_postgres.py`
- `db/imports/import_eu_evidence_package_to_postgres.py`
- `db/imports/import_market_xbrl_package_to_postgres.py`
- `scripts/maintenance/run_market_ingestion_eval.py`

验收：

- 构造 fail package，确认不能写入 canonical。
- 构造 warning package，确认进入 review 或带审计记录的 staging。
- 构造 pass package，确认原有入库流程不退化。

### P0-03 安全边界最小硬化

目标：避免内部服务误暴露，同时不大改开发体验。

执行要求：

1. document-parser、pdf-parser、market-report-finder 若 token 为空，生产 profile 必须拒绝启动。
2. compose 区分 local/dev/prod profile。
3. Milvus、MinIO、Attu、model endpoints 默认绑定 `127.0.0.1`。
4. 生产入口禁止 `--reload`。
5. viewer 不允许上传、解析、重试、删除、入库。
6. Cookie 模式状态变更请求强制 CSRF。

重点文件：

- `apps/api/services/auth_dependencies.py`
- `apps/api/routers/document_parser.py`
- `apps/api/routers/auth.py`
- `apps/document-parser/app.py`
- `apps/pdf-parser/pdf_parser_request_utils.py`
- `infra/docker/docker-compose.yml`
- `infra/vector-index/milvus/docker-compose.yml`
- `apps/web/src/shared/api/client.ts`

验收：

```bash
cd apps/api
uv run python -m pytest \
  tests/test_auth_dependencies.py \
  tests/test_auth_router_current_user.py \
  tests/test_document_parser_proxy.py \
  tests/test_workspace_sync.py \
  -q

docker compose -f infra/docker/docker-compose.yml --env-file infra/env/local.example config
```

### P0-04 Source SSRF 与 HTML 隔离

目标：确保 source 预览和远程下载不会成为 SSRF/XSS 入口。

执行要求：

1. URL 下载每次 redirect 后重新校验 host、scheme、IP 和 allowlist。
2. 禁止访问 private/link-local/loopback/cloud metadata IP。
3. HTML source 预览默认 sandbox，禁止同源脚本能力。
4. table HTML 清洗使用 allowlist 策略，而不是只删除少数危险片段。
5. source token 保持短期、任务级、不可日志化。

重点文件：

- `apps/api/routers/source.py`
- `services/market-report-finder/src/market_report_finder_service/services/downloader.py`
- `services/market-report-finder/src/market_report_finder_service/markets/url_ownership.py`
- `apps/web/src/lib/pdfSanitize.ts`

验收：

- redirect 到内网 IP 被拒绝。
- 普通官方 URL 正常。
- HTML 中 script、event handler、javascript URL、style 注入均被清理或隔离。

### P0-05 前端可信状态修复

目标：不改变主视觉，只修复会误导用户或破坏任务状态的稳定性问题。

执行要求：

1. `PdfSourceWorkbench` cache key 纳入 `taskId` 和 table identity。
2. SSE parser 支持标准空行 dispatch、残留 buffer、多行 `data:`。
3. chat stop 先本地 abort 和 UI 解锁，再 best-effort 调 stop API。
4. PDF result fetch 失败必须可见，不得静默吞掉。
5. 强制入库弹窗展示 gate reason、`force_allowed` 和审计后果。

重点文件：

- `apps/web/src/components/pdf/PdfSourceWorkbench.tsx`
- `apps/web/src/lib/agentChatStream.ts`
- `apps/web/src/lib/agentChatStore.ts`
- `apps/web/src/pages/pdf/usePdfTasks.ts`
- `apps/web/src/components/pdf/MarketEvidencePackagesPanel.tsx`

验收：

```bash
cd apps/web
npm run test:unit
npm run check:frontend
```

## P1：财务精度与可复核性

目标：把“证据存在”提升为“证据值可复核”，同时避免一开始就过度阻断。

### P1-01 值级 evidence verification

执行要求：

1. PDF evidence 校验页码、表格、单元格文本、原始值、标准化值。
2. XBRL evidence 校验 tag、context、period、unit、decimals、scale、dimension。
3. quote evidence 校验原文片段与事实值的可解释关系。
4. 校验失败默认 Soft Gate；进入 canonical 时升级 Hard Gate。

验收：

- 同一事实的 display value、normalized value、source raw value 可互相解释。
- 单位/币种/期间不一致不能进入 canonical。

### P1-02 官方来源 allowlist 与 source manifest

执行要求：

1. 每个 market 定义 official regulator allowlist。
2. 记录 initial URL、final URL、redirect chain、content hash、retrieved_at。
3. `official_issuer` 需要 issuer domain verification。
4. 未验证来源只能进入 candidate/review。

验收：

- SEC/HKEX/ESEF/EDINET/DART official package pass。
- 搜索引擎 URL、未知 CDN、redirect 到非 allowlist 域名不能作为 official evidence。

### P1-03 市场 accounting standard 精细化

执行要求：

1. JP 不默认 IFRS，应支持 `JGAAP`、`IFRS`、`UNKNOWN`。
2. KR 默认 `KIFRS`，未知标准不能 pass。
3. EU/US/HK 的 standard 必须来自 market metadata 或 official package。
4. `UNKNOWN` 在草稿可展示，进入 canonical 必须 review。

验收：

- JP IFRS/J-GAAP 样本分别通过对应规则。
- UNKNOWN standard 不能直接进入 trusted facts。

### P1-04 LLM 财务增强 provenance

执行要求：

1. LLM 输出必须记录 provider、model、prompt_version、input_evidence_ids。
2. 记录 input_hash、output_hash、created_at。
3. evidence hash 变化时 LLM cache 自动失效。
4. LLM 输出不能提高 fact trust level，只能解释或补充已验证 facts。

验收：

- 同一 evidence hash 命中 cache。
- evidence 变化后重新生成。
- 无 evidence 的 LLM 财务结论不能进入 canonical。

### P1-05 golden dataset 扩充

建议样本：

- HK：腾讯、友邦、银行类年报、负数/括号/单位缩放样本。
- EU：ESEF iXBRL、extension tag、多语言表格。
- US：10-K、10-Q、20-F、dimension fact、segment fact。
- JP：EDINET IFRS 与 J-GAAP。
- KR：DART K-IFRS。
- 负样本：伪官方 URL、缺三表、币种错配、期间错配、hash mismatch。

验收：

```bash
python3 -m pytest scripts/maintenance/tests/test_run_market_ingestion_eval.py -q
```

## P2：前端、路径和运行态治理

目标：提升长期维护性，不重塑产品形态。

### P2-01 前端稳定性和可访问性

执行要求：

1. 上传 drop zone 支持键盘访问。
2. 表单 label 与控件显式关联。
3. 最近任务恢复写入 `?task=`，刷新可还原。
4. MarkdownBlocks 避免 O(n^2) 扫描。
5. PDF 虚拟滚动处理长行换行或改用实际测量。

验收：

- 主视觉和交互流程不重做。
- 单测和 frontend check 通过。
- 长报告渲染无明显退化。

### P2-02 路径治理兼容层

执行要求：

1. 不自动迁移 `data/`。
2. 新增 runtime path resolver，统一读取配置。
3. 文档标注旧路径和新路径映射。
4. 新产物优先写入新路径，旧路径保持兼容读取。

建议目标结构：

```text
runtime/
  api/
  document-parser/
  pdf-parser/
  market-report-finder/
artifacts/
  market-ingestion/
  evidence-packages/
  eval-runs/
infra/env/
  local.example
  docker.example
```

验收：

- 旧数据仍可读取。
- 新配置下服务可启动。
- `data/` 不被脚本自动删除、重命名或迁移。

### P2-03 DevOps hardening

执行要求：

1. Dockerfile 覆盖 services 目录。
2. Trivy 阈值从仅 CRITICAL 逐步扩展到 HIGH，但先 observe 一轮。
3. 生成 SBOM。
4. 关键镜像 pin digest。
5. 全服务补 `USER`、`read_only`、`cap_drop`、资源限制，按风险分批。
6. healthcheck 从浅层 alive 升级为依赖可用性检查。

验收：

```bash
docker compose -f infra/docker/docker-compose.yml --env-file infra/env/local.example config
./scripts/check_all.sh
```

## P3：可观测性与生产就绪

目标：让系统在真实运行中可解释、可排障、可审计。

执行要求：

1. 全链路 request-id。
2. parser/import/agent runtime 结构化 JSON 日志。
3. quality gate metrics：pass、review、block、override。
4. 财务事实 metrics：candidate、verified、canonical、retrieval indexed。
5. 内部服务 metrics：latency、queue depth、error rate、file size、memory。
6. 审计日志脱敏：不记录 token、source bearer、LLM key、完整用户敏感输入。
7. 质量回归 dashboard：按市场、issuer、report type、parser version 聚合。

验收：

- 一次 market ingestion 能从日志追踪到 source、package、gate、import、index。
- 一次 agent answer 能追踪到引用的 evidence ids 和 fact trust level。

## 6. 智能体协同执行协议

### 6.1 总协议

每轮执行前：

```bash
git status --short --branch
```

执行规则：

1. 每个 worker 只写自己的授权文件集合。
2. 不修改无关文件。
3. 不回滚他人改动。
4. 不通过删除测试或放宽核心断言制造绿灯。
5. 不把 Hard Gate 改成 Soft Gate，除非任务明确要求且有设计文档说明。
6. 失败时优先报告事实和阻断规则，不做不可逆自动修复。
7. 每个任务完成后运行最小相关测试。

### 6.2 推荐 worker 拆分

#### Worker A：Gate Contract

职责：

- 分级门禁结构。
- market-contracts 测试。
- market-rules gate 输出兼容。

禁止写：

- 前端。
- auth。
- compose。
- import 脚本以外的 DB schema。

#### Worker B：Import Promotion Guard

职责：

- HK/EU/XBRL import 前 gate enforcement。
- review/staging 与 canonical 区分。
- ingestion eval 对 block/review/pass 的测试。

等待：

- Worker A 的 gate contract 稳定。

#### Worker C：Security Boundary

职责：

- auth/RBAC/CSRF。
- internal parser token。
- local/prod compose profile。
- model/vector endpoint bind 修正。

禁止：

- 大改前端视觉。
- 改财务规则。

#### Worker D：Source Safety

职责：

- downloader redirect 校验。
- source HTML sandbox/sanitize。
- source token 日志脱敏测试。

#### Worker E：Frontend Stability

职责：

- SSE parser。
- PDF source cache。
- chat stop。
- PDF result fetch error。
- force dialog。

禁止：

- 改主导航、主视觉、主交互信息架构。

#### Worker F：Evidence Precision

职责：

- 值级 verification。
- official source manifest。
- accounting standard。
- LLM provenance。

等待：

- Worker A/B 完成后再进入 canonical enforcement。

### 6.3 集成顺序

推荐顺序：

1. Worker A：Gate Contract。
2. Worker B：Import Promotion Guard。
3. Worker C：Security Boundary。
4. Worker D：Source Safety。
5. Worker E：Frontend Stability。
6. Worker F：Evidence Precision。
7. 主智能体统一跑测试、审查 diff、处理冲突。

## 7. 测试与验收矩阵

### P0 最小验收

```bash
cd /home/maoyd/siq-research-engine
./scripts/check_all.sh
```

专项：

```bash
cd apps/api
uv run python -m pytest \
  tests/test_auth_dependencies.py \
  tests/test_auth_router_current_user.py \
  tests/test_document_parser_proxy.py \
  tests/test_workspace_sync.py \
  -q

cd ../web
npm run test:unit
npm run check:frontend

cd ../../packages/market-contracts
uv run python -m pytest tests -q

cd ../../services/market-report-rules
uv run --extra dev pytest tests -q
```

### P1 财务验收

```bash
cd /home/maoyd/siq-research-engine
python3 -m pytest scripts/maintenance/tests/test_run_market_ingestion_eval.py -q

cd packages/market-contracts
uv run python -m pytest tests -q

cd ../../services/market-report-finder
uv run python -m pytest tests -q
```

必须覆盖：

- fail package 不入 canonical。
- warning package 进入 review。
- pass package 保持原流程。
- evidence hash 变化触发 LLM cache 失效。
- official URL redirect 到非 allowlist 被拒绝。

### P2/P3 运行验收

```bash
cd /home/maoyd/siq-research-engine
docker compose -f infra/docker/docker-compose.yml --env-file infra/env/local.example config
./scripts/check_all.sh
```

补充检查：

```bash
git status --short --branch
git diff --check
git diff --name-only | rg '(^|/)(\.env|.*\.env|auth\.json)$|(^|/)(data|runtimes?)/' && exit 1 || true
```

## 8. 质量保护机制

### 8.1 禁止质量降级的审查清单

每个 PR 或提交必须检查：

- 是否删除了测试。
- 是否把 hard assertion 改成弱 assertion。
- 是否把 `fail` 改成 `warning` 却没有业务解释。
- 是否新增 `force` 但没有审计字段。
- 是否新增入库路径但没有 gate enforcement。
- 是否新增前端成功态但没有失败态。
- 是否新增外部 URL 下载但没有 allowlist/redirect/IP 校验。
- 是否新增 token/env 但没有示例和脱敏。

### 8.2 例外机制

只允许 Soft Gate 例外。

例外记录必须包含：

- `exception_id`
- `gate_rule_id`
- `package_hash` 或 `task_id`
- `promotion_target`
- `requested_by`
- `approved_by`
- `reason`
- `expires_at`
- `created_at`
- `audit_log_id`

限制：

- Hard Gate 无例外。
- 例外默认不能进入 vector index。
- 例外过期后必须重新 review。
- 例外不能改变原始 gate 结果，只能附加 override decision。

### 8.3 Observe 到 Enforce 的升级标准

一个规则从 observe 升级到 enforce 前必须满足：

1. 有测试覆盖。
2. 有至少一组真实或 golden 样本验证。
3. 误杀有解释和处理路径。
4. 前端或日志能清楚告诉用户为什么被拦。
5. 不会导致智能体无法保存草稿或 review artifact。

## 9. 非目标

本方案不做：

1. 前端大改版。
2. 重写 parser。
3. 自动迁移 `data/`。
4. 一次性重构所有路径。
5. 一次性把所有 warning 变 hard fail。
6. 用 LLM 替代规则引擎。
7. 用人工例外绕过核心安全和财务事实门禁。

## 10. 最终验收标准

项目优化完成后应满足：

1. 全量测试通过，且没有删除或弱化核心质量测试。
2. Hard Gate 命中时不会污染 canonical facts、vector index 或生产配置。
3. Soft Gate 能进入 review queue，并有清晰用户反馈。
4. Observe Gate 有指标记录，不影响智能体探索。
5. 财务事实能追踪到 source、evidence、value verification、quality gate 和 import decision。
6. LLM 输出有 provenance，不能无证据晋升事实等级。
7. 内部服务默认不会被公网误暴露。
8. 前端能正确呈现解析完成、质量待审、可信通过、阻断失败等不同状态。
9. 运行态路径治理不破坏现有数据。
10. 后续智能体可按 worker 写集并行开发，降低冲突和质量回退风险。

## 11. 推荐下一步执行顺序

第一批只做 P0：

1. Gate Contract。
2. Import Promotion Guard。
3. Security Boundary。
4. Source Safety。
5. Frontend Stability。

第二批做 P1：

1. Value-level Evidence Verification。
2. Official Source Manifest。
3. Accounting Standard Refinement。
4. LLM Provenance。
5. Golden Dataset Expansion。

第三批做 P2/P3：

1. Frontend a11y/perf。
2. Runtime Path Resolver。
3. Docker/CI/SBOM hardening。
4. Observability and Audit Dashboard。

这套顺序的核心好处是：先守住可信链路，再提升财务精度，最后治理工程体系。它避免一开始就把所有问题都变成 hard fail，也避免把低可信内容混入正式事实库。
