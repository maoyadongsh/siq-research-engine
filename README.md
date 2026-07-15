# SIQ Research Engine

SIQ Research Engine 是一套面向金融研究与证据生产的本地化工作系统。它把官方披露下载、多市场财报解析、通用文档归一、结构化证据包、规则校验、PostgreSQL / Milvus 沉淀，以及受控多智能体协作串成一条可追溯、可复核、可持续扩展的研究生产线。

它的目标不是生成一段“像研报”的文字，而是让每个数字、判断、风险提示和引用都能回到官方披露文件、XBRL facts、PDF 页码、表格单元格、Markdown 行、数据库记录或法规条款。对 SIQ 来说，证据先于回答，审计链先于流畅表达。

## 项目定位

SIQ 的定位是“可审计研究生产线”，而不是普通的聊天式研究工具或单点 RAG 应用。它关注的是研究链路里的四个硬问题：

- 官方披露怎么稳定获取，而不是依赖二手聚合源。
- PDF、HTML、iXBRL、ESEF、EDINET、DART 这类异构材料怎么归一到同一套证据层。
- 结构化指标、质量告警、证据坐标和入库计划怎么以 contract 形式在多个服务间流转。
- 智能体怎么在证据受控的前提下工作，而不是把模型记忆伪装成事实。

这意味着 SIQ 既是一个工程化的数据与文档系统，也是一个带有严格边界的研究协作系统。

## 当前最新状态

截至 2026-07-15，SIQ 已从“多服务技术验证”推进到“可售卖样板闭环 + 平台化扩展”阶段。当前最成熟的二级市场样板是 A 股全链路研究闭环：以美的集团（000333）为代表，项目内已有年报解析产物、LLM Wiki 事实图谱、三表指标、证据链、分析报告、事实核查、跟踪报告、法务意见和 PDF 溯源产物。与此同时，港股 HKEX、美股 SEC、欧股 ESEF、日股 EDINET、韩股 DART 已形成市场隔离的下载、解析、规则、证据包和入库路径；一级市场 Deal OS、投委会 R0-R4 工作流和会议智能化则构成第二、第三条产品主线。

| 领域 | 当前状态 | 说明 |
| --- | --- | --- |
| 商业 MVP | A 股全链路研究闭环 | CNINFO / 已下载 A 股年报 -> PDF parser -> LLM Wiki / graph / metrics / evidence -> 高精度问答 / analysis -> factcheck / tracking / legal / quality gates |
| 官方披露搜索 | 六市场统一入口 | CN / HK / US / EU / JP / KR 均有官方源抽象；US 已支持 100 家主流美股中文别名到 ticker / CIK 映射 |
| 质量门禁 | package 级阻断 | warning / fail package 默认阻断入库和检索生成，只有研究员显式确认后才允许 `force=true` |
| 安全模型 | bearer + HttpOnly cookie 兼容 | 本地 bearer token 保留；公网可启用 `SIQ_AUTH_COOKIE_MODE=1`，登录 token 不再落 localStorage |
| 评测体系 | 静态样本 + 分析核查 + E2E | A 股样板已有解析质量、三表指标、PDF 溯源、分析报告质量检查和 factcheck；多市场 package eval 继续用于扩展验证 |
| 架构治理 | owner boundary 收口中 | package action、parser route payload、agent runtime 大文件继续拆分，但不阻塞 MVP |
| 智能体协作 | 二级市场 + 一级市场并行 | 二级市场助手/分析/核查/跟踪/法务已成体系；一级市场 Deal OS 已具备材料、证据、争议、R0-R4 决策与审计闭环 |
| 会议智能化 | Web 实时链路 + iOS 原生候选链路 | 会话、流式转写、说话人、术语库、声纹、纪要、导出与回放已有合同；原生锁屏采集仍受真实设备发布门禁约束 |
| 下一阶段 | OpenShell v0.6 任务书已固化 | 面向大规模架构调整的任务边界已记录；任务书是规划，不代表相关能力已经交付 |

当前路线非常明确：先把“可信披露解析 + 质量门禁 + 可追溯入库”做成能演示、能复核、能销售的样板，再把同一套 contract 扩展到更多市场、更多问答评测和更多智能体工作流。

## 三条业务主线

| 产品线 | 核心用户 | 已覆盖的关键工作 | 可交付价值 |
| --- | --- | --- | --- |
| 二级市场研究平台 | 研究员、基金经理、数据与合规团队 | 六市场披露搜索、财报解析、指标与证据、智能分析、事实核查、持续跟踪、法务合规 | 缩短从披露到可引用研究结论的时间，降低错误数字和无来源判断进入报告的概率 |
| 一级市场 Deal OS | 投资经理、行业专家、财务/法务/风控、投委会主席 | 项目与材料管理、数据室、证据构建、专家 R1 报告、争议裁决、R2/R3 汇总、R4 决策与人工确认 | 把尽调与投委会从散落文档和口头判断转成可回放、可签核、可审计的决策链 |
| 会议智能化 | 投研、投委会、访谈与内部协作团队 | 实时/导入转写、说话人轨道、术语纠错、声纹授权、纪要和行动项、版本化导出、音频证据回放 | 将会议内容直接沉淀为带时间轴和责任人的研究资产，减少二次整理和信息损耗 |

三条业务线共享同一套身份权限、证据对象、存储、检索、质量门禁和审计语言。这种复用不是简单共用 UI，而是让披露事实、尽调材料、会议陈述、智能体判断和最终决策可以在同一个证据图谱中相互引用。

## 商业价值

SIQ 面向的不是“让模型读一份 PDF 后回答几句”，而是投研组织里更难、更贵、更容易出错的事实生产环节。它的商业价值体现在四个层面：

| 价值层 | 对客户的意义 | SIQ 的实现方式 |
| --- | --- | --- |
| 降低资料获取成本 | 研究员不用在多个交易所和披露网站反复手工检索 | 多市场官方源 finder、公司主体解析、批量下载与目录治理 |
| 提高事实可信度 | 数字、表格、报告期和引用可以追溯到原始披露 | `source_map`、页码、表格索引、XBRL facts、artifact hash |
| 降低入库风险 | 低质量解析不会静默污染数据库和知识库 | evidence package quality gates、warning/fail 阻断、force override 审计 |
| 提升组织协作 | 分析、核查、跟踪、法务和投委会角色共享同一事实层 | Hermes profiles、PostgreSQL / Milvus 混合记忆、Deal OS artifact |

这让 SIQ 适合三类客户或场景：

- 二级市场研究团队：需要把官方披露、结构化指标、报告证据和复核流程做成稳定生产线。
- 投研数据工程团队：需要把 PDF / HTML / XBRL / ESEF / EDINET / DART 变成可入库、可评测、可重跑的事实资产。
- 一级市场投委会或尽调团队：需要多个专家角色在同一证据底座上形成可回放的 R1-R4 决策链。

## 为什么 SIQ 难

真正难的地方不在“接入大模型”，而在把跨市场研究所需的事实层做对、做稳、做可审。

- 披露源异构：A 股、港股、美股、欧股、日股、韩股的官方入口、标识体系、文件格式和报告周期并不相同。
- 解析链路异构：同样是年报，可能来自 PDF、HTML、XBRL、iXBRL、ZIP 包或图片化扫描件，不能用单一 parser 心智覆盖。
- 证据链要求高：研究结论不仅要“看起来合理”，还要能回到页码、表格、bbox、anchor、source id 和 load plan。
- 规则层复杂：不同市场、会计准则、行业和公司披露口径存在差异，必须由 market rule 和 contract 明确表达边界。
- 智能体治理难：分析、核查、跟踪、法务和投委会角色都需要共享同一事实层，但职责、禁止行为和输出边界不能混淆。

因此，SIQ 的价值建立在“事实层 + 规则层 + 协作层”的复合工程能力上，而不是建立在单个模型回答得多像人类分析师。

## 核心创新

### 1. 官方披露直连

SIQ 优先面向官方披露入口工作，包括 CNINFO、HKEXnews、SEC EDGAR、ESEF 聚合、EDINET 和 DART。系统首先解决“可信来源”问题，再处理解释与消费问题。

### 2. 多市场异构解析

不同市场的披露形态不被硬塞进同一条低精度流水线，而是通过市场下载服务、PDF 解析、通用文档解析和 market rules 服务分层消化。这样既保留市场差异，也维持统一上层消费接口。

### 3. 统一证据合同与可追溯引用

SIQ 通过 `document_full.json`、`quality_report.json`、`source_map.json`、`financial_data.json`、`financial_checks.json` 和 market `evidence package` 等标准产物，把“解析结果”变成“可被系统协作的事实资产”。

### 4. LLM Wiki 知识库架构

SIQ 的主证据库不是传统意义上“把文档切块后写入向量库”的 RAG，而是以 `data/wiki/<market>/companies/.../reports/...` 为核心的 LLM Wiki evidence package 架构。每份披露材料会被整理成带有 manifest、质量报告、结构化指标、source map、table index、artifact hash 和 parser 产物的文件型知识包；PostgreSQL 和 Milvus 分别作为结构化索引与语义召回索引，围绕这份 Wiki package 同步构建，而不是取代它。

这套架构的关键优势是：权威事实层可审计、可重跑、可迁移，向量层可重建、可降级。模型或智能体消费的不是一组来源不明的 chunk，而是一组有市场、公司、报告期、质量门禁、页码、表格、单元格、XBRL tag、hash 和 evidence id 的证据对象。研究员可以从答案回到 Wiki package，再回到原始披露和解析坐标；系统也可以用同一份 package 支撑 PostgreSQL 入库、Milvus dry-run、质量评测和 Agent 引用。

从知识库演进看，SIQ 更接近“Agentic LLM Wiki”而不是单点 RAG：RAG 负责大范围查证据，LLM Wiki 负责把披露、指标、表格和引用编译成可维护的长期知识结构，Hermes 智能体再基于这套结构进行任务规划、核查、补证和结果沉淀。也就是说，SIQ 不是每次提问都从零开始临时拼 chunk，而是把每次解析、校验和研究输出转化为可复用的证据资产。

LLM Wiki 对 SIQ 的优势主要体现在六个方面：

- 知识资产化：PDF、HTML、XBRL、表格和图片不再只是被临时召回的文本，而是沉淀为按市场、公司、报告期和披露来源组织的长期证据包。
- 结构可维护：manifest、metrics、qa、tables、parser artifacts 分层存放，既方便人审阅，也方便程序增量读取、diff、重建和迁移。
- 证据可核验：每个关键事实都尽量带有 source map、page、table、row、column、anchor、XBRL tag 或 hash，研究结论可以回到原始披露。
- 检索可组合：Wiki package 是事实源，PostgreSQL 提供精确结构化查询，Milvus 提供语义召回，reranker 负责精排，任何一层失效都可以降级。
- 协作可复用：分析、核查、跟踪、法务、投委会等 Hermes 角色围绕同一份 evidence package 工作，减少“每个助手各读一遍材料”的不一致。
- 治理可闭环：quality gates、warning/fail、force override、stable id 和 artifact hash 让入库、问答、评测和回放共享同一套审计语言。

| 维度 | 传统 RAG | SIQ LLM Wiki evidence package |
| --- | --- | --- |
| 主事实层 | 向量库 chunk 常成为事实入口 | Wiki package 是权威证据包，向量库只是可重建索引 |
| 知识形态 | 临时文本片段，面向单次回答 | 可版本化、可复核、可重跑的文件型知识包 |
| 可追溯性 | 通常只能追到原文片段或文件名 | 可追到 source map、页码、表格、行列、XBRL fact、artifact hash |
| 质量控制 | 低质量解析可能直接入库召回 | quality gates 先判断 warning/fail，再决定能否入库或生成检索层 |
| 维护方式 | chunk 更新后难以判断影响面 | manifest、stable id、hash、load plan 支持差异分析和幂等重跑 |
| 检索方式 | 主要依赖向量相似度 | Wiki 事实包 + PostgreSQL 精确查 + Milvus 语义召回 + reranker |
| 多市场适配 | 所有市场常被统一切块处理 | market rules 保留 HKEX、SEC、ESEF、EDINET、DART 等市场差异 |
| 重跑与审计 | chunk 变动后较难解释差异 | stable id、manifest、hash 和 package contract 支持回放与对账 |
| 智能体协作 | 模型拿到相似文本后自行判断 | Hermes 围绕同一 evidence package 共享事实、边界和引用 |
| 业务闭环 | 问答、入库、评测常是割裂链路 | 同一 package 支撑问答引用、质量门禁、数据库导入、向量 dry-run 和 eval |

因此，LLM Wiki 不是“RAG 的另一个存储目录”，而是 SIQ 的研究事实底座：它把披露解析、质量门禁、结构化财务、检索索引和智能体引用收束到同一套可审计合同里。

### 5. 受控多智能体协作

Hermes profiles 不以“人格化助手”方式组织，而以研究职责组织。分析、核查、跟踪、法务和投委会角色围绕同一证据层协作，但各自承担不同任务和边界，避免幻觉式越权输出。

### 6. 质量门禁驱动的入库链路

SIQ 把 evidence package 的质量状态前置到产品交互和 API 语义中。证据覆盖不足、三大表缺失、hash mismatch、parser warning 或 rule warning 不再只是日志，而会直接影响 PostgreSQL 导入和 Milvus 生成动作。研究员可以 force override，但必须显式确认，系统保留审计痕迹。

### 7. 市场边界内的智能检索

搜索下载助手遵守用户选择的市场边界。用户选择美国市场后，中文输入“英伟达”会在 US alias catalog 内映射到 `NVDA / CIK 1045810`，再进入 SEC EDGAR 查询；不会因为中文输入而误判为 A 股。解析失败时，所有市场都会提示用户直接输入准确股票代码、CIK、EDINET code、DART corp code 或本地市场代号。

## 商业 MVP：A 股全链路研究闭环

首个可售卖样板应以 A 股年度报告解析与研究生产闭环为主，并以美的集团（000333）2025 年报作为主样板。这个样板不是单一 PDF 解析结果，而是一条已经落在项目目录里的闭环：年报解析、artifact hash、三表指标、evidence、事实图谱、语义增强、分析报告、事实核查、跟踪报告、法务意见和 PDF 溯源产物在同一公司 Wiki 下协同工作。当前美的样板已沉淀 111 个三表科目、83 条 evidence、45 个 graph fact、80 个 segment 和 9 个 claim，可用于展示“极致高精度解析 + 证据链可回溯 + 智能体输出可校验”的完整能力。

```text
CNINFO / 已下载 A 股年报 PDF
  -> PDF parser
  -> document_full / table_index / quality_report / financial_data / financial_checks / artifact hash
  -> LLM Wiki report / metrics / evidence_index / graph facts / semantic enrichment
  -> evidence-first 高精度问答 / PDF page + table + Markdown line 溯源
  -> analysis report / factcheck / tracking / legal opinion
  -> 智能体输出财务校验、质量门禁与后续入库 / 检索生成
```

MVP 的核心不是“能解析一个 PDF”，而是证明以下能力可重复运行：

- 极致高精度解析可复核：A 股 PDF 解析生成 Markdown、`document_full.json`、`table_index.json`、质量报告、财务抽取结果和 artifact hash，后续重跑可以按 manifest 对账。
- 三表与指标可溯源：美的样板的 `three_statements.json` 覆盖资产负债表、利润表、现金流量表共 111 个科目，每个科目带 `task_id`、PDF 页码、表格编号和 Markdown 行号。
- 证据链可回溯可审计：`evidence_index.json` 中的 83 条 evidence 绑定官方 PDF、解析任务、页码、表格和源页面 URL，回答、分析和核查都能回到同一证据对象。
- 高精度问答有事实底座：问答不是只依赖向量相似度拼文本，而是从 LLM Wiki evidence、三表指标、事实图谱和语义索引中取证，再给出 PDF 页、表格或源码页溯源。
- 分析产物可沉淀：美的分析报告同时保留 Markdown、HTML 和 JSON 形态，能与 `semantic/llm/2025-annual/` 的 claims、risks、events、business profile 等结构化产物互相校验。
- 核查、跟踪、法务形成闭环：同一公司 Wiki 下已有 factcheck、商誉专项 factcheck、tracking 和 legal opinion，说明 Hermes 角色不是各自读材料，而是共享同一事实层完成复核、跟踪和合规判断。
- 智能体输出可财务校验：factcheck 能对分析报告做数据一致性、计算一致性、证据覆盖和风险遗漏检查；美的商誉专项核查可把遗漏或假设支撑不足标成 `request_changes`。
- 评测可量化：`parser_success_rate`、`statement_coverage`、`bridge_check_pass_rate`、`evidence_coverage_ratio`、`analysis_quality_pass`、`factcheck_block_rate` 等指标可持续跟踪。

相关 A 股样本和运行记录：

- `data/wiki/companies/000333-美的集团/reports/2025-annual/`
- `data/wiki/companies/000333-美的集团/metrics/latest/three_statements.json`
- `data/wiki/companies/000333-美的集团/evidence/evidence_index.json`
- `data/wiki/companies/000333-美的集团/graph/graph_index.json`
- `data/wiki/companies/000333-美的集团/semantic/llm/2025-annual/`
- `data/wiki/companies/000333-美的集团/analysis/000333-美的集团-2025-analysis.md`
- `data/wiki/companies/000333-美的集团/factcheck/000333-美的集团-2025-factcheck.json`
- `data/wiki/companies/000333-美的集团/factcheck/000333-美的集团-2025-goodwill-factcheck.json`
- `data/wiki/companies/000333-美的集团/tracking/report_manifest.json`
- `data/wiki/companies/000333-美的集团/legal/legal_opinion_20260608_113000.html`
- `data/wiki/documents/default/上海银行_CN_601229_2025-12-31_年报_2026-04-23_manual_ec74b117-sample-a/`
- `data/pdf-parser/output/*/601288_2024_农业银行_农业银行2024年度报告_2025-03-29/`
- `data/pdf-parser/output/*/000002_2024_万科A_2024年年度报告_2025-04-01/`

## 能力矩阵

| 能力层 | A 股 | 港股 | 美股 | 欧股 | 日股 | 韩股 | 通用文档 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 官方披露搜索与下载 | CNINFO | HKEXnews | SEC EDGAR | ESEF / 本地披露入口 | EDINET | DART / OpenDART | URL / 本地文件 |
| 专业解析 | PDF / MinerU / 财务抽取 | PDF / package build | HTML / iXBRL / XBRL package | PDF / ESEF package | PDF / XBRL package | PDF / XML zip package | PDF / HTML / Office / 图片 / 文本 |
| 质量报告 | `quality_report.json` | package quality | package quality | package quality | package quality | package quality | `quality_report.json` |
| 证据坐标 | page / table / md line | evidence targets | filing anchors / facts / sections | table / evidence map | filing anchors / sections | XML / PDF anchors | block / page / table / figure |
| 规则与校验 | A 股三表与勾稽 | HK rule profile | SEC rule profile | IFRS / ESEF rule profile | EDINET rule profile | DART rule profile | schema extraction / table relations |
| 存储沉淀 | Wiki / PostgreSQL / Milvus | Wiki / PostgreSQL / Milvus | Wiki / PostgreSQL / Milvus | Wiki / PostgreSQL / Milvus | Wiki / PostgreSQL / Milvus | Wiki / PostgreSQL / Milvus | Wiki / PostgreSQL / Milvus |
| 智能体消费 | 助手 / 分析 / 核查 / 跟踪 / 法务 | 同上 | 同上 | 同上 | 同上 | 同上 | 助手 / 工作流 / 抽取 |

## 安全与治理

SIQ 的安全模型围绕“本地研发可用、公网演示可控、证据访问受限”展开。

| 治理点 | 当前能力 |
| --- | --- |
| 登录会话 | bearer token 兼容 + `SIQ_AUTH_COOKIE_MODE=1` HttpOnly cookie 模式 |
| Source 访问 | source token 与登录 token 分离，页面图、artifact、下载文件走受控 API |
| 路径治理 | 下载、parser result、Wiki、artifact 均通过 root whitelist 和相对路径解析 |
| Package 动作 | warning/fail package 默认阻断入库和向量生成，force 动作需显式确认 |
| 记忆权限 | `user_private`、`project_shared`、`system_shared` 分层，PostgreSQL ACL 与 Milvus 召回过滤结合 |
| CI / Eval | 单测、构建、E2E、静态 eval run 和脚本语法检查分层覆盖 |

## 系统架构

```text
官方披露源 / 本地文件 / URL / 既有 MinerU 目录
  -> 下载与主体解析
  -> PDF / HTML / iXBRL / ESEF / XML / Office / 文本解析
  -> quality report / source map / financial data / evidence package / load plan
  -> Wiki / PostgreSQL / Milvus / 本地文件系统
  -> API 聚合后端
  -> Web 工作台 + Hermes 智能体
```

可以把 SIQ 分成六层：

1. 控制面：`apps/web` 与 `apps/api`，负责交互、鉴权、任务编排、流式事件和统一访问入口。
2. 下载面：`services/market-report-finder`，负责公司主体解析、官方披露发现与原始文件下载。
3. 解析面：`apps/pdf-parser` 与 `apps/document-parser`，负责把原始材料变成标准 artifact。
4. 规则面：`services/market-report-rules` 与 `packages/market-contracts`，负责 market-specific 提取、校验、load plan 和 contract 复用。
5. 证据面：`data/wiki`、PostgreSQL、Milvus 与本地 artifacts，负责持久化事实层和检索层。
6. 协作面：`agents/hermes`，负责把分析、核查、跟踪、法务和投委会流程接入统一证据底座。

## 关键数据合同

| 产物 | 默认位置 | 作用 |
| --- | --- | --- |
| `document_full.json` | `data/pdf-parser/results/<task_id>/` 或 `data/document-parser/results/<task_id>/` | 文档级统一事实合同 |
| `quality_report.json` | 同上或 package `qa/` | 质量门禁、告警与解析可信度说明 |
| `source_map.json` | 同上或 package `qa/` | 页面、块、表格、坐标、来源映射 |
| `financial_data.json` | `metrics/financial_data.json` | 结构化财务事实层 |
| `financial_checks.json` | `metrics/financial_checks.json` | 勾稽、验证与风险告警 |
| `normalized_metrics.json` | package `metrics/` | 统一口径指标入口 |
| market `evidence package` | `data/wiki/<market>...` | 多市场入库、检索、回放和 Agent 消费单元 |

这些合同不是“导出文件”，而是跨服务协作边界。Web、API、rules、importer、Milvus 和 Hermes 都围绕这些标准产物消费或增强事实层。

## 典型工作流

### 工作流 1：官方披露下载到研究入口

1. 用户在 Web 工作台选择市场并解析公司主体。
2. `market-report-finder` 调用官方来源查询并下载原始披露文件。
3. 下载结果按市场与公司目录落盘，并写入元数据索引。

### 工作流 2：财报或文档解析

1. A 股或 PDF 类入口交给 `apps/pdf-parser`。
2. 通用文件、URL、Office、HTML 或已有 MinerU 目录交给 `apps/document-parser`。
3. 解析服务生成 Markdown、artifact、source map、quality report、financial data 或 table relations。

### 工作流 3：规则校验与证据包构建

1. `services/market-report-rules` 根据市场 profile 读取结构化产物。
2. 生成 `financial_data`、`financial_checks`、`load_plan` 和 evidence targets。
3. `packages/market-contracts` 提供共享 contract 校验与 package 读取能力。

### 工作流 4：证据层沉淀

1. 产物进入 Wiki 目录作为文件型事实资产。
2. `db/imports` 把 structured facts 写入 PostgreSQL。
3. `scripts/vector-index` 把可检索材料写入 Milvus。

### 工作流 5：研究协作与回放

1. `apps/api` 把报告、artifact、source 链接、jobs 和 Agent 会话统一暴露给前端。
2. `apps/web` 承载下载、解析、质量复核、报告阅读、系统状态和向量入库控制台。
3. `agents/hermes` 在受控边界内消费同一证据层，输出分析、核查、跟踪和法务结论。

## 技术栈

| 层 | 选型 | 作用 |
| --- | --- | --- |
| 前端 | React 19、React Router 7、Vite 8、TypeScript 6 | 研究工作台与交互界面 |
| 样式与组件 | Tailwind CSS 4、Radix UI、lucide-react、class-variance-authority | 统一 UI 语义与交互壳层 |
| 控制面后端 | FastAPI、SQLModel、SSE Starlette、Uvicorn | 鉴权、任务编排、Agent 流式代理、系统入口 |
| 文档与 PDF 解析 | Flask、pypdf、MinerU bridge、VLM 上游 | 财报解析、通用文档归一、质量产物生成 |
| 市场规则与契约 | FastAPI、Pydantic、shared contracts | 多市场提取、校验、load plan、evidence package contract |
| 数据存储 | SQLite、PostgreSQL、Redis、Milvus、文件系统 Wiki | 状态、事实层、缓存、语义层、证据层 |
| 模型与检索 | MinerU、vLLM、embedding / reranker、Hermes gateway | OCR / 解析、生成、检索与智能体执行 |
| 会议语音 | Web Audio / AudioWorklet、WebSocket、ASR / diarization、Capacitor 8、Swift AVFoundation | 实时与导入转写、说话人、纪要、回放及隔离式 iOS 原生采集候选链路 |
| 运维与编排 | Docker Compose、systemd user units、shell scripts | 本地服务编排和模型服务管理 |

## 智能体统一记忆系统

SIQ 的智能体记忆系统不是简单的“聊天历史摘要”，而是一套围绕金融研究准确性、用户隔离、项目协作和证据可追溯设计的混合记忆架构。它把 PostgreSQL、Milvus、本机 embedding、本机 reranker、Hermes profile 知识和 Deal OS 项目输出组合成一个可治理的长期记忆底座。

核心原则是：**PostgreSQL 负责记忆真实性和治理，Milvus 负责语义召回速度和泛化，reranker 负责最终相关性判断，时间曲线负责自然遗忘。**

### 记忆系统架构

```text
用户问题 / 智能体任务
  -> API 鉴权、session、profile、deal/project scope 解析
  -> PostgreSQL siq_app.agent_memory
       保存权威记忆、消息、摘要、权限、来源、反馈、时间和有效期
  -> Milvus siq_agent_memory
       保存 Hermes profile 知识和动态记忆的向量索引
  -> Hybrid Retrieval
       Milvus dense recall + PostgreSQL lexical recall + ACL / scope 过滤
  -> 本机 reranker
       对合并候选做精排
  -> 时间遗忘曲线
       动态记忆按 30 天半衰期衰减，硬指令全量检索时绕过
  -> Hermes prompt context
       注入可追溯、已过滤、可降级的 memory context
```

### PostgreSQL 与 Milvus 的职责分工

| 层 | 角色 | 存什么 | 是否权威 | 是否使用 embedding | 是否参与 rerank |
| --- | --- | --- | --- | --- | --- |
| PostgreSQL `siq_app.agent_memory` | 权威记忆账本 | session、message、memory_items、summary、runs、tool_events、ACL、feedback、source、updated_at、valid_until | 是 | 初始 lexical recall 不依赖 embedding | 候选会进入 reranker |
| Milvus `siq_agent_memory` | 语义召回索引 | profile 文件 chunk、动态 memory item 向量、过滤字段、`updated_at_ts` | 否 | 是，用本机 embedding 写入和查询 | 候选会进入 reranker |
| 本机 embedding 服务 | 向量化 | 查询文本、profile chunk、动态 memory item | 否 | 提供向量 | 不直接排序 |
| 本机 reranker | 精排 | PostgreSQL + Milvus 合并候选 | 否 | 可使用 rerank 模型 | 是，负责最终相关性重排 |

因此，PostgreSQL 和 Milvus 不是重复存储同一份“事实”。PostgreSQL 是真相来源，Milvus 是可重建的高性能语义索引。Milvus 丢失或重建不会改变权威记忆，只影响语义召回性能。

### 记忆类型与可见性

| 类型 | 默认可见性 | 典型来源 | 用途 |
| --- | --- | --- | --- |
| `user_private` | 当前用户 | 用户明确说“请记住”、偏好、纠错、个人工作习惯 | 二级市场问答连续性、个人偏好、历史纠错 |
| `project_shared` | deal/project 成员 | 一级市场 IC 报告、R1/R2/R3/R4 输出、风险结论、法务扫描、财务审计 | Deal OS 团队共享、IC 多角色协作、项目决策回放 |
| `system_shared` | 系统可见 | Hermes profile 文件、共享政策、工具说明、流程规则 | 智能体角色能力、工具边界、工作流知识 |

一级市场智能体的共享记忆围绕 `deal_id/project_id` 工作。二级市场智能体默认使用用户私有记忆，避免个人聊天、偏好和历史纠错泄漏给其他用户。

### 混合检索与排序

SIQ 采用多阶段召回，而不是单一路径 RAG：

| 阶段 | 机制 | 目的 |
| --- | --- | --- |
| 1. Scope 解析 | `tenant_id`、`user_id`、`profile`、`agent_group`、`deal_id/project_id` | 确保只检索当前用户或当前项目可见的记忆 |
| 2. Milvus dense recall | `siq_agent_memory` collection + embedding query | 找语义相近的 profile 知识和长期记忆 |
| 3. PostgreSQL lexical recall | `memory_items` 文本、标题、类型、时间与状态过滤 | 找关键词精确命中的权威记忆 |
| 4. ACL 与有效期过滤 | visibility、owner、deal/project、`valid_from/valid_until` | 防止越权、过期和已删除记忆进入候选 |
| 5. reranker 精排 | 本机 reranker 对合并候选排序 | 提高最终相关性，减少向量误召回 |
| 6. 时间遗忘曲线 | 动态记忆按 30 天半衰期衰减 | 让近期经验自然优先，降低旧偏好污染 |
| 7. Prompt 注入 | `<memory-context>` 块 | 给 Hermes 注入可追溯、可降级的上下文 |

时间曲线只作用于动态记忆。静态 profile 知识如 `SOUL.md`、`AGENTS.md`、`TOOLS.md` 不衰减，因为它们代表智能体身份、工具和职责边界，不应因为时间变旧而降低权重。

当用户明确要求“全量检索”“所有记忆”“所有内容”“完整历史”“不要遗忘”时，系统进入 hard full recall 模式：不加半衰期，并提高召回上限，但仍保留权限过滤和上下文长度保护。

### 记忆质量治理

| 治理能力 | 说明 |
| --- | --- |
| 显式记忆提取 | 只有用户明确表达“请记住 / 我的偏好 / 以后默认”等内容时才自动晋升，避免隐式污染 |
| 纠错分类 | “你之前说错了 / 更正 / 以后不要”等输入沉淀为 `correction`，优先用于修正历史错误 |
| 精确去重 | 同一 scope 下相同 normalized content 不重复写入，只更新置信度、重要度和 metadata |
| 有效期过滤 | PostgreSQL 查询过滤 `valid_from/valid_until`，过期记忆不进入召回 |
| 反馈事件 | `feedback_events` 为“有用 / 错误 / 过期 / 删除”这类人工治理预留正式入口 |
| 超时降级 | 默认记忆检索预算为 1200ms，超时自动跳过，不阻断智能体响应 |
| 来源追踪 | 记忆保留 `source_type/source_id/source_path`，可追溯到聊天、报告、profile 文件或 Deal OS artifact |

### Hermes profile 知识入库

Hermes 智能体配置文件会被离线切块、embedding 并写入 Milvus 专用 collection：

```bash
cd /home/maoyd/siq-research-engine/apps/api
uv run python ../../scripts/hermes/ingest_agent_memory_to_milvus.py --dry-run
uv run python ../../scripts/hermes/ingest_agent_memory_to_milvus.py --batch-size 64
```

默认 collection：

```text
siq_agent_memory
```

该 collection 保存：

- 二级市场 profile：`siq_assistant`、`siq_analysis`、`siq_factchecker`、`siq_tracking`、`siq_legal`
- 一级市场 IC profile：主席、战略、行业、财务、法务、风控、协调员
- 共享规则：`shared`、`siq_ic_shared`
- 动态长期记忆：用户私有记忆和项目共享记忆的向量索引

### 记忆系统优势与创新点

| 维度 | 传统聊天记忆 | SIQ 智能体记忆 |
| --- | --- | --- |
| 存储方式 | 会话摘要或本地缓存 | PostgreSQL 权威记忆 + Milvus 可重建向量索引 |
| 权限隔离 | 常依赖 session id 或应用约定 | 显式 `user_id/profile/deal_id/visibility` 与 ACL 过滤 |
| 检索方式 | 单一路径向量召回 | Milvus dense + PostgreSQL lexical + reranker + 时间曲线 |
| 事实安全 | 容易把记忆当事实 | 当前问题和可验证证据优先，记忆只是上下文 |
| 时间感 | 旧记忆长期同权 | 动态记忆 30 天半衰期，硬指令全量检索可绕过 |
| 项目协作 | 多人共享容易串扰 | 一级市场 `project_shared` 记忆按 deal/project 隔离 |
| 可治理性 | 难以审核和删除 | feedback、source、status、valid_until、dedupe、correction |
| 性能保护 | 检索慢会拖累回答 | 1200ms 预算、失败降级、主链路优先 |

这使 SIQ 的记忆系统更接近研究组织里的真实协作方式：个人有偏好和历史，项目有共享底稿和阶段结论，系统有稳定角色知识，旧经验会自然淡出，但用户明确要求时又可以完整追溯。

## 仓库地图

| 路径 | 职责 |
| --- | --- |
| `apps/web` | Web 工作台，承载下载、解析、报告与 Agent 协作入口 |
| `apps/api` | API 聚合后端，统一鉴权、代理、任务和系统状态 |
| `apps/pdf-parser` | 财报 PDF 解析、质量门禁、财务抽取与溯源 |
| `apps/document-parser` | 通用文档解析、artifact 归一、Schema 抽取 |
| `apps/ios-meeting-capture` | Capacitor/Swift 原生会议采集候选实现与真机发布门禁 |
| `services/market-report-finder` | 多市场官方披露搜索与下载 |
| `services/market-report-rules` | 多市场 extraction / validation / load plan 规则服务 |
| `packages/market-contracts` | evidence package shared contract 与 reader |
| `agents/hermes` | 研究与投委会多智能体 profiles、共享脚本和协作规则 |
| `db/imports` | PostgreSQL 导入与结构化查询工具 |
| `scripts` | 评测、运维、批处理、Hermes 冒烟和向量入库脚本 |
| `infra/model-services` | 本地模型服务与 systemd 启动脚本 |
| `datasets` | 可版本化稳定样本、fixtures 和小型测试数据 |
| `eval_datasets` | 历史评测语料与回归集 |
| `data` | 历史兼容运行态目录 |
| `var` | 新增本地运行态推荐目录 |
| `artifacts` | 构建、测试、评测与批处理产物目录 |

## 快速启动

### 本地一键启动

```bash
cd /home/maoyd/siq-research-engine
cp infra/env/local.example infra/env/local.env
export SIQ_AUTH_SECRET_KEY="${SIQ_AUTH_SECRET_KEY:-$(openssl rand -hex 32)}"
export SIQ_SOURCE_TOKEN_SECRET="${SIQ_SOURCE_TOKEN_SECRET:-$(openssl rand -hex 32)}"
./start_all.sh
```

默认 Web 入口：

```text
http://127.0.0.1:15173
```

### Docker Compose 启动

```bash
cd /home/maoyd/siq-research-engine
docker compose -f infra/docker/docker-compose.yml --env-file infra/env/local.env up
```

如需额外 profile：

```bash
docker compose -f infra/docker/docker-compose.yml \
  --env-file infra/env/local.env \
  --profile external-services \
  --profile monitoring \
  up
```

### 常见可选开关

```bash
SIQ_START_HERMES_GATEWAYS=0 ./start_all.sh
SIQ_START_MARKET_REPORT_RULES=1 ./start_all.sh
SIQ_START_MARKET_REPORT_FINDER=1 ./start_all.sh
SIQ_START_VECTOR_INGEST=1 SIQ_MILVUS_COLLECTION=ic_collaboration_shared ./start_all.sh
```

## 健康检查

```bash
curl -s http://127.0.0.1:15173
curl -s http://127.0.0.1:18081/health
curl -s http://127.0.0.1:15000/api/ready
curl -s http://127.0.0.1:15010/api/ready
curl -s http://127.0.0.1:18000/health
curl -s http://127.0.0.1:18020/healthz
curl -s http://127.0.0.1:18642/health
curl -s http://127.0.0.1:18649/health
curl -s http://127.0.0.1:18650/health
curl -s http://127.0.0.1:18651/health
curl -s http://127.0.0.1:18652/health
```

## 关键环境变量

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `SIQ_PROJECT_ROOT` | 仓库根目录 | 项目路径锚点 |
| `SIQ_LOCAL_STATE_ROOT` | 仓库根目录 | 本地状态根；推荐新环境设到外部盘或用户 state 目录 |
| `SIQ_DATA_ROOT` | `$SIQ_LOCAL_STATE_ROOT/data` | 历史兼容运行态根目录 |
| `SIQ_RUNTIME_ROOT` | `$SIQ_LOCAL_STATE_ROOT/var` | 新增本地运行态建议根目录 |
| `SIQ_ARTIFACTS_ROOT` | `$SIQ_LOCAL_STATE_ROOT/artifacts` | 生成产物目录 |
| `SIQ_DATASETS_ROOT` | `datasets` | 可版本化样本目录 |
| `SIQ_WIKI_ROOT` | `$SIQ_DATA_ROOT/wiki` | 文件型事实层目录 |
| `SIQ_REPORT_DOWNLOADS_ROOT` | `$SIQ_DATA_ROOT/market-report-finder/downloads` | 官方披露下载目录 |
| `SIQ_PDF2MD_API_BASE` | `http://127.0.0.1:15000` | PDF 解析服务地址 |
| `SIQ_DOCUMENT_PARSER_API_BASE` | `http://127.0.0.1:15010` | 通用文档解析服务地址 |
| `SIQ_REPORT_FINDER_BASE` | `http://127.0.0.1:18000` | 市场披露下载服务地址 |
| `SIQ_MARKET_REPORT_RULES_BASE` | `http://127.0.0.1:18020` | 多市场规则服务地址 |
| `SIQ_HERMES_HOME` | `$SIQ_DATA_ROOT/hermes/home` | Hermes runtime home |
| `SIQ_AUTH_SECRET_KEY` | 无 | API 鉴权密钥，至少 32 字符 |
| `SIQ_SOURCE_TOKEN_SECRET` | fallback 到 `SIQ_AUTH_SECRET_KEY` | source access token 签名密钥 |
| `SIQ_AUTH_COOKIE_MODE` | `0` | 启用 HttpOnly cookie 登录兼容模式 |
| `SIQ_AUTH_COOKIE_SECURE` | `0` | 公网 HTTPS 部署应设为 `1` |
| `SIQ_UPDATE_DEPS` | `0` | 设为 `1` 时允许 `start_all.sh` 更新依赖；默认使用 frozen/lockfile 安装 |

## 延伸阅读

- [API 聚合后端](https://github.com/maoyadongsh/siq-research-engine/blob/master/apps/api/README.md)
- [PDF 解析服务](https://github.com/maoyadongsh/siq-research-engine/blob/master/apps/pdf-parser/README.md)
- [通用文档解析服务](https://github.com/maoyadongsh/siq-research-engine/blob/master/apps/document-parser/README.md)
- [Web 工作台](https://github.com/maoyadongsh/siq-research-engine/blob/master/apps/web/README.md)
- [统一市场公告搜索下载服务](https://github.com/maoyadongsh/siq-research-engine/blob/master/services/market-report-finder/README.md)
- [多市场财报规则服务](https://github.com/maoyadongsh/siq-research-engine/blob/master/services/market-report-rules/README.md)
- [共享 evidence package contract](https://github.com/maoyadongsh/siq-research-engine/blob/master/packages/market-contracts/README.md)
- [Hermes 智能体体系](https://github.com/maoyadongsh/siq-research-engine/blob/master/agents/hermes/README.md)
- [PostgreSQL 入库工具](https://github.com/maoyadongsh/siq-research-engine/blob/master/db/imports/README.md)
- [本地开发操作说明](https://github.com/maoyadongsh/siq-research-engine/blob/master/docs/operations/local-development.md)
