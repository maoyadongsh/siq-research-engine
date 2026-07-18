# SIQ Research Engine

SIQ Research Engine 是一套面向投研机构的可审计智能研究生产线。项目把官方披露下载、财报与通用文档解析、结构化证据包、PostgreSQL / Milvus 沉淀、Hermes 多智能体协作，以及 NVIDIA OpenShell 安全运行面组合成一个可复核、可回放、可持续扩展的投研系统。

当前产品心智分为三块：

1. 二级市场投研分析智能体集群。
2. 一级市场投研决策智能体集群。
3. 应用中心，包括文档解析、会议转写和向量入库。

SIQ 的核心目标不是“让模型写一篇像研报的文章”，而是让数字、判断、风险提示、引用和行动建议都能回到官方披露、PDF 页码、XBRL facts、表格单元格、Markdown 行、数据库记录、会议时间轴或投委会证据对象。对 SIQ 来说，证据先于回答，质量门禁先于入库，审计链先于流畅表达。

## 项目定位

SIQ 不是普通 RAG、Chatbot 或单文件 PDF 问答工具，而是“从可信材料到结构化证据，再到受控智能体结论”的全链路系统。

它服务三类高价值场景：

| 产品域 | 主要用户 | 核心问题 | SIQ 交付 |
| --- | --- | --- | --- |
| 二级市场投研分析智能体集群 | 研究员、基金经理、投研数据团队、合规团队 | 多市场披露难找、PDF/XBRL 难解析、模型答案难追溯 | 官方披露检索、财报解析、LLM Wiki evidence package、分析/核查/跟踪/法务智能体 |
| 一级市场投研决策智能体集群 | 投资经理、行业专家、财务/法务/风控、投委会主席 | 尽调材料分散、专家结论难对齐、投委会过程难审计 | Deal OS、材料中心、证据构建、R0-R4 工作流、投委会多角色决策链 |
| 应用中心 | 研究运营、数据工程、会议协作、知识库管理员 | 文档、会议和知识库沉淀成本高 | 通用文档解析、会议实时/导入转写、Milvus 向量入库与知识库治理 |

三块能力共享同一个事实层、权限模型、质量门禁和审计语言。二级市场的披露证据、一级市场的尽调材料、会议陈述、智能体判断和最终决策可以在同一套 evidence / source / memory 体系中互相引用。

## 当前状态

截至 2026-07-18，项目已从多服务技术验证进入“可演示、可复核、可扩展”的平台化阶段。

| 方向 | 当前状态 | 说明 |
| --- | --- | --- |
| 二级市场商业样板 | A 股全链路样板成熟，多市场证据包继续扩展 | 上汽集团 `600104` 作为当前主样板，已覆盖 PDF 解析、三表指标、证据、事实图谱、分析、核查、跟踪、法律意见和 OpenShell 灰度验证 |
| 官方披露入口 | CN / HK / US / EU / JP / KR 六市场入口 | US 已支持中文 alias，例如“英伟达”到 `NVDA / CIK 1045810`，且遵守市场选择边界 |
| 多市场解析与规则 | A 股 PDF、HK PDF package、SEC XBRL/iXBRL、ESEF、EDINET、DART | `services/market-report-rules` 和 `packages/market-contracts` 统一 financial data、quality gates 和 load plan |
| 一级市场 Deal OS | R0-R4 投委会工作流、材料中心、专家角色、审计链持续完善 | chairman、strategist、sector、finance、legal、risk、coordinator 等 profiles 形成决策集群 |
| 应用中心 | 文档解析、会议转写、向量入库均有独立链路 | 通用文档 artifact、meeting speech/gateway、Milvus ingest 支撑跨业务复用 |
| 智能体记忆 | Hermes 原生会话记忆 + 本地临时任务记忆 + PostgreSQL 权威长期记忆 + Milvus 语义索引 + reranker | 支持拟人化连续性、全量记忆、半衰期衰减、按需全量召回，以及 `user_private` / `project_shared` / `system_shared` 隔离 |
| NVIDIA OpenShell 安全运行面 | 自研 OpenShell+Hermes 演示/灰度控制面已验证，正式生产门禁仍为 `NO_GO` | 已真实使用 OpenShell 网关、沙箱、Provider、策略、服务转发、公司范围自动创建、对话沙箱代际、资源池租约/隔离/恢复、空闲 TTL 回收和 Host 回退；正式 A/B、人工评审和质量门禁尚未完成 |

## 为什么 SIQ 难

真正难点不在“接入大模型”，而在投研事实生产的工程复杂性。

| 难点 | 说明 | SIQ 的应对 |
| --- | --- | --- |
| 官方源异构 | CNINFO、HKEXnews、SEC EDGAR、ESEF、EDINET、DART 的标识、格式和请求策略完全不同 | `market-report-finder` 按市场隔离实体解析、官方查询、下载目录和限速策略 |
| 文档形态异构 | PDF、HTML、iXBRL、XBRL、ESEF ZIP、EDINET/DART XML、Office、图片和网页需要不同解析路径 | `pdf-parser`、`document-parser` 与市场 adapters 分层处理，不把所有材料粗暴切成 chunk |
| 证据要求高 | 投研结论必须追到页码、表格、行列、bbox、anchor、XBRL tag 或 hash | `document_full.json`、`source_map.json`、`quality_report.json`、evidence package 统一表达 |
| 质量风险高 | 低质量解析一旦进入数据库或向量库，会长期污染问答和报告 | warning/fail package 默认阻断 PostgreSQL import 和 Milvus dry-run，force override 需要显式确认 |
| 多角色协作难 | 分析、核查、跟踪、法务、投委会角色需要共享事实层，又不能越权 | Hermes profiles 按岗位职责建模，输出路径、禁止行为、证据要求和升级条件可审阅 |
| 运行安全难 | 智能体需要终端、文件、代码和网络能力，但不能改源码、Prompt、固化事实或泄露凭据 | 自研 NVIDIA OpenShell+Hermes 方案将执行面放入受控沙箱，并保留 Host 回退和 A/B 质量门 |

## 核心创新

### 1. 官方披露直连

SIQ 优先连接官方披露源，而不是依赖二手聚合站。系统先解决“来源可信”问题，再解决解析、检索和智能体消费问题。

### 2. LLM Wiki 证据包

SIQ 的事实底座不是一组来源不明的向量 chunk，而是按市场、公司、报告期和披露来源组织的文件型证据包。典型 package 包含 manifest、quality、source map、metrics、tables、parser artifact、artifact hash 和 stable id。

这使 Wiki package 成为权威事实层，PostgreSQL 是结构化索引，Milvus 是可重建的语义索引。向量库失效可以重建，事实源不丢。

### 3. 多市场规则与质量门禁

`services/market-report-rules` 把市场差异留在 `markets/<code>` 模块中，输出统一的 `financial_data`、`financial_checks` 和 `load_plan`。`packages/market-contracts` 再把 package 校验、summary/detail reader、stable id、source map 和 value polarity 变成跨服务合同。

### 4. 职责型智能体集群

Hermes profiles 不是“多个人格聊天”，而是岗位合同：分析负责形成研究判断，核查负责拆错，跟踪负责持续观察，法务负责依据和合规，一级市场 IC profiles 负责 R0-R4 决策链。每个角色共享同一证据底座，但职责和禁止行为不同。

### 5. 拟人化全量记忆系统

SIQ 的智能体记忆不是简单聊天摘要，而是让研究助手具备“长期共事感”的拟人化记忆系统。它由四层组成：

| 记忆层 | 保存内容 | 作用 |
| --- | --- | --- |
| Hermes 原生记忆 | 会话、响应、profile runtime、checkpoint、短期上下文 | 保持同一 profile 的对话连续性和工具执行状态 |
| 本地临时任务记忆 | 当前任务工作目录、报告草稿、临时 evidence、intermediate artifacts | 支撑长任务分阶段推理、重试和恢复 |
| PostgreSQL 权威长期记忆 | 用户明确偏好、纠错、项目结论、IC 阶段产物、权限、来源和有效期 | 作为可审计、可删除、可授权的长期记忆账本 |
| Milvus 语义索引 | profile 知识 chunk、动态 memory item 向量、scope metadata | 用于语义召回和泛化检索，可从权威层重建 |

这套系统支持四个关键能力：

- 拟人化连续性：助手能记住用户偏好、历史纠错、项目上下文和角色协作方式，但不会把记忆当作未经验证的事实。
- 全量记忆：长期记忆不是只保留最近几轮摘要，而是按用户、项目、profile、agent group 和可见性沉淀完整记忆项。
- 记忆半衰期30天：动态记忆默认按时间衰减，近期经验自然优先，旧偏好不会永久污染新任务。
- 按需全量召回：当用户明确要求“全量检索”“完整历史”“不要遗忘”时，系统绕过半衰期，但仍保留 ACL、scope 和上下文长度保护。

核心原则是：**记忆提供连续性，证据决定事实。** 对财务数字、法律条款、投资判断和投委会结论，当前 evidence package、数据库事实和原始材料始终优先于模型记忆。

### 6. 自研 NVIDIA OpenShell + Hermes 组合方案

SIQ 没有安装或运行 NemoClaw / NemoHermes，也没有把 Hermes 简单放进普通 Docker。项目基于 NVIDIA OpenShell `v0.0.83`、上游 commit `e3d26dd3ae0dee247bbc5db368545832757ac493` 和冻结的 Hermes `0.13.0`，构建了直接面向 SIQ 投研契约的原生集成：

```text
FastAPI Agent Runtime
  -> 运行面选择 / 公司上下文校验
  -> 对话沙箱代际
  -> 公司范围自动创建
  -> 资源池注册表 / 租约 / 隔离 / 恢复
  -> NVIDIA OpenShell gateway
  -> OpenShell sandbox / BYOC Hermes image
  -> Hermes /v1/runs
  -> OpenShell Provider / Broker / 受控外部服务
  -> 终态确认 / 写入静默后释放
  -> 空闲 TTL 清理 / Host 回退
```

这套方案保留 SIQ 现有 `/v1/runs`、SSE、停止、报告输出路径、公司 Wiki、Hermes profile、业务 Prompt 和工具流程，同时实际使用 OpenShell 网关控制面、沙箱数据面、Landlock 文件边界、进程/seccomp 边界、Provider 凭据隔离、受控服务转发、宿主出网/数据 broker、请求身份、当前公司写入边界、跨公司拒写探测、多沙箱资源池、请求级租约、API 重启恢复、运行来源回执和 Host 回退。

最新状态已经从“长期驻留手工灰度”升级为：有效公司上下文触发自动创建公司级沙箱，同一前端对话内同公司复用沙箱代际，切换公司生成隔离代际，请求结束后租约归零，空闲 TTL 后自动销毁。当前可准确表述为：**针对 SIQ 投研业务合同定制的、非 NemoClaw 路径的原生 NVIDIA OpenShell + Hermes 演示/灰度控制面**。它是项目技术壁垒之一，但正式生产切流必须等待正式 A/B、人工安全评审和 `check_v06_completion.py` 从 `NO_GO` 进入 `GO`；逐项路线见 `docs/runbooks/openshell/no-go-to-go-readiness-matrix.md`。

## 产品架构

```text
官方披露 / 尽调材料 / 会议音频 / 本地文档 / URL
  -> 应用中心
       document-parser / pdf-parser / meeting speech / vector ingest
  -> 证据层
       LLM Wiki evidence package / PostgreSQL / Milvus / artifacts
  -> 控制面
       apps/api / 鉴权 / 任务 / 来源访问 / 记忆 / 运行面选择
  -> 智能体集群
       二级市场 analysis/factcheck/tracking/legal/assistant
       一级市场 IC chairman/strategy/sector/finance/legal/risk/coordinator
  -> NVIDIA OpenShell 安全运行面
       网关 / 沙箱 / Provider / Broker / 策略 / 灰度 / 回滚
  -> Web 工作台
       二级市场 / 一级市场 / 应用中心 / 系统管理
```

## 二级市场投研分析智能体集群

二级市场集群围绕“公开披露事实到研究结论”工作。

| Profile / 能力 | 默认入口 | 职责 |
| --- | --- | --- |
| `siq_assistant` | `/chat` | 通用问答、指标解释、证据定位、报告导航 |
| `siq_analysis` | `/analysis` | 年报经营分析、风险链条、投资研究报告 |
| `siq_analysis_multi_market` | 多市场分析链路 | 面向 US/HK/EU/JP/KR 等跨市场 package 的分析和渲染 |
| `siq_factchecker` | `/verify` | 对分析报告做事实、计算、引用和风险遗漏核查 |
| `siq_factchecker_multi_market` | 多市场核查链路 | 针对多市场 artifact、XBRL/PDF 证据和 normalized metrics 做核查 |
| `siq_tracking` | `/tracking` | 持续跟踪、事件更新、预警和后续研究记录 |
| `siq_tracking_multi_market` | 多市场跟踪链路 | 多市场事件、指标和报告更新跟踪 |
| `siq_legal` | `/legal` | 法规检索、合规分析和法律意见草稿 |

典型闭环：

```text
官方披露下载
  -> 财报解析 / market package build
  -> quality gates / evidence package
  -> PostgreSQL + Milvus + Wiki
  -> analysis
  -> factcheck
  -> tracking / legal
  -> 可回溯报告与审计记录
```

## 一级市场投研决策智能体集群

一级市场集群围绕“材料、证据、专家意见、争议和投委会决策”工作。它不是把公开市场分析搬到项目尽调里，而是建立一套面向 Deal OS 的 R0-R4 过程模型。

| Profile | 职责 |
| --- | --- |
| `siq_ic_master_coordinator` | 项目编排、材料完整性、证据门禁、专家任务收口 |
| `siq_ic_chairman` | 投委会最终裁决、条件化投决、分歧处理和决策签核 |
| `siq_ic_strategist` | 战略适配、基金 thesis、宏观与入场时点 |
| `siq_ic_sector_expert` | 行业格局、产品、客户、竞争和市场判断 |
| `siq_ic_finance_auditor` | 财务一致性、预测、估值和压力测试 |
| `siq_ic_legal_scanner` | 法务尽调、条款风险、监管暴露 |
| `siq_ic_risk_controller` | 下行情景、红黄线、保护条款和风险阈值 |

一级市场的核心价值是把尽调和投委会从散落文档、口头判断和人工会议纪要，转成可回放、可签核、可复核的决策链。

## 应用中心

应用中心提供跨业务复用的基础能力。

| 应用 | 路径 | 价值 |
| --- | --- | --- |
| 文档解析 | `apps/document-parser`、Web `/documents` | 将 PDF、Office、HTML、URL、图片和既有 MinerU 目录归一为 artifact、source map、table relations 和 schema extraction |
| 财报 PDF 解析 | `apps/pdf-parser`、Web `/parse*` | 将财报 PDF 转成 Markdown、document_full、quality、financial_data、source map 和 page/table evidence |
| 会议转写 | `apps/api` meeting routers、`infra/model-services/meeting-speech`、Web `/meetings` | 实时/导入转写、说话人、术语库、声纹、纪要、行动项、音频回放和导出 |
| 向量入库 | `scripts/vector-index/milvus-ingestion`、Web `/vector-ingest` | 将 Wiki package、通用文档、法规库和项目知识转成可重建语义索引 |

应用中心的定位是“材料生产和知识沉淀能力”，它服务二级市场和一级市场，但不直接替代业务智能体集群。

## 能力矩阵

| 能力层 | 二级市场 | 一级市场 | 应用中心 |
| --- | --- | --- | --- |
| 输入材料 | 官方披露、年报、中报、公告、XBRL facts | BP、财务模型、合同、访谈、第三方报告、会议材料 | PDF、Office、HTML、URL、图片、音频、既有解析目录 |
| 事实层 | LLM Wiki package、metrics、evidence、graph facts | Deal evidence、data room、R1-R4 artifacts、project memory | document_full、source map、table relations、transcript segments、chunks |
| 存储层 | Wiki、PostgreSQL、Milvus | Wiki deals、PostgreSQL、Milvus、project_shared memory | 文件 artifact、PostgreSQL、Milvus、artifacts |
| 智能体 | assistant、analysis、factchecker、tracking、legal | coordinator、chairman、strategy、sector、finance、legal、risk | 不直接给投资结论，提供材料和知识工具 |
| 质量门禁 | parser/rules warning、evidence coverage、hash、financial checks | 材料完整性、证据充分性、争议和人工确认 | artifact contract、source map、ASR readiness、chunk metadata |
| 审计回放 | source page/table/line、report manifest、factcheck | deal audit、decision record、phase artifacts | task id、artifact hash、meeting cursor、ingest metadata |

## 技术栈

| 层 | 选型 | 作用 |
| --- | --- | --- |
| 前端 | React 19、React Router 7、Vite 8、TypeScript 6、Tailwind CSS 4、Radix UI、lucide-react | Web 工作台、二级市场、一级市场、应用中心和系统管理 |
| 控制面 | FastAPI、SQLModel、SSE Starlette、Uvicorn、Redis、JWT / HttpOnly cookie | 鉴权、任务编排、Agent stream、source access、Deal OS、会议、系统状态 |
| 解析面 | Flask、pypdf、MinerU bridge、VLM 上游、table relation、schema extraction | PDF 和通用文档解析、质量产物、表格/页图/source map |
| 市场服务 | FastAPI、Pydantic、market adapters、shared contracts | 官方披露发现、下载、market rules、financial checks、load plan |
| 数据层 | PostgreSQL、SQLite、Milvus、文件系统 Wiki、artifact hash | 权威事实账本、结构化查询、语义索引、文件型证据包 |
| 智能体 | Hermes profiles、`/v1/runs` gateway、Hermes 原生记忆、本地临时记忆、PostgreSQL/Milvus memory、reranker | 多角色分析、核查、跟踪、法务和投委会协作 |
| NVIDIA / GPU 运行面 | NVIDIA OpenShell `v0.0.83`、BYOC 沙箱、Landlock、Provider/Broker、范围自动创建、沙箱代际、vLLM、Nemotron 3 Nano Omni、Gemma NVFP4、Qwen FP8/VL 检索 | 安全执行隔离、本地/私有模型服务、GPU 推理、embedding、reranking |
| 运维 | Docker Compose、systemd user units、shell scripts、OpenShell runbooks、sanitized artifacts | 本地私有化启动、模型服务管理、安全证据和回滚 |

## 基础环境与测试情况

SIQ 面向本地私有化和单机/内网部署设计，推荐从 Linux + Docker + Python + Node 的基础环境起步。当前仓库在这台工作机上的采样基线如下，跨机器部署时以各服务 README 和 `infra/env/local.example` 为准。

| 项目 | 当前采样 | 说明 |
| --- | --- | --- |
| OS / Kernel | Linux aarch64，kernel `6.17.0-1014-nvidia` | 当前开发机带 NVIDIA kernel 变体，适合本地 GPU / vLLM / OpenShell 验证 |
| Python | `3.13.12` | 项目服务要求 Python `>=3.11`，部分运行环境可使用独立 venv |
| Node / npm | Node `v22.22.2`，npm `10.9.7` | 前端与 iOS meeting capture 合同使用 TypeScript / Vite / Capacitor |
| uv | `0.11.7` | Python 服务推荐使用 uv 管理依赖和测试 |
| Docker | `29.1.3` | Compose、OpenShell BYOC、模型服务和 sandbox 验证依赖 Docker |
| OpenShell | 固定 NVIDIA OpenShell `v0.0.83` | 项目内使用独立 gateway、patched supervisor、BYOC image 和脱敏证据目录 |

当前仓库测试资产规模：

| 测试资产 | 当前数量 | 覆盖重点 |
| --- | ---: | --- |
| Python 测试文件 | 469 | API、parser、market services、contracts、db imports、Hermes、OpenShell、model-services |
| TypeScript / Playwright / Node 测试文件 | 115 | Web 路由、工作台交互、meeting 前端协议、E2E smoke、iOS capture 合同 |
| Shell 脚本 | 69 | 启动、运维、OpenShell、Hermes、模型服务和 smoke 入口 |
| OpenShell 专项回归 | 最新状态文档记录 `78 passed` | 运行面选择、资源池绑定、租约、范围自动创建、对话沙箱代际、TTL、恢复、Host 回退和运行来源回执 |

测试体系按风险分层：

| 层级 | 命令 | 用途 |
| --- | --- | --- |
| 全仓基础门禁 | `scripts/check_all.sh` | 聚合 Python、前端、脚本、market contract 和工程 hygiene 检查 |
| 控制面 | `cd apps/api && uv run python -m pytest tests` | 鉴权、Agent runtime、Deal OS、会议、market package、source access |
| 前端 | `cd apps/web && npm run check:frontend` | ESLint、TypeScript build、Vite build |
| 前端 E2E | `cd apps/web && npm run e2e` | Playwright smoke，默认使用 mock API，不强依赖真实后端 |
| PDF / 文档解析 | `pytest -q apps/pdf-parser/tests apps/document-parser/tests` | parser artifact、source map、quality、table relation、bridge |
| 市场服务 | `uv run pytest` in `services/*` and `packages/market-contracts` | 官方披露入口、规则服务、package contract |
| PostgreSQL 入库 | `pytest -q db/imports/tests` | 多市场 schema、quality gate、幂等写入、持久化校验 |
| OpenShell 专项回归 | 见 `docs/siq-openshell-hermes-integration-status.md` | 最近记录 `78 passed`，覆盖运行面选择、资源池、租约、自动创建、对话沙箱代际、TTL 和恢复 |
| OpenShell 发布门禁 | `python3 scripts/openshell/check_v06_completion.py --json` | 当前正式生产门禁仍为 `NO_GO`；逐项路线见 `docs/runbooks/openshell/no-go-to-go-readiness-matrix.md`，只在正式证据、A/B、质量门禁和人工评审齐全后才能 GO |

README 更新本身通常只要求 `git diff --check` 和必要的文档关键词检查；涉及代码、接口、路由、contract、OpenShell 或模型运行面变更时，应运行对应层级测试。

## 关键数据合同

| 产物 | 常见位置 | 作用 |
| --- | --- | --- |
| `document_full.json` | `data/pdf-parser/results/<task_id>/`、`data/document-parser/results/<task_id>/` | 文档级统一事实合同 |
| `quality_report.json` | parser result 或 package `qa/` | 质量门禁、告警、coverage 和解析可信度 |
| `source_map.json` | parser result 或 package `qa/` | 页面、块、表格、bbox、anchor 和来源映射 |
| `financial_data.json` | package `metrics/` | 结构化财务事实 |
| `financial_checks.json` | package `metrics/` | 勾稽、校验、缺口和风险告警 |
| `normalized_metrics.json` | package `metrics/` | 跨市场统一口径指标入口 |
| market `evidence package` | `data/wiki/<market>/companies/.../reports/...` | 入库、检索、回放、Agent 消费和离线交付单元 |
| meeting transcript/event | `apps/api` meeting tables 与 artifacts | 会议时间轴、稳定片段、行动项和导出 |
| agent memory | Hermes runtime memory、local task memory、PostgreSQL `agent_memory`、Milvus `siq_agent_memory*` | 拟人化连续性、长期记忆、半衰期衰减、按需全量召回、用户私有/项目共享/系统共享知识 |

这些文件不是“导出结果”，而是跨服务协作边界。Web、API、rules、importer、Milvus 和 Hermes 都围绕这些合同消费或增强事实层。

## 仓库地图

| 路径 | 职责 |
| --- | --- |
| `apps/web` | Web 工作台，承载二级市场、一级市场、应用中心和系统管理 |
| `apps/api` | 控制面后端，鉴权、任务、Agent runtime、source access、Deal OS、会议和 OpenShell pool adapter |
| `apps/pdf-parser` | 财报 PDF 解析、质量报告、财务抽取、source map 和人工修正 |
| `apps/document-parser` | 通用文档解析、artifact 合同、table relations、schema extraction 和 source 预览 |
| `apps/ios-meeting-capture` | iOS 原生会议采集候选链路和 Capacitor 插件合同 |
| `services/market-report-finder` | 多市场官方披露搜索、主体解析和原始文件下载 |
| `services/market-report-rules` | 多市场 extraction、validation、load plan 和市场规则注册 |
| `packages/market-contracts` | evidence package shared contract、reader、hash、stable id 和 value polarity |
| `agents/hermes` | 二级市场与一级市场智能体 profiles、共享规则和岗位合同 |
| `db/imports` | PostgreSQL 导入、市场隔离 schema、持久化校验和只读查询 |
| `scripts` | 运维、批处理、评测、Hermes、OpenShell 和向量入库脚本 |
| `infra/model-services` | MinerU、vLLM、embedding、reranker、Nemotron、meeting-speech 等模型服务入口 |
| `infra/openshell` | OpenShell policy、BYOC、provider、broker、schema、patch 和参考文档 |
| `docs` | 架构设计、runbooks、任务书、状态报告和运维说明 |
| `datasets` | 新增稳定样本、fixtures 和可版本化小数据 |
| `eval_datasets` | 历史评测语料和回归集 |
| `data` | 历史兼容运行态和 Wiki 事实资产默认路径 |
| `var` | 新增本地运行态推荐目录，含 OpenShell 私有运行状态 |
| `artifacts` | 构建、测试、评测、批处理和脱敏 OpenShell 证据产物 |

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

未安装 Hermes 或只想启动核心应用时：

```bash
SIQ_START_HERMES_GATEWAYS=0 ./start_all.sh
```

不启动 OpenShell gateway / brokers：

```bash
SIQ_START_OPENSHELL_GATEWAY=0 SIQ_START_OPENSHELL_BROKERS=0 ./start_all.sh
```

### Docker Compose

```bash
cd /home/maoyd/siq-research-engine
docker compose -f infra/docker/docker-compose.yml --env-file infra/env/local.env up
```

按需启用外部服务 profile：

```bash
docker compose -f infra/docker/docker-compose.yml \
  --env-file infra/env/local.env \
  --profile external-services \
  --profile monitoring \
  up
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
curl -s http://127.0.0.1:18651/health
curl -s http://127.0.0.1:18649/health
curl -s http://127.0.0.1:18650/health
curl -s http://127.0.0.1:18652/health
python3 scripts/openshell/check_v06_completion.py --json
```

OpenShell 完成度检查当前真实门禁仍应显示 `decision=NO_GO`，不要把灰度链路存活误读成正式切流完成。

## 关键环境变量

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `SIQ_PROJECT_ROOT` | 仓库根目录 | 项目路径锚点 |
| `SIQ_LOCAL_STATE_ROOT` | 仓库根目录 | 本地状态根 |
| `SIQ_DATA_ROOT` | `$SIQ_LOCAL_STATE_ROOT/data` | 历史兼容运行态根 |
| `SIQ_RUNTIME_ROOT` | `$SIQ_LOCAL_STATE_ROOT/var` | 新增本地运行态推荐根 |
| `SIQ_ARTIFACTS_ROOT` | `$SIQ_LOCAL_STATE_ROOT/artifacts` | 生成产物目录 |
| `SIQ_WIKI_ROOT` | `$SIQ_DATA_ROOT/wiki` | LLM Wiki 事实层目录 |
| `SIQ_REPORT_DOWNLOADS_ROOT` | `$SIQ_DATA_ROOT/market-report-finder/downloads` | 官方披露下载目录 |
| `SIQ_PDF2MD_API_BASE` | `http://127.0.0.1:15000` | PDF 解析服务地址 |
| `SIQ_DOCUMENT_PARSER_API_BASE` | `http://127.0.0.1:15010` | 通用文档解析服务地址 |
| `SIQ_REPORT_FINDER_BASE` | `http://127.0.0.1:18000` | 官方披露下载服务地址 |
| `SIQ_MARKET_REPORT_RULES_BASE` | `http://127.0.0.1:18020` | 市场规则服务地址 |
| `SIQ_HERMES_HOME` | `$SIQ_DATA_ROOT/hermes/home` | Hermes runtime home |
| `SIQ_HERMES_RUNTIME` | `host` | 默认仍为 Host；OpenShell 正式门禁通过前不自动切流 |
| `SIQ_START_OPENSHELL_GATEWAY` | `1` | 随主项目启动或复用 SIQ 专用 OpenShell gateway |
| `SIQ_START_OPENSHELL_BROKERS` | `auto` | reader secret 存在时启动/复用 brokers |
| `SIQ_AUTH_SECRET_KEY` | 无 | API 鉴权密钥，至少 32 字符 |
| `SIQ_SOURCE_TOKEN_SECRET` | 回退到 `SIQ_AUTH_SECRET_KEY` | source access token 签名密钥 |
| `SIQ_AUTH_COOKIE_MODE` | `0` | 启用 HttpOnly cookie 登录兼容模式 |
| `SIQ_MEETINGS_ENABLED` | `0` | 会议应用中心功能开关 |
| `SIQ_AGENT_MEMORY_ENABLED` | `true` | Agent memory 总开关 |
| `SIQ_AGENT_MEMORY_MILVUS_COLLECTION` | `siq_agent_memory_active` | Agent memory 语义索引 collection |

## 验证命令

```bash
cd /home/maoyd/siq-research-engine
scripts/check_all.sh
git diff --check
```

局部验证：

```bash
cd apps/api && uv run python -m pytest tests
cd apps/web && npm run check:frontend
cd apps/pdf-parser && pytest -q tests
cd apps/document-parser && pytest -q tests
cd services/market-report-finder && uv run pytest
cd services/market-report-rules && uv run pytest
cd packages/market-contracts && uv run python -m pytest tests
```

## 延伸阅读

- [API 聚合后端](apps/api/README.md)
- [Web 工作台](apps/web/README.md)
- [PDF 解析服务](apps/pdf-parser/README.md)
- [通用文档解析服务](apps/document-parser/README.md)
- [统一市场公告搜索下载服务](services/market-report-finder/README.md)
- [多市场财报规则服务](services/market-report-rules/README.md)
- [共享证据包合同](packages/market-contracts/README.md)
- [Hermes 智能体体系](agents/hermes/README.md)
- [OpenShell 运维入口](docs/runbooks/openshell/README.md)
- [OpenShell 基础设施](infra/openshell/README.md)
- [OpenShell + Hermes 集成现状](docs/siq-openshell-hermes-integration-status.md)
- [PostgreSQL 入库工具](db/imports/README.md)
- [本地开发操作说明](docs/operations/local-development.md)
