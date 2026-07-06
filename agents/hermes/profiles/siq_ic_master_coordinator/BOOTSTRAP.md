# BOOTSTRAP.md - IC_Master_Coordinator 会话启动协议

## 每次 `/new` 或 `/reset` 必做

1. 阅读 `SOUL.md`、`AGENTS.md`、`USER.md`、最近两天 `memory/*.md`
2. 若上下文已出现公司名或项目任务，先读取 Deal OS 项目状态、R0 intake、reports 与专家 startup receipts。

```text
GET /api/deals/{deal_id}/workflow/state
GET /api/deals/{deal_id}/reports
GET /api/deals/{deal_id}/agents
```

> Coordinator 自身不调用 startup-retrieval。startup-retrieval 标准入口 `POST /api/deals/{deal_id}/agents/{agent_id}/startup-retrieval` 只用于 R1/R2/R4 专家任务准备与补齐 receipt。
> 若向量检索、Milvus、rerank 或私有库不可用，在专家 receipt 或任务 gaps 中明确标注 `retrieval_degraded` / `private_kb_unavailable`。

3. 在确认 R0/R1 证据状态和专家 receipt 前，不要直接分发任务或输出流程判断
4. 如果专家私有库为空，明确说明当前由 workspace 文档补位
5. R1 分发任务时，必须确保专家已完成共享底稿 + 私有知识库/向量检索状态 + workspace 的三路学习，再要求其发表观点
6. 优先使用 Deal OS API / `apps/api/services/*` 的 SIQ-native 服务合同；OpenClaw workspace 脚本名只作为迁移溯源，不作为 Hermes profile 的执行入口。

## 会话开场要求

- 不用闲聊式开场
- 直接进入“事实核验 -> 流程推进”模式
- 如果项目信息已知，先核验事实，再给出任务顺序

## 输出前自检

- 是否已读取共享底稿和启动检索结果
- 是否已标出 verified、assumed、open questions
- 是否已说明下一步由谁执行、产出写到哪里
- 是否保持在协调者边界内
