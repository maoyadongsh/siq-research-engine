# EU Download Page Mainstream Samples Design

Date: 2026-07-04

## Goal

Add first-class support on the Search & Download page for downloading mainstream European annual reports across the existing EU coverage countries:

- UK
- France
- Germany
- Netherlands
- Switzerland

The user should be able to load either the currently selected country's 10 mainstream annual reports or all five countries' 50 mainstream annual reports, then review, select, and download through the existing report table and download result workflow.

## Current State

The Search & Download page already supports a curated annual-report workflow for JP and KR through `GET /v1/reports/curated-annuals`. The UI loads curated candidates, preselects them, and reuses the existing batch download path.

EU already has country filtering on the page and a `EuAnnualReportCatalog`, but the catalog currently contains only 15 companies: 3 each for UK, FR, DE, NL, and CH. The frontend curated button is currently gated to JP and KR.

## User Workflow

When the user selects EU on the Search & Download page:

1. The page keeps the existing country selector.
2. A primary action loads the selected country's mainstream 10 annual reports.
3. A secondary action loads all five EU countries, 50 annual reports total.
4. Loaded rows appear in the existing annual report table.
5. Rows are selected by default.
6. The existing Download Selected button downloads the selected files.
7. The downloaded files continue to land under:

```text
data/market-report-finder/downloads/EU/<country>/<company>/<year>/年报/
```

## UI Design

For EU only, the curated sample controls should show two actions:

- `载入当前国家 10 家年报`
- `载入五国 50 家年报`

The selected country button uses the current country filter. If the filter is `自动识别`, the UI should either default to all five countries or disable the single-country action with a clear inline hint. The recommended behavior is:

- Single-country action is enabled only when a country is selected.
- Five-country action is always enabled for EU.

JP/KR keep their current single `载入主流 10 家年报` behavior.

## Backend Design

Extend the existing curated annual-report API instead of adding a new endpoint.

`GET /v1/reports/curated-annuals` should continue to accept:

```text
market=EU
report_year=2025
limit=10
```

For EU, add optional `country` query handling using the existing country filter convention:

```text
market=EU&country=UK&report_year=2025&limit=10
```

A country-scoped request returns up to 10 candidates for that country. A five-country request omits `country` and uses `limit=50`; the backend returns a balanced list of 10 candidates per EU country rather than the first 50 entries in raw catalog order.

The implementation should prefer a small typed helper in the EU service/catalog, so the frontend does not need to know company lists or source URLs.

## Catalog Design

Expand `EuAnnualReportCatalog` from 15 entries to 50 entries:

- 10 UK
- 10 FR
- 10 DE
- 10 NL
- 10 CH

Selection principles:

- Prioritize large, mainstream issuers with stable investor-relations pages.
- Prefer official issuer PDF annual reports.
- Allow ESEF ZIP, XHTML, or HTML when PDF is not stable or not available.
- Preserve source metadata: country, ticker, company name, report end, published date, landing URL, file format, source tier.
- Keep the catalog deterministic; no live web search in the request path.

## Error Handling

If a single-country request has fewer than 10 usable entries, return the available candidates and include a warning in the frontend log.

If a download fails, keep the existing batch-download result handling: show per-file success/failure and keep successful downloads.

If the user requests current-country samples while no country is selected, show an inline warning instead of submitting a vague EU request.

## Testing

Frontend:

- Extend curated annuals tests so `canLoadCuratedAnnuals("EU")` is true.
- Test EU selected-country request planning.
- Test EU five-country request planning.
- Test that the apply result dedupes and preselects loaded reports.

Backend:

- Add EU catalog tests for 10 candidates per country.
- Add API/orchestrator tests for EU curated annuals.
- Add download metadata expectations for PDF and non-PDF formats where present.

Smoke:

- Load selected country 10 annual reports in the UI.
- Load five-country 50 annual reports in the UI.
- Download a small selected subset first.
- Then run the full 50 download only after the candidate table is correct.

## Acceptance Criteria

- EU Search & Download page offers both country-10 and five-country-50 curated load actions.
- Each of UK, FR, DE, NL, and CH has 10 mainstream companies in the backend catalog.
- Loading all five countries yields 50 deduplicated annual-report candidates.
- Downloaded files are written under the existing EU download directory with metadata JSON.
- Existing JP/KR curated behavior is unchanged.
- Existing manual EU company search remains unchanged.

## Non-Goals

- This change does not parse the downloaded reports.
- This change does not generate EU evidence packages.
- This change does not import EU reports into PostgreSQL.
- This change does not create new country-specific EU schemas.
