# Market PostgreSQL Schema Equivalence

The multi-market importers write A-share-like row families into market-specific schemas. Some market schemas keep legacy table names for compatibility, so validation should use these equivalence classes instead of requiring identical table names everywhere.

| Row Family | Preferred A-share Name | Market Equivalents |
| --- | --- | --- |
| Company | `companies` | `companies` |
| Filing | `company_filings` | `filings` |
| Parse run | `parse_runs` | `parse_runs` |
| Artifacts | `document_artifacts` | `artifacts`, `parser_artifacts` |
| Sections | `filing_sections` | `filing_sections` |
| Pages | `document_pages` | `document_pages`, `pdf_pages` |
| Content blocks | `content_blocks` | `content_blocks` |
| Tables | `document_tables` | `document_tables`, `pdf_tables`, `html_tables` |
| XBRL contexts | `xbrl_contexts` | `xbrl_contexts` |
| XBRL units | `xbrl_units` | `xbrl_units` |
| Raw XBRL facts | `xbrl_facts_raw` | `xbrl_facts_raw` |
| Financial statements | `financial_statements` | `financial_statements` |
| Statement items | `financial_statement_items` | `financial_statement_items` |
| Balance sheet items | `financial_balance_sheet_items` | `financial_balance_sheet_items` |
| Income statement items | `financial_income_statement_items` | `financial_income_statement_items` |
| Cash-flow statement items | `financial_cash_flow_statement_items` | `financial_cash_flow_statement_items` |
| Key metrics | `financial_key_metrics` | `financial_key_metrics` |
| Operating / differentiated metrics | `operating_metric_facts` | `operating_metric_facts`, industry/company scoped rows in `financial_items_enriched` |
| Wide metrics | `financial_all_metrics_wide` | `financial_all_metrics_wide`, `financial_all_metrics_wide_detail` |
| Quality checks | `financial_checks` | `financial_checks`, `quality_checks` |
| Quality reports | `quality_reports` | `quality_reports` |
| Normalization rules | `financial_normalization_rules` | `financial_normalization_rules` |
| Enriched items | `financial_items_enriched` | `financial_items_enriched` |
| Evidence citations | `evidence_citations` | `evidence_citations` |
| Retrieval chunks | `document_chunks` | `document_chunks`, `retrieval_chunks` |
| Raw payload refs | `raw_payload_refs` | `raw_payload_refs`, artifact rows with payload metadata |

The readiness status used by API/UI follows the design document's operational definition:

```text
postgres_ready = parse_runs > 0 and facts > 0 and tables > 0 and chunks > 0 and evidence > 0
```

Evidence coverage is part of operational readiness. A successfully imported parser result with facts/tables/chunks but no `evidence_citations` is reported as `warning`, not `postgres_ready`.
