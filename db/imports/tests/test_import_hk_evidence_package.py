import importlib.util
import json
from pathlib import Path

import pytest


def _load_importer():
    path = Path(__file__).resolve().parents[1] / "import_hk_evidence_package_to_postgres.py"
    spec = importlib.util.spec_from_file_location("import_hk_evidence_package_to_postgres", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class FakeConnection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params or ()))


def _write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _touched_tables(conn: FakeConnection) -> set[str]:
    tables = set()
    for sql, _params in conn.calls:
        normalized = " ".join(sql.lower().split())
        if normalized.startswith("delete from "):
            table = normalized.split("delete from ", 1)[1].split(" ", 1)[0]
        elif "insert into " in normalized:
            table = normalized.split("insert into ", 1)[1].split(" ", 1)[0]
        else:
            continue
        tables.add(table.rsplit(".", 1)[-1])
    return tables


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
    assert "table_quality_signals" in ddl_text


def test_hk_database_url_defaults_to_siq_hk(monkeypatch):
    importer = _load_importer()
    for key in ("DATABASE_URL", "SIQ_HK_PGDATABASE", "SIQ_PGDATABASE", "PGDATABASE"):
        monkeypatch.delenv(key, raising=False)

    assert importer.database_url(None).endswith("/siq_hk")


def test_hk_upsert_company_writes_identity_columns():
    importer = _load_importer()
    conn = FakeConnection()
    manifest = {
        "company_id": "HK:00700",
        "ticker": "00700",
        "company_name": "Tencent",
        "stock_code": "00700",
        "hkex_stock_code": "00700",
        "short_name": "TENCENT",
        "company_name_en": "Tencent Holdings",
        "company_name_zh": "腾讯控股",
        "aliases": ["Tencent", "腾讯"],
    }

    importer._upsert_company(conn, "pdf2md_hk", manifest)

    sql, params = conn.calls[0]
    for column in ("stock_code", "hkex_stock_code", "short_name", "company_name_en", "company_name_zh", "aliases"):
        assert column in sql
    assert params[3:8] == ("00700", "00700", "TENCENT", "Tencent Holdings", "腾讯控股")


def test_hk_upsert_filing_writes_stock_code_and_falls_back_to_ticker(tmp_path):
    importer = _load_importer()
    manifest = {
        "filing_id": "HK:00700:12100024",
        "company_id": "HK:00700",
        "ticker": "00700",
        "stock_code": "0700",
        "form": "annual",
        "report_type": "annual",
    }

    conn = FakeConnection()
    importer._upsert_filing(conn, "pdf2md_hk", manifest, tmp_path, {})

    sql, params = conn.calls[0]
    assert "stock_code" in sql
    assert "stock_code = excluded.stock_code" in sql
    assert params[3] == "0700"

    fallback_manifest = {key: value for key, value in manifest.items() if key != "stock_code"}
    fallback_conn = FakeConnection()
    importer._upsert_filing(fallback_conn, "pdf2md_hk", fallback_manifest, tmp_path, {})

    _fallback_sql, fallback_params = fallback_conn.calls[0]
    assert fallback_params[3] == "00700"


def test_delete_run_rows_includes_v2_tables():
    importer = _load_importer()
    conn = FakeConnection()

    importer._delete_run_rows(conn, "pdf2md_hk", "run-1")

    deleted_tables = _touched_tables(conn)
    for table in (
        "table_quality_signals",
        "table_relations",
        "financial_note_links",
        "toc_entries",
        "footnotes",
        "content_blocks",
        "parser_artifacts",
    ):
        assert table in deleted_tables


def test_import_v2_artifacts_writes_parser_and_qa_tables(tmp_path):
    importer = _load_importer()
    package_dir = tmp_path
    _write_json(
        package_dir / "parser" / "document_full.json",
        {
            "content_list": [
                {
                    "id": "block-1",
                    "type": "text",
                    "page_idx": 87,
                    "text": "Management discussion",
                }
            ],
            "content_list_enhanced": {"summary": {"count": 1}},
        },
    )
    _write_json(
        package_dir / "parser" / "content_list_enhanced.json",
        {"summary": {"count": 1}},
    )
    _write_json(
        package_dir / "parser" / "table_relations.json",
        {
            "schema_version": "hk_table_relations_v1",
            "relations": [
                {
                    "page": 88,
                    "table_index": 1,
                    "target": "balance_sheet",
                    "related_table_id": "table-2",
                    "relation_type": "statement_note",
                }
            ],
        },
    )
    _write_json(
        package_dir / "qa" / "footnotes.json",
        {
            "schema_version": "hk_footnotes_v1",
            "payload": {
                "references": [
                    {"id": "fn1", "marker": "1", "content": "Footnote text", "page": 88, "table_index": 1, "target": "balance_sheet"}
                ]
            },
        },
    )
    _write_json(
        package_dir / "qa" / "toc.json",
        {"schema_version": "hk_toc_v1", "payload": {"headings": [{"title": "Overview", "level": 1, "page": 3}]}},
    )
    _write_json(
        package_dir / "qa" / "financial_note_links.json",
        {
            "schema_version": "hk_financial_note_links_v1",
            "payload": {
                "links": [
                    {
                        "note": "1",
                        "note_target": "note-1",
                        "statement": "balance_sheet",
                        "page": 88,
                        "table_index": 1,
                        "target": "total_assets",
                    }
                ]
            },
        },
    )
    _write_json(
        package_dir / "qa" / "table_quality_signals.json",
        {
            "schema_version": "hk_table_quality_signals_v1",
            "payload": {
                "signals": [
                    {"type": "table_header", "value": "ok", "page": 88, "table_index": 1, "target": "balance_sheet"}
                ]
            },
        },
    )

    conn = FakeConnection()
    parse_run_id = "run-a"
    filing_id = "filing-1"
    importer._insert_parser_artifacts(conn, "pdf2md_hk", package_dir, filing_id, parse_run_id)
    importer._insert_content_blocks(conn, "pdf2md_hk", package_dir, filing_id, parse_run_id)
    importer._insert_footnotes(conn, "pdf2md_hk", package_dir, filing_id, parse_run_id)
    importer._insert_toc_entries(conn, "pdf2md_hk", package_dir, filing_id, parse_run_id)
    importer._insert_financial_note_links(conn, "pdf2md_hk", package_dir, filing_id, parse_run_id)
    importer._insert_table_relations(conn, "pdf2md_hk", package_dir, filing_id, parse_run_id)
    importer._insert_table_quality_signals(conn, "pdf2md_hk", package_dir, filing_id, parse_run_id)

    assert {
        "parser_artifacts",
        "content_blocks",
        "footnotes",
        "toc_entries",
        "financial_note_links",
        "table_relations",
        "table_quality_signals",
    } <= _touched_tables(conn)
    generated_ids = {}
    for sql, params in conn.calls:
        normalized = " ".join(sql.lower().split())
        for table in ("content_blocks", "footnotes", "toc_entries", "financial_note_links", "table_relations", "table_quality_signals"):
            if f"insert into pdf2md_hk.{table}" in normalized:
                generated_ids[table] = params[0]

    assert generated_ids == {
        "content_blocks": importer.stable_id(parse_run_id, "parser/document_full.json", 88, None, "block-1", 1),
        "footnotes": importer.stable_id(parse_run_id, "qa/footnotes.json", 88, 1, "fn1", 1),
        "toc_entries": importer.stable_id(parse_run_id, "qa/toc.json", 3, None, "Overview", 1),
        "financial_note_links": importer.stable_id(parse_run_id, "qa/financial_note_links.json", 88, 1, "1", 1),
        "table_relations": importer.stable_id(parse_run_id, "parser/table_relations.json", 88, 1, "table-2", 1),
        "table_quality_signals": importer.stable_id(parse_run_id, "qa/table_quality_signals.json", 88, 1, "table_header", 1),
    }


def test_malformed_optional_v2_json_files_are_skipped(tmp_path):
    importer = _load_importer()
    package_dir = tmp_path
    for relative_path in (
        "parser/content_list_enhanced.json",
        "parser/document_full.json",
        "parser/table_relations.json",
        "qa/footnotes.json",
        "qa/toc.json",
        "qa/financial_note_links.json",
        "qa/table_quality_signals.json",
    ):
        path = package_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ malformed", encoding="utf-8")

    conn = FakeConnection()
    for insert in (
        importer._insert_parser_artifacts,
        importer._insert_content_blocks,
        importer._insert_footnotes,
        importer._insert_toc_entries,
        importer._insert_financial_note_links,
        importer._insert_table_relations,
        importer._insert_table_quality_signals,
    ):
        insert(conn, "pdf2md_hk", package_dir, "filing-1", "run-1")

    assert conn.calls == []


def test_strict_json_reader_still_raises_for_malformed_json(tmp_path):
    importer = _load_importer()
    path = tmp_path / "qa" / "quality_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ malformed", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        importer.read_json(path)
