# SIQ Research Engine

SIQ Research Engine 是一套面向投研机构的可审计智能研究生产线。项目把官方披露下载、财报与通用文档解析、结构化证据包、PostgreSQL / Milvus 沉淀、Hermes 多智能体协作，以及 NVIDIA OpenShell 安全运行面组合成一个可复核、可回放、可持续扩展的投研系统。

---

## 三块产品心智

1. **二级市场投研分析智能体集群** —— 围绕公开披露事实到研究结论工作。
2. **一级市场投研决策智能体集群** —— 围绕材料、证据、专家意见、争议和投委会决策工作。
3. **应用中心** —— 文档解析、会议转写、向量入库的跨业务复用能力。

三块能力共享同一个事实层、权限模型、质量门禁和审计语言。二级市场的披露证据、一级市场的尽调材料、会议陈述、智能体判断和最终决策可以在同一套 evidence / source / memory 体系中互相引用。

---

## 核心目标

SIQ 的核心目标不是“让模型写一篇像研报的文章”，而是让数字、判断、风险提示、引用和行动建议都能回到官方披露、PDF 页码、XBRL facts、表格单元格、Markdown 行、数据库记录、会议时间轴或投委会证据对象。

对 SIQ 来说，**证据先于回答，质量门禁先于入库，审计链先于流畅表达。**

---

## 当前状态

截至 2026-07-18，项目已从多服务技术验证进入“可演示、可复核、可扩展”的平台化阶段。

| 方向 | 当前状态 |
| --- | --- |
| 二级市场商业样板 | A 股全链路样板成熟，多市场证据包继续扩展 |
| 官方披露入口 | CN / HK / US / EU / JP / KR 六市场入口 |
| 多市场解析与规则 | A 股 PDF、HK PDF package、SEC XBRL/iXBRL、ESEF、EDINET、DART |
| 一级市场 Deal OS | R0-R4 投委会工作流、材料中心、专家角色持续完善 |
| 应用中心 | 文档解析、会议转写、向量入库均有独立链路 |
| 智能体记忆 | Hermes 原生 + 本地临时 + PostgreSQL 长期 + Milvus 语义 |
| NVIDIA OpenShell 安全运行面 | 演示/灰度控制面已验证，正式生产门禁仍为 `NO_GO` |

---

## 文档导览

<div class="grid cards" markdown>

- :material-book-open: **[项目定位](product/overview.md)**

    ---

    SIQ 是什么、为什么难、核心创新在哪里。

- :material-chart-bar: **[能力矩阵](product/capabilities.md)**

    ---

    二级市场、一级市场、应用中心三块能力的输入、事实层、智能体和质量门禁。

- :material-tools: **[技术栈](product/tech-stack.md)**

    ---

    前端、控制面、解析面、市场服务、数据层、智能体、GPU 运行面的选型。

</div>

<div class="grid cards" markdown>

- :material-sitemap: **[产品架构](architecture/overview.md)**

    ---

    从官方披露到 Web 工作台的全链路分层。

- :material-map-marker-multiple: **[仓库地图](architecture/modules.md)**

    ---

    `apps/`、`services/`、`packages/`、`agents/`、`db/`、`scripts/`、`infra/` 的职责边界。

- :material-robot: **[二级市场智能体集群](architecture/secondary-market.md)**

    ---

    analysis / factcheck / tracking / legal / assistant 的职责合同。

- :material-handshake: **[一级市场 IC 智能体集群](architecture/primary-market.md)**

    ---

    chairman / strategist / sector / finance / legal / risk / coordinator 的 R0-R4 决策链。

- :material-apps: **[应用中心](architecture/app-center.md)**

    ---

    文档解析、PDF 解析、会议转写、向量入库的跨业务复用。

- :material-brain: **[拟人化记忆系统](architecture/memory.md)**

    ---

    Hermes 原生 + 本地临时 + PostgreSQL 长期 + Milvus 语义四层架构。

- :material-shield-lock: **[OpenShell 安全运行面](architecture/openshell.md)**

    ---

    自研 NVIDIA OpenShell + Hermes 控制面与 NO-GO 门禁。

</div>

<div class="grid cards" markdown>

- :material-rocket-launch: **[快速启动](usage/quickstart.md)**

    ---

    一键启动脚本和 Docker Compose 入口。

- :material-key-variant: **[环境变量](usage/environment.md)**

    ---

    项目根、数据目录、服务地址、鉴权、OpenShell 等配置项。

- :material-heart-pulse: **[健康检查](usage/health-check.md)**

    ---

    各服务健康端点与 OpenShell 完成度检查。

- :material-test-tube: **[测试与验证](usage/testing.md)**

    ---

    全仓基础门禁、控制面、前端、解析、市场服务、PostgreSQL、OpenShell 回归。

</div>

---

## 延伸阅读

- [源仓库 README](https://github.com/sunbos/siq-research-engine)
- [OpenShell + Hermes 集成现状](https://github.com/sunbos/siq-research-engine/blob/master/docs/siq-openshell-hermes-integration-status.md)
- [OpenShell NO-GO 到 GO 路线](https://github.com/sunbos/siq-research-engine/blob/master/docs/runbooks/openshell/no-go-to-go-readiness-matrix.md)
