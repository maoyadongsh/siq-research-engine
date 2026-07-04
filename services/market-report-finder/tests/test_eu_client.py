from datetime import date

from market_report_finder_service.markets.eu.catalog import EuAnnualReportCatalog
from market_report_finder_service.markets.eu.client import EsefIndexClient
from market_report_finder_service.markets.eu.service import EuReportFinder
from market_report_finder_service.models.schemas import Market, ReportTarget
from market_report_finder_service.services.orchestrator import ReportFinderOrchestrator


def _index_payload():
    return {
        "724500Y6DUVHQD6OXN27": {
            "entity": {"name": "ASML Holding N.V.", "ticker": "ASML"},
            "filings": {
                "724500Y6DUVHQD6OXN27/2025-12-31/ESEF/NL/0": {
                    "report-package": "asml-2025-12-31-en.zip",
                    "date": "2025-12-31",
                    "lei": "724500Y6DUVHQD6OXN27",
                    "system": "ESEF",
                    "country": "NL",
                    "langs": ["en"],
                    "report": "asml-2025-12-31-en/reports/asml.xhtml",
                    "viewer": "asml-2025-12-31-en/reports/ixbrlviewer.html",
                    "xbrl-json": "asml-2025-12-31-en.json",
                    "sha256sum": "abc123",
                    "added": "2026-02-15",
                }
            },
        },
        "W38RGI023J3WT1HWRP32": {
            "entity": {"name": "Siemens Aktiengesellschaft", "ticker": "SIE"},
            "filings": {
                "W38RGI023J3WT1HWRP32/2025-09-30/ESEF/DE/0": {
                    "report-package": "siemens_2025.zip",
                    "date": "2025-09-30",
                    "lei": "W38RGI023J3WT1HWRP32",
                    "system": "ESEF",
                    "country": "DE",
                    "langs": ["de"],
                    "report": "siemens_2025/reports/siemens.xhtml",
                    "viewer": "siemens_2025/reports/ixbrlviewer.html",
                    "added": "2025-12-10",
                }
            },
        },
    }


def test_resolve_company_filters_by_country_and_builds_esef_candidate():
    client = EsefIndexClient()
    client._index_cache = _index_payload()
    client._index_loaded_at = 999999999

    company, candidates = client.resolve_company(company_name="ASML", country="NL")
    filings = client.list_filings(company, target=ReportTarget.annual_report, forms=[], include_earnings=False, report_year=2025)

    assert candidates[0].market == Market.eu
    assert company.company_id == "724500Y6DUVHQD6OXN27"
    assert company.metadata["country"] == "NL"
    assert len(filings) == 1
    assert filings[0].document_url == "https://filings.xbrl.org/724500Y6DUVHQD6OXN27/2025-12-31/ESEF/NL/0/asml-2025-12-31-en.zip"
    assert filings[0].landing_url.endswith("/asml-2025-12-31-en/reports/ixbrlviewer.html")
    assert filings[0].file_format == "zip"
    assert filings[0].report_end == date(2025, 12, 31)
    assert filings[0].published_at == date(2026, 2, 15)


def test_resolve_company_rejects_unsupported_ch_search_without_fake_candidate():
    client = EsefIndexClient()
    client._index_cache = _index_payload()
    client._index_loaded_at = 999999999

    try:
        client.resolve_company(company_name="Nestle", country="CH")
    except ValueError as exc:
        assert "Swiss EU search" in str(exc)
    else:
        raise AssertionError("expected CH search to require direct official URL provider")


def test_catalog_resolves_current_major_company_report():
    company, candidates = EuAnnualReportCatalog.resolve_company(company_name="ASML", country="NL")
    reports = EuAnnualReportCatalog.filings_for_company(company, report_year=2025)

    assert candidates[0].market == Market.eu
    assert company.company_id == "NL:ASML"
    assert company.metadata["country"] == "NL"
    assert len(reports) == 1
    assert reports[0].title == "ASML Annual Report 2025 based on US GAAP"
    assert reports[0].document_url.endswith("asml-2025-annual-report-based-on-us-gaap.pdf")
    assert reports[0].metadata["source_tier"] == "official_direct"


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


def test_eu_finder_uses_catalog_for_switzerland_search():
    finder = EuReportFinder()

    company, candidates = finder.resolve_company(company_name="Nestle", company_id="CH:NESN")
    reports = finder.list_filings(
        company,
        target=ReportTarget.annual_report,
        forms=[],
        include_amendments=False,
        include_earnings=False,
        report_year=2025,
    )

    assert candidates[0].company_id == "CH:NESN"
    assert company.metadata["country"] == "CH"
    assert len(reports) == 1
    assert reports[0].document_url.endswith("annual-review-2025-en.pdf")


def test_eu_finder_exposes_curated_country_samples():
    finder = EuReportFinder()

    reports = finder.curated_annual_reports(country="FR", report_year=2025, limit=10)

    assert len(reports) == 10
    assert {item.metadata["country"] for item in reports} == {"FR"}


def test_orchestrator_passes_country_to_eu_curated_samples(monkeypatch):
    calls: list[tuple[int | None, int, str | None]] = []

    class StubEuFinder:
        def curated_annual_reports(
            self,
            *,
            report_year: int | None = None,
            limit: int = 10,
            country: str | None = None,
        ):
            calls.append((report_year, limit, country))
            return []

    orchestrator = ReportFinderOrchestrator()
    monkeypatch.setattr(orchestrator, "_market", lambda market: StubEuFinder())

    response = orchestrator.curated_annual_reports(market=Market.eu, report_year=2025, limit=10, country="FR")

    assert calls == [(2025, 10, "FR")]
    assert response["country"] == "FR"


def test_orchestrator_keeps_non_eu_curated_samples_country_compatible(monkeypatch):
    calls: list[tuple[int | None, int]] = []

    class StubJpFinder:
        def curated_annual_reports(self, *, report_year: int | None = None, limit: int = 10):
            calls.append((report_year, limit))
            return []

    orchestrator = ReportFinderOrchestrator()
    monkeypatch.setattr(orchestrator, "_market", lambda market: StubJpFinder())

    response = orchestrator.curated_annual_reports(market=Market.jp, report_year=2025, limit=10, country="FR")

    assert calls == [(2025, 10)]
    assert response["country"] == "FR"
