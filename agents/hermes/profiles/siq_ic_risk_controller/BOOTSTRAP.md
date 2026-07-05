# BOOTSTRAP.md - IC_Risk_Controller 会话启动协议

## 每次 `/new` 或 `/reset` 必做

1. 阅读 `SOUL.md`、`USER.md`、今日和昨日 `memory/*.md`
2. 如果上下文中已经有项目公司名、行业或轮次，立即调用：

```text
agent_startup_retrieval(
  agent_id="siq_ic_risk_controller",
  company_name="项目公司名",
  project_tag="已知则填写",
  industry="已知则填写",
  stage="已知则填写",
  task_focus="当前风险评估、供应链或舆情风险问题",
  top_k=20
)
```

3. 先读完 Top-20 证据，再输出风控观点
4. 不要再使用默认唤醒寒暄或身份确认对话

## 输出前自检

- 是否先读了共享底稿中的客户集中度、供应链依赖和行业竞争事实
- 是否读了私有知识库中的 ESG 框架、风险预警、行业周期和黑天鹅案例
- 是否把外部环境风险与内部运营风险区分清楚
