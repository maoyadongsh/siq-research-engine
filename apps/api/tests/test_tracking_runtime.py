from datetime import datetime

from agents.tracking.agent import TrackingAgent, _report_period_for_quarter
from agents.tracking.modules.sentiment_monitor import SentimentMonitor


def test_sentiment_daily_report_renders_neutral_count_without_name_error():
    report = SentimentMonitor().generate_daily_report(
        "000001",
        "平安银行",
        [
            {"source": "公告", "title": "业绩增长", "polarity": "正面", "score": 0.6},
            {"source": "新闻", "title": "市场担忧", "polarity": "负面", "score": -0.4},
            {"source": "研报", "title": "符合预期", "polarity": "中性", "score": 0.0},
        ],
    )

    assert report.neutral_count == 1
    assert "| 中性 | 1 |" in report.markdown


def test_report_period_for_quarter_uses_explicit_quarter_calculation():
    assert _report_period_for_quarter(datetime(2026, 1, 15)) == "2026-Q1"
    assert _report_period_for_quarter(datetime(2026, 6, 30)) == "2026-Q2"
    assert _report_period_for_quarter(datetime(2026, 12, 1)) == "2026-Q4"


def test_get_dashboard_reads_tracking_items_created_by_report_updater(tmp_path):
    agent = TrackingAgent(wiki_base_path=str(tmp_path))
    agent.report_updater.create_tracking_items_file(
        "000001",
        "平安银行",
        [
            {
                "stock_code": "000001",
                "company_name": "平安银行",
                "category": "风险信号",
                "title": "关注资产质量变化",
                "description": "持续跟踪不良贷款率变化。",
                "due_date": "2026-09-30",
                "threshold_value": "不良率>2%",
                "verification_method": "查阅季度财报",
                "status": "active",
            },
            {
                "stock_code": "000001",
                "company_name": "平安银行",
                "category": "财务承诺",
                "title": "跟踪分红承诺",
                "description": "关注年度分红政策落地。",
                "due_date": None,
                "threshold_value": None,
                "verification_method": "查阅公告",
                "status": "active",
            },
        ],
    )

    dashboard = agent.get_dashboard("000001", "平安银行")

    assert dashboard is not None
    assert dashboard.company_name == "平安银行"
    assert len(dashboard.active_items) == 2
    first = dashboard.active_items[0]
    assert first.category == "风险信号"
    assert first.title == "关注资产质量变化"
    assert first.description == "持续跟踪不良贷款率变化。"
    assert first.due_date == datetime(2026, 9, 30)
    assert first.threshold_value == "不良率>2%"
    assert first.verification_method == "查阅季度财报"
    assert "已加载 2 项跟踪事项" in dashboard.summary


def test_get_dashboard_resolves_company_name_from_existing_stock_directory(tmp_path):
    agent = TrackingAgent(wiki_base_path=str(tmp_path))
    agent.report_updater.create_tracking_items_file(
        "000002",
        "万科A",
        [
            {
                "stock_code": "000002",
                "company_name": "万科A",
                "category": "重大事项",
                "title": "关注债务展期",
                "description": "跟踪公开债务安排。",
                "status": "active",
            }
        ],
    )

    dashboard = agent.get_dashboard("000002", "旧名称")

    assert dashboard is not None
    assert dashboard.company_name == "万科A"
    assert dashboard.active_items[0].company_name == "万科A"
