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

- `eval_datasets/market_document_full_postgres/backtest_report.json`
- `docs/reports/market-document-full-postgres-backtest.md`

Current mode is a fixture contract backtest: it validates `document_full.json`
identity, value, unit/currency, and evidence shapes before PostgreSQL writes.
The production gate still requires at least three real samples per market,
database roundtrip/idempotency checks, and fixed Agent query validation.
`production_sample_manifest.json` lists three local real `document_full.json`
candidates per non-CN market for that production roundtrip gate.
