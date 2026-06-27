# SIQ 事实核查 Agent

`siq_factchecker` 是 SIQ Research Engine 的报告审校 profile，对应 Web 工作台 `/verify` 页面和 API 后端 `/api/factchecker/*`。它对 `siq_analysis` 生成的财务分析报告进行独立复核，关注事实、计算、证据链、逻辑支撑、风险遗漏和输出边界。

## 定位

事实核查 Agent 不给报告打分，也不替代研究员做投资判断。它输出可执行的审校结论：

| Verdict | 含义 |
| --- | --- |
| `approve` | 未发现阻断性问题，可进入阅读或后续跟踪 |
| `request_changes` | 存在需修改的问题，但不阻断整体使用 |
| `block` | 存在事实、计算、证据或合规边界上的严重问题 |

## 核查维度

| 维度 | 检查内容 |
| --- | --- |
| 事实一致性 | 公司、年份、报告期、指标名称、单位和数值是否与证据一致 |
| 计算正确性 | 同比、比率、勾稽关系、单位换算和容差是否合理 |
| 证据完整性 | 是否包含可打开的 PDF 页码、表格、Markdown 行或数据库记录 |
| 逻辑支撑 | 结论是否能由事实推出，风险链条是否完整 |
| 模板合规 | 是否出现评分层、目标价、交易指令或越界表述 |
| 风险遗漏 | 审计意见、问询函、处罚、质押、减持、商誉、政府补助、退市风险等是否被覆盖 |

## 输入

默认核查分析报告：

```text
companies/<company_id>/analysis/
  <stock_code>-<short_name>-<year>-analysis.md
  <stock_code>-<short_name>-<year>-analysis.json
```

核查时会读取同一公司目录下的 metrics、evidence、report、document_full 和 PostgreSQL `pdf2md` 表。

## 输出

核查产物写入：

```text
companies/<company_id>/factcheck/
  <stock_code>-<short_name>-<year>-factcheck.json
  <stock_code>-<short_name>-<year>-factcheck.html
```

JSON 适合工作流处理，HTML 供 Web 工作台展示。

## 技术方案

| 环节 | 实现 | 价值 |
| --- | --- | --- |
| 报告解析 | 读取 Markdown/JSON，抽取声明、数字、指标和风险段落 | 建立待核查对象 |
| 证据对照 | 查询 Wiki metrics、evidence、PDF refs 和数据库表格 | 验证事实来源 |
| 公式检查 | 对同比、比例、勾稽和单位换算做容差判断 | 发现计算错误 |
| 问题分级 | 将问题归类为阻断、需修改、提示 | 便于研究员处理 |
| HTML 渲染 | 输出结构化核查报告 | 与分析报告同一展示体系 |

## 前端与 API

| 项目 | 值 |
| --- | --- |
| 前端页面 | `/verify` |
| API 前缀 | `/api/factchecker/*` |
| Hermes profile | `siq_factchecker` |
| 默认端口 | `18649` |
| 报告目录 | `companies/<company_id>/factcheck/` |

## 输出边界

- 不输出百分制、A/B/C/D、星级或综合评分。
- 不因为语言流畅而放行缺证据报告。
- 不自行补写分析报告，只指出问题和修改方向。
- 不给投资建议、目标价或交易动作。
- 对证据不足的问题要说明缺失字段、缺失来源和建议复核路径。

## 维护检查

```bash
curl -s http://127.0.0.1:18649/health
curl -s http://127.0.0.1:18081/api/wiki/companies/list
```

调整核查规则后，应抽样验证 `approve`、`request_changes`、`block` 三类输出，以及前端报告页能否正确展示问题清单和溯源链接。
