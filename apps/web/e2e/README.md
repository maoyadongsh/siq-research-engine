# SIQ 前端 E2E 烟雾测试

`apps/web/e2e` 是 Web 工作台的端到端烟雾测试目录，用于验证登录、导航、关键页面渲染、报告壳状态、聊天面板和移动端布局。

## 测试目标

E2E 烟雾测试不替代单元测试和人工验收，它重点确认主要用户路径没有断裂：

| 场景 | 目标 |
| --- | --- |
| 登录 | 登录页渲染、测试账号登录、token 写入 |
| 导航 | 桌面端侧边栏、移动端抽屉、底部工具入口 |
| 搜索下载 | `/search` 页面能加载并发起基础请求 |
| 财报解析 | `/parse`、`/parse-hk`、`/parse-us`、`/parse-eu`、`/parse-jp`、`/parse-kr` 页面能展示任务区、上传区和结果区 |
| 通用文档 | `/documents` 页面能展示上传区、参数区、任务区和结果区 |
| 报告页 | `/analysis`、`/verify`、`/tracking`、`/legal` 能展示空状态或报告壳 |
| 聊天 | 全局聊天和专业 Agent 面板能打开、发送、停止 |
| 设置 | 系统状态和 LLM 设置页能加载 |
| 管理 | 管理员用户能访问用户审批和系统平台 |

## 推荐接入

Playwright 已接入 `apps/web`，当前包含登录/注册、工作平台首页、搜索下载、PDF/通用文档解析和聊天响应式烟雾验收。配置文件：

- `playwright.config.ts`
- `e2e/support/mockApi.ts`
- `e2e/tests/auth-responsive.spec.ts`
- `e2e/tests/workspace-responsive.spec.ts`
- `e2e/tests/search-download-responsive.spec.ts`
- `e2e/tests/pdf-parsing-market-filter.spec.ts`
- `e2e/tests/document-result-preview.spec.ts`
- `e2e/tests/chat-responsive.spec.ts`

如需重新安装浏览器运行时：

```bash
cd /home/maoyd/siq-research-engine/apps/web
npx playwright install chromium
```

运行：

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm run e2e
```

`npm run smoke` 当前等价于 `npm run e2e`。

## 端口配置

Playwright 默认使用独立的本地前端端口 `15174`，避免和普通开发默认端口 `15173` 冲突。`playwright.config.ts` 会从同一个入口推导 `use.baseURL`、`webServer.url` 和 dev server 端口：

- 默认：`http://127.0.0.1:15174`
- `SIQ_FRONTEND_PORT`：只覆盖端口。
- `PLAYWRIGHT_BASE_URL`：覆盖完整测试 URL；如果 URL 没有显式端口，会回退使用 `SIQ_FRONTEND_PORT` 或默认 `15174`。

可通过环境变量覆盖：

```bash
cd /home/maoyd/siq-research-engine/apps/web
SIQ_FRONTEND_PORT=15175 npm run e2e
PLAYWRIGHT_BASE_URL=http://127.0.0.1:15175 npm run e2e
```

设置 `PLAYWRIGHT_BASE_URL` 时，建议使用带显式端口的本地 URL；配置中的 dev server 命令会优先使用该 URL 里的端口。普通开发和 `start_all.sh` 仍默认使用 `SIQ_FRONTEND_PORT=15173`。

## 测试账号

当前登录/注册和工作平台等烟雾验收使用 `e2e/support/mockApi.ts` 或测试内 mock route 注入登录态 / API 响应，因此不依赖后端、数据库或真实测试账号。

后续覆盖真实端到端路径时，仍推荐由 API 后端提供专用 seed 用户或测试登录接口，并确保：

- 账号只用于本地或 CI 测试。
- 权限覆盖普通用户和管理员路径。
- 密码或 token 不写入仓库。
- 测试数据可重复创建和清理。

## 运行要求

多数烟雾测试使用 `e2e/support/mockApi.ts` 注入 mock API，直接运行 `npm run e2e` 即可，Playwright 会按上面的端口策略启动或复用 Vite dev server。

如需手动先启动完整本地服务，可保持同一个端口入口：

```bash
cd /home/maoyd/siq-research-engine
export SIQ_AUTH_SECRET_KEY="$(openssl rand -hex 32)"
export SIQ_FRONTEND_PORT=15173
./start_all.sh
```

再运行：

```bash
cd /home/maoyd/siq-research-engine/apps/web
PLAYWRIGHT_BASE_URL=http://127.0.0.1:15173 npm run e2e
```
