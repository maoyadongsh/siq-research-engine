# FinSight 财报智能分析平台设计文档

## 1. 文档目标

本文档基于当前完整项目代码进行审查与整理，描述 FinSight / douge_ai_agent 的整体架构、核心业务链路、模块职责、数据产物、关键设计取舍、创新亮点和后续演进方向。

项目不是一个单点工具，而是一条围绕“上市公司公开财报”的端到端智能分析流水线：

```text
官方公告检索
  -> PDF 下载
  -> PDF 解析与结构化
  -> 财务数据抽取与勾稽
  -> 可审计证据索引
  -> Wiki 语义层构建
  -> 分析 / 核查 / 跟踪 Agent 交互
  -> 前端统一工作台呈现
```

设计重点不是单纯“把 PDF 转成 Markdown”或“做一个聊天机器人”，而是建立一条可复核、可追溯、可扩展的财报智能处理链路。

## 2. 项目定位

FinSight 面向 A 股上市公司公开披露文件，提供从原始官方公告到智能问答与持续跟踪的本地化分析底座。

核心目标包括：

- 降低财报获取成本：通过公司名、简称、代码快速定位官方披露源。
- 降低 PDF 解析成本：自动解析年报、半年报、季报，并保留中间产物。
- 提升财务抽取可信度：用规则、表格分类、来源定位和勾稽校验减少幻觉与误读。
- 支持审计式复核：每个关键数据尽量能回到 Markdown 行号、表格编号、PDF 页码和 bbox。
- 支持智能体协作：分析、事实核查、持续跟踪 Agent 在统一前端中辅助研究。
- 支持长期知识沉淀：将公司级报告、事实、关系、主张和证据写入 Wiki 语义层。

## 3. 总体架构

### 3.1 服务组成

| 模块 | 路径 | 技术栈 | 职责 |
|------|------|--------|------|
| 主前端 | `/home/maoyd/finsight/finall_all_front_0516/front` | React、Vite、TypeScript、Tailwind、lucide-react | 统一工作台、财报搜索下载、PDF 解析工作台、报告展示、Agent 面板 |
| 聚合后端 | `/home/maoyd/finsight/backend` | FastAPI、SQLModel、SQLite、SSE | Agent 代理、聊天历史、Wiki 文件服务、宠物/成就系统、本地 Tracking Agent 原型 |
| PDF 下载服务 | `/home/maoyd/report-finder-service` | FastAPI、httpx、Pydantic | 公司解析、官方源查询、候选排序、PDF 下载缓存 |
| PDF 解析服务 | `/home/maoyd/finsight/pdf2md_web` | Flask、SQLite、MinerU/VLM、本地规则引擎 | PDF 上传队列、Markdown 生成、质量报告、财务抽取、表格溯源、人工修正 |
| 语义抽取脚本 | `/home/maoyd/extract_company_semantics.py` | Python 规则脚本 | 从 Wiki 和解析产物生成公司语义层、事实、关系、主张、证据索引 |
| Hermes Agent | `~/.hermes/profiles/*` | Hermes gateway | 普通聊天、分析、事实核查、持续跟踪 Agent |

### 3.2 请求路由设计

主前端使用 Vite dev server 作为统一入口，根据 URL 前缀转发到不同服务：

```text
浏览器 http://localhost:5173
  |
  |-- /api/chat
  |-- /api/wiki
  |-- /api/analysis
  |-- /api/factchecker
  |-- /api/tracking
  |       -> 聚合后端 http://127.0.0.1:10081
  |
  |-- /api/v1/*
  |       -> PDF 下载服务 http://127.0.0.1:8000/v1/*
  |
  `-- /pdfapi/*
          -> PDF 解析服务 http://127.0.0.1:5000/api/*
```

该设计把“用户入口”收敛到前端，同时保留各后端服务独立演进能力。开发期不需要把所有后端强行合并到一个进程，降低了集成风险。

## 4. 核心业务闭环

### 4.1 财报检索与下载链路

入口页面：`/search`

代码路径：

```text
front/src/pages/SearchDownload.tsx
report-finder-service/src/report_finder_service/services/orchestrator.py
report-finder-service/src/report_finder_service/adapters/cninfo.py
report-finder-service/src/report_finder_service/services/latest_selector.py
```

链路如下：

```text
用户输入公司名 / 股票代码 / 年份
  -> /api/v1/resolve
  -> CompanyResolver：官方动态候选 + 本地别名种子 + 可选 CompanyMappingAgent
  -> /api/v1/reports/recent
  -> CninfoAdapter：topSearch 获取 orgId，hisAnnouncement 获取公告
  -> LatestReportSelector：按 report_end、published_at、report_type_priority 排序
  -> 前端展示年报 / 半年报 / 季报候选
  -> /api/v1/reports/batch-download 或 /select-download
  -> ReportDownloader 保存到 downloads/<公司>/<报告类型>/
```

设计特点：

- 官方源优先：当前主链路收敛到巨潮资讯，不依赖搜索引擎结果页。
- 候选透明：返回候选数量、排序规则、top candidates 和选取理由。
- 支持模糊输入：公司简称、俗称、股票代码可通过 Resolver 进入标准证券实体。
- 下载可缓存：基于 URL / 内容标识去重，避免重复下载。

### 4.2 PDF 解析与结构化链路

入口页面：`/parse`

代码路径：

```text
front/src/pages/PdfParsing.tsx
pdf2md_web/app.py
pdf2md_web/mineru_client.py
pdf2md_web/financial_extractor.py
pdf2md_web/quality_report.py
pdf2md_web/pdf_source_viewer.py
```

链路如下：

```text
PDF 上传
  -> Flask 保存 uploads/<task_id>.pdf
  -> SQLite tasks.db 记录任务状态
  -> 后台 worker 提交 MinerU API
  -> 获取 Markdown、content_list、middle/model output、图片
  -> 写入 results/<task_id>/
  -> 构建 content_list_enhanced.json
  -> 构建 quality_report.json 和 table_index.json
  -> 构建 financial_data.json
  -> 构建 financial_checks.json
  -> 构建 document_full.json 完整解析总账
  -> 前端展示 Markdown、质量报告、财务勾稽、PDF 原页和表格溯源
```

关键产物：

| 产物 | 作用 |
|------|------|
| `result.md` | 基础 Markdown，带 PDF 页码标记 |
| `result_complete.md` | 完整增强版 Markdown，追加可恢复信息附录 |
| `content_list.json` | MinerU 原始内容列表 |
| `content_list_enhanced.json` | 加强后的页码、表格、目录、脚注、附注关系索引 |
| `quality_report.json` | 解析质量诊断、核心表候选、可疑表、缺失章节等 |
| `table_index.json` | 表格编号、位置、类型、预览和来源信息 |
| `financial_data.json` | 三大表和主要指标结构化结果 |
| `financial_checks.json` | 资产负债、利润、现金流、衍生指标等勾稽校验 |
| `document_full.json` | 全链路总账：文本、结构、质量、财务、资源、产物索引集中记录 |
| `corrections.json` | 人工复核修正记录 |
| `pdf_pages/` | PDF 页图缓存，用于可视化定位 |

设计特点：

- 任务队列持久化：上传、排队、处理中、完成、失败等状态写入 SQLite。
- 结果可恢复：历史任务可重新打开，不依赖浏览器内存状态。
- 解析可诊断：不是只给 Markdown，还给质量报告和可疑表提示。
- 数据可复核：表格、指标、页码、bbox、Markdown 行号互相连接。
- 人工可介入：前端支持表格修正和修正版 Markdown 生成。

### 4.3 财务数据抽取与勾稽链路

代码路径：

```text
pdf2md_web/financial_extractor.py
```

抽取流程：

```text
Markdown HTML 表格遍历
  -> 表格解析为二维网格
  -> 规则识别表格类型：主要指标 / 资产负债表 / 利润表 / 现金流量表
  -> 识别报告年份、报告类型、行业 profile、合并/母公司口径
  -> 抽取标准化科目和值
  -> 合并重复指标
  -> 对缺失核心表启用碎片化兜底或可选本地 Qwen/VLLM 裁判
  -> 生成 financial_data.json
  -> 执行财务规则校验
  -> 生成 financial_checks.json
```

勾稽校验覆盖方向：

- 资产负债表：资产总计、负债合计、权益合计、负债和权益总计等关系。
- 利润表：营业利润、利润总额、净利润、归母净利润等关系。
- 现金流量表：经营、投资、筹资现金流与现金等价物变动关系。
- 关键指标：ROE、EPS、归母净资产、人均或每股指标等衍生校验。
- 同比变化：通过报告年度和上一年度数值产生预警。

重要设计取舍：

- 规则优先：事实数值抽取由确定性代码完成，降低 LLM 幻觉风险。
- LLM 只做裁判：本地 Qwen/VLLM 仅在低置信表格候选或三大表缺失时辅助判断表格性质。
- 摘要与全文区分：年报摘要不会被误当作完整年报，缺三大表时给出明确提示。
- 失败分级：校验失败可因来源跨度过大或数量级异常降级为 warning，避免把解析噪声误判为财务异常。

### 4.4 Wiki 文件服务与报告展示链路

入口页面：`/analysis`、`/verify`、`/tracking`

代码路径：

```text
backend/routers/wiki.py
front/src/pages/AnalysisReport.tsx
front/src/pages/FactVerification.tsx
front/src/pages/Tracking.tsx
```

Wiki 默认根目录：

```text
/home/maoyd/wiki/companies/
```

公司目录约定：

```text
companies/<股票代码>-<公司名>/
  company.json
  reports/
  analysis/*.html
  factcheck/*.html
  tracking/*.html
  metrics/
  evidence/
  semantic/
```

聚合后端提供：

- 公司列表：`/api/wiki/companies/list`
- 分析报告列表：`/api/wiki/companies/{company_dir}/reports`
- 事实核查报告列表：`/api/wiki/companies/{company_dir}/factchecks`
- 跟踪报告列表：`/api/wiki/companies/{company_dir}/trackings`
- 白名单文件读取：`/api/wiki/companies/{path}`

前端以 iframe 展示 HTML 报告，同时保留下载与分享入口。右侧嵌入对应 Agent 面板，实现“读报告 + 问 Agent”的联合工作台。

### 4.5 Agent 对话链路

代码路径：

```text
backend/services/hermes_client.py
backend/routers/chat.py
backend/routers/analysis.py
backend/routers/factchecker.py
backend/routers/tracking_agent.py
front/src/lib/useAgentChat.ts
front/src/components/agent/AgentChatPanel.tsx
```

链路如下：

```text
前端输入问题
  -> POST /api/<agent>/chat/stream
  -> 聚合后端读取 SQLite 历史消息
  -> create_run 调用 Hermes /v1/runs
  -> stream_run 订阅 Hermes SSE
  -> 后端转发 delta / tool / reasoning / done 事件
  -> 前端增量渲染助手回复
  -> 后端保存 assistant 完整回复
```

支持的 Hermes profile：

| Profile | 端口 | 用途 |
|---------|------|------|
| `finsight_assistant` | `8642` | 普通聊天 / 财报问答入口 |
| `finsight_analysis` | `8651` | 深度分析报告、偿债能力、同业对比 |
| `finsight_factchecker` | `8649` | 事实核查、证据检查 |
| `finsight_tracking` | `8650` | 持续跟踪、风险观察、监控建议 |

设计特点：

- SSE 流式响应提升交互体验。
- 工具调用事件和 reasoning 事件被统一转发，前端可展示执行过程。
- 每个业务 Agent 维护独立会话 ID，避免跨业务上下文污染。
- 右侧 Agent 面板和左侧报告阅读区并列，适合研究员边读边问。

### 4.6 公司语义层抽取链路

代码路径：

```text
/home/maoyd/extract_company_semantics.py
```

输入：

```text
company.json
reports/<primary_report>/report.md
reports/<primary_report>/report.json
reports/<primary_report>/document_full.json
```

输出：

```text
metrics/key_metrics.json
metrics/three_statements.json
semantic/subject_profile.json
semantic/segments.json
semantic/facts.json
semantic/relations.json
semantic/claims.json
semantic/retrieval_index.json
semantic/note_links.json
semantic/evidence_semantic.json
semantic/image_semantic_manifest.json
semantic/extraction_log.json
_meta/semantic_extraction_manifest.json
```

抽取思路：

- 从 Markdown 标题和增强目录中构建业务段落 segments。
- 从财务指标和三大表中构建 numeric facts。
- 从表格、指标、页码、bbox、原文摘录生成 evidence。
- 从财务勾稽和附注链接生成 relations / claims。
- 生成 retrieval_index，指导 Agent 或检索系统按主题读取材料。

设计特点：

- Rule-first：脚本明确声明不让模型“编摘要”，而是从已有产物生成可审计语义层。
- Evidence-first：事实和主张都绑定证据 ID，证据可以打开 PDF 页、来源页面或表格。
- 面向检索：不是只输出大段文本，而是输出主题、别名、推荐读取顺序和证据索引。

## 5. 数据与产物设计

### 5.1 运行数据分层

项目中的运行数据和代码资产已有初步分离意识：

- `report-finder-service/downloads/`：下载后的 PDF。
- `pdf2md_web/uploads/`：上传原文件。
- `pdf2md_web/results/`：解析结果与结构化产物。
- `pdf2md_web/output/`：MinerU 原始输出。
- `pdf2md_web/tasks.db`：任务队列状态。
- `backend/data/pet.db`：聊天、宠物、成就等 SQLite 数据。
- `/home/maoyd/wiki/companies/`：公司级知识沉淀目录。

`pdf2md_web/path_config.py` 支持通过 `PDF2MD_USE_DATA_LAYOUT=1` 或 `PDF2MD_DATA_DIR` 切换到更清晰的 `data/{uploads,results,output,db,cache,logs}` 布局，这是后续生产化部署的重要基础。

### 5.2 document_full.json 的设计价值

`document_full.json` 是 PDF 解析服务最重要的总账式产物。它不是简单结果文件，而是把以下信息统一封装：

- task 元数据：任务 ID、文件名、状态、页数、提交配置。
- source_files：PDF、Markdown、完整 Markdown 的 path/url 引用。
- markdown：全文、字符数、行数、页面索引。
- content_list 和 content_list_enhanced：版面结构与增强索引。
- middle_json / model_output / payload_summary：底层模型和 MinerU 产物。
- quality_report：质量诊断。
- financial_data：结构化财务数据。
- financial_checks：勾稽校验。
- resources：图片和 PDF 页图资源索引。
- artifacts：所有白名单产物的存在性、路径和 URL。

它让后续语义抽取、Agent 检索、人工复核和外部系统集成有了一个统一读取入口。

## 6. 前端设计审查

### 6.1 信息架构

主前端提供六类核心视图：

- Dashboard：模块入口和工作台总览。
- SearchDownload：官方财报查询与下载。
- PdfParsing：PDF 解析与复核工作台。
- AnalysisReport：公司分析报告浏览 + 分析 Agent。
- FactVerification：事实核查报告浏览 + 核查 Agent。
- Tracking：持续跟踪报告浏览 + 跟踪 Agent。
- ChatPage：普通聊天入口。

### 6.2 交互亮点

- 搜索下载页采用“解析公司 -> 查年报 -> 查定期财报 -> 勾选下载”的线性流程，用户心智清晰。
- PDF 解析页承载完整复核工作台，包含任务列表、状态轮询、质量报告、源码溯源、PDF 页图和人工修正。
- 分析/核查/跟踪页使用统一 `PageWithAgentChat` 布局，形成一致的“报告 + Agent”阅读体验。
- Agent 面板支持历史加载、流式回复、停止生成、快捷问题，适合研究型交互。

### 6.3 当前边界

- 前端直接依赖 Vite 代理规则，生产部署需要明确反向代理方案。
- PDF 解析页较大，后续可拆分为 hooks、组件和领域模型，降低维护成本。
- iframe 展示 HTML 报告简单有效，但如果要做段落级引用和高亮联动，后续需要更结构化的报告渲染器。

## 7. 创新点与项目亮点

### 7.1 从“文件处理”升级为“可审计知识链路”

传统 PDF 解析往往止步于文本或 Markdown。本项目把 PDF 处理拆成多层证据链：

```text
PDF 原文 -> 页图 -> Markdown 行 -> 表格索引 -> 指标来源 -> 勾稽规则 -> 语义事实 -> Agent 检索
```

每一层都有独立产物，且上层结果能回到下层证据。这种设计非常适合金融研究、审计复核和高可信 AI 场景。

### 7.2 规则优先、LLM 辅助的金融抽取范式

项目没有把财务数据抽取完全交给 LLM，而是采用：

- 规则负责事实数值抽取。
- 表格结构负责上下文和口径识别。
- 勾稽规则负责一致性校验。
- LLM 只在低置信表格分类时作为裁判。

这是一种更稳健的金融 AI 设计：让模型做“判断”，让程序做“记账”。

### 7.3 官方源检索 + 选择证据

PDF 下载服务不仅返回文件，还返回候选、排序规则、选择理由和官方落地页。它解决了“为什么选这份报告”的问题，让财报获取本身也具备可解释性。

### 7.4 document_full 总账产物

`document_full.json` 将文本、结构、质量、财务、资源和产物索引统一起来，是后续构建 RAG、知识图谱、审计报告或智能体工具调用的理想中间层。

### 7.5 人机协同复核闭环

前端不是只展示模型结果，而是提供：

- 可疑表优先复核。
- PDF 原页定位。
- bbox 高亮。
- 表格人工修正。
- 修正版 Markdown 下载。

这让系统从“自动抽取工具”变成“分析生产工作台”。

### 7.6 公司级语义层

`extract_company_semantics.py` 将报告变成公司维度的 subject profile、segments、facts、relations、claims 和 evidence index。这比单纯向量化全文更进一步：它为 Agent 提供了结构化阅读路径和可引用证据。

### 7.7 多 Agent 分工

项目把分析、事实核查、持续跟踪拆成不同 Hermes profile，并通过聚合后端统一接入。这样既保留专业 Agent 的独立提示词/工具能力，又给前端统一体验。

### 7.8 渐进式集成策略

项目没有强行做一个“大一统单体”，而是通过 Vite 代理 + 聚合后端 + 独立服务把已有能力整合起来。这种方式适合快速把复杂原型收敛成可演示、可迭代的系统。

## 8. 质量控制与可观测性

项目已经具备多层质量控制：

- 下载层：官方源、候选去重、排序证据、缓存命中。
- 解析层：任务状态、日志、健康检查、schema 版本。
- 结构层：content_list_enhanced、table_index、quality_report。
- 财务层：financial_data、financial_checks、warnings、overall_status。
- 语义层：extraction_log、manifest、quality_min。
- Agent 层：会话历史、SSE 事件、工具执行事件。

建议后续进一步增强：

- 为每个服务增加统一 request_id / task_id 贯穿日志。
- 将健康检查汇总到主前端 Dashboard。
- 为核心产物增加 schema 校验和迁移脚本。
- 对 PDF 解析质量建立样本集和自动回归指标。

## 9. 安全与边界设计

已有安全意识：

- Wiki 文件服务限制在 `WIKI_ROOT` 内，并检查路径穿越。
- Wiki 只允许白名单扩展名。
- PDF 解析服务可通过 `PDF2MD_ACCESS_TOKEN` 设置访问 token。
- Hermes API 使用 Bearer key。
- PDF 解析运行数据可通过环境变量迁移到独立数据盘。

当前需要注意：

- 开发环境 key `change-me-local-dev` 不适合生产。
- Vite dev server 代理适合本地开发，生产需要 Nginx/Caddy 或 API Gateway 统一鉴权。
- 上传 PDF 与产物下载需要更明确的文件大小、访问权限和清理策略。
- 外部官方源访问需要限速、重试和错误隔离。

## 10. 当前实现边界与技术债

### 10.1 服务边界仍偏原型化

多个服务独立运行，适合快速迭代，但部署和运维需要手动管理端口、环境变量和启动顺序。建议后续提供：

- `docker-compose.yml` 或 systemd unit 集合。
- 一键健康检查脚本。
- 统一 `.env.example`。
- 统一日志目录。

### 10.2 Tracking 存在两套概念

当前项目中有：

- 聚合后端本地 `agents/tracking/*`：提取跟踪事项、舆情、指标、预警的本地模块原型；默认文件路径已收口到 `/home/maoyd/wiki/companies/<公司>/tracking/`。
- 前端 `/tracking` 右侧 Hermes `finsight_tracking` Agent：面向对话的持续跟踪 Agent。

两者方向一致，但尚未完全统一。建议后续明确：

- Hermes tracking Agent 是否调用本地 TrackingAgent API。
- Tracking 报告、预警、事项应统一写入 `/home/maoyd/wiki/companies/<公司>/tracking/`。
- 是否以数据库替代当前 tracking markdown 文件目录。

### 10.3 PDF 解析服务单文件体量较大

`pdf2md_web/app.py` 承担了路由、队列、产物写入、质量构建、文件服务等大量职责。建议后续拆分：

- `routes/`：Flask 路由。
- `task_queue.py`：任务队列与 worker。
- `artifact_builder.py`：document_full、complete markdown、artifact status。
- `quality_service.py`：质量报告构建。
- `source_service.py`：PDF 页图、bbox、表格溯源。

### 10.4 Wiki 生成流程仍需标准化

当前语义抽取脚本依赖 Wiki 目录中已有 `report.md`、`report.json`、`document_full.json`。建议补齐从 `pdf2md_web/results/<task_id>` 到 `/home/maoyd/wiki/companies/<公司>/reports/<report_id>` 的标准同步脚本。

## 11. 后续演进建议

### 11.1 第一阶段：工程化收口

- 提供统一启动脚本或 docker-compose。
- 明确端口、环境变量、数据目录和日志目录。
- 将 README、设计文档、API 文档放入统一 docs 目录。
- 为 PDF 解析和语义抽取建立样本回归测试。

### 11.2 第二阶段：数据链路自动化

- 打通“下载 PDF -> 解析 -> 写 Wiki -> 语义抽取 -> 报告生成”的一键流水线。
- 用任务 ID 串联下载文件、解析结果、Wiki report_id 和 Agent 对话。
- 为 document_full 和 semantic 输出建立 schema registry。

### 11.3 第三阶段：Agent 工具化

- 让分析 Agent 可调用 Wiki 检索、财务指标读取、PDF 溯源接口。
- 让事实核查 Agent 输出带 evidence_id 的结论。
- 让 tracking Agent 写入结构化事项、指标阈值和预警状态。

### 11.4 第四阶段：生产级可信系统

- 权限、审计日志和用户体系。
- 多租户数据隔离。
- 报告版本管理和变更 diff。
- 批量公司覆盖和定时更新。
- 面向投资研究流程的任务看板。

## 12. 设计结论

FinSight 当前已经具备一个高价值金融 AI 系统的核心骨架：

- 上游有官方源财报获取能力。
- 中间有可审计 PDF 解析和财务结构化能力。
- 下游有 Wiki 语义沉淀和多 Agent 交互能力。
- 前端已经形成可用的统一工作台。

项目最大的亮点在于“可信链路”的意识：从官方 PDF 到财务数值、从表格 bbox 到语义事实、从质量报告到人工修正，每一步都尽量保留证据和中间产物。这使它比普通 RAG 问答、普通 PDF 转 Markdown、普通财报下载器都更接近可落地的金融研究基础设施。

后续重点不是重写，而是收口：统一启动、统一数据流、统一 schema、统一追踪 ID，并把已有强能力沉淀成稳定的流水线和 Agent 工具接口。
