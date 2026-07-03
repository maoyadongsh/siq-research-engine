import importlib.util
import json
from pathlib import Path


def _load_module(name: str, relative: str):
    source = Path(__file__).resolve().parents[1] / "services" / relative
    spec = importlib.util.spec_from_file_location(name, source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


service = _load_module("temp_document_workflow_service", "document_workflow_service.py")


def test_document_artifact_status_reports_missing_and_ready(tmp_path):
    core = ["manifest.json", "document.md"]
    result_dir = tmp_path / "results" / "task-1"
    result_dir.mkdir(parents=True)
    (result_dir / "manifest.json").write_text("{}", encoding="utf-8")
    (result_dir / "document.md").write_text("# Demo", encoding="utf-8")

    missing = service.document_artifact_status(
        "task-missing",
        safe_task_id=lambda value: value,
        find_result_dir=lambda task_id: None,
        core_artifacts=core,
        artifact_file_info=lambda path, name: {"exists": False},
    )
    assert missing["status"] == "missing"
    assert missing["missing"] == core
    assert missing["ready"] is False

    ready = service.document_artifact_status(
        "task-1",
        safe_task_id=lambda value: value,
        find_result_dir=lambda task_id: result_dir,
        core_artifacts=core,
        artifact_file_info=lambda path, name: {"exists": (path / name).is_file(), "name": name},
    )
    assert ready["status"] == "ready"
    assert ready["readyCount"] == 2
    assert ready["total"] == 2
    assert ready["missing"] == []
    assert set(ready["artifacts"]) == set(core)


def test_document_wiki_status_detects_missing_ready_and_stale(tmp_path):
    result_dir = tmp_path / "results" / "task-1"
    result_dir.mkdir(parents=True)
    (result_dir / "manifest.json").write_text("{}", encoding="utf-8")
    (result_dir / "document_full.json").write_text("source", encoding="utf-8")
    wiki_root = tmp_path / "wiki"

    def read_json(path, default):
        if not path.is_file():
            return default
        return {"document_full_sha256": "package-sha"} if path.name == "manifest.json" and "wiki" in path.parts else {}

    base = {
        "safe_task_id": lambda value: value,
        "safe_collection": lambda value: value or "default",
        "find_result_dir": lambda task_id: result_dir,
        "read_json": read_json,
        "sha256_file": lambda path: "source-sha",
        "wiki_root": wiki_root,
        "package_manifest_name": "manifest.json",
        "document_key_from_manifest": lambda task_id, manifest: f"doc-{task_id}",
    }

    missing = service.document_wiki_status("task-1", "contracts", **base)
    assert missing["status"] == "missing"
    assert missing["collection"] == "contracts"
    assert missing["documentKey"] == "doc-task-1"

    package_dir = wiki_root / "contracts" / "doc-task-1"
    package_dir.mkdir(parents=True)
    (package_dir / "manifest.json").write_text("{}", encoding="utf-8")
    stale = service.document_wiki_status("task-1", "contracts", **base)
    assert stale["status"] == "stale"
    assert stale["stale"] is True
    assert stale["documentFullSha256"] == "package-sha"
    assert stale["sourceDocumentFullSha256"] == "source-sha"

    current = service.document_wiki_status(
        "task-1",
        "contracts",
        **{**base, "sha256_file": lambda path: "package-sha"},
    )
    assert current["status"] == "ready"
    assert current["stale"] is False


def test_document_workflow_status_payload_keeps_target_shape():
    payload = service.document_workflow_status_payload(
        "task-1",
        "contracts",
        safe_task_id=lambda value: value,
        artifact_status=lambda task_id: {"status": "ready", "taskId": task_id},
        wiki_status=lambda task_id, collection: {"status": "ready", "collection": collection},
        postgres_status=lambda task_id, collection: {"status": "missing"},
        milvus_status=lambda task_id, collection: {"status": "ready"},
    )

    assert set(payload) == {"taskId", "targets", "artifacts"}
    assert set(payload["targets"]) == {"wiki", "postgres", "milvus", "full_text", "object_storage"}
    assert payload["targets"]["full_text"] == {"status": "disabled"}
    assert payload["targets"]["object_storage"] == {"status": "disabled"}


def test_document_package_target_and_manifest_artifact():
    assert service.document_package_target("document.md") == "sections/document.md"
    assert service.document_package_target("extraction/result.json") == "extraction/result.json"
    assert service.document_package_target("custom.bin") == "custom.bin"


def test_document_package_manifest_artifact_keeps_lightweight_and_heavy_contract(tmp_path):
    result_dir = tmp_path / "results" / "task-doc-1"
    result_dir.mkdir(parents=True)
    (result_dir / "document.md").write_text("# Demo", encoding="utf-8")
    (result_dir / "document_full.json").write_text('{"schemaVersion":"document_full_v1"}', encoding="utf-8")

    lightweight = service.document_package_manifest_artifact(
        result_dir=result_dir,
        artifact_name="document.md",
        lightweight_artifacts={"document.md"},
        sha256_file=lambda path: f"sha:{path.name}",
        json_artifact_meta=lambda path: {"schemaVersion": "markdown_v1"},
    )
    heavy = service.document_package_manifest_artifact(
        result_dir=result_dir,
        artifact_name="document_full.json",
        lightweight_artifacts={"document.md"},
        sha256_file=lambda path: f"sha:{path.name}",
        json_artifact_meta=lambda path: {"schemaVersion": "document_full_v1"},
    )

    assert lightweight == {
        "source": str(result_dir / "document.md"),
        "package_path": "sections/document.md",
        "sha256": "sha:document.md",
        "size_bytes": len("# Demo"),
        "version": "markdown_v1",
    }
    assert heavy == {
        "source": str(result_dir / "document_full.json"),
        "package_path": "",
        "sha256": "sha:document_full.json",
        "size_bytes": len('{"schemaVersion":"document_full_v1"}'),
        "version": "document_full_v1",
    }


def test_document_package_readme_content_limits_preview():
    package_manifest = {
        "filename": "Contract Demo.pdf",
        "task_id": "task-doc-1",
        "document_kind": "pdf",
        "parser_provider": "pypdf",
        "source_result_dir": "/tmp/results/task-doc-1",
    }
    markdown = "\n".join(f"line-{index}" for index in range(100))

    content = service.document_package_readme_content(package_manifest, markdown)

    assert content.startswith("# Contract Demo.pdf\n")
    assert "- task_id: `task-doc-1`" in content
    assert "line-0" in content
    assert "line-79" in content
    assert "line-80" not in content


def test_document_package_readme_content_falls_back_to_document_key_and_empty_preview():
    content = service.document_package_readme_content(
        {"document_key": "fallback-key", "task_id": "task-doc-2"},
        "",
    )

    assert content.startswith("# fallback-key\n")
    assert "_No markdown preview available._" in content


def test_build_document_package_manifests_keeps_existing_contract(tmp_path):
    result_dir = tmp_path / "results" / "task-doc-1"
    result_dir.mkdir(parents=True)
    (result_dir / "document.md").write_text("# Demo", encoding="utf-8")
    (result_dir / "document_full.json").write_text('{"schemaVersion":"document_full_v1"}', encoding="utf-8")
    (result_dir / "blocks.json").write_text("{}", encoding="utf-8")
    manifest = {
        "filename": "Contract Demo.pdf",
        "document_kind": "pdf",
        "parser_provider": "pypdf",
    }
    core_artifacts = ["document.md", "document_full.json"]
    optional_artifacts = ["blocks.json", "missing.json"]
    lightweight = {"document.md"}
    copied_files = ["document.md"]
    copied_directories = {"raw/original": 1}

    package_manifest, artifact_manifest = service.build_document_package_manifests(
        task_id="task-doc-1",
        collection_name="contracts",
        document_key="contract-demo",
        result_dir=result_dir,
        parser_manifest=manifest,
        created_at="2026-07-03T10:00:00Z",
        document_full_sha="sha-full",
        core_artifacts=core_artifacts,
        optional_artifacts=optional_artifacts,
        lightweight_artifacts=lightweight,
        copied_files=copied_files,
        retained_dirs=["raw/original", "images/original"],
        package_manifest_name="manifest.json",
        artifact_manifest_name="artifact_manifest.json",
        sha256_file=lambda path: f"sha:{path.name}",
        json_artifact_meta=lambda path: json.loads(path.read_text(encoding="utf-8")) if path.name == "document_full.json" else {},
    )

    assert package_manifest["schema_version"] == "generic_document_package_v1"
    assert package_manifest["document_id"] == "doc-task-doc-1"
    assert package_manifest["task_id"] == "task-doc-1"
    assert package_manifest["collection"] == "contracts"
    assert package_manifest["document_key"] == "contract-demo"
    assert package_manifest["filename"] == "Contract Demo.pdf"
    assert package_manifest["document_kind"] == "pdf"
    assert package_manifest["parser_provider"] == "pypdf"
    assert package_manifest["created_at"] == "2026-07-03T10:00:00Z"
    assert package_manifest["document_full_sha256"] == "sha-full"
    assert package_manifest["wiki_keeps"] == [
        "README.md",
        "manifest.json",
        "artifact_manifest.json",
        "sections/document.md",
        "raw/original",
        "images/original",
    ]
    assert package_manifest["artifacts"]["document.md"]["package_path"] == "sections/document.md"
    assert package_manifest["artifacts"]["document_full.json"]["package_path"] == ""
    assert package_manifest["artifacts"]["document_full.json"]["version"] == "document_full_v1"
    assert "missing.json" not in package_manifest["artifacts"]

    assert artifact_manifest == {
        "schema_version": "generic_document_artifact_manifest_v1",
        "task_id": "task-doc-1",
        "collection": "contracts",
        "document_key": "contract-demo",
        "source_result_dir": str(result_dir),
        "generated_at": "2026-07-03T10:00:00Z",
        "artifacts": package_manifest["artifacts"],
    }


def test_build_document_collection_index_replaces_task_and_sorts():
    existing = {
        "documents": [
            {"task_id": "old", "updated_at": "2026-07-01T00:00:00Z"},
            {"task_id": "task-doc-1", "updated_at": "2026-07-02T00:00:00Z"},
        ]
    }
    package_manifest = {
        "document_id": "doc-task-doc-1",
        "document_key": "contract-demo",
        "filename": "Contract Demo.pdf",
        "document_kind": "pdf",
        "created_at": "2026-07-03T00:00:00Z",
    }

    payload = service.build_document_collection_index(
        existing,
        task_id="task-doc-1",
        collection_name="contracts",
        package_dir=Path("/wiki/contracts/contract-demo"),
        package_manifest=package_manifest,
        generated_at="2026-07-04T00:00:00Z",
    )

    assert payload["schema_version"] == "generic_document_collection_index_v1"
    assert payload["collection"] == "contracts"
    assert payload["generated_at"] == "2026-07-04T00:00:00Z"
    assert payload["document_count"] == 2
    assert [item["task_id"] for item in payload["documents"]] == ["task-doc-1", "old"]
    assert payload["documents"][0]["path"] == "/wiki/contracts/contract-demo"


def test_document_postgres_status_payload_missing_waiting_and_ready():
    missing = service.document_postgres_status_payload(
        task_id="task-doc-1",
        wiki_status={"status": "ready", "path": "/wiki/doc"},
        script_path=Path("/missing/import.py"),
        script_exists=False,
    )
    assert missing == {
        "status": "missing",
        "schema": "document_parser",
        "script": "/missing/import.py",
        "message": "通用文档 PostgreSQL importer 不存在",
    }

    waiting = service.document_postgres_status_payload(
        task_id="task-doc-1",
        wiki_status={"status": "missing"},
        script_path=Path("/scripts/import.py"),
        script_exists=True,
    )
    assert waiting == {
        "status": "waiting_for_wiki",
        "schema": "document_parser",
        "script": "/scripts/import.py",
        "message": "请先导入 Wiki 包",
    }

    ready = service.document_postgres_status_payload(
        task_id="task-doc-1",
        wiki_status={"status": "stale", "path": "/wiki/doc"},
        script_path=Path("/scripts/import.py"),
        script_exists=True,
    )
    assert ready == {
        "status": "ready",
        "schema": "document_parser",
        "script": "/scripts/import.py",
        "packagePath": "/wiki/doc",
        "document_id": "doc-task-doc-1",
        "message": "可导入 PostgreSQL document_parser schema",
    }


def test_document_milvus_status_payload_missing_waiting_ready_chunks_and_completed():
    missing = service.document_milvus_status_payload(
        wiki_status={"status": "ready", "path": "/wiki/doc"},
        script_path=Path("/missing/chunks.py"),
        script_exists=False,
    )
    assert missing == {
        "status": "missing",
        "collection": "siq_documents",
        "script": "/missing/chunks.py",
        "message": "通用文档 chunk 脚本不存在",
    }

    waiting = service.document_milvus_status_payload(
        wiki_status={"status": "missing"},
        script_path=Path("/scripts/chunks.py"),
        script_exists=True,
    )
    assert waiting == {
        "status": "waiting_for_wiki",
        "collection": "siq_documents",
        "script": "/scripts/chunks.py",
        "message": "请先导入 Wiki 包",
    }

    ready = service.document_milvus_status_payload(
        wiki_status={"status": "ready", "path": "/wiki/doc"},
        script_path=Path("/scripts/chunks.py"),
        script_exists=True,
        chunks_exists=False,
    )
    assert ready == {
        "status": "ready",
        "collection": "siq_documents",
        "script": "/scripts/chunks.py",
        "packagePath": "/wiki/doc",
        "chunksPath": "/wiki/doc/semantic/chunks.jsonl",
        "chunkCount": 0,
        "message": "可生成语义 chunks",
    }

    chunks_ready = service.document_milvus_status_payload(
        wiki_status={"status": "ready", "path": "/wiki/doc"},
        script_path=Path("/scripts/chunks.py"),
        script_exists=True,
        chunks_exists=True,
        chunk_count=3,
    )
    assert chunks_ready == {
        "status": "chunks_ready",
        "collection": "siq_documents",
        "script": "/scripts/chunks.py",
        "packagePath": "/wiki/doc",
        "chunksPath": "/wiki/doc/semantic/chunks.jsonl",
        "reportPath": "",
        "chunkCount": 3,
        "message": "已生成 3 个语义 chunks",
    }

    completed = service.document_milvus_status_payload(
        wiki_status={"status": "ready", "path": "/wiki/doc"},
        script_path=Path("/scripts/chunks.py"),
        script_exists=True,
        chunks_exists=True,
        chunk_count=3,
        report_path=Path("/wiki/doc/semantic/ingest_report.json"),
        report={"milvus_inserted": 2, "chunk_count": 3, "collection": "custom_docs", "batch_tag": "batch-1"},
    )
    assert completed == {
        "status": "completed",
        "collection": "custom_docs",
        "script": "/scripts/chunks.py",
        "packagePath": "/wiki/doc",
        "chunksPath": "/wiki/doc/semantic/chunks.jsonl",
        "reportPath": "/wiki/doc/semantic/ingest_report.json",
        "chunkCount": 3,
        "insertedCount": 2,
        "batchTag": "batch-1",
        "message": "已写入 Milvus 2 个 chunks",
    }


def test_document_db_import_command_and_env_contract():
    pg_config = {
        "host": "db.local",
        "port": 5432,
        "dbname": "siq",
        "user": "doc_user",
        "password": "secret",
    }
    database_url = "postgresql://doc_user:secret@db.local:5432/siq"

    args = service.document_db_import_command(
        executable="/usr/bin/python",
        script_path=Path("/repo/scripts/import_document.py"),
        package_dir=Path("/wiki/documents/contracts/doc-task-1"),
        database_url=database_url,
    )
    env = service.document_db_import_env(
        {"KEEP": "1", "PGHOST": "old"},
        pg_config=pg_config,
        database_url=database_url,
    )

    assert args == [
        "/usr/bin/python",
        "/repo/scripts/import_document.py",
        "/wiki/documents/contracts/doc-task-1",
        "--database-url",
        database_url,
    ]
    assert env["KEEP"] == "1"
    assert env["PGHOST"] == "db.local"
    assert env["PGPORT"] == "5432"
    assert env["PGDATABASE"] == "siq"
    assert env["PGUSER"] == "doc_user"
    assert env["PGPASSWORD"] == "secret"
    assert env["DATABASE_URL"] == database_url


def test_document_semantic_command_contract_for_chunks_and_milvus():
    package_dir = Path("/wiki/documents/contracts/doc-task-1")
    chunks = service.document_semantic_command(
        executable="/usr/bin/python",
        script_path=Path("/repo/scripts/ingest_document_chunks.py"),
        package_dir=package_dir,
    )
    milvus = service.document_semantic_command(
        executable="/usr/bin/python",
        script_path=Path("/repo/scripts/ingest_document_chunks.py"),
        package_dir=package_dir,
        milvus=True,
    )

    assert chunks["args"] == [
        "/usr/bin/python",
        "/repo/scripts/ingest_document_chunks.py",
        "/wiki/documents/contracts/doc-task-1",
        "--collection",
        "siq_documents",
        "--output",
        "/wiki/documents/contracts/doc-task-1/semantic/chunks.jsonl",
        "--report",
        "/wiki/documents/contracts/doc-task-1/semantic/ingest_report.json",
    ]
    assert chunks["output"] == package_dir / "semantic" / "chunks.jsonl"
    assert chunks["report"] == package_dir / "semantic" / "ingest_report.json"
    assert chunks["timeout"] == 300
    assert chunks["semantic_mode"] == "chunks"

    assert milvus["args"] == [*chunks["args"], "--milvus"]
    assert milvus["timeout"] == 1800
    assert milvus["semantic_mode"] == "milvus"
