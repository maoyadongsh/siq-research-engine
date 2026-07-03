# SIQ Web 工作台

`apps/web` 是 SIQ Research Engine 的 React/Vite 前端。它不是接口调试页，而是研究员和管理员使用整套系统的主界面：官方公告下载、A/HK/US/EU/JP/KR 财报解析、通用文档解析、报告阅读、证据溯源、Agent 对话、用户审批、系统状态和向量入库都在这里闭环。

## 产品定位

Web 工作台强调三件事：

- 研究流程连续：从公司解析、官方披露下载、解析任务、质量复核、入库到报告生成，不让用户在目录和脚本之间来回找。
- 证据可见：报告、Markdown、PDF 页图、表格、source map、质量告警和 Agent 对话并排协作。
- 长任务可控：解析、导入、向量化、SEC/EU 包构建等任务都有状态、日志、错误、重试和刷新入口。

## 启动

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm install
SIQ_FRONTEND_PORT=15173 npm run dev -- --host 0.0.0.0
```

打开：

```text
http://localhost:15173
```

构建、检查和 E2E：

```bash
npm run lint
npm run test:unit
npm run build
npm run e2e
```

普通开发默认前端端口为 `15173`，可通过 `SIQ_FRONTEND_PORT` 覆盖。E2E 默认使用独立端口 `15174`，并可通过 `SIQ_FRONTEND_PORT` 或 `PLAYWRIGHT_BASE_URL` 覆盖；详见 [`e2e/README.md`](./e2e/README.md#端口配置)。

普通构建的 `/login` 不预填账号密码。受控演示环境如需默认填充管理员账号，可设置 `VITE_SIQ_DEMO_LOGIN_DEFAULTS=1` 使用 `admin / Admin@123456`，或设置 `VITE_SIQ_LOGIN_DEFAULT_USERNAME` 与 `VITE_SIQ_LOGIN_DEFAULT_PASSWORD` 覆盖默认值；这些值会进入前端产物，仅用于受控演示环境。

## 路由功能

| 路由 | 页面 | 主要能力 |
| --- | --- | --- |
| `/` | 我的工作台 | 个人资产、近期任务、报告入口和工作区摘要 |
| `/search` | 搜索下载 | CN/HK/US/EU/JP/KR 公司解析、官方报告检索、批量下载、下载文件管理 |
| `/parse` | A 股财报解析 | PDF 上传、已下载报告解析、质量复核、财务抽取、表格修正、工作流导入 |
| `/parse-hk` | 港股解析 | HKEX PDF 解析、港股 evidence package 构建和质量查看 |
| `/parse-us` | 美股解析 | SEC 上传/下载、10-K/10-Q/20-F/6-K evidence package、案例集入库、向量化 |
| `/parse-eu` | 欧股解析 | PDF/ESEF 披露下载、证据包构建、入库和评测 |
| `/parse-jp` | 日股解析 | EDINET PDF/XBRL 下载与解析工作流 |
| `/parse-kr` | 韩股解析 | DART 披露下载与解析工作流 |
| `/documents` | 通用文档解析 | 文件/URL/MinerU 导入、Markdown/JSON/表格/图片/source map、Schema 抽取、Wiki/DB/semantic 工作流 |
| `/analysis` | 智能分析 | 年度分析报告浏览、分析 Agent 对话、报告内溯源 |
| `/verify` | 事实核查 | 核查报告浏览、核查 Agent 对话、问题清单 |
| `/tracking` | 持续跟踪 | 跟踪报告、指标面板、事项、预警和跟踪 Agent |
| `/legal` | 法务合规 | 法律意见书浏览、法规检索型 Agent |
| `/chat` | 问答助手 | 全屏通用问答、附件、会话历史、SSE 流式输出 |
| `/vector-ingest` | 向量入库 | Milvus 入库控制台状态、启动命令和 Gradio 嵌入 |
| `/settings` | 设置 | LLM 设置、系统健康、模型连通性和下游服务状态 |
| `/admin/users` | 用户审批 | 用户管理、审批、权限和审计入口 |
| `/system-dashboard` | 系统平台 | 管理员系统级监控入口 |
| `/help` | 帮助 | 本地使用说明 |

## UI 设计规范

新增/修改的 design token、组件用法与移动端适配规则记录在 [`docs/ui-refresh.md`](./docs/ui-refresh.md)。

## 服务代理

| 前端前缀 | 代理目标 | 用途 |
| --- | --- | --- |
| `/api/auth` | `http://127.0.0.1:18081` | 鉴权、用户、权限 |
| `/api/chat` | `http://127.0.0.1:18081` | 通用助手聊天 |
| `/api/wiki` | `http://127.0.0.1:18081` | 公司与报告文件 |
| `/api/analysis` | `http://127.0.0.1:18081` | 分析 Agent |
| `/api/factchecker` | `http://127.0.0.1:18081` | 核查 Agent |
| `/api/tracking` | `http://127.0.0.1:18081` | 跟踪 Agent 和跟踪业务 API |
| `/api/legal` | `http://127.0.0.1:18081` | 法务 Agent |
| `/api/documents` | `http://127.0.0.1:18081` | 通用文档解析鉴权代理 |
| `/api/workflow` | `http://127.0.0.1:18081` | PDF/文档导入工作流 |
| `/api/market-reports`, `/api/us-sec`, `/api/jobs` | `http://127.0.0.1:18081` | 多市场 evidence package、SEC 案例集、后台 job |
| `/api/downloads` | `http://127.0.0.1:18081` | 已下载文件管理 |
| `/api/source`, `/api/pdf_page` | `http://127.0.0.1:18081` | PDF/报告溯源访问 |
| `/api/settings`, `/api/system` | `http://127.0.0.1:18081` | 模型设置与系统状态 |
| `/api/*` | `http://127.0.0.1:18000/*` | 公告搜索下载服务兜底 |
| `/pdfapi/*` | `http://127.0.0.1:15000/api/*` | PDF 解析服务直连代理 |

代理规则由 `vite.config.ts` 和 `scripts/proxy-config.mjs` 定义。新增 API 聚合后端路由时，应在公告下载兜底规则之前添加更具体的前缀。

## 关键模块

| 文件/目录 | 职责 |
| --- | --- |
| `src/App.tsx` | 路由、鉴权保护、懒加载 |
| `src/pages/SearchDownload.tsx` | 官方公告搜索和下载 |
| `src/pages/MarketParsingPage.tsx` | 多市场财报解析通用页面骨架 |
| `src/pages/PdfParsing.tsx` | A 股财报解析入口 |
| `src/pages/{Hk,Us,Eu,Jp,Kr}Parsing.tsx` | 各市场解析入口和扩展面板 |
| `src/pages/DocumentParsing.tsx` | 通用文档解析工作台 |
| `src/components/pdf/*` | PDF 上传、任务、质量、财务、溯源、工作流组件 |
| `src/components/document-parser/*` | 通用文档上传、参数、任务、结果、抽取和表格关系组件 |
| `src/components/report/*` | 报告选择、工具栏、iframe 安全渲染 |
| `src/components/agent/*`, `src/components/chat/*` | 专业 Agent 面板、通用聊天、附件和消息渲染 |
| `src/lib/pdfApi.ts`, `src/lib/documentApi.ts`, `src/lib/secApi.ts` | PDF、文档、多市场 API client |
| `src/lib/useAgentChat.ts` | SSE 聊天、停止、恢复、会话状态 |

## 技术栈

| 类型 | 选型 |
| --- | --- |
| UI 框架 | React 19、React Router 7 |
| 构建 | Vite 8、TypeScript 6 |
| 样式 | Tailwind CSS 4、Radix UI、class-variance-authority |
| 图标 | lucide-react |
| 图表 | Recharts |
| 内容安全 | DOMPurify、鉴权文件访问、iframe `srcdoc` 构建 |
| E2E | Playwright |

## 公网 HMR 配置

通过 HTTPS 反向代理暴露 Vite dev server 时：

```bash
cd /home/maoyd/siq-research-engine/apps/web
SIQ_PUBLIC_HOST=arthurmao.synology.me \
SIQ_PUBLIC_HMR_PROTOCOL=wss \
SIQ_PUBLIC_HMR_CLIENT_PORT=8276 \
SIQ_FRONTEND_PORT=15173 npm run dev -- --host 0.0.0.0
```

## 目录结构

```text
apps/web/
  src/
    main.tsx
    App.tsx
    pages/
    components/
    hooks/
    lib/
  public/
  e2e/
  scripts/proxy-config.mjs
  package.json
  vite.config.ts
  Dockerfile
```

## 维护原则

- 财报解析和通用文档解析保持概念隔离：前者有市场、公司、期间、三大表和财务校验；后者面向任意文档和 Schema 抽取。
- 所有长任务要暴露状态、错误、日志、重试和刷新入口。
- 报告 iframe、PDF 文件、文档 artifact 和来源链接必须走 API 鉴权或签名访问。
- 新增市场页面时复用 `MarketParsingPage`，但市场专属证据包、入库和评测逻辑放到对应扩展面板。
- 不提交 `node_modules`、`dist`、Playwright 临时报告、日志和构建产物。
