import importlib.util
from pathlib import Path


def _load(name: str):
    path = Path(__file__).resolve().parents[1] / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_market_xbrl_database_url_defaults_to_market_database(monkeypatch):
    module = _load("import_market_xbrl_package_to_postgres")
    for key in ("DATABASE_URL", "SIQ_JP_PGDATABASE", "SIQ_KR_PGDATABASE", "SIQ_PGDATABASE", "PGDATABASE"):
        monkeypatch.delenv(key, raising=False)

    assert module.database_url(None, market="JP", default_database="siq_jp").endswith("/siq_jp")
    assert module.database_url(None, market="KR", default_database="siq_kr").endswith("/siq_kr")


def test_market_xbrl_database_url_prefers_market_database_env(monkeypatch):
    module = _load("import_market_xbrl_package_to_postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SIQ_PGDATABASE", "siq")
    monkeypatch.setenv("SIQ_JP_PGDATABASE", "siq_jp_custom")
    monkeypatch.setenv("SIQ_KR_PGDATABASE", "siq_kr_custom")

    assert module.database_url(None, market="JP", default_database="siq_jp").endswith("/siq_jp_custom")
    assert module.database_url(None, market="KR", default_database="siq_kr").endswith("/siq_kr_custom")


def test_jp_importer_rejects_wrong_schema():
    module = _load("import_jp_evidence_package_to_postgres")
    try:
        module.validate_schema("pdf2md")
    except SystemExit as exc:
        assert "edinet_jp" in str(exc)
    else:
        raise AssertionError("JP importer should reject pdf2md")


def test_kr_importer_rejects_wrong_schema():
    module = _load("import_kr_evidence_package_to_postgres")
    try:
        module.validate_schema("pdf2md")
    except SystemExit as exc:
        assert "dart_kr" in str(exc)
    else:
        raise AssertionError("KR importer should reject pdf2md")
