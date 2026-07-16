# 二级市场三类智能体全市场支持开发任务书

> 状态：待开发
>
> 日期：2026-07-16
>
> 适用仓库：<code>/home/maoyd/siq-research-engine</code>
>
> 目标执行者：Codex 或具备仓库读写权限的开发人员
>
> 本期范围：智能分析、事实核查、持续跟踪支持已解析的中国内地、香港、美国、欧洲、韩国、日本市场公司；CN 保持原链，境外五市场使用独立兼容链
>
> 明确排除：境外法务合规、解析器改造、市场 Wiki 重建、无关功能或视觉改版

## 1. 任务结论

本期不是简单地在前端增加一个市场下拉框，也不是把 A 股目录根路径替换为其他市场路径。完整目标是：

1. 在智能分析、事实核查、持续跟踪三个页面的公司选择框前增加“市场”选择框。
2. 市场名称、顺序和中文描述复用现有前端市场元数据：

   | 顺序 | 市场代码 | 展示名称 |
   | --- | --- | --- |
   | 1 | CN | 中国内地市场 |
   | 2 | HK | 香港市场 |
   | 3 | US | 美国市场 |
   | 4 | EU | 欧洲市场 |
   | 5 | KR | 韩国市场 |
   | 6 | JP | 日本市场 |

3. 选择市场后，只展示该市场下达到报告级 <code>parsed_ready</code> 的公司；选择公司后，只展示该公司的已解析源报告。
4. 智能分析、事实核查、持续跟踪必须使用用户选中的确切公司和确切源报告，完整携带 <code>market</code>、<code>company_id</code>、<code>filing_id</code>、<code>parse_run_id</code>。
5. 中国内地市场继续逐字复用原 `siq_analysis / siq_factchecker / siq_tracking` profile、配置、脚本和 HTML 生成策略；HK、JP、KR、EU 的 PDF 类报告由独立 `_multi_market` profile 适配，不得修改或复用覆盖 CN 配置。
6. 美国 SEC 报告采用独立的输入规范化和证据适配链，直接消费现有 HTML/iXBRL/XBRL 产物；其任务状态、API、UI、输出契约、审计和产物目录仍与其他市场共享。
7. 事实核查和持续跟踪共享统一的公司、源报告、数值事实和证据契约，并通过 PDF 与 SEC 两类适配器读取市场差异。
8. 法务合规保持中国内地市场现状，不扩展境外公司，不修改法律法规库、法务 profile 或法务工作流。

核心设计原则是：

> 消费逻辑对齐、物理产物允许差异；共享产品链路、按源文档家族适配输入和证据。

### 1.1 后续确认的 A 股硬隔离约束

本节优先级高于任务书中任何“六市场统一模板”“CN 进入共享 adapter”或“CN 正式 bundle”表述：

1. `CN` 的分析报告生成必须继续走原 `company/year -> resolve_company.py -> run_analysis_report.py -> html_renderer_v2` 链路。
2. `CN` 的事实核查和持续跟踪必须继续走原 company/year/report-path 或 stock/company 兼容链。
3. 原 `siq_analysis`、`siq_factchecker`、`siq_tracking` 的所有已跟踪配置、规则和脚本必须与开发前 `HEAD` 一致。
4. 今日新增能力独立放入 `siq_analysis_multi_market`、`siq_factchecker_multi_market`、`siq_tracking_multi_market` 及 `tracking/scripts_multi_market`，生产范围仅为 `HK/US/EU/KR/JP`。
5. API 与 runner 双层拒绝 CN 进入 bundle；功能开关开启也不能改变该规则。
6. 六市场验收拆为 `CN legacy golden regression` 与 `5 个境外市场 formal v2 smoke`，不得用新 claims/evidence/template 门禁重渲染 CN。
7. 法务合规仍保持 CN-only，不做任何境外扩展。

### 1.2 2026-07-16 实施验收记录

本任务书所述多市场开发已完成本轮实现与聚焦发布门禁，验收状态如下：

1. CN 原 `siq_analysis / siq_factchecker / siq_tracking` 工作树与开发基线 `HEAD` 一致；CN 不进入 bundle，不生成 AgentArtifactV2，只以美的集团历史精品报告做只读 golden 回归。
2. HK、US、EU、KR、JP 使用独立 `_multi_market` profile；五个市场均实际生成 analysis、factcheck、tracking，共 15 份新产物。
3. 六市场真实 gate 为 `6/6 passed`，五个境外市场内容质量为 `5/5 passed`，六家公司事实面哈希前后一致。权威验收记录见 <code>artifacts/secondary-market-multi-market/real-smoke.sanitized.json</code>。
4. 境外 HTML 设 512 KiB 上限；完整证据保留在 JSON/sidecar，HTML 只展示核心 claim 的可读定位摘要。AAPL 分析 HTML 从约 1.29 MiB 降至约 42 KiB，跟踪 HTML 从约 1.46 MiB 降至约 41 KiB；2061 条完整证据仍保留在 JSON，HTML 展示 14 条核心定位。
5. 前端三页面均以市场作为公司前置选择，首屏 artifact 查询固定 <code>limit=1</code>，只有用户选择后才加载对应 HTML；市场、公司、源报告切换会中止旧列表和分页请求。法务页面保持 CN-only。
6. 合并后聚焦验收：market-contracts 37 passed；后端多市场矩阵 105 passed；境外 analysis/smoke 103 passed；原 A 股 analysis 31 passed、tracking 2 passed；前端 480 passed、production build 通过、Playwright 7 passed。
7. API 全量基线曾运行到 2695 passed、6 skipped、14 failed；其中本任务相关的 tracking YAML 降噪断言已修复并通过聚焦复测。剩余 13 项属于并行 OpenShell/投委会、一级市场或本地缺少 pytest-asyncio，不在本任务授权范围，因此未改动相应模块。

本节记录实际完成状态；下方任务清单保留为需求追踪明细，不应再用于指示 CN 进入新模板或共享 adapter。

## 2. 开发前必须遵守的范围边界

### 2.1 本期允许修改

- 三个页面的市场、公司、源报告和生成结果级联选择。
- 支撑上述页面的只读多市场查询 API。
- 智能分析、事实核查、持续跟踪的请求契约、公司解析、输入适配、证据适配、渲染、产物索引和必要测试。
- 与上述链路直接相关的共享 ResearchIdentity、数值事实、引用定位和派生产物契约。
- 为灰度、回滚和故障定位所必需的功能开关、结构化日志和指标。

### 2.2 本期禁止修改

- 不修改 <code>apps/pdf-parser</code>、<code>apps/document-parser</code> 或市场下载服务。
- 不修改各市场下载、解析、入库和 Wiki 构建脚本。
- 不重跑、不迁移、不删除、不重建 <code>data/wiki/&lt;market&gt;/companies/**</code>。
- 不执行既有多市场 Wiki 设计文档中的删除重建流程。
- 不批量修复现有 catalog、company.json、manifest 或历史产物。
- 不为美股补写 A 股式 <code>report.md</code>、<code>three_statements.json</code> 或 <code>key_metrics.json</code>。
- 不把美国 SEC 原始产物复制到兼容目录；兼容映射只能存在于只读适配器、内存对象或任务临时工作目录。
- 不修改 <code>siq_legal</code> profile、法务工作流、法律法规库或境外 legal 输出目录。
- 不修改一级市场、会议、认证、权限模型、部署拓扑或无关页面。
- 不做跨市场估值比较、自动汇率换算或新的实时行情系统。
- 不做与本需求无关的代码格式化、目录重组、组件重写或视觉主题调整。

### 2.3 事实面与工作面权限

以下目录是只读事实输入，三个工作流运行前后内容必须保持不变：

- <code>reports/</code>
- <code>metrics/</code>
- <code>evidence/</code>
- <code>semantic/</code>
- <code>graph/</code>
- <code>company.json</code>
- <code>_index.json</code> 中由入库流程维护的事实字段

本期只允许向公司工作区中的下列派生目录写入新产物：

- <code>analysis/</code>
- <code>factcheck/</code>
- <code>tracking/</code>

删除操作只允许删除明确选中的派生产物及其 sidecar，不得触及源报告、manifest、指标、证据或其他解析产物。

## 3. 已确认的仓库现状

### 3.1 已有能力

- <code>apps/api/services/agent_runtime_catalog.py</code> 已支持 CN、HK、US、JP、KR、EU catalog 和多市场公司解析。
- <code>apps/api/services/agent_runtime_context.py</code> 已定义完整 ResearchIdentity，并对非 CN 身份不完整场景执行失败关闭。
- <code>apps/api/services/agent_runtime_wiki_context.py</code> 已具备 manifest 感知的报告选择、全文路径和部分市场产物回退逻辑。
- <code>apps/web/src/lib/marketMetadata.ts</code> 已定义本期需要的市场顺序、名称和描述。
- 前后端聊天上下文已经包含 <code>market</code>、<code>company_id</code>、<code>filing_id</code>、<code>parse_run_id</code> 字段。
- 现有多市场 Wiki 逻辑目录已经对齐公司工作区，设计依据见 <code>docs/architecture/2026-07-07-a-share-aligned-multi-market-wiki-design.md</code>。

这些能力必须复用，不得再创建第四套公司模糊匹配、市场根目录映射或 ResearchIdentity 推断逻辑。

### 3.2 当前限制

- <code>apps/web/src/components/report/ReportViewer.tsx</code> 固定请求 <code>/api/wiki/companies/list</code>。
- <code>apps/web/src/components/report/ReportSelector.tsx</code> 当前只有“公司”和“报告版本”，其中“报告版本”实际代表已生成 HTML，不是已解析源报告。
- <code>apps/api/routers/wiki.py</code> 固定以 <code>data/wiki/companies</code> 作为 A 股公司根目录。
- 三个正式工作流仍以公司名、六位代码或年份作为主要输入，并在内部重新推断目录和“最新报告”。
- 智能分析脚本仍存在根 catalog、<code>2025</code>、<code>&lt;year&gt;-annual</code>、人民币、亿元、A 股文案和 PDF 页码等假设。
- 事实核查仍存在 A 股 catalog、A 股 PostgreSQL、六位代码、亿元正则和 PDF-only 引用假设。
- 持续跟踪仍会拼接 <code>companies/&lt;stock&gt;-&lt;name&gt;</code>，并假设六位代码、中文指标和中国市场舆情源。

因此，只增加前端市场下拉框不会完成本需求。

### 3.3 美国市场产物事实

截至 2026-07-16 的本地只读盘点：

- 美股 Wiki 有 50 家公司、51 个报告包。
- 51 个报告包均已有 <code>parser/document_full.json</code>。
- 51 个报告包均已有 <code>sections/report_complete.md</code>。
- 51 个报告包均已有 <code>metrics/financial_data.json</code>。
- 51 个报告包均已有 <code>metrics/normalized_metrics.json</code>。
- 51 个报告包均已有 <code>metrics/financial_checks.json</code>。
- 16 个报告包质量状态为 <code>pass</code>，35 个为 <code>warning</code>。

这些计数只是开发基线快照，运行时不得写死数量。美股当前缺少的是智能体下游生成结果，不是上述核心解析产物。

## 4. 术语与不可混淆的状态

### 4.1 已解析源报告版本

已解析源报告版本是一个 filing/report 包，至少由以下信息唯一确定：

- <code>market</code>
- <code>company_id</code>
- <code>report_id</code>
- <code>filing_id</code>
- <code>parse_run_id</code>

它对应 <code>reports/&lt;report_id&gt;/manifest.json</code> 及 manifest 声明的全文、指标和证据文件。

### 4.2 生成结果版本

生成结果版本是 analysis、factcheck 或 tracking 的派生产物。每个生成结果必须反向绑定：

- 一个确切的已解析源报告版本；
- 完整 ResearchIdentity；
- 所使用的输入适配器和版本；
- 对于事实核查，确切的上游分析产物；
- 对于持续跟踪，确切的分析基线和跟踪检查点。

### 4.3 严禁混用

前端和 API 不得继续用一个“报告版本”字段同时表示上述两个概念。

目标选择顺序是：

~~~text
市场 -> 公司 -> 已解析源报告版本 -> 生成结果版本
~~~

公司只有一个源报告时可以自动选择，但状态模型、URL 参数、聊天上下文和工作流输入仍必须保留独立的 <code>report_id</code>。

无显式 URL 选择参数时，二级市场报告首屏统一定位到中国内地市场的上汽集团（证券代码 <code>600104</code>）。新链路按 <code>market=CN + display_code=600104</code> 解析权威 <code>company_key</code>，旧版 CN-only 链路可使用 <code>600104-上汽集团</code> 目录兼容；不得依赖公司接口返回顺序。显式 <code>market/company_key/report_id/artifact_id</code> 或旧版 <code>company/result</code> 参数始终高于首屏默认值。

## 5. 目标用户体验

### 5.1 页面适用范围

| 页面 | 市场范围 | 行为 |
| --- | --- | --- |
| 智能分析 | all-parsed | 显示六个市场和对应 parsed-ready 公司 |
| 事实核查 | all-parsed | 显示六个市场；只对有匹配分析基线的源报告启用正式核查 |
| 持续跟踪 | all-parsed | 显示六个市场；只对有匹配分析基线的源报告启用正式跟踪 |
| 法务合规 | cn-only | 保持现状，不显示境外公司 |

共享 <code>ReportViewer</code> 的市场范围参数默认值必须是 <code>cn-only</code>。只有前三个页面显式传入 <code>all-parsed</code>，避免法务页面因共享组件变更意外扩展。

### 5.2 选择控件

工具栏保持现有视觉语言、标题、步骤标签、页面说明、下载、分享和删除按钮，不做整体改版。控件顺序调整为：

1. 市场
2. 公司
3. 财报版本或源报告
4. 分析结果、核查结果或跟踪结果

展示要求：

- 市场使用 <code>DISCLOSURE_MARKET_ORDER</code> 和 <code>DISCLOSURE_MARKETS</code>，不复制另一套前端常量。
- 公司选项显示市场内证券代码或 ticker 加公司名称。
- 源报告选项显示报告类型、财年或期间截止日、发布日和质量状态，例如“2025 10-K · 截止 2025-09-27 · warning”。
- <code>warning</code> 可选择，但在选择框和空状态中展示非阻断警告。
- <code>fail</code> 默认不展示；管理员诊断接口可通过显式参数查看失败原因。
- 生成结果选择框只显示绑定当前源报告身份的产物。
- 没有生成结果时，源报告仍可选择，聊天智能体仍可获得完整上下文并启动生成。

### 5.3 级联重置

切换市场时，必须在同一次状态变更中清空：

- 公司
- 源报告
- 生成结果
- iframe HTML
- 内容加载状态
- 删除确认
- 报错
- 当前聊天上下文中的公司、报告和 ResearchIdentity

切换公司时必须清空源报告及以下状态；切换源报告时必须清空生成结果及以下状态。

不得在新列表加载期间继续向 Agent 暴露上一个市场或上一个公司的上下文。

### 5.4 URL 状态

新 URL 参数：

- <code>market</code>
- <code>company_key</code>
- <code>report_id</code>
- <code>artifact_id</code>

恢复顺序必须与级联选择一致。任何参数无效时，从该层开始降级到第一个可用项，不得跨市场模糊匹配。

旧 CN 链接 <code>?company=&amp;result=</code> 继续兼容：

- 只在 CN 范围解析；
- 成功后转换为新的内部选择状态；
- 不把旧目录名参数用于境外市场路径解析。

分享链接必须写入新参数；下载和删除必须使用 <code>artifact_id</code>，不得让浏览器提交文件系统路径。

### 5.5 空状态和错误状态

至少区分：

- 市场暂无 parsed-ready 公司；
- 公司暂无 parsed-ready 源报告；
- 源报告质量为 warning；
- 源报告暂无分析结果；
- 事实核查缺少匹配分析基线；
- 持续跟踪缺少匹配分析基线；
- 当前市场来源能力降级；
- ResearchIdentity 不完整；
- 源报告与生成结果身份不一致；
- 服务端拒绝了不安全路径或跨市场 key。

错误必须可操作、不可误导，不得自动回退到 A 股公司、“最新报告”或同名 ticker。

## 6. 目标架构

~~~mermaid
flowchart LR
    UI[三个全市场页面] --> RU[Research Universe API]
    RU --> CAT[多市场 Catalog]
    RU --> RES[Report Package Resolver]
    RES --> MAN[Report Manifest]
    MAN --> PDF[PDF Market Adapter]
    MAN --> SEC[SEC HTML/XBRL Adapter]
    PDF --> FACTS[Normalized Facts and Evidence]
    SEC --> FACTS
    FACTS --> ANA[Analysis Workflow]
    ANA --> ART[Shared Agent Artifact Contract]
    ART --> FC[Factcheck Workflow]
    ART --> TR[Tracking Workflow]
    FC --> OUT[Market-aware Derived Outputs]
    TR --> OUT
    ANA --> OUT
~~~

架构分层：

1. Research Universe 只负责市场、公司、源报告、能力和生成结果的受控枚举。
2. Report Package Resolver 只根据 catalog 和 manifest 解析权威身份与安全路径。
3. Source Adapter 负责把不同物理产物规范化为共享事实和证据。
4. 三个工作流只消费已解析的服务端对象，不自行猜公司目录或报告版本。
5. 生成产物共享一个版本化 sidecar 契约和市场感知的读取 API。

## 7. 共享契约

共享纯数据契约优先放入 <code>packages/market-contracts</code>，API、Hermes 脚本和测试共同引用。不得在三个工作流中分别复制字段定义。

### 7.1 ResearchIdentity

正式生成任务必须包含全部四个字段：

~~~json
{
  "market": "US",
  "company_id": "US:0000320193",
  "filing_id": "US:0000320193:0000320193-25-000079",
  "parse_run_id": "authoritative-parse-run-id"
}
~~~

规则：

- manifest 和报告级索引优先于目录名。
- 非 CN 任一字段缺失时失败关闭。
- CN 旧数据只能通过已有权威兼容解析补齐；不得根据目录名伪造身份。
- 用户提交的 ResearchIdentity 只是选择提示，服务端必须重新解析并比对。

### 7.2 ResearchTargetV1

这是可序列化的正式工作流输入：

~~~json
{
  "schema_version": "siq_research_target_v1",
  "company_key": "server-issued-opaque-key",
  "company_wiki_id": "AAPL-Apple-Inc",
  "display_code": "AAPL",
  "display_name": "Apple Inc",
  "research_identity": {
    "market": "US",
    "company_id": "US:0000320193",
    "filing_id": "US:0000320193:0000320193-25-000079",
    "parse_run_id": "authoritative-parse-run-id"
  },
  "source_report": {
    "report_id": "2025-10-K-0000320193-25-000079",
    "source_family": "sec_ixbrl",
    "document_format": "ixbrl_html",
    "report_type": "annual",
    "form_type": "10-K",
    "fiscal_year": 2025,
    "period_end": "2025-09-27",
    "published_at": "2025-10-31",
    "accounting_standard": "US_GAAP",
    "reporting_currency": "USD",
    "quality_status": "warning"
  }
}
~~~

客户端不得提交 <code>company_dir</code> 或任何绝对路径。服务端内部的 <code>ResolvedReportPackage</code> 可以持有经过边界校验的 Path 对象，但不得原样返回浏览器。

### 7.3 ResolvedReportPackage

服务端内部对象至少包含：

- ResearchTargetV1 的全部字段；
- 权威 market root、company dir、report dir；
- manifest path 和 manifest 内容摘要；
- 全文候选的已解析安全路径；
- 指标候选的已解析安全路径；
- source map、表格、XBRL 和图片证据候选；
- 产物输出目录；
- 每项能力的 readiness 和不可用原因。

所有路径必须：

1. 来自 catalog、company metadata 或 manifest 声明；
2. 经过 <code>resolve()</code> 后仍位于批准的市场根目录；
3. 拒绝绝对路径注入、<code>..</code>、符号链接逃逸和跨市场 company key；
4. 不以客户端参数直接拼接。

### 7.4 NormalizedFactV1

~~~json
{
  "schema_version": "siq_normalized_fact_v1",
  "metric_key": "revenue",
  "raw_label": "Revenue",
  "raw_value": "391035",
  "normalized_value": 391035000000,
  "currency": "USD",
  "raw_unit": "USD millions",
  "scale": 1000000,
  "period_start": "2024-09-29",
  "period_end": "2025-09-27",
  "accounting_standard": "US_GAAP",
  "research_identity": {},
  "evidence_refs": []
}
~~~

规则：

- 保留原始值、原始单位、币种和 scale。
- 不默认转换成人民币或亿元。
- 缺失值保持缺失，不转为 0。
- 跨期比较只有在币种、单位、期间和会计口径兼容时才执行。
- 同业比较只有在市场、币种、准则、行业和报告期可比时才执行；否则明确省略。

### 7.5 EvidenceRefV1

统一引用模型同时支持 PDF、HTML 和 XBRL：

~~~json
{
  "schema_version": "siq_evidence_ref_v1",
  "research_identity": {},
  "report_id": "2025-10-K-...",
  "kind": "xbrl_fact",
  "source_url": "https://www.sec.gov/...",
  "local_source_id": "raw/filing.htm",
  "pdf_task_id": null,
  "pdf_page": null,
  "table_id": "table_0012",
  "section_id": "mda",
  "html_anchor": "item7",
  "xpath": null,
  "xbrl_fact_id": "fact-123",
  "xbrl_concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
  "xbrl_context": "FY2025",
  "xbrl_unit": "USD",
  "quote": "short evidence excerpt"
}
~~~

<code>apps/api/services/specialist_artifact_contract.py</code> 的引用门禁必须接受新的 HTML/XBRL 定位字段，同时保留现有 PDF task/page/table 和 Markdown line 定位。不得为 SEC 报告伪造 PDF 页码。

### 7.6 AgentArtifactV2

每份新生成结果必须有同名 sidecar：

~~~text
analysis/<artifact_id>.html
analysis/<artifact_id>.artifact.json

factcheck/<artifact_id>.html
factcheck/<artifact_id>.artifact.json

tracking/<artifact_id>.html
tracking/<artifact_id>.artifact.json
~~~

sidecar 至少包含：

~~~json
{
  "schema_version": "siq_agent_artifact_v2",
  "artifact_id": "stable-id",
  "artifact_type": "analysis",
  "status": "completed",
  "created_at": "2026-07-16T00:00:00Z",
  "research_target": {},
  "source_report_id": "2025-10-K-...",
  "source_family": "sec_ixbrl",
  "adapter_version": "sec_ixbrl_v1",
  "upstream_artifact_ids": [],
  "html_file": "<artifact_id>.html",
  "content_hash": "sha256",
  "quality": {
    "status": "pass",
    "warnings": []
  },
  "evidence_summary": {
    "citation_count": 0,
    "unresolved_count": 0
  }
}
~~~

写入规则：

- HTML 和 sidecar 先写入任务工作目录，验证通过后原子发布。
- 不能先发布 HTML 再留下无 sidecar 的半成品。
- factcheck 的 <code>upstream_artifact_ids</code> 必须含被核查的 analysis artifact。
- tracking 必须记录分析基线和检查点。
- 历史 CN HTML 可作为 <code>legacy_unbound</code> 展示，但未能绑定确切源身份时不得作为新事实核查或跟踪的正式基线。
- 不批量回写历史产物。

## 8. Readiness 与能力模型

readiness 是报告级状态，不等于 catalog 中存在公司，也不等于已经生成 HTML。

| 状态 | 判定条件 | 用途 |
| --- | --- | --- |
| catalog_visible | catalog 有权威公司记录且 company dir 存在 | 可进入诊断列表 |
| identity_ready | 四字段 ResearchIdentity 完整且一致 | 可绑定正式任务 |
| parsed_ready | identity_ready；manifest 可读；全文、结构化指标、source map 可读 | 可在页面选择 |
| analysis_input_ready | parsed_ready 且存在已支持的 source adapter | 可生成分析 |
| analysis_output_ready | 存在匹配同一源身份的有效 analysis sidecar | 可查看、可作为下游基线 |
| factcheck_ready | analysis_output_ready 且引用可被对应证据适配器解析 | 可运行事实核查 |
| tracking_ready | analysis_output_ready 且市场跟踪策略可用 | 可运行持续跟踪 |

<code>parsed_ready</code> 的最低检查：

1. 公司目录存在。
2. report manifest 存在且 JSON 可读。
3. ResearchIdentity 完整且 company/report/manifest 一致。
4. 至少一个完整全文入口可读。
5. 至少一个结构化财务指标入口可读。
6. 精确 evidence/source map 可读。
7. 产物路径均未越出市场 Wiki 根。
8. <code>quality_status</code> 不是 fail。

质量策略：

- pass：正常可选。
- warning：可选，必须在 UI、工作流输入和 sidecar 中保留警告。
- fail：默认不进入公司可用计数和选择列表。
- 缺 PostgreSQL 不阻断 Wiki-first 分析；如使用 PostgreSQL 增强，必须按完整身份命中，否则跳过，不能跨库或按 ticker 回退。

## 9. Research Universe API

新增独立 router 和 service，不继续把多市场职责堆入 <code>apps/api/routers/wiki.py</code>。

### 9.1 建议文件

- <code>apps/api/services/research_universe_contracts.py</code>
- <code>apps/api/services/research_universe.py</code>
- <code>apps/api/services/research_report_package.py</code>
- <code>apps/api/routers/research_universe.py</code>
- <code>apps/api/tests/test_research_universe.py</code>
- <code>apps/api/tests/test_research_report_package.py</code>

### 9.2 API 契约

#### 市场

~~~http
GET /api/research-universe/markets?agent_type=analysis
~~~

返回市场代码、显示名称、顺序、是否启用、可选公司数和能力降级原因。

<code>agent_type</code> 允许 analysis、factcheck、tracking、legal。legal 即使被调用也只能返回 CN，作为反向安全门禁。

#### 公司

~~~http
GET /api/research-universe/companies?market=US&agent_type=analysis&q=AAPL
~~~

返回：

- <code>company_key</code>
- <code>market</code>
- <code>company_id</code>
- <code>company_wiki_id</code>
- <code>display_code</code>
- <code>display_name</code>
- <code>parsed_report_count</code>
- readiness/capabilities

只返回服务端枚举出的 key，不返回本地绝对路径。

#### 已解析源报告

~~~http
GET /api/research-universe/companies/{company_key}/reports?market=US&agent_type=analysis
~~~

返回 report_id、报告标签、报告类型、财年、期间截止日、发布日期、质量状态、完整 ResearchIdentity 和能力。

#### 生成结果

~~~http
GET /api/research-universe/companies/{company_key}/artifacts?market=US&artifact_type=analysis&report_id=2025-10-K-...
~~~

只返回与当前 ResearchIdentity 精确一致的生成结果。

#### 产物内容与删除

~~~http
GET /api/research-universe/artifacts/{artifact_id}/content
DELETE /api/research-universe/artifacts/{artifact_id}
~~~

要求：

- 保留现有 <code>company.view</code>、<code>report.view</code>、<code>report.delete</code> 权限语义。
- artifact_id 必须从受控索引解析。
- 删除只允许 analysis、factcheck、tracking 下的目标 HTML、sidecar 和该产物独占的临时文件。
- 不接受任意 filename 或路径。

### 9.3 错误码

至少提供稳定错误码：

- <code>market_not_supported</code>
- <code>company_not_found</code>
- <code>company_market_mismatch</code>
- <code>source_report_not_found</code>
- <code>research_identity_incomplete</code>
- <code>research_identity_mismatch</code>
- <code>source_package_not_ready</code>
- <code>source_adapter_unavailable</code>
- <code>artifact_not_found</code>
- <code>artifact_identity_mismatch</code>
- <code>unsafe_path_rejected</code>
- <code>permission_denied</code>

### 9.4 缓存

如增加缓存：

- key 必须包含 market、agent_type、readiness 版本和 catalog/manifest mtime 摘要；
- market 切换不得复用其他市场列表；
- 写入新生成产物后只失效对应公司和 artifact_type；
- 不直接复用 <code>wiki.py</code> 的全局 CN 公司列表缓存。

## 10. 前端实现

### 10.1 建议文件

新增：

- <code>apps/web/src/features/research-universe/types.ts</code>
- <code>apps/web/src/features/research-universe/api.ts</code>
- <code>apps/web/src/features/research-universe/selectionModel.ts</code>
- 对应单元测试

修改：

- <code>apps/web/src/components/report/ReportViewer.tsx</code>
- <code>apps/web/src/components/report/ReportSelector.tsx</code>
- <code>apps/web/src/lib/reportTypes.ts</code>
- <code>apps/web/src/pages/AnalysisReport.tsx</code>
- <code>apps/web/src/pages/FactVerification.tsx</code>
- <code>apps/web/src/pages/Tracking.tsx</code>

<code>LegalCompliance.tsx</code> 原则上不改。若类型签名要求显式参数，只能添加 <code>marketScope="cn-only"</code>，不得改变其数据源或页面行为。

### 10.2 组件契约

为 <code>ReportViewer</code> 增加：

~~~ts
marketScope?: 'cn-only' | 'all-parsed'
~~~

默认 <code>cn-only</code>。前三个页面显式设置 <code>all-parsed</code>。

将旧的 <code>Company</code> 和 <code>ReportItem</code> 展示模型逐步分成：

- ResearchCompanyOption
- SourceReportOption
- GeneratedArtifactOption

不得继续用 <code>selectedDir</code> 作为跨市场主键；内部使用 <code>company_key</code>，目录名只保留在 CN 旧链接兼容分支。

### 10.3 Agent 上下文

传给 <code>PageWithAgentChat</code> 的上下文必须由当前源报告构造：

~~~json
{
  "company": {
    "market": "US",
    "company_id": "US:...",
    "company_key": "...",
    "code": "AAPL",
    "name": "Apple Inc"
  },
  "source_report": {
    "report_id": "...",
    "filing_id": "...",
    "parse_run_id": "..."
  },
  "artifact": {
    "artifact_id": "...",
    "artifact_type": "analysis"
  },
  "research_identity": {
    "market": "US",
    "company_id": "US:...",
    "filing_id": "...",
    "parse_run_id": "..."
  }
}
~~~

生成结果不存在时，<code>artifact</code> 可缺省，但 <code>source_report</code> 和完整 ResearchIdentity 不得缺省。

后端不能信任浏览器传入的路径或身份；工作流启动前必须通过 company_key 和 report_id 重新解析并比对。

### 10.4 前端测试

覆盖：

- 市场标签和顺序完全复用现有元数据。
- 市场 -> 公司 -> 源报告 -> 生成结果级联加载。
- 没有生成结果的海外公司仍可选择并向 Agent 提供上下文。
- 切换市场不会短暂保留旧 ResearchIdentity。
- warning 源报告可选且显示警告。
- fail 源报告默认隐藏。
- URL 新参数恢复。
- 旧 CN 链接兼容。
- 下载、分享、删除使用 artifact_id。
- 法务页面没有境外市场选择。
- 手工给法务 URL 增加 US/HK 参数不会暴露境外公司。
- 移动端和桌面端控件不溢出、不重叠。

## 11. 智能分析目标链路

### 11.1 共享编排

<code>apps/api/services/analysis_report_workflow.py</code> 的正式请求由公司名和 year 改为以 ResearchTargetV1 为主：

- 页面上下文存在时，必须解析 company_key + report_id。
- 非 CN 不允许使用公司名、ticker、年份推断正式报告。
- CN 无论是否带结构化页面上下文，报告生成都必须保留原兼容行为；结构化字段只能帮助确定 company/year，不能触发新 bundle renderer。
- <code>DEFAULT_YEAR = 2025</code> 不再参与多市场正式生成。
- subprocess 接收服务端生成的只读输入 bundle 路径或明确的 target JSON，不接收客户端路径。

建议新增：

- <code>agents/hermes/profiles/siq_analysis_multi_market/scripts/input_adapters/base.py</code>
- <code>agents/hermes/profiles/siq_analysis_multi_market/scripts/input_adapters/pdf_market.py</code>
- <code>agents/hermes/profiles/siq_analysis_multi_market/scripts/input_adapters/sec_ixbrl.py</code>
- <code>agents/hermes/profiles/siq_analysis_multi_market/scripts/analysis_input_bundle.py</code>

适配路由根据 manifest 的 <code>source_family</code>、<code>document_format</code> 和 <code>source_id</code> 判定，不只写死 <code>market == US</code>。

初始路由：

| source_family | 初始市场 | 输入特点 | 分析链 |
| --- | --- | --- | --- |
| pdf_market | HK/JP/KR/EU 的 PDF 包 | report.md/document_full + PDF/table 证据 | 独立 `_multi_market` research-pack |
| sec_ixbrl | US SEC | HTML/iXBRL/XBRL + SEC sections | 独立输入和证据适配 |
| esef_ixbrl | 未来 EU 结构化披露 | ESEF/iXBRL | 本期只预留接口，不虚假标记 ready |

如果某个 EU 报告实际是本期不支持的 ESEF-only 包，应明确返回 <code>source_adapter_unavailable</code>，不得错误走 PDF 或 SEC 适配器。

### 11.2 PDF 市场适配器

仅适用于当前具备 PDF 类完整产物的 HK、JP、KR、EU 报告；CN 不进入该 adapter。

读取顺序必须 manifest-first：

1. manifest 声明的全文和报告 Markdown；
2. 报告级 <code>report.md</code>、<code>document_full.json</code>；
3. manifest 声明的 report-specific metrics；
4. <code>metrics/reports/&lt;report_id&gt;</code>；
5. <code>metrics/latest</code>；
6. 报告包内 legacy metrics；
7. source map、PDF page/table/image evidence。

以下假设只在 <code>siq_analysis_multi_market</code> 的独立副本中清理；原 <code>siq_analysis</code> 同名脚本不得修改：

- <code>resolve_company.py</code> 的根 catalog 和 A 股目录。
- <code>run_analysis_report.py</code> 的固定 year、<code>&lt;year&gt;-annual</code> 和根 metrics 文件。
- <code>provenance_utils.py</code> 的 PDF task/page-only 定位和固定报告目录。
- <code>peer_metrics_builder.py</code> 的 A 股 catalog、人民币和亿元同业口径。
- <code>market_snapshot_builder.py</code> 的人民币股价和市值字段。
- <code>html_renderer_v2.py</code> 的亿元单位、默认 2025、A 股公司文案和固定中文指标名。
- Prompt、SOUL 和 data source rule 中把目标公司定义为 A 股的表述。

渲染要求：

- 币种和单位来自 NormalizedFactV1。
- 金额可动态使用元、千、百万、十亿等可读 scale，但必须标明原币。
- 比率不附加币种。
- 报告期使用 manifest 的 fiscal period 和 period_end。
- 无行情、无同业或无某项 A 股专属指标时展示“不适用/数据不可用”，不得填 0。
- 页脚使用中性“上市公司公开披露资料”，不得写“A 股上市公司”。

### 11.3 美国 SEC 适配器

美股链直接读取 manifest 声明的现有产物：

- <code>parser/document_full.json</code>
- <code>sections/report_complete.md</code> 或 <code>parser/report_complete.md</code>
- <code>metrics/financial_data.json</code>
- <code>metrics/normalized_metrics.json</code>
- <code>metrics/financial_checks.json</code>
- <code>qa/source_map.json</code>
- <code>sections/*.md</code>
- <code>tables/table_index.json</code> 和 <code>tables/*.json</code>
- <code>xbrl/facts_raw.json</code>
- <code>xbrl/contexts.json</code>
- <code>xbrl/units.json</code>
- <code>xbrl/labels.json</code>

不得要求这些文件迁移到 A 股路径。

SEC 适配器至少处理：

- US GAAP。
- USD 与原始 scale。
- 非自然年度。
- 10-K 和 10-Q。
- accession number、filing date、accepted time 和官方 source URL。
- Business、Risk Factors、MD&A、Financial Statements、Notes、Controls 和 segment 信息。
- GAAP 与 non-GAAP 指标区分。
- XBRL concept、context、unit、period 和 fact id。
- 同名 concept 在不同 context 下的消歧。
- warning 质量状态和 financial_checks 告警。

美国链可以有单独的 research-pack 生成步骤，但必须输出同一个 AgentArtifactV2、同一套任务状态和同一套 UI/API 结果，不复制完整的“美股版产品”。

### 11.4 共同输出质量

本期新增的五个境外市场分析报告必须满足以下要求；CN 继续使用原质量门禁和原 HTML 策略：

- 绑定完整 ResearchIdentity。
- 每个核心财务结论可追溯到 EvidenceRefV1。
- 数字和引用来自同一 report_id、filing_id、parse_run_id。
- 单位和币种在表格、图表、正文中一致。
- 不将缺失指标渲染为 0。
- 不将 warning 静默变为 pass。
- 对不适用章节进行明确降级，不用 A 股模板生成虚假内容。
- 生成 HTML 和 sidecar 后再加入 artifact 列表。
- 完整证据数组保留在 JSON 结构化附件并由 sidecar 记录文件名、总数和哈希；HTML/Markdown 只渲染核心 claims 实际引用的可读定位摘要，默认折叠且最多 64 条。
- HTML 不得铺陈裸 evidence ID、完整 XBRL 事实集或原始 YAML/JSON；单份分析、事实核查、持续跟踪 HTML 均以 512 KiB 为发布门禁上限，超限必须失败关闭。

## 12. 事实核查目标链路

### 12.1 输入

境外市场正式事实核查必须明确指定：

- ResearchTargetV1；
- 被核查的 <code>analysis_artifact_id</code>；
- 对应 analysis sidecar 的 content hash；
- 当前源报告证据适配器。

不得按目录 mtime 自动挑选“最新分析报告”，不得核查与当前源报告身份不一致的 HTML。

### 12.2 需要修改的主要模块

- <code>apps/api/services/factcheck_workflow.py</code>
- <code>agents/hermes/profiles/siq_factchecker_multi_market/scripts/wiki_data_accessor.py</code>
- <code>agents/hermes/profiles/siq_factchecker_multi_market/scripts/factcheck_cli.py</code>
- <code>agents/hermes/profiles/siq_factchecker_multi_market/scripts/factcheck_engine.py</code>
- <code>agents/hermes/profiles/siq_factchecker_multi_market/scripts/generate_factcheck_html.py</code>
- <code>agents/hermes/profiles/siq_factchecker_multi_market/scripts/market_factcheck_engine.py</code>
- <code>agents/hermes/profiles/siq_factchecker_multi_market/SOUL.md</code>
- <code>apps/api/services/specialist_artifact_contract.py</code>

### 12.3 改造要求

1. <code>FactcheckWorkflowRequest</code> 以 ResearchTargetV1 和 analysis_artifact_id 为正式输入。
2. 六位代码和 year 解析只存在于未改动的 CN 原 profile；境外 profile 不读取该兼容入口。
3. <code>WikiDataAccessor</code> 接收服务端解析好的 company/report package，不再读取全局 A 股 catalog。
4. 核查引擎消费 NormalizedFactV1 和 EvidenceRefV1，不写死 2025/2024、亿元或中文指标。
5. 统一核查以下维度：

   - ResearchIdentity 一致性；
   - 数值与单位；
   - 算术和同比计算；
   - 报告期；
   - 声明与源文档一致性；
   - 引用定位可回溯性；
   - 风险披露完整性。

6. 市场风险规则从策略层注入：

   - CN 完全保留原 profile 和现有兼容规则，不由新引擎读取；
   - PDF 境外市场根据 accounting standard 和报告类型使用中性规则；
   - US 增加 US GAAP/non-GAAP、XBRL context、fiscal period、10-K/10-Q 和 SEC section 检查。

7. 原 <code>a_share_risk_completeness</code> 在新契约中改为中性 <code>market_risk_completeness</code>；CN 旧 JSON 键仅在兼容读取时保留。
8. PostgreSQL 只作为可选增强。使用时必须由 market + full ResearchIdentity 选择正确数据库/schema 并精确命中；缺库或不匹配时跳过，不得回退 CN。
9. SEC 引用必须使用 accession、source URL、section/anchor 或 XBRL fact，不得伪造 PDF 页码。
10. 任一关键身份或证据不一致时，结果为 failed/degraded，并明确原因。

### 12.4 输出

factcheck sidecar 的 <code>upstream_artifact_ids</code> 必须包含被核查 analysis artifact，另记录：

- checked_claim_count
- verified_claim_count
- contradicted_claim_count
- unsupported_claim_count
- identity_mismatch_count
- citation_locator_failure_count
- degraded_reasons

## 13. 持续跟踪目标链路

### 13.1 输入

境外市场正式持续跟踪必须明确指定：

- ResearchTargetV1；
- 分析基线 <code>analysis_artifact_id</code>；
- 基线 content hash；
- 跟踪策略 market policy；
- 上次成功检查点。

只有 <code>analysis/README.md</code> 或无 sidecar HTML 不算有效分析基线。

### 13.2 需要修改的主要模块

- <code>apps/api/services/tracking_workflow.py</code>
- <code>data/wiki/tracking/scripts_multi_market/finsight_tracking_rules.py</code>
- <code>data/wiki/tracking/scripts_multi_market/run_all.py</code>
- <code>data/wiki/tracking/scripts_multi_market/module1_item_extractor.py</code>
- <code>data/wiki/tracking/scripts_multi_market/module2_sentiment_monitor.py</code>
- <code>data/wiki/tracking/scripts_multi_market/module3_metrics_tracker.py</code>
- <code>data/wiki/tracking/scripts_multi_market/module4_alert_trigger.py</code>
- <code>data/wiki/tracking/scripts_multi_market/module5_report_updater.py</code>
- <code>data/wiki/tracking/scripts_multi_market/module6_html_reporter.py</code>
- <code>data/wiki/tracking/scripts_multi_market/local_citations.py</code>
- <code>data/wiki/tracking/scripts_multi_market/validate_citations.py</code>
- <code>data/wiki/tracking/scripts_multi_market/daily_run.sh</code>
- <code>agents/hermes/profiles/siq_tracking_multi_market/SOUL.md</code>

### 13.3 改造要求

1. <code>TrackingWorkflowRequest</code> 以 ResearchTargetV1 和 analysis_artifact_id 为正式输入。
2. <code>run_all.py</code> 及模块接收已解析 package 或 target JSON，禁止从 ticker/name 重建路径。
3. 删除六位代码作为领域主键的假设，支持 AAPL、BRK.B、00005 等代码形式。
4. 公司和任务去重键使用 <code>market + company_id + source ResearchIdentity</code>。
5. 指标跟踪消费 NormalizedFactV1，保留原币和 scale。
6. 跨期变化只在单位、币种、会计口径和期间可比时计算。
7. 市场来源通过策略配置：

   - 中国内地由未改动的原 tracking 链继续处理，不进入本策略；
   - HK、US、EU、KR、JP 优先官方披露源和当前已批准的搜索能力；
   - 未接入可靠海外新闻或监管源时明确标记 unavailable/degraded；
   - 不用模拟舆情填充生产结果。

8. 搜索词使用公司名称、ticker、交易所、监管机构和市场语境，不复用中国监管词作为所有市场默认值。
9. 模块无产物、引用验证失败或来源不可用时不能汇总成 completed。
10. tracking 不得默认改写 analysis 基线；任何“更新分析报告”能力保持显式、受控并默认关闭。
11. 检查点和告警必须记录 ResearchIdentity、数据源、时间戳和 adapter 版本。

### 13.4 调度

如本期调整境外市场批量调度（CN 原调度保持不变）：

- 按各市场 catalog 枚举；
- 只调度 tracking_ready 公司；
- 不扫描目录名猜 ticker；
- 单个市场失败不污染其他市场状态；
- 失败重试保持同一 ResearchIdentity；
- 新源报告出现后创建新基线关系，不把旧报告检查点直接套用。

## 14. 法务合规保护

本期法务只做反向回归，不做功能开发：

- 不修改 <code>agents/hermes/profiles/siq_legal/**</code>。
- 不修改 legal workflow。
- 不修改法律法规数据。
- 不生成境外 legal 目录产物。
- 法务页面继续使用 CN 公司列表。
- <code>market=US/HK/JP/KR/EU</code> 的手工 URL 参数不得改变法务公司范围。
- Research Universe 的 legal capability 即使被直接调用也只返回 CN。

任何实现导致法务页面出现境外公司，都视为阻断发布的回归。

## 15. 详细实施任务

以下任务按顺序执行。每个任务完成后先运行该任务的聚焦测试，再进入下一任务。

### T0：工作区保护与基线

**目标**

确认当前未提交改动并建立只读事实面测试基线。

**步骤**

- [ ] 运行 <code>git status --short</code>。
- [ ] 对本计划列出的待修改文件逐一运行局部 <code>git diff -- &lt;file&gt;</code>。
- [ ] 保留现有用户改动；禁止整文件覆盖、回退或批量格式化。
- [ ] 建立合成的六市场最小测试 fixture，不复制完整真实报告或敏感运行数据。
- [ ] fixture 至少表达 PDF、SEC 10-K、SEC 10-Q、warning、fail、多报告、非自然年度和非 CNY。
- [ ] 为 synthetic company dir 建立事实面哈希工具，覆盖 reports、metrics、evidence、semantic、graph。

**完成标准**

- 现有 dirty worktree 被记录。
- fixture 可重复创建。
- 后续工作流测试能证明事实输入运行前后哈希不变。

### T1：共享契约

**目标**

实现 ResearchTargetV1、NormalizedFactV1、EvidenceRefV1、AgentArtifactV2 的唯一契约。

**建议文件**

- <code>packages/market-contracts/src/siq_market_contracts/research_target.py</code>
- <code>packages/market-contracts/src/siq_market_contracts/normalized_fact.py</code>
- <code>packages/market-contracts/src/siq_market_contracts/agent_artifact.py</code>
- <code>packages/market-contracts/tests/</code> 下对应测试

**步骤**

- [ ] 定义版本化 schema 和严格字段验证。
- [ ] 实现 ResearchIdentity 完整性和一致性校验。
- [ ] 实现 source family、currency、scale、period 和 locator 枚举。
- [ ] 实现 JSON 序列化和向后兼容读取。
- [ ] 对历史 CN 无 sidecar 产物返回 <code>legacy_unbound</code>，不伪造身份。
- [ ] 将 <code>specialist_artifact_contract.py</code> 的 locator gate 扩展到 HTML/XBRL。

**完成标准**

- 三个工作流引用同一契约。
- 非 CN 不完整身份被拒绝。
- PDF、SEC HTML、XBRL 引用均可通过各自正确的 locator 校验。

### T2：Report Package Resolver 与 Research Universe API

**目标**

提供安全的市场、公司、源报告、能力和生成结果枚举。

**步骤**

- [ ] 复用 <code>agent_runtime_catalog.py</code> 的市场 catalog 加载。
- [ ] 复用 <code>agent_runtime_context.py</code> 的 ResearchIdentity 校验。
- [ ] 抽取或复用 <code>agent_runtime_wiki_context.py</code> 的 manifest-aware 路径解析。
- [ ] 实现 server-issued company_key，不把目录路径暴露为主键。
- [ ] 实现报告级 readiness。
- [ ] 实现 markets、companies、reports、artifacts、content、delete API。
- [ ] 在 <code>apps/api/main.py</code> 注册新 router。
- [ ] 保留旧 <code>/api/wiki/companies/**</code> CN 路由，不改变其外部行为。
- [ ] 增加路径穿越、绝对路径、符号链接、跨市场 key 和权限测试。

**完成标准**

- 六个市场能按顺序返回。
- US 报告通过 manifest 找到 parser、sections、metrics、qa 和 XBRL 产物。
- 浏览器永远拿不到可用于任意文件读取的路径。
- delete 无法删除事实面。

### T3：前端级联选择

**目标**

前三个页面实现市场 -> 公司 -> 源报告 -> 生成结果。

**步骤**

- [ ] 新建 research-universe feature API、types 和 selection model。
- [ ] 为 ReportViewer 增加默认 cn-only 的 marketScope。
- [ ] 在 ReportSelector 中将市场放在公司前。
- [ ] 分开 SourceReportOption 和 GeneratedArtifactOption。
- [ ] 实现级联加载、取消旧请求和原子重置。
- [ ] 更新 Agent 上下文，以当前源报告为 ResearchIdentity 权威来源。
- [ ] 更新分享 URL 和旧 CN URL 兼容。
- [ ] 下载和删除改用 artifact_id。
- [ ] 对 loading、empty、warning、degraded、error 状态补测试。
- [ ] 验证响应式布局无重叠和文字溢出。

**完成标准**

- 没有历史生成结果的海外公司仍可启动分析。
- 快速切换市场不会出现旧公司或旧聊天上下文。
- 法务页面行为不变。

### T4：共享分析输入与工作流请求

**目标**

让正式分析工作流只消费服务端解析的 ResearchTargetV1。

**步骤**

- [ ] 修改 AnalysisReportWorkflowRequest。
- [ ] 从页面上下文解析 company_key + report_id，并在服务端重新解析。
- [ ] 保留 CN 无页面上下文旧入口，隔离为 compatibility branch。
- [ ] 将 absolute company/report paths 封装在服务端 ResolvedReportPackage。
- [ ] 生成只读 AnalysisInputBundle。
- [ ] subprocess 只接收 bundle 路径或序列化 target，不接收客户端路径。
- [ ] 所有输出 sidecar 带完整身份和 adapter 版本。
- [ ] 删除多市场正式链对 DEFAULT_YEAR 的依赖。

**完成标准**

- 同名、同 ticker 或跨市场代码碰撞不会选错公司。
- 所选报告身份与工作流实际读取文件完全一致。

### T5：PDF 市场分析适配

**目标**

HK、JP、KR、EU 的可用 PDF 报告进入独立跨市场 research-pack 编排；CN 继续原 A 股编排。

**步骤**

- [ ] 实现 pdf_market adapter 的 manifest-first 读取。
- [ ] 清理根 catalog 和 A 股目录拼接。
- [ ] 清理固定 year/report_id。
- [ ] 清理人民币、亿元、六位代码和 A 股页脚。
- [ ] 将同业、行情、行业和定性证据设为 capability，缺失时降级。
- [ ] 将 PDF page/table/source map 转换为 EvidenceRefV1。
- [ ] 更新 renderer 和 quality validator 支持动态币种、单位、期间。
- [ ] 更新相关 Prompt/SOUL，只保留市场中性共享规则和必要市场 policy。

**完成标准**

- CN 现有黄金报告无回归。
- HK/JP/KR/EU 各一个 parsed-ready 包可生成报告。
- 非 CNY 报告不会显示“亿元”。

### T6：美国 SEC 分析适配

**目标**

直接消费美股既有 SEC HTML/iXBRL 产物并输出共享分析结果。

**步骤**

- [ ] 实现 sec_ixbrl adapter。
- [ ] 按 manifest 读取 document_full、report_complete、financial_data、normalized_metrics、financial_checks 和 source_map。
- [ ] 读取 sections、tables 和 XBRL facts/context/unit/label。
- [ ] 实现 10-K、10-Q、非自然年度和 US GAAP 期间解析。
- [ ] 实现 GAAP/non-GAAP 区分和 XBRL context 消歧。
- [ ] 实现 MD&A、Risk Factors、Business、Notes、Controls、segment 的证据映射。
- [ ] 将 SEC section/anchor/XBRL fact 写入 EvidenceRefV1。
- [ ] 复用共享任务状态、renderer 外壳和 AgentArtifactV2。
- [ ] warning 输入在报告和 sidecar 中保留。

**完成标准**

- 至少一个 10-K 和一个 10-Q fixture 通过。
- 不读取不存在的 A 股式文件。
- 不伪造 PDF 页码。
- 输出 USD、财年和 SEC 引用正确。

### T7：事实核查多市场改造

**目标**

核查选中的确切分析产物和源报告证据。

**步骤**

- [ ] 修改 workflow request 和 CLI 参数。
- [ ] 注入 ResolvedReportPackage，删除全局 A 股 catalog 寻址。
- [ ] 使用 NormalizedFactV1 和 EvidenceRefV1。
- [ ] 将风险完整性拆为共享规则和 market policy。
- [ ] 支持 PDF 与 SEC 引用核查。
- [ ] PostgreSQL 增强按市场和完整身份路由；不匹配时跳过。
- [ ] 输出 AgentArtifactV2 并绑定 analysis artifact。
- [ ] 增加身份不匹配、单位错误、引用缺失和 warning 测试。

**完成标准**

- 核查不会自动选择其他报告或其他 analysis HTML。
- SEC XBRL 引用可回溯。
- 缺证据时明确 unsupported/degraded，不伪造结论。

### T8：持续跟踪多市场改造

**目标**

围绕明确分析基线执行市场感知的指标、事项、舆情和预警跟踪。

**步骤**

- [ ] 修改 workflow、run_all 和各模块输入。
- [ ] 删除六位代码和 A 股目录重建。
- [ ] 使用 NormalizedFactV1 做原币跨期跟踪。
- [ ] 按市场注入官方披露和搜索策略。
- [ ] 缺海外来源时返回 degraded/unavailable。
- [ ] 以完整身份保存检查点和去重键。
- [ ] 修复无产物仍标记成功的问题。
- [ ] 输出 AgentArtifactV2 并绑定分析基线。
- [ ] 禁止默认改写 analysis。

**完成标准**

- 非六位 ticker 可运行。
- 代码跨市场碰撞不会串公司。
- 缺来源、缺指标或引用验证失败不会被标记为完整成功。

### T9：兼容、审计和功能开关

**目标**

可灰度启用、可观察、可回滚，且不破坏 CN 旧链路。

**建议开关**

- <code>SIQ_MULTI_MARKET_RESEARCH_ENABLED</code>
- <code>SIQ_US_SEC_ANALYSIS_ENABLED</code>

**步骤**

- [ ] 开发阶段默认关闭新全市场入口。
- [ ] 开关关闭时前三个页面保持当前 CN 行为。
- [ ] US adapter 可独立关闭且返回明确 capability。
- [ ] 日志记录 request_id、agent_type、market、company_key 摘要、ResearchIdentity、source_family、adapter_version、artifact_id 和状态。
- [ ] 不记录报告正文、完整 Prompt 或敏感本地路径。
- [ ] 增加各市场 readiness、成功、degraded、failed、identity mismatch 和 citation failure 计数。
- [ ] 完成旧 CN URL、旧 API 和旧 HTML 展示回归。

**完成标准**

- 一个开关可以回退到原 CN 页面和工作流。
- 回滚不删除任何新旧产物。
- 错误可按 ResearchIdentity 和 adapter 定位。

### T10：全量验收与发布

**目标**

在六市场黄金样本、权限、安全和前端流程全部通过后启用。

**步骤**

- [ ] 运行第 16 节全部聚焦测试。
- [ ] 运行 API、market-contracts、三个 profile 和前端完整测试。
- [ ] 运行事实面哈希不变测试。
- [ ] 执行六市场人工 smoke。
- [ ] 保存每个样本的输入身份、adapter、结果 sidecar 和关键截图。
- [ ] 确认法务 CN-only。
- [ ] 确认旧 CN 分享链接和历史报告。
- [ ] 通过发布门禁后再打开全市场开关。

## 16. 测试矩阵

### 16.1 单元测试

#### 契约

- ResearchIdentity 完整和缺失。
- market 标准化。
- source_family 路由。
- currency、scale、period。
- PDF、HTML、XBRL locator。
- AgentArtifactV2 sidecar 和 legacy_unbound。

#### Resolver/API

- 六市场 catalog。
- 一个公司多个报告。
- report/manifest identity 冲突。
- warning/fail。
- 缺全文、缺 metrics、缺 source map。
- 绝对路径、<code>..</code>、符号链接、跨市场 key。
- 查看和删除权限。
- 缓存隔离。

#### 前端

- 级联选择。
- 请求竞态取消。
- 原子重置。
- URL 恢复。
- 空结果可生成。
- 法务 CN-only。

#### 工作流

- CN 旧入口。
- 六市场结构化页面入口。
- PDF 和 SEC adapter。
- 单位与币种。
- exact identity。
- sidecar 原子发布。
- 事实面哈希不变。

### 16.2 黄金样本

至少覆盖：

| 样本 | 必须验证 |
| --- | --- |
| CN PDF 年报 | 现有 A 股质量无回归 |
| HK PDF 年报 | HKD、五位代码、PDF 引用 |
| JP PDF 报告 | JPY、非六位/市场身份、会计口径 |
| KR PDF 报告 | KRW、代码碰撞、市场来源降级 |
| EU PDF 报告 | EUR/GBP、IFRS、交易所和国家 |
| US 10-K | USD、US GAAP、非自然年度、SEC section、XBRL |
| US 10-Q | 季度/累计期间、XBRL context |
| warning 报告 | 可选、警告透传 |
| fail 报告 | 默认隐藏 |
| 银行或保险 | 不套用一般工业公司指标 |

测试中特别加入：

- AAPL。
- BRK.B 等含标点 ticker。
- HK 00005 等非 A 股代码。
- CN 与 KR 等可能出现相同裸代码的公司。
- USD/HKD/JPY/KRW/EUR 的 million/billion scale。
- 仅有 <code>analysis/README.md</code> 的公司。
- 分析 sidecar 与源报告 parse_run_id 不一致。

### 16.3 建议命令

~~~bash
cd /home/maoyd/siq-research-engine/packages/market-contracts
uv run pytest

cd /home/maoyd/siq-research-engine/apps/api
uv run python -m pytest \
  tests/test_research_universe.py \
  tests/test_research_report_package.py \
  tests/test_wiki_report_lists.py \
  tests/test_analysis_report_workflow.py \
  tests/test_factcheck_workflow.py \
  tests/test_tracking_workflow.py

cd /home/maoyd/siq-research-engine
pytest agents/hermes/profiles/siq_analysis/tests
pytest agents/hermes/profiles/siq_analysis_multi_market/tests
pytest agents/hermes/profiles/siq_tracking/tests

cd /home/maoyd/siq-research-engine/apps/web
npm run test:unit
npm run check:frontend
npm run e2e:default -- tests/secondary-market-multi-market-agents.spec.ts
~~~

最终候选版本再运行：

~~~bash
cd /home/maoyd/siq-research-engine
scripts/check_all.sh
~~~

若因本地缺少外部服务不能运行某项测试，开发交付必须列出未运行项、原因和剩余风险，不得把跳过视为通过。

## 17. 发布验收标准

以下条件必须全部满足：

### 17.1 UI

- [ ] 三个页面的市场选择框位于公司选择框前。
- [ ] 市场名称和顺序与现有 marketMetadata 完全一致。
- [ ] 选择市场后只显示该市场 parsed-ready 公司。
- [ ] 无 URL 参数时，智能分析、事实核查、持续跟踪和法务合规首屏均展示上汽集团；显式分享参数仍可恢复其他市场和公司。
- [ ] 源报告与生成结果版本严格分开。
- [ ] 没有生成结果的海外公司仍可启动分析。
- [ ] 市场、公司和源报告切换没有陈旧上下文。
- [ ] 分享链接可恢复完整选择。
- [ ] 下载、删除和预览工作正常。
- [ ] 移动端和桌面端无控件重叠。
- [ ] 法务页面仍只有 CN。

### 17.2 智能分析

- [ ] HK/JP/KR/EU 可用 PDF 包进入独立 pdf_market 链；CN 不进入。
- [ ] US 进入 sec_ixbrl 链。
- [ ] 实际读取路径来自 manifest/resolver。
- [ ] 美股不依赖 A 股式文件。
- [ ] 币种、单位、财年和会计准则正确。
- [ ] 核心结论有可回溯证据。
- [ ] 输出 sidecar 有完整 ResearchIdentity。

### 17.3 事实核查

- [ ] 只核查用户选中的 analysis artifact。
- [ ] analysis、source report、evidence 身份一致。
- [ ] PDF 和 SEC 引用均可验证。
- [ ] 单位、算术、期间和风险规则市场感知。
- [ ] 不完整证据明确降级。

### 17.4 持续跟踪

- [ ] 只使用明确分析基线。
- [ ] 支持非六位 ticker。
- [ ] 指标保留原币和 scale。
- [ ] 市场来源不可用时明确降级。
- [ ] 失败模块不会被汇总成成功。
- [ ] 不默认改写分析产物。

### 17.5 安全与兼容

- [ ] 非 CN 不完整身份失败关闭。
- [ ] 不接受客户端文件路径。
- [ ] 路径穿越、符号链接逃逸和跨市场 key 被拒绝。
- [ ] 源 Wiki 事实面哈希不变。
- [ ] 旧 CN API、URL、报告和工作流无回归。
- [ ] 关闭功能开关可立即恢复原 CN 行为。
- [ ] 法务 profile、法律库和法务输出未改动。

## 18. 风险与处理

| 风险 | 处理 |
| --- | --- |
| 只改 UI，工作流仍读 A 股 | 正式请求必须使用 ResearchTargetV1；非 CN 禁止 name/year fallback |
| 把生成 HTML 当源报告 | 分开 SourceReportOption 和 GeneratedArtifactOption |
| 美股被要求补 A 股文件 | SEC adapter 直接读取 manifest 现有产物 |
| 同 ticker 或代码跨市场冲突 | company_key + market + canonical company_id；服务端复核 |
| warning 被误认为完整 pass | readiness、UI、sidecar 全链路透传 |
| 非 CNY 被显示成亿元 | NormalizedFact 保留 currency/scale；renderer 动态单位 |
| SEC 引用伪装成 PDF 页码 | EvidenceRef 支持 section/anchor/XBRL fact |
| 事实核查选错分析报告 | 显式 analysis_artifact_id + identity/hash 比对 |
| tracking 只有 README 仍通过 | sidecar、status、identity 和 content hash 联合预检 |
| 海外来源尚不完整 | capability 显式 degraded/unavailable，不使用模拟生产结果 |
| 共享组件误扩法务 | marketScope 默认 cn-only + 法务反向回归 |
| 当前 dirty worktree 被覆盖 | 每个任务先读局部 diff，只做小范围 patch |

## 19. Codex 执行约束

交给 Codex 开发时，必须附带以下指令：

1. 先阅读本任务书、根 <code>AGENTS.md</code> 和相关目录内的 <code>AGENTS.md</code>。
2. 开始每个任务前检查 <code>git status</code> 和目标文件局部 diff。
3. 当前工作树已有未提交修改，禁止回退、覆盖或重写用户改动。
4. 一次只完成一个 T 任务及其测试；依赖未完成时不要并行修改下游契约。
5. 优先复用现有 runtime catalog、context 和 wiki resolver。
6. 不修改解析器、入库链和现有 Wiki 源产物。
7. 不将美股物理目录改造成 A 股目录。
8. 不扩展境外法务。
9. 每次提交只包含本需求直接相关文件，避免格式化噪声。
10. 每个阶段交付时列出：

    - 修改文件；
    - 契约变化；
    - 测试命令和结果；
    - 未运行测试；
    - 已知 degraded capability；
    - 源事实目录哈希验证结果。

## 20. 最终交付物

完成本任务后应交付：

- 版本化共享契约及测试。
- Research Universe API、路径安全和权限测试。
- 三页面全市场级联选择及前端测试。
- PDF 市场分析适配器。
- 美国 SEC HTML/XBRL 分析适配器。
- 多市场事实核查。
- 多市场持续跟踪。
- 统一派生产物 sidecar 和读取/删除 API。
- CN 兼容、法务 CN-only 和安全回归。
- 六市场黄金样本验证记录。
- 功能开关、观测指标和回滚说明。

不应交付：

- 任何被重建或批量改写的市场 Wiki 源数据；
- 美股 A 股式兼容文件副本；
- 境外法务功能；
- 无关页面、主题、解析服务或部署改动。
