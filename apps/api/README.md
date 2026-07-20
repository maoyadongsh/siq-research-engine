# SIQ API 聚合后端

## 模块定位

`apps/api` 是 SIQ Research Engine 的控制面后端。它不只是一个 CRUD API，而是整个系统的统一入口层：前面接 Web 工作台，后面连接 PDF 解析、通用文档解析、市场下载服务、多市场规则服务、Wiki 文件系统、PostgreSQL / Milvus 工作流和 Hermes 智能体。

它的职责不是“替代底层服务”，而是把这些能力包装成带鉴权、带归属、带状态、带审计的统一研究接口。

## 产品归属与业务边界

`apps/api` 同时服务三条产品面，但自身定位始终是控制面，不直接替代 parser、rules、Hermes 或模型服务。

| 产品面 | API 职责 | 关键价值 |
| --- | --- | --- |
| 二级市场投研分析智能体集群 | 统一暴露搜索下载、财报解析、market package、source access、分析/核查/跟踪/法务 Agent、PostgreSQL / Milvus 动作 | 让研究员从披露到报告复核的链路在同一鉴权和审计边界内发生 |
| 一级市场投研决策智能体集群 | 承载 Deal OS、材料中心、证据对象、专家任务、R0-R4 工作流、争议、决策和审计 | 把投委会过程从散落文档转成可回放状态机 |
| 应用中心 | 代理文档解析、会议转写、会议导入/导出、声纹/术语、向量入库和系统设置 | 让材料生产和知识沉淀能力被两大智能体集群复用 |

OpenShell 相关代码也位于 API 控制面边界内。API 负责运行面选择、公司上下文验证、范围自动创建、对话沙箱代际、资源池租约、隔离、重启恢复、空闲 TTL 清理和 Host 回退语义，但不会把 `NO_GO` 的 OpenShell 灰度链路当成默认生产运行面。正式切流必须由 OpenShell 完成度门禁、质量 A/B、人工架构/安全评审和正式生产门禁共同放行。

## 在系统中的位置

```text
apps/web / external client
  -> apps/api
     -> apps/pdf-parser
     -> apps/document-parser
     -> services/market-report-finder
     -> services/market-report-rules
     -> data/wiki / PostgreSQL / Milvus / Hermes
```

在这条链路中，API 后端承担三层责任：

- 统一入口：屏蔽下游服务的路径差异、端口差异和鉴权差异。
- 统一治理：对任务、artifact、下载文件、报告和 source 链接做用户归属与访问控制。
- 统一编排：把下载、解析、导入、Agent 会话、系统状态和设置管理串成同一套前端心智。

## 核心能力

| 能力 | 说明 |
| --- | --- |
| 鉴权与用户治理 | 登录、注册、用户审批、权限判断、审计入口 |
| 解析服务代理 | 对 PDF 解析和通用文档解析做统一鉴权代理与任务归属绑定 |
| 多市场入口 | 统一承接市场下载、evidence package 构建、后台 job 和导入动作 |
| 一级市场 Deal OS | 项目、材料、证据、专家工作流、争议、决策、审计与投后接口 |
| 会议智能化 | 会话、实时流、导入、说话人、术语/声纹、纪要、导出、回放与原生采集协议 |
| Wiki / 报告访问 | 读取分析、核查、跟踪、法务及市场 package 产物 |
| Agent 代理 | 对 Hermes 提供会话、附件、SSE 输出、停止与恢复能力 |
| 拟人化记忆控制 | 串联 Hermes 原生会话记忆、本地临时任务记忆、PostgreSQL 权威长期记忆、Milvus 语义索引和 reranker |
| OpenShell 运行面选择 | 对 `siq_analysis` 提供 Host / OpenShell 灰度路由、范围自动创建、对话代际、资源池租约/隔离/恢复、TTL 回收和失败关闭回退 |
| Source 访问控制 | 为 PDF 页图、source map、artifact、下载文件提供安全访问入口 |
| 工作流编排 | 驱动 Wiki、PostgreSQL、Milvus 等下游导入链路 |
| 系统设置与健康面板 | 汇总模型配置、下游健康和基础系统状态 |

## 当前最新状态

| 方向 | 状态 | 价值 |
| --- | --- | --- |
| Cookie 会话 | 支持 `SIQ_AUTH_COOKIE_MODE=1`，登录接口设置 HttpOnly cookie，前端请求自动 `credentials: include` | 兼容本地 bearer token，同时降低公网部署时 localStorage token 被窃取的风险 |
| Market package 动作 | `/api/market-reports/packages/*` 统一处理 build、import、vector dry-run 与后台 job | 把多市场 evidence package 做成可审计、可恢复的产品动作 |
| 质量门禁 | warning/fail package 未 force 时返回 409，并携带 `quality_gates` | 防止低质量解析静默污染 PostgreSQL 或语义索引 |
| Source 安全 | 下载文件、artifact、PDF 页图、source map 通过受控 API 访问 | 保留证据回跳能力，同时避免裸露本机路径 |
| Deal OS / IC 工作流 | `/api/deals/*`、会议室、agent runtime 与 readiness 接入 | 支撑一级市场 R1-R4 尽调、分歧和投委会决策链 |
| 记忆系统 | Hermes 原生会话记忆 + 本地临时任务记忆 + PostgreSQL 权威长期记忆 + Milvus 语义索引 + reranker | 支撑拟人化连续性、全量记忆、半衰期衰减、按需全量召回和用户/项目/系统隔离 |
| OpenShell 控制面 | `openshell_pool_adapter`、`openshell_scope_lifecycle`、运行协调、资源池恢复与运行路由接入 | 支撑 NVIDIA OpenShell + Hermes 演示/灰度链路：按公司范围自动建沙箱、同对话代际复用/隔离、请求级租约、API 重启恢复和空闲 TTL 删除；正式生产门禁仍为 `NO_GO` |

API 后端的商业价值在于把底层复杂能力包装成“可卖给研究组织的治理面”：权限、审计、质量阻断、任务状态、文件访问和智能体调用都在同一个控制面闭环中完成。

## 高精度问答控制链

`apps/api` 是 SIQ “极致高精度”真正收口的地方。parser 和 rules 负责生产高质量事实，API 负责保证智能体最终使用的事实、计算与引用没有在最后一公里失真。

```text
用户问题 / 图片 / 文档 / 语音
  -> 会话、用户、profile、附件归属校验
  -> market/company/filing/parse run 身份解析
  -> LLM-Wiki logical route first / PostgreSQL fallback / independent Milvus semantic retrieval
  -> 主表与附注按问题类型分路
  -> Hermes 运行 + 记忆上下文 + 受控工具
  -> citation normalization / financial trace extraction
  -> trusted evidence 对齐 + Decimal 重算 + answer audit
  -> SSE 最终回答、source links、validation cards、runtime receipt
```

关键实现分工：

| 实现面 | 代表模块 | 保障内容 |
| --- | --- | --- |
| 问题与证据上下文 | `agent_runtime_context.py`、`agent_runtime_wiki_context.py`、`agent_runtime_statement_context.py` | 公司/市场/报告身份、三大表与附注路由、上下文预算 |
| 结构化事实兜底 | `agent_runtime_market_facts.py`、`agent_runtime_postgres_fallback.py` | Wiki 不足时从市场隔离 PostgreSQL agent view 补证，并保留真实 source type |
| 引用与来源 | `agent_runtime_citations.py`、`agent_runtime_financial_sources.py`、`citation_links.py` | evidence ID、PDF 页码、table/anchor/bbox、受控 source URL |
| 财务守卫 | `agent_runtime_financial_trace.py`、`agent_runtime_financial_claim_verifier.py`、`agent_runtime_financial_guard.py` | trace schema、证据绑定、单位/币种/期间检查、确定性重算、错误阻断 |
| 回答审计 | `agent_runtime_answer_audit.py`、`agent_runtime_financial_provenance.py` | 保存最终回答使用过的证据、计算回执、运行信息和失败原因 |

“高精度”在这里不等于强制输出数字。当权威事实、必要期间或计算 trace 不完整时，正确结果是降级、N/A、明确缺口或要求复核，而不是生成一个看似精确的值。

## 记忆控制与事实隔离

API 将长期记忆作为独立的可治理数据域，而不是把历史对话直接拼进 prompt：

- `agent_memory_service.py` 管理权威 PostgreSQL memory item、message、scope、来源、importance/confidence 和 ResearchIdentity。
- `agent_memory_milvus.py` 管理 Milvus 可重建索引；也保留 pgvector backend 选择，不让向量库成为唯一事实源。
- 默认召回在 rerank 后应用 30 天半衰期；显式“全量检索/完整历史”请求绕过时间衰减，但仍受 ACL、scope、数量上限和上下文预算保护。
- `user_private` 需要用户归属；`project_shared` 需要项目/Deal 范围；`system_shared` 仍按 agent group/profile 约束。一级市场缺少 project/deal context 时不创建项目共享记忆。
- 研究记忆可带完整 `market/company_id/filing_id/parse_run_id`，防止旧报告记忆污染当前报告事实。

记忆只回答“我们之前如何协作、曾经记录了什么”，evidence package 和当前数据库事实才回答“本期披露究竟是什么”。

## 本地多模态接入

| 输入 | API 处理 | 下游 |
| --- | --- | --- |
| 图片附件 | 白名单 MIME、大小与所有权校验，保存到受控 chat root；以 OpenAI vision `image_url` data URL 调用本机 `Nemotron 3 Nano Omni` | 返回文字/数字/表格/图表初步分析，随后交给 Hermes 结合问题与证据作答 |
| PDF/Office/文本附件 | PDF 等待 parser artifact，Office/文本做有界预览，保留本地 artifact 引用 | 文档解析、source map、Hermes 附件上下文 |
| 短语音 | WebM/OGG/M4A/MP3/WAV/AAC 白名单，FFmpeg 归一为 16 kHz mono WAV，限制 60 秒/10 MiB | FunASR 转写后进入普通聊天，同时保留音频附件归属 |
| 长会议音频 | 一次性 ticket、WebSocket gateway、持久 frame/segment/event、finalization worker | meeting-speech、Hermes 纪要/行动项、回放与导出 |

图片链路默认 `SIQ_IMAGE_MODEL_BASE_URL=http://127.0.0.1:8007/v1`，本机服务不可用时显式返回 fallback 状态并交给 Hermes，不会把失败吞成空分析。会议链路和普通短语音链路相互隔离，避免一个服务的延迟、权限或留存策略污染另一个入口。

## 双主模型与并发协调

云端 StepFun `step-3.7-flash` 与本地 Nemotron `nemotron_3_nano_omni` 是当前双主模型。`hermes_model_control.py` 负责把设置、用户显式切换、profile config 和 fallback 顺序解析成稳定 provider/model；运行回执保留实际来源。会议任务使用 immutable target snapshot，创建后不受全局设置变化影响。

问答请求可并发准备 LLM-Wiki 逻辑路由命中的精确事实、PostgreSQL 兜底事实、Milvus 向量候选、长期记忆、附件图片分析和文档 artifact；Qwen reranker 只作用于 Milvus/记忆等语义候选，不重排 Wiki 已按身份、主题、对象 ID 和附注关系确定的权威事实。各分支再按证据优先级裁剪和组装，并绑定 session/user/ResearchIdentity，晚到的旧 scope 结果不能覆盖当前请求。上游任一模型/数据服务失败时只降级相应能力，不应把整个请求静默切成无证据聊天。

LLM-Wiki 的 `semantic/retrieval_index.json` 是逻辑查询索引，不是 embedding 索引。API 依次通过公司/报告身份、topic alias、priority files、fact/claim/evidence IDs、`document_links`/`note_links` 和全文 source coordinates 跳转；这一主路径不调用 embedding、reranker 或 Milvus。这样可以避免传统 RAG 切片对跨页表格、报告期、单位和主表/附注关系的破坏。

API 不直接管理 GPU 显存，但通过输入上限、timeout、candidate limit、job/stream 状态、meeting backpressure 和模型 readiness 把 DGX Spark 的并发资源暴露为有界服务。实际容量仍由各独立 vLLM manager 的 context/sequence/token/memory budget 和整机压力测试决定。

## 技术难点

`apps/api` 的难点不在于定义几十个路由，而在于控制面如何在不破坏下游职责边界的前提下统一系统行为：

- 多下游服务并存：Flask、FastAPI、Wiki 文件、Hermes gateway、PostgreSQL、Milvus 同时存在，接口风格并不统一。
- 资产归属严格：artifact、聊天附件、报告产物、下载文件和 source 链接都必须和用户归属、权限和会话状态绑定。
- 长任务可观测：解析、下载、导入、Agent 运行都不是即时 RPC，需要统一 job / status / stream 语义。
- 证据访问受控：PDF 页图、source 表格、报告 HTML、结构化 JSON 都要可读，但不能裸露底层路径。
- 智能体接入复杂：API 要把多 profile、多端口、多种输出产物抽象成一致的前端体验。

## 关键接口或标准产物

### 关键路由分组

| 分组 | 前缀 | 用途 |
| --- | --- | --- |
| 健康检查 / 指标 | `/health` `/metrics` | 服务存活、基础状态与 Prometheus text 指标 |
| 鉴权 | `/api/auth/*` | 登录、注册、当前用户、管理员用户流程 |
| Wiki / 报告 | `/api/wiki/*` | 公司、报告、报告文件和管理动作 |
| 通用聊天 | `/api/chat/*` | 助手会话、附件、SSE 输出、运行控制 |
| 专业 Agent | `/api/analysis/*` `/api/factchecker/*` `/api/tracking/*` `/api/legal/*` | 专业 profile 代理 |
| PDF / Source | `/api/source/*` `/api/pdf_page/*` `/api/source_access/*` | 页面、表格、短期签名访问 |
| 通用文档 | `/api/documents/*` | 文档解析任务、artifact、抽取与来源访问 |
| 工作流 | `/api/workflow/*` | Wiki、PostgreSQL、Milvus 导入调度 |
| 市场报告 | `/api/market-reports/*` `/api/us-sec/*` `/api/jobs/*` | 多市场 package、后台 job、入库动作 |
| 一级市场 | `/api/deals/*` `/api/primary-market/*` | 材料、证据、R0-R4、争议、决策、审计与投后工作流 |
| 会议 | `/api/meetings/v1/*` | 会话、转写、说话人、词库、声纹、产物、任务、音频与导出 |
| 设置 / 状态 | `/api/settings/*` `/api/system/*` | 模型设置、系统状态、连通性测试 |

### 核心对接对象

| 对象 | 默认地址 / 目录 |
| --- | --- |
| PDF 解析服务 | `http://127.0.0.1:15000` |
| 通用文档解析服务 | `http://127.0.0.1:15010` |
| 市场公告下载服务 | `http://127.0.0.1:18000` |
| 多市场规则服务 | `http://127.0.0.1:18020` |
| Hermes home | `data/hermes/home` |
| Wiki 根目录 | `data/wiki` |
| 下载目录 | `data/market-report-finder/downloads` |

## 启动方式

### 开发启动

```bash
cd /home/maoyd/siq-research-engine/apps/api
export SIQ_AUTH_SECRET_KEY="$(openssl rand -hex 32)"
./start.sh
```

### 手动启动

```bash
cd /home/maoyd/siq-research-engine/apps/api
uv sync --extra dev
export SIQ_AUTH_SECRET_KEY="$(openssl rand -hex 32)"
uv run python -m uvicorn main:app --host 0.0.0.0 --port 18081 --reload
```

### 常用健康检查

```bash
curl -s http://127.0.0.1:18081/health
curl -s http://127.0.0.1:18081/metrics | head
curl -s http://127.0.0.1:18081/api/system/status
```

## 关键环境变量

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `SIQ_BACKEND_PORT` | `18081` | API 监听端口 |
| `SIQ_AUTH_SECRET_KEY` | 无 | 鉴权密钥，必须设置 |
| `SIQ_SOURCE_TOKEN_SECRET` | 回退到 `SIQ_AUTH_SECRET_KEY` | source access token 签名密钥 |
| `SIQ_DATA_ROOT` | `$PROJECT_ROOT/data` | 历史兼容运行态根目录 |
| `SIQ_RUNTIME_ROOT` | `$PROJECT_ROOT/var` | 新增运行态推荐根目录 |
| `SIQ_WIKI_ROOT` | `$SIQ_DATA_ROOT/wiki` | 文件型事实层目录 |
| `SIQ_REPORT_DOWNLOADS_ROOT` | `$SIQ_DATA_ROOT/market-report-finder/downloads` | 官方披露文件目录 |
| `SIQ_PDF2MD_API_BASE` | `http://127.0.0.1:15000` | PDF 解析服务 |
| `SIQ_DOCUMENT_PARSER_API_BASE` | `http://127.0.0.1:15010` | 通用文档解析服务 |
| `SIQ_REPORT_FINDER_BASE` | `http://127.0.0.1:18000` | 市场下载服务 |
| `SIQ_MARKET_REPORT_RULES_BASE` | `http://127.0.0.1:18020` | market rules 服务 |
| `SIQ_HERMES_HOME` | `$SIQ_DATA_ROOT/hermes/home` | Hermes runtime 根目录 |
| `SIQ_AUTH_COOKIE_MODE` | `0` | 启用 HttpOnly cookie 兼容模式 |
| `SIQ_AUTH_ACCESS_COOKIE_NAME` | `siq_access_token` | access cookie 名称 |
| `SIQ_AUTH_COOKIE_SAMESITE` | `lax` | cookie SameSite 策略 |
| `SIQ_AUTH_COOKIE_SECURE` | `0` | HTTPS 公网部署应设为 `1` |
| `SIQ_IMAGE_MODEL_ENABLED` | `true` | 启用本地原生图片理解 |
| `SIQ_IMAGE_MODEL_BASE_URL` | `http://127.0.0.1:8007/v1` | Nemotron/OpenAI-compatible vision 地址 |
| `SIQ_IMAGE_MODEL` | 自动读取 `/models` | 图片模型名，规范部署为 `nemotron_3_nano_omni` |
| `SIQ_FUNASR_BASE_URL` | `http://127.0.0.1:8899/asr` | Chat 短语音转写服务 |
| `SIQ_AGENT_MEMORY_ENABLED` | `true` | 长期记忆总开关 |
| `SIQ_AGENT_MEMORY_TIME_DECAY_HALF_LIFE_DAYS` | `30` | 默认召回时间衰减半衰期 |
| `SIQ_AGENT_MEMORY_RERANK_ENABLED` | `true` | 记忆候选精排开关 |
| `SIQ_HERMES_RUNTIME` | `host` | Host/OpenShell 运行面选择；生产门禁前保持 Host |

## 基础环境与测试情况

API 后端建议在 Python `>=3.11` 环境运行，当前工作机采样为 Python `3.13.12`、uv `0.11.7`、Docker `29.1.3`。API 自身依赖 FastAPI、SQLModel、SSE、Redis/PostgreSQL/Milvus 相关客户端和多个下游 HTTP 服务；跨机器部署时以根 README 的环境表、`infra/env/local.example` 和各下游 README 为准。

| 测试面 | 命令 | 覆盖重点 |
| --- | --- | --- |
| API 单元/集成 | `uv run python -m pytest tests` | 鉴权、路由、Agent runtime、Deal OS、会议、market package、source access |
| Shell 入口 | `bash -n start.sh` | 启动脚本语法和基础环境变量 |
| FastAPI 导入 | `uv run python -c "import main; print(main.app.title)"` | 依赖解析、应用对象初始化 |
| OpenShell 控制面专项 | 见 `docs/siq-openshell-hermes-integration-status.md` | 最新记录 `78 passed`，覆盖运行面选择、资源池绑定、租约、范围自动创建、对话代际、TTL、恢复和 Host 回退 |

README 或文案变更通常只需要 Markdown 检查；如果改动 `services/openshell_*`、Agent stream、source access、Deal OS、会议或 package gate，应补跑对应测试并手动检查 `/health`、`/api/system/status` 和相关业务路由。

## 验证方式

```bash
cd /home/maoyd/siq-research-engine/apps/api
uv run python -m pytest tests
bash -n start.sh
uv run python -c "import main; print(main.app.title)"
```

若修改了代理、任务或路由聚合逻辑，至少补跑对应测试并手动检查 `/health` 与 `/api/system/status`。

## 维护原则

- API 负责统一入口，不负责把下游服务的全部业务逻辑重写一遍。
- 路径与运行态目录统一通过环境变量或 path config 解析，避免硬编码本机绝对路径。
- 与 artifact、source、下载文件相关的接口必须保留路径白名单与用户归属校验。
- 对 Hermes 的代理必须保持可停止、可恢复、可审计的流式语义。
- 新增 API 时优先补充 README、测试和前端调用方，避免出现“后端有能力但系统不可发现”的黑箱路径。

## 技术创新与商业价值

API 层不是传统 CRUD 网关，而是研究生产线的控制面。它把长任务、文件证据、模型流式输出、权限与人工确认组合为同一套可审计状态机。

| 创新点 | 工程实现 | 商业意义 |
| --- | --- | --- |
| 证据优先 API | 答案审计 trace、source token、artifact 路由与 package gate | 客户可以从结论回放到原文件、页码和结构化事实 |
| 多工作流统一编排 | PDF/文档 package、市场入库、Deal R0-R4、会议任务共用 job/status/event 模式 | 降低不同业务线重复建设控制面的成本 |
| 人机共治 | `force`、review、human confirmation、权限依赖与审计日志 | 高风险导入和投资决策不会被模型静默越权完成 |
| 本地模型解耦 | Hermes、解析器、语音与检索服务通过稳定 HTTP/文件合同连接 | 支持私有化部署及模型替换，降低供应商锁定 |

最难的部分是跨边界一致性：后台进程重启、流式连接中断、任务取消、重复请求和文件移动都不能破坏任务状态与证据引用。因此新增接口必须同时考虑幂等、路径白名单、鉴权、可恢复状态和产物可读性。
