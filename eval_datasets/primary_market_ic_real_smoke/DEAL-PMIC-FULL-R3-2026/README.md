# DEAL-PMIC-FULL-R3-2026

仅用于合成评测。这是输入候选，不是 golden 结果。它不包含 Hermes 输出、factcheck 结果、人工确认或质量批准。

- Golden case：`GOLDEN-PMIC-FULL-R3`
- 预期行为：`evidence_complete_high_material_conflict`

重新生成或校验所有独立候选输入：

```bash
python eval_datasets/primary_market_ic_real_smoke/generate_golden_suite_fixtures.py
python eval_datasets/primary_market_ic_real_smoke/generate_golden_suite_fixtures.py --check
```
