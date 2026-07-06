# SIQ 评测体系补强

日期：2026-07-06

## 目标

把“测试能不能跑”升级为“披露解析和回答质量能不能量化”。P2 阶段先覆盖二级市场 MVP，后续再扩展到 US SEC、JP、EU、一级市场 IC workflow 和真实问答 Agent。

## 目录边界

| 目录 | 用途 |
| --- | --- |
| `datasets/` | 新增、稳定、可版本化的评测样本首选目录 |
| `eval_datasets/` | 历史评测语料与旧回归集，默认保持兼容 |
| `artifacts/eval-runs/` | 单次运行输出、运行说明和临时评测报告 |
| `scripts/maintenance/run_market_ingestion_eval.py` | 静态 market evidence package 评测入口 |

新的 MVP 样本放在 `datasets/market_ingestion/secondary_market_mvp_cases.json`；历史多市场样本仍保留在 `eval_datasets/market_ingestion_cases/`。

## 指标口径

| 指标 | 当前来源 | 解释 |
| --- | --- | --- |
| `official_source_hit_rate` | case `source_tier/source_id` 或源路径 | 样本是否来自官方源或明确官方 source tier |
| `parser_success_rate` | package 是否被定位 | `missing_package` 计为解析未成功 |
| `evidence_coverage_ratio` | `quality_report` 或 evidence fallback | 结构化事实是否有证据 |
| `statement_coverage` | `required_statement_status` | 三大表 present/pass/ok 比例 |
| `bridge_check_pass_rate` | `financial_checks` summary/checks | 财务勾稽通过比例 |
| `answer_citation_rate` | 后续 `answer_evals` | 回答是否带有效引用 |
| `numeric_accuracy` | 后续 `answer_evals` | 数字问答是否准确 |
| `hallucination_block_rate` | 后续 `answer_evals` | 缺证时是否拒答或降级 |

前三类回答质量指标目前是 schema 预留；在真实问答 harness 接入前，评测报告中可能为 `null` / `-`。

## 运行方式

历史默认回归：

```bash
python scripts/maintenance/run_market_ingestion_eval.py
```

二级市场 MVP 单独回归：

```bash
python scripts/maintenance/run_market_ingestion_eval.py \
  --case-root datasets/market_ingestion \
  --output artifacts/eval-runs/2026-07-06-secondary-market-mvp/market_ingestion_eval_report.json \
  --markdown artifacts/eval-runs/2026-07-06-secondary-market-mvp/market_ingestion_eval_report.md
```

前端产品闭环：

```bash
cd apps/web
npm run e2e -- e2e/tests/secondary-market-mvp-flow.spec.ts
```

## CI 策略

- CI 默认不依赖真实官方源、模型服务、Milvus 或生产数据库。
- 静态评测可跑 mock/本地已存在 package；缺包应作为评测结果记录，而不是让 CI 因环境缺少下载文件崩溃。
- E2E 使用 mock API 验证质量门禁和 `force=true` 请求链路。
- 真实下载、真实解析和真实问答评测应进入定期/手动 eval run，不作为普通 PR 的硬依赖。

## 后续扩展

1. 为 `answer_evals` 定义统一 JSON schema。
2. 增加 QA harness：输入 question + evidence package，输出 answer、citations、numeric checks 和 abstention 判断。
3. 将 US SEC 的 iXBRL facts coverage 纳入同一 summary 指标。
4. 为 warning/fail package 建立人工复核样本，跟踪 force override 后的审计结果。
