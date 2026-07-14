# 一级市场 IC OpenClaw 行为等价与报告质量优化方案

> 日期：2026-07-13
>
> 文档编号：SIQ-PM-IC-BEHAVIOR-2026-07-13
>
> 状态：待实施
>
> 适用范围：`agents/hermes/profiles/siq_ic_*`、`apps/api` Deal OS/IC runtime、`apps/web` 一级市场投研决策、离线评测与发布门禁
>
> OpenClaw 参考源：`/home/maoyd/.openclaw/workspace`
>
> 关联方案：`2026-06-28-primary-market-openclaw-compat-design.md`、`2026-07-04-primary-market-deal-os-v2-redesign.md`、`2026-07-06-primary-market-ic-agent-effectiveness-development-plan.md`、`2026-07-13-primary-market-prospectus-materials-center-optimization-plan.md`

## 0. 执行结论

一级市场 IC 的后续目标不是继续复制 OpenClaw workspace，而是完成 OpenClaw 投委会方法论在 SIQ Hermes + Deal OS 中的生产级行为等价：

```text
OpenClaw
  作为方法论、角色资产和历史样板来源

Hermes profiles
  作为唯一生产角色权威

Deal OS / IC runtime
  作为阶段编排、任务状态、权限、证据、合同、审计和恢复权威

结构化报告合同
  作为正式事实和决策产物权威
```

“深度复刻”只复刻以下内容：

1. 七个角色的职责边界、分析框架和协作关系。
2. R0-R4 的阶段目标、输入、输出、交接和回退条件。
3. startup retrieval、证据标注、交叉验证、争议识别、主席裁决和红蓝对抗。
4. 专家报告、投决报告、评分纪律和人工确认。
5. 对异常、缺证、超时、失败恢复和审计链的行为要求。

以下内容不得直接复刻：

- OpenClaw session/runtime、heartbeat 和本地 spawn 机制。
- workspace memory、历史会话、缓存、虚拟环境和凭据。
- 硬编码 `/home/maoyd/.openclaw/...` 路径。
- Agent 直接写项目文件、直接访问 Milvus 或绕过 SIQ 权限的脚本。
- 历史项目中的未验证结论、占位符报告和一次性 glue code。

本方案锁定的正式工作流是：

```text
R0 项目与证据准入
  -> R1A 专家独立研究
  -> R1B 风控反证 + 主席初步综合
  -> R1.5 分歧识别 + 主席裁决/补证回路
  -> R2 专家基于裁决和新增证据修订
  -> R3 动态红蓝对抗
  -> R4 主席结构化决策
  -> Coordinator 汇编
  -> Factchecker + deterministic quality gate
  -> 人工确认
```

确定性 R2/R3/R4 继续保留，但定位为 preview、测试、恢复和模型不可用时的降级产物。它们不能冒充真实多智能体正式运行。

## 1. 现状基线与问题确认

### 1.1 已有能力

当前仓库已经具备：

- 七个 `siq_ic_*` Hermes profile 和 `siq_ic_shared`。
- OpenClaw profile asset inventory 和 script migration matrix。
- `ic_workflow_policy.json`、profile matrix、evidence/report/prompt contracts。
- Deal package、Evidence、startup receipt、preflight、workflow state 和 audit。
- R1 单 agent 真实 Hermes 调用、严格串行调度、task lease 和失败审计。
- R1.5 确定性分歧识别、裁决草案和人工裁决写入。
- R2、R3、R4 确定性产物生成。
- 一级市场投研会议室、agent readiness、prepare、R1 run、advance-next 和人工确认。

### 1.2 已确认差距

| 编号 | 当前行为 | 影响 |
| --- | --- | --- |
| A1 | IC agent runtime 的真实 Hermes round 只支持 R1 | R1.5/R2/R3/R4 没有真实模型协作 |
| A2 | R1 output contract 只有通用字段 | 各专业报告深度无法自动验证 |
| A3 | R1 严格串行只做执行门禁，task payload 未明确注入前序报告 | “按顺序运行”并没有形成交叉验证 |
| A4 | R1 以 Markdown + 末尾 JSON 摘要为主 | 正文与摘要可能不一致，结构化验证能力弱 |
| A5 | R2 复制 R1 观点并附加裁决 | 没有真正重读证据、回答争议或更新评分 |
| A6 | R3 把 open questions/risk flags 改写为 challenge | 没有红方立论、蓝方答辩、反驳和主席裁定 |
| A7 | advance-next 默认跳过 R3 | 高质量流程可能无意中绕过反方挑战 |
| A8 | R4 使用 R1 主席分数和确定性摘要 | 主席没有基于 R2/R3 做最终结构化决策 |
| A9 | 最终 Markdown 主要提示“参见其他文件” | 不是可直接提交投委会的完整中文报告 |
| A10 | migration matrix 用单一 `migrated` 表达多种成熟度 | 工程存在性被误认为行为和质量等价 |
| A11 | OpenClaw 历史样板质量不一致 | 占位报告、旧结论和未验证事实不能直接作为黄金集 |
| A12 | 新材料/Evidence 变化缺少统一 snapshot 约束 | Agent 可能基于旧证据继续推进 |

### 1.3 必须修正的项目表述

后续不得只用“已迁移”描述 IC 能力。所有能力改用四级状态：

```text
asset_migrated
contract_migrated
behavior_migrated
quality_accepted
```

定义：

| 状态 | 含义 |
| --- | --- |
| `asset_migrated` | profile、模板或脚本参考已进入仓库 |
| `contract_migrated` | SIQ 已有 API/schema/state/audit 合同 |
| `behavior_migrated` | 真实 Hermes/服务编排能执行对应角色行为 |
| `quality_accepted` | 黄金集、合同门禁、事实核验和真实 smoke 均通过 |

一个能力只有达到 `quality_accepted` 才能在产品中标记为“正式可用”。

## 2. 目标与非目标

### 2.1 产品目标

1. 用户能看到每个阶段由谁执行、读了什么、输出了什么、为何能进入下一阶段。
2. 每个专家报告体现角色专属分析方法，而不是同一通用模板换角色名。
3. 每个关键判断可以回到 Evidence、来源页、计算 trace 和使用的证据快照。
4. R1.5、R2、R3、R4 形成真实的质询、修订、对抗和收敛过程。
5. 最终 R4 是可直接审阅的完整中文投决报告，不要求读者跳转多个文件才能理解结论。
6. 证据不足、重大分歧未解决或质量检查失败时，系统能够拒绝给出 `pass`。

### 2.2 工程目标

1. 将 phase/task/report contract 从 R1 扩展到 R1.5/R2/R3/R4。
2. 使用可恢复 task lease 运行所有正式 Hermes 阶段。
3. 将 Agent 输出改为结构化 JSON 权威、服务端渲染 Markdown/HTML。
4. 建立 claim 级 Evidence 和角色专属 schema。
5. 建立 deterministic validator + Factchecker 双层质量门禁。
6. 建立 OpenClaw 行为清单、黄金集、离线评测和真实 gateway smoke。

### 2.3 非目标

本阶段不做：

- 不引入新的 IC 角色。
- 不让 Agent 自主决定投资执行或资金划拨。
- 不删除 deterministic mode。
- 不要求所有阶段一次性全自动无人值守。
- 不把 OpenClaw 历史 memory 导入 Hermes memory。
- 不用 LLM 自评分替代确定性质量门禁。
- 不因报告篇幅长就认定报告质量高。

## 3. 设计原则

### 3.1 行为等价优先于文件等价

文件名和 prompt 文案可以改变，但以下行为必须保持：

- 角色不能越权替代其他专家。
- 专家正式发言前必须完成检索和证据准备。
- 分歧必须保留双方观点和证据。
- 主席裁决必须说明接受、拒绝和补证理由。
- 报告必须区分 verified、derived、assumed、contested 和 missing。
- 投资条件必须可执行、可验证、可监控。
- 所有阶段都有明确输入、输出和审计。

### 3.2 结构化产物优先

Agent 的正式输出以 versioned JSON envelope 为权威；Markdown 和 HTML 由服务端 renderer 生成。禁止继续依赖“从自由文本最后一个 JSON 对象猜摘要”作为长期主合同。

### 3.3 独立判断与协作收敛分层

R1 首轮必须保留独立判断，防止早期专家互相锚定；风险和主席阶段再读取前序观点完成交叉验证。后续 R2/R3 才要求显式回应其他角色。

### 3.4 模型与确定性核心分层

```text
模型负责：专业分析、提出论点、回应质询、形成裁决草案

确定性服务负责：
  权限
  输入选择
  task lease
  schema 校验
  Evidence 校验
  评分计算
  状态推进
  文件写入
  审计
  报告渲染
  发布门禁
```

### 3.5 缺证优于编造

`insufficient_evidence` 是有效且受鼓励的正式结论。系统不得为了完成 R4 自动补全不存在的数据、估值或法律事实。

## 4. 目标架构

```text
Deal Evidence Snapshot
  -> Phase Planner
  -> Role-specific Task Builder
  -> Hermes Gateway
  -> Structured Output Parser
  -> Role Contract Validator
  -> Claim/Evidence Validator
  -> Phase Artifact Store
  -> Dispute/Revision/Debate Engine
  -> R4 Decision Builder
  -> Report Renderer
  -> Factchecker
  -> Quality Gate
  -> Human Confirmation
```

### 4.1 权威边界

| 内容 | 权威位置 |
| --- | --- |
| 角色身份和方法论 | `agents/hermes/profiles/siq_ic_*` |
| 角色能力、轮次和 schema | `siq_ic_shared` versioned contracts |
| 当前工作流状态 | Deal `phases/workflow_state.json` 或后续 durable repository |
| 证据事实 | Deal Evidence + archived source artifacts |
| Agent 原始输出 | audit artifact，非最终事实权威 |
| 专家正式报告 | 校验后的 phase JSON |
| Markdown/HTML | 由正式 JSON 渲染的展示产物 |
| 最终决策 | R4 structured decision + human confirmation |

### 4.2 Hermes Agent 通信模型

当前 SIQ Hermes runtime 没有复刻 OpenClaw 的 `sessions_send/sessions_history` Agent-to-Agent 通道。实际调用方式是：

```text
API ic_agent_runtime
  -> hermes_client.create_run(profile=A)
  -> profile A 独立 /v1/runs gateway
  -> hermes_client.collect_run_result()
  -> API 校验并保存结果
```

每个 IC profile 使用独立 gateway。Agent 之间不会直接互调 gateway，也不会自动看到其他 Agent 的会话或输出。当前 R1 task 只包含 Deal、Evidence、workflow 和 startup receipt 等输入；严格串行本身不构成 Agent 通信。

这不是需要恢复 OpenClaw session IPC 的缺陷。生产目标采用 **orchestrator-mediated durable handoff**：

```text
Agent A task
  -> Hermes gateway A
  -> structured result A
  -> schema/Evidence validator
  -> durable phase artifact + handoff event
  -> IC phase orchestrator 选择允许传递的 claims/artifacts
  -> 构造 Agent B task + input digest
  -> Hermes gateway B
```

通信规则：

1. 专家之间禁止直接调用彼此 gateway。
2. `siq_ic_master_coordinator` profile 可以提供编排建议，但真正的消息路由、重试、权限、输入裁剪和状态推进由 API application service 执行。
3. Agent B 只能看到 task contract 明确列出的 artifact/claim/evidence refs，不能读取 Agent A 的私有 session、reasoning 或整个 workspace。
4. 每次 handoff 必须持久化，API/Agent 重启后可从 artifact 和 event 恢复，不能依赖内存对话继续存在。
5. 会议室普通聊天不是正式 phase 通信通道；聊天内容只有经人工选择、结构化和 Evidence 校验后才能成为 workflow 输入。
6. Agent 输出失败、schema 非法或 Evidence 校验失败时，不得向下游 Agent 投递为正式输入。
7. 重试使用相同 input digest 和新的 attempt/run ID，避免重复推进阶段。

建议 handoff 合同：

```json
{
  "schema_version": "siq_ic_agent_handoff_v1",
  "handoff_id": "ICHANDOFF-...",
  "workflow_run_id": "ICRUN-...",
  "deal_id": "...",
  "phase": "R2",
  "from_agent_id": "siq_ic_chairman",
  "to_agent_id": "siq_ic_finance_auditor",
  "source_report_ids": [],
  "claim_ids": [],
  "dispute_ids": [],
  "evidence_ids": [],
  "evidence_snapshot_hash": "...",
  "input_digest": "...",
  "created_at": "..."
}
```

各阶段通信内容：

| 阶段 | 发送方 -> 接收方 | 传递内容 |
| --- | --- | --- |
| R1A | Orchestrator -> 四位独立专家 | R0 task、role receipt、Evidence；不传同轮专家结论 |
| R1B Risk | 四位专家 -> 风控 | 已校验 claims、scores、red flags、Evidence refs |
| R1B Chairman | 五位专家 -> 主席 | 已校验报告、冲突摘要、Evidence coverage |
| R1.5 | Dispute detector -> 主席 | 结构化 positions、claim/evidence refs、缺证项 |
| R2 | 主席裁决/peer reports -> 各专家 | 仅与该角色相关的裁决、peer claims、新 Evidence |
| R3 | Debate state -> 红/蓝方 | topic、对方上一轮 argument IDs、Evidence refs、未回答问题 |
| R4 | R2/R3/quality services -> 主席 | 当前有效报告、verdicts、score inputs、quality restrictions |

因此，后续所称“真实 R1.5/R2/R3/R4 Hermes 调用”均指 API orchestrator 调用多个独立 Hermes profile，并通过持久化 handoff 串联结果，不指 Hermes Agent 在网关之间自由互发消息。

## 5. 目标工作流

### 5.1 R0：项目与证据准入

执行者：`siq_ic_master_coordinator` 对应的确定性 application service；模型只可生成补充建议，不代替 gate。

输入：

- Deal identity 和项目阶段。
- active analysis sources。
- `evidence_snapshot_hash`。
- parser/source capabilities。
- Evidence coverage 和 quality。
- 投资策略、行业和阶段适配 policy。

输出：

```json
{
  "schema_version": "siq_ic_r0_readiness_v2",
  "deal_id": "...",
  "evidence_snapshot_hash": "...",
  "admission": "ready|conditional|blocked",
  "blocking_reasons": [],
  "warnings": [],
  "due_diligence_plan": [],
  "role_tasks": {},
  "required_followups": []
}
```

Gate：

- Deal identity 完整。
- 至少一个 active project source。
- business/finance/legal/risk 最低 Evidence coverage 满足 policy。
- 财务能力受限时财务任务明确为补证或 restricted mode。

### 5.2 R1A：专家独立首轮研究

执行者：

```text
siq_ic_strategist
siq_ic_sector_expert
siq_ic_finance_auditor
siq_ic_legal_scanner
```

调度模式：同一 evidence snapshot 下可并行，但每个任务不读取其他 R1A 结论。

目的：形成独立专业判断，减少从众和顺序锚定。

每个任务读取：

- 当前 startup receipt。
- 角色相关 Evidence hits 和 source capabilities。
- R0 尽调任务。
- 角色方法论和禁止事项。
- Deal stage 和投资策略。

不得读取：

- 同轮其他专家的 recommendation/score。
- 主席预设结论。

### 5.3 R1B：反证与初步综合

第一步由 `siq_ic_risk_controller` 读取四份独立报告和原始 Evidence，执行：

- 反证搜索。
- 情景压力测试。
- 关键假设脆弱性分析。
- 风险传导链。
- 红线和止损条件。
- 跨报告事实冲突识别。

第二步由 `siq_ic_chairman` 读取五份专家报告，形成主席初步综合，但不得提前写最终 R4 决策。

主席 R1B 输出包括：

- 一致结论。
- 关键分歧。
- 证据不足项。
- 需要 R1.5 裁决或补证的问题。
- 六维评分初稿及证据来源。

### 5.4 R1.5：分歧识别与主席裁决

分两层：

1. Deterministic dispute detector 从专家结构化 claims、scores、recommendations 和 evidence 中生成候选分歧。
2. 主席 Hermes 对每个候选分歧执行裁决、退回补证或保留争议。

主席不能用一句总结覆盖双方观点。每个裁决必须保存：

```json
{
  "dispute_id": "DSP-...",
  "question": "...",
  "severity": "critical|high|medium|low",
  "positions": [],
  "evidence_ids": [],
  "counter_evidence_ids": [],
  "ruling": "accept_a|accept_b|synthesize|needs_more_evidence|unresolved",
  "rationale": "...",
  "accepted_claim_ids": [],
  "rejected_claim_ids": [],
  "required_followups": [],
  "decision_impact": "..."
}
```

当 `needs_more_evidence` 时，workflow 返回 R0/R1 补证任务，不得自动进入 R2。

### 5.5 R2：专家观点修订

执行者：五位专业专家，不含主席最终决策。

每位专家读取：

- 自己的 R1 报告。
- 与自己相关的 peer claims。
- R1.5 分歧和主席裁决。
- 新增 Evidence 和新的 snapshot hash。
- 自己尚未关闭的 open questions。

R2 不是重写 R1，而是必须给出 delta：

```json
{
  "r1_score": 72,
  "r2_score": 65,
  "score_change": -7,
  "changed_claims": [],
  "unchanged_claims": [],
  "accepted_rulings": [],
  "challenged_rulings": [],
  "new_evidence_ids": [],
  "closed_questions": [],
  "remaining_questions": [],
  "revision_rationale": "..."
}
```

没有观点变化也必须解释为何新证据或裁决不足以改变结论。

### 5.6 R3：动态红蓝对抗

R3 默认模式改为 `dynamic`，不得默认 `skip`。

模式判定：

| 模式 | 条件 |
| --- | --- |
| `skip` | 无 material/critical contested claim、无未解决高风险、证据覆盖达标、所有 R1.5 裁决关闭，且 policy 允许 |
| `short` | 只有 1-2 个中等争议，核心事实一致 |
| `full` | 存在 critical/high 争议、评分跨阈值、法律/财务红线冲突或主席裁决被专业角色挑战 |

skip 必须写结构化原因；正式环境可要求人工确认或 policy 显式允许。

每个 debate topic 的真实交锋：

```text
Round 1: Red thesis
Round 2: Blue defense
Round 3: Red rebuttal / Blue final response
Chairman verdict
```

红蓝阵营根据该争议中的实际立场动态分配，不能永久把某些角色定义为支持方或反对方。

每轮保存 argument、claim IDs、evidence IDs、对方论点引用和 unanswered points。主席 verdict 必须说明哪条论证成立、哪条证据不足以及对最终决策的影响。

### 5.7 R4：结构化决策与完整报告

第一步：主席 Hermes 读取当前 evidence snapshot、R2、R3、R1.5 和 score discipline，输出结构化 R4 decision draft。

第二步：确定性服务校验评分、Evidence、条件、红线和 workflow identity。

第三步：Coordinator renderer 生成完整中文 Markdown/HTML，不新增事实。

第四步：Factchecker 对报告 claims、数字、期间、币种、引用和内部一致性做验证。

第五步：质量通过后进入 human confirmation。

R4 不能继续把 R1 主席分数直接当作最终主席分数。主席必须给出 R4 六维评分及逐维证据和变化原因。

## 6. 统一任务合同

所有正式 Agent task 使用：

```json
{
  "schema_version": "siq_ic_agent_task_v2",
  "task_id": "ICTASK-...",
  "workflow_run_id": "ICRUN-...",
  "deal_id": "...",
  "phase": "R1A|R1B|R1.5|R2|R3|R4",
  "round_name": "...",
  "agent_id": "siq_ic_...",
  "research_identity": {},
  "evidence_snapshot_hash": "...",
  "prompt_contract_version": "...",
  "profile_contract_version": "...",
  "input_artifacts": [],
  "input_digest": "...",
  "role_objectives": [],
  "required_questions": [],
  "hard_rules": [],
  "output_schema": "...",
  "timeout_seconds": 0,
  "created_at": "..."
}
```

### 6.1 输入不可变性

任务开始时保存 input digest。任务运行期间 active source 变化时：

- 当前 Agent run 可以完成，但结果标记 `stale_on_completion`。
- stale 结果不得自动推进 workflow。
- 用户可查看结果，但必须用新 snapshot 重跑或显式人工接受。

### 6.2 任务终态

```text
queued -> running -> succeeded
                  -> failed
                  -> cancelled
                  -> interrupted
                  -> timed_out
                  -> stale_on_completion
```

所有 phase 复用 R1 已有的 claim/heartbeat/lease 语义。不能只有 R1 有租约，R2-R4 又退回无 owner 的文件写入。

### 6.3 模型运行审计

保存：

- Hermes profile 和 gateway。
- model/provider 标识。
- prompt contract version。
- input digest 和 evidence snapshot。
- run ID、开始/结束时间、终态和重试次数。
- 原始输出 artifact hash。
- contract validator 和 quality validator 结果。

不保存密钥或完整内部系统 prompt 到公开 API。

## 7. 通用报告合同

### 7.1 正式报告 envelope

```json
{
  "schema_version": "siq_ic_expert_report_v2",
  "report_id": "ICRPT-...",
  "deal_id": "...",
  "phase": "R1A",
  "agent_id": "siq_ic_finance_auditor",
  "research_identity": {},
  "evidence_snapshot_hash": "...",
  "recommendation": "support|conditional_support|review|reject|insufficient_evidence",
  "score": 0,
  "confidence": "high|medium|low",
  "claims": [],
  "scorecard": {},
  "red_flags": [],
  "open_questions": [],
  "required_followups": [],
  "executive_summary": "...",
  "methodology": [],
  "limitations": [],
  "created_at": "..."
}
```

### 7.2 Claim 合同

```json
{
  "claim_id": "CLM-...",
  "topic": "revenue_quality",
  "conclusion": "...",
  "status": "verified|derived|assumed|contested|missing",
  "evidence_ids": ["EVID-..."],
  "counter_evidence_ids": [],
  "calculation_trace_ids": [],
  "confidence": "high|medium|low",
  "decision_impact": "critical|material|supporting",
  "period": null,
  "currency": null,
  "unit": null
}
```

约束：

- `verified` 必须有 Evidence。
- `derived` 必须有 Evidence 和 calculation/method trace。
- `assumed` 必须说明假设和验证方法。
- `contested` 必须保留双方或反证。
- `critical/material` claim 不得只有模糊来源描述。

### 7.3 Markdown 渲染

Agent 不直接控制正式 Markdown 文件结构。服务端按 report JSON 和角色模板渲染，保证：

- 正文和结构化摘要一致。
- Evidence 引用格式统一。
- 不出现 prompt、内部路径和模型控制字段。
- 缺失章节明确显示“证据不足”，不生成占位符。

原始 Agent narrative 可以作为 audit 附件保留，但不直接成为最终报告权威。

## 8. 角色专属合同

### 8.1 Master Coordinator

职责：

- 检查 Deal、Evidence、receipt 和 phase readiness。
- 拆分任务、监控终态、汇总状态和生成审计链。
- 识别缺失产物和矛盾，不代写专业结论。

输出：

```text
readiness
due_diligence_plan
task_assignments
progress_summary
quality_summary
audit_summary
```

禁止输出独立估值、法律意见或最终投资决策。

### 8.2 Strategist

必选分析：

- 宏观、政策、产业周期和资本流向。
- 项目战略位置与基金策略匹配。
- 募投方向、成长路径、退出窗口和外部冲击。
- 战略情景与领先指标。

专属字段：

```text
policy_assessment
cycle_position
capital_flow_signals
strategic_fit
scenario_matrix
exit_window
```

### 8.3 Sector Expert

必选分析：

- TAM/SAM/SOM 方法、假设和敏感性。
- 竞争者、市场份额、集中度和进入壁垒。
- 技术路线、替代风险、产业链和议价能力。
- 客户/供应商结构和行业生命周期。

专属字段：

```text
market_sizing
competitor_matrix
technology_routes
value_chain
market_share_evidence
industry_lifecycle
```

### 8.4 Finance Auditor

必选分析：

- 报告期财务数据身份、期间、币种和单位。
- 收入质量、利润质量、现金流和资产负债。
- 三表勾稽、异常项目和审计意见。
- 预测、估值情景、敏感性和回报测算。

专属字段：

```text
historical_financials
financial_reconciliations
quality_of_earnings
cash_flow_assessment
forecast_scenarios
valuation_scenarios
sensitivity_analysis
calculation_trace_ids
```

所有决定性数字必须有结构化事实或可重算 trace。

### 8.5 Legal Scanner

必选分析：

- 主体、股权、历史沿革和实际控制人。
- 资质、重大合同、知识产权、劳动人事和数据合规。
- 诉讼处罚、关联交易、同业竞争和特殊股东权利。
- 法律风险、整改方式、交割前提和 TS 保护条款。

专属字段：

```text
legal_issues
legal_basis
severity
remediation
closing_conditions
term_sheet_protections
unresolved_legal_questions
```

### 8.6 Risk Controller

必选分析：

- 对其他专家关键假设进行反证。
- 市场、供应链、竞争、ESG、舆情和黑天鹅风险。
- 基准/悲观/极端压力情景。
- 风险概率、影响、传导链、领先指标和止损阈值。

专属字段：

```text
risk_register
counter_theses
stress_scenarios
risk_transmission
leading_indicators
warning_thresholds
stop_loss_thresholds
veto_flags
```

### 8.7 Chairman

职责：

- 汇总但不抹平专业差异。
- 对分歧逐项裁决。
- 给出六维评分、定性判断、条件和最终建议。
- 说明 weighted agent score 与 chairman dimension score 的差异。

专属字段：

```text
consensus
disputes
rulings
six_dimension_scorecard
weighted_agent_score
chairman_dimension_score
chairman_qualitative_decision
conditions
monitoring_metrics
decision
```

主席不能仅通过加权分数自动生成结论。

## 9. Profile 与 Prompt 组织

### 9.1 Hermes 为唯一生产权威

迁移完成后：

- OpenClaw profile 文件只作为 reference source。
- Hermes profile 是运行时唯一读取位置。
- OpenClaw 后续变更通过显式 diff 和 migration review 进入 Hermes，不做双向自动同步。
- 不允许生产 gateway 回退读取 `.openclaw/workspace`。

### 9.2 建议目录

```text
agents/hermes/profiles/siq_ic_shared/
  ic_profile_matrix.json
  ic_workflow_policy.json
  contracts/
    common_claim.schema.json
    expert_report.schema.json
    r0_readiness.schema.json
    r1_report.schema.json
    r1_5_dispute.schema.json
    r2_revision.schema.json
    r3_debate.schema.json
    r4_decision.schema.json
  rules/
    evidence_rules.md
    scoring_rules.md
    phase_transition_rules.md
    report_quality_rules.md
  templates/
    role_reports/
    r4_decision_report.md
```

角色 profile 保留身份和专业方法，阶段 schema 和跨角色规则放在 shared，避免七份文档各自漂移。

### 9.3 Task prompt

Task prompt 由服务端按以下顺序构造：

```text
角色身份与边界
  -> 当前 phase 目标
  -> 当前 Deal/ResearchIdentity
  -> evidence snapshot 和 capability restrictions
  -> role-specific Evidence/peer inputs
  -> required questions
  -> output schema
  -> hard rules
```

不得把整个 Deal 目录、全部历史聊天或招股书全文直接拼入 prompt。

### 9.4 输出方式

首选严格单 JSON envelope。Hermes 运行时若暂不支持原生 structured output，则：

1. 明确要求只输出一个 JSON 对象。
2. 使用 schema validator 严格解析。
3. 允许一次受控 repair attempt。
4. repair 仍失败则任务失败，不把自由文本保存为正式报告。

移除长期依赖 `_extract_json_object()` 的“首尾大括号猜测”语义。

## 10. Evidence 与快照约束

本方案与招股书材料方案共享：

```text
source_id
ResearchIdentity
evidence_snapshot_hash
Evidence page/block/bbox
source capabilities
```

### 10.1 Receipt v2

```json
{
  "schema_version": "siq_ic_startup_receipt_v2",
  "receipt_id": "...",
  "deal_id": "...",
  "agent_id": "...",
  "phase": "R1A",
  "evidence_snapshot_hash": "...",
  "source_ids": [],
  "queries": [],
  "evidence_hits": [],
  "capability_restrictions": [],
  "gaps": [],
  "gate": {"allowed_to_speak": true, "blocking_reasons": []}
}
```

### 10.2 Claim 校验

每个正式 report validator 检查：

- Evidence ID 存在。
- Evidence 属于当前 Deal 和 snapshot。
- Evidence identity 与 report ResearchIdentity 一致。
- financial claim 的 period/currency/unit 与来源一致。
- derived claim 有 calculation trace。
- counter evidence 没有被丢弃或错误标成 supporting evidence。

### 10.3 快照变化

当招股书、新材料或 active parse run 变化：

- 新 task 使用新 snapshot。
- 旧 queued task 在 claim 前取消或重建。
- 旧 running task 完成后标记 stale。
- 旧正式报告保留，但 readiness 显示 stale。
- 已确认 R4 进入 decision review required。

## 11. 评分与决策纪律

### 11.1 双评分保留

R4 必须同时保留：

```text
weighted_agent_score
chairman_dimension_score
chairman_qualitative_decision
threshold_result
```

`final_score` 不能掩盖两个评分体系的差异。报告必须解释分差来源。

### 11.2 角色评分

每个 scorecard 子维度包含：

```json
{
  "dimension": "...",
  "score": 0,
  "weight": 0,
  "rationale": "...",
  "claim_ids": [],
  "evidence_ids": [],
  "confidence": "..."
}
```

没有 claim/evidence 的分数不能进入正式加权。

### 11.3 红线约束

- critical veto flag 未解决时不能 `pass`。
- 高严重度法律/财务事实为 missing 时不能给高置信度 support。
- assumed 关键项过多时限制 confidence 和 recommendation。
- R2/R3 暴露的新重大风险必须反映在 R4 评分变化中。
- 评分纪律由 deterministic validator 执行，不能只依靠 prompt 提醒。

## 12. R3 动态机制

### 12.1 Planner

新增 deterministic R3 planner，输入：

- R1.5 disputes/rulings。
- R2 changed claims 和 remaining questions。
- score threshold crossing。
- legal/finance/risk critical flags。
- evidence sufficiency。

输出：

```json
{
  "schema_version": "siq_ic_r3_plan_v1",
  "mode": "skip|short|full",
  "reason_codes": [],
  "topics": [],
  "estimated_rounds": 0,
  "requires_human_confirmation_to_skip": true
}
```

### 12.2 Debate contract

```json
{
  "debate_id": "DEB-...",
  "topic": "...",
  "red_team": [],
  "blue_team": [],
  "rounds": [
    {
      "round": 1,
      "speaker": "...",
      "argument": "...",
      "claim_ids": [],
      "evidence_ids": [],
      "responds_to_argument_ids": [],
      "unanswered_points": []
    }
  ],
  "chairman_verdict": {},
  "status": "resolved|unresolved|needs_more_evidence"
}
```

### 12.3 Skip 安全条件

正式 R3 skip 至少要求：

- `critical/high unresolved disputes = 0`。
- `critical contested claims = 0`。
- 专家 recommendation 不跨 pass/reject 两端。
- Evidence gate 和 report quality gate 均 pass。
- 风控/法务无未关闭 veto flag。
- policy 允许且保存 skip reason。

任何条件缺失时不得默认 skip。

## 13. R4 完整报告

### 13.1 报告结构

正式中文投决报告至少包含：

1. 执行摘要和决策结论。
2. 项目、轮次、拟投资结构和证据快照。
3. 证据充分度、数据限制和未验证事项。
4. 企业、产品和商业模式概况。
5. 战略与政策分析。
6. 行业、TAM/SAM/SOM、竞争和技术分析。
7. 历史财务、收入质量、现金流、预测和估值情景。
8. 法律、股权、知识产权、合规和交割风险。
9. 风险登记、压力测试、预警和止损指标。
10. R1.5 核心分歧和主席裁决。
11. R2 观点变化与评分变化。
12. R3 红蓝对抗和最终裁定。
13. 专家加权评分和主席六维评分。
14. 最终建议、前置条件、TS 保护条款和投后监控。
15. Open questions、人工确认和审计摘要。

不得出现：

- “请参见其他文件”替代正文。
- 模板占位符。
- 宿主机路径、内部 prompt 或 gateway 信息。
- 没有 Evidence 的精确财务数字。
- 将缺证自动描述为“未发现风险”。

### 13.2 Renderer

Renderer 只消费校验后的 JSON：

```text
R0 readiness
R1 reports
R1.5 disputes/rulings
R2 revisions
R3 debates
R4 decision
evidence quality
factcheck result
```

Markdown 和 HTML 必须由同一 view model 生成，避免内容漂移。

### 13.3 Factchecker

Factchecker 可以使用 `siq_factchecker`，但 deterministic gate 必须先运行。Factchecker 输出：

```json
{
  "schema_version": "siq_ic_report_factcheck_v1",
  "status": "pass|warn|fail",
  "claim_checks": [],
  "numeric_checks": [],
  "citation_checks": [],
  "contradictions": [],
  "unsupported_claims": [],
  "required_repairs": []
}
```

模型 factchecker 只能发现问题，不能静默修改结构化事实。修复必须生成新 report revision 并重新验收。

## 14. 报告质量门禁

正式 R4 进入人工确认前必须满足：

| Gate | 阻断条件 |
| --- | --- |
| Schema | 任一必需 phase/report schema 非法 |
| Identity | Deal/source/snapshot/profile identity 不一致 |
| Evidence | 未知或跨项目 Evidence ID |
| Critical claims | 决定性 claim 无 Evidence |
| Financial | 决定性数字缺 period/currency/unit/source/trace |
| Disputes | critical/high 分歧未解决却给出 pass |
| Red flags | 未关闭 veto flag 与结论冲突 |
| Scoring | score 无依据、权重错误或结果计算不一致 |
| R3 | 不符合 skip 条件却跳过 |
| Completeness | 必需章节缺失或仍有占位符 |
| Consistency | JSON、Markdown、HTML 结论或数字不一致 |
| Factcheck | factcheck fail 或 critical unsupported claim |

建议发布指标：

```text
schema compliance = 100%
unknown evidence IDs = 0
critical claim evidence coverage = 100%
decision-relevant numeric trace coverage = 100%
unresolved critical/high disputes for pass = 0
placeholder/internal path findings = 0
role boundary violations = 0
JSON/Markdown/HTML decision consistency = 100%
```

## 15. OpenClaw 同步治理

### 15.1 单向迁移

```text
OpenClaw reference
  -> inventory/diff
  -> human methodology review
  -> Hermes/shared contract change
  -> tests/golden evaluation
  -> accepted production version
```

禁止生产期自动双向同步。Hermes accepted version 不因 OpenClaw 本地文件变化自动漂移。

### 15.2 Behavior inventory v2

将现有 migration inventory 升级为：

```json
{
  "behavior_id": "r3.red_blue.full",
  "source_assets": [],
  "target_contracts": [],
  "target_services": [],
  "parity_level": "contract_migrated",
  "acceptance_tests": [],
  "golden_cases": [],
  "known_gaps": [],
  "owner": "...",
  "reviewed_at": null
}
```

迁移矩阵中的 `migrated` 需要按真实证据重新分类，尤其是 R2、R3、R4 和 final report。

### 15.3 允许迁移的资产

- AGENTS/SOUL/IDENTITY 中稳定职责和方法论。
- workflow policy、评分纪律和报告模板。
- startup protocol 的行为要求。
- dispute、R3、report generator 的方法和合同。
- 有事实依据、无占位符的高质量报告结构。

### 15.4 禁止迁移的资产

- `memory/`、历史聊天和个性化长期记忆。
- `.venv`、cache、token 和本地配置。
- 历史项目原始结论作为 profile 固定知识。
- 直接连接生产 Milvus/QCC/外部服务的 profile-local 脚本。
- 无法证明来源的评分、倍数、阈值和事实。

## 16. 黄金集与评测

### 16.1 样板筛选

OpenClaw 历史项目不能整体视为黄金集：

- `SIQ-YUSHU-IPO-2026` 的部分讨论和最终报告是占位内容，只能用于识别失败模式。
- 大金重工样板章节更完整，可用于结构和对抗流程参考，但事实必须重新绑定 SIQ Evidence 后才能成为黄金用例。
- 应另外选择至少一个证据不足、最终应 `review/insufficient_evidence` 的负向项目。

### 16.2 黄金集组成

至少包含：

1. 高质量、证据较完整、可形成 conditional support 的项目。
2. 财务或法律重大风险、应 reject/review 的项目。
3. 材料不足、应 insufficient evidence 的项目。
4. 有明显专家分歧、必须运行 full R3 的项目。
5. 新版招股书启用导致旧 receipt/report stale 的项目。

### 16.3 评测维度

```text
角色边界
Evidence 使用
claim 可验证性
专业分析深度
交叉验证
分歧保真
观点修订质量
红蓝对抗质量
评分纪律
决策条件可执行性
报告完整性
事实/数字准确性
```

模型 judge 只能作为辅助；schema、Evidence、计算和状态一致性使用确定性 evaluator。

## 17. API 设计

复用现有 `/api/deals/{deal_id}/workflow/*`，逐步增加 canonical actions：

```http
GET  /api/deals/{deal_id}/workflow/readiness
POST /api/deals/{deal_id}/workflow/run-r0
POST /api/deals/{deal_id}/workflow/run-r1a
POST /api/deals/{deal_id}/workflow/run-r1b-risk
POST /api/deals/{deal_id}/workflow/run-r1b-chairman
POST /api/deals/{deal_id}/workflow/identify-disputes
POST /api/deals/{deal_id}/workflow/run-r1-5-chairman
POST /api/deals/{deal_id}/workflow/run-r2
POST /api/deals/{deal_id}/workflow/plan-r3
POST /api/deals/{deal_id}/workflow/run-r3
POST /api/deals/{deal_id}/workflow/draft-r4
POST /api/deals/{deal_id}/workflow/validate-r4
POST /api/deals/{deal_id}/decision/human-confirm
```

所有 write/model action 支持：

```json
{
  "dry_run": true,
  "mode": "deterministic|model",
  "expected_evidence_snapshot_hash": "...",
  "overwrite": false,
  "timeout": null
}
```

规则：

- 默认 `dry_run=true` 的现有兼容语义可保留。
- `mode=model` 必须显式授权和 gateway ready。
- snapshot 不一致返回 409，不自动使用最新证据替换任务输入。
- model failure 不自动写 deterministic 正式结果；调用方可以显式选择降级。

## 18. 后端模块规划

### 18.1 建议新增

```text
apps/api/services/ic_task_contracts.py
apps/api/services/ic_report_contracts.py
apps/api/services/ic_phase_orchestrator.py
apps/api/services/ic_r3_debate.py
apps/api/services/ic_report_quality.py
apps/api/services/ic_r4_report_renderer.py
```

职责：

- `ic_task_contracts.py`：各 phase task payload、digest、schema refs。
- `ic_report_contracts.py`：通用和角色专属 report validator。
- `ic_phase_orchestrator.py`：R1A-R4 的计划、调用、lease、终态和阶段推进。
- `ic_r3_debate.py`：R3 planner、阵营、轮次和主席 verdict。
- `ic_report_quality.py`：deterministic gates、Factchecker 输入输出和阻断结论。
- `ic_r4_report_renderer.py`：共享 view model、Markdown 和 HTML。

只有在职责稳定后拆文件；不要为了缩短 `ic_agent_runtime.py` 创建空壳转发层。

### 18.2 需要修改

```text
apps/api/services/ic_agent_runtime.py
apps/api/services/ic_policy.py
apps/api/services/ic_startup_retrieval.py
apps/api/services/ic_task_lease.py
apps/api/services/deal_contracts.py
apps/api/services/deal_disputes.py
apps/api/services/deal_reports.py
apps/api/services/ic_decision_report.py
apps/api/routers/deals.py
apps/api/routers/primary_market_meeting.py
apps/api/tests/test_deals_router.py
apps/api/tests/test_primary_market_meeting_router.py
```

### 18.3 Profile 资产

```text
agents/hermes/profiles/siq_ic_shared/ic_profile_matrix.json
agents/hermes/profiles/siq_ic_shared/ic_workflow_policy.json
agents/hermes/profiles/siq_ic_shared/openclaw_asset_migration_inventory.json
agents/hermes/profiles/siq_ic_shared/openclaw_script_migration_matrix.json
agents/hermes/profiles/siq_ic_shared/contracts/*
agents/hermes/profiles/siq_ic_*/AGENTS.md
agents/hermes/profiles/siq_ic_*/SOUL.md
agents/hermes/profiles/siq_ic_*/TOOLS.md
```

Profile 修改必须与 schema/task contract 同 PR 或有明确兼容版本，防止 prompt 和 validator 漂移。

## 19. 前端产品设计

### 19.1 Readiness

每个 Agent 显示：

```text
gateway health
profile contract version
startup receipt
evidence snapshot
capability restrictions
当前 phase task status
正式 report status
quality status
stale status
```

### 19.2 Workflow 视图

按阶段展示：

- R0 准入与缺口。
- R1A 独立专家任务进度。
- R1B 风控和主席综合。
- R1.5 分歧卡片和主席裁决。
- R2 每个专家的观点/评分 delta。
- R3 topic、阵营、轮次和 verdict。
- R4 report quality、factcheck 和人工确认。

### 19.3 操作区分

```text
预演
运行模型任务
生成确定性降级产物
重新运行
人工接受 stale 结果
确认/拒绝/override 最终决策
```

确定性降级产物必须显示 `deterministic fallback`，不能和真实 Hermes report 使用相同视觉状态。

### 19.4 Report 视图

支持：

- 专家报告按角色和 phase 查看。
- R1/R2 claim 和 score delta 对比。
- Evidence 点击回源。
- 分歧双方证据并列。
- R3 时间线。
- R4 Markdown/HTML 和 quality findings。

## 20. 配置与发布开关

全局保留：

```text
SIQ_ENABLE_IC_HERMES
```

建议新增安全 kill switches：

```text
SIQ_IC_R15_MODEL_ENABLED
SIQ_IC_R2_MODEL_ENABLED
SIQ_IC_R3_MODEL_ENABLED
SIQ_IC_R4_MODEL_ENABLED
SIQ_IC_REPORT_QUALITY_BLOCKING
```

业务默认和 phase mode 放入 `ic_workflow_policy.json`，环境变量只做部署级禁用，不把所有策略散落在 env。

建议 policy：

```json
{
  "execution": {
    "r1_mode": "hybrid_dag",
    "r1_5_mode": "deterministic_detect_model_rule",
    "r2_mode": "model_with_deterministic_fallback",
    "r3_default_mode": "dynamic",
    "r4_mode": "model_decision_deterministic_render",
    "require_human_confirmation": true
  }
}
```

## 21. 可执行任务分解

### PMIC-00：重建行为迁移矩阵

范围：

- 将单一 migrated 状态升级为四级 parity。
- 为 R0-R4、retrieval、report、R3、R4 建 behavior entries。
- 明确 OpenClaw 来源、SIQ target、tests、golden cases 和 known gaps。

验收：

- R2/R3/R4 不再被错误标为 behavior/quality 完成。
- 每个关键 behavior 都有 owner 和验收测试计划。

### PMIC-01：建立 versioned shared schemas

范围：

- common claim、expert report、R0、R1、R1.5、R2、R3、R4 JSON Schema。
- agent handoff、debate turn 和 workflow run identity schema。
- profile matrix 增加 phase capabilities 和 output schema。
- 实现 Python validator 和 schema compatibility tests。

验收：

- 七个 profile 的正式输出都能映射到明确 schema。
- 非法 recommendation、score、claim status、Evidence 引用被拒绝。

### PMIC-02：升级 receipt 和 evidence snapshot

范围：

- startup receipt v2。
- task/report 保存 snapshot hash 和 source IDs。
- stale-on-completion 和 preflight stale gate。
- 接入招股书材料中心 active source。

验收：

- source 变化后旧 receipt 不能运行新正式任务。
- running task 使用旧 snapshot 完成时不自动推进。

### PMIC-03：实现 R1 hybrid DAG

范围：

- R1A 四专家独立运行。
- R1B 风控读取四报告。
- R1B 主席读取五报告。
- 为每个 task 注入正确 peer inputs，防止泄漏不应读取的结论。
- 持久化 agent handoff，保存 source report/claim/evidence refs 和 input digest。
- 所有 task 使用 lease、heartbeat、终态和审计。

验收：

- R1A 互不读取同轮结论。
- 风控能逐项引用前四专家 claim。
- 主席能识别一致、分歧和缺证。
- API/Agent 重启后能从 handoff artifact 恢复下一任务，不依赖旧 session 存活。
- 任一任务失败不会把 R1 标记 completed。

### PMIC-04：角色专属 R1 报告与 renderer

范围：

- 七角色专属字段和 validator。
- JSON-only 正式输出、一次受控 repair。
- 服务端 Markdown renderer。
- claim/Evidence 和角色边界 gate。

验收：

- 财务报告包含 trace、法律报告包含 remediation、风险报告包含 threshold。
- Markdown 与 JSON recommendation/score/claims 一致。
- 未知 Evidence 和自由文本伪造摘要失败。

### PMIC-05：真实主席 R1.5

范围：

- deterministic candidate disputes。
- chairman task builder 和 Hermes execution。
- accept/reject/synthesize/needs-more-evidence/unresolved。
- 补证回路和人工裁决兼容。

验收：

- 主席裁决保留双方 positions 和 Evidence。
- 缺证裁决不能直接关闭 critical dispute。
- 重跑默认保留已人工确认裁决。

### PMIC-06：真实专家 R2

范围：

- 五专家 R2 task。
- 输入自身 R1、相关 peer claims、裁决和新增 Evidence。
- delta report、score change、closed/remaining questions。
- deterministic R2 改名/标记为 fallback。

验收：

- 正式 R2 `hermes_called=true`。
- 每个 score change 有 claim/evidence rationale。
- 复制 R1 且无 revision rationale 的报告失败。

### PMIC-07：真实动态 R3

范围：

- R3 planner 和 skip gate。
- 动态红蓝阵营。
- short/full 多轮 Hermes arguments。
- 主席 verdict。
- deterministic R3 保留为 fallback。

验收：

- 默认不再无条件 skip。
- 每轮 argument 指向对方论点和 Evidence。
- high/critical unresolved debate 阻断 R4 pass。

### PMIC-08：真实主席 R4 与完整报告

范围：

- R4 chairman structured task。
- 六维评分、双评分解释、decision conditions。
- 中文 report view model、Markdown、HTML。
- 不再使用 R1 chairman score 作为 R4 唯一主席分数。

验收：

- 报告包含第 13 节全部章节。
- 没有“参见其他文件”空壳内容。
- JSON/Markdown/HTML 决策和数字一致。

### PMIC-09：Factchecker 与质量阻断

范围：

- deterministic report quality gate。
- `siq_factchecker` 受控调用。
- claim/numeric/citation/contradiction 检查。
- repair revision 和 revalidation。

验收：

- critical unsupported claim 阻断人工确认。
- Factchecker 不直接静默修改正式报告。
- 所有 repair 有 revision 和 audit。

### PMIC-10：会议室和 Workflow UI

范围：

- hybrid DAG readiness。
- R1.5 dispute board。
- R2 delta。
- R3 timeline。
- R4 quality/factcheck/human confirmation。
- deterministic fallback 标识。

验收：

- 用户能区分聊天、正式模型任务和确定性降级。
- 用户能定位阻断原因和下一步动作。
- 页面切换不会串写 Deal/task 状态。

### PMIC-11：黄金集、真实 smoke 与发布门禁

范围：

- 清洗 3-5 个黄金项目。
- 五类正负用例。
- fake Hermes contract tests。
- 七 profile gateway smoke。
- R0-R4 model-assisted E2E。
- 离线质量报告和 CI artifact。

验收：

- 七个 profile 至少各有一个真实成功任务。
- full R3 和 insufficient evidence 路径均被覆盖。
- 所有第 14 节发布指标达到要求。

## 22. 依赖关系

```text
PMIC-00 -> PMIC-01
PMIC-01 -> PMIC-03 -> PMIC-04
PMIC-01 + PMIC-02 + PMIC-04 -> PMIC-05
PMIC-05 -> PMIC-06
PMIC-06 -> PMIC-07
PMIC-07 -> PMIC-08
PMIC-08 -> PMIC-09
PMIC-03..09 -> PMIC-10
PMIC-00..10 -> PMIC-11
```

材料中心依赖：

```text
PMM-05 Evidence snapshot
  -> PMIC-02 receipt/snapshot
  -> PMIC-03..09 正式智能体工作流
```

没有 PMM-05 时，PMIC 可先对现有 Deal Evidence 实现 snapshot，但接口必须与材料方案保持一致。

## 23. 建议提交边界

1. `agents: classify OpenClaw IC behavior parity`
2. `agents: add versioned IC phase and report contracts`
3. `api: bind IC receipts and tasks to evidence snapshots`
4. `api: run R1 as independent research and convergence waves`
5. `api: validate and render role-specific IC reports`
6. `api: add model-backed R1.5 chairman rulings`
7. `api: add model-backed R2 expert revisions`
8. `api: add dynamic model-backed R3 debates`
9. `api: generate and validate complete R4 decision reports`
10. `web: expose IC phase evidence and quality states`
11. `tests: add IC behavior parity and quality release gates`

不要把七个 profile 全量改写、runtime 重构、前端 UI 和黄金集数据放入同一个 PR。

## 24. 测试矩阵

### 24.1 Schema/contract

- 每个 role 正向报告。
- 缺 role-specific 字段。
- recommendation/score 越界。
- verified claim 无 Evidence。
- derived claim 无 trace。
- cross Deal/source/snapshot Evidence。
- JSON 与 rendered report 一致。

### 24.2 Workflow

- R1A 并行或独立执行。
- R1B peer input 可见性。
- task lease 冲突、heartbeat、timeout、restart recovery。
- snapshot stale before claim/during run/after completion。
- partial phase failure 不推进。
- model failure 显式 fallback。

### 24.3 R1.5/R2/R3

- 相反 recommendation。
- 评分跨阈值。
- 同一数字不同 Evidence。
- needs more evidence 回路。
- R2 score delta 合理性。
- R3 skip/short/full。
- 动态阵营和主席 verdict。

### 24.4 R4/quality

- 双评分计算。
- critical dispute 阻断 pass。
- veto flag 阻断。
- financial trace 缺失。
- placeholder/internal path。
- Factchecker fail/warn/pass。
- human confirm/override/audit。

### 24.5 安全

- private Deal BOLA。
- 伪造 task/profile/report ID。
- 低权限触发 model action。
- 读取其他用户 run/audit artifact。
- prompt/path 注入不能扩大文件访问范围。

## 25. 验证命令

实施时按范围运行：

```bash
cd apps/api
uv run python -m pytest \
  tests/test_primary_market_meeting_readiness.py \
  tests/test_primary_market_meeting_router.py \
  tests/test_deals_router.py \
  tests/test_ic_agent_runtime_lease_io.py \
  tests/test_ic_task_lease.py \
  tests/test_ic_task_lease_postgres.py

cd apps/web
npm run test:unit
npm run check:frontend
```

具体测试文件以当前仓库实际命名为准；新增阶段应建立相邻定向测试，不把全部行为堆入单一巨型 test module。

跨阶段完成后运行：

```bash
scripts/check_all.sh
```

真实 Hermes 验收使用显式开关和隔离 fixture，不在普通单元测试中依赖 live model。

## 26. 发布策略

### 26.1 分阶段启用

```text
阶段 1：shadow
  真实模型运行但不推进 workflow，与 deterministic 结果对比

阶段 2：review required
  模型产物可写入，但必须人工接受后推进

阶段 3：gated production
  质量门禁通过后自动推进，R4 仍需人工确认
```

### 26.2 Kill switch

任一 model phase 出现系统性失败时：

- 关闭对应 phase model flag。
- 保留历史 run 和 audit。
- UI 显示 model unavailable。
- 用户可显式生成 deterministic fallback。
- 不把 fallback 冒充模型专家结论。

## 27. 回滚策略

| 变更 | 回滚方式 |
| --- | --- |
| 新 report schema | 保留 v1 reader，停止生成 v2，不删除 v2 artifacts |
| R1 hybrid DAG | policy 切回 strict serial compatibility mode |
| R1.5 model | 关闭 flag，保留 deterministic/manual ruling |
| R2 model | 关闭 flag，允许显式 deterministic fallback |
| R3 model | 关闭 flag，保留 planner 和人工/确定性记录 |
| R4 model | 关闭 flag，保留 deterministic decision draft，但要求人工 review |
| Factchecker blocking | 降为 warn 仅用于故障恢复，保留 findings |
| 新 renderer | 回退旧 renderer，只读保留新 report revisions |

回滚不能清除已生成的 task、report、quality、factcheck 和 human decision audit。

## 28. Codex 实施规则

后续 Codex 执行本任务书时必须：

1. 每次只实施一个 PMIC 任务或明确子任务。
2. 修改前读取 `AGENTS.md`、当前 profile、shared contracts 和相邻测试。
3. 不直接复制 OpenClaw memory、凭据、缓存或硬编码路径。
4. 对 OpenClaw 方法论先提取行为合同，再写 Hermes/服务实现。
5. 先建立 schema 和失败测试，再扩展模型运行路径。
6. 所有 Agent 正式输出必须经过 API validator 后才写 phase artifact。
7. 不让 profile 自己推进 workflow、修改 audit 或写最终 decision。
8. 保留 deterministic fallback，但在 schema、API 和 UI 中明确标识 generation mode。
9. 任何 Evidence/source/snapshot identity 不明确时 fail closed。
10. 每项完成后记录测试、真实 smoke、黄金集结果和剩余风险。

## 29. Definition of Done

“OpenClaw 已深度复刻”只有在以下条件同时成立时才成立：

```text
七个 Hermes profiles 是唯一生产角色来源
  -> 角色职责和边界有 versioned contract
  -> R0-R4 task 输入绑定当前 Evidence snapshot
  -> R1 形成独立研究和协作收敛
  -> R1.5 有真实主席裁决和补证回路
  -> R2 有真实专家观点修订
  -> R3 有可验证的红蓝交锋和主席 verdict
  -> R4 有真实主席结构化决策
  -> 服务端生成完整中文投决报告
  -> 关键 claims、数字和评分可追溯
  -> Factchecker 和 deterministic quality gate 通过
  -> 最终决策仍由人类确认
  -> 任务失败、重启、快照变化和降级均可恢复、可审计
  -> 黄金集和真实 gateway smoke 达到发布标准
```

达到以上标准后，SIQ 才不是“复制了 OpenClaw 的角色文件”，而是把 OpenClaw 的一级市场投委会制度转化成了可运行、可验证、可恢复、可持续优化的生产系统。
