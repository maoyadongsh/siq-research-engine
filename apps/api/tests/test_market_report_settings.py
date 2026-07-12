import importlib.util
from pathlib import Path

from services import market_report_commands


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
    assert settings.MARKET_WIKI_ROOTS["US"].parts[-3:] == ("data", "wiki", "us")
    assert settings.MARKET_WIKI_ROOTS["HK"].parts[-3:] == ("data", "wiki", "hk")
    assert settings.MARKET_WIKI_ROOTS["KR"].parts[-3:] == ("data", "wiki", "kr")
    assert settings.MARKET_DATABASES["HK"] == "siq_hk"
    assert settings.MARKET_VECTOR_COLLECTIONS["US"] == "siq_us_sec_filings"
    assert settings.MARKET_VECTOR_COLLECTIONS["US_SEC"] == settings.MARKET_VECTOR_COLLECTIONS["US"]
    assert settings.MARKET_VECTOR_COLLECTIONS["HK"] == "siq_hk_reports"
    assert settings.MARKET_BUILD_SCRIPTS["EU"].name == "build_eu_pdf_evidence_package.py"
    assert settings.MARKET_DOCUMENT_FULL_ROOTS["US"].parts[-3:] == ("data", "parser-results", "us-sec")
    assert settings.MARKET_DOCUMENT_FULL_ROOTS["US_SEC"] == settings.MARKET_DOCUMENT_FULL_ROOTS["US"]
    assert settings.MARKET_DOCUMENT_FULL_ROOTS["HK"].parts[-3:] == ("data", "pdf-parser", "results")
    assert settings.MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS["HK"].name == "import_hk_document_full_to_postgres.py"
    assert settings.MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS["US"].name == "import_us_sec_document_full_to_postgres.py"
    assert settings.MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS["US_SEC"] == settings.MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS["US"]
    assert settings.MARKET_DATABASES["US_SEC"] == settings.MARKET_DATABASES["US"]


def test_market_report_settings_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("SIQ_REPORT_FINDER_BASE", "http://example.test:18001")
    monkeypatch.setenv("SIQ_MARKET_REPORT_RULES_BASE", "http://example.test:18021")
    monkeypatch.setenv("SIQ_MARKET_REPORT_PROXY_TIMEOUT", "12.5")
    monkeypatch.setenv("SIQ_MARKET_REPORT_ASSIST_TIMEOUT", "6.75")
    monkeypatch.setenv("SIQ_US_SEC_CASE_SET_PATH", str(tmp_path / "case_set.json"))
    monkeypatch.setenv("SIQ_US_VECTOR_COLLECTION", "siq_us_sec_filings_shadow")

    settings = _load_settings_module("temp_market_report_settings_overrides")

    assert settings.REPORT_FINDER_BASE == "http://example.test:18001"
    assert settings.MARKET_RULES_BASE == "http://example.test:18021"
    assert settings.MARKET_REPORT_PROXY_TIMEOUT == 12.5
    assert settings.MARKET_REPORT_ASSIST_TIMEOUT == 6.75
    assert settings.US_SEC_CASE_SET_PATH == (tmp_path / "case_set.json").resolve()
    assert settings.MARKET_VECTOR_COLLECTIONS["US"] == "siq_us_sec_filings_shadow"
    assert settings.MARKET_VECTOR_COLLECTIONS["US_SEC"] == "siq_us_sec_filings_shadow"


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
    kr_document_root = tmp_path / "parser-results" / "kr"
    hk_document_import = tmp_path / "imports" / "hk_document_full.py"
    monkeypatch.setenv("SIQ_HK_WIKI_ROOT", str(hk_root))
    monkeypatch.setenv("SIQ_JP_PACKAGE_BUILD_SCRIPT", str(jp_script))
    monkeypatch.setenv("SIQ_EU_IMPORT_SCRIPT", str(eu_import))
    monkeypatch.setenv("SIQ_KR_DOCUMENT_FULL_ROOT", str(kr_document_root))
    monkeypatch.setenv("SIQ_HK_DOCUMENT_FULL_IMPORT_SCRIPT", str(hk_document_import))

    settings = _load_settings_module("temp_market_report_settings_market_paths")

    assert settings.MARKET_WIKI_ROOTS["HK"] == hk_root.resolve()
    assert settings.MARKET_BUILD_SCRIPTS["JP"] == jp_script.resolve()
    assert settings.MARKET_IMPORT_SCRIPTS["EU"] == eu_import.resolve()
    assert settings.MARKET_DOCUMENT_FULL_ROOTS["KR"] == kr_document_root.resolve()
    assert settings.MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS["HK"] == hk_document_import.resolve()


def test_market_vector_ingest_args_use_contract_us_collection_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("SIQ_US_VECTOR_COLLECTION", raising=False)
    settings = _load_settings_module("temp_market_report_settings_vector_args")

    args, dry_run = market_report_commands.market_vector_ingest_args(
        executable="python",
        script=tmp_path / "ingest_market_evidence_chunks.py",
        package_dir=tmp_path / "pkg",
        payload={"dry_run": False},
        market="US",
        market_vector_collections=settings.MARKET_VECTOR_COLLECTIONS,
    )

    assert dry_run is False
    assert args[args.index("--collection") + 1] == "siq_us_sec_filings"
