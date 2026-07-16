from __future__ import annotations

import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from services import deal_store, document_parser_artifact_transport as transport

DEAL_ID = "DEAL-ARTIFACT-TRANSPORT-001"
DOCUMENT_ID = "DOC-0123456789ABCDEF"
PARSE_RUN_ID = "PRUN-20260716-0123456789AB"
TASK_ID = "document-task.foreign-1"
API_BASE = "http://document-parser.internal:15010"


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _artifact_bytes(task_id: str = TASK_ID) -> dict[str, bytes]:
    return {
        "manifest.json": _json_bytes(
            {
                "schema_version": "document_manifest_v1",
                "task_id": task_id,
                "document_kind": "text",
            }
        ),
        "document.md": b"# Business plan\n\nVerified primary-market evidence.\n",
        "document_full.json": _json_bytes(
            {
                "schema_version": "document_full_v1",
                "task_id": task_id,
                "markdown": "# Business plan",
            }
        ),
        "blocks.json": _json_bytes(
            {
                "schema_version": "document_blocks_v1",
                "task_id": task_id,
                "blocks": [],
            }
        ),
        "source_map.json": _json_bytes(
            {
                "schema_version": "document_source_map_v1",
                "task_id": task_id,
                "sources": [],
            }
        ),
        "quality_report.json": _json_bytes(
            {
                "schema_version": "document_quality_report_v1",
                "task_id": task_id,
                "status": "pass",
            }
        ),
        "layout_blocks.json": _json_bytes(
            {
                "schema_version": "document_layout_blocks_v1",
                "task_id": task_id,
                "blocks": [],
            }
        ),
    }


def _result_contract(
    files: dict[str, bytes],
    *,
    hash_overrides: dict[str, str] | None = None,
) -> dict:
    manifest = json.loads(files["manifest.json"])
    overrides = hash_overrides or {}
    return {
        "artifact_contract_version": transport.RESULT_CONTRACT_VERSION,
        "task": {"task_id": TASK_ID, "status": "completed"},
        "manifest": manifest,
        "artifacts": {
            name: {
                "exists": True,
                "path": name,
                "url": f"/api/artifact/{TASK_ID}/{name}",
                "size_bytes": len(content),
                "sha256": overrides.get(name)
                or hashlib.sha256(content).hexdigest(),
            }
            for name, content in files.items()
        },
    }


def _deal_target(tmp_path: Path) -> tuple[Path, Path]:
    wiki_root = tmp_path / "wiki"
    deal_store.create_deal_package(
        deal_id=DEAL_ID,
        company_name="Artifact Transport Issuer",
        wiki_root=wiki_root,
    )
    target = (
        wiki_root
        / "deals"
        / DEAL_ID
        / "parsed_documents"
        / DOCUMENT_ID
        / "runs"
        / PARSE_RUN_ID
    )
    return wiki_root, target


def _scope_headers() -> dict[str, str]:
    return transport.parser_owner_headers(
        {
            "parser_owner_scope": {
                "owner_id": "17",
                "tenant_id": "tenant-primary",
                "market_scope": "CN",
                "user_role": "analyst",
            }
        },
        access_token="parser-secret",
    )


def _api_handler(
    files: dict[str, bytes],
    *,
    result_status: int = 200,
    seen: list[httpx.Request] | None = None,
):
    contract = _result_contract(files)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path == f"/api/result/{TASK_ID}":
            if result_status != 200:
                return httpx.Response(result_status, json={"error": "upstream"})
            return httpx.Response(200, json=contract)
        prefix = f"/api/artifact/{TASK_ID}/"
        if request.url.path.startswith(prefix):
            name = request.url.path.removeprefix(prefix)
            body = files[name]
            if name == "layout_blocks.json" and request.url.query != b"download=true":
                body = _json_bytes({"task_id": TASK_ID, "blocks": [], "pages": []})
            return httpx.Response(
                200,
                content=body,
                headers={"Content-Length": str(len(body))},
            )
        return httpx.Response(404)

    return handler


def _write_shared_result(root: Path, files: dict[str, bytes]) -> None:
    result_dir = root / TASK_ID
    result_dir.mkdir(parents=True)
    for name, content in files.items():
        (result_dir / name).write_bytes(content)


def test_api_transport_archives_foreign_result_with_frozen_scope(tmp_path: Path):
    wiki_root, target = _deal_target(tmp_path)
    files = _artifact_bytes()
    seen: list[httpx.Request] = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(_api_handler(files, seen=seen)))
    try:
        result = asyncio.run(
            transport.archive_document_parser_result(
                deal_id=DEAL_ID,
                document_id=DOCUMENT_ID,
                parse_run_id=PARSE_RUN_ID,
                parser_task_id=TASK_ID,
                target_dir=target,
                api_base=API_BASE,
                headers=_scope_headers(),
                mode="api",
                shared_results_root=tmp_path / "does-not-exist",
                client=client,
                wiki_root=wiki_root,
            )
        )
    finally:
        asyncio.run(client.aclose())

    assert result["transport"] == "api"
    assert result["document_path"].read_bytes() == files["document.md"]
    assert seen[0].url.query == b"include_markdown=false"
    assert all(
        request.url.query == b"download=true"
        for request in seen[1:]
        if request.url.path.startswith(f"/api/artifact/{TASK_ID}/")
    )
    assert all(request.headers["x-siq-user-id"] == "17" for request in seen)
    assert all(request.headers["x-siq-tenant-id"] == "tenant-primary" for request in seen)
    archive = json.loads((target / "archive_manifest.json").read_text())
    assert archive["artifact_contract_version"] == transport.RESULT_CONTRACT_VERSION
    assert len(archive["bundle_sha256"]) == 64
    assert not any(str(target) in json.dumps(value) for value in archive.values())


@pytest.mark.parametrize("status_code", [401, 404])
def test_auth_or_scope_failure_never_falls_back_to_shared_fs(
    tmp_path: Path,
    status_code: int,
):
    wiki_root, target = _deal_target(tmp_path)
    files = _artifact_bytes()
    shared_root = tmp_path / "shared-results"
    _write_shared_result(shared_root, files)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(_api_handler(files, result_status=status_code))
    )
    try:
        with pytest.raises(transport.DocumentArtifactTransportError):
            asyncio.run(
                transport.archive_document_parser_result(
                    deal_id=DEAL_ID,
                    document_id=DOCUMENT_ID,
                    parse_run_id=PARSE_RUN_ID,
                    parser_task_id=TASK_ID,
                    target_dir=target,
                    api_base=API_BASE,
                    headers=_scope_headers(),
                    mode="auto",
                    shared_results_root=shared_root,
                    client=client,
                    wiki_root=wiki_root,
                )
            )
    finally:
        asyncio.run(client.aclose())
    assert not target.exists()


@pytest.mark.parametrize("status_code", [500, 503, 599])
def test_any_server_error_in_auto_mode_falls_back_to_shared_fs(
    tmp_path: Path,
    status_code: int,
):
    wiki_root, target = _deal_target(tmp_path)
    files = _artifact_bytes()
    shared_root = tmp_path / "shared-results"
    _write_shared_result(shared_root, files)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(_api_handler(files, result_status=status_code))
    )
    try:
        result = asyncio.run(
            transport.archive_document_parser_result(
                deal_id=DEAL_ID,
                document_id=DOCUMENT_ID,
                parse_run_id=PARSE_RUN_ID,
                parser_task_id=TASK_ID,
                target_dir=target,
                api_base=API_BASE,
                headers=_scope_headers(),
                mode="auto",
                shared_results_root=shared_root,
                client=client,
                wiki_root=wiki_root,
            )
        )
    finally:
        asyncio.run(client.aclose())
    assert result["transport"] == "shared_fs"
    assert result["document_path"].read_bytes() == files["document.md"]


def test_bad_hash_and_declared_size_limit_fail_closed(tmp_path: Path):
    wiki_root, target = _deal_target(tmp_path)
    files = _artifact_bytes()
    bad_contract = _result_contract(
        files,
        hash_overrides={"document.md": "0" * 64},
    )

    def bad_hash_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/api/result/{TASK_ID}":
            return httpx.Response(200, json=bad_contract)
        name = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, content=files[name])

    client = httpx.AsyncClient(transport=httpx.MockTransport(bad_hash_handler))
    try:
        with pytest.raises(transport.DocumentArtifactTransportError, match="integrity"):
            asyncio.run(
                transport.archive_document_parser_result(
                    deal_id=DEAL_ID,
                    document_id=DOCUMENT_ID,
                    parse_run_id=PARSE_RUN_ID,
                    parser_task_id=TASK_ID,
                    target_dir=target,
                    api_base=API_BASE,
                    headers=_scope_headers(),
                    mode="api",
                    client=client,
                    wiki_root=wiki_root,
                )
            )
    finally:
        asyncio.run(client.aclose())
    assert not target.exists()

    small_limits = transport.ArtifactLimits(
        max_file_bytes=8,
        max_total_bytes=1024,
        max_files=8,
        max_contract_bytes=1024 * 1024,
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(_api_handler(files)))
    try:
        with pytest.raises(transport.DocumentArtifactTransportError, match="per-file limit"):
            asyncio.run(
                transport.archive_document_parser_result(
                    deal_id=DEAL_ID,
                    document_id=DOCUMENT_ID,
                    parse_run_id=PARSE_RUN_ID,
                    parser_task_id=TASK_ID,
                    target_dir=target,
                    api_base=API_BASE,
                    headers=_scope_headers(),
                    mode="api",
                    limits=small_limits,
                    client=client,
                    wiki_root=wiki_root,
                )
            )
    finally:
        asyncio.run(client.aclose())
    assert not target.exists()


def test_concurrent_publish_keeps_one_immutable_archive(tmp_path: Path):
    wiki_root, target = _deal_target(tmp_path)
    files = _artifact_bytes()

    def archive_once() -> dict:
        async def run() -> dict:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(_api_handler(files))
            ) as client:
                return await transport.archive_document_parser_result(
                    deal_id=DEAL_ID,
                    document_id=DOCUMENT_ID,
                    parse_run_id=PARSE_RUN_ID,
                    parser_task_id=TASK_ID,
                    target_dir=target,
                    api_base=API_BASE,
                    headers=_scope_headers(),
                    mode="api",
                    client=client,
                    wiki_root=wiki_root,
                )

        return asyncio.run(run())

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: archive_once(), range(2)))

    assert {item["status"] for item in results} <= {"archived", "existing"}
    first_bundle = results[0]["archive_manifest"]["bundle_sha256"]
    assert all(item["archive_manifest"]["bundle_sha256"] == first_bundle for item in results)
    verified = asyncio.run(
        transport.archive_document_parser_result(
            deal_id=DEAL_ID,
            document_id=DOCUMENT_ID,
            parse_run_id=PARSE_RUN_ID,
            parser_task_id=TASK_ID,
            target_dir=target,
            api_base=API_BASE,
            headers=_scope_headers(),
            mode="api",
            wiki_root=wiki_root,
        )
    )
    assert verified["status"] == "existing"


def test_existing_archive_manifest_symlink_is_rejected(tmp_path: Path):
    wiki_root, target = _deal_target(tmp_path)
    files = _artifact_bytes()

    async def create_archive() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(_api_handler(files))
        ) as client:
            await transport.archive_document_parser_result(
                deal_id=DEAL_ID,
                document_id=DOCUMENT_ID,
                parse_run_id=PARSE_RUN_ID,
                parser_task_id=TASK_ID,
                target_dir=target,
                api_base=API_BASE,
                headers=_scope_headers(),
                mode="api",
                client=client,
                wiki_root=wiki_root,
            )

    asyncio.run(create_archive())
    archive_path = target / "archive_manifest.json"
    external = tmp_path / "external-archive-manifest.json"
    external.write_bytes(archive_path.read_bytes())
    archive_path.unlink()
    archive_path.symlink_to(external)

    with pytest.raises(transport.DocumentArtifactTransportError, match="manifest is unsafe"):
        asyncio.run(
            transport.archive_document_parser_result(
                deal_id=DEAL_ID,
                document_id=DOCUMENT_ID,
                parse_run_id=PARSE_RUN_ID,
                parser_task_id=TASK_ID,
                target_dir=target,
                api_base=API_BASE,
                headers=_scope_headers(),
                mode="api",
                wiki_root=wiki_root,
            )
        )


def test_legacy_shared_archive_without_research_artifacts_requires_reparse(tmp_path: Path):
    wiki_root, target = _deal_target(tmp_path)
    files = _artifact_bytes()
    files.pop("source_map.json")
    shared_root = tmp_path / "legacy-shared-results"
    _write_shared_result(shared_root, files)

    with pytest.raises(transport.DocumentArtifactTransportError, match="source_map.json"):
        asyncio.run(
            transport.archive_document_parser_result(
                deal_id=DEAL_ID,
                document_id=DOCUMENT_ID,
                parse_run_id=PARSE_RUN_ID,
                parser_task_id=TASK_ID,
                target_dir=target,
                api_base=API_BASE,
                headers=_scope_headers(),
                mode="shared_fs",
                shared_results_root=shared_root,
                wiki_root=wiki_root,
            )
        )
