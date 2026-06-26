# SIQ 评测语料集 v1.0

本目录包含第一版 KupasEval 评测语料，共 100 条：

- 10 家已入库上市公司
- 每家公司 10 条评测样本
- 覆盖财务指标抽取、利润现金流匹配、偿债能力、现金流质量、三大表勾稽、资产质量、盈利驱动、行业适配、证据忠实度、后续跟踪事项

## 文件说明

- `siq_financial_analysis_eval_v1.csv`：适合人工查看和平台表格导入。
- `siq_financial_analysis_eval_v1.jsonl`：适合程序处理和二次转换。
- `siq_financial_analysis_eval_v1_metadata.json`：语料集元信息和推荐智能体接入配置。

## KupasEval 建议

语料集名称：`SIQ上市公司财报智能分析评测语料集`

语料集类型：`评测语料`

行业类型：`金融`

智能体名称：`SIQ 上市公司财报智能分析智能体`

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

建议先使用非流式 / Single 模式完成联通测试，再启动正式评测。
