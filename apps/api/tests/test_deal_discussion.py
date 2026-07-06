import json
from pathlib import Path

import pytest

from services import deal_discussion
from services import deal_store


DEAL_ID = "DEAL-DISCUSSION-001"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _package_with_phase_json(tmp_path: Path) -> Path:
    deal_store.create_deal_package(
        deal_id=DEAL_ID,
        company_name="Router Robotics",
        industry="Robotics",
        stage="Pre-IPO",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / DEAL_ID
    _write_json(
        package_dir / "phases" / "r0_intake.json",
        {
            "schema_version": "siq_ic_r0_intake_v1",
            "deal_id": DEAL_ID,
            "phase": "R0",
            "company_name": "Router Robotics",
            "verification_mode": "local_metadata_only",
            "task_description": {
                "company_name": "Router Robotics",
                "industry": "Robotics",
                "stage": "Pre-IPO",
            },
            "scorecard": {"action": "PROCEED_WITH_CAUTION", "level": "medium"},
            "discrepancies": [],
            "coverage_gaps": ["external_checks_disabled"],
            "created_by": {"id": 7, "username": "analyst", "email": "hidden@example.test"},
            "source_root": "/tmp/hidden",
        },
    )
    _write_json(
        package_dir / "phases" / "r1_reports.json",
        {
            "siq_ic_strategist": {
                "agent_id": "siq_ic_strategist",
                "round_name": "R1",
                "score": 84,
                "recommendation": "support",
                "summary": "Strategic wedge is attractive.",
                "open_questions": [],
                "source_root": "/tmp/hidden",
            },
            "siq_ic_finance_auditor": {
                "agent_id": "siq_ic_finance_auditor",
                "round_name": "R1",
                "score": 72,
                "recommendation": "conditional_pass",
                "summary": "Revenue quality needs one follow-up.",
                "open_questions": ["Refresh customer concentration bridge"],
            },
        },
    )
    _write_json(
        package_dir / "phases" / "r1_5_disputes.json",
        {
            "schema_version": "siq_ic_disputes_v1",
            "deal_id": DEAL_ID,
            "disputes": [
                {
                    "dispute_id": "DISP-001",
                    "topic": "Revenue quality follow-up",
                    "dimension": "finance",
                    "severity": "medium",
                    "positions": [{"agent_id": "siq_ic_finance_auditor"}],
                    "chairman_ruling": {
                        "decision": "resolved_with_conditions",
                        "required_followups": ["Refresh customer concentration bridge"],
                    },
                    "resolved": True,
                }
            ],
        },
    )
    _write_json(
        package_dir / "phases" / "r2_reports.json",
        {
            "reports": {
                "siq_ic_finance_auditor": {
                    "agent_id": "siq_ic_finance_auditor",
                    "round_name": "R2",
                    "r2_score": 78,
                    "recommendation": "support",
                    "summary": "Follow-up was reviewed.",
                    "revisions": ["Updated customer bridge"],
                }
            }
        },
    )
    _write_json(
        package_dir / "phases" / "r3_reports.json",
        {
            "mode": "skip",
            "skip_reason": "No red-blue escalation required.",
            "reports": {},
        },
    )
    _write_json(
        package_dir / "phases" / "r4_decision.json",
        {
            "schema_version": "siq_ic_r4_decision_v1",
            "deal_id": DEAL_ID,
            "decision": "pass",
            "final_score": 80.5,
            "weighted_agent_score": 82,
            "chairman_dimension_score": 80.5,
            "chairman_qualitative_decision": "Proceed with customer renewal monitoring.",
            "conditions": ["Customer renewal validation"],
            "monitoring_metrics": ["Net revenue retention"],
            "human_confirmation": {"status": "pending"},
        },
    )
    return package_dir


def test_build_deal_discussion_dry_run_returns_redacted_preview_without_writing(tmp_path):
    package_dir = _package_with_phase_json(tmp_path)
    audit_before = deal_store.read_json(package_dir / "audit" / "audit_log.json", {})

    result = deal_discussion.build_deal_discussion(
        DEAL_ID,
        dry_run=True,
        created_by={"id": 7, "username": "analyst", "email": "hidden@example.test"},
        wiki_root=tmp_path,
    )

    assert result["schema_version"] == "siq_deal_discussion_builder_v1"
    assert result["status"] == "pass"
    assert result["dry_run"] is True
    assert result["would_write"] is False
    assert result["counts"]["phases"] == 6
    assert result["counts"]["sections"] == 6
    assert result["section_counts"]["R1"]["reports"] == 2
    assert result["section_counts"]["R1.5"]["resolved"] == 1
    assert result["section_counts"]["R3"]["reports"] == 0
    assert result["artifacts"]["markdown"]["path"] == "discussion/IC_DISCUSSION.md"
    assert result["artifacts"]["markdown"]["available"] is False
    assert result["artifacts"]["inputs"]["R0"]["path"] == "phases/r0_intake.json"
    assert "# IC Discussion" in result["redacted_preview"]
    assert "R4 - R4 Decision" in result["redacted_preview"]
    payload_text = json.dumps(result, ensure_ascii=False)
    assert "hidden@example.test" not in payload_text
    assert "/tmp/hidden" not in payload_text
    assert not (package_dir / "discussion" / "IC_DISCUSSION.md").exists()
    assert deal_store.read_json(package_dir / "audit" / "audit_log.json", {}) == audit_before


def test_build_deal_discussion_writes_markdown_and_audit_event(tmp_path):
    package_dir = _package_with_phase_json(tmp_path)

    result = deal_discussion.build_deal_discussion(
        DEAL_ID,
        dry_run=False,
        created_by={"id": 7, "username": "analyst"},
        wiki_root=tmp_path,
    )

    output_path = package_dir / "discussion" / "IC_DISCUSSION.md"
    assert result["dry_run"] is False
    assert result["would_write"] is True
    assert result["written"] is True
    assert result["artifacts"]["markdown"]["available"] is True
    assert result["artifacts"]["markdown"]["written"] is True
    assert output_path.is_file()
    markdown = output_path.read_text(encoding="utf-8")
    assert markdown.startswith("# IC Discussion")
    assert "R1 - R1 Expert Diligence" in markdown
    assert "R4 - R4 Decision" in markdown
    assert "Proceed with customer renewal monitoring." in markdown
    assert "/tmp/hidden" not in markdown
    audit = deal_store.read_json(package_dir / "audit" / "audit_log.json", {})
    assert audit["events"][-1]["event_type"] == "deal_discussion_markdown_built"
    assert audit["events"][-1]["markdown_path"] == "discussion/IC_DISCUSSION.md"
    assert audit["events"][-1]["phase_count"] == 6
    phase_audit = deal_store.read_json(package_dir / "phases" / "audit_log.json", {})
    assert phase_audit["events"][-1]["event_type"] == "deal_discussion_markdown_built"


def test_build_deal_discussion_respects_overwrite_and_phase_subset(tmp_path):
    package_dir = _package_with_phase_json(tmp_path)
    deal_discussion.build_deal_discussion(DEAL_ID, dry_run=False, wiki_root=tmp_path)

    with pytest.raises(FileExistsError, match="discussion artifact already exists"):
        deal_discussion.build_deal_discussion(DEAL_ID, dry_run=False, wiki_root=tmp_path)

    subset = deal_discussion.build_deal_discussion(
        DEAL_ID,
        dry_run=True,
        phases=["R1", "r4"],
        wiki_root=tmp_path,
    )

    assert subset["status"] == "warn"
    assert subset["blocking_reasons"] == ["discussion_markdown_exists"]
    assert subset["counts"]["phases"] == 2
    assert set(subset["artifacts"]["inputs"]) == {"R1", "R4"}
    assert "R1 - R1 Expert Diligence" in subset["redacted_preview"]
    assert "R4 - R4 Decision" in subset["redacted_preview"]
    assert "R0 - R0 Intake" not in subset["redacted_preview"]

    overwritten = deal_discussion.build_deal_discussion(
        DEAL_ID,
        dry_run=False,
        overwrite=True,
        phases=["R4"],
        wiki_root=tmp_path,
    )

    assert overwritten["status"] == "pass"
    assert overwritten["counts"]["phases"] == 1
    markdown = (package_dir / "discussion" / "IC_DISCUSSION.md").read_text(encoding="utf-8")
    assert "R4 - R4 Decision" in markdown
    assert "R1 - R1 Expert Diligence" not in markdown
    audit = deal_store.read_json(package_dir / "audit" / "audit_log.json", {})
    assert audit["events"][-1]["overwrite"] is True
    assert audit["events"][-1]["phases"] == ["R4"]


def test_build_deal_discussion_rejects_unknown_phase(tmp_path):
    _package_with_phase_json(tmp_path)

    with pytest.raises(ValueError, match="unsupported discussion phase"):
        deal_discussion.build_deal_discussion(DEAL_ID, phases=["R5"], wiki_root=tmp_path)
