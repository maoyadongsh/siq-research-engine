# 一级市场 IC 智能体职责发挥增强开发方案

> 日期：2026-07-06
> 适用仓库：`/home/maoyd/siq-research-engine`
> 状态：可落地开发方案
> 目标窗口：一级市场模块、IC Hermes 智能体、投研会议室、Deal OS workflow
> 核心约束：不破坏现有交易工作台、不影响二级市场智能体路由、不直接让前端调用 Hermes 或 Milvus。

## 1. 背景与目标

当前一级市场已经具备以下基础能力：

- 前端新增了一级市场工作平台、材料中心、投研会议室。
- 投研会议室已支持选择 7 个 IC Hermes 智能体单聊、全体委员发言、总协调员工作流窗口。
- 一级市场聊天路由已和二级市场智能体路由分离：
  - 一级市场：`/api/primary-market/meeting/{siq_ic_*}/chat`
  - 二级市场：仍保留原有问答助手与二级市场 agent 路由。
- 后端已有 Deal OS 正式工作流能力：
  - `ic_startup_retrieval.py`
  - `ic_agent_runtime.py`
  - `ic_decision_report.py`
  - `ic_policy.py`
  - `/api/deals/{deal_id}/workflow/*`
- 7 个 Hermes IC profile 已位于：
  - `agents/hermes/profiles/siq_ic_master_coordinator`
  - `agents/hermes/profiles/siq_ic_chairman`
  - `agents/hermes/profiles/siq_ic_strategist`
  - `agents/hermes/profiles/siq_ic_sector_expert`
  - `agents/hermes/profiles/siq_ic_finance_auditor`
  - `agents/hermes/profiles/siq_ic_legal_scanner`
  - `agents/hermes/profiles/siq_ic_risk_controller`
- 运行时 profile 目录和 7 个 IC Hermes gateway 当前可用。

本方案目标是把 7 个 IC 智能体从“能聊天”升级为“能按职责参与投委会流程”：

1. 每个智能体回答前知道自己的职责边界。
2. 每个智能体正式发言前具备 startup-retrieval receipt。
3. 每个智能体输出后能沉淀结构化产物。
4. 总协调员能驱动 R0-R4，而不是只做普通聊天。
5. 系统能自动检查越权、缺证、缺产物和投决门禁。

## 2. 当前状态评估

### 2.1 已完成能力

| 能力 | 当前状态 |
| --- | --- |
| 一级市场路由隔离 | 已完成，一级市场路由只允许 `siq_ic_*` |
| 7 个 IC profile Hermes port | 已配置，默认端口 `18660-18666` |
| 聊天窗口选择智能体 | 已完成 |
| 新建/历史/删除会话 | 已复刻问答助手能力 |
| 附件上传 | 已复用聊天附件能力 |
| 默认问题 | 已按角色职责预设，点击后走模型实时回答 |
| 后端职责护栏 | 已在 `primary_market_meeting.py` 做第一版 profile-scoped message 注入 |
| startup retrieval | 已有 `/api/deals/{deal_id}/agents/{profile_id}/startup-retrieval` |
| R1 agent task | 已有 `/api/deals/{deal_id}/workflow/run-r1-agent` |
| R1 serial | 已有 `/api/deals/{deal_id}/workflow/run-r1-serial` |
| R2/R3/R4 workflow | 已有 deterministic workflow endpoint |

### 2.2 主要差距

| 差距 | 影响 |
| --- | --- |
| 职责护栏第一版仍是路由内精简硬编码 | profile 文件更新后可能漂移 |
| 普通聊天尚未自动读取 receipt 摘要 | 智能体容易只基于页面摘要回答，证据深度不足 |
| “点名对话”和“正式投研任务”边界不够清晰 | 用户可能把聊天结果误当正式 R1 产物 |
| 前端没有展示每个 agent 的 receipt/readiness/report 状态 | 用户不知道某智能体是否已经完成检索和正式报告 |
| 总协调员工作流按钮目前偏聊天 | 还没有把 `/api/deals/{deal_id}/workflow/advance-next` 做成会议室的一等操作 |
| 缺少回答质量评估 | 无法自动判断越权、缺证、缺 JSON 摘要、缺下一步 |
| 产物视图不足 | 用户只能看聊天，不易看到 R0-R4 产物闭环 |

## 3. 目标架构

### 3.1 分层原则

```text
前端会议室
  只负责选择项目、选择窗口、展示聊天、展示 receipt/report/workflow 状态、触发后端动作

一级市场会议路由 /api/primary-market/meeting/*
  负责 IC-only 路由隔离、会话、附件、聊天、职责护栏、会议纪要和 UI 聚合接口

Deal OS 正式工作流 /api/deals/*
  负责 startup retrieval、R1/R2/R3/R4 正式产物、preflight、decision、audit

Hermes profiles
  负责模型能力和专业身份；每个 profile 读取自身 AGENTS/IDENTITY/SOUL/USER

Wiki / Deal Package
  权威归档与事实源

Milvus / Postgres
  召回与结构化索引，不作为唯一事实源
```

### 3.2 三类交互模式

| 模式 | 用途 | 是否写正式产物 | 调用路径 |
| --- | --- | --- | --- |
| 单聊 | 人类向某个智能体咨询 | 默认不写正式 R1 产物，只写聊天历史和会议纪要 | `/api/primary-market/meeting/{profile}/chat/stream` |
| 全体委员发言 | 多个 profile 在一个窗口顺序回答同一问题 | 默认不写正式 R1 产物，可写会议纪要 | 前端顺序调用 meeting chat |
| 正式 workflow | 触发 R1/R2/R3/R4 产物生成 | 写入 `data/wiki/deals/{deal_id}` | `/api/deals/{deal_id}/workflow/*` |

### 3.3 关键原则

1. 聊天结果是讨论记录，不等于正式投决产物。
2. 正式 R1/R2/R3/R4 必须走 `ic_agent_runtime` 和 workflow endpoint。
3. 每个 agent 的正式发言必须有 startup retrieval receipt。
4. 普通聊天可以允许无 receipt，但必须显示“缺 receipt，当前为临时咨询”。
5. 总协调员负责建议流程动作，但实际写入动作必须由后端 workflow endpoint 完成。

## 4. 开发路线图

## P0：职责契约与 readiness 聚合

### P0.1 新增 `ic_profile_contract` 服务

新增文件：

```text
apps/api/services/ic_profile_contract.py
```

职责：

- 从 `agents/hermes/profiles/siq_ic_shared/ic_profile_matrix.json` 读取 profile 基础信息。
- 从每个 profile 的 `IDENTITY.md`、`AGENTS.md`、`SOUL.md`、`USER.md` 提取或拼装职责契约。
- 对外输出统一结构：

```python
class IcProfileContract(TypedDict):
    profile_id: str
    label: str
    role: str
    responsibilities: list[str]
    focus: str
    outputs: list[str]
    boundaries: list[str]
    source_files: list[str]
    startup_retrieval_required: bool
    r1_sequence_index: int | None
```

第一版可以不做复杂 NLP 提取，直接采用：

- `ic_profile_matrix.json` 的 `responsibilities`
- profile 文件存在性和路径
- `ic_policy.list_ic_profiles()` 的 `startup_retrieval_required`、`r1_sequence_index`
- 对 `AGENTS.md` 中 `红线`、`禁止事项` 用简单 heading 提取，失败则使用 matrix fallback。

后续迭代再引入更强的 profile 摘要生成。

### P0.2 替换路由内硬编码职责护栏

当前临时护栏位于：

```text
apps/api/routers/primary_market_meeting.py
```

需要改为：

```python
from services import ic_profile_contract

contract = ic_profile_contract.get_ic_profile_contract(profile)
guard = ic_profile_contract.render_meeting_role_guard(contract)
```

保留 `_profile_scoped_meeting_message()`，但让它调用 service。

验收标准：

- `primary_market_meeting.py` 不再维护大段 profile 职责映射。
- profile 更新后，职责来源能随 profile/matrix 变化。
- 所有 meeting chat 调用仍注入职责护栏。

### P0.3 新增会议室 agent readiness 聚合接口

新增 endpoint：

```text
GET /api/primary-market/meeting/{deal_id}/agents/readiness
```

返回：

```json
{
  "deal_id": "DEAL-...",
  "profiles": [
    {
      "profile_id": "siq_ic_finance_auditor",
      "label": "财务审计委员",
      "role": "finance",
      "runtime": {
        "health": "running",
        "port": 18664
      },
      "contract": {
        "responsibilities": ["financial consistency", "..."],
        "source_files": ["IDENTITY.md", "AGENTS.md", "SOUL.md", "USER.md"]
      },
      "startup_receipt": {
        "present": true,
        "receipt_id": "startup-siq_ic_finance_auditor-R1-001",
        "shared_hits": 6,
        "private_hits": 0,
        "gaps": []
      },
      "r1_report": {
        "present": true,
        "score": 82,
        "recommendation": "support",
        "artifact_path": "discussion/01_R1_finance_auditor_report.md"
      },
      "quality": {
        "ready_for_formal_task": true,
        "blocking_reasons": [],
        "warnings": []
      }
    }
  ]
}
```

实现复用：

- `ic_policy.list_ic_profiles(include_runtime=True)`
- `ic_startup_retrieval.read_startup_retrieval_receipt()`
- `deal_reports.list_r1_agent_reports()`
- `ic_agent_runtime.build_r1_agent_readiness()`
- `system_status` 中已有 Hermes health 逻辑可参考，避免重复太多网络探测。

### P0.4 前端展示 readiness

修改：

```text
apps/web/src/pages/PrimaryMarketMeeting.tsx
apps/web/src/features/primary-market/primaryMarketApi.ts
apps/web/src/features/primary-market/primaryMarketViewModel.ts
```

新增：

- `fetchPrimaryMarketMeetingAgentReadiness(dealId)`
- 智能体下拉旁展示：
  - Hermes running / disabled
  - Receipt present / missing
  - R1 report present / missing
  - role contract source ok / missing
- 空状态智能体简介下方可展示一行小字：
  - `Receipt: present · R1 report: missing · Profile: AGENTS/IDENTITY/SOUL loaded`

不要让 UI 变复杂。主聊天仍保持简洁。

### P0.5 P0 测试

后端：

```text
apps/api/tests/test_ic_profile_contract.py
apps/api/tests/test_primary_market_meeting_router.py
```

覆盖：

- 7 个 profile 都能生成 contract。
- contract source files 存在。
- role guard 包含 profile_id、职责、边界。
- meeting chat 实际传给 runtime 的 message 含 guard。
- readiness endpoint 在 receipt/report missing 时返回 warning，而不是 500。

前端：

```text
apps/web/src/features/primary-market/primaryMarketApi.test.ts
apps/web/src/features/primary-market/primaryMarketViewModel.test.ts
```

覆盖：

- readiness response normalize。
- missing receipt / present receipt 显示状态。

## P1：聊天前证据上下文增强

P1 的目标是让“普通聊天”也尽量基于项目证据和对应 agent receipt，而不是只依赖页面摘要。

### P1.1 聊天前自动读取 receipt 摘要

在 `primary_market_meeting.py` 的 `primary_market_runtime_context()` 或新 service 中加入：

```python
def build_meeting_evidence_context(deal_id: str, profile: str) -> str:
    receipt = try_read_startup_receipt(deal_id, profile)
    r1_report = try_read_r1_report(deal_id, profile)
    evidence_quality = try_read_evidence_quality(deal_id)
    return compact_context
```

注入到 `ChatContext.page.title` 或直接注入 `_profile_scoped_meeting_message()` 的正文。

上下文控制在 4-8KB：

- receipt id
- shared/private hits
- Top 5 evidence ids
- gaps
- known R1 report score/recommendation
- evidence coverage

### P1.2 缺 receipt 时的策略

普通单聊：

- 不阻断。
- 注入提示：
  - `当前缺少 startup-retrieval receipt，本轮回答只能作为临时咨询，不得视为正式 R1 观点。`
- 前端展示 warning chip。

正式 workflow：

- 保持现有 `ic_agent_runtime` 的 preflight/gate。
- 缺 receipt 则阻断正式 R1 agent run。

### P1.3 一键准备智能体

新增 meeting wrapper endpoint：

```text
POST /api/primary-market/meeting/{deal_id}/agents/{profile_id}/prepare
```

请求：

```json
{
  "round_name": "R1",
  "limit": 10,
  "include_vector": true,
  "include_rerank": false,
  "include_external": false
}
```

内部调用现有：

```text
POST /api/deals/{deal_id}/agents/{profile_id}/startup-retrieval
```

为什么需要 wrapper：

- meeting 页面只关心“准备这个智能体”，不用暴露 Deal OS 细节。
- 可以统一刷新 transcript/audit/readiness。
- 可以未来扩展为“准备全体委员”。

### P1.4 前端交互

在会议室 header 或 agent 状态旁增加轻量动作：

- `准备智能体`
- `准备全体委员`
- `查看 Receipt`

限制：

- 不放大 UI 面积。
- Receipt 详情用折叠弹层或 history panel。
- 默认仍让用户能直接聊天。

### P1.5 P1 测试

后端：

- prepare endpoint 调用 `ic_startup_retrieval.generate_startup_retrieval_receipt()`。
- 缺 deal 返回 404。
- master coordinator prepare 返回说明“不需要 startup retrieval”或跳过。
- chat context 在 receipt present 时注入 receipt id。
- chat context 在 receipt missing 时注入 warning。

前端：

- 点击准备智能体后刷新 readiness。
- 准备失败展示 toast。

## P2：正式任务与聊天分流

P2 的目标是明确“问一下”和“执行正式投研任务”的区别。

### P2.1 前端增加任务执行入口

在投研会议室保留三个模式按钮：

- `点名对话`
- `全体委员`
- `总协调员工作流`

新增动作区：

```text
执行正式任务
  - 预演任务 dry-run
  - 执行当前智能体 R1
  - 执行 R1 串行
  - 推进 Workflow
```

对应现有 API：

| 前端动作 | 后端接口 |
| --- | --- |
| 预演当前智能体 R1 | `POST /api/deals/{deal_id}/workflow/run-r1-agent {dry_run:true}` |
| 执行当前智能体 R1 | `POST /api/deals/{deal_id}/workflow/run-r1-agent {dry_run:false}` |
| 执行 R1 串行 | `POST /api/deals/{deal_id}/workflow/run-r1-serial` |
| 推进 Workflow | `POST /api/deals/{deal_id}/workflow/advance-next` |
| R2 | `POST /api/deals/{deal_id}/workflow/run-r2` |
| R3 | `POST /api/deals/{deal_id}/workflow/run-r3` |
| R4 | `POST /api/deals/{deal_id}/workflow/finalize-r4` |

### P2.2 任务结果回写会议纪要

当正式 workflow endpoint 完成后，前端或后端追加 meeting transcript event：

```json
{
  "event_type": "artifact_written",
  "speaker": "总协调员",
  "title": "R1 财务审计委员报告已生成",
  "body": "artifact_path: discussion/01_R1_finance_auditor_report.md",
  "agent_id": "siq_ic_finance_auditor",
  "phase": "R1"
}
```

建议后端 wrapper 统一做，减少前端重复逻辑。

### P2.3 正式产物卡片

在聊天窗口下方或右侧增加“产物视图”，不要堆聊天卡：

```text
正式产物
  R0 信息校验: pass/warn/fail
  Startup Receipts: 6/6
  R1 Reports: 4/6
  R1.5 Disputes: 2 unresolved
  R2 Reports: missing
  R3 Review: skipped/completed
  R4 Decision: pending/completed
```

数据复用：

- `fetchPrimaryMarketWorkflow`
- `fetchPrimaryMarketPhaseArtifacts`
- `fetchPrimaryMarketAgents`
- `fetchPrimaryMarketDecision`
- `fetchPrimaryMarketAudit`

## P3：质量门禁与越权检测

P3 的目标是让系统检查“智能体是否按职责回答”。

### P3.1 新增 `ic_agent_output_quality.py`

新增服务：

```text
apps/api/services/ic_agent_output_quality.py
```

输入：

```python
evaluate_ic_agent_reply(
    profile_id: str,
    message: str,
    reply: str,
    context: dict,
) -> dict
```

输出：

```json
{
  "profile_id": "siq_ic_finance_auditor",
  "status": "pass|warn|fail",
  "checks": [
    {
      "id": "role.boundary",
      "status": "pass",
      "detail": "未发现明显越权"
    },
    {
      "id": "evidence.reference",
      "status": "warn",
      "detail": "未引用 startup receipt 或 evidence id"
    },
    {
      "id": "verified_assumed",
      "status": "warn",
      "detail": "未区分 verified/assumed"
    },
    {
      "id": "next_action",
      "status": "pass",
      "detail": "包含下一步建议"
    }
  ]
}
```

第一版规则用 deterministic text checks：

- 是否出现该 profile 禁止替代的领域词过多。
- 是否出现 evidence id / receipt id / artifact path。
- 是否出现 verified / assumed / 待核验。
- 是否包含下一步、补证、建议、条件等行动词。

P4 再考虑用模型做 judge。

### P3.2 质量结果存档

路径：

```text
data/wiki/deals/{deal_id}/discussion/meeting_quality.json
```

结构：

```json
{
  "schema_version": "siq_primary_market_meeting_quality_v1",
  "events": [
    {
      "event_id": "meeting-...",
      "profile_id": "siq_ic_finance_auditor",
      "lane": "agent-siq_ic_finance_auditor",
      "quality": {...},
      "created_at": "..."
    }
  ]
}
```

### P3.3 前端展示

聊天消息头部可以展示小型质量 chip：

- `role ok`
- `needs evidence`
- `boundary warning`

避免在主 UI 大面积铺开。

## P4：总协调员自动主持

P4 的目标是把“总协调员工作流”从聊天建议升级为可执行 conductor。

### P4.1 新增 meeting workflow run wrapper

新增 endpoint：

```text
POST /api/primary-market/meeting/{deal_id}/workflow/advance
```

请求：

```json
{
  "dry_run": false,
  "allow_hermes": true,
  "max_agents": 1,
  "r3_skip": true,
  "r4_overwrite": false
}
```

内部调用：

```text
ic_agent_runtime.run_workflow_advance_next()
```

并生成 meeting transcript event：

- 预演结果
- 实际执行步骤
- 写入产物路径
- 阻断原因
- 下一步建议

### P4.2 前端 workflow panel

把“总协调员工作流”模式的按钮改为更明确：

- `预演下一步`
- `执行下一步`
- `执行 R1 串行`
- `生成 R4 草案`

每次执行后：

- 刷新 workflow/readiness/phase artifacts。
- 在聊天窗口插入一条系统事件。
- 若产生 artifact，展示 artifact link。

### P4.3 人工确认门禁

在以下动作前必须显示确认：

- R1 串行执行全体委员。
- R4 finalize。
- 覆盖已有 R4。
- 跳过 R3。

请求参数保留：

```json
{
  "human_confirmed": true,
  "confirmation_note": "..."
}
```

第一版可以只在前端确认，P5 再把 human confirmation 写入后端强校验。

## P5：Human-in-the-loop 投决闭环

### P5.1 R4 人工确认

新增 endpoint：

```text
POST /api/primary-market/meeting/{deal_id}/decision/human-confirm
```

请求：

```json
{
  "status": "approved|rejected|needs_revision",
  "note": "人工确认意见",
  "override": false
}
```

写入：

```text
data/wiki/deals/{deal_id}/phases/r4_decision.json
data/wiki/deals/{deal_id}/decision/IC_DECISION_REPORT.md
data/wiki/deals/{deal_id}/audit/audit_log.json
```

### P5.2 红线 override

若 Legal/Risk 出现 hard gate：

- 默认阻断 R4 pass。
- 主席或人类可以 override，但必须写入：
  - override reason
  - risk owner
  - mitigation plan
  - monitoring metrics

### P5.3 UI

R4 决策卡片增加：

- `预览`
- `要求修订`
- `人工确认通过`
- `人工否决`

## 5. 数据契约

### 5.1 Profile contract

建议 schema：

```json
{
  "schema_version": "siq_ic_profile_contract_v1",
  "profile_id": "siq_ic_finance_auditor",
  "label": "财务审计委员",
  "role": "finance",
  "responsibilities": [],
  "focus": "",
  "outputs": [],
  "boundaries": [],
  "source_files": [],
  "startup_retrieval_required": true,
  "r1_sequence_index": 2,
  "updated_at": "..."
}
```

### 5.2 Meeting readiness

建议 schema：

```json
{
  "schema_version": "siq_primary_market_meeting_readiness_v1",
  "deal_id": "DEAL-...",
  "profiles": [],
  "summary": {
    "runtime_running": 7,
    "receipt_present": 6,
    "r1_reports_present": 3,
    "blocking_profiles": []
  }
}
```

### 5.3 Meeting task event

建议 schema：

```json
{
  "schema_version": "siq_primary_market_meeting_task_event_v1",
  "event_id": "meeting-task-...",
  "deal_id": "DEAL-...",
  "lane": "workflow-main",
  "profile_id": "siq_ic_master_coordinator",
  "action": "workflow.advance-next",
  "dry_run": false,
  "status": "completed|blocked|failed",
  "artifact_paths": [],
  "blocking_reasons": [],
  "created_at": "..."
}
```

## 6. 前端落地清单

### 6.1 API 文件

修改：

```text
apps/web/src/features/primary-market/primaryMarketApi.ts
```

新增函数：

- `fetchPrimaryMarketMeetingAgentReadiness(dealId)`
- `preparePrimaryMarketMeetingAgent(dealId, profileId, options)`
- `preparePrimaryMarketMeetingCommittee(dealId, options)`
- `advancePrimaryMarketMeetingWorkflow(dealId, payload)`
- `confirmPrimaryMarketDecision(dealId, payload)` P5

### 6.2 View model

修改：

```text
apps/web/src/features/primary-market/primaryMarketViewModel.ts
```

新增：

- readiness normalize
- agent status chip 派生
- artifact status 派生
- formal task buttons enabled/disabled rules

### 6.3 页面

修改：

```text
apps/web/src/pages/PrimaryMarketMeeting.tsx
```

重点：

- 不扩大会议室 UI 复杂度。
- 聊天主体验保持问答助手风格。
- readiness、receipt、artifact 作为轻量状态或可折叠 panel。
- 任务执行入口和聊天输入区分开。

### 6.4 CSS

尽量复用现有：

```text
apps/web/src/styles/chat.css
apps/web/src/styles/quick-questions.css
```

如新增状态 chips，优先使用现有 `StatusBadge`。

## 7. 后端落地清单

### 7.1 新增服务

```text
apps/api/services/ic_profile_contract.py
apps/api/services/ic_agent_output_quality.py      # P3
apps/api/services/primary_market_meeting_readiness.py
```

### 7.2 修改路由

```text
apps/api/routers/primary_market_meeting.py
```

新增：

- `GET /primary-market/meeting/{deal_id}/agents/readiness`
- `POST /primary-market/meeting/{deal_id}/agents/{profile_id}/prepare`
- `POST /primary-market/meeting/{deal_id}/agents/prepare-all`
- `POST /primary-market/meeting/{deal_id}/workflow/advance`
- `POST /primary-market/meeting/{deal_id}/decision/human-confirm` P5

### 7.3 复用现有服务

不要重写：

- `ic_startup_retrieval.generate_startup_retrieval_receipt`
- `ic_agent_runtime.run_workflow_r1_agent`
- `ic_agent_runtime.run_workflow_r1_serial`
- `ic_agent_runtime.run_workflow_advance_next`
- `ic_decision_report.finalize`
- `deal_phase_artifacts.summarize_deal_phase_artifacts`
- `deal_reports.*`
- `deal_contracts.run_deal_preflight`

## 8. 权限与安全

沿用现有权限：

| 动作 | 权限 |
| --- | --- |
| 查看 readiness / receipt / artifacts | `report.view` |
| 准备 agent / 生成 receipt | `report.create` |
| 执行 R1/R2/R3/R4 workflow | `report.create` |
| R4 人工确认 | 建议 `report.create`，后续可升级为 `report.approve` |

安全边界：

- 前端不得直接访问 Hermes port。
- 前端不得直接写 `data/wiki/deals`。
- meeting wrapper 不返回未脱敏绝对路径。
- transcript meta 继续走 `deal_store.redact_public_payload`。

## 9. 测试计划

### 9.1 后端单测

新增/扩展：

```text
apps/api/tests/test_ic_profile_contract.py
apps/api/tests/test_primary_market_meeting_router.py
apps/api/tests/test_primary_market_meeting_readiness.py
apps/api/tests/test_ic_agent_output_quality.py
```

覆盖：

- 7 个 profile contract 完整。
- readiness missing receipt/report 不报错。
- prepare endpoint 生成 receipt。
- prepare-all 跳过 master coordinator，覆盖 6 个 R1 profiles。
- workflow advance dry-run 不写产物。
- workflow advance non-dry-run 写 transcript event。
- role guard 不重复注入。
- chat display_message 不被 guard 污染。

### 9.2 前端测试

新增/扩展：

```text
apps/web/src/features/primary-market/primaryMarketApi.test.ts
apps/web/src/features/primary-market/primaryMarketViewModel.test.ts
```

覆盖：

- readiness normalize。
- prepare 请求参数。
- workflow advance 请求参数。
- agent 状态派生。

### 9.3 手工验收

1. 打开 `/primary-market/meeting?dealId=...`。
2. 选择财务审计委员。
3. 查看状态应显示：
   - Hermes running
   - receipt present/missing
   - report present/missing
4. 点击“准备智能体”生成 receipt。
5. 点击默认问题，回答应以财务职责为主，越权时提示边界。
6. 切换法务、风险，回答职责明显不同。
7. 点击“预演下一步”，不写入产物。
8. 点击“执行下一步”，写入 workflow artifact，并刷新产物状态。

## 10. 分阶段开发排期

### Phase 0：1-2 天

- 新增 `ic_profile_contract.py`
- 替换职责护栏来源
- 新增 readiness endpoint
- 前端展示轻量 readiness
- 单测通过

### Phase 1：1-2 天

- 聊天注入 receipt/evidence context
- prepare agent / prepare all endpoint
- 前端准备按钮
- 单测和手工验收

### Phase 2：2-3 天

- 正式任务入口接入 `/api/deals/{deal_id}/workflow/*`
- workflow action 写入 meeting transcript
- 产物视图
- R1/R2/R3/R4 状态刷新

### Phase 3：2 天

- `ic_agent_output_quality.py`
- 聊天消息质量 chip
- meeting_quality 存档

### Phase 4：2-3 天

- workflow advance wrapper
- 总协调员主持动作
- 人工确认门禁 UI

### Phase 5：2 天

- R4 人工确认 endpoint
- override 记录
- 决策卡片闭环

## 11. 风险与回滚

### 11.1 风险

| 风险 | 处理 |
| --- | --- |
| profile 文件格式不稳定 | P0 使用 matrix + 文件存在性 fallback，不强依赖复杂解析 |
| Hermes 输出不满足 R1 JSON contract | 继续由 `ic_agent_runtime` 抛出 contract error，前端展示阻断 |
| 会议室 UI 过重 | readiness 和产物视图做折叠，不打断聊天 |
| workflow 误写入 | 所有正式动作默认先 dry-run；非 dry-run 加确认 |
| 旧页面受影响 | 所有新增 API 在 `/api/primary-market/meeting/*` 下包装，`/api/deals/*` 不改语义 |

### 11.2 回滚策略

- P0/P1 出问题：前端隐藏 readiness/prepare 按钮，聊天仍可用。
- P2 出问题：禁用正式任务入口，现有 `/deals/:dealId/workflow` 仍可用。
- P3 出问题：不展示 quality chip，不影响聊天和 workflow。
- P4/P5 出问题：回退到现有 workflow 页面和 decision 页面处理。

## 12. 推荐开发顺序

最推荐的首批任务：

1. `ic_profile_contract.py`
2. readiness endpoint
3. 前端 readiness chips
4. prepare agent endpoint
5. 聊天注入 receipt 摘要
6. workflow advance wrapper
7. 产物视图
8. output quality

这条路线收益最高，因为它先解决“智能体是否按职责、有证据地回答”的底层问题，再扩展自动工作流。

## 13. 验收标准

达到以下条件，可认为 7 个智能体“充分发挥职责”的第一阶段完成：

- 每个 profile 的职责契约来自 profile/matrix，而不是页面手写。
- 每次一级市场聊天都会注入对应 profile 的职责护栏。
- 前端能看到每个 agent 的 Hermes、receipt、R1 report 状态。
- 用户能一键为单个 agent 或全体委员生成 startup retrieval receipt。
- 正式 R1 任务只能在 readiness 合格后执行。
- R1 产物能写入项目包并在会议室展示。
- 总协调员可以预演和执行 workflow 下一步。
- 聊天回答能标注缺证、越权、缺 verified/assumed 等质量问题。
