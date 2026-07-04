# KR PDF Wiki Pipeline Design

## Status

Approved direction: build the first KR annual-report wiki pipeline from PDF parser outputs, then keep the package compatible with PostgreSQL and market evidence APIs. DART/XBRL may enrich the same package later, but it is not required for the first production pass.

## Goals

- Generate a Korean market wiki root at `data/wiki/kr_reports` from completed KR PDF parser results.
- Follow the A-share wiki idea of company-centric reports, metrics, evidence, semantic routing, and auditable catalogs.
- Keep KR data isolated from the A-share `data/wiki/companies` list and A-share `_meta` catalogs.
- Preserve a hard evidence contract so agent answers can cite the original PDF page, table index, markdown line, and parser task.
- Connect generated KR packages to existing frontend/backend market-report interactions.
- Support batch generation for the 30-company KR annual-report sample set.

## Non-Goals

- Do not rebuild the A-share wiki pipeline.
- Do not require DART/XBRL facts for the first KR package generation pass.
- Do not merge KR companies into the A-share dashboard company list.
- Do not make LLM semantic extraction a required dependency for first-pass wiki generation.

## Existing Context

A-share wiki uses `data/wiki/companies/<stock_code>-<name>` with `reports`, `metrics`, `evidence`, `semantic`, and `_meta` catalogs. Its strongest contract is traceability: financial numbers and claims must lead back to `task_id`, `report_id`, PDF page, table index, and markdown line.

Multi-market packages already exist for HK, JP, KR, EU, and US. The API has `MARKET_WIKI_ROOTS`, `MARKET_BUILD_SCRIPTS`, and `MARKET_IMPORT_SCRIPTS`; KR already defaults to `data/wiki/kr_reports`, uses `scripts/kr/build_kr_evidence_package.py`, and imports through `db/imports/import_kr_evidence_package_to_postgres.py` into `dart_kr`.

The current KR scripts are XBRL/API-oriented and accept an optional PDF parser result. The new first-pass workflow inverts that priority: PDF parser output is the primary source, while `xbrl/facts_raw.json` can remain empty with an explicit warning.

## Directory Contract

KR wiki packages live under a separate root:

```text
data/wiki/kr_reports/
  README.md
  AGENTS.md
  _meta/
    company_catalog.json
    report_catalog.json
    ingest_manifest.json
    coverage_report.json
    wiki_naming_contract.md
  companies/
    005930-SamsungElectronics/
      company.md
      company.json
      reports/
        2025-annual_<task_or_rcp>/
          manifest.json
          README.md
          raw/
            report.pdf
            report.metadata.json
          parser/
            document_full.json
            quality_report.json
            financial_data.json
            financial_checks.json
            table_relations.json
            content_list_enhanced.json
          sections/
            report.md
            report_complete.md
            section_index.json
          tables/
            table_index.json
            table_0001.json
          metrics/
            financial_data.json
            financial_checks.json
            load_plan.json
            normalized_metrics.json
            operating_metrics.json
            three_statements.json
            key_metrics.json
            validation.json
          evidence/
            evidence_index.json
            pdf_refs.json
          semantic/
            retrieval_index.json
            segments.json
            facts.json
            claims.json
            note_links.json
            extraction_log.json
          qa/
            quality_report.json
            source_map.json
            extraction_warnings.json
            table_quality_signals.json
          xbrl/
            facts_raw.json
```

The package path is A-share-like, but each report directory remains a valid `market_evidence_package_v1` through `manifest.json`. This lets agents read it like a wiki and lets `/api/market-reports/*`, PostgreSQL import, and vector ingestion read it like a market evidence package.

`company_id` in paths is filesystem-safe: `<six_digit_ticker>-<ascii_company_slug>`. Technical IDs remain explicit inside JSON, for example `company_id: "KR:005930"` and `ticker: "005930"`.

## Metadata And Naming

The KR naming contract is:

- `market`: `KR`
- `ticker`: six-digit KRX ticker, zero-padded
- `company_id`: `KR:<ticker>` unless DART `corp_code` is required for a filing-specific link
- `company_dir`: `<ticker>-<ascii_slug>`
- `report_id`: `<fiscal_year>-annual_<task_id_or_rcp_no>`
- `filing_id`: `KR:<ticker>:<task_id_or_rcp_no>`
- `accounting_standard`: `KIFRS`
- `currency`: `KRW`

Source metadata is read from the KR download manifest, adjacent `*.metadata.json`, parser task metadata, and filename fallback. Missing fields are allowed but must be recorded in `qa/extraction_warnings.json` and `_meta/coverage_report.json`.

## Source Inputs

The first pass consumes completed PDF parser result directories:

```text
data/pdf-parser/results/<task_id>/document_full.json
data/pdf-parser/results/<task_id>/quality_report.json
data/pdf-parser/results/<task_id>/financial_data.json
data/pdf-parser/results/<task_id>/financial_checks.json
data/pdf-parser/results/<task_id>/table_relations.json
data/pdf-parser/results/<task_id>/content_list_enhanced.json
```

The case discovery script also reads:

```text
data/market-report-finder/kr_2025_annual_download_queue_manifest.json
data/market-report-finder/downloads/KR/**/<report>.pdf
```

The parser result is eligible only when `market` is `KR`, `document_full.json` exists, and the parser task is complete or has enough artifacts to build a package. Ambiguous market values are skipped rather than imported into A-share paths.

## Scripts

### `scripts/kr/discover_kr_parsed_cases.py`

Discovers KR parser cases and writes a deterministic case set, for example:

```text
eval_datasets/market_ingestion_cases/kr_30_pdf_cases.json
```

Each case contains `market`, `ticker`, `company_name`, `industry`, `pdf_path`, `parser_result`, `task_id`, `report_year`, `report_type`, `period_end`, `published_at`, and metadata provenance. It filters out CN/HK/JP/EU/US tasks, non-annual reports, missing parser artifacts, and duplicate PDF/task pairs.

### `scripts/kr/build_kr_pdf_wiki_package.py`

Builds one KR PDF wiki package:

```bash
python3 scripts/kr/build_kr_pdf_wiki_package.py \
  --pdf data/market-report-finder/downloads/KR/.../Samsung_KR_005930_2025-12-31_年报_2026-03-10_dart_public_x.pdf \
  --parser-result data/pdf-parser/results/<task_id> \
  --output-root data/wiki/kr_reports \
  --force
```

It writes the A-share-like directory, `manifest.json`, metrics, evidence, semantic seed files, and QA files. It must print the final package directory so existing API job runners can read package detail after build.

### `scripts/kr/kr_pdf_wiki_lib.py`

Holds reusable logic for:

- identity and report ID normalization
- metadata inference from manifest, parser task, and filename
- parser artifact copying
- markdown and section index generation
- table splitting and table index generation
- KR financial data to compatibility metrics
- source map and A-share-style evidence index generation
- semantic retrieval seed generation
- catalog updates
- package validation

This module can reuse `market_report_rules_service.evidence_package` helpers and selected KR profile outputs. It should not duplicate large A-share wikiset logic wholesale.

### `scripts/kr/ingest_kr_case_set.py`

Batch builds the 30-company set:

```bash
python3 scripts/kr/ingest_kr_case_set.py \
  --case-set eval_datasets/market_ingestion_cases/kr_30_pdf_cases.json \
  --output-root data/wiki/kr_reports \
  --force
```

Optional flags:

- `--limit N` for smoke runs
- `--ticker 005930` for focused rebuilds
- `--import-postgres` to call `db/imports/import_kr_evidence_package_to_postgres.py`
- `--vector-ingest` to call `scripts/vector-index/milvus-ingestion/ingest_market_evidence_chunks.py`

The script writes `_meta/ingest_manifest.json` and `_meta/coverage_report.json` with counts, skipped cases, warning summaries, package paths, and validation results.

## Package Artifacts

`manifest.json` remains the primary machine contract. Required fields include `schema_version`, `market`, `filing_id`, `company_id`, `ticker`, `company_name`, `report_type`, `fiscal_year`, `fiscal_period`, `period_end`, `published_at`, `source_url`, `local_source_path`, `accounting_standard`, `parser_version`, `rules_version`, `quality_status`, `pdf_parser_task_id`, `parser_result_dir`, and `artifact_hashes`.

`metrics/financial_data.json` and `metrics/financial_checks.json` are copied from parser outputs when present. `metrics/three_statements.json`, `metrics/key_metrics.json`, and `metrics/validation.json` are compatibility artifacts for A-share-style agents. They are derived from KR `financial_data`, KR quality candidates, and financial checks, not independently invented.

`evidence/evidence_index.json` is the A-share-compatible evidence entry point. `qa/source_map.json` remains the market evidence package entry point. Both point to the same underlying evidence IDs where possible.

`semantic/retrieval_index.json` is a first-pass routing index for agent retrieval. It stores section/table candidates for core financial statements, segment information, revenue, operating profit, net income, total assets, EPS, risks, governance, shareholders, and management discussion. It may contain sparse semantic facts in the first pass, but every row must have source pointers or a warning.

## Evidence Contract

Every generated evidence entry must preserve as many of these fields as the parser can provide:

- `evidence_id`
- `market`
- `company_id`
- `ticker`
- `report_id`
- `filing_id`
- `task_id` or `pdf_parser_task_id`
- `source_type`
- `target`
- `canonical_name`
- `local_name`
- `quote_text`
- `pdf_page_number`
- `table_index`
- `row_index`
- `column_index`
- `md_line`
- `wiki_path`
- `local_path`
- `source_url`
- `confidence`
- `fallback_reason`

Agent answer citations must include at least `report_id` and `pdf_page_number`. Financial table answers should include `table_index` when available. If only `md_line` is available, the citation must resolve the nearest preceding `[PDF_PAGE: n]` marker in `sections/report.md` and mark the page as inferred. Missing page/table anchors are warnings, not silent success.

## Frontend And API Integration

Existing backend settings already define:

```text
MARKET_WIKI_ROOTS["KR"] = data/wiki/kr_reports
MARKET_BUILD_SCRIPTS["KR"] = scripts/kr/build_kr_evidence_package.py
MARKET_IMPORT_SCRIPTS["KR"] = db/imports/import_kr_evidence_package_to_postgres.py
```

The implementation should either point `MARKET_BUILD_SCRIPTS["KR"]` at `build_kr_pdf_wiki_package.py` or keep `build_kr_evidence_package.py` as a compatibility wrapper that delegates to the PDF wiki builder when `--parser-result` is provided with a PDF source.

`market_package_repository.iter_market_packages()` currently scans non-EU packages with a shallow `*/*/*/manifest.json` pattern. The KR A-share-style path requires adding a KR-specific pattern:

```text
companies/*/reports/*/manifest.json
```

Existing endpoints should then work for KR packages:

- `GET /api/market-reports/packages?market=KR`
- `GET /api/market-reports/package?market=KR&package_path=...`
- `GET /api/market-reports/packages/{filing_id}?market=KR`
- `GET /api/market-reports/package/quality?market=KR&package_path=...`
- `GET /api/market-reports/evidence/{evidence_id}?market=KR&package_path=...`
- `GET /api/market-reports/package-file?market=KR&package_path=...&file=sections/report.md`

The frontend market parsing page should expose package links after KR package build:

- show generated `package_path`, `filing_id`, `quality_status`, and warning count
- provide an action to open package detail from `GET /api/market-reports/package`
- show evidence/source buttons for quality candidates and financial checks using `evidence_id`
- open source files through authenticated `/api/market-reports/package-file`
- open PDF page/table evidence using existing PDF source trace UI where `pdf_page_number` and `table_index` exist

The KR package list should appear in market-package panels when the market filter is `KR`. It must not appear in the A-share `/api/wiki/companies/list` dashboard unless a future product decision explicitly adds a cross-market view.

## PostgreSQL And Retrieval Follow-Up

The first wiki pass must keep package files compatible with `db/imports/import_kr_evidence_package_to_postgres.py`. PostgreSQL import should populate `dart_kr.companies`, `filings`, `parse_runs`, `pdf_tables`, `evidence_citations`, `financial_facts`, `operating_metric_facts`, `financial_checks`, and `retrieval_chunks`.

`retrieval_chunks` should preserve `wiki_path`, `evidence_id`, `page_number`, `table_index`, `canonical_name`, and `period_key` so later chat retrieval can cite PDF pages without rereading the full package.

## Validation And Tests

Unit tests should cover:

- KR filename and manifest metadata inference
- KR-only case discovery and market isolation
- one-package directory generation
- `manifest.json` required fields
- A-share-compatible `metrics/three_statements.json`, `evidence/evidence_index.json`, and `semantic/retrieval_index.json`
- evidence entries with PDF page and table anchors
- catalog generation under `data/wiki/kr_reports/_meta`
- backend market package scanning for `companies/*/reports/*/manifest.json`
- frontend/API payloads returning KR package detail and evidence file URLs

Smoke tests should build one known KR parser result, then assert:

- `GET /api/market-reports/packages?market=KR` returns the package
- package detail includes metrics, evidence, tables, QA, and files
- evidence lookup returns `pdf_page_number` and `table_index` for at least one core statement
- no KR package appears in `/api/wiki/companies/list`

## Rollout Plan

1. Add KR PDF wiki library and single-package builder.
2. Add discovery and batch ingest scripts for the 30-company sample set.
3. Add KR-specific market package path scanning in the backend repository helper.
4. Wire the frontend package/evidence actions to existing market-report APIs for KR.
5. Build one package and run API smoke tests.
6. Build the full 30-company set and write coverage report.
7. Optionally import packages into PostgreSQL and vector index after wiki package quality passes.
