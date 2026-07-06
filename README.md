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

### 4. 受控多智能体协作

Hermes profiles 不以“人格化助手”方式组织，而以研究职责组织。分析、核查、跟踪、法务和投委会角色围绕同一证据层协作，但各自承担不同任务和边界，避免幻觉式越权输出。

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
| 运维与编排 | Docker Compose、systemd user units、shell scripts | 本地服务编排和模型服务管理 |

## 仓库地图

| 路径 | 职责 |
| --- | --- |
| `apps/web` | Web 工作台，承载下载、解析、报告与 Agent 协作入口 |
| `apps/api` | API 聚合后端，统一鉴权、代理、任务和系统状态 |
| `apps/pdf-parser` | 财报 PDF 解析、质量门禁、财务抽取与溯源 |
| `apps/document-parser` | 通用文档解析、artifact 归一、Schema 抽取 |
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
curl -s http://127.0.0.1:15000/api/health
curl -s http://127.0.0.1:15010/api/health
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
| `SIQ_DATA_ROOT` | `data` | 历史兼容运行态根目录 |
| `SIQ_RUNTIME_ROOT` | `var` | 新增本地运行态建议根目录 |
| `SIQ_ARTIFACTS_ROOT` | `artifacts` | 生成产物目录 |
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

## 延伸阅读

- [API 聚合后端](apps/api/README.md)
- [PDF 解析服务](apps/pdf-parser/README.md)
- [通用文档解析服务](apps/document-parser/README.md)
- [Web 工作台](apps/web/README.md)
- [统一市场公告搜索下载服务](services/market-report-finder/README.md)
- [多市场财报规则服务](services/market-report-rules/README.md)
- [共享 evidence package contract](packages/market-contracts/README.md)
- [Hermes 智能体体系](agents/hermes/README.md)
- [PostgreSQL 入库工具](db/imports/README.md)
- [本地开发操作说明](docs/operations/local-development.md)
