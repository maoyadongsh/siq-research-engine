import json
import sys
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services import deal_reports
from services import deal_store
from services import ic_report_submission


DEAL_ID = "DEAL-R1-SUBMIT-001"
RECEIPT_ID = "startup-siq_ic_strategist-R1-001"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_ndjson(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _create_package(tmp_path: Path) -> Path:
    deal_store.create_deal_package(
        deal_id=DEAL_ID,
        company_name="R1 Submit Co",
        industry="Robotics",
        stage="Pre-IPO",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / DEAL_ID
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v1",
            "deal_id": DEAL_ID,
            "agents": {
                "siq_ic_strategist": {
                    "receipt_id": RECEIPT_ID,
                    "agent_id": "siq_ic_strategist",
                    "round_name": "R1",
                    "shared_hits": 1,
                    "private_hits": 0,
                    "evidence_hits": [{"evidence_id": "EVID-DEAL-R1-SUBMIT-001-000001"}],
                },
            },
        },
    )
    _write_ndjson(
        package_dir / "evidence" / "evidence_items.ndjson",
        [
            {
                "evidence_id": "EVID-DEAL-R1-SUBMIT-001-000001",
                "deal_id": DEAL_ID,
                "claim": "Strategic pull evidence",
            }
        ],
    )
    return package_dir


def _write_startup_receipts(package_dir: Path) -> None:
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v1",
            "deal_id": DEAL_ID,
            "agents": {
                "siq_ic_strategist": {
                    "receipt_id": RECEIPT_ID,
                    "agent_id": "siq_ic_strategist",
                    "round_name": "R1",
                    "shared_hits": 1,
                    "private_hits": 0,
                    "evidence_hits": [{"evidence_id": "EVID-DEAL-R1-SUBMIT-001-000001"}],
                },
            },
        },
    )


def _report_payload(**overrides) -> dict:
    payload = {
        "profile_id": "ic_strategist",
        "round_name": "R1",
        "score": 82,
        "recommendation": "conditional_pass",
        "verified": [{"claim": "market pull", "evidence_id": "EVID-DEAL-R1-SUBMIT-001-000001"}],
        "assumed": ["IPO window remains open"],
        "open_questions": [],
        "startup_receipt_id": RECEIPT_ID,
        "summary": "Strategic fit is promising.",
        "evidence_ids": ["EVID-DEAL-R1-SUBMIT-001-000001"],
        "key_points": ["Distribution leverage is plausible."],
        "risk_flags": [],
    }
    payload.update(overrides)
    return payload


def test_submit_r1_expert_report_dry_run_has_no_file_or_audit_side_effects(tmp_path):
    package_dir = _create_package(tmp_path)
    audit_before = (package_dir / "audit" / "audit_log.json").read_text(encoding="utf-8")

    result = ic_report_submission.submit_r1_expert_report(
        DEAL_ID,
        agent_id="ic_strategist",
        report_payload=_report_payload(source_root="/tmp/hidden"),
        created_by={"id": 7, "username": "ic-admin", "email": "hide@example.test"},
        wiki_root=tmp_path,
    )

    assert result["schema_version"] == "siq_ic_r1_expert_report_submission_v1"
    assert result["dry_run"] is True
    assert result["status"] == "validated"
    assert result["agent_id"] == "siq_ic_strategist"
    assert result["report_written"] is False
    assert result["audit_written"] is False
    assert result["startup_receipt"]["linkage"] == "match"
    assert result["report"]["agent_id"] == "siq_ic_strategist"
    assert result["report"]["created_by"] == {"id": 7, "username": "ic-admin"}
    assert "/tmp/hidden" not in json.dumps(result, ensure_ascii=False)
    assert "hide@example.test" not in json.dumps(result, ensure_ascii=False)
    assert not (package_dir / "phases" / "r1_reports.json").exists()
    assert not (package_dir / "discussion" / "01_R1_strategist_report.md").exists()
    assert (package_dir / "audit" / "audit_log.json").read_text(encoding="utf-8") == audit_before


def test_submit_r1_expert_report_persists_report_markdown_and_audit(tmp_path):
    package_dir = _create_package(tmp_path)

    result = ic_report_submission.submit_r1_expert_report(
        DEAL_ID,
        profile_id="ic_strategist",
        payload=_report_payload(),
        dry_run=False,
        created_by={"id": 7, "username": "ic-admin", "email": "hide@example.test"},
        wiki_root=tmp_path,
    )

    assert result["dry_run"] is False
    assert result["status"] == "submitted"
    assert result["action"] == "create"
    assert result["paths"] == {
        "json": "phases/r1_reports.json",
        "markdown": "discussion/01_R1_strategist_report.md",
    }
    assert result["audit_event"]["created_by"] == {"id": 7, "username": "ic-admin"}

    reports = json.loads((package_dir / "phases" / "r1_reports.json").read_text(encoding="utf-8"))
    stored = reports["siq_ic_strategist"]
    assert stored["schema_version"] == "siq_ic_r1_agent_report_v1"
    assert stored["deal_id"] == DEAL_ID
    assert stored["agent_id"] == "siq_ic_strategist"
    assert stored["profile_id"] == "siq_ic_strategist"
    assert stored["round_name"] == "R1"
    assert stored["phase"] == "R1"
    assert stored["score"] == 82
    assert stored["startup_receipt_id"] == RECEIPT_ID
    assert stored["artifact_path"] == "discussion/01_R1_strategist_report.md"

    markdown = (package_dir / "discussion" / "01_R1_strategist_report.md").read_text(encoding="utf-8")
    assert "# R1 Expert Report - siq_ic_strategist" in markdown
    assert "## 检索结果摘要" in markdown
    assert "### 共享底稿证据" in markdown
    assert "### 私有知识库证据" in markdown
    assert "### 信息缺口清单" in markdown
    assert "### 检索后观点" in markdown

    audit = json.loads((package_dir / "audit" / "audit_log.json").read_text(encoding="utf-8"))
    assert audit["events"][-1]["event_type"] == "deal_r1_expert_report_submitted"
    assert audit["events"][-1]["agent_id"] == "siq_ic_strategist"
    assert audit["events"][-1]["startup_receipt_id"] == RECEIPT_ID
    assert audit["events"][-1]["markdown_path"] == "discussion/01_R1_strategist_report.md"
    workflow = json.loads((package_dir / "phases" / "workflow_state.json").read_text(encoding="utf-8"))
    assert workflow["current_phase"] == "R1"
    assert workflow["status"] == "r1_in_progress"
    assert workflow["phases"]["R1"]["submitted_agents"] == ["siq_ic_strategist"]
    assert result["workflow"]["phases"]["R1"]["latest_agent_id"] == "siq_ic_strategist"

    summary = deal_reports.list_r1_agent_reports(DEAL_ID, wiki_root=tmp_path)
    strategist = next(agent for agent in summary["agents"] if agent["agent_id"] == "siq_ic_strategist")
    assert strategist["status"] == "pass"
    assert strategist["startup_receipt_linkage"] == "match"
    assert strategist["artifact_available"] is True


def test_submit_r1_expert_report_rejects_startup_receipt_mismatch_without_writes(tmp_path):
    package_dir = _create_package(tmp_path)
    audit_before = (package_dir / "audit" / "audit_log.json").read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="startup_receipt_id_mismatch"):
        ic_report_submission.submit_r1_expert_report(
            DEAL_ID,
            "siq_ic_strategist",
            _report_payload(startup_receipt_id="startup-siq_ic_strategist-R1-other"),
            dry_run=False,
            wiki_root=tmp_path,
        )

    assert not (package_dir / "phases" / "r1_reports.json").exists()
    assert not (package_dir / "discussion" / "01_R1_strategist_report.md").exists()
    assert (package_dir / "audit" / "audit_log.json").read_text(encoding="utf-8") == audit_before


def test_submit_r1_expert_report_requires_receipt_and_known_evidence(tmp_path):
    package_dir = _create_package(tmp_path)
    (package_dir / "phases" / "startup_receipts.json").unlink()

    with pytest.raises(ValueError, match="startup_receipts_missing"):
        ic_report_submission.submit_r1_expert_report(
            DEAL_ID,
            "siq_ic_strategist",
            _report_payload(),
            dry_run=True,
            wiki_root=tmp_path,
        )

    _write_startup_receipts(package_dir)
    with pytest.raises(ValueError, match="evidence_ids_missing"):
        ic_report_submission.submit_r1_expert_report(
            DEAL_ID,
            "siq_ic_strategist",
            _report_payload(evidence_ids=[], verified=[], key_points=[]),
            dry_run=True,
            wiki_root=tmp_path,
        )

    with pytest.raises(ValueError, match="evidence_ids_unknown:EVID-UNKNOWN"):
        ic_report_submission.submit_r1_expert_report(
            DEAL_ID,
            "siq_ic_strategist",
            _report_payload(evidence_ids=["EVID-UNKNOWN"]),
            dry_run=True,
            wiki_root=tmp_path,
        )

    _write_ndjson(
        package_dir / "evidence" / "evidence_items.ndjson",
        [
            {"evidence_id": "EVID-DEAL-R1-SUBMIT-001-000001"},
            {"evidence_id": "EVID-DEAL-R1-SUBMIT-001-OTHER"},
        ],
    )
    with pytest.raises(ValueError, match="evidence_ids_not_in_startup_receipt"):
        ic_report_submission.submit_r1_expert_report(
            DEAL_ID,
            "siq_ic_strategist",
            _report_payload(
                evidence_ids=["EVID-DEAL-R1-SUBMIT-001-OTHER"],
                verified=[{"claim": "other evidence", "evidence_id": "EVID-DEAL-R1-SUBMIT-001-OTHER"}],
            ),
            dry_run=True,
            wiki_root=tmp_path,
        )


def test_submit_r1_expert_report_requires_overwrite_for_existing_report(tmp_path):
    package_dir = _create_package(tmp_path)
    ic_report_submission.submit_r1_expert_report(
        DEAL_ID,
        "siq_ic_strategist",
        _report_payload(score=80),
        dry_run=False,
        wiki_root=tmp_path,
    )

    with pytest.raises(FileExistsError, match="already exists"):
        ic_report_submission.submit_r1_expert_report(
            DEAL_ID,
            "siq_ic_strategist",
            _report_payload(score=91),
            dry_run=False,
            wiki_root=tmp_path,
        )

    dry_run_conflict = ic_report_submission.submit_r1_expert_report(
        DEAL_ID,
        "siq_ic_strategist",
        _report_payload(score=91),
        wiki_root=tmp_path,
    )
    assert dry_run_conflict["status"] == "blocked"
    assert dry_run_conflict["allowed"] is False
    assert dry_run_conflict["blocking_reasons"] == ["r1_report_already_exists"]
    assert dry_run_conflict["report_written"] is False

    result = ic_report_submission.submit_r1_expert_report(
        DEAL_ID,
        "siq_ic_strategist",
        _report_payload(score=91),
        dry_run=False,
        overwrite=True,
        wiki_root=tmp_path,
    )

    assert result["action"] == "update"
    reports = json.loads((package_dir / "phases" / "r1_reports.json").read_text(encoding="utf-8"))
    assert reports["siq_ic_strategist"]["score"] == 91
    audit = json.loads((package_dir / "audit" / "audit_log.json").read_text(encoding="utf-8"))
    assert audit["events"][-1]["action"] == "update"
    assert audit["events"][-1]["overwrite"] is True
