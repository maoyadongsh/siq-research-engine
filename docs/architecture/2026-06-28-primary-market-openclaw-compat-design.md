# SIQ 一级市场投委会智能体设计方案

> 日期：2026-06-28
> 更新：2026-07-03
> 状态：设计方案 / 可落地任务书
> 目标：在 SIQ Research Engine 中引入一级市场投研决策能力，第一阶段保真复刻 `/home/maoyd/.openclaw/workspace` 中的 OpenClaw 多智能体投委会系统，并以 `siq_ic_*` Hermes profiles 独立维护。

## 0. 本轮深度检查结论

本方案原始方向正确：一级市场应独立于二级市场 `companies` 研究链路，采用 `Deal Project -> Evidence Package -> R0-R4 IC Workflow -> Decision Archive` 的业务边界。

但原方案还缺 6 个会影响落地的关键约束，本次更新已补齐：

1. **Profile 命名与发现机制**：用户要求 profile ID 直接采用 `siq_ic_xxx`。现有 SIQ Hermes 运行时只硬编码了 5 个二级市场 profile，需要新增 profile registry、端口、环境变量和启动脚本支持，否则目录建好也无法日常调用。
2. **OpenClaw 冻结规则**：OpenClaw 的单一事实源是 `ic_master_coordinator_workspace/config/siq_workflow_policy.json`。P0 必须迁移其中的角色权重、阈值、证据门禁、阶段超时和主席六维评分配置。
3. **R1 调度方式**：OpenClaw Coordinator 明确 R1 严格串行：`strategist -> sector -> finance -> legal -> risk -> chairman`，且每位专家发言前必须完成共享底稿库 + 私有知识库 + workspace 规则学习。P0 不做并行 R1。
4. **项目包兼容层**：OpenClaw 真实 golden path 项目 `SIQ-YUSHU-2026-002` 已形成 `project_meta.json / phases/*.json / discussion/*.md / 40_decision/IC_DECISION_REPORT.md` 合同。SIQ 应以该合同为迁移输入，同时落到 `data/wiki/deals/<deal_id>` 的新版目录。
5. **评分语义**：OpenClaw 同时有 agent 权重加权和主席六维阶段权重。实现时不得把二者混为一个机械评分器。最终结果应保留 `weighted_agent_score`、`chairman_dimension_score`、`chairman_qualitative_decision` 三类字段。
6. **人类最终控制**：系统输出是“投委会建议”，不是自动投资指令。R4 之后仍需要人工确认或 override，并写入审计链。

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

P0 阶段目标是建立 `SIQ IC Compatibility Layer`：

1. 在 SIQ 中新增一级市场 `Deal OS / 投委会` 业务域。
2. 在 `agents/hermes/profiles` 下新增并独立维护 7 个 `siq_ic_*` Hermes profiles。
3. 复刻 OpenClaw 的 7 个一级市场投委会 Agent。
4. 复刻 R0-R4 工作流、关键数据合同、证据门禁、审计链和评分阈值。
5. 复用 SIQ 的通用文档解析、Wiki、PostgreSQL、Milvus、API 鉴权、Hermes 网关和 Web 工作台。
6. 形成可在前端操作、可审计、可归档的一级市场投决项目包。

### 2.2 非目标

P0 阶段暂不做：

- 不优化或重写 OpenClaw 的 Agent 角色分工。
- 不新增技术尽调、客户验证、投后管理等新 Agent。
- 不为每个项目创建独立 profile 或 agent clone。
- 不引入复杂团队权限、多租户隔离或外部 CRM。
- 不承诺完全自动投资决策。
- 不把一级市场结论混入二级市场的“买入/卖出/持有”研究语言。
- 不让二级市场 Agent 默认访问一级市场私密数据。

## 3. 总体架构

新增一级市场业务域：

```text
数据室材料 / Teaser / BP / 财务模型 / 法务材料 / 访谈纪要 / URL
  -> apps/document-parser 通用解析
  -> deal evidence package
  -> R0 项目准入与证据门禁
  -> R1 专家串行尽调
  -> R1.5 分歧识别与主席裁决
  -> R2 观点完善
  -> R3 红蓝对抗或留痕跳过
  -> R4 投决报告与审计归档
  -> Web 工作台展示与人工复核
```

推荐模块边界：

```text
apps/web
  -> /deals 一级市场工作台

apps/api
  -> /api/deals/* 项目、证据、工作流、报告、审计
  -> Hermes profile registry 扩展，支持 siq_ic_* 日常调用

apps/document-parser
  -> 数据室材料解析和 artifact 生成

agents/hermes/profiles/siq_ic_*
  -> OpenClaw IC Agent 复刻

agents/hermes/profiles/siq_ic_shared
  -> 投委会共享 policy、report contract、evidence contract、工具说明

data/wiki/deals/<deal_id>
  -> 一级市场项目事实源和归档源

db/ddl + db/imports
  -> 一级市场 PostgreSQL 索引和导入

scripts/vector-index/milvus-ingestion
  -> deal evidence chunks 入 Milvus
```

### 3.1 运行时调用链

```text
Web /deals workflow button
  -> apps/api/routers/deals.py
  -> apps/api/services/ic_workflow.py
  -> apps/api/services/ic_agent_runtime.py
  -> services/hermes_client.py profile="siq_ic_<role>"
  -> Hermes gateway for the profile
  -> profile reads siq_ic_shared policy + deal package + retrieval receipts
  -> writes phase artifact
  -> ic_audit.py records event
```

## 4. Hermes Profiles 架构设计

用户明确要求：OpenClaw 相关智能体在 `/home/maoyd/siq-research-engine/agents/hermes/profiles` 下单独设立，但 profile ID 直接使用 `siq_ic_xxx`。

### 4.1 推荐目录

```text
agents/hermes/profiles/
  siq_assistant/
  siq_analysis/
  siq_factchecker/
  siq_tracking/
  siq_legal/
  shared/

  siq_ic_shared/
    README.md
    config.yaml.example
    ic_workflow_policy.json
    ic_report_contract.md
    ic_evidence_contract.md
    ic_prompt_contract.md
    ic_profile_matrix.json
    tools/
      README.md
      startup_retrieval_contract.md
      deal_package_contract.md
    templates/
      r1_agent_report.md
      r1_5_dispute_record.md
      r2_revision_report.md
      r3_red_blue_report.md
      r4_decision_report.md

  siq_ic_master_coordinator/
    config.yaml
    SOUL.md
    AGENTS.md
    IDENTITY.md
    TOOLS.md
    README.md
    rules/
      coordinator_protocol.md
      r0_gate.md
      r1_serial_dispatch.md
    templates/
      r0_intake_report.md
      workflow_summary.md

  siq_ic_chairman/
    config.yaml
    SOUL.md
    AGENTS.md
    IDENTITY.md
    TOOLS.md
    README.md
    rules/
      chairman_scoring.md
      dispute_ruling.md
      final_decision.md
    templates/
      chairman_ruling.md
      ic_decision_report.md

  siq_ic_strategist/
    config.yaml
    SOUL.md
    AGENTS.md
    IDENTITY.md
    TOOLS.md
    README.md
    rules/
      macro_strategy_scope.md

  siq_ic_sector_expert/
    config.yaml
    SOUL.md
    AGENTS.md
    IDENTITY.md
    TOOLS.md
    README.md
    rules/
      sector_scope.md

  siq_ic_finance_auditor/
    config.yaml
    SOUL.md
    AGENTS.md
    IDENTITY.md
    TOOLS.md
    README.md
    rules/
      valuation_scope.md
      financial_red_flags.md

  siq_ic_legal_scanner/
    config.yaml
    SOUL.md
    AGENTS.md
    IDENTITY.md
    TOOLS.md
    README.md
    rules/
      legal_scope.md
      ts_clause_review.md

  siq_ic_risk_controller/
    config.yaml
    SOUL.md
    AGENTS.md
    IDENTITY.md
    TOOLS.md
    README.md
    rules/
      risk_scope.md
      red_blue_protocol.md
```

### 4.2 为什么不用二级目录 `profiles/openclaw/...`

现有运行时默认按 `profiles/<profile_id>/config.yaml` 发现 profile。若使用 `profiles/openclaw/siq_ic_xxx/config.yaml`，需要修改更多路径解析和模型同步逻辑。

因此 P0 推荐平铺命名空间：

```text
profiles/siq_ic_master_coordinator
profiles/siq_ic_chairman
profiles/siq_ic_strategist
...
profiles/siq_ic_shared
```

隔离能力通过以下方式实现：

- `siq_ic_*` 统一前缀。
- `siq_ic_shared` 作为投委会共享配置目录。
- `agents/hermes/profiles/manifest.json` 增加 `groups.ic`。
- API profile registry 增加 `domain="primary_market"` 元数据。
- Web 只在 `/deals` 域展示 `siq_ic_*`。

### 4.3 Profile 命名映射

| OpenClaw Agent ID | SIQ Hermes Profile | 日常 alias | 角色 |
| --- | --- | --- | --- |
| `ic_master_coordinator` | `siq_ic_master_coordinator` | `ic_master` / `ic_coordinator` | 投委会秘书 / 协调者 |
| `ic_chairman` | `siq_ic_chairman` | `ic_chairman` | 投委会主席 |
| `ic_strategist` | `siq_ic_strategist` | `ic_strategy` | 宏观战略专家 |
| `ic_sector_expert` | `siq_ic_sector_expert` | `ic_sector` | 行业专家 |
| `ic_finance_auditor` | `siq_ic_finance_auditor` | `ic_finance` | 财务专家 |
| `ic_legal_scanner` | `siq_ic_legal_scanner` | `ic_legal` | 法务专家 |
| `ic_risk_controller` | `siq_ic_risk_controller` | `ic_risk` | 风控专家 |

### 4.4 Profile 内部文件职责

| 文件 | 必须性 | 职责 |
| --- | --- | --- |
| `config.yaml` | 必须 | Hermes 模型、工具、gateway 端口、禁用工具、timeout |
| `SOUL.md` | 必须 | 角色心智、工作原则、红线、输出边界 |
| `AGENTS.md` | 必须 | 调度协议、检索协议、阶段职责、可执行规则 |
| `IDENTITY.md` | 推荐 | 角色定位简表，便于人工维护 |
| `TOOLS.md` | 推荐 | 可用 SIQ API/Hermes tool 映射，不写真实密钥 |
| `README.md` | 必须 | 日常启动、调用、维护和测试说明 |
| `rules/*.md` | 推荐 | 复杂规则拆分，减少 SOUL.md 膨胀 |
| `templates/*.md` | 推荐 | 阶段报告模板，供 API/report builder 使用 |

### 4.5 共享配置单一事实源

`siq_ic_shared/ic_workflow_policy.json` 必须由 OpenClaw 的 `ic_master_coordinator_workspace/config/siq_workflow_policy.json` 迁移而来，P0 不得改语义。

必须包含：

```json
{
  "version": "2026-04-13-siq-port",
  "workflow": {
    "name": "SIQ Investment Committee",
    "decision_mode": "fixed_v2",
    "phases": ["R0", "R1", "R1.5", "R2", "R3", "R4"],
    "discussion_rounds": [1, 2, 3],
    "stage_timeouts_minutes": {
      "R0": 10,
      "R1": 30,
      "R1.5": 10,
      "R2": 20,
      "R3": 20,
      "R4": 10
    }
  },
  "weights": {
    "chairman": 0.3,
    "strategy": 0.15,
    "sector": 0.15,
    "finance": 0.15,
    "risk": 0.15,
    "legal": 0.1
  },
  "thresholds": {
    "pass": 70,
    "review_min": 68,
    "review_max": 69
  },
  "evidence_gate": {
    "required_verified_items": 3,
    "required_dimensions": ["business", "finance", "legal", "risk"],
    "max_unresolved_disputes": 0,
    "min_expert_reports": 5,
    "required_report_fields": ["score", "recommendation"],
    "required_report_metadata": ["verified", "assumed", "open_questions"]
  },
  "chairman_scoring": {
    "method": "six_dimension_weighted",
    "dimension_scale": "0-10",
    "output_scale": "0-100"
  }
}
```

### 4.6 Hermes Runtime 需要扩展的代码点

仅创建目录不足以日常调用。后续开发必须同步扩展以下文件：

```text
apps/api/services/hermes_client.py
  - SIQ_HERMES_DEFAULT_PORTS 增加 siq_ic_* 端口
  - HERMES_COMPAT_PORTS 可选增加兼容端口
  - HERMES_PROFILE_ALIASES 增加 ic_* alias
  - HERMES_ENV_PREFIXES 增加 IC_MASTER / IC_CHAIRMAN 等
  - HERMES_PROFILE_MODELS 增加 siq_ic_*
  - HermesProfile Literal 和 HERMES_PROFILE_ORDER 增加 siq_ic_*

apps/api/services/path_config.py
  - HERMES_PROFILE_ROOTS 增加 7 个 siq_ic_* root
  - 支持 SIQ_HERMES_IC_*_PROFILE_ROOT env override

apps/api/services/hermes_model_control.py
  - PROFILE_CONFIGS 增加 siq_ic_* config.yaml
  - PROFILE_LABELS 增加中文名
  - 模型切换同步覆盖 siq_ic_* 或按 domain 控制

scripts/hermes/profile_dir.sh
  - 支持 siq_ic_* 和 ic_* alias
  - usage 文案更新

scripts/hermes/run_gateway.sh
  - 支持 siq_ic_* 和 ic_* alias

start_all.sh
  - P0 不默认启动 7 个 IC gateway，避免本地资源压力
  - 增加 SIQ_ENABLE_IC_HERMES=1 时启动 IC profiles
  - 增加端口占用检查和健康检查

scripts/ops/health_check.py
  - SIQ_ENABLE_IC_HERMES=1 时检查 IC gateway

agents/hermes/README.md
  - 增加一级市场投委会 profile 矩阵

agents/hermes/profiles/manifest.json
  - 增加 groups.ic 和 profiles 列表
```

### 4.7 推荐端口段和环境变量

P0 推荐使用 18660-18666，不占用现有 18642/18649/18650/18651/18652。

| Profile | 默认端口 | 环境变量前缀 |
| --- | ---: | --- |
| `siq_ic_master_coordinator` | 18660 | `SIQ_HERMES_IC_MASTER_*` |
| `siq_ic_chairman` | 18661 | `SIQ_HERMES_IC_CHAIRMAN_*` |
| `siq_ic_strategist` | 18662 | `SIQ_HERMES_IC_STRATEGIST_*` |
| `siq_ic_sector_expert` | 18663 | `SIQ_HERMES_IC_SECTOR_*` |
| `siq_ic_finance_auditor` | 18664 | `SIQ_HERMES_IC_FINANCE_*` |
| `siq_ic_legal_scanner` | 18665 | `SIQ_HERMES_IC_LEGAL_*` |
| `siq_ic_risk_controller` | 18666 | `SIQ_HERMES_IC_RISK_*` |

可选兼容端口：8660-8666，仅在 `SIQ_HERMES_ALLOW_COMPAT_PORTS=1` 时启用。

### 4.8 Profile config.yaml 基线

7 个 IC profile 的 `config.yaml` 可以先从 `siq_analysis/config.yaml` 复制后收敛，但必须调整：

```yaml
model:
  default: MiniMax-M3
  provider: minimax-cn

toolsets:
  - terminal
  - file
  - code_execution
  - web

agent:
  max_turns: 80
  gateway_timeout: 1800
  tool_use_enforcement: true
  disabled_toolsets:
    - browser
    - skills
    - memory
    - session_search
    - todo
    - cronjob

terminal:
  backend: local
  cwd: /home/maoyd/siq-research-engine
  timeout: 300
  persistent_shell: true

security:
  redact_secrets: true
  command_approval: manual

approvals:
  mode: 'off'

platforms:
  api_server:
    enabled: true
    extra:
      host: 127.0.0.1
      port: 18660
      model_name: siq_ic_master_coordinator
```

各 profile 只改 `port` 和 `model_name`。P0 不在 profile 中写生产密钥。

### 4.9 日常调用方式

CLI：

```bash
scripts/hermes/run_gateway.sh siq_ic_master_coordinator
scripts/hermes/run_gateway.sh ic_chairman
```

API：

```python
await create_run(
    input=prompt,
    conversation_history=[],
    profile="siq_ic_finance_auditor",
    session_id=f"deal:{deal_id}:R1:siq_ic_finance_auditor"
)
```

HTTP：

```bash
curl -s http://127.0.0.1:18664/v1/runs \
  -H "Authorization: Bearer $HERMES_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"siq_ic_finance_auditor","input":"..."}'
```

Deal OS 后端不应让前端直接拼 Hermes 端口。前端只调用 `/api/deals/{deal_id}/workflow/run-r1-agent`，由 API 按 workflow policy 选择 profile。

## 5. 业务对象模型

### 5.1 Deal Project

一级市场的核心对象是 `deal_project`，不是上市公司。

核心字段：

| 字段 | 说明 |
| --- | --- |
| `deal_id` | 项目唯一 ID，例如 `DEAL-YUSHU-2026-001` |
| `legacy_project_id` | 可选，OpenClaw 原项目 ID，例如 `SIQ-YUSHU-2026-002` |
| `company_name` | 项目公司名称 |
| `industry` | 行业，例如机器人、半导体、新能源设备 |
| `stage` | 融资阶段，例如 Seed、Series A、Series B、Pre-IPO |
| `deal_type` | 股权投资、Pre-IPO、战略投资、并购少数股权等 |
| `source` | 项目来源，例如 internal、broker、founder、openclaw_import |
| `status` | `draft`、`r0_ready`、`r1_in_progress`、`r4_completed` 等 |
| `created_by` | 创建用户 |
| `created_at` / `updated_at` | 时间戳 |
| `final_decision` | `pass`、`review`、`fail`、`manual_override` |
| `final_score` | 最终分数，P0 沿用 OpenClaw 0-100 |
| `confidentiality_level` | `private`、`restricted`、`committee_only` |

### 5.2 Deal Document

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

### 5.3 Deal Evidence

Deal evidence 是面向投委会 Agent 的证据单元，必须区分：

| 类型 | 说明 |
| --- | --- |
| `verified` | 来自已核验材料、官方文件、签署文件或可信数据源 |
| `assumed` | 分析师或项目方假设 |
| `estimated` | 由公式、模型或推算得到 |
| `inferred` | 基于证据推论 |
| `unknown` | 当前缺失，不能补齐 |

证据单元最小合同：

```json
{
  "schema_version": "siq_deal_evidence_item_v1",
  "evidence_id": "EVID-DEAL-YUSHU-2026-001-000001",
  "deal_id": "DEAL-YUSHU-2026-001",
  "document_id": "DOC-0001",
  "claim": "公司 2025 年收入同比增长...",
  "evidence_type": "verified",
  "dimension": "finance",
  "source_path": "parsed_documents/<task_id>/document.md",
  "source_anchor": {
    "page": 12,
    "table_index": 3,
    "md_line": 188
  },
  "citation": "BP 第 12 页表 3",
  "confidence": 0.86,
  "role_hints": ["siq_ic_finance_auditor", "siq_ic_chairman"],
  "created_at": "2026-07-03T10:00:00+08:00"
}
```

## 6. 项目包目录合同

一级市场项目包采用文件系统优先，PostgreSQL 作为索引层。根目录：

```text
data/wiki/deals/<deal_id>/
```

推荐结构：

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
      blocks.json
      tables.json
      figures.json
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

### 6.1 OpenClaw 兼容映射

| OpenClaw 路径 | SIQ 新路径 |
| --- | --- |
| `shared/projects/<project_id>/project_meta.json` | `data/wiki/deals/<deal_id>/project_meta.json` |
| `shared/projects/<project_id>/phases/workflow_state.json` | `data/wiki/deals/<deal_id>/phases/workflow_state.json` |
| `shared/projects/<project_id>/phases/r1_reports.json` | `data/wiki/deals/<deal_id>/phases/r1_reports.json` |
| `shared/projects/<project_id>/phases/r1_5_disputes.json` | `data/wiki/deals/<deal_id>/phases/r1_5_disputes.json` |
| `shared/projects/<project_id>/phases/startup_receipts.json` | `data/wiki/deals/<deal_id>/phases/startup_receipts.json` |
| `shared/projects/<project_id>/discussion/*.md` | `data/wiki/deals/<deal_id>/discussion/*.md` |
| `shared/projects/<project_id>/40_decision/IC_DECISION_REPORT.md` | `data/wiki/deals/<deal_id>/decision/IC_DECISION_REPORT.md` |
| `shared/projects/<project_id>/90_audit/*` | `data/wiki/deals/<deal_id>/audit/*` |

P0 应提供导入脚本任务，但本方案不要求现在实现：

```text
scripts/deals/import_openclaw_project.py
  --source /home/maoyd/.openclaw/workspace/ic_master_coordinator_workspace/shared/projects/SIQ-YUSHU-2026-002
  --deal-id DEAL-YUSHU-2026-001
```

### 6.2 manifest.json 合同

```json
{
  "schema_version": "siq_deal_manifest_v1",
  "deal_id": "DEAL-YUSHU-2026-001",
  "legacy_project_id": "SIQ-YUSHU-2026-002",
  "company_name": "杭州宇树科技股份有限公司",
  "created_at": "2026-07-03T10:00:00+08:00",
  "updated_at": "2026-07-03T10:00:00+08:00",
  "documents": [],
  "evidence": {
    "index_path": "evidence/evidence_index.json",
    "items_path": "evidence/evidence_items.ndjson",
    "quality_path": "evidence/evidence_quality_report.json"
  },
  "workflow": {
    "state_path": "phases/workflow_state.json",
    "policy_version": "2026-04-13-siq-port"
  },
  "decision": {
    "markdown_path": "decision/IC_DECISION_REPORT.md",
    "html_path": "decision/IC_DECISION_REPORT.html"
  },
  "hashes": {}
}
```

## 7. OpenClaw Agent 复刻方案

### 7.1 复刻角色

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

### 7.2 兼容原则

P0 阶段必须保留：

- OpenClaw 角色名称、职责边界和红线。
- Coordinator 不输出专家观点，不创建项目专属变体 agent。
- R1 发言顺序：战略、行业、财务、法务、风控、主席。
- R1 严格串行调度，主席最后发言。
- 每个专家发言前必须完成 startup retrieval。
- 权重：chairman 30%，strategy / sector / finance / risk 各 15%，legal 10%。
- 阈值：`>=70 pass`，`68-69 review`，`<68 fail`。注意 OpenClaw 文案里也出现过 `<70 fail`，实现以 policy 的 `review_min=68/review_max=69` 为准。
- 专家报告中的 `score / recommendation / verified / assumed / open_questions`。
- 分歧识别与主席裁决。
- R3 可执行或跳过，但跳过必须写 `mode=skip` 和原因。
- 审计日志。

P0 阶段允许调整：

- 文件路径从 OpenClaw 路径迁移到 SIQ `data/wiki/deals`。
- Milvus collection 名称增加 SIQ 前缀，或通过 alias 兼容旧名称。
- 工具调用由 OpenClaw runtime 适配为 SIQ API / Hermes tool。
- 前端展示和状态管理按 SIQ 产品形态实现。

### 7.3 角色职责边界

| Profile | 必做 | 禁止 |
| --- | --- | --- |
| `siq_ic_master_coordinator` | R0 准入、证据门禁、任务调度、分歧整理、审计留痕 | 代替专家输出行业/财务/法律/风控观点 |
| `siq_ic_chairman` | 争议裁决、六维评估、条款建议、最终投决建议 | 代替财务建模、法律审查、行业技术细评 |
| `siq_ic_strategist` | 宏观政策、资本流向、经济周期、地缘政治、退出窗口 | 微观产品、合同条款、财务审计 |
| `siq_ic_sector_expert` | TAM/SAM/SOM、竞争格局、技术路线、国产替代、行业生命周期 | 宏观政策主判断、TS 法律条款 |
| `siq_ic_finance_auditor` | 收入质量、现金流、估值方法、财务红黄线、国资条款财务影响 | 法律结论、行业技术路线主判断 |
| `siq_ic_legal_scanner` | 主体股权、合同、IP、诉讼处罚、资质许可、数据合规、TS 条款法律风险 | 宏观政策、市场规模、财务估值主判断 |
| `siq_ic_risk_controller` | 市场风险、ESG、舆情、供应链、黑天鹅、执行风险、红蓝对抗 | 法律细条款、财务模型主判断 |

## 8. R0-R4 工作流

### 8.1 阶段定义

| 阶段 | 名称 | 目标 | 主要产物 |
| --- | --- | --- | --- |
| R0 | 项目准入与信息校验 | 创建项目、导入材料、建立证据包、检查门禁 | `project_meta.json`、`evidence_index.json`、`00_项目信息_R0.md` |
| R1 | 专家尽调 | 5 位专家按顺序输出独立观点，主席最后点评 | `r1_reports.json`、`01_R1_尽调汇总.md` |
| R1.5 | 分歧识别与主席裁决 | 显性化关键分歧和证据缺口 | `r1_5_disputes.json`、`02_R1.5_裁决记录.md` |
| R2 | 观点完善 | 专家根据裁决补充和修订 | `r2_reports.json`、`03_R2_观点完善汇总.md` |
| R3 | 红蓝对抗 | 挑战核心假设，可执行或跳过但必须留痕 | `r3_reports.json`、`04_R3_红蓝对抗.md` |
| R4 | 投决归档 | 生成最终报告、评分、结论、审计材料 | `r4_decision.json`、`IC_DECISION_REPORT.md` |

### 8.2 工作流状态

`phases/workflow_state.json`：

```json
{
  "schema_version": "siq_deal_workflow_state_v1",
  "deal_id": "DEAL-YUSHU-2026-001",
  "legacy_project_id": "SIQ-YUSHU-2026-002",
  "company_name": "杭州宇树科技股份有限公司",
  "industry": "机器人",
  "stage": "Pre-IPO",
  "status": "r1_in_progress",
  "current_phase": "R1",
  "policy_version": "2026-04-13-siq-port",
  "phases": {
    "R0": {
      "status": "completed",
      "started_at": "2026-07-03T10:00:00+08:00",
      "completed_at": "2026-07-03T10:15:00+08:00",
      "evidence_gate": "passed"
    },
    "R1": {
      "status": "in_progress",
      "active_agent": "siq_ic_sector_expert",
      "submitted_agents": ["siq_ic_strategist"]
    },
    "R1.5": {"status": "pending"},
    "R2": {"status": "pending"},
    "R3": {"status": "pending"},
    "R4": {"status": "pending"}
  },
  "updated_at": "2026-07-03T10:30:00+08:00"
}
```

### 8.3 R1 报告合同

`phases/r1_reports.json`：

```json
{
  "siq_ic_finance_auditor": {
    "agent_id": "siq_ic_finance_auditor",
    "legacy_agent_id": "ic_finance_auditor",
    "round_name": "R1",
    "score": 83,
    "recommendation": "SUPPORT",
    "confidence": "Medium",
    "summary": "...",
    "verified": ["..."],
    "assumed": ["..."],
    "open_questions": ["..."],
    "key_points": ["..."],
    "risk_flags": ["..."],
    "evidence_stats": {
      "shared": 15,
      "private": 3,
      "total": 18
    },
    "startup_receipt_id": "startup-siq_ic_finance_auditor-R1-001",
    "artifact_path": "discussion/01_R1_finance_auditor_report.md",
    "created_at": "2026-07-03T10:30:00+08:00"
  }
}
```

### 8.4 Startup Retrieval Receipt 合同

每位专家在 R1/R2/R4 发言前必须有 receipt。P0 可先模拟 SIQ 检索服务，不能跳过 receipt 文件。

`phases/startup_receipts.json`：

```json
{
  "schema_version": "siq_ic_startup_receipts_v1",
  "deal_id": "DEAL-YUSHU-2026-001",
  "agents": {
    "siq_ic_sector_expert": {
      "receipt_id": "startup-siq_ic_sector_expert-R1-001",
      "agent_id": "siq_ic_sector_expert",
      "legacy_agent_id": "ic_sector_expert",
      "round_name": "R1",
      "query": "宇树科技 机器人 Pre-IPO",
      "project_tag": "DEAL-YUSHU-2026-001",
      "shared_hits": 15,
      "private_hits": 3,
      "workspace_rules_read": ["SOUL.md", "AGENTS.md"],
      "gaps": [],
      "created_at": "2026-07-03T10:20:00+08:00"
    }
  }
}
```

### 8.5 证据门禁

P0 沿用 OpenClaw policy：

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
- `phases/workflow_state.json`
- `phases/audit_log.json`
- 前端 workflow 页面

### 8.6 分歧记录合同

`phases/r1_5_disputes.json`：

```json
{
  "schema_version": "siq_ic_disputes_v1",
  "deal_id": "DEAL-YUSHU-2026-001",
  "disputes": [
    {
      "dispute_id": "DISP-001",
      "topic": "估值是否支撑 Pre-IPO 定价",
      "dimension": "finance",
      "severity": "high",
      "positions": [
        {
          "agent_id": "siq_ic_finance_auditor",
          "stance": "support_with_terms",
          "evidence_ids": ["EVID-001"]
        },
        {
          "agent_id": "siq_ic_risk_controller",
          "stance": "caution",
          "evidence_ids": ["EVID-002"]
        }
      ],
      "chairman_ruling": {
        "agent_id": "siq_ic_chairman",
        "decision": "resolved_with_conditions",
        "rationale": "...",
        "required_followups": ["补充 IPO 估值区间敏感性分析"]
      },
      "resolved": true
    }
  ]
}
```

### 8.7 R4 决策合同

`phases/r4_decision.json`：

```json
{
  "schema_version": "siq_ic_r4_decision_v1",
  "deal_id": "DEAL-YUSHU-2026-001",
  "decision": "pass",
  "final_score": 78.55,
  "weighted_agent_score": 84.2,
  "chairman_dimension_score": 78.55,
  "chairman_qualitative_decision": "建议投资，但需设置估值和退出保护条款",
  "threshold_result": "pass",
  "conditions": [
    "设置 IPO 时间表触发的回购保护",
    "补充核心客户续约验证"
  ],
  "human_confirmation": {
    "status": "pending",
    "confirmed_by": null,
    "confirmed_at": null,
    "override_reason": null
  },
  "artifact_paths": {
    "markdown": "decision/IC_DECISION_REPORT.md",
    "html": "decision/IC_DECISION_REPORT.html"
  }
}
```

## 9. 后端设计

### 9.1 新增文件

```text
apps/api/routers/deals.py
apps/api/services/deal_store.py
apps/api/services/deal_documents.py
apps/api/services/deal_evidence.py
apps/api/services/ic_policy.py
apps/api/services/ic_workflow.py
apps/api/services/ic_agent_runtime.py
apps/api/services/ic_audit.py
apps/api/services/ic_report_builder.py
apps/api/services/ic_openclaw_importer.py
```

### 9.2 模块职责

| 模块 | 职责 |
| --- | --- |
| `deals.py` | 暴露 `/api/deals/*` 路由 |
| `deal_store.py` | 创建、读取、更新项目和项目包路径 |
| `deal_documents.py` | 数据室上传、文档解析任务绑定 |
| `deal_evidence.py` | 从文档 artifact 构建 evidence index |
| `ic_policy.py` | 读取 `siq_ic_shared/ic_workflow_policy.json`，提供权重、阈值、阶段顺序 |
| `ic_workflow.py` | R0-R4 状态机、门禁、阶段推进 |
| `ic_agent_runtime.py` | 调用 Hermes profiles 执行专家任务 |
| `ic_audit.py` | 写入审计事件 |
| `ic_report_builder.py` | 汇总最终投决报告 |
| `ic_openclaw_importer.py` | 导入 OpenClaw 项目包，生成 SIQ deal package |

### 9.3 核心 API

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
POST   /api/deals/{deal_id}/workflow/human-confirm
POST   /api/deals/{deal_id}/workflow/manual-override
```

Agent 调用：

```text
GET    /api/deals/ic/profiles
POST   /api/deals/{deal_id}/agents/{profile_id}/startup-retrieval
POST   /api/deals/{deal_id}/agents/{profile_id}/run
GET    /api/deals/{deal_id}/agents/{profile_id}/reports
```

报告和审计：

```text
GET    /api/deals/{deal_id}/reports
GET    /api/deals/{deal_id}/reports/{report_name}
GET    /api/deals/{deal_id}/decision
GET    /api/deals/{deal_id}/audit
GET    /api/deals/{deal_id}/manifest
```

OpenClaw 导入：

```text
POST   /api/deals/import/openclaw
GET    /api/deals/import/openclaw/{job_id}
```

### 9.4 后台 Job

以下任务应走后台 job：

- 批量文档解析。
- 构建 evidence package。
- 运行某个 IC Agent。
- 识别分歧。
- 生成最终报告。
- 导入 OpenClaw 项目包。
- 入库 PostgreSQL / Milvus。

任务状态可复用现有 `/api/jobs/*` 模式，或者新增：

```text
GET /api/deals/{deal_id}/jobs
GET /api/deals/{deal_id}/jobs/{job_id}
```

### 9.5 Agent Runtime 调用规则

`ic_agent_runtime.py` 不应让调用方随意指定任何 profile。必须按 allowlist：

```python
IC_PROFILES = {
    "master_coordinator": "siq_ic_master_coordinator",
    "chairman": "siq_ic_chairman",
    "strategy": "siq_ic_strategist",
    "sector": "siq_ic_sector_expert",
    "finance": "siq_ic_finance_auditor",
    "legal": "siq_ic_legal_scanner",
    "risk": "siq_ic_risk_controller",
}
```

每次运行 agent 前必须：

1. 校验 deal 权限。
2. 读取 workflow state。
3. 校验当前阶段是否允许该 profile 发言。
4. 生成或读取 startup retrieval receipt。
5. 构造包含 deal path、policy path、artifact output path 的 prompt。
6. 调用 Hermes。
7. 解析输出并写入 phase JSON。
8. 写审计日志。

## 10. 前端设计

### 10.1 路由

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

### 10.2 页面职责

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

### 10.3 工作台布局

项目总览页建议三栏：

```text
左栏：项目基本信息、阶段、操作按钮
中栏：R0-R4 时间线、专家状态、分歧卡片
右栏：证据覆盖率、缺口、最新审计事件、最终结论
```

### 10.4 R0-R4 交互

| 阶段 | 主要控件 |
| --- | --- |
| R0 | 项目信息表单、数据室上传、构建证据包、门禁检查 |
| R1 | 6 个发言卡片，按顺序运行，查看报告和 startup receipt |
| R1.5 | 分歧识别按钮、分歧列表、主席裁决入口 |
| R2 | 专家修订报告、补充证据、重新提交 |
| R3 | 红蓝对抗执行 / 跳过，跳过必须写理由 |
| R4 | 生成投决报告、人工确认、归档 |

### 10.5 前端文件建议

```text
apps/web/src/pages/Deals.tsx
apps/web/src/pages/DealWorkspace.tsx
apps/web/src/pages/DealDataRoom.tsx
apps/web/src/pages/DealEvidence.tsx
apps/web/src/pages/DealWorkflow.tsx
apps/web/src/pages/DealAgents.tsx
apps/web/src/pages/DealDisputes.tsx
apps/web/src/pages/DealDecision.tsx
apps/web/src/pages/DealAudit.tsx

apps/web/src/components/deals/
  DealProjectCard.tsx
  DealStageTimeline.tsx
  DealEvidencePanel.tsx
  DealAgentReportCard.tsx
  DealDisputeMatrix.tsx
  DealAuditTimeline.tsx
  DealDecisionSummary.tsx

apps/web/src/lib/dealApi.ts
apps/web/src/lib/dealTypes.ts
```

### 10.6 UI 维护原则

- `/deals` 是工作台，不是营销页。
- 状态和证据优先，避免大面积装饰卡片。
- Agent 卡片必须展示 `profile_id`、发言顺序、当前状态、报告入口、检索 receipt 状态。
- 最终报告页面复用现有 ReportViewer 思路，但下载/预览必须走鉴权 API。

## 11. PostgreSQL 设计

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
| `deal_os.profile_runs` | Hermes profile 调用记录 |

原则：

- PostgreSQL 保存索引、状态、证据定位和结构化查询字段。
- 原文、报告、完整 JSON 仍以 Wiki 项目包为权威归档。
- 所有表必须带 `deal_id` 和 `artifact_path`，可回跳到文件层。
- P0 若暂不建库，服务层接口仍按 repository pattern 设计，避免后续迁移成本过高。

## 12. Milvus 设计

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

OpenClaw 旧名称：

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
- Profile 文档中仍保留 `legacy_collection` 字段，便于核对历史资料。

Milvus metadata 最小字段：

| 字段 | 说明 |
| --- | --- |
| `schema_version` | `siq_deal_chunk_v1` |
| `deal_id` | 项目 ID |
| `legacy_project_id` | OpenClaw 项目 ID，可选 |
| `document_id` | 文档 ID |
| `evidence_id` | 证据 ID |
| `source_path` | 项目包内路径 |
| `source_type` | `bp`、`teaser`、`financial_model` 等 |
| `confidence` | `verified`、`assumed`、`estimated` |
| `role_hint` | 推荐使用该证据的 Agent |
| `citation` | 人类可读引用 |

## 13. Agent 工具适配

OpenClaw 工具语义到 SIQ 的映射：

| OpenClaw 概念 | SIQ 实现 |
| --- | --- |
| `shared/projects` | `data/wiki/deals` |
| `ic_collaboration_shared` | `siq_deal_shared` |
| 私有 collection | `siq_ic_<role>` |
| `agent_startup_retrieval` | `ic_agent_runtime.startup_retrieval()` |
| `unified_hybrid_retriever.py` | SIQ Milvus 检索服务或现有 vector-index 工具 |
| `sessions_send` | Hermes Runs API / SIQ 后台 job |
| `siq_workflow_policy.json` | `agents/hermes/profiles/siq_ic_shared/ic_workflow_policy.json` |
| `audit_log.json` | `ic_audit.py` 统一写入 |

P0 可以先做轻量工具：

```text
POST /api/deals/{deal_id}/agents/{profile_id}/startup-retrieval
POST /api/deals/{deal_id}/agents/{profile_id}/run
```

Profile `TOOLS.md` 不直接写内部 Python import，写 API/tool 语义：

```text
startup_retrieval(deal_id, profile_id, round_name, task_focus)
read_deal_manifest(deal_id)
write_phase_report(deal_id, phase, profile_id, payload)
append_audit_event(deal_id, event)
```

## 14. 安全与权限

一级市场材料通常包含更高敏感度，必须默认私有：

- 数据室原文不公开。
- 项目包默认只对创建者、管理员和授权用户可见。
- Agent 输出、审计链、最终投决报告都走 API 鉴权。
- 文档下载和预览必须经过签名访问或 API 代理。
- 不把真实密钥、会话、外部系统 token 写入项目包。
- 人工 override 必须记录操作者、时间、原因。
- Hermes prompt 中不得包含不必要的密钥、cookie、数据库连接串。
- `siq_ic_*` profile 默认不得访问 `data/wiki/companies`，除非用户明确发起跨域对标任务且 API 层授权。

## 15. 与现有二级市场模块的边界

不能混用的部分：

- 不把一级市场项目写入 `data/wiki/companies`。
- 不把一级市场财务模型强行导入 `pdf2md`、`sec_us`、`pdf2md_hk` 等二级市场 schema。
- 不把一级市场结论展示为二级市场交易评级。
- 不让二级市场 Agent 默认访问一级市场私密数据。
- 不把 `siq_ic_legal_scanner` 与现有 `siq_legal` 混为同一 profile：前者审查交易/项目法律风险，后者服务法规检索和法律意见书。

可以复用的部分：

- `apps/document-parser`
- `apps/api` 鉴权、文件代理、后台 job
- Hermes Runs API 代理
- Wiki / PostgreSQL / Milvus 基础设施
- 报告阅读器
- 系统状态和设置页面
- `shared/scripts/pg_query.py` 等通用只读工具，但必须经过权限边界封装

## 16. 实施计划

### P0-A：Profile 家族落地

目标：在 `agents/hermes/profiles` 下建立可维护的 `siq_ic_*` profile 家族。

任务：

1. 新增 `siq_ic_shared` 目录和共享 policy / contract / templates。
2. 从 OpenClaw 迁移 `siq_workflow_policy.json` 到 `siq_ic_shared/ic_workflow_policy.json`。
3. 新增 7 个 `siq_ic_*` profile 目录。
4. 从 OpenClaw workspace 迁移每个角色的 `SOUL.md / AGENTS.md / IDENTITY.md / TOOLS.md`，并把路径、collection、工具调用改为 SIQ 口径。
5. 为每个 profile 新增 `README.md`，说明职责、调用方式、维护规则。
6. 更新 `agents/hermes/profiles/manifest.json`，增加 `groups.ic`。

验收：

- 7 个 profile 均有 `config.yaml / SOUL.md / AGENTS.md / README.md`。
- `siq_ic_shared/ic_workflow_policy.json` 与 OpenClaw policy 关键字段一致。
- 文档中的可执行 profile ID 均采用 `siq_ic_*`。
- Profile 中不写真实密钥。

### P0-B：Hermes Runtime 识别与日常调用

目标：`siq_ic_*` 能被 API、脚本和本地 gateway 识别。

任务：

1. 扩展 `apps/api/services/hermes_client.py` profile registry。
2. 扩展 `apps/api/services/path_config.py` profile roots。
3. 扩展 `apps/api/services/hermes_model_control.py` 模型控制。
4. 扩展 `scripts/hermes/profile_dir.sh` 和 `scripts/hermes/run_gateway.sh`。
5. 扩展 `start_all.sh`，用 `SIQ_ENABLE_IC_HERMES=1` 控制是否启动 IC profiles。
6. 扩展 health check 和 README。

验收：

- `scripts/hermes/profile_dir.sh siq_ic_chairman` 输出正确路径。
- `scripts/hermes/run_gateway.sh siq_ic_finance_auditor` 可启动对应 gateway。
- `hermes_client.hermes_profile_config("siq_ic_finance_auditor")` 返回 18664 runs URL。
- 模型设置页面或系统状态能显示 IC profiles，或明确按 domain 隐藏但 API 可用。

### P0-C：Deal Package 和 OpenClaw Import

目标：能把 OpenClaw golden path 项目导入 SIQ deal package。

任务：

1. 新增 `deal_store.py`，支持 `data/wiki/deals/<deal_id>` 创建、读取、更新。
2. 新增 manifest / project_meta / workflow_state 写入工具。
3. 新增 `ic_openclaw_importer.py`，导入 `SIQ-YUSHU-2026-002`。
4. 保留 legacy_project_id、legacy_agent_id、legacy_path。
5. 写入审计事件 `openclaw_imported`。

验收：

- 可生成 `data/wiki/deals/DEAL-YUSHU-2026-001`。
- `phases/r1_reports.json` 等兼容文件可被读取。
- `decision/IC_DECISION_REPORT.md` 存在。
- manifest 中 hash 和 artifact path 可追溯。

### P0-D：最小 API 和 Web 工作台

目标：前端能查看导入项目和投委会结果。

任务：

1. 新增 `/api/deals` 最小项目列表和详情。
2. 新增 `/api/deals/{deal_id}/workflow`。
3. 新增 `/api/deals/{deal_id}/decision`。
4. 新增 `/api/deals/{deal_id}/audit`。
5. 前端新增 `/deals`、`/deals/:id`、`/deals/:id/workflow`、`/deals/:id/decision`。

验收：

- 可在 Web 看到导入的 YUSHU demo。
- 可查看 R0-R4 状态、专家报告摘要、最终报告。
- 下载/预览通过鉴权 API，不暴露本地路径。

### P0-E：R0-R4 最小运行闭环

目标：跑通一个完整一级市场项目。

任务：

1. 新增数据室上传和文档解析绑定。
2. 新增 evidence build。
3. 新增 startup retrieval receipt。
4. 新增 R1 单 agent 运行，再扩展为串行顺序运行。
5. 新增 R1.5 分歧识别。
6. 新增 R2/R3/R4 产物写入。
7. 新增 `IC_DECISION_REPORT.md` 生成和审计归档。

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

## 17. 开发落地补充规格

本章用于给后续开发窗口直接拆任务。若与前文存在粒度差异，以本章的文件级清单为准。

### 17.1 Profile 迁移源文件矩阵

从 OpenClaw 迁移到 SIQ profile 时，只迁移角色规则和模板，不迁移历史 memory、会话状态、缓存、虚拟环境和私有密钥。

| SIQ profile | OpenClaw 源目录 | 必迁文件 | 可参考文件 | 不迁移 |
| --- | --- | --- | --- | --- |
| `siq_ic_master_coordinator` | `/home/maoyd/.openclaw/workspace/ic_master_coordinator_workspace` | `SOUL.md`、`AGENTS.md`、`IDENTITY.md`、`TOOLS.md` | `BOOTSTRAP.md`、`QUICK_REFERENCE.md`、`config/siq_workflow_policy.json`、`scripts/workflow_policy.py` | `.venv*`、`memory/`、历史项目输出 |
| `siq_ic_chairman` | `/home/maoyd/.openclaw/workspace/ic_chairman_workspace` | `SOUL.md`、`AGENTS.md`、`IDENTITY.md`、`TOOLS.md` | `QUICK_REFERENCE.md`、`KNOWLEDGE_BASE.md` | `.openclaw/`、`memory/` |
| `siq_ic_strategist` | `/home/maoyd/.openclaw/workspace/ic_strategist_workspace` | `SOUL.md`、`AGENTS.md`、`IDENTITY.md`、`TOOLS.md` | 行业/宏观方法论文档 | `memory/`、会话状态 |
| `siq_ic_sector_expert` | `/home/maoyd/.openclaw/workspace/ic_sector_expert_workspace` | `SOUL.md`、`AGENTS.md`、`IDENTITY.md`、`TOOLS.md` | 行业框架、输出模板 | `memory/`、会话状态 |
| `siq_ic_finance_auditor` | `/home/maoyd/.openclaw/workspace/ic_finance_auditor_workspace` | `SOUL.md`、`AGENTS.md`、`IDENTITY.md`、`TOOLS.md` | 估值框架、红黄线模板 | `memory/`、会话状态 |
| `siq_ic_legal_scanner` | `/home/maoyd/.openclaw/workspace/ic_legal_scanner_workspace` | `SOUL.md`、`AGENTS.md`、`IDENTITY.md`、`TOOLS.md` | 法务边界、TS 条款模板 | `memory/`、会话状态 |
| `siq_ic_risk_controller` | `/home/maoyd/.openclaw/workspace/ic_risk_controller_workspace` | `SOUL.md`、`AGENTS.md`、`IDENTITY.md`、`TOOLS.md` | 红蓝对抗、风险分级模板 | `memory/`、会话状态 |

迁移时必须做 5 类替换：

1. 路径：`/home/maoyd/.openclaw/workspace/.../shared/projects` 改为 `data/wiki/deals`。
2. Collection：旧 `ic_collaboration_shared` / `ic_<role>` 改为 `siq_deal_shared` / `siq_ic_<role>`，并保留 `legacy_collection` 说明。
3. 工具：旧 `unified_hybrid_retriever.py` / `sessions_send` 改为 SIQ API / Hermes Runs API 语义。
4. 产物：旧 `40_decision` / `90_audit` 改为 `decision` / `audit`，导入器负责兼容旧路径。
5. 品牌：可保留“OpenClaw 兼容来源”说明，但可执行 profile ID 必须是 `siq_ic_*`。

### 17.2 Runtime Registry 精确改动点

`apps/api/services/hermes_client.py` 需要把 profile registry 从“五个固定助手”扩为“二级市场 + 一级市场”。P0 可以继续使用硬编码字典，P1 再抽成 manifest-driven registry。

必须新增的 canonical profiles：

```text
siq_ic_master_coordinator
siq_ic_chairman
siq_ic_strategist
siq_ic_sector_expert
siq_ic_finance_auditor
siq_ic_legal_scanner
siq_ic_risk_controller
```

必须新增的 aliases：

```text
ic_master -> siq_ic_master_coordinator
ic_coordinator -> siq_ic_master_coordinator
ic_chairman -> siq_ic_chairman
ic_strategy -> siq_ic_strategist
ic_strategist -> siq_ic_strategist
ic_sector -> siq_ic_sector_expert
ic_finance -> siq_ic_finance_auditor
ic_legal -> siq_ic_legal_scanner
ic_risk -> siq_ic_risk_controller
```

必须新增的 env prefixes：

```text
siq_ic_master_coordinator -> IC_MASTER
siq_ic_chairman -> IC_CHAIRMAN
siq_ic_strategist -> IC_STRATEGIST
siq_ic_sector_expert -> IC_SECTOR
siq_ic_finance_auditor -> IC_FINANCE
siq_ic_legal_scanner -> IC_LEGAL
siq_ic_risk_controller -> IC_RISK
```

必须新增的 tests：

```text
apps/api/tests/test_hermes_ic_profiles.py
  - normalize_profile("ic_finance") == "siq_ic_finance_auditor"
  - hermes_profile_config("siq_ic_finance_auditor") 默认端口为 18664
  - SIQ_HERMES_IC_FINANCE_PORT 可覆盖端口
  - profiles_root 下存在 siq_ic_finance_auditor/config.yaml 时 model_name 为 siq_ic_finance_auditor

apps/api/tests/test_ic_policy.py
  - 能读取 siq_ic_shared/ic_workflow_policy.json
  - 权重和阈值与 OpenClaw policy 一致
  - R1 顺序固定为 strategist/sector/finance/legal/risk/chairman

apps/api/tests/test_deal_package_contract.py
  - 创建 deal package 后 manifest/project_meta/workflow_state 均存在
  - legacy_project_id 可选但不丢失
  - 路径不能逃逸 data/wiki/deals
```

### 17.3 `start_all.sh` 启动策略

IC profiles 不默认启动。后续开发应实现：

```bash
SIQ_ENABLE_IC_HERMES=1 ./start_all.sh
```

启用后启动顺序建议：

```text
siq_ic_master_coordinator -> 18660
siq_ic_chairman -> 18661
siq_ic_strategist -> 18662
siq_ic_sector_expert -> 18663
siq_ic_finance_auditor -> 18664
siq_ic_legal_scanner -> 18665
siq_ic_risk_controller -> 18666
```

不启用时，系统状态页可以显示 “IC Hermes disabled”，但 `/api/deals` 项目浏览、导入和报告查看仍应可用。

### 17.4 Agent Prompt Payload 合同

`ic_agent_runtime.py` 调用 Hermes 前，应构造结构化 prompt，而不是只塞自然语言。

最小 payload：

```json
{
  "schema_version": "siq_ic_agent_task_v1",
  "deal_id": "DEAL-YUSHU-2026-001",
  "company_name": "杭州宇树科技股份有限公司",
  "industry": "机器人",
  "stage": "Pre-IPO",
  "phase": "R1",
  "round_name": "R1",
  "agent_id": "siq_ic_finance_auditor",
  "legacy_agent_id": "ic_finance_auditor",
  "deal_package_root": "data/wiki/deals/DEAL-YUSHU-2026-001",
  "workflow_policy_path": "agents/hermes/profiles/siq_ic_shared/ic_workflow_policy.json",
  "startup_receipt_path": "phases/startup_receipts.json",
  "input_artifacts": {
    "manifest": "manifest.json",
    "evidence_index": "evidence/evidence_index.json",
    "workflow_state": "phases/workflow_state.json"
  },
  "output_contract": {
    "json_path": "phases/r1_reports.json",
    "markdown_path": "discussion/01_R1_finance_auditor_report.md",
    "required_fields": ["score", "recommendation", "verified", "assumed", "open_questions"]
  },
  "hard_rules": [
    "必须先读取 startup receipt",
    "必须区分 verified/assumed",
    "不得访问 data/wiki/companies，除非任务显式授权",
    "不得输出投资执行指令，只输出投委会建议"
  ]
}
```

Hermes 返回内容应由 API 解析和持久化。Profile 可以生成 Markdown，但最终写文件的责任建议收敛到 API 服务层，避免 profile 绕过权限和审计。

### 17.5 OpenClaw Import 验收样本

首个导入样本固定使用：

```text
/home/maoyd/.openclaw/workspace/ic_master_coordinator_workspace/shared/projects/SIQ-YUSHU-2026-002
```

导入后必须得到：

```text
data/wiki/deals/DEAL-YUSHU-2026-001/
  project_meta.json
  manifest.json
  phases/workflow_state.json
  phases/r1_reports.json
  phases/r1_5_disputes.json
  phases/r2_reports.json
  phases/r3_reports.json
  phases/r4_decision.json
  phases/startup_receipts.json
  discussion/00_项目信息_R0.md
  discussion/01_R1_尽调汇总.md
  discussion/02_R1.5_裁决记录.md
  discussion/03_R2_观点完善汇总.md
  discussion/04_R3_红蓝对抗.md
  discussion/05_最终投决报告.md
  decision/IC_DECISION_REPORT.md
  audit/archive_manifest.json
```

导入器必须记录：

- `source_root`
- `legacy_project_id`
- `imported_at`
- `file_count`
- 每个导入文件的 `sha256`
- 未导入文件及原因

### 17.6 前端验收用例

最小 E2E 用例建议：

```text
apps/web/e2e/tests/deals-workflow.spec.ts
  - 访问 /deals 可看到导入项目
  - 点击项目进入 /deals/:dealId
  - Workflow 页展示 R0-R4 阶段
  - Agents 页展示 7 个 siq_ic_* profile 状态
  - Decision 页展示 IC_DECISION_REPORT
  - Audit 页展示 openclaw_imported 和 r4_decision_generated 事件
```

UI 必须显示 profile ID，不只显示中文角色名，便于排查运行时问题。

### 17.7 Definition of Done

P0 的完成标准：

- `siq_ic_*` profile 目录存在且可以被 `profile_dir.sh` 解析。
- `SIQ_ENABLE_IC_HERMES=1` 时可启动至少一个 IC gateway。
- OpenClaw YUSHU demo 能导入到 `data/wiki/deals`。
- `/api/deals` 能列出该 demo。
- Web 能查看 workflow、agents、decision、audit。
- R4 决策 JSON 同时保留 `weighted_agent_score`、`chairman_dimension_score`、`chairman_qualitative_decision`。
- 审计链记录 import、agent run、manual override 或 human confirmation。
- 文档、代码和 UI 中可执行 profile ID 全部使用 `siq_ic_*`。

## 18. 风险与应对

| 风险 | 说明 | 应对 |
| --- | --- | --- |
| Profile 建了但无法调用 | 现有 Hermes registry 硬编码 5 个 profile | P0-B 同步扩展 runtime registry、脚本和端口 |
| 角色迁移失真 | 过早优化导致 OpenClaw 方法论被破坏 | P0 冻结角色、顺序、权重、阈值和证据门禁 |
| 评分语义混乱 | agent 权重和主席六维权重混为一个分数 | R4 同时保存 weighted_agent_score 和 chairman_dimension_score |
| 路径混乱 | OpenClaw 和 SIQ 项目根不一致 | 统一使用 `data/wiki/deals`，保留 legacy_path |
| 敏感数据泄露 | 一级市场材料私密性高 | 默认鉴权、禁止写入日志和 README、文件代理下载 |
| Agent 幻觉 | 未检索证据直接输出观点 | 强制 startup retrieval receipt 和 verified/assumed |
| 工作流过重 | 一次性自动化复杂度高 | 先人工推进阶段，再逐步自动化 |
| 二级市场污染 | 一级市场数据进入公司研究链路 | schema、Wiki namespace、前端路由、profile 权限隔离 |
| 本地资源压力 | 7 个 IC gateway 全部启动消耗资源 | `SIQ_ENABLE_IC_HERMES=1` 控制，不默认启动 |

## 19. 推荐下一步

建议按以下顺序进入实现：

1. 新增 `agents/hermes/profiles/siq_ic_shared` 和 7 个 `siq_ic_*` profile 目录。
2. 迁移 OpenClaw `siq_workflow_policy.json`，先冻结 policy。
3. 扩展 Hermes profile registry、端口、alias 和启动脚本，使 `siq_ic_*` 可日常调用。
4. 新增 `data/wiki/deals` 和 deal package helper。
5. 编写 OpenClaw golden path importer，导入 `SIQ-YUSHU-2026-002`。
6. 在 `apps/api` 新增最小 `/api/deals`。
7. 在 `apps/web` 新增 `/deals` 和项目详情页。
8. 再接入 R0-R4 自动运行。

这条路径的核心是先把 OpenClaw 的投委会制度完整接入 SIQ，再利用 SIQ 的工程底座逐步产品化。第一阶段的成功标准不是“更聪明”，而是“同样的投委会流程在 SIQ 中稳定、可见、可审计地运行”。
