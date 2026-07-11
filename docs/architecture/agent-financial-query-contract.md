# Agent Financial Query Contract

This contract defines the machine-readable fact shape used by SIQ financial
question answering. Runtime answers remain Wiki-first; PostgreSQL is a fallback
when Wiki metrics/evidence are missing, damaged, insufficient for structured
aggregation, or explicitly requested by the user.

Hermes profile routing rules must reference this machine contract through
`agents/hermes/profiles/shared/rules/financial_source_routing_contract.md`.

## AgentFinancialFact

Every fact returned to agents or audit traces should preserve these fields when
available:

| Field | Meaning |
| --- | --- |
| `market` | Market code such as `CN`, `HK`, `US`, `EU`, `JP`, `KR`. |
| `schema` | PostgreSQL schema when the fact came from PostgreSQL. |
| `company_id` | Stable market-scoped company id. |
| `filing_id` | Stable filing/report id. |
| `parse_run_id` | Parser/import run id. |
| `metric_name` | Display metric or original item name. |
| `canonical_name` | Normalized metric key, when mapped. |
| `period` | Period key, fiscal year, or period end. |
| `value` | Extracted numeric value. |
| `raw_value` | Original value text or raw extracted value. |
| `unit` | Unit label. |
| `currency` | Fact currency or reporting currency. |
| `source_page` | PDF page or page-like evidence locator. |
| `table_index` | Table index or HTML table identifier. |
| `bbox` | Optional evidence bounding box when parser output provides coordinates. |
| `evidence_id` | Evidence citation id. |
| `quote` | Evidence quote/snippet. |
| `source_url` | Official source URL if available. |
| `wiki_report_path` | Wiki/evidence package report path if available. |
| `source_type` | `wiki_metrics`, `postgresql`, `postgresql_agent_view`, etc. |

## Runtime Policy

- Production answers do not perform real-time Wiki/PostgreSQL parity checks.
- Wiki metrics/evidence is the primary source for financial facts.
- PostgreSQL facts are fallback evidence and must not be presented as Wiki
  facts.
- Offline backtests and release gates may compare Wiki and PostgreSQL for data
  quality diagnostics.
- Derived calculations must still use the shared financial calculator or
  reconciliation validator, with input facts cited separately.
