# FinSight / douge_ai_agent 启动指南

本文档说明如何在本地启动完整联调环境：主前端、FastAPI 后端、Wiki 报告服务，以及三个 FinSight Hermes 子 Agent。

## 架构概览

```
浏览器 (5173)
    │
    ├─ /api/chat, /api/wiki, /api/analysis, /api/factchecker, /api/tracking → 10081 (FastAPI)
    ├─ /api/* (其余)  → 8000 (财报检索等，可选)
    └─ /pdfapi/*      → 5000 (PDF 解析，可选)

FastAPI (10081)
    ├─ /api/wiki          → 读取 /home/maoyd/wiki/companies/
    ├─ /api/analysis/chat → Hermes finsight_analysis :8651
    ├─ /api/factchecker/chat → Hermes finsight_factchecker :8649
    └─ /api/tracking/chat    → Hermes finsight_tracking :8650
```

## 环境要求

| 工具 | 版本建议 | 用途 |
|------|----------|------|
| Python | ≥ 3.11 | 后端 |
| [uv](https://docs.astral.sh/uv/) | 最新 | Python 包管理与运行 |
| Node.js | ≥ 18 | 主前端 |
| npm | 随 Node | 前端依赖 |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | 已安装 CLI | 三个子 Agent API |

Wiki 数据默认目录：`/home/maoyd/wiki`（可通过环境变量 `WIKI_ROOT` 修改）。

---

## 端口一览

| 服务 | 端口 | 说明 |
|------|------|------|
| 主前端 (Vite) | **5173** | 开发入口 http://localhost:5173 |
| FastAPI 后端 | **10081** | 聊天、Wiki、三个 Agent 代理 |
| finsight_analysis | **8651** | 分析报告 Agent |
| finsight_factchecker | **8649** | 事实核查 Agent |
| finsight_tracking | **8650** | 持续跟踪 Agent |
| 财报检索 API（可选） | 8000 | 搜索下载等页 `/api` 代理 |
| PDF 解析 API（可选） | 5000 | PDF 解析页 `/pdfapi` 代理 |
| Hermes 主 Gateway（可选） | 8642 | 宠物问答 `/api/chat` 默认 |

---

## 推荐启动顺序

按依赖从下到上启动；**至少需要步骤 1～3** 才能使用分析报告页与三个右侧 Agent。

### 1. 启动三个 Hermes 子 Agent

每个 Profile 需单独启动 Gateway（内含 API Server）。在**三个终端**中分别执行，或依次切换 Profile 后启动：

```bash
# 终端 A：分析 Agent (8651)
hermes profile use finsight_analysis
hermes gateway start

# 终端 B：事实核查 Agent (8649)
hermes profile use finsight_factchecker
hermes gateway start

# 终端 C：跟踪 Agent (8650)
hermes profile use finsight_tracking
hermes gateway start
```

也可使用 Profile 别名（若已配置）：`finsight_analysis`、`finsight_factchecker`、`finsight_tracking`。

**健康检查：**

```bash
curl -s http://localhost:8651/health   # {"status":"ok",...}
curl -s http://localhost:8649/health
curl -s http://localhost:8650/health
```

查看所有 Gateway 状态：

```bash
hermes gateway list
```

停止某个 Profile 的 Gateway：

```bash
hermes profile use finsight_analysis
hermes gateway stop
```

Hermes API 鉴权默认与后端一致：`Authorization: Bearer change-me-local-dev`（见各 Profile 下 `.env` 中 `API_SERVER_KEY`）。

---

### 2. 启动 FastAPI 后端

```bash
cd /home/maoyd/finsight/backend
uv sync
uv run uvicorn main:app --reload --host 0.0.0.0 --port 10081
```

**健康检查：**

```bash
curl -s http://localhost:10081/health
curl -s http://localhost:10081/api/wiki/companies/list | head -c 200
```

可选环境变量：

```bash
export WIKI_ROOT=/home/maoyd/wiki   # 公司报告根目录，默认即此路径
```

---

### 3. 启动主前端

```bash
cd /home/maoyd/finsight/finall_all_front_0516/front
npm install
npm run dev
```

浏览器打开：**http://localhost:5173**

Vite 已将以下路径代理到 `10081`：

- `/api/chat`、`/api/wiki`
- `/api/analysis`、`/api/factchecker`、`/api/tracking`

其余 `/api/*` 代理到 `8000`（需该服务已启动）。`/pdfapi` 代理到 `5000`。

---

### 4. 可选：财报检索 / PDF 解析

若使用「搜索下载」「PDF 解析」等页面，需另行启动对应服务（端口见上表）。未启动时仅这些功能不可用，不影响分析报告与 Agent 对话。

---

## 前端页面与 Agent 对应关系

| 路由 | 功能 | 右侧 Agent API |
|------|------|----------------|
| `/analysis` | 分析报告（Wiki HTML） | `/api/analysis/chat` |
| `/verify` | 事实检验 | `/api/factchecker/chat` |
| `/tracking` | 持续跟踪 | `/api/tracking/chat` |
| `/chat` | 财报问答助手 | `/api/chat` → Hermes :8642 |

三个业务页右侧为可折叠 Agent 面板；主内容区单独滚动，Agent 固定在视口内。

---

## 后端主要 API（10081）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/api/wiki/companies/list` | 公司列表 |
| GET | `/api/wiki/companies/{dir}/reports` | 某公司报告列表 |
| GET | `/api/wiki/companies/{path}` | 静态报告文件（HTML 等） |
| POST | `/api/analysis/chat` | 分析 Agent（非流式） |
| POST | `/api/analysis/chat/stream` | 分析 Agent（SSE） |
| GET | `/api/analysis/chat/history` | 分析会话历史 |
| DELETE | `/api/analysis/chat/session` | 重置分析会话 |
| POST | `/api/factchecker/chat/stream` | 事实核查 Agent（SSE） |
| POST | `/api/tracking/chat/stream` | 跟踪 Agent（SSE） |

`factchecker`、`tracking` 同样提供 `/chat`、`/chat/stream`、`/chat/history`、`/chat/session` 四端点。

多轮对话说明见：[backend/hermes-api-multi-turn.md](backend/hermes-api-multi-turn.md)

---

## 目录结构（简要）

```
douge_ai_agent/
├── backend/                 # FastAPI（uv + uvicorn）
│   ├── main.py
│   ├── routers/             # chat, wiki, analysis, factchecker, tracking_agent
│   └── services/hermes_client.py
├── finall_all_front_0516/front/   # 主 React 前端（推荐）
├── front/                   # 简易单页聊天（可选，同源 10081）
└── wiki/                    # 部分跟踪示例数据

~/.hermes/profiles/
├── finsight_analysis/       # :8651
├── finsight_factchecker/    # :8649
└── finsight_tracking/       # :8650

/home/maoyd/wiki/companies/  # 公司分析报告 HTML（默认 WIKI_ROOT）
```

---

## 常见问题

### 分析报告页空白、无法选公司

- 确认 `10081` 已启动且已挂载 Wiki 路由。
- 检查：`curl http://localhost:10081/api/wiki/companies/list` 应返回 `companies` 数组。
- 确认 `/home/maoyd/wiki/companies/` 下存在公司目录及 `analysis/*.html`。

### Agent 对话报错或一直转圈

- 确认对应 Hermes Gateway 已启动：`hermes gateway list` 显示 ✓。
- 确认端口可达：`curl http://localhost:8651/health`（分析页用 8651，以此类推）。
- 查看 Profile 日志：`~/.hermes/profiles/finsight_analysis/logs/agent.log`。

### 前端请求打到 8000 导致 404

- 分析报告 / Agent 相关请求必须走 `10081`；已在 `vite.config.ts` 中为 `/api/analysis` 等单独配置代理，修改配置后需重启 `npm run dev`。

### 右侧 Agent 随页面滚走

- 请使用最新前端代码：`PageWithAgentChat` 为左右分栏，左侧滚动、右侧固定高度。

---

## 一键检查脚本（可选）

```bash
echo "=== Backend ===" && curl -sf http://localhost:10081/health && echo OK || echo FAIL
echo "=== Wiki ===" && curl -sf http://localhost:10081/api/wiki/companies/list >/dev/null && echo OK || echo FAIL
echo "=== Hermes analysis ===" && curl -sf http://localhost:8651/health && echo OK || echo FAIL
echo "=== Hermes factchecker ===" && curl -sf http://localhost:8649/health && echo OK || echo FAIL
echo "=== Hermes tracking ===" && curl -sf http://localhost:8650/health && echo OK || echo FAIL
echo "=== Frontend ===" && curl -sf http://localhost:5173 >/dev/null && echo OK || echo FAIL
```

全部 OK 后访问 http://localhost:5173/analysis 即可联调。
