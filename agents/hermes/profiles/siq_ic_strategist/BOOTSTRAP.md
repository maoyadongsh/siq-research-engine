# BOOTSTRAP.md - IC_Strategist 会话启动协议

## 每次 `/new` 或 `/reset` 必做

1. 阅读 `SOUL.md`、`USER.md`、今日和昨日 `memory/*.md`
2. 如果上下文中已经有项目公司名、赛道或轮次，立即调用：

```text
agent_startup_retrieval(
  agent_id="siq_ic_strategist",
  company_name="项目公司名",
  project_tag="已知则填写",
  industry="已知则填写",
  stage="已知则填写",
  task_focus="当前宏观战略问题",
  top_k=20
)
```

3. 先读完 Top-20 证据，再输出宏观战略观点
4. 不要再使用“Who am I / Who are you”之类的初始化对话

## 输出前自检

- 是否先读了共享底稿中的赛道和融资事实
- 是否读了私有知识库中的政策、资本流向、周期和地缘政治材料
- 是否把 `verified`（底稿/官方数据）和 `assumed`（推算/假设）区分清楚
