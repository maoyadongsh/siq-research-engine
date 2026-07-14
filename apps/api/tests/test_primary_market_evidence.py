import json
from pathlib import Path

from services import deal_evidence, deal_store

DEAL_ID = "DEAL-PMM-EVIDENCE-001"
DOCUMENT_ID = "DOC-0123456789ABCDEF"
PARSE_RUN_ID = "PRUN-20260713-ABCDEF01"


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _package(tmp_path: Path) -> Path:
    wiki_root = tmp_path / "wiki"
    deal_store.create_deal_package(
        deal_id=DEAL_ID,
        company_name="Evidence Issuer",
        wiki_root=wiki_root,
    )
    package = deal_store.safe_deal_dir(DEAL_ID, wiki_root=wiki_root)
    metadata = {
        "schema_version": "siq_deal_document_v2",
        "deal_id": DEAL_ID,
        "document_id": DOCUMENT_ID,
        "document_type": "prospectus",
        "document_profile": "cn_a_share_prospectus",
        "parser_kind": "pdf",
        "original_filename": "issuer-prospectus.pdf",
        "sha256": "a" * 64,
        "current_parse_run_id": PARSE_RUN_ID,
        "analysis_source_status": "ready_with_restrictions",
    }
    _write_json(package / "data_room" / "metadata" / f"{DOCUMENT_ID}.json", metadata)
    run_dir = package / "parsed_documents" / DOCUMENT_ID / "runs" / PARSE_RUN_ID
    _write_json(
        run_dir / "content_list_enhanced.json",
        {
            "blocks": [
                {"id": "risk-1", "page_idx": 4, "bbox": [1, 2, 3, 4], "text": "风险因素及重大不利影响"},
                {"id": "finance-1", "page": 126, "text": "报告期营业收入及利润情况"},
            ]
        },
    )
    _write_json(run_dir / "archive_manifest.json", {"bundle_sha256": "bundle-a"})
    source = {
        "source_id": f"PM:{DEAL_ID}:{DOCUMENT_ID}:{PARSE_RUN_ID}",
        "source_type": "primary_market_prospectus",
        "deal_id": DEAL_ID,
        "document_id": DOCUMENT_ID,
        "parse_run_id": PARSE_RUN_ID,
        "artifact_manifest_path": f"parsed_documents/{DOCUMENT_ID}/runs/{PARSE_RUN_ID}/archive_manifest.json",
        "status": "ready_with_restrictions",
        "capabilities": {
            "text_evidence": "ready",
            "source_page_trace": "ready",
            "financial_facts": "blocked",
            "semantic_index": "pending",
        },
    }
    _write_json(
        package / "sources" / "analysis_sources.json",
        {"schema_version": "siq_primary_market_analysis_sources_v1", "deal_id": DEAL_ID, "sources": [source]},
    )
    return package


def test_build_pdf_archive_evidence_preserves_source_page_and_capability(tmp_path: Path):
    package = _package(tmp_path)

    result = deal_evidence.build_deal_evidence_package(DEAL_ID, wiki_root=tmp_path / "wiki")

    assert result["counts"]["documents_indexed"] == 1
    items = [json.loads(line) for line in (package / "evidence" / "evidence_items.ndjson").read_text().splitlines()]
    assert items[0]["parse_run_id"] == PARSE_RUN_ID
    assert items[0]["page"] == 5
    assert items[0]["bbox"] == [1, 2, 3, 4]
    assert items[0]["source_url"].startswith(f"/api/primary-market/projects/{DEAL_ID}/")
    finance = next(item for item in items if item["dimension"] == "finance")
    assert finance["evidence_type"] == "restricted"
    assert finance["capability_restrictions"] == ["financial_facts"]
    assert result["evidence_snapshot"]["source_ids"] == [f"PM:{DEAL_ID}:{DOCUMENT_ID}:{PARSE_RUN_ID}"]


def test_refresh_snapshot_marks_old_receipt_stale(tmp_path: Path):
    package = _package(tmp_path)
    _write_json(
        package / "phases" / "startup_receipts.json",
        {
            "agents": {
                "siq_ic_finance_auditor": {
                    "agent_id": "siq_ic_finance_auditor",
                    "evidence_snapshot_hash": "old-hash",
                }
            }
        },
    )

    snapshot = deal_evidence.refresh_evidence_snapshot(DEAL_ID, wiki_root=tmp_path / "wiki")

    receipts = json.loads((package / "phases" / "startup_receipts.json").read_text())
    receipt = receipts["agents"]["siq_ic_finance_auditor"]
    assert receipt["readiness_status"] == "stale"
    assert receipt["current_evidence_snapshot_hash"] == snapshot["snapshot_hash"]


def test_snapshot_change_marks_confirmed_r4_for_review(tmp_path: Path):
    package = _package(tmp_path)
    first = deal_evidence.refresh_evidence_snapshot(DEAL_ID, wiki_root=tmp_path / "wiki")
    _write_json(
        package / "phases" / "r4_decision.json",
        {
            "deal_id": DEAL_ID,
            "evidence_snapshot_hash": first["snapshot_hash"],
            "human_confirmation": {"status": "confirmed"},
        },
    )
    registry = json.loads((package / "sources" / "analysis_sources.json").read_text())
    registry["sources"][0]["archive_manifest_sha256"] = "changed-bundle"
    _write_json(package / "sources" / "analysis_sources.json", registry)

    second = deal_evidence.refresh_evidence_snapshot(DEAL_ID, wiki_root=tmp_path / "wiki")

    assert second["snapshot_hash"] != first["snapshot_hash"]
    workflow = json.loads((package / "phases" / "workflow_state.json").read_text())
    project = json.loads((package / "project_meta.json").read_text())
    assert workflow["status"] == "decision_review_required"
    assert workflow["confirmed_decision_snapshot_hash"] == first["snapshot_hash"]
    assert project["decision_review_required"] is True
