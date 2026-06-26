# SIQ Assistant

`siq_assistant` 是 SIQ 的入口型财报问答 Hermes profile。它服务主前端 `/chat` 页面和全局普通问答入口，主要回答已入库 A 股年报、OKF 指标、PDF 解析产物、PostgreSQL 证据和项目运行状态相关问题。

## 当前检查结论

检查时间：2026-05-29。`http://127.0.0.1:8642/health` 返回正常。当前 profile 的 `state.db` 中有 207 个 sessions、2041 条 messages。主前端通过聚合后端 `/api/chat/*` 调用该 Agent。

## 当前配置

| 项目 | 当前值 |
| --- | --- |
| Profile 路径 | `/home/maoyd/.hermes/profiles/siq_assistant` |
| API Server | `127.0.0.1:8642` |
| 默认模型 | `kimi-for-coding` |
| Fallback | MiniMax、本地 Qwen3.6 |
| 项目根目录 | `/home/maoyd/siq-research-engine` |
| OKF 根目录 | `/home/maoyd/okf_staging` |
| PDF 解析结果 | `/home/maoyd/siq-research-engine/pdf2md_web/results` |

## 评委技术说明

`siq_assistant` 是五个业务 Agent 中的入口层，技术目标是把普通财报问答也纳入同一套证据契约。它不追求一次生成完整报告，而是快速回答“指标是多少、口径是什么、证据在哪里、能否复核”这类高频问题。

| 维度 | 实现说明 |
| --- | --- |
| 技术架构 | 主前端 `/chat` 或全局悬浮入口 -> 聚合后端 `/api/chat/*` -> Hermes Runs API `:8642` -> 本地 OKF/PostgreSQL/PDF 证据 |
| 技术栈 | Hermes profile、Kimi provider、MiniMax/Qwen fallback、terminal/file/code_execution/session_search 工具 |
| 数据流 | 解析用户公司/年份/指标 -> 读取 OKF metrics/evidence/semantic -> 必要时用 PostgreSQL 补页码和表格 -> 生成含引用来源的回答 |
| 算法模型 | 公司解析、指标别名识别、引用补全、PDF 页码链接生成、证据不足判定 |
| 创新价值 | 把普通聊天从“泛化问答”变成“可追溯财报问答”，让用户每个数字都能回到 PDF 页或数据库记录 |

该 Agent 的工程边界非常明确：它负责轻量查询和解释，遇到完整分析、核查、跟踪或法律意见时会引导到专用 profile。这样可以避免入口助手无限扩张职责，也让后续报告型 Agent 能使用更严格的模板和质量门禁。

## 职责边界

- 可以处理：单指标查询、证据溯源、口径解释、跨年度对比、轻量公司问答、PDF 页码定位、数据缺口说明。
- 不应处理：完整年度分析报告、事实核查报告、持续跟踪预警、正式法律意见书。
- 遇到专用任务时，应引导到 `siq_analysis`、`siq_factchecker`、`siq_tracking` 或 `siq_legal`。

## 数据与引用规则

默认读取优先级：

1. `/home/maoyd/okf_staging/companies/<company_id>/reports/<report_id>/metrics/*.json`
2. `/home/maoyd/okf_staging/companies/<company_id>/reports/<report_id>/evidence/evidence_index.json`
3. `/home/maoyd/okf_staging/companies/<company_id>/reports/<report_id>/semantic/retrieval_index.json`
4. `/home/maoyd/okf_staging/companies/<company_id>/reports/<report_id>/report.md`
5. `/home/maoyd/okf_staging/companies/<company_id>/reports/<report_id>/document_full.json`
6. PostgreSQL `pdf2md` schema，只读补缺和交叉校验

结构化 metrics/evidence/semantic 没命中时，必须先搜索完整 `reports/<report_id>/report.md`，再查完整 `reports/<report_id>/document_full.json` 里的 `markdown.content`、`content_list`、`content_list_enhanced`、`middle_json`、`model_output`、`financial_data`、`financial_checks`。不要把 `companies/<company_id>/graph/report.md` 当完整报告；那只是 Obsidian 图谱节点。不要把 `reports/<report_id>/report.json` 当 full json；它只是摘要/轻量结构化索引。

上海银行有 `2025-quarterly-report` 和 `2025-annual` 两份报告。用户默认问“年报/年度报告/2025 年报”时，使用 `reports/2025-annual/report.md` 与 `reports/2025-annual/document_full.json`；只有明确问季报/季度报告时才使用 `2025-quarterly-report`。

任何财报数字、经营判断或风险判断都必须能回到 OKF 文件、`task_id`、PDF 页码、`table_index`、Markdown 行或数据库表。证据不足时应明确说明，不得猜测。

## 决赛关注点

| 维度 | 本 Agent 贡献 |
| --- | --- |
| 创新性 | 把普通问答也纳入可追溯证据契约，避免财报问答只靠模型记忆生成 |
| 技术难度 | 需要在自然语言问答中解析公司、年份、指标、来源口径并补全 PDF 页码链接 |
| 完成度 | 已接入主前端 `/chat`、聚合后端聊天历史、SSE 流式输出和 Hermes gateway |
| 商业价值 | 为投研人员提供低门槛入口，能快速回答“某家公司某指标从哪来、是否可靠、如何复核” |
