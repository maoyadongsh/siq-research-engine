# SIQ API 聚合后端

## 模块定位

`apps/api` 是 SIQ Research Engine 的控制面后端。它不只是一个 CRUD API，而是整个系统的统一入口层：前面接 Web 工作台，后面连接 PDF 解析、通用文档解析、市场下载服务、多市场规则服务、Wiki 文件系统、PostgreSQL / Milvus 工作流和 Hermes 智能体。

它的职责不是“替代底层服务”，而是把这些能力包装成带鉴权、带归属、带状态、带审计的统一研究接口。

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
| Wiki / 报告访问 | 读取分析、核查、跟踪、法务及市场 package 产物 |
| Agent 代理 | 对 Hermes 提供会话、附件、SSE 输出、停止与恢复能力 |
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
| 记忆系统 | PostgreSQL 权威记忆 + Milvus 语义索引 + profile / deal scope | 支撑用户私有记忆、项目共享记忆和系统共享知识的隔离召回 |

API 后端的商业价值在于把底层复杂能力包装成“可卖给研究组织的治理面”：权限、审计、质量阻断、任务状态、文件访问和智能体调用都在同一个控制面闭环中完成。

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
| 健康检查 | `/health` | 服务存活与基础状态 |
| 鉴权 | `/api/auth/*` | 登录、注册、当前用户、管理员用户流程 |
| Wiki / 报告 | `/api/wiki/*` | 公司、报告、报告文件和管理动作 |
| 通用聊天 | `/api/chat/*` | 助手会话、附件、SSE 输出、运行控制 |
| 专业 Agent | `/api/analysis/*` `/api/factchecker/*` `/api/tracking/*` `/api/legal/*` | 专业 profile 代理 |
| PDF / Source | `/api/source/*` `/api/pdf_page/*` `/api/source_access/*` | 页面、表格、短期签名访问 |
| 通用文档 | `/api/documents/*` | 文档解析任务、artifact、抽取与来源访问 |
| 工作流 | `/api/workflow/*` | Wiki、PostgreSQL、Milvus 导入调度 |
| 市场报告 | `/api/market-reports/*` `/api/us-sec/*` `/api/jobs/*` | 多市场 package、后台 job、入库动作 |
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
curl -s http://127.0.0.1:18081/api/system/status
```

## 关键环境变量

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `SIQ_BACKEND_PORT` | `18081` | API 监听端口 |
| `SIQ_AUTH_SECRET_KEY` | 无 | 鉴权密钥，必须设置 |
| `SIQ_SOURCE_TOKEN_SECRET` | fallback 到 `SIQ_AUTH_SECRET_KEY` | source access token 签名密钥 |
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
