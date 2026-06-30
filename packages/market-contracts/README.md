# SIQ Market Contracts

Shared Python contracts and readers for SIQ market evidence packages.

This package owns the stable filesystem contract around:

- `manifest.json`
- `metrics/financial_data.json`
- `metrics/financial_checks.json`
- `metrics/normalized_metrics.json`
- `qa/quality_report.json`
- `qa/source_map.json`
- `tables/table_index.json`

It is intentionally small and dependency-light so API, market rules, import
scripts, and batch tools can share the same validation and summary logic.
