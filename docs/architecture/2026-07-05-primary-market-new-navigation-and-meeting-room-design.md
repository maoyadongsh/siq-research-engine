# 一级市场新增导航与投研会议室设计文档

> 日期：2026-07-05
> 状态：方案设计 / 可交给开发窗口执行
> 适用仓库：`/home/maoyd/siq-research-engine`
> 核心约束：不改动当前已有交易工作台页面和既有后端行为，只做新增能力；已有后端可用则直接复用。

## 0. 快速结论

本轮一级市场新增开发采用“新增产品入口 + 复用现有 Deal OS 后端”的方案。

新增导航为：

```text
一级市场
  - 工作平台
  - 材料中心
  - 投研会议室
```

其中：

- 工作平台用于查看所有一级市场项目的任务状态、阻断原因、下一步动作和待人工确认事项。
- 材料中心用于按项目上传、管理、解析和构建一级市场材料证据。
- 投研会议室用于承载由总协调员自动驱动、人类可干预的 R0-R4 多智能体投委会流程。

开发方式：

1. P0 只新增前端页面和路由，优先复用 `apps/web/src/lib/dealApi.ts` 与现有 `/api/deals/*`。
2. P1 再考虑新增 `/api/primary-market/*` 聚合接口，作为现有 Deal OS service 的只读/动作包装层。
3. P2 引入真正的会议事件流、Coordinator 自动推进和阶段锁。

关键边界：

- 不替换现有 `/deals`、`/deals/:dealId` 和各子页面。
- 不改变现有 `/api/deals/*` 行为。
- 不迁移或重建当前 Milvus 大型背景知识库，先通过逻辑名映射使用已有 legacy `ic_*` collection。
- 不把一级市场项目材料默认沉淀进全局背景知识库。
- R4 投决只能生成建议和草案，最终确认、驳回或 override 必须由人完成。

## 0.1 开发禁止项

为避免与现有交易工作台和二级市场链路相互污染，本轮明确禁止：

- 禁止删除、重命名或重排当前 `交易工作台` 导航。
- 禁止把当前 `/deals/:dealId` 改造成会议室。
- 禁止让前端直接调用 Hermes gateway 端口或直接拼 Milvus collection 名称。
- 禁止绕过 API 直接写 `data/wiki/deals/<deal_id>`。
- 禁止把一级市场 data room 原始文件通过静态目录直接暴露。
- 禁止让二级市场 Agent 默认读取一级市场私密项目包。
- 禁止把未人工审核的项目讨论、项目底稿或投决过程写入共享背景知识库。

## 1. 背景与目标

SIQ Research Engine 当前已经具备一级市场 Deal OS 的基础能力，包括：

- `data/wiki/deals/<deal_id>` 项目包归档。
- `/api/deals/*` 项目、材料、证据、工作流、报告、决策和审计接口。
- `agents/hermes/profiles/siq_ic_*` 投委会 Hermes profiles。
- OpenClaw 迁移而来的 R0-R4 投委会流程和 `siq_ic_shared` 工作流政策。
- Milvus 中已有 legacy `ic_*` 智能体背景知识库。

现有“交易工作台”入口已经承载项目详情、证据、流程、报告、决策和审计等能力。本轮不改当前页面，而是在一级市场域下新增一组导航页，让用户以更接近真实投资工作的方式使用系统：

```text
一级市场
  - 工作平台
  - 材料中心
  - 投研会议室
```

本设计的核心目标是把一级市场从“项目包页面集合”升级为“可运行的投委会工作平台”：

1. 工作平台：查看所有一级市场项目和任务状态。
2. 材料中心：上传、归类、解析和构建项目材料证据。
3. 投研会议室：由总协调员自动驱动 R0-R4 投委会流程，人类在过程中干预、确认和最终决策。

## 2. 设计原则

### 2.1 Additive Only

本轮开发只新增路由、页面、组件和可选聚合接口，不替换现有页面，不重写已有 `/api/deals/*` 语义。

现有页面继续保留：

```text
/deals
/deals/:dealId
/deals/:dealId/data-room
/deals/:dealId/evidence
/deals/:dealId/agents
/deals/:dealId/workflow
/deals/:dealId/reports
/deals/:dealId/decision
/deals/:dealId/audit
```

新增页面推荐使用：

```text
/primary-market
/primary-market/materials
/primary-market/meeting
```

### 2.2 复用优先

前端优先复用现有 `apps/web/src/lib/dealApi.ts` 能力。后端优先复用现有 `apps/api/routers/deals.py` 和 `apps/api/services/deal_*` 服务。

只有当投研会议室需要更流畅的聚合状态或事件流时，才新增 `/api/primary-market/*` 作为包装层。包装层不得改变现有 Deal OS 服务的行为。

### 2.3 Coordinator-Driven Meeting

投研会议室不是“多个智能体聊天页”，而是“总协调员自动主持的投委会运行台”。

产品心智：

```text
总协调员负责排议程、点名、追问、总结和推进阶段；
人类负责监督、暂停、插话、纠偏、补充材料和最终确认。
```

用户不是逐个手动点击智能体发言，而是在同一个会议窗口中看总协调员驱动 R0-R4。智能体选择下拉仍然保留，但它是人工干预工具，不是主流程。

### 2.4 证据先于发言

所有正式专家观点都必须建立在 startup retrieval 或 round context receipt 之上。没有有效检索凭证的智能体输出只能作为草稿或人工插话，不能进入正式阶段报告。

### 2.5 Milvus 只做召回

一级市场知识组织遵循 SIQ 全局原则：

```text
Wiki / Deal Package = 权威归档与事实源
PostgreSQL = 结构化索引、状态、权限、查询
Milvus = 语义召回索引
```

Milvus 命中结果必须能回查到 Wiki 项目包、源文件、证据 ID、页码或阶段产物。

## 3. 信息架构

### 3.1 一级市场导航

新增导航组：

```text
一级市场
  工作平台
  材料中心
  投研会议室
```

建议在 `apps/web/src/app/routes.tsx` 新增路由定义，在侧边栏新增一级市场入口。不要移除或重命名当前 `交易工作台`。

### 3.2 页面职责

| 页面 | 路由 | 核心问题 |
| --- | --- | --- |
| 一级市场工作平台 | `/primary-market` | 当前一级市场项目池里有哪些任务，各自卡在哪一步？ |
| 一级市场材料中心 | `/primary-market/materials` | 某个项目的材料是否足够支撑投委会流程？ |
| 多智能体投研会议室 | `/primary-market/meeting` | 当前项目如何由总协调员驱动 R0-R4，并产出可审计投决？ |
| 现有交易工作台 | `/deals` / `/deals/:dealId` | 项目包详情、开发期管理和既有子页面入口。 |

## 4. 一级市场工作平台设计

### 4.1 定位

工作平台是一级市场项目池和任务状态总览，不是单项目详情页。

页面应该让用户一眼看到：

- 当前有哪些项目。
- 每个项目处于哪个投委会阶段。
- 哪些项目材料不足。
- 哪些项目 evidence 或 Agent readiness 阻断。
- 哪些项目等待 R4 人工确认。
- 最近发生了什么。

### 4.2 推荐布局

```text
顶部：一级市场状态概览
  项目总数 / 进行中 / 阻断中 / 待人工确认 / 已完成

主体左侧：项目列表
  公司、行业、融资阶段、当前 R 阶段、下一步动作、最终投决

主体右侧：待办与最近活动
  阻断队列、人工确认队列、最近审计事件

底部：阶段分布与风险信号
  R0-R4 项目分布、证据缺口、Agent 缺口、失败任务
```

### 4.3 数据来源

优先调用现有接口：

| 用途 | 现有接口 |
| --- | --- |
| 项目列表 | `GET /api/deals` |
| 项目状态 | `GET /api/deals/{deal_id}/status` |
| 工作流 | `GET /api/deals/{deal_id}/workflow` |
| Agent readiness | `GET /api/deals/{deal_id}/agents` |
| 最终投决 | `GET /api/deals/{deal_id}/decision` |
| 审计事件 | `GET /api/deals/{deal_id}/audit` |

P0 可以在前端并发拉取前 N 个项目的状态；P1 可新增聚合接口减少请求数。

### 4.4 状态模型

工作平台建议派生以下状态：

| 状态 | 判定来源 |
| --- | --- |
| `materials_missing` | documents 为空或 evidence missing |
| `evidence_blocked` | status component 中 evidence 为 blocking |
| `agent_blocked` | agents counts blocked > 0 |
| `workflow_ready` | ready_for_next_action = true |
| `decision_pending` | R4 已生成但 human_confirmation pending |
| `completed` | final_decision 存在且人工确认完成 |

### 4.5 主要交互

- 选择项目后跳转材料中心，并带上 `dealId` query。
- 点击“进入会议”跳转投研会议室，并带上 `dealId` query。
- 点击“查看项目包”进入现有 `/deals/:dealId`。
- 点击阻断卡片下钻到现有 evidence / workflow / agents 子页。

## 5. 一级市场材料中心设计

### 5.1 定位

材料中心是项目数据室和证据准备入口。它不只是文件上传页，而是回答：

```text
这个项目的材料是否足够开投委会？
```

### 5.2 推荐布局

```text
顶部：项目选择器 + 材料 readiness

左侧：材料类型导航
  BP / Teaser / 财务模型 / 法务材料 / 行业报告 / 访谈纪要 / 条款清单 / 其他

中间：材料列表与上传区
  文件、类型、解析状态、绑定任务、证据数量、质量告警

右侧：证据准备状态
  business / finance / legal / risk / sector / strategy / terms 覆盖情况
  R0 是否可执行
  R1 是否可启动
```

### 5.3 材料类型

建议 P0 使用枚举：

```text
teaser
bp
financial_model
audit_report
legal_doc
industry_report
interview_note
term_sheet
meeting_note
other
```

### 5.4 证据维度

建议 P0 使用：

```text
business
finance
legal
risk
sector
strategy
team
terms
```

### 5.5 数据来源

复用现有接口：

| 用途 | 现有接口 |
| --- | --- |
| 项目列表 | `GET /api/deals` |
| 材料列表 | `GET /api/deals/{deal_id}/documents` |
| 上传材料 | `POST /api/deals/{deal_id}/documents` |
| 删除材料 | `DELETE /api/deals/{deal_id}/documents/{document_id}` |
| 绑定解析任务 | `POST /api/deals/{deal_id}/documents/{document_id}/bind-parser-task` |
| 证据列表 | `GET /api/deals/{deal_id}/evidence` |
| 构建证据 | `POST /api/deals/{deal_id}/evidence/build` |

### 5.6 项目包落盘路径

权威归档仍然使用：

```text
data/wiki/deals/<deal_id>/
  data_room/
    raw/
    metadata/
  parsed_documents/
  evidence/
```

材料中心不直接绕过 API 写文件；前端只调用 API，由后端负责写入项目包。

## 6. 投研会议室设计

### 6.1 定位

投研会议室是一级市场模块的核心页面。

它模拟真实投委会，但不是自由聊天工具。它把“对话体验”和“流程状态机”结合：

- 用户看到的是一场会议。
- 后端沉淀的是 R0-R4 阶段产物。
- 每个正式结论都能回到 evidence、receipt、agent report、audit。

### 6.2 页面布局

```text
┌───────────────────────────────────────────────────────────┐
│ 顶部：项目选择 / 当前阶段 / 会议状态 / 模式 / 操作按钮       │
├───────────────┬────────────────────────────┬──────────────┤
│ 左侧议程       │ 中间会议实录                │ 右侧上下文    │
│ R0-R4          │ Coordinator / Agent / Human │ Evidence      │
│ 阶段状态        │ 多智能体发言流               │ Receipts      │
│ 下一步动作      │ 人类干预输入框               │ Artifacts     │
│ 阻断原因        │                              │ Decision      │
└───────────────┴────────────────────────────┴──────────────┘
```

### 6.3 左侧议程

阶段固定为：

```text
R0 信息校验
R1 专家首轮
R1.5 分歧识别
R2 观点修订
R3 红蓝对抗
R4 投决草案
人工确认
```

每个阶段展示：

- 状态：`pending` / `ready` / `running` / `blocked` / `completed` / `needs_human`
- 当前 active agent。
- 阻断原因。
- 产物是否存在。
- 进入下一阶段条件。

### 6.4 中间会议实录

会议实录不直接等同于原始聊天记录，而是由结构化事件渲染。

消息类型建议：

```text
coordinator_instruction
agent_speech
human_intervention
phase_summary
dispute_detected
chairman_ruling
decision_draft
system_blocking
artifact_written
```

事件最小合同：

```json
{
  "event_id": "evt_...",
  "deal_id": "DEAL-YUSHU-2026-001",
  "phase": "R1",
  "type": "agent_speech",
  "agent_id": "siq_ic_finance_auditor",
  "role_label": "财务审计委员",
  "content": "...",
  "summary": "...",
  "score": 83,
  "recommendation": "SUPPORT",
  "evidence_ids": ["EVID-..."],
  "receipt_id": "startup-...",
  "artifact_path": "phases/r1_reports.json",
  "created_at": "2026-07-05T12:00:00+08:00"
}
```

P0 可以从现有 report、workflow、audit 产物拼装事件；P2 再引入真实 SSE。

### 6.5 右侧上下文

右侧面板建议分 tab：

```text
证据
Receipt
阶段产物
评分
投决
审计
```

右侧必须支持从会议发言回跳到：

- evidence item。
- source document。
- parser artifact。
- phase JSON。
- report markdown。

### 6.6 主流程按钮

主流程按钮由总协调员状态决定：

```text
启动 R0
确认进入 R1
继续自动推进
暂停会议
识别分歧
进入 R2
规划 R3
启动 R3
生成 R4 投决草案
人工确认
驳回
Override
```

按钮必须明确分级：

| 级别 | 示例 | UI 要求 |
| --- | --- | --- |
| 只读 | 查看 evidence、查看报告 | 普通按钮 |
| Preview | R2 dry-run、R4 dry-run | 次级按钮 |
| Deterministic write | 写入 R2/R3/R4 兜底产物 | 必须确认 |
| Model run | R1 串行模型运行 | 显示耗时、gateway 状态、确认弹窗 |
| Final human action | R4 确认、驳回、override | 必须填写操作者和理由 |

### 6.7 人类干预

底部输入框不是普通聊天框，而是“人类干预入口”。

支持：

```text
暂停会议
继续自动推进
点名智能体
追问某个观点
要求引用证据
要求重写结论
要求补充材料
要求总协调员总结
要求进入下一阶段
```

智能体下拉保留：

```text
总协调员
投委会主席
战略专家
行业专家
财务审计委员
法务合规委员
风险管理委员
```

但点名请求需要经过 Coordinator 判断。若不符合当前阶段，应作为人工插话记录，不直接写正式阶段报告。

## 7. R0-R4 编排规则

### 7.1 R0 信息校验

职责：

- 校验项目基本信息。
- 扫描材料完整性。
- 构建 evidence readiness。
- 生成 R0 intake artifact。
- 判断是否允许进入 R1。

产物：

```text
phases/r0_intake.json
discussion/00_项目信息_R0.md
```

### 7.2 R1 专家首轮

R1 固定严格串行：

```text
siq_ic_strategist
siq_ic_sector_expert
siq_ic_finance_auditor
siq_ic_legal_scanner
siq_ic_risk_controller
siq_ic_chairman
```

每位专家发言前必须有 startup receipt。

产物：

```text
phases/startup_receipts.json
phases/r1_reports.json
discussion/01_R1_尽调汇总.md
```

### 7.3 R1.5 分歧识别与主席裁决

职责：

- 识别评分差异、建议冲突、风险判断冲突、估值口径冲突。
- 生成结构化争议。
- 由主席裁决或由 deterministic 规则生成裁决草案。
- 人类可确认、驳回或要求补充讨论。

产物：

```text
phases/r1_5_disputes.json
discussion/02_R1.5_裁决记录.md
```

### 7.4 R2 观点修订

职责：

- 专家基于 R1.5 裁决修订观点。
- 明确与 R1 的变化。
- 说明评分调整理由。

产物：

```text
phases/r2_reports.json
discussion/03_R2_观点完善汇总.md
```

### 7.5 R3 红蓝对抗

R3 根据 R1.5/R2 争议动态决定：

```text
skip  无争议或争议已解决
short 少量争议，压缩对抗
full  存在重大未解决争议
```

产物：

```text
phases/r3_reports.json
discussion/04_R3_红蓝对抗.md
```

### 7.6 R4 投决草案与人工确认

职责：

- 计算角色权重加权分。
- 保留主席六维评分。
- 生成最终投决草案。
- 人类确认、驳回或 override。

产物：

```text
phases/r4_decision.json
decision/decision_payload.json
decision/IC_DECISION_REPORT.md
decision/IC_DECISION_REPORT.html
discussion/05_最终投决报告.md
```

## 8. 知识库与存档设计

### 8.1 权威项目包

一级市场项目权威归档源：

```text
data/wiki/deals/<deal_id>/
  project_meta.json
  manifest.json
  artifact_map.json
  data_room/
    raw/
    metadata/
  parsed_documents/
    <document_task_id>/
      manifest.json
      document.md
      document_full.json
      source_map.json
      quality_report.json
  evidence/
    evidence_index.json
    evidence_items.ndjson
    evidence_quality_report.json
    retrieval_receipts.json
  phases/
    workflow_state.json
    startup_receipts.json
    round_context_receipts.json
    r0_intake.json
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
    decision_payload.json
  audit/
    audit_log.json
    archive_manifest.json
```

### 8.2 Milvus 当前实际状态

当前本机 Milvus `127.0.0.1:19530` 已存在 legacy OpenClaw collection：

| Collection | 实体数 | 用途 |
| --- | ---: | --- |
| `ic_collaboration_shared` | 2,193 | 共享项目底稿库 |
| `ic_chairman` | 1,599 | 主席背景知识 |
| `ic_strategist` | 985 | 战略/政策背景知识 |
| `ic_sector_expert` | 3,750 | 行业背景知识 |
| `ic_finance_auditor` | 1,335 | 财务/估值背景知识 |
| `ic_legal_scanner` | 198,780 | 法律法规库 |
| `ic_risk_controller` | 1,205 | 风险预警资料 |
| `ic_master_coordinator` | 0 | 空库，协调员无私有库 |
| `ic_archive_sop` | 0 | 空库 |

当前 `siq_ic_*` 物理 collection 尚不存在。短期不迁移大库，通过兼容映射使用 legacy collection。

### 8.3 Collection 逻辑映射

开发层统一使用 SIQ 逻辑名：

```text
siq_deal_shared          -> ic_collaboration_shared
siq_ic_chairman          -> ic_chairman
siq_ic_strategist        -> ic_strategist
siq_ic_sector_expert     -> ic_sector_expert
siq_ic_finance_auditor   -> ic_finance_auditor
siq_ic_legal_scanner     -> ic_legal_scanner
siq_ic_risk_controller   -> ic_risk_controller
siq_ic_master_coordinator -> ic_master_coordinator
```

当前仓库已有兼容映射：

```text
scripts/vector-index/milvus-ingestion/scripts/runtime_compat.py
```

新增开发应复用该映射，不要在前端或业务代码里硬编码 legacy collection。

### 8.4 检索策略

每位专家发言前检索三类材料：

```text
1. 当前项目证据：siq_deal_shared where deal_id/project_tag = 当前项目
2. 角色私有知识：siq_ic_<role>
3. 共享投委会知识：未来 siq_ic_shared_knowledge
```

P0 若 `siq_ic_shared_knowledge` 不存在，可先跳过并在 receipt warning 中记录。

### 8.5 Startup Receipt

receipt 必须保存到：

```text
data/wiki/deals/<deal_id>/phases/startup_receipts.json
```

推荐合同：

```json
{
  "receipt_id": "startup-siq_ic_finance_auditor-R1-001",
  "deal_id": "DEAL-YUSHU-2026-001",
  "agent_id": "siq_ic_finance_auditor",
  "round_name": "R1",
  "retrieval_mode": "hybrid_vector_v1",
  "collections": ["siq_deal_shared", "siq_ic_finance_auditor"],
  "physical_collections": ["ic_collaboration_shared", "ic_finance_auditor"],
  "queries": [],
  "hits": [],
  "evidence_ids": [],
  "source_paths": [],
  "embedding_version": "Qwen3-VL-Embedding-2B",
  "gate": {
    "allowed_to_speak": true,
    "blocking_reasons": [],
    "warnings": []
  },
  "created_at": "2026-07-05T12:00:00+08:00"
}
```

## 9. API 复用与可选新增接口

### 9.1 P0 直接复用接口

| 能力 | 接口 |
| --- | --- |
| 项目列表 | `GET /api/deals` |
| 项目详情 | `GET /api/deals/{deal_id}` |
| 状态聚合 | `GET /api/deals/{deal_id}/status` |
| 材料管理 | `GET/POST /api/deals/{deal_id}/documents` |
| 绑定解析 | `POST /api/deals/{deal_id}/documents/{document_id}/bind-parser-task` |
| 证据 | `GET /api/deals/{deal_id}/evidence` |
| 构建证据 | `POST /api/deals/{deal_id}/evidence/build` |
| Agent readiness | `GET /api/deals/{deal_id}/agents` |
| startup retrieval | `POST /api/deals/{deal_id}/agents/{profile_id}/startup-retrieval` |
| 工作流 | `GET /api/deals/{deal_id}/workflow` |
| R1 串行 | `POST /api/deals/{deal_id}/workflow/run-r1-serial` |
| R1.5 分歧 | `POST /api/deals/{deal_id}/workflow/identify-disputes` |
| R2 | `POST /api/deals/{deal_id}/workflow/run-r2` |
| R3 | `POST /api/deals/{deal_id}/workflow/run-r3` |
| R4 | `POST /api/deals/{deal_id}/workflow/finalize-r4` |
| 决策 | `GET /api/deals/{deal_id}/decision` |
| 人工确认 | `POST /api/deals/{deal_id}/decision/human-confirmation` |
| 审计 | `GET /api/deals/{deal_id}/audit` |

### 9.2 P1/P2 可选新增聚合接口

如果前端并发请求过多或会议室需要事件流，可新增：

```text
GET  /api/primary-market/projects
GET  /api/primary-market/projects/{deal_id}/dashboard
GET  /api/primary-market/projects/{deal_id}/meeting-state
POST /api/primary-market/projects/{deal_id}/meeting/actions
GET  /api/primary-market/projects/{deal_id}/meeting/events
```

这些接口只做编排聚合：

- 调用现有 `deal_*` services。
- 不改变现有 `/api/deals/*`。
- 不绕过现有权限和审计。
- 所有写入仍由 Deal OS 服务层完成。

## 10. 前端开发建议

### 10.1 新增文件建议

```text
apps/web/src/pages/PrimaryMarketWorkbench.tsx
apps/web/src/pages/PrimaryMarketMaterials.tsx
apps/web/src/pages/PrimaryMarketMeeting.tsx
apps/web/src/features/primary-market/
  primaryMarketApi.ts
  primaryMarketTypes.ts
  primaryMarketViewModel.ts
  meetingEvents.ts
  meetingAgenda.ts
```

### 10.2 UI 组件建议

```text
PrimaryMarketProjectSelector
PrimaryMarketStatusStrip
PrimaryMarketTaskQueue
PrimaryMarketMaterialUploader
PrimaryMarketEvidenceReadiness
MeetingAgendaRail
MeetingTranscript
MeetingContextPanel
MeetingActionBar
AgentSpeakerBadge
ReceiptSummaryCard
DecisionDraftPanel
```

### 10.3 设计注意事项

- 不做营销式 landing page。
- 工作平台采用密度适中的操作台布局。
- 会议室以可读性和状态清晰优先。
- 所有会写入或调用模型的按钮必须清楚标识风险。
- 不在会议室里放“解释这个页面怎么用”的大段说明。
- 按钮使用图标 + 简短文本，图标优先使用 lucide-react。

## 11. 后端开发建议

### 11.1 P0

P0 不要求新增后端。前端通过现有 `dealApi.ts` 聚合。

### 11.2 P1

新增只读聚合服务：

```text
apps/api/routers/primary_market.py
apps/api/services/primary_market_dashboard.py
apps/api/services/primary_market_meeting.py
```

职责：

- 聚合项目状态。
- 聚合会议状态。
- 从 workflow/report/audit 拼装会议事件。

### 11.3 P2

新增 action runner 和事件流：

```text
POST /api/primary-market/projects/{deal_id}/meeting/actions
GET  /api/primary-market/projects/{deal_id}/meeting/events
```

Action 类型：

```text
start_r0
confirm_r0
start_r1_serial
pause_meeting
resume_meeting
identify_disputes
confirm_ruling
run_r2
plan_r3
run_r3
finalize_r4
human_confirm
human_reject
human_override
human_intervention
```

## 12. 开发阶段

### P0：新增入口与只读/半交互骨架

目标：

- 新增一级市场三页。
- 不改现有页面和后端。
- 复用现有 `/api/deals/*`。

任务：

1. 新增导航与路由。
2. 实现工作平台项目状态总览。
3. 实现材料中心项目选择、材料列表、上传、构建 evidence。
4. 实现投研会议室三栏骨架。
5. 会议室从 workflow、reports、decision、audit 拼装静态会议流。

验收：

- 当前 `/deals` 页面不受影响。
- 新入口能看到已有 deal。
- 材料中心可上传并构建 evidence。
- 会议室能展示 R0-R4 进度和已有阶段产物。

### P1：会议室接入真实操作

目标：

- 在会议室内执行已有 workflow actions。
- 支持人类干预记录。
- 显示 startup receipt 和 agent readiness。

任务：

1. 接入 startup retrieval。
2. 接入 R1 serial dry-run / run。
3. 接入 R1.5 dispute preview / write。
4. 接入 R2/R3/R4 preview / write。
5. 接入 human confirmation。
6. 新增可选聚合接口减少前端请求。

验收：

- 能在会议室内完成 R1-R4 的现有动作。
- 每次动作后会议流刷新。
- 写入动作有确认和审计反馈。

### P2：Coordinator 自动推进与事件流

目标：

- 形成真正的投委会运行台。
- 支持总协调员自动推进。
- 支持 SSE 事件流。

任务：

1. 新增 meeting action state machine。
2. 新增 event store 或从 audit 派生事件。
3. 接入 Hermes coordinator model run。
4. 支持 pause/resume/cancel。
5. 支持失败恢复和阶段锁。

验收：

- 用户点击“继续自动推进”后，Coordinator 可按规则推进下一步。
- 失败可重试，不静默覆盖已成功产物。
- 人工确认仍是最终投决必要环节。

## 13. 验收标准

功能验收：

- 新增一级市场导航三页。
- 不影响现有交易工作台。
- 工作平台能展示所有一级市场项目状态。
- 材料中心能完成上传、绑定解析、构建 evidence。
- 投研会议室能展示并推进 R0-R4。
- 所有正式输出可回到 `data/wiki/deals/<deal_id>`。

知识库验收：

- 前端显示 `siq_ic_*`。
- 底层可通过 alias 使用已有 `ic_*` collection。
- startup receipt 记录逻辑 collection 和物理 collection。
- Milvus 命中可回查 source/evidence。

流程验收：

- R1 按固定顺序。
- 无 receipt 不允许正式发言。
- R1.5 不可跳过。
- R4 必须有人类确认或 override。

安全验收：

- 不把一级市场项目材料默认写入全局背景知识库。
- 不让二级市场 Agent 默认访问一级市场私密项目包。
- 原始 data room 文件不通过静态文件直接暴露。
- 人工 override 必须记录理由和审计事件。

## 14. 推荐先做的最小实现

最小可交付版本：

1. 新增 `/primary-market` 工作平台。
2. 新增 `/primary-market/materials` 材料中心。
3. 新增 `/primary-market/meeting` 会议室骨架。
4. 会议室先只读展示 R0-R4、报告和投决。
5. 材料中心复用现有上传和构建 evidence。
6. 所有写入动作暂时跳转现有 `/deals/:dealId/workflow` 完成。

这样可以在不碰现有功能的前提下，先建立一级市场产品入口和交互心智。随后再逐步把 workflow actions 内嵌到会议室。
