from __future__ import annotations

import json
import logging

from routers import research_universe as research_universe_router
from services import analysis_report_workflow, factcheck_workflow, tracking_workflow
from services.observability import metrics_snapshot, reset_observability_metrics_for_tests


IDENTITY = {
    "market": "US",
    "company_id": "US:0000320193",
    "filing_id": "US:0000320193:0000320193-25-000079",
    "parse_run_id": "parse-us-aapl",
}
TARGET = {
    "company_key": "opaque-us-aapl-key",
    "research_identity": IDENTITY,
    "source_report": {
        "report_id": "2025-10-K-0000320193-25-000079",
        "source_family": "sec_ixbrl",
    },
}
CONTEXT = {"market": "US", "research_target": TARGET}


def _structured_messages(caplog) -> list[dict]:
    return [json.loads(item.message) for item in caplog.records if item.message.startswith("{")]


def test_three_workflows_emit_redacted_envelopes_and_terminal_validation_metrics(caplog):
    reset_observability_metrics_for_tests()
    caplog.set_level(logging.INFO)

    analysis_report_workflow._workflow_response(
        analysis_report_workflow.AnalysisReportWorkflowRequest(
            company_query="AAPL",
            formal_target=True,
            context_payload=CONTEXT,
            company_key=TARGET["company_key"],
            report_id=TARGET["source_report"]["report_id"],
            research_identity=IDENTITY,
        ),
        {
            "ok": True,
            "stage": "completed",
            "adapter": {"source_family": "sec_ixbrl", "version": "sec_ixbrl_v1"},
            "artifact_id": "analysis-aapl",
            "html_path": "/private/wiki/us/analysis-aapl.html",
        },
    )
    factcheck_workflow._workflow_response(
        factcheck_workflow.FactcheckWorkflowRequest(
            company_query="AAPL",
            research_context=CONTEXT,
            upstream_analysis_artifact_id="analysis-aapl",
        ),
        {
            "ok": False,
            "stage": "failed",
            "artifact": {"source_family": "sec_ixbrl", "adapter_version": "market_factcheck_v1"},
            "validation_result": {
                "failures": ["research_identity_consistent", "citations_traceable"],
            },
            "report_path": "/private/wiki/us/report.html",
        },
    )
    tracking_workflow._workflow_response(
        tracking_workflow.TrackingWorkflowRequest(
            company_query="AAPL",
            research_context=CONTEXT,
            upstream_analysis_artifact_id="analysis-aapl",
        ),
        {
            "ok": True,
            "stage": "degraded",
            "artifact": {
                "artifact_id": "tracking-aapl",
                "source_family": "sec_ixbrl",
                "adapter_version": "market_tracking_v1",
            },
            "prompt": "must never be logged",
        },
    )

    snapshot = metrics_snapshot()
    assert snapshot["research_workflow_terminal_counts"]["US|analysis|success"] == 1
    assert snapshot["research_workflow_terminal_counts"]["US|factcheck|failed"] == 1
    assert snapshot["research_workflow_terminal_counts"]["US|tracking|degraded"] == 1
    assert snapshot["research_identity_mismatch_counts"]["US|factcheck"] == 1
    assert snapshot["research_citation_failure_counts"]["US|factcheck"] == 1

    messages = _structured_messages(caplog)
    assert {item["agent_type"] for item in messages} >= {"analysis", "factcheck", "tracking"}
    for item in messages:
        assert item["company_key_summary"].startswith("sha256:")
        assert item["research_identity"] == IDENTITY
    rendered = "\n".join(item.message for item in caplog.records)
    assert "/private/wiki" not in rendered
    assert "must never be logged" not in rendered
    assert TARGET["company_key"] not in rendered


def test_research_universe_records_market_readiness_without_dynamic_labels(monkeypatch, caplog):
    reset_observability_metrics_for_tests()
    caplog.set_level(logging.INFO, logger=research_universe_router.logger.name)
    monkeypatch.setattr(research_universe_router, "_permission", lambda *_args: None)
    monkeypatch.setattr(
        research_universe_router,
        "list_markets",
        lambda **_kwargs: {
            "markets": [
                {"market": "CN", "company_count": 3, "degraded_reasons": []},
                {"market": "US", "company_count": 2, "degraded_reasons": ["source_adapter_unavailable"]},
                {"market": "JP", "company_count": 0, "degraded_reasons": []},
            ]
        },
    )

    payload = research_universe_router.get_markets(agent_type="analysis", current_user=object())

    assert len(payload["markets"]) == 3
    snapshot = metrics_snapshot()
    assert snapshot["research_readiness_counts"]["CN|analysis|ready"] == 1
    assert snapshot["research_readiness_counts"]["US|analysis|degraded"] == 1
    assert snapshot["research_readiness_counts"]["JP|analysis|unavailable"] == 1
    message = _structured_messages(caplog)[-1]
    assert message["event"] == "research_universe_markets_listed"
    assert message["agent_type"] == "analysis"
