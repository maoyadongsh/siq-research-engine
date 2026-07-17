# 二级市场 MVP 评测运行

Run ID：`2026-07-06-secondary-market-mvp`

## 范围

本次运行对应 `docs/architecture/2026-07-06-siq-mvp-scope.md`，只覆盖港股二级市场 MVP：

- HK 年报 PDF 样本。
- Wiki 证据包定位。
- 质量门禁字段与前端阻断流程。
- PostgreSQL import / vector dry-run 的 `force=true` 显式确认链路。

## 输入

- 稳定 cases：`datasets/market_ingestion/secondary_market_mvp_cases.json`
- UI 回归：`apps/web/e2e/tests/secondary-market-mvp-flow.spec.ts`
- 静态入库评测器：`scripts/maintenance/run_market_ingestion_eval.py`

## 命令

```bash
python scripts/maintenance/run_market_ingestion_eval.py \
  --case-root datasets/market_ingestion \
  --output artifacts/eval-runs/2026-07-06-secondary-market-mvp/market_ingestion_eval_report.json \
  --markdown artifacts/eval-runs/2026-07-06-secondary-market-mvp/market_ingestion_eval_report.md

cd apps/web && npm run e2e -- e2e/tests/secondary-market-mvp-flow.spec.ts
```

## MVP 指标

| 指标 | 来源 | 门禁 |
| --- | --- | --- |
| `official_source_hit_rate` | case `source_tier` / `source_id` | 目标 100% |
| `parser_success_rate` | 找到并评估 package | package 缺失则 case 失败 |
| `evidence_coverage_ratio` | package quality report 或 evidence fallback | 低于 0.8 记 warning |
| `statement_coverage` | `required_statement_status` | 低于 1.0 记 warning |
| `bridge_check_pass_rate` | financial check summary | 低于 0.95 记 fail |
| `answer_citation_rate` | 后续 QA 评测输出 | QA harness 后持续跟踪 |
| `numeric_accuracy` | 后续 QA 评测输出 | QA harness 后持续跟踪 |
| `hallucination_block_rate` | 后续 QA 评测输出 | QA harness 后持续跟踪 |

## 当前说明

- 本运行目录可以保留在 Git 中，因为它记录 MVP 验收计划，不包含已下载披露文件。
- 运行 evaluator 后，生成的 JSON / Markdown 报告会保存在本 README 同级目录。
- CI 不应依赖真实 HKEX、网络、模型或数据库调用；E2E 使用 mock API 响应验证产品流程。
- 该运行同时展示当前产品缺口和产品优势：pipeline 可以定位 package、量化质量，也能显式识别 normalized metric 缺失，而不是静默通过。

## 最新本地结果

生成时间：`2026-07-06T05:16:11Z`

| 指标 | 值 |
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

失败 case 是 `HK 00700 2025 annual`，原因是 normalized metrics 中缺少 `operating_cash_flow`。
