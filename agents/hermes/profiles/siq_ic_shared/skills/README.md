# SIQ IC 共享技能

这些技能从 OpenClaw 工作区迁移而来，供 SIQ IC profiles 共享。所有可执行 `siq_ic_*` Hermes profiles 都可以使用它们，并由 `scripts/hermes/run_gateway.sh` 同步到每个运行态 profile。

## 当前角色

本目录是 SIQ 一级市场 Deal OS 的可复用技能库。技能覆盖尽调、估值、term sheet、市场规模、公司研究、投后监控和 IC memo 生产。它们不是独立产品；只有在 `siq_ic_shared` 合同、项目 evidence 和 API/Web workflow 门禁约束下才产生业务价值。

商业目的在于可复制性：SIQ 可以在不同 deal 中复用相似尽调工作，而不需要每个交易重新编写 prompt 或重建分析师 checklist。

## 包含的技能

### 批次 1：核心投委会能力

- `ic-finance-auditor`
- `ic-memo`
- `venture-capital`
- `due-diligence-analyst`
- `due-diligence-dataroom`
- `deal-screening`
- `startup-tools`
- `term-sheet-analyzer`
- `tam-sam-som`
- `cap-table-manager`
- `unit-economics`
- `dcf-model`
- `3-statement-model`

### 批次 2：研究与估值流水线

- `market-intelligence-claw`
- `competitive-analysis`
- `strategic-competitor-analysis`
- `thesis-tracker`
- `comps-analysis`
- `equity-valuation-framework`
- `financial-analyst`
- `teaser`
- `pitch-deck`
- `deal-sourcing`
- `deal-tracker`
- `risk-metrics-calculation`
- `portfolio-monitoring`

### 批次 3：尽调、研究与材料生产

- `company-investment-research`
- `dd-checklist`
- `dd-meeting-prep`
- `sector-overview`
- `mckinsey-research`
- `initiating-coverage`
- `cim-builder`
- `buyer-list`
- `value-creation-plan`
- `ai-readiness`
- `financial-analysis-agent`
- `merger-model`
- `returns-analysis`
- `return-rate-impact-calculator`

## 迁移规则

- 不复制运行态状态、隐藏 metadata 目录、凭据、缓存和项目输出。
- 携带凭据或旧 workspace 路径的 OpenClaw 本地脚本由 SIQ 服务替代。
- 文本资产中的 agent ID 和 collection 名统一规范为 `siq_ic_*` 与 `siq_deal_shared`。
