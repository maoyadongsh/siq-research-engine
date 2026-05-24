# FinSight 主前端 README

本目录是当前实际使用的 FinSight 主前端。它基于 React + TypeScript + Vite，提供完整研究工作台、PDF 搜索下载、财报解析、报告查看、业务 Agent 对话、设置和帮助页面。

> 当前主入口是本目录：`/home/maoyd/finsight/finall_all_front_0516/front`。项目根目录下的 `front/` 是旧单页聊天 HTML，不是当前主 UI。

## 1. 技术栈

| 类型 | 当前实现 |
| --- | --- |
| 框架 | React 19 |
| 路由 | react-router-dom 7 |
| 构建 | Vite 8 |
| 语言 | TypeScript 6 |
| 样式 | Tailwind CSS v4 + `src/index.css` 自定义 design tokens |
| 图标 | lucide-react |
| 图表 | recharts |
| 包管理 | npm，锁文件为 `package-lock.json` |

## 2. 启动与构建

```bash
cd /home/maoyd/finsight/finall_all_front_0516/front
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

浏览器访问：

```text
http://localhost:5173
```

构建：

```bash
npm run build
```

Lint：

```bash
npm run lint
```

预览构建结果：

```bash
npm run preview
```

## 3. 页面与服务依赖

| 路由 | 页面文件 | 功能 | 依赖 |
| --- | --- | --- | --- |
| `/` | `src/pages/Dashboard.tsx` | 工作平台、研究对象状态、近期任务 | `/api/wiki/*` |
| `/search` | `src/pages/SearchDownload.tsx` | 公司解析、公告查询、财报下载、已下载 PDF 管理 | `/api/v1/*`、`/api/downloads/*` |
| `/parse` | `src/pages/PdfParsing.tsx` | PDF 上传/解析/溯源/复核/导入 Wiki 和 DB | `/pdfapi/*`、`/api/workflow/*`、`/api/downloads/*` |
| `/analysis` | `src/pages/AnalysisReport.tsx` | 智能分析 HTML 报告 + 分析助手 | `/api/wiki/*`、`/api/analysis/chat/*` |
| `/verify` | `src/pages/FactVerification.tsx` | 事实核查 HTML 报告 + 核查助手 | `/api/wiki/*`、`/api/factchecker/chat/*` |
| `/tracking` | `src/pages/Tracking.tsx` | 持续跟踪 HTML 报告 + 跟踪助手 | `/api/wiki/*`、`/api/tracking/chat/*` |
| `/legal` | `src/pages/LegalCompliance.tsx` | 法务合规 HTML 法律意见书 + 法务助手 | `/api/wiki/*`、`/api/legal/chat/*` |
| `/chat` | `src/pages/ChatPage.tsx` | 全屏财报问答助手 | `/api/chat/*` |
| `/settings` | `src/pages/Settings.tsx` | 服务连接、本地/云端模型配置、系统状态 | `/api/settings/*`、`/api/system/status` |
| `/help` | `src/pages/Help.tsx` | 操作指南 | 无后端强依赖 |

## 4. Vite 代理

代理配置在 `vite.config.ts`。前端开发时所有 API 都走相对路径，由 Vite 分发：

| 请求前缀 | 目标 | 说明 |
| --- | --- | --- |
| `/api/chat` | `http://127.0.0.1:10081` | 普通财报问答 |
| `/api/wiki` | `http://127.0.0.1:10081` | Wiki 公司和 HTML 报告 |
| `/api/analysis` | `http://127.0.0.1:10081` | 分析助手 |
| `/api/factchecker` | `http://127.0.0.1:10081` | 核查助手 |
| `/api/tracking` | `http://127.0.0.1:10081` | 跟踪助手和跟踪业务 API |
| `/api/legal` | `http://127.0.0.1:10081` | 法务助手 |
| `/api/settings` | `http://127.0.0.1:10081` | 模型设置 |
| `/api/system` | `http://127.0.0.1:10081` | 系统状态 |
| `/api/downloads` | `http://127.0.0.1:10081` | 已下载 PDF 列表/打开/删除 |
| `/api/workflow` | `http://127.0.0.1:10081` | PDF 解析产物导入 |
| `/api/source` | `http://127.0.0.1:10081` | PDF 来源表格/页面代理 |
| `/api/pdf_page` | `http://127.0.0.1:10081` | PDF 页面图片代理 |
| `/api/*` | `http://127.0.0.1:8000/*` | PDF 下载服务；会去掉 `/api` 前缀 |
| `/pdfapi/*` | `http://127.0.0.1:5000/api/*` | PDF 解析服务；会改写为 `/api` |

重要：`/api` 是兜底规则，会转发到 `8000`。新增聚合后端 API 时必须把更具体的前缀写在兜底 `/api` 之前。

## 5. 目录结构

```text
front/
  index.html
  package.json
  package-lock.json
  vite.config.ts
  tsconfig*.json
  eslint.config.js
  src/
    main.tsx                     React 入口
    App.tsx                      路由声明
    index.css                    Tailwind v4、主题 token、页面样式、头像动画
    pages/
      Dashboard.tsx              工作台
      SearchDownload.tsx         搜索下载
      PdfParsing.tsx             PDF 解析和溯源工作台
      AnalysisReport.tsx         分析报告页配置
      FactVerification.tsx       核查报告页配置
      Tracking.tsx               跟踪报告页配置
      LegalCompliance.tsx        法务合规页配置
      ChatPage.tsx               全屏问答
      Settings.tsx               设置
      Help.tsx                   帮助
    components/
      layout/                    侧边栏、顶栏、主布局
      agent/                     右侧业务 Agent 面板和头像
      chat/                      普通聊天组件、消息渲染、历史会话
      report/ReportViewer.tsx    四类 HTML 报告的通用查看器
      ui/                        Button/Card/Input/Select/Toast/Tooltip
    lib/
      useAgentChat.ts            聊天状态 store、SSE 消费、停止、恢复、历史会话
      useAutosizeTextarea.ts     输入框自适应高度
      hooks.ts                   主题和 API base 本地配置
      clipboard.ts               剪贴板兼容封装
  public/
    favicon.svg
    icons.svg
    pet/                         普通助手头像资源
      agent-drafts/              专业 agent 头像、动画和候选稿
```

## 6. 核心组件说明

### 6.1 全局布局

- `Layout.tsx`：承载侧边栏、顶栏、页面出口和全局悬浮聊天。
- `Sidebar.tsx`：主导航，包含工作台、搜索下载、财报解析、智能分析、事实核查、持续跟踪、法务合规、设置、帮助、问答助手。
- `Topbar.tsx`：全局搜索、任务通知、亮暗主题切换。

`Layout.tsx` 中当前隐藏全局悬浮聊天的页面：

```ts
const AGENT_PAGE_PATHS = ['/analysis', '/verify', '/tracking']
```

注意：`/legal` 当前会显示页面右侧法务助手，同时仍可能显示全局悬浮聊天。如需和其他业务报告页一致，应将 `/legal` 加入该列表。

### 6.2 报告查看器

`ReportViewer.tsx` 是 `/analysis`、`/verify`、`/tracking`、`/legal` 共用的报告查看器。它负责：

- 拉取公司列表：`/api/wiki/companies/list`
- 拉取报告列表：`reports`、`factchecks`、`trackings`、`legals`
- 使用 iframe 展示 HTML 报告
- 注入 `REPORT_VIEWER_THEME`，把不同来源 HTML 统一成浅色阅读样式
- 下载、分享、删除 HTML 报告
- 右侧挂载 `PageWithAgentChat`

四个页面只传不同配置：

| 页面 | reportType | reportApiSuffix | Agent API |
| --- | --- | --- | --- |
| 智能分析 | `analysis` | `reports` | `/api/analysis` |
| 事实核查 | `factcheck` | `factchecks` | `/api/factchecker` |
| 持续跟踪 | `tracking` | `trackings` | `/api/tracking` |
| 法务合规 | `legal` | `legals` | `/api/legal` |

### 6.3 聊天 store

`src/lib/useAgentChat.ts` 为所有聊天入口共享同一套状态管理：

- 按 `apiPrefix` 缓存一个 `AgentChatStore`。
- 初始化时读取历史并尝试恢复 active run。
- 发送消息走 `${apiPrefix}/chat/stream`。
- 停止消息走 `${apiPrefix}/chat/stop`。
- 新会话/切换会话/清空会话走 `${apiPrefix}/chat/session*`。
- SSE 消费事件：`run`、`delta`、`done`、普通文本 fallback。

普通全局聊天使用：

```ts
useAgentChat('/api')
```

业务助手使用：

```ts
useAgentChat('/api/analysis')
useAgentChat('/api/factchecker')
useAgentChat('/api/tracking')
useAgentChat('/api/legal')
```

### 6.4 头像与动画

专业 agent 头像映射在 `components/agent/AgentAvatar.tsx`：

| kind | 文件 |
| --- | --- |
| `analysis` | `/pet/agent-drafts/finsight-analysis-avatar-animated-transparent.webp` |
| `factchecker` | `/pet/agent-drafts/finsight-factchecker-avatar-animated-transparent.webp` |
| `tracking` | `/pet/agent-drafts/finsight-tracking-avatar-animated-transparent.webp` |
| `legal` | `/pet/agent-drafts/finsight-legal-avatar-animated-transparent.webp` |

普通财报助手头像在 `components/chat/PetFairy.tsx`：

```text
/pet/finsight-avatar-animated.webp
```

头像状态：

- `idle`
- `thinking`
- `replying`
- `error`

CSS 动画在 `src/index.css` 的 `.agent-avatar-*`、`.pet-*` 相关规则中。

## 7. 页面细节

### 7.1 工作平台 `/`

主要请求：

```text
GET /api/wiki/companies/list
GET /api/wiki/companies/recent-results?limit=<localStorage recent_task_limit>
```

展示：

- Wiki 公司数
- 智能分析/事实核查/持续跟踪/法务结果数量
- 当前优先研究对象
- 工作流步骤
- 近期任务列表

### 7.2 搜索下载 `/search`

主要请求：

```text
POST /api/v1/resolve
POST /api/v1/reports/recent
POST /api/v1/reports/batch-download
POST /api/v1/reports/download
POST /api/v1/reports/select-download
GET  /api/downloads/reports
GET  /api/downloads/report-file
DELETE /api/downloads/report-file
```

`/api/v1/*` 会被 Vite 代理到 `report-finder-service :8000`。

### 7.3 财报解析 `/parse`

主要请求：

```text
GET  /pdfapi/health
GET  /pdfapi/tasks
POST /pdfapi/upload
GET  /pdfapi/status/{task_id}
GET  /pdfapi/result/{task_id}
GET  /pdfapi/quality/{task_id}
GET  /pdfapi/financial/{task_id}
POST /pdfapi/cancel/{task_id}
POST /pdfapi/refetch/{task_id}
POST /pdfapi/reparse/{task_id}
DELETE /pdfapi/tasks/{task_id}
GET  /pdfapi/source/{task_id}/table/{table_index}
GET  /pdfapi/source/{task_id}/page/{page_number}
POST /pdfapi/source/{task_id}/table/{table_index}/correction
GET  /api/workflow/task/{task_id}/status
POST /api/workflow/task/{task_id}/wiki-import
POST /api/workflow/task/{task_id}/semantic
POST /api/workflow/task/{task_id}/db-import
```

页面内有较多 PDF 表格复核样式，局部 CSS 写在 `PdfParsing.tsx` 的 `CSS` 字符串中。

### 7.4 设置 `/settings`

localStorage 项：

| key | 说明 |
| --- | --- |
| `api_base` | 后端 API Base；留空时使用当前域名/Vite 代理 |
| `pdf_api_base` | PDF 解析 API Base，默认 `/pdfapi` |
| `wiki_root_hint` | 前端显示用 Wiki 根目录提示 |
| `recent_task_limit` | 工作台近期任务数量 |
| `theme` | `light` 或 `dark` |

后端设置接口：

```text
GET  /api/settings/llm
PUT  /api/settings/llm
POST /api/settings/llm/test
GET  /api/system/status
```

## 8. 静态资源

| 路径 | 说明 |
| --- | --- |
| `public/favicon.svg` | favicon |
| `public/icons.svg` | 图标资源 |
| `public/pet/finsight-avatar-animated.webp` | 当前普通财报助手动画 |
| `public/pet/finsight-avatar-static.png` | 普通财报助手静态图 |
| `public/pet/agent-drafts/*-animated-transparent.webp` | 专业 agent 当前前端动画 |
| `public/pet/agent-drafts/beauty-candidates/` | 头像候选稿 |
| `public/pet/agent-drafts/selected-legal-20260520/` | 法务头像选中版本备份 |

已确认归档在项目根：

```text
/home/maoyd/finsight/agent-avatar-archive-20260520
```

## 9. 常见问题

### 9.1 页面能打开但接口 404

检查 Vite 是否按本目录的 `vite.config.ts` 启动。直接用其他静态服务器打开构建前源码时不会有代理。

### 9.2 `/search` 查询失败

确认 PDF 下载服务运行：

```bash
curl -s http://localhost:8000/health
```

### 9.3 `/parse` 解析失败

确认 PDF 解析服务运行：

```bash
curl -s http://localhost:5000/api/health
```

如果 health 中 MinerU/VLM 不健康，前端上传成功后也可能无法进入解析。

### 9.4 业务 Agent 没有响应

确认聚合后端和对应 Hermes profile 都运行：

```bash
curl -s http://localhost:10081/health
curl -s http://localhost:8651/health   # analysis
curl -s http://localhost:8649/health   # factchecker
curl -s http://localhost:8650/health   # tracking
curl -s http://localhost:8652/health   # legal
```

### 9.5 头像不显示

检查静态文件：

```bash
ls -la public/pet/agent-drafts/*-animated-transparent.webp
```

如果浏览器缓存旧图，可强制刷新页面或临时改文件名再更新 `AgentAvatar.tsx` 映射。

