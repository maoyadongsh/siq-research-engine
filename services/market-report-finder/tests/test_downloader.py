from datetime import date

from market_report_finder_service.core.config import settings
from market_report_finder_service.models.schemas import FilingCandidate, Market, ReportFamily, ReportType
from market_report_finder_service.services.downloader import ReportDownloader


def _candidate() -> FilingCandidate:
    return FilingCandidate(
        source_id="sec",
        source_name="SEC EDGAR",
        source_domain="sec.gov",
        market=Market.us,
        company_id="320193",
        cik="320193",
        ticker="AAPL",
        company_name="Apple Inc.",
        report_type=ReportType.form_10k,
        report_family=ReportFamily.annual,
        form="10-K",
        title="Apple 10-K",
        accession_number="0000320193-25-000079",
        primary_document="aapl-20250927.htm",
        report_end=date(2025, 9, 27),
        published_at=date(2025, 10, 31),
        document_url="https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
        landing_url="https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/0000320193-25-000079-index.html",
        file_format="htm",
    )


def test_build_file_name_is_ascii_and_keeps_source_suffix():
    downloader = ReportDownloader()

    file_name = downloader._build_file_name(_candidate())

    assert file_name.endswith(".htm")
    assert file_name.startswith("Apple-Inc_US_AAPL_2025-09-27_10-K_2025-10-31_sec_")
    assert "10-K" in file_name
    assert "/" not in file_name


def test_build_file_name_matches_report_finder_contract():
    import re

    downloader = ReportDownloader()

    file_name = downloader._build_file_name(_candidate())
    pattern = re.compile(
        r"^(?P<company_name>.+?)_"
        r"(?P<market>CN|HK|US|EU|KR|JP)_"
        r"(?P<ticker>[^_]+)_"
        r"(?P<report_end>\d{4}-\d{2}-\d{2})_"
        r"(?P<report_type>[^_]+)_"
        r"(?P<published_at>\d{4}-\d{2}-\d{2})_"
        r"(?P<source_id>.+)_"
        r"(?P<url_hash>[0-9a-fA-F]{8})\.htm$"
    )

    assert pattern.match(file_name)


def test_download_dir_uses_market_and_family():
    downloader = ReportDownloader()

    path = downloader._download_dir(_candidate())

    assert path.as_posix().endswith("downloads/US/Apple-Inc/2025/年报")


def test_eu_download_dir_uses_country_between_market_and_company():
    downloader = ReportDownloader()
    candidate = FilingCandidate(
        source_id="xbrl_filings_esef",
        source_name="filings.xbrl.org ESEF index",
        source_domain="filings.xbrl.org",
        market=Market.eu,
        company_id="724500Y6DUVHQD6OXN27",
        ticker="ASML",
        company_name="ASML Holding N.V.",
        report_type=ReportType.annual,
        report_family=ReportFamily.annual,
        form="ESEF",
        title="ASML annual financial report",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 2, 15),
        document_url="https://filings.xbrl.org/724500Y6DUVHQD6OXN27/2025-12-31/ESEF/NL/0/asml.zip",
        landing_url="https://filings.xbrl.org/724500Y6DUVHQD6OXN27/2025-12-31/ESEF/NL/0/viewer.html",
        file_format="zip",
        metadata={"country": "NL"},
    )

    path = downloader._download_dir(candidate)

    assert path.as_posix().endswith("downloads/EU/NL/ASML-Holding-N.V/2025/年报")


def test_eu_sec_source_uses_sec_user_agent(monkeypatch):
    captured: dict[str, object] = {}

    class StubResponse:
        content = b"<html>ok</html>"
        headers = {"content-type": "text/html"}

        def raise_for_status(self) -> None:
            return None

    class StubClient:
        def __init__(self, *, timeout, headers, follow_redirects):
            captured["timeout"] = timeout
            captured["headers"] = headers
            captured["follow_redirects"] = follow_redirects

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url: str):
            captured["url"] = url
            return StubResponse()

    monkeypatch.setattr("market_report_finder_service.services.downloader.httpx.Client", StubClient)
    candidate = _candidate().model_copy(
        update={
            "market": Market.eu,
            "company_id": "CH:UBSG",
            "ticker": "UBSG",
            "company_name": "UBS Group AG",
            "document_url": "https://www.sec.gov/Archives/edgar/data/1610520/000161052026000023/ubs-20251231.htm",
            "metadata": {"country": "CH"},
        }
    )

    content, content_type = ReportDownloader()._fetch_content(candidate)

    assert content == b"<html>ok</html>"
    assert content_type == "text/html"
    assert captured["url"] == candidate.document_url
    assert captured["headers"]["User-Agent"] == settings.sec_user_agent
    assert captured["headers"]["Host"] == "www.sec.gov"


def test_eu_bmw_download_uses_base_user_agent(monkeypatch):
    captured: dict[str, object] = {}

    class StubResponse:
        content = b"%PDF-1.7 ok"
        headers = {"content-type": "application/pdf"}

        def raise_for_status(self) -> None:
            return None

    class StubClient:
        def __init__(self, *, timeout, headers, follow_redirects):
            captured["headers"] = headers

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url: str):
            captured["url"] = url
            return StubResponse()

    monkeypatch.setattr("market_report_finder_service.services.downloader.httpx.Client", StubClient)
    candidate = _candidate().model_copy(
        update={
            "source_id": "issuer_annual_report",
            "market": Market.eu,
            "company_id": "DE:BMW",
            "ticker": "BMW",
            "company_name": "Bayerische Motoren Werke Aktiengesellschaft",
            "document_url": "https://www.bmwgroup.com/en/report/2025/downloads/BMW-Group-Financial-Statements-2025-en.pdf",
            "metadata": {"country": "DE"},
        }
    )

    content, content_type = ReportDownloader()._fetch_content(candidate)

    assert content == b"%PDF-1.7 ok"
    assert content_type == "application/pdf"
    assert captured["url"] == candidate.document_url
    assert captured["headers"]["User-Agent"] == settings.sec_user_agent
    assert "Accept-Language" not in captured["headers"]


def test_issuer_direct_cache_lookup_does_not_alias_landing_url():
    candidate = _candidate().model_copy(
        update={
            "source_id": "issuer_annual_report",
            "market": Market.eu,
            "company_id": "CH:ABBN",
            "ticker": "ABBN",
            "company_name": "ABB Ltd",
            "document_url": "https://library.e.abb.com/public/c81058c6d8cc4437bba6acf6a43a21d2/ABB%20Integrated%20Report%202025.pdf",
            "landing_url": "https://www.abb.com/global/en/company/annual-reporting-suite",
            "metadata": {"country": "CH"},
        }
    )

    assert ReportDownloader._cache_lookup_urls(candidate) == (candidate.document_url,)


def test_cn_quarter_file_name_uses_specific_report_label():
    downloader = ReportDownloader()
    candidate = FilingCandidate(
        source_id="cninfo",
        source_name="巨潮资讯",
        source_domain="www.cninfo.com.cn",
        market=Market.cn,
        company_id="600519",
        ticker="600519",
        company_name="贵州茅台",
        report_type=ReportType.q1,
        report_family=ReportFamily.quarterly,
        form="q1",
        title="贵州茅台2025年第一季度报告",
        report_end=date(2025, 3, 31),
        published_at=date(2025, 4, 17),
        document_url="https://static.cninfo.com.cn/finalpage/2026-04-17/1225114741.PDF",
        landing_url="https://www.cninfo.com.cn/new/disclosure/detail?announcementId=1225114741",
        file_format="pdf",
    )

    file_name = downloader._build_file_name(candidate)

    assert file_name.startswith("贵州茅台_CN_600519_2025-03-31_一季报_2025-04-17_cninfo_")
    assert file_name.endswith(".pdf")
