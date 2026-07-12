# Market Document Full PostgreSQL Backtest Cases

Tiny `document_full.json` examples for the multi-market PostgreSQL design.

The non-A-share PostgreSQL gate covers **HK, JP, KR, EU, and US**. CN/A-share
fixtures may remain here only as small row-shape contract examples for the
legacy A-share path; CN/A-share imports and PostgreSQL DB gates are not part of
the non-A multi-market gate.

These fixtures are intentionally small. `cases.json` follows the architecture
doc's assertion style (`market`, `company_id`, `report_year`, `period_key`,
`assertions[].expected_value`, `required_evidence`) and adds fixture paths plus
unit/currency/evidence checks. The examples cover the row shapes the importer
family must preserve before writing market schemas:

- EU period-map statement items with `values`, `raw_values`, and `sources`.
- HK/JP/KR row-per-period statement items with `value`, `raw_value`, and `evidence`.
- US SEC HTML/iXBRL facts with `concept`, `context_ref`, `unit`, and `html_anchor`.
- CN period-map fixtures are legacy/A-share contract fixtures only, not
  non-A-share PostgreSQL gate coverage.

Run:

```bash
python3 db/imports/backtests/market_document_full_postgres_backtest.py
```

By default the runner writes:

- `artifacts/eval-runs/local/market_document_full_postgres_backtest.json`
- `artifacts/eval-runs/local/market_document_full_postgres_backtest.md`

Full JSON/Markdown gate outputs should stay under ignored artifact directories
such as `artifacts/eval-runs/local/` or `artifacts/eval-runs/ci/`. The tracked
`backtest_report.json` in this directory is a small redacted summary only; do
not overwrite it with a full gate result.

Current mode is a fixture contract backtest plus a real-sample manifest
preflight: it validates `document_full.json` identity, value, unit/currency,
evidence shapes, fixed fact lookups, and that each non-CN market has at least
three real `document_full.json` samples listed in the manifest. Contract mode
checks only that manifest structure and does not require the files to exist.

Runtime PostgreSQL DDL authority is the checked-in SQL under `db/ddl/*.sql`.
Generated reset DDL from `db/imports/market_ingestion_contract.py` contains
`DROP SCHEMA CASCADE` and is only for contract/dry-run inspection or explicit
unsafe reset calls in tests.

The strict production gate is explicit because it writes to PostgreSQL and
reads real samples from a directory outside the repository checkout. Configure
`SIQ_MARKET_POSTGRES_SAMPLE_ROOT` as the root that replaces the manifest's
leading `data/` path segment. For example, this manifest path:

```text
data/pdf-parser/results/<task_id>/document_full.json
```

resolves to:

```text
$SIQ_MARKET_POSTGRES_SAMPLE_ROOT/pdf-parser/results/<task_id>/document_full.json
```

Run the portable gate entry point with either the environment variable or the
equivalent explicit option:

```bash
python3 scripts/maintenance/run_market_document_full_postgres_gate.py \
  --mode offline-postgres \
  --production-sample-root /srv/siq-market-postgres-samples
```

The gate rejects roots inside the checkout and lists every missing sample before
opening a PostgreSQL connection. The lower-level runner also honors the same
environment variable:

```bash
SIQ_MARKET_POSTGRES_SAMPLE_ROOT=/srv/siq-market-postgres-samples \
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
