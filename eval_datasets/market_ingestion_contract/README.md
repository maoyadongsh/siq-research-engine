# Market Ingestion Contract Fixture

This directory is the portable PR-CI fixture for
`scripts/maintenance/run_market_ingestion_eval.py --strict`.

It intentionally contains one small, synthetic HK evidence package. The real
multi-market cases remain in `datasets/market_ingestion/` and resolve packages
from `data/wiki/` only in local, nightly, or release environments.

Run the contract gate from the repository root:

```bash
python3 scripts/maintenance/run_market_ingestion_eval.py \
  --case-root eval_datasets/market_ingestion_contract/cases \
  --legacy-case-root eval_datasets/market_ingestion_contract/cases \
  --wiki-root eval_datasets/market_ingestion_contract/wiki \
  --strict
```
