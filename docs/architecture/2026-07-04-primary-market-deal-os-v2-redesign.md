# SIQ Deal OS V2 重新设计方案

> 日期：2026-07-04  
> 状态：重新设计方案 / 后续实现任务书  
> 基线：基于 `2026-06-28-primary-market-openclaw-compat-design.md` 已迁移能力  
> 目标：把 OpenClaw 兼容层升级为 SIQ 原生一级市场 Deal OS，把“可导入、可查看、可 dry-run”的投委会原型改造成“可运行、可审计、可扩展、可产品化”的投研决策系统。

## 0. 设计结论

现有迁移已经证明三件事：

1. `siq_ic_*` Hermes profile 家族可以被 SIQ runtime 识别、启动、调用。
2. `data/wiki/deals/<deal_id>` 项目包可以承载 OpenClaw 项目导入、R1-R4 阶段产物、审计链和前端展示。
3. Deal OS API 和 Web 工作台已经具备从项目列表、证据、专家、工作流、报告、决策到审计的最小产品骨架。

因此 V2 不再以“复刻 OpenClaw 文件和流程”为主线，而以 SIQ 原生业务闭环为主线：

```text
Deal Intake
  -> Evidence Readiness
  -> Committee Execution
  -> Decision Review
  -> Archive and Learning
```

OpenClaw 继续作为 legacy import 来源和方法论参考；运行时主合同、UI、任务编排、证据检索和审计链都应收敛到 SIQ Deal OS。

## 1. 已迁移能力基线

V2 设计以当前代码中已经存在的能力为地基，而不是重新发明一套系统。

### 1.1 已具备能力

| 领域 | 当前能力 | V2 处理方式 |
| --- | --- | --- |
| Profile | 7 个 `siq_ic_*` profile、`siq_ic_shared`、manifest group、端口和 alias 已存在 | 保留 profile ID，补 profile registry 元数据和能力声明 |
| Hermes runtime | API registry、profile root、模型控制、gateway 脚本、`SIQ_ENABLE_IC_HERMES=1` 已支持 | 继续使用，但增加 health/readiness 与真实调用验收矩阵 |
| Deal package | `data/wiki/deals/<deal_id>`、manifest、project_meta、phases、discussion、decision、audit 已存在 | 继续作为归档权威源，新增 package schema 版本治理 |
| OpenClaw import | `ic_openclaw_importer.py` 可导入 legacy 项目并补部分合同 | 降级为 import adapter，不再驱动核心设计 |
| Evidence | file-backed evidence build、quality report、ingest dry-run、startup receipt 已存在 | 升级为 Evidence Service，接 PostgreSQL/Milvus 实写 |
| Workflow | R1 dry-run/real single agent、R1 serial、R1.5 dispute、R2/R3/R4 deterministic 写入、advance-next 已存在 | 抽象为 Phase Engine，统一状态迁移、锁、重试和审计 |
| Web | `/deals`、overview、data-room、evidence、workflow、agents、reports、decision、audit 已存在 | 重设计信息架构和操作权限，区分 preview、write、model-run |
| Tests | 后端 targeted tests、前端 unit、mock Playwright E2E 已覆盖关键路径 | 补真实 demo E2E、gateway real smoke、PostgreSQL/Milvus ingest tests |

### 1.2 当前主要问题

| 问题 | 影响 | V2 修正方向 |
| --- | --- | --- |
| 兼容层口径仍重 | 文档和 UI 仍围绕 OpenClaw 导入，产品心智不清晰 | 把 OpenClaw 移到 import/source adapter 层 |
| R2/R3/R4 是 deterministic | 可以写产物，但不是真实多 Agent 推理闭环 | 引入 deterministic core + optional model augmentation 双层模式 |
| Startup retrieval 是本地 evidence | 没有真实共享库、私有库、向量检索 | 升级 retrieval receipt 为可验证检索凭证 |
| Demo 包未产品化交付 | 本地存在但被 `.gitignore`，验收不稳定 | 提供 seed fixture 或一键导入命令 |
| 原始 R4 legacy 合同残留 | 服务层可兼容，归档层不纯净 | 增加 package migration/repair job |
| API 命名有迁移痕迹 | `start-r0` 等原计划接口与实现不完全一致 | 建立 V2 canonical API，legacy alias 只保留兼容 |
| 缺 job 锁和恢复语义 | 多用户/长任务可能重复写入或半写入 | 所有 write/model-run 进入 Job + phase lock |

## 2. V2 设计目标

### 2.1 产品目标

1. 一级市场项目从创建、材料导入、证据准备、投委会执行、人工确认到归档形成完整闭环。
2. 每个结论必须能回跳到 evidence、agent report、phase artifact 和 audit event。
3. 用户能明确区分三种操作：`preview`、`write deterministic result`、`run model agent`。
4. 系统输出是投委会建议，不是自动投资执行指令；R4 后必须保留人工确认或 override。
5. OpenClaw 导入项目可以作为 demo/历史归档继续使用，但新项目不需要理解 OpenClaw 路径。

### 2.2 工程目标

1. 以 `data/wiki/deals/<deal_id>` 为权威归档源，PostgreSQL/Milvus 作为索引和检索层。
2. 以 `Phase Engine` 统一 R0-R4 的状态推进、门禁、锁、审计和失败恢复。
3. 以 `Evidence Service` 统一文档解析产物、证据单元、质量门禁、向量入库和检索 receipt。
4. 以 `IC Agent Runtime` 统一 prompt payload、Hermes 调用、输出解析、合同校验和报告持久化。
5. 所有模型输出先经过 API 服务层校验和写入，不允许 profile 自行绕过权限写项目包。

### 2.3 非目标

P0.5/P1 阶段暂不做：

- 不新增新的专家角色。
- 不做复杂多租户权限模型。
- 不让一级市场数据进入二级市场 `companies` 链路。
- 不把 deterministic mode 删除；它仍是 preview、测试和失败恢复的重要兜底。
- 不默认启动 7 个 IC Hermes gateway。

## 3. 总体架构

V2 推荐采用四层结构：

```text
apps/web Deal OS Workbench
  -> apps/api routers/deals.py
  -> Deal Application Services
       - deal_store / package repository
       - evidence service
       - phase engine
       - agent runtime
       - decision service
       - audit service
  -> Infrastructure
       - data/wiki/deals package archive
       - PostgreSQL deal_os schema
       - Milvus siq_deal_* collections
       - Hermes siq_ic_* gateways
       - document-parser artifacts
```

关键改变：

```text
旧主线：OpenClaw project -> SIQ compatibility package -> Web view
新主线：SIQ deal -> evidence readiness -> phase engine -> committee decision -> archive
```

OpenClaw import 只进入 `Import Adapter`：

```text
OpenClaw Import Adapter
  -> normalize legacy artifacts
  -> repair package contracts
  -> append import audit
  -> hand off to normal Deal OS workflow
```

## 4. 核心领域模型

### 4.1 Deal Project

Deal Project 是一级市场业务根对象。V2 保留当前 `project_meta.json`，但增加状态分层：

| 字段 | 说明 |
| --- | --- |
| `status` | 项目生命周期：`draft`、`active`、`decision_pending`、`confirmed`、`archived` |
| `workflow_status` | 工作流运行状态：`r0_ready`、`r1_in_progress`、`r2_completed`、`r4_completed` |
| `review_status` | 人工复核状态：`pending`、`confirmed`、`rejected`、`overridden` |
| `source_kind` | `manual`、`openclaw_import`、`seed_fixture`、`api_import` |
| `confidentiality_level` | `private`、`restricted`、`committee_only` |

这样 UI 不再用一个 `status` 同时表达项目阶段、系统运行和人工确认。

### 4.2 Evidence Item

V2 继续使用 `siq_deal_evidence_item_v1`，但把证据质量分成两类：

| 类型 | 字段 | 用途 |
| --- | --- | --- |
| 事实属性 | `evidence_type`、`dimension`、`confidence` | 判断是否能进入 evidence gate |
| 检索属性 | `embedding_status`、`postgres_row_id`、`milvus_pk`、`retrieval_tags` | 判断是否能被 agent 检索 |

新增建议字段：

```json
{
  "indexing": {
    "postgres_status": "pending|indexed|failed",
    "milvus_status": "pending|indexed|failed",
    "last_indexed_at": null,
    "index_errors": []
  }
}
```

### 4.3 Startup Retrieval Receipt

当前 receipt 是“本地 evidence package 摘要”。V2 要把它升级为“发言资格凭证”：

```json
{
  "schema_version": "siq_ic_startup_receipt_v2",
  "receipt_id": "startup-siq_ic_finance_auditor-R1-001",
  "deal_id": "DEAL-YUSHU-2026-001",
  "agent_id": "siq_ic_finance_auditor",
  "round_name": "R1",
  "retrieval_mode": "hybrid_vector_v1",
  "shared_hits": 12,
  "private_hits": 4,
  "evidence_ids": [],
  "queries": [],
  "collections": ["siq_deal_shared", "siq_ic_finance_auditor"],
  "rules_read": ["SOUL.md", "AGENTS.md"],
  "gate": {
    "allowed_to_speak": true,
    "blocking_reasons": [],
    "warnings": []
  },
  "created_at": "..."
}
```

没有 valid receipt 的 agent 输出不得进入正式 R1/R2/R4 报告。

### 4.4 Decision Contract

R4 决策合同必须完全 SIQ 化，不再允许 legacy shape 作为主归档：

```json
{
  "schema_version": "siq_ic_r4_decision_v2",
  "deal_id": "DEAL-YUSHU-2026-001",
  "decision": "pass|review|fail|manual_override",
  "final_score": 78.55,
  "weighted_agent_score": 84.2,
  "chairman_dimension_score": 78.55,
  "chairman_qualitative_decision": "建议投资，但需设置估值和退出保护条款",
  "threshold_result": "pass",
  "conditions": [],
  "monitoring_metrics": [],
  "human_confirmation": {
    "status": "pending|confirmed|rejected|overridden",
    "confirmed_by": null,
    "confirmed_at": null,
    "override_reason": null,
    "override_decision": null,
    "override_score": null
  },
  "artifact_paths": {
    "markdown": "decision/IC_DECISION_REPORT.md",
    "html": "decision/IC_DECISION_REPORT.html",
    "payload": "decision/decision_payload.json"
  }
}
```

Legacy R4 payload 只能存在于 `compatibility.legacy_payload` 或 `audit/legacy_*`，不能作为主合同继续扩散。

## 5. Phase Engine 重新设计

### 5.1 统一阶段状态机

当前 workflow 能写 R1/R2/R3/R4，但逻辑分散在 endpoint/service 中。V2 抽象为 Phase Engine：

```text
Phase Engine
  - load_state(deal_id)
  - preflight(phase, mode)
  - preview(action)
  - acquire_lock(action)
  - execute(action)
  - validate_artifacts(action)
  - commit_state(action)
  - append_audit(action)
  - release_lock(action)
```

每个动作都有统一 envelope：

```json
{
  "action": "run-r2",
  "mode": "preview|deterministic|model",
  "dry_run": true,
  "allowed": true,
  "blocking_reasons": [],
  "warnings": [],
  "inputs": {},
  "outputs": {},
  "audit_preview": {}
}
```

### 5.2 三种执行模式

| 模式 | 是否调用模型 | 是否写文件 | 用途 |
| --- | --- | --- | --- |
| `preview` | 否 | 否 | 前端展示门禁、计划和风险 |
| `deterministic` | 否 | 是 | R1.5、R2/R3/R4 兜底产物、测试、恢复 |
| `model` | 是 | 是 | 正式专家发言、模型主席裁决、模型 R2/R3/R4 |

现有 `dry_run=true/false` 保留，但 V2 API 应显式增加 `mode`，避免用户把 deterministic 写入误解为模型自动投委会。

### 5.3 阶段职责

| 阶段 | V2 目标 | 默认模式 | 升级路径 |
| --- | --- | --- | --- |
| R0 | 项目准入、材料完整性、证据门禁 | deterministic | 增加 coordinator model review，但不替代 gate |
| R1 | 专家串行尽调 | model | 保留 dry-run 和单 agent real smoke |
| R1.5 | 分歧识别和主席裁决 | deterministic + optional model | 先 deterministic 生成草案，再允许主席模型修订 |
| R2 | 专家观点修订 | deterministic | 增加 per-agent model revision |
| R3 | 红蓝对抗或留痕跳过 | deterministic | 增加 risk/chairman model challenge |
| R4 | 投决报告、评分、人工确认入口 | deterministic | 增加 chairman model report drafting |

## 6. Agent Runtime 重新设计

### 6.1 Agent 不是文件写入者

Hermes profile 只负责生成结构化观点，API 服务层负责：

1. 构造 task payload。
2. 调用 Hermes。
3. 提取 JSON summary。
4. 校验合同和 evidence IDs。
5. 写 phase JSON 和 markdown。
6. 推进 workflow state。
7. 写 audit event。

这条边界必须保留，否则权限、审计和恢复都会失控。

### 6.2 Profile 能力声明

V2 在 `ic_profile_matrix.json` 或 registry 中给每个 profile 增加能力字段：

```json
{
  "id": "siq_ic_finance_auditor",
  "domain": "primary_market",
  "role": "finance",
  "allowed_phases": ["R1", "R2"],
  "requires_startup_receipt": true,
  "default_collection": "siq_ic_finance_auditor",
  "can_make_final_decision": false,
  "can_generate_terms": false
}
```

`siq_ic_chairman` 是唯一可以生成 `chairman_ruling` 和 `chairman_qualitative_decision` 的模型角色；`siq_ic_master_coordinator` 只做编排和门禁，不代替专家观点。

### 6.3 R1 串行执行规则

继续保留固定顺序：

```text
siq_ic_strategist
siq_ic_sector_expert
siq_ic_finance_auditor
siq_ic_legal_scanner
siq_ic_risk_controller
siq_ic_chairman
```

但 V2 增加两层执行保护：

- `sequence lock`：同一 deal 同一 phase 只能有一个 active agent run。
- `receipt lock`：receipt 生成后进入 agent run 前要记录 hash，避免 agent 基于旧检索结果发言。

## 7. Evidence Service 重新设计

### 7.1 从 build 到 readiness

V2 不再把 evidence build 当成单次工具，而是一个 readiness pipeline：

```text
Document artifacts
  -> evidence extraction
  -> quality gate
  -> postgres indexing
  -> milvus chunk indexing
  -> retrieval readiness
  -> startup receipts
```

前端展示不只显示 evidence count，还要显示：

- gate 是否满足 R0/R1/R4。
- PostgreSQL 是否 indexed。
- Milvus 是否 indexed。
- 哪些 agent 的 retrieval readiness 未满足。
- 哪些 evidence IDs 被报告引用、哪些从未被使用。

### 7.2 PostgreSQL 和 Milvus 分工

| 层 | 责任 |
| --- | --- |
| `data/wiki/deals` | 权威归档，保留原文、报告、审计、完整 JSON |
| PostgreSQL `deal_os` | 项目、文档、证据、报告、分歧、决策、审计的索引和查询 |
| Milvus | 证据 chunk、角色私有知识库、语义检索 |
| Manifest | 记录 package 文件、hash、索引状态和迁移状态 |

### 7.3 检索必须可复现

每个 startup receipt 至少保存：

- 查询文本和 query intent。
- collection 名称。
- evidence IDs。
- hit score。
- source path。
- embedding/index version。
- created_at。

这样 agent 输出引用 evidence_id 时，可以回放“模型发言前看到了什么”。

## 8. API 重新设计

### 8.1 Canonical API

V2 推荐保留现有 route，但把语义整理为 canonical API：

```text
GET  /api/deals
POST /api/deals
GET  /api/deals/{deal_id}

POST /api/deals/{deal_id}/documents
POST /api/deals/{deal_id}/documents/{document_id}/bind-parser-task

POST /api/deals/{deal_id}/evidence/build
POST /api/deals/{deal_id}/evidence/index
GET  /api/deals/{deal_id}/evidence/readiness

GET  /api/deals/{deal_id}/workflow
POST /api/deals/{deal_id}/workflow/actions/{action}/preview
POST /api/deals/{deal_id}/workflow/actions/{action}/execute
POST /api/deals/{deal_id}/workflow/advance-next

GET  /api/deals/{deal_id}/agents
POST /api/deals/{deal_id}/agents/{profile_id}/startup-retrieval
POST /api/deals/{deal_id}/agents/{profile_id}/runs

GET  /api/deals/{deal_id}/reports
GET  /api/deals/{deal_id}/decision
POST /api/deals/{deal_id}/decision/human-confirmation
GET  /api/deals/{deal_id}/audit
```

现有 endpoint 如 `workflow/run-r2`、`workflow/finalize-r4` 可以先保留；新 UI 优先走 actions API，旧 API 作为兼容层。

### 8.2 Action Registry

所有 workflow action 注册到一个表：

| Action | Phase | Preview | Deterministic | Model | Writes |
| --- | --- | --- | --- | --- | --- |
| `build-evidence` | R0 | yes | yes | no | evidence package |
| `run-r1-agent` | R1 | yes | no | yes | r1_reports |
| `run-r1-serial` | R1 | yes | no | yes | r1_reports |
| `identify-disputes` | R1.5 | yes | yes | optional | r1_5_disputes |
| `generate-dispute-rulings` | R1.5 | yes | yes | optional | r1_5_disputes |
| `run-r2` | R2 | yes | yes | optional | r2_reports |
| `run-r3` | R3 | yes | yes | optional | r3_reports |
| `finalize-r4` | R4 | yes | yes | optional | r4_decision, report |
| `human-confirm` | R4 | yes | yes | no | r4_decision, audit |

前端通过 action metadata 决定按钮、文案和风险提示，减少硬编码。

## 9. Web 工作台重新设计

### 9.1 导航结构

保留当前路由，但重新定义页面职责：

| 页面 | V2 职责 |
| --- | --- |
| `/deals` | 项目列表、导入、新建、状态筛选、需要人工处理的队列 |
| `/deals/:dealId` | 项目 command center：下一步动作、风险、证据、决策状态 |
| `/data-room` | 材料、解析绑定、解析状态、缺口 |
| `/evidence` | evidence readiness，而不是单纯 evidence list |
| `/workflow` | 阶段推进台：preview / deterministic write / model run 明确分离 |
| `/agents` | profile readiness、startup receipt、报告状态、gateway health |
| `/reports` | 阶段产物目录和可读报告 |
| `/decision` | R4 决策、评分拆解、人工确认、override |
| `/audit` | import、agent run、phase write、human confirmation、hash/migration 状态 |

### 9.2 操作按钮分级

所有会写入项目包或调用模型的按钮必须分级：

| 级别 | 示例 | UI 行为 |
| --- | --- | --- |
| 只读 | 查看 workflow、查看报告 | 普通按钮 |
| Preview | R2 dry-run、R4 dry-run | 次级按钮，无确认 |
| Deterministic write | 写入 R2/R3/R4 deterministic 结果 | 必须勾选“已复核 preview” |
| Model run | 运行 R1 agent、运行 R1 serial | 必须显示 gateway health、成本/耗时、确认弹窗 |
| Final human action | 确认/驳回/override R4 | 必须填写理由或确认操作者 |

### 9.3 工作台首页信号

Deal overview 首页优先展示：

1. 当前下一步动作。
2. 阻断原因。
3. Evidence readiness。
4. Agent readiness。
5. R4 decision/human confirmation 状态。
6. 最近 5 条审计事件。

不要把 OpenClaw import 作为首页主叙事。OpenClaw 只在 source metadata 和 audit 中展示。

## 10. Job 和并发模型

V2 所有长任务都应走 job：

- OpenClaw import。
- Evidence build/index。
- Startup retrieval 批量生成。
- R1 single/serial model run。
- R2/R3/R4 model augmentation。
- Package migration/repair。

Job envelope 增加 Deal OS 字段：

```json
{
  "job_id": "...",
  "job_type": "deal.workflow.action",
  "deal_id": "DEAL-YUSHU-2026-001",
  "phase": "R1",
  "action": "run-r1-agent",
  "mode": "model",
  "profile_id": "siq_ic_finance_auditor",
  "status": "queued|running|succeeded|failed|cancelled",
  "lock_key": "deal:DEAL-YUSHU-2026-001:phase:R1",
  "artifact_paths": [],
  "audit_event_id": null
}
```

Phase lock 规则：

- 同一 deal 同一 phase 同一时间只允许一个写任务。
- read/preview 不加写锁。
- model run 失败不能推进 workflow。
- partial artifacts 必须写到 temp path，合同校验通过后再 commit。

## 11. Package Migration 和 Demo 交付

### 11.1 迁移任务

新增 `deal_package_migrator.py` 或扩展 importer：

```text
scripts/deals/repair_deal_package.py --deal-id DEAL-YUSHU-2026-001
```

修复内容：

- 补齐 `phases/r4_decision.json` 的 V2 必需字段。
- 生成 `decision/decision_payload.json`。
- 修复 manifest hash mismatch。
- 标记缺失 legacy 文件为 accepted missing 或 optional。
- 统一 `policy_version`。
- 补 `audit` 和 `phases/audit_log.json` source mismatch。

### 11.2 Demo 交付方式

当前 `data/wiki/` 被忽略，不能把本地 YUSHU 包当成可靠交付。V2 推荐二选一：

1. `scripts/deals/seed_yushu_demo.py`：从 OpenClaw 源或压缩 fixture 生成 demo。
2. `fixtures/deals/yushu_openclaw_minimal.tar.zst`：提交脱敏最小 fixture，测试和演示时解压导入。

不要把真实敏感 data room 原文提交到仓库。

## 12. 安全与权限

V2 默认安全策略：

- Deal package 私有，不通过静态文件服务暴露。
- 报告、decision、audit、manifest 走鉴权 API。
- data room 原文必须单独权限，不因能看报告而自动能下载原文。
- Hermes prompt 不带密钥、cookie、数据库连接串。
- `siq_ic_*` 默认不能访问 `data/wiki/companies`。
- Human override 必须写操作者、理由、时间、原始决策、覆盖后决策。
- Model run 输出被拒绝也必须写 audit rejected event，但不能写正式报告。

## 13. 实施路线

### P0.5：迁移后收敛

目标：把当前兼容层修成一个干净、可验收的 SIQ Deal OS baseline。

任务：

1. 增加 package repair/migration，修复 YUSHU demo R4 合同和 manifest/audit warning。
2. 确定 demo 交付方式：seed script 或脱敏 fixture。
3. 统一 policy version 口径，建议统一为 `2026-04-13-siq-port` 或新增 `source_policy_version`。
4. 补真实 Hermes smoke：sector、legal、risk、chairman、R1 serial real。
5. 明确 canonical API 和 legacy API，对文档做一次同步。
6. Web 上给 deterministic write 和 model run 做清晰区分。

验收：

- `DEAL-YUSHU-2026-001` 可由 seed/import 命令稳定生成。
- R4 原始 JSON 通过 V2 必需字段检查。
- API preflight 不再因 demo package 合同残留产生非预期 warning。
- 7 个 profile 的 health/smoke 状态有明确矩阵。

### P1：Evidence 和 Retrieval 产品化

目标：让 Agent 的发言建立在真实检索凭证上。

任务：

1. 建 `deal_os` PostgreSQL schema。
2. Evidence ingest 从 dry-run 升级为实写。
3. Milvus collection 和 alias 落地。
4. Startup receipt 使用 shared + private hybrid retrieval。
5. Agent report 强制 evidence_id 引用和 receipt hash 校验。
6. 前端 Evidence 页面展示 indexing/readiness。

验收：

- 任一 R1 agent 发言前都能看到 receipt 的 shared/private hits。
- 报告 evidence_id 能回跳到 source document/page/block。
- PostgreSQL/Milvus 失败不会破坏 package archive，但会阻断 model run。

### P2：Model Workflow 产品化

目标：从 R1 单 agent real run 扩展到完整模型投委会流程。

任务：

1. Phase Engine 抽象落地。
2. R1 serial real run 加 phase lock 和失败恢复。
3. R1.5 支持 chairman model ruling。
4. R2 支持 per-agent model revision。
5. R3 支持 risk/chairman red-blue model review。
6. R4 支持 chairman model report drafting，但评分和人工确认仍由服务层控制。

验收：

- 一键推进可以按 action 执行，不会跳过门禁。
- 任一阶段失败可重试，已成功 artifact 不被静默覆盖。
- R4 同时保留 agent weighted score、chairman dimension score、qualitative decision。

### P3：产品化和复盘

目标：让 Deal OS 从单项目工具变成一级市场投研工作台。

任务：

1. 项目模板：机器人、半导体、新能源、企业服务等。
2. 行业证据 checklist。
3. IC decision HTML 报告主题和下载。
4. 投后监控指标入口。
5. 决策复盘：预测、实际结果、偏差和角色改进。

## 14. Definition of Done

V2 baseline 完成标准：

- 新建或导入一个 deal 后，能完成 evidence readiness、startup retrieval、R1 serial、R1.5、R2、R3、R4、human confirmation。
- 所有 write/model-run action 都有 preview、blocking reasons、audit event 和 artifact paths。
- 所有正式 agent report 都有 valid startup receipt 和 evidence_id。
- R4 原始 JSON 满足 SIQ V2 contract，不依赖 legacy wrapper 才能展示。
- Web 明确区分 preview、deterministic write、model run 和 human final action。
- PostgreSQL/Milvus 实写可开启，dry-run 可保留。
- YUSHU demo 可通过 seed/import 命令稳定生成，mock E2E 和真实 API E2E 都通过。

## 15. 推荐立即开始的任务

1. 新增 package repair 脚本，修复当前 YUSHU demo 的 R4 JSON、manifest hash 和 audit mismatch。
2. 把 demo 交付方式定为 seed script 或脱敏 fixture，并在 README 写清楚。
3. 给 workflow action 增加 `mode=preview|deterministic|model`，先兼容现有 `dry_run`。
4. 补 4 个剩余 profile 的真实 Hermes smoke，再跑 R1 serial real smoke。
5. 设计并实现 `deal_os` PostgreSQL schema 的最小索引表。
6. 把 Evidence 页面从 list 升级为 readiness dashboard。
7. 增加真实 API Playwright E2E，不只用 mock API。

这条路线的核心不是推翻迁移成果，而是把迁移成果“扶正”：兼容层退到 adapter，SIQ Deal OS 成为主系统；deterministic 能力保留为工程兜底，模型能力通过 Phase Engine 和 Evidence Service 受控进入正式投委会流程。
