# 市场入库合同 Fixture

本目录是 `scripts/maintenance/run_market_ingestion_eval.py --strict` 使用的可移植 PR-CI fixture。

它有意只包含一个小型合成 HK evidence package。真实多市场 cases 保留在 `datasets/market_ingestion/`，并且只在本地、nightly 或发布环境中从 `data/wiki/` 解析 package。

从仓库根目录运行合同门禁：

```bash
python3 scripts/maintenance/run_market_ingestion_eval.py \
  --case-root eval_datasets/market_ingestion_contract/cases \
  --legacy-case-root eval_datasets/market_ingestion_contract/cases \
  --wiki-root eval_datasets/market_ingestion_contract/wiki \
  --strict
```
