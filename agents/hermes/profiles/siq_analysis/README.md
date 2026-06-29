# SIQ 智能分析 Agent

`siq_analysis` 是面向 A 股上市公司的年度经营诊断 profile，对应 Web 工作台 `/analysis` 页面和 API 后端 `/api/analysis/*`。它基于 Wiki、PDF 解析产物、PostgreSQL 证据和财务模型，生成可追溯、可复核的公司年度分析报告。

## 定位

该 Agent 不是财务摘要器，也不是评分器。它的任务是把公开披露数据解释成经营质量、盈利成因、资产质量、现金流质量、债务安全、行业周期、治理合规和后续验证事项。

核心问题包括：

- 利润变化来自真实经营改善、价格/销量变化、成本费用、非经常性损益还是减值因素。
- 经营现金流是否支撑利润。
- 资产负债表是否暴露资产质量和流动性压力。
- 杜邦、自由现金流、营运资本、偿债能力等模型能否由已有字段可靠计算。
- 哪些风险会影响 A 股二级市场语境下的研究判断。
- 哪些证据可以推翻当前结论。

## 技术方案

| 环节 | 实现 | 价值 |
| --- | --- | --- |
| 公司定位 | 读取 Wiki catalog 和公司目录 | 避免手写路径导致主体错误 |
| 证据装配 | 汇总 metrics、evidence、semantic、report.md、PostgreSQL 表格 | 形成可引用事实包 |
| 分章节生成 | 固定章节模板、检查点和长任务控制 | 保证报告结构稳定、可续跑 |
| 财务模型 | 毛利率、扣非、经营现金流/净利润、FCF、偿债、杜邦、CCC 等 | 用可解释公式支撑判断 |
| 引用修复 | 补全 PDF 页码、表格编号、Markdown 行和来源链接 | 让关键结论可回溯 |
| 质量门禁 | 检查证据链、越界表述、评分层、数据缺口和风险链条 | 控制金融报告幻觉 |
| HTML 渲染 | 输出可由前端 iframe 展示的 HTML | 支持阅读、下载和分享 |

## 输入

标准输入来自公司 Wiki 和证据层：

```text
companies/<company_id>/
  company.json
  metrics/three_statements.json
  metrics/key_metrics.json
  metrics/validation.json
  evidence/evidence_index.json
  semantic/retrieval_index.json
  reports/<report_id>/report.md
  reports/<report_id>/document_full.json
```

## 输出

分析报告写入：

```text
companies/<company_id>/analysis/
  <stock_code>-<short_name>-<year>-analysis.md
  <stock_code>-<short_name>-<year>-analysis.json
  <stock_code>-<short_name>-<year>-analysis.html
```

## 证据规则

- 所有关键数字必须来自结构化指标、PDF 表格、Markdown 行或数据库记录。
- 字段不足时明确写出“无法可靠计算”，不填补假设值。
- 若 Wiki 与数据库口径不一致，应说明采用口径和差异来源。
- 引用优先包含 `task_id`、`pdf_page`、`table_index`、`md_line` 和报告文件。
- 不输出综合评分、星级、AAA/CCC、目标价或交易建议。
