# SIQ Web 工作台

`apps/web` 是 SIQ Research Engine 的 React/Vite 前端。它提供研究工作台、公告搜索下载、PDF 解析工作流、生成报告浏览、专业 Agent 面板、设置、帮助、鉴权和工作区页面。

该应用由 SIQ 前端迁移而来。新开发使用 SIQ 路径和 `SIQ_*` 环境变量；部分 `SIQ_*` 变量仅作为迁移期兼容回退。

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

构建：

```bash
npm run build
```

Lint：

```bash
npm run lint
```

预览构建产物：

```bash
npm run preview
```

## 公网 HMR 配置

通过 HTTPS 反向代理暴露 Vite dev server 时，优先使用 SIQ 前缀变量：

```bash
cd /home/maoyd/siq-research-engine/apps/web
SIQ_PUBLIC_HOST=arthurmao.synology.me \
SIQ_PUBLIC_HMR_PROTOCOL=wss \
SIQ_PUBLIC_HMR_CLIENT_PORT=8276 \
npm run dev -- --host 0.0.0.0 --port 15173
```

`SIQ_PUBLIC_*` 变量仍作为迁移期兼容回退。

## 路由和服务依赖

| 路由 | 页面 | 依赖 |
| --- | --- | --- |
| `/` | 工作台概览 | `/api/wiki/*` |
| `/search` | 公告搜索下载 | `/api/v1/*`, `/api/downloads/*` |
| `/parse` | PDF 解析、复核和导入 | `/pdfapi/*`, `/api/workflow/*`, `/api/source/*` |
| `/analysis` | 分析报告和分析助手 | `/api/wiki/*`, `/api/analysis/chat/*` |
| `/verify` | 核查报告和核查助手 | `/api/wiki/*`, `/api/factchecker/chat/*` |
| `/tracking` | 跟踪报告和跟踪助手 | `/api/wiki/*`, `/api/tracking/chat/*` |
| `/legal` | 法务报告和法务助手 | `/api/wiki/*`, `/api/legal/chat/*` |
| `/chat` | 全屏通用助手 | `/api/chat/*` |
| `/settings` | LLM 设置和系统健康 | `/api/settings/*`, `/api/system/status` |
| `/help` | 本地帮助页 | 前端静态内容 |

## Vite 代理

代理规则由 `vite.config.ts` 和 `scripts/proxy-config.mjs` 定义。

| 请求前缀 | 目标 | 用途 |
| --- | --- | --- |
| `/api/chat` | `http://127.0.0.1:18081` | 通用助手聊天 |
| `/api/wiki` | `http://127.0.0.1:18081` | Wiki 和报告 API |
| `/api/analysis` | `http://127.0.0.1:18081` | 分析助手 |
| `/api/factchecker` | `http://127.0.0.1:18081` | 核查助手 |
| `/api/tracking` | `http://127.0.0.1:18081` | 跟踪助手和跟踪业务 API |
| `/api/legal` | `http://127.0.0.1:18081` | 法务助手 |
| `/api/eval` | `http://127.0.0.1:18081` | 评测 API |
| `/api/settings` | `http://127.0.0.1:18081` | 模型设置 |
| `/api/system` | `http://127.0.0.1:18081` | 系统状态 |
| `/api/downloads` | `http://127.0.0.1:18081` | 已下载 PDF 管理 |
| `/api/workflow` | `http://127.0.0.1:18081` | PDF 导入工作流 |
| `/api/source`, `/api/pdf_page` | `http://127.0.0.1:18081` | 证据与 PDF 溯源 |
| `/api/*` | `http://127.0.0.1:18000/*` | 公告搜索下载兜底 |
| `/pdfapi/*` | `http://127.0.0.1:15000/api/*` | PDF 解析服务 |

通用 `/api/*` 是公告搜索下载服务的兜底规则。新增 API 聚合后端路由时，应在兜底规则之前添加更具体的前缀。

## 目录结构

```text
apps/web/
  src/
    main.tsx
    App.tsx
    index.css
    pages/
    components/
    lib/
  public/
    pet/
    videos/
    illustrations/
  scripts/
    proxy-config.mjs
    trial-server.mjs
  package.json
  vite.config.ts
  Dockerfile
```

`node_modules`、`dist` 和日志都是生成或本地运行态文件，不应提交。

## 关键组件

| 组件 | 职责 |
| --- | --- |
| `src/pages/Dashboard.tsx` | 工作台首页概览 |
| `src/pages/SearchDownload.tsx` | 公告搜索和已下载 PDF 管理 |
| `src/pages/PdfParsing.tsx` | PDF 解析任务、质量复核、溯源和工作流导入 |
| `src/components/report/ReportViewer.tsx` | 分析/核查/跟踪/法务 HTML 报告浏览器 |
| `src/components/agent/AgentChatPanel.tsx` | 专业 Agent 对话面板 |
| `src/lib/useAgentChat.ts` | SSE 聊天状态、停止、恢复和会话处理 |
| `src/lib/apiClient.ts` | 带鉴权的 API client |

## 运行态资产

前端直接服务的运行态 UI 资产放在 `public/`。历史头像候选、生成的 review sheet 等不再保留在当前仓库；只有当前 UI 直接引用的资产才应进入 `public/`。

## 开发验证

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm run build
npm run lint
```

## 迁移注意事项

- 不要新增指向旧源目录的路径引用。
- 公网 HMR 配置优先使用 `SIQ_PUBLIC_*`。
- `SIQ_PUBLIC_*` 只作为迁移期兼容回退。
- 保持 `dist`、`node_modules` 和生成日志忽略。
