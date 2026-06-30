import importlib.util
from pathlib import Path


def _load_settings_module(tmp_name: str = "temp_market_report_settings"):
    source = Path(__file__).resolve().parents[1] / "services" / "market_report_settings.py"
    spec = importlib.util.spec_from_file_location(tmp_name, source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_market_report_settings_defaults(monkeypatch):
    for name in [
        "SIQ_REPORT_FINDER_BASE",
        "REPORT_FINDER_BASE",
        "SIQ_MARKET_REPORT_RULES_BASE",
        "MARKET_REPORT_RULES_BASE",
        "SIQ_MARKET_REPORT_PROXY_TIMEOUT",
        "SIQ_MARKET_REPORT_ASSIST_TIMEOUT",
    ]:
        monkeypatch.delenv(name, raising=False)

    settings = _load_settings_module("temp_market_report_settings_defaults")

    assert settings.REPORT_FINDER_BASE == "http://127.0.0.1:18000"
    assert settings.MARKET_RULES_BASE == "http://127.0.0.1:18020"
    assert settings.MARKET_REPORT_PROXY_TIMEOUT == 120.0
    assert settings.MARKET_REPORT_ASSIST_TIMEOUT == 45.0
    assert settings.MARKET_WIKI_ROOTS["US"].name == "us_sec"
    assert settings.MARKET_BUILD_SCRIPTS["EU"].name == "build_eu_pdf_evidence_package.py"


def test_market_report_settings_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("SIQ_REPORT_FINDER_BASE", "http://example.test:18001")
    monkeypatch.setenv("SIQ_MARKET_REPORT_RULES_BASE", "http://example.test:18021")
    monkeypatch.setenv("SIQ_MARKET_REPORT_PROXY_TIMEOUT", "12.5")
    monkeypatch.setenv("SIQ_MARKET_REPORT_ASSIST_TIMEOUT", "6.75")
    monkeypatch.setenv("SIQ_US_SEC_CASE_SET_PATH", str(tmp_path / "case_set.json"))

    settings = _load_settings_module("temp_market_report_settings_overrides")

    assert settings.REPORT_FINDER_BASE == "http://example.test:18001"
    assert settings.MARKET_RULES_BASE == "http://example.test:18021"
    assert settings.MARKET_REPORT_PROXY_TIMEOUT == 12.5
    assert settings.MARKET_REPORT_ASSIST_TIMEOUT == 6.75
    assert settings.US_SEC_CASE_SET_PATH == (tmp_path / "case_set.json").resolve()
