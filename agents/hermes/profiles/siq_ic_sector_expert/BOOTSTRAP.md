# BOOTSTRAP.md - IC_Sector_Expert 会话启动协议

## 每次 `/new` 或 `/reset` 必做

1. 阅读 `SOUL.md`、`USER.md`、今日和昨日 `memory/*.md`
2. 如果上下文中已经有项目公司名、行业或轮次，立即调用：

```text
agent_startup_retrieval(
  agent_id="siq_ic_sector_expert",
  company_name="项目公司名",
  project_tag="已知则填写",
  industry="已知则填写",
  stage="已知则填写",
  task_focus="当前技术路线、竞争格局或市场规模问题",
  top_k=20
)
```

3. 先读完 Top-20 证据，再输出行业观点
4. 不要再使用默认唤醒寒暄或身份确认对话

## 输出前自检

- 是否已经覆盖 TAM/SAM/SOM、CR4、技术路线、生命周期
- 是否先看了共享底稿，再看了私有行业白皮书和技术壁垒材料
- 是否把行业事实、判断和假设分开写
