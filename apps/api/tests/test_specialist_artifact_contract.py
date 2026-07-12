from services.agent_runtime_answer_audit import get_answer_audit_trace
from services.specialist_artifact_contract import (
    SpecialistArtifactValidation,
    citation_has_locator,
    finalize_specialist_artifact,
    normalize_citations,
)


def test_specialist_contract_records_traceable_facts_and_session(monkeypatch, tmp_path):
    monkeypatch.setenv("SIQ_ANSWER_AUDIT_TRACE_LOG_PATH", str(tmp_path / "answer_audit.jsonl"))
    citations = normalize_citations(
        [
            {
                "source_path": "companies/600104/reports/2025/report.md",
                "chunk_index": 12,
                "quote": "营业收入同比增长",
            }
        ],
        default_source_type="factcheck_evidence",
    )
    validation = SpecialistArtifactValidation(
        ok=True,
        checks={"citations_present": True},
    )

    contract = finalize_specialist_artifact(
        artifact_type="factcheck",
        company_id="600104-上汽集团",
        source_report_path="companies/600104/analysis/report.md",
        output_path="companies/600104/factcheck/report.json",
        html_url="/api/wiki/companies/600104/factcheck/report.html",
        citations=citations,
        validation_result=validation,
        profile="siq_factchecker",
        message="核查营业收入",
        session_id="user-7-factchecker-session",
        specialist_facts={"factcheck_claim_verdicts": [{"claim_id": "revenue", "verdict": "pass"}]},
    )

    trace = get_answer_audit_trace(contract.audit_trace_id)
    assert trace is not None
    assert trace["session_id"] == "user-7-factchecker-session"
    assert trace["citations"][0]["source_type"] == "factcheck_evidence"
    assert trace["factcheck_claim_verdicts"][0]["claim_id"] == "revenue"
    assert trace["specialist_artifact"]["validation_result"]["ok"] is True


def test_citation_gate_rejects_unlocated_evidence():
    assert citation_has_locator({"source_path": "report.md", "chunk_index": 3}) is True
    assert citation_has_locator({"source_path": "report.md"}) is False
    assert citation_has_locator({"quote": "unsupported"}) is False
