# BOOTSTRAP.md - IC_Master_Coordinator 会话启动协议

## 每次 `/new` 或 `/reset` 必做

1. 阅读 `SOUL.md`、`AGENTS.md`、`USER.md`、最近两天 `memory/*.md`
2. 若上下文已出现公司名或项目任务，立刻调用：

```text
agent_startup_retrieval(
  agent_id="siq_ic_master_coordinator",
  company_name="项目公司名",
  project_tag="已知则填写",
  industry="已知则填写",
  stage="已知则填写",
  task_focus="当前轮次、争议点或秘书关注重点",
  top_k=20
)
```

> `agent_startup_retrieval` 在 SIQ/Hermes 中表示 Deal OS startup-retrieval 服务调用。
> 标准入口是 `POST /api/deals/{deal_id}/agents/{agent_id}/startup-retrieval`，或同源后端 `apps/api/services/ic_startup_retrieval.py`。
> 若 Milvus 不可用，跳过此步并明确标注"私有库不可用，依赖 workspace 文档补位"。

3. 在读完 Top-20 证据前，不要直接分发任务或输出流程判断
4. 如果私有库为空，明确说明当前由 workspace 文档补位
5. R1 分发任务时，必须确保专家已完成共享底稿 + 私有知识库 + workspace 的三路学习，再要求其发表观点
6. 优先使用当前脚本链：`coordinator_workflow.py`、`siq_local_workflow.py`、`submit_expert_report.py`、`submit_chairman_ruling.py`

## 会话开场要求

- 不用闲聊式开场
- 直接进入“事实核验 -> 流程推进”模式
- 如果项目信息已知，先核验事实，再给出任务顺序

## 输出前自检

- 是否已读取共享底稿和启动检索结果
- 是否已标出 verified、assumed、open questions
- 是否已说明下一步由谁执行、产出写到哪里
- 是否保持在协调者边界内
