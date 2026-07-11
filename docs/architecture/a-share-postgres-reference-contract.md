# A Share PostgreSQL Reference Contract

This note freezes the A-share `document_full.json -> PostgreSQL` contract as the reference model for non-A-share market importers. It is descriptive only; the A-share importer remains `db/imports/import_document_full_to_postgres.py`.

## Primary Input

The importer reads a single parser artifact:

```text
data/pdf-parser/results/<task_id>/document_full.json
```

It may use Wiki company metadata for identity enrichment, but financial facts, table coordinates, quality signals, chunks, and citations are derived from `document_full.json`.

## Row Model

| Layer | Tables / Functions | Source Fields |
| --- | --- | --- |
| Document identity | `pdf2md.documents`, `collect_document_params()` | `task`, `source_files`, `artifacts.document_full.json`, `markdown`, `quality_report`, `financial_data`, `financial_checks` |
| Company / filing / parse run | `companies`, `company_filings`, `parse_runs` | `task`, filename identity, `financial_data`, Wiki company metadata as enrichment |
| Artifacts and structure | `document_artifacts`, `document_pages`, `content_blocks`, `document_tables` | `artifacts`, `markdown.pages`, `content_list`, `content_list_enhanced.tables`, `quality_report.table_index` |
| Financial statements | `financial_statements`, `financial_statement_items` | `financial_data.statements[].items[].values` |
| Split fact tables | `financial_balance_sheet_items`, `financial_income_statement_items`, `financial_cash_flow_statement_items` | Statement type plus per-period item values |
| Key metrics | `financial_key_metrics` | `financial_data.key_metrics` |
| Wide query layer | `financial_all_metrics_wide` | Statement items and key metrics grouped by `period_key` |
| Quality and notes | `financial_checks`, `financial_note_links`, `quality_warnings`, `footnotes`, `toc_entries` | `financial_checks`, `content_list_enhanced`, `quality_report.warnings` |
| Retrieval and evidence | `document_chunks`, `evidence_citations`, `raw_payload_refs` | `markdown.pages`, tables, financial checks, artifact refs |
| Enriched layer | `financial_items_enriched` | Refreshed by `db/dml/002_build_financial_items_enriched.sql` from raw fact tables |

## Idempotency

`db/dml/001_upsert_document_full.sql` deletes child rows by `task_id` before rewriting the document's facts, tables, chunks, citations, and derived rows. Existing parse run identity is reused where present.

The delete block covers:

```text
raw_payload_refs
financial_all_metrics_wide
financial_cash_flow_statement_items
financial_income_statement_items
financial_balance_sheet_items
financial_checks
financial_key_metrics
financial_statement_items
financial_statements
financial_note_links
toc_entries
footnotes
quality_warnings
document_tables
content_blocks
document_pages
document_artifacts
document_chunks
evidence_citations
```

## Guardrail Test

`db/imports/tests/test_a_share_document_full_contract.py` is a read-only contract test. It imports helper functions from the A-share importer and verifies that a fixture `document_full.json` produces the expected A-share row families without opening a database connection or changing A-share code.
