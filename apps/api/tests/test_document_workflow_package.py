from __future__ import annotations

import importlib.util
import json
import sys
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
    assert seen["args"] == [sys.executable, str(script), str(document_full), "--config-py", str(config_py)]
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
    monkeypatch.setattr(workflow, "_db_connect_config", lambda: {"host": "h", "port": 5432, "dbname": "d", "user": "u", "password": "p"})
    monkeypatch.setattr(workflow, "_run_command", fake_run_command)
    monkeypatch.setattr(workflow, "_db_status", lambda task_id: {"status": "ready"})

    workflow.import_task_to_database("task-db-env")

    assert seen["args"] == [
        sys.executable,
        str(script),
        str(document_full),
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
