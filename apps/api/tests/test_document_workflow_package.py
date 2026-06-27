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
        seen["env"] = env
        return {"returnCode": 0, "stdout": '{"ok":true}', "stderr": ""}

    monkeypatch.setattr(workflow, "_run_command", fake_run_command)
    workflow._import_document_task_to_wiki(task_id, "contracts")

    result = workflow.import_document_task_to_database(task_id, "contracts")

    assert result["ok"] is True
    assert str(script) in seen["args"]
    assert result["postgres"]["status"] == "ready"


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
        output = Path(args[args.index("--output") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text('{"chunk_uid":"c1"}\n', encoding="utf-8")
        return {"returnCode": 0, "stdout": '{"ok":true}', "stderr": ""}

    monkeypatch.setattr(workflow, "_run_command", fake_run_command)
    workflow._import_document_task_to_wiki(task_id, "contracts")

    result = workflow.build_document_semantic_chunks(task_id, "contracts")

    assert result["ok"] is True
    assert str(script) in seen["args"]
    assert "--milvus" not in seen["args"]
    assert result["milvus"]["status"] == "chunks_ready"
    assert result["milvus"]["chunkCount"] == 1


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
    assert result["semanticMode"] == "milvus"
    assert result["milvus"]["status"] == "completed"
    assert result["milvus"]["insertedCount"] == 1
