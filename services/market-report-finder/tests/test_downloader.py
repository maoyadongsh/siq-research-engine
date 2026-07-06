import json
import socket
from datetime import date

import pytest

from market_report_finder_service.core.config import settings
from market_report_finder_service.markets.us.service import UsReportFinder
from market_report_finder_service.models.schemas import DirectReportDownloadRequest, FilingCandidate, Market, ReportFamily, ReportType
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


def test_edinet_download_retries_429(monkeypatch):
    sleeps = []

    class StubResponse:
        def __init__(self, status_code: int, content: bytes = b"", content_type: str = "application/pdf"):
            self.status_code = status_code
            self.content = content
            self.headers = {"content-type": content_type}

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise AssertionError(f"unexpected status {self.status_code}")

    class StubClient:
        def __init__(self):
            self.responses = [
                StubResponse(429),
                StubResponse(200, b"%PDF-1.7 ok"),
            ]

        def get(self, url: str):
            return self.responses.pop(0)

    monkeypatch.setattr("market_report_finder_service.services.downloader.time.sleep", lambda seconds: sleeps.append(seconds))

    content, content_type = ReportDownloader._fetch_edinet_content(StubClient(), "https://api.edinet-fsa.go.jp/api/v2/documents/S100TEST?type=2")

    assert content == b"%PDF-1.7 ok"
    assert content_type == "application/pdf"
    assert sleeps == [2.0]


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


def test_user_supplied_non_official_url_is_manual_unverified():
    request = DirectReportDownloadRequest(
        market=Market.us,
        company_name="Apple Inc.",
        ticker="AAPL",
        document_url="https://sec.gov.evil.example/archive/aapl-2025.htm",
        form="10-K",
        report_end=date(2025, 9, 27),
        published_at=date(2025, 10, 31),
    )

    candidate = UsReportFinder().direct_candidate(request)

    assert candidate.source_id == "manual_unverified"
    assert candidate.source_domain == "sec.gov.evil.example"
    assert candidate.metadata["source_verification_status"] == "manual_unverified"
    assert candidate.metadata["original_source_id"] == "sec"
    assert not UsReportFinder.owns_url("https://www.sec.gov.evil.example/report.htm")


def test_downloader_streams_and_writes_metadata_index_atomically(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "download_dir", tmp_path)
    chunks = [b"<html>", b"ok</html>"]

    class StubResponse:
        url = _candidate().document_url
        headers = {"content-type": "text/html", "content-length": str(sum(len(chunk) for chunk in chunks))}

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self):
            yield from chunks

    class StreamContext:
        def __enter__(self):
            return StubResponse()

        def __exit__(self, exc_type, exc, tb):
            return False

    class StubClient:
        def __init__(self, *, timeout, headers, follow_redirects):
            self.headers = headers

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, method: str, url: str, headers=None):
            return StreamContext()

    monkeypatch.setattr("market_report_finder_service.services.downloader.httpx.Client", StubClient)

    downloaded = ReportDownloader().download(_candidate())

    saved_path = tmp_path / "US" / "Apple-Inc" / "2025" / "年报" / downloaded.file_name
    metadata_path = saved_path.with_suffix(saved_path.suffix + ".metadata.json")
    index_path = saved_path.parent / settings.download_index_file
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    index = json.loads(index_path.read_text(encoding="utf-8"))

    assert saved_path.read_bytes() == b"".join(chunks)
    assert downloaded.content_sha256
    assert metadata["source_verification"]["original_url"] == _candidate().document_url
    assert metadata["source_verification"]["effective_url"] == _candidate().document_url
    assert metadata["source_verification"]["source_verification_status"] == "official_verified"
    assert index["by_url"][_candidate().document_url]["source_verification_status"] == "official_verified"
    assert not list(tmp_path.rglob("*.tmp"))


def test_downloader_rejects_official_redirect_outside_allowlist(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "download_dir", tmp_path)

    class StubResponse:
        url = "https://evil.example/report.htm"
        headers = {"content-type": "text/html"}

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self):
            yield b"<html>bad</html>"

    class StreamContext:
        def __enter__(self):
            return StubResponse()

        def __exit__(self, exc_type, exc, tb):
            return False

    class StubClient:
        def __init__(self, *, timeout, headers, follow_redirects):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, method: str, url: str, headers=None):
            return StreamContext()

    monkeypatch.setattr("market_report_finder_service.services.downloader.httpx.Client", StubClient)

    with pytest.raises(ValueError, match="redirect escaped"):
        ReportDownloader().download(_candidate())

    assert not [path for path in tmp_path.rglob("*") if path.is_file()]


def test_downloader_size_limit_removes_temp_file(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "download_dir", tmp_path)
    monkeypatch.setitem(ReportDownloader.MAX_DOWNLOAD_BYTES_BY_MARKET, Market.us, 5)

    class StubResponse:
        url = _candidate().document_url
        headers = {"content-type": "text/html", "content-length": "6"}

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self):
            yield b"123456"

    class StreamContext:
        def __enter__(self):
            return StubResponse()

        def __exit__(self, exc_type, exc, tb):
            return False

    class StubClient:
        def __init__(self, *, timeout, headers, follow_redirects):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, method: str, url: str, headers=None):
            return StreamContext()

    monkeypatch.setattr("market_report_finder_service.services.downloader.httpx.Client", StubClient)

    with pytest.raises(ValueError, match="exceeds"):
        ReportDownloader().download(_candidate())

    assert not [path for path in tmp_path.rglob("*") if path.is_file()]



def test_cache_lookup_uses_redacted_url_before_legacy_raw_token_url():
    candidate = _candidate().model_copy(
        update={
            "document_url": "https://www.sec.gov/Archives/report.htm?source_token=secret&keep=1",
            "landing_url": "https://www.sec.gov/Archives/index.htm?access_token=jwt&keep=1",
        }
    )

    keys = ReportDownloader._cache_lookup_urls(candidate)

    assert keys[0] == "https://www.sec.gov/Archives/report.htm?source_token=%5Bredacted%5D&keep=1"
    assert keys[1] == candidate.document_url
    assert keys[2] == "https://www.sec.gov/Archives/index.htm?access_token=%5Bredacted%5D&keep=1"
    assert keys[3] == candidate.landing_url


def test_downloader_rejects_redirect_to_private_ip_literal(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "download_dir", tmp_path)

    class StubResponse:
        def __init__(self, *, status_code: int, url: str, headers: dict[str, str], body: bytes = b""):
            self.status_code = status_code
            self.url = url
            self.headers = headers
            self._body = body

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self):
            if self._body:
                yield self._body

    class StreamContext:
        def __init__(self, response):
            self.response = response

        def __enter__(self):
            return self.response

        def __exit__(self, exc_type, exc, tb):
            return False

    class StubClient:
        def __init__(self, *, timeout, headers, follow_redirects):
            self.urls = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, method: str, url: str, headers=None):
            self.urls.append(url)
            return StreamContext(
                StubResponse(
                    status_code=302,
                    url=url,
                    headers={"location": "http://169.254.169.254/latest/meta-data"},
                )
            )

    monkeypatch.setattr("market_report_finder_service.services.downloader.httpx.Client", StubClient)

    with pytest.raises(ValueError, match="private|metadata|non-public"):
        ReportDownloader().download(_candidate())

    assert not [path for path in tmp_path.rglob("*") if path.is_file()]


def test_downloader_rejects_allowlisted_host_that_resolves_private_ip(monkeypatch):
    def fake_getaddrinfo(host, port, type=0):
        assert host == "www.sec.gov"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr("market_report_finder_service.services.downloader.socket.getaddrinfo", fake_getaddrinfo)

    with pytest.raises(ValueError, match="private|loopback"):
        ReportDownloader()._validate_effective_url(_candidate(), _candidate().document_url)


def test_downloader_allows_official_url_with_public_dns(monkeypatch):
    def fake_getaddrinfo(host, port, type=0):
        assert host == "www.sec.gov"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr("market_report_finder_service.services.downloader.socket.getaddrinfo", fake_getaddrinfo)

    ReportDownloader()._validate_effective_url(_candidate(), _candidate().document_url)


def test_downloader_metadata_and_index_redact_source_tokens(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "download_dir", tmp_path)
    token_url = "https://www.sec.gov/Archives/report.htm?source_token=secret-source&crtfc_key=secret-key&keep=1"
    candidate = _candidate().model_copy(update={"document_url": token_url, "landing_url": token_url})

    class StubResponse:
        status_code = 200
        url = token_url
        headers = {"content-type": "text/html"}

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self):
            yield b"<html>ok</html>"

    class StreamContext:
        def __enter__(self):
            return StubResponse()

        def __exit__(self, exc_type, exc, tb):
            return False

    class StubClient:
        def __init__(self, *, timeout, headers, follow_redirects):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, method: str, url: str, headers=None):
            return StreamContext()

    monkeypatch.setattr("market_report_finder_service.services.downloader.httpx.Client", StubClient)
    monkeypatch.setattr(
        "market_report_finder_service.services.downloader.socket.getaddrinfo",
        lambda host, port, type=0: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))],
    )

    downloaded = ReportDownloader().download(candidate)
    saved_path = tmp_path / "US" / "Apple-Inc" / "2025" / "年报" / downloaded.file_name
    metadata = json.loads(saved_path.with_suffix(saved_path.suffix + ".metadata.json").read_text(encoding="utf-8"))
    index = json.loads((saved_path.parent / settings.download_index_file).read_text(encoding="utf-8"))
    serialized = json.dumps({"metadata": metadata, "index": index}, ensure_ascii=False)

    assert "secret-source" not in serialized
    assert "secret-key" not in serialized
    assert "%5Bredacted%5D" in serialized
    assert token_url not in index["by_url"]
