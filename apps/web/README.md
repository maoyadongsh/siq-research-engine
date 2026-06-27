# SIQ Web 工作台

`apps/web` 是 SIQ Research Engine 的 React / Vite 前端。它把财报研究的主要流程组织为一个统一工作台：公司资料、公告下载、PDF 解析、报告浏览、事实核查、持续跟踪、法务合规、通用问答、系统设置和用户管理。

## 产品定位

前端不是简单的接口调试页，而是研究员日常工作的主界面。它强调：

- 从官方公告到 PDF 解析再到报告产物的连续工作流。
- 报告阅读、证据溯源和 Agent 对话并排协作。
- 管理员、普通用户、工作区和权限状态清晰分离。
- 长任务通过 SSE 和状态面板呈现，避免“点击后失联”的体验。

## 启动

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm install
npm run dev -- --host 0.0.0.0 --port 15173
```

打开：

```text
http://localhost:15173
```

构建与检查：

```bash
npm run build
npm run lint
```

预览构建产物：

```bash
npm run preview
```

## 路由功能

| 路由 | 页面 | 主要能力 |
| --- | --- | --- |
| `/` | 工作平台 | 工作区摘要、项目资产、近期研究入口 |
| `/search` | 搜索下载 | 公司解析、官方公告检索、批量下载、下载文件管理 |
| `/parse` | 财报解析 | PDF 上传、解析任务、质量复核、财务抽取、溯源、导入工作流 |
| `/analysis` | 智能分析 | 年度分析报告浏览、分析 Agent 对话 |
| `/verify` | 事实核查 | 核查报告浏览、核查 Agent 对话 |
| `/tracking` | 持续跟踪 | 跟踪报告、事项、指标、预警和跟踪 Agent |
| `/legal` | 法务合规 | 法律意见书浏览、法规检索型 Agent |
| `/chat` | 问答助手 | 全屏通用财报问答、附件、会话历史 |
| `/account` | 账户 | 当前用户资料和本地账户状态 |
| `/settings` | 设置 | LLM 设置、系统健康、下游服务状态 |
| `/vector-ingest` | 向量入库 | Milvus 入库控制台状态、启动命令和 Gradio 嵌入 |
| `/admin/users` | 用户审批 | 用户管理、审批、权限和审计入口 |
| `/system-dashboard` | 系统平台 | 系统级监控与管理入口 |
| `/help` | 帮助 | 本地使用说明 |

## 服务依赖

| 前端前缀 | 代理目标 | 用途 |
| --- | --- | --- |
| `/api/auth` | `http://127.0.0.1:18081` | 鉴权、用户、权限 |
| `/api/chat` | `http://127.0.0.1:18081` | 通用助手聊天 |
| `/api/wiki` | `http://127.0.0.1:18081` | 公司与报告文件 |
| `/api/analysis` | `http://127.0.0.1:18081` | 分析 Agent |
| `/api/factchecker` | `http://127.0.0.1:18081` | 核查 Agent |
| `/api/tracking` | `http://127.0.0.1:18081` | 跟踪 Agent 和跟踪业务 API |
| `/api/legal` | `http://127.0.0.1:18081` | 法务 Agent |
| `/api/settings` | `http://127.0.0.1:18081` | 模型设置 |
| `/api/system` | `http://127.0.0.1:18081` | 系统状态 |
| `/api/downloads` | `http://127.0.0.1:18081` | 已下载 PDF 管理 |
| `/api/workflow` | `http://127.0.0.1:18081` | PDF 导入工作流 |
| `/api/source`, `/api/pdf_page` | `http://127.0.0.1:18081` | 证据与 PDF 溯源 |
| `/api/*` | `http://127.0.0.1:18000/*` | 公告搜索下载服务兜底 |
| `/pdfapi/*` | `http://127.0.0.1:15000/api/*` | PDF 解析服务 |

代理规则由 `vite.config.ts` 和 `scripts/proxy-config.mjs` 定义。新增 API 聚合后端路由时，应在公告下载兜底规则之前添加更具体的前缀。

## 关键组件

| 文件 | 职责 |
| --- | --- |
| `src/App.tsx` | 路由、鉴权保护和懒加载 |
| `src/components/layout/*` | 侧边栏、顶栏、全局搜索、通知菜单 |
| `src/pages/SearchDownload.tsx` | 官方公告搜索与下载 |
| `src/pages/PdfParsing.tsx` | PDF 解析任务、质量复核、导入和溯源 |
| `src/components/pdf/*` | PDF 阅读、页图、质量、财务、任务和工作流组件 |
| `src/components/report/*` | HTML 报告选择、工具栏、iframe 安全渲染 |
| `src/components/agent/*` | 专业 Agent 面板、头像、进度卡片 |
| `src/components/chat/*` | 通用聊天、附件、会话历史和消息渲染 |
| `src/lib/useAgentChat.ts` | SSE 聊天、停止、恢复、会话状态 |
| `src/lib/apiClient.ts` | 带鉴权的 API client |
| `src/lib/authenticatedSourceLinks.ts` | 报告内溯源链接鉴权处理 |

## 技术栈

| 类型 | 选型 |
| --- | --- |
| UI 框架 | React 19、React Router 7 |
| 构建 | Vite 8、TypeScript 6 |
| 样式 | Tailwind CSS 4、Radix UI、class-variance-authority |
| 图标 | lucide-react |
| 图表 | Recharts |
| 内容安全 | DOMPurify、鉴权文件访问、iframe srcdoc 构建 |

## 公网 HMR 配置

通过 HTTPS 反向代理暴露 Vite dev server 时：

```bash
cd /home/maoyd/siq-research-engine/apps/web
SIQ_PUBLIC_HOST=arthurmao.synology.me \
SIQ_PUBLIC_HMR_PROTOCOL=wss \
SIQ_PUBLIC_HMR_CLIENT_PORT=8276 \
npm run dev -- --host 0.0.0.0 --port 15173
```

## 目录结构

```text
apps/web/
  src/
    main.tsx
    App.tsx
    index.css
    pages/
    components/
    hooks/
    lib/
  public/
    agent/
    videos/
    illustrations/
  scripts/
    proxy-config.mjs
    trial-server.mjs
  package.json
  vite.config.ts
  Dockerfile
```

## 开发原则

- 报告页优先保持“报告阅读 + 专业 Agent + 溯源链接”的协作布局。
- 长任务要暴露状态、错误和重试入口。
- 报告 iframe、PDF 文件和来源链接必须走鉴权或签名访问。
- 不提交 `node_modules`、`dist`、日志和临时构建产物。
