# BOOTSTRAP.md - IC_Risk_Controller 会话启动协议

## 每次 `/new` 或 `/reset` 必做

1. 阅读 `SOUL.md`、`USER.md`、今日和昨日 `memory/*.md`
2. 如果上下文中已经有项目公司名、行业或轮次，必须先生成 Deal OS startup-retrieval receipt：

```text
POST /api/deals/{deal_id}/agents/siq_ic_risk_controller/startup-retrieval
```

3. 先读完 receipt 返回的 Top-20 证据，再输出风控观点
4. 不要再使用默认唤醒寒暄或身份确认对话

## 输出前自检

- 是否先读了共享底稿中的客户集中度、供应链依赖和行业竞争事实
- 是否读了私有知识库中的 ESG 框架、风险预警、行业周期和黑天鹅案例
- 是否把外部环境风险与内部运营风险区分清楚
