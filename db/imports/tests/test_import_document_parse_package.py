import importlib.util
import json
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

    path = Path(__file__).resolve().parents[1] / "import_document_parse_package_to_postgres.py"
    spec = importlib.util.spec_from_file_location("import_document_parse_package_to_postgres", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class FakeTransaction:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeConn:
    def __init__(self):
        self.calls = []

    def transaction(self):
        return FakeTransaction()

    def execute(self, sql, params=None):
        self.calls.append((sql, params))


def write_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def build_package(package_dir: Path):
    write_json(package_dir / "manifest.json", {
        "schema_version": "generic_document_package_v1",
        "document_id": "doc-task-a",
        "task_id": "task-a",
        "collection": "default",
        "document_key": "demo-task-a",
        "filename": "demo.pdf",
        "document_kind": "pdf",
        "parser_provider": "pypdf_text_parser",
        "document_full_sha256": "abc",
    })
    write_json(package_dir / "qa" / "parse_manifest.json", {"task_id": "task-a", "file_sha256": "file-sha", "parser_version": "v1"})
    write_json(package_dir / "qa" / "quality_report.json", {"overall_status": "pass", "warnings": []})
    write_json(package_dir / "sections" / "blocks.json", {
        "blocks": [{"block_id": "b1", "type": "paragraph", "text": "hello", "source_ref": {"evidence_id": "e1"}}]
    })
    write_json(package_dir / "tables" / "tables.json", {
        "physical_tables": [{"table_id": "t1", "quality": {"row_count": 1, "column_count": 1}, "cells": [{"row_index": 0, "column_index": 0, "text": "A"}]}]
    })
    write_json(package_dir / "logical_tables" / "logical_tables.json", {"logical_tables": [{"logical_table_id": "lt1", "fragment_table_ids": ["t1"]}]})
    write_json(package_dir / "logical_tables" / "table_relations.json", {"relations": []})
    write_json(package_dir / "figures" / "figures.json", {"figures": [{"image_id": "img1", "caption": "cap"}]})
    write_json(package_dir / "qa" / "source_map.json", {"sources": [{"evidence_id": "e1", "source_type": "text_block", "block_id": "b1"}]})
    write_json(package_dir / "extraction" / "result.json", {"status": "not_run", "result": {}})


def test_document_importer_rejects_wrong_schema():
    importer = _load_importer()
    try:
        importer.validate_schema("pdf2md")
    except SystemExit as exc:
        assert "document_parser" in str(exc)
    else:
        raise AssertionError("validate_schema should reject non document_parser schema")


def test_document_parse_run_id_is_stable():
    importer = _load_importer()
    manifest = {"task_id": "task-a", "document_full_sha256": "abc"}
    hashes = {"manifest.json": "1"}
    assert importer.stable_parse_run_id(manifest, hashes) == importer.stable_parse_run_id(manifest, hashes)


def test_document_import_package_emits_core_inserts(tmp_path):
    importer = _load_importer()
    package_dir = tmp_path / "package"
    build_package(package_dir)
    conn = FakeConn()

    parse_run_id = importer.import_package(conn, package_dir)

    assert parse_run_id
    sql_text = "\n".join(sql for sql, _ in conn.calls)
    assert "document_parser.documents" in sql_text
    assert "document_parser.parse_runs" in sql_text
    assert "document_parser.blocks" in sql_text
    assert "document_parser.sources" in sql_text
