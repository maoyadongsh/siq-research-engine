# 财务问答 Benchmark v1

这是 SIQ 财务问答的确定性 benchmark。

默认 CI 模式是 `trace-offline`：它用 golden cases 校验预记录的 `answer_audit_trace`。该模式不调用 LLM，不启动 Hermes，也不连接 PostgreSQL。

`wiki-static` 会用 `wiki_static_artifacts.json` 中的 package 与 SHA-256 绑定校验每个真实公司 case。它会把权威 package manifest 身份和 `document_full.json` 中的 legacy 身份分开验证，然后检查事实。重建的 legacy package 还必须把官方下载 metadata 和 PDF、parser 上传/metadata、artifact manifest，以及精确表格/页面 locator 都绑定到 SHA-256。缺少 package、身份漂移、lineage 漂移、hash 漂移和事实/证据漂移都会失败关闭。

`fixture-contract` 是单独的合成通道。它校验权威 `eval_datasets/market_document_full_postgres/cases.json` 合同声明的 `*:FIXTURE:*` 身份、内容 hash 和事实。合成文档永远不能满足真实公司 `wiki-static` 门禁。

当前 P0 覆盖：

- `trace-offline`：12 个 case，覆盖 CN/HK/US/JP/KR/EU，9 个关键事实、1 次 calculator run、1 次 evidence-missing 拒答、1 次工商银行收入 `financial_claim_mismatch` 攻击、1 次等值跨公司 `financial_evidence_identity_mismatch` 攻击，以及 1 次伪造自由文本 calculator-marker 攻击，该攻击必须以 `financial_calculation_trace_missing` 失败。
- `wiki-static`：7 个跨 CN/HK/US/JP/KR/EU 的真实 `document_full` fact case。全部七个都有完整权威绑定，包括 Vodafone FY2025。
- 证据检查会校验 table/page、quote/html anchor 和其他声明证据字段的必需字段与精确值。

v1 CLI 暴露 `trace-offline`、`wiki-static` 和隔离的 `fixture-contract` 通道。PostgreSQL fallback 评测保留给后续手工或 nightly 门禁。

case `modes` 语义：

- `suite.json` 中的 suite 级默认值会把真实公司 `cases.jsonl` 行分配到 `trace-offline` 和 `wiki-static`。
- ad-hoc case 缺少 `modes` 时，仍表示该 case 会在所有已实现确定性模式中运行：`trace-offline`、`wiki-static` 和 `fixture-contract`。生产 suites 必须设置显式身份范围和通道默认值。
- 只有当 case 没有稳定 `document_full.json` fixture，或只对 answer trace 有意义时，才使用类似 `["trace-offline"]` 的显式列表，例如 calculator 或 refusal cases。
- 未来保留模式例如 `postgres-fallback` 在 v1 schema 中会被有意拒绝，直到 evaluator 实现，避免 PR 门禁静默跳过或误分类 case。

运行：

```bash
python3 scripts/maintenance/run_financial_qa_benchmark.py \
  --mode trace-offline \
  --case-root datasets/eval/financial_qa_benchmark/v1 \
  --trace-log datasets/eval/financial_qa_benchmark/v1/traces/p0_golden_traces.jsonl \
  --output artifacts/eval-runs/financial-qa/financial_qa_benchmark.json \
  --markdown artifacts/eval-runs/financial-qa/financial_qa_benchmark.md
```

P0 要求精确值、期间、单位/币种、source-policy、解析身份、精确声明证据、calculator trace 合规和 guardrail 拒答合规。受保护 claim-attack case 还可以要求精确 guardrail reason 和选定 claim-verifier violation 字段，包括 claimed value 和 evidence value。
