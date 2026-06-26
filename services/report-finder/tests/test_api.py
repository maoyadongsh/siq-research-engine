from datetime import date, datetime, timezone
import hashlib
from urllib.parse import unquote

from fastapi.testclient import TestClient

from report_finder_service.app import app
from report_finder_service.models.schemas import (
    CompanyEntity,
    DownloadedReportFile,
    LatestReportDownloadResponse,
    Market,
    ReportCandidate,
    ReportCandidateSnapshot,
    ReportTarget,
    ReportType,
    SelectionEvidence,
    DirectReportDownloadResponse,
)


client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_resolve_api():
    response = client.post("/v1/resolve", json={"company_name": "贵州茅台"})
    assert response.status_code == 200
    assert response.json()["resolved"]["ticker"] == "600519"
    assert response.json()["candidate_count"] >= 1
    assert response.json()["candidates"][0]["market"] == "CN"


def test_resolve_api_ticker_exact():
    response = client.post(
        "/v1/resolve",
        json={"company_name": "任意输入", "ticker": "000001", "exchange_hint": "SZSE"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["resolved"]["match_reason"] == "cninfo_exact_ticker:000001"
    assert body["candidate_count"] >= 1


def test_resolve_api_returns_candidates():
    response = client.post("/v1/resolve", json={"company_name": "国华网安"})
    assert response.status_code == 200
    body = response.json()
    assert body["resolved"]["ticker"] == "000004"
    assert any(candidate["ticker"] == "000004" for candidate in body["candidates"])


def test_latest_report_api():
    response = client.post(
        "/v1/reports/latest",
        json={"company_name": "贵州茅台", "target": "annual_report"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["resolved"]["ticker"] == "600519"
    assert body["selected"]["report_type"] == "annual"
    assert body["selection_evidence"]["target_scope"] == "annual_report"
    assert body["selection_evidence"]["filtered_candidates_count"] >= 1


def test_sources_api_exposes_scope_and_status():
    response = client.get("/v1/sources")
    assert response.status_code == 200
    body = response.json()
    source_ids = {item["source_id"]: item for item in body["sources"]}
    assert set(source_ids) == {"cninfo"}
    assert source_ids["cninfo"]["implementation_status"] == "live"
    assert "public_financial_reports" in source_ids["cninfo"]["data_scope"]


def test_latest_report_api_default_target():
    response = client.post(
        "/v1/reports/latest",
        json={"company_name": "平安银行"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["target"] == "annual_report"
    assert body["selected"]["report_type"] == "annual"


def test_recent_reports_api_lists_multiple_reports():
    response = client.post(
        "/v1/reports/recent",
        json={
            "ticker": "000001",
            "exchange_hint": "SZSE",
            "target": "financial_report",
            "limit": 5,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["resolved"]["ticker"] == "000001"
    assert body["candidates_total"] >= len(body["reports"])
    assert body["reports"]
    assert all("document_url" in item for item in body["reports"])
    assert all(item["report_type"] in {"annual", "semiannual", "q1", "q3"} for item in body["reports"])


def test_latest_report_api_ticker_exact():
    response = client.post(
        "/v1/reports/latest",
        json={
            "company_name": "任意输入",
            "ticker": "000001",
            "exchange_hint": "SZSE",
            "target": "annual_report",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["resolved"]["ticker"] == "000001"
    assert body["resolved"]["match_reason"] == "cninfo_exact_ticker:000001"


def test_download_latest_report_api(monkeypatch, tmp_path):
    file_name = "平安银行_CN_000001_2025-12-31_年报_2026-03-20_cninfo_demo1234.pdf"
    file_path = tmp_path / file_name
    file_path.write_bytes(b"%PDF-1.4 latest report")

    def fake_download(**kwargs):
        selected = ReportCandidate(
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
            selection_reason="demo",
        )
        return LatestReportDownloadResponse(
            query=kwargs["company_name"],
            target=ReportTarget.annual_report,
            resolved=CompanyEntity(
                canonical_name="平安银行",
                display_name="平安银行",
                aliases=["平安银行"],
                market=Market.cn,
                exchange="SZSE",
                ticker="000001",
                confidence=0.99,
                match_reason="demo",
            ),
            selected=selected,
            candidates_considered=1,
            selection_evidence=SelectionEvidence(
                checked_at=datetime.now(timezone.utc),
                target_scope=ReportTarget.annual_report,
                ranking_rule="demo",
                filtered_candidates_count=1,
                top_candidates=[
                    ReportCandidateSnapshot(
                        title=selected.title,
                        report_type=selected.report_type,
                        report_end=selected.report_end,
                        published_at=selected.published_at,
                        document_url=selected.document_url,
                        landing_url=selected.landing_url,
                    )
                ],
                selected_is_latest_by_report_end=True,
                selected_is_latest_by_published_at=True,
            ),
            downloaded_file=DownloadedReportFile(
                file_name=file_name,
                saved_path=str(file_path),
                size_bytes=file_path.stat().st_size,
                content_type="application/pdf",
                downloaded_at=datetime.now(timezone.utc),
            ),
        )

    monkeypatch.setattr(
        "report_finder_service.api.routes.downloads.orchestrator.find_latest_report_and_download",
        fake_download,
    )

    response = client.post(
        "/v1/reports/latest/download",
        json={"company_name": "平安银行"},
    )

    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 latest report"
    assert file_name in unquote(response.headers["content-disposition"])
    assert response.headers["x-report-end"] == "2025-12-31"


def test_direct_download_api(monkeypatch, tmp_path):
    fixed_now = datetime(2026, 4, 27, tzinfo=timezone.utc)
    document_url = "https://example.com/pfbank-2025-annual.pdf"
    url_hash = hashlib.sha256(document_url.encode("utf-8")).hexdigest()[:8]
    file_name = f"浦发银行_CN_manual_2025-12-31_年报_2026-03-20_manual_official_{url_hash}.pdf"
    file_path = tmp_path / file_name
    file_path.write_bytes(b"%PDF-1.4 direct report")

    def fake_direct_download(**kwargs):
        return DirectReportDownloadResponse(
            company_name=kwargs["company_name"],
            source_name=kwargs["source_name"],
            source_domain="example.com",
            document_url=kwargs["document_url"],
            landing_url=kwargs["landing_url"],
            report_type=kwargs["report_type"],
            report_end=date(2025, 12, 31),
            published_at=date(2026, 3, 20),
            downloaded_file=DownloadedReportFile(
                file_name=file_name,
                saved_path=str(file_path),
                size_bytes=file_path.stat().st_size,
                content_type="application/pdf",
                downloaded_at=fixed_now,
            ),
        )

    monkeypatch.setattr(
        "report_finder_service.api.routes.downloads.orchestrator.download_direct_official_report",
        fake_direct_download,
    )

    response = client.post(
        "/v1/reports/direct-download",
        json={
            "company_name": "浦发银行",
            "document_url": document_url,
            "landing_url": "https://example.com/pfbank-2025-annual",
            "source_name": "manual_official",
            "report_type": "annual",
            "report_end": "2025-12-31",
            "published_at": "2026-03-20",
        },
    )

    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 direct report"
    assert file_name in unquote(response.headers["content-disposition"])
    assert response.headers["x-report-type"] == "annual"


def test_batch_download_uses_resolved_ticker_when_item_ticker_missing(monkeypatch, tmp_path):
    from report_finder_service.services.orchestrator import ReportFinderOrchestrator

    def fake_download(self, candidate, sub_dir=None):
        file_name = self._build_file_name(candidate)
        file_path = tmp_path / file_name
        file_path.write_bytes(b"%PDF-1.4 batch report")
        return DownloadedReportFile(
            file_name=file_name,
            saved_path=str(file_path),
            size_bytes=file_path.stat().st_size,
            content_type="application/pdf",
            downloaded_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr("report_finder_service.services.report_downloader.ReportDownloader.download", fake_download)

    orchestrator = ReportFinderOrchestrator()
    result = orchestrator.download_batch(
        items=[
            type("Item", (), {
                "document_url": "https://example.com/sangfor-annual.pdf",
                "company_name": "深信服",
                "title": "深信服2025年年度报告",
                "ticker": None,
                "report_type": ReportType.annual,
                "report_end": date(2025, 12, 31),
                "published_at": date(2026, 4, 25),
            })()
        ],
        default_company_name="深信服",
    )

    assert result.succeeded == 1
    assert result.results[0].file_name.startswith("深信服_CN_300454_2025-12-31_年报_2026-04-25_manual_")


def test_select_download_uses_explicit_report_metadata(monkeypatch, tmp_path):
    from report_finder_service.services.orchestrator import ReportFinderOrchestrator

    selected_candidates = []

    def fake_resolve_with_candidates(self, **kwargs):
        return (
            CompanyEntity(
                canonical_name="华大基因",
                display_name="华大基因",
                aliases=["华大基因"],
                market=Market.cn,
                exchange="SZSE",
                ticker="300676",
                confidence=1.0,
                match_reason="test",
            ),
            [],
        )

    def fake_download(self, candidate, sub_dir=None):
        selected_candidates.append(candidate)
        file_name = self._build_file_name(candidate)
        file_path = tmp_path / file_name
        file_path.write_bytes(b"%PDF-1.4 selected q1 report")
        return DownloadedReportFile(
            file_name=file_name,
            saved_path=str(file_path),
            size_bytes=file_path.stat().st_size,
            content_type="application/pdf",
            downloaded_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(
        "report_finder_service.services.company_resolver.CompanyResolver.resolve_with_candidates",
        fake_resolve_with_candidates,
    )
    monkeypatch.setattr("report_finder_service.services.report_downloader.ReportDownloader.download", fake_download)

    explicit_report = ReportCandidate(
        source_id="cninfo",
        source_name="巨潮资讯",
        source_domain="www.cninfo.com.cn",
        company_name="华大基因",
        ticker="300676",
        market=Market.cn,
        report_type=ReportType.q1,
        title="2025年一季度报告",
        report_end=date(2025, 3, 31),
        published_at=date(2025, 4, 25),
        document_url="https://static.cninfo.com.cn/finalpage/2025-q1.pdf",
        landing_url="https://www.cninfo.com.cn/2025-q1",
        file_format="pdf",
    )

    result = ReportFinderOrchestrator().download_selected(
        company_name="华大基因",
        ticker=None,
        exchange_hint=None,
        report_types=["q1"],
        reports=[explicit_report],
        report_year=None,
    )

    assert result["succeeded"] == 1
    assert selected_candidates[0].title == "2025年一季度报告"
    assert selected_candidates[0].report_end == date(2025, 3, 31)
    assert "2025-03-31" in result["files"][0].file_name
