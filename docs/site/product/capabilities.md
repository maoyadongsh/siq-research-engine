# 能力矩阵

## 三块产品能力总览

| 能力层 | 二级市场 | 一级市场 | 应用中心 |
| --- | --- | --- | --- |
| 输入材料 | 官方披露、年报、中报、公告、XBRL facts | BP、财务模型、合同、访谈、第三方报告、会议材料 | PDF、Office、HTML、URL、图片、音频、既有解析目录 |
| 事实层 | LLM Wiki package、metrics、evidence、graph facts | Deal evidence、data room、R1-R4 artifacts、project memory | document_full、source map、table relations、transcript segments、chunks |
| 存储层 | Wiki、PostgreSQL、Milvus | Wiki deals、PostgreSQL、Milvus、project_shared memory | 文件 artifact、PostgreSQL、Milvus、artifacts |
| 智能体 | assistant、analysis、factchecker、tracking、legal | coordinator、chairman、strategy、sector、finance、legal、risk | 不直接给投资结论，提供材料和知识工具 |
| 质量门禁 | parser/rules warning、evidence coverage、hash、financial checks | 材料完整性、证据充分性、争议和人工确认 | artifact contract、source map、ASR readiness、chunk metadata |
| 审计回放 | source page/table/line、report manifest、factcheck | deal audit、decision record、phase artifacts | task id、artifact hash、meeting cursor、ingest metadata |

## 二级市场能力

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

## 一级市场能力

| Profile | 职责 |
| --- | --- |
| `siq_ic_master_coordinator` | 项目编排、材料完整性、证据门禁、专家任务收口 |
| `siq_ic_chairman` | 投委会最终裁决、条件化投决、分歧处理和决策签核 |
| `siq_ic_strategist` | 战略适配、基金 thesis、宏观与入场时点 |
| `siq_ic_sector_expert` | 行业格局、产品、客户、竞争和市场判断 |
| `siq_ic_finance_auditor` | 财务一致性、预测、估值和压力测试 |
| `siq_ic_legal_scanner` | 法务尽调、条款风险、监管暴露 |
| `siq_ic_risk_controller` | 下行情景、红黄线、保护条款和风险阈值 |

一级市场的核心价值是把尽调和投委会从散落文档、口头判断和人工会议纪要，转成**可回放、可签核、可复核的决策链**。

## 应用中心能力

| 应用 | 路径 | 价值 |
| --- | --- | --- |
| 文档解析 | `apps/document-parser`、Web `/documents` | 将 PDF、Office、HTML、URL、图片和既有 MinerU 目录归一为 artifact、source map、table relations 和 schema extraction |
| 财报 PDF 解析 | `apps/pdf-parser`、Web `/parse*` | 将财报 PDF 转成 Markdown、document_full、quality、financial_data、source map 和 page/table evidence |
| 会议转写 | `apps/api` meeting routers、`infra/model-services/meeting-speech`、Web `/meetings` | 实时/导入转写、说话人、术语库、声纹、纪要、行动项、音频回放和导出 |
| 向量入库 | `scripts/vector-index/milvus-ingestion`、Web `/vector-ingest` | 将 Wiki package、通用文档、法规库和项目知识转成可重建语义索引 |

应用中心的定位是"材料生产和知识沉淀能力"，它服务二级市场和一级市场，但不直接替代业务智能体集群。

## 官方披露入口覆盖

| 市场 | 入口 | 已支持能力 |
| --- | --- | --- |
| CN（A 股） | CNINFO | PDF 下载、解析、规则、入库、分析全链路样板 |
| HK | HKEXnews | PDF package 下载、解析、规则 |
| US | SEC EDGAR | XBRL/iXBRL 解析、中文 alias（例如"英伟达"→ `NVDA / CIK 1045810`） |
| EU | ESEF | ESEF ZIP 解析 |
| JP | EDINET | XML 解析 |
| KR | DART | XML 解析 |

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
| agent memory | Hermes runtime memory、local task memory、PostgreSQL `agent_memory`、Milvus `siq_agent_memory*` | 拟人化连续性、长期记忆、半衰期衰减、按需全量召回 |

这些文件不是"导出结果"，而是**跨服务协作边界**。Web、API、rules、importer、Milvus 和 Hermes 都围绕这些合同消费或增强事实层。