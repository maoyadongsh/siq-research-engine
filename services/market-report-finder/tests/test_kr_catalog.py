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
