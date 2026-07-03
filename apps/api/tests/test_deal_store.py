import json
import hashlib
from io import BytesIO
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from routers import deals
from services import deal_contracts
from services import deal_documents
from services import deal_store
from services.ic_openclaw_importer import import_openclaw_project


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_ndjson(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def test_create_and_read_deal_package(tmp_path):
    summary = deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="杭州宇树科技股份有限公司",
        industry="机器人",
        stage="Pre-IPO",
        wiki_root=tmp_path,
    )

    assert summary["deal_id"] == "DEAL-YUSHU-2026-001"
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    assert (package_dir / "project_meta.json").is_file()
    assert (package_dir / "manifest.json").is_file()
    assert (package_dir / "phases" / "workflow_state.json").is_file()

    detail = deal_store.read_deal_detail("DEAL-YUSHU-2026-001", wiki_root=tmp_path)
    assert detail["project_meta"]["company_name"] == "杭州宇树科技股份有限公司"
    assert detail["workflow"]["current_phase"] == "R0"
    assert deal_store.list_deals(wiki_root=tmp_path)[0]["stage"] == "Pre-IPO"


def test_deal_id_rejects_path_escape(tmp_path):
    with pytest.raises(ValueError):
        deal_store.safe_deal_dir("../escape", wiki_root=tmp_path)


def test_import_openclaw_project_maps_core_files(tmp_path):
    openclaw_root = tmp_path / "openclaw" / "projects"
    source = openclaw_root / "SIQ-YUSHU-2026-002"
    _write_json(
        source / "project_meta.json",
        {
            "company_name": "杭州宇树科技股份有限公司",
            "industry": "机器人",
            "stage": "Pre-IPO",
        },
    )
    _write_json(
        source / "phases" / "workflow_state.json",
        {
            "company_name": "杭州宇树科技股份有限公司",
            "status": "r4_completed",
            "final_decision": "pass",
            "final_score": 78.55,
        },
    )
    _write_json(source / "phases" / "r1_reports.json", {"ic_strategist": {"score": 87}})
    _write_json(source / "phases" / "r4_decision.json", {"decision": "pass", "final_score": 78.55})
    (source / "discussion").mkdir(parents=True)
    (source / "discussion" / "05_最终投决报告.md").write_text("# Final", encoding="utf-8")
    (source / "40_decision").mkdir(parents=True)
    (source / "40_decision" / "IC_DECISION_REPORT.md").write_text("# IC Decision", encoding="utf-8")

    result = import_openclaw_project(
        source_root=source,
        deal_id="DEAL-YUSHU-2026-001",
        wiki_root=tmp_path / "wiki",
        openclaw_projects_root=openclaw_root,
    )

    package_dir = tmp_path / "wiki" / "deals" / "DEAL-YUSHU-2026-001"
    assert result["deal"]["project_meta"]["legacy_project_id"] == "SIQ-YUSHU-2026-002"
    assert (package_dir / "phases" / "r1_reports.json").is_file()
    assert (package_dir / "discussion" / "05_最终投决报告.md").is_file()
    assert (package_dir / "decision" / "IC_DECISION_REPORT.md").read_text(encoding="utf-8") == "# IC Decision"
    workflow = json.loads((package_dir / "phases" / "workflow_state.json").read_text(encoding="utf-8"))
    assert workflow["deal_id"] == "DEAL-YUSHU-2026-001"
    assert workflow["legacy_project_id"] == "SIQ-YUSHU-2026-002"
    assert workflow["current_phase"] == "R4"
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["openclaw_import"]["file_count"] >= 4
    assert manifest["hashes"]["decision/IC_DECISION_REPORT.md"]
    assert manifest["hashes"]["phases/workflow_state.json"] == _sha256(package_dir / "phases" / "workflow_state.json")
    assert "source_root" not in result["deal"]["manifest"]["openclaw_import"]
    assert not result["deal"]["summary"]["package_path"].startswith("/")


def test_import_openclaw_project_rejects_source_outside_root(tmp_path):
    openclaw_root = tmp_path / "openclaw" / "projects"
    outside = tmp_path / "outside" / "SIQ-YUSHU-2026-002"
    outside.mkdir(parents=True)

    with pytest.raises(ValueError):
        import_openclaw_project(
            source_root=outside,
            deal_id="DEAL-YUSHU-2026-001",
            wiki_root=tmp_path / "wiki",
            openclaw_projects_root=openclaw_root,
        )


def test_import_openclaw_project_rejects_symlink_files(tmp_path):
    openclaw_root = tmp_path / "openclaw" / "projects"
    source = openclaw_root / "SIQ-YUSHU-2026-002"
    _write_json(source / "project_meta.json", {"company_name": "宇树"})
    _write_json(source / "phases" / "workflow_state.json", {"company_name": "宇树"})
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    (source / "discussion").mkdir(parents=True)
    (source / "discussion" / "leak.md").symlink_to(outside)

    result = import_openclaw_project(
        source_root=source,
        deal_id="DEAL-YUSHU-2026-001",
        wiki_root=tmp_path / "wiki",
        openclaw_projects_root=openclaw_root,
    )

    package_dir = tmp_path / "wiki" / "deals" / "DEAL-YUSHU-2026-001"
    assert not (package_dir / "discussion" / "leak.md").exists()
    files = result["archive_manifest"]["files"]
    rejected = [item for item in files if item.get("source") == "discussion/leak.md"]
    assert rejected and rejected[0]["status"] == "rejected"


def test_import_openclaw_project_overwrite_removes_stale_files(tmp_path):
    openclaw_root = tmp_path / "openclaw" / "projects"
    source = openclaw_root / "SIQ-YUSHU-2026-002"
    _write_json(source / "project_meta.json", {"company_name": "宇树"})
    _write_json(source / "phases" / "workflow_state.json", {"company_name": "宇树"})
    (source / "40_decision").mkdir(parents=True)
    (source / "40_decision" / "IC_DECISION_REPORT.md").write_text("# Old", encoding="utf-8")
    import_openclaw_project(
        source_root=source,
        deal_id="DEAL-YUSHU-2026-001",
        wiki_root=tmp_path / "wiki",
        openclaw_projects_root=openclaw_root,
    )
    package_dir = tmp_path / "wiki" / "deals" / "DEAL-YUSHU-2026-001"
    assert (package_dir / "decision" / "IC_DECISION_REPORT.md").is_file()

    (source / "40_decision" / "IC_DECISION_REPORT.md").unlink()
    import_openclaw_project(
        source_root=source,
        deal_id="DEAL-YUSHU-2026-001",
        wiki_root=tmp_path / "wiki",
        openclaw_projects_root=openclaw_root,
        overwrite=True,
    )

    assert not (package_dir / "decision" / "IC_DECISION_REPORT.md").exists()


def test_import_openclaw_deal_queues_background_job(monkeypatch):
    seen = {}

    def fake_import_openclaw_project(**kwargs):
        seen["import_kwargs"] = kwargs
        return {
            "deal": {
                "summary": {
                    "deal_id": kwargs["deal_id"],
                    "company_name": "宇树科技",
                    "package_path": "deals/DEAL-YUSHU-2026-001",
                },
                "manifest": {
                    "openclaw_import": {
                        "legacy_project_id": "SIQ-YUSHU-2026-002",
                        "file_count": 3,
                    },
                },
            },
            "archive_manifest": {
                "schema_version": "siq_openclaw_import_v1",
                "legacy_project_id": "SIQ-YUSHU-2026-002",
                "source_root": "SIQ-YUSHU-2026-002",
                "file_count": 3,
                "files": [{"target": "project_meta.json"}],
            },
        }

    def fake_start(kind, target, *, created_by=None):
        seen["kind"] = kind
        seen["created_by"] = created_by
        seen["target_result"] = target()
        return {"job_id": "deal-openclaw-import-abc123", "kind": kind, "status": "queued", "result": None}

    monkeypatch.setattr(deals, "import_openclaw_project", fake_import_openclaw_project)
    monkeypatch.setattr(deals.deal_job_service, "start", fake_start)

    result = deals.import_openclaw_deal(
        deals.OpenClawImportRequest(
            source_root="/tmp/openclaw/projects/SIQ-YUSHU-2026-002",
            deal_id="DEAL-YUSHU-2026-001",
            overwrite=True,
        ),
        wait=False,
        current_user=SimpleNamespace(id=7, username="analyst"),
    )

    assert result["ok"] is True
    assert result["queued"] is True
    assert result["job_id"] == "deal-openclaw-import-abc123"
    assert seen["kind"] == "deal-openclaw-import"
    assert seen["created_by"] == {"id": 7, "username": "analyst"}
    assert seen["import_kwargs"]["overwrite"] is True
    assert seen["target_result"]["ok"] is True
    assert seen["target_result"]["legacy_project_id"] == "SIQ-YUSHU-2026-002"
    assert seen["target_result"]["archive_manifest"] == {
        "schema_version": "siq_openclaw_import_v1",
        "file_count": 3,
    }
    assert "files" not in seen["target_result"]["archive_manifest"]
    assert "source_root" not in json.dumps(seen["target_result"], ensure_ascii=False)


def test_import_openclaw_deal_wait_true_runs_synchronously(monkeypatch):
    seen = {}

    def fake_import_openclaw_project(**kwargs):
        seen.update(kwargs)
        return {"deal": {"summary": {"deal_id": kwargs["deal_id"]}}, "archive_manifest": {"file_count": 1}}

    monkeypatch.setattr(deals, "import_openclaw_project", fake_import_openclaw_project)

    result = deals.import_openclaw_deal(
        deals.OpenClawImportRequest(project_id="SIQ-YUSHU-2026-002", deal_id="DEAL-YUSHU-2026-001"),
        wait=True,
        current_user=SimpleNamespace(id=8, username="pm"),
    )

    assert result["archive_manifest"]["file_count"] == 1
    assert str(seen["source_root"]).endswith("SIQ-YUSHU-2026-002")
    assert seen["created_by"] == {"id": 8, "username": "pm"}


def test_import_openclaw_deal_rejects_invalid_project_id():
    with pytest.raises(HTTPException) as exc:
        deals.import_openclaw_deal(
            deals.OpenClawImportRequest(project_id="../SIQ-YUSHU-2026-002", deal_id="DEAL-YUSHU-2026-001"),
            wait=True,
            current_user=SimpleNamespace(id=8, username="pm"),
        )

    assert exc.value.status_code == 400
    assert "project_id" in str(exc.value.detail)


def test_deal_job_status_uses_deal_job_service(monkeypatch):
    monkeypatch.setattr(
        deals.deal_job_service,
        "get",
        lambda job_id: {
            "job_id": job_id,
            "status": "running",
            "created_by": {"id": 7, "username": "analyst", "email": "hidden@example.com"},
            "result": {"source_root": "/tmp/secret", "deal_id": "DEAL-YUSHU-2026-001"},
        },
    )

    result = deals.get_deal_job_status("deal-openclaw-import-abc123", current_user=SimpleNamespace(id=7, username="analyst"))

    assert result["job_id"] == "deal-openclaw-import-abc123"
    assert result["status"] == "running"
    assert result["created_by"] == {"id": 7, "username": "analyst"}
    assert result["result"] == {"deal_id": "DEAL-YUSHU-2026-001"}


def test_deal_workflow_artifacts_summarize_legacy_agent_reports(tmp_path):
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    _write_json(
        package_dir / "phases" / "r1_reports.json",
        {
            "ic_strategist": {
                "agent_id": "ic_strategist",
                "score": 82,
                "recommendation": "SUPPORT",
                "confidence": "Medium",
                "summary": "战略窗口明确",
                "verified": ["增长率", "政策窗口"],
                "assumed": ["退出窗口"],
                "open_questions": ["核心客户续约"],
                "risk_flags": ["估值偏高"],
                "artifact_path": "discussion/01_R1_strategist_report.md",
                "source_root": "/tmp/secret",
            }
        },
    )
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "agents": {
                "ic_strategist": {
                    "agent_id": "ic_strategist",
                    "receipt_id": "startup-siq_ic_strategist-R1-001",
                    "source_root": "/tmp/secret",
                }
            }
        },
    )
    _write_json(
        package_dir / "phases" / "r1_5_disputes.json",
        {
            "disputes": [
                {
                    "topic": "估值是否支撑 Pre-IPO 定价",
                    "dimension": "finance",
                    "severity": "high",
                    "positions": [{"agent_id": "ic_finance_auditor"}, {"agent_id": "ic_risk_controller"}],
                    "chairman_ruling": {"decision": "resolved_with_conditions"},
                    "resolved": True,
                }
            ]
        },
    )

    result = deals._read_deal_workflow_artifacts(package_dir)

    strategist = result["agent_reports"][0]
    assert strategist["agent_id"] == "siq_ic_strategist"
    assert strategist["has_report"] is True
    assert strategist["has_startup_receipt"] is True
    assert strategist["score"] == 82
    assert strategist["recommendation"] == "SUPPORT"
    assert strategist["verified_count"] == 2
    assert strategist["startup_receipt_id"] == "startup-siq_ic_strategist-R1-001"
    assert result["agent_reports"][2]["agent_id"] == "siq_ic_finance_auditor"
    assert result["agent_reports"][2]["has_report"] is False
    assert result["startup_receipts"] == {"count": 1, "agents": ["siq_ic_strategist"]}
    assert result["disputes"][0]["position_count"] == 2
    assert result["artifact_status"] == {
        "r1_reports": True,
        "startup_receipts": True,
        "r1_5_disputes": True,
    }
    assert "source_root" not in json.dumps(result, ensure_ascii=False)
    assert "/tmp/secret" not in json.dumps(result, ensure_ascii=False)


def test_deal_preflight_warns_for_draft_package_without_execution_artifacts(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )

    result = deal_contracts.run_deal_preflight("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    checks = {item["id"]: item for item in result["checks"]}
    assert result["status"] == "warn"
    assert checks["core.project_meta"]["status"] == "pass"
    assert checks["core.manifest"]["status"] == "pass"
    assert checks["core.workflow_state"]["status"] == "pass"
    assert checks["r1.report_count"]["status"] == "warn"
    assert checks["r4.decision"]["status"] == "warn"


def test_deal_preflight_passes_complete_minimum_contract(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    report_agents = [
        "siq_ic_strategist",
        "siq_ic_sector_expert",
        "siq_ic_finance_auditor",
        "siq_ic_legal_scanner",
        "siq_ic_risk_controller",
    ]
    _write_json(
        package_dir / "phases" / "r1_reports.json",
        {
            agent_id: {
                "agent_id": agent_id,
                "score": 80,
                "recommendation": "SUPPORT",
                "verified": [],
                "assumed": [],
                "open_questions": [],
            }
            for agent_id in report_agents
        },
    )
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "agents": {
                agent_id: {"agent_id": agent_id, "receipt_id": f"startup-{agent_id}-R1-001"}
                for agent_id in report_agents
            }
        },
    )
    _write_ndjson(
        package_dir / "evidence" / "evidence_items.ndjson",
        [
            {"evidence_type": "verified", "dimension": "business", "claim": "business"},
            {"evidence_type": "verified", "dimension": "finance", "claim": "finance"},
            {"evidence_type": "verified", "dimension": "legal", "claim": "legal"},
            {"evidence_type": "verified", "dimension": "risk", "claim": "risk"},
        ],
    )
    _write_json(
        package_dir / "phases" / "r4_decision.json",
        {
            "weighted_agent_score": 82.5,
            "chairman_dimension_score": 78.0,
            "chairman_qualitative_decision": "建议投资但需保护条款",
        },
    )

    result = deal_contracts.run_deal_preflight("DEAL-YUSHU-2026-001", wiki_root=tmp_path)

    assert result["status"] == "pass"
    assert result["counts"] == {
        "r1_reports": 5,
        "startup_receipts": 5,
        "evidence_items": 4,
        "verified_evidence_items": 4,
    }


def test_deal_document_upload_list_get_delete_updates_manifest(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )

    document = deal_documents.create_deal_document(
        deal_id="DEAL-YUSHU-2026-001",
        filename="../BP Final.PDF",
        content_type="application/pdf",
        stream=BytesIO(b"hello deal room"),
        document_type="business_plan",
        source_note="founder upload",
        created_by={"id": 7, "username": "analyst", "email": "hidden@example.com"},
        wiki_root=tmp_path,
    )

    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    assert document["document_id"].startswith("DOC-")
    assert document["original_filename"] == "BP Final.PDF"
    assert document["storage_path"].startswith("data_room/raw/DOC-")
    assert not document["storage_path"].startswith("/")
    assert document["created_by"] == {"id": 7, "username": "analyst"}
    raw_path = package_dir / document["storage_path"]
    assert raw_path.read_bytes() == b"hello deal room"
    assert document["sha256"] == hashlib.sha256(b"hello deal room").hexdigest()

    documents = deal_documents.list_deal_documents("DEAL-YUSHU-2026-001", wiki_root=tmp_path)
    assert [item["document_id"] for item in documents] == [document["document_id"]]
    loaded = deal_documents.get_deal_document("DEAL-YUSHU-2026-001", document["document_id"], wiki_root=tmp_path)
    assert loaded["document_type"] == "business_plan"
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["documents"][0]["document_id"] == document["document_id"]

    result = deal_documents.delete_deal_document(
        "DEAL-YUSHU-2026-001",
        document["document_id"],
        deleted_by={"id": 7, "username": "analyst"},
        wiki_root=tmp_path,
    )

    assert result == {"ok": True, "document_id": document["document_id"]}
    assert not raw_path.exists()
    assert deal_documents.list_deal_documents("DEAL-YUSHU-2026-001", wiki_root=tmp_path) == []
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["documents"] == []


def test_deal_document_rejects_oversized_upload(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )

    with pytest.raises(ValueError):
        deal_documents.create_deal_document(
            deal_id="DEAL-YUSHU-2026-001",
            filename="oversized.pdf",
            content_type="application/pdf",
            stream=BytesIO(b"abcdef"),
            wiki_root=tmp_path,
            max_bytes=5,
        )

    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    assert list((package_dir / "data_room" / "raw").iterdir()) == []


def test_deal_document_delete_removes_symlink_not_target(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    protected = package_dir / "data_room" / "raw" / "protected.txt"
    protected.write_text("keep", encoding="utf-8")
    link = package_dir / "data_room" / "raw" / "DOC-ABCDEF1234567890.txt"
    link.symlink_to(protected)
    metadata = {
        "schema_version": "siq_deal_document_v1",
        "deal_id": "DEAL-YUSHU-2026-001",
        "document_id": "DOC-ABCDEF1234567890",
        "original_filename": "link.txt",
        "storage_path": "data_room/raw/DOC-ABCDEF1234567890.txt",
    }
    _write_json(package_dir / "data_room" / "metadata" / "DOC-ABCDEF1234567890.json", metadata)

    result = deal_documents.delete_deal_document(
        "DEAL-YUSHU-2026-001",
        "DOC-ABCDEF1234567890",
        wiki_root=tmp_path,
    )

    assert result == {"ok": True, "document_id": "DOC-ABCDEF1234567890"}
    assert not link.exists()
    assert protected.read_text(encoding="utf-8") == "keep"


def test_deal_document_bind_parser_task_updates_metadata_manifest_and_audit(tmp_path, monkeypatch):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    document = deal_documents.create_deal_document(
        deal_id="DEAL-YUSHU-2026-001",
        filename="bp.pdf",
        content_type="application/pdf",
        stream=BytesIO(b"hello"),
        wiki_root=tmp_path,
    )
    parser_root = tmp_path / "parser-results"
    artifact = parser_root / "parser-task-1" / "document.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("# parsed", encoding="utf-8")
    monkeypatch.setattr(deal_documents, "DOCUMENT_PARSER_RESULTS_ROOT", parser_root)

    bound = deal_documents.bind_parser_task(
        "DEAL-YUSHU-2026-001",
        document["document_id"],
        task_id="parser-task-1",
        artifact_path="document.md",
        note="manual link",
        bound_by={"id": 7, "username": "analyst", "email": "hidden@example.com"},
        wiki_root=tmp_path,
    )

    assert bound["status"] == "parse_bound"
    assert bound["parse_task_id"] == "parser-task-1"
    assert bound["parsed_artifact_path"] == "document.md"
    assert bound["parser_status_url"] == "/api/documents/status/parser-task-1"
    assert bound["parser_artifact_url"] == "/api/documents/artifact/parser-task-1/document.md"
    assert bound["parser_artifact_exists"] is True
    assert bound["parse_bound_by"] == {"id": 7, "username": "analyst"}
    assert "hidden@example.com" not in json.dumps(bound, ensure_ascii=False)
    package_dir = tmp_path / "deals" / "DEAL-YUSHU-2026-001"
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["documents"][0]["parse_task_id"] == "parser-task-1"
    audit = json.loads((package_dir / "audit" / "audit_log.json").read_text(encoding="utf-8"))
    assert audit["events"][-1]["event_type"] == "deal_document_parser_task_bound"


def test_deal_document_bind_parser_task_rejects_unsafe_inputs(tmp_path):
    deal_store.create_deal_package(
        deal_id="DEAL-YUSHU-2026-001",
        company_name="宇树科技",
        wiki_root=tmp_path,
    )
    document = deal_documents.create_deal_document(
        deal_id="DEAL-YUSHU-2026-001",
        filename="bp.pdf",
        content_type="application/pdf",
        stream=BytesIO(b"hello"),
        wiki_root=tmp_path,
    )

    with pytest.raises(ValueError):
        deal_documents.bind_parser_task(
            "DEAL-YUSHU-2026-001",
            document["document_id"],
            task_id="../bad",
            wiki_root=tmp_path,
        )
    with pytest.raises(ValueError):
        deal_documents.bind_parser_task(
            "DEAL-YUSHU-2026-001",
            document["document_id"],
            task_id="parser-task-1",
            artifact_path="../secret.md",
            wiki_root=tmp_path,
        )
