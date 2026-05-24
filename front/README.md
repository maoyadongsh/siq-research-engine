# 旧单页聊天前端

本目录是早期的单文件聊天页面，只包含：

```text
front/
  index.html
  README.md
```

它可以对接聚合后端的普通聊天接口 `/api/chat/stream`，适合快速调试 Hermes `finsight_assistant` 和 SSE 输出，但不是当前 FinSight 主工作台。

当前主前端在：

```text
/home/maoyd/finsight/finall_all_front_0516/front
```

## 使用场景

- 快速验证聚合后端 `:10081` 是否能完成普通问答。
- 绕开 React/Vite 工作台，单独调试 SSE。
- 作为后端根路径 `/` 的静态页面：`backend/main.py` 会返回这个 `index.html`。

## 推荐同源使用

先启动聚合后端：

```bash
cd /home/maoyd/finsight/backend
uv run uvicorn main:app --reload --host 0.0.0.0 --port 10081
```

打开：

```text
http://127.0.0.1:10081/
```

页面中的 API 地址留空即可，浏览器会同源请求：

```text
POST   /api/chat/stream
GET    /api/chat/history
GET    /api/chat/sessions
POST   /api/chat/session
DELETE /api/chat/session
POST   /api/chat/stop
```

## 独立静态服务

也可以用任意静态服务器托管：

```bash
cd /home/maoyd/finsight/front
python3 -m http.server 5174
```

访问：

```text
http://127.0.0.1:5174
```

此时需要在页面 API 输入框填写：

```text
http://127.0.0.1:10081
```

或通过 URL 参数：

```text
http://127.0.0.1:5174/index.html?api=http://127.0.0.1:10081
```

跨域时需确认聚合后端 CORS 放行对应来源。当前后端默认放行 `http://localhost:5173`、`tauri://localhost` 和 `https://tauri.localhost`，如果从其他端口打开可能需要调整 `backend/main.py`。

## 与主前端的区别

| 项目 | 本目录旧页面 | 当前主前端 |
| --- | --- | --- |
| 路径 | `douge_ai_agent/front` | `douge_ai_agent/finall_all_front_0516/front` |
| 技术 | 单个 HTML | React + TypeScript + Vite |
| 功能 | 普通聊天 | 完整工作台、搜索下载、解析、分析、核查、跟踪、法务、设置 |
| Agent | 仅 `/api/chat` | assistant、analysis、factchecker、tracking、legal |
| 头像 | 内嵌/旧样式 | 当前确认版动画头像 |

