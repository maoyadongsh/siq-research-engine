import importlib.util
from pathlib import Path


def _load_importer():
    path = Path(__file__).resolve().parents[1] / "import_eu_evidence_package_to_postgres.py"
    spec = importlib.util.spec_from_file_location("import_eu_evidence_package_to_postgres", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_eu_database_url_defaults_to_siq_eu(monkeypatch):
    importer = _load_importer()
    for key in ("DATABASE_URL", "SIQ_EU_PGDATABASE", "SIQ_PGDATABASE", "PGDATABASE"):
        monkeypatch.delenv(key, raising=False)

    assert importer.database_url(None).endswith("/siq_eu")


def test_eu_database_url_prefers_eu_database_env(monkeypatch):
    importer = _load_importer()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SIQ_PGDATABASE", "siq")
    monkeypatch.setenv("SIQ_EU_PGDATABASE", "siq_eu_custom")

    assert importer.database_url(None).endswith("/siq_eu_custom")


def test_eu_importer_rejects_non_eu_schema():
    importer = _load_importer()
    try:
        importer.validate_schema("pdf2md")
    except SystemExit as exc:
        assert "eu_ifrs" in str(exc)
    else:
        raise AssertionError("validate_schema should reject legacy pdf2md")


def test_eu_importer_accepts_eu_schema():
    importer = _load_importer()
    importer.validate_schema("eu_ifrs")


def test_eu_importer_parse_run_id_is_stable():
    importer = _load_importer()
    manifest = {
        "filing_id": "EU:NL:ASML:2025:annual",
        "parser_version": "p1",
        "rules_version": "r1",
        "artifact_hashes": {"metrics/financial_data.json": "abc"},
    }
    first = importer.stable_parse_run_id(manifest, manifest["artifact_hashes"])
    second = importer.stable_parse_run_id(manifest, manifest["artifact_hashes"])
    assert first == second


def test_eu_importer_iterates_xbrl_record_shapes():
    importer = _load_importer()
    payload_list = {"contexts": [{"context_ref": "c1"}]}
    payload_dict = {"contexts": {"c2": {"period_end": "2025-12-31"}}}
    assert list(importer._iter_xbrl_records(payload_list, "contexts")) == [{"context_ref": "c1"}]
    assert list(importer._iter_xbrl_records(payload_dict, "contexts")) == [{"id": "c2", "period_end": "2025-12-31"}]
