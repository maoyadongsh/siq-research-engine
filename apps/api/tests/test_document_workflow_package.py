from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

spec = importlib.util.spec_from_file_location("workflow_router", BACKEND_ROOT / "routers" / "workflow.py")
workflow = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(workflow)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def build_result_dir(root: Path, task_id: str) -> Path:
    result_dir = root / task_id
    result_dir.mkdir(parents=True)
    manifest = {
        "schema_version": "generic_document_parse_v1",
        "task_id": task_id,
        "filename": "Contract Demo.pdf",
        "document_kind": "pdf",
        "parser_provider": "pypdf_text_parser",
    }
    write_json(result_dir / "manifest.json", manifest)
    (result_dir / "document.md").write_text("# Contract Demo\n\nparty_a: Alice\n", encoding="utf-8")
    write_json(result_dir / "document_full.json", {"schema_version": "document_full_v1", "task_id": task_id})
    write_json(result_dir / "blocks.json", {"schema_version": "document_blocks_v1", "task_id": task_id, "blocks": []})
    write_json(result_dir / "tables.json", {"schema_version": "document_tables_v1", "task_id": task_id, "tables": []})
    write_json(result_dir / "logical_tables.json", {"schema_version": "document_logical_tables_v1", "task_id": task_id, "logical_tables": []})
    write_json(result_dir / "table_relations.json", {"schema_version": "document_table_relations_v1", "task_id": task_id, "relations": []})
    write_json(result_dir / "figures.json", {"schema_version": "document_figures_v1", "task_id": task_id, "figures": []})
    write_json(result_dir / "figure_index.json", {"schema_version": "document_figure_index_v1", "task_id": task_id, "figures": []})
    write_json(result_dir / "comparison_map.json", {"schema_version": "document_comparison_map_v1", "task_id": task_id, "entries": []})
    write_json(result_dir / "source_map.json", {"schema_version": "document_source_map_v1", "task_id": task_id, "sources": []})
    write_json(result_dir / "quality_report.json", {"schema_version": "document_quality_v1", "task_id": task_id, "overall_status": "pass"})
    write_json(result_dir / "extraction" / "result.json", {"schema_version": "document_extraction_result_v1", "task_id": task_id})
    write_json(result_dir / "raw" / "original" / "Contract Demo.pdf", {"placeholder": True})
    return result_dir


def test_document_wiki_import_builds_package_from_existing_artifacts(monkeypatch, tmp_path):
    task_id = "task-doc-001"
    results_root = tmp_path / "results"
    wiki_root = tmp_path / "wiki" / "documents"
    build_result_dir(results_root, task_id)
    monkeypatch.setattr(workflow, "DOCUMENT_PARSER_RESULTS_ROOT", results_root)
    monkeypatch.setattr(workflow, "DOCUMENT_WIKI_ROOT", wiki_root)

    status = workflow._document_workflow_status_payload(task_id, "contracts")
    assert status["artifacts"]["ready"] is True
    assert status["targets"]["wiki"]["status"] == "missing"

    result = workflow._import_document_task_to_wiki(task_id, "contracts")
    package_dir = Path(result["packageDir"])

    assert result["ok"] is True
    assert package_dir.is_dir()
    assert (package_dir / "manifest.json").is_file()
    assert (package_dir / "README.md").read_text(encoding="utf-8").startswith("# Contract Demo.pdf")
    assert (package_dir / "sections" / "document.md").is_file()
    assert (package_dir / "qa" / "source_map.json").is_file()
    assert (package_dir / "raw" / "original" / "Contract Demo.pdf").is_file()

    package_manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    assert package_manifest["schema_version"] == "generic_document_package_v1"
    assert package_manifest["collection"] == "contracts"
    assert package_manifest["task_id"] == task_id

    next_status = workflow._document_workflow_status_payload(task_id, "contracts")
    assert next_status["targets"]["wiki"]["status"] == "ready"


def test_document_workflow_status_payload_keeps_target_contract(monkeypatch, tmp_path):
    task_id = "task-doc-status"
    results_root = tmp_path / "results"
    wiki_root = tmp_path / "wiki" / "documents"
    build_result_dir(results_root, task_id)
    monkeypatch.setattr(workflow, "DOCUMENT_PARSER_RESULTS_ROOT", results_root)
    monkeypatch.setattr(workflow, "DOCUMENT_WIKI_ROOT", wiki_root)
    monkeypatch.setattr(workflow, "DOCUMENT_DB_IMPORT_SCRIPT", tmp_path / "missing_db_import.py")
    monkeypatch.setattr(workflow, "DOCUMENT_CHUNK_SCRIPT", tmp_path / "missing_chunks.py")

    payload = workflow._document_workflow_status_payload(task_id, "contracts")

    assert set(payload) == {"taskId", "targets", "artifacts"}
    assert payload["taskId"] == task_id
    assert payload["artifacts"]["ready"] is True

    targets = payload["targets"]
    assert set(targets) == {"wiki", "postgres", "milvus", "full_text", "object_storage"}
    assert targets["full_text"] == {"status": "disabled"}
    assert targets["object_storage"] == {"status": "disabled"}

    wiki = targets["wiki"]
    assert {
        "status",
        "taskId",
        "collection",
        "documentKey",
        "path",
        "manifestPath",
        "documentFullSha256",
        "sourceDocumentFullSha256",
        "stale",
        "message",
    }.issubset(wiki)
    assert wiki["status"] == "missing"
    assert wiki["collection"] == "contracts"
    assert wiki["sourceDocumentFullSha256"]
    assert targets["postgres"]["status"] == "missing"
    assert targets["milvus"]["status"] == "missing"


def test_document_wiki_import_response_keeps_lightweight_package_contract(monkeypatch, tmp_path):
    task_id = "task-doc-import-contract"
    results_root = tmp_path / "results"
    wiki_root = tmp_path / "wiki" / "documents"
    build_result_dir(results_root, task_id)
    monkeypatch.setattr(workflow, "DOCUMENT_PARSER_RESULTS_ROOT", results_root)
    monkeypatch.setattr(workflow, "DOCUMENT_WIKI_ROOT", wiki_root)

    result = workflow._import_document_task_to_wiki(task_id, "contracts")
    package_dir = Path(result["packageDir"])
    package_manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    artifact_manifest = json.loads((package_dir / "artifact_manifest.json").read_text(encoding="utf-8"))

    assert {
        "ok",
        "taskId",
        "collection",
        "documentKey",
        "packageDir",
        "manifestPath",
        "copiedFiles",
        "copiedDirectories",
        "wiki",
    } == set(result)
    assert result["ok"] is True
    assert result["taskId"] == task_id
    assert result["collection"] == "contracts"
    assert result["documentKey"] == result["wiki"]["documentKey"]
    assert result["manifestPath"] == str(package_dir / "manifest.json")
    assert set(result["copiedFiles"]) == {"manifest.json", "document.md", "quality_report.json", "source_map.json"}
    assert result["copiedDirectories"] == {"raw/original": 1}
    assert result["wiki"]["status"] == "ready"
    assert result["wiki"]["path"] == result["packageDir"]
    assert result["wiki"]["manifestPath"] == result["manifestPath"]
    assert result["wiki"]["stale"] is False

    assert package_manifest["schema_version"] == "generic_document_package_v1"
    assert package_manifest["document_id"] == f"doc-{task_id}"
    assert package_manifest["task_id"] == task_id
    assert package_manifest["document_key"] == result["documentKey"]
    assert package_manifest["filename"] == "Contract Demo.pdf"
    assert package_manifest["document_kind"] == "pdf"
    assert package_manifest["parser_provider"] == "pypdf_text_parser"
    assert package_manifest["package_version"] == "1"
    assert package_manifest["document_full_sha256"]
    assert package_manifest["full_parse_archive"] == "document_parser_results_and_postgresql"
    assert set(package_manifest["wiki_keeps"]) >= {
        "README.md",
        "manifest.json",
        "artifact_manifest.json",
        "qa/parse_manifest.json",
        "sections/document.md",
        "qa/quality_report.json",
        "qa/source_map.json",
        "raw/original",
        "images/original",
    }
    assert package_manifest["import_targets"] == {
        "postgres": {"schema": "document_parser", "document_id": f"doc-{task_id}", "last_imported_at": None},
        "milvus": {"collection": "siq_documents", "last_imported_at": None},
    }

    artifacts = package_manifest["artifacts"]
    assert artifacts["document.md"]["package_path"] == "sections/document.md"
    for artifact_name in [
        "document_full.json",
        "blocks.json",
        "tables.json",
        "logical_tables.json",
        "table_relations.json",
        "figures.json",
        "figure_index.json",
        "comparison_map.json",
    ]:
        artifact = artifacts[artifact_name]
        assert artifact["package_path"] == ""
        assert artifact["sha256"]
        assert artifact["size_bytes"] > 0
        assert artifact["source"].startswith(str(results_root))
    assert not (package_dir / "document_full.json").exists()
    assert not (package_dir / "blocks.json").exists()
    assert (package_dir / "raw" / "original" / "Contract Demo.pdf").is_file()

    assert artifact_manifest["schema_version"] == "generic_document_artifact_manifest_v1"
    assert artifact_manifest["artifacts"] == package_manifest["artifacts"]


def test_document_wiki_import_rejects_incomplete_artifacts(monkeypatch, tmp_path):
    task_id = "task-doc-002"
    results_root = tmp_path / "results"
    wiki_root = tmp_path / "wiki" / "documents"
    result_dir = build_result_dir(results_root, task_id)
    (result_dir / "source_map.json").unlink()
    monkeypatch.setattr(workflow, "DOCUMENT_PARSER_RESULTS_ROOT", results_root)
    monkeypatch.setattr(workflow, "DOCUMENT_WIKI_ROOT", wiki_root)

    with pytest.raises(workflow.HTTPException) as exc:
        workflow._import_document_task_to_wiki(task_id, "contracts")
    assert exc.value.status_code == 422


def test_document_db_connect_config_defaults_to_document_parser_database(monkeypatch):
    monkeypatch.setattr(workflow, "_load_pg_config", lambda: None)
    for key in ("SIQ_DOCUMENT_PGDATABASE", "SIQ_PGDATABASE", "PGDATABASE"):
        monkeypatch.delenv(key, raising=False)

    assert workflow._db_connect_config()["dbname"] == "siq_document_parser"


def test_document_db_connect_config_prefers_document_database_env(monkeypatch):
    monkeypatch.setattr(workflow, "_load_pg_config", lambda: None)
    monkeypatch.setenv("SIQ_PGDATABASE", "siq")
    monkeypatch.setenv("SIQ_DOCUMENT_PGDATABASE", "siq_document_parser_custom")

    assert workflow._db_connect_config()["dbname"] == "siq_document_parser_custom"


def test_document_db_import_endpoint_uses_wiki_package(monkeypatch, tmp_path):
    task_id = "task-doc-003"
    results_root = tmp_path / "results"
    wiki_root = tmp_path / "wiki" / "documents"
    script = tmp_path / "import_document_parse_package_to_postgres.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    build_result_dir(results_root, task_id)
    monkeypatch.setattr(workflow, "DOCUMENT_PARSER_RESULTS_ROOT", results_root)
    monkeypatch.setattr(workflow, "DOCUMENT_WIKI_ROOT", wiki_root)
    monkeypatch.setattr(workflow, "DOCUMENT_DB_IMPORT_SCRIPT", script)
    monkeypatch.setattr(workflow, "_db_connect_config", lambda: {"host": "h", "port": 5432, "dbname": "d", "user": "u", "password": "p"})
    seen = {}

    def fake_run_command(args, timeout=300, env=None):
        seen["args"] = args
        seen["timeout"] = timeout
        seen["env"] = env
        return {"returnCode": 0, "stdout": '{"ok":true}', "stderr": ""}

    monkeypatch.setattr(workflow, "_run_command", fake_run_command)
    workflow._import_document_task_to_wiki(task_id, "contracts")

    result = workflow.import_document_task_to_database(task_id, "contracts")

    assert result["ok"] is True
    assert str(script) in seen["args"]
    assert seen["timeout"] == 300
    assert seen["env"]["PGHOST"] == "h"
    assert seen["env"]["PGPORT"] == "5432"
    assert seen["env"]["PGDATABASE"] == "d"
    assert seen["env"]["PGUSER"] == "u"
    assert seen["env"]["PGPASSWORD"] == "p"
    assert seen["env"]["DATABASE_URL"] == "postgresql://u:p@h:5432/d"
    assert result["postgres"]["status"] == "ready"


def test_document_db_import_rejects_missing_package_manifest(monkeypatch, tmp_path):
    package_dir = tmp_path / "wiki" / "documents" / "contracts" / "doc"
    package_dir.mkdir(parents=True)
    script = tmp_path / "import_document_parse_package_to_postgres.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(workflow, "DOCUMENT_DB_IMPORT_SCRIPT", script)
    monkeypatch.setattr(
        workflow,
        "_document_wiki_status",
        lambda task_id, collection: {"status": "ready", "path": str(package_dir)},
    )

    with pytest.raises(workflow.HTTPException) as exc:
        workflow.import_document_task_to_database("task-doc-missing-manifest", "contracts")

    assert exc.value.status_code == 404


def test_document_db_import_rejects_missing_wiki_without_running_command(monkeypatch, tmp_path):
    script = tmp_path / "import_document_parse_package_to_postgres.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(workflow, "DOCUMENT_DB_IMPORT_SCRIPT", script)
    monkeypatch.setattr(
        workflow,
        "_document_wiki_status",
        lambda task_id, collection: {"status": "missing"},
    )

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("DB import must not run without a ready Wiki package")

    monkeypatch.setattr(workflow, "_run_command", fail_if_called)

    with pytest.raises(workflow.HTTPException) as exc:
        workflow.import_document_task_to_database("task-doc-missing-wiki", "contracts")

    assert exc.value.status_code == 422


def test_document_db_import_rejects_missing_script_without_running_command(monkeypatch, tmp_path):
    package_dir = tmp_path / "wiki" / "documents" / "contracts" / "doc"
    package_dir.mkdir(parents=True)
    write_json(package_dir / "manifest.json", {"schema_version": "generic_document_package_v1"})
    monkeypatch.setattr(workflow, "DOCUMENT_DB_IMPORT_SCRIPT", tmp_path / "missing_import.py")
    monkeypatch.setattr(
        workflow,
        "_document_wiki_status",
        lambda task_id, collection: {"status": "ready", "path": str(package_dir)},
    )

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("DB import must not run without its script")

    monkeypatch.setattr(workflow, "_run_command", fail_if_called)

    with pytest.raises(workflow.HTTPException) as exc:
        workflow.import_document_task_to_database("task-doc-missing-script", "contracts")

    assert exc.value.status_code == 500
    assert "Document DB import script not found" in exc.value.detail


def test_document_db_import_maps_command_failure_to_500(monkeypatch, tmp_path):
    task_id = "task-doc-db-failure"
    results_root = tmp_path / "results"
    wiki_root = tmp_path / "wiki" / "documents"
    script = tmp_path / "import_document_parse_package_to_postgres.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    build_result_dir(results_root, task_id)
    monkeypatch.setattr(workflow, "DOCUMENT_PARSER_RESULTS_ROOT", results_root)
    monkeypatch.setattr(workflow, "DOCUMENT_WIKI_ROOT", wiki_root)
    monkeypatch.setattr(workflow, "DOCUMENT_DB_IMPORT_SCRIPT", script)
    monkeypatch.setattr(workflow, "_db_connect_config", lambda: {"host": "h", "port": 5432, "dbname": "d", "user": "u", "password": "p"})
    failure = {"returnCode": 1, "stdout": "", "stderr": "boom"}
    monkeypatch.setattr(workflow, "_run_command", lambda *args, **kwargs: failure)
    workflow._import_document_task_to_wiki(task_id, "contracts")

    with pytest.raises(workflow.HTTPException) as exc:
        workflow.import_document_task_to_database(task_id, "contracts")

    assert exc.value.status_code == 500
    assert exc.value.detail == failure


def test_task_db_import_uses_config_py_without_env(monkeypatch, tmp_path):
    document_full = tmp_path / "document_full.json"
    document_full.write_text("{}", encoding="utf-8")
    script = tmp_path / "db_import.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    config_py = tmp_path / "config.py"
    config_py.write_text("DATABASE_URL = 'postgresql://from-config'\n", encoding="utf-8")
    seen = {}

    def fake_run_command(args, timeout=300, env=None):
        seen["args"] = args
        seen["timeout"] = timeout
        seen["env"] = env
        return {"returnCode": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(workflow, "_find_task_document_full", lambda task_id: document_full)
    monkeypatch.setattr(workflow, "DB_IMPORT_SCRIPT", script)
    monkeypatch.setattr(workflow, "DB_CONFIG_PY", config_py)
    monkeypatch.setattr(workflow, "_run_command", fake_run_command)
    monkeypatch.setattr(workflow, "_db_status", lambda task_id: {"status": "ready", "task_id": task_id})

    result = workflow.import_task_to_database("task-db-config")

    assert result == {
        "ok": True,
        "taskId": "task-db-config",
        "documentFull": str(document_full),
        "result": {"returnCode": 0, "stdout": "ok", "stderr": ""},
        "database": {"status": "ready", "task_id": "task-db-config"},
    }
    assert seen["args"] == [sys.executable, str(script), str(document_full), "--ddl", "--config-py", str(config_py)]
    assert seen["timeout"] == 300
    assert seen["env"] is None


def test_task_db_import_uses_database_url_env_without_config_py(monkeypatch, tmp_path):
    document_full = tmp_path / "document_full.json"
    document_full.write_text("{}", encoding="utf-8")
    script = tmp_path / "db_import.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    missing_config = tmp_path / "missing_config.py"
    seen = {}

    def fake_run_command(args, timeout=300, env=None):
        seen["args"] = args
        seen["timeout"] = timeout
        seen["env"] = env
        return {"returnCode": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(workflow, "_find_task_document_full", lambda task_id: document_full)
    monkeypatch.setattr(workflow, "DB_IMPORT_SCRIPT", script)
    monkeypatch.setattr(workflow, "DB_CONFIG_PY", missing_config)
    monkeypatch.setattr(workflow, "_pdf2md_db_connect_config", lambda: {"host": "h", "port": 5432, "dbname": "d", "user": "u", "password": "p"})
    monkeypatch.setattr(workflow, "_run_command", fake_run_command)
    monkeypatch.setattr(workflow, "_db_status", lambda task_id: {"status": "ready"})

    workflow.import_task_to_database("task-db-env")

    assert seen["args"] == [
        sys.executable,
        str(script),
        str(document_full),
        "--ddl",
        "--database-url",
        "postgresql://u:p@h:5432/d",
    ]
    assert seen["timeout"] == 300
    assert seen["env"]["PGHOST"] == "h"
    assert seen["env"]["PGPORT"] == "5432"
    assert seen["env"]["PGDATABASE"] == "d"
    assert seen["env"]["PGUSER"] == "u"
    assert seen["env"]["PGPASSWORD"] == "p"
    assert seen["env"]["DATABASE_URL"] == "postgresql://u:p@h:5432/d"


def test_task_db_import_routes_pdf_market_to_document_full_importer(monkeypatch, tmp_path):
    document_full = tmp_path / "hk-task" / "document_full.json"
    document_full.parent.mkdir(parents=True)
    document_full.write_text('{"metadata":{"market":"HK"}}', encoding="utf-8")
    market_script = tmp_path / "imports" / "import_hk_document_full_to_postgres.py"
    market_script.parent.mkdir(parents=True)
    market_script.write_text("print('ok')\n", encoding="utf-8")
    a_share_script = tmp_path / "imports" / "import_document_full_to_postgres.py"
    a_share_script.write_text("print('a share')\n", encoding="utf-8")
    seen = {}

    def fake_run_command(args, timeout=300, env=None):
        seen["args"] = args
        seen["timeout"] = timeout
        seen["env"] = env
        return {"returnCode": 0, "stdout": "market ok", "stderr": ""}

    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:secret@db/siq")
    monkeypatch.setattr(workflow, "_find_task_document_full", lambda task_id: document_full)
    monkeypatch.setattr(workflow, "_infer_task_market", lambda task_id: "HK")
    monkeypatch.setitem(workflow.MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS, "HK", market_script)
    monkeypatch.setitem(workflow.MARKET_DATABASES, "HK", "siq_hk")
    monkeypatch.setattr(workflow, "DB_IMPORT_SCRIPT", a_share_script)
    monkeypatch.setattr(workflow, "_run_command", fake_run_command)
    monkeypatch.setattr(
        workflow,
        "_market_document_full_db_status",
        lambda task_id, market, document_full: {"status": "ready", "market": market, "schema": "pdf2md_hk", "parseRunId": "parse-hk"},
    )

    result = workflow.import_task_to_database("task-hk-db")

    assert result["ok"] is True
    assert result["market"] == "HK"
    assert result["database"] == {"status": "ready", "market": "HK", "schema": "pdf2md_hk", "parseRunId": "parse-hk"}
    assert seen["args"] == [sys.executable, str(market_script), str(document_full), "--market", "HK", "--ddl"]
    assert str(a_share_script) not in seen["args"]
    assert seen["timeout"] == 900
    assert seen["env"]["SIQ_HK_PGDATABASE"] == "siq_hk"
    assert "DATABASE_URL" not in seen["env"]


def test_pdf_market_workflow_status_uses_document_full_postgres_schema(monkeypatch, tmp_path):
    task_id = "task-hk-status"
    document_full = tmp_path / "results" / task_id / "document_full.json"
    document_full.parent.mkdir(parents=True)
    document_full.write_text('{"metadata":{"market":"HK"}}', encoding="utf-8")

    class FakeCursor:
        def __init__(self):
            self.row = None

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def execute(self, sql, params=None):
            text = " ".join(str(sql).split())
            if "information_schema.tables" in text:
                self.row = (1,)
            elif text.startswith("select parse_run_id, filing_id"):
                self.row = ("parse-hk-1", "filing-hk-1")
            elif text.startswith("select count(*)"):
                table = text.split(" from ", 1)[1].split(" where ", 1)[0]
                counts = {
                    "pdf2md_hk.parse_runs": 1,
                    "pdf2md_hk.financial_statement_items": 2,
                    "pdf2md_hk.financial_facts": 0,
                    "pdf2md_hk.xbrl_facts_raw": 0,
                    "pdf2md_hk.document_tables": 1,
                    "pdf2md_hk.html_tables": 0,
                    "pdf2md_hk.pdf_tables": 0,
                    "pdf2md_hk.document_chunks": 3,
                    "pdf2md_hk.retrieval_chunks": 0,
                    "pdf2md_hk.evidence_citations": 2,
                }
                self.row = (counts.get(table, 0),)
            else:
                raise AssertionError(f"unexpected SQL: {sql!r} params={params!r}")

        def fetchone(self):
            return self.row

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def cursor(self):
            return FakeCursor()

    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=lambda _url: FakeConn()))
    monkeypatch.setattr(workflow, "_find_task_document_full", lambda task: document_full)
    monkeypatch.setattr(workflow, "_find_task_result_dir", lambda task: document_full.parent)
    monkeypatch.setattr(workflow, "_infer_task_market", lambda task: "HK")
    monkeypatch.setattr(workflow, "_artifact_bundle_status", lambda task: {"status": "ready", "ready": True, "message": "ok"})
    monkeypatch.setattr(workflow, "_wiki_import_status_at_root", lambda task, root, market: {"status": "ready", "companyDir": "00700-Tencent", "message": "ok"})
    monkeypatch.setattr(workflow, "_semantic_status_at_root", lambda company, task, root: {"status": "ready", "counts": {"facts": 1, "evidence": 1}})
    monkeypatch.setattr(workflow, "_obsidian_status_at_root", lambda company, semantic, root: {"status": "ready"})
    monkeypatch.setitem(workflow.MARKET_DATABASES, "HK", "siq_hk")

    status = workflow._workflow_status_payload(task_id)

    assert status["database"]["status"] == "ready"
    assert status["database"]["marketStatus"] == "postgres_ready"
    assert status["database"]["schema"] == "pdf2md_hk"
    assert status["database"]["facts"] == 2
    assert status["database"]["chunks"] == 3


def test_pdf_market_workflow_status_requires_evidence_for_ready(monkeypatch, tmp_path):
    task_id = "task-eu-status"
    document_full = tmp_path / "results" / task_id / "document_full.json"
    document_full.parent.mkdir(parents=True)
    document_full.write_text('{"metadata":{"market":"EU"}}', encoding="utf-8")

    class FakeCursor:
        def __init__(self):
            self.row = None

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def execute(self, sql, params=None):
            text = " ".join(str(sql).split())
            if "information_schema.tables" in text:
                self.row = (1,)
            elif text.startswith("select parse_run_id, filing_id"):
                self.row = ("parse-eu-1", "filing-eu-1")
            elif text.startswith("select count(*)"):
                table = text.split(" from ", 1)[1].split(" where ", 1)[0]
                counts = {
                    "eu_ifrs.parse_runs": 1,
                    "eu_ifrs.financial_statement_items": 2,
                    "eu_ifrs.document_tables": 1,
                    "eu_ifrs.document_chunks": 3,
                    "eu_ifrs.evidence_citations": 0,
                }
                self.row = (counts.get(table, 0),)
            else:
                raise AssertionError(f"unexpected SQL: {sql!r} params={params!r}")

        def fetchone(self):
            return self.row

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def cursor(self):
            return FakeCursor()

    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=lambda _url: FakeConn()))
    monkeypatch.setattr(workflow, "_find_task_document_full", lambda task: document_full)
    monkeypatch.setattr(workflow, "_find_task_result_dir", lambda task: document_full.parent)
    monkeypatch.setattr(workflow, "_infer_task_market", lambda task: "EU")
    monkeypatch.setattr(workflow, "_artifact_bundle_status", lambda task: {"status": "ready", "ready": True, "message": "ok"})
    monkeypatch.setattr(workflow, "_wiki_import_status_at_root", lambda task, root, market: {"status": "ready", "companyDir": "SAP", "message": "ok"})
    monkeypatch.setattr(workflow, "_semantic_status_at_root", lambda company, task, root: {"status": "ready"})
    monkeypatch.setattr(workflow, "_obsidian_status_at_root", lambda company, semantic, root: {"status": "ready"})
    monkeypatch.setitem(workflow.MARKET_DATABASES, "EU", "siq_eu")

    status = workflow._workflow_status_payload(task_id)

    assert status["database"]["status"] == "partial"
    assert status["database"]["marketStatus"] == "warning"
    assert status["database"]["evidence"] == 0
    assert status["database"]["missingCounts"] == ["evidence"]


def test_semantic_status_rejects_pdf_market_placeholder_files(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "jp"
    company_dir = "7267-Honda-Motor"
    company_root = wiki_root / "companies" / company_dir
    report_dir = company_root / "reports" / "2025-annual"
    semantic_dir = company_root / "semantic"
    report_dir.mkdir(parents=True)
    semantic_dir.mkdir(parents=True)
    write_json(company_root / "company.json", {"market": "JP", "primary_report_id": "2025-annual"})
    (report_dir / "report.md").write_text("# 事業の内容\n", encoding="utf-8")
    write_json(report_dir / "report.json", {"report_id": "2025-annual"})
    write_json(report_dir / "document_full.json", {"schema_version": "pdf_document_full_v1"})
    for name in (
        "subject_profile.json",
        "segments.json",
        "facts.json",
        "relations.json",
        "claims.json",
        "retrieval_index.json",
        "note_links.json",
        "evidence_semantic.json",
    ):
        write_json(semantic_dir / name, {"schema_version": "placeholder"})
    write_json(semantic_dir / "extraction_log.json", {"schema_version": "jp_semantic_extraction_log_v1", "steps": []})
    monkeypatch.setattr(workflow, "LLM_SEMANTIC_ENABLED", True)
    monkeypatch.setattr(workflow, "LLM_SEMANTIC_REQUIRED", False)

    status = workflow._semantic_status_at_root(company_dir, None, wiki_root)

    assert status["status"] == "missing"
    assert status["missing"] == []
    assert "占位" in status["message"]


def test_semantic_status_accepts_market_rule_log_with_inputs(monkeypatch, tmp_path):
    wiki_root = tmp_path / "wiki" / "eu"
    company_dir = "AI-Air-Liquide"
    company_root = wiki_root / "companies" / company_dir
    report_dir = company_root / "reports" / "2025-annual"
    semantic_dir = company_root / "semantic"
    report_dir.mkdir(parents=True)
    semantic_dir.mkdir(parents=True)
    write_json(company_root / "company.json", {"market": "EU", "primary_report_id": "2025-annual"})
    (report_dir / "report.md").write_text("# Strategic report\n", encoding="utf-8")
    write_json(report_dir / "report.json", {"report_id": "2025-annual"})
    write_json(report_dir / "document_full.json", {"schema_version": "pdf_document_full_v1"})
    for name in (
        "subject_profile.json",
        "segments.json",
        "facts.json",
        "relations.json",
        "claims.json",
        "retrieval_index.json",
        "note_links.json",
        "evidence_semantic.json",
    ):
        write_json(semantic_dir / name, {"schema_version": "real"})
    inputs = {
        "company_json_sha256": workflow._sha256_file(company_root / "company.json"),
        "report_md_sha256": workflow._sha256_file(report_dir / "report.md"),
        "report_json_sha256": workflow._sha256_file(report_dir / "report.json"),
        "document_full_sha256": workflow._sha256_file(report_dir / "document_full.json"),
    }
    write_json(
        semantic_dir / "extraction_log.json",
        {
            "inputs": inputs,
            "counts": {"segments": 4, "facts": 0, "relations": 0, "claims": 1, "evidence": 6},
            "quality": {"facts_with_evidence_ratio": 1.0},
        },
    )
    monkeypatch.setattr(workflow, "LLM_SEMANTIC_ENABLED", True)
    monkeypatch.setattr(workflow, "LLM_SEMANTIC_REQUIRED", False)

    status = workflow._semantic_status_at_root(company_dir, None, wiki_root)

    assert status["status"] == "ready"
    assert status["counts"]["segments"] == 4
    assert status["counts"]["evidence"] == 6


def test_task_db_import_routes_us_sec_alias_to_document_full_importer(monkeypatch, tmp_path):
    document_full = tmp_path / "us-sec-task" / "document_full.json"
    document_full.parent.mkdir(parents=True)
    document_full.write_text('{"metadata":{"market":"US_SEC"}}', encoding="utf-8")
    market_script = tmp_path / "imports" / "import_us_sec_document_full_to_postgres.py"
    market_script.parent.mkdir(parents=True)
    market_script.write_text("print('ok')\n", encoding="utf-8")
    seen = {}

    def fake_run_command(args, timeout=300, env=None):
        seen["args"] = args
        seen["timeout"] = timeout
        seen["env"] = env
        return {"returnCode": 0, "stdout": "us ok", "stderr": ""}

    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:secret@db/siq")
    monkeypatch.setattr(workflow, "_find_task_document_full", lambda task_id: document_full)
    monkeypatch.setattr(workflow, "_infer_task_market", lambda task_id: "US_SEC")
    monkeypatch.setitem(workflow.MARKET_DOCUMENT_FULL_IMPORT_SCRIPTS, "US", market_script)
    monkeypatch.setitem(workflow.MARKET_DATABASES, "US", "siq_us")
    monkeypatch.setattr(workflow, "_run_command", fake_run_command)
    monkeypatch.setattr(
        workflow,
        "_market_document_full_db_status",
        lambda task_id, market, document_full: {"status": "ready", "market": market, "schema": "sec_us", "parseRunId": "parse-us"},
    )

    result = workflow.import_task_to_database("task-us-db")

    assert result["ok"] is True
    assert result["market"] == "US"
    assert result["database"] == {"status": "ready", "market": "US", "schema": "sec_us", "parseRunId": "parse-us"}
    assert seen["args"] == [sys.executable, str(market_script), str(document_full), "--market", "US", "--ddl"]
    assert seen["timeout"] == 900
    assert seen["env"]["SIQ_US_PGDATABASE"] == "siq_us"
    assert "DATABASE_URL" not in seen["env"]


def test_task_db_import_rejects_missing_script_without_running_command(monkeypatch, tmp_path):
    document_full = tmp_path / "document_full.json"
    document_full.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(workflow, "_find_task_document_full", lambda task_id: document_full)
    monkeypatch.setattr(workflow, "DB_IMPORT_SCRIPT", tmp_path / "missing_db_import.py")

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("DB import must not run without its script")

    monkeypatch.setattr(workflow, "_run_command", fail_if_called)

    with pytest.raises(workflow.HTTPException) as exc:
        workflow.import_task_to_database("task-db-missing-script")

    assert exc.value.status_code == 500
    assert "DB import script not found" in exc.value.detail


def test_document_semantic_endpoint_builds_chunks(monkeypatch, tmp_path):
    task_id = "task-doc-004"
    results_root = tmp_path / "results"
    wiki_root = tmp_path / "wiki" / "documents"
    script = tmp_path / "ingest_document_chunks.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    build_result_dir(results_root, task_id)
    monkeypatch.setattr(workflow, "DOCUMENT_PARSER_RESULTS_ROOT", results_root)
    monkeypatch.setattr(workflow, "DOCUMENT_WIKI_ROOT", wiki_root)
    monkeypatch.setattr(workflow, "DOCUMENT_CHUNK_SCRIPT", script)
    seen = {}

    def fake_run_command(args, timeout=300, env=None):
        seen["args"] = args
        seen["timeout"] = timeout
        output = Path(args[args.index("--output") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text('{"chunk_uid":"c1"}\n', encoding="utf-8")
        return {"returnCode": 0, "stdout": '{"ok":true}', "stderr": ""}

    monkeypatch.setattr(workflow, "_run_command", fake_run_command)
    workflow._import_document_task_to_wiki(task_id, "contracts")

    result = workflow.build_document_semantic_chunks(task_id, "contracts")

    assert result["ok"] is True
    assert str(script) in seen["args"]
    assert seen["timeout"] == 300
    assert "--milvus" not in seen["args"]
    assert result["milvus"]["status"] == "chunks_ready"
    assert result["milvus"]["chunkCount"] == 1


def test_document_semantic_maps_command_failure_to_500(monkeypatch, tmp_path):
    task_id = "task-doc-semantic-failure"
    results_root = tmp_path / "results"
    wiki_root = tmp_path / "wiki" / "documents"
    script = tmp_path / "ingest_document_chunks.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    build_result_dir(results_root, task_id)
    monkeypatch.setattr(workflow, "DOCUMENT_PARSER_RESULTS_ROOT", results_root)
    monkeypatch.setattr(workflow, "DOCUMENT_WIKI_ROOT", wiki_root)
    monkeypatch.setattr(workflow, "DOCUMENT_CHUNK_SCRIPT", script)
    failure = {"returnCode": 2, "stdout": "", "stderr": "chunk failure"}
    monkeypatch.setattr(workflow, "_run_command", lambda *args, **kwargs: failure)
    workflow._import_document_task_to_wiki(task_id, "contracts")

    with pytest.raises(workflow.HTTPException) as exc:
        workflow.build_document_semantic_chunks(task_id, "contracts")

    assert exc.value.status_code == 500
    assert exc.value.detail == failure


def test_document_semantic_rejects_missing_wiki_without_running_command(monkeypatch, tmp_path):
    script = tmp_path / "ingest_document_chunks.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(workflow, "DOCUMENT_CHUNK_SCRIPT", script)
    monkeypatch.setattr(
        workflow,
        "_document_wiki_status",
        lambda task_id, collection: {"status": "missing"},
    )

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("semantic command must not run without a ready Wiki package")

    monkeypatch.setattr(workflow, "_run_command", fail_if_called)

    with pytest.raises(workflow.HTTPException) as exc:
        workflow.build_document_semantic_chunks("task-doc-missing-wiki", "contracts")

    assert exc.value.status_code == 422


def test_document_semantic_rejects_missing_script_without_running_command(monkeypatch, tmp_path):
    package_dir = tmp_path / "wiki" / "documents" / "contracts" / "doc"
    package_dir.mkdir(parents=True)
    monkeypatch.setattr(workflow, "DOCUMENT_CHUNK_SCRIPT", tmp_path / "missing_chunks.py")
    monkeypatch.setattr(
        workflow,
        "_document_wiki_status",
        lambda task_id, collection: {"status": "ready", "path": str(package_dir)},
    )

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("semantic command must not run without its script")

    monkeypatch.setattr(workflow, "_run_command", fail_if_called)

    with pytest.raises(workflow.HTTPException) as exc:
        workflow.build_document_semantic_chunks("task-doc-missing-script", "contracts")

    assert exc.value.status_code == 500
    assert "Document chunk script not found" in exc.value.detail


def test_document_semantic_endpoint_can_request_milvus_ingest(monkeypatch, tmp_path):
    task_id = "task-doc-005"
    results_root = tmp_path / "results"
    wiki_root = tmp_path / "wiki" / "documents"
    script = tmp_path / "ingest_document_chunks.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    build_result_dir(results_root, task_id)
    monkeypatch.setattr(workflow, "DOCUMENT_PARSER_RESULTS_ROOT", results_root)
    monkeypatch.setattr(workflow, "DOCUMENT_WIKI_ROOT", wiki_root)
    monkeypatch.setattr(workflow, "DOCUMENT_CHUNK_SCRIPT", script)
    seen = {}

    def fake_run_command(args, timeout=300, env=None):
        seen["args"] = args
        seen["timeout"] = timeout
        output = Path(args[args.index("--output") + 1])
        report = Path(args[args.index("--report") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text('{"chunk_uid":"c1"}\n', encoding="utf-8")
        report.write_text(json.dumps({"chunk_count": 1, "milvus_inserted": 1, "collection": "siq_documents"}), encoding="utf-8")
        return {"returnCode": 0, "stdout": '{"ok":true}', "stderr": ""}

    monkeypatch.setattr(workflow, "_run_command", fake_run_command)
    workflow._import_document_task_to_wiki(task_id, "contracts")

    result = workflow.build_document_semantic_chunks(task_id, "contracts", milvus=True)

    assert result["ok"] is True
    assert "--milvus" in seen["args"]
    assert seen["timeout"] == 1800
    assert result["semanticMode"] == "milvus"
    assert result["milvus"]["status"] == "completed"
    assert result["milvus"]["insertedCount"] == 1
