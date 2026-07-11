# Market Document Full PostgreSQL Backtest Cases

Tiny `document_full.json` examples for the multi-market PostgreSQL design.

These fixtures are intentionally small. `cases.json` follows the architecture
doc's assertion style (`market`, `company_id`, `report_year`, `period_key`,
`assertions[].expected_value`, `required_evidence`) and adds fixture paths plus
unit/currency/evidence checks. The examples cover the row shapes the importer
family must preserve before writing market schemas:

- CN/EU period-map statement items with `values`, `raw_values`, and `sources`.
- HK/JP/KR row-per-period statement items with `value`, `raw_value`, and `evidence`.
- US SEC HTML/iXBRL facts with `concept`, `context_ref`, `unit`, and `html_anchor`.

Run:

```bash
python3 db/imports/backtests/market_document_full_postgres_backtest.py
```

By default the runner writes:

- `artifacts/eval-runs/local/market_document_full_postgres_backtest.json`
- `artifacts/eval-runs/local/market_document_full_postgres_backtest.md`

Use explicit paths when intentionally refreshing tracked release reports:

```bash
python3 db/imports/backtests/market_document_full_postgres_backtest.py \
  --output eval_datasets/market_document_full_postgres/backtest_report.json \
  --markdown docs/reports/market-document-full-postgres-backtest.md
```

Current mode is a fixture contract backtest plus a real-sample manifest
preflight: it validates `document_full.json` identity, value, unit/currency,
evidence shapes, fixed fact lookups, and that each non-CN market has at least
three local real `document_full.json` samples.

The strict production gate is explicit because it writes to PostgreSQL:

```bash
python3 db/imports/backtests/market_document_full_postgres_backtest.py \
  --db --import-before-db-check --idempotency \
  --production-sample-db --production-agent-query
```

That mode imports the tiny fixtures, imports all real samples from
`production_sample_manifest.json` with same-market samples coexisting, repeats
each import for idempotency, checks table-family/evidence counts, validates fixed
Agent questions through each market's `v_agent_financial_facts` view, and probes
every real sample's imported `parse_run_id` for Agent-view facts, values, and
reviewable evidence. In DB import mode it also compares the same metrics between
the source `document_full`/Wiki package facts and PostgreSQL Agent-view rows so
value, unit/currency, and evidence drift is visible in the report; automatically
generated real-sample comparisons record non-blocking parity warnings for source
facts that current rules do not yet expose in the Agent view.
