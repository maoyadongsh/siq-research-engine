import importlib.util
from pathlib import Path


def _load_importer():
    path = Path(__file__).resolve().parents[1] / "import_hk_evidence_package_to_postgres.py"
    spec = importlib.util.spec_from_file_location("import_hk_evidence_package_to_postgres", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_hk_importer_rejects_non_hk_schema():
    importer = _load_importer()
    try:
        importer.validate_schema("pdf2md")
    except SystemExit as exc:
        assert "pdf2md_hk" in str(exc)
    else:
        raise AssertionError("validate_schema should reject legacy pdf2md")


def test_hk_importer_parse_run_id_is_stable():
    importer = _load_importer()
    manifest = {
        "filing_id": "HK:00700:12100024",
        "parser_version": "p1",
        "rules_version": "r1",
        "artifact_hashes": {"metrics/financial_data.json": "abc"},
    }
    first = importer.stable_parse_run_id(manifest, manifest["artifact_hashes"])
    second = importer.stable_parse_run_id(manifest, manifest["artifact_hashes"])
    assert first == second


def test_hk_importer_database_url_defaults_to_siq_hk(monkeypatch):
    importer = _load_importer()
    for name in ("DATABASE_URL", "SIQ_HK_PGDATABASE", "SIQ_PGDATABASE", "PGDATABASE"):
        monkeypatch.delenv(name, raising=False)

    assert importer.database_url(None).endswith("/siq_hk")


def test_hk_importer_database_url_prefers_hk_database_env(monkeypatch):
    importer = _load_importer()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SIQ_PGDATABASE", "siq")
    monkeypatch.setenv("PGDATABASE", "postgres")
    monkeypatch.setenv("SIQ_HK_PGDATABASE", "siq_hk_custom")

    assert importer.database_url(None).endswith("/siq_hk_custom")
