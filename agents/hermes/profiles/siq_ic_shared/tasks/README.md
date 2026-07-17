# SIQ IC 阶段任务模板

这些模板定义 Hermes 一级市场投委会 profiles 在生产链路中的预期行为。API 编排器负责任务投递、lease、持久化交接、重试和产物写入；profile 本身不直接与其他 gateway 通信。

## 来源类别

- `project_evidence`：Deal 范围内的项目事实，来自 `siq_deal_shared` / `ic_collaboration_shared`，必须带 Evidence ID 和来源坐标。
- `background_knowledge`：角色私有 Milvus collection 中的研究方法、行业 benchmark、历史案例和 challenge hypothesis。

背景知识不能验证项目事实。关于标的公司的正式判断必须引用项目 Evidence。每个任务都要分别报告 shared retrieval 与 private retrieval 的状态，避免把知识库启发误写成项目证据。

## 模板

- `R0_COORDINATOR_READINESS.md`：身份、材料、快照、召回和范围 gate。
- `R1_INDEPENDENT_RESEARCH.md`：跨智能体锚定前的独立专家分析。
- `R1_CROSS_VALIDATION.md`：R1A 交接后的风险压力测试与主席初步综合。
- `R1_5_CHAIRMAN_RULING.md`：分歧裁决与证据补充要求。
- `R2_EXPERT_REVISION.md`：基于证据的观点和评分修订。
- `R3_RED_BLUE_DEBATE.md`：红方论证、蓝方回应、反驳和主席裁定。
- `R4_CHAIRMAN_DECISION.md`：结构化最终决策与六维评分。
- `DETERMINISTIC_FALLBACK.md`：非模型恢复产物的强制身份模板。

`quality_accepted` 不能由模板存在自动推断。验收必须绑定具名 golden case、自动化检查和人工方法论审批。
