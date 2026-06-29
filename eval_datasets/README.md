# SIQ 评测语料

`eval_datasets` 保存 SIQ Research Engine 的评测语料和回归样本。这里既有财报分析评测，也有市场入库和通用文档解析回归集。

## 数据集概况

| 目录 | 目的 |
| --- | --- |
| `eval_datasets/siq_financial_analysis_eval_v1_upload.md` | 财报智能分析评测语料上传说明 |
| `eval_datasets/document_parser_cases` | 通用文档解析回归样本 |
| `eval_datasets/market_ingestion_cases` | 多市场 evidence package / 入库评测样本 |
| `eval_datasets/upload_split` | 财报分析样本拆分文件 |

## 覆盖能力

| 类别 | 评测目标 |
| --- | --- |
| 财务指标抽取 | 能否从结构化指标或 PDF 证据中找出正确数值 |
| 利润现金流匹配 | 能否判断利润是否有经营现金流支撑 |
| 偿债能力 | 能否识别流动性、资产负债率和债务安全问题 |
| 现金流质量 | 能否区分利润和现金回款质量 |
| 三大表勾稽 | 能否发现资产负债表、利润表、现金流量表之间的关系 |
| 资产质量 | 能否识别存货、应收、商誉、减值等资产风险 |
| 证据忠实度 | 是否引用正确来源并避免编造 |
| 多市场入库 | evidence package、load plan 和市场隔离是否正确 |
| 通用文档解析 | Markdown、blocks、source map、表格关系和 Schema 抽取是否稳定 |

## 推荐接入

Request Body Template：

```json
{
  "message": "$(input)"
}
```

Response outputField：

```text
reply
```

建议先用少量样本做非流式联通测试，确认鉴权、字段映射和响应解析正常后，再执行完整评测。

## 评测建议

- 对回答进行事实正确性、证据完整性、口径说明和风险边界四类人工复核。
- 对涉及数字的问题要求单位、期间和来源，不只看自然语言流畅度。
- 对分析型问题关注“事实 -> 解释 -> 风险/改善条件 -> 后续验证”的链条。
- 对缺证据样本允许回答无法确认，但不允许编造数值或页码。
