# SIQ 前端 E2E 烟雾测试

## 测试目标

`apps/web/e2e` 负责验证 Web 工作台的关键用户路径没有被破坏。它不是覆盖所有业务细节的唯一测试层，而是回答一个更直接的问题：对于真实用户来说，系统最核心的操作路径现在还能不能用。

这些 smoke tests 按产品面覆盖：

| 产品面 | E2E 关注点 |
| --- | --- |
| 二级市场投研分析智能体集群 | 搜索下载、解析页、package quality gate、报告阅读、分析/核查/跟踪/法务/聊天入口 |
| 一级市场投研决策智能体集群 | `/primary-market`、`/deals` 壳层、材料/证据/决策入口和空状态 |
| 应用中心 | `/documents`、`/meetings`、`/vector-ingest`、设置与系统状态入口 |

OpenShell runtime provenance、Agent memory 注入和真实模型输出属于 API/OpenShell 专项测试或 mock contract 验证范围；前端 E2E 主要确保这些状态能被页面稳定承载和展示。

## 覆盖范围

| 场景 | 目标 |
| --- | --- |
| 登录与注册 | 页面渲染、登录态建立与基础跳转 |
| 首页与导航 | 桌面端侧边栏、移动端抽屉、主要功能入口 |
| 搜索下载 | `/search` 能正常展示并发起基础查询 |
| 财报解析 | `/parse` 与各市场解析页能展示核心工作区 |
| 通用文档解析 | `/documents` 的上传、任务、结果和预览壳层可用 |
| 报告阅读 | `/analysis` `/verify` `/tracking` `/legal` 至少能加载报告壳或空状态 |
| 聊天 | 助手与专业 Agent 面板可打开、发送、停止 |
| 设置与管理 | `/settings`、用户审批、系统平台等管理入口可访问 |
| 二级市场 MVP | `/parse-hk` package quality gate、force import、force vector dry-run |

当前重点是 smoke coverage，而不是对每个页面做像素级或全流程回归。

## 当前最新状态

新增的商业 MVP 回归用例：

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm run e2e -- e2e/tests/secondary-market-mvp-flow.spec.ts
```

该用例使用 mock API 验证 `/parse-hk` 的 package 面板、quality gates、warning/fail 阻断、确认后 `force=true` 请求体等关键产品语义。它不依赖真实 HKEX、真实 PDF parser、真实 PostgreSQL 或真实 Milvus，因此适合进入 CI smoke。

## 运行方式

### 安装浏览器运行时

```bash
cd /home/maoyd/siq-research-engine/apps/web
npx playwright install chromium
```

### 执行 E2E

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm run e2e
```

`npm run smoke` 当前与 `npm run e2e` 等价。

GitHub Actions 的 `web-e2e-smoke` job 也执行同一组 Playwright smoke tests，并在 CI runner 上安装 Chromium 运行时；这些用例应继续保持 mock-based，不依赖真实后端、真实数据库或外部模型服务。

## 端口与环境变量

Playwright 默认使用独立端口，避免与日常开发的 Vite 端口冲突。

- 普通开发默认端口：`15173`
- Playwright 默认端口：`15174`

可通过以下变量覆盖：

| 变量 | 作用 |
| --- | --- |
| `SIQ_FRONTEND_PORT` | 只覆盖 dev server 端口 |
| `PLAYWRIGHT_BASE_URL` | 覆盖完整测试入口 URL |

示例：

```bash
cd /home/maoyd/siq-research-engine/apps/web
SIQ_FRONTEND_PORT=15175 npm run e2e
PLAYWRIGHT_BASE_URL=http://127.0.0.1:15175 npm run e2e
```

## Mock 与真实链路边界

当前多数 smoke tests 使用 `e2e/support/mockApi.ts` 或测试内 route mock，不强依赖完整后端、真实数据库或固定测试账号。这样做的价值是：

- 提高本地执行稳定性。
- 降低对下游服务启动状态的耦合。
- 把 smoke test 的关注点聚焦在前端壳层、导航和核心交互上。

如果要覆盖真实链路，应额外提供：

- 可重复创建和清理的测试用户。
- 可预测的后端数据基线。
- 不写入仓库的密钥与登录信息。

## 维护原则

- smoke tests 优先覆盖最容易影响用户主路径的页面和入口。
- 前端 mock 行为要尽量对齐 API 真实 payload 结构，避免测试通过但集成失败。
- 涉及响应式布局、导航结构和核心工作台壳层的改动，应补相应 E2E 场景。
- 不把 E2E 测试写成脆弱的样式快照；优先验证可见功能和关键语义。
