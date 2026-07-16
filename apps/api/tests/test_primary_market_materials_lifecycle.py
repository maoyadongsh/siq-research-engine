import asyncio
import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from services import (
    deal_documents,
    deal_evidence,
    deal_evidence_milvus,
    deal_store,
    primary_market_materials as materials,
    primary_market_wiki,
)

DEAL_ID = "DEAL-PMM-LIFECYCLE-001"


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _deal(tmp_path: Path) -> Path:
    wiki_root = tmp_path / "wiki"
    deal_store.create_deal_package(
        deal_id=DEAL_ID,
        company_name="Lifecycle Issuer",
        wiki_root=wiki_root,
    )
    return wiki_root


def _upload(wiki_root: Path, content: bytes = b"%PDF-1.7\nprospectus") -> dict:
    return materials.create_prospectus_document(
        deal_id=DEAL_ID,
        filename="issuer.pdf",
        content_type="application/pdf",
        stream=io.BytesIO(content),
        exchange="SSE",
        board="star",
        filing_stage="registration_draft",
        document_date="2026-07-01",
        created_by={"id": 7, "username": "owner"},
        wiki_root=wiki_root,
    )


def _parser_result(root: Path, task_id: str) -> Path:
    result = root / task_id
    result.mkdir(parents=True)
    markdown = "\n".join(
        [
            "# 重大事项提示",
            "# 风险因素",
            "# 发行人基本情况与股权结构",
            "# 业务与技术",
            "# 行业与竞争格局及市场地位",
            "# 公司治理、独立性与关联交易",
            "# 财务会计信息与管理层分析",
            "# 募集资金运用",
            "# 投资者保护、重要合同与诉讼",
        ]
    ) + "\n" + "招股说明书正文。" * 180
    (result / "result.md").write_text(markdown, encoding="utf-8")
    _write_json(result / "content_list.json", [{"id": "b1", "page": 1, "text": "风险因素"}])
    _write_json(result / "financial_checks.json", {"overall_status": "pass"})
    _write_json(result / "financial_data.json", {"statements": [{"period": "2025"}]})
    artifact_names = ("result.md", "content_list.json", "financial_checks.json", "financial_data.json")
    artifacts = {}
    for name in artifact_names:
        path = result / name
        artifacts[name] = {
            "exists": True,
            "size_bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    _write_json(
        result / "artifact_manifest.json",
        {"schema_version": "pdf_parser_artifact_manifest_v1", "task_id": task_id, "artifacts": artifacts},
    )
    return result


def test_upload_validates_pdf_and_reuses_identical_active_material(tmp_path: Path):
    wiki_root = _deal(tmp_path)

    first = _upload(wiki_root)
    second = _upload(wiki_root)

    assert first["reused"] is False
    assert second["reused"] is True
    assert second["document"]["document_id"] == first["document"]["document_id"]
    raw = materials.deal_raw_pdf_path(DEAL_ID, first["document"]["document_id"], wiki_root=wiki_root)
    assert raw.read_bytes().startswith(b"%PDF-")
    assert len(materials.list_primary_market_materials(DEAL_ID, wiki_root=wiki_root)) == 1

    with pytest.raises(ValueError, match="invalid_pdf"):
        materials.create_prospectus_document(
            deal_id=DEAL_ID,
            filename="fake.pdf",
            content_type="application/pdf",
            stream=io.BytesIO(b"not pdf"),
            wiki_root=wiki_root,
        )
    with pytest.raises(ValueError, match="prospectus_too_large"):
        materials.create_prospectus_document(
            deal_id=DEAL_ID,
            filename="large.pdf",
            content_type="application/pdf",
            stream=io.BytesIO(b"%PDF-" + b"x" * 20),
            max_bytes=10,
            wiki_root=wiki_root,
        )


def test_parse_run_promotion_is_immutable_and_activates_source(tmp_path: Path):
    wiki_root = _deal(tmp_path)
    document = _upload(wiki_root)["document"]
    run = materials.create_parse_run(
        DEAL_ID,
        document["document_id"],
        submitted_by={"id": 7},
        wiki_root=wiki_root,
    )
    task_id = "pmm-parser-task-1"
    materials.update_parse_run_submission(
        DEAL_ID,
        document["document_id"],
        run["parse_run_id"],
        parser_task_id=task_id,
        status="queued",
        wiki_root=wiki_root,
    )
    parser_root = tmp_path / "parser-results"
    _parser_result(parser_root, task_id)

    promoted = materials.promote_parse_run_artifacts(
        DEAL_ID,
        document["document_id"],
        run["parse_run_id"],
        promoted_by={"id": 7},
        wiki_root=wiki_root,
        results_root=parser_root,
    )

    assert promoted["status"] == "promoted"
    assert promoted["quality"]["status"] == "ready"
    assert promoted["analysis_source"]["status"] == "ready"
    current = json.loads(
        materials.deal_current_parse_run_path(
            DEAL_ID, document["document_id"], wiki_root=wiki_root
        ).read_text()
    )
    assert current["parse_run_id"] == run["parse_run_id"]

    repeated = materials.promote_parse_run_artifacts(
        DEAL_ID,
        document["document_id"],
        run["parse_run_id"],
        wiki_root=wiki_root,
        results_root=parser_root,
    )
    assert repeated["status"] == "existing"

    archive = materials.deal_parse_run_dir(
        DEAL_ID, document["document_id"], run["parse_run_id"], wiki_root=wiki_root
    )
    (archive / "document.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="hash conflict"):
        materials.promote_parse_run_artifacts(
            DEAL_ID,
            document["document_id"],
            run["parse_run_id"],
            wiki_root=wiki_root,
            results_root=parser_root,
        )


def test_failed_promotion_does_not_update_current_pointer(tmp_path: Path):
    wiki_root = _deal(tmp_path)
    document = _upload(wiki_root)["document"]
    run = materials.create_parse_run(
        DEAL_ID,
        document["document_id"],
        submitted_by={"id": 7, "username": "owner"},
        parser_owner_scope={
            "owner_id": "7",
            "tenant_id": "tenant-primary",
            "market_scope": "CN",
            "user_role": "analyst",
        },
        wiki_root=wiki_root,
    )
    task_id = "pmm-parser-task-missing-markdown"
    materials.update_parse_run_submission(
        DEAL_ID,
        document["document_id"],
        run["parse_run_id"],
        parser_task_id=task_id,
        status="queued",
        wiki_root=wiki_root,
    )
    parser_root = tmp_path / "parser-results"
    result = parser_root / task_id
    result.mkdir(parents=True)
    _write_json(result / "artifact_manifest.json", {"task_id": task_id, "artifacts": {}})

    with pytest.raises(ValueError, match="canonical parser Markdown"):
        materials.promote_parse_run_artifacts(
            DEAL_ID,
            document["document_id"],
            run["parse_run_id"],
            wiki_root=wiki_root,
            results_root=parser_root,
        )

    assert not materials.deal_current_parse_run_path(
        DEAL_ID, document["document_id"], wiki_root=wiki_root
    ).exists()


def test_background_recovery_completes_generic_material_wiki_and_evidence(tmp_path, monkeypatch):
    wiki_root = _deal(tmp_path)
    document = deal_documents.create_deal_document(
        deal_id=DEAL_ID,
        filename="business-plan.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        stream=io.BytesIO(b"office document"),
        document_type="business_plan",
        wiki_root=wiki_root,
    )
    run = materials.create_parse_run(
        DEAL_ID,
        document["document_id"],
        submitted_by={"id": 7, "username": "owner"},
        parser_owner_scope={
            "owner_id": "7",
            "tenant_id": "tenant-primary",
            "market_scope": "CN",
            "user_role": "analyst",
        },
        wiki_root=wiki_root,
    )
    task_id = "generic-document-task-1"
    materials.update_parse_run_submission(
        DEAL_ID,
        document["document_id"],
        run["parse_run_id"],
        parser_task_id=task_id,
        status="queued",
        wiki_root=wiki_root,
    )
    generic_root = tmp_path / "document-results"
    result_dir = generic_root / task_id
    result_dir.mkdir(parents=True)
    (result_dir / "document.md").write_text(
        "# Business plan\n\nRevenue and customer evidence.\n",
        encoding="utf-8",
    )
    _write_json(
        result_dir / "manifest.json",
        {"schema_version": "document_manifest_v1", "task_id": task_id},
    )
    _write_json(
        result_dir / "document_full.json",
        {"schema_version": "document_full_v1", "task_id": task_id},
    )
    _write_json(
        result_dir / "blocks.json",
        {"schema_version": "document_blocks_v1", "task_id": task_id, "blocks": []},
    )
    _write_json(
        result_dir / "source_map.json",
        {"schema_version": "document_source_map_v1", "task_id": task_id, "sources": []},
    )
    _write_json(
        result_dir / "quality_report.json",
        {"schema_version": "document_quality_report_v1", "task_id": task_id, "status": "pass"},
    )
    monkeypatch.setattr(deal_documents, "DOCUMENT_PARSER_RESULTS_ROOT", generic_root)
    monkeypatch.setattr(primary_market_wiki, "DOCUMENT_PARSER_RESULTS_ROOT", generic_root)
    monkeypatch.setattr(deal_evidence, "DOCUMENT_PARSER_RESULTS_ROOT", generic_root)
    monkeypatch.delenv("SIQ_PRIMARY_MARKET_MILVUS_INDEX_ENABLED", raising=False)

    summary = materials.recover_primary_market_materials_on_startup(
        wiki_root=wiki_root,
        document_results_root=generic_root,
        document_artifact_mode="shared_fs",
    )

    assert summary["promoted"] == 1
    metadata = deal_store.read_json(
        wiki_root / "deals" / DEAL_ID / "data_room" / "metadata" / f"{document['document_id']}.json",
        {},
    )
    assert metadata["parse_status"] == "succeeded"
    assert metadata["wiki_path"].startswith("wiki/company/materials/teaser_bp/")
    items = (wiki_root / "deals" / DEAL_ID / "evidence" / "evidence_items.ndjson").read_text()
    assert '"wiki_path": "wiki/company/materials/teaser_bp/' in items


def test_startup_recovery_archives_generic_material_over_api_without_shared_fs(
    tmp_path: Path,
    monkeypatch,
):
    wiki_root = _deal(tmp_path)
    document = deal_documents.create_deal_document(
        deal_id=DEAL_ID,
        filename="due-diligence.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        stream=io.BytesIO(b"office document"),
        document_type="due_diligence",
        wiki_root=wiki_root,
    )
    run = materials.create_parse_run(
        DEAL_ID,
        document["document_id"],
        submitted_by={"id": 17, "username": "owner"},
        parser_owner_scope={
            "owner_id": "17",
            "tenant_id": "tenant-primary",
            "market_scope": "CN",
            "user_role": "analyst",
        },
        wiki_root=wiki_root,
    )
    task_id = "generic-api-recovery-1"
    materials.update_parse_run_submission(
        DEAL_ID,
        document["document_id"],
        run["parse_run_id"],
        parser_task_id=task_id,
        status="queued",
        wiki_root=wiki_root,
    )
    file_payloads = {
        "manifest.json": json.dumps(
            {"schema_version": "document_manifest_v1", "task_id": task_id}
        ).encode(),
        "document.md": b"# Due diligence\n\nContract and ownership evidence.\n",
        "document_full.json": json.dumps(
            {"schema_version": "document_full_v1", "task_id": task_id}
        ).encode(),
        "blocks.json": json.dumps(
            {
                "schema_version": "document_blocks_v1",
                "task_id": task_id,
                "blocks": [],
            }
        ).encode(),
        "source_map.json": json.dumps(
            {
                "schema_version": "document_source_map_v1",
                "task_id": task_id,
                "sources": [],
            }
        ).encode(),
        "quality_report.json": json.dumps(
            {
                "schema_version": "document_quality_report_v1",
                "task_id": task_id,
                "status": "pass",
            }
        ).encode(),
    }
    contract = {
        "artifact_contract_version": "document_parser_artifact_contract_v1",
        "task": {"task_id": task_id, "status": "completed"},
        "manifest": json.loads(file_payloads["manifest.json"]),
        "artifacts": {
            name: {
                "exists": True,
                "path": name,
                "url": f"/api/artifact/{task_id}/{name}",
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for name, content in file_payloads.items()
        },
    }
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["x-siq-user-id"] == "17"
        assert request.headers["x-siq-tenant-id"] == "tenant-primary"
        if request.url.path == f"/api/result/{task_id}":
            assert request.url.query == b"include_markdown=false"
            return httpx.Response(200, json=contract)
        name = request.url.path.removeprefix(f"/api/artifact/{task_id}/")
        return httpx.Response(200, content=file_payloads[name])

    original_project = primary_market_wiki.project_material_to_company_wiki_safe
    projection_seen = {}

    def assert_archive_published_before_wiki(*args, **kwargs):
        source_path = Path(kwargs["source_path"])
        archive_dir = Path(kwargs["structured_artifact_dir"])
        assert source_path.parent == archive_dir
        assert (archive_dir / "archive_manifest.json").is_file()
        assert (archive_dir / "blocks.json").is_file()
        projection_seen["archive_dir"] = archive_dir
        return original_project(*args, **kwargs)

    monkeypatch.setattr(
        primary_market_wiki,
        "project_material_to_company_wiki_safe",
        assert_archive_published_before_wiki,
    )
    monkeypatch.setenv("SIQ_DOCUMENT_PARSER_API_BASE", "http://parser.internal:15010")
    monkeypatch.delenv("SIQ_PRIMARY_MARKET_MILVUS_INDEX_ENABLED", raising=False)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        summary = materials.recover_primary_market_materials_on_startup(
            wiki_root=wiki_root,
            document_results_root=tmp_path / "absent-shared-results",
            document_artifact_mode="api",
            document_artifact_client=client,
        )
    finally:
        asyncio.run(client.aclose())

    assert summary["promoted"] == 1
    assert projection_seen["archive_dir"].is_dir()
    metadata = deal_store.read_json(
        wiki_root
        / "deals"
        / DEAL_ID
        / "data_room"
        / "metadata"
        / f"{document['document_id']}.json",
        {},
    )
    latest = metadata["parse_runs"][-1]
    assert latest["archive_receipt"]["transport"] == "api"
    assert latest["archive_receipt"]["artifact_contract_version"] == (
        "document_parser_artifact_contract_v1"
    )
    assert requests


def test_startup_recovery_keeps_temporary_generic_archive_outage_pending(
    tmp_path: Path,
    monkeypatch,
):
    wiki_root = _deal(tmp_path)
    document = deal_documents.create_deal_document(
        deal_id=DEAL_ID,
        filename="temporary-outage.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        stream=io.BytesIO(b"office document"),
        document_type="due_diligence",
        wiki_root=wiki_root,
    )
    run = materials.create_parse_run(
        DEAL_ID,
        document["document_id"],
        submitted_by={"id": 17, "username": "owner"},
        parser_owner_scope={
            "owner_id": "17",
            "tenant_id": "tenant-primary",
            "market_scope": "CN",
            "user_role": "analyst",
        },
        wiki_root=wiki_root,
    )
    materials.update_parse_run_submission(
        DEAL_ID,
        document["document_id"],
        run["parse_run_id"],
        parser_task_id="generic-temporary-outage-1",
        status="queued",
        wiki_root=wiki_root,
    )

    async def unavailable(**_kwargs):
        raise materials.document_parser_artifact_transport.DocumentArtifactTransportUnavailable(
            "temporary parser outage"
        )

    monkeypatch.setattr(
        materials.document_parser_artifact_transport,
        "archive_document_parser_result",
        unavailable,
    )
    summary = materials.recover_primary_market_materials_on_startup(
        wiki_root=wiki_root,
        document_results_root=tmp_path / "absent-shared-results",
        document_artifact_mode="api",
    )

    assert summary["pending"] == 1
    assert summary["failed"] == 0
    current = materials.read_material_parse_status(
        DEAL_ID,
        document["document_id"],
        wiki_root=wiki_root,
    )
    assert current["parse_run"]["status"] == "archiving"
    assert current["parse_run"].get("failure_code") is None
    assert current["document"].get("wiki_status") != "failed"


def test_completed_task_reconcile_is_concurrent_and_restart_safe(tmp_path: Path):
    wiki_root = _deal(tmp_path)
    document = _upload(wiki_root)["document"]
    run = materials.create_parse_run(DEAL_ID, document["document_id"], wiki_root=wiki_root)
    task_id = "pmm-parser-task-recovery"
    materials.update_parse_run_submission(
        DEAL_ID,
        document["document_id"],
        run["parse_run_id"],
        parser_task_id=task_id,
        status="queued",
        wiki_root=wiki_root,
    )
    parser_root = tmp_path / "parser-results"
    _parser_result(parser_root, task_id)

    def reconcile():
        return materials.reconcile_parse_run(
            DEAL_ID,
            document["document_id"],
            parser_task={"task_id": task_id, "status": "completed"},
            wiki_root=wiki_root,
            results_root=parser_root,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reconcile(), range(2)))

    assert all(result["document"]["parse_status"] == "succeeded" for result in results)
    run_dir = materials.deal_parse_run_dir(
        DEAL_ID, document["document_id"], run["parse_run_id"], wiki_root=wiki_root
    )
    assert (run_dir / "archive_manifest.json").is_file()
    assert not list(run_dir.parent.glob(".staging-*"))


def test_reconcile_archive_failure_is_diagnostic_and_keeps_current_unchanged(tmp_path: Path):
    wiki_root = _deal(tmp_path)
    document = _upload(wiki_root)["document"]
    run = materials.create_parse_run(DEAL_ID, document["document_id"], wiki_root=wiki_root)
    task_id = "pmm-parser-task-recovery-fail"
    materials.update_parse_run_submission(
        DEAL_ID,
        document["document_id"],
        run["parse_run_id"],
        parser_task_id=task_id,
        status="queued",
        wiki_root=wiki_root,
    )
    parser_root = tmp_path / "parser-results"
    result = parser_root / task_id
    result.mkdir(parents=True)
    _write_json(result / "artifact_manifest.json", {"task_id": task_id, "artifacts": {}})

    with pytest.raises(materials.ArtifactPromotionError):
        materials.reconcile_parse_run(
            DEAL_ID,
            document["document_id"],
            parser_task={"status": "completed"},
            wiki_root=wiki_root,
            results_root=parser_root,
        )

    status = materials.read_material_parse_status(
        DEAL_ID, document["document_id"], wiki_root=wiki_root
    )
    assert status["parse_run"]["status"] == "failed"
    assert status["parse_run"]["failure_code"] == "artifact_promotion_failed"
    assert not materials.deal_current_parse_run_path(
        DEAL_ID, document["document_id"], wiki_root=wiki_root
    ).exists()


def test_delete_prospectus_disables_source_stales_receipts_and_cleans_vectors(
    tmp_path: Path,
    monkeypatch,
):
    wiki_root = _deal(tmp_path)
    document = _upload(wiki_root)["document"]
    package_dir = wiki_root / "deals" / DEAL_ID
    source_id = f"PM:{DEAL_ID}:{document['document_id']}:PRUN-20260716-ABCDEF123456"
    _write_json(
        package_dir / "sources" / "analysis_sources.json",
        {
            "schema_version": materials.PRIMARY_MARKET_ANALYSIS_SOURCES_SCHEMA,
            "deal_id": DEAL_ID,
            "sources": [{
                "source_id": source_id,
                "document_id": document["document_id"],
                "status": "ready",
            }],
        },
    )
    first_snapshot = deal_evidence.refresh_evidence_snapshot(DEAL_ID, wiki_root=wiki_root)
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "agents": {
                "siq_ic_legal_scanner": {
                    "agent_id": "siq_ic_legal_scanner",
                    "evidence_snapshot_hash": first_snapshot["snapshot_hash"],
                    "source_ids": [source_id],
                    "gate": {"allowed_to_speak": True, "blocking_reasons": []},
                }
            },
            "by_agent_phase": {
                "siq_ic_legal_scanner": {
                    "R1": {
                        "agent_id": "siq_ic_legal_scanner",
                        "evidence_snapshot_hash": first_snapshot["snapshot_hash"],
                        "source_ids": [source_id],
                        "gate": {"allowed_to_speak": True, "blocking_reasons": []},
                    }
                }
            },
        },
    )
    cleanup_calls = []
    _write_json(
        package_dir / deal_evidence_milvus.MILVUS_INDEX_RECEIPT_PATH,
        {"status": "indexed", "snapshot_hash": first_snapshot["snapshot_hash"]},
    )
    monkeypatch.setattr(
        deal_evidence_milvus,
        "remove_deal_document_rows",
        lambda deal_id, document_id, **kwargs: cleanup_calls.append(
            {"deal_id": deal_id, "document_id": document_id, **kwargs}
        ) or {"status": "cleaned", "deleted": 1},
    )

    result = deal_documents.delete_deal_document(
        DEAL_ID,
        document["document_id"],
        deleted_by={"id": 7},
        wiki_root=wiki_root,
    )

    assert result == {"ok": True, "document_id": document["document_id"]}
    sources = materials.list_analysis_sources(DEAL_ID, wiki_root=wiki_root)
    assert sources[0]["status"] == "disabled"
    assert sources[0]["disable_note"] == "document_deleted"
    receipts = deal_store.read_json(package_dir / "phases" / "startup_receipts.json", {})
    assert receipts["agents"]["siq_ic_legal_scanner"]["gate"]["allowed_to_speak"] is False
    assert receipts["by_agent_phase"]["siq_ic_legal_scanner"]["R1"]["readiness_status"] == "stale"
    assert cleanup_calls[0]["deal_id"] == DEAL_ID
    assert cleanup_calls[0]["document_id"] == document["document_id"]
