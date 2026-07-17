# SIQ Web 工作台

## 模块定位

`apps/web` 是 SIQ 的主交互入口。它不是接口调试页，也不是一组分散的后台表单，而是把官方披露下载、财报解析、通用文档解析、质量复核、报告阅读、证据回跳、向量入库和智能体协作串成同一套研究工作台。

在使用者视角里，Web 工作台的价值不只是“能看数据”，而是让完整研究链路在一个界面里连续发生。

## 在系统中的位置

```text
用户
  -> apps/web
     -> apps/api
        -> parser / finder / rules / wiki / postgres / milvus / hermes
```

`apps/web` 的核心作用是把控制面能力变成可操作的产品化流程：

- 选择市场、公司和报告。
- 触发解析、下载、导入和检查动作。
- 查看 quality report、source map、表格、页图和报告。
- 打开不同研究角色的 Agent 对话与产物。

## 核心能力

| 能力 | 说明 |
| --- | --- |
| 搜索与下载入口 | 统一承接 CN / HK / US / EU / JP / KR 官方披露搜索与下载 |
| 解析工作台 | 承载 A 股 PDF、多市场 package 和通用文档解析工作流 |
| 证据可视化 | 支持 Markdown、artifact、PDF 页图、source map、质量报告和任务状态联动展示 |
| 报告阅读 | 分析、核查、跟踪、法务和其他 HTML/JSON/Markdown 产物统一阅读 |
| 智能体协作 | 助手、分析、核查、跟踪、法务和 `/deals` 相关角色入口 |
| 系统与运维面板 | 模型设置、健康状态、用户审批和向量入库控制台 |

## 当前最新状态

| 工作流 | 页面 | 当前能力 |
| --- | --- | --- |
| 官方披露搜索下载 | `/search` | 市场优先的智能检索；中文公司名在所选市场内映射本地代码，US 已覆盖 100 家主流美股 alias；解析失败时提示输入准确股票代码或代号 |
| 港股商业 MVP | `/parse-hk` | 已下载年报 / 上传 PDF -> parser -> 解析产物 -> PostgreSQL 入库；Wiki 由解析产物派生 |
| 质量门禁 | `/parse-hk` package 面板 | 展示 evidence coverage、statement coverage、hash、parser/rule warnings；warning/fail 需要确认后才发送 `force=true` |
| 美股 SEC 工作台 | `/parse-us` | 下载列表、SEC package build、PostgreSQL import 和核心 artifact 清单独立呈现 |
| 通用文档解析 | `/documents` | 上传、URL、MinerU 目录导入、source map、table relation、schema extraction |
| Cookie mode | 全站 API 调用 | `SIQ_AUTH_COOKIE_MODE=1` 时不再持久化 JWT 到 localStorage，SIQ API 自动带 cookie |
| 一级市场 Deal OS | `/primary-market` `/deals/*` | 项目、材料中心、数据室、证据、agent readiness、争议、R0-R4 工作流、决策确认与审计回放 |
| 会议智能化 | `/meetings/*` | 新建/导入会议、实时转写、说话人、术语库、声纹、纪要/行动项、音频回放与版本化导出；由 feature gate 控制入口 |

Web 工作台的商业价值是把“研究生产线”做成研究员能真实操作的流程：从官方文件到质量复核、从证据包到入库动作、从报告阅读到多角色协作，都不是隐藏在脚本里的能力。

## 技术难点

Web 工作台的难点不在“页面多”，而在“把复杂研究系统做成可操作产品”：

- 链路长：下载、解析、规则、导入、报告和 Agent 都是异步、多阶段任务，前端必须稳定呈现状态。
- 证据密度高：一个结论往往需要同时联动 Markdown、表格、页图、JSON artifact 和 source 坐标。
- 多市场差异大：A 股 PDF 解析、美股 SEC package、欧股 ESEF、日股 EDINET、韩股 DART 并不是一套完全相同的页面逻辑。
- 权限与安全要求高：artifact、source、报告 iframe 和下载文件不能直接暴露底层路径。
- 角色入口多：研究员、管理员、普通用户、Agent profile 和 `/deals` 角色需要共处同一产品壳层。

## 关键接口或标准产物

### 主要页面

| 路由 | 页面 | 主要能力 |
| --- | --- | --- |
| `/` | 我的工作台 | 个人资产、近期任务、快捷入口 |
| `/search` | 搜索下载 | 公司解析、官方披露检索与批量下载 |
| `/parse` | A 股财报解析 | 上传、任务、质量、财务抽取、导入工作流 |
| `/parse-hk` `/parse-us` `/parse-eu` `/parse-jp` `/parse-kr` | 多市场解析 | package 构建、质量、入库与市场专属入口 |
| `/documents` | 通用文档解析 | 上传、URL 导入、artifact、source map、schema extraction |
| `/meetings` `/meetings/:meetingId` | 会议智能化 | 会话、实时/导入转写、说话人、术语与声纹、纪要、回放和导出 |
| `/primary-market` | 一级市场工作台 | 项目入口、材料中心、投研决策与投后管理 |
| `/deals/:dealId/*` | Deal OS | 数据室、证据、智能体、工作流、报告、决策与审计 |
| `/analysis` | 智能分析 | 分析报告阅读与分析 Agent |
| `/verify` | 事实核查 | 核查报告阅读与核查 Agent |
| `/tracking` | 持续跟踪 | 跟踪事项、预警与跟踪 Agent |
| `/legal` | 法务合规 | 法规型检索与法律意见草稿入口 |
| `/chat` | 助手问答 | 通用聊天、附件、流式输出 |
| `/vector-ingest` | 向量入库 | Milvus / Gradio 控制台入口 |
| `/settings` | 设置 | 模型设置与系统状态 |

### 主要代理前缀

| 前端前缀 | 代理目标 | 用途 |
| --- | --- | --- |
| `/api/auth` | `apps/api` | 鉴权、用户、权限 |
| `/api/chat` | `apps/api` | 助手聊天与会话 |
| `/api/wiki` | `apps/api` | 报告与公司数据 |
| `/api/analysis` `/api/factchecker` `/api/tracking` `/api/legal` | `apps/api` | 专业 Agent 代理 |
| `/api/documents` | `apps/api` | 通用文档解析工作流 |
| `/api/workflow` | `apps/api` | Wiki / PostgreSQL / Milvus 导入工作流 |
| `/api/market-reports` `/api/us-sec` `/api/jobs` | `apps/api` | 多市场 package、后台 job |
| `/api/source` `/api/pdf_page` | `apps/api` | source 访问和页图 |
| `/api/pdf` | `apps/api` | 受保护的 PDF 解析代理 |
| `/api/*` | `services/market-report-finder` 兜底 | 市场下载兜底代理 |

聊天和专业 Agent 使用 SSE 长连接。生产反向代理必须把 `/api/chat`、`/api/analysis`、`/api/factchecker`、`/api/tracking`、`/api/legal` 等后端前缀转发到 `apps/api`，关闭响应缓冲，并将读写超时设置为不小于后端 1800 秒的运行上限。仓库的 `nginx.conf.template` 已固定为 `1900s`；外部 Synology/Nginx 配置不会随本仓库自动重载，发布后需单独核对上游端口、HTTP/1.1、SSE `Content-Type` 和超时。不要把 `/api/chat/stream` 的 GET 误当成健康检查，它只接受 POST。

## 启动方式

### 开发启动

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm install
SIQ_FRONTEND_PORT=15173 npm run dev -- --host 0.0.0.0
```

默认地址：

```text
http://127.0.0.1:15173
```

### 构建与测试

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm run lint
npm run test:unit
npm run build
npm run e2e
```

### 公网 HMR 示例

```bash
cd /home/maoyd/siq-research-engine/apps/web
SIQ_PUBLIC_HOST=arthurmao.synology.me \
SIQ_PUBLIC_HMR_PROTOCOL=wss \
SIQ_PUBLIC_HMR_CLIENT_PORT=8276 \
SIQ_FRONTEND_PORT=15173 npm run dev -- --host 0.0.0.0
```

## 关键环境变量

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `SIQ_FRONTEND_PORT` | `15173` | 前端开发端口 |
| `PLAYWRIGHT_BASE_URL` | 空 | E2E 测试访问入口 |
| `SIQ_PUBLIC_HOST` | 空 | 对外 HMR 主机名 |
| `SIQ_PUBLIC_HMR_PROTOCOL` | 空 | HMR 协议 |
| `SIQ_PUBLIC_HMR_CLIENT_PORT` | 空 | HMR 客户端端口 |
| `VITE_SIQ_DEMO_LOGIN_DEFAULTS` | `0` | 演示环境默认登录表单填充开关 |
| `VITE_SIQ_LOGIN_DEFAULT_USERNAME` | 空 | 演示用户名覆盖 |
| `VITE_SIQ_LOGIN_DEFAULT_PASSWORD` | 空 | 演示密码覆盖 |
| `VITE_SIQ_AUTH_COOKIE_MODE` | 空 | 构建期 cookie mode 开关 |
| `VITE_SIQ_MEETINGS_ENABLED` | `0` | 会议页面和导航的构建期 feature gate |
| `SIQ_AUTH_COOKIE_MODE` | 空 | 运行时 cookie mode 兼容开关 |

## 验证方式

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm run lint
npm run test:unit
npm run build
```

如果改动了路由、工作台布局或关键交互，额外运行：

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm run e2e
npm run e2e -- e2e/tests/secondary-market-mvp-flow.spec.ts
```

## 维护原则

- 优先把复杂链路做成稳定工作流，而不是把每个后端能力都单独暴露成散乱按钮。
- 财报解析、通用文档解析和市场 package 页面要共享视觉语言，但不能抹平它们的业务差异。
- artifact、报告 iframe、下载文件和页图都必须通过受控 API 访问。
- 新增市场入口时优先复用现有页面骨架和状态心智，再补市场专属模块。
- 不提交 `node_modules`、`dist`、Playwright 产物或本地调试缓存。

## 产品创新与商业价值

Web 工作台把复杂研究链路压缩成可操作的证据工作台。研究员看到的不只是模型答案，还能在同一上下文中检查原文、解析块、表格、质量状态、智能体阶段和人工决策。

| 工作区 | 关键交互 | 业务价值 |
| --- | --- | --- |
| 二级市场 | 市场内搜索、下载、解析、指标、分析、核查、跟踪、法务 | 从资料获取到投研复核形成连续桌面 |
| 一级市场 | 项目、材料、数据室、证据、专家、争议、报告、决策、审计 | 把投委会过程产品化，而非只呈现最终结论 |
| 文档与会议 | 结构审阅、表格关系、实时转写、说话人、术语、回放、导出 | 将非结构化材料快速转成可复用研究资产 |
| 管理控制 | RBAC、用户审批、模型设置、系统状态、向量入库 | 满足机构部署中的权限、运维和治理要求 |

前端坚持 route-level lazy loading、请求所有权隔离、受控 HTML/PDF 展示和 feature gate。技术难度来自高密度数据、跨页面任务状态和安全展示的组合，而不是普通表单渲染。
