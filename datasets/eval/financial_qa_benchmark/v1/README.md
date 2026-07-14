# Financial QA Benchmark v1

Deterministic benchmark for SIQ financial question answering.

Default CI mode is `trace-offline`: it validates pre-recorded
`answer_audit_trace` records against golden cases. It does not call an LLM, does
not start Hermes, and does not connect to PostgreSQL.

`wiki-static` validates each real-company case against the package and SHA-256
binding in `wiki_static_artifacts.json`. It verifies the authoritative package
manifest identity separately from any legacy identity embedded in
`document_full.json`, then checks facts. Reconstructed legacy packages must also
bind the official download metadata and PDF, parser upload/metadata, artifact
manifest, and exact table/page locator by SHA-256. Missing packages, identity
drift, lineage drift, hash drift, and fact/evidence drift fail closed.

`fixture-contract` is the separate synthetic lane. It validates the
`*:FIXTURE:*` identities, content hashes, and facts declared by the authoritative
`eval_datasets/market_document_full_postgres/cases.json` contract. Synthetic
documents can never satisfy the real-company `wiki-static` gate.

Current P0 coverage:

- `trace-offline`: 12 cases, covering CN/HK/US/JP/KR/EU, 9 key facts, 1
  calculator run, 1 evidence-missing refusal, 1 ICBC revenue
  `financial_claim_mismatch` attack, and 1 equal-value cross-company
  `financial_evidence_identity_mismatch` attack, plus 1 forged free-text
  calculator-marker attack that must fail as `financial_calculation_trace_missing`.
- `wiki-static`: 7 real `document_full` fact cases across CN/HK/US/JP/KR/EU.
  All seven have complete authoritative bindings, including Vodafone FY2025.
- Evidence checks validate required fields and exact values for table/page,
  quote/html anchor, and other declared evidence fields.

The v1 CLI exposes `trace-offline`, `wiki-static`, and the isolated
`fixture-contract` lane. PostgreSQL fallback evaluation is reserved for a later
manual/nightly gate.

Case `modes` semantics:

- Suite-level defaults in `suite.json` assign the real-company `cases.jsonl`
  rows to `trace-offline` and `wiki-static` only.
- Missing `modes` in an ad-hoc case still means the case runs in every currently
  implemented deterministic mode: `trace-offline`, `wiki-static`, and
  `fixture-contract`. Production suites must set an explicit identity scope and
  lane defaults.
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
refusal compliance. Guarded claim-attack cases can additionally require the
exact guardrail reason and selected claim-verifier violation fields, including
claimed and evidence values.
