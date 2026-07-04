import importlib.util
import sys
import types
from pathlib import Path


def _load_importer():
    psycopg = types.ModuleType("psycopg")
    psycopg.connect = lambda *args, **kwargs: None
    psycopg_types = types.ModuleType("psycopg.types")
    psycopg_json = types.ModuleType("psycopg.types.json")
    psycopg_json.Jsonb = lambda value: value
    sys.modules.setdefault("psycopg", psycopg)
    sys.modules.setdefault("psycopg.types", psycopg_types)
    sys.modules.setdefault("psycopg.types.json", psycopg_json)

    path = Path(__file__).resolve().parents[1] / "import_sec_filing_to_postgres.py"
    spec = importlib.util.spec_from_file_location("import_sec_filing_to_postgres", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_sec_database_url_defaults_to_siq_us(monkeypatch):
    importer = _load_importer()
    for key in ("DATABASE_URL", "SIQ_US_PGDATABASE", "SIQ_PGDATABASE", "PGDATABASE"):
        monkeypatch.delenv(key, raising=False)

    assert importer.database_url(None).endswith("/siq_us")


def test_sec_database_url_prefers_us_database_env(monkeypatch):
    importer = _load_importer()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SIQ_PGDATABASE", "siq")
    monkeypatch.setenv("SIQ_US_PGDATABASE", "siq_us_custom")

    assert importer.database_url(None).endswith("/siq_us_custom")


def test_sec_database_url_prefers_us_database_over_generic_database_url(monkeypatch):
    importer = _load_importer()
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@127.0.0.1:15432/siq")
    monkeypatch.setenv("SIQ_US_PGDATABASE", "siq_us")

    assert importer.database_url(None).endswith("/siq_us")


def test_sec_db_fact_id_is_filing_scoped():
    importer = _load_importer()

    assert importer.db_fact_id("US:0001:aaa", "fact-local") != importer.db_fact_id("US:0002:bbb", "fact-local")
    assert importer.db_fact_id("US:0001:aaa", None) is None
