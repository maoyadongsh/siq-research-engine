# SIQ README 体系重构设计

日期：2026-07-05
状态：方案已确认，等待文档复核
仓库：`/home/maoyd/siq-research-engine`

## 1. 目标

本轮工作目标不是简单“补全 README”，而是对 SIQ 主仓文档体系进行一次全量重构，使其同时满足两类需求：

- 对外能够清楚说明 SIQ 是什么、难点在哪里、创新点是什么、为什么值得信任。
- 对内能够成为研发、交付、运维和新成员快速上手的工程说明入口。

README 重构后的目标状态是：根 README 负责建立项目认知和技术高度，各主路径 README 负责承接工程落地、上下游关系、运行方式和维护边界，整套文档共同体现 SIQ 的系统创新、技术难度和工程成熟度。

## 2. 当前上下文

当前主仓已经具备较完整的 README 覆盖，但整体仍存在以下问题：

- 信息质量不一致。有的 README 已包含不少工程细节，有的 README 仍停留在目录说明或最小使用说明。
- 叙事层次不稳定。根 README 已开始承担系统总览职责，但部分子 README 仍像零散模块注释，和根 README 的高度不匹配。
- 亮点表达偏弱。现有文档能说明“系统能做什么”，但对“为什么难”“为什么有壁垒”“与普通 RAG/Agent 项目有何不同”表达不足。
- 模块关系不够清晰。若读者不熟悉仓库，很难从 README 体系中迅速建立控制面、解析面、规则面、证据面和运行面的关系图。
- 文档风格不统一。存在中文、术语密度、章节顺序和详略程度不统一的问题。

因此，本轮不是增量补丁，而是一次带有明确文档架构目标的系统性重写。

## 3. 受众与语言策略

### 3.1 受众定位

本轮 README 体系面向“双受众”设计：

1. 对外展示与合作沟通。
2. 内部研发、交付与维护团队。

对应的表达策略为：

- 根 README 偏对外叙事，强调系统定位、创新点、技术壁垒、可审计能力和多市场覆盖。
- 子路径 README 偏工程落地，强调职责边界、关键能力、运行方式、数据合同、上下游关系和维护原则。

### 3.2 语言策略

文档采用中文主叙事，关键术语保留英文，例如：`evidence package`、`source map`、`load plan`、`schema extraction`、`workflow`、`contract`、`market adapter`。

不采用中英双语并列，以避免信息冗余和文档体积失控。

## 4. 设计原则

README 体系重构遵循以下原则：

### 4.1 双层叙事

根 README 负责回答：

- SIQ 是什么。
- 它解决什么问题。
- 它为什么难。
- 它与普通 AI 研究工具的差异是什么。

子 README 负责回答：

- 这个模块在整条链路中的位置是什么。
- 它解决哪个关键问题。
- 它如何与上下游协同。
- 它运行时依赖什么、产出什么、边界在哪里。

### 4.2 亮点必须落到机制

所有“创新点”“亮点”“技术壁垒”的表述都必须落到具体机制，而不是抽象形容词。例如：

- 官方披露直连，而不是“数据源丰富”。
- 多市场异构解析和市场隔离规则，而不是“支持全球市场”。
- 统一证据合同、source map 和 evidence package，而不是“可追溯”。
- 受控多智能体协作，而不是“智能分析能力强”。

### 4.3 工程信息与叙事分层

根 README 不承担所有细节；子 README 不重复整套系统介绍。每份文档都站在所属层级解决对应问题。

### 4.4 面向协作

每份 README 都应让新接手的人快速理解：

- 这个模块为什么存在。
- 改它之前要看什么。
- 它最容易踩哪些坑。

### 4.5 统一术语与路径

所有 README 必须统一以下内容的说法：

- 目录角色：`data`、`var`、`artifacts`、`datasets`、`eval_datasets`。
- 核心产物：`document_full.json`、`quality_report.json`、`source_map.json`、`financial_data.json`、`financial_checks.json`。
- 服务角色：`apps/api`、`apps/pdf-parser`、`apps/document-parser`、`services/market-report-finder`、`services/market-report-rules`、`agents/hermes`。

## 5. 推荐方案

采用“`双层叙事型 README 重构方案`”。

这是三种可选策略中最适合当前仓库的方案：

- 不采用纯品牌型方案，因为那会让子路径 README 工程信息不足。
- 不采用纯工程型方案，因为那会削弱 SIQ 在系统设计、证据可信度和多市场能力上的高度表达。
- 采用双层叙事方案，使根 README 承担项目高度，子 README 承担工程落地。

该方案的核心收益：

- 对外看，SIQ 会被理解为“可审计研究生产线”，而不是普通聊天式研究工具。
- 对内看，文档能直接服务工程协作，而不是停留在展示层。

## 6. 创新点与技术难点表达框架

### 6.1 根 README 重点表达的系统级亮点

根 README 的核心叙事应围绕以下四个系统亮点展开：

1. 官方披露直连。
2. 多市场异构解析。
3. 证据包与可追溯引用体系。
4. 受控多智能体协作。

目标是让读者明确感知：SIQ 的难点不在于“接一个大模型”，而在于搭建一条从原始披露文件到结构化证据再到研究结论的可信链路。

### 6.2 `apps/*` README 重点表达的工程难点

- `apps/api`：统一鉴权、任务编排、流式 Agent 代理、下游服务治理、证据访问控制。
- `apps/pdf-parser`：财报 PDF 解析、质量门禁、表格关系、财务抽取、勾稽校验和人工修正闭环。
- `apps/document-parser`：任意文档类型归一到统一 artifact 合同、source map 和 schema extraction。
- `apps/web`：把下载、解析、证据复核、报告与智能体协作转化为可操作工作台，而非简单展示层。

### 6.3 `services/*` README 重点表达的规则与市场壁垒

- `market-report-finder` 不是简单下载器，而是多市场官方披露入口抽象层。
- `market-report-rules` 不是后处理脚本，而是多市场 evidence package、字段归一、质量门禁、load plan 和入库契约的规则中枢。

### 6.4 `packages/*` README 重点表达的合同价值

`packages/market-contracts` 应突出“共享证据语义合同”的价值，解释跨服务复用如何依赖稳定 contract，而不是靠约定俗成的脚本接口。

### 6.5 `agents/hermes/*` README 重点表达的协作治理

智能体相关 README 不应写成“角色介绍卡片”，而应强调：

- 依赖什么证据。
- 能做什么决策。
- 不能越过什么边界。
- 与其他 agent 如何协同。

### 6.6 工具与数据层 README 重点表达的工程成熟度

`scripts`、`db/imports`、`infra/model-services`、`data`、`datasets`、`eval_datasets`、`artifacts`、`var` 等目录应突出：

- SIQ 不只是能跑 demo。
- 它有数据治理、评测回归、导入管线、模型运维和运行态隔离能力。

## 7. README 模板体系

### 7.1 根 README 模板

根 README 使用“对外叙事 + 内部导航”的结构，建议包含：

- 项目定位。
- 为什么难。
- 核心创新。
- 能力矩阵。
- 系统架构。
- 关键数据合同。
- 典型工作流。
- 技术栈。
- 仓库地图。
- 快速启动。
- 健康检查。
- 关键环境变量。
- 验证命令。
- 延伸阅读入口。

### 7.2 应用、服务、共享包 README 模板

适用于 `apps/*`、`services/*`、`packages/*`，建议包含：

- 模块定位。
- 解决的核心问题。
- 在系统中的位置。
- 上下游依赖。
- 核心能力。
- 关键接口或标准产物。
- 启动方式。
- 运行态目录。
- 关键环境变量。
- 验证方式。
- 维护原则。

其中“技术难点”应单独成节，避免 README 退化为命令清单。

### 7.3 智能体 README 模板

适用于 `agents/hermes` 及各 profile，建议包含：

- 角色定位。
- 职责边界。
- 输入证据。
- 输出产物。
- 与其他 agent 的协同关系。
- 禁止行为。
- 运行入口。
- 关键脚本或规则文件。
- 适用场景。

### 7.4 工具与数据治理 README 模板

适用于 `scripts`、`db/imports`、`infra/model-services`、`data`、`datasets`、`eval_datasets`、`artifacts`、`var`，建议包含：

- 目录职责。
- 在系统中的位置。
- 内容边界。
- 典型用法。
- 数据或运行约束。
- 治理规则。
- 维护建议。

## 8. 本次改写范围

本轮纳入重写范围的 README 如下。

### 8.1 根目录

- `README.md`

### 8.2 应用与服务

- `apps/api/README.md`
- `apps/document-parser/README.md`
- `apps/pdf-parser/README.md`
- `apps/web/README.md`
- `apps/web/e2e/README.md`
- `services/market-report-finder/README.md`
- `services/market-report-rules/README.md`
- `services/market-report-rules/src/market_report_rules_service/markets/README.md`
- `packages/market-contracts/README.md`

### 8.3 智能体

- `agents/hermes/README.md`
- `agents/hermes/profiles/siq_analysis/README.md`
- `agents/hermes/profiles/siq_assistant/README.md`
- `agents/hermes/profiles/siq_factchecker/README.md`
- `agents/hermes/profiles/siq_tracking/README.md`
- `agents/hermes/profiles/siq_legal/README.md`
- `agents/hermes/profiles/siq_ic_chairman/README.md`
- `agents/hermes/profiles/siq_ic_finance_auditor/README.md`
- `agents/hermes/profiles/siq_ic_legal_scanner/README.md`
- `agents/hermes/profiles/siq_ic_master_coordinator/README.md`
- `agents/hermes/profiles/siq_ic_risk_controller/README.md`
- `agents/hermes/profiles/siq_ic_sector_expert/README.md`
- `agents/hermes/profiles/siq_ic_shared/README.md`
- `agents/hermes/profiles/siq_ic_shared/templates/README.md`
- `agents/hermes/profiles/siq_ic_strategist/README.md`

### 8.4 工具与数据层

- `scripts/README.md`
- `scripts/vector-index/milvus-ingestion/README.md`
- `scripts/vector-index/milvus-ingestion/tools/knowledge_ingest/README.md`
- `db/imports/README.md`
- `infra/model-services/README.md`
- `data/README.md`
- `datasets/README.md`
- `eval_datasets/README.md`
- `eval_datasets/document_parser_cases/README.md`
- `artifacts/README.md`
- `var/README.md`

## 9. 明确排除范围

以下 README 不纳入本轮重写：

- `data/wiki/**`、`data/hermes/**` 等运行态或样本产物目录中的 README。
- `runtimes/**`、`.pytest_cache/**` 及其他缓存或运行时目录中的 README。
- 第三方依赖、可执行环境、site-packages、自带模板或外部工具目录中的 README。
- 自动生成、备份或历史快照目录中的 README。

排除这些目录的原因是：它们不是主仓长期维护的项目级文档，重写后也无法稳定体现系统结构，反而会增加噪音和维护负担。

## 10. 统一写作规则

### 10.1 每份 README 最少回答五个问题

1. 这个模块解决什么关键问题。
2. 它在整条研究链路里的位置是什么。
3. 它最难的技术点是什么。
4. 它通过什么合同与上下游协作。
5. 维护或扩展时最容易踩什么坑。

### 10.2 避免空泛形容词堆积

不写：

- “功能强大”
- “体验优秀”
- “高性能”
- “支持全球市场”

除非后文紧跟机制或边界说明。

### 10.3 根 README 和子 README 不重复大段系统介绍

- 根 README 写全局叙事。
- 子 README 写本模块职责和边界。

### 10.4 运行态、版本化、生成产物严格区分

所有文档需要统一说明：

- `data`：历史兼容运行态。
- `var`：新增本地运行态推荐目录。
- `artifacts`：构建、测试、评测和批处理生成产物。
- `datasets`：可版本化稳定样本和 fixtures。
- `eval_datasets`：历史评测语料和回归集。

## 11. 质量校验标准

README 重写完成后，应进行以下一致性检查：

### 11.1 术语一致性

检查关键术语是否统一，例如：

- `evidence package`
- `source map`
- `quality report`
- `load plan`
- `workflow`
- `contract`

### 11.2 路径和端口一致性

检查文中出现的：

- 服务端口。
- 默认目录。
- 核心脚本入口。
- 关键环境变量。

确保与仓库实际实现一致。

### 11.3 模块关系一致性

检查各 README 对上下游依赖的描述是否互相匹配，例如：

- `apps/api` 对 `apps/document-parser`、`apps/pdf-parser`、`services/*` 的依赖。
- `services/market-report-rules` 与 `packages/market-contracts` 的协作。
- `agents/hermes` 与 API、Wiki、数据库、共享脚本之间的关系。

### 11.4 目录边界一致性

检查是否把：

- 运行态目录误写成版本化目录。
- 评测语料误写成运行结果。
- 临时产物误写成长期资产。

### 11.5 亮点表达一致性

检查所有高层描述是否围绕同一条主线：

“SIQ 是一条从官方披露到结构化证据再到受控研究结论的可审计研究生产线。”

## 12. 实施顺序建议

README 落地时建议按以下顺序推进：

1. 先重写根 README，统一全仓叙事口径。
2. 再重写 `apps/*`、`services/*`、`packages/*`，固定核心模块边界。
3. 再重写 `agents/hermes/*`，统一受控智能体的描述方式。
4. 最后重写工具与数据治理目录，完成全仓收口。

这样可以减少重复返工，保证术语和结构从上到下逐层对齐。

## 13. 不采用的方案

### 13.1 纯对外展示型 README 方案

若只强化品牌叙事和项目介绍，会让子 README 失去工程说明价值，不适合当前仓库的研发协作需求。

### 13.2 纯工程说明书型 README 方案

若所有 README 都只写职责、命令和路径，会削弱 SIQ 的系统高度，难以充分体现创新点、技术难度和差异化价值。

### 13.3 无差别批量套模板方案

若所有 README 只做统一模板替换，虽然整齐，但会丢失不同层级文档应有的语气、粒度和重点，不利于建立项目整体认知。

## 14. 预期结果

本轮 README 重构完成后，预期达到以下结果：

- 新读者能在根 README 中快速理解 SIQ 的定位、创新点和可信度来源。
- 工程成员能在子 README 中快速找到模块职责、运行方式、数据合同和维护边界。
- 全仓 README 术语、风格、层次和目录边界统一。
- 文档能够明显体现 SIQ 的技术壁垒：多市场异构解析、证据合同、质量门禁、数据治理和受控智能体协作。

## 15. 下一步

在本设计文档确认后，下一步进入实现计划阶段，再执行主仓 README 全量重写与一致性校验。
