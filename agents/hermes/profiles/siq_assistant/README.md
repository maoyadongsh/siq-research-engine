# SIQ 通用问答助手

`siq_assistant` 是 SIQ Research Engine 的入口型财报问答 profile，对应 Web 工作台 `/chat` 页面和 API 后端 `/api/chat/*`。它用于快速回答公司、年份、指标、口径、证据位置和系统使用相关问题。

## 定位

通用助手负责“轻量查询和解释”，不负责生成完整年度分析报告、核查报告、持续跟踪报告或正式法务意见。遇到专业任务时，应引导用户进入对应功能：

| 任务 | 推荐入口 |
| --- | --- |
| 年度经营诊断报告 | `siq_analysis` |
| 已生成报告的事实核查 | `siq_factchecker` |
| 后续事项和预警跟踪 | `siq_tracking` |
| 法规检索和合规意见书 | `siq_legal` |

## 核心能力

| 能力 | 说明 |
| --- | --- |
| 指标查询 | 回答营业收入、净利润、现金流、资产负债率等指标数值与口径 |
| 证据溯源 | 指出数据来自哪个报告、PDF 页、表格、Markdown 行或数据库记录 |
| 跨期对比 | 对同一公司不同年度或不同报告期做轻量比较 |
| 数据缺口说明 | 找不到可靠数据时说明缺口，而不是猜测 |
| 附件问答 | 通过 API 后端处理聊天附件并纳入上下文 |
| 会话管理 | 支持历史会话、SSE 流式输出、停止和运行恢复 |

## 数据优先级

默认按以下顺序查找证据：

1. Wiki 公司主数据和报告目录。
2. `metrics/*.json` 中的结构化财务指标。
3. `evidence/evidence_index.json` 和 `pdf_refs.json`。
4. `semantic/retrieval_index.json`。
5. `reports/<report_id>/report.md` 和 `document_full.json`。
6. PostgreSQL `pdf2md` schema 中的页面、表格、指标和引用记录。

回答财报数字时应尽量给出单位、期间、来源和可复核路径。证据不足时输出“无法可靠确认”的原因。

## 前端与 API

| 项目 | 值 |
| --- | --- |
| 前端页面 | `/chat` |
| API 前缀 | `/api/chat/*` |
| Hermes profile | `siq_assistant` |
| 默认端口 | `18642` |
| 主要后端模块 | `apps/api/routers/chat.py`, `apps/api/services/agent_chat_runtime.py` |

## 输出边界

- 不输出投资评级、目标价、买入/卖出/减仓等交易动作。
- 不用模型记忆替代本地证据。
- 不把非官方文件当作最终事实来源。
- 不在回答中泄露 API Key、数据库口令、用户会话或本地隐私路径。
- 对口径不一致、单位不一致、报告期不一致的指标必须提醒用户。

## 维护检查

```bash
curl -s http://127.0.0.1:18642/health
curl -s http://127.0.0.1:18081/health
```

前端聊天问题应同时验证普通文本、附件上传、停止生成、历史会话和页面刷新后的运行恢复。
