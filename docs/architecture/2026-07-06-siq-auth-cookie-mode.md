# SIQ Auth Cookie Mode 第一阶段设计

> 日期：2026-07-06
> 目标：保留本地 bearer token 兼容，同时为公网/多租户部署引入 HttpOnly cookie 会话能力。

## 背景

当前 Web 把 `access_token` 存在 `localStorage`，所有 API 请求通过 `Authorization: Bearer <token>` 访问后端。这个模式适合本地开发、内网演示和脚本调试，但公网部署时一旦发生 XSS，攻击者可以直接读取并转移 token。

第一阶段不一次性替换全站认证流，而是增加兼容开关：

```text
SIQ_AUTH_COOKIE_MODE=1
```

开启后，后端登录接口仍返回原有 JSON 结构，但同时设置 HttpOnly access cookie；前端不再把 JWT 写入 `localStorage`，API 请求对 SIQ API 自动带 `credentials: include`。

## 本地版与公网版差异

| 维度 | 本地/内网版 | 公网/多租户版 |
| --- | --- | --- |
| token 存储 | `localStorage.access_token` | HttpOnly cookie |
| API 鉴权 | `Authorization: Bearer` | cookie 优先兼容，bearer 继续可用 |
| CSRF 防护 | 风险较低，通常同源开发 | 必须叠加 SameSite、CSRF token 或双提交 token |
| XSS 后果 | token 可被脚本直接读取 | cookie 不可被 JS 读取，但仍可发起同源请求 |
| 登出 | 前端清 localStorage | 前端清 user cache，后端清 cookie |

## 已落地行为

- `routers.auth.get_current_user` 和 `services.auth_dependencies.get_current_user` 同时接受 bearer token 与 `siq_access_token` cookie。
- `POST /api/auth/login` 与 `POST /api/auth/demo-login` 在 `SIQ_AUTH_COOKIE_MODE=1` 时设置 HttpOnly cookie。
- `POST /api/auth/logout` 在 cookie mode 下清除 HttpOnly cookie。
- Web `apiFetch` 与 `fetchWithAuth` 在 cookie mode 下只对受保护的 `/api/*` 请求设置 `credentials: include`；浏览器不再直连 parser。
- Web `AuthProvider` 在 cookie mode 下不再持久化 JWT 到 `localStorage`，刷新时可通过 `/api/auth/me` 恢复用户。

## 配置

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `SIQ_AUTH_COOKIE_MODE` | `0` | 开启兼容式 cookie 会话 |
| `SIQ_AUTH_ACCESS_COOKIE_NAME` | `siq_access_token` | access cookie 名称 |
| `SIQ_AUTH_COOKIE_PATH` | `/` | cookie path |
| `SIQ_AUTH_COOKIE_SAMESITE` | `lax` | `lax`、`strict`、`none` |
| `SIQ_AUTH_COOKIE_SECURE` | `0` | 公网 HTTPS 部署应设为 `1` |
| `SIQ_ACCESS_TOKEN_EXPIRE_MINUTES` | `480` | cookie max-age 与 access token 有效期对齐 |

前端构建/运行时支持 `SIQ_AUTH_COOKIE_MODE`、`VITE_SIQ_AUTH_COOKIE_MODE` 或运行时 `window.__SIQ_CONFIG__.SIQ_AUTH_COOKIE_MODE`。本地调试也可临时在 `localStorage` 写入 `SIQ_AUTH_COOKIE_MODE=1`。

## 威胁模型

| 威胁 | 风险 | 第一阶段缓解 |
| --- | --- | --- |
| XSS token 窃取 | localStorage JWT 可被直接读取 | cookie mode 不持久化 JWT，HttpOnly cookie 不暴露给 JS |
| CSRF | cookie 会自动随请求发送 | 默认 SameSite=Lax；下一阶段为写操作增加 CSRF token |
| source token 泄漏 | 溯源链接可能混入登录 token | 继续使用独立 `SIQ_SOURCE_TOKEN_SECRET` 与短期 source token |
| 跨租户访问 | token/cookie 被错误复用 | 现阶段仍依赖用户身份与 workspace 权限；多租户需补 tenant claim 与 DB 约束 |
| 登出残留 | 只清前端状态无法清 cookie | cookie mode 下调用后端 logout 清 cookie |

## 后续路线

1. 增加 refresh token cookie 与短 access token，refresh cookie 使用更严格 path，例如 `/api/auth/refresh`。
2. 为非 GET/HEAD 的写操作增加 CSRF token，优先采用 `X-SIQ-CSRF-Token` 双提交或服务端 session nonce。
3. JWT 增加 `tenant_id`、`session_id`、`token_version`，支持服务端失效与跨租户校验。
4. 把 source access token 与登录 token 完全分域，禁止任何 source URL 接受登录 token query 参数。
5. 公网部署默认 `SIQ_AUTH_COOKIE_MODE=1`、`SIQ_AUTH_COOKIE_SECURE=1`、`SIQ_AUTH_COOKIE_SAMESITE=lax` 或按跨站嵌入需求设 `none`。
