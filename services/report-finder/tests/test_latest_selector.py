from datetime import date

from report_finder_service.models.schemas import Market, ReportCandidate, ReportTarget, ReportType
from report_finder_service.services.latest_selector import LatestReportSelector


def _candidate(report_type: ReportType, report_end: date, published_at: date) -> ReportCandidate:
    return ReportCandidate(
        source_id="cninfo",
        source_name="巨潮资讯",
        source_domain="www.cninfo.com.cn",
        company_name="Demo A股",
        ticker="000001",
        market=Market.cn,
        report_type=report_type,
        title="Demo",
        report_end=report_end,
        published_at=published_at,
        document_url="https://example.com/demo.pdf",
        landing_url="https://example.com/demo",
    )


def test_select_latest_annual():
    selector = LatestReportSelector()
    selected, evidence = selector.select_with_evidence(
        [
            _candidate(ReportType.annual, date(2025, 12, 31), date(2026, 4, 1)),
            _candidate(ReportType.q1, date(2026, 3, 31), date(2026, 4, 20)),
        ],
        ReportTarget.annual_report,
    )
    assert selected.report_type == ReportType.annual
    assert evidence.filtered_candidates_count == 1
    assert evidence.selected_is_latest_by_report_end is True
    assert evidence.top_candidates[0].report_type == ReportType.annual


def test_select_latest_financial():
    selector = LatestReportSelector()
    selected, evidence = selector.select_with_evidence(
        [
            _candidate(ReportType.annual, date(2025, 12, 31), date(2026, 3, 28)),
            _candidate(ReportType.q1, date(2026, 3, 31), date(2026, 4, 18)),
        ],
        ReportTarget.financial_report,
    )
    assert selected.report_type == ReportType.q1
    assert evidence.filtered_candidates_count == 2
    assert evidence.selected_is_latest_by_published_at is True
    assert evidence.top_candidates[0].report_type == ReportType.q1


def test_select_latest_report_default_scope():
    selector = LatestReportSelector()
    selected, evidence = selector.select_with_evidence(
        [
            _candidate(ReportType.annual, date(2025, 12, 31), date(2026, 3, 28)),
            _candidate(ReportType.q1, date(2026, 3, 31), date(2026, 4, 18)),
            _candidate(ReportType.earnings, date(2026, 4, 1), date(2026, 4, 20)),
        ],
        ReportTarget.latest_report,
    )
    assert selected.report_type == ReportType.q1
    assert evidence.filtered_candidates_count == 2
