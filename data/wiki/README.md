# 上市公司年报分析 Wiki

`/home/maoyd/wiki` 是一个面向 A 股上市公司年报研究的本地知识库。它以单家公司为主入口，把年报正文、结构化财务指标、证据链、语义层、Obsidian 图谱和后续跟踪预警产物放在同一套可审计目录中。

当前库不是普通 Markdown 归档，而是给人和智能体共同使用的研究底座：回答、分析、复核和生成报告时，必须能回到原始年报文本、PDF 页码、表格索引和抽取日志。

## 当前状态

更新时间口径：2026-06-09 本地 catalog 检查。

| 项目 | 状态 |
|---|---:|
| 当前工作集公司数 | 18（以 `_meta/company_catalog.json` 为准） |
| 当前工作集主报告数 | 18（以 `_meta/report_catalog.json` 为准） |
| 报告类型 | 2025 年年度报告 |
| `company.json` 覆盖 | 以 catalog 实时目录为准 |
| `reports/2025-annual/report.md` 覆盖 | 以 report_catalog 实时目录为准 |
| `metrics/key_metrics.json` 覆盖 | 优先 `metrics/reports/<report_id>/` 或 `metrics/latest/` |
| `semantic/*.json` 覆盖 | 每家公司 11 个语义 JSON |
| 生成 HTML 结果 | 当前统计 50 个：analysis 24、factcheck 11、tracking 11、legal 4 |
| 语义抽取失败数 | 0 |
| 已初始化 tracking 的公司 | 以各公司 `tracking/` 目录为准 |

当前工作集公司：

```text
以 /home/maoyd/wiki/_meta/company_catalog.json 的实时内容为准。
```

`/home/maoyd/wiki/derived/financial_metrics.db` 为派生索引，不作为公司范围权威；公司范围以 `_meta/company_catalog.json` 为准。

质量约束当前全部通过：事实、claims、数值事实均带有可回溯证据。

### 决赛关注点

| 维度 | 本 Wiki 的作用 |
|---|---|
| 创新性 | 以单家公司为中心沉淀报告、指标、证据、语义对象和多智能体产物，形成可审计知识资产 |
| 技术难度 | 需要保持 `task_id/report_id/pdf_page/table_index/md_line` 在 Markdown、JSON、数据库和 HTML 之间可回溯 |
| 完成度 | 当前主前端和五个 Agent 已统一读取本目录；实际覆盖以 `_meta/company_catalog.json`、`_meta/report_catalog.json` 和各公司目录为准 |
| 商业价值 | 让投研团队把一次性报告生产沉淀成可复用、可复核、可持续跟踪的公司研究档案 |

## 评委技术说明

`/home/maoyd/wiki` 是 FinSight 的本地知识中台。它不是普通文件夹归档，而是把官方年报 PDF 解析结果、结构化财务指标、证据索引、语义对象、多智能体产物和 Obsidian 图谱组织到同一套公司目录中，让前端、后端、PostgreSQL 和 Hermes Agent 都能围绕同一个 `company_id/report_id/task_id` 协同工作。

| 维度 | 实现说明 |
| --- | --- |
| 技术架构 | 文件系统知识库 + `_meta` 全局目录 + `companies/<stock_code>-<short_name>` 公司目录 + metrics/evidence/semantic/analysis/factcheck/tracking/legal 分层 |
| 技术栈 | Markdown、JSON、JSONL/manifest、Obsidian 图谱、Python wikiset 脚本、聚合后端 Wiki API、Hermes Agent 文件工具 |
| 数据流 | `pdf2md_web/results/<task_id>/document_full.json` -> `wikiset` 导入 -> 公司目录 `reports/metrics/evidence/semantic` -> Agent 生成报告 -> 前端 `/api/wiki/*` 展示 |
| 算法模型 | 规则语义抽取、segment/fact/claim/evidence 绑定、本地 Qwen3.6 LLM 语义增强、引用链修复、Obsidian 图谱索引 |
| 证据契约 | 财务事实和经营判断必须能回到 `task_id`、`report_id`、PDF 页码、表格索引、Markdown 行或数据库记录 |
| 商业价值 | 把一次性 PDF 解析和报告生成沉淀成长期可复用的公司研究档案，支持投研、合规、审计和后续跟踪 |

### 数据处理流程

```text
官方 PDF
  -> pdf2md_web 解析产物 document_full.json
  -> wikiset 导入公司目录
  -> reports/report.md + report.json + artifact_manifest.json
  -> metrics/key_metrics.json + three_statements.json + validation.json
  -> evidence/evidence_index.json + pdf_refs.json
  -> semantic/segments/facts/claims/relations/note_links
  -> analysis/factcheck/tracking/legal HTML 结果
  -> 聚合后端 /api/wiki/* 与五个 Hermes Agent 消费
```

这条链路的创新点在于 Wiki 不是模型输出的终点，而是所有业务智能体共享的“事实源”。分析助手生成报告、事实核查助手复核结论、跟踪助手沉淀事项、法务助手保存意见书，都必须回写到公司目录，形成可追溯、可复用、可继续评测的知识资产。

## 快速入口

优先从这些文件进入：

```text
_meta/company_catalog.json
_meta/report_catalog.json
_meta/semantic_extraction_manifest.json
_meta/obsidian_graph_manifest.json
_meta/AGENT_GUIDE.md
companies/
tracking/SKILL.md
tracking/_meta/workflow.md
```

单家公司推荐读取顺序：

1. `companies/<company_id>/semantic/retrieval_index.json`
2. `companies/<company_id>/semantic/subject_profile.json`
3. `companies/<company_id>/semantic/facts.json`
4. `companies/<company_id>/semantic/relations.json`
5. `companies/<company_id>/semantic/claims.json`
6. `companies/<company_id>/semantic/note_links.json`
7. `companies/<company_id>/metrics/key_metrics.json`
8. `companies/<company_id>/evidence/evidence_index.json`
9. `companies/<company_id>/reports/2025-annual/report.md`
10. `companies/<company_id>/reports/2025-annual/document_full.json`

`document_full.json` 通常只在深度审计、复跑抽取或定位上游解析结构时读取；日常问答和报告生成优先使用 `semantic/`、`metrics/`、`evidence/` 和 `report.md`。

## 目录结构

## 命名与存档规则

下载后的 PDF 文件名作为报告实例来源契约，推荐格式：

```text
<公司简称>_<市场>_<股票代码>_<报告截止日>_<报告类型>_<公告日期>_<来源>_<hash>.pdf
```

wiki 存档路径统一继承上汽集团样本：

```text
companies/<股票代码>-<公司简称>/
  reports/<年度>-<报告类型slug>/
```

示例：

```text
上汽集团_CN_600104_2025-12-31_年报_2026-04-01_manual_180a0748.pdf
companies/600104-上汽集团/reports/2025-annual/
```

`company_id` 只允许采用 `股票代码-公司简称`，不得包含市场、报告截止日、公告日期、来源或 hash。报告实例来源信息保存在 `report.json`、`_meta/report_catalog.json` 和 evidence metadata 中，供 PostgreSQL、语义层和 Obsidian 继续回溯。

完整规则见 `_meta/wiki_naming_contract.md`。

顶层结构：

```text
wiki/
  README.md
  AGENTS.md
  _meta/
  companies/
  derived/
  tracking/
```

单家公司结构：

```text
companies/<stock_code>-<short_name>/
  company.md
  company.json
  reports/
    2025-annual/
      report.md
      report.json
      document_full.json
      images/
  metrics/
    key_metrics.json
    three_statements.json
    validation.json
  evidence/
    evidence_index.json
    pdf_refs.json
    image_manifest.json
  semantic/
    retrieval_index.json
    subject_profile.json
    segments.json
    facts.json
    relations.json
    claims.json
    note_links.json
    evidence_semantic.json
    image_semantic_manifest.json
    extraction_log.json
  graph/
    company.md
    report.md
    graph_index.json
    facts/
    claims/
    segments/
    notes/
  obsidian/
    index.md
    README.md
  analysis/
    README.md
  tracking/
    tracking-items.md
    sentiment/
    metrics/
    alerts/
    updates/
    <stock_code>-<short_name>-跟踪报告-<date>.html
```

`tracking/` 只会出现在已经初始化跟踪流程的公司目录下。

## 公司范围

当前工作集限定为 `_meta/company_catalog.json` 中列出的公司。历史 README 曾记录过 10/20 家工作集，均不再作为权威范围；完整旧版本备份仍保留在 `/home/maoyd/wiki_full_167_backup_20260510T075645Z`。

完整旧版本备份在：

```text
/home/maoyd/wiki_full_167_backup_20260510T075645Z
```

默认检索、回答和跟踪任务都应以当前 `_meta/company_catalog.json` 工作集为准，不应自动跨到完整备份目录。

## 数据来源

上游来源目录：

```text
/home/maoyd/pdf2md_web/results
```

进入 wiki 的核心上游产物：

| 上游产物 | wiki 中的位置/用途 |
|---|---|
| `result_complete.md` | `reports/2025-annual/report.md`，年报正文主文本 |
| `document_full.json` | `reports/2025-annual/document_full.json`，完整结构化解析结果 |
| `content_list_enhanced.json` | 已进入 `document_full.json`，用于表格、页码、目录、图片语义块和附注映射 |
| `financial_data.json` | 已进入 `document_full.json`，并派生 `metrics/key_metrics.json` |
| `financial_checks.json` | 已进入 `document_full.json`，并派生 `metrics/validation.json` |
| `table_index.json` | 核心信息进入 `report.json`，用于表格定位和 PDF 回溯 |

不直接复制到 wiki 的内容：

- PDF 页面截图缓存 `pdf_pages/`，由 `/home/maoyd/pdf2md_web` 管理。
- 未被 Markdown 引用且没有语义价值的图片。
- 重复任务产物。

## 证据规则

分析或回答时必须优先保留证据链：

- 股票代码和公司简称。
- 报告年份和报告类型。
- Markdown 原文位置或语义对象 ID。
- PDF 页码。
- 表格索引或附注编号。
- 必要时补充 `task_id`，用于回到上游 pdf2md 任务。

审计财务报表项目、会计科目明细或附注解释时，优先读取：

```text
companies/<company_id>/semantic/document_links.json
companies/<company_id>/semantic/note_links.json
```

`document_links.json` 保存通用“报表项目 -> 附注 -> 同节表格”的跳转图；`note_links.json` 保存原始“报表项目 -> 报表页/表格 -> 附注编号/标题 -> 附注页”的对应关系。该机制适用于应收、存货、商誉、固定资产、借款、收入成本、减值、关联方等所有可结构化项目，不是商誉专用。

## 语义层

`semantic/` 是智能体读取公司信息的主层：

| 文件 | 用途 |
|---|---|
| `retrieval_index.json` | 公司级检索入口，指向核心事实、片段和证据 |
| `subject_profile.json` | 公司主体画像 |
| `segments.json` | 年报主题片段 |
| `facts.json` | 可审计事实 |
| `relations.json` | 事实、主体、业务、指标之间的关系 |
| `claims.json` | 可验证判断或结论 |
| `note_links.json` | 财报项目与附注关系 |
| `document_links.json` | 主表项目、附注和附注表格之间的跳转图 |
| `evidence_semantic.json` | 证据语义化索引 |
| `image_semantic_manifest.json` | 图片语义清单 |
| `extraction_log.json` | 抽取日志 |

抽取规则见：

```text
_meta/single_company_subject_extraction_rules.md
/home/maoyd/extract_company_semantics.py
```

## 数据抽取与归一化

语义层由 `/home/maoyd/extract_company_semantics.py` 生成。这个脚本的设计原则是 rule-first：先用确定性规则、结构化输入和证据链生成可审计语义对象，再让 LLM 或智能体基于这些对象推理，而不是让模型直接从长文本中自由总结。

### 输入层

抽取器按公司目录读取以下输入：

| 输入 | 用途 |
|---|---|
| `company.json` | 公司身份、股票代码、交易所、简称、全称、别名、主报告 ID |
| `reports/2025-annual/report.md` | 年报 Markdown 主文本，含 `[PDF_PAGE]` 页码锚点 |
| `reports/2025-annual/report.json` | 表格索引、PDF/source URL 模板、质量摘要、task_id |
| `reports/2025-annual/document_full.json` | 上游完整结构化解析结果，含目录、图片语义块、附注对应关系 |
| `metrics/key_metrics.json` | 关键指标的跨期值、原始值、单位、表格来源 |
| `metrics/three_statements.json` | 三大报表核心科目的标准化数值与来源 |

脚本会为输入文件计算 SHA-256，写入 `semantic/extraction_log.json`，用于确认语义层到底基于哪一版底稿生成。

### 分层抽取逻辑

1. `segments`：优先使用 `document_full.content_list_enhanced.toc.headings`，缺失时回退扫描 Markdown 标题；再用 `TOPIC_ALIASES` 和 `classify_segment()` 归类为公司简介、关键财务、经营分析、业务概览、行业分析、风险、财报附注等主题。
2. `subject_profile`：把公司身份、主报告、业务范围、行业背景、战略、治理、质量摘要等聚合成公司画像，并保留对应 segment IDs。
3. `facts`：从身份字段、核心关键指标、三大报表核心科目抽取事实。每条事实都有 `fact_type`、subject、predicate、object、period、value、unit、dimensions 和 evidence IDs。
4. `relations`：为公司与指标之间建立 `company_reported_metric` 等关系，使智能体能沿“公司 -> 指标 -> 期间 -> 证据”导航。
5. `claims`：只基于已抽取事实计算同比变化，例如 `(current - previous) / abs(previous)`，并记录公式、输入值、支撑 fact IDs 和证据；不让 LLM 自行编造结论。
6. `note_links`：读取 `document_full.content_list_enhanced.financial_note_links.links`，归一报表项目、附注编号、附注标题、报表页、附注页和金额校验结果。
7. `document_links`：在 `note_links` 基础上生成通用跳转边，连接主表项目、附注标题和同一附注节内的表格；LLM 只可辅助判定跳转语义，不抽取数据。
8. `image_semantic_manifest`：保留图片路径、PDF 页码、语义类型、actionability 和是否需要 OCR/VLM 复核。
9. `retrieval_index`：按主题生成推荐读取文件、query aliases、segment/fact/claim/evidence IDs，形成面向智能体的检索路由表。

### 归一化逻辑

本项目的归一化不是简单改字段名，而是把“年报文本里的分散信息”变成可计算、可回溯、可组合的对象：

| 归一化对象 | 处理方式 |
|---|---|
| 公司身份 | 统一为 `company:<stock_code>`，同时保留简称、全称、aliases |
| 报告口径 | 统一使用 `primary_report_id`，当前为 `2025-annual` |
| 主题分类 | 通过 `TOPIC_ALIASES` 把中文标题归一到稳定 topic type，例如 `key_financials`、`risk_factors` |
| 指标名称 | 只抽取核心 canonical metrics，例如 `operating_revenue`、`parent_net_profit`、`total_assets` |
| 指标单位 | 三大报表核心指标统一为 `亿元`，同时保留 `raw_value`、`base_scale`、`unit_hint` |
| 期间 | 年度、上年同期和调整后期间通过 `period_candidates()` 归一，用于同比 claim 计算 |
| 表格定位 | 用 `table_index`、Markdown 行号、PDF 页码和 bbox 统一定位 |
| 附注关系 | 建立 `by_statement_item`、`by_note_ref`、`by_note_title` 三套索引 |
| 证据对象 | 文本、表格、指标、图片统一写入 `evidence_semantic.json`，并带打开 PDF/source 的 URL 模板 |

一个重要细节是 `secondary_amount_check()`：当上游附注金额校验因单位或归一化尺度不一致而显示 `unverified` 时，脚本会用原始值和归一值做二次近似匹配，在容差内恢复为 `verified`，并把匹配方法、差额和容差写入结果。这能减少“格式问题导致的假阴性”。

### 质量与审计机制

语义层每次生成都会输出：

- `semantic/extraction_log.json`：输入文件哈希、对象数量、质量比例、待人工复核证据。
- `_meta/semantic_extraction_manifest.json`：全局公司数、失败数、对象总量和最低质量指标。
- `needs_review`：对低置信度、缺页码、金额 mismatch/failed、图片需要 OCR/VLM 的对象打标。
- `confidence`：每条证据和对象都有 high/medium/low 置信度。

当前工作集的质量下限为：

```text
facts_with_evidence_ratio = 1.0
claims_with_evidence_ratio = 1.0
numeric_facts_with_metric_source_ratio = 1.0
```

### 抽取亮点

- 先结构化、后推理：LLM 使用事实和证据，不直接从全文自由生成结论。
- 证据优先：每个 fact/claim 都能回到 Markdown 行号、PDF 页码、表格索引或图片位置。
- 可复跑：输入文件哈希、规则版本和生成时间完整记录，便于复现和差异审计。
- 面向财报场景优化：专门处理三大报表、关键指标、同比 claims、附注跳转、金额校验和金融行业口径。
- 检索即路由：`retrieval_index.json` 不只是向量召回结果，而是告诉智能体针对某个主题应该读哪些文件和哪些对象。
- 图谱可视化可派生：`graph/` 和 `obsidian/` 从语义层生成，不反过来污染事实源。

## 指标与派生数据

公司级指标在：

```text
companies/<company_id>/metrics/
```

全局派生数据在：

```text
derived/three_statements_latest.json
derived/financial_metrics.db
derived/tushare_sw_industry_mapping.json
```

`derived/three_statements_latest.json` 已过滤为当前工作集。完整旧版本备份为：

```text
derived/three_statements_latest_full_165_backup.json
```

## 行业分类

行业分类采用 SWHY2021 申万行业口径。

入口文件：

```text
_meta/industry/sw2021_active_company_mapping.json
_meta/industry/sw2021_industry_classification.json
_meta/industry/sw2021_industry_classification.csv
```

行业字典规模：

| 层级 | 数量 |
|---|---:|
| 一级行业 | 31 |
| 二级行业 | 134 |
| 三级行业 | 346 |

当前工作集公司字段包括：

```text
industry_sw1_code/name
industry_sw2_code/name
industry_sw3_code/name
industry_profile
```

## Obsidian 图谱

Obsidian 可视化入口：

```text
companies/<company_id>/obsidian/index.md
companies/<company_id>/graph/
```

`graph/` 和 `obsidian/` 由 `semantic/` 派生，主要用于 Markdown 双链和图谱浏览。正式事实、指标和证据审计仍以 `semantic/`、`metrics/`、`evidence/` 和 `report.md` 为准。

根目录中可能出现 Obsidian 或 macOS 临时文件，例如 `.obsidian/`、`*.canvas`、`*.base`、`._*`。这些不是研究数据入口，已在 `.gitignore` 中标记。

## LLM-Wiki 与传统 RAG

这里的 LLM-Wiki 指“为 LLM/智能体预先组织好的可审计知识工程层”，不是把文档切块后直接丢进向量库。传统 RAG 通常以 chunk 为中心：切分、embedding、召回、拼上下文、让模型回答。这个流程适合泛文档问答，但面对上市公司年报这类强结构、强审计、强数值一致性的材料时，容易出现证据漂移、表格错配、单位误读和跨期比较错误。

**本 LLM-Wiki 的查询链路不使用 embedding、reranker 或 Milvus。** `semantic/` 目录中的“语义”指已经抽取并建立关系的知识对象，不代表向量；`retrieval_index.json` 是 query alias、优先文件和对象 ID 构成的确定性路由表，也不是向量检索结果。项目中的 Qwen3-VL Embedding/Reranker 与 Milvus 属于独立的跨模态和 Agent memory 检索能力，不参与 Wiki 内部事实定位。

本 wiki 以对象为中心：公司、报告、主题片段、事实、关系、指标、claim、附注链接、证据都是稳定对象。LLM 的任务从“在几段召回文本里猜答案”，变成“按检索路由读取对象，基于事实和证据做解释”。

| 维度 | 传统 RAG | 本项目 LLM-Wiki |
|---|---|---|
| 基本单元 | 文本 chunk | 公司、报告、segment、fact、relation、claim、note_link、evidence |
| 检索方式 | 相似度召回为主 | 主题别名 + priority files + 对象 ID + 主表/附注跳转 + 必要时全文回溯 |
| 数值处理 | 依赖模型读表和理解单位 | 指标预归一，保留 raw/normalized/unit/source |
| 可审计性 | 常只能给出召回片段 | 可回到 Markdown 行号、PDF 页码、表格索引、bbox、task_id |
| 结论生成 | 模型即时概括 | rule-first 生成 claims，LLM 解释和组合 |
| 附注追踪 | 容易漏掉报表项目到附注的跳转 | `note_links.json` 显式维护报表项目和附注关系 |
| 更新与复现 | embedding/chunk 版本不易审计 | 输入哈希、规则版本、manifest、extraction_log 可复查 |
| 多智能体协作 | 各自重新检索上下文 | 共享同一组稳定对象和读取顺序 |

显著优势：

1. 更少幻觉：LLM 面对的是已归一事实和证据，而不是长文本碎片。
2. 更适合财务分析：金额、单位、期间、报表类型和附注关系都在模型回答前被结构化。
3. 更强可追溯性：任何关键判断都能追到原始年报页码和表格。
4. 更高复用性：同一语义层可同时服务问答、分析报告、Obsidian 图谱、tracking 预警和后续数据库。
5. 更容易持续维护：新公司或新报告只需复跑抽取器，manifest 会暴露失败和质量下降。
6. 更利于复杂任务：跨章节审计、指标同比、风险跟踪、附注核验不再依赖一次性 prompt，而是依赖稳定数据结构。

传统 RAG 可以作为整个 SIQ 系统的并行补充能力，例如在 Milvus 中查找未结构化长段落、图片或 Agent memory；但它位于 LLM-Wiki 之外。Wiki 内部默认且唯一的知识导航入口是 `retrieval_index.json`、结构化对象层和 `document_links`/`note_links` 逻辑跳转，必要时直接回溯 `report.md`/`document_full.json`，不会调用向量召回或 reranker。

### 逻辑跳转查询链

```text
ResearchIdentity
  -> _meta/company_catalog.json / report_catalog.json
  -> company.json / report.json
  -> semantic/retrieval_index.json topic + query_aliases
  -> metrics / facts / relations / claims / segments
  -> document_links / note_links
  -> evidence_index / evidence_semantic
  -> report.md / document_full.json / PDF source
```

这条链路确保“检索和召回精度”来自准确的身份、知识类型和显式关系，而不是相似度概率：主表数字优先进入 `metrics`，经营主题进入对应 `segment`，财务构成沿附注关系跳转，最终每条重要事实都回到 source coordinates。知识抽取脚本对输入计算 SHA-256，记录规则版本、对象计数、证据覆盖和 review 状态，因此每次重建都能解释知识从哪里来、为什么被路由到这里。

## Tracking 跟踪系统

`tracking/` 是 finsight_tracking 金融跟踪与预警系统。

职责：

- 从 `analysis/*.md` 和 `metrics/key_metrics.json` 提取持续跟踪事项。
- 生成舆情日报。
- 生成指标追踪面板。
- 触发四级预警。
- 更新分析报告。
- 输出合并 HTML 跟踪报告。

脚本入口：

```text
tracking/scripts/run_all.py
tracking/scripts/module1_item_extractor.py
tracking/scripts/module2_sentiment_monitor.py
tracking/scripts/module3_metrics_tracker.py
tracking/scripts/module4_alert_trigger.py
tracking/scripts/module5_report_updater.py
tracking/scripts/module6_html_reporter.py
```

当前已初始化且规则校验通过的公司：

```text
000063-中兴通讯
600399-抚顺特钢
```

常用命令：

```bash
cd /home/maoyd/wiki

# 验证所有已初始化 tracking 的公司
python3 tracking/scripts/run_all.py --validate-all

# 为公司初始化 tracking 目录
python3 tracking/scripts/run_all.py --setup --stock 600104 --company 上汽集团

# 运行完整跟踪流程
python3 tracking/scripts/run_all.py --stock 600104 --company 上汽集团

# 跳过舆情模块运行
python3 tracking/scripts/run_all.py --stock 600104 --company 上汽集团 --skip-sentiment
```

注意：`module2_sentiment_monitor.py` 当前使用模拟舆情数据。真实巨潮资讯、东方财富、财联社、雪球等数据源需要后续接入 API。

定时任务脚本：

```bash
/home/maoyd/wiki/tracking/scripts/daily_run.sh
```

建议 cron：

```cron
0 8 * * * /home/maoyd/wiki/tracking/scripts/daily_run.sh
```

## 维护检查

修改数据、脚本或重建目录后，建议执行：

```bash
cd /home/maoyd/wiki

# 元数据 JSON 解析
python3 -m json.tool _meta/company_catalog.json >/dev/null
python3 -m json.tool _meta/report_catalog.json >/dev/null
python3 -m json.tool _meta/semantic_extraction_manifest.json >/dev/null

# 重新生成语义层（全量；会覆盖 semantic/ 和 manifest）
python3 /home/maoyd/extract_company_semantics.py --wiki-root /home/maoyd/wiki

# tracking 脚本静态编译
python3 -m compileall -q tracking/scripts

# tracking 规则校验
python3 tracking/scripts/run_all.py --validate-all
```

本次 README 更新前的检查结果：

- 三个核心 `_meta/*.json` 均可解析。
- `tracking/scripts` 静态编译通过。
- tracking 规则校验：2 家已初始化公司，2 家通过，0 家失败。

## 智能体使用约束

智能体读取本库时应遵守：

1. 默认只在 `_meta/company_catalog.json` 当前列出的公司范围内回答。
2. 单家公司分析先读 `semantic/retrieval_index.json`，不要直接全文扫 `document_full.json`。
3. 引用财报事实时必须带证据链。
4. 金融、银行、保险类公司需尊重 `industry_profile`，不要套用通用制造业指标解释。
5. `graph/` 和 `obsidian/` 只用于可视化浏览，不作为最终事实源。
6. 上游 PDF 页面由 `/home/maoyd/pdf2md_web` 提供，wiki 不复制 `pdf_pages/`。
7. tracking 模块写入公司级 `tracking/` 目录，不应把每日产物散落到根目录。

## 备份与历史

已知历史备份：

```text
/home/maoyd/wiki_legacy_20260510_pre_rebuild
/home/maoyd/wiki_pre_schema8_refresh_20260510
/home/maoyd/wiki_full_167_backup_20260510T075645Z
```

这些目录可用于追溯旧版结构或完整公司集，但不作为当前默认知识库。
