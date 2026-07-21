# 从证据链到安全智能体：SIQ Research Engine 的 DGX Spark 工程实践

> 本文是 NVIDIA DGX Spark Hackathon 项目 SIQ Research Engine 的技术分享。
> 项目开源地址：<https://github.com/maoyadongsh/siq-research-engine>
> 文中运行状态和测试数据采样于 2026 年 7 月 20 日至 21 日。动态状态以发布时的仓库与实际环境为准。

<!-- CSDN 发布前编辑提示：请把文中的 GitHub Raw 图片转存至 CSDN 图床，并在第四节补充解析任务、财务 trace、factchecker 和原文回跳截图。 -->

**摘要：**

大语言模型已经很擅长生成一篇“像研报”的文章，但在真实投研中，语言流畅远远不够。研究员还需要知道：数字来自哪一份官方披露、哪一个报告期、哪一张表、哪一页；同比和占比由谁计算；模型是否串了公司、市场或币种；智能体调用了哪些工具；敏感材料是否离开本地；最终结论能否复核、回放和审计。

SIQ Research Engine 正是围绕这些问题构建的一套可审计智能研究生产线。它连接 CN、HK、US、EU、JP、KR 六个市场的官方披露入口，将 PDF、HTML、iXBRL、XBRL、Office、图片和会议音频转换为带身份、质量状态和来源坐标的证据对象；再通过 LLM-Wiki、PostgreSQL、Milvus、Hermes 岗位智能体、确定性财务计算器和 NVIDIA OpenShell 安全运行面，把“材料进入”一直连接到“研究结论、投委会决策和原文回跳”。

项目运行在 NVIDIA DGX Spark 上：本地 Nemotron 负责私有多模态推理，MinerU 负责文档视觉解析，Qwen3-VL Embedding/Reranker 负责独立语义检索，FunASR 负责语音与会议，StepFun Step-3.7 Flash 提供云端复杂推理；FastAPI、React、PostgreSQL、Milvus、MinIO、Redis、Hermes 和 OpenShell 则共同构成一套可以复核、回放和扩展的研究基础设施。

**先说明当前边界：** 截至采样日，`siq_analysis` 的 OpenShell 目标链路已经在本机真实前端跑通，但正式 production quality gate 仍是 `NO_GO`；公开主干 CI 的主要后端、市场、构建和会议任务已通过，默认 Web E2E、Meeting additive baseline 与部分 Trivy 策略仍有红灯。本文会同时展示已经验证的能力和尚未完成的发布证据，不把“功能跑通”等同于“正式生产完成”。

**关键词：** DGX Spark、智能投研、多智能体、LLM-Wiki、财务勾稽、证据链、Hermes、NVIDIA OpenShell、Milvus、Nemotron

---

## 目录

1. 前言：投研 AI 最大的问题不是不会回答，而是无法证明
2. 项目概述：SIQ 是什么，又不是什么
3. 产品全景：三块产品，一个事实与审计底座
4. 一条完整主线：从官方年报到可回跳的研究结论
5. 创新一：六市场官方披露与 ResearchIdentity
6. 创新二：LLM-Wiki，不以向量相似度决定权威事实
7. 创新三：从解析质量到最终回答的三重质量面
8. 关键工程设计一：岗位智能体与 R0-R4 投委会 Hybrid DAG
9. 关键工程设计二：记忆负责连续性，证据负责事实
10. 创新四：OpenShell + Hermes 安全智能体运行面
11. DGX Spark：让多模型、多数据服务与安全控制面在单机协同
12. 工程完整性与可复核证据
13. 商业价值、竞争差异与可复制壁垒
14. 当前边界与下一阶段路线
15. 结语：让事实、推理、权限和错误都变得可见

---

## 一、前言：投研 AI 最大的问题不是不会回答，而是无法证明

先从一个看起来很简单的问题开始：

> “这家公司的资产负债率同比变化是多少？”

普通财报问答系统可能几秒钟就能给出一个数字和一段解释。但专业研究者真正关心的，往往是后面的十个问题：

1. 用的是哪一家公司的哪一份报告？
2. 是合并口径，还是母公司口径？
3. 本期与上期是否来自同一个财务口径？
4. 原始单位是元、千元、百万元，还是亿元？
5. 资产和负债是否来自同一报告期？
6. 同比是模型心算，还是确定性程序重新计算？
7. 公式中的每个输入能否绑定到证据 ID？
8. 能否打开原始 PDF 的对应页和表格？
9. 如果证据缺失，系统会不会仍然编出一个“精确数字”？
10. 这次回答是在 Host 上运行，还是在受控沙箱中运行？

这也是金融 AI 从 Demo 走向生产必须跨越的分界线：**生成文本只是最后一步，前面还需要来源、身份、解析、口径、计算、权限、审计和失败处理。**

传统“上传 PDF -> 切片 -> embedding -> top-k -> 大模型回答”的路径，对一般知识问答很有效，但财报和尽调材料有几个天然难题：

- 跨页表格可能被切断；
- 主表与附注相距数百页；
- 同一个指标可能同时存在合并口径、母公司口径和分部口径；
- 不同报告期、币种和金额单位很容易在相似文本中混用；
- 向量命中只能说明文字相似，不能证明数字就是权威事实；
- 一个引用链接存在，不代表它真的支持前面的结论；
- Agent 获得文件、终端和网络能力后，安全风险会急剧上升。

因此，SIQ 从一开始就没有把目标定义成“更会写研报的模型”，而是定义成：

> **从可信材料到结构化证据，再到受控智能体结论的可审计研究生产线。**

在 SIQ 中，证据先于回答，质量门禁先于入库，审计链先于流畅表达。高精度也不意味着任何问题都必须给出一个精确数字，而是系统必须说明：数字来自哪里、如何计算、是否通过校验；当无法证明时，可靠地返回 warning、degraded、N/A 或 evidence gap。

### 从 Sovereign-IQ 到 SIQ Research Engine

在上一篇 [《Sovereign-IQ 主权智感多智能体投研决策系统》](https://blog.csdn.net/outmanmao/article/details/159980602) 中，我们重点展示了多岗位投委会、分阶段决策和本地模型协同。这次 SIQ Research Engine 不是简单更名，而是把上一版的方法论继续向工程底座推进：研究对象有了稳定身份，材料有了 evidence contract，数字有了确定性计算和 trace，智能体有了数据 scope 与安全运行面，失败也有了可见的质量状态。

因此，本文不会把上一篇的模型、案例数量或性能数字直接迁移过来。所有状态、配置、样板和测试结果都以当前 `siq-research-engine` 仓库及 2026 年 7 月采样为准。

---

## 二、项目概述：SIQ 是什么，又不是什么

SIQ Research Engine 面向证券研究、资产管理、一级市场尽调、投委会决策、合规审查和研究运营等场景。

它不是以下几类产品的简单组合：

- 不是只支持单文件问答的 PDF Chat；
- 不是把所有材料切成匿名 chunk 的普通 RAG；
- 不是给同一个大模型换多个角色提示词的“人格群聊”；
- 不是只提供若干金融指标的数据终端；
- 不是让 Agent 获得宿主机权限后自由执行的通用自动化平台；
- 也不是用一个超大模型包办解析、检索、计算、语音和决策。

SIQ 更接近一套面向机构级场景演进的“研究操作系统”：它把材料生产、事实治理、智能体协作、计算核验、安全执行和审计回放组织成稳定合同。

项目的核心对象不是一段模型回答，而是一组可以被不同服务反复消费的结构化资产，例如：

- `document_full.json`：文档级统一事实合同；
- `source_map.json`：页面、表格、单元格、bbox、anchor 和来源映射；
- `quality_report.json`：解析质量、覆盖率和风险状态；
- `financial_data.json`：带期间、单位、币种和来源的财务事实；
- `financial_checks.json`：资产负债、利润、现金和附注勾稽结果；
- `normalized_metrics.json`：跨市场统一口径指标；
- evidence package：可入库、可检索、可回放、可离线交付的市场证据单元；
- agent memory：带用户、项目、角色、来源、权限和生命周期的长期记忆项；
- meeting transcript/event：可回到时间轴和音频片段的会议证据。

为了方便后文阅读，先统一四个项目内术语：

| 术语 | 含义 |
| --- | --- |
| ResearchIdentity | `market/company_id/filing_id/parse_run_id`，一次研究所使用事实的身份边界 |
| evidence package | 将材料、事实、质量和来源坐标封装在一起的可复核交付单元 |
| LLM-Wiki | 面向 Agent 的逻辑知识对象与读取路径，不是向量库的别名 |
| runtime provenance | 本次回答由哪个 profile、模型、运行面和 sandbox generation 完成，是否发生 fallback |

这些不是一次性导出文件，而是 Web、API、解析器、市场规则、PostgreSQL importer、Milvus 和 Hermes 之间的协作边界。底层模型可以升级，向量索引可以重建，安全运行面可以灰度回退，但事实身份、原始证据和审计链不能随着模型版本漂移。

---

## 三、产品全景：三块产品，一个事实与审计底座

SIQ 当前形成了三个彼此独立、又共享基础设施的产品域。

| 产品域 | 主要用户 | 典型问题 | 系统交付 |
| --- | --- | --- | --- |
| 二级市场投研分析智能体集群 | 研究员、基金经理、投研数据和合规团队 | 多市场披露难找、财报难解析、模型答案难追溯 | 官方披露检索、财报解析、分析、事实核查、持续跟踪、法律合规与证据回跳 |
| 一级市场投研决策智能体集群 | 投资经理、行业/财务/法务/风控专家、投委会主席 | 尽调材料分散、专家观点容易相互污染、投委会过程难审计 | Deal OS、材料中心、双库检索、R0-R4 工作流、争议裁决、红蓝对抗与决策归档 |
| 应用中心 | 研究运营、数据工程、会议协作和知识库管理员 | 文档、会议和知识沉淀成本高 | 通用文档解析、财报 PDF 解析、会议转写、向量入库和知识库治理 |

三块产品共享同一套事实层、权限模型、质量门禁和审计语言：

这里的“共享”是共享基础设施和证据合同，不是共享证据范围。二级市场披露只服务相应研究链，一级市场尽调材料只服务当前 Deal，会议陈述按所属业务归档；跨业务引用必须经过显式授权、scope 校验和重新绑定。

```text
官方披露 / 尽调材料 / 本地文档 / 图片 / 会议音频 / URL
  -> 文档与市场应用层
       pdf-parser / document-parser / market finder / market rules / meeting / vector ingest
  -> 证据与事实层
       LLM-Wiki / PostgreSQL / Milvus / MinIO / artifacts
  -> API 控制面
       鉴权 / 任务 / SSE / 取消 / 恢复 / source token / memory / runtime selection
  -> Agent runtime 分支
       OpenShell Gateway / Sandbox -> BYOC Hermes
       或 Host Hermes fallback
  -> Hermes 岗位智能体 profiles
       二级市场 analysis/factchecker/tracking/legal/assistant
       一级市场 coordinator/chairman/strategy/sector/finance/legal/risk
  -> Web 工作台
       二级市场 / Deal OS / 文档 / 会议 / 向量入库 / 系统管理
```

![SIQ Research Engine 系统架构图](https://raw.githubusercontent.com/maoyadongsh/siq-research-engine/master/artifacts/architecture-drafts/architecture-v6-4k.png)

**图 1：SIQ Research Engine 系统架构。** 这张图的重点不是服务数量，而是模型层、事实层、智能体层和执行安全层被明确解耦。

---

## 四、一条完整主线：从官方年报到可回跳的研究结论

为了避免把文章写成菜单列表，我们用当前 A 股全链路主样板上汽集团 `600104` 说明一次研究任务如何运行。

README 中对这条样板的定义是：覆盖 PDF 解析、三表指标、证据、事实图谱、分析、核查、跟踪、法律意见和 OpenShell 灰度验证。这里不展示某个未经复核的投资结论，而展示结论是怎样被生产和约束的。

公开仓库中的脱敏 CI fixture 冻结了一份可复核的样板身份：`2025-annual` 年报，报告期末为 2025 年 12 月 31 日，披露日为 2026 年 4 月 1 日，解析任务 ID 为 `7dbc35a7-7626-4e81-810e-5dbb764434e0`，artifact bundle SHA-256 为 `117a3057dd5ce70414a36ac860aad70c8ef5e402c46ff3719b1257c07fa35649`。fixture 不包含原始 PDF，却保留了测试所需的事实、页码、表格和身份映射，因此读者可以复跑合同与计算，而不会把本地完整数据上传到公开仓库。

### 第一步：找到正确的官方材料

用户先选择市场、公司和报告。系统不会只依赖公司名称模糊匹配，而是冻结市场、公司、披露和解析运行身份。

```text
ResearchIdentity = market / company_id / filing_id / parse_run_id
```

这四个字段贯穿解析、入库、检索、计算和回答审计。它们用于防止最危险、又最难被肉眼发现的错误：串市场、串公司、串报告期和误用旧解析结果。

### 第二步：恢复文档，而不是只提取纯文本

PDF 进入解析服务后，MinerU/VLM、PDF 规则和市场适配器共同恢复：

- 页面与 reading order；
- 标题、段落和列表；
- 表格结构、行列和跨页关系；
- 图片、公式与 bbox；
- Markdown 行号；
- 页码、表格索引和原文件 hash。

系统随后生成 `document_full`、`content_list_enhanced`、tables、figures、source map 和 quality report。原始值不会被归一化值覆盖，方便后续追责和重建。

### 第三步：先过质量门，再进入事实层

解析完成不等于可以入库。SIQ 会检查文档完整性、三表覆盖、来源定位、财务关系和 evidence resolvability。

- hard block 即使设置 `force=true` 也不能执行正式 import 或 vector ingest，只能留在 review/quarantine；
- soft warning 要求人工复核；
- soft gate 只有携带 reason、operator、ticket、expiry 或 one-shot 等审计字段时才能请求例外，不能静默绕过。

这一步非常重要。错误解析一旦进入长期数据库或向量库，后续每一次回答都可能稳定地复现同一个错误。

### 第四步：构建 LLM-Wiki 权威知识对象

通过质量门后，系统把公司、报告、主题、事实、关系、指标、附注和证据组织成可寻址对象，并为不同问题建立逻辑路由。对“经营变化”“资产负债率同比”“商誉减值构成”这三类问题，系统走的不是同一条 top-k 路径：

- 经营变化进入主题 segment、claims 和受控叙述候选；
- 资产负债率进入带期间、单位和证据 ID 的指标事实，再调用确定性计算器；
- 商誉减值先定位主表事实，再沿 `document_links` / `note_links` 跳转到附注原值、准备和净额。

### 第五步：岗位智能体形成分析，而不是直接替代事实

`siq_analysis` 根据 ResearchIdentity 和问题类型读取授权证据，形成经营分析、风险链条和研究判断。它可以使用长期记忆理解用户偏好，也可以调用补充语义检索，但不能用记忆或向量相似度覆盖权威财务事实。

### 第六步：确定性程序重算派生数字

凡是同比、占比、CAGR、人均、单位换算、外汇换算或原值-准备-净额勾稽，模型都不能只在正文中声称“已经计算”。SIQ 使用 `Decimal` 计算器或同源后端函数重算，并保存：

- 公式；
- 每一个输入值；
- 输入 evidence ID；
- 期间、单位与币种；
- ResearchIdentity；
- 计算状态和异常原因。

在上面的公开样板中，商誉就是一个实际例子。资产负债表合并口径的“商誉”位于 PDF 第 65 页、表格索引 84，原始值为 `1,183,122,320.47` 元；附注中的“商誉账面原值”和“商誉减值准备”都位于 PDF 第 137 页，分别来自表格索引 165 和 166。确定性校验器实际执行：

```text
1,282,085,915.36 元 - 98,963,594.89 元
= 1,183,122,320.47 元

显示口径：12.82 亿元 - 0.99 亿元 = 11.83 亿元
主表净额：11.83 亿元
校验差异：0 元
```

这个例子体现了 SIQ 所说的“证据链”到底是什么：不是在回答末尾附一条年报链接，而是让主表净额、附注原值、减值准备、公式、单位、报告身份和来源坐标共同进入计算回执。任何一项跨公司、跨报告或缺失，guardrail 都应阻止系统把结果包装成已核验数字。

克隆仓库并安装 API 依赖后，可以直接对公开 fixture 复跑这一计算：

```bash
SIQ_WIKI_ROOT="$PWD/datasets/fixtures/api_ci/wiki" \
uv run --project apps/api python \
  agents/hermes/profiles/shared/scripts/financial_reconciliation_validator.py \
  goodwill --company 600104 --report-id 2025-annual --format json
```

### 第七步：factchecker 反向核查

分析报告随后交给事实核查角色。核查对象不是写作风格，而是：

- 数字是否被证据支持；
- 引用是否真的支撑前文 claim；
- 计算 trace 是否完整；
- 报告是否遗漏了关键风险；
- 是否发生跨公司、跨期或口径漂移；
- 是否出现“伪造 calculator marker”之类绕过后端验证的行为。

另一个模型“同意”原结论，不算核查通过。核查仍必须回到证据和确定性工具。

### 第八步：用户回到原始材料

最终结果不仅显示引用编号，还可以回到 PDF 页、源表格、Markdown 行、bbox 或 XBRL fact。用户可以从结论下钻到数字，再从数字回到官方材料。

![SIQ 二级市场分析工作台](https://raw.githubusercontent.com/maoyadongsh/siq-research-engine/master/artifacts/secondary-market-multi-market/ui-analysis-desktop-1440.png)

**图 2：二级市场分析工作台桌面端。** 验证目标是把研究输出、公司/报告上下文和证据入口放在同一工作流中，而不是把引用藏在模型长文末尾。

### 第九步：记录运行来源

回答还会附带 runtime provenance：由哪个 profile、模型和运行面完成，是否发生 fallback，OpenShell scope 和 sandbox generation 是什么。对于需要审计的机构，这与“答案本身”同样重要。

这条主线可以概括为：

```text
官方披露
  -> 身份与文件 hash
  -> 文档结构与来源坐标
  -> 质量门
  -> LLM-Wiki / PostgreSQL / Milvus
  -> Hermes analysis
  -> 确定性财务计算
  -> factchecker
  -> answer audit
  -> PDF/表格回跳
  -> tracking / legal / 组织记忆
```

---

## 五、创新一：六市场官方披露与 ResearchIdentity

投研准确性的第一步不是模型，而是来源。

SIQ 优先连接官方披露渠道，并把市场差异保留在各自适配器中：

| 市场 | 官方入口/体系 | 主要材料形态 | SIQ 的处理重点 |
| --- | --- | --- | --- |
| CN | CNINFO 等官方披露入口 | PDF、公告 | 中文公司身份、财报表格、页码与附注关系 |
| HK | HKEXnews | PDF package | 中英文标题、上市公司身份、跨页表格与 package 合同 |
| US | SEC EDGAR | HTML、iXBRL、XBRL | ticker/CIK/accession、XBRL context/unit、HTML anchor 与 statement normalization |
| EU | ESEF | XHTML、XBRL、ZIP | taxonomy、context、单位、ESEF package 与跨发行人身份 |
| JP | EDINET | XBRL、ZIP、文档包 | EDINET 身份、日文披露、XBRL facts 与报告目录 |
| KR | DART | XML/XBRL、报告包 | DART 公司代码、报告身份、结构化披露与统一 evidence contract |

`market-report-finder` 负责实体解析、官方查询、下载边界和限速策略；`market-report-rules` 把不同市场的解析结果统一成 `financial_data`、`financial_checks` 和 `load_plan`；`packages/market-contracts` 再把 stable ID、source map、value polarity、summary/detail reader 和 package validation 固化为跨服务合同。

为什么要如此强调身份？因为一个数字在投研中从来不只是 `1000`。它至少还包含：

```text
公司 + 市场 + 报告 + 解析运行
+ 指标 + 期间 + 币种 + 单位 + 口径
+ 来源文件 + 页码/表格/XBRL context + evidence_id
```

缺少这些信息的数字，即使表面正确，也很难被可靠复用。

---

## 六、创新二：LLM-Wiki，不以向量相似度决定权威事实

LLM-Wiki 是 SIQ 最容易被误解、也最有辨识度的设计之一。

它不是“在 Wiki 里再做一次向量检索”，也不是传统 RAG 的换名。LLM-Wiki 本身不调用 embedding、reranker 或 Milvus 完成内部查询。它是一层面向 LLM/Agent 的知识抽取、组织和逻辑跳转系统。

### 传统 RAG 与 SIQ LLM-Wiki 的差异

| 维度 | 传统向量 RAG | SIQ LLM-Wiki |
| --- | --- | --- |
| 基本单元 | 固定长度文本 chunk | company、report、segment、fact、relation、claim、metric、note link、evidence |
| 身份约束 | 常依赖文档 metadata 过滤 | 先冻结 ResearchIdentity，再进入对象目录 |
| 召回方式 | embedding + top-k | topic alias + priority files + object IDs + 逻辑跳转 |
| 财务事实 | 从片段中推断期间、单位和口径 | raw/normalized/value/unit/currency/period/source 一起保存 |
| 主表与附注 | 依赖相邻文本或相似度 | `note_links.json` / `document_links.json` 显式连接 |
| 证据定位 | 返回命中文本 | 返回 evidence ID、PDF 页、table index、Markdown line、bbox、task ID |
| 更新复现 | chunk 和 embedding 变化后难比较 | 输入 hash、规则版本、manifest、extraction log 和稳定 ID 可重建 |
| 多 Agent 复用 | 每个 Agent 重新拼上下文 | 所有 profile 共享同一事实与证据合同 |

### LLM-Wiki 的生产路径

```text
身份冻结
  -> 解析产物保真
  -> rule-first segments/facts/relations/claims
  -> 主表-附注 note/document links
  -> retrieval_index 主题路由
  -> 受控语义增强
  -> evidence 回链
```

其中 `retrieval_index.json` 记录 query aliases、优先文件、对象 ID 和推荐读取顺序。它是面向智能体的逻辑导航表，不是向量 top-k 结果。

LLM 仍然有发挥空间：它可以解释、比较、归纳开放文本，也可以为经营画像和重大事项生成带 `needs_review` 的候选。但候选必须保留来源 segment 与 evidence IDs，不能自动升级为财务数字的权威来源。

### LLM-Wiki、PostgreSQL 和 Milvus 各自做什么

| 组件 | 定位 | 能否作为权威财务事实 |
| --- | --- | --- |
| LLM-Wiki | 文件型 evidence package、主题路由、事实与关系逻辑跳转 | 是，前提是 package 与质量状态满足要求 |
| PostgreSQL | 精确结构化索引、长期记忆账本、任务与审计状态 | 经 ResearchIdentity 和来源合同约束后，可作为 Wiki-first 路径的受控 fallback；不取代原始 evidence package |
| Milvus | 文本、图片、表格、法规和记忆的可重建语义索引 | 否，适合发现候选，不替代权威事实 |
| Qwen3-VL Reranker | 对 Milvus 或 memory 候选精排 | 否，不重排 Wiki 已确定的权威事实顺序 |

一句话概括这项设计：

> **向量库可以重建，模型可以替换，但事实身份、原始证据和审计链不能随模型版本漂移。**

---

## 七、创新三：从解析质量到最终回答的三重质量面

“降低幻觉”是一个过于笼统的目标。SIQ 将质量拆成三个可以分别度量、又能够串联追责的层次。

### 1. 解析质量

解析质量回答：原始材料是否被正确恢复？

- 页面和 reading order 是否正确；
- 表格是否完整，行列是否错位；
- 跨页表是否被正确关联；
- 图片、公式和 bbox 是否保留；
- PDF 页码、打印页码、Markdown 行和 table index 是否可解析；
- source-map target 是否真的存在。

### 2. 财务事实质量

财务事实质量回答：报表本身是否完整、自洽？

| 校验层 | 代表检查 |
| --- | --- |
| 抽取完整性 | 资产负债表、利润表、现金流量表及行业必需指标 |
| 资产负债勾稽 | 资产=负债+权益；流动+非流动；归母+少数股东 |
| 利润桥 | 毛利=收入-成本；净利润=税前利润-所得税 |
| 现金桥 | 现金净变动与期末现金关系 |
| 附注净额 | 商誉、应收、存货、固定资产等原值-准备=净额 |
| 来源一致性 | 公式输入是否属于同一报告身份和来源族 |

### 3. 最终回答质量

最终回答质量回答：模型在正文中使用数字的方式是否正确？

- claim 中的数字能否映射到 trusted evidence；
- ResearchIdentity 是否一致；
- 期间、单位和币种是否一致；
- 公式是否由后端重新执行；
- trace 中的结果是否与正文 claim 相同；
- 引用是否真的支持结论；
- 缺证据时是否显式降级。

### 为什么一定要让程序重新计算

LLM 非常擅长解释公式，却不应该承担最终数值责任。例如同比：

```text
yoy = (current - previous) / abs(previous)
```

当上期为负数、零或缺失时，不同业务口径会产生完全不同的含义。SIQ 的共享计算器会返回状态，而不是只返回一个浮点数：正常、负基数、除零、缺失、N/A、缺汇率或证据不一致。

同样，商誉问题不能只引用主表中的净额，然后由模型猜测原值和减值准备。系统必须沿附注关系分别取回原值、准备和净额，并执行：

```text
原值 - 减值准备 = 账面净额
```

模型正文写着“经核验”不构成核验证据。只有后端工具回执、输入 evidence、公式和结果一致，才算完成。

---

## 八、关键工程设计一：岗位智能体与 R0-R4 投委会 Hybrid DAG

SIQ 的 Agent 设计重点不在数量，而在岗位合同。

每个 Hermes profile 都定义了：输入、职责、可见事实、私有背景知识、可用工具、禁止行为、输出路径、失败条件和升级机制。所有角色共享证据底座，但不能互相背书来替代证据。

### 二级市场岗位链

| Profile | 核心职责 |
| --- | --- |
| `siq_assistant` | 通用问答、指标解释、证据定位和报告导航 |
| `siq_analysis` | 年报经营分析、风险链条和研究报告 |
| `siq_factchecker` | 事实、计算、引用和风险遗漏核查 |
| `siq_tracking` | 持续跟踪、事件更新、预警和研究记录 |
| `siq_legal` | 法规检索、合规分析和法律意见草稿 |

多市场版本的 analysis、factchecker 和 tracking 使用同一岗位思想，但消费不同市场的 artifact、XBRL/PDF 证据和 normalized metrics。

二级市场和一级市场并不是两套互不相关的 Agent Demo。它们消费同一种身份、证据、权限和审计合同，但工作流不同：二级市场使用面向持续研究的岗位链，一级市场使用有阶段依赖和隔离语义的决策 DAG。共享基础设施让两条业务线能够复用底座，业务 scope 则阻止证据越界。

### 一级市场 Deal OS 的真实流程

一级市场不是把多个 Agent 放进聊天室轮流发言。在上一版投委会方法论基础上，SIQ 将独立研究、风控反证、主席裁决和红蓝对抗工程化为一张有依赖约束、证据门和隔离语义的 Hybrid DAG：

```text
R0  信息校验、材料完整性与证据门禁

R1A strategist | sector_expert | finance_auditor | legal_scanner
    四位专家独立研究，不读取同轮 peer report
      -> R1B risk_controller 读取四份报告做反证
      -> R1B chairman 最后读取五份报告做初步综合

R1.5 争议识别与主席裁决
  -> R2 专家回应裁决并修订
  -> R3 动态红蓝对抗
  -> R4 加权评分、主席结论与归档
```

R1A 的隔离尤其重要。四位专家如果一开始就读取彼此观点，很容易产生锚定、从众和观点污染。SIQ 允许底层任务串行执行，但不允许因此把同轮 peer report 注入 R1A；风控必须等待四份独立报告后再反证，主席最后综合。

每位专家在发表 R1 观点前，还必须完成三步学习：

1. 检索当前 Deal 的共享项目证据；
2. 检索当前岗位的私有方法论知识库；
3. 读取自身 workspace 规则和行为合同。

共享证据用于验证项目事实，私有库只提供行业方法和背景知识，不能替代当前项目材料。正式任务如果私有检索为空或不可用，流程 fail closed；只有预演或显式 fallback 才能降级继续。

当前投委会合同还固定了权重与阈值：主席 30%，战略、行业、财务、风控各 15%，法务 10%；总分 `>=70` 通过，`68-69` 可以复议一次，`<68` 不通过。系统不会在项目运行中临时修改这些规则。

这套流程的价值不是声称 AI 可以替代投委会，而是把以下内容结构化保存：

- 谁在什么证据基础上形成了什么观点；
- 哪些事实已验证，哪些是假设或缺口；
- 哪些分歧被主席裁决；
- 专家如何回应裁决并修改结论；
- 红蓝双方围绕什么争议交锋；
- 最终权重、条件、保护条款和行动项如何形成。

---

## 九、关键工程设计二：记忆负责连续性，证据负责事实

如果智能体每次对话都完全失忆，它无法像长期共事的研究伙伴；如果它把所有历史内容都当成事实，又会被旧结论和错误偏好污染。

SIQ 因此把记忆拆成四层：

| 记忆层 | 保存内容 | 作用 |
| --- | --- | --- |
| Hermes 原生记忆 | 会话、响应、profile runtime、checkpoint 和短期上下文 | 保持对话与工具状态连续 |
| 本地临时任务记忆 | 草稿、临时 evidence、intermediate artifacts | 支撑长任务分阶段推理、重试和恢复 |
| PostgreSQL 权威长期记忆 | 用户偏好、明确纠错、项目结论、权限、来源和有效期 | 可审计、可删除、可授权的长期账本 |
| Milvus 语义索引 | profile 知识和 memory item 的向量与 scope metadata | 语义召回与泛化检索，可从权威层重建 |

长期记忆支持 `user_private`、`project_shared` 和 `system_shared` 等可见性。动态记忆默认按 30 天半衰期衰减，让近期经验自然优先；当用户明确要求“完整历史”或“全量检索”时，可以绕过时间衰减，但仍然受 ACL、scope、rerank 和上下文预算限制。

这里的“全量记忆”不是无限上下文，也不是让模型永久记住所有聊天，而是完整保存经过授权的记忆项，并在需要时按权限召回。

SIQ 坚持一条边界：

> **记忆负责连续性，证据负责事实。**

用户偏好可以来自记忆；财务数字、法律条款和投决结论必须回到当前证据包、数据库事实和原始材料。

---

## 十、创新四：OpenShell + Hermes 安全智能体运行面

研究智能体需要读取公司材料、运行脚本、查询数据库、写报告，有时还需要受控联网。如果直接把这些能力交给宿主机上的 Agent，风险会迅速扩大：误写源码、跨公司读写、凭据泄露、任意出网、僵尸进程和并发写冲突都可能发生。

SIQ 直接基于 NVIDIA OpenShell 构建了面向投研业务的 Hermes 安全运行面。这里的“自研”是指 SIQ 自行完成业务集成和控制面，不是声称重新实现了 OpenShell；项目也没有引入或运行 NemoClaw/NemoHermes。

### 三层职责边界

可以用一句话理解：

> **API 决定谁可以研究哪家公司；OpenShell 决定执行环境实际上能访问什么；Hermes 决定如何在授权范围内完成任务。**

项目实际使用 OpenShell 的 Gateway、Sandbox、Provider、service forwarding、Landlock、进程/seccomp、网络策略和凭据隔离，并在其上实现：

- 公司级 scope；
- 对话 sandbox generation；
- 多沙箱资源池；
- 请求级 lease；
- single writer 与 fencing；
- API 重启后的恢复与隔离；
- 空闲 TTL 回收；
- Host fallback；
- runtime provenance；
- PostgreSQL/Milvus 宿主数据 Broker；
- 受控上传、出网与服务转发。

### 一次请求的运行路径

```text
FastAPI Agent Runtime
  -> 用户/公司/报告上下文校验
  -> runtime selection
  -> 公司 scope 自动创建
  -> 对话 sandbox generation
  -> pool binding / lease / fencing
  -> OpenShell Gateway
  -> Sandbox 内 BYOC Hermes /v1/runs
  -> Provider / Broker / 受控外部服务
  -> SSE 终态与写入静默确认
  -> lease 释放
  -> idle TTL 清理或 Host fallback
```

同一对话研究同一家公司时可以复用 sandbox generation；切换公司会生成隔离代际，防止上下文和写路径泄漏。API 重启后，recovery 会核对 sandbox、forward 和 lease 状态，而不是盲目假定旧进程仍然安全。

### “已经跑通”与“正式生产完成”必须分开

这是项目最容易被误读的状态，必须准确说明。

| 状态面 | 当前结论 |
| --- | --- |
| OpenShell + Hermes 技术集成 | `siq_analysis` 目标范围内的技术链路已打通，真实使用 Gateway、Sandbox、Provider、Policy、Forwarding 和 Broker |
| `siq_analysis` 真实前端链路 | 已跑通并验证公司 scope、SSE、lease、generation、provenance、释放和 TTL |
| 当前工作机运行采样 | 2026-07-21 运行态选择文件为 `target=openshell`、`session_mode=all`；API 读取来源为 `runtime_file` |
| 全局环境回退基线 | Host 仍保留为 fallback，不把所有 profile 和多公司任务强制切流 |
| Formal production quality gate | 当前仍为 `NO_GO` |

`NO_GO` 不表示功能没实现，而表示正式 Host/OpenShell A/B、质量等价、完整回滚/删除证据、可复现脱敏证据和人工安全评审尚未全部完成。

运行态选择文件位于 `var/openshell/runtime-selection/`，不进入 Git。它能证明当前工作机在采样时如何路由，不能替代带 source commit、配置 digest、sandbox image digest、A/B、回滚和审批摘要的正式 cutover receipt。

项目刻意把三件事建模为不同状态：

1. 沙箱和权限边界是否工作；
2. 业务回答质量是否达到发布线；
3. 是否已经完成正式生产切流。

这比把“sandbox 能启动”直接写成“生产就绪”更保守，也更符合金融系统的治理要求。

---

## 十一、DGX Spark：让多模型、多数据服务与安全控制面在单机协同

SIQ 对 DGX Spark 的使用，不是“把一个大模型跑在 GPU 上”，而是让一条完整研究流水线在一台设备上高密度协同。

当前采样环境为：

- NVIDIA GB10；
- CUDA 13；
- Linux aarch64；
- 20 核 ARM CPU；
- 约 128 GB 统一内存；
- Docker + NVIDIA Container Toolkit；
- 多个独立 vLLM/HTTP 模型进程。

### 模型职责矩阵

| 模型/服务 | 当前配置要点 | 在 SIQ 中的职责 |
| --- | --- | --- |
| NVIDIA Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4 | vLLM 0.20、NVFP4、262144 context target、FP8 KV、prefix cache | 本地私有推理、工具调用、长上下文和原生图片/音频/视频理解 |
| StepFun Step-3.7 Flash | 200K context contract、受控 Provider | 云端复杂推理、报告生成，与本地 Nemotron 形成可选择/回退/A-B 的双主模型结构 |
| MinerU2.5-Pro-2604-1.2B | 独立 VLM 与 MinerU API | PDF/扫描件版面、表格、公式、图片和 reading order 恢复 |
| Qwen3-VL-Embedding-2B | 本地多模态 embedding，当前维度 1024 | 文本、图片、表格、法规、项目知识和记忆向量化 |
| Qwen3-VL-Reranker-2B | 独立 HTTP wrapper、单次 1:N | 对 Milvus 和 memory 候选精排，不重排 Wiki 权威事实 |
| Fun-ASR-Nano-2512 + VAD + speaker | 本地 ASR、时间戳和说话人链路 | 短语音提问、会议实时/导入转写 |

上述 memory target 和 context 都是启动配置目标，不是固定显存占用或性能承诺。真实驻留受请求长度、并发序列、KV cache、CUDA graph、量化方式和统一内存压力影响。

### 为什么统一内存适合这种系统

DGX Spark 的价值在于 GPU、ARM CPU 和大容量统一内存共同承载：

- GPU：生成、视觉编码、文档解析、embedding、rerank 和 ASR；
- ARM CPU：FastAPI、Flask、规则校验、文件 hash、音频编解码和 OpenShell 控制面；
- 本地服务：PostgreSQL、Milvus、MinIO、Redis、LLM-Wiki 和文件 artifact；
- Agent runtime：Hermes、Provider、Broker、SSE、任务状态与恢复。

模型按职责拆成独立服务，每个服务有自己的端口、缓存、环境、PID/health 和资源预算。单个模型重启不要求停止整个研究平台。

### 真实复杂性：统一内存不是无限内存

统一内存也意味着所有组件竞争同一个资源池：模型权重、KV cache、数据库 cache、Milvus segment、Docker page cache、批量解析和并发会议必须整体规划。

2026 年 7 月 20 日的本机采样中，系统内存和 swap 已接近满载。这不是一个应该隐藏的事实，它说明 SIQ 已经进入真实的容量工程阶段：长上下文、批量解析、Reranker 并发和会议任务必须设置上限、排队、超时和压力门禁。

项目使用的工程手段包括：

- NVFP4 权重与 FP8 KV cache；
- vLLM continuous batching 与 prefix caching；
- `max_num_seqs`、batched tokens 和 context 上限；
- 独立 GPU memory target；
- Reranker 1:N 批处理与单 EngineCore 并发保护；
- 上层 API timeout、候选数量限制、meeting backpressure；
- liveness、HTTP readiness、最小推理和业务质量四层检查；
- 显式 degraded/fallback，而不是空结果伪装成功。

因此，DGX Spark 在这个项目中的作用不是背景硬件，而是把多模型推理、私有数据服务和安全智能体运行面压缩到一台可控设备中的系统基础。

---

## 十二、工程完整性与可复核证据

Hackathon 项目最容易出现的情况，是功能截图很多，但很难在干净环境复现。SIQ 为此建立了分层测试和可版本化脱敏 fixture。

### 仓库级工程规模

截至 2026 年 7 月 21 日，排除运行态、虚拟环境和依赖目录后，仓库包含：

| 测试/脚本资产 | 数量 |
| --- | ---: |
| Python 测试文件 | 493 |
| TypeScript / Playwright / Node 测试文件 | 115 |
| Shell 脚本 | 77 |

数量不能替代覆盖率，但它可以说明项目不是由少量演示脚本组成。测试覆盖 API、解析器、市场服务、共享合同、数据库导入、Hermes、OpenShell、模型服务、Web 和会议链路。

### 2026 年 7 月 21 日的可复跑收口结果

以下结果绑定公开主干 commit [`3abd1645`](https://github.com/maoyadongsh/siq-research-engine/commit/3abd1645dcd93dda2ed096f9cf4e1718a6e79908)。主要复跑入口记录在根 README，包括 API 的 `uv run python -m pytest tests`、OpenShell 的 `python3 -m pytest -q scripts/openshell/tests`、前端的 `npm run check:frontend`，以及聚合入口 `scripts/check_all.sh`。

| 验证项 | 结果 |
| --- | --- |
| API 非慢速/非网络测试 | `2919 passed, 5 skipped, 3 deselected` |
| OpenShell 专项离线回归 | `1126 passed` |
| Market ingestion helper suite | `484 passed` |
| Market contracts | `37 passed` |
| Python Ruff / 编译 / diff check | 通过 |

API CI 使用仓库内脱敏 Wiki fixture，而不是开发机完整数据。fixture 只保留测试需要的 metrics、表格窗口、页码和身份映射，不包含原始 PDF、用户上传、运行数据库、模型回答或密钥。

财务 QA 还包含：

- 12 个 `trace-offline` 确定性 case；
- 7 个覆盖 CN/HK/US/JP/KR/EU 的 `wiki-static` case；
- 正常事实查询和同比计算；
- 证据缺失拒答；
- 数值篡改；
- 跨公司身份攻击；
- 伪造 calculator marker 等负向场景。

这些数据证明的是合同和 fail-closed 行为，不应被写成“模型准确率”或客户 SLA。

### 为什么仍然保留红灯和 NO_GO

截至本文采样时，[GitHub Actions run 29798432777](https://github.com/maoyadongsh/siq-research-engine/actions/runs/29798432777) 的整体结论仍为 failure。主干的 API、market、Python services、script syntax、Web unit/build、PMIC 和两套 meeting feature-flag E2E 已通过；仍有三类已知问题：

1. 默认 Web E2E 为 `49 passed, 9 failed, 9 skipped`，仍有页面契约与移动端时序失败；
2. Meeting additive contract 的 pre-meeting baseline 仍需正式治理；
3. Trivy filesystem misconfiguration scan 报告若干 Dockerfile 未声明 `USER`，并因变量名 `HERMES_AUTH_FILE` 将认证文件路径启发式标记为 potential secret-in-ENV。仓库提交的是路径 `/sandbox/runtime-auth/auth.json`，不是密钥值；正式 OpenShell sandbox Dockerfile 实际以非 root 用户运行，但这些告警仍需要逐项修正或完成抑制审查。

项目没有为了“看起来全绿”而删除 fail-closed 检查，也没有把 OpenShell `NO_GO` 字符串直接改成 `GO`。对一个强调审计的系统而言，公开已知边界比隐藏失败更重要。

---

## 十三、商业价值、竞争差异与可复制壁垒

SIQ 的商业价值不应只用“降本增效”四个字概括。更准确的表达是：它瞄准机构研究生产方式中的材料、事实、协作和审计成本。

### 1. 缩短材料到结论的周期

官方材料发现、下载、解析、结构化、核数和引用整理占据研究员大量时间。SIQ 将这些重复劳动沉淀为可复用管线，目标是释放材料处理时间，让研究者把更多精力用于假设、比较、访谈和决策。这是下一阶段需要通过客户试点验证的收益，不是本文已经证明的 ROI。

可量化指标包括：单份报告处理时长、人工翻页次数、首稿交付时间、人工核数步骤和重复材料整理量。

### 2. 降低事实与合规风险

ResearchIdentity、source map、财务勾稽、answer audit、权限 scope 和 runtime provenance 用于防控以下风险：

- 串公司、串市场和串报告期；
- 金额单位和币种误读；
- 主表净额与附注原值混用；
- 引用存在但不支持结论；
- 模型心算错误；
- Agent 越权访问或跨公司写入。

可量化指标包括：引用覆盖率、财务校验通过率、人工复核问题数、越权拒绝事件和审计回放时间。

### 3. 复用组织知识，而不是沉淀更多聊天记录

用户纠错、项目经验、岗位方法和投委会产物进入有来源、有权限、有生命周期的记忆体系。它们可以被召回、删除、更新和审计，而不是散落在个人文档、聊天群和历史会话中。

### 4. 支持私有化和模型可替换

Nemotron、MinerU、Qwen、FunASR、Milvus 和 OpenShell 可以在 DGX Spark/内网环境协同，为减少敏感材料外发提供技术路径。需要明确的是，StepFun 是云端模型路径；只有选择本地 Nemotron，或由 Provider 策略严格控制发送范围、上下文与凭据时，系统才能形成相应的数据边界。更重要的是，模型层与事实层解耦：机构可以更换生成模型，而不必重做市场适配、证据合同、财务规则和审计流程。

### 5. 同一底座支撑一级市场和二级市场

二级市场关注官方披露、财务事实和持续跟踪；一级市场关注材料完整性、专家分工、争议和决策。两者业务流程不同，但都需要身份、证据、权限、记忆、质量和回放。SIQ 把这些共性能力沉淀为统一基础设施。

### 与典型架构范式相比，SIQ 的差异

| 对比对象 | 典型关注重点 | SIQ 的差异 |
| --- | --- | --- |
| 通用大模型/PDF Chat | 流畅回答和摘要 | 要求身份、证据、计算、审计和原文回跳 |
| 传统向量 RAG | 相似片段和 top-k 上下文 | 权威财务事实不由相似度排序决定 |
| 金融数据终端 | 数据查询和图表 | 进一步组织材料生产、分析、核查、协作和审计过程 |
| 通用多智能体框架 | 多角色对话和任务委派 | 岗位合同、Hybrid DAG、证据门和权限边界按投研业务建模 |
| 单一本地大模型 | 私有生成接口 | 同时具备解析、检索、ASR、事实服务、计算与安全执行面 |
| 普通容器化 Agent | 进程隔离 | 权限与用户、公司、会话、profile、Provider 和写入路径绑定 |

这张表比较的是架构范式，不代表 SIQ 已经与具体商业产品完成同条件 benchmark。

### 可复制壁垒在哪里

SIQ 的壁垒不是某一个 Prompt，也不是某一个模型版本，而是长期协同形成的系统资产：

- 六市场 adapter 与身份映射；
- 解析 artifact 和 source map；
- evidence package 与跨服务合同；
- 财务抽取、勾稽和确定性计算规则；
- ResearchIdentity 与 answer audit；
- 记忆 ACL 和生命周期；
- 二级市场岗位链与一级市场 Hybrid DAG；
- OpenShell 业务控制面、Broker、租约和恢复；
- 可复跑 fixture、负向测试和失败样本。

这些资产不必因为更换生成模型而失效，也有望让新模型更快进入受控研究流程。

目前项目还没有公开的真实客户、效率提升或 ROI 数据，因此本文不声称“已经节省多少成本”或“已经提升多少准确率”。可探索的商业化方向包括机构私有化部署、研究工作台、Deal OS 模块、证据/解析服务和合规审计能力；是否成立，需要通过客户试点和真实基线验证。

---

## 十四、当前边界与下一阶段路线

一个可信的技术分享不仅要写已完成什么，也要写什么还没有完成。

### 已在代码与合同中落地

- CN/HK/US/EU/JP/KR 六市场官方披露入口与统一 evidence package 合同；
- 二级市场 analysis/factchecker/tracking/legal 岗位链；
- 一级市场 Deal OS、双库检索和 R0-R4 岗位合同；
- 文档解析、会议转写和向量入库应用；
- LLM-Wiki、PostgreSQL、Milvus 的事实/索引边界；
- 财务确定性计算、引用守卫、answer audit 与 OpenShell 运行面控制。

### 已完成本机或公开样板验证

- 上汽集团 A 股全链路主样板与商誉勾稽 trace；
- LLM-Wiki 逻辑知识对象与主表/附注跳转；
- Nemotron、MinerU、Qwen Embedding/Reranker、FunASR 多模型 readiness；
- `siq_analysis` 的 OpenShell 真实前端全链路；
- 可版本化脱敏 fixture 与分层测试。

### 尚未完成正式发布验证

- 六市场 evidence package 的深度与真实失败覆盖；
- OpenShell 正式 Host/OpenShell A/B 与生产发布证据；
- 统一内存下的容量、长上下文和并发压力门；
- Web 默认 E2E 稳定性；
- Meeting additive baseline 治理；
- 更稳定的 live quality benchmark；
- 一级市场真实材料与人工专家评审闭环；
- 会议真实多说话人场景的完整发布证据。

### 下一阶段路线

1. 以固定问题集完成公开、可复核的 DGX Spark 性能与质量证据包，包括 TTFT、P95、吞吐、统一内存、引用覆盖率和数字准确性；
2. 完成 OpenShell Limited GO 所需 A/B、回滚、删除、人工安全评审和 production receipt；
3. 通过真实客户试点建立“人工流程 vs SIQ 辅助流程”的时间、质量和 ROI 基线；
4. 完善多市场样板和失败案例，不只增加成功截图；
5. 推进数据库迁移、超大核心文件拆分、profile 注册和 parser 分叉治理；
6. 将会议陈述、研究结论、投委会决议和跟踪任务连接成更完整的投前、投中、投后闭环。

---

## 十五、结语：让事实、推理、权限和错误都变得可见

SIQ 不试图证明 AI 可以替代研究员，更不试图把投资责任交给模型。

它试图回答一个更现实的问题：当 AI 真正进入研究和投决流程后，怎样让每一个数字知道自己来自哪里，每一个计算能够重新执行，每一个智能体知道自己的职责和权限，每一次分歧留下过程记录，每一个最终决定都可以被复盘和质疑。

对投研而言，真正有价值的智能化，不是让系统表现得更像一个自信的人，而是让事实、推理、权限和错误都变得可见。

当模型可以替换、向量库可以重建、运行面可以回退，而证据身份、质量状态和审计链仍然稳定存在时，AI 才开始从“内容生成工具”走向“专业研究基础设施”。

这就是 SIQ Research Engine 想迈出的那一步。

---

## 项目与技术资料

- GitHub：<https://github.com/maoyadongsh/siq-research-engine>
- 项目 README：<https://github.com/maoyadongsh/siq-research-engine/blob/master/README.md>
- DGX Spark Hackathon 评审与演示指南：<https://github.com/maoyadongsh/siq-research-engine/blob/master/docs/competition/dgx-spark-hackathon-evaluation-guide.md>
- 多市场 evidence package 合同：<https://github.com/maoyadongsh/siq-research-engine/blob/master/docs/architecture/market-evidence-package-contract.md>
- OpenShell + Hermes 集成状态：<https://github.com/maoyadongsh/siq-research-engine/blob/master/docs/siq-openshell-hermes-integration-status.md>
- OpenShell NO_GO 到 GO 路线：<https://github.com/maoyadongsh/siq-research-engine/blob/master/docs/runbooks/openshell/no-go-to-go-readiness-matrix.md>
- DGX Spark 模型服务归档：<https://github.com/maoyadongsh/siq-research-engine/tree/master/infra/model-services>

> 免责声明：本文介绍的是工程系统、验证结果和可探索的商业价值，不构成证券研究报告、投资建议或收益承诺。项目中的模型输出必须经过证据核验和有权人员复核后使用。
