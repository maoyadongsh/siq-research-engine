# Hermes API 多轮对话指南

API 地址：`http://localhost:8642/v1`

认证方式：`Authorization: Bearer change-me-local-dev`

## 方式一：conversation 参数（推荐）

通过指定相同的 `conversation` 名称，Hermes 自动维护会话上下文，无需客户端管理历史消息。

### 第一轮

```bash
curl -s http://localhost:8642/v1/responses \
  -H "Authorization: Bearer change-me-local-dev" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "hermes-agent",
    "input": "请记住我的名字是小明",
    "conversation": "my-session",
    "store": true
  }'
```

### 第二轮（同一会话）

```bash
curl -s http://localhost:8642/v1/responses \
  -H "Authorization: Bearer change-me-local-dev" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "hermes-agent",
    "input": "我叫什么名字？",
    "conversation": "my-session"
  }'
```

> `conversation` 名称相同即视为同一会话。`store: true` 表示存储响应以支持后续检索。

## 方式二：previous_response_id 链式调用

手动通过上一轮返回的 `id` 串联对话。

### 第一轮

```bash
curl -s http://localhost:8642/v1/responses \
  -H "Authorization: Bearer change-me-local-dev" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "hermes-agent",
    "input": "请记住我的名字是小明",
    "store": true
  }'
```

返回中获取 `"id": "resp_xxx"`。

### 第二轮

```bash
curl -s http://localhost:8642/v1/responses \
  -H "Authorization: Bearer change-me-local-dev" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "hermes-agent",
    "input": "我叫什么名字？",
    "previous_response_id": "resp_xxx"
  }'
```

> 将 `resp_xxx` 替换为第一轮返回的实际 ID。

## 方式三：X-Hermes-Session-Id（推荐，最简洁）

通过 HTTP Header 指定会话 ID，服务端自动维护上下文，客户端无需管理对话历史。适用于 `/v1/chat/completions` 接口。

### 第一轮

```bash
curl -s http://localhost:8642/v1/chat/completions \
  -H "Authorization: Bearer change-me-local-dev" \
  -H "X-Hermes-Session-Id: demo-session-001" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "hermes-agent",
    "messages": [{"role": "user", "content": "请记住我最喜欢的颜色是蓝色"}]
  }'
```

### 第二轮（同一会话）

```bash
curl -s http://localhost:8642/v1/chat/completions \
  -H "Authorization: Bearer change-me-local-dev" \
  -H "X-Hermes-Session-Id: demo-session-001" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "hermes-agent",
    "messages": [{"role": "user", "content": "我最喜欢的颜色是什么？"}]
  }'
```

> 只需保持 `X-Hermes-Session-Id` 一致，每次请求只发当前消息即可，无需携带历史。

## 方式四：Chat Completions 手动管理历史

`/v1/chat/completions` 无 session header 时为无状态接口，需要客户端自行在 `messages` 数组中携带完整对话历史。

```bash
curl -s http://localhost:8642/v1/chat/completions \
  -H "Authorization: Bearer change-me-local-dev" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "hermes-agent",
    "messages": [
      {"role": "user", "content": "请记住我的名字是小明"},
      {"role": "assistant", "content": "好的，小明！我记住了。"},
      {"role": "user", "content": "我叫什么名字？"}
    ]
  }'
```

## 方式五：Runs API（长会话流式方案）

Runs API 适用于需要订阅进度事件而非自行管理流式传输的长会话场景。

> **多轮上下文说明**：经测试，Runs API 中 `session_id` 仅用于 UI 关联标签，**不自动维护对话上下文**。`previous_response_id` 同样不传递上下文。要实现多轮对话，必须通过 `conversation_history` 参数手动传入历史消息。

### 创建 Run

```bash
curl -s http://localhost:8642/v1/runs \
  -H "Authorization: Bearer change-me-local-dev" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "hermes-agent",
    "input": "周杰伦是谁",
    "session_id": "runs-demo-session"
  }'
```

返回：

```json
{
  "run_id": "run_abc123",
  "status": "started"
}
```

### 查询 Run 状态

```bash
curl -s http://localhost:8642/v1/runs/run_abc123 \
  -H "Authorization: Bearer change-me-local-dev"
```

返回：

```json
{
  "object": "hermes.run",
  "run_id": "run_abc123",
  "status": "completed",
  "session_id": "runs-demo-session",
  "model": "hermes-agent",
  "last_event": "run.completed",
  "output": "周杰伦（Jay Chou）……",
  "usage": {"input_tokens": 15636, "output_tokens": 299, "total_tokens": 15935}
}
```

### 多轮对话（需传 conversation_history）

```bash
curl -s http://localhost:8642/v1/runs \
  -H "Authorization: Bearer change-me-local-dev" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "hermes-agent",
    "input": "ta有几个小孩",
    "session_id": "runs-demo-session",
    "conversation_history": [
      {"role": "user", "content": "周杰伦是谁"},
      {"role": "assistant", "content": "周杰伦（Jay Chou），华语乐坛创作歌手，2015年与昆凌结婚。"}
    ]
  }'
```

> 客户端需要缓存每一轮的 input 和 output，作为 `conversation_history` 传入下一轮。

### 订阅 Run 事件（SSE）

```bash
curl -s -N http://localhost:8642/v1/runs/run_abc123/events \
  -H "Authorization: Bearer change-me-local-dev"
```

返回 Server-Sent Events 流，包含 `message.delta`（token 增量）、工具调用进度和生命周期事件。

### 停止 Run

```bash
curl -s -X POST http://localhost:8642/v1/runs/run_abc123/stop \
  -H "Authorization: Bearer change-me-local-dev"
```

返回：

```json
{"status": "stopping"}
```

> Hermes 会在下一个安全中断点请求活跃 agent 停止。终端状态（completed/failed/cancelled）会在短暂保留后清除。

## 其他端点

| 端点 | 说明 |
|------|------|
| `GET /health` | 健康检查 |
| `GET /v1/models` | 查看可用模型 |
| `GET /v1/responses/{id}` | 检索已存储的响应 |
| `DELETE /v1/responses/{id}` | 删除已存储的响应 |
| `POST /v1/runs` | 创建 agent run（支持 session_id / conversation_history） |
| `GET /v1/runs/{run_id}` | 查询 run 状态 |
| `GET /v1/runs/{run_id}/events` | 订阅 run 事件流（SSE） |
| `POST /v1/runs/{run_id}/stop` | 停止运行中的 run |
