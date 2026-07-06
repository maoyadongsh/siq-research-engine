# SIQ 商业 MVP 范围

日期：2026-07-06

## 结论

首个可售卖样板收敛为“港股二级市场研究闭环”：

1. 从 HKEX / 已下载港股年报进入解析。
2. PDF 解析生成 Markdown、财务数据、质量报告和 Wiki evidence package。
3. Wiki evidence package 列表展示质量门禁：证据覆盖、三大表覆盖、hash 校验、parser/rule warning。
4. 质量为 warning/fail 的 package 默认阻断 PostgreSQL 入库与 Milvus/语义层生成。
5. 研究员显式确认后，可带 `force=true` 触发入库或检索 dry-run，并留下可审计动作。

这个范围优先证明“可信披露解析 + 质量门禁 + 可追溯入库”能闭环，暂不同时扩展一级市场 IC workflow、全部市场适配和真实问答 Agent。

## 用户与场景

目标用户是二级市场研究员、投研数据工程师和质控负责人。

核心演示路径：

1. 打开 `/parse-hk`。
2. 从已下载港股 PDF 或本地上传触发解析。
3. 在同页 `Wiki 证据包` 区域查看已生成 package。
4. 检查质量门禁：
   - `质量 pass/warning/fail`
   - `证据 xx%`
   - `报表 x/3`
   - `hash ok/missing/mismatch`
   - parser/rule warnings 数量
5. 对 warning/fail package 点击“强制入库”或“强制检索”时必须确认原因。
6. 后端收到请求体中的 `force=true` 后才执行对应动作。

## 当前边界

纳入 MVP：

- 市场：HK。
- 文档类型：年度报告 PDF。
- 数据链路：PDF parser -> Wiki evidence package -> quality gates -> PostgreSQL import / vector dry-run。
- 验收：mock API E2E、package contract tests、API gate tests、前端 unit/check。

暂不纳入首版：

- US SEC HTML/iXBRL 作为独立工作台继续保留，不并入 HK MVP 演示。
- JP/KR/EU 仅保留解析页面和后端能力，不挂质量 package 面板。
- 一级市场 Deal data room、R1-R4 workflow、会议室 readiness 不进入本 MVP。
- 真实模型问答、真实 Milvus 写入和真实生产数据库不作为 CI 必须条件。

## 关键验收

代码验收：

```bash
cd packages/market-contracts && uv run python -m pytest tests
cd services/market-report-rules && uv run --extra dev pytest
cd apps/api && uv run python -m pytest tests/test_market_reports_proxy.py tests/test_market_package_repository.py
cd apps/web && npm run test:unit && npm run check:frontend
cd apps/web && npm run e2e -- e2e/tests/secondary-market-mvp-flow.spec.ts
```

产品验收：

- `/parse-hk` 首屏下方存在 `Wiki 证据包` 面板。
- warning/fail package 的入库与检索按钮显示为强制动作。
- 未确认时不触发后端动作。
- 确认后请求体包含 `force: true`。
- API 在未 force 时对 warning/fail package 返回 409，并包含 `quality_gates` 细节。

## 质量指标

本 MVP 的最小评测看板使用这些字段：

| 指标 | MVP 目标 |
| --- | --- |
| official_source_hit_rate | 100%，样本必须来自 HKEX 或显式 official source tier |
| parser_success_rate | package 能被定位并完成解析 |
| evidence_coverage_ratio | 样本均值 >= 0.8，低于阈值进入 warning |
| statement_coverage | 年报三大表覆盖，缺失进入 warning |
| bridge_check_pass_rate | 财务勾稽通过率，fail 阻断入库 |
| answer_citation_rate | 后续问答评测接入，有效引用比例 |
| numeric_accuracy | 后续问答评测接入，数字答案准确率 |
| hallucination_block_rate | 后续问答评测接入，缺证时拒答/降级比例 |

## 证据文件

- E2E：`apps/web/e2e/tests/secondary-market-mvp-flow.spec.ts`
- 稳定样本：`datasets/market_ingestion/secondary_market_mvp_cases.json`
- 单次运行记录：`artifacts/eval-runs/2026-07-06-secondary-market-mvp/README.md`
