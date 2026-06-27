from datetime import date

from market_report_finder_service.models.schemas import Market, ReportAssistCandidate, ReportAssistRequest
from market_report_finder_service.services.assist import ReportAssistService


def test_assist_parses_korean_natural_language_request():
    service = ReportAssistService()

    result = service.assist(ReportAssistRequest(prompt="下载三星电子 2025 年年报和三季度报告", market=Market.kr))

    assert result.intent.market == Market.kr
    assert result.intent.ticker == "005930"
    assert result.intent.company_query == "三星电子"
    assert result.intent.company_id == "00126380"
    assert result.intent.report_year == 2025
    assert result.intent.report_types == ["annual", "q3"]


def test_assist_maps_chinese_japanese_company_alias_to_local_identifier():
    service = ReportAssistService()

    result = service.assist(ReportAssistRequest(prompt="铠侠 2025 年有价证券报告书", market=Market.jp))

    assert result.intent.market == Market.jp
    assert result.intent.ticker == "285A"
    assert result.intent.company_query == "铠侠"
    assert result.intent.report_year == 2025
    assert result.intent.report_types == ["annual"]


def test_assist_maps_sumitomo_heavy_chinese_alias():
    service = ReportAssistService()

    result = service.assist(ReportAssistRequest(prompt="住友重工 2025 年年报", market=Market.jp))

    assert result.intent.market == Market.jp
    assert result.intent.ticker == "6302"
    assert result.intent.company_query == "住友重工"
    assert result.intent.report_year == 2025
    assert result.intent.report_types == ["annual"]


def test_assist_respects_explicit_market_scope():
    service = ReportAssistService()

    result = service.assist(ReportAssistRequest(prompt="三星电子 2025 年年报", market=Market.cn))

    assert result.intent.market == Market.cn
    assert result.intent.ticker is None


def test_assist_explains_and_recommends_official_candidates():
    service = ReportAssistService()
    request = ReportAssistRequest(
        prompt="下载三星电子 2025 年三季度报告",
        market=Market.kr,
        report_year=2025,
        report_types=["q3"],
        candidates=[
            ReportAssistCandidate(
                document_url="https://opendart.fss.or.kr/api/document.xml?rcept_no=1",
                title="분기보고서 (2025.09)",
                report_type="quarterly",
                report_end=date(2025, 9, 30),
                published_at=date(2025, 11, 14),
            ),
            ReportAssistCandidate(
                document_url="https://opendart.fss.or.kr/api/document.xml?rcept_no=2",
                title="사업보고서 (2025.12)",
                report_type="annual",
                report_end=date(2025, 12, 31),
                published_at=date(2026, 3, 20),
            ),
        ],
    )

    result = service.assist(request)
    explanations = {item.document_url: item for item in result.candidate_explanations}

    assert explanations["https://opendart.fss.or.kr/api/document.xml?rcept_no=1"].recommended is True
    assert "季度报告" in explanations["https://opendart.fss.or.kr/api/document.xml?rcept_no=1"].title_zh
    assert explanations["https://opendart.fss.or.kr/api/document.xml?rcept_no=2"].recommended is False
