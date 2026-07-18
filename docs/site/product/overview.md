# 项目定位

SIQ Research Engine 是一套面向投研机构的**可审计智能研究生产线**。项目把官方披露下载、财报与通用文档解析、结构化证据包、PostgreSQL / Milvus 沉淀、Hermes 多智能体协作，以及 NVIDIA OpenShell 安全运行面组合成一个可复核、可回放、可持续扩展的投研系统。

## 三块产品心智

1. **二级市场投研分析智能体集群** —— 研究员、基金经理、投研数据团队、合规团队使用。
2. **一级市场投研决策智能体集群** —— 投资经理、行业专家、财务/法务/风控、投委会主席使用。
3. **应用中心** —— 研究运营、数据工程、会议协作、知识库管理员使用。

三块能力共享同一个事实层、权限模型、质量门禁和审计语言。二级市场的披露证据、一级市场的尽调材料、会议陈述、智能体判断和最终决策可以在同一套 evidence / source / memory 体系中互相引用。

## SIQ 不是什么

SIQ **不是**普通 RAG、Chatbot 或单文件 PDF 问答工具，而是"从可信材料到结构化证据，再到受控智能体结论"的全链路系统。

| 产品域 | 主要用户 | 核心问题 | SIQ 交付 |
| --- | --- | --- | --- |
| 二级市场投研分析智能体集群 | 研究员、基金经理、投研数据团队、合规团队 | 多市场披露难找、PDF/XBRL 难解析、模型答案难追溯 | 官方披露检索、财报解析、LLM Wiki evidence package、分析/核查/跟踪/法务智能体 |
| 一级市场投研决策智能体集群 | 投资经理、行业专家、财务/法务/风控、投委会主席 | 尽调材料分散、专家结论难对齐、投委会过程难审计 | Deal OS、材料中心、证据构建、R0-R4 工作流、投委会多角色决策链 |
| 应用中心 | 研究运营、数据工程、会议协作、知识库管理员 | 文档、会议和知识库沉淀成本高 | 通用文档解析、会议实时/导入转写、Milvus 向量入库与知识库治理 |

## 核心目标

SIQ 的核心目标不是"让模型写一篇像研报的文章"，而是让数字、判断、风险提示、引用和行动建议都能回到：

- 官方披露
- PDF 页码
- XBRL facts
- 表格单元格
- Markdown 行
- 数据库记录
- 会议时间轴
- 投委会证据对象

对 SIQ 来说，**证据先于回答，质量门禁先于入库，审计链先于流畅表达。**

## 为什么 SIQ 难

真正难点不在"接入大模型"，而在投研事实生产的工程复杂性。

| 难点 | 说明 | SIQ 的应对 |
| --- | --- | --- |
| 官方源异构 | CNINFO、HKEXnews、SEC EDGAR、ESEF、EDINET、DART 的标识、格式和请求策略完全不同 | `market-report-finder` 按市场隔离实体解析、官方查询、下载目录和限速策略 |
| 文档形态异构 | PDF、HTML、iXBRL、XBRL、ESEF ZIP、EDINET/DART XML、Office、图片和网页需要不同解析路径 | `pdf-parser`、`document-parser` 与市场 adapters 分层处理 |
| 证据要求高 | 投研结论必须追到页码、表格、行列、bbox、anchor、XBRL tag 或 hash | `document_full.json`、`source_map.json`、`quality_report.json`、evidence package 统一表达 |
| 质量风险高 | 低质量解析一旦进入数据库或向量库，会长期污染问答和报告 | warning/fail package 默认阻断 PostgreSQL import 和 Milvus dry-run |
| 多角色协作难 | 分析、核查、跟踪、法务、投委会角色需要共享事实层，又不能越权 | Hermes profiles 按岗位职责建模 |
| 运行安全难 | 智能体需要终端、文件、代码和网络能力，但不能改源码、Prompt、固化事实或泄露凭据 | 自研 NVIDIA OpenShell+Hermes 方案将执行面放入受控沙箱 |

## 核心创新

### 1. 官方披露直连

SIQ 优先连接官方披露源，而不是依赖二手聚合站。

### 2. LLM Wiki 证据包

SIQ 的事实底座不是一组来源不明的向量 chunk，而是按市场、公司、报告期和披露来源组织的文件型证据包。**向量库失效可以重建，事实源不丢。**

### 3. 多市场规则与质量门禁

`services/market-report-rules` 把市场差异留在 `markets/<code>` 模块中，输出统一的 `financial_data`、`financial_checks` 和 `load_plan`。

### 4. 职责型智能体集群

Hermes profiles 不是"多个人格聊天"，而是岗位合同。

### 5. 拟人化全量记忆系统

详见 [拟人化记忆系统](../architecture/memory.md)。核心原则：**记忆提供连续性，证据决定事实。**

### 6. 自研 NVIDIA OpenShell + Hermes 组合方案

详见 [OpenShell 安全运行面](../architecture/openshell.md)。