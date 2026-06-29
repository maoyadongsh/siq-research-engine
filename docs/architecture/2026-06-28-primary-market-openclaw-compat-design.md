# SIQ 一级市场投委会智能体设计方案

> 日期：2026-06-28
> 状态：设计方案
> 目标：在 SIQ Research Engine 中引入一级市场投研决策能力，第一阶段保真复刻 `/home/maoyd/.openclaw/workspace` 中的 OpenClaw 多智能体投委会系统。

## 1. 背景

SIQ Research Engine 当前主要面向二级市场研究：围绕上市公司官方披露、财报解析、财务事实、研究报告、事实核查、持续跟踪和法务合规建立可审计的研究生产线。

一级市场投研决策与二级市场研究共享“证据先行、模型受控、结论可追溯”的原则，但业务对象、材料形态和决策机制不同：

- 二级市场对象是上市公司、股票代码、官方披露文件和市场数据。
- 一级市场对象是投资项目、数据室材料、尽调报告、估值假设、条款建议和投委会裁决。
- 二级市场更强调公开信息、财报事实、持续覆盖和风险提示。
- 一级市场更强调项目准入、材料校验、专家尽调、分歧裁决、投资条款和审计链。

OpenClaw 中已经形成一套较完整的一级市场投委会多 Agent 原型：R0 信息校验、R1 专家尽调、R1.5 分歧识别、R2 观点完善、R3 红蓝对抗、R4 最终投决。SIQ 应优先复刻这套已验证的方法论，再在 SIQ 的前后端、文档解析、证据层和权限体系中产品化。

## 2. 设计目标

### 2.1 P0 目标

P0 阶段目标是建立 `OpenClaw IC Compatibility Layer`：

1. 在 SIQ 中新增一级市场 `Deal OS / 投委会` 业务域。
2. 复刻 OpenClaw 的 7 个一级市场投委会 Agent。
3. 复刻 R0-R4 工作流和关键数据合同。
4. 复用 SIQ 的通用文档解析、Wiki、PostgreSQL、Milvus、API 鉴权、Hermes 网关和 Web 工作台。
5. 形成可在前端操作、可审计、可归档的一级市场投决项目包。

### 2.2 非目标

P0 阶段暂不做：

- 不优化或重写 OpenClaw 的 Agent 角色分工。
- 不新增技术尽调、客户验证、投后管理等新 Agent。
- 不引入复杂团队权限、多租户隔离或外部 CRM。
- 不承诺完全自动投资决策。
- 不把一级市场结论混入二级市场的“买入/卖出/持有”研究语言。

## 3. 总体架构

新增一级市场业务域：

```text
数据室材料 / Teaser / BP / 财务模型 / 法务材料 / 访谈纪要 / URL
  -> apps/document-parser 通用解析
  -> deal evidence package
  -> R0 项目准入与证据门禁
  -> R1 专家尽调
  -> R1.5 分歧识别与主席裁决
  -> R2 观点完善
  -> R3 红蓝对抗
  -> R4 投决报告与审计归档
  -> Web 工作台展示与人工复核
```

推荐模块边界：

```text
apps/web
  -> /deals 一级市场工作台

apps/api
  -> /api/deals/* 项目、证据、工作流、报告、审计

apps/document-parser
  -> 数据室材料解析和 artifact 生成

agents/hermes/profiles/siq_ic_*
  -> OpenClaw IC Agent 复刻

data/wiki/deals/<deal_id>
  -> 一级市场项目事实源

db/ddl + db/imports
  -> 一级市场 PostgreSQL 索引和导入

scripts/vector-index/milvus-ingestion
  -> deal evidence chunks 入 Milvus
```

## 4. 业务对象模型

### 4.1 Deal Project

一级市场的核心对象是 `deal_project`，不是上市公司。

核心字段：

| 字段 | 说明 |
| --- | --- |
| `deal_id` | 项目唯一 ID，例如 `DEAL-YUSHU-2026-001` |
| `company_name` | 项目公司名称 |
| `industry` | 行业，例如机器人、半导体、新能源设备 |
| `stage` | 融资阶段，例如 Seed、Series A、Series B、Pre-IPO |
| `deal_type` | 股权投资、Pre-IPO、战略投资、并购少数股权等 |
| `status` | `draft`、`r0_ready`、`r1_in_progress`、`r4_completed` 等 |
| `created_by` | 创建用户 |
| `created_at` / `updated_at` | 时间戳 |
| `final_decision` | `pass`、`review`、`fail`、`manual_override` |
| `final_score` | 最终分数，P0 沿用 OpenClaw 0-100 |

### 4.2 Deal Document

一级市场材料包括：

- Teaser
- BP
- 财务模型
- 审计报告
- 业务合同
- 法务尽调材料
- 工商和股权结构材料
- 行业报告
- 访谈纪要
- 路演材料
- 投资条款清单
- 邮件或会议纪要

所有材料先进入 `data_room/`，再通过通用文档解析生成结构化 artifact。

### 4.3 Deal Evidence

Deal evidence 是面向投委会 Agent 的证据单元，必须区分：

| 类型 | 说明 |
| --- | --- |
| `verified` | 来自已核验材料、官方文件、签署文件或可信数据源 |
| `assumed` | 分析师或项目方假设 |
| `estimated` | 由公式、模型或推算得到 |
| `inferred` | 基于证据推论 |
| `unknown` | 当前缺失，不能补齐 |

## 5. 项目包目录合同

一级市场项目包采用文件系统优先，PostgreSQL 作为索引层。根目录：

```text
data/wiki/deals/<deal_id>/
```

推荐结构：

```text
data/wiki/deals/<deal_id>/
  project_meta.json
  manifest.json
  data_room/
    raw/
    metadata/
  parsed_documents/
    <document_task_id>/
      manifest.json
      document.md
      document_full.json
      blocks.json
      tables.json
      figures.json
      source_map.json
      quality_report.json
  evidence/
    evidence_index.json
    evidence_items.ndjson
    evidence_quality_report.json
  phases/
    workflow_state.json
    startup_receipts.json
    r1_reports.json
    r1_5_disputes.json
    r2_reports.json
    r3_reports.json
    r4_decision.json
    audit_log.json
  discussion/
    00_项目信息_R0.md
    01_R1_尽调汇总.md
    02_R1.5_裁决记录.md
    03_R2_观点完善汇总.md
    04_R3_红蓝对抗.md
    05_最终投决报告.md
  decision/
    IC_DECISION_REPORT.md
    IC_DECISION_REPORT.html
  audit/
    audit_log.json
    archive_manifest.json
```

说明：

- `project_meta.json` 是项目主元数据。
- `manifest.json` 是项目包清单，记录文档、证据、阶段产物和 artifact hash。
- `data_room/raw` 保存原始上传材料。
- `parsed_documents` 保存通用文档解析产物。
- `evidence` 保存 Agent 可消费的证据索引。
- `phases` 保存机器可读的工作流状态。
- `discussion` 保存人类可读的阶段纪要。
- `decision` 保存最终投决报告。
- `audit` 保存审计链和归档清单。

## 6. OpenClaw Agent 复刻方案

### 6.1 复刻角色

P0 保真复刻以下 7 个 Agent：

| OpenClaw Agent ID | SIQ Hermes Profile | 角色 |
| --- | --- | --- |
| `ic_master_coordinator` | `siq_ic_master_coordinator` | 投委会秘书 / 协调者 |
| `ic_chairman` | `siq_ic_chairman` | 投委会主席 |
| `ic_strategist` | `siq_ic_strategist` | 宏观战略专家 |
| `ic_sector_expert` | `siq_ic_sector_expert` | 行业专家 |
| `ic_finance_auditor` | `siq_ic_finance_auditor` | 财务专家 |
| `ic_legal_scanner` | `siq_ic_legal_scanner` | 法务专家 |
| `ic_risk_controller` | `siq_ic_risk_controller` | 风控专家 |

### 6.2 Profile 目录

```text
agents/hermes/profiles/
  siq_ic_master_coordinator/
    config.yaml
    SOUL.md
    AGENTS.md
    IDENTITY.md
    TOOLS.md
    README.md
  siq_ic_chairman/
  siq_ic_strategist/
  siq_ic_sector_expert/
  siq_ic_finance_auditor/
  siq_ic_legal_scanner/
  siq_ic_risk_controller/
  shared/
    ic_workflow_policy.json
    ic_report_contract.md
    ic_evidence_contract.md
```

### 6.3 兼容原则

P0 阶段必须保留：

- OpenClaw 角色名称和职责边界。
- R1 发言顺序：战略、行业、财务、法务、风控、主席。
- 权重：chairman 30%，strategy / sector / finance / risk 各 15%，legal 10%。
- 阈值：`>=70 pass`，`68-69 review`，`<68 fail`。
- 专家报告中的 `verified / assumed / open_questions`。
- 分歧识别与主席裁决。
- 审计日志。

P0 阶段允许调整：

- 文件路径从 OpenClaw 路径迁移到 SIQ `data/wiki/deals`。
- Milvus collection 名称增加 SIQ 前缀。
- 工具调用由 OpenClaw runtime 适配为 SIQ API / Hermes tool。
- 前端展示和状态管理按 SIQ 产品形态实现。

## 7. R0-R4 工作流

### 7.1 阶段定义

| 阶段 | 名称 | 目标 | 主要产物 |
| --- | --- | --- | --- |
| R0 | 项目准入与信息校验 | 创建项目、导入材料、建立证据包、检查门禁 | `project_meta.json`、`evidence_index.json`、`00_项目信息_R0.md` |
| R1 | 专家尽调 | 6 位专家按顺序输出独立观点 | `r1_reports.json`、`01_R1_尽调汇总.md` |
| R1.5 | 分歧识别与主席裁决 | 显性化关键分歧和证据缺口 | `r1_5_disputes.json`、`02_R1.5_裁决记录.md` |
| R2 | 观点完善 | 专家根据裁决补充和修订 | `r2_reports.json`、`03_R2_观点完善汇总.md` |
| R3 | 红蓝对抗 | 挑战核心假设，可执行或跳过但必须留痕 | `r3_reports.json`、`04_R3_红蓝对抗.md` |
| R4 | 投决归档 | 生成最终报告、评分、结论、审计材料 | `r4_decision.json`、`IC_DECISION_REPORT.md` |

### 7.2 工作流状态

`phases/workflow_state.json`：

```json
{
  "schema_version": "siq_deal_workflow_state_v1",
  "deal_id": "DEAL-YUSHU-2026-001",
  "company_name": "杭州宇树科技股份有限公司",
  "industry": "机器人",
  "stage": "Pre-IPO",
  "status": "r1_in_progress",
  "current_phase": "R1",
  "phases": {
    "R0": {
      "status": "completed",
      "started_at": "2026-06-28T10:00:00+08:00",
      "completed_at": "2026-06-28T10:15:00+08:00",
      "evidence_gate": "passed"
    },
    "R1": {
      "status": "in_progress",
      "active_agent": "ic_sector_expert",
      "submitted_agents": ["ic_strategist"]
    },
    "R1.5": {"status": "pending"},
    "R2": {"status": "pending"},
    "R3": {"status": "pending"},
    "R4": {"status": "pending"}
  },
  "updated_at": "2026-06-28T10:30:00+08:00"
}
```

### 7.3 证据门禁

P0 沿用 OpenClaw 思想：

```json
{
  "required_verified_items": 3,
  "required_dimensions": ["business", "finance", "legal", "risk"],
  "max_unresolved_disputes": 0,
  "min_expert_reports": 5,
  "required_report_fields": ["score", "recommendation"],
  "required_report_metadata": ["verified", "assumed", "open_questions"]
}
```

SIQ 中应将门禁失败明确写入：

- `evidence/evidence_quality_report.json`
- `phases/audit_log.json`
- 前端 workflow 页面

## 8. 后端设计

### 8.1 新增文件

```text
apps/api/routers/deals.py
apps/api/services/deal_store.py
apps/api/services/deal_documents.py
apps/api/services/deal_evidence.py
apps/api/services/ic_workflow.py
apps/api/services/ic_agent_runtime.py
apps/api/services/ic_audit.py
apps/api/services/ic_report_builder.py
```

### 8.2 模块职责

| 模块 | 职责 |
| --- | --- |
| `deals.py` | 暴露 `/api/deals/*` 路由 |
| `deal_store.py` | 创建、读取、更新项目和项目包路径 |
| `deal_documents.py` | 数据室上传、文档解析任务绑定 |
| `deal_evidence.py` | 从文档 artifact 构建 evidence index |
| `ic_workflow.py` | R0-R4 状态机、门禁、阶段推进 |
| `ic_agent_runtime.py` | 调用 Hermes profiles 执行专家任务 |
| `ic_audit.py` | 写入审计事件 |
| `ic_report_builder.py` | 汇总最终投决报告 |

### 8.3 核心 API

项目管理：

```text
GET    /api/deals
POST   /api/deals
GET    /api/deals/{deal_id}
PATCH  /api/deals/{deal_id}
DELETE /api/deals/{deal_id}
```

数据室：

```text
POST   /api/deals/{deal_id}/documents
GET    /api/deals/{deal_id}/documents
GET    /api/deals/{deal_id}/documents/{document_id}
DELETE /api/deals/{deal_id}/documents/{document_id}
POST   /api/deals/{deal_id}/documents/{document_id}/parse
```

证据包：

```text
POST   /api/deals/{deal_id}/evidence/build
GET    /api/deals/{deal_id}/evidence
GET    /api/deals/{deal_id}/evidence/{evidence_id}
GET    /api/deals/{deal_id}/evidence/quality
```

工作流：

```text
GET    /api/deals/{deal_id}/workflow
POST   /api/deals/{deal_id}/workflow/start-r0
POST   /api/deals/{deal_id}/workflow/start-r1
POST   /api/deals/{deal_id}/workflow/run-r1-agent
POST   /api/deals/{deal_id}/workflow/identify-disputes
POST   /api/deals/{deal_id}/workflow/run-r2
POST   /api/deals/{deal_id}/workflow/run-r3
POST   /api/deals/{deal_id}/workflow/finalize-r4
```

报告和审计：

```text
GET    /api/deals/{deal_id}/reports
GET    /api/deals/{deal_id}/reports/{report_name}
GET    /api/deals/{deal_id}/decision
GET    /api/deals/{deal_id}/audit
GET    /api/deals/{deal_id}/manifest
```

### 8.4 后台 Job

以下任务应走后台 job：

- 批量文档解析。
- 构建 evidence package。
- 运行某个 IC Agent。
- 识别分歧。
- 生成最终报告。
- 入库 PostgreSQL / Milvus。

任务状态可复用现有 `/api/jobs/*` 模式，或者新增：

```text
GET /api/deals/{deal_id}/jobs
GET /api/deals/{deal_id}/jobs/{job_id}
```

## 9. 前端设计

### 9.1 路由

新增一级市场工作台：

```text
/deals
/deals/new
/deals/:dealId
/deals/:dealId/data-room
/deals/:dealId/evidence
/deals/:dealId/workflow
/deals/:dealId/agents
/deals/:dealId/disputes
/deals/:dealId/decision
/deals/:dealId/audit
```

### 9.2 页面职责

| 页面 | 说明 |
| --- | --- |
| `/deals` | 项目列表、状态、行业、阶段、最终结论 |
| `/deals/new` | 新建项目，填写公司、行业、阶段、项目来源 |
| `/deals/:dealId` | 项目总览，展示当前阶段、证据覆盖、关键风险 |
| `/data-room` | 上传和管理数据室材料 |
| `/evidence` | 查看 evidence index、质量门禁、证据来源 |
| `/workflow` | R0-R4 时间线、阶段按钮、运行日志 |
| `/agents` | 专家报告卡片、检索摘要、立场和置信度 |
| `/disputes` | 分歧矩阵、主席裁决、补充尽调要求 |
| `/decision` | 最终投决报告、评分、结论、条款建议 |
| `/audit` | 审计事件、人工 override、文件 hash 和阶段记录 |

### 9.3 工作台布局

项目总览页建议三栏：

```text
左栏：项目基本信息、阶段、操作按钮
中栏：R0-R4 时间线、专家状态、分歧卡片
右栏：证据覆盖率、缺口、最新审计事件、最终结论
```

### 9.4 R0-R4 交互

| 阶段 | 主要控件 |
| --- | --- |
| R0 | 项目信息表单、数据室上传、构建证据包、门禁检查 |
| R1 | 6 个专家卡片，按顺序运行，查看报告 |
| R1.5 | 分歧识别按钮、分歧列表、主席裁决入口 |
| R2 | 专家修订报告、补充证据、重新提交 |
| R3 | 红蓝对抗执行 / 跳过，跳过必须写理由 |
| R4 | 生成投决报告、人工确认、归档 |

### 9.5 前端文件建议

```text
apps/web/src/pages/Deals.tsx
apps/web/src/pages/DealWorkspace.tsx
apps/web/src/pages/DealDataRoom.tsx
apps/web/src/pages/DealEvidence.tsx
apps/web/src/pages/DealWorkflow.tsx
apps/web/src/pages/DealAgents.tsx
apps/web/src/pages/DealDecision.tsx
apps/web/src/pages/DealAudit.tsx

apps/web/src/components/deals/
  DealProjectCard.tsx
  DealStageTimeline.tsx
  DealEvidencePanel.tsx
  DealAgentReportCard.tsx
  DealDisputeMatrix.tsx
  DealAuditTimeline.tsx

apps/web/src/lib/dealApi.ts
apps/web/src/lib/dealTypes.ts
```

## 10. PostgreSQL 设计

P0 可文件系统优先，P1 引入 PostgreSQL 索引。推荐 schema：

```sql
CREATE SCHEMA IF NOT EXISTS deal_os;
```

核心表：

| 表 | 说明 |
| --- | --- |
| `deal_os.projects` | 项目主表 |
| `deal_os.documents` | 数据室文档 |
| `deal_os.evidence_items` | 证据索引 |
| `deal_os.workflow_runs` | 工作流运行记录 |
| `deal_os.agent_reports` | 专家报告 |
| `deal_os.disputes` | 分歧记录 |
| `deal_os.decisions` | 最终决策 |
| `deal_os.audit_events` | 审计事件 |

原则：

- PostgreSQL 保存索引、状态、证据定位和结构化查询字段。
- 原文、报告、完整 JSON 仍以 Wiki 项目包为权威归档。
- 所有表必须带 `deal_id` 和 `artifact_path`，可回跳到文件层。

## 11. Milvus 设计

P0 推荐新增 collection：

```text
siq_deal_shared
siq_ic_strategist
siq_ic_sector_expert
siq_ic_finance_auditor
siq_ic_legal_scanner
siq_ic_risk_controller
siq_ic_chairman
```

也可以为了 OpenClaw 兼容暂时保留旧名称：

```text
ic_collaboration_shared
ic_strategist
ic_sector_expert
ic_finance_auditor
ic_legal_scanner
ic_risk_controller
ic_chairman
```

推荐折中：

- SIQ 内部默认使用 `siq_*`。
- 迁移 OpenClaw 项目时提供 collection alias 或配置映射。

Milvus metadata 最小字段：

| 字段 | 说明 |
| --- | --- |
| `schema_version` | `siq_deal_chunk_v1` |
| `deal_id` | 项目 ID |
| `document_id` | 文档 ID |
| `evidence_id` | 证据 ID |
| `source_path` | 项目包内路径 |
| `source_type` | `bp`、`teaser`、`financial_model` 等 |
| `confidence` | `verified`、`assumed`、`estimated` |
| `role_hint` | 推荐使用该证据的 Agent |
| `citation` | 人类可读引用 |

## 12. Agent 工具适配

OpenClaw 工具语义到 SIQ 的映射：

| OpenClaw 概念 | SIQ 实现 |
| --- | --- |
| `shared/projects` | `data/wiki/deals` |
| `ic_collaboration_shared` | `siq_deal_shared` |
| 私有 collection | `siq_ic_<role>` |
| `agent_startup_retrieval` | `ic_agent_runtime.startup_retrieval()` |
| `unified_hybrid_retriever.py` | SIQ Milvus 检索服务或现有 vector-index 工具 |
| `sessions_send` | Hermes Runs API / SIQ 后台 job |
| `siq_workflow_policy.json` | `agents/hermes/profiles/shared/ic_workflow_policy.json` |
| `audit_log.json` | `ic_audit.py` 统一写入 |

P0 可以先做轻量工具：

```text
POST /api/deals/{deal_id}/agents/{agent_id}/startup-retrieval
POST /api/deals/{deal_id}/agents/{agent_id}/run
```

## 13. 安全与权限

一级市场材料通常包含更高敏感度，必须默认私有：

- 数据室原文不公开。
- 项目包默认只对创建者、管理员和授权用户可见。
- Agent 输出、审计链、最终投决报告都走 API 鉴权。
- 文档下载和预览必须经过签名访问或 API 代理。
- 不把真实密钥、会话、外部系统 token 写入项目包。
- 人工 override 必须记录操作者、时间、原因。

## 14. 与现有二级市场模块的边界

不能混用的部分：

- 不把一级市场项目写入 `data/wiki/companies`。
- 不把一级市场财务模型强行导入 `pdf2md`、`sec_us`、`pdf2md_hk` 等二级市场 schema。
- 不把一级市场结论展示为二级市场交易评级。
- 不让二级市场 Agent 默认访问一级市场私密数据。

可以复用的部分：

- `apps/document-parser`
- `apps/api` 鉴权、文件代理、后台 job
- Hermes Runs API 代理
- Wiki / PostgreSQL / Milvus 基础设施
- 报告阅读器
- 系统状态和设置页面

## 15. 实施计划

### P0：OpenClaw 保真复刻

目标：跑通一个完整一级市场项目。

任务：

1. 新增设计文档和 deal package 合同。
2. 新增 `/api/deals` 项目管理。
3. 新增 `data/wiki/deals` 项目包读写。
4. 新增数据室上传和文档解析绑定。
5. 新增 R0-R4 workflow state。
6. 迁移 7 个 OpenClaw Agent 为 Hermes profiles。
7. 新增前端 `/deals`、`/deals/:id/workflow`、`/deals/:id/decision`。
8. 支持生成 `IC_DECISION_REPORT.md` 和 `audit_log.json`。

验收：

- 可创建一级市场项目。
- 可上传并解析数据室材料。
- 可构建 evidence index。
- 可运行 R0-R4 状态流。
- 可看到 7 个 Agent 的报告或占位状态。
- 可输出最终投决报告和审计链。

### P1：证据层和检索增强

任务：

1. Deal evidence 入 PostgreSQL。
2. Deal chunks 入 Milvus。
3. 实现 startup retrieval。
4. Agent 报告强制附带检索摘要。
5. 前端展示证据覆盖率和缺口。

验收：

- 每个 Agent 可以检索 deal shared evidence。
- 专家报告能引用 evidence_id。
- Milvus 命中能回跳到文档 source map。

### P2：工作流自动化增强

任务：

1. R1 串行调度自动化。
2. R1.5 自动分歧识别。
3. R2 自动发起修订任务。
4. R3 动态执行或跳过。
5. R4 自动生成投决报告。

验收：

- 用户可以一键推进阶段。
- 每个阶段失败能恢复或重试。
- 审计链完整记录自动和人工操作。

### P3：一级市场产品化

任务：

1. 导入 OpenClaw golden path 项目作为 demo。
2. 增加项目模板。
3. 增加投委会报告 HTML 渲染。
4. 增加投后监控和复盘入口。
5. 增加角色优化和行业模板。

## 16. 风险与应对

| 风险 | 说明 | 应对 |
| --- | --- | --- |
| 角色迁移失真 | 过早优化导致 OpenClaw 方法论被破坏 | P0 冻结角色和权重 |
| 路径混乱 | OpenClaw 和 SIQ 项目根不一致 | 统一使用 `data/wiki/deals` |
| 敏感数据泄露 | 一级市场材料私密性高 | 默认鉴权、禁止写入日志和 README |
| Agent 幻觉 | 未检索证据直接输出观点 | 强制 startup retrieval 和 verified/assumed |
| 工作流过重 | 一次性自动化复杂度高 | 先人工推进阶段，再逐步自动化 |
| 二级市场污染 | 一级市场数据进入公司研究链路 | schema、Wiki namespace、前端路由隔离 |

## 17. 推荐下一步

建议按以下顺序进入实现：

1. 新增 `data/wiki/deals` 和 deal package helper。
2. 在 `apps/api` 新增最小 `/api/deals`。
3. 在 `apps/web` 新增 `/deals` 和项目详情页。
4. 迁移 `siq_workflow_policy.json` 到 SIQ 共享 profile。
5. 迁移 7 个 OpenClaw Agent profile。
6. 选择 OpenClaw 的 golden path 项目做导入验证。

这条路径的核心是先把 OpenClaw 的投委会制度完整接入 SIQ，再利用 SIQ 的工程底座逐步产品化。第一阶段的成功标准不是“更聪明”，而是“同样的投委会流程在 SIQ 中稳定、可见、可审计地运行”。
