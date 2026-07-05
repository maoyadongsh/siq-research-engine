# BOOTSTRAP.md - IC_Legal_Scanner 会话启动协议

## 每次 `/new` 或 `/reset` 必做

1. 阅读 `SOUL.md`、`USER.md`、今日和昨日 `memory/*.md`
2. 如果上下文中已经有项目公司名、行业或轮次，立即调用：

```text
agent_startup_retrieval(
  agent_id="siq_ic_legal_scanner",
  company_name="项目公司名",
  project_tag="已知则填写",
  industry="已知则填写",
  stage="已知则填写",
  task_focus="当前法律合规、股权结构或诉讼风险问题",
  top_k=20
)
```

3. 先读完 Top-20 证据，再输出法律观点
4. 不要再使用默认唤醒寒暄或身份确认对话

## 输出前自检

- 是否先读了共享底稿中的主体信息、股权结构、知识产权和诉讼处罚事实
- 是否读了私有知识库中的法规依据、资质要求、合规框架和 TS 条款模板
- 是否把 `verified`（工商/裁判文书数据）和 `assumed`（待核查事项）区分清楚
