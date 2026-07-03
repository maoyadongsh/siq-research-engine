from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


ArtifactInfoFactory = Callable[[Path, str], dict[str, Any]]
SafeTaskId = Callable[[str], str]
SafeCollection = Callable[[str | None], str]
ResultDirFinder = Callable[[str], Path | None]
ReadJson = Callable[[Path, Any], Any]
Sha256File = Callable[[Path], str | None]
DocumentKeyFactory = Callable[[str, dict[str, Any]], str]


def document_package_target(artifact_name: str) -> str:
    mapping = {
        "manifest.json": "qa/parse_manifest.json",
        "document.md": "sections/document.md",
        "blocks.json": "sections/blocks.json",
        "tables.json": "tables/tables.json",
        "logical_tables.json": "logical_tables/logical_tables.json",
        "table_relations.json": "logical_tables/table_relations.json",
        "table_merge_corrections.json": "logical_tables/table_merge_corrections.json",
        "figures.json": "figures/figures.json",
        "figure_index.json": "figures/figure_index.json",
        "comparison_map.json": "comparison/comparison_map.json",
        "layout_blocks.json": "comparison/layout_blocks.json",
        "reading_order.json": "comparison/reading_order.json",
        "source_map.json": "qa/source_map.json",
        "quality_report.json": "qa/quality_report.json",
        "extraction/schema.json": "extraction/schema.json",
        "extraction/result.json": "extraction/result.json",
        "extraction/evidence_map.json": "extraction/evidence_map.json",
        "extraction/validation_report.json": "extraction/validation_report.json",
    }
    return mapping.get(artifact_name, artifact_name)


def document_artifact_status(
    task_id: str,
    *,
    safe_task_id: SafeTaskId,
    find_result_dir: ResultDirFinder,
    core_artifacts: Iterable[str],
    artifact_file_info: ArtifactInfoFactory,
) -> dict[str, Any]:
    task_id = safe_task_id(task_id)
    core = list(core_artifacts)
    result_dir = find_result_dir(task_id)
    if not result_dir:
        return {
            "status": "missing",
            "taskId": task_id,
            "ready": False,
            "resultDir": "",
            "readyCount": 0,
            "total": len(core),
            "missing": list(core),
            "message": "未找到通用文档解析产物目录",
        }
    artifacts = {}
    missing = []
    for name in core:
        info = artifact_file_info(result_dir, name)
        artifacts[name] = info
        if not info["exists"]:
            missing.append(name)
    ready = not missing
    return {
        "status": "ready" if ready else "missing",
        "taskId": task_id,
        "ready": ready,
        "resultDir": str(result_dir),
        "readyCount": len(core) - len(missing),
        "total": len(core),
        "missing": missing,
        "artifacts": artifacts,
        "message": f"{len(core) - len(missing)}/{len(core)} 个核心文件已生成" if ready else f"缺少 {len(missing)} 个核心文件",
    }


def document_package_dir(
    task_id: str,
    collection: str | None,
    manifest: dict[str, Any] | None,
    *,
    safe_task_id: SafeTaskId,
    safe_collection: SafeCollection,
    find_result_dir: ResultDirFinder,
    read_json: ReadJson,
    wiki_root: Path,
    document_key_from_manifest: DocumentKeyFactory,
) -> Path:
    task_id = safe_task_id(task_id)
    collection_name = safe_collection(collection)
    if manifest is None:
        result_dir = find_result_dir(task_id)
        manifest = read_json(result_dir / "manifest.json", {}) if result_dir else {}
    return wiki_root / collection_name / document_key_from_manifest(task_id, manifest or {})


def document_wiki_status(
    task_id: str,
    collection: str | None,
    *,
    safe_task_id: SafeTaskId,
    safe_collection: SafeCollection,
    find_result_dir: ResultDirFinder,
    read_json: ReadJson,
    sha256_file: Sha256File,
    wiki_root: Path,
    package_manifest_name: str,
    document_key_from_manifest: DocumentKeyFactory,
) -> dict[str, Any]:
    task_id = safe_task_id(task_id)
    collection_name = safe_collection(collection)
    result_dir = find_result_dir(task_id)
    manifest = read_json(result_dir / "manifest.json", {}) if result_dir else {}
    package_dir = document_package_dir(
        task_id,
        collection_name,
        manifest,
        safe_task_id=safe_task_id,
        safe_collection=safe_collection,
        find_result_dir=find_result_dir,
        read_json=read_json,
        wiki_root=wiki_root,
        document_key_from_manifest=document_key_from_manifest,
    )
    package_manifest = read_json(package_dir / package_manifest_name, {}) if package_dir.is_dir() else {}
    source_sha = sha256_file(result_dir / "document_full.json") if result_dir else None
    package_sha = package_manifest.get("document_full_sha256")
    stale = bool(package_manifest and source_sha and package_sha and source_sha != package_sha)
    status = "stale" if stale else ("ready" if package_manifest else ("missing" if result_dir else "unavailable"))
    return {
        "status": status,
        "taskId": task_id,
        "collection": collection_name,
        "documentKey": package_dir.name,
        "path": str(package_dir),
        "manifestPath": str(package_dir / package_manifest_name),
        "documentFullSha256": package_sha or "",
        "sourceDocumentFullSha256": source_sha or "",
        "stale": stale,
        "message": "通用文档 Wiki 包需刷新" if stale else ("已归档到通用文档 Wiki" if package_manifest else ("可归档到通用文档 Wiki" if result_dir else "未找到解析产物")),
    }


def document_workflow_status_payload(
    task_id: str,
    collection: str | None,
    *,
    safe_task_id: SafeTaskId,
    artifact_status: Callable[[str], dict[str, Any]],
    wiki_status: Callable[[str, str | None], dict[str, Any]],
    postgres_status: Callable[[str, str | None], dict[str, Any]],
    milvus_status: Callable[[str, str | None], dict[str, Any]],
) -> dict[str, Any]:
    artifact_payload = artifact_status(task_id)
    wiki_payload = wiki_status(task_id, collection)
    postgres_payload = postgres_status(task_id, collection)
    return {
        "taskId": safe_task_id(task_id),
        "targets": {
            "wiki": wiki_payload,
            "postgres": postgres_payload,
            "milvus": milvus_status(task_id, collection),
            "full_text": {"status": "disabled"},
            "object_storage": {"status": "disabled"},
        },
        "artifacts": artifact_payload,
    }


def document_package_manifest_artifact(
    *,
    result_dir: Path,
    artifact_name: str,
    lightweight_artifacts: set[str],
    sha256_file: Sha256File,
    json_artifact_meta: Callable[[Path], Mapping[str, Any]],
) -> dict[str, Any]:
    artifact_path = result_dir / artifact_name
    return {
        "source": str(artifact_path),
        "package_path": document_package_target(artifact_name) if artifact_name in lightweight_artifacts else "",
        "sha256": sha256_file(artifact_path),
        "size_bytes": artifact_path.stat().st_size if artifact_path.is_file() else 0,
        "version": json_artifact_meta(artifact_path).get("schemaVersion") or "",
    }


def document_package_readme_content(package_manifest: Mapping[str, Any], document_markdown: str) -> str:
    title = package_manifest.get("filename") or package_manifest.get("document_key") or package_manifest.get("task_id")
    preview = "\n".join(document_markdown.splitlines()[:80]).strip()
    content = [
        f"# {title}",
        "",
        f"- task_id: `{package_manifest.get('task_id')}`",
        f"- document_kind: `{package_manifest.get('document_kind')}`",
        f"- parser_provider: `{package_manifest.get('parser_provider')}`",
        f"- source_result_dir: `{package_manifest.get('source_result_dir')}`",
        "",
        "## Preview",
        "",
        preview or "_No markdown preview available._",
        "",
    ]
    return "\n".join(content)


def build_document_package_manifests(
    *,
    task_id: str,
    collection_name: str,
    document_key: str,
    result_dir: Path,
    parser_manifest: Mapping[str, Any],
    created_at: str,
    document_full_sha: str | None,
    core_artifacts: Iterable[str],
    optional_artifacts: Iterable[str],
    lightweight_artifacts: set[str],
    copied_files: Iterable[str],
    retained_dirs: Iterable[str],
    package_manifest_name: str,
    artifact_manifest_name: str,
    sha256_file: Sha256File,
    json_artifact_meta: Callable[[Path], Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    artifact_names = list(core_artifacts) + list(optional_artifacts)
    artifacts_manifest = {
        name: document_package_manifest_artifact(
            result_dir=result_dir,
            artifact_name=name,
            lightweight_artifacts=lightweight_artifacts,
            sha256_file=sha256_file,
            json_artifact_meta=json_artifact_meta,
        )
        for name in artifact_names
        if (result_dir / name).is_file()
    }
    package_manifest = {
        "schema_version": "generic_document_package_v1",
        "document_id": f"doc-{task_id}",
        "task_id": task_id,
        "collection": collection_name,
        "document_key": document_key,
        "filename": parser_manifest.get("filename") or "",
        "document_kind": parser_manifest.get("document_kind") or "",
        "parser_provider": parser_manifest.get("parser_provider") or "",
        "source_result_dir": str(result_dir),
        "package_version": "1",
        "created_at": created_at,
        "document_full_sha256": document_full_sha,
        "artifacts": artifacts_manifest,
        "wiki_keeps": [
            "README.md",
            package_manifest_name,
            artifact_manifest_name,
            *[document_package_target(name) for name in copied_files],
            *list(retained_dirs),
        ],
        "full_parse_archive": "document_parser_results_and_postgresql",
        "note": "Wiki 只保留轻量入口、原始源文件树和 artifact_manifest.json；完整解析包继续保存在 source_result_dir，并由 PostgreSQL document_parser schema 入库。",
        "import_targets": {
            "postgres": {"schema": "document_parser", "document_id": f"doc-{task_id}", "last_imported_at": None},
            "milvus": {"collection": "siq_documents", "last_imported_at": None},
        },
    }
    artifact_manifest = {
        "schema_version": "generic_document_artifact_manifest_v1",
        "task_id": task_id,
        "collection": collection_name,
        "document_key": document_key,
        "source_result_dir": str(result_dir),
        "generated_at": created_at,
        "artifacts": artifacts_manifest,
    }
    return package_manifest, artifact_manifest


def build_document_collection_index(
    index_payload: Mapping[str, Any],
    *,
    task_id: str,
    collection_name: str,
    package_dir: Path,
    package_manifest: Mapping[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    documents = [
        item for item in (index_payload.get("documents") or [])
        if isinstance(item, dict) and item.get("task_id") != task_id
    ]
    documents.append({
        "task_id": task_id,
        "document_id": package_manifest["document_id"],
        "document_key": package_manifest["document_key"],
        "filename": package_manifest["filename"],
        "document_kind": package_manifest["document_kind"],
        "path": str(package_dir),
        "updated_at": package_manifest["created_at"],
    })
    return {
        **dict(index_payload),
        "schema_version": "generic_document_collection_index_v1",
        "collection": collection_name,
        "generated_at": generated_at,
        "document_count": len(documents),
        "documents": sorted(documents, key=lambda item: str(item.get("updated_at") or ""), reverse=True),
    }


def document_postgres_status_payload(
    *,
    task_id: str,
    wiki_status: Mapping[str, Any],
    script_path: Path,
    script_exists: bool,
) -> dict[str, Any]:
    if not script_exists:
        return {
            "status": "missing",
            "schema": "document_parser",
            "script": str(script_path),
            "message": "通用文档 PostgreSQL importer 不存在",
        }
    if wiki_status.get("status") not in {"ready", "stale"}:
        return {
            "status": "waiting_for_wiki",
            "schema": "document_parser",
            "script": str(script_path),
            "message": "请先导入 Wiki 包",
        }
    return {
        "status": "ready",
        "schema": "document_parser",
        "script": str(script_path),
        "packagePath": wiki_status.get("path") or "",
        "document_id": f"doc-{task_id}",
        "message": "可导入 PostgreSQL document_parser schema",
    }


def document_milvus_status_payload(
    *,
    wiki_status: Mapping[str, Any],
    script_path: Path,
    script_exists: bool,
    chunks_exists: bool = False,
    chunk_count: int = 0,
    report_path: Path | None = None,
    report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not script_exists:
        return {
            "status": "missing",
            "collection": "siq_documents",
            "script": str(script_path),
            "message": "通用文档 chunk 脚本不存在",
        }
    if wiki_status.get("status") not in {"ready", "stale"}:
        return {
            "status": "waiting_for_wiki",
            "collection": "siq_documents",
            "script": str(script_path),
            "message": "请先导入 Wiki 包",
        }
    package_dir = Path(str(wiki_status.get("path") or ""))
    chunks_path = package_dir / "semantic" / "chunks.jsonl"
    active_report = report or {}
    inserted = int(active_report.get("milvus_inserted") or 0) if isinstance(active_report, Mapping) else 0
    if inserted:
        return {
            "status": "completed",
            "collection": active_report.get("collection") or "siq_documents",
            "script": str(script_path),
            "packagePath": str(package_dir),
            "chunksPath": str(chunks_path),
            "reportPath": str(report_path) if report_path else "",
            "chunkCount": int(active_report.get("chunk_count") or 0),
            "insertedCount": inserted,
            "batchTag": active_report.get("batch_tag") or "",
            "message": f"已写入 Milvus {inserted} 个 chunks",
        }
    if not chunks_exists:
        return {
            "status": "ready",
            "collection": "siq_documents",
            "script": str(script_path),
            "packagePath": str(package_dir),
            "chunksPath": str(chunks_path),
            "chunkCount": 0,
            "message": "可生成语义 chunks",
        }
    return {
        "status": "chunks_ready",
        "collection": "siq_documents",
        "script": str(script_path),
        "packagePath": str(package_dir),
        "chunksPath": str(chunks_path),
        "reportPath": str(report_path) if report_path else "",
        "chunkCount": chunk_count,
        "message": f"已生成 {chunk_count} 个语义 chunks",
    }


def document_db_import_command(
    *,
    executable: str,
    script_path: Path,
    package_dir: Path,
    database_url: str,
) -> list[str]:
    return [
        executable,
        str(script_path),
        str(package_dir),
        "--database-url",
        database_url,
    ]


def document_db_import_env(
    base_env: Mapping[str, str],
    *,
    pg_config: Mapping[str, Any],
    database_url: str,
) -> dict[str, str]:
    env = dict(base_env)
    env.update({
        "PGHOST": str(pg_config["host"]),
        "PGPORT": str(pg_config["port"]),
        "PGDATABASE": str(pg_config["dbname"]),
        "PGUSER": str(pg_config["user"]),
        "PGPASSWORD": str(pg_config["password"]),
        "DATABASE_URL": database_url,
    })
    return env


def document_db_import_plan(
    *,
    executable: str,
    script_path: Path,
    package_dir: Path,
    base_env: Mapping[str, str],
    pg_config: Mapping[str, Any],
    database_url: str,
    timeout: int = 300,
) -> dict[str, Any]:
    return {
        "args": document_db_import_command(
            executable=executable,
            script_path=script_path,
            package_dir=package_dir,
            database_url=database_url,
        ),
        "env": document_db_import_env(
            base_env,
            pg_config=pg_config,
            database_url=database_url,
        ),
        "timeout": timeout,
    }


def document_semantic_plan(
    *,
    executable: str,
    script_path: Path,
    package_dir: Path,
    milvus: bool = False,
    collection: str = "siq_documents",
) -> dict[str, Any]:
    output = package_dir / "semantic" / "chunks.jsonl"
    report = package_dir / "semantic" / "ingest_report.json"
    args = [
        executable,
        str(script_path),
        str(package_dir),
        "--collection",
        collection,
        "--output",
        str(output),
        "--report",
        str(report),
    ]
    if milvus:
        args.append("--milvus")
    return {
        "args": args,
        "output": output,
        "report": report,
        "timeout": 1800 if milvus else 300,
        "semantic_mode": "milvus" if milvus else "chunks",
    }


def document_semantic_command(
    *,
    executable: str,
    script_path: Path,
    package_dir: Path,
    milvus: bool = False,
    collection: str = "siq_documents",
) -> dict[str, Any]:
    return document_semantic_plan(
        executable=executable,
        script_path=script_path,
        package_dir=package_dir,
        milvus=milvus,
        collection=collection,
    )
