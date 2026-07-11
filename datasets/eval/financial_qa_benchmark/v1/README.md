# Financial QA Benchmark v1

Deterministic benchmark for SIQ financial question answering.

Default CI mode is `trace-offline`: it validates pre-recorded
`answer_audit_trace` records against golden cases. It does not call an LLM, does
not start Hermes, and does not connect to PostgreSQL.

`wiki-static` mode validates the same golden facts directly against referenced
`document_full.json` files. That mode is useful for checking fixture drift before
answer traces are regenerated.

Current P0 coverage:

- `trace-offline`: 9 cases, covering CN/HK/US/JP/KR/EU, 9 key facts, 1
  calculator run, and 1 evidence-missing refusal.
- `wiki-static`: 7 document_full fact cases across CN/HK/US/JP/KR/EU.
- Evidence checks validate required fields and exact values for table/page,
  quote/html anchor, and other declared evidence fields.

The v1 CLI intentionally exposes only `trace-offline` and `wiki-static`.
PostgreSQL fallback evaluation is reserved for a later manual/nightly gate.

Case `modes` semantics:

- Missing `modes` means the case runs in every currently implemented
  deterministic mode: `trace-offline` and `wiki-static`.
- Use an explicit list such as `["trace-offline"]` only when a case does not
  have a stable `document_full.json` fixture or is meaningful only for answer
  traces, such as calculator or refusal cases.
- Reserved future modes such as `postgres-fallback` are intentionally rejected
  by the v1 schema until their evaluator is implemented, so PR gates cannot
  silently skip or misclassify cases.

Run:

```bash
python3 scripts/maintenance/run_financial_qa_benchmark.py \
  --mode trace-offline \
  --case-root datasets/eval/financial_qa_benchmark/v1 \
  --trace-log datasets/eval/financial_qa_benchmark/v1/traces/p0_golden_traces.jsonl \
  --output artifacts/eval-runs/financial-qa/financial_qa_benchmark.json \
  --markdown artifacts/eval-runs/financial-qa/financial_qa_benchmark.md
```

P0 requires exact value, period, unit/currency, source-policy, resolved
identity, exact declared evidence, calculator trace compliance, and guardrail
refusal compliance.
