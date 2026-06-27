# SIQ 持续跟踪 Agent

`siq_tracking` 是 SIQ Research Engine 的持续跟踪与预警 profile，对应 Web 工作台 `/tracking` 页面和 API 后端 `/api/tracking/*`。它把一次性的年度分析和事实核查结果转化为可持续观察的事项、指标、预警、更新记录和 HTML 跟踪报告。

## 定位

持续跟踪 Agent 不重新写完整深度报告，也不输出交易指令。它回答：

- 上游分析报告中哪些假设、风险和异常需要继续观察。
- 最新财务指标、公告、舆情或人工输入是否触发预警。
- 哪些变化需要反馈到分析报告和研究结论中。
- 哪些异常可能来自数据抽取、单位、口径或报告期问题，应先复核。

## 核心模块

| 模块 | 输入 | 输出 |
| --- | --- | --- |
| 跟踪事项提取 | 分析报告、核查结果、风险段落、metrics | `tracking-items.md` 和事项清单 |
| 指标面板 | `metrics/key_metrics.json`、三大表指标、历史值 | 指标快照、阈值、异常说明 |
| 舆情与公告观察 | 外部更新文本、公告线索、人工输入 | 更新记录、待核查事项 |
| 预警触发 | 指标阈值、风险关键词、事项状态 | `alerts/*.md` 和预警等级 |
| 报告更新 | 新证据、预警、事项进度 | 综合跟踪 HTML 和更新日志 |
| Agent 对话 | 用户问题、跟踪产物、证据层 | 可追溯的解释和后续动作建议 |

## 输入

标准公司目录：

```text
companies/<stock_code>-<company_name>/
  analysis/*.md
  factcheck/*.json
  metrics/key_metrics.json
  metrics/three_statements.json
  evidence/evidence_index.json
```

推荐至少具备分析报告和关键指标文件。指标字段应包含名称、规范名、单位、年度值和来源信息。

## 输出

跟踪产物写入：

```text
companies/<company_id>/tracking/
  tracking-items.md
  metrics-panel.json
  alerts/
  updates/
  <stock_code>-<short_name>-tracking.html
```

Web 工作台 `/tracking` 读取 HTML 报告，并在右侧接入跟踪 Agent。

## 预警等级

| 等级 | 含义 |
| --- | --- |
| `CRITICAL` | 可能显著改变研究结论或触发重大风险复核 |
| `HIGH` | 需要优先处理的财务、公告或事项异常 |
| `MEDIUM` | 需要跟进观察或等待更多证据 |
| `LOW` | 记录型提示或轻微变化 |

预警必须尽量关联指标、阈值、来源文件和后续复核动作。

## 技术方案

持续跟踪采用“规则阈值 + 事项状态 + 证据引用”的组合：

- 财务异常优先由指标变化、阈值和报告期比较触发。
- 风险事项来自分析报告、核查结果和人工补充。
- 模型负责解释变化和生成后续动作，但不能凭语感判断异常。
- 更新记录和预警应持续写入公司 Wiki，形成可追踪研究资产。

## 前端与 API

| 项目 | 值 |
| --- | --- |
| 前端页面 | `/tracking` |
| API 前缀 | `/api/tracking/*` |
| Hermes profile | `siq_tracking` |
| 默认端口 | `18650` |
| 报告目录 | `companies/<company_id>/tracking/` |

## 输出边界

- 不输出买入、卖出、减仓、止损等交易动作。
- 不输出总分、评级或目标价。
- 不把数据抽取异常直接解释为经营风险，必须先给出复核建议。
- 不覆盖上游分析报告的事实来源，更新必须保留依据和时间。

## 维护检查

```bash
curl -s http://127.0.0.1:18650/health
curl -s http://127.0.0.1:18081/api/wiki/companies/list
```

调整指标阈值或跟踪模板后，应验证跟踪 HTML、预警列表、更新记录和前端 Agent 面板。
