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


def test_hk_ddl_contains_v2_tables_and_identity_columns():
    ddl_path = Path(__file__).resolve().parents[2] / "ddl" / "020_create_pdf2md_hk_schema.sql"
    ddl_text = ddl_path.read_text()

    assert "short_name" in ddl_text
    assert "stock_code" in ddl_text
    assert "hkex_stock_code" in ddl_text
    assert "content_blocks" in ddl_text
    assert "footnotes" in ddl_text
    assert "toc_entries" in ddl_text
    assert "financial_note_links" in ddl_text
    assert "table_relations" in ddl_text
    assert "parser_artifacts" in ddl_text
