# Market Evidence Package Contract

Version: `market_evidence_package_v1`

This contract is the shared minimum for US, HK, JP, KR, and EU report evidence packages. It deliberately does not change the legacy CN `pdf2md` flow.

## Directory Layout

```text
data/wiki/<market_namespace>/<ticker>/<fiscal_year>/<form_or_type>_<filing_key>/
  manifest.json
  README.md
  raw/
  sections/
  tables/
  xbrl/
  metrics/
  qa/
```

## Required Manifest Fields

`manifest.json` must contain:

```json
{
  "schema_version": "market_evidence_package_v1",
  "market": "US|HK|JP|KR|EU",
  "filing_id": "...",
  "company_id": "...",
  "ticker": "...",
  "company_name": "...",
  "country": "Required for EU packages: UK|FR|DE|NL|CH",
  "source_id": "sec|hkex|edinet|dart|issuer_annual_report|eu_direct|six_direct",
  "source_tier": "Required for EU packages: official_direct|official_mirror|mainstream_repository",
  "form": "...",
  "report_type": "annual|semiannual|quarterly",
  "fiscal_year": 2025,
  "fiscal_period": "FY|H1|Q1|Q2|Q3|Q4",
  "period_end": "2025-12-31",
  "published_at": "2026-04-01",
  "source_url": "...",
  "local_source_path": "raw/...",
  "document_format": "Required for EU packages: pdf|esef_zip|ixbrl_xhtml|html|xml|unknown",
  "accounting_standard": "US_GAAP|IFRS|HKFRS|CASBE|JGAAP|KIFRS|UNKNOWN",
  "parser_version": "...",
  "rules_version": "...",
  "quality_status": "pass|warning|fail",
  "artifact_hashes": {}
}
```

Market-specific identifiers such as `cik`, `accession_number`, `doc_id`, `edinet_code`, `rcp_no`, or `corp_code` may be added as extra fields.

## Required Artifacts

The following files must exist:

- `metrics/financial_data.json`
- `metrics/financial_checks.json`
- `qa/quality_report.json`
- `qa/source_map.json`

`metrics/financial_data.json` keeps the rules-service contract. Every fact row must include at least one evidence source.

`qa/source_map.json` entries must include stable `evidence_id` values plus enough market-specific location data to jump back to source:

- US: `filing_id`, `accession_number`, `xbrl_tag`, `context_ref`, and `html_anchor` or `source_url`
- HK: `filing_id`, `page_number`, `table_index`, `row_index`, `column_index`
- JP: `filing_id` or `doc_id`, `xbrl_tag/context_ref` or PDF table coordinates
- KR: `filing_id` or `rcp_no`, `xbrl_tag/context_ref` or PDF/XML table coordinates
- EU PDF: `country`, `filing_id`, `page_number`, `table_index`, `row_index`, `column_index`
- EU ESEF/iXBRL: `country`, `filing_id`, `xbrl_tag`, `context_ref`, `unit_ref`, and `fact_id/html_anchor/source_url`

## Quality Report

`qa/quality_report.json` should include:

- `overall_status`
- `section_count`
- `table_count`
- `raw_fact_count`
- `normalized_metric_count`
- `evidence_coverage_ratio`
- `required_statement_status`
- `critical_warnings`
- `parser_warnings`
- `rule_warnings`

## Validation

The shared validator lives at:

```text
services/market-report-rules/src/market_report_rules_service/evidence_package.py
```

It checks required manifest fields, required directories/files, artifact hashes, local source path existence, metrics/checks presence, and evidence coverage.
