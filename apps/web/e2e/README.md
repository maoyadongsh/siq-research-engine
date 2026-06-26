# SIQ 前端 E2E 烟雾测试（待完善）

## 当前状态

本目录为前端烟雾测试预留结构，但目前**未启用自动化运行**。

## 阻塞点

项目当前没有稳定的测试登录方案：

- 登录依赖 `/api/auth/login` 和本地 `localStorage` 中的 `access_token` / `user`。
- 在 CI 或自动化环境中没有预留的测试账号/种子数据。
- 硬编码账号密码会引入安全风险，因此不在本阶段实现。

## 建议的覆盖范围（后续实现）

等后端提供测试账号或 mock 登录接口后，建议补充以下 Playwright 用例：

1. 登录页能正常渲染。
2. 桌面端左侧导航可见。
3. 移动端侧边栏可打开和关闭。
4. `/search` 正常渲染。
5. `/parse` 正常渲染。
6. `/analysis`、`/verify`、`/tracking`、`/legal` 能正常渲染报告壳状态。
7. 全局聊天可以打开和关闭。
8. 报告页的专用 Agent 面板可以展开和折叠。

## 建议接入方式

```bash
npm install --save-dev @playwright/test
npx playwright install
```

然后在 `package.json` 增加：

```json
{
  "scripts": {
    "smoke": "playwright test"
  }
}
```

并在 `e2e/` 下创建 `smoke.spec.ts`。
