import importlib.util
from pathlib import Path


PROFILE_DIR = Path(__file__).resolve().parents[1]

PROHIBITED_TRADING_TERMS = ("买入", "卖出", "减仓", "止损", "调整仓位", "目标价", "交易指令")


def _load_profile_alert_trigger():
    path = PROFILE_DIR / "modules" / "alert_trigger.py"
    spec = importlib.util.spec_from_file_location("siq_tracking_alert_trigger", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_profile_alert_recommendations_are_review_oriented():
    alert_trigger = _load_profile_alert_trigger().AlertTrigger()

    for level in ("INFO", "WATCH", "WARNING", "CRITICAL"):
        recommendation = alert_trigger._generate_recommendation(level, "净利润转亏")
        assert not any(term in recommendation for term in PROHIBITED_TRADING_TERMS)

    critical = alert_trigger._generate_recommendation("CRITICAL", "监管处罚")
    assert "人工审阅" in critical
    assert "风险暴露" in critical


def test_profile_alert_markdown_is_review_oriented():
    alert_trigger = _load_profile_alert_trigger().AlertTrigger()
    report = alert_trigger._build_alert_report(
        "600000",
        "测试公司",
        {"level": "CRITICAL", "name": "净利润转亏", "description": "归母净利润由盈转亏"},
        "归母净利润为 -1.23 亿元，出现亏损",
    )
    content = report.markdown

    assert not any(term in content for term in PROHIBITED_TRADING_TERMS)
    assert "人工审阅" in content
    assert "风险暴露" in content
