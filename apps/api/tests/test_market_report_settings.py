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
        "SIQ_HK_PGDATABASE",
        "SIQ_HK_MILVUS_COLLECTION",
    ]:
        monkeypatch.delenv(name, raising=False)

    settings = _load_settings_module("temp_market_report_settings_defaults")

    assert settings.REPORT_FINDER_BASE == "http://127.0.0.1:18000"
    assert settings.MARKET_RULES_BASE == "http://127.0.0.1:18020"
    assert settings.MARKET_REPORT_PROXY_TIMEOUT == 120.0
    assert settings.MARKET_REPORT_ASSIST_TIMEOUT == 45.0
    assert settings.MARKET_WIKI_ROOTS["US"].name == "us_sec"
    assert settings.MARKET_BUILD_SCRIPTS["EU"].name == "build_eu_pdf_evidence_package.py"
    assert settings.MARKET_DATABASES["HK"] == "siq_hk"
    assert settings.MARKET_VECTOR_COLLECTIONS["HK"] == "siq_hk_reports"


def test_market_report_settings_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("SIQ_REPORT_FINDER_BASE", "http://example.test:18001")
    monkeypatch.setenv("SIQ_MARKET_REPORT_RULES_BASE", "http://example.test:18021")
    monkeypatch.setenv("SIQ_MARKET_REPORT_PROXY_TIMEOUT", "12.5")
    monkeypatch.setenv("SIQ_MARKET_REPORT_ASSIST_TIMEOUT", "6.75")
    monkeypatch.setenv("SIQ_US_SEC_CASE_SET_PATH", str(tmp_path / "case_set.json"))
    monkeypatch.setenv("SIQ_HK_PGDATABASE", "siq_hk_test")
    monkeypatch.setenv("SIQ_HK_MILVUS_COLLECTION", "siq_hk_vectors_test")

    settings = _load_settings_module("temp_market_report_settings_overrides")

    assert settings.REPORT_FINDER_BASE == "http://example.test:18001"
    assert settings.MARKET_RULES_BASE == "http://example.test:18021"
    assert settings.MARKET_REPORT_PROXY_TIMEOUT == 12.5
    assert settings.MARKET_REPORT_ASSIST_TIMEOUT == 6.75
    assert settings.US_SEC_CASE_SET_PATH == (tmp_path / "case_set.json").resolve()
    assert settings.MARKET_DATABASES["HK"] == "siq_hk_test"
    assert settings.MARKET_VECTOR_COLLECTIONS["HK"] == "siq_hk_vectors_test"


def test_market_report_settings_ignores_blank_primary_env_and_trims_legacy_url(monkeypatch):
    monkeypatch.setenv("SIQ_REPORT_FINDER_BASE", "   ")
    monkeypatch.setenv("REPORT_FINDER_BASE", "  http://legacy-finder.test/api/  ")
    monkeypatch.setenv("SIQ_MARKET_REPORT_RULES_BASE", "")
    monkeypatch.setenv("MARKET_REPORT_RULES_BASE", " http://legacy-rules.test/ ")

    settings = _load_settings_module("temp_market_report_settings_legacy_urls")

    assert settings.REPORT_FINDER_BASE == "http://legacy-finder.test/api"
    assert settings.MARKET_RULES_BASE == "http://legacy-rules.test"


def test_market_report_settings_invalid_float_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("SIQ_MARKET_REPORT_PROXY_TIMEOUT", "not-a-float")
    monkeypatch.setenv("SIQ_MARKET_REPORT_ASSIST_TIMEOUT", "   ")

    settings = _load_settings_module("temp_market_report_settings_invalid_float")

    assert settings.MARKET_REPORT_PROXY_TIMEOUT == 120.0
    assert settings.MARKET_REPORT_ASSIST_TIMEOUT == 45.0


def test_market_report_settings_market_specific_paths_resolve_env_overrides(monkeypatch, tmp_path):
    hk_root = tmp_path / "relative-root"
    jp_script = tmp_path / "scripts" / "jp_build.py"
    eu_import = tmp_path / "imports" / "eu_import.py"
    monkeypatch.setenv("SIQ_HK_WIKI_ROOT", str(hk_root))
    monkeypatch.setenv("SIQ_JP_PACKAGE_BUILD_SCRIPT", str(jp_script))
    monkeypatch.setenv("SIQ_EU_IMPORT_SCRIPT", str(eu_import))

    settings = _load_settings_module("temp_market_report_settings_market_paths")

    assert settings.MARKET_WIKI_ROOTS["HK"] == hk_root.resolve()
    assert settings.MARKET_BUILD_SCRIPTS["JP"] == jp_script.resolve()
    assert settings.MARKET_IMPORT_SCRIPTS["EU"] == eu_import.resolve()
