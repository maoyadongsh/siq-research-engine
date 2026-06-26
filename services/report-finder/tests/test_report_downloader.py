from datetime import date
import hashlib
from pathlib import Path

import httpx

from report_finder_service.core.config import settings
from report_finder_service.models.schemas import Market, ReportCandidate, ReportType
from report_finder_service.services.report_downloader import ReportDownloader, parse_report_finder_file_name


def test_report_downloader_saves_file(tmp_path, monkeypatch):
    candidate = ReportCandidate(
        source_id="cninfo",
        source_name="巨潮资讯",
        source_domain="www.cninfo.com.cn",
        company_name="平安银行",
        ticker="000001",
        market=Market.cn,
        report_type=ReportType.annual,
        title="平安银行2025年年度报告",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 20),
        document_url="https://static.cninfo.com.cn/finalpage/demo.pdf",
        landing_url="https://www.cninfo.com.cn/demo",
        file_format="pdf",
    )

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            content=b"%PDF-1.4 demo report",
            headers={"content-type": "application/pdf"},
        )
    )
    downloader = ReportDownloader()

    monkeypatch.setattr(settings, "download_dir", str(tmp_path))
    monkeypatch.setattr(settings, "download_overwrite", True)
    monkeypatch.setattr(downloader, "_client", lambda source_id: httpx.Client(transport=transport))

    downloaded = downloader.download(candidate)

    url_hash = hashlib.sha256(candidate.document_url.encode("utf-8")).hexdigest()[:8]
    assert downloaded.file_name == f"平安银行_CN_000001_2025-12-31_年报_2026-03-20_cninfo_{url_hash}.pdf"
    assert downloaded.size_bytes > 0
    assert downloaded.content_type == "application/pdf"
    assert downloaded.cache_hit is False
    assert downloaded.deduplicated is False
    assert downloaded.content_sha256 is not None
    assert downloaded.saved_path.endswith(f"年报/{downloaded.file_name}")
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert downloaded.saved_path
    assert Path(downloaded.saved_path).read_bytes() == b"%PDF-1.4 demo report"


def test_parse_report_finder_file_name_supports_structured_cn_name():
    parsed = parse_report_finder_file_name(
        "上汽集团_CN_600104_2025-12-31_年报_2026-04-01_manual_180a0748.pdf"
    )

    assert parsed == {
        "company_name": "上汽集团",
        "market": "CN",
        "ticker": "600104",
        "report_end": "2025-12-31",
        "report_type": "年报",
        "published_at": "2026-04-01",
        "source_id": "manual",
        "url_hash": "180a0748",
        "file_format": "pdf",
    }


def test_report_downloader_supports_direct_official_url(tmp_path, monkeypatch):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            content=b"%PDF-1.4 direct report",
            headers={"content-type": "application/pdf"},
        )
    )
    downloader = ReportDownloader()

    monkeypatch.setattr(settings, "download_dir", str(tmp_path))
    monkeypatch.setattr(settings, "download_overwrite", True)
    monkeypatch.setattr(downloader, "_client", lambda source_id: httpx.Client(transport=transport))

    result = downloader.download_direct(
        company_name="浦发银行",
        document_url="https://example.com/pfbank-2025-annual.pdf",
        landing_url="https://example.com/pfbank-2025-annual",
        source_name="manual_official",
        report_type=ReportType.annual,
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 20),
    )

    assert result.source_domain == "example.com"
    assert result.downloaded_file.file_name.startswith("浦发银行_CN_manual_2025-12-31_年报_2026-03-20_manual_official_")
    assert result.downloaded_file.file_name.endswith(".pdf")
    assert Path(result.downloaded_file.saved_path).read_bytes() == b"%PDF-1.4 direct report"


def test_report_downloader_reuses_cached_url_without_network(tmp_path, monkeypatch):
    candidate = ReportCandidate(
        source_id="cninfo",
        source_name="巨潮资讯",
        source_domain="www.cninfo.com.cn",
        company_name="平安银行",
        ticker="000001",
        market=Market.cn,
        report_type=ReportType.annual,
        title="平安银行2025年年度报告",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 20),
        document_url="https://static.cninfo.com.cn/finalpage/demo.pdf",
        landing_url="https://www.cninfo.com.cn/demo",
        file_format="pdf",
    )
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        return httpx.Response(
            200,
            content=b"%PDF-1.4 demo report",
            headers={"content-type": "application/pdf"},
        )

    transport = httpx.MockTransport(handler)
    downloader = ReportDownloader()

    monkeypatch.setattr(settings, "download_dir", str(tmp_path))
    monkeypatch.setattr(settings, "download_overwrite", False)
    monkeypatch.setattr(downloader, "_client", lambda source_id: httpx.Client(transport=transport))

    first = downloader.download(candidate)
    second = downloader.download(candidate)

    assert calls["count"] == 1
    assert first.saved_path == second.saved_path
    assert second.cache_hit is True


def test_report_downloader_deduplicates_same_content_across_urls(tmp_path, monkeypatch):
    candidate_a = ReportCandidate(
        source_id="manual_official",
        source_name="manual_official",
        source_domain="example.com",
        company_name="浦发银行",
        ticker="manual",
        market=Market.cn,
        report_type=ReportType.annual,
        title="浦发银行2025年年度报告A",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 20),
        document_url="https://example.com/pfbank-a.pdf",
        landing_url="https://example.com/pfbank-a",
        file_format="pdf",
    )
    candidate_b = ReportCandidate(
        source_id="manual_official",
        source_name="manual_official",
        source_domain="example.com",
        company_name="浦发银行",
        ticker="manualb",
        market=Market.cn,
        report_type=ReportType.annual,
        title="浦发银行2025年年度报告B",
        report_end=date(2025, 12, 31),
        published_at=date(2026, 3, 20),
        document_url="https://example.com/pfbank-b.pdf",
        landing_url="https://example.com/pfbank-b",
        file_format="pdf",
    )
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        return httpx.Response(
            200,
            content=b"%PDF-1.4 same content",
            headers={"content-type": "application/pdf"},
        )

    transport = httpx.MockTransport(handler)
    downloader = ReportDownloader()

    monkeypatch.setattr(settings, "download_dir", str(tmp_path))
    monkeypatch.setattr(settings, "download_overwrite", False)
    monkeypatch.setattr(downloader, "_client", lambda source_id: httpx.Client(transport=transport))

    first = downloader.download(candidate_a)
    second = downloader.download(candidate_b)

    assert calls["count"] == 2
    assert first.saved_path == second.saved_path
    assert second.deduplicated is True
