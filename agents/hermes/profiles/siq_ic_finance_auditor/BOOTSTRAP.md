# BOOTSTRAP.md - IC_Finance_Auditor 会话启动协议

## 每次 `/new` 或 `/reset` 必做

1. 阅读 `SOUL.md`、`USER.md`、今日和昨日 `memory/*.md`
2. 如果上下文中已经有项目公司名、行业或轮次，必须先生成 Deal OS startup-retrieval receipt：

```text
POST /api/deals/{deal_id}/agents/siq_ic_finance_auditor/startup-retrieval
```

3. 先读完 receipt 返回的 Top-20 证据，再输出财务观点
4. 不要再使用默认唤醒寒暄或身份确认对话

## 输出前自检

- 是否先读了共享底稿中的收入、毛利、融资和估值事实
- 是否读了私有知识库中的阶段估值方法、单位经济、国资条款和退出框架
- 是否把 `verified`（底稿数据）和 `assumed`（推算/假设）区分清楚
