# EU Download Page Mainstream Samples Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add EU curated annual-report download controls on the Search & Download page, supporting both selected-country 10-company loading and five-country 50-company loading.

**Architecture:** Extend the existing curated annuals path instead of adding a new page or endpoint family. The backend owns the EU mainstream company catalog and balanced country selection; the frontend only requests country-10 or all-50 modes and reuses the existing report table and batch download flow.

**Tech Stack:** FastAPI, Pydantic, Python dataclass catalogs, React 19, TypeScript, Vite, existing `node:test` frontend tests, pytest for backend service tests.

---

## File Structure

- Modify `services/market-report-finder/src/market_report_finder_service/api/routes/reports.py`
  - Add optional `country` query parameter for `GET /v1/reports/curated-annuals`.
- Modify `services/market-report-finder/src/market_report_finder_service/services/orchestrator.py`
  - Pass `country` through to market-specific curated annual loaders.
- Modify `services/market-report-finder/src/market_report_finder_service/markets/eu/catalog.py`
  - Expand the EU curated catalog to 50 entries.
  - Add `sample_filings(country=..., limit=..., report_year=...)`.
  - Add balanced all-country sampling for `limit=50`.
- Modify `services/market-report-finder/src/market_report_finder_service/markets/eu/service.py`
  - Add `curated_annual_reports()` delegating to the EU catalog.
- Modify `services/market-report-finder/tests/test_eu_client.py`
  - Add EU catalog count, country filtering, and balanced all-country tests.
- Modify `apps/web/src/features/search-download/curatedAnnuals.ts`
  - Enable EU curated samples.
  - Add selected-country and all-countries request planning.
- Modify `apps/web/src/features/search-download/curatedAnnuals.test.ts`
  - Cover EU gated loading and request planning.
- Modify `apps/web/src/pages/SearchDownload.tsx`
  - Render two EU buttons: current-country 10 and five-country 50.
  - Keep existing JP/KR single-button behavior.
- Optionally modify `apps/web/src/features/search-download/model.ts`
  - Only if a tiny helper label is cleaner than inline copy in `SearchDownload.tsx`.

---

## Fixed EU Company Roster

Keep the existing 15 entries and add 35 entries so each country has 10 mainstream issuers.

UK:
- Existing: AstraZeneca, BP, Barclays
- Add: HSBC Holdings, Shell, Unilever, Diageo, Rio Tinto, Glencore, London Stock Exchange Group

France:
- Existing: TotalEnergies, Sanofi, Air Liquide
- Add: LVMH, L'Oreal, Schneider Electric, BNP Paribas, AXA, Airbus, Vinci

Germany:
- Existing: Siemens, SAP, Deutsche Telekom
- Add: Allianz, Mercedes-Benz Group, BMW, Volkswagen, BASF, Infineon Technologies, Munich Re

Netherlands:
- Existing: ASML, Koninklijke Philips, Heineken
- Add: Shell, Unilever, ING Groep, Prosus, Adyen, Ahold Delhaize, DSM-Firmenich

Switzerland:
- Existing: Nestle, Novartis, Roche
- Add: UBS Group, Zurich Insurance Group, ABB, Richemont, Swiss Re, Sika, Holcim

Source rules for each added entry:
- Prefer the official issuer annual report PDF for fiscal year 2025.
- If the official PDF URL is unstable, use the official annual report landing page as `landing_url` and a stable official HTML/XHTML/ESEF URL as `document_url`.
- Keep `source_tier="official_direct"` unless the source is `filings.xbrl.org`, in which case use `source_tier="official_mirror"` and `source_id="xbrl_filings_esef"`.
- Verify each `document_url` with `curl -L -I` or a small GET before adding it.

---

### Task 1: Backend API Country Pass-Through

**Files:**
- Modify: `services/market-report-finder/src/market_report_finder_service/api/routes/reports.py`
- Modify: `services/market-report-finder/src/market_report_finder_service/services/orchestrator.py`
- Test: `services/market-report-finder/tests/test_eu_client.py`

- [ ] **Step 1: Write failing tests for EU curated country and all-country behavior**

Add these tests to `services/market-report-finder/tests/test_eu_client.py`:

```python
def test_eu_catalog_curated_country_samples_return_ten_for_uk():
    reports = EuAnnualReportCatalog.sample_filings(country="UK", report_year=2025, limit=10)

    assert len(reports) == 10
    assert {item.metadata["country"] for item in reports} == {"GB"}
    assert {item.report_end.year for item in reports} == {2025}


def test_eu_catalog_curated_all_samples_are_balanced_by_country():
    reports = EuAnnualReportCatalog.sample_filings(report_year=2025, limit=50)

    counts: dict[str, int] = {}
    for item in reports:
        counts[item.metadata["country"]] = counts.get(item.metadata["country"], 0) + 1

    assert len(reports) == 50
    assert counts == {"GB": 10, "FR": 10, "DE": 10, "NL": 10, "CH": 10}


def test_eu_finder_exposes_curated_country_samples():
    finder = EuReportFinder()

    reports = finder.curated_annual_reports(country="FR", report_year=2025, limit=10)

    assert len(reports) == 10
    assert {item.metadata["country"] for item in reports} == {"FR"}
```

- [ ] **Step 2: Run tests to verify they fail before implementation**

Run:

```bash
cd /home/maoyd/siq-research-engine/services/market-report-finder
.venv/bin/python -m pytest tests/test_eu_client.py -q
```

Expected: FAIL because `EuAnnualReportCatalog.sample_filings` and `EuReportFinder.curated_annual_reports` do not exist yet.

- [ ] **Step 3: Add `country` query parameter to the route**

Change `curated_annual_reports` in `services/market-report-finder/src/market_report_finder_service/api/routes/reports.py` to:

```python
@router.get("/v1/reports/curated-annuals")
def curated_annual_reports(
    market: Market = Query(...),
    report_year: int | None = Query(default=None, ge=1900, le=2100),
    limit: int = Query(default=10, ge=1, le=50),
    country: str | None = Query(default=None, max_length=16),
):
    return orchestrator.curated_annual_reports(market=market, report_year=report_year, limit=limit, country=country)
```

- [ ] **Step 4: Pass `country` through the orchestrator**

Change the orchestrator method signature and call in `services/market-report-finder/src/market_report_finder_service/services/orchestrator.py`:

```python
def curated_annual_reports(
    self,
    *,
    market: Market,
    report_year: int | None = None,
    limit: int = 10,
    country: str | None = None,
) -> dict:
    finder = self._market(market)
    if not hasattr(finder, "curated_annual_reports"):
        raise HTTPException(status_code=400, detail=f"{market.value} does not provide curated annual-report samples")
    reports = finder.curated_annual_reports(report_year=report_year, limit=limit, country=country)  # type: ignore[attr-defined]
    return {
        "market": market,
        "report_year": report_year,
        "limit": limit,
        "country": country,
        "candidates_total": len(reports),
        "reports": reports,
        "ranking_rule": "Curated mainstream companies by market, balanced by country when applicable.",
        "checked_at": datetime.now(timezone.utc),
    }
```

- [ ] **Step 5: Keep JP/KR compatibility**

Because JP/KR `curated_annual_reports` methods do not accept `country`, update the orchestrator call to branch:

```python
if market == Market.eu:
    reports = finder.curated_annual_reports(report_year=report_year, limit=limit, country=country)  # type: ignore[attr-defined]
else:
    reports = finder.curated_annual_reports(report_year=report_year, limit=limit)  # type: ignore[attr-defined]
```

- [ ] **Step 6: Commit backend pass-through**

Run:

```bash
git add services/market-report-finder/src/market_report_finder_service/api/routes/reports.py \
  services/market-report-finder/src/market_report_finder_service/services/orchestrator.py \
  services/market-report-finder/tests/test_eu_client.py
git commit -m "finder: pass country to curated annual samples"
```

---

### Task 2: EU Catalog Sampling And 50 Entries

**Files:**
- Modify: `services/market-report-finder/src/market_report_finder_service/markets/eu/catalog.py`
- Modify: `services/market-report-finder/src/market_report_finder_service/markets/eu/service.py`
- Test: `services/market-report-finder/tests/test_eu_client.py`

- [ ] **Step 1: Add country order and `sample_filings` helper**

In `services/market-report-finder/src/market_report_finder_service/markets/eu/catalog.py`, add this constant inside `EuAnnualReportCatalog`:

```python
SAMPLE_COUNTRY_ORDER = ("GB", "FR", "DE", "NL", "CH")
```

Add this classmethod:

```python
@classmethod
def sample_filings(
    cls,
    *,
    limit: int = 10,
    report_year: int | None = None,
    country: str | None = None,
) -> list[FilingCandidate]:
    target_country = cls.normalize_country(country)
    entries = [
        entry
        for entry in EU_ANNUAL_REPORT_CATALOG
        if report_year is None or entry.report_end.year == report_year
    ]
    if target_country:
        return [cls.filing_candidate(entry) for entry in entries if entry.country == target_country][:limit]

    by_country: dict[str, list[EuAnnualReportCatalogEntry]] = {code: [] for code in cls.SAMPLE_COUNTRY_ORDER}
    for entry in entries:
        if entry.country in by_country:
            by_country[entry.country].append(entry)

    per_country = max(1, limit // len(cls.SAMPLE_COUNTRY_ORDER))
    selected: list[EuAnnualReportCatalogEntry] = []
    seen: set[str] = set()
    for code in cls.SAMPLE_COUNTRY_ORDER:
        for entry in by_country[code][:per_country]:
            selected.append(entry)
            seen.add(entry.document_url)

    if len(selected) < limit:
        for entry in entries:
            if entry.document_url in seen:
                continue
            selected.append(entry)
            seen.add(entry.document_url)
            if len(selected) >= limit:
                break

    return [cls.filing_candidate(entry) for entry in selected[:limit]]
```

- [ ] **Step 2: Add EU service method**

In `services/market-report-finder/src/market_report_finder_service/markets/eu/service.py`, add:

```python
def curated_annual_reports(self, *, report_year: int | None = None, limit: int = 10, country: str | None = None) -> list[FilingCandidate]:
    return EuAnnualReportCatalog.sample_filings(limit=limit, report_year=report_year, country=country)
```

- [ ] **Step 3: Add and verify the 35 new catalog entries**

Add entries for the fixed roster above to `EU_ANNUAL_REPORT_CATALOG`. Use this exact validation loop before committing:

```bash
cd /home/maoyd/siq-research-engine
PYTHONPATH=services/market-report-finder/src services/market-report-finder/.venv/bin/python - <<'PY'
from collections import Counter
from market_report_finder_service.markets.eu.catalog import EU_ANNUAL_REPORT_CATALOG

counts = Counter(entry.country for entry in EU_ANNUAL_REPORT_CATALOG if entry.report_end.year == 2025)
print(dict(sorted(counts.items())))
assert counts == {"CH": 10, "DE": 10, "FR": 10, "GB": 10, "NL": 10}
for entry in EU_ANNUAL_REPORT_CATALOG:
    if entry.report_end.year == 2025:
        assert entry.document_url.startswith("https://"), entry
        assert entry.landing_url.startswith("https://"), entry
        assert entry.file_format in {"pdf", "zip", "xhtml", "html", "xml"}, entry
PY
```

Expected output:

```text
{'CH': 10, 'DE': 10, 'FR': 10, 'GB': 10, 'NL': 10}
```

- [ ] **Step 4: Verify official source URLs before committing the catalog**

After adding the catalog entries, run this script. It validates every 2025 EU catalog URL without requiring the engineer to copy URLs by hand:

```bash
cd /home/maoyd/siq-research-engine
PYTHONPATH=services/market-report-finder/src services/market-report-finder/.venv/bin/python - <<'PY'
import httpx
from market_report_finder_service.markets.eu.catalog import EU_ANNUAL_REPORT_CATALOG

for entry in EU_ANNUAL_REPORT_CATALOG:
    if entry.report_end.year != 2025:
        continue
    response = httpx.get(entry.document_url, follow_redirects=True, timeout=20, headers={"User-Agent": "SIQ Research URL verifier"})
    print(entry.country, entry.ticker, response.status_code, response.headers.get("content-type"), len(response.content), entry.document_url)
    assert response.status_code < 400, entry.document_url
    assert len(response.content) > 1000, entry.document_url
PY
```

Expected: every printed row has status below 400 and content length above 1000 bytes. If an official issuer blocks scripted GET but opens normally in a browser, replace that entry with a stable official PDF/ESEF/HTML URL that passes this script.

- [ ] **Step 5: Run backend tests**

Run:

```bash
cd /home/maoyd/siq-research-engine/services/market-report-finder
.venv/bin/python -m pytest tests/test_eu_client.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit catalog work**

Run:

```bash
git add services/market-report-finder/src/market_report_finder_service/markets/eu/catalog.py \
  services/market-report-finder/src/market_report_finder_service/markets/eu/service.py \
  services/market-report-finder/tests/test_eu_client.py
git commit -m "finder: expand eu curated annual samples"
```

---

### Task 3: Frontend Curated Annuals Helpers

**Files:**
- Modify: `apps/web/src/features/search-download/curatedAnnuals.ts`
- Modify: `apps/web/src/features/search-download/curatedAnnuals.test.ts`

- [ ] **Step 1: Write failing frontend tests**

Extend `apps/web/src/features/search-download/curatedAnnuals.test.ts` with:

```ts
test('canLoadCuratedAnnuals includes EU', () => {
  assert.equal(canLoadCuratedAnnuals('EU'), true)
})

test('buildCuratedAnnualsRequestPlan supports EU selected-country samples', () => {
  const plan = buildCuratedAnnualsRequestPlan('EU', '2025', { mode: 'country', country: 'UK' })

  assert.equal(plan.params.toString(), 'market=EU&report_year=2025&limit=10&country=UK')
  assert.equal(plan.loadingLog, '正在载入 欧股 当前国家 10 家年报样本 (2025)')
})

test('buildCuratedAnnualsRequestPlan supports EU five-country samples', () => {
  const plan = buildCuratedAnnualsRequestPlan('EU', '2025', { mode: 'all-eu' })

  assert.equal(plan.params.toString(), 'market=EU&report_year=2025&limit=50')
  assert.equal(plan.loadingLog, '正在载入 欧股 五国 50 家年报样本 (2025)')
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm run test:unit -- curatedAnnuals
```

Expected: FAIL because the helper signature only accepts numeric `limit`.

- [ ] **Step 3: Update helper types and request planning**

Replace the current `canLoadCuratedAnnuals` and `buildCuratedAnnualsRequestPlan` implementation in `apps/web/src/features/search-download/curatedAnnuals.ts` with:

```ts
export type CuratedAnnualsPlanOptions =
  | number
  | { mode?: 'default'; limit?: number }
  | { mode: 'country'; country: string; limit?: number }
  | { mode: 'all-eu'; limit?: number }

export function canLoadCuratedAnnuals(market: MarketCode) {
  return market === 'JP' || market === 'KR' || market === 'EU'
}

export function buildCuratedAnnualsRequestPlan(
  market: MarketCode,
  year: string,
  options: CuratedAnnualsPlanOptions = 10,
): CuratedAnnualsRequestPlan {
  const marketLabel = MARKET_CONFIGS[market].label
  const normalized = typeof options === 'number' ? { mode: 'default' as const, limit: options } : options
  const limit = normalized.mode === 'all-eu' ? normalized.limit || 50 : normalized.limit || 10
  const params = new URLSearchParams({ market, report_year: year, limit: String(limit) })
  if (normalized.mode === 'country' && normalized.country) params.set('country', normalized.country)
  if (market === 'EU' && normalized.mode === 'country') {
    return {
      params,
      loadingLog: `正在载入 ${marketLabel} 当前国家 ${limit} 家年报样本 (${year})`,
    }
  }
  if (market === 'EU' && normalized.mode === 'all-eu') {
    return {
      params,
      loadingLog: `正在载入 ${marketLabel} 五国 ${limit} 家年报样本 (${year})`,
    }
  }
  return {
    params,
    loadingLog: `正在载入 ${marketLabel} 主流 ${limit} 家年报样本 (${year})`,
  }
}
```

- [ ] **Step 4: Run frontend helper tests**

Run:

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm run test:unit -- curatedAnnuals
```

Expected: PASS.

- [ ] **Step 5: Commit helper work**

Run:

```bash
git add apps/web/src/features/search-download/curatedAnnuals.ts \
  apps/web/src/features/search-download/curatedAnnuals.test.ts
git commit -m "web: plan eu curated annual requests"
```

---

### Task 4: Frontend EU Buttons On Search Download Page

**Files:**
- Modify: `apps/web/src/pages/SearchDownload.tsx`

- [ ] **Step 1: Add a mode parameter to the load handler**

Change `handleLoadCuratedAnnuals` in `apps/web/src/pages/SearchDownload.tsx` to accept:

```ts
const handleLoadCuratedAnnuals = async (mode: 'default' | 'eu-country' | 'eu-all' = 'default') => {
  if (!canLoadCuratedAnnuals(market)) return
  if (market === 'EU' && mode === 'eu-country' && !marketFilter) {
    addLog('请先选择一个欧股国家，再载入当前国家 10 家年报', 'warn')
    return
  }
  const plan = market === 'EU' && mode === 'eu-country'
    ? buildCuratedAnnualsRequestPlan(market, year, { mode: 'country', country: marketFilter })
    : market === 'EU' && mode === 'eu-all'
      ? buildCuratedAnnualsRequestPlan(market, year, { mode: 'all-eu' })
      : buildCuratedAnnualsRequestPlan(market, year)
```

Keep the existing body of the function after `const plan = ...`.

- [ ] **Step 2: Render EU controls while preserving JP/KR**

Replace the current JP/KR-only curated control block with:

```tsx
          {market === 'EU' ? (
            <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center">
              <button
                type="button"
                onClick={() => handleLoadCuratedAnnuals('eu-country')}
                disabled={curatedLoading || loading || !marketFilter}
                className="inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-primary/25 bg-primary/5 px-4 text-sm font-semibold text-primary transition-colors hover:bg-primary/10 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {curatedLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}
                载入当前国家 10 家年报
              </button>
              <button
                type="button"
                onClick={() => handleLoadCuratedAnnuals('eu-all')}
                disabled={curatedLoading || loading}
                className="inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-border bg-card px-4 text-sm font-semibold text-text transition-colors hover:bg-bg disabled:cursor-not-allowed disabled:opacity-60"
              >
                {curatedLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}
                载入五国 50 家年报
              </button>
              <span className="text-xs leading-5 text-text-muted">
                当前国家需先选择 UK/FR/DE/NL/CH；五国模式会自动载入 50 家
              </span>
            </div>
          ) : (market === 'JP' || market === 'KR') ? (
            <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center">
              <button
                type="button"
                onClick={() => handleLoadCuratedAnnuals()}
                disabled={curatedLoading || loading}
                className="inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-primary/25 bg-primary/5 px-4 text-sm font-semibold text-primary transition-colors hover:bg-primary/10 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {curatedLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}
                载入主流 10 家年报
              </button>
              <span className="text-xs leading-5 text-text-muted">
                {market === 'JP' ? '公司 IR 官方 PDF' : 'DART 官方 PDF'}
              </span>
            </div>
          ) : null}
```

- [ ] **Step 3: Run frontend checks**

Run:

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm run test:unit -- curatedAnnuals
npm run check:frontend
```

Expected: both commands pass.

- [ ] **Step 4: Commit UI work**

Run:

```bash
git add apps/web/src/pages/SearchDownload.tsx
git commit -m "web: add eu curated annual controls"
```

---

### Task 5: End-To-End Verification And Download Smoke

**Files:**
- Runtime data only under `data/market-report-finder/downloads/EU/`
- No code changes expected unless smoke exposes a bug.

- [ ] **Step 1: Run backend tests**

Run:

```bash
cd /home/maoyd/siq-research-engine/services/market-report-finder
.venv/bin/python -m pytest tests/test_eu_client.py tests/test_downloader.py -q
```

Expected: PASS.

- [ ] **Step 2: Run frontend tests**

Run:

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm run test:unit -- curatedAnnuals
npm run check:frontend
```

Expected: PASS.

- [ ] **Step 3: Verify API responses directly**

Run the report finder service if it is not running:

```bash
cd /home/maoyd/siq-research-engine/services/market-report-finder
MARKET_REPORT_DOWNLOAD_DIR=/home/maoyd/siq-research-engine/data/market-report-finder/downloads \
.venv/bin/python -m uvicorn market_report_finder_service.app:app --host 127.0.0.1 --port 18000
```

In another shell, run:

```bash
curl -s 'http://127.0.0.1:18000/v1/reports/curated-annuals?market=EU&country=UK&report_year=2025&limit=10' | python3 -m json.tool | grep -E '"candidates_total"|"country"|"ticker"' | head -30
curl -s 'http://127.0.0.1:18000/v1/reports/curated-annuals?market=EU&report_year=2025&limit=50' | python3 -m json.tool | grep '"candidates_total"'
```

Expected:

```text
"candidates_total": 10
"candidates_total": 50
```

- [ ] **Step 4: Download one small selected subset first**

Use the UI or the batch API to download 2-3 EU entries from different countries. Confirm each successful result has a file and metadata JSON under the existing EU country/company/year annual-report directory structure:

```text
data/market-report-finder/downloads/EU/UK/AstraZeneca-PLC/2025/年报/
```

- [ ] **Step 5: Full 50 download only after subset success**

Use the Search & Download page EU `载入五国 50 家年报` action, leave all rows selected, and click `下载选中披露文件`.

Expected:
- The result panel lists per-file success or failure.
- Successful files are cached with `.metadata.json`.
- The already existing 15 files should report cache hits or dedupe rather than being duplicated.

- [ ] **Step 6: Summarize downloaded country counts**

Run:

```bash
cd /home/maoyd/siq-research-engine
python3 - <<'PY'
from pathlib import Path
root = Path("data/market-report-finder/downloads/EU")
for country in ["UK", "FR", "DE", "NL", "CH"]:
    companies = sorted({p.parts[p.parts.index(country)+1] for p in (root / country).glob("*/*/年报/*") if p.is_file() and not p.name.endswith(".metadata.json") and not p.name.startswith(".")})
    print(country, len(companies), companies)
PY
```

Expected: each country has at least 10 companies.

- [ ] **Step 7: Commit any smoke fixes**

If smoke required code fixes, commit only those fixes:

```bash
git status --short
git add services/market-report-finder/src/market_report_finder_service/markets/eu/catalog.py \
  services/market-report-finder/src/market_report_finder_service/markets/eu/service.py \
  services/market-report-finder/src/market_report_finder_service/api/routes/reports.py \
  services/market-report-finder/src/market_report_finder_service/services/orchestrator.py \
  services/market-report-finder/tests/test_eu_client.py \
  apps/web/src/features/search-download/curatedAnnuals.ts \
  apps/web/src/features/search-download/curatedAnnuals.test.ts \
  apps/web/src/pages/SearchDownload.tsx
git commit -m "fix: stabilize eu curated annual downloads"
```

If no fixes were needed, do not create an empty commit.
