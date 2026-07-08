# A-Share Aligned Multi-Market Wiki Design

Date: 2026-07-07

## Goal

Other markets must use the same company-centric Wiki folder architecture as the A-share Wiki. Market-specific evidence packages remain useful, but they must live inside a long-lived company Wiki, not beside it as a separate package warehouse.

The target is:

```text
data/wiki/<market>/companies/<company_wiki_id>/
  company.json
  company.md
  _index.json
  reports/<report_id>/
  metrics/reports/<report_id>/
  metrics/latest/
  evidence/
  semantic/
  graph/
  analysis/
  factcheck/
  tracking/
  legal/
  obsidian/
```

For A-share legacy compatibility, `data/wiki/companies/<stock>-<name>/` remains valid, but new non-A-share market design should use `data/wiki/<market>/companies/...`.

## Why This Shape

The current A-share Wiki is more than a parsed report archive. It is a company knowledge workspace:

- `reports/<report_id>/` stores the original parsed report and artifact manifest.
- `metrics/latest` and `metrics/reports/<report_id>` provide the first-class numeric fact layer.
- `evidence/` stores company-level evidence indexes and source refs.
- `semantic/` stores retrieval, subject profile, facts, claims, relations, and note links.
- `graph/` stores fact, claim, note, segment, and graph index assets.
- `analysis/`, `factcheck/`, `tracking/`, and `legal/` store downstream agent outputs.
- `_index.json` gives frontend and agents a single company status entry.

Other markets should therefore not stop at:

```text
data/wiki/us/companies/NVDA-NVIDIA/reports/2025-10-K-xxx/manifest.json
```

They should grow the same company workspace around that report.

## Canonical Directory Contract

### Root

```text
data/wiki/<market>/
  _meta/
    AGENT_GUIDE.md
    company_catalog.json
    package_index.json
    quality_summary.json
    migration_report.json
  companies/
```

`<market>` should be lowercase and stable:

| Market | Root |
| --- | --- |
| US SEC | `data/wiki/us/` |
| HK | `data/wiki/hk/` |
| JP | `data/wiki/jp/` |
| KR | `data/wiki/kr/` |
| EU | `data/wiki/eu/` |

Avoid new legacy roots such as `hk_reports`, `jp_reports`, or `us_sec` as primary storage. They may remain migration inputs only.

### Company ID

`company_wiki_id` should be deterministic, human-readable, and path-safe.

| Market | Preferred company_wiki_id |
| --- | --- |
| A-share | `<6-digit-stock_code>-<short_name>` |
| HK | `<5-digit-hkex_code>-<company_slug>` |
| US | `<ticker>-<company_slug>` |
| JP | `<ticker>-<company_slug>` or `<edinet_code>-<company_slug>` when ticker is absent |
| KR | `<6-digit-ticker>-<company_slug>` |
| EU | `<ticker>-<company_slug>` with country/exchange in `company.json` |

The slug rule must strip path separators, normalize whitespace to `-`, and keep enough company name to make folders inspectable.

### Company Layer

Every market company folder must contain:

```text
company.json
company.md
_index.json
reports/
metrics/
evidence/
semantic/
graph/
analysis/
factcheck/
tracking/
legal/
obsidian/
```

Empty folders are allowed at migration time. This preserves the same workspace contract for agents and UI.

## `company.json` Required Shape

All markets should expose these common fields:

```json
{
  "schema_version": "<market>_company_wiki_v1",
  "market": "US",
  "company_id": "US:0001045810",
  "company_wiki_id": "NVDA-NVIDIA-Corporation",
  "company_wiki_path": "data/wiki/us/companies/NVDA-NVIDIA-Corporation",
  "primary_report_id": "2025-10-K_0001045810-25-000023",
  "report_count": 1,
  "reports": [],
  "metrics": {
    "latest": {},
    "by_report": {}
  },
  "evidence": {},
  "updated_at": "2026-07-07"
}
```

Market-specific identity fields are additive:

| Market | Additive fields |
| --- | --- |
| HK | `ticker`, `hkex_stock_code`, `exchange`, `currency`, `industry_profile` |
| US | `ticker`, `cik`, `exchange`, `sic`, `fiscal_year_end`, `accounting_standard` |
| JP | `ticker`, `edinet_code`, `securities_code`, `exchange`, `accounting_standard` |
| KR | `ticker`, `corp_code`, `stock_code`, `exchange`, `accounting_standard` |
| EU | `ticker`, `lei`, `country`, `exchange`, `isin`, `accounting_standard` |

Consumers must rely on the common fields first and use market fields only when market-specific logic is needed.

## Report Layer

Each disclosure lives under:

```text
reports/<report_id>/
  manifest.json
  README.md
  raw/
  parser/
  sections/
  tables/
  xbrl/
  metrics/
  qa/
```

PDF parser based packages may also include:

```text
reports/<report_id>/
  report.md
  report.json
  document_full.json
  artifact_manifest.json
  images/
```

SEC or XBRL-first packages may instead keep HTML/iXBRL/XBRL artifacts in `raw/`, `sections/`, `tables/`, `xbrl/`, `metrics/`, and `qa/`. The key rule is that `manifest.json`, `metrics/financial_data.json`, `metrics/financial_checks.json`, `qa/quality_report.json`, and `qa/source_map.json` remain the package-level contract.

## Metrics Layer

The A-share precedence should be copied:

```text
metrics/reports/<report_id>/
  three_statements.json              # A-share style when available
  key_metrics.json                   # A-share style when available
  validation.json                    # A-share style when available
  financial_data.json                # market package contract
  financial_checks.json              # market package contract
  normalized_metrics.json            # market package contract

metrics/latest/
  three_statements.json
  key_metrics.json
  validation.json
  financial_data.json
  financial_checks.json
  normalized_metrics.json
```

For non-A-share markets, `financial_data.json` and `normalized_metrics.json` can be generated first. `three_statements.json` is the compatibility bridge for A-share-style analysis agents and should be added when the market parser can confidently map the three statements.

Read priority:

1. `metrics/reports/<primary_report_id>/...`
2. `metrics/latest/...`
3. Legacy market package path under `reports/<report_id>/metrics/...`

## Evidence Layer

Company-level evidence should mirror A-share:

```text
evidence/
  evidence_index.json
  pdf_refs.json
  image_manifest.json
  source_map_latest.json
```

For HTML/iXBRL markets, `pdf_refs.json` can be absent or replaced by source refs that include:

- `source_url`
- `local_source_path`
- `html_anchor`
- `xpath`
- `xbrl_fact_id`
- `table_id`
- `section_id`

The package-level `qa/source_map.json` remains the precise report evidence map. Company-level `evidence/evidence_index.json` is the stable entrypoint for agents.

## Semantic And Graph Layer

Every market should reserve:

```text
semantic/
  retrieval_index.json
  subject_profile.json
  facts.json
  claims.json
  relations.json
  document_links.json
  note_links.json
  evidence_semantic.json
  llm/<report_id>/

graph/
  graph_index.json
  company.md
  report.md
  facts/
  claims/
  notes/
  segments/
```

The first migration can create folders only. Later enrichment scripts should write the same file names so analysis, factcheck, tracking, and legal agents do not need market-specific folder logic.

## `_index.json`

`_index.json` should stay a lightweight status file for frontend and agent startup:

```json
{
  "schema_version": "<market>_company_index_v1",
  "market": "US",
  "company_id": "US:0001045810",
  "company_wiki_id": "NVDA-NVIDIA-Corporation",
  "primary_report_id": "2025-10-K_0001045810-25-000023",
  "data": {},
  "analysis": {},
  "factcheck": {},
  "tracking": {},
  "legal": {},
  "updated_at": "2026-07-07"
}
```

The existing A-share `update_company_index.py` can become the baseline for a market-aware updater.

## Rebuild Rule

Existing non-A-share Wiki package roots are audit targets and deletion/rebuild targets, not authoritative generation inputs:

```text
data/wiki/hk/companies/
data/wiki/eu/companies/
data/wiki/jp/companies/
data/wiki/kr/companies/
```

The authoritative source for PDF markets is the parser result archive, normally under:

```text
data/pdf-parser/results/<task_id>/
```

Existing `data/wiki/<market>/companies/...` artifacts may be read only for audit reports, gap analysis, and identity reconciliation. They must not be copied forward as evidence, parser output, metrics, or report content during rebuild.

Rebuild scripts should:

1. Discover parser result directories and market report metadata.
2. Derive `company_wiki_id` and `report_id` from market metadata and parser manifests.
3. With explicit `--apply --purge-existing`, remove or archive stale target Wiki folders for the selected market/company/report.
4. Create the A-share-aligned company folder layout.
5. Import from parser outputs into `reports/<report_id>/`.
6. Generate `manifest.json`, `report.json`, `artifact_manifest.json`, and hash manifests from the parser result files.
7. Generate metrics, evidence, table relation, company, and index files from parser outputs and market adapters.
8. Refresh `_meta/company_catalog.json`, `_meta/package_index.json`, and `_meta/quality_summary.json`.
9. Validate quality gates before PostgreSQL, Milvus, or agent ingestion.

## Script Work Plan

### Phase 0: PDF Artifact Governance

Before extracting company-level Wiki assets, every PDF market package must be audited against the A-share-aligned parser archive contract.

Implemented entrypoint:

```text
scripts/wiki/audit_pdf_market_artifacts.py
```

Responsibilities:

- Scan `data/wiki/{hk,eu,jp,kr}/companies/*/reports/*/manifest.json`.
- Check the PDF parser archive, package contract, root compatibility files, and company workspace files.
- Classify every package:
  - `A_complete_pdf_wiki_archive`
  - `B_missing_root_compat_only`
  - `B_missing_company_workspace`
  - `C_missing_parser_archive`
  - `D_missing_financial_evidence_or_table_layer`
- Emit capability flags:
  - `can_basic_wiki`
  - `can_postgres_import`
  - `can_note_relation_extract`
  - `can_agent_deep_research`
- Write JSON and Markdown reports for batch governance and CI-style checks.

Typical usage:

```bash
cd /home/maoyd/siq-research-engine
python3 scripts/wiki/audit_pdf_market_artifacts.py \
  --markets HK,EU,JP,KR \
  --json-output data/wiki/_meta/pdf_market_artifact_audit.json \
  --markdown-output docs/superpowers/reports/pdf_market_artifact_audit.md
```

The audit is intentionally strict. A market package may be good enough for partial PostgreSQL import but still fail A-share-style deep Wiki readiness if `document_full.json`, `result_complete.md`, or `table_relations.json` is missing.

### Phase 0.1: Parser Result Contract Governance

PDF Wiki rebuilds must use parser results as the authoritative source. Every parser result directory should expose the same contract before Wiki ingestion starts:

```text
data/pdf-parser/results/<task_id>/
  metadata.json
  artifact_manifest.json
  hash_manifest.json
```

Implemented shared service and backfill entrypoint:

```text
apps/pdf-parser/pdf_parser_result_manifest_service.py
apps/pdf-parser/scripts/backfill_result_manifests.py
```

The standard parser result contract requires these core artifacts:

```text
result.md
result_complete.md
document_full.json
content_list_enhanced.json
table_index.json
table_relations.json
financial_data.json
financial_checks.json
quality_report.json
content_list.json
metadata.json
artifact_manifest.json
hash_manifest.json
```

`metadata.json` carries normalized task, market, company, ticker, report period, source, and parser configuration metadata. `artifact_manifest.json` carries the canonical readiness state, artifact sizes, schema/rule versions, JSON validity, per-file `sha256`, and bundle hash. `hash_manifest.json` is a compact hash-only audit view generated from the same service.

Current state after backfill and HK non-annual cleanup:

```text
data/pdf-parser/results: 298 task-backed result directories
CN: 73
HK: 50
EU: 64
JP: 30
KR: 30
US: 51
ready: 298
```

One orphan parser result without a task database record was removed before backfill. Five HKEX files whose filenames looked like annual reports but whose body content was not a full annual report were also removed from the HK annual-report regression set: supplemental announcement, overseas regulatory announcement, and corporate communication notice samples. Future parser completion flow now writes the same contract after core parser artifacts are generated, so later Wiki rebuild scripts should read parser `metadata.json` and `artifact_manifest.json` instead of inferring from existing Wiki packages.

### Phase 0.2: Market Parser Product Regression

Parser product rules must be derived from already parsed outputs, not from market assumptions alone. The current regression entrypoint is:

```text
apps/pdf-parser/scripts/audit_result_contracts.py
```

Typical usage:

```bash
cd /home/maoyd/siq-research-engine
python3 apps/pdf-parser/scripts/audit_result_contracts.py \
  --markets HK,EU,JP,KR \
  --json-output data/pdf-parser/pdf_market_result_contract_audit.json \
  --markdown-output docs/superpowers/reports/pdf_market_result_contract_audit.md
```

Current audited baseline:

```text
HK/EU/JP/KR total: 174
aligned: 174
not_aligned: 0

EU: 64
HK: 50
JP: 30
KR: 30
```

Regression checks:

- `metadata.json`, `artifact_manifest.json`, and `hash_manifest.json` exist.
- `artifact_manifest.core.ready == true`.
- Required parser artifacts exist and JSON artifacts are valid.
- `metadata.market` is present.
- `financial_data.market` and `financial_checks.market` match `metadata.market` for non-CN markets.
- HK/EU/JP/KR carry current `profile_rule_version` in financial artifacts.
- `table_relations.json` uses the shared `document_table_relations_v1` schema.
- `financial_data.json`, `financial_checks.json`, and `quality_report.json` expose schema versions.

The first regression run found 9 real drift cases:

- 8 HK parser results had current core artifacts but stale financial artifacts without `profile_rule_version`.
- 1 EU parser result (`BP-p.l.c`) had CN-style generic financial artifacts even though parser metadata identified it as EU.

Those cases were repaired by rebuilding financial/quality/document-full artifacts from the parser result, not by copying from Wiki packages. `apps/pdf-parser/scripts/rebuild_financial_artifacts.py` was tightened so "current" requires both schema freshness and market match.

Empirical market statistics after HK report-kind cleanup and HK parser-rule enhancement:

| Market | Results | Table relations | Statements | Key metrics | Financial check fails |
| --- | ---: | ---: | ---: | ---: | ---: |
| EU | 64 | 1125 | 152 | 193 | 1 |
| HK | 50 | 78 | 137 | 30 | 32 |
| JP | 30 | 499 | 90 | 238 | 0 |
| KR | 30 | 8486 | 90 | 873 | 0 |

These numbers guide enhancement priorities:

- HK has many financial check failures and very few table relations, but all remaining HK regression samples are now full annual reports. Its parser product rules now distinguish non-full HKEX documents from annual reports by body text, support plain-text bank statement headings, split cash-flow statement pages, 20-F `Statements of Operations` headings, US$ convenience translation column exclusion, and FX cash bridge rows. Its next parser product work should focus on remaining issuer-specific statement gaps, insurer variants, parent-company/segment table filtering, unit harmonization, and conservative note/table relation enrichment.
- EU has broad IFRS coverage but a few zero-statement or weak-statement cases. Its next work should focus on issuer annual report variants, universal registration documents, and direct PDF vs issuer PDF duplication handling.
- JP already has stable three-statement extraction and profile coverage. Its next work should refine J-GAAP/IFRS concept aliases and note relation quality rather than changing the file contract.
- KR has strong extraction but very high table relation counts because DART reports are table-heavy. Its next work should classify relation confidence and filter noisy continuation candidates.

### Phase 0.3: Parser Product Generation Rules

All PDF markets inherit the A-share product chain:

```text
result.md
content_list.json
  -> content_list_enhanced.json
  -> result_complete.md
  -> table_relations.json
  -> financial_data.json / financial_checks.json
  -> quality_report.json / table_index.json
  -> document_full.json
  -> metadata.json / artifact_manifest.json / hash_manifest.json
```

`result_complete.md` remains the human/LLM readable full text product:

```text
original result.md + structured appendix from content_list_enhanced.json
```

`document_full.json` remains the machine-readable full container:

```text
task metadata
source files
markdown content and page index
content_list
content_list_enhanced
quality_report
table_relations
financial_data
financial_checks
images and pdf page refs
artifact status
```

Market-specific rules must not change the file contract. They may only affect:

- market metadata
- report kind
- accounting standard
- statement/table aliases
- core financial table candidates
- financial extraction mappings
- validation rules
- quality warnings and info messages
- table/note relation confidence and labels

Current implementation shape:

| Layer | Shared or market-specific | Code area |
| --- | --- | --- |
| `result_complete.md` generation | Shared | `pdf_parser_content_list_enhanced_service.py` |
| `document_full.json` assembly | Shared | `pdf_parser_document_full_service.py` |
| physical table continuation | Shared baseline | `table_merge.py` |
| financial artifact dispatch | Shared dispatcher | `pdf_parser_financial_service.py` |
| HK financial/profile rules | Market-specific | `hk_financial_artifacts.py`, `hk_quality_adapter.py` |
| EU financial/profile rules | Market-specific | `eu_market_profile.py`, `eu_quality_adapter.py` |
| JP financial/profile rules | Market-specific | `jp_financial_artifacts.py`, `jp_market_profile.py`, `jp_quality_adapter.py` |
| KR financial/profile rules | Market-specific | `kr_financial_artifacts.py`, `kr_market_profile.py`, `kr_quality_adapter.py` |
| parser result governance | Shared | `pdf_parser_result_manifest_service.py`, `audit_result_contracts.py` |

This means future Wiki ingestion can assume a single parser product contract across HK/EU/JP/KR. Market adapters should improve extraction quality, but they must not create market-specific parser result file names.

### Phase 1: Shared Library

Create a shared helper, for example:

```text
scripts/wiki/company_wiki_layout.py
```

Responsibilities:

- slug and path normalization
- `company_wiki_id` generation by market
- standard folder creation
- `company.json` merge/update
- `_index.json` generation
- `_meta/company_catalog.json` generation
- latest report selection
- metrics copy and latest refresh

The helper should be market-aware but not market-specific. It owns directory creation and JSON envelope consistency; market adapters own extraction rules.

### Phase 1.5: PDF Parser Result Rebuilder

Create:

```text
scripts/wiki/rebuild_pdf_market_wiki_from_parser.py
scripts/wiki/discover_pdf_market_parser_results.py
```

Responsibilities:

- Treat parser result directories as the only source of report content, parser evidence, table structure, and financial extraction.
- Default to dry-run and print a full deletion/rebuild plan.
- Require explicit `--apply --purge-existing` before removing stale `data/wiki/<market>/companies/...` targets.
- Prefer archive-to-trash for bulk cleanup, for example `data/wiki/_trash/<market>-<timestamp>/`; use hard delete only behind a separate explicit flag.
- Recreate target company folders from the shared A-share-aligned layout.
- Import parser artifacts into the standard report package locations:
  - `report.md`
  - `document_full.json`
  - `artifact_manifest.json`
  - `parser/result.md`
  - `parser/result_complete.md`
  - `parser/document_full.json`
  - `parser/content_list_enhanced.json`
  - `parser/table_index.json`
  - `parser/table_relations.json`
  - `parser/financial_data.json`
  - `parser/financial_checks.json`
  - `parser/quality_report.json`
  - `tables/table_index.json`
  - `tables/table_relations.json`
  - `metrics/financial_data.json`
  - `metrics/financial_checks.json`
  - `metrics/normalized_metrics.json`
  - `qa/quality_report.json`
  - `qa/source_map.json`
- Generate hash manifests from the actual parser result files used in the rebuild.
- Refuse deep Wiki readiness when parser evidence is absent. Missing `document_full.json`, `result_complete.md`, or `table_relations.json` must trigger an incomplete status or parser rerun, not synthetic evidence.

The rebuilder should be idempotent with respect to parser inputs: the same parser result plus the same market adapter should generate the same target Wiki files and hashes.

### Phase 1.6: Market PDF Adapters

Create market adapters:

```text
scripts/wiki/adapters/hk_pdf_parser.py
scripts/wiki/adapters/eu_pdf_parser.py
scripts/wiki/adapters/jp_pdf_parser.py
scripts/wiki/adapters/kr_pdf_parser.py
```

Adapter responsibilities:

- Resolve market identity, company identity, disclosure type, fiscal year, report period, and accounting standard from parser manifests and market metadata.
- Normalize market-specific report metadata into the shared `company.json` and `manifest.json` fields.
- Generate or validate `table_relations.json` using market-specific section, language, note-reference, and table-title rules.
- Generate A-share-style compatibility metrics when possible:
  - `three_statements.json`
  - `key_metrics.json`
  - `validation.json`
- Generate company-level evidence:
  - `evidence/evidence_index.json`
  - `evidence/pdf_refs.json`
  - `evidence/image_manifest.json`
- Keep market-specific accounting and disclosure concepts in additive fields rather than changing the shared folder layout.

Market-specific rule emphasis:

| Market | Adapter focus |
| --- | --- |
| HK | Bilingual/traditional Chinese and English headings; HKFRS/IFRS statements; bank, insurer, real estate, internet platform statement variants |
| EU | IFRS PDF diversity; country/exchange/currency preservation; annual report vs universal registration document vs integrated report |
| JP | Japanese and English headings; EDINET securities report vs integrated report; Japanese concept aliases |
| KR | Korean statement and note headings; consolidated vs separate statements; DART report structure and Korean concept aliases |

### Phase 2: Market Script Alignment

Update these scripts to use the shared helper:

- `scripts/wiki/rebuild_pdf_market_wiki_from_parser.py`
- `scripts/wiki/discover_pdf_market_parser_results.py`
- `scripts/hk/migrate_hk_reports_to_company_wiki.py`
- `scripts/jp/migrate_jp_reports_to_company_wiki.py`
- `scripts/kr/build_kr_pdf_wiki_package.py`
- `scripts/eu/migrate_eu_reports_to_company_wiki.py`
- `scripts/us-sec/build_sec_wiki.py`
- `scripts/us-sec/build_sec_wiki_index.py`

PDF market scripts should stop using existing Wiki package files as the source of truth. US can keep its SEC/iXBRL-specific source pipeline, but its final company workspace should still match the same A-share-aligned folder contract.

### Phase 3: Consumer Alignment

Update consumers to resolve markets through the same contract:

- API Wiki list endpoints
- agent chat runtime artifact path resolution
- analysis and factcheck `WikiDataAccessor`
- vector ingestion chunk metadata
- PostgreSQL importers

The fallback order should remain Wiki files first, PostgreSQL second, Milvus semantic recall third.

## Non-Goals

- Do not force all markets into A-share accounting labels on day one.
- Do not remove market-specific `manifest.json` fields.
- Do not make PostgreSQL the canonical evidence source.
- Do not make Milvus the primary fact store.

The goal is folder and entrypoint consistency, while preserving market-specific disclosure formats and accounting rules.

## Acceptance Criteria

For each non-A-share market:

- A sample company has `company.json`, `company.md`, `_index.json`, `reports/<report_id>/`, `metrics/latest/`, `metrics/reports/<report_id>/`, `evidence/`, `semantic/`, `graph/`, `analysis/`, `factcheck/`, `tracking/`, `legal/`, and `obsidian/`.
- `company.json.primary_report_id` points to an existing report folder.
- Every report `manifest.json` contains `company_wiki_id`, `company_wiki_path`, `wiki_report_path`, `report_id`, `parse_run_id`, and `artifact_hashes`.
- `_meta/company_catalog.json` can list companies without scanning report internals.
- Importers can write PostgreSQL rows with `wiki_report_path`.
- Milvus chunk metadata contains `market`, `company_wiki_id`, `report_id`, `wiki_package_path`, and `wiki_report_path`.
- Agents can use the same read order as A-share: `company.json` -> `metrics` -> `evidence` -> `semantic` -> `reports` -> PostgreSQL fallback.
