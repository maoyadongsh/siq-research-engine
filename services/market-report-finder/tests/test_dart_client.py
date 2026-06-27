from datetime import date

from market_report_finder_service.markets.kr.client import DartClient
from market_report_finder_service.markets.kr.public_dart import DartPublicClient
from market_report_finder_service.markets.kr.service import KrReportFinder
from market_report_finder_service.models.schemas import CompanyEntity, Market, ReportFamily, ReportTarget, ReportType
from market_report_finder_service.services.downloader import ReportDownloader


def _kr_company() -> CompanyEntity:
    return CompanyEntity(
        market=Market.kr,
        company_id="00126380",
        ticker="005930",
        company_name="Samsung Electronics",
        exchange="KRX",
    )


def test_dart_resolves_stock_code_from_corp_rows():
    client = DartClient()
    rows = [
        {"corp_code": "00126380", "corp_name": "삼성전자", "stock_code": "005930", "modify_date": "20260101"},
        {"corp_code": "00164779", "corp_name": "현대자동차", "stock_code": "005380"},
    ]

    candidates = client._company_candidates(rows, company_name=None, ticker="5930", company_id=None)

    assert candidates[0].market == Market.kr
    assert candidates[0].company_id == "00126380"
    assert candidates[0].ticker == "005930"
    assert candidates[0].confidence == 0.99


def test_dart_builds_periodic_candidates():
    client = DartClient()
    row = {
        "corp_cls": "Y",
        "corp_name": "삼성전자",
        "corp_code": "00126380",
        "stock_code": "005930",
        "report_nm": "분기보고서 (2025.09)",
        "rcept_no": "20251114000123",
        "rcept_dt": "20251114",
        "flr_nm": "삼성전자",
    }

    candidate = client._build_candidate(_kr_company(), row, ReportType.quarterly)

    assert candidate is not None
    assert candidate.market == Market.kr
    assert candidate.source_id == "dart"
    assert candidate.report_type == ReportType.quarterly
    assert candidate.report_family == ReportFamily.quarterly
    assert candidate.report_end == date(2025, 9, 30)
    assert candidate.document_url.startswith(DartClient.DOCUMENT_URL)
    assert "crtfc_key" not in candidate.document_url


def test_dart_allowed_types_by_target():
    client = DartClient()

    assert client._form_to_report_type("business-report") == ReportType.annual
    assert client._form_to_report_type("q3") == ReportType.quarterly


def test_dart_public_parses_business_report_html():
    html = """
    <table><tbody>
      <tr>
        <td>1</td>
        <td class="tL"><a title="삼성전자 기업개황 새창">삼성전자</a></td>
        <td class="tL">
          <a href="/dsaf001/main.do?rcpNo=20260310002820" title="사업보고서 공시뷰어 새창">
            사업보고서
            (2025.12)
          </a>
        </td>
        <td>삼성전자</td>
        <td>2026.03.10</td>
      </tr>
    </tbody></table>
    """

    candidates = DartPublicClient._parse_search_html(_kr_company(), html, expected_report_type=ReportType.annual)

    assert len(candidates) == 1
    assert candidates[0].source_id == "dart_public"
    assert candidates[0].accession_number == "20260310002820"
    assert candidates[0].document_url == "https://dart.fss.or.kr/report/combined.do?rcpNo=20260310002820"
    assert candidates[0].landing_url == "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260310002820"
    assert candidates[0].report_end == date(2025, 12, 31)
    assert candidates[0].published_at == date(2026, 3, 10)
    assert candidates[0].file_format == "html"


def test_dart_public_parses_dcm_no_and_pdf_urls():
    html = """
    <script>
      viewDoc("20260310002820", "11104488", "1", "957", "4524", "dart4.xsd", "");
    </script>
    """

    dcm_no = DartPublicClient._parse_viewer_dcm_no(html, receipt_no="20260310002820")

    assert dcm_no == "11104488"
    assert (
        DartPublicClient.pdf_document_url("20260310002820", dcm_no)
        == "https://dart.fss.or.kr/pdf/download/pdf.do?rcp_no=20260310002820&dcm_no=11104488"
    )
    assert (
        DartPublicClient.pdf_landing_url("20260310002820", dcm_no)
        == "https://dart.fss.or.kr/pdf/download/main.do?rcp_no=20260310002820&dcm_no=11104488"
    )


def test_dart_downloader_parses_viewer_sections():
    html = """
    <script>
      var node1 = {};
      node1['text'] = "사 업 보 고 서";
      node1['rcpNo'] = "20260310002820";
      node1['dcmNo'] = "11104488";
      node1['eleId'] = "1";
      node1['offset'] = "957";
      node1['length'] = "4524";
      node1['dtd'] = "dart4.xsd";
      treeData.push(node1);
    </script>
    """

    sections = ReportDownloader._dart_viewer_sections(html)

    assert sections == [
        {
            "title": "사 업 보 고 서",
            "rcp_no": "20260310002820",
            "dcm_no": "11104488",
            "ele_id": "1",
            "offset": "957",
            "length": "4524",
            "dtd": "dart4.xsd",
            "url": "https://dart.fss.or.kr/report/viewer.do?rcpNo=20260310002820&dcmNo=11104488&eleId=1&offset=957&length=4524&dtd=dart4.xsd",
        }
    ]


def test_dart_public_pdf_does_not_cache_by_viewer_landing_url():
    candidate = DartPublicClient._parse_search_html(
        _kr_company(),
        '<tr><td></td><td></td><td><a href="/dsaf001/main.do?rcpNo=20260310002820">사업보고서 (2025.12)</a></td><td></td><td>2026.03.10</td></tr>',
        expected_report_type=ReportType.annual,
    )[0].model_copy(
        update={
            "document_url": DartPublicClient.pdf_document_url("20260310002820", "11104488"),
            "file_format": "pdf",
        }
    )

    assert ReportDownloader._cache_lookup_urls(candidate) == (candidate.document_url,)
    assert ReportDownloader._should_cache_landing_url(candidate) is False


def test_kr_finder_uses_public_dart_when_opendart_key_missing(monkeypatch):
    finder = KrReportFinder()
    public_candidate = DartPublicClient._parse_search_html(
        _kr_company(),
        '<tr><td></td><td></td><td><a href="/dsaf001/main.do?rcpNo=20260310002820">사업보고서 (2025.12)</a></td><td></td><td>2026.03.10</td></tr>',
        expected_report_type=ReportType.annual,
    )[0]
    monkeypatch.setattr(finder.public, "list_filings", lambda *args, **kwargs: [public_candidate])
    monkeypatch.setattr(finder.client, "list_filings", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("DART_API_KEY is required for Korean market report search")))

    company, _ = finder.resolve_company(ticker="005930")
    reports = finder.list_filings(
        company,
        target=ReportTarget.annual_report,
        forms=["annual"],
        include_amendments=False,
        include_earnings=False,
        report_year=2025,
    )

    assert reports == [public_candidate]
