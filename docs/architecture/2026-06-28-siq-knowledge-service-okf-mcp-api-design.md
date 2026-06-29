# SIQ 对外知识服务模块设计方案

> 日期：2026-06-28
> 状态：设计方案
> 目标：把 SIQ 现有的证据链、结构化事实层、语义检索层与文档包能力，产品化为面向外部用户和 Agent 的知识服务。

## 1. 结论

结论先放在前面：**可行，而且有明确的产品价值。**

但正确的实现方式不是“把 PostgreSQL、Milvus、Wiki 直接暴露出去”，而是把它们抽象成一个新的产品层：

```text
SIQ Knowledge Service
  = 结构化事实查询
  + 语义证据检索
  + OKF 知识包导出
  + MCP 工具调用
```

这个方向成立的原因很直接：市场上很多 API 和 MCP 只返回“冷结构化数据”，例如 ticker、财务指标、公告列表、搜索结果。SIQ 的优势不在于再做一个数据查询壳，而在于提供：

- 可引用的答案
- 可回放的证据链
- 可跨市场比较的语义上下文
- 可迁移的知识包
- 可被 Agent 直接消费的工具集

这会让 SIQ 从“金融数据接口”升级成“全球上市公司证据知识基础设施”。

## 2. 项目事实基础

仓库里已经具备这条路需要的大部分基础能力：

- `apps/api` 已经有统一鉴权、额度、工作流代理、citation 链接修复与文档代理。
- `apps/api/routers/document_parser.py` 已经是文档解析入口。
- `apps/api/routers/market_reports.py` 已经承载市场报告、证据包、导入与评测任务。
- `apps/api/routers/wiki.py` 已经是 Wiki 文件服务入口。
- `apps/api/services/citation_links.py` 已经在做 source / page / table 的可点击回源。
- `db/ddl/060_create_document_parser_schema.sql` 已经把解析结果、表格、图片、source、artifact 分表。
- `db/imports/import_market_xbrl_package_to_postgres.py`、`import_hk_evidence_package_to_postgres.py` 等已经形成市场证据包入库模式。
- `scripts/vector-index/milvus-ingestion/ingest_market_evidence_chunks.py` 已经在把证据包切成可检索 chunk。
- `docs/architecture/market-evidence-package-contract.md` 已经定义了市场证据包最低合同。

所以这件事的关键不是“从零发明一个新系统”，而是把已有系统的事实层、语义层、文件层，统一成一个对外接口层。

## 3. 设计判断

### 3.1 为什么可行

1. SIQ 已经具备证据合同，而不是只有原始文本。
2. SIQ 已经具备多市场事实层，而不是只有单一股票数据。
3. SIQ 已经具备 source map 和 citation 回源，而不是只有检索结果。
4. SIQ 已经具备文件系统优先的 Wiki 结构，适合做 OKF export。
5. SIQ 已经具备 API 聚合后端，适合挂载对外服务。

### 3.2 为什么有市场需求

现在多数金融类 API 的输出仍然是：

- 指标
- 原始字段
- 行情
- 公告列表
- 搜索片段

但 Agent 和研究人员真正需要的是：

- 这个数字来自哪里
- 这个风险在原文哪一段
- 这个口径跨市场怎么对齐
- 这个结论是否经过质量门禁
- 我能否直接拿到一组可引用上下文

SIQ 恰好有能力把“事实”与“证据”绑在一起，这就是差异化。

### 3.3 这件事不能怎么做

不能把它做成：

- 一个直接暴露数据库的 SQL 入口
- 一个只返回向量 topK 的黑盒检索口
- 一个没有证据链的答案生成器
- 一个把 OKF 当成新运行时或新框架的重型系统

正确做法是把底层能力分层封装。

## 4. 产品定位

推荐对外定位为：

> **SIQ Knowledge Service：面向 Agent 和开发者的全球上市公司证据知识服务。**

它提供四类能力：

1. **Structured Facts**
   - 公司、市场、财报、指标、期间、对比、事实引用

2. **Semantic Evidence**
   - 年报语义、风险因素、管理层讨论、法律合规、跨市场上下文检索

3. **OKF Knowledge Bundles**
   - 可迁移、可版本化、可 git 管理的知识包

4. **MCP Tools**
   - 让 Claude、Cursor、Codex、企业 Agent 直接调用

### 4.1 第一阶段聚焦场景

不要一开始就泛化成“全行业知识平台”。建议第一阶段聚焦：

- 全球上市公司年报 / 财报语义查询
- 财务事实溯源
- 风险因素与管理层讨论检索
- 法律 / 合规语义查询
- 公司对比与跨市场口径对齐

这已经足够形成明确的产品轮廓。

## 5. 总体架构

```text
官方披露 / 本地上传 / 市场证据包 / 通用文档
  -> 下载与解析
  -> document_full.json / source_map.json / quality_report.json
  -> market evidence package
  -> PostgreSQL 事实层
  -> Milvus 语义层
  -> Wiki / OKF 知识包
  -> SIQ Knowledge API
  -> MCP Server
  -> 外部用户 / 外部 Agent / 内部工作台
```

### 5.1 三层数据真相

建议把真相层次定义清楚：

1. **源文件层**
   - 原始 PDF、HTML、XBRL、图片、办公文档

2. **证据包层**
   - `document_full.json`
   - `source_map.json`
   - `quality_report.json`
   - `market_evidence_package_v1`

3. **服务层**
   - PostgreSQL 结构化事实
   - Milvus 语义召回
   - OKF bundle
   - API / MCP 返回结果

服务层不应反过来成为新的事实源。

## 6. OKF 方案

Google 在 2026-06-12 发布了 Open Knowledge Format（OKF）。官方说明里已经明确了几个关键点：

- 它是一个开放规范
- 目标是把知识表示成可迁移的 Markdown + YAML frontmatter
- 它是 format，不是 runtime
- v0.1 还是 draft，适合兼容，不适合强绑定

这对 SIQ 很合适，但要注意一个核心原则：

> **OKF 应该是 SIQ 的导出层与分发层，不应该替代内部事实存储。**

### 6.1 OKF 在 SIQ 中的角色

建议分成两种视图：

- **内部 canonical store**
  - 继续使用现有 `data/wiki/`、PostgreSQL、Milvus、市场证据包

- **外部 OKF bundle**
  - 从 canonical store 派生
  - 用于交付、迁移、共享、Agent 消费

这样既能拥抱 OKF 的开放性，又不把内部系统绑死在一个仍在演化的标准上。

### 6.2 推荐的 OKF bundle 结构

```text
okf/
  companies/
    US/
      NVDA/
        index.md
        filings/
          index.md
          2025-10-k.md
        metrics/
          revenue.md
          operating_margin.md
        risks/
          risk_factors.md
        legal/
          export_controls.md
    HK/
    JP/
    KR/
    EU/
  concepts/
    revenue.md
    operating_cash_flow.md
    gross_margin.md
  legal/
    cn/
    us/
    eu/
  log.md
```

### 6.3 Frontmatter 约定

OKF 官方最小思想是 `type + title + description + resource + tags + timestamp`。SIQ 可以在此基础上加一个 `siq:` 命名空间：

```yaml
---
type: company_filing
title: NVIDIA 2025 Form 10-K
description: Official annual report evidence bundle
resource: siq://filing/US/NVDA/2025-10-k
tags: [US, SEC, 10-K, semiconductor]
timestamp: 2026-02-25T00:00:00Z
siq:
  market: US
  ticker: NVDA
  company_id: 0001045810
  filing_id: 1f4c...
  evidence_package: data/wiki/us_sec/NVDA/2025/10-k_...
  quality_status: pass
  evidence_coverage_ratio: 0.97
  source_map: data/wiki/us_sec/.../qa/source_map.json
  source_url: https://www.sec.gov/...
---
```

### 6.4 OKF 文档类型建议

建议优先生成这些 concept types：

- `company_profile`
- `filing`
- `financial_metric`
- `risk_factor`
- `management_discussion`
- `legal_topic`
- `peer_comparison`
- `evidence_note`
- `methodology`

### 6.5 OKF 与 source map 的关系

OKF 不替代 source map。相反，它应该链接到 source map：

- 概念正文负责“人和 Agent 可读”
- frontmatter 负责“机器可检索”
- source map 负责“回源定位”

这三者合起来，才是 SIQ 的完整可迁移知识包。

## 7. Structured API 设计

这一层负责稳定商业接口，建议优先 REST，不要把业务协议绑死在 MCP 上。

### 7.1 API 原则

- 只提供领域接口，不开放原始 SQL
- 每个响应都必须带证据引用
- 每个接口都要有版本号
- 每个结果都要带质量状态
- 每个结果都要能回到原始证据包或 source map

### 7.2 推荐路由

```text
GET  /api/public/v1/companies/{market}/{ticker}
GET  /api/public/v1/companies/{market}/{ticker}/filings
GET  /api/public/v1/filings/{filing_id}
GET  /api/public/v1/metrics/{metric_name}
GET  /api/public/v1/metrics/{metric_name}/timeseries
POST /api/public/v1/compare
GET  /api/public/v1/evidence/{evidence_id}
POST /api/public/v1/search/semantic
POST /api/public/v1/search/legal
GET  /api/public/v1/okf/bundles/{bundle_id}
GET  /api/public/v1/okf/bundles/{bundle_id}/index
```

### 7.3 统一响应壳

建议所有对外返回都包一层统一 envelope：

```json
{
  "request_id": "req_...",
  "status": "ok",
  "data": {},
  "citations": [
    {
      "evidence_id": "ev_...",
      "source_type": "xbrl_fact",
      "locator": {
        "filing_id": "...",
        "page_number": 72,
        "table_index": 88,
        "row_index": 14,
        "column_index": 3,
        "xbrl_tag": "RevenueFromContractWithCustomerExcludingAssessedTax"
      },
      "quality": "pass"
    }
  ],
  "quality": {
    "status": "pass",
    "coverage_ratio": 0.97,
    "warnings": []
  }
}
```

### 7.4 Structured API 的价值

这一层适合：

- 金融终端 / 量化平台
- 企业知识系统
- 投研 SaaS
- 财务分析助手
- 需要稳定 JSON 合同的客户

它解决的是“可编程访问”问题。

## 8. Semantic API 设计

Milvus 不应该作为事实源暴露，但可以作为语义检索能力对外服务。

### 8.1 核心能力

- 年报语义检索
- 风险因素检索
- 管理层讨论检索
- 法律 / 合规语义检索
- 多市场比较检索
- 证据支持的 claim resolution

### 8.2 推荐路由

```text
POST /api/public/v1/search/filings
POST /api/public/v1/search/risk-factors
POST /api/public/v1/search/management-discussion
POST /api/public/v1/search/legal
POST /api/public/v1/search/company-context
POST /api/public/v1/resolve-claim
```

### 8.3 查询参数建议

```json
{
  "query": "ASML 2025 年报里对中国出口管制风险怎么描述",
  "markets": ["US", "EU"],
  "corpora": ["filings", "legal"],
  "company": {
    "ticker": "ASML",
    "name": "ASML Holding"
  },
  "period": "FY2025",
  "jurisdiction": "EU",
  "top_k": 10,
  "rerank": true,
  "return_citations": true,
  "return_highlights": true
}
```

### 8.4 语义 API 的输出重点

不要只返回相似段落，要返回：

- 命中的证据片段
- 对应 filing / section / page / table
- source map 路径
- 质量状态
- 是否需要人工复核

这样语义查询才是“可引用的语义查询”。

## 9. PostgreSQL 的服务化边界

PostgreSQL 适合承载事实和索引，但不适合直接对外开放 SQL。

### 9.1 适合服务化的内容

- 公司主数据
- 市场与证券标识
- filing 列表
- 结构化财务指标
- 指标时间序列
- 事实引用
- evidence 元数据
- 对比结果

### 9.2 不建议直接暴露的内容

- 原始内部表结构
- 通用 SQL 连接
- 内部审计字段
- 解析中间态
- 未过滤的用户数据

### 9.3 推荐查询模式

面向外部用户的 PostgreSQL 服务应该是 domain API，而不是 SQL portal：

- `get company profile`
- `list filings`
- `get metric`
- `compare peers`
- `resolve evidence`
- `fetch facts by period`

### 9.4 为什么这样做

原因很简单：

- 降低注入和越权风险
- 降低 schema 漏出风险
- 方便做版本演进
- 方便做缓存和限流
- 方便把多个市场统一成一套业务语义

## 10. Milvus 的服务化边界

Milvus 适合做召回，不适合做真相源。

### 10.1 适合服务化的内容

- filings 语义段落
- legal corpus 语义段落
- 风险因素 chunk
- 管理层讨论 chunk
- 投资/合规主题 chunk

### 10.2 不建议直接暴露的内容

- 原始向量值
- collection 管理权限
- 内部实验 collection
- 未过滤的私有语料

### 10.3 推荐的对外语义能力

建议只暴露“查询意图”，不要让用户感知 collection 细节：

- `semantic_search_filings`
- `semantic_search_legal`
- `find_evidence_for_claim`
- `compare_company_context`

### 10.4 检索结果应包含什么

每个 hit 至少要有：

- `text`
- `score`
- `market`
- `company`
- `filing_id`
- `section_path`
- `evidence_id`
- `open_url`
- `quality_status`

## 11. MCP Server 设计

MCP 适合做 Agent 适配层，不适合承载核心事实逻辑。

### 11.1 角色定位

MCP Server 的职责是：

- 把 SIQ 的领域能力暴露给 Agent
- 提供工具列表
- 提供资源读取
- 做最小必要的上下文编排

它不是独立数据层，也不是数据库网关。

### 11.2 推荐工具

```text
siq_get_company_profile
siq_get_filing_list
siq_get_filing_detail
siq_get_financial_metric
siq_compare_companies
siq_search_semantic_corpus
siq_search_legal_corpus
siq_resolve_evidence
siq_export_okf_bundle
siq_get_okf_bundle_index
```

### 11.3 推荐资源

```text
siq://company/{market}/{ticker}
siq://filing/{filing_id}
siq://evidence/{evidence_id}
siq://bundle/{bundle_id}
siq://legal/{jurisdiction}/{topic}
```

### 11.4 MCP 与 REST 的关系

- REST 是稳定商业接口
- MCP 是 Agent 友好适配层
- 二者共享同一套内部 service layer

这样可以避免重复实现，也不会把商业接口绑死在某一种 Agent 协议上。

## 12. 权限、合规与商业边界

这是这类产品成败的关键之一。

### 12.1 建议的访问层级

1. **Public Demo**
   - 只开放精选样本和有限查询
   - 适合试用和演示

2. **Developer API**
   - API key + quota
   - 适合开发者和小规模产品集成

3. **Partner API**
   - 绑定组织、项目、用途
   - 可接入私有语料和定制索引

4. **Enterprise Private**
   - VPC / 私有部署 / 本地部署
   - 适合金融机构和合规要求高的客户

### 12.2 合规风险

必须逐市场审查：

- 官方披露的再分发范围
- 第三方资料的版权与使用权
- 研报、翻译、整理内容的许可边界
- 法律文本与数据库的授权条款

因此，内部 canonical store 和对外可分发包之间要有清晰白名单。

### 12.3 质量风险

多市场天然会遇到：

- 会计准则差异
- 币种和单位差异
- OCR 误差
- 表格跨页
- XBRL tag 不一致
- 语义段落不可直接比对

所以每个返回都应该带：

- `quality_status`
- `coverage_ratio`
- `warnings`
- `market_standard`
- `needs_review`

## 13. 推荐模块划分

### 13.1 第一阶段先不拆太散

建议先复用 `apps/api` 作为统一 gateway，再逐步拆分：

```text
apps/api
  -> routers/public_knowledge.py
  -> routers/mcp_gateway.py
  -> services/knowledge/
  -> services/okf_export/
  -> services/semantic_retrieval/
  -> services/fact_query/
```

### 13.2 成熟后再拆独立服务

如果流量和组织边界起来了，再拆：

```text
services/siq-knowledge-api/
services/siq-mcp-server/
services/okf-exporter/
```

### 13.3 推荐的内部依赖方向

```text
market-report-finder / document-parser
  -> evidence packages
  -> PostgreSQL / Milvus / Wiki
  -> knowledge service
  -> REST / MCP / OKF
```

不要反向让对外服务去调用原始解析器做实时重活。

## 14. 分阶段实施

### Phase 0：内核整理

目标：

- 统一 evidence package 到知识服务的数据入口
- 统一 company / filing / metric / evidence 的 ID 规则
- 明确哪些数据可公开，哪些只能内部使用

交付：

- 知识对象模型
- OKF frontmatter 规范
- 公共 API 响应壳

### Phase 1：只读 REST API

目标：

- 先把 Structured API 和 Semantic API 做出来
- 先开放给内部和少量合作方

交付：

- `/api/public/v1/*`
- 证据引用返回
- 质量状态返回
- 限流和 API key

### Phase 2：OKF Export

目标：

- 从现有 wiki / evidence package 生成 OKF bundle
- 支持 git 级版本管理和下载

交付：

- `data/okf/` 或 `data/wiki/okf/`
- `export_okf_bundle.py`
- bundle index / log / concept docs

### Phase 3：MCP Server

目标：

- 把 REST 能力映射成 MCP tools / resources
- 让外部 Agent 能直接调用

交付：

- MCP tools 列表
- MCP 资源读取
- tool-to-REST mapping

### Phase 4：商业化与分层部署

目标：

- Partner API
- Enterprise Private
- 计费 / 用量 / 配额 / 审计

交付：

- key 管理
- usage events
- billing hooks
- tenant isolation

## 15. 验收标准

### 15.1 产品验收

用户可以：

- 按市场和 ticker 查询公司 profile
- 按 filing 获取事实和证据
- 按自然语言检索年报 / 法律语义
- 直接拿到可引用证据
- 下载 OKF bundle
- 用 MCP 调用相同能力

### 15.2 技术验收

- 每个事实返回至少一个证据引用
- 每个语义结果返回 source locator
- 每个 OKF concept 可通过 git diff 审核
- REST 与 MCP 共享同一事实层
- 不直接暴露 raw SQL 和 raw vector
- 返回结果带 quality status

### 15.3 商业验收

- 有清晰的 public / partner / enterprise 分层
- 有明确的市场授权边界
- 有 API key、quota、审计日志
- 有版本管理和可回滚 bundle

## 16. 风险与规避

### 16.1 风险：把 OKF 当成运行时

规避：

- OKF 只做格式，不做执行层
- 内部仍保留 PostgreSQL / Milvus / Wiki

### 16.2 风险：直接开放数据库

规避：

- 只开放 domain API
- 只开放经过证据封装的结果

### 16.3 风险：语义结果无证据

规避：

- 语义检索必须返回 evidence
- claim resolution 必须返回 citations

### 16.4 风险：跨市场口径失真

规避：

- 明确市场、准则、币种、期间
- 允许 `quality_status = warning`
- 不把不可比项硬归一

### 16.5 风险：内容授权不清

规避：

- 公共层只放可分发内容
- 其他内容保留在 private / partner 层

## 17. 推荐目录与文件

建议先新增这些设计与实现入口：

```text
docs/architecture/2026-06-28-siq-knowledge-service-okf-mcp-api-design.md
apps/api/routers/public_knowledge.py
apps/api/routers/mcp_gateway.py
apps/api/services/knowledge_service.py
apps/api/services/okf_exporter.py
scripts/okf/export_okf_bundle.py
```

后续如果流量起来，再拆成独立服务目录。

## 18. 参考资料

- Google Cloud Blog: [Introducing the Open Knowledge Format](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing)
- Google Cloud OKF Spec: [Open Knowledge Format (OKF) v0.1](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
- Google Cloud OKF README: [OKF repository overview](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/README.md)
- MCP Specification: [Model Context Protocol](https://github.com/modelcontextprotocol/specification)
