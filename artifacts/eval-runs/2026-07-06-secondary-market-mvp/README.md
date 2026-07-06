# Secondary Market MVP Eval Run

Run ID: `2026-07-06-secondary-market-mvp`

## Scope

本次运行对应 `docs/architecture/2026-07-06-siq-mvp-scope.md`，只覆盖港股二级市场 MVP：

- HK 年报 PDF 样本。
- Wiki evidence package 定位。
- 质量门禁字段与前端阻断流程。
- PostgreSQL import / vector dry-run 的 `force=true` 显式确认链路。

## Inputs

- Stable cases: `datasets/market_ingestion/secondary_market_mvp_cases.json`
- UI regression: `apps/web/e2e/tests/secondary-market-mvp-flow.spec.ts`
- Static ingestion evaluator: `scripts/maintenance/run_market_ingestion_eval.py`

## Commands

```bash
python scripts/maintenance/run_market_ingestion_eval.py \
  --case-root datasets/market_ingestion \
  --output artifacts/eval-runs/2026-07-06-secondary-market-mvp/market_ingestion_eval_report.json \
  --markdown artifacts/eval-runs/2026-07-06-secondary-market-mvp/market_ingestion_eval_report.md

cd apps/web && npm run e2e -- e2e/tests/secondary-market-mvp-flow.spec.ts
```

## MVP Metrics

| Metric | Source | Gate |
| --- | --- | --- |
| official_source_hit_rate | case `source_tier` / `source_id` | target 100% |
| parser_success_rate | package found and evaluated | package missing fails case |
| evidence_coverage_ratio | package quality report or evidence fallback | warning below 0.8 |
| statement_coverage | `required_statement_status` | warning below 1.0 |
| bridge_check_pass_rate | financial check summary | fail below 0.95 |
| answer_citation_rate | future QA evaluation output | tracked after QA harness |
| numeric_accuracy | future QA evaluation output | tracked after QA harness |
| hallucination_block_rate | future QA evaluation output | tracked after QA harness |

## Current Notes

- This run directory is safe to keep in git because it records the MVP acceptance plan and does not contain downloaded filings.
- Generated JSON/Markdown reports are stored beside this README after running the evaluator.
- CI must not depend on real HKEX/network/model/database calls; the E2E uses mocked API responses for the product flow.
- The run demonstrates the current product gap as well as the product strength: the pipeline can locate packages and quantify quality, and it can explicitly identify a normalized metric miss instead of silently passing.

## Latest Local Result

Generated: `2026-07-06T05:16:11Z`

| Metric | Value |
| --- | ---: |
| cases | 2 |
| pass | 1 |
| fail | 1 |
| missing_package | 0 |
| official_source_hit_rate | 100.00% |
| parser_success_rate | 100.00% |
| evidence_coverage_ratio | 100.00% |
| statement_coverage | 100.00% |
| bridge_check_pass_rate | 96.70% |

The failing case is `HK 00700 2025 annual`, with `operating_cash_flow` missing from normalized metrics.
