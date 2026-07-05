### Task 1: Add KR Catalog Target Tests

**Files:**
- Create: `services/market-report-finder/tests/test_kr_catalog.py`

**Interfaces:**
- Consumes: `KR_ANNUAL_REPORT_CATALOG`, `KrAnnualReportCatalog.resolve_company()`.
- Produces: failing tests that define the 30-company target and catalog hygiene requirements.

- [ ] **Step 1: Write the failing catalog tests**

Create `services/market-report-finder/tests/test_kr_catalog.py` with:

```python
from market_report_finder_service.markets.kr.catalog import KR_ANNUAL_REPORT_CATALOG, KrAnnualReportCatalog


TARGET_TICKERS = (
    "005930",
    "000660",
    "035420",
    "005380",
    "003490",
    "005490",
    "051910",
    "055550",
    "068270",
    "017670",
    "000270",
    "012330",
    "373220",
    "006400",
    "207940",
    "066570",
    "105560",
    "086790",
    "032830",
    "000810",
    "015760",
    "036460",
    "329180",
    "012450",
    "034020",
    "035720",
    "259960",
    "090430",
    "023530",
    "097950",
)


def test_kr_catalog_contains_30_unique_target_companies():
    tickers = [entry.ticker for entry in KR_ANNUAL_REPORT_CATALOG]

    assert len(KR_ANNUAL_REPORT_CATALOG) == 30
    assert len(set(tickers)) == 30
    assert tickers == list(TARGET_TICKERS)


def test_kr_catalog_has_broad_industry_coverage():
    industries = {entry.industry for entry in KR_ANNUAL_REPORT_CATALOG}

    assert len(industries) >= 18
    assert "Automotive" in industries
    assert "Banking" in industries
    assert "Batteries" in industries
    assert "Gaming" in industries
    assert "Shipbuilding" in industries
    assert "Utilities" in industries


def test_kr_catalog_resolves_each_target_by_ticker():
    for ticker in TARGET_TICKERS:
        company, candidates = KrAnnualReportCatalog.resolve_company(ticker=ticker)

        assert candidates
        assert company.ticker == ticker
        assert company.market.value == "KR"
        assert company.exchange == "KRX"
        assert company.company_name
        assert company.metadata["stock_code"] == ticker


def test_kr_catalog_does_not_emit_blank_aliases():
    for entry in KR_ANNUAL_REPORT_CATALOG:
        company = KrAnnualReportCatalog.company_entity(entry)

        assert all(alias for alias in company.aliases)
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
cd /home/maoyd/siq-research-engine/services/market-report-finder
uv run pytest tests/test_kr_catalog.py -q
```

Expected: FAIL because the catalog currently has 10 entries and the new test file expects 30.

- [ ] **Step 3: Commit only the failing tests if working in SDD mode**

Do not commit this failing-test-only state on `master`. If using a subagent branch/worktree, commit with:

```bash
git add services/market-report-finder/tests/test_kr_catalog.py
git commit -m "test(kr): define 30 company catalog target"
```

---

### Task 2: Expand The KR Curated Catalog To 30 Entries

**Files:**
- Modify: `services/market-report-finder/src/market_report_finder_service/markets/kr/catalog.py`
- Test: `services/market-report-finder/tests/test_kr_catalog.py`

**Interfaces:**
- Consumes: `KrAnnualReportCatalogEntry`.
- Produces: `KR_ANNUAL_REPORT_CATALOG` with exactly 30 ordered entries and no blank aliases in `company_entity()`.

- [ ] **Step 1: Filter empty aliases in `company_entity()`**

In `KrAnnualReportCatalog.company_entity()`, replace:

```python
aliases = list(dict.fromkeys([entry.company_name, entry.ticker, entry.company_id, *entry.aliases]))
```

with:

```python
aliases = list(dict.fromkeys(part for part in [entry.company_name, entry.ticker, entry.company_id, *entry.aliases] if part))
```

- [ ] **Step 2: Append the 20 new entries after SK Telecom**

Add these entries to `KR_ANNUAL_REPORT_CATALOG` after the existing 10 entries. Use `company_id=""` unless a verified DART corp code is available during implementation.

```python
    KrAnnualReportCatalogEntry(
        industry="Automotive",
        company_id="",
        ticker="000270",
        company_name="Kia Corporation",
        aliases=("Kia", "기아", "起亚", "起亞"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Auto Parts",
        company_id="",
        ticker="012330",
        company_name="Hyundai Mobis Co., Ltd.",
        aliases=("Hyundai Mobis", "현대모비스", "现代摩比斯", "現代摩比斯"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Batteries",
        company_id="",
        ticker="373220",
        company_name="LG Energy Solution, Ltd.",
        aliases=("LG Energy Solution", "LG에너지솔루션", "LG新能源", "LG新能源"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Batteries / Electronic Materials",
        company_id="",
        ticker="006400",
        company_name="Samsung SDI Co., Ltd.",
        aliases=("Samsung SDI", "삼성SDI", "三星SDI"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Biopharmaceuticals",
        company_id="",
        ticker="207940",
        company_name="Samsung Biologics Co., Ltd.",
        aliases=("Samsung Biologics", "삼성바이오로직스", "三星生物制剂", "三星生物製劑"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Consumer Electronics",
        company_id="",
        ticker="066570",
        company_name="LG Electronics Inc.",
        aliases=("LG Electronics", "LG전자", "LG电子", "LG電子"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Banking",
        company_id="",
        ticker="105560",
        company_name="KB Financial Group Inc.",
        aliases=("KB Financial Group", "KB금융", "KB金融"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Banking",
        company_id="",
        ticker="086790",
        company_name="Hana Financial Group Inc.",
        aliases=("Hana Financial Group", "하나금융지주", "韩亚金融", "韓亞金融"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Insurance",
        company_id="",
        ticker="032830",
        company_name="Samsung Life Insurance Co., Ltd.",
        aliases=("Samsung Life", "삼성생명", "三星生命"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Insurance",
        company_id="",
        ticker="000810",
        company_name="Samsung Fire & Marine Insurance Co., Ltd.",
        aliases=("Samsung Fire & Marine", "삼성화재", "三星火灾海上保险", "三星火災海上保險"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Utilities",
        company_id="",
        ticker="015760",
        company_name="Korea Electric Power Corporation",
        aliases=("KEPCO", "한국전력공사", "韩国电力公社", "韓國電力公社"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Utilities / Gas",
        company_id="",
        ticker="036460",
        company_name="Korea Gas Corporation",
        aliases=("KOGAS", "한국가스공사", "韩国天然气公社", "韓國天然氣公社"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Shipbuilding",
        company_id="",
        ticker="329180",
        company_name="HD Hyundai Heavy Industries Co., Ltd.",
        aliases=("HD Hyundai Heavy Industries", "HD현대중공업", "现代重工", "現代重工"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Aerospace / Defense",
        company_id="",
        ticker="012450",
        company_name="Hanwha Aerospace Co., Ltd.",
        aliases=("Hanwha Aerospace", "한화에어로스페이스", "韩华航空航天", "韓華航空航天"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Power Equipment",
        company_id="",
        ticker="034020",
        company_name="Doosan Enerbility Co., Ltd.",
        aliases=("Doosan Enerbility", "두산에너빌리티", "斗山能源", "斗山能源"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Internet Platforms",
        company_id="",
        ticker="035720",
        company_name="Kakao Corp.",
        aliases=("Kakao", "카카오", "韩国 Kakao", "可可"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Gaming",
        company_id="",
        ticker="259960",
        company_name="Krafton, Inc.",
        aliases=("Krafton", "크래프톤", "魁匠团", "魁匠團"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Consumer / Beauty",
        company_id="",
        ticker="090430",
        company_name="Amorepacific Corporation",
        aliases=("Amorepacific", "아모레퍼시픽", "爱茉莉太平洋", "愛茉莉太平洋"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Retail",
        company_id="",
        ticker="023530",
        company_name="Lotte Shopping Co., Ltd.",
        aliases=("Lotte Shopping", "롯데쇼핑", "乐天购物", "樂天購物"),
    ),
    KrAnnualReportCatalogEntry(
        industry="Food / Consumer Staples",
        company_id="",
        ticker="097950",
        company_name="CJ CheilJedang Corporation",
        aliases=("CJ CheilJedang", "CJ제일제당", "CJ 第一制糖", "CJ第一製糖"),
    ),
```

- [ ] **Step 3: Run catalog tests**

Run:

```bash
cd /home/maoyd/siq-research-engine/services/market-report-finder
uv run pytest tests/test_kr_catalog.py -q
```

Expected: PASS.

- [ ] **Step 4: Run existing DART tests**

Run:

```bash
cd /home/maoyd/siq-research-engine/services/market-report-finder
uv run pytest tests/test_dart_client.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit catalog changes**

Run:

```bash
cd /home/maoyd/siq-research-engine
git add services/market-report-finder/src/market_report_finder_service/markets/kr/catalog.py services/market-report-finder/tests/test_kr_catalog.py
git commit -m "feat(kr): expand curated annual report catalog"
```

---

